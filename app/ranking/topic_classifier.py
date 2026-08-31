"""
Deterministic Topic Classification and Topic Distribution Audit Engine.

Categorizes business news events into 17 stable business theme buckets
strictly deterministically using pattern matching across headlines, entities,
event types, and extracted body text.
Zero Gemini API calls are made for topic classification.
"""

import re
from typing import Any, Dict, List, Optional, Tuple
from app.logging_config import get_logger

logger = get_logger("ranking.topic_classifier")

TOPIC_AI_TECH = "AI_TECH"
TOPIC_SEMICONDUCTORS = "SEMICONDUCTORS"
TOPIC_EARNINGS = "EARNINGS"
TOPIC_MNA = "MNA"
TOPIC_FUNDING = "FUNDING"
TOPIC_BANKING_FINANCE = "BANKING_FINANCE"
TOPIC_MARKETS = "MARKETS"
TOPIC_ENERGY = "ENERGY"
TOPIC_INFRASTRUCTURE = "INFRASTRUCTURE"
TOPIC_MANUFACTURING = "MANUFACTURING"
TOPIC_PHARMA_HEALTHCARE = "PHARMA_HEALTHCARE"
TOPIC_CONSUMER_RETAIL = "CONSUMER_RETAIL"
TOPIC_REGULATION = "REGULATION"
TOPIC_COMMODITIES = "COMMODITIES"
TOPIC_MACRO_TRADE = "MACRO_TRADE"
TOPIC_CONTRACT_AWARD = "CONTRACT_AWARD"
TOPIC_OTHER_BUSINESS = "OTHER_BUSINESS"

TOPIC_BUCKETS = [
    TOPIC_AI_TECH,
    TOPIC_SEMICONDUCTORS,
    TOPIC_EARNINGS,
    TOPIC_MNA,
    TOPIC_FUNDING,
    TOPIC_BANKING_FINANCE,
    TOPIC_MARKETS,
    TOPIC_ENERGY,
    TOPIC_INFRASTRUCTURE,
    TOPIC_MANUFACTURING,
    TOPIC_PHARMA_HEALTHCARE,
    TOPIC_CONSUMER_RETAIL,
    TOPIC_REGULATION,
    TOPIC_COMMODITIES,
    TOPIC_MACRO_TRADE,
    TOPIC_CONTRACT_AWARD,
    TOPIC_OTHER_BUSINESS,
]

# Topic Matching Patterns (ordered by specificity)
TOPIC_PATTERNS: List[Tuple[str, List[str]]] = [
    (
        TOPIC_CONTRACT_AWARD,
        [
            r"\b(?:bags|bagged|secures?|secured|wins?|won|awarded)\s+(?:an?\s+)?(?:[\$₹\d\w\s,]+?)?(?:order|contract|epc project|tender|mandate)\b",
            r"\b(?:epc contract|epc order|order win|contract award|order worth|contract worth|construction order)\b",
            r"\b(?:bags ₹\s*[\d,]+|secures ₹\s*[\d,]+|bags \$?\s*[\d,]+|secures \$?\s*[\d,]+)\b",
        ],
    ),
    (
        TOPIC_MNA,
        [
            r"\b(?:agrees? to acquire|completes? acquisition|to acquire|acquires?|acquisition of|buys? (?:controlling )?stake|stake purchase|stake sale|buyout of|buys out|merger agreement|to merge with|completes? merger|merger of|amalgamation|demerger|spin-off|takeover of|takes over)\b",
            r"\b(?:all-cash deal|nclt scheme of arrangement|joint venture agreement|forms? jv|enters? jv)\b",
        ],
    ),
    (
        TOPIC_FUNDING,
        [
            r"\b(?:raises? \$?\s*[\d,]+|secures? \$?\s*[\d,]+|raises? ₹\s*[\d,]+|secures? ₹\s*[\d,]+)\s*(?:million|billion|crore|cr)?\s*(?:in |through )?(?:funding|seed|series [a-h]|round|growth equity|venture capital|qip|rights issue|capital raise)\b",
            r"\b(?:funding round|series [a-h] funding|venture funding|qip issue|qualified institutional placement|raises funds|fundraise)\b",
        ],
    ),
    (
        TOPIC_EARNINGS,
        [
            r"\b(?:quarterly results|financial results|q[1-4] results|q[1-4] profit|q[1-4] net profit|q[1-4] revenue|q[1-4] loss|annual results|full-year results)\b",
            r"\b(?:net profit (?:rises|falls|jumps|drops|surges|slumps|up|down)|profit (?:rises|falls|jumps|drops|surges|slumps)|revenue (?:rises|falls|jumps|drops|grows))\b",
            r"\b(?:reports? (?:record )?(?:net )?profit|reports? (?:quarterly )?loss|ebitda rises|ebitda margin|earnings beat|earnings miss|beats? estimates|tops? estimates)\b",
        ],
    ),
    (
        TOPIC_SEMICONDUCTORS,
        [
            r"\b(?:semiconductor|semiconductors|chipmaker|chipmakers|microchips?|wafer fab|foundry|foundries|tsmc|intel|asml|qualcomm|arm holdings|nvidia chips|micron technology|broadcom|advanced micro devices|amd chip)\b",
            r"\b(?:chip manufacturing|semiconductor plant|chip packaging|silicon wafer|sub-micron|nanometer chip)\b",
        ],
    ),
    (
        TOPIC_AI_TECH,
        [
            r"\b(?:artificial intelligence|generative ai|genai|large language model|llms?|chatgpt|openai|anthropic|deepmind|copilot|ai model|ai chips?|ai startup|ai platform)\b",
            r"\b(?:cloud computing|software as a service|saas|cybersecurity|enterprise software|hyperscaler|datacenter infrastructure|data center)\b",
            r"\b(?:alphabet|google cloud|microsoft azure|aws|amazon web services|meta platforms|apple intelligence)\b",
        ],
    ),
    (
        TOPIC_PHARMA_HEALTHCARE,
        [
            r"\b(?:pharma|pharmaceutical|pharmaceuticals|biotech|biotechnology|drugmaker|drugmakers|fda approval|usfda|clinical trial|phase [123] trial|vaccine|active pharmaceutical ingredient|api unit)\b",
            r"\b(?:sun pharma|cipla|dr reddy|lupin|zydus|divis|aurobindo|biocon|pfizer|eli lilly|novo nordisk|astrazeneca|roche|merck|novartis|hospital chain|diagnostics)\b",
        ],
    ),
    (
        TOPIC_ENERGY,
        [
            r"\b(?:crude oil|brent crude|opec|petroleum|natural gas|lng terminal|refinery|pipeline|oil output|barrel)\b",
            r"\b(?:renewable energy|solar power|solar park|wind power|green hydrogen|clean energy|power grid|thermal power|electricity generation|nuclear power)\b",
            r"\b(?:aramco|exxon|chevron|bp|shell|totalenergies|reliance oil|ongc|iocl|bpcl|adani green|tata power|ntpc)\b",
        ],
    ),
    (
        TOPIC_INFRASTRUCTURE,
        [
            r"\b(?:highway|expressway|railway|bullet train|metro rail|freight corridor|airport|seaport|port terminal|bridge|tunnel|urban infrastructure)\b",
            r"\b(?:real estate|reit|housing project|commercial real estate|construction project|dlf|godrej properties|macrotech|l&t|knr constructions|dilip buildcon|gmr)\b",
        ],
    ),
    (
        TOPIC_BANKING_FINANCE,
        [
            r"\b(?:commercial bank|lender|lending|private bank|public sector bank|nbfc|microfinance|credit card|home loan|npa|bad loans|net interest income|nii)\b",
            r"\b(?:hdfc bank|icici bank|state bank of india|sbi|kotak mahindra|axis bank|indusind|bajaj finance|jpmorgan|goldman sachs|citigroup|morgan stanley|hsbc)\b",
            r"\b(?:mutual fund|amc|asset management|private equity|venture capital fund|life insurance|general insurance|pension fund|sovereign wealth fund)\b",
        ],
    ),
    (
        TOPIC_MANUFACTURING,
        [
            r"\b(?:automotive|carmaker|automaker|commercial vehicles|electric vehicle|ev plant|two-wheeler|suv|passenger vehicle)\b",
            r"\b(?:tata motors|maruti suzuki|mahindra & mahindra|hyundai|toyota|tesla|byd|volkswagen|bmw)\b",
            r"\b(?:manufacturing plant|assembly line|factory output|industrial machinery|heavy engineering|aerospace manufacturing|boeing|airbus|foxconn)\b",
        ],
    ),
    (
        TOPIC_CONSUMER_RETAIL,
        [
            r"\b(?:retail chain|supermarket|hypermarket|fmcg|consumer goods|packaged goods|e-commerce|quick commerce|food delivery)\b",
            r"\b(?:reliance retail|trent|dmart|avenue supermarts|zomato|swiggy|blinkit|zepto|flipkart|amazon india|walmart|target|costco|nestle|unilever|hul|itc consumer)\b",
        ],
    ),
    (
        TOPIC_REGULATION,
        [
            r"\b(?:antitrust|cci order|competition commission|sebi penalty|sebi order|rbi penalty|rbi mandate|monetary penalty|enforcement directorate|ed attaches|doj lawsuit|ftc probe)\b",
            r"\b(?:regulatory fine|regulatory approval|licence cancellation|compliance scrutiny|tariff policy|customs duty|import ban|export restriction)\b",
        ],
    ),
    (
        TOPIC_COMMODITIES,
        [
            r"\b(?:gold prices?|silver prices?|copper|aluminum|lithium mining|nickel|zinc|iron ore|steel prices?|rare earths?)\b",
            r"\b(?:agri commodities|wheat export|sugar export|palm oil|cotton|grain shipment|food grain)\b",
            r"\b(?:tata steel|jsw steel|vedanta|hindalco|nmdc|coal india)\b",
        ],
    ),
    (
        TOPIC_MACRO_TRADE,
        [
            r"\b(?:gdp growth|gross domestic product|retail inflation|cpi data|wholesale inflation|wpi|trade deficit|current account deficit|forex reserves)\b",
            r"\b(?:interest rate decision|repo rate|monetary policy committee|mpc|federal reserve rate|fed cut|ecb rate|central bank hike|currency valuation|rupee falls|dollar index)\b",
        ],
    ),
    (
        TOPIC_MARKETS,
        [
            r"\b(?:sensex|nifty|stock market|bse|nse|wall street|dow jones|s&p 500|nasdaq|benchmark index|indices|bull run|market breadth)\b",
            r"\b(?:institutional flows|fii buying|fii selling|dii investment|foreign investors|equity inflow|ipo debut|shares list|listing gains)\b",
        ],
    ),
]


def classify_topic_bucket(
    headline: str,
    body: str = "",
    event_type: str = "",
    entity: str = "",
) -> str:
    """
    Classify a business story into one of the 17 deterministic business topic buckets.
    Examines headline, entity, event_type, and opening body text.
    """
    headline_clean = (headline or "").strip()
    entity_clean = (entity or "").strip()
    event_type_clean = (event_type or "").strip()
    body_clean = (body or "").strip()[:500]

    # Combine text giving headline and entity the highest importance
    eval_text = f"{headline_clean} {entity_clean} {event_type_clean} {body_clean}".lower()
    headline_low = headline_clean.lower()

    # Step 1: Check patterns in order of specificity
    for topic_bucket, regex_list in TOPIC_PATTERNS:
        # Check if headline matches directly (highest confidence)
        if any(re.search(pat, headline_low, re.IGNORECASE) for pat in regex_list):
            return topic_bucket

    # Step 2: Check full evaluation text (headline + entity + body)
    for topic_bucket, regex_list in TOPIC_PATTERNS:
        if any(re.search(pat, eval_text, re.IGNORECASE) for pat in regex_list):
            return topic_bucket

    return TOPIC_OTHER_BUSINESS


def audit_topic_distribution(
    candidates: List[Any],
    section_name: str,
) -> Dict[str, int]:
    """
    Audit and log topic distribution for a selected candidate list.
    Logs TOPIC_CONCENTRATION_WARNING if any topic appears >= 3 times.
    """
    distribution: Dict[str, int] = {}
    for cand in candidates:
        ev = getattr(cand, "event", cand)
        meta = getattr(ev, "metadata", {}) or {}
        bucket = meta.get("topic_bucket")
        if not bucket:
            title = getattr(ev, "canonical_title", "")
            desc = getattr(ev, "description", "")
            bucket = classify_topic_bucket(headline=title, body=desc)
        distribution[bucket] = distribution.get(bucket, 0) + 1

    print(f"\n{section_name.upper()} TOPIC DISTRIBUTION:")
    for topic, count in sorted(distribution.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {topic}: {count}")
        if count >= 3:
            logger.warning(
                "TOPIC_CONCENTRATION_WARNING: Section '%s' has %d stories in topic '%s'",
                section_name, count, topic,
            )
            print(f"  -> TOPIC_CONCENTRATION_WARNING: {topic} has {count} stories")

    return distribution
