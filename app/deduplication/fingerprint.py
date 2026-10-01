"""
Event Fingerprinting and Semantic Matching.

Generates deterministic hashes and keys from (company, event_type, date, key_event_facts)
to accurately deduplicate multi-source reports of the same business event.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import re
from typing import Any, List, Optional, Set, Tuple

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


def normalize_event_type(event_type: Optional[str]) -> str:
    """Normalize event type category to lowercase alphanumeric slug."""
    if not event_type:
        return "general"
    norm = re.sub(r"[^\w]+", "_", str(event_type).lower().strip().replace("&", "_and_")).strip("_")
    if "earning" in norm or "quarterly_result" in norm or "financial_result" in norm:
        return "earnings"
    if "acquisition" in norm or "tender_offer" in norm or "buyout" in norm or "merger" in norm:
        return "acquisition"
    return norm or "general"


def strip_date_from_fingerprint(fp: str) -> str:
    """Strip YYYY-MM-DD from fingerprint string to yield canonical stable fingerprint."""
    if not fp:
        return ""
    # Matches :YYYY-MM-DD: or :YYYY-MM-DD at the end
    cleaned = re.sub(r":\d{4}-\d{2}-\d{2}(?=:|$)", "", fp)
    return cleaned


def generate_event_fingerprint(
    company: str,
    event_type: str = "general",
    event_date: Optional[date] = None,
    key_facts: Optional[List[str]] = None,
    secondary_entity: Optional[str] = None,
    include_date: bool = True,
) -> Tuple[str, str]:
    """
    Generate deterministic canonical fingerprint key and SHA256 hash.

    Args:
        company: Standardized company or entity name.
        event_type: Event category string (e.g. EARNINGS, M_AND_A).
        event_date: Optional date of the event (defaults to today).
        key_facts: List of extracted numbers/percentages/facts.
        secondary_entity: Optional second company involved (e.g. target in M&A).
        include_date: Whether to include date into fingerprint (defaults to True).

    Returns:
        Tuple of (fingerprint_key, fingerprint_hash).
    """
    norm_comp = normalize_entity_name(company)
    norm_type = normalize_event_type(event_type)

    if isinstance(event_date, (list, tuple, set)):
        if key_facts is None:
            key_facts = list(event_date)
        event_date = None

    target_date = event_date or date.today()
    date_str = target_date.strftime("%Y-%m-%d")

    facts_str = "_".join(sorted(key_facts or []))

    parts = [norm_comp, norm_type]
    if secondary_entity:
        norm_sec = normalize_entity_name(secondary_entity)
        if norm_sec and norm_sec not in ("unspecified_entity", norm_comp):
            parts.append(norm_sec)

    if include_date:
        parts.append(date_str)

    if facts_str:
        parts.append(facts_str)

    fingerprint_key = ":".join(parts).rstrip(":")
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


def is_event_historical_repeat(
    event: Optional[Any],
    history_store: Optional[Any],
    target_date: Optional[date] = None,
    lookback_days: int = 3,
    headline: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Check if an event (or headline) matches any story in history within lookback_days.
    Uses canonical entity normalization, date-stripped fingerprints, and title checks.
    """
    if not event or not history_store:
        return False, "OK"

    eval_date = target_date or date.today()
    recent_fps = history_store.get_recent_fingerprints(
        target_date=eval_date, lookback_days=lookback_days, include_target_date=False
    )
    if not recent_fps:
        return False, "OK"

    # 1. Direct title/headline check
    canon_title = getattr(event, "canonical_title", "") or ""
    if canon_title and canon_title in recent_fps:
        return True, f"Event canonical title '{canon_title}' already appeared in briefing within previous {lookback_days} days"

    if headline and headline in recent_fps:
        return True, f"Headline '{headline}' already appeared in briefing within previous {lookback_days} days"

    # 2. Fingerprint check
    companies = getattr(event, "companies_involved", None) or []
    comp = companies[0] if companies else "unspecified"
    fin_figures = getattr(event, "financial_figures", None) or []
    ev_type = (getattr(event, "metadata", None) or {}).get("event_type") or getattr(event, "event_type", None) or "general"

    # Try specific event_type, "general", and "other" to catch legacy DB records
    types_to_try = {normalize_event_type(ev_type), "general", "other"}
    for t in types_to_try:
        fkey, fhash = generate_event_fingerprint(
            company=comp,
            event_type=t,
            event_date=eval_date,
            key_facts=fin_figures,
        )
        stable_fp = strip_date_from_fingerprint(fkey)
        stable_hash = hashlib.sha256(stable_fp.encode("utf-8")).hexdigest() if stable_fp else ""

        if fhash in recent_fps or fkey in recent_fps:
            return True, f"Event fingerprint already appeared in briefing within previous {lookback_days} days ({fkey})"
        if stable_fp and stable_fp in recent_fps:
            return True, f"Event fingerprint already appeared in briefing within previous {lookback_days} days ({stable_fp})"
        if stable_hash and stable_hash in recent_fps:
            return True, f"Event fingerprint already appeared in briefing within previous {lookback_days} days ({stable_hash})"

    return False, "OK"

