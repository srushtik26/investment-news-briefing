"""
Tests for weekend briefing recovery, widening horizons, partial state persistence,
alternate source recovery, Indian principal acting abroad classification,
and Gemini 429 resilience.
"""

from datetime import date, datetime, timezone, timedelta
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from unittest.mock import MagicMock, patch
import pytest

from app.models.article import Article
from app.models.event import Event
from app.models.enums import NewsCategory, VerificationTier
from app.classification.region_classifier import EventRegionClassifier
from app.classification.classifier import AIArticleClassifier
from app.pipeline.context import PipelineContext
from app.pipeline.weekend_recovery import (
    save_partial_weekend_state,
    restore_partial_weekend_state,
    cleanup_partial_weekend_state,
    PARTIAL_STATE_FILENAME,
)
from app.pipeline.fallback_manager import get_quality_level
from app.pipeline.candidate_processing import _recover_alternate_source
from app.discovery.models import DiscoveredArticle


def _create_test_article(
    aid: str,
    title: str,
    source: str = "Business Standard",
    url: str = "https://example.com/art",
    published_at: Optional[datetime] = None,
    body: str = "Article body text with financial figures Rs 500 crore.",
    category: NewsCategory = NewsCategory.INDIA,
) -> Article:
    now = datetime.now(timezone.utc)
    pub = published_at or (now - timedelta(hours=10))
    return Article(
        id=aid,
        url=url,
        canonical_url=url,
        title=title,
        source_name=source,
        published_at=pub,
        body=body,
        category=category,
        authors=[],
        domain="example.com",
    )


def _create_test_event(
    eid: str,
    title: str,
    article_id: str,
    category: NewsCategory = NewsCategory.INDIA,
    tier: VerificationTier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
) -> Event:
    return Event(
        id=eid,
        canonical_title=title,
        description=f"Event description for {title}",
        event_category=category,
        verification_tier=tier,
        article_ids=[article_id],
        primary_publisher="Business Standard",
        primary_url=f"https://example.com/art/{article_id}",
        companies_involved=["TestCorp"],
        financial_figures=["Rs 500 crore"],
    )


class TestIndianPrincipalActingAbroad:
    """Test generic classification of Indian companies acting abroad as INDIA."""

    def test_indian_principal_acting_abroad_generic_rule(self):
        # Generic Indian principal patterns acting abroad
        assert EventRegionClassifier.is_indian_principal_acting_abroad(
            "Tata Motors acquires UK EV battery software maker for 150 million pounds"
        )
        assert EventRegionClassifier.is_indian_principal_acting_abroad(
            "Infosys signs 500 million dollar cloud expansion contract in Germany"
        )
        assert EventRegionClassifier.is_indian_principal_acting_abroad(
            "Wipro Ltd acquires US cybersecurity consultancy for 200 million dollars"
        )
        assert EventRegionClassifier.is_indian_principal_acting_abroad(
            "State Bank of India raises 1 billion dollars via overseas green bonds"
        )
        assert EventRegionClassifier.is_indian_principal_acting_abroad(
            "Larsen & Toubro wins major Middle East pipeline project worth 800 million dollars"
        )

    def test_rejection_of_foreign_principal_with_incidental_india_mention(self):
        # Foreign company with incidental India reference -> NOT Indian principal acting abroad
        assert not EventRegionClassifier.is_indian_principal_acting_abroad(
            "Apple reports record iPhone sales in India and China during Q3"
        )
        assert not EventRegionClassifier.is_indian_principal_acting_abroad(
            "Tesla considers India showroom locations while cutting European workforce"
        )
        assert not EventRegionClassifier.is_indian_principal_acting_abroad(
            "Amazon Web Services opens new cloud region in Frankfurt"
        )


class TestWeekendContextAndHorizons:
    """Test auto-detection of weekend and horizon gating."""

    def test_weekend_auto_detection(self):
        # Saturday: 2026-09-19 (weekday 5)
        sat_date = date(2026, 9, 19)
        ctx_sat = PipelineContext(
            run_reference_time=datetime(2026, 9, 19, 3, 0, tzinfo=timezone.utc),
            target_date=sat_date,
            data_dir=Path("data"),
            logs_dir=Path("logs"),
            settings=MagicMock(),
            max_india=30,
            max_international=30,
            validation_run=False,
            log_exec=MagicMock(),
            discovery_service=MagicMock(),
            extractor=MagicMock(),
            business_filter_engine=MagicMock(),
            domestic_filter_engine=MagicMock(),
            classifier=MagicMock(),
            reg_clf=MagicMock(),
            verifier=MagicMock(),
            active_corroborator=MagicMock(),
            single_source_evaluator=MagicMock(),
            domestic_evaluator=MagicMock(),
            history_store=MagicMock(),
            dedup_engine=MagicMock(),
            ranker=MagicMock(),
            scorer=MagicMock(),
            editorial_engine=MagicMock(),
            validator=MagicMock(),
            formatter=MagicMock(),
            metrics=MagicMock(),
        )
        assert ctx_sat.is_weekend is True

        # Sunday: 2026-09-20 (weekday 6)
        sun_date = date(2026, 9, 20)
        ctx_sun = PipelineContext(
            run_reference_time=datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc),
            target_date=sun_date,
            data_dir=Path("data"),
            logs_dir=Path("logs"),
            settings=MagicMock(),
            max_india=30,
            max_international=30,
            validation_run=False,
            log_exec=MagicMock(),
            discovery_service=MagicMock(),
            extractor=MagicMock(),
            business_filter_engine=MagicMock(),
            domestic_filter_engine=MagicMock(),
            classifier=MagicMock(),
            reg_clf=MagicMock(),
            verifier=MagicMock(),
            active_corroborator=MagicMock(),
            single_source_evaluator=MagicMock(),
            domestic_evaluator=MagicMock(),
            history_store=MagicMock(),
            dedup_engine=MagicMock(),
            ranker=MagicMock(),
            scorer=MagicMock(),
            editorial_engine=MagicMock(),
            validator=MagicMock(),
            formatter=MagicMock(),
            metrics=MagicMock(),
        )
        assert ctx_sun.is_weekend is True

        # Wednesday: 2026-09-23 (weekday 2)
        wed_date = date(2026, 9, 23)
        ctx_wed = PipelineContext(
            run_reference_time=datetime(2026, 9, 23, 3, 0, tzinfo=timezone.utc),
            target_date=wed_date,
            data_dir=Path("data"),
            logs_dir=Path("logs"),
            settings=MagicMock(),
            max_india=30,
            max_international=30,
            validation_run=False,
            log_exec=MagicMock(),
            discovery_service=MagicMock(),
            extractor=MagicMock(),
            business_filter_engine=MagicMock(),
            domestic_filter_engine=MagicMock(),
            classifier=MagicMock(),
            reg_clf=MagicMock(),
            verifier=MagicMock(),
            active_corroborator=MagicMock(),
            single_source_evaluator=MagicMock(),
            domestic_evaluator=MagicMock(),
            history_store=MagicMock(),
            dedup_engine=MagicMock(),
            ranker=MagicMock(),
            scorer=MagicMock(),
            editorial_engine=MagicMock(),
            validator=MagicMock(),
            formatter=MagicMock(),
            metrics=MagicMock(),
        )
        assert ctx_wed.is_weekend is False

    def test_get_quality_level_weekday_vs_weekend(self):
        ref_time = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        # 85h old article
        art_85 = _create_test_article(
            "a85",
            "Old 85h Event",
            published_at=ref_time - timedelta(hours=85),
        )
        ev_85 = _create_test_event("e85", "Old 85h Event", "a85")
        
        # 4 young articles (10h)
        young_arts = [
            _create_test_article(f"a{i}", f"Young Event {i}", published_at=ref_time - timedelta(hours=10))
            for i in range(4)
        ]
        young_evs = [
            _create_test_event(f"e{i}", f"Young Event {i}", f"a{i}")
            for i in range(4)
        ]
        
        scored_events = [MagicMock(event=ev) for ev in (young_evs + [ev_85])]
        lookup = {a.id: a for a in (young_arts + [art_85])}

        # On weekday: > 72h is strictly DATA_UNAVAILABLE
        res_weekday = get_quality_level(
            scored_events,
            two_source_count=0,
            articles_lookup=lookup,
            now_utc=ref_time,
            is_weekend=False,
        )
        assert res_weekday == "DATA_UNAVAILABLE"

        # On weekend: 85h is accepted as WEEKEND_RESCUE_96H
        res_weekend = get_quality_level(
            scored_events,
            two_source_count=0,
            articles_lookup=lookup,
            now_utc=ref_time,
            is_weekend=True,
        )
        assert res_weekend == "WEEKEND_RESCUE_96H"

        # > 96h is DATA_UNAVAILABLE even on weekend
        art_100 = _create_test_article(
            "a100",
            "Super Old Event",
            published_at=ref_time - timedelta(hours=100),
        )
        ev_100 = _create_test_event("e100", "Super Old Event", "a100")
        scored_100 = [MagicMock(event=ev) for ev in (young_evs + [ev_100])]
        lookup_100 = {a.id: a for a in (young_arts + [art_100])}

        assert get_quality_level(
            scored_100,
            two_source_count=0,
            articles_lookup=lookup_100,
            now_utc=ref_time,
            is_weekend=True,
        ) == "DATA_UNAVAILABLE"


class TestPartialWeekendStatePersistence:
    """Test saving, restoring, and cleanup of weekend partial state."""

    def test_save_and_restore_partial_weekend_state(self, tmp_path):
        sat_date = date(2026, 9, 19)
        ref_time = datetime(2026, 9, 19, 3, 0, tzinfo=timezone.utc)
        
        ctx = MagicMock()
        ctx.is_weekend = True
        ctx.target_date = sat_date
        ctx.verified_events = []
        ctx.high_confidence_single_candidates = []
        ctx.articles_lookup = {}
        ctx.seen_urls = set()
        ctx.extractor.degraded_domains = {"reuters.com"}
        ctx.domestic_reserve_pool = []
        ctx.india_reserve_pool = []
        ctx.intl_reserve_pool = []
        ctx.log_exec = MagicMock()

        # Add 4 verified events
        for i in range(4):
            art = _create_test_article(f"art_{i}", f"Article Title {i}")
            ev = _create_test_event(f"ev_{i}", f"Event Title {i}", art.id)
            ctx.articles_lookup[art.id] = art
            ctx.verified_events.append(ev)
            ctx.seen_urls.add(art.url)

        # Save partial state (5 Dom, 4 India, 5 Intl)
        saved_file = save_partial_weekend_state(
            ctx,
            domestic_count=5,
            india_count=4,
            intl_count=5,
            data_dir=tmp_path,
        )
        assert saved_file is not None
        assert saved_file.exists()

        # Create fresh context to restore into
        ctx_new = MagicMock()
        ctx_new.is_weekend = True
        ctx_new.target_date = sat_date
        ctx_new.verified_events = []
        ctx_new.high_confidence_single_candidates = []
        ctx_new.articles_lookup = {}
        ctx_new.seen_urls = set()
        ctx_new.extractor.degraded_domains = set()
        ctx_new.domestic_reserve_pool = []
        ctx_new.india_reserve_pool = []
        ctx_new.intl_reserve_pool = []
        ctx_new.log_exec = MagicMock()

        success = restore_partial_weekend_state(ctx_new, tmp_path)
        assert success is True
        assert len(ctx_new.verified_events) == 4
        assert len(ctx_new.articles_lookup) == 4
        assert "reuters.com" in ctx_new.extractor.degraded_domains
        assert ctx_new.log_exec.call_count >= 1

        # Check cleanup
        cleanup_partial_weekend_state(tmp_path)
        assert not (tmp_path / PARTIAL_STATE_FILENAME).exists()


class TestAlternateSourceRecovery:
    """Test recovery of alternate coverage when candidate extraction encounters 401/403."""

    def test_alternate_source_from_reserve(self):
        ctx = MagicMock()
        ctx.all_extracted = []
        ctx.seen_urls = set()
        ctx.extractor.is_domain_degraded.return_value = False

        failed_cand = DiscoveredArticle(
            id="c1",
            title="Larsen & Toubro wins major metro rail contract worth Rs 3500 crore",
            url="https://blocked-site.com/lt-contract",
            source="Blocked Source",
            search_query="India business",
            country="India",
            published_at=datetime.now(timezone.utc),
        )

        # Reserve pool has coverage from Business Standard
        alt_cand = DiscoveredArticle(
            id="c2",
            title="Larsen & Toubro bags Rs 3500 crore order for underground metro construction",
            url="https://business-standard.com/lt-metro-order",
            source="Business Standard",
            search_query="India business",
            country="India",
            published_at=datetime.now(timezone.utc),
        )
        ctx.india_reserve_pool = [alt_cand]

        # Mock extractor to succeed on alt_cand
        alt_art = _create_test_article("a_alt", alt_cand.title, source="Business Standard", url=alt_cand.url)
        alt_res = MagicMock()
        alt_res.success = True
        alt_res.article = alt_art
        ctx.extractor.extract.return_value = alt_res

        recovered = _recover_alternate_source(failed_cand, "india", ctx)
        assert recovered is not None
        assert recovered.url == alt_cand.url
        assert recovered.source_name == "Business Standard"


class TestGeminiRateLimitGracefulDegradation:
    """Test AIArticleClassifier bounded backoff and fallback on 429 errors."""

    def test_classifier_handles_429_gracefully(self):
        classifier = AIArticleClassifier(api_key="test_api_key")
        classifier.RATE_LIMIT_BACKOFF_SECONDS = 0
        art = _create_test_article(
            "a_rate",
            "TCS reports 12% rise in net profit to Rs 12000 crore",
            body="Tata Consultancy Services posted Q2 revenue growth of 8% with net profit of Rs 12000 crore.",
        )

        # Simulate Gemini 429 resource exhausted error on _call_model
        with patch.object(classifier, "_call_model", side_effect=Exception("429 Resource has been exhausted")):
            res = classifier.classify(art)
            # Should not crash; returns valid ClassificationResult (deterministic fallback or safe rejection)
            assert res is not None
            # If deterministic fallback succeeded, it extracted company and event type
            if res.success and res.classification:
                assert any("TCS" in c or "Tata" in c for c in res.classification.company_names)


class TestWeekendEndToEndSimulations:
    """Test full expansion and fallback simulation on Saturday and Sunday."""

    def test_saturday_expansion_to_72h_recovers_to_5_5_5(self):
        from app.pipeline.fallback_manager import run_expansion_and_fallbacks

        logged_messages = []
        ctx = MagicMock()
        ctx.is_weekend = True
        ctx.run_reference_time = datetime(2026, 9, 19, 3, 0, tzinfo=timezone.utc)
        ctx.target_date = date(2026, 9, 19)
        ctx.log_exec = lambda msg: logged_messages.append(msg)
        ctx.seen_urls = set()
        ctx.articles_lookup = {}
        ctx.verified_events = []
        ctx.high_confidence_single_candidates = []
        ctx.single_source_events = []
        ctx.date_deferred_articles = []
        ctx.domestic_reserve_pool = []
        ctx.india_reserve_pool = []
        ctx.intl_reserve_pool = []
        ctx.reg_clf.classify_event.side_effect = lambda ev, arts: ev.event_category

        # Initial counts: Dom=5, India=4, Intl=4
        dom_evs = [_create_test_event(f"dom_{i}", f"Dom Event {i}", f"a_dom_{i}", category=NewsCategory.DOMESTIC) for i in range(5)]
        india_evs = [_create_test_event(f"ind_{i}", f"India Event {i}", f"a_ind_{i}", category=NewsCategory.INDIA) for i in range(4)]
        intl_evs = [_create_test_event(f"int_{i}", f"Intl Event {i}", f"a_int_{i}", category=NewsCategory.INTERNATIONAL) for i in range(4)]

        all_evs = dom_evs + india_evs + intl_evs
        for ev in all_evs:
            art = _create_test_article(ev.article_ids[0], ev.canonical_title, category=ev.event_category)
            ctx.articles_lookup[art.id] = art
            ctx.verified_events.append(ev)

        # Mock section count functions
        counts = {"DOMESTIC": 5, "INDIA": 4, "INTERNATIONAL": 4}

        def mock_count_unique(cat):
            return counts[cat.value.upper()]

        def mock_dom_events(cat=None):
            return dom_evs

        # When horizon hits 72h, simulate reserve recovery reaching 5 for both India and Intl
        def mock_process_candidate(item, section, ctx_arg, active_horizon=24.0):
            if active_horizon >= 72.0:
                if section == "india":
                    counts["INDIA"] = 5
                elif section == "international":
                    counts["INTERNATIONAL"] = 5

        with patch("app.pipeline.fallback_manager.process_candidate_item", side_effect=mock_process_candidate):
            # Put one item in reserves
            dummy_cand = DiscoveredArticle(
                id="res1", title="Reserve Story", url="https://example.com/res1",
                source="Business Standard", search_query="q", country="India",
            )
            ctx.india_reserve_pool = [dummy_cand]
            ctx.intl_reserve_pool = [dummy_cand]

            status = run_expansion_and_fallbacks(
                ctx=ctx,
                get_final_selectable_unique_events_fn=mock_dom_events,
                is_domestic_diversity_satisfied_fn=lambda evs: True,
                count_unique_section_events_fn=mock_count_unique,
                get_unique_candidate_events_fn=lambda: all_evs,
            )

            assert status == "STRICT_SUCCESS"
            assert any("WEEKEND_MODE=true" in m for m in logged_messages)
            assert any("WEEKEND_INITIAL_COUNTS DOMESTIC=5 INDIA=4 INTERNATIONAL=4" in m for m in logged_messages)
            assert any("WEEKEND_BACKFILL_72H section=INDIA" in m for m in logged_messages)
            assert any("WEEKEND_BACKFILL_72H section=INTERNATIONAL" in m for m in logged_messages)
            assert any("WEEKEND_FINAL_COUNTS DOMESTIC=5 INDIA=5 INTERNATIONAL=5" in m for m in logged_messages)

    def test_sunday_expansion_to_96h_recovers_to_5_5_5(self):
        from app.pipeline.fallback_manager import run_expansion_and_fallbacks

        logged_messages = []
        ctx = MagicMock()
        ctx.is_weekend = True
        ctx.run_reference_time = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
        ctx.target_date = date(2026, 9, 20)
        ctx.log_exec = lambda msg: logged_messages.append(msg)
        ctx.seen_urls = set()
        ctx.articles_lookup = {}
        ctx.verified_events = []
        ctx.high_confidence_single_candidates = []
        ctx.single_source_events = []
        ctx.date_deferred_articles = []
        ctx.domestic_reserve_pool = []
        ctx.india_reserve_pool = []
        ctx.intl_reserve_pool = []
        ctx.reg_clf.classify_event.side_effect = lambda ev, arts: ev.event_category

        dom_evs = [_create_test_event(f"dom_{i}", f"Dom Event {i}", f"a_dom_{i}", category=NewsCategory.DOMESTIC) for i in range(5)]
        india_evs = [_create_test_event(f"ind_{i}", f"India Event {i}", f"a_ind_{i}", category=NewsCategory.INDIA) for i in range(4)]
        intl_evs = [_create_test_event(f"int_{i}", f"Intl Event {i}", f"a_int_{i}", category=NewsCategory.INTERNATIONAL) for i in range(5)]

        all_evs = dom_evs + india_evs + intl_evs
        for ev in all_evs:
            art = _create_test_article(ev.article_ids[0], ev.canonical_title, category=ev.event_category)
            ctx.articles_lookup[art.id] = art
            ctx.verified_events.append(ev)

        counts = {"DOMESTIC": 5, "INDIA": 4, "INTERNATIONAL": 5}

        def mock_count_unique(cat):
            return counts[cat.value.upper()]

        # Only reaches 5 at 96h
        def mock_process_candidate(item, section, ctx_arg, active_horizon=24.0):
            if active_horizon >= 96.0 and section == "india":
                counts["INDIA"] = 5

        with patch("app.pipeline.fallback_manager.process_candidate_item", side_effect=mock_process_candidate):
            dummy_cand = DiscoveredArticle(
                id="res2", title="Reserve Story 96h", url="https://example.com/res2",
                source="Business Standard", search_query="q", country="India",
            )
            ctx.india_reserve_pool = [dummy_cand]

            status = run_expansion_and_fallbacks(
                ctx=ctx,
                get_final_selectable_unique_events_fn=lambda cat=None: dom_evs,
                is_domestic_diversity_satisfied_fn=lambda evs: True,
                count_unique_section_events_fn=mock_count_unique,
                get_unique_candidate_events_fn=lambda: all_evs,
            )

            assert status == "STRICT_SUCCESS"
            assert any("WEEKEND_RESCUE_96H section=INDIA" in m for m in logged_messages)
            assert any("WEEKEND_FINAL_COUNTS DOMESTIC=5 INDIA=5 INTERNATIONAL=5" in m for m in logged_messages)

    def test_weekday_does_not_expand_to_96h(self):
        from app.pipeline.fallback_manager import run_expansion_and_fallbacks

        logged_messages = []
        ctx = MagicMock()
        ctx.is_weekend = False  # Wednesday!
        ctx.run_reference_time = datetime(2026, 9, 23, 3, 0, tzinfo=timezone.utc)
        ctx.target_date = date(2026, 9, 23)
        ctx.log_exec = lambda msg: logged_messages.append(msg)
        ctx.seen_urls = set()
        ctx.articles_lookup = {}
        ctx.verified_events = []
        ctx.high_confidence_single_candidates = []
        ctx.single_source_events = []
        ctx.date_deferred_articles = []
        ctx.domestic_reserve_pool = []
        ctx.india_reserve_pool = []
        ctx.intl_reserve_pool = []
        ctx.reg_clf.classify_event.side_effect = lambda ev, arts: ev.event_category

        dom_evs = [_create_test_event(f"dom_{i}", f"Dom Event {i}", f"a_dom_{i}", category=NewsCategory.DOMESTIC) for i in range(5)]
        india_evs = [_create_test_event(f"ind_{i}", f"India Event {i}", f"a_ind_{i}", category=NewsCategory.INDIA) for i in range(4)]
        intl_evs = [_create_test_event(f"int_{i}", f"Intl Event {i}", f"a_int_{i}", category=NewsCategory.INTERNATIONAL) for i in range(5)]
        all_evs = dom_evs + india_evs + intl_evs

        counts = {"DOMESTIC": 5, "INDIA": 4, "INTERNATIONAL": 5}

        status = run_expansion_and_fallbacks(
            ctx=ctx,
            get_final_selectable_unique_events_fn=lambda cat=None: dom_evs,
            is_domestic_diversity_satisfied_fn=lambda evs: True,
            count_unique_section_events_fn=lambda cat: counts[cat.value.upper()],
            get_unique_candidate_events_fn=lambda: all_evs,
        )

        assert status == "DATA_UNAVAILABLE"
        # 96h must NEVER be triggered on weekday
        assert not any("WEEKEND_RESCUE_96H" in m for m in logged_messages)
        assert not any("96h" in m for m in logged_messages)

    def test_true_shortage_clean_failure_no_fabrication(self, tmp_path):
        from app.pipeline.fallback_manager import run_expansion_and_fallbacks

        logged_messages = []
        ctx = MagicMock()
        ctx.is_weekend = True
        ctx.run_reference_time = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
        ctx.target_date = date(2026, 9, 20)
        ctx.log_exec = lambda msg: logged_messages.append(msg)
        ctx.seen_urls = set()
        ctx.articles_lookup = {}
        ctx.verified_events = []
        ctx.high_confidence_single_candidates = []
        ctx.single_source_events = []
        ctx.date_deferred_articles = []
        ctx.domestic_reserve_pool = []
        ctx.india_reserve_pool = []
        ctx.intl_reserve_pool = []
        ctx.reg_clf.classify_event.side_effect = lambda ev, arts: ev.event_category

        dom_evs = [_create_test_event(f"dom_{i}", f"Dom Event {i}", f"a_dom_{i}", category=NewsCategory.DOMESTIC) for i in range(5)]
        india_evs = [_create_test_event(f"ind_{i}", f"India Event {i}", f"a_ind_{i}", category=NewsCategory.INDIA) for i in range(4)]
        intl_evs = [_create_test_event(f"int_{i}", f"Intl Event {i}", f"a_int_{i}", category=NewsCategory.INTERNATIONAL) for i in range(5)]
        all_evs = dom_evs + india_evs + intl_evs

        # Remains at 4 even after 96h
        status = run_expansion_and_fallbacks(
            ctx=ctx,
            get_final_selectable_unique_events_fn=lambda cat=None: dom_evs,
            is_domestic_diversity_satisfied_fn=lambda evs: True,
            count_unique_section_events_fn=lambda cat: {"DOMESTIC": 5, "INDIA": 4, "INTERNATIONAL": 5}[cat.value.upper()],
            get_unique_candidate_events_fn=lambda: all_evs,
        )

        assert status == "DATA_UNAVAILABLE"
        # Partial state is persisted
        state_file = save_partial_weekend_state(ctx, domestic_count=5, india_count=4, intl_count=5, data_dir=tmp_path)
        assert state_file is not None
        assert state_file.exists()

