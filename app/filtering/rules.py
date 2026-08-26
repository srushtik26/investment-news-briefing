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
        "bseindia.com",
        "nseindia.com",
        "sebi.gov.in",
        "rbi.org.in",
        "sec.gov",
        "federalreserve.gov",
        "ecb.europa.eu",
        "bankofengland.co.uk",
        "businesswire.com",
        "globenewswire.com",
        "prnewswire.com",
    }

    @property
    def rule_name(self) -> str:
        return "SOURCE"

    def evaluate(self, article: Article, now_utc: Optional[datetime] = None) -> FilterResult:
        source_name = (article.source_name or "").strip().lower()
        netloc = urlparse(article.url).netloc.lower()

        # Check source name
        name_valid = any(allowed in source_name for allowed in self.ALLOWED_SOURCES)
        
        # Check domain netloc
        domain_valid = any(d in netloc for d in self.ALLOWED_DOMAINS)

        if not name_valid and not domain_valid:
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

        return FilterResult(
            is_accepted=True,
            article_url=article.url,
            article_title=article.title,
        )


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
            "opinion_editorial",
            r"\b(opinion|editorial|column|view|analysis|commentary|our take|expert view)\s*:|\b(why we think|opinion column|editorial view)\b",
        ),
        (
            "analyst_rating",
            r"\b(upgrades?|downgrades?|initiates? coverage|reiterates?|brokerage firm|brokerage|buy rating|sell rating|hold rating|underperform rating|outperform rating)\b",
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
        ),
        (
            "speculative_transaction",
            r"\b(likely|may|might|could)\s+(?:to\s+)?(?:acquire|buy|purchase|merge|take over)\b",
        ),
        (
            "ipo_intraday",
            r"\b(day [123] subscription|subscribed \d+(\.\d+)?x on day|ipo subscription status|ipo bidding status|ipo day [123] update)\b",
        ),
        (
            "profile_feature",
            r"\bwho is\b|\b(biography|profile feature|career profile)\b",
        ),
        (
            "earnings_preview",
            r"\b(earnings preview|preview.*earnings|what to expect|watch ahead|faces? (?:a )?big test.*earnings|analysts? expect.*earnings)\b",
        ),
        (
            "speculative_transaction",
            r"\b(likely|may|might|could)\s+(?:to\s+)?(?:acquire|buy|purchase|merge|take over)\b",
        ),
    ]

    # HARD BUSINESS EVENT ACCEPTANCE PATTERNS
    ACCEPT_EVENT_PATTERNS: List[Tuple[str, str]] = [
        (
            "earnings_figures",
            r"\b(net profit|revenue|q[1-4] profit|q[1-4] revenue|ebitda|margin|surges|jumps|rises \d+%|falls \d+%|reports profit of|profit rises|profit falls|revenue rises|revenue falls|profit jumps|profit drops|earnings beat|earnings miss|₹\s*[\d,]+|rs\.?\s*[\d,]+|\$\s*[\d,]+|crore|billion|million|quarterly results|annual results|net income|operating income|gross profit|comparable sales|same-store sales)\b",
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
            r"\b(rbi\b.*\b(penalty|fine|order|ban)|sebi\b.*\b(order|penalty|ban)|cci\b.*\b(approves?|order)|antitrust|sec charges|doj lawsuit|eu fine|penalty order|monetary penalty|regulatory penalty|tribunal order|regulatory violations)\b",
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
        eval_text = f"{article.title} {article.content_text[:500]}".lower()

        # 1. Check for Rejection / Noise Patterns First
        for pattern_name, regex_pattern in self.REJECT_NOISE_PATTERNS:
            match = re.search(regex_pattern, eval_text, re.IGNORECASE)
            if match:
                matched_str = match.group(0)
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
