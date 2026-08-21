"""
Publisher & Corporate Entity Sanitizer.

Filters out publication/media names from corporate entity lists,
preventing publisher names (e.g. 'Business Standard', 'The Economic Times')
from ever being classified or deduplicated as company names.
"""

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
}


def sanitize_company_entities(companies: Optional[List[str]], publisher: Optional[str] = None) -> List[str]:
    """
    Sanitize company entities:
    - Removes publishers and media organizations from the company list.
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
        if not c or not isinstance(c, str):
            continue
        c_clean = c.strip()
        c_low = c_clean.lower()
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
