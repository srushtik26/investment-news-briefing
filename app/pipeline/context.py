"""
Pipeline Context: shared runtime state and configuration across all pipeline stages.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Any, Optional, Callable

from app.models import Article, Event
from app.utils.performance_metrics import PipelineMetrics


@dataclass
class PipelineContext:
    """Shared execution context passed across modular pipeline stages."""
    # Run configuration & timing
    run_reference_time: datetime
    data_dir: Path
    logs_dir: Path
    settings: Any
    max_india: Optional[int] = None
    max_international: Optional[int] = None

    # Logging callback
    log_exec: Callable[[str], None] = field(default=lambda m: None)

    # Core services & engines
    discovery_service: Any = None
    extractor: Any = None
    business_filter_engine: Any = None
    domestic_filter_engine: Any = None
    classifier: Any = None
    reg_clf: Any = None
    verifier: Any = None
    active_corroborator: Any = None
    single_source_evaluator: Any = None
    domestic_evaluator: Any = None
    history_store: Any = None
    dedup_engine: Any = None
    ranker: Any = None
    scorer: Any = None
    editorial_engine: Any = None
    validator: Any = None
    formatter: Any = None
    metrics: PipelineMetrics = field(default_factory=PipelineMetrics.get_instance)

    # In-memory working collections
    articles_lookup: Dict[str, Article] = field(default_factory=dict)
    seen_urls: Set[str] = field(default_factory=set)
    verified_events: List[Event] = field(default_factory=list)
    high_confidence_single_candidates: List[Event] = field(default_factory=list)
    single_source_events: List[Event] = field(default_factory=list)
    fallback_events: List[Event] = field(default_factory=list)
    all_extracted: List[Article] = field(default_factory=list)
    all_records: List[Dict[str, Any]] = field(default_factory=list)
    rejections: List[Any] = field(default_factory=list)
    date_deferred_articles: List[Article] = field(default_factory=list)
    rejected_events_list: List[Dict[str, Any]] = field(default_factory=list)
    class_map: Dict[str, Any] = field(default_factory=dict)

    # Reserve pools
    domestic_reserve_pool: List[Any] = field(default_factory=list)
    india_reserve_pool: List[Any] = field(default_factory=list)
    intl_reserve_pool: List[Any] = field(default_factory=list)

    # Execution counters
    corroboration_searches: int = 0
    second_sources_found: int = 0
    organic_second_sources_found: int = 0
    rss_india_used: int = 0
    rss_international_used: int = 0
    internal_pipeline_errors: int = 0
