"""
News Discovery Service.

Orchestrates business news candidate discovery across target regions,
categories, and publishers with automatic URL deduplication.
"""

import re
from typing import Any, Dict, List, Optional, Set

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
        self.portfolio_discovery_executed: bool = False
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

    def discover_portfolio_news(
        self,
        max_candidates: int = 50,
        max_per_query: int = 15,
        log_callback: Optional[Any] = None,
        budget: Optional[Any] = None,
        target_qualified: int = 5,
        min_reserves: int = 3,
    ) -> List[DiscoveredArticle]:
        """
        Discover candidate news articles specifically for the 32 portfolio watchlist companies
        using efficient staged discovery:
            Step 1: Grouped/batched queries across holdings
            Step 2: Immediate URL deduplication
            Step 3: Identify companies with no results
            Step 4: Targeted fallback queries only for missing companies (bounded)
            Step 5: Early stop when enough candidates + reserves exist
        """
        from app.discovery.queries import SearchQueryBuilder, PORTFOLIO_EVENT_TERMS
        from app.ranking.watchlist import (
            PORTFOLIO_WATCHLIST,
            get_watchlist_match_details,
            match_portfolio_company,
        )

        def _log(msg: str):
            logger.info(msg)
            if log_callback:
                try:
                    log_callback(msg)
                except Exception:
                    pass

        _log("[PORTFOLIO_DISCOVERY_START]")
        self.portfolio_discovery_executed = True
        discovered: List[DiscoveredArticle] = []
        seen_urls: Set[str] = set()

        def _add_article(art: DiscoveredArticle) -> bool:
            norm_url = (art.url or "").strip().lower().rstrip("/")
            if norm_url and norm_url not in seen_urls:
                seen_urls.add(norm_url)
                art.category_tag = "india"
                discovered.append(art)
                return True
            return False

        # Step 1: Grouped/batched queries
        portfolio_queries = SearchQueryBuilder.build_portfolio_queries(include_site_filters=True)
        companies_checked = len(PORTFOLIO_WATCHLIST)
        matched_companies: Set[str] = set()

        for grp_name, query in portfolio_queries:
            _log(f'[PORTFOLIO_DISCOVERY_QUERY]\ngroup={grp_name}\nquery="{query}"')
            if budget:
                budget.record_portfolio_query()
            results = self.provider.discover(
                query=query,
                country="India",
                max_results=max_per_query,
                category_tag="india",
            )
            _log(f'[PORTFOLIO_DISCOVERY_RESULT]\nquery="{query}"\nresults={len(results)}')

            # Step 2: Deduplicate URLs immediately
            for r in results:
                _add_article(r)

            # Step 5 check: check if enough high-quality portfolio candidates exist
            for art in discovered:
                m = match_portfolio_company(text=art.title)
                if m and m.eligible_for_priority:
                    matched_companies.add(m.canonical_name)

            if len(matched_companies) >= (target_qualified + min_reserves):
                _log(f"[PORTFOLIO_EARLY_STOP] Found {len(matched_companies)} companies with material news across groups; early stopping")
                break

        # Step 3: Identify portfolio companies with no useful recent result
        missing_companies = [cname for cname, _ in PORTFOLIO_WATCHLIST if cname not in matched_companies]

        # Step 4: Run targeted fallback queries only for missing companies if we still need more candidates
        if len(matched_companies) < (target_qualified + min_reserves) and missing_companies:
            max_fallback_queries = min(len(missing_companies), (target_qualified + min_reserves) - len(matched_companies) + 2)
            sources = SearchQueryBuilder.get_sources_for_country("India")
            site_clause = " (" + " OR ".join([f"site:{s.domain}" for s in sources[:4]]) + ")"

            for cname in missing_companies[:max_fallback_queries]:
                fallback_query = f'"{cname}" {PORTFOLIO_EVENT_TERMS} when:1d{site_clause}'
                _log(f'[PORTFOLIO_FALLBACK_QUERY]\ncompany="{cname}"\nquery="{fallback_query}"')
                if budget:
                    budget.record_portfolio_query()
                fb_results = self.provider.discover(
                    query=fallback_query,
                    country="India",
                    max_results=5,
                    category_tag="india",
                )
                added_for_company = False
                for r in fb_results:
                    if _add_article(r):
                        m = match_portfolio_company(text=r.title)
                        if m and m.eligible_for_priority:
                            matched_companies.add(m.canonical_name)
                            added_for_company = True
                if not added_for_company:
                    logger.debug("PORTFOLIO_NO_MATERIAL_NEWS company=%s", cname)

                if len(matched_companies) >= (target_qualified + min_reserves):
                    break

        unique_results = discovered[:max_candidates]
        for article in unique_results:
            article.category_tag = "india"

        # Concise production logging
        _log(f"PORTFOLIO_COMPANIES_CHECKED={companies_checked}")
        _log(f"PORTFOLIO_DISCOVERY_URLS={len(seen_urls)}")
        _log(f"PORTFOLIO_CANDIDATES={len(unique_results)}")
        _log(f"PORTFOLIO_QUALIFIED={len(matched_companies)}")

        _log(f"[PORTFOLIO_DISCOVERY_END]\nqualified={len(unique_results)}\nselected={len(matched_companies)}")
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

        Domestic quality rules intentionally reject routine courts, opinion, and political
        reaction noise. A simple concatenate-then-slice strategy can exhaust the reserve
        before high-value economy, crisis, security, infrastructure, and policy candidates
        are represented. This keeps the same reserve size and verification gates while
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

        # Reject obvious foreign-only headlines that leak in through Indian publishers'
        # world sections. Geopolitical stories that explicitly mention India remain eligible.
        foreign_only_pattern = re.compile(
            r"\b(?:former\s+uk\s+pm|uk\s+prime\s+minister|british\s+prime\s+minister|"
            r"us\s+president|u\.s\.\s+president|white\s+house|european\s+union|"
            r"french\s+president|german\s+chancellor|canadian\s+prime\s+minister|"
            r"australian\s+prime\s+minister|japanese\s+prime\s+minister)\b",
            re.IGNORECASE,
        )
        india_context_pattern = re.compile(
            r"\b(?:india|indian|new\s+delhi|centre|central\s+government|union\s+government|"
            r"pm\s+modi|narendra\s+modi|rbi|sebi|lok\s+sabha|rajya\s+sabha|parliament)\b",
            re.IGNORECASE,
        )

        def add_article(article: DiscoveredArticle) -> bool:
            title = (article.title or "").strip()
            if foreign_only_pattern.search(title) and not india_context_pattern.search(title):
                return False

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

        # Second pass: backfill underfilled quotas from all discovered candidates without
        # changing downstream freshness, source, verification, or score thresholds.
        if len(selected) < max_candidates:
            for article in self._deduplicate_candidates(all_discovered):
                add_article(article)
                if len(selected) >= max_candidates:
                    break

        unique_results = selected[:max_candidates]
        for article in unique_results:
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
        budget: Optional[Any] = None,
    ) -> Dict[str, List[DiscoveredArticle]]:
        """
        Run staged discovery across Domestic, India Business, and International categories,
        with Portfolio-First discovery executing before general India discovery.
        General India discovery fills gaps based on portfolio candidate yield.
        """
        logger.info("Running staged news discovery for Investment Committee briefing (Portfolio -> General India -> Domestic -> Intl)...")
        domestic_candidates = self.discover_domestic_news(max_candidates=max_domestic) if max_domestic > 0 else []
        portfolio_candidates = self.discover_portfolio_news(
            max_candidates=50,
            max_per_query=15,
            budget=budget,
            target_qualified=5,
            min_reserves=3,
        )

        # Gap-filling general India discovery: only fetch what is needed for 5 + reserves
        needed_general = max(10, max_india - len(portfolio_candidates))
        india_candidates = self.discover_india_news(max_candidates=needed_general)
        intl_candidates = self.discover_international_news(max_candidates=max_international)

        # Portfolio candidates are ordered first in the India business candidate pool
        combined_india = self._deduplicate_candidates(portfolio_candidates + india_candidates)[: max_india * 2]

        result: Dict[str, List[DiscoveredArticle]] = {
            "portfolio": portfolio_candidates,
            "india": combined_india,
            "international": intl_candidates,
        }
        if max_domestic > 0:
            result["domestic"] = domestic_candidates

        return result

