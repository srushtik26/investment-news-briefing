"""
API Budget Manager.

Tracks and bounds external API calls (Gemini, SerpAPI, HTTP requests, retries,
portfolio queries, and general India queries) per pipeline run.
"""

from dataclasses import dataclass, field
import logging
from typing import Optional

logger = logging.getLogger("pipeline.budget")


@dataclass
class ApiBudget:
    """Tracks and limits API resource consumption during briefing generation."""
    max_gemini_calls: int = 50
    max_serpapi_calls: int = 30
    max_http_requests: int = 150
    
    gemini_calls: int = 0
    serpapi_calls: int = 0
    http_requests: int = 0
    retries: int = 0
    portfolio_queries: int = 0
    general_india_queries: int = 0
    gemini_offline_mode: bool = False

    def can_call_gemini(self) -> bool:
        """Check if Gemini API calls are permitted."""
        if self.gemini_offline_mode:
            return False
        return self.gemini_calls < self.max_gemini_calls

    def record_gemini_call(self, success: bool = True, status_code: Optional[int] = None) -> None:
        """Record a Gemini API call and transition to offline mode if quota exhausted."""
        self.gemini_calls += 1
        if status_code == 429:
            logger.warning("[API_BUDGET] Gemini 429 quota exhausted; switching immediately to deterministic offline mode")
            self.gemini_offline_mode = True

    def record_serpapi_call(self) -> None:
        """Record a SerpAPI query."""
        self.serpapi_calls += 1

    def record_http_request(self) -> None:
        """Record an HTTP fetch request."""
        self.http_requests += 1

    def record_retry(self) -> None:
        """Record a retry attempt."""
        self.retries += 1

    def record_portfolio_query(self) -> None:
        """Record a portfolio discovery query."""
        self.portfolio_queries += 1

    def record_general_india_query(self) -> None:
        """Record a general India discovery query."""
        self.general_india_queries += 1

    def can_call_serpapi(self) -> bool:
        """Check if SerpAPI queries are within budget."""
        return self.serpapi_calls < self.max_serpapi_calls

    def log_budget_summary(self) -> str:
        """Return formatted budget summary for production logging."""
        summary = (
            f"[API_BUDGET_SUMMARY]\n"
            f"gemini_calls={self.gemini_calls}/{self.max_gemini_calls}\n"
            f"serpapi_calls={self.serpapi_calls}/{self.max_serpapi_calls}\n"
            f"http_requests={self.http_requests}\n"
            f"retries={self.retries}\n"
            f"portfolio_queries={self.portfolio_queries}\n"
            f"general_india_queries={self.general_india_queries}\n"
            f"gemini_offline_mode={'true' if self.gemini_offline_mode else 'false'}"
        )
        logger.info(summary)
        return summary
