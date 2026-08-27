"""
Unit tests for Deterministic Final Validation Engine (20 Gatekeeping Checks).
"""

from datetime import date, datetime, timedelta, timezone
import pytest

from app.ai.models import BriefingEditorialPayload, EditorialStorySelection
from app.models.article import Article
from app.models.enums import NewsCategory, VerificationTier
from app.models.event import Event
from app.deduplication.fingerprint import generate_event_fingerprint
from app.deduplication.history import HistoryStore
from app.validation import (
    BriefingValidationReport,
    FinalValidationEngine,
    ValidationStatus,
)


@pytest.fixture
def valid_briefing_fixtures():
    """Fixture providing a 100% compliant 5 India + 5 International briefing setup."""
    articles_lookup: dict[str, Article] = {}
    events_lookup: dict[str, Event] = {}
    india_stories: list[EditorialStorySelection] = []
    intl_stories: list[EditorialStorySelection] = []
    candidate_urls: set[str] = set()

    # 5 India Stories
    for i in range(1, 6):
        art_id1 = f"art-in-a-{i}"
        art_id2 = f"art-in-b-{i}"
        url1 = f"https://www.business-standard.com/companies/news/india-corp-{i}-results-123.html"
        url2 = f"https://economictimes.indiatimes.com/news/india-corp-{i}-results-456.cms"
        comp = f"IndiaCorp{i}"

        art1 = Article(
            id=art_id1,
            title=f"{comp} Q1 Net Profit Jumps {10 + i}% YoY to ₹{i * 1000} Crore",
            url=url1,
            source_name="Business Standard",
            published_at=datetime.now(timezone.utc) - timedelta(hours=4),
            content_text=f"{comp} on Tuesday posted an {10 + i}% rise in net profit to ₹{i * 1000} crore with robust margins.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        art2 = Article(
            id=art_id2,
            title=f"{comp} Reports Q1 Profit of ₹{i * 1000} Cr, Up {10 + i}%",
            url=url2,
            source_name="The Economic Times",
            published_at=datetime.now(timezone.utc) - timedelta(hours=5),
            content_text=f"{comp} quarterly profit expanded to ₹{i * 1000} crore.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        articles_lookup[art_id1] = art1
        articles_lookup[art_id2] = art2
        candidate_urls.add(url1)
        candidate_urls.add(url2)

        evt_id = f"evt-in-{i}"
        event = Event(
            id=evt_id,
            canonical_title=art1.title,
            description=art1.content_text,
            companies_involved=[comp],
            financial_figures=[f"₹{i * 1000} crore", f"{10 + i}%"],
            event_category=NewsCategory.INDIA,
            article_ids=[art_id1, art_id2],
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
        )
        events_lookup[evt_id] = event

        india_stories.append(
            EditorialStorySelection(
                section="india",
                event_id=evt_id,
                headline=f"{comp} Q1 Net Profit Jumps {10 + i}% YoY to ₹{i * 1000} Cr",
                source="Business Standard",
                url=url1,
            )
        )

    # 5 International Stories
    for i in range(1, 6):
        art_id1 = f"art-intl-a-{i}"
        art_id2 = f"art-intl-b-{i}"
        url1 = f"https://www.reuters.com/business/global-corp-{i}-acquisition-deal.html"
        url2 = f"https://www.bloomberg.com/news/articles/global-corp-{i}-acquisition-deal"
        comp = f"GlobalCorp{i}"

        art1 = Article(
            id=art_id1,
            title=f"{comp} Inks ${i}.5 Billion Acquisition of Peer",
            url=url1,
            source_name="Reuters",
            published_at=datetime.now(timezone.utc) - timedelta(hours=6),
            content_text=f"{comp} has signed a definitive all-cash merger agreement valued at ${i}.5 billion.",
            category=NewsCategory.INTERNATIONAL,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        art2 = Article(
            id=art_id2,
            title=f"{comp} Agrees to Buy Competitor for ${i}.5 Billion",
            url=url2,
            source_name="Bloomberg",
            published_at=datetime.now(timezone.utc) - timedelta(hours=7),
            content_text=f"{comp} confirmed ${i}.5 billion takeover.",
            category=NewsCategory.INTERNATIONAL,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        articles_lookup[art_id1] = art1
        articles_lookup[art_id2] = art2
        candidate_urls.add(url1)
        candidate_urls.add(url2)

        evt_id = f"evt-intl-{i}"
        event = Event(
            id=evt_id,
            canonical_title=art1.title,
            description=art1.content_text,
            companies_involved=[comp],
            financial_figures=[f"${i}.5 billion"],
            event_category=NewsCategory.INTERNATIONAL,
            article_ids=[art_id1, art_id2],
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
        )
        events_lookup[evt_id] = event

        intl_stories.append(
            EditorialStorySelection(
                section="international",
                event_id=evt_id,
                headline=f"{comp} Agrees ${i}.5B Acquisition in All-Cash Deal",
                source="Reuters",
                url=url1,
            )
        )

    # 5 Domestic Stories
    domestic_stories: list[EditorialStorySelection] = []
    dom_titles = [
        "Supreme Court Constitution Bench rules on national tribunal appointments",
        "Cabinet approves Rs 10000 crore national semiconductor mission package",
        "Election Commission announces schedule for assembly elections",
        "ISRO successfully launches next generation navigation satellite",
        "Parliament passes landmark national digital data protection bill",
    ]
    for i in range(1, 6):
        art_id1 = f"art-dom-a-{i}"
        art_id2 = f"art-dom-b-{i}"
        url1 = f"https://www.thehindu.com/news/national/domestic-policy-{i}.html"
        url2 = f"https://indianexpress.com/article/india/domestic-policy-{i}.html"
        title = dom_titles[i - 1]

        art1 = Article(
            id=art_id1,
            title=title,
            url=url1,
            source_name="The Hindu",
            published_at=datetime.now(timezone.utc) - timedelta(hours=3),
            content_text=f"Government national policy {title}",
            category=NewsCategory.DOMESTIC,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        art2 = Article(
            id=art_id2,
            title=title,
            url=url2,
            source_name="The Indian Express",
            published_at=datetime.now(timezone.utc) - timedelta(hours=4),
            content_text=f"Government national policy {title}",
            category=NewsCategory.DOMESTIC,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        articles_lookup[art_id1] = art1
        articles_lookup[art_id2] = art2
        candidate_urls.add(url1)
        candidate_urls.add(url2)

        evt_id = f"evt-dom-{i}"
        event = Event(
            id=evt_id,
            canonical_title=title,
            description=art1.content_text,
            event_category=NewsCategory.DOMESTIC,
            article_ids=[art_id1, art_id2],
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
            primary_publisher="The Hindu",
            primary_url=url1,
            secondary_publisher="The Indian Express",
            secondary_url=url2,
        )
        events_lookup[evt_id] = event

        domestic_stories.append(
            EditorialStorySelection(
                section="domestic",
                event_id=evt_id,
                headline=title,
                source="The Hindu",
                url=url1,
            )
        )

    payload = BriefingEditorialPayload(
        domestic_stories=domestic_stories,
        india_stories=india_stories,
        international_stories=intl_stories,
    )

    return payload, events_lookup, articles_lookup, candidate_urls


class TestFinalValidationEngine:
    """Tests for the 20 deterministic gatekeeping checks."""

    def test_all_20_checks_pass_on_valid_payload(self, valid_briefing_fixtures):
        """Test full validation passes with status PASSED (20/20 checks)."""
        payload, events_map, articles_map, cand_urls = valid_briefing_fixtures

        engine = FinalValidationEngine()
        report: BriefingValidationReport = engine.validate_briefing(
            payload=payload,
            events_lookup=events_map,
            articles_lookup=articles_map,
            candidate_urls=cand_urls,
            target_date=date.today(),
            strict_5_per_section=True,
        )

        assert report.status == ValidationStatus.PASSED
        assert report.is_valid is True
        assert report.passed_checks == 20
        assert report.failed_checks == 0
        assert report.failure_reason is None

    def test_check_1_and_2_fails_on_incorrect_story_counts(self, valid_briefing_fixtures):
        """Test check 1 fails when India has 4 stories."""
        payload, events_map, articles_map, cand_urls = valid_briefing_fixtures
        # Remove 1 India story
        payload.india_stories = payload.india_stories[:4]

        engine = FinalValidationEngine()
        report = engine.validate_briefing(
            payload=payload,
            events_lookup=events_map,
            articles_lookup=articles_map,
            candidate_urls=cand_urls,
            strict_5_per_section=True,
        )

        assert report.status == ValidationStatus.FAILED
        assert report.failed_check_id == 2
        assert "Expected exactly 5 India stories" in (report.failure_reason or "")

    def test_check_3_and_4_fails_on_dead_url(self, valid_briefing_fixtures):
        """Test check 4 fails on dead/broken URL."""
        payload, events_map, articles_map, cand_urls = valid_briefing_fixtures
        payload.india_stories[0].url = "https://www.business-standard.com/dead-link-404.html"
        cand_urls.add("https://www.business-standard.com/dead-link-404.html")

        engine = FinalValidationEngine()
        report = engine.validate_briefing(
            payload=payload,
            events_lookup=events_map,
            articles_lookup=articles_map,
            candidate_urls=cand_urls,
        )

        assert report.status == ValidationStatus.FAILED
        assert report.failed_check_id == 4
        assert "URL is inaccessible or dead" in (report.failure_reason or "")

    def test_check_5_fails_on_hub_url(self, valid_briefing_fixtures):
        """Test check 5 fails on category/hub URL."""
        payload, events_map, articles_map, cand_urls = valid_briefing_fixtures
        bad_url = "https://www.business-standard.com/category/companies-news"
        payload.india_stories[0].url = bad_url
        cand_urls.add(bad_url)
        articles_map["art-in-a-1"].url = bad_url

        engine = FinalValidationEngine()
        report = engine.validate_briefing(
            payload=payload,
            events_lookup=events_map,
            articles_lookup=articles_map,
            candidate_urls=cand_urls,
        )

        assert report.status == ValidationStatus.FAILED
        assert report.failed_check_id == 5

    def test_check_6_fails_on_headline_event_mismatch(self, valid_briefing_fixtures):
        """Test check 6 fails when headline has zero overlap with article."""
        payload, events_map, articles_map, cand_urls = valid_briefing_fixtures
        payload.india_stories[0].headline = "Completely Unrelated Semiconductor Breakthrough in Tokyo"

        engine = FinalValidationEngine()
        report = engine.validate_briefing(
            payload=payload,
            events_lookup=events_map,
            articles_lookup=articles_map,
            candidate_urls=cand_urls,
        )

        assert report.status == ValidationStatus.FAILED
        assert report.failed_check_id == 6
        assert "zero semantic token overlap" in (report.failure_reason or "")

    def test_check_7_fails_on_stale_date(self, valid_briefing_fixtures):
        """Test check 7 fails when article is published 90 hours ago."""
        payload, events_map, articles_map, cand_urls = valid_briefing_fixtures
        articles_map["art-in-a-1"].published_at = datetime.now(timezone.utc) - timedelta(hours=90)

        engine = FinalValidationEngine()
        report = engine.validate_briefing(
            payload=payload,
            events_lookup=events_map,
            articles_lookup=articles_map,
            candidate_urls=cand_urls,
        )

        assert report.status == ValidationStatus.FAILED
        assert report.failed_check_id == 7

    def test_check_8_fails_on_single_source_event(self, valid_briefing_fixtures):
        """Test check 8 fails if an event has only 1 source and lacks verified tier."""
        payload, events_map, articles_map, cand_urls = valid_briefing_fixtures
        events_map["evt-in-1"].article_ids = ["art-in-a-1"]  # Only 1 source
        events_map["evt-in-1"].verification_tier = VerificationTier.UNVERIFIED

        engine = FinalValidationEngine()
        report = engine.validate_briefing(
            payload=payload,
            events_lookup=events_map,
            articles_lookup=articles_map,
            candidate_urls=cand_urls,
        )

        assert report.status == ValidationStatus.FAILED
        assert report.failed_check_id == 8
        assert "lacks TWO_SOURCE_VERIFIED tier" in (report.failure_reason or "") or "minimum 3 required" in (report.failure_reason or "")

    def test_check_9_fails_on_3_day_history_repeat(self, valid_briefing_fixtures):
        """Test check 9 fails when story already appeared in previous 3 days."""
        payload, events_map, articles_map, cand_urls = valid_briefing_fixtures
        history_store = HistoryStore(db_path=":memory:")

        # Save story in history from 2 days ago
        target_d = date.today()
        two_days_ago = target_d - timedelta(days=2)
        fkey, fhash = generate_event_fingerprint(
            company="IndiaCorp1",
            event_type="general",
            event_date=target_d,
            key_facts=["₹1000 crore", "11%"],
        )
        history_store.save_briefing(
            briefing_date=two_days_ago,
            stories=[
                {
                    "event_id": "evt-in-1",
                    "event_fingerprint": fhash,
                    "headline": "IndiaCorp1 Q1 Net Profit Jumps 11%",
                    "company_name": "IndiaCorp1",
                    "category": "india",
                    "published_date": two_days_ago,
                }
            ],
        )

        engine = FinalValidationEngine(history_store=history_store)
        report = engine.validate_briefing(
            payload=payload,
            events_lookup=events_map,
            articles_lookup=articles_map,
            candidate_urls=cand_urls,
            target_date=target_d,
        )

        assert report.status == ValidationStatus.FAILED
        assert report.failed_check_id == 9

    def test_check_10_fails_on_duplicate_india_company(self, valid_briefing_fixtures):
        """Test check 10 fails when same India company appears twice."""
        payload, events_map, articles_map, cand_urls = valid_briefing_fixtures
        # Make event 2 belong to IndiaCorp1 as well
        events_map["evt-in-2"].companies_involved = ["IndiaCorp1"]

        engine = FinalValidationEngine()
        report = engine.validate_briefing(
            payload=payload,
            events_lookup=events_map,
            articles_lookup=articles_map,
            candidate_urls=cand_urls,
        )

        assert report.status == ValidationStatus.FAILED
        assert report.failed_check_id == 10
        assert "Duplicate company 'IndiaCorp1'" in (report.failure_reason or "")

    def test_check_11_and_12_fails_on_fabricated_number(self, valid_briefing_fixtures):
        """Test check 11 and 12 fail on invented financial number in headline."""
        payload, events_map, articles_map, cand_urls = valid_briefing_fixtures
        # Introduce fabricated number ₹99,999 Cr into headline
        payload.india_stories[0].headline = "IndiaCorp1 Net Profit Reaches ₹99999 Crore Milestone"

        engine = FinalValidationEngine()
        report = engine.validate_briefing(
            payload=payload,
            events_lookup=events_map,
            articles_lookup=articles_map,
            candidate_urls=cand_urls,
        )

        assert report.status == ValidationStatus.FAILED
        assert report.failed_check_id in (11, 12)
        assert "fabricated" in (report.failure_reason or "").lower()

    def test_check_13_fails_on_fabricated_url(self, valid_briefing_fixtures):
        """Test check 13 fails on URL not in verified candidate manifest."""
        payload, events_map, articles_map, cand_urls = valid_briefing_fixtures
        payload.india_stories[0].url = "https://www.business-standard.com/hallucinated-story-url.html"

        engine = FinalValidationEngine()
        report = engine.validate_briefing(
            payload=payload,
            events_lookup=events_map,
            articles_lookup=articles_map,
            candidate_urls=cand_urls,
        )

        assert report.status == ValidationStatus.FAILED
        assert report.failed_check_id == 13
        assert "was not in the verified candidate manifest" in (report.failure_reason or "")

    def test_check_14_to_18_fails_on_prohibited_story_type(self, valid_briefing_fixtures):
        """Test check 14 fails on analyst rating story."""
        payload, events_map, articles_map, cand_urls = valid_briefing_fixtures
        articles_map["art-in-a-1"].title = "Jefferies Upgrades IndiaCorp1 to Buy with Target Price of Rs 1,500"
        articles_map["art-in-a-1"].content_text = "Jefferies issued a research note upgrading IndiaCorp1."

        engine = FinalValidationEngine()
        report = engine.validate_briefing(
            payload=payload,
            events_lookup=events_map,
            articles_lookup=articles_map,
            candidate_urls=cand_urls,
        )

        assert report.status == ValidationStatus.FAILED
        assert report.failed_check_id == 14

    def test_check_19_fails_on_geopolitical_story_without_numbers(self, valid_briefing_fixtures):
        """Test check 19 fails on unquantified geopolitical story."""
        payload, events_map, articles_map, cand_urls = valid_briefing_fixtures
        payload.international_stories[0].headline = "Geopolitical Tensions Escalate as Nations Implement Trade Tariffs"
        articles_map["art-intl-a-1"].title = "Geopolitical Tensions Escalate as Nations Implement Trade Tariffs"
        articles_map["art-intl-a-1"].content_text = "Geopolitical sanctions impact global shipping transit and trade tariffs across the continent."

        engine = FinalValidationEngine()
        report = engine.validate_briefing(
            payload=payload,
            events_lookup=events_map,
            articles_lookup=articles_map,
            candidate_urls=cand_urls,
        )

        assert report.status == ValidationStatus.FAILED
        assert report.failed_check_id == 19
        assert "lacks quantified market impact figures" in (report.failure_reason or "")
