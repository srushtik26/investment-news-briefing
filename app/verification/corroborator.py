"""
Active Corroboration Engine.

When an event has only one article source in the initial candidate pool,
this module fires targeted Google News RSS searches to find a genuinely
independent second publisher covering the same underlying event.

Design principles:
  - Deterministic entity extraction (no Gemini).
  - Maximum 2 queries per event.
  - Maximum 20 total corroboration searches per pipeline run.
  - Stop immediately once a valid independent second source is found.
  - Never bypass paywalls.
  - Never count same-publisher or syndicated articles as independent.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.parse import urlparse, quote_plus

from config import get_settings
from app.logging_config import get_logger
from app.models.article import Article
from app.models.event import Event
from app.verification.verifier import TwoSourceVerifier

logger = get_logger("verification.corroborator")

# Module-level run counter (reset between pipeline runs)
_run_corroboration_count = 0
MAX_CORROBORATION_SEARCHES_PER_RUN = get_settings().MAX_CORROBORATION_SEARCHES
MAX_QUERIES_PER_EVENT = 2
MAX_ARTICLES_PER_QUERY = 5

# Words to exclude from being treated as primary entities
GENERIC_SINGLE_WORDS = {
    "india", "company", "news", "board", "results", "shares", "market", "group",
    "the", "a", "an", "and", "for", "with", "its", "stock", "stocks", "quarterly",
    "annual", "report", "reports", "today", "live", "update", "updates", "page",
    "exclusive", "says", "said", "first", "second", "third", "fourth", "q1", "q2",
    "q3", "q4", "fy24", "fy25", "fy26", "fy27", "billion", "million", "crore",
    "lakh", "percent", "pct", "rs", "inr", "usd", "business", "financial",
    "economic", "times", "standard", "mint", "express", "profit", "reuters",
    "bloomberg", "cnbc", "journal", "street", "wall", "press", "associated"
}

GENERIC_PHRASES = {
    "stock market", "market update", "quarterly results", "annual report",
    "business news", "live updates", "financial results", "share market"
}


def reset_corroboration_counter() -> None:
    """Reset the per-run corroboration search counter."""
    global _run_corroboration_count
    _run_corroboration_count = 0


def get_corroboration_count() -> int:
    """Return the current number of corroboration searches used this run."""
    return _run_corroboration_count


@dataclass
class CorroborationResult:
    """Outcome of an active corroboration search for one event."""
    event_id: str
    success: bool                          # True = second independent source found
    primary_article: Optional[Article]
    corroborating_article: Optional[Article]
    queries_fired: int = 0
    articles_fetched: int = 0
    failure_reason: Optional[str] = None
    source_1_url: Optional[str] = None
    source_1_publisher: Optional[str] = None
    source_1_date: Optional[str] = None
    source_2_url: Optional[str] = None
    source_2_publisher: Optional[str] = None
    source_2_date: Optional[str] = None
    verification_score: float = 0.0
    verification_reason: Optional[str] = None


class ActiveCorroborator:
    """
    Performs targeted RSS-based corroboration searches when an event has only
    one article source after initial discovery.

    Usage (called from run_pipeline.py Stage 5):
        corroborator = ActiveCorroborator(extractor=extractor)
        result = corroborator.corroborate(event=event, primary_article=art)
        if result.success:
            # add result.corroborating_article to event, re-verify
    """

    # Common event keywords that help build targeted queries
    _EVENT_KEYWORDS = {
        "acquisition": ["acquisition", "acquires", "buyout", "takeover"],
        "merger": ["merger", "merges", "amalgamation", "amalgamates"],
        "fundraise": ["raises", "funding", "fundraise", "capital raise", "secures funding"],
        "results": ["results", "profit", "revenue", "earnings", "quarterly"],
        "ipo": ["ipo", "listing", "drhp", "filed ipo"],
        "regulatory": ["penalty", "fine", "order", "ban", "sebi", "rbi", "sec", "eu"],
        "contract": ["contract", "order", "wins order", "bags order"],
        "partnership": ["joint venture", "partnership", "tie-up", "collaboration"],
        "leadership": ["appoints", "ceo", "md", "managing director", "resigns", "steps down"],
        "expansion": ["expansion", "capex", "capacity", "plant", "greenfield"],
    }

    # Corroboration-eligible sources for India
    INDIA_CORROBORATION_DOMAINS = [
        "economictimes.indiatimes.com",
        "business-standard.com",
        "livemint.com",
        "financialexpress.com",
        "moneycontrol.com",
        "businesstoday.in",
        "ndtvprofit.com",
        "thehindu.com",
        "indianexpress.com",
    ]

    # Corroboration-eligible sources for International
    INTL_CORROBORATION_DOMAINS = [
        "cnbc.com",
        "apnews.com",
        "bbc.com",
        "marketwatch.com",
        "theguardian.com",
        "fortune.com",
        "reuters.com",
        "bloomberg.com",
        "ft.com",
        "wsj.com",
    ]

    def __init__(self, extractor=None) -> None:
        """
        Args:
            extractor: ArticleExtractor instance (reused for HTTP + parsing).
                       If None, one will be created lazily.
        """
        self._extractor = extractor
        self._verifier = TwoSourceVerifier()

    def _get_extractor(self):
        if self._extractor is None:
            from app.extraction.extractor import ArticleExtractor
            self._extractor = ArticleExtractor()
        return self._extractor

    def _extract_entities(self, article: Article) -> List[str]:
        """
        Extract candidate entity tokens from article title and content.
        Prioritizes multi-word proper nouns, uppercase company names, and title entities,
        strictly excluding generic noise words.
        """
        title = article.title or ""
        snippet = (article.content_text or "")[:300]
        full_text = f"{title} {snippet}"

        # Multi-word proper nouns (e.g. "Tata Motors", "Home Depot", "Astra Space")
        multi_proper = re.findall(r"\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)+\b", full_text)

        # Title proper nouns / acronyms
        title_words = re.findall(r"\b[A-Z][a-zA-Z0-9]{2,}\b", title)
        title_acronyms = re.findall(r"\b[A-Z]{2,}\b", title)

        # Snippet proper nouns
        snippet_words = re.findall(r"\b[A-Z][a-zA-Z0-9]{2,}\b", snippet)

        candidates = multi_proper + title_words + title_acronyms + snippet_words

        seen = set()
        result = []
        for cand in candidates:
            tok = cand.strip()
            norm = tok.lower()
            if (
                tok
                and norm not in GENERIC_SINGLE_WORDS
                and norm not in GENERIC_PHRASES
                and norm not in seen
            ):
                seen.add(norm)
                result.append(tok)

        return result[:6]

    def _extract_numbers(self, text: str) -> List[str]:
        """Extract key financial numbers/percentages or deal values from text."""
        matches = re.findall(
            r"(?:₹|\$|rs\.?\s*)?[\d,]+(?:\.\d+)?\s*(?:%|crore|cr|billion|b|million|m)?",
            text,
            re.IGNORECASE,
        )
        cleaned = []
        for m in matches:
            tok = m.strip()
            if any(c.isdigit() for c in tok) and tok.lower() not in ("1", "2", "3"):
                cleaned.append(tok)
        return cleaned[:3]

    def _detect_event_type(self, article: Article) -> str:
        """Detect the dominant event type keyword from article title."""
        title_lower = article.title.lower()
        content_snippet = (article.content_text or "")[:200].lower()
        combined = f"{title_lower} {content_snippet}"

        for event_type, keywords in self._EVENT_KEYWORDS.items():
            if any(kw in combined for kw in keywords):
                return event_type
        return "business event"

    def _build_corroboration_queries(self, article: Article) -> List[str]:
        """
        Construct up to MAX_QUERIES_PER_EVENT targeted search query strings.
        Queries are compact and specific using primary company/entity, detected event type,
        distinctive title words, secondary entities, and numbers.
        """
        entities = self._extract_entities(article)
        event_type = self._detect_event_type(article)
        numbers = self._extract_numbers(article.title)

        queries = []

        primary_entity = entities[0] if entities else None
        second_entity = entities[1] if len(entities) >= 2 else None

        # Distinctive title words (excluding generic noise and stop words)
        stopwords = {"the", "a", "an", "and", "for", "with", "its", "in", "on", "at", "to", "of", "is", "by", "from", "as"}
        title_tokens = [
            w for w in re.findall(r"\b[a-zA-Z0-9]+\b", article.title)
            if w.lower() not in stopwords and w.lower() not in GENERIC_SINGLE_WORDS and len(w) > 2
        ]

        if primary_entity:
            # Query 1: Primary Entity + Event Type + Second Entity (or number)
            event_kw = event_type if event_type != "business event" else ""
            q1_parts = [primary_entity]
            if event_kw:
                q1_parts.append(event_kw)
            if second_entity:
                q1_parts.append(second_entity)
            elif numbers:
                q1_parts.append(numbers[0])
            elif title_tokens:
                for tt in title_tokens:
                    if tt.lower() not in primary_entity.lower():
                        q1_parts.append(tt)
                        break

            queries.append(" ".join(q1_parts))

            # Query 2: Primary Entity + Second Entity + Number (or distinctive title terms)
            if second_entity:
                q2_parts = [primary_entity, second_entity]
                if numbers:
                    q2_parts.append(numbers[0])
                queries.append(" ".join(q2_parts))
            elif numbers:
                q2_parts = [primary_entity, numbers[0]]
                if title_tokens:
                    for tt in title_tokens:
                        if tt.lower() not in primary_entity.lower() and tt.lower() not in numbers[0].lower():
                            q2_parts.append(tt)
                            break
                queries.append(" ".join(q2_parts))
            elif title_tokens:
                distinctive = [
                    t for t in title_tokens
                    if t.lower() not in primary_entity.lower()
                ][:3]
                if distinctive:
                    queries.append(f"{primary_entity} " + " ".join(distinctive))
                else:
                    queries.append(primary_entity)
        else:
            # Fallback when no clear entity extracted: use top title words
            if title_tokens:
                queries.append(" ".join(title_tokens[:5]))
                if len(title_tokens) >= 3:
                    queries.append(" ".join(title_tokens[2:6]))

        # Deduplicate while preserving order
        unique_queries = []
        for q in queries:
            q_clean = q.strip()
            if q_clean and q_clean not in unique_queries:
                unique_queries.append(q_clean)

        return unique_queries[:MAX_QUERIES_PER_EVENT]

    def _build_site_clause(self, article: Article) -> str:
        """Build site: filter clause for RSS query, excluding primary publisher."""
        primary_netloc = urlparse(article.url).netloc.lower().replace("www.", "")

        india_signals = ["economictimes", "business-standard", "livemint",
                         "financialexpress", "moneycontrol", "businesstoday",
                         "ndtvprofit", "thehindu", "indianexpress"]
        is_india = any(sig in primary_netloc for sig in india_signals)

        domains = self.INDIA_CORROBORATION_DOMAINS if is_india else self.INTL_CORROBORATION_DOMAINS
        # Exclude the primary publisher's domain
        eligible = [d for d in domains if d not in primary_netloc]
        if not eligible:
            eligible = domains  # fallback — verifier will still enforce independence

        site_terms = [f"site:{d}" for d in eligible]
        return " (" + " OR ".join(site_terms) + ")"

    def _search_rss(self, query: str, country: str) -> List[dict]:
        """
        Perform a single targeted Google News RSS search.
        Returns a list of dicts with 'url', 'title', 'source', 'published_at'.
        """
        from app.discovery.rss_provider import GoogleNewsRSSDiscoveryProvider
        provider = GoogleNewsRSSDiscoveryProvider()
        results = provider.discover(
            query=query,
            country=country,
            max_results=MAX_ARTICLES_PER_QUERY,
        )
        return [
            {
                "url": r.url,
                "title": r.title,
                "source": r.source,
                "published_at": r.published_at,
            }
            for r in results
        ]

    def _detect_country(self, article: Article) -> str:
        """Detect India vs International for the corroboration RSS query."""
        from app.models.enums import NewsCategory
        if article.category == NewsCategory.INDIA:
            return "India"
        return "International"

    def corroborate(
        self,
        event: Event,
        primary_article: Article,
    ) -> CorroborationResult:
        """
        Attempt to find a genuinely independent second publisher for the event.

        Args:
            event: The Event object with single-source articles.
            primary_article: The primary article already extracted for this event.

        Returns:
            CorroborationResult with success=True if a valid second source is found.
        """
        global _run_corroboration_count

        event_id = event.id

        # Check global run budget
        if _run_corroboration_count >= MAX_CORROBORATION_SEARCHES_PER_RUN:
            logger.info(
                "Corroboration budget exhausted (%d/%d). Skipping event '%s'.",
                _run_corroboration_count,
                MAX_CORROBORATION_SEARCHES_PER_RUN,
                event.canonical_title[:45],
            )
            return CorroborationResult(
                event_id=event_id,
                success=False,
                primary_article=primary_article,
                corroborating_article=None,
                failure_reason=f"Corroboration budget exhausted ({_run_corroboration_count}/{MAX_CORROBORATION_SEARCHES_PER_RUN})",
            )

        queries = self._build_corroboration_queries(primary_article)
        site_clause = self._build_site_clause(primary_article)
        country = self._detect_country(primary_article)

        extractor = self._get_extractor()
        queries_fired = 0
        articles_fetched = 0

        logger.info(
            "Active corroboration for event '%s' — firing up to %d queries (country: %s)",
            event.canonical_title[:45],
            len(queries),
            country,
        )

        for query in queries:
            if _run_corroboration_count >= MAX_CORROBORATION_SEARCHES_PER_RUN:
                break

            full_query = f"{query}{site_clause}"
            logger.info("  Corroboration query [%d]: '%s'", queries_fired + 1, full_query[:80])

            rss_results = self._search_rss(full_query, country)
            queries_fired += 1
            _run_corroboration_count += 1

            for cand in rss_results:
                articles_fetched += 1
                cand_url = cand["url"].strip()

                # Skip if same URL as primary
                if cand_url.lower().rstrip("/") == primary_article.url.strip().lower().rstrip("/"):
                    logger.debug("  CORROBORATION REJECTED [SAME_URL]: %s", cand_url[:80])
                    continue

                # Extract the candidate article
                try:
                    res = extractor.extract(
                        url=cand_url,
                        source_name=cand.get("source"),
                        candidate_title=cand.get("title"),
                        candidate_category=country,
                        candidate_pub_date=cand.get("published_at"),
                    )
                except Exception as exc:
                    logger.debug("  CORROBORATION REJECTED [EXTRACTION_FAILED]: %s | %s", cand_url[:80], exc)
                    continue

                if not res.success or not res.article:
                    logger.debug("  CORROBORATION REJECTED [EXTRACTION_FAILED]: %s | %s", cand_url[:80], res.error_message)
                    continue

                candidate_art = res.article

                # Check same event
                is_same, score, reason = self._verifier.is_same_underlying_event(
                    primary_article, candidate_art
                )
                if not is_same:
                    logger.debug(
                        "  CORROBORATION REJECTED [NOT_SAME_EVENT]: '%s' (score=%.2f): %s",
                        candidate_art.title[:50], score, reason
                    )
                    continue

                # Check publisher independence
                grp1 = self._verifier.get_publisher_group(primary_article)
                grp2 = self._verifier.get_publisher_group(candidate_art)
                if grp1 == grp2:
                    logger.debug(
                        "  CORROBORATION REJECTED [SAME_PUBLISHER]: '%s' from publisher group '%s'",
                        candidate_art.title[:50], grp1
                    )
                    continue

                # Check not syndicated
                is_synd, synd_reason = self._verifier.is_syndicated_republication(
                    primary_article, candidate_art
                )
                if is_synd:
                    logger.debug(
                        "  CORROBORATION REJECTED [SYNDICATED]: '%s' - %s",
                        candidate_art.title[:50], synd_reason
                    )
                    continue

                # ✅ Found a valid independent second source!
                logger.debug(
                    "  CORROBORATION ACCEPTED: '%s' from '%s'",
                    candidate_art.title[:50], candidate_art.source_name
                )
                logger.info(
                    "  CORROBORATION SUCCESS for event '%s': Source 2 = '%s' (%s)",
                    event.canonical_title[:45],
                    candidate_art.source_name,
                    candidate_art.url[:60],
                )

                p_date = primary_article.published_at.isoformat() if primary_article.published_at else None
                c_date = candidate_art.published_at.isoformat() if candidate_art.published_at else None

                return CorroborationResult(
                    event_id=event_id,
                    success=True,
                    primary_article=primary_article,
                    corroborating_article=candidate_art,
                    queries_fired=queries_fired,
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

        logger.info(
            "  Corroboration FAILED for event '%s' after %d queries, %d candidates fetched.",
            event.canonical_title[:45], queries_fired, articles_fetched
        )
        return CorroborationResult(
            event_id=event_id,
            success=False,
            primary_article=primary_article,
            corroborating_article=None,
            queries_fired=queries_fired,
            articles_fetched=articles_fetched,
            failure_reason="No independent second source found within corroboration budget",
        )
