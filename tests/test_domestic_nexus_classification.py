from app.classification.region_classifier import EventRegionClassifier
from app.models.enums import NewsCategory


def test_foreign_subject_does_not_survive_domestic_discovery_prior():
    classifier = EventRegionClassifier()

    category, reason = classifier.classify_with_reason(
        title='"Significant Reduction" In Chinese Deployment: Defence Ministry Annual Report',
        content='The report discusses Chinese troop deployment and regional military posture.',
        discovery_region=NewsCategory.DOMESTIC,
    )

    assert category == NewsCategory.INTERNATIONAL
    assert "foreign" in reason.lower() or "no indian domestic nexus" in reason.lower()


def test_valid_indian_public_affairs_story_remains_domestic():
    classifier = EventRegionClassifier()

    category, reason = classifier.classify_with_reason(
        title='Union Cabinet clears major national infrastructure programme',
        content='The Government of India approved the programme for implementation across India.',
        discovery_region=NewsCategory.DOMESTIC,
    )

    assert category == NewsCategory.DOMESTIC
    assert "india national public affairs" in reason.lower() or "indian nexus" in reason.lower()
