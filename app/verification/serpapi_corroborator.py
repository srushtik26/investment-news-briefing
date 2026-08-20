"""
SerpAPI Secondary Corroboration Fallback Module.

Used ONLY as a secondary fallback when normal Google News RSS corroboration fails
to find an independent second source for a single-source business event.

Design & Cost Control Principles:
  - Never replaces Google News RSS discovery.
  - Never called unless normal RSS corroboration fails.
  - Skipped cleanly if SERPAPI_API_KEY is not set or empty.
  - Maximum 1 query per single-source event.
  - Maximum MAX_SERPAPI_SEARCHES_PER_RUN queries per pipeline run (default: 8).
  - Caches identical queries within the same run.
  - Extracted candidates must be verified by TwoSourceVerifier.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlparse
import requests

from config import get_settings
from app.logging_config import get_logger
from app.models.article import Article
from app.models.enums import NewsCategory
from app.models.event import Event
from app.verification.corroborator import ActiveCorroborator, CorroborationResult
from app.verification.verifier import TwoSourceVerifier

logger = get_logger("verification.serpapi_corroborator")

# Module-level run counter and query cache
_run_serpapi_count = 0
_serpapi_query_cache: Dict[str, List[dict]] = {}


def reset_serpapi_counter() -> None:
    """Reset the per-run SerpAPI search counter and query cache."""
    global _run_serpapi_count, _serpapi_query_cache
    _run_serpapi_count = 0
    _serpapi_query_cache.clear()


def get_serpapi_count() -> int:
    """Return the number of SerpAPI search queries executed this run."""
    return _run_serpapi_count


class SerpAPICorroborator:
    """
    Optional secondary corroboration provider using SerpAPI Google News search engine.
    """

    def __init__(self, extractor=None, api_key: Optional[str] = None, max_searches: Optional[int] = None) -> None:
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.SERPAPI_API_KEY
        self.max_searches = max_searches if max_searches is not None else settings.MAX_SERPAPI_SEARCHES_PER_RUN
        self._extractor = extractor
        self._verifier = TwoSourceVerifier()
        self._active_corroborator = ActiveCorroborator()

    @property
    def has_api_key(self) -> bool:
        """Return True if a non-empty SerpAPI API key is configured."""
        return bool(self.api_key and self.api_key.strip())

    def _get_extractor(self):
        if self._extractor is None:
            from app.extraction.extractor import ArticleExtractor
            self._extractor = ArticleExtractor()
        return self._extractor

    def _build_targeted_query(self, article: Article) -> str:
        """
        Build a concise, highly specific search query string using:
        - primary company/entity
        - second entity if available
        - event type
        - important financial number
        """
        entities = self._active_corroborator._extract_entities(article)
        event_type = self._active_corroborator._detect_event_type(article)
        numbers = self._active_corroborator._extract_numbers(article.title)

        primary_entity = entities[0] if entities else None
        second_entity = entities[1] if len(entities) >= 2 else None

        query_parts = []
        if primary_entity:
            query_parts.append(primary_entity)
        if event_type and event_type != "business event":
            query_parts.append(event_type)
        if second_entity:
            query_parts.append(second_entity)
        elif numbers:
            query_parts.append(numbers[0])

        if not query_parts and article.title:
            query_parts = article.title.split()[:5]

        return " ".join(query_parts)

    def _build_site_clause(self, article: Article) -> str:
        """Build site: filter clause using approved extractable publishers excluding primary domain."""
        return self._active_corroborator._build_site_clause(article)

    def _search_serpapi(self, query: str, site_clause: str) -> List[dict]:
        """
        Execute targeted SerpAPI Google News search if budget allows.
        Returns list of candidate dicts: [{'url': ..., 'title': ..., 'source': ..., 'published_at': ...}].
        """
        global _run_serpapi_count, _serpapi_query_cache

        if not self.has_api_key:
            logger.debug("SerpAPI skipped: no API key configured")
            return []

        if _run_serpapi_count >= self.max_searches:
            logger.info("SerpAPI budget exhausted (%d/%d). Skipping search.", _run_serpapi_count, self.max_searches)
            return []

        full_query = f"{query}{site_clause}".strip()

        # Cache check
        if full_query in _serpapi_query_cache:
            logger.info("SERPAPI QUERY USED (cached): '%s'", full_query[:80])
            candidates = _serpapi_query_cache[full_query]
            logger.info("SERPAPI CANDIDATES FOUND (cached): %d", len(candidates))
            return candidates

        params = {
            "engine": "google_news",
            "q": full_query,
            "api_key": self.api_key,
        }

        try:
            logger.info("SERPAPI QUERY USED: '%s'", full_query[:80])
            response = requests.get("https://serpapi.com/search.json", params=params, timeout=10)
            _run_serpapi_count += 1

            if response.status_code != 200:
                logger.warning("SerpAPI request returned HTTP %d: %s", response.status_code, response.text[:100])
                _serpapi_query_cache[full_query] = []
                return []

            data = response.json()
            news_results = data.get("news_results", [])
            parsed_results = []
            for item in news_results[:5]:
                link = item.get("link")
                title = item.get("title")
                src = item.get("source", {}).get("name") if isinstance(item.get("source"), dict) else item.get("source")
                pub_date = item.get("date")
                if link and title:
                    parsed_results.append({
                        "url": link,
                        "title": title,
                        "source": src,
                        "published_at": pub_date,
                    })

            logger.info("SERPAPI CANDIDATES FOUND: %d", len(parsed_results))
            _serpapi_query_cache[full_query] = parsed_results
            return parsed_results

        except Exception as exc:
            logger.warning("SerpAPI request failed: %s", exc)
            _serpapi_query_cache[full_query] = []
            return []

    def corroborate(self, event: Event, primary_article: Article) -> CorroborationResult:
        """
        Perform SerpAPI fallback corroboration for a single-source event.
        Candidate articles are extracted and verified via TwoSourceVerifier.
        """
        if not self.has_api_key:
            return CorroborationResult(
                event_id=event.id,
                success=False,
                primary_article=primary_article,
                corroborating_article=None,
                failure_reason="SerpAPI disabled (no API key)",
            )

        logger.info("SERPAPI FALLBACK START for event: '%s'", event.canonical_title[:50])

        if _run_serpapi_count >= self.max_searches:
            logger.info("SERPAPI BUDGET EXHAUSTED (%d/%d)", _run_serpapi_count, self.max_searches)
            return CorroborationResult(
                event_id=event.id,
                success=False,
                primary_article=primary_article,
                corroborating_article=None,
                failure_reason=f"SerpAPI budget exhausted ({_run_serpapi_count}/{self.max_searches})",
            )

        query = self._build_targeted_query(primary_article)
        site_clause = self._build_site_clause(primary_article)
        candidates = self._search_serpapi(query, site_clause)

        extractor = self._get_extractor()
        articles_fetched = 0

        for cand in candidates:
            articles_fetched += 1
            cand_url = cand["url"].strip()

            # Skip identical URL
            if cand_url.lower().rstrip("/") == primary_article.url.strip().lower().rstrip("/"):
                continue

            try:
                res = extractor.extract(
                    url=cand_url,
                    source_name=cand.get("source"),
                    candidate_title=cand.get("title"),
                    candidate_category="India" if primary_article.category == NewsCategory.INDIA else "International",
                    candidate_pub_date=cand.get("published_at"),
                )
            except Exception as exc:
                logger.debug("SerpAPI candidate extraction error: %s | %s", cand_url[:60], exc)
                continue

            if not res.success or not res.article:
                continue

            candidate_art = res.article

            # Enforce TwoSourceVerifier checks
            is_same, score, reason = self._verifier.is_same_underlying_event(primary_article, candidate_art)
            if not is_same:
                logger.debug("SerpAPI candidate NOT_SAME_EVENT: %s", candidate_art.title[:40])
                continue

            grp1 = self._verifier.get_publisher_group(primary_article)
            grp2 = self._verifier.get_publisher_group(candidate_art)
            if grp1 == grp2:
                logger.debug("SerpAPI candidate SAME_PUBLISHER: %s", candidate_art.title[:40])
                continue

            is_synd, synd_reason = self._verifier.is_syndicated_republication(primary_article, candidate_art)
            if is_synd:
                logger.debug("SerpAPI candidate SYNDICATED: %s", candidate_art.title[:40])
                continue

            logger.info(
                "SERPAPI SECOND SOURCE ACCEPTED: '%s' (%s)",
                candidate_art.source_name,
                candidate_art.url[:60],
            )

            p_date = primary_article.published_at.isoformat() if primary_article.published_at else None
            c_date = candidate_art.published_at.isoformat() if candidate_art.published_at else None

            return CorroborationResult(
                event_id=event.id,
                success=True,
                primary_article=primary_article,
                corroborating_article=candidate_art,
                queries_fired=1,
                articles_fetched=articles_fetched,
                source_1_url=primary_article.url,
                source_1_publisher=primary_article.source_name,
                source_1_date=p_date,
                source_2_url=candidate_art.url,
                source_2_publisher=candidate_art.source_name,
                source_2_date=c_date,
                verification_score=score,
                verification_reason=reason,
            )

        fail_reason = "No independent second source found via SerpAPI fallback"
        logger.info("SERPAPI FALLBACK FAILED for event '%s': %s", event.canonical_title[:45], fail_reason)
        return CorroborationResult(
            event_id=event.id,
            success=False,
            primary_article=primary_article,
            corroborating_article=None,
            queries_fired=1,
            articles_fetched=articles_fetched,
            failure_reason=fail_reason,
        )
