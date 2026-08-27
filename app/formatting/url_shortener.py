"""
URL Shortener utility for presentation display in the final briefing.

Uses TinyURL's public API endpoint with strict timeouts, in-memory caching,
and seamless fallback to the original URL if shortening is unavailable or fails.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from typing import Dict, Optional

from app.logging_config import get_logger

logger = get_logger(__name__)

# In-memory run cache: { original_url: shortened_url }
_URL_CACHE: Dict[str, str] = {}

TINYURL_API_ENDPOINT = "https://tinyurl.com/api-create.php"
DEFAULT_TIMEOUT_SECONDS = 3.0


def shorten_url(original_url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    """
    Shorten a URL using the TinyURL public endpoint.

    Parameters
    ----------
    original_url:
        The direct full-length verified URL.
    timeout:
        Network request timeout in seconds (default 3.0s).

    Returns
    -------
    str
        Shortened URL if successful; otherwise original_url on any failure.
    """
    if not original_url or not isinstance(original_url, str):
        return original_url or ""

    clean_url = original_url.strip()
    if not clean_url.startswith(("http://", "https://")):
        return clean_url

    # Check cache first
    if clean_url in _URL_CACHE:
        return _URL_CACHE[clean_url]

    try:
        encoded_target = urllib.parse.quote(clean_url, safe=":/?#[]@!$&'()*+,;=")
        api_url = f"{TINYURL_API_ENDPOINT}?url={encoded_target}"

        req = urllib.request.Request(
            api_url,
            headers={"User-Agent": "InvestmentBriefing/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                shortened = response.read().decode("utf-8", errors="replace").strip()
                if shortened.startswith("https://") and len(shortened) <= 50 and " " not in shortened:
                    _URL_CACHE[clean_url] = shortened
                    logger.debug("Shortened URL: %s -> %s", clean_url, shortened)
                    return shortened
                elif shortened.startswith("http://") and len(shortened) <= 50 and " " not in shortened:
                    # Upgrade to https if possible
                    shortened_https = shortened.replace("http://", "https://", 1)
                    _URL_CACHE[clean_url] = shortened_https
                    return shortened_https
    except Exception as e:
        logger.debug("URL shortening fallback for %s: %s", clean_url, e)

    # Fallback: cache original so we don't repeatedly retry failing URLs in the same run
    _URL_CACHE[clean_url] = clean_url
    return clean_url


def get_display_url(original_url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    """
    Safe public wrapper for getting a display URL.
    Never throws an exception; returns original_url on any issue.
    """
    try:
        return shorten_url(original_url, timeout=timeout)
    except Exception:
        return original_url


def clear_url_cache() -> None:
    """Clear the in-memory shortening cache (primarily for unit tests)."""
    _URL_CACHE.clear()
