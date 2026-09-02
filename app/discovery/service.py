"""
News Discovery Service.

Orchestrates business news candidate discovery across target regions,
categories, and publishers with automatic URL deduplication.
"""

from typing import Dict, List, Optional, Set

from config import get_settings
from app.logging_config import get_logger
from app.discovery.base import DiscoveryProvider
from app.discovery.mock_provider import MockDiscoveryProvider
from app.discovery.models import DiscoveredArticle
from app.discovery.queries import (
    INDIA_EVENT_CATEGORIES,
    OFFICIAL_INDIA_SOURCES,
    INTERNATIONAL_EVENT_CATEGORIES,
    SearchQueryBuilder,
)

logger = get_logger("discovery.service")


class NewsDiscoveryService:
    """
    Coordinator for discovering candidate business news stories.
    """

    def __init__(self, provider: Optional[DiscoveryProvider] = None) -> None:
        self.provider: DiscoveryProvider = provider or MockDiscoveryProvider()
        logger.info("NewsDiscoveryService initialized with provider: %s", self.provider.provider_name)

    def _deduplicate_candidates(self, articles: List[DiscoveredArticle]) -> List[DiscoveredArticle]:
        """
        Deduplicate candidates deterministically based on normalized URL.
        """
        seen_urls: Set[str] = set()
        unique_articles: List[DiscoveredArticle] = []

        for article in articles:
            norm_url = article.url.strip().lower().rstrip("/")
            if norm_url not in seen_urls:
                seen_urls.add(norm_url)
                unique_articles.append(article)

        return unique_articles

    def discover_india_news(
        self,
        categories: Optional[List[str]] = None,
        max_candidates: int = 20,
        max_per_query: int = 5,
    ) -> List[DiscoveredArticle]:
        """
        Discover candidate news articles for the India section across hard business categories.
        """
        all_categories = INDIA_EVENT_CATEGORIES
        target_cats = (
            {k: v for k, v in all_categories.items() if k in categories}
            if categories
            else all_categories
        )

        discovered: List[DiscoveredArticle] = []
        sources = SearchQueryBuilder.get_sources_for_country("India") + OFFICIAL_INDIA_SOURCES
        site_clause = " (" + " OR ".join([f"site:{s.domain}" for s in sources]) + ")"

        for cat_name, phrase_list in target_cats.items():
            for phrase in phrase_list:
                query = f"{phrase}{site_clause}"
                results = self.provider.discover(
                    query=query,
                    country="India",
                    max_results=max_per_query,
                    category_tag=cat_name,
                )
                discovered.extend(results)

                if len(self._deduplicate_candidates(discovered)) >= max_candidates * 2:
                    break

        unique_results = self._deduplicate_candidates(discovered)[:max_candidates]
        logger.info("Discovered %d unique candidate articles for India", len(unique_results))
        return unique_results

    def discover_international_news(
        self,
        categories: Optional[List[str]] = None,
        max_candidates: int = 20,
        max_per_query: int = 5,
    ) -> List[DiscoveredArticle]:
        """
        Discover candidate news articles for the International section across hard business categories.
        """
        all_categories = INTERNATIONAL_EVENT_CATEGORIES
        target_cats = (
            {k: v for k, v in all_categories.items() if k in categories}
            if categories
            else all_categories
        )

        discovered: List[DiscoveredArticle] = []
        sources = SearchQueryBuilder.get_accessible_sources_for_country("International")
        site_clause = " (" + " OR ".join([f"site:{s.domain}" for s in sources]) + ")"

        for cat_name, phrase_list in target_cats.items():
            for phrase in phrase_list:
                query = f"{phrase}{site_clause}"
                results = self.provider.discover(
                    query=query,
                    country="International",
                    max_results=max_per_query,
                    category_tag=cat_name,
                )
                discovered.extend(results)

                if len(self._deduplicate_candidates(discovered)) >= max_candidates * 2:
                    break

        unique_results = self._deduplicate_candidates(discovered)[:max_candidates]
        logger.info("Discovered %d unique candidate articles for International", len(unique_results))
        return unique_results

    def discover_domestic_news(
        self,
        categories: Optional[List[str]] = None,
        max_candidates: int = 20,
        max_per_query: int = 5,
    ) -> List[DiscoveredArticle]:
        """
        Discover Domestic India macro/policy candidates using a balanced category mix.

        Domestic quality rules became intentionally stricter around routine court/news
        noise. A simple concatenate-then-slice strategy could therefore exhaust the
        40-candidate reserve with many near-duplicate political reaction stories before
        high-value economy, crisis, security, infrastructure, and policy candidates were
        represented. This method keeps the same reserve size and verification gates while
        balancing discovery toward the topics the Domestic section is meant to surface.
        """
        from app.discovery.queries import DOMESTIC_EVENT_CATEGORIES, DOMESTIC_SOURCES

        all_categories = DOMESTIC_EVENT_CATEGORIES
        target_cats = (
            {k: v for k, v in all_categories.items() if k in categories}
            if categories
            else all_categories
        )

        site_clause = " (" + " OR ".join([f"site:{s.domain}" for s in DOMESTIC_SOURCES]) + ")"
        discovered_by_category: Dict[str, List[DiscoveredArticle]] = {}
        all_discovered: List[DiscoveredArticle] = []

        for cat_name, phrase_list in target_cats.items():
            cat_results: List[DiscoveredArticle] = []
            for phrase in phrase_list:
                query = f"{phrase}{site_clause}"
                results = self.provider.discover(
                    query=query,
                    country="India",
                    max_results=max_per_query,
                    category_tag=cat_name,
                )
                cat_results.extend(results)
                all_discovered.extend(results)

            discovered_by_category[cat_name] = self._deduplicate_candidates(cat_results)

        # Weighted reserve composition: politics/government and national economy first,
        # meaningful space for crises/security/infrastructure, and only a narrow court slot.
        # Quotas are scaled automatically when max_candidates differs from the production 40.
        priority_weights = [
            ("politics_and_government", 9),
            ("economy_and_national_crisis", 9),
            ("breaking_national", 6),
            ("defence_security", 5),
            ("weather_disaster_environment", 4),
            ("infrastructure_transport", 3),
            ("science_space_tech", 2),
            ("health_education", 1),
            ("courts_and_law", 1),
        ]
        weight_total = sum(weight for _, weight in priority_weights)

        selected: List[DiscoveredArticle] = []
        selected_urls: Set[str] = set()

        def add_article(article: DiscoveredArticle) -> bool:
            norm_url = article.url.strip().lower().rstrip("/")
            if not norm_url or norm_url in selected_urls:
                return False
            selected_urls.add(norm_url)
            selected.append(article)
            return True

        # First pass: fill topic-aware quotas.
        for cat_name, weight in priority_weights:
            if cat_name not in discovered_by_category:
                continue
            quota = max(1, round(max_candidates * weight / weight_total))
            added = 0
            for article in discovered_by_category[cat_name]:
                if add_article(article):
                    added += 1
                if added >= quota or len(selected) >= max_candidates:
                    break
            if len(selected) >= max_candidates:
                break

        # Second pass: if a category did not have enough unique results, backfill from
        # every discovered Domestic candidate without changing any downstream quality gate.
        if len(selected) < max_candidates:
            for article in self._deduplicate_candidates(all_discovered):
                add_article(article)
                if len(selected) >= max_candidates:
                    break

        unique_results = selected[:max_candidates]
        for article in unique_results:
            # Preserve the existing downstream section contract.
            article.category_tag = "domestic"

        logger.info(
            "Discovered %d balanced unique candidate articles for Domestic India",
            len(unique_results),
        )
        return unique_results

    def discover_all(
        self,
        max_india: int = 40,
        max_international: int = 40,
        max_domestic: int = 40,
    ) -> Dict[str, List[DiscoveredArticle]]:
        """
        Run discovery across Domestic, India Business, and International categories.
        """
        logger.info("Running full news discovery for Investment Committee briefing (Domestic + India + Intl)...")
        domestic_candidates = self.discover_domestic_news(max_candidates=max_domestic) if max_domestic > 0 else []
        india_candidates = self.discover_india_news(max_candidates=max_india)
        intl_candidates = self.discover_international_news(max_candidates=max_international)

        result: Dict[str, List[DiscoveredArticle]] = {
            "india": india_candidates,
            "international": intl_candidates,
        }
        if max_domestic > 0:
            result["domestic"] = domestic_candidates

        return result
