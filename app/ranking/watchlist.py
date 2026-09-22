"""
Portfolio Watchlist: 32 tracked equity holdings for Plutus Wealth Management.

Centralized matching engine with canonical company naming, pre-compiled regexes,
role extraction, and priority qualification.

Provides:
    PORTFOLIO_WATCHLIST         - list of (canonical_name, compiled_regex) for each holding.
    PortfolioMatch              - dataclass for structured portfolio match result.
    match_portfolio_company     - canonical helper returning Optional[PortfolioMatch].
    is_watchlist_company        - legacy helper returning (matched: bool, company_name: str).
    get_watchlist_match_details - legacy helper returning (matched, company_name, matched_alias).
    get_portfolio_company_role  - returns (matched, company_name, role, eligible_for_priority).
"""

from dataclasses import dataclass
import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Canonical Watchlist Definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PortfolioMatch:
    """Canonical structured representation of a portfolio holding match."""
    canonical_name: str
    matched_alias: str
    confidence: float
    match_reason: str
    role: str = "primary_subject"
    eligible_for_priority: bool = True


# ---------------------------------------------------------------------------
# (canonical_name, regex_pattern) pairs.
# Patterns cover: full name, common short names, NSE ticker where unambiguous.
# ---------------------------------------------------------------------------
_RAW: List[Tuple[str, str]] = [
    ("Accelya Solutions India",      r"\baccelya\b"),
    ("BASF India",                   r"\bbasf india\b"),
    ("Bharat Electronics Ltd",       r"\bbharat electronics(?: ltd| limited)?\b|\bbel\b(?=\s+(?:ltd|limited|shares|stocks?|radar|defence|contract|order|q[1-4]|results|dividend|secures|bags|wins)\b)|\bbel\b(?=.*?\b(?:defence|radar|electronics|avionics|army|navy|air force|mod|psu|ordnance|naval)\b)"),
    ("Bharat Heavy Electricals Ltd", r"\bbharat heavy electricals(?: ltd| limited)?\b|\bbhel\b"),
    ("Bombay Dyeing",                r"\bbombay dyeing\b"),
    ("Cartrade Tech",                r"\bcartrade\b"),
    ("Choice International",         r"\bchoice international\b"),
    ("Coal India",                   r"\bcoal india\b|\bcil\b(?=\s+(?:ltd|limited|shares|stocks?|coal|dividend|q[1-4]|results|output|production|declares|announces)\b)|\bcil\b(?=.*?\b(?:coal|mining|e-auction|thermal|pithead)\b)"),
    ("Cohance Lifesciences",         r"\bcohance\b"),
    ("Eternal Ltd",                  r"\beternal ltd\b|\beternal limited\b"),
    ("Granules India",               r"\bgranules india\b"),
    ("Harsha Engineers",             r"\bharsha engineers\b"),
    ("Infosys",                      r"\binfosys\b"),
    ("Kaya Ltd",                     r"\bkaya ltd\b|\bkaya limited\b|\bkaya clinic\b"),
    ("Nazara Technologies",          r"\bnazara\b"),
    ("NTPC",                         r"\bntpc\b"),
    ("Rategain Travel Technologies", r"\brategain\b"),
    ("State Bank of India",          r"\bstate bank of india\b|\bsbi\b"),
    ("Steel Authority of India Ltd", r"\bsteel authority of india(?: ltd| limited)?\b|\bsail\b(?=\s+(?:ltd|limited)\b)|\bSAIL\b(?=\s+(?:signs|bags|secures|wins|orders?|contracts?|shares|stocks?|plant|capex|profit|revenue|dividend|expansion|mou|results|q[1-4]|posts|reports)\b)|\bSAIL\b(?=.*?\b(?:steel|railway|blast furnace|bokaro|bhilai|rourkela|durgapur|rail supply)\b)|\bsail\b(?=.*?\b(?:steel|blast furnace|bokaro|bhilai|rourkela|durgapur|rail supply)\b)"),
    ("Sumitomo Chemical India",      r"\bsumitomo chemical india\b"),
    ("Tata Consumer Products",       r"\btata consumer\b"),
    ("Tega Industries",              r"\btega industries\b"),
    ("Tilaknagar Industries",        r"\btilaknagar\b"),
    ("Titan Company",                r"\btitan company\b|\btitan co\b|\btitan(?!\s+(?:of|is|was|has|have|had|will|would|could|should|may|might))\b"),
    ("Trent Ltd",                    r"\btrent ltd\b|\btrent limited\b|\btrent\b(?!.*(?:trend|trending|trenton))"),
    ("Vedanta",                      r"\bvedanta\b"),
    ("Windsor Machines",             r"\bwindsor machines\b"),
    ("Ashok Leyland",                r"\bashok leyland(?: ltd| limited)?\b"),
    ("Adani Group",                  r"\badani group\b|\badani enterprises\b|\badani ports\b|\badani power\b|\badani energy solutions\b|\badani green energy\b|\badani total gas\b|\bambuja cements?\b|\bacc\b(?=\s+(?:ltd|limited|cements?|shares|stocks?|plant|facility|unit|grinding|results|q[1-4]|earnings|pat|ebitda)\b)|\bacc\b(?=.*?\b(?:cement|ambuja|adani|clinker|grinding unit)\b)"),
    ("Britannia Industries Ltd",     r"\bbritannia(?:\s+industries(?: ltd| limited)?)?\b"),
    ("Sun Pharmaceutical Industries Ltd", r"\bsun pharmaceutical industries(?: ltd| limited)?\b|\bsun pharma\b"),
    ("Havells India Ltd",            r"\bhavells(?:\s+india(?: ltd| limited)?)?\b"),
]

# Pre-compile company patterns once at module load time (case-insensitive)
PORTFOLIO_WATCHLIST: List[Tuple[str, re.Pattern]] = [
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in _RAW
]

_WATCHLIST_MAP: Dict[str, re.Pattern] = {name: pat for name, pat in PORTFOLIO_WATCHLIST}

# Canonical name alias normalization dictionary
CANONICAL_ALIASES: Dict[str, str] = {
    "bel": "Bharat Electronics Ltd",
    "bharat electronics": "Bharat Electronics Ltd",
    "bhel": "Bharat Heavy Electricals Ltd",
    "bharat heavy electricals": "Bharat Heavy Electricals Ltd",
    "sbi": "State Bank of India",
    "sail": "Steel Authority of India Ltd",
    "sun pharma": "Sun Pharmaceutical Industries Ltd",
    "sun pharmaceutical industries": "Sun Pharmaceutical Industries Ltd",
    "cil": "Coal India",
    "coal india": "Coal India",
    "accelya": "Accelya Solutions India",
    "basf": "BASF India",
    "adani": "Adani Group",
    "adani group": "Adani Group",
    "ambuja cement": "Adani Group",
    "ambuja cements": "Adani Group",
    "acc": "Adani Group",
    "britannia": "Britannia Industries Ltd",
    "havells": "Havells India Ltd",
}

# Module-level pre-compiled regexes for role evaluation
_COLON_SUBJECT_PATTERN = re.compile(
    r"^([A-Za-z0-9\s&.\-]+?)\s+(?:shares?|stocks?|rally|surge|gain|jump|fall|drop|tumble)\s*[:\-–|]\s*(.*)$",
    re.IGNORECASE,
)

# Pre-compile company-specific role patterns once at module load time
_ROLE_PATTERNS: Dict[str, Dict[str, re.Pattern]] = {}
for _name, _cpat in PORTFOLIO_WATCHLIST:
    _pat_str = _cpat.pattern
    _ROLE_PATTERNS[_name] = {
        "order_from": re.compile(
            rf"\b(?:bags?|wins?|secures?|awarded|receives?|gets?)\b.*?\b(?:from|by)\s+.*?(?:{_pat_str})",
            re.IGNORECASE,
        ),
        "order_lifts": re.compile(
            rf"(?:{_pat_str})(?:'s|\s+power's|\s+group's)?\s+.*?\border\b.*?(?:lifts?|boosts?|drives?|spurs?)\s+\b[A-Za-z0-9\s&]+?\b(?:shares?|stocks?)",
            re.IGNORECASE,
        ),
        "issuer": re.compile(
            rf"(?:{_pat_str}).*?\b(?:issues?|issuance|raises?|launches?|plans?|files?)\b.*?\b(?:bonds?|notes?|debt|ncds?|qip|ipo|drhp|dividend|buyback|shares?)\b|\b(?:bonds?|qip|ipo|dividend|buyback)\b.*?\b(?:by|from|of)\s+.*?(?:{_pat_str})",
            re.IGNORECASE,
        ),
        "acquirer": re.compile(
            rf"(?:{_pat_str}).*?\b(?:acquires?|to acquire|buys?|takeover|buys\s+stake|buys\s+into)\b|\b(?:acquisition|buyout)\s+by\s+.*?(?:{_pat_str})",
            re.IGNORECASE,
        ),
        "target": re.compile(
            rf"\b(?:stake in|acquisition of|buyout of|buys\s+into)\s+.*?(?:{_pat_str})|(?:{_pat_str}).*?\b(?:stake sale|to be acquired|approves sale of)\b",
            re.IGNORECASE,
        ),
        "order_recipient": re.compile(
            rf"(?:{_pat_str}).*?\b(?:bags?|wins?|secures?|awarded|receives?|gets?)\b.*?\b(?:orders?|contracts?|pact|deal|tender|project)\b",
            re.IGNORECASE,
        ),
        "regulated_entity": re.compile(
            rf"\b(?:sebi|cci|rbi|nclt|court|tribunal|dgca|ed|cbi)\b.*?\b(?:orders?|penalizes?|slaps?|fines?|probes?|notices?|summons?|raids?)\s+.*?(?:{_pat_str})|(?:{_pat_str}).*?\b(?:gets? notice|fined|penalized|faces probe|moves court|challenges)\b",
            re.IGNORECASE,
        ),
        "jv_participant": re.compile(
            rf"(?:{_pat_str}).*?\b(?:forms?|signs?|enters?)\b.*?\b(?:jv|joint venture|consortium)\b|\b(?:joint venture|jv)\b.*?\b(?:with|and)\s+.*?(?:{_pat_str})",
            re.IGNORECASE,
        ),
    }


def match_portfolio_company(
    event: Optional[Any] = None,
    article: Optional[Any] = None,
    text: Optional[str] = None,
) -> Optional[PortfolioMatch]:
    """
    Canonical single implementation for portfolio watchlist matching.
    Checks event/article or raw text, identifies holding, determines role, and returns PortfolioMatch.
    """
    # Fast path: check if cached on event metadata
    if event and hasattr(event, "metadata") and isinstance(event.metadata, dict):
        cached_sc = event.metadata.get("__story_context__")
        if cached_sc and hasattr(cached_sc, "portfolio_match"):
            return cached_sc.portfolio_match

    # Extract text components
    title = ""
    body = ""
    companies: List[str] = []

    if event:
        title = getattr(event, "canonical_title", "") or ""
        body = getattr(event, "description", "") or ""
        companies = getattr(event, "companies_involved", []) or []
    if article:
        if not title:
            title = getattr(article, "title", "") or ""
        if not body:
            body = getattr(article, "content_text", "") or ""

    if text:
        if not title:
            title = text
        else:
            body = f"{body} {text}".strip()

    title_clean = (title or "").strip()
    full_text = f"{title_clean} {body}".strip()
    if not full_text and not companies:
        return None

    # Step 1: Check companies_involved directly
    for c in companies:
        c_clean = c.strip().lower()
        if c_clean in CANONICAL_ALIASES:
            canonical_name = CANONICAL_ALIASES[c_clean]
            role, eligible = _evaluate_role(canonical_name, title_clean)
            return PortfolioMatch(
                canonical_name=canonical_name,
                matched_alias=c,
                confidence=1.0,
                match_reason=f"Direct entity match in companies_involved: '{c}'",
                role=role,
                eligible_for_priority=eligible,
            )

    # Step 2: Check title (highest priority)
    for cname, cpat in PORTFOLIO_WATCHLIST:
        m = cpat.search(title_clean)
        if m:
            role, eligible = _evaluate_role(cname, title_clean)
            return PortfolioMatch(
                canonical_name=cname,
                matched_alias=m.group(0),
                confidence=1.0,
                match_reason=f"Title matched portfolio holding '{cname}' via alias '{m.group(0)}'",
                role=role,
                eligible_for_priority=eligible,
            )

    # Step 3: Check full body text
    for cname, cpat in PORTFOLIO_WATCHLIST:
        m = cpat.search(full_text)
        if m:
            # If in body but not title, holding is counterparty/background
            return PortfolioMatch(
                canonical_name=cname,
                matched_alias=m.group(0),
                confidence=0.85,
                match_reason=f"Body mention of portfolio holding '{cname}' via alias '{m.group(0)}'",
                role="counterparty",
                eligible_for_priority=False,
            )

    return None


def _evaluate_role(cname: str, title: str) -> Tuple[str, bool]:
    """Evaluate role of portfolio company in headline using pre-compiled patterns."""
    if not title:
        return "primary_subject", True

    patterns = _ROLE_PATTERNS.get(cname)
    if not patterns:
        return "primary_subject", True

    cpat = _WATCHLIST_MAP.get(cname)
    t_lower = title.lower()

    # 1. Colon pattern (other company reaction)
    colon_m = _COLON_SUBJECT_PATTERN.match(title)
    if colon_m:
        other_subj = colon_m.group(1).strip()
        if cpat and not cpat.search(other_subj):
            return "counterparty", False

    # 2. Counterparty: order from / order lifts
    if patterns["order_from"].search(t_lower):
        return "counterparty", False
    if patterns["order_lifts"].search(t_lower):
        return "counterparty", False

    # 3. Active roles
    if patterns["issuer"].search(t_lower):
        return "issuer", True
    if patterns["acquirer"].search(t_lower):
        return "acquirer", True
    if patterns["target"].search(t_lower):
        return "target", True
    if patterns["order_recipient"].search(t_lower):
        return "order_recipient", True
    if patterns["regulated_entity"].search(t_lower):
        return "regulated_entity", True
    if patterns["jv_participant"].search(t_lower):
        return "jv_participant", True

    return "primary_subject", True


def is_watchlist_company(text: str) -> Tuple[bool, str]:
    """Check whether text mentions any portfolio watchlist company."""
    if not text:
        return False, ""
    match = match_portfolio_company(text=text)
    if match:
        return True, match.canonical_name
    return False, ""


def get_watchlist_match_details(text: str) -> Tuple[bool, str, str]:
    """Check whether text mentions any portfolio watchlist company and return matched alias."""
    if not text:
        return False, "", ""
    match = match_portfolio_company(text=text)
    if match:
        return True, match.canonical_name, match.matched_alias
    return False, "", ""


def get_portfolio_company_role(
    title: str,
    body: str = "",
    event: Optional[Any] = None,
) -> Tuple[bool, str, str, bool]:
    """Determine whether a text mentions a portfolio holding and ascertain its role."""
    match = match_portfolio_company(event=event, text=f"{title} {body}".strip())
    if match:
        return True, match.canonical_name, match.role, match.eligible_for_priority
    return False, "", "none", False


def format_portfolio_role_log(company: str, role: str, eligible_for_priority: bool) -> str:
    """Format structured log block for portfolio company role evaluation."""
    return (
        f"[PORTFOLIO_ROLE]\n"
        f'company="{company}"\n'
        f'role="{role}"\n'
        f'eligible_for_priority={str(eligible_for_priority).lower()}'
    )
