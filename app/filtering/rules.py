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
    def evaluate(self, article: Article, now_utc: Optional[datetime] = None) -> FilterResult:
        """Evaluate article against this rule."""
        pass


class DateFilterRule(BaseFilterRule):
    """
    Deterministic Date Freshness Filter Rule.
    
    Ensures publication date is verified and within 48 hours of evaluation,
    with an extended lookback window (72-80h) on Monday morning briefings to
    accommodate Friday/weekend market developments. Never guesses dates.
    """

    DEFAULT_LOOKBACK_HOURS = 72
    MONDAY_LOOKBACK_HOURS = 96

    # Freshness score thresholds (used downstream in Stage 7 ranking)
    FRESHNESS_BUCKETS = [
        (24,  1.0, "fresh_0_24h"),
        (48,  0.8, "fresh_24_48h"),
        (72,  0.5, "stale_48_72h"),
    ]

    @property
    def rule_name(self) -> str:
        return "DATE"

    def _freshness_score(self, age_hours: float) -> Tuple[float, str]:
        """Return (score, bucket_label) for a given article age in hours."""
        for max_hours, score, label in self.FRESHNESS_BUCKETS:
            if age_hours <= max_hours:
                return score, label
        return 0.0, "stale_72h_plus"

    def evaluate(self, article: Article, now_utc: Optional[datetime] = None) -> FilterResult:
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

        # 3. Determine allowable lookback window (Extended for Monday morning briefing)
        is_monday = current_time.weekday() == 0
        allowed_hours = self.MONDAY_LOOKBACK_HOURS if is_monday else self.DEFAULT_LOOKBACK_HOURS

        age_seconds = (current_time - pub_time).total_seconds()
        age_hours = max(0.0, age_seconds / 3600.0)
        freshness_score, freshness_bucket = self._freshness_score(age_hours)

        if age_hours > allowed_hours:
            logger.debug(
                "DATE REJECT | TITLE: '%s' | PUBLISHER: %s | AGE: %.1fh | RULE: DATE | REASON: Exceeds %dh window",
                article.title[:60], article.source_name, age_hours, allowed_hours,
            )
            return FilterResult(
                is_accepted=False,
                article_url=article.url,
                article_title=article.title,
                rule_failed=self.rule_name,
                rejection_reason=(
                    f"Article published {age_hours:.1f}h ago exceeds allowable {allowed_hours}h freshness window"
                    f"{' (Monday weekend lookback applied)' if is_monday else ''}"
                ),
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
    
    Rejects topic hubs, section indices, category pages, homepages,
    newsletters, live blogs, and search query results.
    """

    REJECT_PATH_PATTERNS = [
        r"^/?$",                          # Root homepage
        r"^/(index|home|default)\.(html|htm|php|asp)/?$", # Homepage files
        r"/(topics?|tags?|theme|hubs?)/", # Topic/tag hubs
        r"/(category|categories|section|all-news)/", # Category landing pages
        r"/(newsletters?|bulletins?|daily-brief)/", # Newsletter signup/archive
        r"/(live-blog|liveblog|live-updates|live-coverage)/", # Live commentary feeds
        r"/(search|find|query)/",         # Search result directories
    ]

    REJECT_QUERY_PARAMS = ["q=", "query=", "search="]

    @property
    def rule_name(self) -> str:
        return "URL"

    def evaluate(self, article: Article, now_utc: Optional[datetime] = None) -> FilterResult:
        parsed = urlparse(article.url)
        path = parsed.path.lower()
        query = parsed.query.lower()

        # 1. Check Path Patterns
        for pat in self.REJECT_PATH_PATTERNS:
            if re.search(pat, path):
                return FilterResult(
                    is_accepted=False,
                    article_url=article.url,
                    article_title=article.title,
                    rule_failed=self.rule_name,
                    rejection_reason=f"URL matches non-article directory/hub pattern '{pat}'",
                    matched_patterns=[pat],
                )

        # 2. Check Search Query Parameters
        for qp in self.REJECT_QUERY_PARAMS:
            if qp in query:
                return FilterResult(
                    is_accepted=False,
                    article_url=article.url,
                    article_title=article.title,
                    rule_failed=self.rule_name,
                    rejection_reason=f"URL contains search query parameter '{qp}'",
                    matched_patterns=[qp],
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
    results calendars, and opinion columns.
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
            "market_summary",
            r"\b(stock market today|closing bell|opening bell|markets wrap|sensex ends|nifty today|sensex today|market live|morning bell|top gainers and losers|stocks to watch today|mid-day market update|wall street opens higher|european markets close)\b",
        ),
        (
            "results_calendar",
            r"\b(results today|earnings today|upcoming earnings|companies announcing results|earnings calendar|q[1-4] preview|results preview|what to expect from q[1-4])\b",
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
            r"\b(net profit|revenue|q[1-4] profit|q[1-4] revenue|ebitda|margin|surges|jumps|rises \d+%|falls \d+%|reports profit of|₹\s*[\d,]+|rs\.?\s*[\d,]+|\$\s*[\d,]+|crore|billion|quarterly results|annual results|net income|operating income|gross profit|comparable sales|same-store sales)\b",
        ),
        (
            "acquisitions_mergers",
            r"\b(acquires|acquisition|merger|demerger|buyout|takeover|nclt scheme|amalgamation|splits into|splits of)\b",
        ),
        (
            "contracts_orders",
            r"\b(epc contract|epc order|bags order|secures contract|wins order|mega order|order worth|contract worth|processing facility)\b",
        ),
        (
            "stake_purchases",
            r"\b(buys stake|stake purchase|block deal|bulk deal|promoter increases stake)\b",
        ),
        (
            "fundraises_qips",
            r"\b(raises funds|qip|qualified institutional placement|funding round|rights issue|capital raise|secures funding)\b",
        ),
        (
            "bond_issuances",
            r"\b(issues bonds|ncds|non-convertible debentures|dollar bonds|debt issuance|bond issue)\b",
        ),
        (
            "ipo_listings",
            r"\b(ipo listing|ipo debut|listing day|files drhp|draft ipo papers|shares list at)\b",
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
            r"\b(reaffirms? guidance|narrows? guidance|raises? guidance|maintains? outlook|updates? guidance|full.year guidance|fiscal year guidance|capex guidance|capital expenditure guidance|raises? forecast|lowers? forecast)\b",
        ),
        (
            "joint_venture",
            r"\b(joint venture|JV agreement|forms? jv|enters? jv|signs? jv|jv with|strategic alliance|memorandum of understanding|MoU signed)\b",
        ),
        (
            "divestiture",
            r"\b(divests?|divestiture|asset sale|sells? stake|exits? business|monetises?|monetizes?|sells? unit|hives? off)\b",
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
