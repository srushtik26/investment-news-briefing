"""
Pipeline Performance Metrics and Observability Tracking.

Tracks stage execution times (discovery, extraction, filtering, verification,
enrichment, editorial, total) and performance counters (cache hits/misses,
skips, early stopping). Thread-safe for bounded concurrency.
"""

import time
import threading
from typing import Dict, Optional, Any
from app.logging_config import get_logger

logger = get_logger("utils.performance_metrics")


class PipelineMetrics:
    """
    Thread-safe performance metrics and counters tracker for the briefing pipeline.
    """

    _instance = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._timers: Dict[str, float] = {
            "discovery_seconds": 0.0,
            "extraction_seconds": 0.0,
            "filtering_seconds": 0.0,
            "verification_seconds": 0.0,
            "enrichment_seconds": 0.0,
            "editorial_seconds": 0.0,
            "total_seconds": 0.0,
        }
        self._active_start: Dict[str, float] = {}
        self._counters: Dict[str, int] = {
            "extraction_cache_hits": 0,
            "extraction_cache_misses": 0,
            "google_resolution_cache_hits": 0,
            "pre_extraction_skips": 0,
            "degraded_domain_skips": 0,
            "early_stop_count": 0,
        }

    @classmethod
    def get_instance(cls) -> "PipelineMetrics":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls) -> "PipelineMetrics":
        with cls._lock:
            cls._instance = cls()
            return cls._instance

    def start_timer(self, stage: str) -> None:
        """Record start time for a stage."""
        with self._lock:
            self._active_start[stage] = time.perf_counter()

    def stop_timer(self, stage: str) -> float:
        """Stop timer for a stage, add elapsed time, and return it."""
        with self._lock:
            start = self._active_start.pop(stage, None)
            if start is not None:
                elapsed = time.perf_counter() - start
                timer_key = f"{stage}_seconds" if not stage.endswith("_seconds") else stage
                self._timers[timer_key] = self._timers.get(timer_key, 0.0) + elapsed
                return elapsed
            return 0.0

    def increment(self, counter_name: str, amount: int = 1) -> int:
        """Increment a named performance counter."""
        with self._lock:
            val = self._counters.get(counter_name, 0) + amount
            self._counters[counter_name] = val
            return val

    def get_counter(self, counter_name: str) -> int:
        """Get value of a named counter."""
        with self._lock:
            return self._counters.get(counter_name, 0)

    def get_timer(self, timer_name: str) -> float:
        """Get elapsed seconds for a timer."""
        with self._lock:
            key = f"{timer_name}_seconds" if not timer_name.endswith("_seconds") else timer_name
            return self._timers.get(key, 0.0)

    def get_summary(self) -> Dict[str, Any]:
        """Return full dictionary of timers and counters."""
        with self._lock:
            return {
                "timers": dict(self._timers),
                "counters": dict(self._counters),
            }

    def format_summary(self) -> str:
        """Return formatted multi-line summary of pipeline timing and counters."""
        with self._lock:
            timers = dict(self._timers)
            counters = dict(self._counters)

        lines = [
            "============================================================",
            "PIPELINE PERFORMANCE & SPEED METRICS",
            "============================================================",
            f"  Discovery Time:           {timers.get('discovery_seconds', 0.0):.2f}s",
            f"  Extraction Time:          {timers.get('extraction_seconds', 0.0):.2f}s",
            f"  Filtering Time:           {timers.get('filtering_seconds', 0.0):.2f}s",
            f"  Verification Time:        {timers.get('verification_seconds', 0.0):.2f}s",
            f"  Enrichment Time:          {timers.get('enrichment_seconds', 0.0):.2f}s",
            f"  Editorial Time:           {timers.get('editorial_seconds', 0.0):.2f}s",
            f"  Total Pipeline Time:      {timers.get('total_seconds', 0.0):.2f}s",
            "------------------------------------------------------------",
            f"  Extraction Cache Hits:    {counters.get('extraction_cache_hits', 0)}",
            f"  Extraction Cache Misses:  {counters.get('extraction_cache_misses', 0)}",
            f"  Google Resolution Hits:   {counters.get('google_resolution_cache_hits', 0)}",
            f"  Pre-Extraction Skips:     {counters.get('pre_extraction_skips', 0)}",
            f"  Degraded Domain Skips:    {counters.get('degraded_domain_skips', 0)}",
            f"  Early Stop Trigger Count: {counters.get('early_stop_count', 0)}",
            "============================================================",
        ]
        return "\n".join(lines)
