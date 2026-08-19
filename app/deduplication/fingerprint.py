"""
Event Fingerprinting and Semantic Matching.

Generates deterministic hashes and keys from (company, event_type, date, key_event_facts)
to accurately deduplicate multi-source reports of the same business event.
"""

from datetime import date, datetime
import hashlib
import re
from typing import List, Optional, Set, Tuple

from app.models.article import Article
from app.models.event import Event


def normalize_entity_name(name: Optional[str]) -> str:
    """Normalize company or entity name for consistent deduplication."""
    if not name:
        return "unspecified_entity"
    cleaned = name.lower().strip()
    # Remove common corporate suffixes
    cleaned = re.sub(r"\b(ltd|limited|inc|corp|corporation|plc|nv|sa|llc|pvt|co)\b", "", cleaned)
    cleaned = re.sub(r"[^\w\s]", "", cleaned)
    cleaned = "_".join(cleaned.split())
    return cleaned or "unspecified_entity"


def normalize_metric_facts(text: str) -> List[str]:
    """Extract and normalize key financial facts, figures, and percentages."""
    facts: Set[str] = set()

    # Percentages
    for pct in re.findall(r"\b\d+(?:\.\d+)?%", text):
        facts.add(pct.replace("%", "pct").strip())

    # Currencies and amounts
    for num in re.findall(r"(?:₹|\$|rs\.?\s*)?[\d,]+(?:\.\d+)?\s*(?:crore|cr|billion|million|b|m)?", text, re.IGNORECASE):
        norm = num.lower().replace("₹", "").replace("$", "").replace("rs.", "").replace("rs", "").replace(",", "").strip()
        norm = re.sub(r"\s+", "", norm)
        if norm and len(norm) > 1 and any(c.isdigit() for c in norm):
            facts.add(norm)

    return sorted(facts)


def generate_event_fingerprint(
    company: str,
    event_type: str,
    event_date: Optional[date] = None,
    key_facts: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """
    Generate deterministic fingerprint key and SHA256 hash.

    Args:
        company: Standardized company name.
        event_type: Event category string (e.g. EARNINGS, M&A).
        event_date: Date of the event (defaults to today).
        key_facts: List of extracted numbers/percentages.

    Returns:
        Tuple of (fingerprint_key, fingerprint_hash).
    """
    norm_comp = normalize_entity_name(company)
    norm_type = re.sub(r"[^\w]+", "_", event_type.lower().strip().replace("&", "_and_")).strip("_")
    target_date = event_date or date.today()
    date_str = target_date.strftime("%Y-%m-%d")

    facts_str = "_".join(sorted(key_facts or []))
    fingerprint_key = f"{norm_comp}:{norm_type}:{date_str}:{facts_str}".rstrip(":")
    fingerprint_hash = hashlib.sha256(fingerprint_key.encode("utf-8")).hexdigest()

    return fingerprint_key, fingerprint_hash


def are_articles_same_event(art1: Article, art2: Article) -> bool:
    """
    Evaluate if two articles represent the exact same underlying event.

    Example:
    - Reuters: "Company X profit rises 15%"
    - Business Standard: "Company X Q1 profit jumps 15%"
    -> Returns True (Must become ONE event).
    """
    t1 = art1.title.lower()
    t2 = art2.title.lower()

    # Extract words
    stopwords = {"the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "is", "with", "by", "its", "from"}
    w1 = {w for w in re.findall(r"[a-zA-Z0-9]+", t1) if w not in stopwords and len(w) >= 2}
    w2 = {w for w in re.findall(r"[a-zA-Z0-9]+", t2) if w not in stopwords and len(w) >= 2}

    overlap = w1.intersection(w2)
    union = w1.union(w2)
    similarity = len(overlap) / len(union) if union else 0.0

    facts1 = set(normalize_metric_facts(t1))
    facts2 = set(normalize_metric_facts(t2))
    shared_facts = facts1.intersection(facts2)

    # If shared facts exist and high word similarity or overlap >= 3
    if (shared_facts and similarity >= 0.25) or similarity >= 0.45 or len(overlap) >= 4:
        return True

    return False
