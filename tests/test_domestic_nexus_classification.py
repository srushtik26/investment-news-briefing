from app.classification.region_classifier import EventRegionClassifier, verify_region_eligibility, has_positive_indian_nexus
from app.models.enums import NewsCategory
from app.models.event import Event
from app.models.article import Article
from datetime import datetime, timezone
from run_daily_15 import _canonical_numbers


def test_foreign_subject_does_not_survive_domestic_discovery_prior():
    classifier = EventRegionClassifier()

    category, reason = classifier.classify_with_reason(
        title='"Significant Reduction" In Chinese Deployment: Defence Ministry Annual Report',
        content='The report discusses Chinese troop deployment and regional military posture.',
        discovery_region=NewsCategory.DOMESTIC,
    )

    assert category == NewsCategory.INTERNATIONAL
    assert "foreign" in reason.lower() or "no indian domestic nexus" in reason.lower()


def test_china_pulls_back_modi_xi_talks_classifies_without_error():
    """Verify border talk headline classifies properly without NameError on has_india_mention."""
    classifier = EventRegionClassifier()
    category, reason = classifier.classify_with_reason(
        title='China Pulls Back From LAC As Modi-Xi Talks Signal A Cautious Thaw',
        content='Diplomatic and military officials reported pullback across friction points.',
        discovery_region=None,
    )
    assert category in (NewsCategory.INTERNATIONAL, NewsCategory.DOMESTIC, NewsCategory.INDIA)
    assert len(reason) > 0


def test_valid_indian_public_affairs_story_remains_domestic():
    classifier = EventRegionClassifier()

    category, reason = classifier.classify_with_reason(
        title='Union Cabinet clears major national infrastructure programme',
        content='The Government of India approved the programme for implementation across India.',
        discovery_region=NewsCategory.DOMESTIC,
    )

    assert category == NewsCategory.DOMESTIC
    assert "india national public affairs" in reason.lower() or "indian nexus" in reason.lower()


def test_rajasthan_urban_local_body_polls_classifies_as_domestic():
    """State / municipal election in Rajasthan must classify as DOMESTIC and pass eligibility."""
    classifier = EventRegionClassifier()
    title = "Rajasthan Urban Local Body polls results:BJP secures 4 of 10 municipal corporations; Congress blames 'unfair' delimitation"
    content = "Jaipur: The Rajasthan local body election results were announced on Tuesday with BJP securing municipal corporations."

    category, reason = classifier.classify_with_reason(
        title=title,
        content=content,
        discovery_region=NewsCategory.DOMESTIC,
    )

    assert category == NewsCategory.DOMESTIC
    assert "indian state/city/subnational" in reason.lower() or "indian nexus" in reason.lower()

    # Verify region eligibility gate
    event = Event(
        id="evt_raj",
        canonical_title=title,
        description="Rajasthan Urban Local Body polls municipal corporation election outcomes.",
        event_category=NewsCategory.DOMESTIC,
        article_ids=["art_raj"],
        first_seen_utc=datetime.now(timezone.utc),
    )
    art = Article(
        id="art_raj",
        title=title,
        content_text=content,
        url="https://example.com/rajasthan-polls",
        source_name="The Times of India",
        published_at=datetime.now(timezone.utc),
    )
    is_valid, elig_reason = verify_region_eligibility(event, art, NewsCategory.DOMESTIC)
    assert is_valid is True
    assert "valid" in elig_reason.lower() or "passed" in elig_reason.lower()


def test_up_politics_akhilesh_yadav_dalit_outreach_classifies_as_domestic():
    """Regional politics in Uttar Pradesh must classify as DOMESTIC and pass eligibility."""
    classifier = EventRegionClassifier()
    title = "The Politics Of Blue: Why Akhilesh Yadav-vs-Mayawati Dalit outreach will play a big role in 2027 UP election"
    content = "Lucknow: Samajwadi Party chief Akhilesh Yadav and BSP chief Mayawati are ramping up Dalit outreach in Uttar Pradesh ahead of the 2027 assembly election."

    category, reason = classifier.classify_with_reason(
        title=title,
        content=content,
        discovery_region=NewsCategory.DOMESTIC,
    )

    assert category == NewsCategory.DOMESTIC
    assert "indian state/city/subnational" in reason.lower() or "indian nexus" in reason.lower()

    event = Event(
        id="evt_up",
        canonical_title=title,
        description="Dalit outreach politics in Uttar Pradesh featuring Akhilesh Yadav and Mayawati.",
        event_category=NewsCategory.DOMESTIC,
        article_ids=["art_up"],
        first_seen_utc=datetime.now(timezone.utc),
    )
    art = Article(
        id="art_up",
        title=title,
        content_text=content,
        url="https://example.com/up-election",
        source_name="NDTV",
        published_at=datetime.now(timezone.utc),
    )
    is_valid, elig_reason = verify_region_eligibility(event, art, NewsCategory.DOMESTIC)
    assert is_valid is True


def test_chinese_deployment_without_indian_nexus_rejected_from_domestic():
    """Foreign deployment story without positive Indian nexus is rejected from Domestic."""
    title = '"Significant Reduction" In Chinese Deployment: Defence Ministry Annual Report'
    content = 'The report discusses Chinese troop deployment and regional military posture in east Asia.'

    event = Event(
        id="evt_china",
        canonical_title=title,
        description="Defence ministry annual report discusses Chinese troop deployment.",
        event_category=NewsCategory.DOMESTIC,
        article_ids=["art_china"],
        first_seen_utc=datetime.now(timezone.utc),
    )
    art = Article(
        id="art_china",
        title=title,
        content_text=content,
        url="https://example.com/china-deployment",
        source_name="Global News",
        published_at=datetime.now(timezone.utc),
    )
    is_valid, elig_reason = verify_region_eligibility(event, art, NewsCategory.DOMESTIC)
    assert is_valid is False
    assert "indian" in elig_reason.lower()


def test_verify_region_eligibility_for_india_and_international():
    """Verify region eligibility gate works as expected for India and International."""
    india_title = "Reliance Industries and TCS announce major AI infrastructure investments"
    india_event = Event(
        id="evt_ind",
        canonical_title=india_title,
        description="Reliance Industries and Tata Consultancy Services announce AI investment.",
        event_category=NewsCategory.INDIA,
        article_ids=["art_ind"],
        first_seen_utc=datetime.now(timezone.utc),
    )
    india_art = Article(
        id="art_ind",
        title=india_title,
        content_text="Mumbai: Reliance Industries and Tata Consultancy Services announced multi-billion dollar projects.",
        url="https://example.com/ril-tcs",
        source_name="The Economic Times",
        published_at=datetime.now(timezone.utc),
    )
    is_valid, _ = verify_region_eligibility(india_event, india_art, NewsCategory.INDIA)
    assert is_valid is True

    intl_title = "Federal Reserve holds interest rates steady as US inflation cools"
    intl_event = Event(
        id="evt_intl",
        canonical_title=intl_title,
        description="Federal Reserve maintains interest rates steady in monetary policy meeting.",
        event_category=NewsCategory.INTERNATIONAL,
        article_ids=["art_intl"],
        first_seen_utc=datetime.now(timezone.utc),
    )
    intl_art = Article(
        id="art_intl",
        title=intl_title,
        content_text="Washington: The Federal Reserve held benchmark interest rates unchanged at 5.25%-5.50%.",
        url="https://example.com/fed-rates",
        source_name="Reuters",
        published_at=datetime.now(timezone.utc),
    )
    is_valid, _ = verify_region_eligibility(intl_event, intl_art, NewsCategory.INTERNATIONAL)
    assert is_valid is True


def test_number_canonicalisation_preserves_indian_grouping():
    """Verify Indian comma numbering like 5,57,700% matches 557,700%."""
    n1 = _canonical_numbers("Shares rallied 5,57,700% in multi-year breakout")
    n2 = _canonical_numbers("Shares rallied 557,700% in multi-year breakout")
    assert "557700" in n1
    assert "557700" in n2
    assert n1 == n2


def test_selection_region_rejected_and_backfill_logging():
    """Verify Stage 7 emits structured REGION_REJECTED, BACKFILL, and FINAL_REGION_COUNTS logs."""
    from pathlib import Path
    from unittest.mock import MagicMock
    from app.pipeline.context import PipelineContext
    from app.ranking.models import RankedCandidatePool, ScoredEvent, ScoreBreakdown
    from app.models.enums import VerificationTier
    from app.pipeline.selection import run_ranking_and_selection
    from app.verification.domestic_trending import DomesticTrendingEvaluator

    now = datetime.now(timezone.utc)
    logs = []
    ctx = PipelineContext(
        run_reference_time=now,
        data_dir=Path("./tmp"),
        logs_dir=Path("./tmp"),
        settings=MagicMock(MAX_CORROBORATION_SEARCHES=20),
        log_exec=lambda m: logs.append(m),
    )
    articles = {}
    events = {}

    def _mk(eid, title, content, cat, tier=VerificationTier.TWO_SOURCE_VERIFIED):
        art = Article(
            id=f"art_{eid}",
            title=title,
            content_text=content,
            url=f"https://example.com/{eid}",
            source_name="The Times of India" if cat != NewsCategory.INTERNATIONAL else "Reuters",
            published_at=now,
        )
        ev = Event(
            id=eid,
            canonical_title=title,
            description=content[:100],
            event_category=cat,
            verification_tier=tier,
            verification_confidence=90.0,
            article_ids=[art.id],
            first_seen_utc=now,
        )
        articles[art.id] = art
        events[eid] = ev
        return ScoredEvent(
            event=ev,
            score_breakdown=ScoreBreakdown(
                financial_magnitude=0.0, market_impact=0.0, investor_relevance=0.0,
                corporate_significance=0.0, source_quality=90.0, strategic_bonuses=0.0,
                editorial_signals=0.0, relevance_penalties=0.0, total_score=75.0, rationale="test",
            ),
            investment_score=75.0,
        )

    # Ineligible domestic candidate (Chinese deployment without Indian nexus)
    bad_china = _mk(
        "bad_china",
        '"Significant Reduction" In Chinese Deployment: Defence Ministry Annual Report',
        "The report discusses Chinese troop deployment and regional military posture in east Asia.",
        NewsCategory.DOMESTIC,
    )
    # 5 valid domestic candidates with high trending scores
    dom1 = _mk("dom1", "Union Cabinet approves major national highways development plan across India", "Modi Cabinet clears major national highways development plan across India. The Prime Minister announced this landmark infrastructure initiative.", NewsCategory.DOMESTIC)
    dom1.event.article_ids = ["art_dom1", "art_dom1_b"]
    dom2 = _mk("dom2", "Supreme Court delivers historic verdict on constitutional electoral law", "Supreme Court bench rules on parliamentary election reforms and national democratic policy.", NewsCategory.DOMESTIC)
    dom2.event.article_ids = ["art_dom2", "art_dom2_b"]
    dom3 = _mk("dom3", "Rajasthan Urban Local Body polls results: BJP secures 4 of 10 municipal corporations across state", "Jaipur: Rajasthan election results announced for municipal corporations with Prime Minister Narendra Modi welcoming the democratic outcome across the state.", NewsCategory.DOMESTIC)
    dom3.event.article_ids = ["art_dom3", "art_dom3_b"]
    dom4 = _mk("dom4", "The Politics Of Blue: Akhilesh Yadav and Mayawati Dalit outreach in Uttar Pradesh election campaign", "Lucknow: Samajwadi Party chief Akhilesh Yadav ramps up UP election outreach as Parliament discusses national political implications.", NewsCategory.DOMESTIC)
    dom4.event.article_ids = ["art_dom4", "art_dom4_b"]
    dom5 = _mk("dom5", "Parliament monsoon session passes landmark criminal law amendments", "Indian Parliament clears legislative bills on national public policy and justice reforms.", NewsCategory.DOMESTIC)
    dom5.event.article_ids = ["art_dom5", "art_dom5_b"]

    # 5 valid India candidates
    ind_cands = [
        _mk(f"ind_{i}", f"Indian firm Tata Power approves capital expenditure project {i} worth Rs 5000 crore", f"Mumbai: Tata Power announces expansion in Maharashtra.", NewsCategory.INDIA)
        for i in range(1, 6)
    ]

    # 5 valid International candidates
    intl_cands = [
        _mk(f"intl_{i}", f"Global market indices move as US Federal Reserve updates growth forecast {i}", f"Washington: Global markets rally 2.5% on Fed statements.", NewsCategory.INTERNATIONAL)
        for i in range(1, 6)
    ]

    ctx.articles_lookup = articles
    ctx.domestic_evaluator = DomesticTrendingEvaluator()
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "different")

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=[bad_china, dom1, dom2, dom3, dom4, dom5],
        india_candidates=ind_cands,
        international_candidates=intl_cands,
    )
    ctx.ranker = mock_ranker

    accepted = (
        [{"event_id": bad_china.event.id, "category": "domestic"}]
        + [{"event_id": d.event.id, "category": "domestic"} for d in [dom1, dom2, dom3, dom4, dom5]]
        + [{"event_id": ind.event.id, "category": "india"} for ind in ind_cands]
        + [{"event_id": intl.event.id, "category": "international"} for intl in intl_cands]
    )

    candidate_pool, dom_pool, ind_pool, intl_pool, sufficient, status = run_ranking_and_selection(
        ctx, accepted, events
    )

    all_logs = "\n".join(logs)

    # 1. Assert REGION_REJECTED log was emitted for bad_china
    assert "REGION_REJECTED:" in all_logs
    assert 'headline=""Significant Reduction" In Chinese Deployment: Defence Ministry Annual Report"' in all_logs or "Significant Reduction" in all_logs
    assert "requested_region=DOMESTIC" in all_logs

    # 2. Assert BACKFILL log was emitted with replacement
    assert "BACKFILL:" in all_logs
    assert "region=DOMESTIC" in all_logs
    assert "rejected=1" in all_logs

    # 3. Assert FINAL_REGION_COUNTS was emitted
    assert "FINAL_REGION_COUNTS:" in all_logs
    assert "DOMESTIC=5" in all_logs
    assert "INDIA=5" in all_logs
    assert "INTERNATIONAL=5" in all_logs

    assert len(dom_pool) == 5
    assert len(ind_pool) == 5
    assert len(intl_pool) == 5
    assert sufficient is True

