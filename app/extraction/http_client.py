"""
HTTP Client for Article Extraction.

Provides resilient HTTP retrieval with retry mechanisms, timeout controls,
user-agent headers, and strict URL preservation.
"""

import time
from typing import Optional, Tuple
from urllib.parse import urlparse

import httpx

from config import get_settings
from app.logging_config import get_logger

logger = get_logger("extraction.http")


class ArticleFetcher:
    """
    HTTP client for fetching raw HTML content from news article URLs.
    """

    def __init__(
        self,
        timeout_seconds: Optional[int] = None,
        user_agent: Optional[str] = None,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ) -> None:
        settings = get_settings()
        self.timeout = timeout_seconds or settings.REQUEST_TIMEOUT_SECONDS
        self.user_agent = user_agent or settings.USER_AGENT
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

    def _get_headers(self) -> dict[str, str]:
        """Construct standard browser headers."""
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

    def fetch_html(self, url: str) -> Tuple[bool, Optional[str], Optional[int], Optional[str]]:
        """
        Fetch HTML content from the exact given URL with retries.

        Args:
            url: The exact article URL returned by discovery.

        Returns:
            Tuple of (success: bool, html_content: Optional[str], status_code: Optional[int], error_msg: Optional[str])
        """
        # Validate URL scheme
        parsed = urlparse(url)
        if not parsed.scheme or parsed.scheme.lower() not in ("http", "https"):
            return False, None, None, f"Invalid URL scheme '{parsed.scheme}'. Must be http or https."

        headers = self._get_headers()
        last_error = None
        last_status = None

        for attempt in range(1, self.max_retries + 1):
            logger.debug("Fetching URL (attempt %d/%d): %s", attempt, self.max_retries, url)
            try:
                with httpx.Client(
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers=headers,
                    verify=True,
                ) as client:
                    response = client.get(url)
                    last_status = response.status_code

                    # Check Content-Type (must be HTML or text)
                    content_type = response.headers.get("content-type", "").lower()
                    if "text/html" not in content_type and "application/xhtml" not in content_type:
                        logger.warning("Rejected non-HTML response content-type '%s' for %s", content_type, url)
                        return False, None, last_status, f"Non-HTML content type: {content_type}"

                    if response.status_code == 200:
                        logger.debug("Successfully fetched %d bytes from %s", len(response.text), url)
                        return True, response.text, 200, None

                    # If client error (e.g. 404, 403, 410), don't retry
                    if 400 <= response.status_code < 500:
                        logger.warning("HTTP %d client error for %s. Not retrying.", response.status_code, url)
                        return False, None, response.status_code, f"HTTP {response.status_code} client error"

                    logger.warning("HTTP %d server error on attempt %d for %s", response.status_code, attempt, url)
                    last_error = f"HTTP {response.status_code} server error"

            except httpx.TimeoutException as exc:
                last_error = f"Request timed out after {self.timeout}s"
                logger.warning("Timeout fetching %s (attempt %d): %s", url, attempt, exc)
            except httpx.NetworkError as exc:
                last_error = f"Network connection error: {exc}"
                logger.warning("Network error fetching %s (attempt %d): %s", url, attempt, exc)
            except Exception as exc:
                last_error = f"Unexpected fetch error: {exc}"
                logger.warning("Unexpected error fetching %s (attempt %d): %s", url, attempt, exc)

            if attempt < self.max_retries:
                sleep_time = self.backoff_factor * (2 ** (attempt - 1))
                time.sleep(sleep_time)

        return False, None, last_status, last_error or "Failed to fetch HTML after retries"
