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
    if cleaned in (
        "unspecified", "unknown", "n/a", "na", "none", "unspecified_entity", "",
        "ai", "revenue", "bank", "group", "technology", "tech", "energy", "green",
        "jewellery", "jewellers", "properties", "property", "developer", "retail",
    ):
        return "unspecified_entity"
    # Normalize hyphens and dashes to underscores or empty string
    cleaned = cleaned.replace("d-mart", "dmart").replace("d mart", "dmart")
    # Remove common corporate suffixes
    cleaned = re.sub(r"\b(ltd|limited|inc|corp|corporation|plc|nv|sa|llc|pvt|co|enterprises|enterprise)\b", "", cleaned)
    cleaned = re.sub(r"[^\w\s]", "", cleaned)
    cleaned = "_".join(cleaned.split())
    if not cleaned or cleaned in (
        "unspecified_entity", "ai", "revenue", "bank", "group", "technology",
        "tech", "energy", "green", "jewellery", "jewellers", "retail",
    ):
        return "unspecified_entity"

    KNOWN_ENTITY_ALIASES = {
        "larsen_toubro": "lt",
        "larsen_and_toubro": "lt",
        "state_bank_of_india": "sbi",
        "one97_communications": "paytm",
        "tata_consultancy_services": "tcs",
        "dmart": "avenue_supermarts",
        "d_mart": "avenue_supermarts",
        "d_mart_retail": "avenue_supermarts",
        "avenue_supermart": "avenue_supermarts",
        "avenue_supermarts": "avenue_supermarts",
        "reliance_jio": "reliance",
        "reliance_retail": "reliance",
        "reliance_industries": "reliance",
        "hdfc": "hdfc_bank",
        "icici": "icici_bank",
        "kotak": "kotak_mahindra_bank",
        "nvidias": "nvidia",
        "nvidia": "nvidia",
    }
    return KNOWN_ENTITY_ALIASES.get(cleaned, cleaned)


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
    Delegates to TwoSourceVerifier.is_same_underlying_event for consistent, robust verification.
    """
    from app.verification.verifier import TwoSourceVerifier
    is_same, _, _ = TwoSourceVerifier().is_same_underlying_event(art1, art2)
    return is_same
