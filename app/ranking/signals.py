"""
Deterministic Institutional Editorial Signals.

Encapsulates approved Investment Committee briefing patterns and negative low-priority
patterns as reusable deterministic pattern signals for candidate ranking and selection.

DO NOT use embedding or RAG.
DO NOT add AI calls.
DO NOT hardcode article URLs as required stories.
Source quality remains separate from editorial pattern signals.
"""

from dataclasses import dataclass, field
import re
from typing import Dict, List, Optional, Tuple

from app.models.article import Article
from app.models.event import Event


@dataclass
class EditorialPatternSignal:
    name: str
    category: str  # 'positive_india', 'positive_intl', 'negative'
    weight: float
    description: str


@dataclass
class EditorialEvaluationResult:
    score_adjustment: float
    positive_signals: List[str] = field(default_factory=list)
    negative_signals: List[str] = field(default_factory=list)
    matched_patterns: List[str] = field(default_factory=list)
    is_disqualified: bool = False
    rejection_reason: Optional[str] = None


# =============================================================================
# Approved Investment Committee Editorial Patterns
# =============================================================================

# INDIA PATTERNS
PATTERN_INDIA_JV_MA = re.compile(
    r"\b(?:joint venture|jv\b|mou\b|non-binding mou|merger|acquisition|acquires?|buyout|takeover|"
    r"stake (?:sale|purchase|buy|acquisition)|buys? (?:a )?\d+% stake|sells? (?:a )?\d+% stake|"
    r"all-cash deal|definitive agreement|concession pact|jsw.*skoda|skoda.*jsw)\b",
    re.IGNORECASE,
)

PATTERN_INDIA_FPI_FLOWS = re.compile(
    r"\b(?:fpis?\b|fiis?\b|foreign portfolio investor|foreign institutional investor|foreign flow|"
    r"fdi\b|foreign capital|fpi selling|fpi buying|foreign investors? (?:sell|buy|dump|pump|offload)|"
    r"sustained foreign selling|outflows? of \$|inflows? of \$)\b",
    re.IGNORECASE,
)

PATTERN_INDIA_REGULATORY_FRAMEWORK = re.compile(
    r"\b(?:regulatory framework|policy framework|gazette notification|guidelines for|"
    r"cabinet approves|cabinet clears|union cabinet|ministry of finance|ministry of commerce|"
    r"rbi\b|reserve bank of india|sebi\b|securities and exchange board|cci\b|competition commission|"
    r"antitrust authority|monetary policy|repo rate|sovereign bond calendar|nclt\b|supreme court delivers judgment)\b",
    re.IGNORECASE,
)

PATTERN_INDIA_ENERGY_COAL_POLICY = re.compile(
    r"\b(?:energy policy|coal policy|coal allocation|commercial coal|captive coal|crude oil|"
    r"natural gas pricing|power grid|renewable energy|green hydrogen mission|solar capacity|"
    r"power generation tariff|refinery expansion|lng terminal|lithium reserve|critical minerals?)\b",
    re.IGNORECASE,
)

PATTERN_INDIA_STRATEGIC_PHARMA_EXPANSION = re.compile(
    r"\b(?:pharma.*(?:expansion|facility|acquisition|usfda|approval)|biologics facility|"
    r"active pharmaceutical ingredient|api manufacturing|formulations plant|clinical pipeline|"
    r"vaccine facility|strategic business expansion|commercial expansion across)\b",
    re.IGNORECASE,
)

PATTERN_INDIA_LARGE_CAPEX = re.compile(
    r"\b(?:capex|capital expenditure|greenfield (?:plant|facility|project)|brownfield expansion|"
    r"gigafactory|manufacturing plant|new facility|invests? ₹\s*[\d,]+.*crore|investment to build|"
    r"capacity expansion to \d+|semiconductor fab|assembly plant)\b",
    re.IGNORECASE,
)

PATTERN_INDIA_INFRASTRUCTURE = re.compile(
    r"\b(?:expressway|freight corridor|high-speed rail|bullet train|deepwater port|container terminal|"
    r"airport terminal|metro corridor|highway project|nhai\b|transmission network|power transmission corridor|"
    r"infrastructure investment|dedicated freight)\b",
    re.IGNORECASE,
)

PATTERN_INDIA_LAYOFFS_RESTRUCTURING = re.compile(
    r"\b(?:cuts? \d+ jobs|slashes workforce|workforce reduction of \d+|layoffs?|demerger plan|"
    r"corporate restructuring|splits into \d+|spinoff|business reorganization|severance program)\b",
    re.IGNORECASE,
)

PATTERN_INDIA_BANKING_POLICY = re.compile(
    r"\b(?:banking policy|prompt corrective action|pca framework|priority sector lending|psl norms|"
    r"rbi norms|bad loan resolution|npa resolution|insolvency and bankruptcy|ibc framework|"
    r"deposit insurance|bank privatization|statutory liquidity ratio|slr\b|crr\b|capital adequacy)\b",
    re.IGNORECASE,
)

PATTERN_INDIA_SATELLITE_TELECOM = re.compile(
    r"\b(?:satellite spectrum|spectrum allocation|satellite communication|satcom\b|dcc\b|"
    r"digital communications commission|department of telecommunications|dot\b|trai\b|"
    r"telecom reforms?|spectrum auction|5g rollout|6g mission|broadband allocation)\b",
    re.IGNORECASE,
)


# INTERNATIONAL PATTERNS
PATTERN_INTL_CHIP_AI = re.compile(
    r"\b(?:chip deal|semiconductor fab|foundry deal|advanced packaging|nvidia|tsmc|intel|amd|"
    r"qualcomm|broadcom|arm holdings|ai computing|ai chip|datacenter buildout|hyperscale data center|"
    r"generative ai model|ai infrastructure|supercomputing cluster)\b",
    re.IGNORECASE,
)

PATTERN_INTL_TRADE_RESTRICTIONS = re.compile(
    r"\b(?:tariff|customs duty|import duty|export restriction|export curb|anti-dumping duty|"
    r"trade pact|free trade agreement|fta\b|trade dispute|wto ruling|trade barrier|sanctions?|"
    r"entity list|trade embargo)\b",
    re.IGNORECASE,
)

PATTERN_INTL_ENERGY_COMMODITY = re.compile(
    r"\b(?:petroleum reserves?|strategic petroleum reserve|spr\b|opec|opec\+|crude output|"
    r"brent crude|natural gas pipeline|lng shipment|commodity reserves?|uranium reserve|"
    r"critical mineral supply|rare earth export)\b",
    re.IGNORECASE,
)

PATTERN_INTL_REGULATORY_CONFLICT = re.compile(
    r"\b(?:antitrust probe|regulatory conflict|doj lawsuit|ftc lawsuit|european commission fine|"
    r"regulatory fine of \$|monopoly probe|cross-border regulatory clash|sec probe|cfius review)\b",
    re.IGNORECASE,
)

PATTERN_INTL_BILATERAL_TREATIES = re.compile(
    r"\b(?:bilateral investment treaty|bit\b|bilateral trade pact|intergovernmental agreement|"
    r"trade accord|defense industrial pact|cross-border economic partnership)\b",
    re.IGNORECASE,
)

PATTERN_INTL_CAPITAL_RAISES = re.compile(
    r"\b(?:raises? \$[1-9]\d* (?:billion|million)|capital raise of \$|sovereign wealth fund investment|"
    r"mega equity issue|secures \$[\d.]+\s*billion in funding|debt restructuring of \$)\b",
    re.IGNORECASE,
)

PATTERN_INTL_GEOPOLITICAL_BUSINESS = re.compile(
    r"\b(?:strait of hormuz|red sea shipping|suez canal|maritime corridor|chokepoint disruption|"
    r"supply chain embargo|geopolitical conflict impacts? (?:supply|shipping|energy|markets)|"
    r"war risk insurance|trade blockade)\b",
    re.IGNORECASE,
)


# BAD / LOW-PRIORITY PATTERNS
PATTERN_BAD_ROUTINE_RESULTS = re.compile(
    r"\b(?:quarterly results?|q[1-4] results?|unaudited financial results|financial results for the quarter|"
    r"standalone results|consolidated results|q[1-4] profit (?:up|down|rises|falls) \d+%)"
    r"(?:.*?(?:bse\s*[:\-]?\s*\d+\.\d+|nse\s*[:\-]?\s*\d+\.\d+|bse\s+\d{3,}|share price\s*:\s*\d+))?",
    re.IGNORECASE,
)

PATTERN_BAD_IPO_GMP = re.compile(
    r"\b(?:ipo gmp|grey market premium|gmp today|ipo grey market|gmp climbs|gmp slips|gmp indicates)\b",
    re.IGNORECASE,
)

PATTERN_BAD_STOCKS_TO_WATCH = re.compile(
    r"\b(?:stocks? to watch(?: today)?|stocks? in focus(?: today)?|buzzing stocks?|top stocks? to buy|"
    r"\d+\s+stocks? to (?:watch|buy|track)|stocks? in the news|trade setup for today|hot stocks? to buy)\b",
    re.IGNORECASE,
)

PATTERN_BAD_GENERIC_PRICE = re.compile(
    r"\b(?:shares? (?:jump|fall|surge|dip|rise|drop|tumble|tank|rally|gain|slide)s? (?:up to )?\d+%(?: in morning trade| today)?|"
    r"52-week (?:high|low)|stock price today|share price today|why (?:[\w\s]+) shares are (?:rising|falling)|"
    r"stock plunges \d+%|upper circuit|lower circuit)\b",
    re.IGNORECASE,
)

PATTERN_BAD_BROKER_TARGET = re.compile(
    r"\b(?:target price|price target|brokerage (?:recommends|retains|maintains|gives|cuts|hikes|initiates)|"
    r"(?:recommends|maintains|gives)\s+(?:buy|sell|hold|overweight|underweight|accumulate)|"
    r"analysts? (?:see|sees|maintain|recommend|give)|target rs\b|target ₹|should you buy|buy, sell or hold|"
    r"top stock picks?)\b",
    re.IGNORECASE,
)

PATTERN_BAD_MINOR_DISCLOSURE = re.compile(
    r"\b(?:regulation 30|regulation 33|trading window closure|closure of trading window|"
    r"loss of share certificates?|duplicate share certificates?|investor presentation uploaded|"
    r"scrutinizer report|secretarial audit|compliance certificate under|board meeting intimation|"
    r"general meeting notice|egm notice|audio recording of earnings call)\b",
    re.IGNORECASE,
)


def evaluate_editorial_signals(
    event: Event,
    article: Optional[Article] = None,
) -> EditorialEvaluationResult:
    """
    Evaluate deterministic investment committee editorial category signals on an event.

    Returns:
        EditorialEvaluationResult containing:
        - score_adjustment: additive bonus or penalty (-50.0 to +30.0)
        - positive_signals: list of matched approved investment committee patterns
        - negative_signals: list of matched bad/low-priority patterns
        - is_disqualified: boolean indicating if event should be discarded
        - rejection_reason: diagnostic message if disqualified
    """
    title = (getattr(event, "canonical_title", "") or (article.title if article else "")).strip()
    body = (getattr(event, "description", "") or (article.content_text if article else "")).strip()
    full_text = f"{title} {body[:1500]}".strip()
    title_low = title.lower()

    positive_signals: List[str] = []
    negative_signals: List[str] = []
    matched_patterns: List[str] = []
    pos_score = 0.0
    neg_score = 0.0

    # -------------------------------------------------------------------------
    # Positive India Patterns (+15 to +25)
    # -------------------------------------------------------------------------
    if PATTERN_INDIA_JV_MA.search(full_text):
        pos_score += 25.0
        positive_signals.append("approved_india_jv_ma(+25)")
        matched_patterns.append("major JV / M&A")

    if PATTERN_INDIA_FPI_FLOWS.search(full_text):
        pos_score += 25.0
        positive_signals.append("approved_india_fpi_flows(+25)")
        matched_patterns.append("FPI flows")

    if PATTERN_INDIA_REGULATORY_FRAMEWORK.search(full_text):
        pos_score += 20.0
        positive_signals.append("approved_india_regulatory_framework(+20)")
        matched_patterns.append("government regulatory framework")

    if PATTERN_INDIA_ENERGY_COAL_POLICY.search(full_text):
        pos_score += 20.0
        positive_signals.append("approved_india_energy_coal_policy(+20)")
        matched_patterns.append("energy/coal policy")

    if PATTERN_INDIA_STRATEGIC_PHARMA_EXPANSION.search(full_text):
        pos_score += 15.0
        positive_signals.append("approved_india_pharma_expansion(+15)")
        matched_patterns.append("strategic pharma/business expansion")

    if PATTERN_INDIA_LARGE_CAPEX.search(full_text):
        pos_score += 20.0
        positive_signals.append("approved_india_large_capex(+20)")
        matched_patterns.append("large corporate capex")

    if PATTERN_INDIA_INFRASTRUCTURE.search(full_text):
        pos_score += 15.0
        positive_signals.append("approved_india_infrastructure(+15)")
        matched_patterns.append("infrastructure investment")

    if PATTERN_INDIA_LAYOFFS_RESTRUCTURING.search(full_text):
        pos_score += 15.0
        positive_signals.append("approved_india_restructuring(+15)")
        matched_patterns.append("material layoffs/restructuring")

    if PATTERN_INDIA_BANKING_POLICY.search(full_text):
        pos_score += 20.0
        positive_signals.append("approved_india_banking_policy(+20)")
        matched_patterns.append("major banking policy")

    if PATTERN_INDIA_SATELLITE_TELECOM.search(full_text):
        pos_score += 20.0
        positive_signals.append("approved_india_satellite_telecom(+20)")
        matched_patterns.append("strategic satellite/telecom developments")

    # -------------------------------------------------------------------------
    # Positive International Patterns (+15 to +25)
    # -------------------------------------------------------------------------
    if PATTERN_INTL_CHIP_AI.search(full_text):
        pos_score += 25.0
        positive_signals.append("approved_intl_chip_ai(+25)")
        matched_patterns.append("major chip/AI deals")

    if PATTERN_INTL_TRADE_RESTRICTIONS.search(full_text):
        pos_score += 20.0
        positive_signals.append("approved_intl_trade_restrictions(+20)")
        matched_patterns.append("major tariffs/trade restrictions")

    if PATTERN_INTL_ENERGY_COMMODITY.search(full_text):
        pos_score += 20.0
        positive_signals.append("approved_intl_energy_commodity(+20)")
        matched_patterns.append("strategic petroleum/energy shifts")

    if PATTERN_INTL_REGULATORY_CONFLICT.search(full_text):
        pos_score += 20.0
        positive_signals.append("approved_intl_regulatory_conflict(+20)")
        matched_patterns.append("major regulatory conflict")

    if PATTERN_INTL_BILATERAL_TREATIES.search(full_text):
        pos_score += 15.0
        positive_signals.append("approved_intl_bilateral_treaties(+15)")
        matched_patterns.append("bilateral investment treaties")

    if PATTERN_INTL_CAPITAL_RAISES.search(full_text):
        pos_score += 20.0
        positive_signals.append("approved_intl_capital_raises(+20)")
        matched_patterns.append("large equity/capital raises")

    if PATTERN_INTL_GEOPOLITICAL_BUSINESS.search(full_text):
        pos_score += 20.0
        positive_signals.append("approved_intl_geopolitical_business(+20)")
        matched_patterns.append("major geopolitical events with direct business impact")

    # -------------------------------------------------------------------------
    # Negative Low-Priority Patterns (-25 to -45)
    # -------------------------------------------------------------------------
    is_mega_earnings = bool(
        re.search(r"\b(?:surges|record profit|historic profit|blowout earnings)\b", full_text, re.IGNORECASE)
        or re.search(r"₹\s*([1-9]\d{4,}|\d{5,})\s*crore", full_text, re.IGNORECASE)
        or re.search(r"\$\s*([1-9]\d*)\s*billion", full_text, re.IGNORECASE)
    )

    is_routine_result = not is_mega_earnings and bool(PATTERN_BAD_ROUTINE_RESULTS.search(full_text))
    is_ipo_gmp = bool(PATTERN_BAD_IPO_GMP.search(title_low))
    is_stocks_watch = bool(PATTERN_BAD_STOCKS_TO_WATCH.search(title_low))
    is_generic_price = bool(PATTERN_BAD_GENERIC_PRICE.search(title_low))
    is_broker_target = bool(PATTERN_BAD_BROKER_TARGET.search(title_low))
    is_minor_disc = bool(PATTERN_BAD_MINOR_DISCLOSURE.search(full_text))

    if is_routine_result:
        neg_score += 40.0
        negative_signals.append("bad_routine_small_company_quarterly_results(-40)")
        matched_patterns.append("routine small-company quarterly results")

    if is_ipo_gmp:
        neg_score += 45.0
        negative_signals.append("bad_ipo_gmp(-45)")
        matched_patterns.append("IPO GMP articles")

    if is_stocks_watch:
        neg_score += 40.0
        negative_signals.append("bad_stocks_to_watch(-40)")
        matched_patterns.append("stocks-to-watch")

    if is_generic_price:
        neg_score += 35.0
        negative_signals.append("bad_generic_price_movement(-35)")
        matched_patterns.append("generic price-movement stories")

    if is_broker_target:
        neg_score += 35.0
        negative_signals.append("bad_broker_target_recommendation(-35)")
        matched_patterns.append("broker target/recommendation")

    if is_minor_disc:
        neg_score += 30.0
        negative_signals.append("bad_minor_corporate_disclosure(-30)")
        matched_patterns.append("minor company announcements")

    # Hard disqualification check for IPO GMP and broker recommendation lists
    is_disqualified = False
    rejection_reason = None
    if is_ipo_gmp:
        is_disqualified = True
        rejection_reason = "Rejected: IPO GMP speculation article is not institutional investment material."
    elif is_stocks_watch:
        is_disqualified = True
        rejection_reason = "Rejected: Generic stocks-to-watch listicle is not institutional investment material."
    elif is_broker_target and not positive_signals:
        is_disqualified = True
        rejection_reason = "Rejected: Broker target/recommendation piece is not institutional investment material."
    elif is_routine_result and not positive_signals and re.search(r"(?:bse\s*[\d\.]+|bse\s+\d{3,})", title_low):
        is_disqualified = True
        rejection_reason = "Rejected: Generic periodic BSE quarterly results ticker without strategic deal or magnitude."

    # Cap positive adjustment at +30.0 and negative at -50.0
    capped_pos = min(30.0, pos_score)
    capped_neg = min(50.0, neg_score)
    net_adjustment = round(capped_pos - capped_neg, 2)

    return EditorialEvaluationResult(
        score_adjustment=net_adjustment,
        positive_signals=positive_signals,
        negative_signals=negative_signals,
        matched_patterns=matched_patterns,
        is_disqualified=is_disqualified,
        rejection_reason=rejection_reason,
    )
