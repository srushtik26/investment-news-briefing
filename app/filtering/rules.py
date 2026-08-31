"""
Deterministic Filter Rules for Candidate Business News Articles.

Implements strict validation rules for Publication Dates, Approved Sources,
Non-Article URL Patterns, and Hard Business Event vs Noise Filtering.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
import re
from typing import List, Optional, Set, Tuple
from urllib.parse import urlparse

from app.models.article import Article
from app.filtering.models import FilterResult
from app.logging_config import get_logger

logger = get_logger("filtering.rules")


class BaseFilterRule(ABC):
    """Abstract base class for deterministic filter rules."""

    @property
    @abstractmethod
    def rule_name(self) -> str:
        """Name of the filter rule."""
        pass

    @abstractmethod
    def evaluate(
        self,
        article: Article,
        now_utc: Optional[datetime] = None,
        max_age_hours: Optional[float] = None,
    ) -> FilterResult:
        """Evaluate article against this rule."""
        pass


class DateFilterRule(BaseFilterRule):
    """
    Deterministic Date Freshness Filter Rule.
    
    Ensures publication date is verified and within 48 hours of evaluation,
    with an extended lookback window (72-80h) on Monday morning briefings to
    accommodate Friday/weekend market developments. Never guesses dates.
    """

    DEFAULT_LOOKBACK_HOURS = 24.0

    @property
    def max_age_hours(self) -> float:
        from config import get_settings
        return float(getattr(get_settings(), "STORY_FRESHNESS_HOURS", self.DEFAULT_LOOKBACK_HOURS))

    # Freshness score thresholds (used downstream in Stage 7 ranking)
    FRESHNESS_BUCKETS = [
        (6,   1.0, "fresh_0_6h"),
        (12,  0.9, "fresh_6_12h"),
        (24,  0.8, "fresh_12_24h"),
    ]

    @property
    def rule_name(self) -> str:
        return "DATE"

    def _freshness_score(self, age_hours: float) -> Tuple[float, str]:
        """Return (score, bucket_label) for a given article age in hours."""
        for max_hours, score, label in self.FRESHNESS_BUCKETS:
            if age_hours <= max_hours:
                return score, label
        return 0.0, "stale_24h_plus"

    def evaluate(
        self,
        article: Article,
        now_utc: Optional[datetime] = None,
        max_age_hours: Optional[float] = None,
    ) -> FilterResult:
        current_time = now_utc or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        # 1. Require verified publication timestamp
        if not article.published_at or not getattr(article, "date_verified", True):
            logger.debug(
                "DATE REJECT | TITLE: '%s' | PUBLISHER: %s | RULE: DATE | REASON: Missing/unverified date",
                article.title[:60], article.source_name,
            )
            return FilterResult(
                is_accepted=False,
                article_url=article.url,
                article_title=article.title,
                rule_failed=self.rule_name,
                rejection_reason="Missing or unverified publication date (strictly no date guessing allowed)",
            )

        pub_time = article.published_at
        if pub_time.tzinfo is None:
            pub_time = pub_time.replace(tzinfo=timezone.utc)

        # 2. Check for impossible future dates (> 1 hour tolerance for server clock drift)
        if pub_time > current_time + timedelta(hours=1):
            return FilterResult(
                is_accepted=False,
                article_url=article.url,
                article_title=article.title,
                rule_failed=self.rule_name,
                rejection_reason=f"Publication timestamp is in the future ({pub_time.isoformat()} > {current_time.isoformat()})",
            )

        # 3. Determine allowable lookback window (Strictly 24 hours)
        allowed_hours = max_age_hours if max_age_hours is not None else self.max_age_hours

        # 4. Check explicit structured event date in title (e.g. 'AVENIQUE LIMITED Quarterly Results, 18 Feb 2019 - BSE 3.65')
        title_date_match = re.search(
            r"(?:Quarterly\s+Results|Financial\s+Results|Annual\s+Results|Results|Board\s+Meeting)[,\s\-]+(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\b",
            article.title or "",
            re.IGNORECASE,
        )
        if title_date_match:
            date_str = title_date_match.group(1).strip()
            try:
                for m_full, m_short in [
                    ("January", "Jan"), ("February", "Feb"), ("March", "Mar"), ("April", "Apr"),
                    ("August", "Aug"), ("September", "Sep"), ("October", "Oct"), ("November", "Nov"), ("December", "Dec"),
                ]:
                    date_str = re.sub(rf"\b{m_full}\b", m_short, date_str, flags=re.IGNORECASE)
                parsed_title_dt = datetime.strptime(date_str, "%d %b %Y").replace(tzinfo=timezone.utc)
                # If explicit date is a previous date older than pub_time day or older than allowed window
                if parsed_title_dt.date() == pub_time.date():
                    explicit_age_hours = age_hours
                else:
                    explicit_age_hours = (current_time - parsed_title_dt).total_seconds() / 3600.0
                if explicit_age_hours > allowed_hours and (current_time.date() - parsed_title_dt.date()).days > (allowed_hours / 24.0):
                    logger.debug(
                        "DATE REJECT | TITLE: '%s' | EXPLICIT EVENT AGE: %.1fh | RULE: DATE | REASON: Stale explicit event date (%s)",
                        article.title[:60], explicit_age_hours, date_str,
                    )
                    return FilterResult(
                        is_accepted=False,
                        article_url=article.url,
                        article_title=article.title,
                        rule_failed=self.rule_name,
                        rejection_reason=f"Stale explicit event date '{date_str}' in title ({explicit_age_hours:.1f}h old > {allowed_hours:.0f}h limit)",
                    )
            except Exception:
                pass

        age_seconds = (current_time - pub_time).total_seconds()
        age_hours = max(0.0, age_seconds / 3600.0)
        freshness_score, freshness_bucket = self._freshness_score(age_hours)

        if age_hours > allowed_hours:
            logger.debug(
                "DATE REJECT | TITLE: '%s' | PUBLISHER: %s | AGE: %.1fh | RULE: DATE | REASON: Exceeds %.0fh window",
                article.title[:60], article.source_name, age_hours, allowed_hours,
            )
            return FilterResult(
                is_accepted=False,
                article_url=article.url,
                article_title=article.title,
                rule_failed=self.rule_name,
                rejection_reason=f"Article published {age_hours:.1f}h ago exceeds allowable {allowed_hours:.0f}h freshness window",
            )

        # Store freshness metadata on the article for downstream ranking
        if not article.metadata:
            object.__setattr__(article, 'metadata', {})
        try:
            article.metadata["freshness_score"] = freshness_score
            article.metadata["freshness_bucket"] = freshness_bucket
            article.metadata["age_hours"] = round(age_hours, 1)
        except Exception:
            pass  # metadata may be immutable in some test stubs

        logger.debug(
            "DATE ACCEPT | TITLE: '%s' | AGE: %.1fh | BUCKET: %s | SCORE: %.1f",
            article.title[:60], age_hours, freshness_bucket, freshness_score,
        )
        return FilterResult(
            is_accepted=True,
            article_url=article.url,
            article_title=article.title,
        )


class SourceFilterRule(BaseFilterRule):
    """
    Deterministic Source Validation Filter Rule.
    
    Verifies that the publisher outlet is in the approved whitelist of
    reputable Indian and International financial media.
    """

    ALLOWED_SOURCES: Set[str] = {
        # India Sources
        "business standard",
        "business-standard",
        "economic times",
        "the economic times",
        "economictimes",
        "livemint",
        "mint",
        "financial express",
        "the financial express",
        "financialexpress",
        "moneycontrol",
        "business today",
        "businesstoday",
        "ndtv profit",
        "ndtvprofit",
        "bse corporate announcements",
        "nse corporate announcements",
        "sebi",
        "rbi",
        # International Sources
        "cnbc",
        "ap news",
        "associated press",
        "bbc",
        "bbc news",
        "marketwatch",
        "guardian",
        "the guardian",
        "fortune",
        "reuters",
        "bloomberg",
        "financial times",
        "the financial times",
        "ft",
        "wall street journal",
        "the wall street journal",
        "wsj",
        "sec edgar",
        "federal reserve",
        "ecb",
        "bank of england",
        "business wire",
        "globenewswire",
        "pr newswire",
    }

    ALLOWED_DOMAINS: Set[str] = {
        "business-standard.com",
        "economictimes.indiatimes.com",
        "livemint.com",
        "financialexpress.com",
        "moneycontrol.com",
        "businesstoday.in",
        "ndtvprofit.com",
        "cnbc.com",
        "apnews.com",
        "bbc.com",
        "marketwatch.com",
        "theguardian.com",
        "fortune.com",
        "reuters.com",
        "bloomberg.com",
        "ft.com",
        "wsj.com",
        "sec.gov",
        "federalreserve.gov",
        "ecb.europa.eu",
        "bankofengland.co.uk",
        "businesswire.com",
        "globenewswire.com",
        "prnewswire.com",
        "bseindia.com",
        "nseindia.com",
        "sebi.gov.in",
        "rbi.org.in",
    }

    @property
    def rule_name(self) -> str:
        return "SOURCE"

    @classmethod
    def is_first_party_primary(cls, article: Article) -> Tuple[bool, str]:
        """
        Check if an unwhitelisted source qualifies as a FIRST_PARTY_PRIMARY candidate.
        A first-party source may proceed past SourceFilter ONLY when ALL 6 conditions are met:
        1. URL/domain clearly belongs to the named company/entity in the event.
        2. Page is clearly a newsroom / investor-relations / press-release / financial-results page.
        3. Event is a concrete HARD event (acquisition, earnings, funding, regulatory, contract, guidance, financing).
        4. Article extraction succeeds with sufficient content (>= 50 words).
        5. Date is verified and inside active horizon.
        6. It is NOT commentary, opinion, analyst rating, preview or rumor.
        """
        if not article or not article.url:
            return False, "Missing article or URL"

        netloc = urlparse(article.url).netloc.lower().split(":")[0]
        parts = [p for p in netloc.split(".") if p]
        if len(parts) < 2:
            return False, "Invalid domain structure"

        # 4. Article extraction content check
        words = (article.content_text or "").strip().split()
        if len(words) < 50:
            return False, f"Insufficient extracted content ({len(words)} words < 50)"

        # 5. Date verified
        if not getattr(article, "date_verified", False) or not article.published_at:
            return False, "Unverified publication date"

        # 1. URL/domain clearly belongs to the named company/entity in the event
        generic_tlds = {"com", "org", "net", "io", "co", "in", "ai", "gov", "edu", "uk", "us", "de", "fr", "jp", "cn"}
        generic_sub = {"www", "corporate", "newsroom", "ir", "investor", "investors", "press", "news", "media", "about"}
        domain_tokens = [p for p in parts if p not in generic_tlds and p not in generic_sub]
        if not domain_tokens:
            return False, "No distinct entity token found in domain"

        title_lower = (article.title or "").lower()
        title_norm = re.sub(r"[^a-z0-9]", "", title_lower)
        text_prefix_norm = re.sub(r"[^a-z0-9]", "", (article.content_text or "")[:350].lower())

        matches_entity = False
        matched_tok = ""
        for tok in domain_tokens:
            core_tok = re.sub(r"(news|corp|group)$", "", tok)
            for candidate_t in {tok, core_tok}:
                if len(candidate_t) >= 3 and (candidate_t in title_norm or candidate_t in text_prefix_norm):
                    matches_entity = True
                    matched_tok = candidate_t
                    break
            if matches_entity:
                break

        if not matches_entity:
            return False, f"Domain tokens {domain_tokens} not found in article entity/title"

        # 2. Page is clearly a newsroom / investor-relations / press-release / financial-results page
        subdomain_prefix = parts[0] if len(parts) > 2 else ""
        url_path = urlparse(article.url).path.lower()
        valid_subdomains = {"newsroom", "corporate", "ir", "investor", "investors", "press", "news", "media"}
        valid_path_segments = [
            "/news/", "/newsroom/", "/press-releases/", "/press/", "/investor-relations/",
            "/investors/", "/financial-results/", "/earnings/", "/news-releases/",
            "/announcements/", "/releases/", "/media/", "/sec-filings/", "/filings/",
        ]
        is_newsroom_page = (
            any(sub in subdomain_prefix for sub in valid_subdomains) or
            any(seg in url_path for seg in valid_path_segments) or
            bool(re.search(r"/(news|press|releases?|investor|earnings|financials?)/", url_path))
        )
        prohibited_path_segments = ["/products/", "/pricing/", "/features/", "/promo/", "/shop/", "/store/", "/solutions/", "/careers/"]
        if any(p in url_path for p in prohibited_path_segments):
            return False, "Promotional or product marketing page"
        if not is_newsroom_page:
            return False, "Not a recognized newsroom, investor-relations, or press release page"

        # 6. Check NOT commentary, opinion, analyst rating, preview or rumor
        story_rule = StoryTypeFilterRule()
        full_text = f"{article.title} {article.content_text or ''}"
        for noise_cat, pattern in story_rule.REJECT_NOISE_PATTERNS:
            if re.search(pattern, article.title or "", re.IGNORECASE) or re.search(pattern, full_text[:400], re.IGNORECASE):
                return False, f"Matched noise pattern: {noise_cat}"

        # 3. Event is a concrete HARD event
        HARD_EVENT_PATTERNS = [
            r"\b(?:agrees? to acquire|completes? acquisition|definitive agreement to acquire|to acquire|acquires?|merger agreement|to merge with|completes? merger|buyout of)\b",
            r"\b(?:quarterly results|financial results|earnings|net income|net profit|revenue of|q[1-4] results|operating profit|reports .*? results)\b",
            r"\b(?:funding round|series [a-g]|raises? \$?\d+|completed financing|growth equity round)\b",
            r"\b(?:regulatory approval|antitrust clearance|fda approval|sec approval|clears acquisition|clears merger)\b",
            r"\b(?:awarded contract|secures? \$?\d+.*?contract|signs? \$?\d+.*?agreement|order win|contract award)\b",
            r"\b(?:guidance|full-year outlook|raises outlook|lowers outlook|forecasts? revenue|outlook update)\b",
            r"\b(?:completed financing|credit facility|debt offering|notes offering|closes \$?\d+.*?(?:financing|offering))\b",
        ]
        if not any(re.search(pat, full_text, re.IGNORECASE) for pat in HARD_EVENT_PATTERNS):
            return False, "Not a recognized concrete hard business event"

        return True, f"Legitimate first-party corporate event for entity '{matched_tok}'"

    def evaluate(self, article: Article, now_utc: Optional[datetime] = None) -> FilterResult:
        source_name = (article.source_name or "").strip().lower()
        netloc = urlparse(article.url).netloc.lower()

        # Check source name
        name_valid = any(allowed in source_name for allowed in self.ALLOWED_SOURCES)
        
        # Check domain netloc
        domain_valid = any(d in netloc for d in self.ALLOWED_DOMAINS)

        if not name_valid and not domain_valid:
            is_fp, fp_reason = self.is_first_party_primary(article)
            if is_fp:
                if not article.metadata:
                    object.__setattr__(article, "metadata", {})
                article.metadata["source_class"] = "FIRST_PARTY_PRIMARY"
                logger.info("[FIRST_PARTY_PRIMARY_ALLOWED] '%s' (%s) - %s", (article.title or "")[:60], netloc, fp_reason)
                print(f"[FIRST_PARTY_PRIMARY_ALLOWED] {(article.title or '')[:60]} ({netloc})")
                return FilterResult(
                    is_accepted=True,
                    article_url=article.url,
                    article_title=article.title,
                )

            return FilterResult(
                is_accepted=False,
                article_url=article.url,
                article_title=article.title,
                rule_failed=self.rule_name,
                rejection_reason=f"Source '{article.source_name}' ({netloc}) is not in approved publisher whitelist",
            )

        return FilterResult(
            is_accepted=True,
            article_url=article.url,
            article_title=article.title,
        )


class URLFilterRule(BaseFilterRule):
    """
    Deterministic URL Pattern Filter Rule.
    
    Rejects topic hubs, section indices, category pages, agency pagination, homepages,
    newsletters, unsubscribe redirects, live blogs, search results, and non-HTML files.
    """

    REJECT_PATH_PATTERNS = [
        r"^/?$",                                                              # Root homepage
        r"^/(index|home|default)\.(html|htm|php|asp)/?$",                     # Homepage files
        r"^/(index|home|default)/?$",                                         # Homepage subpath
        r"^/(markets?|investing|business|economy|money|news|world)/?$",       # Section landing pages
        r"/(topics?|tags?|theme|hubs?)/",                                     # Topic/tag hubs
        r"/(category|categories|section|sections|all-news)/",                 # Category landing pages
        r"/(agency|agencies|author|authors|profile)/",                        # Agency/author feeds
        r"/(page|pages|p)/\d+/?$",                                            # Standalone pagination pages
        r"/(newsletters?|bulletins?|daily-brief|unsubscribe|opt-out|email-preferences?)/", # Newsletter / opt-out
        r"/(live-blog|liveblog|live-updates|live-coverage)/",                 # Live commentary feeds
        r"/(search|find|query)/",                                             # Search result directories
        r"/(investing/stock/|stockpricequote/|share-price/|quotes?/|ticker/|company-profile/)", # Stock quotes and market data
        r"/stocks/[^/]+/(infocompanyhistory|companyid|financials|overview|price|quote)", # Exchange company overview pages
        r"/stocks-[^/]+-share-price",                                         # Livemint share price pages
        r"\.(pdf|docx?|xlsx?|pptx?|zip|tar|gz|mp4|mp3|avi|mov|exe)$",         # Non-HTML document/media files
    ]

    REJECT_QUERY_PARAMS = ["q=", "query=", "search=", "s="]

    @classmethod
    def is_valid_url(cls, url: str) -> Tuple[bool, str]:
        """
        Evaluate if a URL points to a plausible article page before attempting network extraction.
        Returns:
            (is_valid, reason)
        """
        if not url or not url.strip():
            return False, "Empty or missing URL"

        parsed = urlparse(url.strip())
        if not parsed.scheme or not parsed.netloc:
            return False, f"Invalid URL structure: '{url}'"

        netloc = parsed.netloc.lower()
        path = parsed.path.lower()
        query = parsed.query.lower()

        # Specific host validation: MarketWatch
        if "marketwatch.com" in netloc:
            clean_path = path.rstrip("/")
            if clean_path in ("", "/markets", "/investing", "/investing/news", "/personal-finance", "/economy-politics", "/watchlist", "/tools", "/column", "/latest-news"):
                return False, "NON_ARTICLE_URL: MarketWatch category/index/hub page is a non-article pattern"
            if not path.startswith("/story/"):
                return False, "NON_ARTICLE_URL: MarketWatch non-article pattern — URL must be a direct article path (/story/...)"

        # Direct rejection for dedicated advice and opinion column paths
        if re.search(r"/(opinion/columns?|personal-finance/(?:advice|tips)|wealth/(?:advice|planning)|advice/)", path):
            return False, "NON_ARTICLE_URL: Dedicated opinion columns / personal advice path is a non-article pattern"

        # 1. Check Path Patterns
        for pat in cls.REJECT_PATH_PATTERNS:
            if re.search(pat, path):
                return False, f"URL path matches non-article pattern '{pat}'"

        # 2. Check Query Parameters
        for qp in cls.REJECT_QUERY_PARAMS:
            if qp in query:
                return False, f"URL contains search/query parameter '{qp}'"

        return True, "Valid article URL"

    @property
    def rule_name(self) -> str:
        return "URL"

    def evaluate(self, article: Article, now_utc: Optional[datetime] = None) -> FilterResult:
        is_valid, reason = self.is_valid_url(article.url)
        if not is_valid:
            return FilterResult(
                is_accepted=False,
                article_url=article.url,
                article_title=article.title,
                rule_failed=self.rule_name,
                rejection_reason=reason,
                matched_patterns=[reason],
            )

        # Check URL path noise patterns: /opinion/, /columns/, /editorial/, /comment/, /analysis/, /personal-finance/, /wealth/, /advice/
        # when there is no independently identifiable hard business event in title or body.
        opinion_path_match = re.search(r"/(opinion|columns?|editorial|comment|analysis|personal-finance|wealth|advice)/", (article.url or "").lower())
        if opinion_path_match:
            eval_text = f"{article.title} {(article.content_text or '')[:300]}".lower()
            has_hard_event = bool(re.search(
                r"\b(net profit|revenue rises|revenue falls|profit rises|profit falls|q[1-4] results|earnings beat|beats estimates|acquires?|acquisition|buyout|merger|bags order|secures contract|order win|raises funds|funding round|qip|rights issue|files for ipo|share buyback|dividend|penalty order|antitrust fine)\b",
                eval_text
            ))
            if not has_hard_event:
                return FilterResult(
                    is_accepted=False,
                    article_url=article.url,
                    article_title=article.title,
                    rule_failed=self.rule_name,
                    rejection_reason=f"URL path '{opinion_path_match.group(0)}' is opinion/commentary/advice without an identifiable hard business event",
                    matched_patterns=[opinion_path_match.group(0)],
                )

        return FilterResult(
            is_accepted=True,
            article_url=article.url,
            article_title=article.title,
        )


def is_generic_headline(title: str) -> Tuple[bool, str]:
    """
    Evaluate if a headline is an umbrella/index/generic title lacking a named entity and concrete action.
    Examples that fail:
        'Company Announcements'
        'Latest News'
        'Market Updates'
        'Business News'
        'Corporate Announcements'
        'Stock Market Live'
        'Today's News'
        'News Updates'
    Examples that pass:
        'BYD profit falls 18% as China competition intensifies'
        'KNR Constructions bags ₹158 crore EPC order from GHMC'
    """
    if not title or not title.strip():
        return True, "Empty title"

    t_clean = title.strip()
    t_lower = t_clean.lower()

    GENERIC_TITLES = {
        "company announcement", "company announcements",
        "corporate announcement", "corporate announcements",
        "latest news", "market update", "market updates",
        "business news", "stock market live", "today's news",
        "todays news", "news update", "news updates",
        "top news", "headlines today", "morning bell",
        "closing bell", "market live", "live market updates",
        "live updates", "daily brief", "business roundup",
        "announcements", "financial news", "world news",
    }

    normalized = re.sub(r"[^\w\s]", "", t_lower).strip()
    if normalized in GENERIC_TITLES:
        return True, f"Headline '{title}' is an umbrella/generic index phrase"

    for g in GENERIC_TITLES:
        if normalized == g or normalized.startswith(g + " ") or normalized.endswith(" " + g):
            if len(normalized.split()) <= len(g.split()) + 1:
                return True, f"Headline '{title}' is a generic index title"

    words = t_clean.split()
    if len(words) <= 3 and not re.search(r"[\$₹\d%]", t_clean):
        has_action = bool(re.search(r"\b(buys|acquired?|profit|loss|rises|falls|jumps|drops|wins|bags|files|hikes|cuts|merges|deal)\b", t_lower))
        if not has_action:
            return True, f"Headline '{title}' is too short and lacks a concrete action/event"

    return False, "Valid specific headline"


class StoryTypeFilterRule(BaseFilterRule):
    """
    Deterministic Story Type & Noise Filter Rule.
    
    Accepts hard business events (earnings with numbers, M&A, QIPs, fundraises,
    regulatory actions, policy, leadership changes, macro data, quantified geopolitics)
    and strictly rejects market commentary, analyst upgrades/downgrades, price targets,
    results calendars, speculative price-moves, and opinion columns.
    """

    # NOISE REJECTION PATTERNS
    REJECT_NOISE_PATTERNS: List[Tuple[str, str]] = [
        (
            "personal_finance_and_advice",
            r"\b(what next\?|deciding what to do after|financial freedom|retirement planning|saving for retirement|do you own\??|what should investors do|how should you invest|money habits|wealth creation tips|smart money moves)\b",
        ),
        (
            "opinion_editorial",
            r"\b(opinion|editorial|column|view|analysis|commentary|our take|expert view)\s*:|\b(why we think|opinion column|editorial view)\b",
        ),
        (
            "analyst_rating",
            r"\b(upgrades?|downgrades?|initiates? coverage|reiterates?|buy rating|sell rating|hold rating|underperform rating|outperform rating|brokerages?\s+(?:upgrades?|downgrades?|raises?|cuts?|sees?|initiates?|reiterates?|targets?|price target))\b",
        ),
        (
            "analyst_speculation",
            r"\b(one analyst (?:now )?thinks|analysts? thinks?|analysts? says?.*?\bcould\b|could hit|could reach|heading for|price target|wall street thinks|analysts? predicts?|analysts? expects? shares could|could become|is .* heading for)\b",
        ),
        (
            "investment_advice_and_stock_picks",
            r"\b(stocks? to buy|best stocks? to buy|top stocks?|top stock picks?|stock picks?|dividend stocks?|portfolio boost|portfolio picks?|analyst recommends?|buy these stocks?|stocks? analysts? love|stocks? poised to rise|investment ideas?|portfolio ideas?|stocks? could give your portfolio a boost|these .* stocks could)\b",
        ),
        (
            "price_target",
            r"\b(target price|price target|sees target|ups target to|cuts target to|target price of|target price rs|share target price|target of rs|target of \$)\b",
        ),
        (
            "speculative_price_rally",
            r"\b(soars?|surges?|jumps?|plunges?|rallies|rally|climbs?|slides?|slumps?)\s+.*?\b(amid|on|following|hopes? of|speculation of|expectation of|rumou?rs? of)\b.*?\b(crypto|bitcoin|etf approval|approval hopes?|fed rate cut hopes?|rate cut hopes?)\b",
        ),
        (
            "speculative_hope",
            r"\b(hopes? of|speculation of|bets on)\b.*?\b(etf approval|approval|rate cut)\b",
        ),
        (
            "market_summary",
            r"\b(stock market today|closing bell|opening bell|markets wrap|sensex ends|nifty today|sensex today|market live|morning bell|top gainers and losers|stocks to watch today|mid-day market update|wall street opens higher|european markets close)\b",
        ),
        (
            "results_calendar",
            r"\b(results today|earnings today|upcoming earnings|companies announcing results|earnings calendar|q[1-4] preview|results preview|what to expect from q[1-4])\b",
        ),
        (
            "profile_feature",
            r"\bwho is\b|\b(biography|profile feature|career profile)\b",
        ),
        (
            "earnings_preview",
            r"\b(earnings preview|preview.*earnings|what to expect|watch ahead|faces? (?:a )?big test.*earnings|analysts? expect.*earnings)\b",
        ),        (
            "speculative_transaction",
            r"\b(likely|may|might|could)\s+(?:to\s+)?(?:consider\s+)?(?:acquire|acquiring|buy|buying|purchase|purchasing|merge|merging|take\s*over)\b|\b(considering\s+(?:acquiring|acquisition|buying|buyout|takeover|sale|merger))\b|\b(could acquire|may acquire|exploring sale|reportedly negotiating)\b",
        ),
        (
            "speculative_deal_talks",
            r"\b(in talks|in (?:early |advanced )?talks (?:to|with|for)?|in discussions (?:to|with|for)|eyeing (?:a )?(?:controlling )?stake|eyes? (?:a )?(?:controlling )?stake|mulls? (?:stake|acquisition|buying|sale)|exploring (?:acquisition|sale|buying|options|stake)|explores? (?:sale|acquisition|buying|stake)|report says talks|seeking to buy|weighs (?:bid|acquisition|sale|buying)|in talks to buy|in talks to acquire|nears? (?:[$\w\s\.]*?)deal|nears? (?:acquisition|buyout|merger)|close to (?:deal|buying|acquiring|merger|acquisition)|reportedly (?:negotiating|in talks|close to|exploring|considering))\b",
        ),
        (
            "ipo_intraday",
            r"\b(day [123] subscription|subscribed \d+(\.\d+)?x on day|ipo subscription status|ipo bidding status|ipo day [123] update)\b",
        ),
    ]

    # HARD BUSINESS EVENT ACCEPTANCE PATTERNS
    ACCEPT_EVENT_PATTERNS: List[Tuple[str, str]] = [
        (
            "earnings_figures",
            r"\b(net profit|revenue|q[1-4] profit|q[1-4] revenue|ebitda|margin|surges|jumps|rises \d+%|falls \d+%|reports profit of|profit rises|profit falls|revenue rises|revenue falls|profit jumps|profit drops|earnings beat|earnings miss|beats? (?:quarterly |q[1-4] |earnings |wall street )?estimates|hikes? (?:its )?(?:full.year )?outlook|beats? expectations|tops? estimates|tops? expectations|₹\s*[\d,]+|rs\.?\s*[\d,]+|\$\s*[\d,]+|crore|billion|million|quarterly results|annual results|net income|operating income|gross profit|comparable sales|same-store sales)\b",
        ),
        (
            "acquisitions_mergers",
            r"\b(to buy|buys\b|acquires?|acquisition|mergers?|merge|buyout|takeover|nclt scheme|amalgamation|demerger|spin-off|splits into|splits of|deal to buy|agrees to buy|all-cash deal)\b",
        ),
        (
            "contracts_orders",
            r"\b(epc contract|epc order|bags order|secures contract|wins order|mega order|order worth|contract worth|processing facility)\b",
        ),
        (
            "stake_and_investment",
            r"\b(buys stake|stake purchase|stake sale|block deal|bulk deal|equity changes hands|promoter stake sale|institutional stake sale|promoter increases stake|invests in|investment in|invests\b|investment\b|plant investment|capacity expansion|strategic investment|divestment|asset sale)\b",
        ),
        (
            "fundraises_qips",
            r"\b(raises funds|raises funding|funding round|qip|qualified institutional placement|rights issue|capital raise|secures funding|fundraise)\b",
        ),
        (
            "bond_issuances",
            r"\b(issues bonds|ncds|non-convertible debentures|dollar bonds|debt issuance|bond issue)\b",
        ),
        (
            "ipo_listings",
            r"\b(ipo listing|ipo debut|listing day|files drhp|draft ipo papers|shares list at|files for ipo|ipo\b)\b",
        ),
        (
            "corporate_actions",
            r"\b(dividend|special dividend|interim dividend|share buyback|stock buyback|buyback)\b",
        ),
        (
            "regulatory_actions",
            r"\b(penalty order|monetary penalty|nclt approves|sebi order|rbi penalty|antitrust probe|regulatory fine)\b",
        ),
        (
            "government_policy",
            r"\b(pli scheme|customs duty|export tax|subsidy|tariff revision|policy impact)\b",
        ),
        (
            "leadership_changes",
            r"\b(appoints ceo|md resigns|new managing director|new cfo|appoints chairman|steps down|chief financial officer)\b",
        ),
        (
            "macroeconomic_data",
            r"\b(gdp growth|cpi inflation|retail inflation|iip data|trade deficit|industrial output)\b",
        ),
        (
            "geopolitical_quantified",
            r"\b(oil price surge|supply disruption|shipping transit|sanctions impact|barrel)\b",
        ),
        (
            "guidance_corporate",
            r"\b(reaffirms? guidance|narrows? guidance|raises? guidance|guidance raised|guidance cut|lowers? guidance|cuts? guidance|maintains? outlook|updates? guidance|full.year guidance|fiscal year guidance|capex guidance|capital expenditure guidance|raises? forecast|lowers? forecast)\b",
        ),
        (
            "joint_venture",
            r"\b(joint venture|JV agreement|forms? jv|enters? jv|signs? jv|jv with|strategic alliance|memorandum of understanding|MoU signed)\b",
        ),
        (
            "divestiture",
            r"\b(divests?|divestiture|divestment|asset sale|sells? stake|exits? business|monetises?|monetizes?|sells? unit|hives? off)\b",
        ),
        (
            "restructuring",
            r"\b(restructuring|job cuts|layoffs?|retrenchment|workforce reduction|sever|headcount reduction|cost rationali)\b",
        ),
    ]

    @property
    def rule_name(self) -> str:
        return "STORY_TYPE"

    def evaluate(self, article: Article, now_utc: Optional[datetime] = None) -> FilterResult:
        # Check for generic umbrella/index headline
        is_gen, gen_reason = is_generic_headline(article.title or "")
        if is_gen:
            logger.debug(
                "STORY_TYPE REJECT | TITLE: '%s' | REASON: %s",
                article.title[:60], gen_reason
            )
            return FilterResult(
                is_accepted=False,
                article_url=article.url,
                article_title=article.title,
                rule_failed=self.rule_name,
                rejection_reason=f"Generic headline rejected: {gen_reason}",
                matched_patterns=["generic_headline", gen_reason],
            )

        eval_text = f"{article.title} {article.content_text[:500]}".lower()

        # 1. Check for Rejection / Noise Patterns First
        for pattern_name, regex_pattern in self.REJECT_NOISE_PATTERNS:
            match = re.search(regex_pattern, eval_text, re.IGNORECASE)
            if match:
                matched_str = match.group(0)

                # EXCEPTION: If the story matches speculative transaction/deal talks, but ALSO contains
                # an actual completed hard business event (e.g. block deal, earnings beat, signed acquisition, order win),
                # allow the concrete completed event to survive.
                if pattern_name in ("speculative_transaction", "speculative_deal_talks"):
                    has_completed_hard_event = bool(re.search(
                        r"\b(block deal|bulk deal|equity changes hands|net profit|revenue rises|revenue jumps|revenue falls|profit rises|profit falls|q[1-4] profit|q[1-4] net profit|earnings beat|earnings miss|beats? (?:quarterly |q[1-4] |earnings |wall street )?estimates|hikes? (?:its )?(?:full.year )?outlook|agrees to buy|signed definitive agreement|all-cash deal|nclt scheme|bags (?:mega )?order|secures contract|issues bonds|files for ipo|share buyback|dividend|quarterly results|annual results)\b",
                        eval_text,
                        re.IGNORECASE,
                    ))
                    if has_completed_hard_event:
                        logger.debug(
                            "STORY_TYPE ALLOW_OVERRIDE | TITLE: '%s' | REASON: Concrete completed hard event present despite secondary speculative talks phrasing",
                            article.title[:60],
                        )
                        continue

                logger.debug(
                    "STORY_TYPE REJECT | TITLE: '%s' | PUBLISHER: %s | RULE: %s | REASON: Noise '%s' (match: '%s')",
                    article.title[:60], article.source_name, pattern_name, pattern_name, matched_str
                )
                return FilterResult(
                    is_accepted=False,
                    article_url=article.url,
                    article_title=article.title,
                    rule_failed=self.rule_name,
                    rejection_reason=f"Article matches prohibited noise/commentary pattern '{pattern_name}' (matched: '{matched_str}')",
                    matched_patterns=[pattern_name, matched_str],
                )

        # 2. Check for Acceptance / Hard Business Event Patterns
        matched_acceptances: List[str] = []
        for pattern_name, regex_pattern in self.ACCEPT_EVENT_PATTERNS:
            match = re.search(regex_pattern, eval_text, re.IGNORECASE)
            if match:
                matched_acceptances.append(pattern_name)

        if not matched_acceptances:
            logger.debug(
                "STORY_TYPE REJECT | TITLE: '%s' | PUBLISHER: %s | RULE: STORY_TYPE | REASON: No hard event pattern matched",
                article.title[:60], article.source_name
            )
            return FilterResult(
                is_accepted=False,
                article_url=article.url,
                article_title=article.title,
                rule_failed=self.rule_name,
                rejection_reason="Article lacks concrete hard business event indicators (general commentary or unclassified narrative)",
            )

        return FilterResult(
            is_accepted=True,
            article_url=article.url,
            article_title=article.title,
            matched_patterns=matched_acceptances,
        )


class DomesticSourceFilterRule(BaseFilterRule):
    """
    Source validation for Domestic (General Trending India News) articles.

    Validates against the trusted general national news publisher whitelist.
    Does NOT share the business financial publisher whitelist — domestic
    sources include general news outlets (The Hindu, NDTV, HT, India Today, TOI, etc.)
    that are NOT in the business SourceFilterRule.

    Accepts any of the DOMESTIC_ALLOWED_DOMAINS or DOMESTIC_ALLOWED_SOURCE_NAMES.
    """

    DOMESTIC_ALLOWED_SOURCE_NAMES: Set[str] = {
        # General national news
        "the hindu",
        "hindu",
        "indian express",
        "the indian express",
        "hindustan times",
        "ndtv",
        "india today",
        "times of india",
        "toi",
        # Also accept business outlets that publish general news
        "economic times",
        "the economic times",
        "economictimes",
        "business standard",
        "business-standard",
        "livemint",
        "mint",
        "financial express",
        "the financial express",
        "the hindu businessline",
        "hindu businessline",
        "businessline",
        # Government / Official
        "pib",
        "press information bureau",
        "pmo india",
        "isro",
        "supreme court of india",
        # Wire services
        "bbc",
        "bbc news",
        "ani",
        "pti",
        "reuters",
        "ap news",
        "associated press",
        # Regional but major outlets
        "moneycontrol",
        "ndtv profit",
    }

    DOMESTIC_ALLOWED_DOMAINS: Set[str] = {
        "thehindu.com",
        "indianexpress.com",
        "hindustantimes.com",
        "ndtv.com",
        "indiatoday.in",
        "timesofindia.indiatimes.com",
        "economictimes.indiatimes.com",
        "business-standard.com",
        "livemint.com",
        "thehindubusinessline.com",
        "financialexpress.com",
        "bbc.com",
        "bbc.co.uk",
        "pib.gov.in",
        "sci.gov.in",
        "isro.gov.in",
        "pmo.gov.in",
        # Also allow government ministry sites
        "mohfw.gov.in",
        "moesgov.in",
        "imd.gov.in",
        "mha.gov.in",
        # Wire services
        "reuters.com",
        "apnews.com",
    }

    @property
    def rule_name(self) -> str:
        return "DOMESTIC_SOURCE"

    def evaluate(self, article: Article, now_utc: Optional[datetime] = None) -> FilterResult:
        source_name = (article.source_name or "").strip().lower()
        netloc = urlparse(article.url).netloc.lower()

        name_valid = any(allowed in source_name for allowed in self.DOMESTIC_ALLOWED_SOURCE_NAMES)
        domain_valid = any(d in netloc for d in self.DOMESTIC_ALLOWED_DOMAINS)

        if not name_valid and not domain_valid:
            return FilterResult(
                is_accepted=False,
                article_url=article.url,
                article_title=article.title,
                rule_failed=self.rule_name,
                rejection_reason=(
                    f"Domestic source '{article.source_name}' ({netloc}) is not in the trusted "
                    f"domestic publisher registry (thehindu.com, indianexpress.com, "
                    f"hindustantimes.com, ndtv.com, indiatoday.in, timesofindia.indiatimes.com, etc.)"
                ),
            )

        return FilterResult(
            is_accepted=True,
            article_url=article.url,
            article_title=article.title,
        )

