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

    def filter_candidates(
        self,
        articles: List[Article],
        now_utc: Optional[datetime] = None,
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
            result = self.filter_article(article, now_utc=current_time)
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
