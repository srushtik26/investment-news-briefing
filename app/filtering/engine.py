"""
Hard Filter Engine Coordinator.

Executes deterministic validation checks in sequential order:
URL -> Source -> Date Freshness -> Story Type / Noise Rejection.
"""

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from app.logging_config import get_logger
from app.models.article import Article
from app.filtering.models import FilterResult
from app.filtering.rules import (
    BaseFilterRule,
    DateFilterRule,
    DomesticSourceFilterRule,
    SourceFilterRule,
    StoryTypeFilterRule,
    URLFilterRule,
)

logger = get_logger("filtering.engine")


class HardFilterEngine:
    """
    Coordinator executing all deterministic business filters on candidate news articles.
    """

    def __init__(self, custom_rules: Optional[List[BaseFilterRule]] = None) -> None:
        self.rules: List[BaseFilterRule] = custom_rules or [
            URLFilterRule(),
            SourceFilterRule(),
            DateFilterRule(),
            StoryTypeFilterRule(),
        ]
        logger.info(
            "HardFilterEngine initialized with %d deterministic rules: %s",
            len(self.rules),
            [r.rule_name for r in self.rules],
        )

    def filter_article(
        self,
        article: Article,
        now_utc: Optional[datetime] = None,
        max_age_hours: Optional[float] = None,
    ) -> FilterResult:
        """
        Evaluate an individual article through all deterministic filter rules.

        Args:
            article: Extracted Article instance.
            now_utc: Optional evaluation timestamp (defaults to UTC now).

        Returns:
            FilterResult indicating acceptance or specific rule failure.
        """
        current_time = now_utc or datetime.now(timezone.utc)

        for rule in self.rules:
            if isinstance(rule, DateFilterRule):
                result = rule.evaluate(article, now_utc=current_time, max_age_hours=max_age_hours)
            else:
                result = rule.evaluate(article, now_utc=current_time)
            if not result.is_accepted:
                logger.info(
                    "Article rejected [%s]: '%s' - Reason: %s",
                    result.rule_failed,
                    article.title[:50],
                    result.rejection_reason,
                )
                return result

        logger.debug("Article accepted through all hard filters: '%s'", article.title[:50])
        return FilterResult(
            is_accepted=True,
            article_url=article.url,
            article_title=article.title,
        )

    def evaluate(
        self,
        article: Article,
        now_utc: Optional[datetime] = None,
        max_age_hours: Optional[float] = None,
    ) -> FilterResult:
        """Alias for filter_article to ensure consistent API contract."""
        return self.filter_article(article, now_utc=now_utc, max_age_hours=max_age_hours)

    def filter_candidates(
        self,
        articles: List[Article],
        now_utc: Optional[datetime] = None,
        max_age_hours: Optional[float] = None,
    ) -> Tuple[List[Article], List[FilterResult]]:
        """
        Filter a batch of candidate articles.

        Args:
            articles: List of extracted articles.
            now_utc: Optional evaluation timestamp.

        Returns:
            Tuple of (accepted_articles, rejection_results).
        """
        current_time = now_utc or datetime.now(timezone.utc)
        accepted: List[Article] = []
        rejections: List[FilterResult] = []

        logger.info("Evaluating %d candidate articles through Hard Filter Engine...", len(articles))

        for article in articles:
            result = self.filter_article(article, now_utc=current_time, max_age_hours=max_age_hours)
            if result.is_accepted:
                accepted.append(article)
            else:
                rejections.append(result)

        logger.info(
            "Hard Filter Engine complete: %d accepted, %d rejected (out of %d total)",
            len(accepted),
            len(rejections),
            len(articles),
        )
        return accepted, rejections


class DomesticHardFilterEngine:
    """
    Lightweight filter engine for Domestic (General Trending India News) articles.

    Applies ONLY:
        1. URLFilterRule          — reject non-article hub/category URLs
        2. DomesticSourceFilterRule — must be a trusted domestic publisher
        3. DateFilterRule         — must be within 24h freshness window

    Does NOT apply:
        - SourceFilterRule (business financial whitelist)
        - StoryTypeFilterRule (business hard-event requirement)

    Domestic articles are general national news (Supreme Court, Cabinet, ISRO,
    weather, health, education, defence). They do NOT need to be corporate events.
    """

    def __init__(self) -> None:
        self.url_rule = URLFilterRule()
        self.source_rule = DomesticSourceFilterRule()
        self.date_rule = DateFilterRule()
        logger.info(
            "DomesticHardFilterEngine initialized: [URLFilterRule, DomesticSourceFilterRule, DateFilterRule]"
        )

    def filter_article(
        self,
        article: Article,
        now_utc: Optional[datetime] = None,
        max_age_hours: Optional[float] = None,
    ) -> FilterResult:
        """
        Evaluate a domestic candidate article through URL, source, and date rules only.
        Returns FilterResult; does NOT check for business hard events.
        """
        current_time = now_utc or datetime.now(timezone.utc)

        # 1. URL validity
        result = self.url_rule.evaluate(article, now_utc=current_time)
        if not result.is_accepted:
            logger.info(
                "Domestic article rejected [URL]: '%s' — %s",
                article.title[:50],
                result.rejection_reason,
            )
            return result

        # 2. Domestic source whitelist
        result = self.source_rule.evaluate(article, now_utc=current_time)
        if not result.is_accepted:
            logger.info(
                "Domestic article rejected [DOMESTIC_SOURCE]: '%s' — %s",
                article.title[:50],
                result.rejection_reason,
            )
            return result

        # 3. Freshness (24h)
        result = self.date_rule.evaluate(article, now_utc=current_time, max_age_hours=max_age_hours)
        if not result.is_accepted:
            logger.info(
                "Domestic article rejected [DATE]: '%s' — %s",
                article.title[:50],
                result.rejection_reason,
            )
            return result

        logger.debug("Domestic article accepted: '%s'", article.title[:50])
        return FilterResult(
            is_accepted=True,
            article_url=article.url,
            article_title=article.title,
        )

    def filter_candidates(
        self,
        articles: List[Article],
        now_utc: Optional[datetime] = None,
        max_age_hours: Optional[float] = None,
    ) -> Tuple[List[Article], List[FilterResult]]:
        """Filter a batch of domestic candidate articles."""
        current_time = now_utc or datetime.now(timezone.utc)
        accepted: List[Article] = []
        rejections: List[FilterResult] = []

        logger.info(
            "DomesticHardFilterEngine evaluating %d domestic candidates...", len(articles)
        )
        for article in articles:
            result = self.filter_article(article, now_utc=current_time, max_age_hours=max_age_hours)
            if result.is_accepted:
                accepted.append(article)
            else:
                rejections.append(result)

        logger.info(
            "DomesticHardFilterEngine complete: %d accepted, %d rejected",
            len(accepted),
            len(rejections),
        )
        return accepted, rejections

