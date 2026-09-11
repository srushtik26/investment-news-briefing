"""
Investment Materiality Evaluation Layer.

Evaluates candidate news stories for institutional investment committee materiality.
Prevents technically valid (HCSS-compliant) but low-value routine filings,
micro-cap disclosures, stock price buzz, and listicles from reaching the final briefing.

Threshold:
    INVESTMENT_MATERIALITY_SCORE >= 60.0

A candidate story must pass BOTH:
1. Existing verification / HCSS threshold (HCSS >= 80, Domestic >= 60, or Two-Source Verified)
2. Investment Materiality Gate (Score >= 60.0)
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.logging_config import get_logger
from app.models.article import Article
from app.models.event import Event

logger = get_logger("verification.materiality")

INVESTMENT_MATERIALITY_THRESHOLD = 60.0

# ---------------------------------------------------------------------------
# Systemically Relevant / Large Companies Whitelist
# ---------------------------------------------------------------------------
LARGE_CAP_ENTITIES: Set[str] = {
    # India Large-Cap / Nifty 50 / Major conglomerates
    "tata", "tata motors", "tata steel", "tcs", "tata consultancy", "tata power",
    "reliance", "reliance industries", "jio", "reliance retail",
    "infosys", "wipro", "hcl", "hcl tech", "tech mahindra",
    "hdfc", "hdfc bank", "icici", "icici bank", "sbi", "state bank of india",
    "axis bank", "kotak", "kotak mahindra", "bajaj", "bajaj finance", "bajaj finserv",
    "adani", "adani ports", "adani enterprises", "adani power", "adani green", "adani group",
    "l&t", "larsen & toubro", "larsen and toubro",
    "bharti airtel", "airtel", "itc", "hindustan unilever", "hul",
    "maruti", "maruti suzuki", "mahindra", "mahindra & mahindra", "m&m",
    "sun pharma", "dr reddy", "cipla", "lupin",
    "britannia", "britannia industries", "havells", "havells india",
    "ntpc", "ongc", "coal india", "power grid", "bhel", "ioc", "bpcl", "hpcl",
    "vedanta", "hindalco", "jsw", "jsw steel", "titan", "asian paints",
    "ultratech", "grasim", "zomato", "swiggy", "paytm",
    # Major Public Institutions & Agencies
    "union cabinet", "finance ministry", "reserve bank of india", "rbi",
    "supreme court", "isro", "national highway authority", "nhai",
    "ministry of power", "ministry of finance", "ministry of commerce",
    "ministry of road transport", "morth", "meity", "ministry of electronics",
    # Prominent Indian Tech Unicorns / Logistics / Platforms
    "shiprocket", "delhivery", "zepto", "blinkit", "flipkart", "phonepe", "razorpay",
    # Global Giants
    "microsoft", "apple", "google", "alphabet", "amazon", "amazon web services", "aws",
    "nvidia", "meta", "tesla", "tsmc", "intel", "amd", "qualcomm", "broadcom", "samsung",
    "berkshire", "jpmorgan", "goldman sachs", "morgan stanley", "citi",
    "blackrock", "blackstone", "softbank", "boeing", "airbus", "pfizer",
    "moderna", "astrazeneca", "toyota", "byd", "volkswagen", "shell", "bp",
    "ge", "ge aerospace", "general electric", "spacex", "siemens", "honeywell",
    "rolls-royce", "lockheed", "lockheed martin", "raytheon", "rtx",
}

def extract_monetary_magnitude(text: str) -> Tuple[Optional[float], Optional[str]]:
    """
    Extract the highest single monetary magnitude in text.
    Returns (normalized_value, currency_code) where:
      - currency_code is 'USD' (normalized to Millions USD)
      - currency_code is 'INR' (normalized to Crores INR)
    """
    t = text.lower()
    max_usd_m = 0.0
    max_inr_cr = 0.0

    # 1. USD / Foreign currency with explicit magnitude units (billion, million, b, m)
    usd_matches = re.finditer(
        r"(?:us\$|usd\s*|\$)\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(billion|b\b|million|m\b)",
        t,
    )
    for m in usd_matches:
        raw_num = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").lower()
        val_m = raw_num * 1000.0 if unit in ("billion", "b") else raw_num
        if val_m > max_usd_m:
            max_usd_m = val_m

    named_usd = re.finditer(
        r"(\d+(?:,\d+)*(?:\.\d+)?)\s*(billion|b\b|million|m\b)\s*(?:dollars?|usd)",
        t,
    )
    for m in named_usd:
        raw_num = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").lower()
        val_m = raw_num * 1000.0 if unit in ("billion", "b") else raw_num
        if val_m > max_usd_m:
            max_usd_m = val_m

    # 2. INR patterns with explicit magnitude units (crore, cr, lakh)
    inr_matches = re.finditer(
        r"(?:rs\.?|₹|inr\s*)\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(crore|cr|lakh)\b",
        t,
    )
    for m in inr_matches:
        raw_num = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").lower()
        val_cr = raw_num if unit in ("crore", "cr") else (raw_num / 100.0)
        if val_cr > max_inr_cr:
            max_inr_cr = val_cr

    named_inr = re.finditer(
        r"(\d+(?:,\d+)*(?:\.\d+)?)\s*(crore|cr)\b",
        t,
    )
    for m in named_inr:
        raw_num = float(m.group(1).replace(",", ""))
        if raw_num > max_inr_cr:
            max_inr_cr = raw_num

    if max_usd_m > 0 and (max_inr_cr == 0 or max_usd_m * 84.0 > max_inr_cr):
        return max_usd_m, "USD"
    elif max_inr_cr > 0:
        return max_inr_cr, "INR"
    return None, None


# ---------------------------------------------------------------------------
# Evaluation Result Data Structure
# ---------------------------------------------------------------------------
@dataclass
class MaterialityResult:
    is_material: bool
    score: float
    breakdown: Dict[str, float] = field(default_factory=dict)
    positive_reasons: List[str] = field(default_factory=list)
    negative_reasons: List[str] = field(default_factory=list)
    summary_reason: str = ""


# Concrete Event Patterns for Portfolio Companies (qualifies for +20 materiality bonus)
CONCRETE_PORTFOLIO_EVENT_PATTERNS: List[Tuple[str, str]] = [
    ("order_win", r"\b(?:order win|wins? (?:major |large |defence |fleet |export )?order|bags? (?:major |large |defence |fleet |export )?order|secures? (?:major |large |defence |fleet |export )?order|awarded (?:major |large |defence |fleet |export )?order|wins? (?:large |major )?contract|secures? (?:large |major )?contract|bags? (?:large |major )?contract|contract win)\b"),
    ("acquisition_stake", r"\b(?:acquires?|acquisition|stake purchase|buys? (?:controlling |majority |minority |strategic )?stake|buyout|takeover|purchases? (?:controlling |majority |stake))\b"),
    ("divestment_sale", r"\b(?:divests?|divestment|asset sale|sells? (?:stake|business|unit|division|assets?)|exits? business|stake sale|monetises?)\b"),
    ("capex_expansion", r"\b(?:capex|capital expenditure|capacity expansion|retail expansion|network expansion|(?:major |large )?expansion|expands? (?:capacity|manufacturing|operations|network|quick commerce|retail)|invests? (?:crore|billion|million|\$|₹)|announces? (?:major |large |renewable )?capex|plans? (?:major |large )?capex|capital outlay|production capacity)\b"),
    ("plant_commissioning", r"\b(?:commissions?|commissioning|opens? (?:new |advanced )?(?:plant|facility|lab|unit|hub|factory|stores?)|launches? commercial production|operationalises?)\b"),
    ("govt_contract_pact", r"\b(?:government order|govt order|defence (?:order|contract|procurement)|signs? (?:[\w-]+\s+)*(?:concession|pact|agreement|contract|mou)|concession agreement|concession for|power purchase agreement|ppa)\b"),
    ("regulatory_action_approval", r"\b(?:regulatory (?:approval|event|action|penalty|order)|nod from|receives? (?:approval|clearance|nod)|usfda clearance|usfda approval|nclt (?:approval|nod|clears)|sebi (?:approval|order|nod|clears|penalty)|rbi (?:approval|order|nod|clears|penalty)|cci (?:approval|clears)|antitrust clearance)\b"),
    ("strategic_partnership_jv", r"\b(?:strategic partnership|joint venture|signs? jv|forms? jv|tie-up|collaborates? with|partnership with)\b"),
    ("debt_refinancing", r"\b(?:debt refinancing|refinances?|raises? (?:debt|capital|funds?|financing)|credit facility|dollar bonds?|bond issue|issues? (?:ncds?|bonds?|debentures?)|qip|rights issue)\b"),
    ("management_change", r"\b(?:appoints? (?:new )?(?:ceo|managing director|md|cfo|chairman)|(?:ceo|md|cfo|chairman) (?:resigns|steps down|retires)|leadership change|names? (?:new )?(?:ceo|md))\b"),
    ("earnings_surprise_guidance", r"\b(?:posts? record|reports? record|profit jumps?|net profit (?:surges|jumps|doubles|rises \d+%)|revenue (?:jumps|surges|grows \d+%)|raises? guidance|upbeat guidance|strong earnings)\b"),
    ("litigation_tax_penalty", r"\b(?:wins? (?:arbitration|legal battle|lawsuit)|settles? (?:tax|dispute|lawsuit)|litigation|tax penalty|tribunal ruling|court relief)\b"),
    ("merger_demerger", r"\b(?:merger|demerger|amalgamation|restructuring plan|scheme of arrangement)\b"),
    ("production_disruption", r"\b(?:production disruption|plant shutdown|halts? production|suspends? operations|force majeure)\b"),
]


class InvestmentMaterialityEvaluator:
    """
    Deterministic Investment Materiality Evaluation Gate.
    """

    def __init__(self, threshold: float = INVESTMENT_MATERIALITY_THRESHOLD):
        self.threshold = threshold


    def evaluate(
        self,
        event: Event,
        article: Optional[Article] = None,
        ctx: Optional[Any] = None,
    ) -> MaterialityResult:
        headline = (getattr(event, "canonical_title", "") or (article.title if article else "")).strip()
        body = (getattr(event, "description", "") or (article.content_text if article else "")).strip()
        full_text = f"{headline} {body[:1500]}".lower()
        headline_low = headline.lower()

        score = 0.0
        breakdown: Dict[str, float] = {}
        positive_reasons: List[str] = []
        negative_reasons: List[str] = []

        # =====================================================================
        # POSITIVE FACTORS
        # =====================================================================

        # 1. Major strategic corporate / national event (+30)
        # Mega M&A, large buyouts, mergers, acquisitions, joint ventures, major funding rounds, national missions
        is_strategic_event = bool(
            re.search(
                r"\b(?:merger|amalgamate|amalgamation|acquires?|acquisition|buyout|takeover|divestment|"
                r"divests?|strategic partnership|joint venture|signs definitive agreement|signs concession pact|"
                r"signs pact|signs deal|completes deal|all-cash deal|cash transaction|hostile bid|demerger|"
                r"to buy\s+(?:an?|the|its|controlling|majority|minority|\d+%|stake|castings|supplier|firm|company|startup|maker|business|unit|assets?|rival)|"
                r"buys\s+(?:an?|the|its|controlling|majority|minority|\d+%|stake|castings|supplier|firm|company|startup|maker|business|unit|assets?|rival)|"
                r"to combine with|combines with|combination with|takeover bid|majority stake|controlling stake|reverse merger|"
                r"announces\s+.*?investment|investment to build|invests?\b|capital expenditure|\bcapex\b|"
                r"it modernization|wins?\s+.*?deal|wins?\s+.*?contract|bags\s+.*?order|concession pact|"
                r"raises?\s+(?:[\$€£₹]|rs\.?|usd\s*)?\d+(?:,\d+)*(?:\.\d+)?\s*(?:billion|b|crore|cr|million|m)?|"
                r"funding round|series\s+[a-g]|growth round|venture funding|equity financing|capital raise|funds raised|"
                r"secures?\s+(?:[\$€£₹]|rs\.?|usd\s*)?\d+(?:,\d+)*(?:\.\d+)?\s*(?:billion|b|crore|cr|million|m)?\s+funding|"
                r"co-led by|led by\s+[a-z]|valued at\s+[\$€£₹]?\d+|valuation of\s+[\$€£₹]?\d+|at a\s+[\$€£₹]?\d+.*?\bvalue|"
                r"build rocket engine|rocket engine|launch vehicle|satellite constellation|orbital launch|commercial space|"
                r"isro|reusable launch vehicle|space mission|approves semiconductor|approves .*?scheme|approves .*?package|"
                r"dedicated freight corridor|freight corridor|infrastructure corridor|expressway network|freight network|"
                r"completes? (?:[\w-]+\s+)*(?:corridor|network|expressway|highway|rail line)|"
                r"unveils? (?:[\w-]+\s+)*(?:projects?|infrastructure|capex|corridor)|"
                r"projects? unveiled|infrastructure unveiled|inaugurates? (?:[\w-]+\s+)*(?:corridor|railway|highway|port|terminal|power plant|solar park)|"
                r"central capex|state capex|public investment programme|national infrastructure pipeline|pm gati shakti|"
                r"industrial corridor|logistics park|freight terminal|multimodal logistics|"
                r"high-speed rail|bullet train|national highway network|"
                r"supreme court delivers judgment|landmark ruling|delivers judgment|policy reform)\b",
                full_text,
            )
        )
        if is_strategic_event:
            score += 30.0
            breakdown["strategic_event"] = 30.0
            positive_reasons.append("major_strategic_corporate_event(+30)")

        # 2. Major policy / regulatory / macroeconomic significance (+25)
        # Cabinet, RBI, SEBI, CCI, GST Council, GDP, inflation, macro policy, national incentives
        is_policy_macro = bool(
            re.search(
                r"\b(?:union cabinet|cabinet approves|cabinet clears|finance ministry|rbi|reserve bank of india|"
                r"monetary policy|repo rate|sebi|securities and exchange board|cci approves|cci clears|"
                r"antitrust approval|gst council|gdp growth|retail inflation|cpi inflation|wholesale inflation|"
                r"fiscal deficit|pli scheme|semiconductor mission|national highway authority|nhai|sovereign bond|"
                r"supreme court|high court bench|election commission|government procurement|interstate|state dispute|"
                r"river water dispute|delivers judgment|ruling on|isro|space mission|ministry of \w+|government issues|"
                r"guidelines for|regulatory framework|gazette notification|"
                r"multi-crore (?:projects?|infrastructure|investment|works?|capex)|"
                r"public investment|government capex|capital outlay|infrastructure outlay|"
                r"freight corridor network|freight corridor|freight network|national highway network|rail network expansion|"
                r"power grid expansion|renewable energy capacity|solar park commissioning|green hydrogen|"
                r"industrial corridor|national logistics policy|pm gati shakti|"
                r"central government approves|state government approves|cabinet committee on economic affairs|ccea approves)\b",
                full_text,
            )
        )
        if is_policy_macro:
            score += 25.0
            breakdown["policy_macro"] = 25.0
            positive_reasons.append("policy_regulatory_macro_significance(+25)")

        # 3. Quantified deal / capex / funding / contract (+20)
        # Explicit numbers with mandatory metric units
        has_quantified_deal = False
        if hasattr(event, "financial_figures") and event.financial_figures:
            for fig in event.financial_figures:
                fig_clean = str(fig).lower()
                if any(kw in fig_clean for kw in ["crore", "billion", "million", "trillion", "₹", "$"]):
                    has_quantified_deal = True
                    break

        if not has_quantified_deal:
            quant_match = re.search(
                r"(?:(?:rs\.?|₹|\$|€|£|us\$|usd\s*|inr\s*)\s*\d+(?:,\d+)*(?:\.\d+)?\s*(?:crore|cr|billion|million|trillion|b\b|m\b)\b|"
                r"\b\d+(?:,\d+)*(?:\.\d+)?\s*(?:crore|cr|billion|million|trillion)\b|"
                r"\b(?:multi-crore|multi-billion|mega capex|mega project|billion-dollar project)\b|"
                r"\b(?:bags order|wins contract|secures order|awards? (?:[\w-]+\s+)*contracts?|concession pact|order worth|invests? ₹?\$?\d+|funding round of ₹?\$?\d+)\b)",
                full_text,
            )
            if quant_match:
                has_quantified_deal = True

        if has_quantified_deal:
            score += 20.0
            breakdown["quantified_deal"] = 20.0
            positive_reasons.append("quantified_deal_capex_funding_contract(+20)")

        # 3b. Deterministic Magnitude Bands
        val, cur = extract_monetary_magnitude(full_text)
        if val and cur:
            if cur == "USD":
                if val >= 5000.0:
                    score += 20.0
                    breakdown["magnitude_bonus"] = 20.0
                    positive_reasons.append("very_high_magnitude_transaction(+20)")
                elif val >= 1000.0:
                    score += 15.0
                    breakdown["magnitude_bonus"] = 15.0
                    positive_reasons.append("high_magnitude_transaction(+15)")
                elif val >= 250.0:
                    score += 10.0
                    breakdown["magnitude_bonus"] = 10.0
                    positive_reasons.append("meaningful_magnitude_transaction(+10)")
            elif cur == "INR":
                if val >= 10000.0:
                    score += 20.0
                    breakdown["magnitude_bonus"] = 20.0
                    positive_reasons.append("very_high_magnitude_transaction(+20)")
                elif val >= 2000.0:
                    score += 15.0
                    breakdown["magnitude_bonus"] = 15.0
                    positive_reasons.append("high_magnitude_transaction(+15)")
                elif val >= 500.0:
                    score += 10.0
                    breakdown["magnitude_bonus"] = 10.0
                    positive_reasons.append("meaningful_magnitude_transaction(+10)")

        # 4. Large / systemically relevant company or institution (+15)
        comps = [c.lower().strip() for c in (getattr(event, "companies_involved", []) or [])]
        is_large_company = False
        for c in comps:
            if any(lcap in c for lcap in LARGE_CAP_ENTITIES):
                is_large_company = True
                break
        if not is_large_company:
            for lcap in LARGE_CAP_ENTITIES:
                if re.search(rf"\b{re.escape(lcap)}\b", headline_low):
                    is_large_company = True
                    break

        if is_large_company:
            score += 15.0
            breakdown["large_company"] = 15.0
            positive_reasons.append("large_systemic_company(+15)")

        # 5. Direct investment / industry / ownership implication (+15)
        is_direct_investment = bool(
            re.search(
                r"\b(?:capex|capital expenditure|manufacturing facility|new plant|gigafactory|data cent(?:er|re)s?|"
                r"semiconductor(?: fab)?|chip manufacturing|assembly plant|capacity expansion|warehouse network|"
                r"expressway|highway corridor|railway corridor|procurement|incentive guidelines|incentive scheme|"
                r"files drhp|public issue opens|ipo opens|listing debut|lists at premium|debuts at premium|"
                r"qip issue|rights issue|commercial production|commercial operations|contract win|enterprise ai|"
                r"computing platform|autonomous driving|cloud data center|sovereign cloud|sovereign bond calendar|"
                r"borrowing calendar|renewable energy integration|grid integration|"
                r"reverse merger|merger|acquisition|buyout|takeover|controlling stake|majority stake|minority stake|"
                r"stake purchase|to combine with|combines with|combination with|demerger|amalgamation|"
                r"satellite constellation|rocket engine|build rocket engine|"
                r"dedicated freight corridor|freight corridor|corridor network|projects? unveiled|infrastructure unveiled|"
                r"commissions?(?: new)?|commissioning|solar plant|power plant|renewable plant|container terminal|concession agreement|ppa|power purchase agreement|retail expansion|network expansion)\b",
                full_text,
            )
        )
        if is_direct_investment:
            score += 15.0
            breakdown["direct_investment"] = 15.0
            positive_reasons.append("direct_investment_industry_implication(+15)")

        # 6. Cross-border / global significance (+10)
        is_global = bool(
            re.search(
                r"\b(?:cross-border|foreign direct investment|fdi|bilateral|overseas acquisition|"
                r"global supply chain|export order|multinational|european union|united states|us-based|"
                r"frankfurt|europe|international market|global market share|geopolitical|"
                r"global expansion|international expansion|overseas expansion)\b",
                full_text,
            )
        )
        if is_global:
            score += 10.0
            breakdown["global_significance"] = 10.0
            positive_reasons.append("cross_border_global_significance(+10)")

        # 7. Major sector impact (+10)
        # Strategically important sectors: AI, semiconductor, aerospace, banking, energy, telecom, infrastructure, space
        is_sector_impact = bool(
            re.search(
                r"\b(?:ai\b|artificial intelligence|generative ai|aerospace|aviation|space|spacetech|spacex|"
                r"satellite|satellites|hyperspectral|rocket|launch vehicle|propulsion|"
                r"semiconductor|chips?|foundry|banking|banks?|lender|financial services|"
                r"telecom|telecommunications|power|energy|oil & gas|renewable energy|clean energy|solar|wind|"
                r"infrastructure|logistics|supply chain|auto|automotive|electric vehicle|evs?|battery|gigafactory|"
                r"pharma|pharmaceutical|biotech|real estate|data cent(?:er|re)s?|cloud|cyber security|"
                r"deepwater|defense|defence|railway|expressway|ports?|shipping|mining|metals?|steel|"
                r"freight|freight corridor|highways?|expressways?|rail network|power grid|renewable grid|multimodal|"
                r"industry-wide|nationwide|across \d+ cities|rollout|bond|bonds|debt market|sovereign debt|treasury|g-sec)\b",
                full_text,
            )
        )
        if is_sector_impact:
            score += 10.0
            breakdown["sector_impact"] = 10.0
            positive_reasons.append("major_sector_impact(+10)")

        # 8. Strategic supply-chain / capacity consequence (+10)
        is_strategic_consequence = bool(
            re.search(
                r"\b(?:supply chain(?: bottleneck| resilience| disruption)?|bottleneck|castings supplier|"
                r"tackle\s+.*?bottleneck|expand capacity|capacity expansion|build rocket engine|"
                r"manufacturing capacity|commercial production|scale production|production capacity|"
                r"market share|reshape\s+.*?landscape|critical supplier|strategic supplier|"
                r"vertical integration|import substitution|self-reliance|sovereign capability|"
                r"freight capacity|logistics capacity|bolster(?:ing)? logistics|freight movement|transit time|"
                r"freight corridor (?:network|milestone|completion)|corridor network completed|network completed|"
                r"logistics corridor|connectivity milestone|freight transit|multimodal connectivity)\b",
                full_text,
            )
        )
        if is_strategic_consequence:
            score += 10.0
            breakdown["strategic_consequence"] = 10.0
            positive_reasons.append("strategic_supply_chain_capacity_consequence(+10)")

        # 9. Valuation or lead investor backing (+10)
        is_valuation_or_lead = bool(
            re.search(
                r"\b(?:valued at|at a\s+[\$€£₹]?\d+.*?\bvaluation|at a\s+[\$€£₹]?\d+.*?\bvalue|\bvaluation of\s+[\$€£₹]?\d+|"
                r"co-led by|led by\s+[a-z]|series [a-g]|growth round|unicorn)\b",
                full_text,
            )
        )
        if is_valuation_or_lead:
            score += 10.0
            breakdown["valuation_lead_investor"] = 10.0
            positive_reasons.append("valuation_or_lead_investor_backing(+10)")

        # 10. Institutional sponsor / private equity controlling stake buyout (+10)
        # Recognizes formal change-of-control acquisitions backed by institutional private equity sponsors
        is_institutional_control = bool(
            re.search(
                r"\b(?:private equity|pe firm|buyout firm|financial sponsor|institutional investors?|"
                r"consortium of investors?|investors? led by|l catterton|sovereign wealth fund)\b",
                full_text,
            )
            and re.search(
                r"\b(?:controlling stake|majority stake|majority control|controlling interest|"
                r"buyout of controlling stake|majority buyout|buyout)\b",
                full_text,
            )
        )
        if is_institutional_control:
            score += 10.0
            breakdown["institutional_control_buyout"] = 10.0
            positive_reasons.append("institutional_sponsor_control_buyout(+10)")

        # 11. Portfolio Watchlist Concrete Event Bonus (+20)
        from app.ranking.watchlist import get_watchlist_match_details
        is_pf, pf_company, pf_alias = get_watchlist_match_details(full_text)
        if is_pf:
            match_log = f'[PORTFOLIO_MATCH]\ncompany="{pf_company}"\nalias="{pf_alias}"\ntitle="{headline}"'
            logger.info(match_log)
            if ctx and hasattr(ctx, "log_exec"):
                ctx.log_exec(match_log)

            concrete_event_matched = None
            for ev_type, pat in CONCRETE_PORTFOLIO_EVENT_PATTERNS:
                if re.search(pat, full_text):
                    concrete_event_matched = ev_type
                    break

            if concrete_event_matched:
                score += 20.0
                breakdown["portfolio_concrete_event"] = 20.0
                positive_reasons.append(f"portfolio_concrete_event_{concrete_event_matched}(+20)")
                bonus_log = f'[PORTFOLIO_EVENT_BONUS]\ncompany="{pf_company}"\nevent_type="{concrete_event_matched}"\nbonus=20'
                logger.info(bonus_log)
                if ctx and hasattr(ctx, "log_exec"):
                    ctx.log_exec(bonus_log)

        # =====================================================================
        # NEGATIVE FACTORS (Penalties)
        # =====================================================================

        # 1. Routine quarterly-results page / small disclosure (-40)
        # Matches pages that are just routine periodic results, particularly with exchange codes (BSE, NSE, price tickers)
        is_routine_results = bool(
            re.search(
                r"\b(?:quarterly results?|q[1-4] results?|q[1-4] net profit|q[1-4] profit|financial results for the quarter)\b",
                headline_low,
            )
            and (
                bool(re.search(r"(?:-\s*bse\s*[\d\.]+|bse:\s*[\d\.]+|nse:\s*[\d\.]+|bse\s+\d{3,})", headline_low))
                or bool(re.search(r"\b(?:unaudited financial results|outcome of board meeting|financial results for the period)\b", full_text))
            )
        )
        if is_routine_results:
            score -= 40.0
            breakdown["routine_quarterly_results"] = -40.0
            negative_reasons.append("routine_quarterly_results_page(-40)")

        # 2. Generic stock-price movement (-35)
        # Price fluctuations without hard transaction/corporate event
        is_stock_price_movement = bool(
            re.search(
                r"\b(?:shares? (?:gain|gains|surge|surges|jump|jumps|fall|falls|drop|drops|slump|slumps|rise|rises|tank|tanks|slip|slips)\s+\d+(?:\.\d+)?%|"
                r"stock (?:gains|surges|jumps|falls|drops|rises|slumps)\s+\d+(?:\.\d+)?%|"
                r"hits? (?:52-week|all-time) (?:high|low)|upper circuit|lower circuit|why shares? (?:is|are) (?:rising|falling|down|up)|"
                r"share price today|stock price today|trading (?:higher|lower) in morning)\b",
                headline_low,
            )
        )
        if is_stock_price_movement:
            score -= 35.0
            breakdown["stock_price_movement"] = -35.0
            negative_reasons.append("generic_stock_price_movement(-35)")

        # 3. Stocks-to-watch / listicle (-35)
        is_listicle = bool(
            re.search(
                r"\b(?:stocks? to watch(?: today)?|stocks? in focus(?: today)?|buzzing stocks?|top stocks? to (?:buy|watch)|"
                r"\d+\s+stocks? to (?:watch|buy|track)|stocks? in the news|market roundup|closing bell|morning bell|"
                r"nifty outlook|sensex today|trade setup for today|hot stocks? to buy)\b",
                headline_low,
            )
        )
        if is_listicle:
            score -= 35.0
            breakdown["stocks_to_watch_listicle"] = -35.0
            negative_reasons.append("stocks_to_watch_listicle(-35)")

        # 4. Analyst / broker recommendation / opinion (-30)
        is_broker_opinion = bool(
            re.search(
                r"\b(?:target price|price target|brokerage (?:recommends|retains|maintains|gives|cuts|hikes|initiates)|"
                r"(?:recommends|maintains|gives)\s+(?:buy|sell|hold|overweight|underweight|accumulate)|"
                r"analysts? (?:see|sees|maintain|recommend|give)|jefferies|nomura|morgan stanley|goldman sachs|clsa|macquarie|"
                r"motilal oswal|emkay|icici direct|hdfc securities|kotak institutional)\s+(?:maintains?|gives?|recommends?|targets?|sees?)\b|"
                r"\b(?:should you buy|buy, sell or hold|top stock picks?)\b",
                headline_low,
            )
        )
        if is_broker_opinion:
            score -= 30.0
            breakdown["broker_opinion"] = -30.0
            negative_reasons.append("analyst_broker_recommendation_opinion(-30)")

        # 5. Small-company routine disclosure / secretarial filing (-30)
        is_small_disclosure = bool(
            re.search(
                r"\b(?:regulation 30|regulation 33|trading window closure|closure of trading window|"
                r"board meeting intimation|scrutiny of postal ballot|postal ballot notice|"
                r"loss of share certificates?|duplicate share certificates?|investor presentation uploaded|"
                r"audio recording of earnings call|transcript of earnings call|change in registered office|"
                r"change in company secretary|compliance certificate under|disclosure under sebi sast)\b",
                full_text,
            )
        )
        if is_small_disclosure:
            score -= 30.0
            breakdown["small_company_disclosure"] = -30.0
            negative_reasons.append("small_company_routine_disclosure(-30)")

        # 6. Speculative story without confirmed action (-25)
        is_speculative = bool(
            re.search(
                r"\b(?:may acquire|in talks to (?:buy|acquire|merge|sell)|could raise|likely to consider|"
                r"reportedly eyeing|sources say considering|rumou?red to (?:buy|acquire)|weighs (?:bid|options)|"
                r"mulls stake sale|in early talks|exploring sale of)\b",
                headline_low,
            )
        )
        if is_speculative:
            score -= 25.0
            breakdown["speculative_unconfirmed"] = -25.0
            negative_reasons.append("speculative_story_without_confirmed_action(-25)")

        # 7. Routine political speeches / election meetings / party whips (-30)
        is_routine_politics = bool(
            re.search(
                r"\b(?:poll strategy|election strategy|seat-sharing|party whip|three-line whip|"
                r"voter entry|voter list|bypassed rules|campaign rally|party infighting|infighting in|"
                r"rebel mla|rebel mlc|party workers?|caste census|speech at rally|slams government|"
                r"attacks centre|attacks state|alleges neglect|accuses .*?government of neglecting|"
                r"cpm accuses|bjp slams|congress attacks|aap claim)\b",
                headline_low,
            )
        )
        if is_routine_politics:
            score -= 30.0
            breakdown["routine_politics"] = -30.0
            negative_reasons.append("routine_politics_infighting_election_speech(-30)")

        # 8. Routine weather / travel inconvenience (-40)
        is_weather_travel = bool(
            re.search(
                r"\b(?:bad weather forces?|poor visibility forces?|helicopter (?:returns?|forced to land)|"
                r"flight delayed due to fog|traffic snarl|train delayed due to weather)\b",
                headline_low,
            )
        )
        if is_weather_travel:
            score -= 40.0
            breakdown["weather_travel_inconvenience"] = -40.0
            negative_reasons.append("weather_travel_inconvenience(-40)")

        # 9. Sports administration / sports governance (-30)
        is_sports_admin = bool(
            re.search(
                r"\b(?:sports governance|sports governance act|national sports governance|"
                r"golf union|cricket board|bcci|olympic association|wrestling federation|"
                r"amendments to moa to align with national sports)\b",
                headline_low,
            )
        )
        if is_sports_admin:
            score -= 30.0
            breakdown["sports_administration"] = -30.0
            negative_reasons.append("sports_administration_governance(-30)")

        # 10. Small local disaster fund / local crime / minor protests (-30)
        is_local_disaster_crime = bool(
            re.search(
                r"\b(?:disaster fund for hills|earthquake of magnitude|minor tremor hits|"
                r"addicted to reels|suicide after reel|protesters seek recruitment notification|"
                r"highway blockade|body announces intensified highway blockade|pg collapse|pg that collapsed)\b",
                headline_low,
            )
        )
        if is_local_disaster_crime:
            score -= 30.0
            breakdown["local_disaster_crime_minor_protest"] = -30.0
            negative_reasons.append("local_disaster_crime_minor_protest(-30)")

        # Bound score between 0.0 and 100.0
        final_score = max(0.0, min(100.0, score))
        is_material = final_score >= self.threshold

        pos_str = ", ".join(positive_reasons) if positive_reasons else "none"
        neg_str = ", ".join(negative_reasons) if negative_reasons else "none"
        summary_reason = f"score={final_score:.1f} (pos: [{pos_str}], neg: [{neg_str}])"

        if is_material:
            log_msg = f"[MATERIALITY_ACCEPT]\ntitle={headline}\nscore={final_score:.1f}\nreason={summary_reason}"
            logger.info(log_msg)
            if ctx and hasattr(ctx, "log_exec"):
                ctx.log_exec(log_msg)
        else:
            log_msg = f"[MATERIALITY_REJECT]\ntitle={headline}\nscore={final_score:.1f}\nreason={summary_reason}"
            logger.info(log_msg)
            if ctx and hasattr(ctx, "log_exec"):
                ctx.log_exec(log_msg)
            if is_pf:
                pf_rej = f'[PORTFOLIO_REJECT]\ncompany="{pf_company}"\nreason="{summary_reason}"'
                logger.info(pf_rej)
                if ctx and hasattr(ctx, "log_exec"):
                    ctx.log_exec(pf_rej)

        return MaterialityResult(
            is_material=is_material,
            score=final_score,
            breakdown=breakdown,
            positive_reasons=positive_reasons,
            negative_reasons=negative_reasons,
            summary_reason=summary_reason,
        )


def evaluate_investment_materiality(
    event: Event,
    article: Optional[Article] = None,
    ctx: Optional[Any] = None,
) -> Tuple[bool, float, str]:
    """
    Convenience function returning (is_material, score, summary_reason).
    """
    evaluator = InvestmentMaterialityEvaluator()
    res = evaluator.evaluate(event=event, article=article, ctx=ctx)
    return res.is_material, res.score, res.summary_reason
