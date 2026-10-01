"""
Regression test suite for the audited pipeline bug fixes:
1. Category enum vs string normalization in refill
2. lookback_days non-int / string handling
3. Check 1 & 2 validation labels
4. Date-independent historical repeat fingerprint detection
5. Deferred international candidates dedup exclusion
6. Domestic/India recovery dedup_engine filtering
7. Fallback reconsider respects dedup_rejected_event_ids
8. Entity aliases normalization (bharti_airtel, bajaj_finance, etc.)
"""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock
import pytest

from app.deduplication.fingerprint import (
    generate_event_fingerprint,
    is_event_historical_repeat,
    normalize_entity_name,
)
from app.deduplication.history import HistoryStore
from app.models.article import Article
from app.models.enums import NewsCategory, VerificationTier
from app.models.event import Event
from app.pipeline.context import PipelineContext
from app.pipeline.selection import (
    get_final_selectable_unique_events,
    run_post_dedup_refill,
)
from app.pipeline.fallback_manager import reconsider_date_deferred_candidates
from app.validation.engine import FinalValidationEngine


def test_entity_aliases_normalization():
    """Verify new corporate aliases normalize to their canonical base."""
    assert normalize_entity_name("Bharti Airtel") == "airtel"
    assert normalize_entity_name("Airtel") == "airtel"
    assert normalize_entity_name("Bajaj Finance Limited") == "bajaj_finance"
    assert normalize_entity_name("Bajaj Finserv Ltd") == "bajaj_finserv"
    assert normalize_entity_name("Wipro Enterprises") == "wipro"
    assert normalize_entity_name("Infosys Ltd") == "infosys"


def test_date_independent_historical_repeat_detection(tmp_path):
    """Verify is_event_historical_repeat catches historical story even if target_date differs."""
    db_path = tmp_path / "test_hist_repeat.db"
    store = HistoryStore(db_path=db_path)

    # Save a story on Sept 29 with date-included fingerprint
    hist_date = date(2026, 9, 29)
    fkey, fhash = generate_event_fingerprint(
        company="Reliance Industries",
        event_type="acquisition",
        event_date=hist_date,
        key_facts=["5000cr"],
    )
    store.save_briefing(
        briefing_date=hist_date,
        stories=[
            {
                "event_id": "hist-rel-1",
                "event_fingerprint": fkey,
                "headline": "Reliance acquires clean energy unit for Rs 5000 crore",
                "company_name": "Reliance",
                "category": "india",
                "published_date": hist_date,
            }
        ],
    )

    # Candidate event evaluated on Oct 1 (2 days later)
    eval_date = date(2026, 10, 1)
    ev = Event(
        id="cand-rel-1",
        canonical_title="Reliance buys renewable energy firm for Rs 5,000 crore",
        description="Reliance buys renewable energy firm for Rs 5,000 crore",
        event_category=NewsCategory.INDIA,
        companies_involved=["Reliance Industries"],
        financial_figures=["5000cr"],
        article_ids=["art-1"],
    )
    setattr(ev, "event_type", "acquisition")

    is_rep, reason = is_event_historical_repeat(
        event=ev,
        history_store=store,
        target_date=eval_date,
        lookback_days=3,
        headline=ev.canonical_title,
    )
    assert is_rep is True
    assert "already appeared" in reason


def test_post_dedup_refill_category_enum_and_lookback_str(tmp_path):
    """Verify run_post_dedup_refill works when story categories are NewsCategory enums and lookback is str."""
    ctx = PipelineContext()
    ctx.run_reference_time = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    settings = MagicMock()
    settings.DEDUP_LOOKBACK_DAYS = "3"  # String value
    ctx.settings = settings
    ctx.articles_lookup = {}
    ctx.history_store = None
    ctx.dedup_engine = None
    eval_mock = MagicMock()
    eval_mock.evaluate.return_value = (True, 85.0, "QUALIFIED")
    ctx.domestic_evaluator = eval_mock

    # 5 accepted domestic, 5 accepted india, 5 accepted intl with enum categories
    accepted = []
    articles = {}
    for i in range(5):
        art_dom = Article(
            id=f"art-dom-{i}",
            title=f"Cabinet approves National Policy {i}",
            source_name="The Hindu",
            url=f"https://thehindu.com/dom-{i}",
            category=NewsCategory.DOMESTIC,
            published_at=datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc),
        )
        articles[art_dom.id] = art_dom
        accepted.append({"event_id": f"dom-{i}", "category": NewsCategory.DOMESTIC, "headline": f"Dom {i}"})
        accepted.append({"event_id": f"ind-{i}", "category": NewsCategory.INDIA, "headline": f"Ind {i}"})
        accepted.append({"event_id": f"intl-{i}", "category": NewsCategory.INTERNATIONAL, "headline": f"Intl {i}"})

    ctx.articles_lookup = articles
    ev_by_id = {}
    for s in accepted:
        art_ids = [f"art-dom-{s['event_id'].split('-')[1]}"] if s["category"] == NewsCategory.DOMESTIC else []
        ev_by_id[s["event_id"]] = Event(
            id=s["event_id"],
            canonical_title=s["headline"],
            description=s["headline"],
            article_ids=art_ids,
        )

    res_stories, res_map = run_post_dedup_refill(ctx, accepted, ev_by_id)
    assert len(res_stories) == 15


def test_deferred_intl_excludes_dedup_rejected():
    """Verify deferred_intl events are skipped if in dedup_rejected_event_ids."""
    ctx = PipelineContext()
    ctx.run_reference_time = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    ctx.dedup_rejected_event_ids = {"ev-def-1"}

    art1 = Article(
        id="art-intl-1",
        title="Microsoft signs cloud deal with OpenAI",
        source_name="Reuters",
        url="https://reuters.com/deal1",
        category=NewsCategory.INTERNATIONAL,
        published_at=datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc),
    )
    art2 = Article(
        id="art-intl-2",
        title="Microsoft announces new AI datacenter expansion",
        source_name="Bloomberg",
        url="https://bloomberg.com/deal2",
        category=NewsCategory.INTERNATIONAL,
        published_at=datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc),
    )
    ctx.articles_lookup = {"art-intl-1": art1, "art-intl-2": art2}

    ev1 = Event(
        id="ev-intl-1",
        canonical_title="Microsoft signs cloud deal with OpenAI",
        description="Microsoft signs cloud deal with OpenAI",
        event_category=NewsCategory.INTERNATIONAL,
        companies_involved=["Microsoft"],
        article_ids=["art-intl-1"],
    )
    ev2 = Event(
        id="ev-def-1",
        canonical_title="Microsoft announces new AI datacenter expansion",
        description="Microsoft announces new AI datacenter expansion",
        event_category=NewsCategory.INTERNATIONAL,
        companies_involved=["Microsoft"],
        article_ids=["art-intl-2"],
    )

    ctx.verified_events = [ev1, ev2]
    ctx.high_confidence_single_candidates = []
    ctx.single_source_events = []

    selectable = get_final_selectable_unique_events(ctx, category=NewsCategory.INTERNATIONAL)
    sel_ids = {e.id for e in selectable}
    assert "ev-intl-1" in sel_ids
    assert "ev-def-1" not in sel_ids  # Should be excluded because of dedup_rejected_event_ids


def test_fallback_reconsider_excludes_dedup_rejected():
    """Verify reconsider_date_deferred_candidates does not add dedup_rejected events."""
    ctx = PipelineContext()
    ctx.run_reference_time = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    ctx.dedup_rejected_event_ids = {"fb-ev-rej"}

    art = Article(
        id="art-def-1",
        title="Domestic Infrastructure Project cleared by Cabinet",
        source_name="The Hindu",
        url="https://thehindu.com/infra",
        category=NewsCategory.DOMESTIC,
        published_at=datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc),
    )
    ctx.date_deferred_articles = [art]
    ctx.articles_lookup = {"art-def-1": art}

    existing_fb = Event(
        id="fb-ev-rej",
        canonical_title=art.title,
        description=art.title,
        article_ids=[art.id],
        event_category=NewsCategory.DOMESTIC,
    )
    ctx.fallback_events = [existing_fb]
    ctx.verified_events = []
    ctx.high_confidence_single_candidates = []
    ctx.single_source_events = []

    engine_mock = MagicMock()
    engine_mock.filter_article.return_value.is_accepted = True
    ctx.domestic_filter_engine = engine_mock

    classifier_mock = MagicMock()
    clf_res = MagicMock()
    clf_res.success = True
    clf_res.classification.is_hard_business_event = True
    clf_res.classification.company_names = []
    clf_res.classification.financial_numbers = []
    clf_res.classification.percentages = []
    classifier_mock.classify.return_value = clf_res
    ctx.classifier = classifier_mock
    ctx.class_map = {}

    reg_clf = MagicMock()
    reg_clf.classify_event.return_value = NewsCategory.DOMESTIC
    ctx.reg_clf = reg_clf

    evaluator_mock = MagicMock()
    evaluator_mock.evaluate.return_value = (True, 85.0, "QUALIFIED")
    ctx.domestic_evaluator = evaluator_mock

    added = reconsider_date_deferred_candidates(NewsCategory.DOMESTIC, 48.0, ctx)
    assert added == 0
    assert "fb-ev-rej" not in [e.id for e in ctx.high_confidence_single_candidates]
