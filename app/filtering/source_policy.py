"""
SourcePolicy — Pre-extraction Domain Gating for Free-Tier Operation.

Classifies every discovered URL into one of four tiers BEFORE any HTTP fetch:

  TIER_1_PRIMARY   — Approved, freely accessible publishers.
                     Proceed with extraction immediately.
  TIER_2_OFFICIAL  — Official regulatory / exchange sources.
                     Proceed with extraction (low-volume, high-signal).
  TIER_3_SECONDARY — Signalling sources (bloomberg, ft, wsj, reuters).
                     Use ONLY for corroboration label/title checks.
                     Do NOT extract full article (paywalled).
  BLOCKED          — Aggregators, social, aggregated feeds, noise.
                     Skip entirely before any HTTP fetch.

Usage:
    from app.filtering.source_policy import SourcePolicy

    if not SourcePolicy.is_extraction_worthy(url, source_name):
        pre_filter_skips += 1
        continue
"""

from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Tier 1: Freely accessible primary publishers — extract these
# ---------------------------------------------------------------------------
_TIER1_PRIMARY_DOMAINS: frozenset[str] = frozenset({
    # India business
    "economictimes.indiatimes.com",
    "business-standard.com",
    "livemint.com",
    "financialexpress.com",
    "moneycontrol.com",
    "businesstoday.in",
    "ndtvprofit.com",
    "thehindubusinessline.com",
    "cnbctv18.com",
    # Domestic India
    "thehindu.com",
    "indianexpress.com",
    "hindustantimes.com",
    "ndtv.com",
    "indiatoday.in",
    "timesofindia.indiatimes.com",
    # International wire / accessible
    "cnbc.com",
    "apnews.com",
    "bbc.com",
    "bbc.co.uk",
    "marketwatch.com",
    "theguardian.com",
    "fortune.com",
    "businesswire.com",
    "globenewswire.com",
    "prnewswire.com",
    # Accessible financial publishers
    "investing.com",
    "barrons.com",
    "morningstar.com",
})

# ---------------------------------------------------------------------------
# Tier 2: Official/regulatory sources — extract these (high signal)
# ---------------------------------------------------------------------------
_TIER2_OFFICIAL_DOMAINS: frozenset[str] = frozenset({
    "bseindia.com",
    "nseindia.com",
    "sebi.gov.in",
    "rbi.org.in",
    "pib.gov.in",
    "sec.gov",
    "federalreserve.gov",
    "ecb.europa.eu",
    "bankofengland.co.uk",
    "mof.gov.in",
    "dipp.gov.in",
})

# ---------------------------------------------------------------------------
# Tier 3: Signalling only — title/headline reference OK; do NOT extract
# ---------------------------------------------------------------------------
_TIER3_SECONDARY_DOMAINS: frozenset[str] = frozenset({
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "reuters.com",
    "nytimes.com",
    "economist.com",
})

# ---------------------------------------------------------------------------
# Blocked: Never extract — aggregators, social, paywalls, noise
# ---------------------------------------------------------------------------
_BLOCKED_DOMAINS: frozenset[str] = frozenset({
    # Financial aggregators
    "tradingview.com",
    "stockanalysis.com",
    "finviz.com",
    "seekingalpha.com",
    "motleyfool.com",
    "fool.com",
    "zacks.com",
    "tipranks.com",
    "wisesheets.io",
    # Trade/industry niche (not approved publishers)
    "wastetodaymagazine.com",
    "todaysmedicaldevelopments.com",
    "machinedesign.com",
    "industryweek.com",
    "manufacturingdive.com",
    "supplychaindive.com",
    # Social / community
    "reddit.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "facebook.com",
    "telegram.org",
    # Paywalled generic news
    "theatlantic.com",
    "newyorker.com",
    # Aggregators / syndication
    "yahoo.com",
    "msn.com",
    "feedly.com",
    # Markets data
    "markets.ft.com",
})

# Blocked URL path patterns (applies to any domain)
_BLOCKED_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/tag/|/tags/|/topic/|/topics/", re.IGNORECASE),
    re.compile(r"/author/|/authors/|/contributor/", re.IGNORECASE),
    re.compile(r"/category/|/categories/", re.IGNORECASE),
    re.compile(r"/search\?|/search/", re.IGNORECASE),
    re.compile(r"/video/|/videos/|/podcast/|/podcasts/", re.IGNORECASE),
    re.compile(r"/slideshow/|/gallery/|/photos/", re.IGNORECASE),
    re.compile(r"/opinion/|/editorial/|/commentary/|/column/", re.IGNORECASE),
    re.compile(r"/markets-data/|/data/announce/detail", re.IGNORECASE),
)

# Blocked title keyword patterns (apply when source_name available)
_BLOCKED_TITLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:stocks? to watch|top stocks?|buzzing stocks?|hot stocks?)\b", re.IGNORECASE),
    re.compile(r"\b(?:analyst says?|price target|rating reiterated|outperform|buy recommendation)\b", re.IGNORECASE),
    re.compile(r"\b(?:listicle|top \d+ (?:stocks?|companies?|funds?))\b", re.IGNORECASE),
)


@lru_cache(maxsize=4096)
def _get_netloc(url: str) -> str:
    """Extract and normalize netloc from URL (cached for performance)."""
    try:
        netloc = urlparse(url).netloc.lower()
        # Strip port
        netloc = netloc.split(":")[0]
        # Strip www.
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def _domain_in_set(netloc: str, domain_set: frozenset[str]) -> bool:
    """Check if netloc matches any domain in set (including subdomains)."""
    if netloc in domain_set:
        return True
    for d in domain_set:
        if netloc.endswith("." + d):
            return True
    return False


def get_tier(url: str, source_name: str = "") -> str:
    """
    Return the tier of a discovered URL.

    Returns one of: 'PRIMARY' | 'OFFICIAL' | 'SECONDARY' | 'BLOCKED'
    """
    netloc = _get_netloc(url)
    if not netloc:
        return "BLOCKED"

    # Handle Google News RSS redirect URLs via publisher source_name
    if netloc in ("news.google.com", "news.google.co.in"):
        if source_name:
            norm_src = source_name.lower().strip()
            # Check if publisher is known blocked
            for b in ("tradingview", "seeking alpha", "stockanalysis", "motley fool", "fool", "zacks", "tipranks"):
                if b in norm_src:
                    return "BLOCKED"
            # Check if publisher is known secondary (paywalled)
            for s in ("bloomberg", "reuters", "financial times", "ft", "wall street journal", "wsj", "new york times", "nytimes"):
                if s in norm_src:
                    return "SECONDARY"
            # Check if publisher is official
            for o in ("bse", "nse", "sebi", "rbi", "sec", "pib"):
                if o in norm_src:
                    return "OFFICIAL"
        return "PRIMARY"

    # Check blocked domains first
    if _domain_in_set(netloc, _BLOCKED_DOMAINS):
        return "BLOCKED"

    # Check blocked path patterns
    try:
        path = urlparse(url).path
        for pat in _BLOCKED_PATH_PATTERNS:
            if pat.search(path):
                return "BLOCKED"
    except Exception:
        pass

    # Tier checks (ordered by precedence)
    if _domain_in_set(netloc, _TIER2_OFFICIAL_DOMAINS):
        return "OFFICIAL"
    if _domain_in_set(netloc, _TIER3_SECONDARY_DOMAINS):
        return "SECONDARY"
    if _domain_in_set(netloc, _TIER1_PRIMARY_DOMAINS):
        return "PRIMARY"

    # Default for other domains (including test domains and unlisted publishers)
    return "PRIMARY"


def is_extraction_worthy(url: str, source_name: str = "") -> bool:
    """
    Return True if the URL is worth spending an HTTP extraction budget on.

    Only PRIMARY and OFFICIAL tiers qualify.
    SECONDARY (bloomberg, reuters, ft, wsj) and BLOCKED domains are skipped.
    """
    tier = get_tier(url, source_name)
    return tier in ("PRIMARY", "OFFICIAL")


def is_corroboration_eligible(url: str, source_name: str = "") -> bool:
    """
    Return True if the URL may be used as a corroboration signal source
    (title/headline check, not full extraction).

    PRIMARY, OFFICIAL, and SECONDARY all qualify.
    """
    tier = get_tier(url, source_name)
    return tier in ("PRIMARY", "OFFICIAL", "SECONDARY")


def is_blocked(url: str, source_name: str = "") -> bool:
    """Return True if the URL should be completely skipped."""
    return get_tier(url, source_name) == "BLOCKED"


class SourcePolicy:
    """
    Namespace for source tier policy helpers.

    Prefer calling module-level functions directly for performance.
    This class is provided for import convenience.
    """

    get_tier = staticmethod(get_tier)
    is_extraction_worthy = staticmethod(is_extraction_worthy)
    is_corroboration_eligible = staticmethod(is_corroboration_eligible)
    is_blocked = staticmethod(is_blocked)

    # Tier constants
    PRIMARY = "PRIMARY"
    OFFICIAL = "OFFICIAL"
    SECONDARY = "SECONDARY"
    BLOCKED = "BLOCKED"
