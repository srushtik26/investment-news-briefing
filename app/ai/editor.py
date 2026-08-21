"""
Gemini Final Editorial Selection and Synthesis Engine.

Uses Gemini API as the executive editor to select 5 India and 5 International stories,
generate concise financial headlines, and programmatically validate exact URL preservation.

Rate-limit behaviour:
  - On first 429  → exponential backoff then retry ONCE.
  - On second consecutive 429 → returns EditorialResult(success=False) with
    error_message starting with "RATE_LIMITED:" so the runner can detect and stop.

Usage is recorded via GeminiUsageLogger on every call attempt.
"""

import json
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set

from pydantic import ValidationError

from config import get_settings
from app.logging_config import get_logger
from app.models.article import Article
from app.ranking.models import RankedCandidatePool, ScoredEvent
from app.deduplication.fingerprint import normalize_entity_name
from app.ai.models import (
    BriefingEditorialPayload,
    EditorialResult,
    EditorialStorySelection,
)
from app.ai.prompts import (
    SYSTEM_EDITORIAL_PROMPT,
    build_editorial_user_prompt,
)
from app.ai.usage_logger import GeminiUsageLogger

logger = get_logger("ai.editor")

# Sentinel prefix used by the runner to detect rate-limited editorial failure
RATE_LIMITED_PREFIX = "RATE_LIMITED:"

# Sentinel object: means "use value from settings"
_NOT_SET = object()


class GeminiEditorialEngine:
    """
    Coordinates final editorial curation, headline synthesis, and URL validation via Gemini.

    Hard limit
    ----------
    The editorial stage makes at most 2 live Gemini calls per run (1 attempt + 1 retry
    on validation failure). Two consecutive 429s cause an immediate RATE_LIMITED return
    rather than sleeping for several minutes.

    Forcing offline mode
    --------------------
    Pass ``api_key=""`` (empty string) to force the deterministic offline fallback
    regardless of any GEMINI_API_KEY in the environment. Useful in tests.
    """

    # Seconds to wait after the first 429 before the single retry
    RATE_LIMIT_BACKOFF_SECONDS = 30

    def __init__(
        self,
        api_key=_NOT_SET,
        model_name: Optional[str] = None,
        mock_responder: Optional[Callable[[RankedCandidatePool], str]] = None,
    ) -> None:
        settings = get_settings()
        # If caller explicitly passed a value (including ""), honour it.
        # Only fall back to settings when the sentinel is seen.
        if api_key is _NOT_SET:
            self.api_key = settings.GEMINI_API_KEY
        else:
            self.api_key = api_key

        if self.api_key and isinstance(self.api_key, str):
            self.api_key = self.api_key.strip().strip("'\"")

        self.model_name = model_name or settings.GEMINI_MODEL
        self.mock_responder = mock_responder

        self._client = None
        if self.api_key and not self.mock_responder:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
                logger.info("Initialized Gemini Editorial Client with model: %s", self.model_name)
            except Exception as exc:
                logger.warning("Failed to initialize google.genai Client: %s", exc)

    def select_and_synthesize_briefing(
        self,
        ranked_pool: RankedCandidatePool,
        articles_map: Dict[str, Article],
    ) -> EditorialResult:
        """
        Execute final editorial selection and validate output against candidate manifest.

        Args:
            ranked_pool: Top candidate pool from relevance scoring.
            articles_map: Lookup dictionary mapping article_id to Article.

        Returns:
            EditorialResult containing validated BriefingEditorialPayload.
            On consecutive rate-limits, returns success=False with
            error_message starting with RATE_LIMITED_PREFIX.
        """
        logger.info(
            "Executing final editorial selection (India candidates: %d, Intl candidates: %d)...",
            len(ranked_pool.india_candidates),
            len(ranked_pool.international_candidates),
        )

        # FIX 5: DO NOT CALL EDITORIAL GEMINI WITH ZERO STORIES
        if len(ranked_pool.india_candidates) == 0 and len(ranked_pool.international_candidates) == 0:
            logger.warning("Zero candidate events in pool. Skipping Gemini Editorial call to preserve API quota.")
            return EditorialResult(
                success=False,
                error_message="NO_STORIES_AVAILABLE: Zero verified candidate events available for editorial selection.",
                attempts=0,
                raw_response=None,
            )

        # Build candidate validation manifests
        valid_events_map: Dict[str, ScoredEvent] = {}
        valid_urls_map: Dict[str, str] = {}  # url -> event_id

        for scored in ranked_pool.india_candidates + ranked_pool.international_candidates:
            e = scored.event
            valid_events_map[e.id] = scored
            for art_id in e.article_ids:
                if art_id in articles_map:
                    valid_urls_map[articles_map[art_id].url] = e.id

        max_attempts = 2
        last_error: Optional[str] = None
        raw_text: Optional[str] = None
        consecutive_429s: int = 0

        for attempt in range(1, max_attempts + 1):
            logger.debug("Executing editorial selection call (attempt %d/%d)", attempt, max_attempts)
            try:
                raw_text = self._call_model(ranked_pool, articles_map, attempt=attempt)
                if not raw_text or not raw_text.strip():
                    raise ValueError("Editorial model returned empty response")

                # Parse JSON and validate Pydantic schema
                parsed_json = self._extract_json_from_response(raw_text)
                payload = BriefingEditorialPayload.model_validate(parsed_json)

                # Programmatic Validation Checks
                self._validate_editorial_payload(payload, valid_events_map, valid_urls_map)

                GeminiUsageLogger.record(
                    stage="editorial",
                    model=self.model_name,
                    success=True,
                    retry_number=attempt - 1,
                )
                logger.info(
                    "Editorial curation successful: Selected %d India and %d International stories",
                    len(payload.india_stories),
                    len(payload.international_stories),
                )
                consecutive_429s = 0
                return EditorialResult(
                    success=True,
                    selection=payload,
                    attempts=attempt,
                    raw_response=raw_text,
                )

            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = f"Validation failure on attempt {attempt}: {exc}"
                GeminiUsageLogger.record(
                    stage="editorial",
                    model=self.model_name,
                    success=False,
                    retry_number=attempt - 1,
                    error_summary=str(exc)[:120],
                )
                logger.warning("Editorial validation error (attempt %d): %s", attempt, exc)

            except Exception as exc:
                exc_str = str(exc)
                is_rate_limit = "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str

                if is_rate_limit:
                    consecutive_429s += 1
                    GeminiUsageLogger.record(
                        stage="editorial",
                        model=self.model_name,
                        success=False,
                        http_status=429,
                        retry_number=attempt - 1,
                        error_summary="RESOURCE_EXHAUSTED",
                    )
                    if consecutive_429s >= 2:
                        # Two consecutive 429s — stop immediately
                        logger.error(
                            "Two consecutive 429s in editorial curation. Stopping Gemini calls."
                        )
                        return EditorialResult(
                            success=False,
                            error_message=(
                                f"{RATE_LIMITED_PREFIX} Editorial halted after two consecutive "
                                "429 RESOURCE_EXHAUSTED responses. Quota exceeded for today."
                            ),
                            attempts=attempt,
                            raw_response=raw_text,
                        )
                    # First 429 → wait then retry
                    logger.warning(
                        "Rate limit 429 on editorial attempt %d. Backing off %ds...",
                        attempt, self.RATE_LIMIT_BACKOFF_SECONDS,
                    )
                    time.sleep(self.RATE_LIMIT_BACKOFF_SECONDS)
                    last_error = f"Rate limit (429) on attempt {attempt}"
                else:
                    last_error = f"API error on attempt {attempt}: {exc}"
                    GeminiUsageLogger.record(
                        stage="editorial",
                        model=self.model_name,
                        success=False,
                        retry_number=attempt - 1,
                        error_summary=exc_str[:120],
                    )
                    logger.error("Editorial API error (attempt %d): %s", attempt, exc, exc_info=True)

        logger.error(
            "Editorial selection rejected after %d attempts: %s", max_attempts, last_error
        )
        return EditorialResult(
            success=False,
            error_message=last_error or "Editorial selection failed after retries",
            attempts=max_attempts,
            raw_response=raw_text,
        )

    # ---------------------------------------------------------------------- #
    # Internal helpers                                                         #
    # ---------------------------------------------------------------------- #

    def _validate_editorial_payload(
        self,
        payload: BriefingEditorialPayload,
        valid_events: Dict[str, ScoredEvent],
        valid_urls: Dict[str, str],
    ) -> None:
        """Enforce strict programmatic checks on Gemini output."""
        all_stories = payload.india_stories + payload.international_stories
        if not all_stories:
            raise ValueError("Editorial selection returned 0 stories")

        # 1. Validate Event IDs and Exact URLs
        for story in all_stories:
            if story.event_id not in valid_events:
                raise ValueError(
                    f"Selected event_id '{story.event_id}' was not in the verified candidate list"
                )
            if story.url not in valid_urls:
                raise ValueError(
                    f"Returned URL '{story.url}' does not match any supplied verified candidate URL"
                )

        # 2. Validate India Same-Company Restriction
        seen_india_companies: Set[str] = set()
        for story in payload.india_stories:
            scored = valid_events[story.event_id]
            companies = scored.event.companies_involved or [story.source]
            for comp in companies:
                norm = normalize_entity_name(comp)
                if norm in seen_india_companies and norm != "unspecified_entity":
                    raise ValueError(f"India section selected duplicate company: '{comp}'")
                seen_india_companies.add(norm)

    def _call_model(
        self,
        ranked_pool: RankedCandidatePool,
        articles_map: Dict[str, Article],
        attempt: int = 1,
    ) -> str:
        """Execute model generation request or mock responder."""
        if self.mock_responder:
            return self.mock_responder(ranked_pool)

        if not self._client:
            if not self.api_key:
                logger.info("No GEMINI_API_KEY configured; running deterministic editorial fallback")
                return self._generate_offline_editorial_fallback(ranked_pool, articles_map)
            raise RuntimeError("Gemini client is not initialized")

        from google.genai import types

        user_content = build_editorial_user_prompt(
            india_candidates=ranked_pool.india_candidates,
            international_candidates=ranked_pool.international_candidates,
            articles_map=articles_map,
        )
        if attempt > 1:
            user_content += (
                "\n\nCRITICAL: Ensure valid JSON and exact URL matching from the candidate list."
            )

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_EDITORIAL_PROMPT,
            temperature=0.1,
            response_mime_type="application/json",
        )

        response = self._client.models.generate_content(
            model=self.model_name,
            contents=user_content,
            config=config,
        )
        return response.text or ""

    def _extract_json_from_response(self, text: str) -> dict[str, Any]:
        """Extract and parse clean JSON dictionary from model response."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        start_idx = cleaned.find("{")
        end_idx = cleaned.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
            cleaned = cleaned[start_idx : end_idx + 1]

        return json.loads(cleaned)

    def _generate_offline_editorial_fallback(
        self,
        ranked_pool: RankedCandidatePool,
        articles_map: Dict[str, Article],
    ) -> str:
        """Deterministic heuristic fallback when no API key is present."""
        india_selected: List[Dict[str, str]] = []
        seen_india_comps: Set[str] = set()

        for scored in ranked_pool.india_candidates:
            if len(india_selected) >= 5:
                break
            e = scored.event
            comp = normalize_entity_name(
                e.companies_involved[0] if e.companies_involved else "unspecified"
            )
            if comp in seen_india_comps and comp != "unspecified_entity":
                continue
            seen_india_comps.add(comp)

            art = articles_map.get(e.article_ids[0]) if e.article_ids else None
            source_name = art.source_name if art else "Business Standard"
            url = art.url if art else f"https://example.com/india-{e.id}"

            india_selected.append({
                "section": "india",
                "event_id": e.id,
                "headline": e.canonical_title,
                "source": source_name,
                "url": url,
            })

        intl_selected: List[Dict[str, str]] = []
        for scored in ranked_pool.international_candidates[:5]:
            e = scored.event
            art = articles_map.get(e.article_ids[0]) if e.article_ids else None
            source_name = art.source_name if art else "Reuters"
            url = art.url if art else f"https://example.com/intl-{e.id}"

            intl_selected.append({
                "section": "international",
                "event_id": e.id,
                "headline": e.canonical_title,
                "source": source_name,
                "url": url,
            })

        return json.dumps({
            "india_stories": india_selected,
            "international_stories": intl_selected,
        })
