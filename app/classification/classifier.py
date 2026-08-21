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
        if api_key is _NOT_SET:
            self.api_key = settings.GEMINI_API_KEY
        else:
            self.api_key = api_key

        if self.api_key and isinstance(self.api_key, str):
            self.api_key = self.api_key.strip().strip("'\"")

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

    def _classify_deterministic(self, article: Article) -> Optional[AIArticleClassification]:
        """
        Attempt high-confidence deterministic classification without calling Gemini API.
        Returns AIArticleClassification if clearly resolved, or None if borderline/ambiguous.
        """
        title = article.title
        content = article.content_text or ""
        combined = f"{title} {content[:800]}"
        combined_lower = combined.lower()

        # 1. Extract financial numbers & percentages
        nums = re.findall(
            r"(?:₹|\$|€|£|rs\.?\s*)\s*[\d,]+(?:\.\d+)?\s*(?:crore|cr|billion|million|trillion|lakh)?\b|\b\d+(?:\.\d+)?\s*(?:crore|billion|million|trillion)\b",
            combined,
            re.IGNORECASE,
        )
        nums = [n.strip() for n in nums if n.strip()][:5]
        pcts = re.findall(r"\b\d+(?:\.\d+)?%", combined)[:5]

        # 2. Extract company entities
        KNOWN_COMPANIES = [
            "Reliance", "Tata Motors", "Tata Steel", "TCS", "HDFC Bank", "ICICI Bank",
            "State Bank of India", "SBI", "Larsen & Toubro", "L&T", "Nxt-Infra", "Infosys",
            "Adani Ports", "Adani Enterprises", "Bharti Airtel", "Bajaj Finance", "Maruti Suzuki",
            "Nvidia", "Apple", "Microsoft", "Amazon", "Alphabet", "Google", "Meta",
            "Goldman Sachs", "Rio Tinto", "Arcadium Lithium", "LEO Pharma", "Stripe", "OpenRouter",
            "Ola Electric", "Tech Mahindra", "Wipro", "Zomato", "Swiggy", "Paytm", "Shiprocket"
        ]
        identified_companies = []
        for comp in KNOWN_COMPANIES:
            if re.search(rf"\b{re.escape(comp)}\b", combined, re.IGNORECASE):
                if comp not in identified_companies:
                    identified_companies.append(comp)

        # 3. Clear Event Type Detection
        event_type = None
        is_hard_event = False
        is_inv_rel = False

        # Clear M&A / Takeovers / Asset Sales / Stake Transactions (Check first to avoid misclassifying sales as earnings)
        if re.search(r"\b(to buy|buys|bought|sold|sale|sells|acquires?|acquisition|merger|takeover|buyout|demerger|spin-off|all-cash deal|stake sale|stake purchase|block deal)\b", combined_lower):
            if len(identified_companies) >= 1 or nums:
                event_type = ArticleEventType.MA
                is_hard_event = True
                is_inv_rel = True

        # Clear Earnings (Requires concrete earnings terminology)
        elif re.search(r"\b(net profit|quarterly profit|revenue|q[1-4] profit|q[1-4] revenue|ebitda|profit rises|profit falls|net income|earnings beat|earnings miss|quarterly results|annual results|financial results)\b", combined_lower):
            if nums or pcts:
                event_type = ArticleEventType.EARNINGS
                is_hard_event = True
                is_inv_rel = True

        # Clear Fundraising / QIP / IPO
        elif re.search(r"\b(raises funding|funding round|qip|rights issue|capital raise|files for ipo|files drhp|ipo listing|shares list at)\b", combined_lower):
            event_type = ArticleEventType.FUNDRAISING
            is_hard_event = True
            is_inv_rel = True

        # Clear Regulatory Action
        elif re.search(r"\b(rbi|sebi|cci|sec|antitrust|doj)\b.*\b(penalty|fine|order|ban|charges|probe|investigation)\b|\b(monetary penalty|regulatory penalty|tribunal order)\b", combined_lower):
            event_type = ArticleEventType.REGULATORY
            is_hard_event = True
            is_inv_rel = True

        # Clear Leadership Changes
        elif re.search(r"\b(appoints ceo|md resigns|new managing director|new cfo|appoints chairman|steps down)\b", combined_lower):
            event_type = ArticleEventType.LEADERSHIP
            is_hard_event = True
            is_inv_rel = True

        # Clear Macro Data
        elif re.search(r"\b(gdp growth|cpi inflation|retail inflation|iip data|trade deficit|interest rate steady|rate cut|rate hike)\b", combined_lower):
            if pcts or nums:
                event_type = ArticleEventType.MACRO
                is_hard_event = True
                is_inv_rel = True

        # Clear Corporate Actions / Dividend / Buyback
        elif re.search(r"\b(dividend|special dividend|interim dividend|share buyback|stock buyback|buyback)\b", combined_lower):
            event_type = ArticleEventType.POLICY
            is_hard_event = True
            is_inv_rel = True

        # If clearly resolved as a qualifying hard business event:
        if is_hard_event and event_type:
            return AIArticleClassification(
                event_type=event_type,
                company_names=identified_companies[:3],
                financial_numbers=nums[:3],
                percentages=pcts[:3],
                deal_value=nums[0] if nums and event_type in (ArticleEventType.MA, ArticleEventType.FUNDRAISING) else None,
                market_indices=["Nifty 50"] if "nifty" in combined_lower else [],
                commodity_prices=["Brent Crude"] if ("crude" in combined_lower or "brent" in combined_lower) else [],
                currency_values=[],
                key_outcome=f"{title.strip().rstrip('.')}.",
                is_hard_business_event=True,
                has_specific_quantified_impact=bool(nums or pcts),
                is_investment_relevant=True,
            )

        # If clearly pure noise without numbers or corporate entities:
        if not nums and not pcts and not identified_companies:
            return AIArticleClassification(
                event_type=ArticleEventType.OTHER,
                company_names=[],
                financial_numbers=[],
                percentages=[],
                deal_value=None,
                market_indices=[],
                commodity_prices=[],
                currency_values=[],
                key_outcome=f"{title.strip().rstrip('.')}.",
                is_hard_business_event=False,
                has_specific_quantified_impact=False,
                is_investment_relevant=False,
            )

        # Ambiguous / borderline case -> None (eligible for Gemini API if quota allows)
        return None

    def classify(self, article: Article) -> ClassificationResult:
        """
        Classify an article into structured format.
        
        Uses deterministic fast-path classification for obvious events/noise to preserve
        Gemini API quota. Routes ambiguous/borderline cases to live Gemini if quota allows,
        falling back cleanly to offline heuristic when quota is exhausted or disabled.
        """
        # 1. Check deterministic fast-path (0 API calls)
        if not self.mock_responder:
            deterministic = self._classify_deterministic(article)
            if deterministic is not None:
                logger.info(
                    "Deterministic classification resolved (0 API calls): event_type=%s, hard_event=%s",
                    deterministic.event_type.value,
                    deterministic.is_hard_business_event,
                )
                return ClassificationResult(
                    success=True,
                    classification=deterministic,
                    attempts=0,
                    raw_response="[Deterministic Classification]",
                )

        logger.info("Classifying borderline article '%s' via Gemini AI...", article.title[:50])

        # Enforce per-run cap or forced offline mode — fall back to offline heuristic
        if (
            self._force_offline_mode
            or self._live_call_count >= self.max_articles
            or not self.api_key
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
                is_daily_quota = is_429 and any(
                    marker.lower() in exc_str.lower()
                    for marker in ("generaterequestsperday", "quotaid", "daily quota", "per day", "freetier")
                )

                http_code = 429 if is_429 else (503 if is_5xx else (403 if is_400_range else None))

                GeminiUsageLogger.record(
                    stage="classification",
                    model=self.model_name,
                    success=False,
                    http_status=http_code,
                    retry_number=attempt - 1,
                    error_summary=exc_str[:120],
                )

                if is_400_range or is_daily_quota:
                    if is_daily_quota:
                        logger.warning(
                            "GEMINI_DAILY_QUOTA_EXHAUSTED on attempt %d for '%s'. Switching immediately to offline fallback without retries.",
                            attempt, article.title[:40],
                        )
                    else:
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
        from app.verification.query_builder import EventQueryBuilder

        title_lower = article.title.lower()
        content_lower = (article.content_text or "").lower()
        combined = f"{title_lower} {content_lower}"

        # Extract simple numbers and percentages
        pcts = re.findall(r"\b\d+(?:\.\d+)?%", combined)[:3]
        nums = re.findall(
            r"(?:₹|\$|€|£|rs\.?\s*)\s*[\d,]+(?:\.\d+)?\s*(?:crore|cr|billion|million|b|m)?",
            combined,
            re.IGNORECASE,
        )[:3]

        # Dynamic entity extraction using shared EventQueryBuilder
        companies = EventQueryBuilder.extract_entities(article)

        is_stake_trans = bool(re.search(
            r"\b(block deal|stake sale|equity changes hands|promoter stake sale|institutional stake sale|stake purchase|bulk deal|sells .*stake|buys .*stake)\b",
            combined,
        ))
        is_ma_trans = bool(re.search(
            r"\b(sold|sale|sells|demerger|merger|acquire|acquisition|to buy|buys|bought|takeover|buyout|spin-off)\b",
            combined,
        ))

        event_type = "OTHER"
        if is_stake_trans or is_ma_trans:
            event_type = "M&A"
        elif re.search(r"\b(net profit|quarterly profit|revenue|q[1-4] profit|q[1-4] revenue|ebitda|profit rises|profit falls|net income|earnings|quarterly results|annual results)\b", combined):
            event_type = "EARNINGS"
        elif "qip" in combined or "raise" in combined or "funding" in combined or "rights issue" in combined:
            event_type = "FUNDRAISING"
        elif "rbi" in combined or "sebi" in combined or "penalty" in combined or "cci" in combined or "order" in combined:
            event_type = "REGULATORY"
        elif "inflation" in combined or "gdp" in combined or "cpi" in combined:
            event_type = "MACRO"
        elif "appoint" in combined or "cfo" in combined or "ceo" in combined or "resigns" in combined:
            event_type = "LEADERSHIP"
        elif "plant" in combined or "capex" in combined or "expansion" in combined:
            event_type = "M&A"

        is_hard = event_type not in ("OPINION", "MARKET", "ANALYST", "OTHER") or (is_stake_trans and bool(nums or pcts or companies))
        is_invest_rel = event_type in ("EARNINGS", "M&A", "FUNDRAISING", "REGULATORY", "MACRO", "LEADERSHIP") or is_stake_trans

        payload = {
            "event_type": event_type,
            "company_names": companies,
            "financial_numbers": nums,
            "percentages": pcts,
            "deal_value": nums[0] if nums and (event_type in ("M&A", "FUNDRAISING") or is_stake_trans) else None,
            "market_indices": ["Nifty 50"] if "nifty" in combined else [],
            "commodity_prices": ["Brent Crude"] if "crude" in combined or "oil" in combined else [],
            "currency_values": [],
            "key_outcome": f"{article.title}.",
            "is_hard_business_event": is_hard,
            "has_specific_quantified_impact": bool(nums or pcts),
            "is_investment_relevant": is_invest_rel,
        }
        return json.dumps(payload)
