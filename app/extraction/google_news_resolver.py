"""
Google News URL Resolver Service.

Detects and resolves Google News RSS article URLs (e.g. https://news.google.com/rss/articles/...)
to direct canonical publisher URLs using googlenewsdecoder with robust HTTP redirect and HTML
scraping fallback mechanisms.
"""

from dataclasses import dataclass
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

from config import get_settings
from app.logging_config import get_logger

logger = get_logger("extraction.google_news_resolver")


@dataclass
class ResolutionResult:
    """
    Structured result model for Google News URL resolution.
    """
    original_url: str
    resolved_url: str
    is_google_news: bool
    success: bool
    failure_reason: Optional[str] = None
    resolution_method: str = "passthrough"


class GoogleNewsURLResolver:
    """
    Dedicated resolver for transforming Google News RSS URLs into actual publisher URLs.
    """

    GOOGLE_NEWS_DOMAINS = {"news.google.com", "google.com"}
    GOOGLE_NEWS_PATH_PATTERNS = [r"/rss/articles/", r"/articles/", r"/read/"]

    def __init__(
        self,
        timeout_seconds: Optional[int] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        self.timeout = timeout_seconds or settings.REQUEST_TIMEOUT_SECONDS
        self.user_agent = user_agent or settings.USER_AGENT

    @staticmethod
    def _is_valid_publisher_url(url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower().split(":")[0]
            path = parsed.path.lower()

            if parsed.scheme not in ("http", "https") or not host:
                return False

            blocked_domains = (
                "google.com",
                "gstatic.com",
                "googleusercontent.com",
                "googleapis.com",
                "ggpht.com",
            )

            if any(
                host == domain or host.endswith("." + domain)
                for domain in blocked_domains
            ):
                return False

            blocked_extensions = (
                ".png", ".jpg", ".jpeg", ".gif", ".webp",
                ".svg", ".ico", ".css", ".js", ".json", ".xml"
            )

            if path.endswith(blocked_extensions):
                return False

            return True

        except Exception:
            return False

    def is_google_news_url(self, url: str) -> bool:
        """
        Check whether a URL is a Google News RSS or article redirect URL.
        """
        if not url:
            return False
        try:
            parsed = urlparse(url.strip())
            netloc = parsed.netloc.lower()
            path = parsed.path.lower()

            domain_match = any(domain in netloc for domain in self.GOOGLE_NEWS_DOMAINS)
            if not domain_match:
                return False

            path_match = any(re.search(pat, path) for pat in self.GOOGLE_NEWS_PATH_PATTERNS)
            return path_match
        except Exception:
            return False

    def resolve(self, url: str) -> ResolutionResult:
        """
        Resolve input URL to direct publisher URL.
        If non-Google URL, returns input URL unchanged.
        """
        clean_url = url.strip()
        if not self.is_google_news_url(clean_url):
            return ResolutionResult(
                original_url=clean_url,
                resolved_url=clean_url,
                is_google_news=False,
                success=True,
                resolution_method="passthrough",
            )

        logger.info("Resolving Google News URL: %s", clean_url[:80])

        # Method 1: Primary - googlenewsdecoder library
        try:
            from googlenewsdecoder import gnewsdecoder
            res = gnewsdecoder(clean_url)
            if isinstance(res, dict) and res.get("status") is True:
                decoded_url = res.get("decoded_url")
                if decoded_url and self._is_valid_publisher_url(decoded_url):
                    logger.info("Successfully resolved Google News URL via gnewsdecoder: %s -> %s", clean_url[:60], decoded_url)
                    return ResolutionResult(
                        original_url=clean_url,
                        resolved_url=decoded_url,
                        is_google_news=True,
                        success=True,
                        resolution_method="gnewsdecoder",
                    )
        except Exception as exc:
            logger.debug("gnewsdecoder resolution failed for %s: %s", clean_url[:60], exc)

        # Method 2: Fallback - HTTP follow redirects
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
                response = client.get(clean_url)
                final_url = str(response.url)
                if self._is_valid_publisher_url(final_url):
                    logger.info("Successfully resolved Google News URL via HTTP redirect: %s -> %s", clean_url[:60], final_url)
                    return ResolutionResult(
                        original_url=clean_url,
                        resolved_url=final_url,
                        is_google_news=True,
                        success=True,
                        resolution_method="http_redirect",
                    )

                # Method 3: Fallback - HTML inspection for data-n-au or non-google links
                html_text = response.text
                data_n_au = re.findall(r'data-n-au="(https?://[^"]+)"', html_text)
                valid_data_n_au = next(
                    (candidate for candidate in data_n_au if self._is_valid_publisher_url(candidate)),
                    None,
                )
                if valid_data_n_au:
                    logger.info("Successfully resolved Google News URL via HTML data attribute: %s -> %s", clean_url[:60], valid_data_n_au)
                    return ResolutionResult(
                        original_url=clean_url,
                        resolved_url=valid_data_n_au,
                        is_google_news=True,
                        success=True,
                        resolution_method="html_data_attr",
                    )

                hrefs = re.findall(r'href="(https?://[^"]+)"', html_text)
                non_google_hrefs = [h for h in hrefs if self._is_valid_publisher_url(h)]
                if non_google_hrefs:
                    target_url = non_google_hrefs[0]
                    logger.info("Successfully resolved Google News URL via HTML link regex: %s -> %s", clean_url[:60], target_url)
                    return ResolutionResult(
                        original_url=clean_url,
                        resolved_url=target_url,
                        is_google_news=True,
                        success=True,
                        resolution_method="html_link_regex",
                    )

        except Exception as exc:
            logger.warning("HTTP fallback resolution failed for %s: %s", clean_url[:60], exc)

        logger.error("Failed to resolve Google News URL to publisher: %s", clean_url)
        return ResolutionResult(
            original_url=clean_url,
            resolved_url=clean_url,
            is_google_news=True,
            success=False,
            failure_reason="Google News URL resolution failed: could not extract direct publisher link",
            resolution_method="failed",
        )
