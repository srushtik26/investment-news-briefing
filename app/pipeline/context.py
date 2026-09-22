"""
Pipeline Context: shared runtime state and configuration across all pipeline stages.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Any, Optional, Callable

from config import get_settings, get_target_date_ist
from app.models import Article, Event
from app.utils.performance_metrics import PipelineMetrics


@dataclass
class PipelineContext:
    """Shared execution context passed across modular pipeline stages."""
    # Run configuration & timing
    run_reference_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    target_date: Optional[date] = None
    data_dir: Optional[Path] = field(default_factory=lambda: Path("data"))
    logs_dir: Optional[Path] = field(default_factory=lambda: Path("logs"))
    settings: Any = field(default_factory=lambda: get_settings())
    max_india: Optional[int] = None
    max_international: Optional[int] = None
    is_weekend: bool = False

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
    validation_run: bool = False
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
    stage6_rejected_stories: List[Dict[str, Any]] = field(default_factory=list)
    class_map: Dict[str, Any] = field(default_factory=dict)

    # Reserve pools
    domestic_reserve_pool: List[Any] = field(default_factory=list)
    india_reserve_pool: List[Any] = field(default_factory=list)
    intl_reserve_pool: List[Any] = field(default_factory=list)

    # Deduplication and Refill Exclusion Sets
    seen_event_ids: Set[str] = field(default_factory=set)
    dedup_rejected_event_ids: Set[str] = field(default_factory=set)
    refill_attempted_event_ids: Set[str] = field(default_factory=set)
    failed_urls: Set[str] = field(default_factory=set)
    failed_domains: Set[str] = field(default_factory=set)

    # Execution counters
    corroboration_searches: int = 0
    second_sources_found: int = 0
    organic_second_sources_found: int = 0
    rss_domestic_used: int = 0
    rss_india_used: int = 0
    rss_international_used: int = 0
    internal_pipeline_errors: int = 0
    portfolio_discovery_executed: bool = False

    def __post_init__(self):
        """Initialize missing default engines if not provided."""
        if self.target_date is None:
            self.target_date = get_target_date_ist(self.run_reference_time)
        if self.target_date is not None:
            self.is_weekend = self.target_date.weekday() in (5, 6)
        if self.scorer is None:
            from app.ranking.scorer import InvestmentRelevanceScorer
            self.scorer = InvestmentRelevanceScorer()
        if self.ranker is None:
            from app.ranking import CandidatePoolRanker
            self.ranker = CandidatePoolRanker(scorer=self.scorer)
        if self.reg_clf is None:
            from app.classification.region_classifier import EventRegionClassifier
            self.reg_clf = EventRegionClassifier()
        if self.history_store is None:
            from app.deduplication import HistoryStore
            from config import is_testing_or_dry_run
            is_iso = self.validation_run or is_testing_or_dry_run()
            if is_iso:
                self.history_store = HistoryStore(is_isolated=True)
            elif self.data_dir is None:
                self.history_store = HistoryStore(db_path=":memory:", is_isolated=False)
            else:
                self.data_dir = Path(self.data_dir)
                self.data_dir.mkdir(parents=True, exist_ok=True)
                db_target = self.data_dir / "briefings.db"
                if not db_target.exists() and (self.data_dir / "briefing_history.json").exists():
                    db_target = self.data_dir / "briefing_history.json"
                self.history_store = HistoryStore(db_path=str(db_target), is_isolated=False)
        if self.dedup_engine is None:
            from app.deduplication import DeduplicationEngine
            self.dedup_engine = DeduplicationEngine(history_store=self.history_store)
        if self.verifier is None:
            from app.verification import TwoSourceVerifier
            self.verifier = TwoSourceVerifier()
        if self.domestic_evaluator is None:
            from app.verification.domestic_trending import DomesticTrendingEvaluator
            self.domestic_evaluator = DomesticTrendingEvaluator()
