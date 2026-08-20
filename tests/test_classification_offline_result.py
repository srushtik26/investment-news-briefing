"""
Unit test for ClassificationResult attempts validation with offline fallback.
"""

from app.classification.models import ClassificationResult, AIArticleClassification, ArticleEventType


def test_classification_result_accepts_zero_attempts():
    """Verify ClassificationResult accepts attempts=0 for offline fallback calls."""
    classification = AIArticleClassification(
        event_type=ArticleEventType.EARNINGS,
        company_names=["Tata Motors"],
        financial_numbers=["Rs 775 crore"],
        percentages=["80%"],
        is_hard_business_event=True,
        is_investment_relevant=True,
    )

    res = ClassificationResult(
        success=True,
        classification=classification,
        attempts=0,
        raw_response="{}",
    )

    assert res.success is True
    assert res.attempts == 0
    assert res.classification.company_names == ["Tata Motors"]
