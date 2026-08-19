"""
HTML Article Parser.

Extracts structured metadata (JSON-LD, OpenGraph, meta tags), cleans article body
text, and strictly verifies publication dates.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import email.utils
import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from app.logging_config import get_logger

logger = get_logger("extraction.parser")

# Minimum word count to qualify as a legitimate article body
MIN_ARTICLE_WORD_COUNT = 35


@dataclass
class ParsedArticleData:
    """Structured data extracted from an HTML page."""
    title: str
    content_text: str
    published_at: Optional[datetime] = None
    author: Optional[str] = None
    source_name: Optional[str] = None
    summary: Optional[str] = None
    date_verified: bool = False
    is_article: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    word_count: int = 0


class HTMLArticleParser:
    """
    Parses HTML content using structured metadata, OpenGraph, semantic tags,
    and heuristic text extraction.
    """

    # Unwanted HTML elements to strip before body extraction
    UNWANTED_SELECTORS = [
        "script", "style", "noscript", "iframe", "header", "footer", "nav",
        "aside", "form", "svg", "button", ".ad", ".advertisement", ".ads",
        ".social-share", ".share-buttons", ".comments", ".comment-section",
        ".related-stories", ".related-articles", ".newsletter-signup",
        ".cookie-banner", ".privacy-prompt", ".sidebar", ".menu"
    ]

    # Common article content container selectors in news websites
    ARTICLE_CONTAINER_SELECTORS = [
        '[itemprop="articleBody"]',
        "article",
        ".article-body",
        ".story-body",
        ".article-content",
        ".story-content",
        ".article__content",
        ".article__body",
        ".story__content",
        ".main-content",
        "#article-body",
        "#story-body",
        ".entry-content",
        ".content-article",
    ]

    def parse(
        self,
        html: str,
        url: str = "",
        fallback_source: Optional[str] = None,
        fallback_title: Optional[str] = None,
    ) -> ParsedArticleData:
        """
        Parse HTML and extract metadata, publication date, and body text.
        """
        if not html or not html.strip():
            return ParsedArticleData(
                title=fallback_title or "",
                content_text="",
                source_name=fallback_source,
                is_article=False,
            )

        soup = BeautifulSoup(html, "html.parser")

        # 1. Structured metadata extraction (JSON-LD)
        json_ld_data = self._extract_json_ld(soup)

        # 2. Meta tags and OpenGraph extraction
        meta_data = self._extract_meta_tags(soup)

        # 3. Resolve Title
        title = (
            json_ld_data.get("title")
            or meta_data.get("title")
            or self._extract_tag_title(soup)
            or fallback_title
            or ""
        ).strip()

        # 4. Resolve Publication Date (Strict - No Guessing)
        raw_date_str = (
            json_ld_data.get("published_at")
            or meta_data.get("published_at")
            or self._extract_time_tag_date(soup)
        )
        published_at, date_verified = self._parse_date_strictly(raw_date_str)

        # 5. Resolve Author
        author = (
            json_ld_data.get("author")
            or meta_data.get("author")
            or self._extract_author_byline(soup)
        )

        # 6. Resolve Source / Publisher
        source_name = (
            json_ld_data.get("source_name")
            or meta_data.get("source_name")
            or fallback_source
            or self._infer_source_from_url(url)
        )

        # 7. Resolve Summary
        summary = (
            json_ld_data.get("summary")
            or meta_data.get("description")
        )

        # 8. Extract and Clean Body Text
        content_text = json_ld_data.get("body_text") or self._extract_body_text(soup)
        word_count = len(content_text.split()) if content_text else 0

        # 9. Verify legitimate article criteria
        is_article = bool(title and word_count >= MIN_ARTICLE_WORD_COUNT)

        return ParsedArticleData(
            title=title,
            content_text=content_text,
            published_at=published_at,
            author=author,
            source_name=source_name,
            summary=summary,
            date_verified=date_verified,
            is_article=is_article,
            metadata={
                "json_ld": bool(json_ld_data),
                "open_graph": bool(meta_data),
                "raw_date_found": raw_date_str,
            },
            word_count=word_count,
        )

    def _extract_json_ld(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extract structured NewsArticle or Article metadata from JSON-LD scripts."""
        data: Dict[str, Any] = {}
        scripts = soup.find_all("script", type="application/ld+json")

        for script in scripts:
            if not script.string:
                continue
            try:
                raw_json = json.loads(script.string.strip())
                items = raw_json if isinstance(raw_json, list) else [raw_json]

                # Check if wrapped in @graph
                if isinstance(raw_json, dict) and "@graph" in raw_json:
                    items = raw_json["@graph"]

                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_type = str(item.get("@type", ""))
                    if any(t in item_type for t in ("Article", "NewsArticle", "Report", "BlogPosting", "TechArticle")):
                        if "headline" in item and not data.get("title"):
                            data["title"] = str(item["headline"])
                        elif "name" in item and not data.get("title"):
                            data["title"] = str(item["name"])

                        if "datePublished" in item and not data.get("published_at"):
                            data["published_at"] = str(item["datePublished"])
                        elif "dateCreated" in item and not data.get("published_at"):
                            data["published_at"] = str(item["dateCreated"])

                        # Author extraction
                        if "author" in item and not data.get("author"):
                            author_obj = item["author"]
                            if isinstance(author_obj, dict):
                                data["author"] = str(author_obj.get("name", ""))
                            elif isinstance(author_obj, list) and len(author_obj) > 0:
                                first = author_obj[0]
                                data["author"] = str(first.get("name", "")) if isinstance(first, dict) else str(first)
                            elif isinstance(author_obj, str):
                                data["author"] = author_obj

                        # Publisher extraction
                        if "publisher" in item and not data.get("source_name"):
                            pub = item["publisher"]
                            data["source_name"] = str(pub.get("name", "")) if isinstance(pub, dict) else str(pub)

                        if "description" in item and not data.get("summary"):
                            data["summary"] = str(item["description"])

                        if "articleBody" in item and len(str(item["articleBody"]).split()) >= MIN_ARTICLE_WORD_COUNT:
                            data["body_text"] = str(item["articleBody"]).strip()

            except Exception as exc:
                logger.debug("Failed parsing JSON-LD script block: %s", exc)

        return data

    def _extract_meta_tags(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extract OpenGraph and meta tag attributes."""
        meta: Dict[str, Any] = {}

        # 1. Title
        for prop in ("og:title", "twitter:title"):
            tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content"):
                meta["title"] = tag["content"].strip()
                break

        # 2. Date
        date_props = [
            "article:published_time", "article:modified_time", "og:pubdate",
            "pubdate", "publishdate", "date", "dc.date", "dc.date.issued",
            "parsely-pub-date", "sailthru.date", "bt:pubdate"
        ]
        for prop in date_props:
            tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content"):
                meta["published_at"] = tag["content"].strip()
                break

        # 3. Author
        author_props = [
            "article:author", "author", "twitter:creator",
            "parsely-author", "sailthru.author", "byl"
        ]
        for prop in author_props:
            tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content"):
                meta["author"] = tag["content"].strip()
                break

        # 4. Source / Site Name
        for prop in ("og:site_name", "twitter:site", "application-name"):
            tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content"):
                meta["source_name"] = tag["content"].strip()
                break

        # 5. Description
        for prop in ("og:description", "twitter:description", "description"):
            tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content"):
                meta["description"] = tag["content"].strip()
                break

        return meta

    def _extract_tag_title(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract title from HTML <title> or <h1> tag."""
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            return h1.get_text(strip=True)

        if soup.title and soup.title.string:
            raw = soup.title.string.strip()
            # Clean common publisher suffix (e.g., 'Article Title | Reuters' -> 'Article Title')
            for sep in (" | ", " - ", " : "):
                if sep in raw:
                    raw = raw.rsplit(sep, 1)[0].strip()
            return raw
        return None

    def _extract_time_tag_date(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract datetime from HTML5 <time> tag."""
        time_tag = soup.find("time")
        if time_tag:
            if time_tag.get("datetime"):
                return time_tag["datetime"].strip()
            if time_tag.get("content"):
                return time_tag["content"].strip()
            if time_tag.get_text(strip=True):
                return time_tag.get_text(strip=True)
        return None

    def _extract_author_byline(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract author byline from HTML byline elements."""
        for sel in [".byline", ".author", '[rel="author"]', ".article__author", ".story-author"]:
            elem = soup.select_one(sel)
            if elem and elem.get_text(strip=True):
                byline_text = elem.get_text(strip=True)
                # Strip leading 'By '
                if byline_text.lower().startswith("by "):
                    byline_text = byline_text[3:].strip()
                if len(byline_text) < 60:
                    return byline_text
        return None

    def _parse_date_strictly(self, raw_date_str: Optional[str]) -> tuple[Optional[datetime], bool]:
        """
        Strictly parse publication date string into UTC datetime.
        Returns (parsed_datetime, is_verified). Never guesses or invents dates.
        """
        if not raw_date_str or not raw_date_str.strip():
            return None, False

        cleaned = raw_date_str.strip()

        # 1. Try ISO 8601 parsing (e.g. 2026-08-18T08:30:00Z or 2026-08-18T08:30:00+05:30)
        try:
            # Handle trailing 'Z' for ISO parsing
            iso_str = cleaned.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc), True
        except Exception:
            pass

        # 2. Try RFC 2822 format (e.g. Tue, 18 Aug 2026 08:30:00 GMT)
        try:
            dt = email.utils.parsedate_to_datetime(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc), True
        except Exception:
            pass

        # 3. Try standard date patterns (e.g., YYYY-MM-DD, YYYY/MM/DD, DD-MM-YYYY)
        match_iso = re.search(r"(\d{4})[/-](\d{2})[/-](\d{2})", cleaned)
        if match_iso:
            try:
                year, month, day = map(int, match_iso.groups())
                dt = datetime(year, month, day, tzinfo=timezone.utc)
                return dt, True
            except Exception:
                pass

        match_dmy = re.search(r"(\d{2})[/-](\d{2})[/-](\d{4})", cleaned)
        if match_dmy:
            try:
                day, month, year = map(int, match_dmy.groups())
                dt = datetime(year, month, day, tzinfo=timezone.utc)
                return dt, True
            except Exception:
                pass

        # 4. Try Month DD, YYYY or DD Month YYYY (e.g., August 18, 2026 or 18 Aug 2026)
        months_map = {
            "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
            "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
            "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
            "nov": 11, "november": 11, "dec": 12, "december": 12
        }

        # Match 'Aug 18, 2026' or 'August 18 2026'
        match_mdy = re.search(r"([a-zA-Z]+)\s+(\d{1,2}),?\s+(\d{4})", cleaned)
        if match_mdy:
            m_str, d_str, y_str = match_mdy.groups()
            m_num = months_map.get(m_str.lower())
            if m_num:
                try:
                    dt = datetime(int(y_str), m_num, int(d_str), tzinfo=timezone.utc)
                    return dt, True
                except Exception:
                    pass

        # Match '18 Aug 2026' or '18 August 2026'
        match_dmy_str = re.search(r"(\d{1,2})\s+([a-zA-Z]+)\s+(\d{4})", cleaned)
        if match_dmy_str:
            d_str, m_str, y_str = match_dmy_str.groups()
            m_num = months_map.get(m_str.lower())
            if m_num:
                try:
                    dt = datetime(int(y_str), m_num, int(d_str), tzinfo=timezone.utc)
                    return dt, True
                except Exception:
                    pass

        logger.debug("Could not reliably parse date string: '%s'. Marking date_verified=False", raw_date_str)
        return None, False

    def _extract_body_text(self, soup: BeautifulSoup) -> str:
        """
        Extract clean, readable article body paragraphs from HTML.
        """
        # Create a working copy of soup to modify safely
        soup_copy = BeautifulSoup(str(soup), "html.parser")

        # Strip unwanted elements
        for selector in self.UNWANTED_SELECTORS:
            for elem in soup_copy.select(selector):
                elem.decompose()

        # Locate article body container
        container: Optional[Tag] = None
        for sel in self.ARTICLE_CONTAINER_SELECTORS:
            match = soup_copy.select_one(sel)
            if match:
                container = match
                break

        if container is None:
            container = soup_copy.find("main") or soup_copy.find("body")

        if container is None:
            return ""

        # Extract paragraphs
        paragraphs = container.find_all("p")
        cleaned_paragraphs: List[str] = []

        for p in paragraphs:
            text = p.get_text(separator=" ", strip=True)
            # Filter out boilerplate / short noise
            if len(text) < 20:
                continue
            if any(text.lower().startswith(prefix) for prefix in (
                "click here to subscribe", "also read:", "follow us on", "read more:",
                "subscribe to our newsletter", "sign up for", "advertisement", "disclaimer:"
            )):
                continue

            cleaned_paragraphs.append(text)

        return "\n\n".join(cleaned_paragraphs)

    def _infer_source_from_url(self, url: str) -> Optional[str]:
        """Infer source publisher from URL domain name as fallback."""
        if not url:
            return None
        netloc = urlparse(url).netloc.lower()
        if "economictimes" in netloc:
            return "The Economic Times"
        if "business-standard" in netloc:
            return "Business Standard"
        if "livemint" in netloc:
            return "Livemint"
        if "financialexpress" in netloc:
            return "Financial Express"
        if "reuters" in netloc:
            return "Reuters"
        if "bloomberg" in netloc:
            return "Bloomberg"
        if "cnbc" in netloc:
            return "CNBC"
        if "ft.com" in netloc:
            return "Financial Times"
        if "wsj.com" in netloc:
            return "Wall Street Journal"
        return netloc
