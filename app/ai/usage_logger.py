"""
Gemini API Usage Logger.

Tracks every Gemini API call made during a pipeline run and emits a
structured usage summary at the end of the run.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from app.logging_config import get_logger

logger = get_logger("ai.usage")


@dataclass
class GeminiCallRecord:
    """Record of a single Gemini API call."""
    timestamp: str
    stage: str               # "classification" | "editorial"
    model: str
    request_number: int      # 1-based index within the stage
    success: bool
    http_status: Optional[int]   # None on success; 429 on rate limit; etc.
    retry_number: int        # 0 = first attempt, 1 = first retry, etc.
    error_summary: Optional[str] = None


class GeminiUsageLogger:
    """
    Thread-safe singleton logger for Gemini API usage during a pipeline run.
    Call reset() at the start of each run.
    """

    _lock = threading.Lock()
    _records: List[GeminiCallRecord] = []
    _request_counters: dict = {}   # stage -> count

    @classmethod
    def reset(cls) -> None:
        """Clear all records. Call once at the beginning of each pipeline run."""
        with cls._lock:
            cls._records = []
            cls._request_counters = {}

    @classmethod
    def record(
        cls,
        stage: str,
        model: str,
        success: bool,
        http_status: Optional[int] = None,
        retry_number: int = 0,
        error_summary: Optional[str] = None,
    ) -> None:
        """Record a single Gemini API call."""
        with cls._lock:
            if stage not in cls._request_counters:
                cls._request_counters[stage] = 0
            cls._request_counters[stage] += 1
            req_num = cls._request_counters[stage]

            rec = GeminiCallRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                stage=stage,
                model=model,
                request_number=req_num,
                success=success,
                http_status=http_status,
                retry_number=retry_number,
                error_summary=error_summary,
            )
            cls._records.append(rec)

            status_tag = "OK" if success else f"FAIL({http_status or 'ERR'})"
            retry_tag = f" retry#{retry_number}" if retry_number > 0 else ""
            logger.info(
                "[Gemini] stage=%s req#%d%s %s model=%s",
                stage, req_num, retry_tag, status_tag, model,
            )

    @classmethod
    def summary(cls) -> dict:
        """Return a structured summary dict for end-of-run reporting."""
        with cls._lock:
            total = len(cls._records)
            successful = sum(1 for r in cls._records if r.success)
            failed = total - successful
            rate_limited = sum(1 for r in cls._records if r.http_status == 429)
            retries = sum(1 for r in cls._records if r.retry_number > 0)
            return {
                "total_calls": total,
                "successful": successful,
                "failed": failed,
                "rate_limited_429": rate_limited,
                "retries": retries,
                "by_stage": cls._request_counters.copy(),
            }

    @classmethod
    def print_summary(cls) -> None:
        """Print a formatted end-of-run Gemini usage report."""
        s = cls.summary()
        print("\n--- Gemini API Usage Summary ---")
        print(f"  Gemini calls:   {s['total_calls']}")
        print(f"  Successful:     {s['successful']}")
        print(f"  Failed:         {s['failed']}")
        print(f"  429 (rate lim): {s['rate_limited_429']}")
        print(f"  Retries:        {s['retries']}")
        print(f"  By stage:       {s['by_stage']}")
        print("--------------------------------\n")
