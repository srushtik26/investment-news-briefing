"""
Tests for Structural Refactor Compatibility and Behavioral Parity:
- Verifies all compatibility imports from run_pipeline continue to work.
- Verifies app.pipeline modules export expected helpers.
- Verifies deterministic mock pipeline run works identically without live API calls.
"""

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
import pytest

import run_pipeline
from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
import app.pipeline as pipeline_pkg
from app.pipeline.context import PipelineContext


def test_1_run_pipeline_all_symbols_exported():
    """Verify run_pipeline exports all public helpers and required symbols."""
    expected_symbols = [
        "run_pipeline",
        "urlparse",
        "Article",
        "Event",
        "NewsCategory",
        "NewsDiscoveryService",
        "HardFilterEngine",
        "DomesticHardFilterEngine",
        "EditorialStorySelection",
        "BriefingEditorialPayload",
        "DomesticTrendingEvaluator",
        "_extract_candidates",
        "get_candidate_published_at",
        "get_article_age_hours",
        "populate_event_companies",
        "_score_discovery_candidate",
        "get_fallback_search_window",
        "evaluate_single_source_for_horizon",
        "get_quality_level",
        "get_section_quality_state",
        "prioritize_intl_final_mile_candidates",
        "get_intl_serpapi_reservation",
        "get_serpapi_event_key",
        "has_unattempted_intl_upgrade",
        "get_general_serpapi_limit",
        "should_stop_expansion",
        "ladder_quality_key",
    ]
    for sym in expected_symbols:
        assert hasattr(run_pipeline, sym), f"run_pipeline missing exported symbol: {sym}"


def test_2_app_pipeline_package_exports():
    """Verify app.pipeline package exports public interface."""
    assert hasattr(pipeline_pkg, "run_pipeline")
    assert hasattr(pipeline_pkg, "PipelineContext")
    assert hasattr(pipeline_pkg, "_extract_candidates")
    assert hasattr(pipeline_pkg, "run_expansion_and_fallbacks")
    assert hasattr(pipeline_pkg, "run_second_source_enrichment")
    assert hasattr(pipeline_pkg, "run_deduplication")
    assert hasattr(pipeline_pkg, "run_ranking_and_selection")


def test_3_score_discovery_candidate_behavioral_parity():
    """Verify _score_discovery_candidate produces exact scores."""
    test_cases = [
        ("Tata Motors acquires 50% stake in joint venture for Rs 500 crore", 50.0 + 20.0 + 25.0),
        ("Stock to buy: brokerage target price recommendation", 50.0 - 40.0),
        ("Generic company announcement", 50.0),
    ]
    for headline, expected_score in test_cases:
        score_from_compat = run_pipeline._score_discovery_candidate(headline)
        score_from_mod = pipeline_pkg._score_discovery_candidate(headline)
        assert score_from_compat == score_from_mod == expected_score


def test_4_get_fallback_search_window_parity():
    """Verify get_fallback_search_window returns when:1d."""
    assert run_pipeline.get_fallback_search_window(1) == "when:1d"
    assert run_pipeline.get_fallback_search_window(2) == "when:1d"
    assert pipeline_pkg.get_fallback_search_window(1) == "when:1d"


def test_5_get_section_quality_state_parity():
    """Verify get_section_quality_state produces identical state dictionary."""
    ev1 = Event(
        canonical_title="Event 1 Title",
        description="Event 1 Description",
        article_ids=["a1"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
    )
    ev2 = Event(
        canonical_title="Event 2 Title",
        description="Event 2 Description",
        article_ids=["a2"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
    )
    state = run_pipeline.get_section_quality_state([ev1], [ev2], NewsCategory.INDIA)
    assert state["two_source_count"] == 1
    assert state["single_source_count"] == 1
    assert state["eligible_total"] == 2
    assert state["slot_deficit"] == 3
    assert state["section_complete"] is False


def test_6_line_count_targets():
    """Verify run_pipeline.py line count is well under 150 lines."""
    with open("run_pipeline.py", "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) < 150, f"run_pipeline.py length {len(lines)} exceeds target of 150 lines!"


def test_7_setup_logging_called_with_valid_arguments():
    """Verify setup_logging signature accepts log_file and runner doesn't pass log_dir."""
    import inspect
    from app.logging_config import setup_logging
    sig = inspect.signature(setup_logging)
    assert "log_file" in sig.parameters
    assert "log_dir" not in sig.parameters

    with open("app/pipeline/runner.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "log_dir=logs_dir" not in content
    assert "log_file=" in content


def test_8_core_services_runtime_construction_smoke_test(tmp_path):
    """
    Runtime-construction smoke test that initializes the pipeline's core services
    and PipelineContext without making network/API calls.
    Catches invalid constructor keyword arguments (e.g. log_dir, use_serpapi).
    """
    from app.logging_config import setup_logging
    from app.deduplication import HistoryStore, DeduplicationEngine
    from app.discovery import NewsDiscoveryService
    from app.discovery.rss_provider import GoogleNewsRSSDiscoveryProvider
    from app.extraction import ArticleExtractor
    from app.filtering.engine import HardFilterEngine, DomesticHardFilterEngine
    from app.classification import AIArticleClassifier
    from app.classification.region_classifier import EventRegionClassifier
    from app.verification import TwoSourceVerifier, ActiveCorroborator
    from app.verification.single_source import SingleSourceEvaluator
    from app.verification.domestic_trending import DomesticTrendingEvaluator
    from app.ranking import CandidatePoolRanker, ArticlePreRanker
    from app.ranking.scorer import InvestmentRelevanceScorer
    from app.ai import GeminiEditorialEngine
    from app.validation import FinalValidationEngine
    from app.formatting.formatter import BriefingFormatter
    from config import get_settings

    settings = get_settings()
    log_file = tmp_path / "app.log"

    # Verify setup_logging
    setup_logging(log_level=settings.LOG_LEVEL, log_file=log_file)

    # Instantiate core services exactly as runner.py does
    history_store = HistoryStore()
    rss_provider = GoogleNewsRSSDiscoveryProvider()
    discovery_service = NewsDiscoveryService(provider=rss_provider)
    assert discovery_service.provider.provider_name == "GoogleNewsRSSDiscoveryProvider"
    assert discovery_service.provider.provider_name != "MockDiscoveryProvider"

    extractor = ArticleExtractor()
    extractor.reset_run_health()
    business_filter_engine = HardFilterEngine()
    domestic_filter_engine = DomesticHardFilterEngine()
    classifier = AIArticleClassifier(api_key="")  # offline mode, no network
    reg_clf = EventRegionClassifier()
    verifier = TwoSourceVerifier()
    active_corroborator = ActiveCorroborator(extractor=extractor)
    single_source_evaluator = SingleSourceEvaluator()
    domestic_evaluator = DomesticTrendingEvaluator()
    dedup_engine = DeduplicationEngine(history_store=history_store)
    pre_ranker = ArticlePreRanker()
    assert hasattr(pre_ranker, "select_top_balanced_candidates")
    assert not hasattr(pre_ranker, "rank_articles")

    ranker = CandidatePoolRanker()
    scorer = InvestmentRelevanceScorer()
    editorial_engine = GeminiEditorialEngine(api_key="")  # offline mode, no network
    validator = FinalValidationEngine(history_store=history_store)
    formatter = BriefingFormatter()

    # Instantiate PipelineContext
    ctx = PipelineContext(
        run_reference_time=datetime.now(timezone.utc),
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        settings=settings,
        discovery_service=discovery_service,
        extractor=extractor,
        business_filter_engine=business_filter_engine,
        domestic_filter_engine=domestic_filter_engine,
        classifier=classifier,
        reg_clf=reg_clf,
        verifier=verifier,
        active_corroborator=active_corroborator,
        single_source_evaluator=single_source_evaluator,
        domestic_evaluator=domestic_evaluator,
        history_store=history_store,
        dedup_engine=dedup_engine,
        ranker=ranker,
        scorer=scorer,
        editorial_engine=editorial_engine,
        validator=validator,
        formatter=formatter,
    )
    assert ctx is not None

    # Static audit of runner.py to ensure prohibited keyword arguments never return
    with open("app/pipeline/runner.py", "r", encoding="utf-8") as f:
        runner_code = f.read()
    assert "log_dir=" not in runner_code
    assert "use_serpapi=" not in runner_code
    assert "rank_articles(" not in runner_code
    assert "select_top_balanced_candidates(" in runner_code
    assert "GoogleNewsRSSDiscoveryProvider()" in runner_code
    assert "used_offline_fallback" not in runner_code
    assert "res.rate_limited" not in runner_code
    assert "v_res.primary_publisher" not in runner_code
    assert "corrob_res.is_corroborated" not in runner_code
    assert "corrob_res.second_article" not in runner_code


def test_9_runner_production_wiring_and_execution_smoke_test(tmp_path):
    """
    Execute runner.run_pipeline with mocked external I/O to verify full
    production wiring without network or live API calls.
    """
    from app.pipeline.runner import run_pipeline

    with patch("app.pipeline.runner.NewsDiscoveryService") as mock_disc_cls, \
         patch("app.pipeline.runner.discover_initial_reserves") as mock_disc, \
         patch("app.pipeline.runner._extract_candidates") as mock_ext:

        # Mock initial discovery to return 0 candidates
        mock_disc.return_value = ([], 0, 0, 0)
        mock_ext.return_value = ([], [], 0, 0, 0, 0, 0)

        # Run pipeline
        exit_code = run_pipeline(
            max_india=5,
            max_international=5,
            run_reference_time=datetime.now(timezone.utc),
        )
        # Should exit with code 1 due to insufficient stories (0 discovered), but without crashing
        assert exit_code in (0, 1)

        # Verify NewsDiscoveryService was instantiated with GoogleNewsRSSDiscoveryProvider
        assert mock_disc_cls.call_count == 1
        provider_arg = mock_disc_cls.call_args.kwargs.get("provider")
        assert provider_arg is not None
        assert provider_arg.__class__.__name__ == "GoogleNewsRSSDiscoveryProvider"


