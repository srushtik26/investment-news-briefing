"""
Portfolio Watchlist: 32 tracked equity holdings for Plutus Wealth Management.

Provides:
    PORTFOLIO_WATCHLIST  - list of (display_name, compiled_regex) for each holding.
    is_watchlist_company - returns (matched: bool, company_name: str) for any text.
    get_watchlist_match_details - returns (matched: bool, company_name: str, matched_alias: str).

Matching is case-insensitive and uses word-boundary anchors to avoid false positives
(e.g. "trent" must not match "current").  Each company entry also covers common short
names / ticker abbreviations used in financial headlines.
"""

import re
from typing import Any, List, Optional, Tuple

# ---------------------------------------------------------------------------
# (display_name, regex_pattern) pairs.
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

# Pre-compile all patterns once at import time (case-insensitive).
PORTFOLIO_WATCHLIST: List[Tuple[str, re.Pattern]] = [
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in _RAW
]


def is_watchlist_company(text: str) -> Tuple[bool, str]:
    """
    Check whether *text* mentions any portfolio watchlist company.

    Args:
        text: Concatenated canonical_title + " " + description (lowercased or not).

    Returns:
        (True, display_name) if a watchlist company is found, else (False, "").
    """
    if not text:
        return False, ""
    for display_name, pattern in PORTFOLIO_WATCHLIST:
        if pattern.search(text):
            return True, display_name
    return False, ""


def get_watchlist_match_details(text: str) -> Tuple[bool, str, str]:
    """
    Check whether *text* mentions any portfolio watchlist company and return matched alias.

    Returns:
        (matched, display_name, matched_alias)
    """
    if not text:
        return False, "", ""
    for display_name, pattern in PORTFOLIO_WATCHLIST:
        m = pattern.search(text)
        if m:
            return True, display_name, m.group(0)
    return False, "", ""


def get_portfolio_company_role(
    title: str,
    body: str = "",
    event: Optional[Any] = None,
) -> Tuple[bool, str, str, bool]:
    """
    Determine whether a text mentions a portfolio holding and ascertain its role:
    Returns:
        (matched: bool, company_name: str, role: str, eligible_for_priority: bool)

    Roles:
        - "primary_subject": holding is the principal actor / subject of corporate news
        - "acquirer": holding is direct buyer / acquirer
        - "target": holding is acquisition or investment target
        - "issuer": holding is issuer of debt, equity, QIP, dividend, buyback
        - "order_recipient": holding is winning / receiving a major contract / order
        - "regulated_entity": holding is subject of regulatory or legal action / ruling
        - "jv_participant": holding is direct partner in joint venture or consortium
        - "counterparty": holding is mentioned as counterparty, vendor, client, or in passing

    Rule:
        Portfolio priority requires the holding to NOT be a mere counterparty.
        If role == "counterparty", eligible_for_priority = False.
    """
    if not title:
        return False, "", "none", False

    t_clean = title.strip()
    full_text = f"{t_clean} {body}".strip()

    matched, cname = is_watchlist_company(full_text)
    if not matched:
        return False, "", "none", False

    # Find the specific regex pattern for this company
    cpat = None
    for display_name, pattern in PORTFOLIO_WATCHLIST:
        if display_name == cname:
            cpat = pattern
            break

    if not cpat:
        return False, "", "none", False

    t_lower = t_clean.lower()
    in_title = bool(cpat.search(t_clean))

    # If holding is NOT in the headline at all, it's almost certainly a passing mention / background
    if not in_title:
        return True, cname, "counterparty", False

    # 1. Check for Counterparty Indicators in Headline
    # Pattern A: Headline subject is another company's shares/stock reaction before colon/dash
    colon_match = re.match(r"^([A-Za-z0-9\s&.\-]+?)\s+(?:shares?|stocks?|rally|surge|gain|jump|fall|drop|tumble)\s*[:\-–|]\s*(.*)$", t_clean, re.IGNORECASE)
    if colon_match:
        other_subject = colon_match.group(1).strip()
        clause = colon_match.group(2).strip()
        if not cpat.search(other_subject):
            return True, cname, "counterparty", False

    # Pattern B: Another company bags/secures/receives an order FROM the portfolio company
    order_from_pattern = rf"\b(?:bags?|wins?|secures?|awarded|receives?|gets?)\b.*?\b(?:from|by)\s+.*?(?:{cpat.pattern})"
    if re.search(order_from_pattern, t_lower):
        return True, cname, "counterparty", False

    # Pattern C: Portfolio company's order lifts / boosts another company
    order_lifts_pattern = rf"(?:{cpat.pattern})(?:'s|\s+power's|\s+group's)?\s+.*?\border\b.*?(?:lifts?|boosts?|drives?|spurs?)\s+\b[A-Za-z0-9\s&]+?\b(?:shares?|stocks?)"
    if re.search(order_lifts_pattern, t_lower):
        return True, cname, "counterparty", False

    # 2. Check for Specific Active Roles:
    # Role: "issuer" / "borrower"
    issuer_pattern = rf"(?:{cpat.pattern}).*?\b(?:issues?|issuance|raises?|launches?|plans?|files?)\b.*?\b(?:bonds?|notes?|debt|ncds?|qip|ipo|drhp|dividend|buyback|shares?)\b|\b(?:bonds?|qip|ipo|dividend|buyback)\b.*?\b(?:by|from|of)\s+.*?(?:{cpat.pattern})"
    if re.search(issuer_pattern, t_lower):
        return True, cname, "issuer", True

    # Role: "acquirer"
    acquirer_pattern = rf"(?:{cpat.pattern}).*?\b(?:acquires?|to acquire|buys?|takeover|buys\s+stake|buys\s+into)\b|\b(?:acquisition|buyout)\s+by\s+.*?(?:{cpat.pattern})"
    if re.search(acquirer_pattern, t_lower):
        return True, cname, "acquirer", True

    # Role: "target"
    target_pattern = rf"\b(?:stake in|acquisition of|buyout of|buys\s+into)\s+.*?(?:{cpat.pattern})|(?:{cpat.pattern}).*?\b(?:stake sale|to be acquired|approves sale of)\b"
    if re.search(target_pattern, t_lower):
        return True, cname, "target", True

    # Role: "order_recipient"
    order_recipient_pattern = rf"(?:{cpat.pattern}).*?\b(?:bags?|wins?|secures?|awarded|receives?|gets?)\b.*?\b(?:orders?|contracts?|pact|deal|tender|project)\b"
    if re.search(order_recipient_pattern, t_lower):
        return True, cname, "order_recipient", True

    # Role: "regulated_entity"
    reg_pattern = rf"\b(?:sebi|cci|rbi|nclt|court|tribunal|dgca|ed|cbi)\b.*?\b(?:orders?|penalizes?|slaps?|fines?|probes?|notices?|summons?|raids?)\s+.*?(?:{cpat.pattern})|(?:{cpat.pattern}).*?\b(?:gets? notice|fined|penalized|faces probe|moves court|challenges)\b"
    if re.search(reg_pattern, t_lower):
        return True, cname, "regulated_entity", True

    # Role: "jv_participant"
    jv_pattern = rf"(?:{cpat.pattern}).*?\b(?:forms?|signs?|enters?)\b.*?\b(?:jv|joint venture|consortium)\b|\b(?:joint venture|jv)\b.*?\b(?:with|and)\s+.*?(?:{cpat.pattern})"
    if re.search(jv_pattern, t_lower):
        return True, cname, "jv_participant", True

    # If the company appears in the headline without counterparty phrasing, it's the primary subject
    return True, cname, "primary_subject", True


def format_portfolio_role_log(company: str, role: str, eligible_for_priority: bool) -> str:
    """Format structured log block for portfolio company role evaluation."""
    return (
        f"[PORTFOLIO_ROLE]\n"
        f'company="{company}"\n'
        f'role="{role}"\n'
        f'eligible_for_priority={str(eligible_for_priority).lower()}'
    )


