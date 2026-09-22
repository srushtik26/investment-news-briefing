"""
Pure shared validation helper utilities.

Contains pure functions for semantic token overlap and canonical numeric token
extraction without circular dependencies into AI or validation engine layers.
"""
from __future__ import annotations

import re
from typing import Any, Set, Tuple

_NUMBER_TOKEN_RE = re.compile(
    r"(?<!\w)(?:₹|\$|rs\.?\s*)?(\d[\d,]*(?:\.\d+)?)\s*(%|crore|cr|billion|million|b|m)?(?!\w)",
    re.IGNORECASE,
)


def canonical_numeric_tokens(text: str) -> Set[str]:
    """Extract whole numeric tokens and normalize grouping/symbols for exact comparison.

    Normalizes Indian 2,2,3 grouping (e.g. 5,57,700) and Western 3,3 grouping (557,700)
    to canonical digit strings (557700), along with standard units (cr, %, b, m).
    """
    values: Set[str] = set()
    if not text:
        return values

    for match in _NUMBER_TOKEN_RE.finditer(text):
        num_part = match.group(1).replace(",", "").strip()
        unit_part = match.group(2)
        if not num_part:
            continue

        unit_clean = ""
        if unit_part:
            u = unit_part.lower().strip()
            if u in ("crore", "cr"):
                unit_clean = "cr"
            elif u in ("billion", "b"):
                unit_clean = "b"
            elif u in ("million", "m"):
                unit_clean = "m"
            elif u == "%":
                unit_clean = "%"

        if len(num_part) >= 2 or unit_clean:
            values.add(f"{num_part}{unit_clean}")
            if unit_clean and len(num_part) >= 2:
                values.add(num_part)

    # Also capture any comma-grouped integers or floats directly
    for token in re.findall(r"(?<!\w)\d[\d,]*(?:\.\d+)?%?(?!\w)", text):
        norm = token.replace(",", "").replace("%", "").strip()
        if len(norm) >= 2:
            values.add(norm)

    return values


def calculate_semantic_token_overlap(
    headline: str,
    article_or_text: Any,
) -> Tuple[bool, int, Set[str]]:
    """
    Evaluate semantic token overlap between headline and source article/text.
    Matches the exact tokenization rules of Stage 9 Check #6:
        headline_tokens = set(re.findall(r"\w{4,}", headline.lower()))
        art_tokens = set(re.findall(r"\w{4,}", source_text.lower()))
    Returns:
        (has_overlap: bool, overlap_count: int, shared_tokens: Set[str])
    """
    if not headline:
        return False, 0, set()

    headline_tokens = set(re.findall(r"\w{4,}", headline.lower()))
    if not headline_tokens:
        return False, 0, set()

    if isinstance(article_or_text, str):
        source_text = article_or_text
    elif hasattr(article_or_text, "title") or hasattr(article_or_text, "content_text"):
        t = getattr(article_or_text, "title", "") or ""
        c = getattr(article_or_text, "content_text", "") or ""
        source_text = f"{t} {c}"
    else:
        source_text = str(article_or_text or "")

    art_tokens = set(re.findall(r"\w{4,}", source_text.lower()))
    shared = headline_tokens & art_tokens
    return bool(shared), len(shared), shared


def build_headline_grounding_source(
    event: Any = None,
    article: Any = None,
) -> str:
    """
    Construct robust grounding source text using actual Article and Event schemas.
    Safely handles None and avoids non-existent attributes (e.g. lead_paragraph, body_text).
    """
    parts = []

    if event:
        canon_t = getattr(event, "canonical_title", "") or ""
        if canon_t:
            parts.append(canon_t)

    if article:
        art_t = getattr(article, "title", "") or ""
        if art_t:
            parts.append(art_t)

        art_sum = getattr(article, "summary", "") or ""
        if art_sum:
            parts.append(art_sum)

        art_content = getattr(article, "content_text", "") or ""
        if art_content:
            parts.append(art_content[:1500])

    return " ".join(parts)


