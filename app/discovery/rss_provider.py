"""
Google News RSS Discovery Provider.

Fetches live candidate business news articles via RSS search endpoints
without requiring external paid API keys or headless browsers.
"""

from datetime import datetime, timezone
import email.utils
from typing import List, Optional
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from config import get_settings
from app.logging_config import get_logger
from app.discovery.base import DiscoveryProvider
from app.discovery.models import DiscoveredArticle

logger = get_logger("discovery.rss")


class GoogleNewsRSSDiscoveryProvider(DiscoveryProvider):
    """
    Discovery provider backed by Google News RSS search endpoints.
    """

    BASE_RSS_URL = "https://news.google.com/rss/search"

    def __init__(
        self,
        timeout_seconds: Optional[int] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        self.timeout = timeout_seconds or settings.REQUEST_TIMEOUT_SECONDS
        self.user_agent = user_agent or settings.USER_AGENT

    @property
    def provider_name(self) -> str:
        return "GoogleNewsRSSDiscoveryProvider"

    def _get_geo_params(self, country: str) -> dict[str, str]:
        """Return locale parameters for India or International."""
        norm = country.strip().title()
        if norm in ("India", "In"):
            return {"hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
        return {"hl": "en-US", "gl": "US", "ceid": "US:en"}

    def _parse_pub_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Safely parse RFC 2822 or ISO dates."""
        if not date_str:
            return None
        try:
            dt = email.utils.parsedate_to_datetime(date_str)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _clean_snippet(self, raw_desc: Optional[str]) -> Optional[str]:
        """Strip HTML tags from RSS description/snippet."""
        if not raw_desc:
            return None
        try:
            soup = BeautifulSoup(raw_desc, "html.parser")
            return soup.get_text(separator=" ", strip=True)
        except Exception:
            return raw_desc.strip()

    def discover(
        self,
        query: str,
        country: str,
        max_results: int = 10,
        category_tag: Optional[str] = None,
    ) -> List[DiscoveredArticle]:
        """
        Query Google News RSS feed for given query and country.
        """
        geo = self._get_geo_params(country)
        encoded_q = quote_plus(query.strip())
        url = f"{self.BASE_RSS_URL}?q={encoded_q}&hl={geo['hl']}&gl={geo['gl']}&ceid={geo['ceid']}"

        headers = {"User-Agent": self.user_agent}

        logger.debug("Executing RSS discovery query for %s: '%s'", country, query)
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                xml_content = response.text
        except Exception as exc:
            logger.warning("RSS discovery request failed for query '%s': %s", query, exc)
            return []

        return self.parse_rss_feed(
            xml_content=xml_content,
            query=query,
            country=country,
            max_results=max_results,
            category_tag=category_tag,
        )

    def parse_rss_feed(
        self,
        xml_content: str,
        query: str,
        country: str,
        max_results: int = 10,
        category_tag: Optional[str] = None,
    ) -> List[DiscoveredArticle]:
        """
        Parse RSS XML content into DiscoveredArticle models.
        """
        results: List[DiscoveredArticle] = []
        if not xml_content or not xml_content.strip():
            return results

        try:
            root = ET.fromstring(xml_content)
            channel = root.find("channel")
            if channel is None:
                return results

            items = channel.findall("item")
            for item in items[: max_results * 2]:
                title_elem = item.find("title")
                link_elem = item.find("link")
                pub_elem = item.find("pubDate")
                desc_elem = item.find("description")
                source_elem = item.find("source")

                if title_elem is None or not title_elem.text or link_elem is None or not link_elem.text:
                    continue

                raw_title = title_elem.text.strip()
                raw_url = link_elem.text.strip()
                pub_date = self._parse_pub_date(pub_elem.text if pub_elem is not None else None)
                snippet = self._clean_snippet(desc_elem.text if desc_elem is not None else None)

                # Extract source name and clean title
                if source_elem is not None and source_elem.text:
                    source_name = source_elem.text.strip()
                    if raw_title.endswith(f" - {source_name}"):
                        raw_title = raw_title[: -len(f" - {source_name}")].strip()
                    elif " - " in raw_title:
                        raw_title = raw_title.rsplit(" - ", 1)[0].strip()
                elif " - " in raw_title:
                    parts = raw_title.rsplit(" - ", 1)
                    raw_title = parts[0].strip()
                    source_name = parts[1].strip()
                else:
                    source_name = "Unknown Source"

                try:
                    article = DiscoveredArticle(
                        title=raw_title,
                        url=raw_url,
                        source=source_name,
                        snippet=snippet,
                        published_at=pub_date,
                        search_query=query,
                        country=country,
                        category_tag=category_tag,
                    )
                    results.append(article)
                except Exception as val_err:
                    logger.debug("Skipping invalid RSS article entry: %s", val_err)
                    continue

                if len(results) >= max_results:
                    break

        except ET.ParseError as parse_err:
            logger.error("Failed to parse RSS XML response: %s", parse_err)
        except Exception as exc:
            logger.error("Unexpected error parsing RSS feed: %s", exc, exc_info=True)

        return results
