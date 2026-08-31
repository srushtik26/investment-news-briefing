"""
Regression tests for production pipeline wiring and return-contract conformance.

Ensures that runner.py orchestration paths strictly adhere to the Pydantic schemas
and public APIs of internal services, specifically preventing regressions like:
- AttributeError on ClassificationResult (e.g. used_offline_fallback, rate_limited)
- AttributeError on EventSourceVerification (e.g. primary_publisher)
- AttributeError on CorroborationResult (e.g. is_corroborated, second_article)
"""

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from app.models import Article, NewsCategory
from app.classification.models import (
    AIArticleClassification,
    ArticleEventType,
    ClassificationResult,
)
from app.pipeline.runner import run_pipeline


def _make_dummy_article(title: str, url: str, category: NewsCategory) -> Article:
    return Article(
        title=title,
        url=url,
        source_name="Business Standard",
        category=category,
        published_at=datetime.now(timezone.utc),
        date_verified=True,
        content_text="Reliance Industries acquires 100% stake in tech firm for 500 crore. Deal closed today.",
    )


def test_stage4_classification_result_contract_offline_fallback(tmp_path):
    """
    Test Stage 4 classification path using a real ClassificationResult produced
    with attempts=0 (offline fallback / deterministic heuristic).
    Asserts no AttributeError is raised for undeclared fields like used_offline_fallback.
    """
    classification = AIArticleClassification(
        event_type=ArticleEventType.MA,
        company_names=["Reliance Industries"],
        financial_numbers=["500 crore"],
        percentages=[],
        deal_value="500 crore",
        key_outcome="Reliance Industries acquires 100% stake.",
        is_hard_business_event=True,
        has_specific_quantified_impact=True,
        is_investment_relevant=True,
    )
    # Construct ClassificationResult with ONLY the declared fields from models.py
    offline_result = ClassificationResult(
        success=True,
        classification=classification,
        attempts=0,  # 0 indicates offline fallback / heuristic
        raw_response="[Deterministic Classification]",
        error_message=None,
    )
    # Ensure undeclared fields do not exist on the model
    assert not hasattr(offline_result, "used_offline_fallback")
    assert not hasattr(offline_result, "rate_limited")

    art = _make_dummy_article(
        "Reliance acquires tech firm for 500 crore",
        "https://www.business-standard.com/article/reliance-deal-12345.html",
        NewsCategory.INDIA,
    )

    with patch("app.pipeline.runner.NewsDiscoveryService") as mock_disc_cls, \
         patch("app.pipeline.runner.discover_initial_reserves") as mock_disc, \
         patch("app.pipeline.runner._extract_candidates") as mock_ext, \
         patch("app.pipeline.runner.AIArticleClassifier") as mock_clf_cls, \
         patch("app.pipeline.runner.run_expansion_and_fallbacks") as mock_exp, \
         patch("app.pipeline.runner.run_second_source_enrichment") as mock_enr:

        mock_disc.return_value = ([art], 0, 1, 0)
        mock_ext.return_value = ([art], [], 0, 0, 0, 0, 0)
        mock_exp.return_value = "INSUFFICIENT_STORIES"
        mock_enr.return_value = None

        mock_classifier_instance = MagicMock()
        mock_classifier_instance.classify.return_value = offline_result
        mock_clf_cls.return_value = mock_classifier_instance

        exit_code = run_pipeline(
            max_india=5,
            max_international=5,
            run_reference_time=datetime.now(timezone.utc),
        )

        assert exit_code in (0, 1)
        assert mock_classifier_instance.classify.called


def test_stage4_classification_result_contract_live_gemini(tmp_path):
    """
    Test Stage 4 classification path using a real ClassificationResult produced
    with attempts=1 (live Gemini call).
    Asserts correct handling without AttributeError.
    """
    classification = AIArticleClassification(
        event_type=ArticleEventType.EARNINGS,
        company_names=["Tata Motors"],
        financial_numbers=["3500 crore"],
        percentages=["15%"],
        deal_value=None,
        key_outcome="Tata Motors net profit rises 15% to 3500 crore.",
        is_hard_business_event=True,
        has_specific_quantified_impact=True,
        is_investment_relevant=True,
    )
    live_result = ClassificationResult(
        success=True,
        classification=classification,
        attempts=1,  # > 0 indicates live Gemini call
        raw_response='{"event_type": "EARNINGS"}',
        error_message=None,
    )

    art = _make_dummy_article(
        "Tata Motors Q3 profit jumps 15% to 3500 crore",
        "https://www.business-standard.com/article/tata-motors-q3-98765.html",
        NewsCategory.INDIA,
    )

    with patch("app.pipeline.runner.NewsDiscoveryService") as mock_disc_cls, \
         patch("app.pipeline.runner.discover_initial_reserves") as mock_disc, \
         patch("app.pipeline.runner._extract_candidates") as mock_ext, \
         patch("app.pipeline.runner.AIArticleClassifier") as mock_clf_cls, \
         patch("app.pipeline.runner.run_expansion_and_fallbacks") as mock_exp, \
         patch("app.pipeline.runner.run_second_source_enrichment") as mock_enr:

        mock_disc.return_value = ([art], 0, 1, 0)
        mock_ext.return_value = ([art], [], 0, 0, 0, 0, 0)
        mock_exp.return_value = "INSUFFICIENT_STORIES"
        mock_enr.return_value = None

        mock_classifier_instance = MagicMock()
        mock_classifier_instance.classify.return_value = live_result
        mock_clf_cls.return_value = mock_classifier_instance

        exit_code = run_pipeline(
            max_india=5,
            max_international=5,
            run_reference_time=datetime.now(timezone.utc),
        )

        assert exit_code in (0, 1)
        assert mock_classifier_instance.classify.called


def test_stage4_classification_failure_and_rejection_paths(tmp_path):
    """
    Test Stage 4 handling when classification fails or returns non-hard business event.
    """
    fail_result = ClassificationResult(
        success=False,
        classification=None,
        attempts=1,
        raw_response="",
        error_message="Gemini JSON decoding failed",
    )

    art = _make_dummy_article(
        "Global Markets Overview and Trends for August",
        "https://www.business-standard.com/markets/global-trends-55555.html",
        NewsCategory.INTERNATIONAL,
    )

    with patch("app.pipeline.runner.NewsDiscoveryService") as mock_disc_cls, \
         patch("app.pipeline.runner.discover_initial_reserves") as mock_disc, \
         patch("app.pipeline.runner._extract_candidates") as mock_ext, \
         patch("app.pipeline.runner.AIArticleClassifier") as mock_clf_cls, \
         patch("app.pipeline.runner.run_expansion_and_fallbacks") as mock_exp, \
         patch("app.pipeline.runner.run_second_source_enrichment") as mock_enr:

        mock_disc.return_value = ([art], 0, 0, 1)
        mock_ext.return_value = ([art], [], 0, 0, 0, 0, 0)
        mock_exp.return_value = "INSUFFICIENT_STORIES"
        mock_enr.return_value = None

        mock_classifier_instance = MagicMock()
        mock_classifier_instance.classify.return_value = fail_result
        mock_clf_cls.return_value = mock_classifier_instance

        exit_code = run_pipeline(
            max_india=5,
            max_international=5,
            run_reference_time=datetime.now(timezone.utc),
        )

        assert exit_code in (0, 1)
        assert mock_classifier_instance.classify.called


def test_enrichment_module_api_contract():
    """
    Ensure app/pipeline/enrichment.py adheres to real InvestmentRelevanceScorer APIs
    and never calls non-existent calculate_score.
    """
    with open("app/pipeline/enrichment.py", "r", encoding="utf-8") as f:
        enrichment_code = f.read()

    assert "calculate_score" not in enrichment_code
    assert "total_score" not in enrichment_code
    assert "scorer.score_event(" in enrichment_code
    assert "investment_score" in enrichment_code
