"""
Publisher & Corporate Entity Sanitizer.

Filters out publication/media names from corporate entity lists,
preventing publisher names (e.g. 'Business Standard', 'The Economic Times')
from ever being classified or deduplicated as company names.
"""

import re
from typing import List, Optional, Set
from app.deduplication.fingerprint import normalize_entity_name

PUBLISHER_ENTITY_BLACKLIST: Set[str] = {
    "business standard", "economic times", "the economic times", "et",
    "moneycontrol", "business today", "financial express", "mint", "livemint",
    "reuters", "bloomberg", "cnbc", "bbc", "fortune", "marketwatch", "pti",
    "ani", "businessline", "the hindu businessline", "the hindu", "outlook business",
    "ndtv profit", "ndtv", "wall street journal", "wsj", "ft", "financial times",
    "indian express", "the indian express", "times of india", "toi", "press trust of india",
    "asian news international", "ap news", "associated press", "the guardian", "guardian",
    "scconline", "pulse2", "forbes", "marketscreener", "hapag-lloyd",
    "bsindia", "@bsindia", "moneycontrol.com", "economictimes", "financialexpress",
    "pr newswire", "prnewswire", "globenewswire", "globe newswire", "business wire", "businesswire",
}

GENERIC_STANDALONE_ENTITY_BLACKLIST: Set[str] = {
    "the", "a", "an", "this", "that", "these", "those",
    "market", "markets", "investors", "revenue", "profit", "gold",
    "options", "shares", "stocks", "ceo", "company",
}


def normalize_publisher_name(source_name: Optional[str]) -> str:
    """Normalize publisher names and aliases into canonical names."""
    if not source_name:
        return "Unknown"
    norm = source_name.strip()
    norm_low = norm.lower().rstrip("/")
    
    if norm_low in ("@bsindia", "bsindia", "bs", "business-standard", "business standard"):
        return "Business Standard"
    if norm_low in ("moneycontrol.com", "moneycontrol"):
        return "Moneycontrol"
    if norm_low in ("the economic times", "economictimes", "economic times", "et", "economictimes.indiatimes.com"):
        return "Economic Times"
    if norm_low in ("the financial express", "financialexpress", "financial express", "fe", "financialexpress.com"):
        return "Financial Express"
    if norm_low in ("the hindu business line", "the hindu businessline", "hindu business line", "hindu businessline", "businessline", "thehindubusinessline.com"):
        return "The Hindu BusinessLine"
    if norm_low in ("the wall street journal", "wall street journal", "wsj", "wsj.com"):
        return "Wall Street Journal"
    if norm_low in ("the guardian", "guardian", "theguardian.com"):
        return "The Guardian"
    if norm_low in ("ap news", "ap", "associated press", "apnews.com"):
        return "AP News"
    if norm_low in ("bbc news", "bbc", "bbc.com"):
        return "BBC"
    if norm_low in ("ndtv profit", "ndtv", "ndtvprofit.com"):
        return "NDTV Profit"
    if norm_low in ("livemint", "mint", "livemint.com"):
        return "Livemint"
    if norm_low in ("business today", "businesstoday", "businesstoday.in"):
        return "Business Today"
    if norm_low in ("marketwatch", "market watch", "marketwatch.com"):
        return "MarketWatch"
    if norm_low in ("financial times", "ft", "ft.com"):
        return "Financial Times"
    if norm_low in ("bloomberg", "bloomberg news", "bloomberg.com"):
        return "Bloomberg"
    if norm_low in ("reuters", "reuters news", "reuters.com"):
        return "Reuters"
    if norm_low in ("cnbc", "cnbc tv18", "cnbctv18", "cnbc.com"):
        return "CNBC"
    if norm_low in ("pr newswire", "prnewswire", "prnewswire.com"):
        return "PR Newswire"
    if norm_low in ("globenewswire", "globe newswire", "globenewswire.com"):
        return "GlobeNewswire"
    if norm_low in ("business wire", "businesswire", "businesswire.com"):
        return "Business Wire"
    if norm_low in ("sec", "sec edgar", "sec.gov"):
        return "SEC"
    if norm_low in ("federal reserve", "fed", "federalreserve.gov"):
        return "Federal Reserve"
    if norm_low in ("ecb", "ecb.europa.eu"):
        return "ECB"
    if norm_low in ("rbi", "rbi.org.in"):
        return "RBI"
    if norm_low in ("sebi", "sebi.gov.in"):
        return "SEBI"
    if norm_low in ("bse", "bseindia.com"):
        return "BSE"
    if norm_low in ("nse", "nseindia.com"):
        return "NSE"
        
    return norm


DATE_AND_METRIC_PATTERNS = [
    # Full dates: '26 Aug 2026', '26 August 2026', 'August 26 2026', '2026-08-26', '26/08/2026'
    r"^\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{2,4}$",
    r"^(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?(?:,\s*|\s+)\d{2,4}$",
    r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$",
    r"^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$",
    # Reporting periods & standalone metrics: 'FY26', 'Q3', 'FY 2026', 'Q1 FY26'
    r"^(?:fy\s*\d{2,4}|q[1-4](?:\s*fy\s*\d{2,4})?)$",
    # Pure numbers, decimals, percentages or currencies: '1.06', '₹84.5 crore', '$100 million', '25%'
    r"^(?:₹|\$|rs\.?\s*)?[\d,]+(?:\.\d+)?\s*(?:%|crore|cr|billion|million|lakh|pct)?$",
    # Standalone financial words
    r"^(?:net profit|revenue|quarterly profit|standalone profit|profit|net loss|ebitda|operating income|sales|pat|pbt)$",
]

TRAILING_METRIC_SUFFIXES = [
    r"\s+fy\s*\d{2,4}(?:\s+net(?:\s+profit)?)?$",
    r"\s+q[1-4](?:\s+fy\s*\d{2,4})?(?:\s+net(?:\s+profit)?)?$",
    r"\s+net(?:\s+profit)?$",
    r"\s+q[1-4]$",
    r"\s+fy\s*\d{2,4}$",
]


def clean_company_name(name: str) -> Optional[str]:
    """Clean company candidate by stripping trailing metrics or rejecting invalid strings."""
    if not name or not isinstance(name, str):
        return None
    c_clean = name.strip().strip("'\".,;:()[]{}")
    c_low = c_clean.lower()
    
    if len(c_clean) < 2:
        return None

    # Check date and metric rejection patterns
    for pat in DATE_AND_METRIC_PATTERNS:
        if re.match(pat, c_low, re.IGNORECASE):
            return None

    # Strip trailing periods/metrics (e.g. 'boAt FY26 net' -> 'boAt')
    for suff in TRAILING_METRIC_SUFFIXES:
        c_clean = re.sub(suff, "", c_clean, flags=re.IGNORECASE).strip()
        c_low = c_clean.lower()

    if len(c_clean) < 2:
        return None

    # Re-check patterns after stripping
    for pat in DATE_AND_METRIC_PATTERNS:
        if re.match(pat, c_low, re.IGNORECASE):
            return None

    return c_clean


def sanitize_company_entities(companies: Optional[List[str]], publisher: Optional[str] = None) -> List[str]:
    """
    Sanitize company entities:
    - Removes publishers and media organizations from the company list.
    - Removes dates, metrics, reporting periods, pure numbers, and financial keywords.
    - Cleans trailing metric fragments (e.g. 'boAt FY26 net' -> 'boAt').
    - If an extracted entity matches the article's publisher, removes it.
    - Normalizes corporate aliases.
    - Returns only legitimate business/corporate entities (empty list if none exist).
    """
    if not companies:
        return []

    sanitized: List[str] = []
    seen: Set[str] = set()
    pub_norm = (publisher or "").lower().strip()

    for c in companies:
        c_clean = clean_company_name(c)
        if not c_clean:
            continue
        c_low = c_clean.lower()
        if c_low in GENERIC_STANDALONE_ENTITY_BLACKLIST:
            continue
        if c_low in PUBLISHER_ENTITY_BLACKLIST:
            continue
        if pub_norm and (c_low == pub_norm or c_low in pub_norm or pub_norm in c_low):
            continue
        norm = normalize_entity_name(c_clean)
        if norm in ("unspecified_entity", ""):
            continue
        if norm not in seen:
            sanitized.append(c_clean)
            seen.add(norm)

    return sanitized

