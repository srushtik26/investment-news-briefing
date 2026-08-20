"""
AI Article Classifier Service.

Coordinates with the Gemini API to perform structured, factual news classification
and entity extraction with Pydantic validation and automatic retry on malformed JSON.

Rate-limit behaviour:
  - On first 429  → exponential backoff (configurable) then retry ONCE.
  - On second consecutive 429 → raises GeminiRateLimitError immediately.
    The pipeline runner is expected to catch this and propagate RATE_LIMITED status.

Usage is recorded via GeminiUsageLogger on every call attempt.
"""

import json
import re
import time
from typing import Any, Callable, Optional

from pydantic import ValidationError

from config import get_settings
from app.logging_config import get_logger
from app.models.article import Article
from app.classification.models import (
    AIArticleClassification,
    ArticleEventType,
    ClassificationResult,
)
from app.classification.prompts import (
    SYSTEM_CLASSIFICATION_PROMPT,
    build_classification_user_prompt,
)
from app.ai.usage_logger import GeminiUsageLogger

logger = get_logger("classification.ai")

_NOT_SET = object()  # Sentinel: means "use the value from settings"


class GeminiRateLimitError(RuntimeError):
    """Raised when two consecutive 429s occur so the runner can stop early."""


class AIArticleClassifier:
    """
    Service for classifying news articles and extracting key entities via Gemini API.

    Hard per-run cap
    ----------------
    Pass ``max_articles`` to limit how many live Gemini calls are made in a
    single pipeline run (default: 15, matching free-tier RPM safety budget).
    Articles beyond the cap receive the deterministic offline fallback.

    Forcing offline mode
    --------------------
    Pass ``api_key=""`` (empty string) to force the offline heuristic regardless
    of any GEMINI_API_KEY in the environment. Useful in tests.
    """

    # How long (seconds) to wait after a 429 before the single retry
    RATE_LIMIT_BACKOFF_SECONDS = 20

    def __init__(
        self,
        api_key=_NOT_SET,
        model_name: Optional[str] = None,
        mock_responder: Optional[Callable[[Article], str]] = None,
        max_articles: Optional[int] = None,
    ) -> None:
        settings = get_settings()
        # If caller explicitly passed a value (including ""), use it.
        # Only fall back to settings when the sentinel is seen.
        if api_key is _NOT_SET:
            self.api_key = settings.GEMINI_API_KEY
        else:
            self.api_key = api_key
        self.model_name = model_name or settings.GEMINI_MODEL
        self.mock_responder = mock_responder
        self.max_articles = max_articles if max_articles is not None else settings.MAX_GEMINI_CLASSIFICATIONS

        # Track how many live Gemini calls have been made this run
        self._live_call_count: int = 0
        self._force_offline_mode: bool = False

        self._client = None
        if self.api_key and not self.mock_responder:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
                logger.info("Initialized Gemini Client with model: %s", self.model_name)
            except Exception as exc:
                logger.warning("Failed to initialize google.genai Client: %s", exc)

    # ---------------------------------------------------------------------- #
    # Public classify API                                                      #
    # ---------------------------------------------------------------------- #

    def _run_offline_fallback(self, article: Article) -> ClassificationResult:
        """Run deterministic heuristic classifier and return ClassificationResult with attempts=0."""
        raw_text = self._generate_offline_fallback(article)
        try:
            parsed_json = self._extract_json_from_response(raw_text)
            classification = AIArticleClassification.model_validate(parsed_json)
            return ClassificationResult(
                success=True,
                classification=classification,
                attempts=0,  # 0 = offline, no API call
                raw_response=raw_text,
            )
        except Exception as exc:
            return ClassificationResult(
                success=False,
                error_message=f"Offline fallback parse error: {exc}",
                attempts=0,
            )

    def classify(self, article: Article) -> ClassificationResult:
        """
        Classify an article into structured format with one retry on recoverable API errors
        (429, 500, 502, 503) or malformed JSON.

        If Gemini is disabled, max_articles cap is reached, or an unrecoverable API error
        / rate limit occurs, falls back to the deterministic offline classifier for the
        current article and all remaining candidates.
        """
        logger.info("Classifying article '%s' via Gemini AI...", article.title[:50])

        # Enforce per-run cap or forced offline mode — fall back to offline heuristic
        if (
            self._force_offline_mode
            or self._live_call_count >= self.max_articles
        ):
            logger.info(
                "Using offline classification fallback for: %s", article.title[:50],
            )
            return self._run_offline_fallback(article)

        # Live Gemini path — up to 2 attempts (1 initial + 1 retry on bad JSON/recoverable error)
        max_attempts = 2
        last_error: Optional[str] = None
        raw_text: Optional[str] = None

        for attempt in range(1, max_attempts + 1):
            logger.debug(
                "Executing classification call (attempt %d/%d) for %s",
                attempt, max_attempts, article.url,
            )
            try:
                raw_text = self._call_model(article, attempt=attempt)
                if not raw_text or not raw_text.strip():
                    raise ValueError("Model returned empty response")

                # Parse JSON and validate against Pydantic schema
                parsed_json = self._extract_json_from_response(raw_text)
                classification = AIArticleClassification.model_validate(parsed_json)

                GeminiUsageLogger.record(
                    stage="classification",
                    model=self.model_name,
                    success=True,
                    retry_number=attempt - 1,
                )
                logger.info(
                    "Classification successful: event_type=%s, hard_event=%s, investment_relevant=%s",
                    classification.event_type.value,
                    classification.is_hard_business_event,
                    classification.is_investment_relevant,
                )
                return ClassificationResult(
                    success=True,
                    classification=classification,
                    attempts=attempt,
                    raw_response=raw_text,
                )

            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = f"Malformed output on attempt {attempt}: {exc}"
                logger.warning(
                    "Classification validation error for '%s' (attempt %d): %s",
                    article.title[:40], attempt, exc,
                )
                GeminiUsageLogger.record(
                    stage="classification",
                    model=self.model_name,
                    success=False,
                    retry_number=attempt - 1,
                    error_summary=str(exc)[:120],
                )

            except Exception as exc:
                exc_str = str(exc)

                is_400_range = any(code in exc_str for code in ("400", "401", "403", "404"))
                is_429 = "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str
                is_5xx = any(code in exc_str for code in ("500", "502", "503", "UNAVAILABLE", "INTERNAL"))

                http_code = 429 if is_429 else (503 if is_5xx else (403 if is_400_range else None))

                GeminiUsageLogger.record(
                    stage="classification",
                    model=self.model_name,
                    success=False,
                    http_status=http_code,
                    retry_number=attempt - 1,
                    error_summary=exc_str[:120],
                )

                if is_400_range:
                    logger.warning(
                        "Client/Auth error on attempt %d for '%s'. Switching to offline fallback: %s",
                        attempt, article.title[:40], exc,
                    )
                    self._force_offline_mode = True
                    return self._run_offline_fallback(article)

                if attempt == 1 and (is_429 or is_5xx):
                    backoff = self.RATE_LIMIT_BACKOFF_SECONDS if (is_429 and not self.mock_responder) else (2 if (is_5xx and not self.mock_responder) else 0)
                    logger.warning(
                        "API error (%s) on attempt 1 for '%s'. Backing off %ds before retry...",
                        "429 Rate Limit" if is_429 else "5xx Server Error",
                        article.title[:40],
                        backoff,
                    )
                    if backoff > 0:
                        time.sleep(backoff)
                    last_error = f"API error on attempt 1: {exc}"
                    continue
                else:
                    logger.warning(
                        "API error on attempt %d for '%s'. Switching to offline classifier for remaining candidates: %s",
                        attempt, article.title[:40], exc,
                    )
                    self._force_offline_mode = True
                    return self._run_offline_fallback(article)

        logger.error(
            "Classification permanently rejected for '%s' after %d attempts: %s",
            article.title[:50], max_attempts, last_error,
        )
        return ClassificationResult(
            success=False,
            error_message=last_error or "Classification failed after retries",
            attempts=max_attempts,
            raw_response=raw_text,
        )

    # ---------------------------------------------------------------------- #
    # Internal helpers                                                         #
    # ---------------------------------------------------------------------- #

    def _call_model(self, article: Article, attempt: int = 1) -> str:
        """Execute model generation request or mock responder."""
        self._live_call_count += 1
        # 1. Check for custom mock responder (used in tests/offline mode)
        if self.mock_responder:
            return self.mock_responder(article)

        # 2. Check for missing API Key
        if not self._client:
            if not self.api_key:
                logger.info(
                    "No GEMINI_API_KEY configured; generating deterministic rule-based "
                    "classification fallback"
                )
                return self._generate_offline_fallback(article)
            raise RuntimeError("Gemini client is not initialized")

        # 3. Call live Gemini API via google.genai
        from google.genai import types

        user_content = build_classification_user_prompt(article)
        if attempt > 1:
            user_content += (
                "\n\nCRITICAL: Your previous response contained invalid JSON syntax. "
                "Ensure strictly valid JSON."
            )

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_CLASSIFICATION_PROMPT,
            temperature=0.1,  # Low temperature for deterministic classification
            response_mime_type="application/json",
        )

        self._live_call_count += 1
        response = self._client.models.generate_content(
            model=self.model_name,
            contents=user_content,
            config=config,
        )
        return response.text or ""

    def _extract_json_from_response(self, text: str) -> dict[str, Any]:
        """Extract and parse clean JSON dictionary from model response text."""
        cleaned = text.strip()

        # Strip markdown code fences if present (e.g. ```json ... ```)
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        # Find first '{' and last '}'
        start_idx = cleaned.find("{")
        end_idx = cleaned.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
            cleaned = cleaned[start_idx : end_idx + 1]

        return json.loads(cleaned)

    def _generate_offline_fallback(self, article: Article) -> str:
        """Deterministic heuristic fallback when no API key is present."""
        title_lower = article.title.lower()
        content_lower = (article.content_text or "").lower()
        combined = f"{title_lower} {content_lower}"

        event_type = "OTHER"
        if "profit" in combined or "revenue" in combined or "results" in combined:
            event_type = "EARNINGS"
        elif "demerger" in combined or "merger" in combined or "acquire" in combined or "acquisition" in combined:
            event_type = "M&A"
        elif "qip" in combined or "raise" in combined or "funding" in combined:
            event_type = "FUNDRAISING"
        elif "rbi" in combined or "sebi" in combined or "penalty" in combined:
            event_type = "REGULATORY"
        elif "inflation" in combined or "gdp" in combined or "cpi" in combined:
            event_type = "MACRO"
        elif "appoint" in combined or "cfo" in combined or "ceo" in combined:
            event_type = "LEADERSHIP"

        # Extract simple numbers and percentages
        pcts = re.findall(r"\b\d+(?:\.\d+)?%", combined)[:3]
        nums = re.findall(
            r"(?:₹|\$|rs\.?\s*)\s*[\d,]+(?:\.\d+)?\s*(?:crore|cr|billion|million|b|m)?",
            combined,
            re.IGNORECASE,
        )[:3]

        companies = []
        if article.source_name:
            for candidate in ("Tata Motors", "HDFC Bank", "Reliance", "L&T", "Nvidia", "Rio Tinto", "Infosys"):
                if candidate.lower() in combined:
                    companies.append(candidate)

        payload = {
            "event_type": event_type,
            "company_names": companies,
            "financial_numbers": nums,
            "percentages": pcts,
            "deal_value": nums[0] if nums and event_type in ("M&A", "FUNDRAISING") else None,
            "market_indices": ["Nifty 50"] if "nifty" in combined else [],
            "commodity_prices": ["Brent Crude"] if "crude" in combined or "oil" in combined else [],
            "currency_values": [],
            "key_outcome": f"{article.title}.",
            "is_hard_business_event": event_type not in ("OPINION", "MARKET", "ANALYST", "OTHER"),
            "has_specific_quantified_impact": bool(nums or pcts),
            "is_investment_relevant": event_type in ("EARNINGS", "M&A", "FUNDRAISING", "REGULATORY", "MACRO"),
        }
        return json.dumps(payload)
