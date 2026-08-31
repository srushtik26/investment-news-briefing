"""
High-Confidence Single-Source Verification Evaluator.

Evaluates candidate single-source events against strict deterministic criteria:
1. Trusted publication registry (India & International).
2. Recognized hard-event business types (Earnings, M&A, Stakes, IPOs, Regulatory, Capex, etc.).
3. Concrete quantified facts and named subject entity.
4. Strict <= 24h publication age with verified timestamp.
5. Successfully extracted full article content.
6. Deterministic confidence scoring with threshold >= 80.
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.logging_config import get_logger
from app.models.article import Article, NewsCategory
from app.models.enums import VerificationTier
from app.models.event import Event
from config import get_settings

logger = get_logger("verification.single_source")

SINGLE_SOURCE_MIN_CONFIDENCE = 80.0

# Canonical trusted publisher registries
TRUSTED_INDIA_PUBLISHERS: Set[str] = {
    "economic times",
    "the economic times",
    "economictimes",
    "business standard",
    "bsindia",
    "@bsindia",
    "mint",
    "livemint",
    "financial express",
    "the financial express",
    "financialexpress",
    "moneycontrol",
    "moneycontrol.com",
    "business today",
    "businesstoday",
    "ndtv profit",
    "ndtv",
    "the hindu business line",
    "the hindu businessline",
    "hindu business line",
    "hindu businessline",
    "businessline",
    "reuters",
    "bloomberg",
    "pti",
    "press trust of india",
    "ani",
    "the hindu",
    "the indian express",
    "indian express",
    "times of india",
    "the times of india",
    "fortune india",
    "forbes india",
    "cnbc tv18",
    "cnbctv18",
    "zee business",
    "bse",
    "nse",
    "sebi",
    "rbi",
    "reserve bank of india",
    "press information bureau",
    "pib",
    "pib.gov.in",
    "rbi.org.in",
    "sebi.gov.in",
    "mospi.gov.in",
    "finmin.gov.in",
    "ministry of finance",
}

TRUSTED_INTL_PUBLISHERS: Set[str] = {
    "reuters",
    "bloomberg",
    "cnbc",
    "ap",
    "ap news",
    "associated press",
    "bbc",
    "bbc news",
    "marketwatch",
    "fortune",
    "forbes",
    "financial times",
    "ft",
    "wall street journal",
    "wsj",
    "the wall street journal",
    "the guardian",
    "guardian",
    "yahoo finance",
    "business insider",
    "barron's",
    "barrons",
    "investing.com",
    "seeking alpha",
    "pr newswire",
    "globenewswire",
    "business wire",
    "dow jones",
    "sec",
    "sec edgar",
    "federal reserve",
    "ecb",
    "bank of england",
}

# Allowed hard-event types
ALLOWED_HARD_EVENT_TYPES: Set[str] = {
    "EARNINGS",
    "M&A",
    "STAKE_TRANSACTION",
    "BLOCK_DEAL",
    "FUNDRAISING",
    "IPO",
    "DRHP",
    "LISTING",
    "REGULATORY_ACTION",
    "CAPEX",
    "MAJOR_CONTRACT",
    "JOINT_VENTURE",
    "LEADERSHIP_CHANGE",
    "QUANTIFIED_MACRO_EVENT",
}

# Negative topic / noise / roundup markers
MULTI_EVENT_ROUNDUP_PATTERNS = [
    r"\bmorning squawk\b",
    r"\b5 things to know\b",
    r"\bfive things to know\b",
    r"\bmarkets today\b",
    r"\bmarket wrap\b",
    r"\bdaily briefing\b",
    r"\btop business news\b",
    r"\bnews wrap\b",
    r"\bhere are the top\b",
    r"\blive updates\b",
    r"\bmarket live\b",
    r"\bstock market highlights\b",
    r"\bopening bell\b",
    r"\bclosing bell\b",
    r"\bthings you need to know\b",
]

COMMENTARY_OPINION_PATTERNS = [
    r"\bopinion\b",
    r"\beditorial\b",
    r"\bcolumn\b",
    r"\bcommentary\b",
    r"\banalysis:",
    r"\bview:",
    r"\bwhy we think\b",
    r"\bexpert take\b",
    r"\brumou?r\b",
    r"\bspeculat(e|ion|ing)\b",
    r"\b(one analyst (?:now )?thinks|analysts? thinks?|analysts? says?.*?\bcould\b|could hit|could reach|heading for|price target|wall street thinks|analysts? predicts?|analysts? expects? shares could|could become|is .* heading for)\b",
    r"\b(stocks? to buy|best stocks? to buy|top stocks?|top stock picks?|stock picks?|dividend stocks?|portfolio boost|portfolio picks?|analyst recommends?|buy these stocks?|stocks? analysts? love|stocks? poised to rise|investment ideas?|portfolio ideas?|stocks? could give your portfolio a boost|these .* stocks could)\b",
    r"\b(in (?:early |advanced )?talks (?:to|with|for)|in discussions (?:to|with|for)|eyeing (?:a )?(?:controlling )?stake|eyes? (?:a )?(?:controlling )?stake|mulls? (?:stake|acquisition|buying|sale)|exploring (?:acquisition|sale|buying|options|stake)|explores? (?:sale|acquisition|buying|stake)|report says talks|seeking to buy|weighs (?:bid|acquisition|sale|buying)|in talks to buy|in talks to acquire)\b",
    r"\bstock(s)? to buy\b",
    r"\btarget price\b",
    r"\bbrokerage call\b",
    r"\bshare price today\b",
    r"\b(soars?|surges?|jumps?|plunges?|rallies|rally|climbs?|slides?|slumps?)\b.*?\b(hopes? of|speculation of|amid crypto rally|bets on|expectation of)\b",
    r"\bhopes? of (?:an? )?etf approval\b",
    r"\bhopes? of (?:an? )?approval\b",
    r"\bsoars to .*? (?:high|low) amid crypto rally\b",
]


PERSONAL_PROFILE_PATTERNS = [
    r"\bbillionaire\b",
    r"\bnet worth\b",
    r"\bco-founder is worth\b",
    r"\bis worth\b",
    r"\bpersonal fortune\b",
    r"\bturned down .*job offers\b",
    r"\bcareer story\b",
    r"\bentrepreneur journey\b",
    r"\bsuccess story\b",
    r"\bhow (?:he|she|they) became\b",
    r"\bwealth profile\b",
    r"\bpersonal finance profile\b",
    r"\blifestyle feature\b",
    r"\bmeet the founder\b",
]


def is_valid_named_company_entity(comp: Optional[str]) -> bool:
    """Validate that entity is a legitimate company name and not a pure number or generic token."""
    if not comp:
        return False
    c = comp.strip()
    c_low = c.lower()
    if len(c) < 2 or len(c) > 60:
        return False
    # Must contain at least one alphabetic character
    if not re.search(r"[a-zA-Z]", c):
        return False
    # Generic stopwords / noise words
    noise_tokens = {
        "unspecified", "company", "companies", "india", "us", "u.s.", "usa", "global",
        "firm", "corp", "inc", "ltd", "block deals", "block deal", "bulk deals", "bulk deal", "block", "bulk",
        "quarterly results", "annual results", "financial results", "ai", "wall street",
        "lost", "today", "shares", "stock", "stocks", "markets", "market", "deal", "deals", "stake",
        "report", "news"
    }
    if c_low in noise_tokens:
        return False
    # Reject pure numbers: "20", "31", "2026", "1.7", "75"
    if re.fullmatch(r"[\d,\.]+", c_low):
        return False
    # Reject explicit currency values: "$100M", "₹500 crore", "$1.7B"
    if re.fullmatch(r"[\$₹€£]\s*[\d,]+(\.\d+)?\s*(crore|cr|billion|million|trillion|lakh|b|m|k|%)?", c_low):
        return False
    # Reject standalone numeric unit values: "500 crore", "100 million", "25%"
    if re.fullmatch(r"[\d,]+(\.\d+)?\s*(crore|cr|billion|million|trillion|lakh|percent|%)", c_low):
        return False
    return True


def is_personal_profile_or_wealth_feature(title: str, text: Optional[str] = None) -> bool:
    """Detect personal wealth profiles, billionaire features, career stories."""
    comb = f"{title} {(text or '')[:300]}".lower()
    for pat in PERSONAL_PROFILE_PATTERNS:
        if re.search(pat, comb):
            return True
    return False


def is_multi_event_roundup(title: str) -> bool:
    """Detect multi-topic roundup or market squawk headlines."""
    t_low = title.lower()
    for pat in MULTI_EVENT_ROUNDUP_PATTERNS:
        if re.search(pat, t_low):
            return True
    # Detect comma-separated / multi-company combined headlines e.g. "Treasury buyback, Walmart earnings, Boise..."
    # Note: (?<!\d),(?!\d) ensures numeric thousands separators (e.g. 1,417 or 2,888) are not split
    parts = [p.strip() for p in re.split(r"(?<!\d),(?!\d)\s*|[;\|]\s*", title) if len(p.strip()) > 5]
    if len(parts) >= 3 and any("earnings" in p.lower() or "results" in p.lower() or "deal" in p.lower() for p in parts):
        return True
    return False


def is_commentary_or_rumour(title: str, text: Optional[str] = None) -> bool:
    """Detect commentary, opinion, rumours, or broker advice."""
    comb = (title + " " + (text[:300] if text else "")).lower()
    has_completed_hard_event = bool(re.search(
        r"\b(block deal|bulk deal|equity changes hands|net profit|revenue rises|revenue jumps|revenue falls|profit rises|profit falls|q[1-4] profit|q[1-4] net profit|earnings beat|earnings miss|beats? (?:quarterly |q[1-4] |earnings |wall street )?estimates|hikes? (?:its )?(?:full.year )?outlook|agrees to buy|signed definitive agreement|all-cash deal|nclt scheme|bags (?:mega )?order|secures contract|issues bonds|files for ipo|share buyback|dividend|quarterly results|annual results)\b",
        comb,
        re.IGNORECASE,
    ))

    for pat in COMMENTARY_OPINION_PATTERNS:
        if re.search(pat, comb):
            if has_completed_hard_event and any(spec in pat for spec in ("talks", "speculat", "likely", "rumou")):
                continue
            return True
    return False


def is_trusted_publisher(source_name: Optional[str], region: NewsCategory = NewsCategory.INDIA) -> bool:
    """Check if publisher is in the approved high-confidence registry or an official corporate release/wire."""
    if not source_name:
        return False
    norm = source_name.strip().lower()
    # Normalize common variations
    norm = re.sub(r"^(the\s+)", "", norm)
    if any(tp in norm or norm in tp for tp in TRUSTED_INDIA_PUBLISHERS):
        return True
    if any(tp in norm or norm in tp for tp in TRUSTED_INTL_PUBLISHERS):
        return True
    # Official corporate release / wire / IR recognition
    if any(wire in norm for wire in ("prnewswire", "globenewswire", "businesswire", "pr newswire", "globe newswire", "business wire", "investor relations", "press release")):
        return True
    return False


class SingleSourceEvaluator:
    """
    Deterministic Evaluator for High-Confidence Single-Source fallback stories.
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    def evaluate_event(
        self,
        event: Event,
        primary_article: Article,
        now_utc: Optional[datetime] = None,
        max_age_hours: Optional[float] = None,
    ) -> Tuple[bool, float, str]:
        """
        Evaluate if a single-source event achieves HIGH_CONFIDENCE_SINGLE_SOURCE tier.

        Returns:
            (is_eligible: bool, confidence_score: float, reason: str)
        """
        current_time = now_utc or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        title = primary_article.title or event.canonical_title or ""
        body = primary_article.content_text or ""
        source = primary_article.source_name or ""

        # First-party primary sources require independent approved corroboration
        if (primary_article.metadata or {}).get("source_class") == "FIRST_PARTY_PRIMARY":
            return False, 0.0, "REJECT: First-party primary source requires independent corroboration"

        # 1. Date Freshness Rule D: strictly <= max_age (default: 24h, or active fallback horizon)
        if not primary_article.published_at:
            return False, 0.0, "REJECT: Missing publication timestamp"

        pub_time = primary_article.published_at
        if pub_time.tzinfo is None:
            pub_time = pub_time.replace(tzinfo=timezone.utc)

        # Check explicit structured event date in title
        title_date_match = re.search(
            r"(?:Quarterly\s+Results|Financial\s+Results|Annual\s+Results|Results|Board\s+Meeting)[,\s\-]+(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\b",
            title,
            re.IGNORECASE,
        )
        max_age = max_age_hours if max_age_hours is not None else float(getattr(self.settings, "STORY_FRESHNESS_HOURS", 24.0))
        if title_date_match:
            date_str = title_date_match.group(1).strip()
            try:
                for m_full, m_short in [
                    ("January", "Jan"), ("February", "Feb"), ("March", "Mar"), ("April", "Apr"),
                    ("August", "Aug"), ("September", "Sep"), ("October", "Oct"), ("November", "Nov"), ("December", "Dec"),
                ]:
                    date_str = re.sub(rf"\b{m_full}\b", m_short, date_str, flags=re.IGNORECASE)
                parsed_title_dt = datetime.strptime(date_str, "%d %b %Y").replace(tzinfo=timezone.utc)
                explicit_age_hours = (current_time - parsed_title_dt).total_seconds() / 3600.0
                if explicit_age_hours > max_age:
                    return False, 0.0, f"REJECT: Stale explicit event date '{date_str}' ({explicit_age_hours:.1f}h > {max_age:.0f}h)"
            except Exception:
                pass

        age_hours = max(0.0, (current_time - pub_time).total_seconds() / 3600.0)
        if age_hours > max_age:
            return False, 0.0, f"REJECT: Stale publication date ({age_hours:.1f}h > {max_age:.0f}h)"

        # 2. Rule E: Must be successfully extracted
        if not body or len(body.split()) < 30:
            return False, 0.0, f"REJECT: Insufficient article extraction body ({len(body.split())} words)"

        # 3. Rule B: Reject Multi-event roundups & commentary/rumours & personal profiles
        if is_multi_event_roundup(title):
            return False, 0.0, "REJECT: Multi-event roundup / morning squawk"

        if is_commentary_or_rumour(title, body):
            return False, 0.0, "REJECT: Commentary, opinion, rumour, or share price advice"

        if is_personal_profile_or_wealth_feature(title, body):
            return False, 0.0, "REJECT: Personal profile / wealth feature (not a corporate hard event)"

        # 4. Deterministic Score Calculation
        score = 0.0
        reasons: List[str] = []

        # A. Trusted publication (+25)
        is_trusted = is_trusted_publisher(source, event.event_category)
        if is_trusted:
            score += 25.0
            reasons.append("trusted_pub(+25)")
        else:
            return False, 0.0, f"REJECT: Untrusted publisher for single-source '{source}'"

        # B. Clear named company/entity (+15, or -30 if missing)
        clean_companies = [c for c in event.companies_involved if is_valid_named_company_entity(c)]
        if not clean_companies:
            from app.verification.query_builder import EventQueryBuilder
            from app.models.entity_sanitizer import sanitize_company_entities
            extracted = EventQueryBuilder.extract_entities(primary_article)
            clean_companies = [c for c in sanitize_company_entities(extracted, publisher=source) if is_valid_named_company_entity(c)]
            if clean_companies:
                event.companies_involved = clean_companies

        if clean_companies:
            score += 15.0
            reasons.append(f"entity({clean_companies[0]})(+15)")
        else:
            score -= 30.0
            reasons.append("weak_entity(-30)")

        # C. Recognized hard event type (+20)
        has_hard_action = bool(re.search(
            r"\b(acquires?|acquisition|to buy|to acquire|acquisition of|buys|bought|sold|sale|sells|sell|to sell|offload|pares|stake|merger|takeover|buyout|block deal|stake sale|stake purchase|divests|calls off|terminates|net profit|earnings|financial results|results|revenue|ebitda|q[1-4]|ipo|drhp|funding|financing|raises|guidance|buyback|repurchase|dividend|investment|invests?|to invest|commits|penalty|fine|order|capex|contract|joint venture|appointed|resigns|antitrust|launches?|launch|"
            r"monetary policy|repo rate|reverse repo|mpc|interest rate decision|inflation|cpi|wpi|gdp|iip|industrial production|trade deficit|gst council|tax rate|customs duty|import duty|export duty|pli scheme|semiconductor incentive|national mission|cabinet approves|centre approves|government approves|fdi policy|banking regulation|nbfc regulation|liquidity framework|crr|slr)\b",
            title.lower() + " " + body[:300].lower()
        ))
        if has_hard_action:
            score += 20.0
            reasons.append("hard_event_type(+20)")
        else:
            return False, 0.0, "REJECT: No recognized hard business event action"

        # D. Concrete amount/percentage facts (+15)
        has_concrete_metric = bool(
            event.financial_figures
            or re.search(r"\b(\d+(\.\d+)?\s*(crore|cr|billion|million|\$|₹|%|rs\.?|bps|basis points))\b", (title + " " + body[:400]).lower())
        )
        if has_concrete_metric:
            score += 15.0
            reasons.append("concrete_facts(+15)")
        else:
            # Leadership change, regulatory action, policy actions, or explicit M&A / strategic actions may pass if explicit
            if not re.search(r"\b(appointed|resigns|named ceo|named md|penalty|order|probe|to buy|to acquire|acquisition of|acquires|acquired|merger|buyout|calls off|terminates|block deal|stake sale|monetary policy|repo rate|inflation|cpi|gdp|iip|gst council|cabinet approves|pli scheme)\b", title.lower()):
                return False, 0.0, "REJECT: Missing concrete quantified facts or key metrics"

        # E. Verified publication timestamp (+10, or -20 if unverified)
        if getattr(primary_article, "date_verified", True):
            score += 10.0
            reasons.append("verified_date(+10)")
        else:
            score -= 20.0
            reasons.append("unverified_date(-20)")

        # F. Strong structured metadata (+5)
        if primary_article.metadata and len(primary_article.metadata) > 2:
            score += 5.0
            reasons.append("metadata(+5)")

        # G. Direct article URL (+5)
        if not primary_article.url.startswith("https://news.google.com"):
            score += 5.0
            reasons.append("direct_url(+5)")

        # H. Article body sufficient (+5)
        if len(body.split()) >= 100:
            score += 5.0
            reasons.append("sufficient_body(+5)")

        score = max(0.0, min(100.0, score))
        is_eligible = (score >= SINGLE_SOURCE_MIN_CONFIDENCE)

        reason_str = f"Score={score:.0f}/100 [{', '.join(reasons)}]"
        return is_eligible, score, reason_str
