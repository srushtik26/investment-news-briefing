"""
Article Extractor Coordinator.

Orchestrates Google News URL resolution, HTTP fetching, HTML parsing, primary & fallback metadata
extraction, date verification, and detailed failure handling for news article URLs.
"""

from datetime import datetime, timezone
import re
from typing import Optional

from bs4 import BeautifulSoup

from app.logging_config import get_logger
from app.models.article import Article
from app.models.enums import NewsCategory
from app.extraction.http_client import ArticleFetcher
from app.extraction.html_parser import HTMLArticleParser, ParsedArticleData, MIN_ARTICLE_WORD_COUNT
from app.extraction.google_news_resolver import GoogleNewsURLResolver, ResolutionResult
from app.extraction.models import ExtractionResult

logger = get_logger("extraction.service")


class ArticleExtractor:
    """
    Main extraction service for retrieving and parsing full article contents.
    """

    def __init__(
        self,
        fetcher: Optional[ArticleFetcher] = None,
        parser: Optional[HTMLArticleParser] = None,
        resolver: Optional[GoogleNewsURLResolver] = None,
    ) -> None:
        self.fetcher = fetcher or ArticleFetcher()
        self.parser = parser or HTMLArticleParser()
        self.resolver = resolver or GoogleNewsURLResolver()

    def extract(
        self,
        url: str,
        source_name: Optional[str] = None,
        candidate_title: Optional[str] = None,
        candidate_category: Optional[str] = None,
        candidate_pub_date: Optional[datetime] = None,
    ) -> ExtractionResult:
        """
        Resolve candidate URL and extract full article content.

        Args:
            url: Candidate URL (Google News RSS URL or direct link).
            source_name: Optional discovery source name.
            candidate_title: Optional candidate title from discovery.
            candidate_category: Optional region/category tag.

        Returns:
            ExtractionResult indicating success or descriptive failure.
        """
        original_url = url.strip()
        logger.info("Extracting article from URL: %s", original_url)

        # 1. Resolve URL (Google News RSS -> Direct Publisher URL)
        resolution: ResolutionResult = self.resolver.resolve(original_url)
        resolved_url = resolution.resolved_url

        if resolution.is_google_news and not resolution.success:
            logger.warning("Resolution failed for Google News URL %s: %s", original_url[:60], resolution.failure_reason)
            return ExtractionResult(
                success=False,
                url=original_url,
                original_url=original_url,
                resolved_url=resolved_url,
                status_code=None,
                error_message=resolution.failure_reason or "Google News URL resolution failed",
                date_verified=False,
                extraction_method="none",
                word_count=0,
            )

        # Reject if resolved URL is still a Google News landing page
        if self.resolver.is_google_news_url(resolved_url):
            logger.warning("Resolved URL is still a Google News page: %s", resolved_url)
            return ExtractionResult(
                success=False,
                url=original_url,
                original_url=original_url,
                resolved_url=resolved_url,
                status_code=None,
                error_message="URL is a Google News landing page, not a direct article",
                date_verified=False,
                extraction_method="none",
                word_count=0,
            )

        # 2. Fetch raw HTML from resolved publisher URL
        fetch_success, html, status_code, error_msg = self.fetcher.fetch_html(resolved_url)
        if not fetch_success or not html:
            logger.warning("Failed to fetch article from %s: %s (Status: %s)", resolved_url, error_msg, status_code)
            return ExtractionResult(
                success=False,
                url=resolved_url,
                original_url=original_url,
                resolved_url=resolved_url,
                status_code=status_code,
                error_message=error_msg or "Failed to retrieve HTML content",
                date_verified=False,
                extraction_method="none",
                word_count=0,
            )

        # 3. Parse and Validate HTML using Primary + Fallback strategies
        return self.extract_from_html(
            html=html,
            url=resolved_url,
            original_url=original_url,
            source_name=source_name,
            candidate_title=candidate_title,
            candidate_category=candidate_category,
            candidate_pub_date=candidate_pub_date,
            status_code=status_code or 200,
        )

    def extract_from_html(
        self,
        html: str,
        url: str,
        original_url: Optional[str] = None,
        source_name: Optional[str] = None,
        candidate_title: Optional[str] = None,
        candidate_category: Optional[str] = None,
        candidate_pub_date: Optional[datetime] = None,
        status_code: int = 200,
    ) -> ExtractionResult:
        """
        Parse raw HTML content using primary parser with a robust fallback strategy.
        """
        orig_url = original_url or url

        # Reject obvious Google News landing page titles or empty responses
        if self._is_google_landing_page(html, candidate_title):
            logger.warning("Rejected extraction for %s: Page is a Google News landing page", url)
            return ExtractionResult(
                success=False,
                url=url,
                original_url=orig_url,
                resolved_url=url,
                status_code=status_code,
                error_message="Page is a Google News landing page, not a direct article",
                date_verified=False,
                extraction_method="none",
                word_count=0,
            )

        # Primary Extraction Strategy
        parsed: ParsedArticleData = self.parser.parse(
            html=html,
            url=url,
            fallback_source=source_name,
            fallback_title=candidate_title,
        )

        extraction_method = "primary"

        # Check if Primary extraction succeeded
        if not parsed.is_article or parsed.word_count < MIN_ARTICLE_WORD_COUNT:
            logger.info("Primary extraction yielded insufficient text (%d words) for %s. Trying fallback extractor...", parsed.word_count, url)
            
            # Fallback Extraction Strategy
            fallback_parsed = self._run_fallback_extraction(
                html=html,
                url=url,
                fallback_source=source_name,
                fallback_title=candidate_title,
            )
            
            if fallback_parsed.is_article and fallback_parsed.word_count >= 25:
                logger.info("Fallback extraction succeeded for %s (%d words)", url, fallback_parsed.word_count)
                parsed = fallback_parsed
                extraction_method = "fallback"
            else:
                reason = (
                    f"Page content could not be confirmed as a valid article "
                    f"(primary_words={parsed.word_count}, fallback_words={fallback_parsed.word_count}, "
                    f"title='{(parsed.title or candidate_title or '')[:40]}...')"
                )
                logger.warning("Extraction validation failed for %s: %s", url, reason)
                return ExtractionResult(
                    success=False,
                    url=url,
                    original_url=orig_url,
                    resolved_url=url,
                    status_code=status_code,
                    error_message=reason,
                    date_verified=parsed.date_verified or fallback_parsed.date_verified,
                    extraction_method="failed",
                    word_count=parsed.word_count,
                )

        # Resolve Category
        category_enum = NewsCategory.UNKNOWN
        if candidate_category:
            norm_cat = candidate_category.lower()
            if "india" in norm_cat:
                category_enum = NewsCategory.INDIA
            elif "international" in norm_cat or "global" in norm_cat or "us" in norm_cat:
                category_enum = NewsCategory.INTERNATIONAL

        # Resolve Publication Date (Use HTML parsed date or fallback to RSS candidate pub_date)
        pub_date = parsed.published_at or candidate_pub_date
        date_verified = parsed.date_verified or (candidate_pub_date is not None)

        # Build Article model with resolved publisher URL
        article = Article(
            title=parsed.title or candidate_title or "Untitled Article",
            url=url,  # Direct publisher URL
            source_name=parsed.source_name or source_name or "Unknown Source",
            published_at=pub_date,
            author=parsed.author,
            extracted_at=datetime.now(timezone.utc),
            content_text=parsed.content_text,
            summary=parsed.summary,
            category=category_enum,
            is_verified_url=True,
            date_verified=date_verified,
            is_valid_date=date_verified,
            metadata={
                **parsed.metadata,
                "original_url": orig_url,
                "resolved_url": url,
                "extraction_method": extraction_method,
                "word_count": parsed.word_count,
            },
        )

        logger.info(
            "Successfully extracted article '%s' (%d words, source='%s', method='%s', date_verified=%s)",
            article.title[:50],
            parsed.word_count,
            article.source_name,
            extraction_method,
            parsed.date_verified,
        )

        return ExtractionResult(
            success=True,
            url=url,
            original_url=orig_url,
            resolved_url=url,
            article=article,
            status_code=status_code,
            date_verified=parsed.date_verified,
            extraction_method=extraction_method,
            word_count=parsed.word_count,
        )

    def _is_google_landing_page(self, html: str, candidate_title: Optional[str] = None) -> bool:
        """Check if HTML is a Google News intermediary page."""
        if not html:
            return True
        soup = BeautifulSoup(html[:10000], "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else ""

        if title == "Google News" or title.startswith("Google News - "):
            # Check if there is actual article text beyond Google boilerplate
            body_text = soup.get_text()
            if len(body_text.split()) < 50:
                return True
        return False

    def _run_fallback_extraction(
        self,
        html: str,
        url: str,
        fallback_source: Optional[str] = None,
        fallback_title: Optional[str] = None,
    ) -> ParsedArticleData:
        """
        Fallback extraction strategy using trafilatura (if installed) or BeautifulSoup paragraph heuristic.
        """
        # Try trafilatura first if available
        try:
            import trafilatura
            downloaded = trafilatura.extract(html, include_links=False, include_comments=False)
            if downloaded:
                words = downloaded.split()
                if len(words) >= 25:
                    return ParsedArticleData(
                        title=fallback_title or "Article",
                        content_text=downloaded.strip(),
                        source_name=fallback_source,
                        is_article=True,
                        word_count=len(words),
                        date_verified=False,
                    )
        except Exception as exc:
            logger.debug("trafilatura fallback failed: %s", exc)

        # BeautifulSoup Paragraph Heuristic Fallback
        try:
            soup = BeautifulSoup(html, "html.parser")

            # Remove noise tags
            for elem in soup(["script", "style", "nav", "footer", "header", "aside"]):
                elem.decompose()

            # Find all paragraph blocks
            paragraphs = [p.get_text(separator=" ", strip=True) for p in soup.find_all("p")]
            valid_paragraphs = [p for p in paragraphs if len(p) > 25 and not any(p.lower().startswith(x) for x in ("cookie", "subscribe", "privacy"))]
            
            combined_text = "\n\n".join(valid_paragraphs)
            word_count = len(combined_text.split())

            title = fallback_title
            if soup.title and soup.title.string and not title:
                title = soup.title.string.strip()

            return ParsedArticleData(
                title=title or "Article",
                content_text=combined_text,
                source_name=fallback_source,
                is_article=word_count >= 25,
                word_count=word_count,
                date_verified=False,
            )
        except Exception as exc:
            logger.debug("BS4 fallback failed: %s", exc)

        return ParsedArticleData(
            title=fallback_title or "",
            content_text="",
            source_name=fallback_source,
            is_article=False,
            word_count=0,
        )
