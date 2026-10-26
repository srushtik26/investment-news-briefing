"""
Regression tests for production run fixes:
1. Preventing false India reclassification of global central bank stories (e.g. US Fed rate hike).
2. Rejecting bare regulatory filing forms (e.g. 'N-2/A') as generic headlines.
3. Domestic multi-horizon selection recovery when candidate count is deficient.
"""
from datetime import datetime, timezone, timedelta
import pytest

from app.models.article import Article
from app.models.event import Event
from app.models.enums import NewsCategory, VerificationTier
from app.classification.region_classifier import (
    EventRegionClassifier,
    verify_india_business_nexus,
)
from app.filtering.rules import is_generic_headline


def test_us_fed_rate_hike_not_reclassified_to_india():
    """Verify that a global central bank/rate hike headline with passing RBI mention is NOT reclassified to India."""
    title = "The backdrop in which the U.S. Fed raised interest rate"
    content = (
        "The Reserve Bank of India maintained its stance, but the global economy watched as "
        "the U.S. Federal Reserve raised benchmark interest rates to counter inflation pressures. "
        "Federal Reserve Chairman Jerome Powell indicated further policy tightening."
    )
    article = Article(
        id="art_fed_1",
        title=title,
        url="https://www.thehindu.com/business/Economy/the-backdrop-in-which-the-us-fed-raised-interest-rate/article123.ece",
        content_text=content,
        published_at=datetime.now(timezone.utc),
        source_name="The Hindu",
    )
    event = Event(
        canonical_title=title,
        article_ids=[article.id],
        description="US Federal Reserve raised interest rates to counter inflation",
        event_category=NewsCategory.INTERNATIONAL,
    )

    # 1. India business nexus check must REJECT
    is_nexus, nexus_reason = verify_india_business_nexus(event, article)
    assert not is_nexus, f"Expected nexus to be rejected, got reason: {nexus_reason}"
    assert "international central bank" in nexus_reason.lower() or "no strong india business signal" in nexus_reason.lower()

    # 2. Region classification must be INTERNATIONAL
    clf = EventRegionClassifier()
    detected_cat, cat_reason = clf.classify_with_reason(
        title=event.canonical_title,
        content=article.content_text[:500],
        companies=event.companies_involved,
        discovery_region=NewsCategory.INTERNATIONAL,
    )
    assert detected_cat == NewsCategory.INTERNATIONAL, f"Expected INTERNATIONAL, got {detected_cat}: {cat_reason}"


def test_bare_form_codes_rejected_as_generic_headlines():
    """Verify bare SEC/regulatory filing form codes are rejected as generic headlines."""
    bare_forms = [
        "N-2/A", "10-K", "8-K", "Form 10-Q", "SC 13D/A", "424B2", "Form 4", "DEF 14A",
        "EX-99.1", "ex-99.1", "Exhibit 99.1", "EX 99.1", "EX-10.1",
    ]
    for form in bare_forms:
        is_gen, reason = is_generic_headline(form)
        assert is_gen, f"Form code '{form}' should have been rejected as generic headline, got reason: {reason}"

    # Valid headlines mentioning form codes should still be accepted
    valid_titles = [
        "Tesla files 10-K report showing 15% revenue expansion",
        "Berkshire Hathaway submits Form 4 detailing Apple share sale",
        "Blackstone amends N-2/A filing for $2 billion credit fund",
        "Company files EX-99.1 detailing quarterly earnings results",
    ]
    for valid_t in valid_titles:
        is_gen, reason = is_generic_headline(valid_t)
        assert not is_gen, f"Valid headline '{valid_t}' was incorrectly rejected: {reason}"


def test_short_headline_graceful_handling_and_fallback():
    """Verify short headlines (< 10 chars) are gracefully padded and never crash EditorialStorySelection."""
    from app.ai.models import EditorialStorySelection
    from app.ai.headline_synthesis import generate_grounded_fallback_headline
    from app.pipeline.story_prep import prepare_final_story, StoryContext
    from app.models.event import Event
    from app.models.article import Article
    from app.models import NewsCategory

    # 1. Direct EditorialStorySelection instantiation with short headline
    story = EditorialStorySelection(
        section="international",
        event_id="test_ev_short",
        headline="EX-99.1",
        source="SEC",
        url="https://www.sec.gov/ex991",
    )
    assert len(story.headline) >= 10
    assert "EX-99.1" in story.headline

    # 2. generate_grounded_fallback_headline with short title article
    art = Article(
        title="EX-99.1",
        url="https://www.sec.gov/ex991",
        source_name="SEC",
        published_at=datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc),
        content_text="Sample corporate filing exhibit without long title.",
    )
    ev = Event(
        canonical_title="EX-99.1",
        description="Sample SEC exhibit disclosure event.",
        event_category=NewsCategory.INTERNATIONAL,
        article_ids=[art.id],
    )
    hl = generate_grounded_fallback_headline(event=ev, article=art)
    assert len(hl) >= 10

    # 3. prepare_final_story with short headline
    from app.pipeline.story_prep import build_story_context
    sc = build_story_context(ev, art)
    final_story = prepare_final_story(sc)
    assert isinstance(final_story, EditorialStorySelection)
    assert len(final_story.headline) >= 10



def test_domestic_multi_horizon_recovery():
    """Verify domestic selection recovers candidates beyond 24h when today/24h pool has fewer than 5 stories."""
    from unittest.mock import MagicMock
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import run_ranking_and_selection
    from app.ranking.models import RankedCandidatePool, ScoredEvent, ScoreBreakdown
    from app.verification.domestic_trending import DomesticTrendingEvaluator

    now = datetime(2026, 9, 25, 4, 0, tzinfo=timezone.utc)
    ctx = PipelineContext(run_reference_time=now)
    ctx.domestic_evaluator = DomesticTrendingEvaluator()
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "different")

    # Create 4 fresh domestic stories (1h old)
    dom_s = []
    all_arts = {}
    all_evs = {}
    for i in range(4):
        art = Article(
            id=f"dom_art_{i}",
            title=f"Prime Minister inaugurates national highway corridor phase {i} in Maharashtra",
            url=f"https://www.thehindu.com/news/national/highway-phase-{i}",
            content_text=f"The Prime Minister inaugurated a massive infrastructure project in Maharashtra with investment of Rs {1000 + i * 100} crore.",
            published_at=now - timedelta(hours=1),
            source_name="The Hindu",
        )
        ev = Event(
            canonical_title=art.title,
            article_ids=[art.id],
            description="Infrastructure inauguration by PM",
            event_category=NewsCategory.DOMESTIC,
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        scored = ScoredEvent(
            event=ev,
            score_breakdown=ScoreBreakdown(
                financial_magnitude=0.0, market_impact=0.0, investor_relevance=0.0,
                corporate_significance=0.0, source_quality=75.0, strategic_bonuses=0.0,
                editorial_signals=0.0, relevance_penalties=0.0, total_score=75.0, rationale="fresh",
            ),
            investment_score=75.0,
            rank=i + 1,
        )
        dom_s.append(scored)
        all_arts[art.id] = art
        all_evs[ev.id] = ev

    # Create 1 older domestic story (30h old) in memory that was deferred/older
    older_art = Article(
        id="dom_art_older",
        title="Supreme Court issues landmark ruling on environmental clearances for mining",
        url="https://indianexpress.com/article/india/supreme-court-mining-clearances",
        content_text="The Supreme Court of India delivered a comprehensive judgment on environmental norms and forest clearance regulations.",
        published_at=now - timedelta(hours=30),
        source_name="The Indian Express",
    )
    older_ev = Event(
        canonical_title=older_art.title,
        article_ids=[older_art.id],
        description="Supreme Court ruling on environment clearances",
        event_category=NewsCategory.DOMESTIC,
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
    )
    all_arts[older_art.id] = older_art
    all_evs[older_ev.id] = older_ev

    ctx.articles_lookup = all_arts
    ctx.high_confidence_single_candidates = [older_ev]

    # Populate 5 India and 5 International
    ind_s = []
    intl_s = []
    for i in range(5):
        in_art = Article(
            id=f"in_art_{i}",
            title=f"Tata Motors Q2 net profit jumps {20 + i}% to Rs {3500 + i * 100} crore",
            url=f"https://www.livemint.com/market/tata-motors-q2-{i}",
            content_text="Tata Motors declared strong quarterly results beating street estimates.",
            published_at=now - timedelta(hours=2),
            source_name="Livemint",
        )
        in_ev = Event(
            canonical_title=in_art.title,
            article_ids=[in_art.id],
            description="Tata Motors Q2 financial results",
            event_category=NewsCategory.INDIA,
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        all_arts[in_art.id] = in_art
        all_evs[in_ev.id] = in_ev
        ind_s.append(ScoredEvent(
            event=in_ev,
            score_breakdown=ScoreBreakdown(
                financial_magnitude=0.0, market_impact=0.0, investor_relevance=0.0,
                corporate_significance=0.0, source_quality=80.0, strategic_bonuses=0.0,
                editorial_signals=0.0, relevance_penalties=0.0, total_score=80.0, rationale="fresh",
            ),
            investment_score=80.0,
            rank=i + 1,
        ))

        intl_art = Article(
            id=f"intl_art_{i}",
            title=f"Microsoft acquires cloud AI cybersecurity startup for ${100 + i} million",
            url=f"https://www.cnbc.com/2026/09/25/microsoft-deal-{i}.html",
            content_text="Microsoft announced an agreement to acquire an AI security vendor.",
            published_at=now - timedelta(hours=2),
            source_name="CNBC",
        )
        intl_ev = Event(
            canonical_title=intl_art.title,
            article_ids=[intl_art.id],
            description="Microsoft corporate acquisition",
            event_category=NewsCategory.INTERNATIONAL,
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        all_arts[intl_art.id] = intl_art
        all_evs[intl_ev.id] = intl_ev
        intl_s.append(ScoredEvent(
            event=intl_ev,
            score_breakdown=ScoreBreakdown(
                financial_magnitude=0.0, market_impact=0.0, investor_relevance=0.0,
                corporate_significance=0.0, source_quality=80.0, strategic_bonuses=0.0,
                editorial_signals=0.0, relevance_penalties=0.0, total_score=80.0, rationale="fresh",
            ),
            investment_score=80.0,
            rank=i + 1,
        ))

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=dom_s,
        india_candidates=ind_s,
        international_candidates=intl_s,
    )
    ctx.ranker = mock_ranker
    accepted = [{"event_id": k} for k in all_evs]

    # Force 24h fallback status to verify recovery expands to 36h
    ctx.pipeline_status = "FALLBACK_SUCCESS_24H"

    _, dom_res, in_res, intl_res, sufficient, _ = run_ranking_and_selection(ctx, accepted, all_evs)
    assert len(dom_res) == 5, f"Expected 5 domestic candidates, got {len(dom_res)}"
    selected_titles = [s.event.canonical_title for s in dom_res]
    assert older_ev.canonical_title in selected_titles, "30h old domestic event was not recovered during horizon expansion"


def test_institutional_anchor_not_rejected_by_political_attack_pattern():
    """Verify that major constitutional/institutional events with political fallout are not rejected as noise."""
    from app.verification.domestic_trending import DomesticTrendingEvaluator

    evaluator = DomesticTrendingEvaluator()

    # 1. Headline about Election Commission rift with political attack in subordinate clause: MUST PASS
    title = "Election Commission Rift Sparks Political Face-Off as BJP Attacks Rahul Gandhi"
    text = "The Election Commission faced internal dissent as commissioners clashed over electoral revision rules."
    is_noise, reason = evaluator.is_domestic_noise(title, text)
    assert not is_noise, f"Election Commission rift should not be noise: {reason}"

    # 2. Pure mudslinging headline: MUST BE REJECTED
    mudslinging_title = "BJP attacks Rahul Gandhi over remarks in London"
    mudslinging_text = "The BJP launched a sharp counter-attack against Rahul Gandhi over his recent statements."
    is_noise, reason = evaluator.is_domestic_noise(mudslinging_title, mudslinging_text)
    assert is_noise, "Pure political mudslinging should be flagged as noise"

    # 3. Pure partisan attack headline starting with party: MUST BE REJECTED
    attack_title = "BJP slams Election Commission over poll date announcement"
    attack_text = "Party leaders accused the commission of bias in election scheduling."
    is_noise, reason = evaluator.is_domestic_noise(attack_title, attack_text)
    assert is_noise, "Partisan attack headline starting with 'BJP slams' should be flagged as noise"


def test_get_final_selectable_unique_events_filters_ineligible_domestic():
    """Verify get_final_selectable_unique_events filters out ineligible domestic events when category=DOMESTIC."""
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import get_final_selectable_unique_events
    from app.verification.domestic_trending import DomesticTrendingEvaluator

    ctx = PipelineContext(run_reference_time=datetime.now(timezone.utc))
    ctx.domestic_evaluator = DomesticTrendingEvaluator()

    # Eligible domestic article
    art_elig = Article(
        id="art_el_1",
        title="Cabinet approves Rs 10,000 crore expansion for Vande Bharat train network",
        url="https://www.thehindu.com/news/national/vande-bharat-expansion",
        content_text="The Union Cabinet approved a Rs 10,000 crore infrastructure package for railway expansion across India.",
        published_at=datetime.now(timezone.utc),
        source_name="The Hindu",
    )
    ev_elig = Event(
        canonical_title=art_elig.title,
        article_ids=[art_elig.id],
        description="Cabinet approved Vande Bharat expansion",
        event_category=NewsCategory.DOMESTIC,
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        single_source_confidence_score=90.0,
    )

    # Ineligible domestic article (routine petty theft / local accident)
    art_inelig = Article(
        id="art_inel_1",
        title="Two held for theft in local market by city police",
        url="https://www.hindustantimes.com/india-news/local-theft-arrest",
        content_text="Local police arrested two individuals for stealing a motorcycle in the city market.",
        published_at=datetime.now(timezone.utc),
        source_name="Hindustan Times",
    )
    ev_inelig = Event(
        canonical_title=art_inelig.title,
        article_ids=[art_inelig.id],
        description="Petty theft arrest",
        event_category=NewsCategory.DOMESTIC,
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        single_source_confidence_score=90.0,
    )

    ctx.articles_lookup = {art_elig.id: art_elig, art_inelig.id: art_inelig}
    ctx.high_confidence_single_candidates = [ev_elig, ev_inelig]

    selectable = get_final_selectable_unique_events(ctx, category=NewsCategory.DOMESTIC)
    sel_ids = {e.id for e in selectable}

    assert ev_elig.id in sel_ids, "Eligible domestic event was omitted from selectable events"
    assert ev_inelig.id not in sel_ids, "Ineligible domestic event (petty crime) was incorrectly included in selectable events"



def test_stage6_history_reject_blocked_from_refill_pool():
    """
    Regression test for 2026-09-28 production failure (Check #9 / FINAL_VALIDATION).

    Root cause: run_deduplication did not write rejected event IDs into
    ctx.dedup_rejected_event_ids, so get_final_selectable_unique_events and
    run_post_dedup_refill could pull a Stage-6-rejected event back into the
    candidate pool.  Stage 9 validation (Check #9) then detected the 3-day
    fingerprint collision and aborted the pipeline.

    This test verifies that:
    1. After run_deduplication, ctx.dedup_rejected_event_ids contains the IDs
       of every story rejected by filter_stories.
    2. get_final_selectable_unique_events never returns an event whose ID is in
       ctx.dedup_rejected_event_ids, even when that event is otherwise valid and
       present in ctx.verified_events / ctx.high_confidence_single_candidates.
    """
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import get_final_selectable_unique_events

    ref_time = datetime.now(timezone.utc)
    ctx = PipelineContext(run_reference_time=ref_time)

    # Build a valid international article & event that would normally pass all
    # quality gates — simulating "Buy these two stocks..." (Mizuho, 2026-09-28).
    art_repeat = Article(
        id="art_repeat_intl_1",
        title="Mizuho Recommends Select Real Estate Equities as 10-Year Treasury Yield Touches 5%",
        url="https://www.livemint.com/market/stock-market-news/mizuho-real-estate-reco-2026",
        content_text=(
            "Mizuho Securities issued a buy recommendation on select real estate equities "
            "yielding 4% as the US 10-year Treasury yield crossed 5%, offering relative value."
        ),
        published_at=ref_time,
        source_name="Mint",
    )
    ev_repeat = Event(
        canonical_title=art_repeat.title,
        article_ids=[art_repeat.id],
        description="Mizuho buy recommendation on real estate equities",
        event_category=NewsCategory.INTERNATIONAL,
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        single_source_confidence_score=92.0,
    )

    # Simulate Stage 6 having rejected this event (3-day history repeat).
    ctx.dedup_rejected_event_ids.add(ev_repeat.id)

    ctx.articles_lookup = {art_repeat.id: art_repeat}
    ctx.high_confidence_single_candidates = [ev_repeat]

    # get_final_selectable_unique_events must NOT return the rejected event.
    selectable = get_final_selectable_unique_events(ctx, category=NewsCategory.INTERNATIONAL)
    sel_ids = {e.id for e in selectable}

    assert ev_repeat.id not in sel_ids, (
        "Stage 6 history-rejected event was incorrectly returned by "
        "get_final_selectable_unique_events — it would re-enter the refill pool "
        "and trigger Check #9 in Stage 9 validation."
    )


def test_run_deduplication_populates_dedup_rejected_event_ids():
    """
    Verify that run_deduplication writes rejected event IDs into
    ctx.dedup_rejected_event_ids immediately after filter_stories.

    This is the upstream half of the 2026-09-28 hotfix: without this write,
    the check in get_final_selectable_unique_events is never triggered.
    """
    from unittest.mock import MagicMock, patch
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import run_deduplication

    ref_time = datetime.now(timezone.utc)
    ctx = PipelineContext(run_reference_time=ref_time)

    art1 = Article(
        id="art_jsw_1",
        title="JSW One Platforms Files DRHP With SEBI To Raise ₹3,054 Crore via IPO",
        url="https://economictimes.com/jsw-one-drhp-sebi-ipo",
        content_text="JSW One Platforms filed its DRHP with SEBI for an IPO to raise up to ₹3,054 crore.",
        published_at=ref_time,
        source_name="Economic Times",
    )
    ev1 = Event(
        canonical_title=art1.title,
        article_ids=[art1.id],
        description="JSW One Platforms DRHP filing",
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        single_source_confidence_score=88.0,
        companies_involved=["JSW One Platforms"],
    )

    ctx.articles_lookup = {art1.id: art1}
    ctx.verified_events = []
    ctx.high_confidence_single_candidates = [ev1]
    ctx.single_source_events = []

    # Stub the dedup engine: reject ev1 (simulates 3-day repeat)
    fake_rejected = [{"event_id": ev1.id, "headline": ev1.canonical_title, "category": "india"}]
    ctx.dedup_engine = MagicMock()
    ctx.dedup_engine.filter_stories.return_value = ([], fake_rejected)

    # Stub the class_map entry so candidate dict building doesn't fail
    mock_cls = MagicMock()
    mock_cls.event_type.value = "IPO"
    ctx.class_map = {art1.id: mock_cls}

    # Stub ctx.log_exec to avoid side effects
    ctx.log_exec = MagicMock()

    accepted, _ = run_deduplication(ctx)

    # The rejected event ID must now be in ctx.dedup_rejected_event_ids
    assert ev1.id in ctx.dedup_rejected_event_ids, (
        "run_deduplication did not write rejected event_id into "
        "ctx.dedup_rejected_event_ids — the downstream refill guard will not fire."
    )
    assert accepted == [], "Expected no accepted stories when all were rejected"


def test_india_recovery_passes_dynamic_horizon_and_processes_unseen_urls():
    """
    Ensure India recovery passes dynamic recovery horizon (72h on Mon/weekend)
    and does not prematurely insert the URL into ctx.seen_urls before calling process_candidate_item.
    """
    from unittest.mock import MagicMock, patch
    from app.discovery.models import DiscoveredArticle
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import run_ranking_and_selection
    from app.ranking.models import RankedCandidatePool
    from app.verification.corroborator import reset_corroboration_counter
    from app.deduplication.history import HistoryStore

    reset_corroboration_counter()
    ref_time = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)  # Monday
    ctx = PipelineContext(
        run_reference_time=ref_time,
        target_date=ref_time.date(),
        history_store=HistoryStore(db_path=":memory:", is_isolated=True),
    )
    ctx.settings = MagicMock()
    ctx.settings.DEDUP_LOOKBACK_DAYS = 3
    ctx.log_exec = MagicMock()
    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=[],
        india_candidates=[],
        international_candidates=[],
    )
    ctx.ranker = mock_ranker

    candidate = DiscoveredArticle(
        title="Tata Steel commissions ₹10,000 crore blast furnace expansion",
        url="https://business-standard.com/companies/tata-steel-10000-crore",
        source="Business Standard",
        category="india",
        search_query="India corporate capex",
        country="India",
        published_at=ref_time,
    )

    mock_provider = MagicMock()
    mock_provider.discover.return_value = [candidate]
    ctx.discovery_service = MagicMock()
    ctx.discovery_service.provider = mock_provider

    with patch("app.pipeline.candidate_processing.process_candidate_item") as mock_proc:
        mock_proc.return_value = None  # Just inspecting call invocation
        try:
            run_ranking_and_selection(ctx, [], {})
        except Exception:
            pass

        # Check active_horizon passed was 72.0 on Monday for India recovery
        india_call = next(
            (c for c in mock_proc.call_args_list if len(c[0]) > 1 and c[0][1] == "india"),
            mock_proc.call_args,
        )
        kwargs = india_call[1]
        assert kwargs.get("active_horizon") == 72.0, (
            f"Expected active_horizon=72.0 on Monday run, got {kwargs.get('active_horizon')}"
        )


def test_us_geography_regex_and_nexus_reject():
    """Verify headlines with U.S. or U.K. are identified as foreign geography and not misclassified with Indian nexus."""
    from app.classification.region_classifier import verify_india_business_nexus
    from app.models.event import Event
    from app.models.article import Article

    ev = Event(
        canonical_title="83% of CFOs say U.S. stocks are overvalued, even as optimism about their compani",
        description="A survey of CFOs regarding US stocks",
        event_category=NewsCategory.INTERNATIONAL,
    )
    art = Article(
        id="art-cfo-1",
        title="83% of CFOs say U.S. stocks are overvalued, even as optimism about their compani",
        url="https://economictimes.indiatimes.com/cfo-us-stocks",
        published_at=datetime.now(timezone.utc),
        content_text="83% of CFOs say U.S. stocks are overvalued. BSE and NSE market trading updates.",
        source_name="Economic Times",
    )
    is_nexus, reason = verify_india_business_nexus(ev, art)
    assert not is_nexus, f"Expected is_nexus=False for U.S. CFO article, got True ({reason})"
    assert "foreign geography" in reason.lower()


def test_run_post_dedup_refill_prunes_international_noise():
    """Verify run_post_dedup_refill prunes non-business/weather disaster stories from accepted_stories."""
    from unittest.mock import MagicMock
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import run_post_dedup_refill
    from app.models.event import Event
    from app.models.article import Article

    ref_time = datetime(2026, 9, 30, 11, 0, 0, tzinfo=timezone.utc)
    ctx = PipelineContext(run_reference_time=ref_time)
    ctx.settings.DEDUP_LOOKBACK_DAYS = 3
    ctx.log_exec = MagicMock()

    ev_weather = Event(
        canonical_title="27 killed, 4 missing in Nepal's latest weather disaster; scary visuals surface | World News",
        description="Nepal weather disaster causes fatalities",
        event_category=NewsCategory.INTERNATIONAL,
    )
    art_weather = Article(
        id="art-weather-1",
        title="27 killed, 4 missing in Nepal's latest weather disaster; scary visuals surface | World News",
        url="https://hindustantimes.com/world-news/nepal-disaster",
        published_at=ref_time,
        content_text="27 killed, 4 missing in Nepal's latest weather disaster.",
        source_name="Hindustan Times",
        category=NewsCategory.DOMESTIC,
    )
    ev_weather.article_ids = [art_weather.id]
    ctx.articles_lookup[art_weather.id] = art_weather

    accepted_stories = [
        {"event_id": ev_weather.id, "category": "international", "headline": ev_weather.canonical_title}
    ]
    event_by_id = {ev_weather.id: ev_weather}

    # Should prune the weather disaster
    valid_stories, _ = run_post_dedup_refill(ctx, accepted_stories, event_by_id)
    assert len(valid_stories) == 0, f"Expected weather disaster to be pruned, got {valid_stories}"


def test_get_final_selectable_unique_events_international_backfill():
    """Verify get_final_selectable_unique_events filters noise and backfills from deferred duplicates if count < 5."""
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import get_final_selectable_unique_events
    from app.models.event import Event
    from app.models.article import Article

    ref_time = datetime(2026, 9, 30, 11, 0, 0, tzinfo=timezone.utc)
    ctx = PipelineContext(run_reference_time=ref_time)

    # Event 1 & 2 share the same company "Google"
    ev1 = Event(
        canonical_title="Google acquires cybersecurity startup for $2B",
        description="Google deal",
        event_category=NewsCategory.INTERNATIONAL,
        companies_involved=["Google"],
    )
    art1 = Article(
        id="a1",
        title="Google acquires cybersecurity startup for $2B",
        url="https://techcrunch.com/google-acquires",
        published_at=ref_time,
        content_text="Google announced the $2B buyout.",
        source_name="TechCrunch",
    )
    ev1.article_ids = [art1.id]
    ctx.articles_lookup[art1.id] = art1

    ev2 = Event(
        canonical_title="Google unveils new quantum computing chip investing $5B capex",
        description="Google quantum",
        event_category=NewsCategory.INTERNATIONAL,
        companies_involved=["Google"],
    )
    art2 = Article(
        id="a2",
        title="Google unveils new quantum computing chip investing $5B capex",
        url="https://reuters.com/google-quantum",
        published_at=ref_time,
        content_text="Google plans $5B capex in quantum.",
        source_name="Reuters",
    )
    ev2.article_ids = [art2.id]
    ctx.articles_lookup[art2.id] = art2

    ctx.verified_events = [ev1, ev2]
    ctx.high_confidence_single_candidates = []

    selectable = get_final_selectable_unique_events(ctx, category=NewsCategory.INTERNATIONAL)
    # Both events should be kept because total international < 5, so ev2 is backfilled from deferred_intl
    assert len(selectable) == 2
    assert {e.id for e in selectable} == {ev1.id, ev2.id}


def test_2026_10_23_production_failure_url_with_word_broken_not_rejected():
    """
    Regression test for 2026-10-23 production failure (Check #4 / FINAL_VALIDATION).

    The production run on 2026-10-23 failed with:
    PIPELINE_FAILED stage=FINAL_VALIDATION check_id=4 reason=URL is inaccessible or dead:
    'https://timesofindia.indiatimes.com/world/rest-of-world/rival-factions-civil-war-shadow-of-terror-why-libya-is-still-broken-15-years-after-gaddafis-bloody-end/articleshow/135116605.cms'

    The substring 'broken' in the URL slug triggered a false-positive dead link marker.
    Check 4 must only reject explicit dead link markers (e.g. 'dead-link', 'broken-link', '/404.html'),
    and allow legitimate news slugs that contain the word 'broken'.
    """
    from app.ai.models import BriefingEditorialPayload, EditorialStorySelection
    from app.validation.engine import FinalValidationEngine, ValidationStatus

    target_url = "https://timesofindia.indiatimes.com/world/rest-of-world/rival-factions-civil-war-shadow-of-terror-why-libya-is-still-broken-15-years-after-gaddafis-bloody-end/articleshow/135116605.cms"
    ref_time = datetime(2026, 10, 23, 5, 0, 0, tzinfo=timezone.utc)

    # Build 5 domestic, 5 India, 5 International stories
    articles_lookup = {}
    events_lookup = {}
    dom_stories = []
    ind_stories = []
    intl_stories = []

    for i in range(1, 6):
        # Domestic
        d_art = Article(
            id=f"dom_{i}",
            title=f"National Infrastructure Project {i} Commences Commercial Operations",
            url=f"https://thehindu.com/news/national/project-{i}",
            source_name="The Hindu",
            published_at=ref_time - timedelta(hours=2),
            content_text=f"National Infrastructure Project {i} commences commercial operations across India.",
            category=NewsCategory.DOMESTIC,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        d_ev = Event(
            id=f"ev_dom_{i}",
            canonical_title=d_art.title,
            description=d_art.content_text,
            event_category=NewsCategory.DOMESTIC,
            article_ids=[d_art.id],
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
        )
        articles_lookup[d_art.id] = d_art
        events_lookup[d_ev.id] = d_ev
        dom_stories.append(EditorialStorySelection(section="domestic", event_id=d_ev.id, headline=d_art.title, source=d_art.source_name, url=d_art.url))

        # India
        in_art = Article(
            id=f"in_{i}",
            title=f"Tata Motors Q2 Net Profit Climbs {10+i}% to Rs {1000*i} Crore",
            url=f"https://business-standard.com/companies/tata-motors-q2-{i}",
            source_name="Business Standard",
            published_at=ref_time - timedelta(hours=3),
            content_text=f"Tata Motors Q2 net profit climbed {10+i}% to Rs {1000*i} crore with record sales.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        in_ev = Event(
            id=f"ev_in_{i}",
            canonical_title=in_art.title,
            description=in_art.content_text,
            companies_involved=[f"Tata Motors Company {i}"],
            financial_figures=[f"Rs {1000*i} crore", f"{10+i}%"],
            event_category=NewsCategory.INDIA,
            article_ids=[in_art.id],
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
        )
        articles_lookup[in_art.id] = in_art
        events_lookup[in_ev.id] = in_ev
        ind_stories.append(EditorialStorySelection(section="india", event_id=in_ev.id, headline=in_art.title, source=in_art.source_name, url=in_art.url))

        # International
        intl_url = target_url if i == 1 else f"https://reuters.com/business/global-corp-{i}-secures-order"
        intl_title = (
            "Rival factions, civil war, shadow of terror: Why Libya is still broken 15 years after Gaddafi's bloody end"
            if i == 1
            else f"Global Corp {i} Secures $10 Billion Infrastructure Order"
        )
        int_art = Article(
            id=f"intl_{i}",
            title=intl_title,
            url=intl_url,
            source_name="Times of India" if i == 1 else "Reuters",
            published_at=ref_time - timedelta(hours=4),
            content_text=f"International report on global development {i} with key market details.",
            category=NewsCategory.INTERNATIONAL,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        int_ev = Event(
            id=f"ev_intl_{i}",
            canonical_title=int_art.title,
            description=int_art.content_text,
            companies_involved=[f"Global Firm {i}"],
            financial_figures=["$10 billion"] if i != 1 else [],
            event_category=NewsCategory.INTERNATIONAL,
            article_ids=[int_art.id],
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
        )
        articles_lookup[int_art.id] = int_art
        events_lookup[int_ev.id] = int_ev
        intl_stories.append(EditorialStorySelection(section="international", event_id=int_ev.id, headline=int_art.title, source=int_art.source_name, url=int_art.url))

    payload = BriefingEditorialPayload(
        domestic_stories=dom_stories,
        india_stories=ind_stories,
        international_stories=intl_stories,
    )

    validator = FinalValidationEngine()
    report = validator.validate_briefing(
        payload=payload,
        events_lookup=events_lookup,
        articles_lookup=articles_lookup,
        run_reference_time=ref_time,
    )

    # Check 4 MUST pass
    c4 = next(r for r in report.check_results if r.check_id == 4)
    assert c4.passed is True, f"Check 4 failed unexpectedly: {c4.failure_reason}"


def test_finance_yahoo_whitelisted_and_source_policy():
    """Verify finance.yahoo.com and 'Yahoo Finance' pass SourceFilterRule and SourcePolicy."""
    from app.filtering.rules import SourceFilterRule
    from app.filtering.source_policy import SourcePolicy, get_tier, is_extraction_worthy

    # 1. SourceFilterRule check
    rule = SourceFilterRule()
    art_yf = Article(
        id="art_yf_1",
        title="Meta and SpaceX Announce Strategic Satellite Partnership",
        url="https://finance.yahoo.com/news/meta-spacex-strategic-partnership-12345.html",
        source_name="Yahoo Finance",
        published_at=datetime.now(timezone.utc),
        content_text="SpaceX and Meta signed a major satellite data transmission contract.",
    )
    res_yf = rule.evaluate(art_yf)
    assert res_yf.is_accepted, f"SourceFilterRule unexpectedly rejected Yahoo Finance article: {res_yf.rejection_reason}"

    art_yf_bang = Article(
        id="art_yf_2",
        title="Berkshire Hathaway Acquires Occidental Shares",
        url="https://finance.yahoo.com/news/berkshire-hathaway-acquires-shares-67890.html",
        source_name="Yahoo! Finance",
        published_at=datetime.now(timezone.utc),
        content_text="Warren Buffett's conglomerate purchased additional shares.",
    )
    res_bang = rule.evaluate(art_yf_bang)
    assert res_bang.is_accepted, f"SourceFilterRule unexpectedly rejected Yahoo! Finance article: {res_bang.rejection_reason}"

    # 2. SourcePolicy check: finance.yahoo.com must be PRIMARY and extraction worthy
    tier_yf = get_tier("https://finance.yahoo.com/news/market-update-123.html")
    assert tier_yf == "PRIMARY", f"Expected PRIMARY for finance.yahoo.com, got {tier_yf}"
    assert is_extraction_worthy("https://finance.yahoo.com/news/market-update-123.html") is True

    tier_regional = get_tier("https://uk.finance.yahoo.com/news/uk-budget-impact-456.html")
    assert tier_regional == "PRIMARY", f"Expected PRIMARY for uk.finance.yahoo.com, got {tier_regional}"

    # 3. Generic yahoo.com non-finance should still be BLOCKED
    tier_generic = get_tier("https://yahoo.com/entertainment/celeb-news-789.html")
    assert tier_generic == "BLOCKED", f"Expected BLOCKED for generic yahoo.com, got {tier_generic}"



