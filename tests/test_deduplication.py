"""
Unit tests for Event Deduplication, Fingerprinting, SQLite 3-Day History, and Same-Company Constraints.
"""

from datetime import date, datetime, timedelta, timezone
import pytest

from app.models.article import Article
from app.models.enums import NewsCategory
from app.deduplication import (
    DeduplicationEngine,
    EventClusterer,
    HistoryStore,
    are_articles_same_event,
    generate_event_fingerprint,
    normalize_entity_name,
)


@pytest.fixture
def memory_history_store() -> HistoryStore:
    """Fixture providing an in-memory SQLite history store for isolated unit testing."""
    return HistoryStore(db_path=":memory:")


class TestEventFingerprintAndSimilarity:
    """Tests for fingerprint calculation and multi-outlet story matching."""

    def test_fingerprint_generation_determinism(self):
        """Test fingerprint generation is deterministic and normalized."""
        key1, hash1 = generate_event_fingerprint(
            company="Tata Motors Ltd.",
            event_type="M&A",
            event_date=date(2026, 8, 18),
            key_facts=["1200cr", "24pct"],
        )
        key2, hash2 = generate_event_fingerprint(
            company="tata motors",
            event_type="M&A",
            event_date=date(2026, 8, 18),
            key_facts=["24pct", "1200cr"],
        )

        assert key1 == key2
        assert hash1 == hash2
        assert "tata_motors:m_and_a:2026-08-18:1200cr_24pct" in key1

    def test_reuters_and_business_standard_merge_into_one_event(self):
        """
        Test user specification example:
        Reuters: 'Company X profit rises 15%'
        Business Standard: 'Company X Q1 profit jumps 15%'
        Must evaluate as the same underlying event.
        """
        from datetime import timedelta
        art_reuters = Article(
            title="Company X profit rises 15%",
            url="https://www.reuters.com/company-x-results",
            source_name="Reuters",
            published_at=datetime.now(timezone.utc) - timedelta(hours=2),
            content_text="Company X reported a 15% rise in quarterly net profit.",
            category=NewsCategory.INTERNATIONAL,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )

        art_bs = Article(
            title="Company X Q1 profit jumps 15%",
            url="https://www.business-standard.com/company-x-q1",
            source_name="Business Standard",
            published_at=datetime.now(timezone.utc) - timedelta(hours=2),
            content_text="Company X announced its Q1 net profit jumped 15% YoY.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )

        assert are_articles_same_event(art_reuters, art_bs) is True

    def test_unrelated_articles_do_not_merge(self):
        """Test distinct corporate events are not merged."""
        art1 = Article(
            title="HDFC Bank Q1 profit rises 18%",
            url="https://www.livemint.com/hdfc",
            source_name="Livemint",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        art2 = Article(
            title="Tata Motors demerger approved by board",
            url="https://economictimes.indiatimes.com/tata",
            source_name="The Economic Times",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )

        assert are_articles_same_event(art1, art2) is False


class TestEventClusterer:
    """Tests for EventClusterer grouping."""

    def test_clustering_multi_source_articles(self):
        """Test clustering groups multi-source reports into unified events."""
        art1 = Article(
            title="Company X profit rises 15%",
            url="https://www.reuters.com/comp-x-1",
            source_name="Reuters",
            content_text="Company X net profit rose 15% to $500M.",
            category=NewsCategory.INTERNATIONAL,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        art2 = Article(
            title="Company X Q1 profit jumps 15%",
            url="https://www.bloomberg.com/comp-x-2",
            source_name="Bloomberg",
            content_text="Company X quarterly net profit surged 15% to $500M.",
            category=NewsCategory.INTERNATIONAL,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        art3 = Article(
            title="Nvidia announces $50B buyback",
            url="https://www.cnbc.com/nvidia-buyback",
            source_name="CNBC",
            content_text="Nvidia approved a new $50 billion share repurchase plan.",
            category=NewsCategory.INTERNATIONAL,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )

        clusterer = EventClusterer()
        events = clusterer.cluster_articles_into_events([art1, art2, art3])

        assert len(events) == 2
        # One cluster with 2 linked article IDs (Company X)
        comp_x_event = next(e for e in events if "Company X" in e.canonical_title)
        assert len(comp_x_event.article_ids) == 2
        assert art1.id in comp_x_event.article_ids
        assert art2.id in comp_x_event.article_ids


class TestDeduplicationEngine:
    """Tests for 3-day history lookback and company constraints."""

    def test_previous_3_day_history_rejection(self, memory_history_store: HistoryStore):
        """Test that stories appearing in the previous 3 days are rejected as duplicate."""
        today = date(2026, 8, 18)
        two_days_ago = today - timedelta(days=2)

        # Pre-seed SQLite database with a story from 2 days ago
        historical_stories = [
            {
                "event_id": "evt-hdfc-123",
                "event_fingerprint": "hdfc_bank:earnings:2026-08-18:16175cr_18pct",
                "headline": "HDFC Bank Q1 Net Profit Surges 18% to ₹16,175 Cr",
                "company_name": "HDFC Bank",
                "category": "india",
                "published_date": two_days_ago,
            }
        ]
        memory_history_store.save_briefing(briefing_date=two_days_ago, stories=historical_stories)

        engine = DeduplicationEngine(history_store=memory_history_store)

        # Candidate stories for today's briefing
        today_candidates = [
            # Story 1: Same event as 2 days ago
            {
                "headline": "HDFC Bank Q1 Net Profit Jumps 18%",
                "company_name": "HDFC Bank",
                "event_type": "EARNINGS",
                "category": "india",
                "key_facts": ["16175cr", "18pct"],
            },
            # Story 2: Fresh new event
            {
                "headline": "L&T Bags ₹4,200 Crore EPC Contract in Middle East",
                "company_name": "Larsen & Toubro",
                "event_type": "SECTOR",
                "category": "india",
                "key_facts": ["4200cr"],
            },
        ]

        accepted, rejected = engine.filter_stories(
            candidate_stories=today_candidates,
            target_date=today,
            lookback_days=3,
        )

        assert len(accepted) == 1
        assert accepted[0]["company_name"] == "Larsen & Toubro"

        assert len(rejected) == 1
        assert rejected[0]["company_name"] == "HDFC Bank"
        assert rejected[0]["rejection_rule"] == "3_DAY_HISTORY"

    def test_story_older_than_3_days_accepted(self, memory_history_store: HistoryStore):
        """Test that events older than 3 days (e.g. 5 days ago) are not rejected by 3-day window."""
        today = date(2026, 8, 18)
        five_days_ago = today - timedelta(days=5)

        historical_stories = [
            {
                "event_id": "evt-ril-999",
                "event_fingerprint": "reliance:fundraising:2026-08-18:8500cr",
                "headline": "Reliance Retail Raises ₹8,500 Crore in QIP",
                "company_name": "Reliance",
                "category": "india",
                "published_date": five_days_ago,
            }
        ]
        memory_history_store.save_briefing(briefing_date=five_days_ago, stories=historical_stories)

        engine = DeduplicationEngine(history_store=memory_history_store)
        candidate = [
            {
                "headline": "Reliance Retail Launches New QIP Offering",
                "company_name": "Reliance",
                "event_type": "FUNDRAISING",
                "category": "india",
                "key_facts": ["8500cr"],
            }
        ]

        accepted, rejected = engine.filter_stories(
            candidate_stories=candidate,
            target_date=today,
            lookback_days=3,
        )
        assert len(accepted) == 1
        assert len(rejected) == 0

    def test_india_same_company_restriction(self, memory_history_store: HistoryStore):
        """
        Test India section rule: The same company cannot appear in two final stories.
        """
        engine = DeduplicationEngine(history_store=memory_history_store)

        candidates = [
            # Story 1: Tata Motors Demerger (India)
            {
                "headline": "Tata Motors Demerger Approved by Board",
                "company_name": "Tata Motors",
                "event_type": "M&A",
                "category": "india",
                "key_facts": [],
            },
            # Story 2: Tata Motors EV Investment (India - Same Company)
            {
                "headline": "Tata Motors Plans ₹15,000 Crore EV Plant in Tamil Nadu",
                "company_name": "Tata Motors",
                "event_type": "POLICY",
                "category": "india",
                "key_facts": ["15000cr"],
            },
            # Story 3: Infosys CFO Appointment (India - Distinct Company)
            {
                "headline": "Infosys Board Appoints New CFO",
                "company_name": "Infosys",
                "event_type": "LEADERSHIP",
                "category": "india",
                "key_facts": [],
            },
        ]

        accepted, rejected = engine.filter_stories(
            candidate_stories=candidates,
            target_date=date(2026, 8, 18),
        )

        assert len(accepted) == 2
        assert [s["company_name"] for s in accepted] == ["Tata Motors", "Infosys"]

        assert len(rejected) == 1
        assert rejected[0]["rejection_rule"] == "INDIA_SAME_COMPANY"
        assert "Same company ('Tata Motors') already selected" in rejected[0]["rejection_reason"]

    def test_international_multiple_events_for_same_company_allowed(self, memory_history_store: HistoryStore):
        """
        Test International section rule: Allow multiple companies/stories if underlying events differ,
        but prevent duplicate underlying events.
        """
        engine = DeduplicationEngine(history_store=memory_history_store)

        candidates = [
            # Story 1: Nvidia Q2 Earnings (International)
            {
                "headline": "Nvidia Reports Record $30B Revenue",
                "company_name": "Nvidia",
                "event_type": "EARNINGS",
                "category": "international",
                "key_facts": ["30b", "122pct"],
            },
            # Story 2: Nvidia Share Buyback (International - Different Event for same company)
            {
                "headline": "Nvidia Board Authorizes $50B Share Buyback",
                "company_name": "Nvidia",
                "event_type": "FUNDRAISING",
                "category": "international",
                "key_facts": ["50b"],
            },
            # Story 3: Nvidia Duplicate Q2 Earnings (International - Duplicate Event)
            {
                "headline": "Nvidia Q2 Revenue Jumps 122% to $30 Billion",
                "company_name": "Nvidia",
                "event_type": "EARNINGS",
                "category": "international",
                "key_facts": ["30b", "122pct"],
            },
        ]

        accepted, rejected = engine.filter_stories(
            candidate_stories=candidates,
            target_date=date(2026, 8, 18),
        )

        assert len(accepted) == 2
        assert len(rejected) == 1
        assert rejected[0]["rejection_rule"] == "INTL_DUPLICATE_EVENT"


class TestUnspecifiedCompanyDeduplicationRegression:
    """Regression tests ensuring unspecified company placeholders do not cause false deduplication."""

    def test_two_events_with_company_unspecified_are_not_duplicates(self, memory_history_store: HistoryStore):
        """Test 1: Two India events with company='unspecified' are NOT rejected as duplicate companies."""
        engine = DeduplicationEngine(history_store=memory_history_store)
        candidates = [
            {
                "headline": "Shiprocket Expands Logistics Network with ₹500 Crore Investment",
                "company_name": "unspecified",
                "event_type": "FUNDRAISING",
                "category": "india",
                "key_facts": ["500cr"],
            },
            {
                "headline": "L&T Buys Over 2 Crore Units of Nxt-Infra Trust for ₹250 Crore",
                "company_name": "unspecified",
                "event_type": "M&A",
                "category": "india",
                "key_facts": ["250cr"],
            },
        ]

        accepted, rejected = engine.filter_stories(
            candidate_stories=candidates,
            target_date=date(2026, 8, 18),
        )

        assert len(accepted) == 2
        assert len(rejected) == 0

    def test_unknown_is_not_a_duplicate_company(self, memory_history_store: HistoryStore):
        """Test 2: Placeholder strings 'unknown', 'n/a', 'na', '', None are NOT rejected as duplicate companies."""
        engine = DeduplicationEngine(history_store=memory_history_store)
        placeholders = ["unknown", "n/a", "na", "", None]
        candidates = [
            {
                "headline": f"India Business Event {i}",
                "company_name": placeholder,
                "event_type": f"EVENT_TYPE_{i}",
                "category": "india",
                "key_facts": [f"fact_{i}"],
            }
            for i, placeholder in enumerate(placeholders, 1)
        ]

        accepted, rejected = engine.filter_stories(
            candidate_stories=candidates,
            target_date=date(2026, 8, 18),
        )

        assert len(accepted) == 5
        assert len(rejected) == 0

    def test_two_real_reliance_events_are_rejected(self, memory_history_store: HistoryStore):
        """Test 3: Two real Reliance events are still rejected under INDIA_SAME_COMPANY restriction."""
        engine = DeduplicationEngine(history_store=memory_history_store)
        candidates = [
            {
                "headline": "Reliance Retail Launches ₹8,500 Crore QIP",
                "company_name": "Reliance",
                "event_type": "FUNDRAISING",
                "category": "india",
                "key_facts": ["8500cr"],
            },
            {
                "headline": "Reliance Jio Announces 5G Expansion Plan",
                "company_name": "Reliance",
                "event_type": "POLICY",
                "category": "india",
                "key_facts": ["5g"],
            },
        ]

        accepted, rejected = engine.filter_stories(
            candidate_stories=candidates,
            target_date=date(2026, 8, 18),
        )

        assert len(accepted) == 1
        assert len(rejected) == 1
        assert rejected[0]["rejection_rule"] == "INDIA_SAME_COMPANY"

    def test_fingerprint_deduplication_remains_unchanged(self, memory_history_store: HistoryStore):
        """Test 4: Event fingerprint and 3-day history deduplication remain fully functional."""
        today = date(2026, 8, 18)
        yesterday = today - timedelta(days=1)

        historical = [
            {
                "event_id": "evt-historical-1",
                "event_fingerprint": "unspecified_entity:m_and_a:2026-08-18:250cr",
                "headline": "Infrastructure Deal Signed for ₹250 Crore",
                "company_name": "unspecified",
                "category": "india",
                "published_date": yesterday,
            }
        ]
        memory_history_store.save_briefing(briefing_date=yesterday, stories=historical)

        engine = DeduplicationEngine(history_store=memory_history_store)
        candidates = [
            {
                "headline": "Infrastructure Deal Signed for ₹250 Crore",
                "company_name": "unspecified",
                "event_type": "M&A",
                "category": "india",
                "key_facts": ["250cr"],
            }
        ]

        accepted, rejected = engine.filter_stories(
            candidate_stories=candidates,
            target_date=today,
            lookback_days=3,
        )

        assert len(accepted) == 0
        assert len(rejected) == 1
        assert rejected[0]["rejection_rule"] == "3_DAY_HISTORY"
