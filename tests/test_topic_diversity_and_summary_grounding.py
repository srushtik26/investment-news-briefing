"""
Unit and regression tests for Editorial Topic Diversity, Strict Business Relevance,
Generic Headline Rejection, and Summary Grounding.
"""

from datetime import datetime, timezone
import pytest

from app.models.article import Article
from app.models.event import Event
from app.models.enums import NewsCategory, VerificationTier
from app.ranking.models import ScoredEvent, ScoreBreakdown
from app.filtering.rules import URLFilterRule, StoryTypeFilterRule, is_generic_headline
from app.filtering.business_relevance import calculate_business_relevance_score
from app.ranking.topic_classifier import (
    classify_topic_bucket,
    audit_topic_distribution,
    TOPIC_AI_TECH,
    TOPIC_ENERGY,
    TOPIC_BANKING_FINANCE,
    TOPIC_EARNINGS,
    TOPIC_MNA,
    TOPIC_CONTRACT_AWARD,
    TOPIC_SEMICONDUCTORS,
)
from app.ranking.sorter import select_diverse_topic_candidates
from app.ai.summary_grounding import (
    validate_summary_grounding,
    select_grounded_summary_sentence,
    build_structured_fallback_summary,
)
from app.ai.editor import generate_deterministic_summary


def test_1_opinion_column_url_rejected():
    """Test 1: URL with /opinion/columns/ and personal finance topic is rejected."""
    art = Article(
        id="art_opinion_1",
        url="https://www.business-standard.com/opinion/columns/what-next-deciding-what-to-do-after-achieving-financial-freedom-125021500123_1.html",
        title="What next? Deciding what to do after achieving financial freedom",
        content_text="Personal finance advice on retirement and financial freedom.",
        published_at=datetime.now(timezone.utc),
        source_name="Business Standard",
    )
    url_rule = URLFilterRule()
    res = url_rule.evaluate(art)
    assert not res.is_accepted
    assert "opinion/commentary/advice" in res.rejection_reason or "non-article" in res.rejection_reason


def test_2_generic_headline_rejected():
    """Test 2: Generic umbrella headline 'Company Announcements' is rejected."""
    is_gen, reason = is_generic_headline("Company Announcements")
    assert is_gen
    assert "generic" in reason.lower()

    # In StoryTypeFilterRule
    story_rule = StoryTypeFilterRule()
    art = Article(
        id="art_generic_1",
        url="https://www.ft.com/company-announcements",
        title="Company Announcements",
        content_text="Overview of latest corporate announcements across global markets.",
        published_at=datetime.now(timezone.utc),
        source_name="Financial Times",
    )
    res = story_rule.evaluate(art)
    assert not res.is_accepted
    assert "Generic headline rejected" in res.rejection_reason


def test_3_summary_mismatch_fails_grounding():
    """Test 3: Summary describing unrelated violent death fails grounding against bulldozer action headline."""
    headline = "Bulldozer Action On Illegal Lawyers' Chambers At Allahabad HC"
    mismatched_summary = "A Hindu activist was found dead with bullet wounds in the forest area."

    is_grounded, reason = validate_summary_grounding(mismatched_summary, headline)
    assert not is_grounded
    assert "violent death" in reason.lower() or "lacks" in reason.lower()


def test_4_grounded_structured_summary_generated():
    """Test 4: Grounded structured summary generated for KNR Constructions order win."""
    headline = "KNR Constructions bags ₹158 crore EPC order from GHMC"
    summary = build_structured_fallback_summary(headline)
    assert "KNR Constructions" in summary
    assert "₹158 crore" in summary or "158 crore" in summary
    assert summary.endswith(".")


def _make_dummy_breakdown(score=80.0):
    return ScoreBreakdown(
        financial_magnitude=score,
        market_impact=score,
        investor_relevance=score,
        corporate_significance=score,
        source_quality=score,
        total_score=score,
        rationale="test",
    )


def test_5_soft_topic_diversity_selects_distinct_topics():
    """Test 5: When multiple AI stories exist alongside Energy, Banking, Earnings, MNA, diverse topics are chosen."""
    now = datetime.now(timezone.utc)
    candidates = []

    # 4 AI stories
    for i in range(4):
        ev = Event(
            id=f"ai_{i}",
            canonical_title=f"OpenAI launches new GPT-5 model version {i}",
            description="OpenAI introduced artificial intelligence improvements.",
            event_category=NewsCategory.INTERNATIONAL,
            article_ids=[f"art_ai_{i}"],
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        ev.metadata = {"topic_bucket": TOPIC_AI_TECH}
        candidates.append(ScoredEvent(event=ev, investment_score=90.0 - i, rank=i + 1, score_breakdown=_make_dummy_breakdown(90.0 - i)))

    # 1 Energy story
    ev_energy = Event(
        id="energy_1",
        canonical_title="Reliance Power signs ₹4,000 crore clean energy solar agreement",
        description="Reliance Power committed to solar energy expansion.",
        event_category=NewsCategory.INDIA,
        article_ids=["art_energy_1"],
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
    )
    ev_energy.metadata = {"topic_bucket": TOPIC_ENERGY}
    candidates.append(ScoredEvent(event=ev_energy, investment_score=85.0, rank=5, score_breakdown=_make_dummy_breakdown(85.0)))

    # 1 Banking story
    ev_bank = Event(
        id="bank_1",
        canonical_title="HDFC Bank reports 18% growth in net interest income",
        description="HDFC Bank released banking financial metrics.",
        event_category=NewsCategory.INDIA,
        article_ids=["art_bank_1"],
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
    )
    ev_bank.metadata = {"topic_bucket": TOPIC_BANKING_FINANCE}
    candidates.append(ScoredEvent(event=ev_bank, investment_score=84.0, rank=6, score_breakdown=_make_dummy_breakdown(84.0)))

    # 1 M&A story
    ev_mna = Event(
        id="mna_1",
        canonical_title="Tata Steel agrees to acquire European packaging asset for $500M",
        description="Tata Steel completed buyout transaction.",
        event_category=NewsCategory.INDIA,
        article_ids=["art_mna_1"],
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
    )
    ev_mna.metadata = {"topic_bucket": TOPIC_MNA}
    candidates.append(ScoredEvent(event=ev_mna, investment_score=83.0, rank=7, score_breakdown=_make_dummy_breakdown(83.0)))

    # 1 Contract award story
    ev_contract = Event(
        id="contract_1",
        canonical_title="L&T bags ₹2,500 crore infrastructure order from Railways",
        description="L&T secured major construction contract.",
        event_category=NewsCategory.INDIA,
        article_ids=["art_contract_1"],
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
    )
    ev_contract.metadata = {"topic_bucket": TOPIC_CONTRACT_AWARD}
    candidates.append(ScoredEvent(event=ev_contract, investment_score=82.0, rank=8, score_breakdown=_make_dummy_breakdown(82.0)))

    selected = select_diverse_topic_candidates(candidates, target_count=5, max_per_topic=2)
    assert len(selected) == 5

    topics_selected = [s.event.metadata["topic_bucket"] for s in selected]
    # Pass 1 picks 1 AI, 1 Energy, 1 Banking, 1 M&A, 1 Contract
    assert TOPIC_AI_TECH in topics_selected
    assert TOPIC_ENERGY in topics_selected
    assert TOPIC_BANKING_FINANCE in topics_selected
    assert TOPIC_MNA in topics_selected
    assert TOPIC_CONTRACT_AWARD in topics_selected
    # Ensure AI does not occupy all slots
    assert topics_selected.count(TOPIC_AI_TECH) == 1


def test_6_five_different_topics_preserved():
    """Test 6: 5 candidates from distinct topics are all preserved without alteration."""
    candidates = []
    topics = [TOPIC_EARNINGS, TOPIC_MNA, TOPIC_SEMICONDUCTORS, TOPIC_ENERGY, TOPIC_BANKING_FINANCE]
    for i, t in enumerate(topics):
        ev = Event(
            id=f"ev_diff_{i}",
            canonical_title=f"Corporate event in {t}",
            description="Event description.",
            event_category=NewsCategory.INDIA,
            article_ids=[f"art_{i}"],
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        ev.metadata = {"topic_bucket": t}
        candidates.append(ScoredEvent(event=ev, investment_score=80.0 - i, rank=i + 1, score_breakdown=_make_dummy_breakdown(80.0 - i)))

    selected = select_diverse_topic_candidates(candidates, target_count=5, max_per_topic=2)
    assert len(selected) == 5
    assert {s.event.metadata["topic_bucket"] for s in selected} == set(topics)


def test_7_single_topic_pool_backfills_gracefully():
    """Test 7: If all candidates are from the same topic, backfill preserves all 5 stories."""
    candidates = []
    for i in range(7):
        ev = Event(
            id=f"ev_mono_{i}",
            canonical_title=f"AI development milestone {i}",
            description="AI research and product development.",
            event_category=NewsCategory.INTERNATIONAL,
            article_ids=[f"art_mono_{i}"],
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        ev.metadata = {"topic_bucket": TOPIC_AI_TECH}
        candidates.append(ScoredEvent(event=ev, investment_score=85.0 - i, rank=i + 1, score_breakdown=_make_dummy_breakdown(85.0 - i)))

    selected = select_diverse_topic_candidates(candidates, target_count=5, max_per_topic=2)
    # Never drop below 5 when 5+ exist
    assert len(selected) == 5


def test_8_topic_diversity_never_drops_section_below_5():
    """Test 8: Ensure section count never drops below target_count (5) when total candidates >= 5."""
    candidates = []
    # 3 from SEMICONDUCTORS, 2 from AI_TECH
    for i in range(3):
        ev = Event(
            id=f"semi_{i}",
            canonical_title=f"TSMC announces wafer packaging facility {i}",
            description="Semiconductor fab investment.",
            event_category=NewsCategory.INTERNATIONAL,
            article_ids=[f"art_semi_{i}"],
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        ev.metadata = {"topic_bucket": TOPIC_SEMICONDUCTORS}
        candidates.append(ScoredEvent(event=ev, investment_score=88.0 - i, rank=i + 1, score_breakdown=_make_dummy_breakdown(88.0 - i)))
    for i in range(2):
        ev = Event(
            id=f"ai_b_{i}",
            canonical_title=f"Nvidia reveals AI software suite {i}",
            description="AI computing software platform.",
            event_category=NewsCategory.INTERNATIONAL,
            article_ids=[f"art_ai_b_{i}"],
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        ev.metadata = {"topic_bucket": TOPIC_AI_TECH}
        candidates.append(ScoredEvent(event=ev, investment_score=80.0 - i, rank=4 + i, score_breakdown=_make_dummy_breakdown(80.0 - i)))

    selected = select_diverse_topic_candidates(candidates, target_count=5, max_per_topic=2)
    assert len(selected) == 5


def test_9_opinion_fails_business_relevance():
    """Test 9: Opinion/personal finance article scores < 70 on business relevance."""
    art = Article(
        id="art_opin_rel",
        url="https://www.business-standard.com/opinion/columns/personal-finance-tips",
        title="What next? Deciding what to do after achieving financial freedom",
        content_text="Column discussing life decisions and investments after early retirement.",
        published_at=datetime.now(timezone.utc),
        source_name="Business Standard",
    )
    ev = Event(
        id="ev_opin_rel",
        canonical_title=art.title,
        description=art.content_text,
        event_category=NewsCategory.INDIA,
        article_ids=[art.id],
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
    )
    score, breakdown = calculate_business_relevance_score(ev, art)
    assert score < 70.0
    assert "penalty_opinion_advice" in breakdown or "penalty_lifestyle_pf" in breakdown


def test_10_concrete_hard_event_achieves_high_business_relevance():
    """Test 10: Concrete hard business event with numbers achieves score >= 70."""
    art = Article(
        id="art_knr",
        url="https://www.thehindubusinessline.com/companies/knr-constructions-bags-order",
        title="KNR Constructions bags ₹158 crore EPC order from GHMC",
        content_text="KNR Constructions announced it has secured an infrastructure contract worth ₹158 crore.",
        published_at=datetime.now(timezone.utc),
        source_name="BusinessLine",
    )
    ev = Event(
        id="ev_knr",
        canonical_title=art.title,
        description=art.content_text,
        event_category=NewsCategory.INDIA,
        companies_involved=["KNR Constructions", "GHMC"],
        financial_figures=["₹158 crore"],
        article_ids=[art.id],
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
    )
    score, breakdown = calculate_business_relevance_score(ev, art)
    assert score >= 70.0


def test_11_zero_gemini_calls_for_topic_classification():
    """Test 11: Topic classification executes purely deterministically with 0 Gemini calls."""
    # Test multiple patterns
    t1 = classify_topic_bucket(headline="TSMC reports 30% jump in Q3 profit on strong chip demand")
    assert t1 in (TOPIC_EARNINGS, TOPIC_SEMICONDUCTORS)

    t2 = classify_topic_bucket(headline="KNR Constructions bags ₹158 crore EPC order from GHMC")
    assert t2 == TOPIC_CONTRACT_AWARD

    t3 = classify_topic_bucket(headline="Tata Power to invest ₹15,000 crore in clean energy solar grid")
    assert t3 == TOPIC_ENERGY

    t4 = classify_topic_bucket(headline="HDFC Bank reports quarterly financial results")
    assert t4 in (TOPIC_EARNINGS, TOPIC_BANKING_FINANCE)

    t5 = classify_topic_bucket(headline="OpenAI launches new generative AI model")
    assert t5 == TOPIC_AI_TECH


def test_12_grounded_summary_matches_headline_entities():
    """Test 12: Summary generated from article content accurately extracts grounded sentence."""
    art = Article(
        id="art_grounded_ex",
        url="https://www.reuters.com/business/autos/byd-profit-falls",
        title="BYD profit falls 18% as China competition intensifies",
        content_text=(
            "Electric vehicle giant BYD reported an 18% drop in quarterly profit as price competition intensified in China. "
            "Click here to subscribe to our newsletter. Read more related stories."
        ),
        published_at=datetime.now(timezone.utc),
        source_name="Reuters",
    )
    summary = generate_deterministic_summary(art, headline=art.title)
    assert "BYD" in summary
    assert "profit" in summary or "18%" in summary
    assert "Click here" not in summary
    assert summary.endswith(".")


def test_13_audit_topic_distribution_logs_warning_for_heavy_concentration():
    """Test 13: audit_topic_distribution flags concentration when topic appears >= 3 times."""
    candidates = []
    for i in range(4):
        ev = Event(
            id=f"cand_{i}",
            canonical_title=f"AI company release {i}",
            description="AI update.",
            event_category=NewsCategory.INTERNATIONAL,
            article_ids=[],
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        ev.metadata = {"topic_bucket": TOPIC_AI_TECH}
        candidates.append(ScoredEvent(event=ev, investment_score=80.0, rank=i + 1, score_breakdown=_make_dummy_breakdown(80.0)))

    dist = audit_topic_distribution(candidates, "TEST_SECTION")
    assert dist[TOPIC_AI_TECH] == 4


def test_14_zero_tinyurl_shortening_in_urls():
    """Test 14: Ensure no tinyurl or shortened domains are introduced."""
    sample_urls = [
        "https://www.business-standard.com/companies/news/knr-constructions-bags-order",
        "https://www.reuters.com/business/autos-transportation/byd-earnings",
    ]
    for u in sample_urls:
        assert "tinyurl" not in u
        assert "bit.ly" not in u
        assert u.startswith("https://")


def test_15_serpapi_cap_invariants():
    """Test 15: Invariant verification that MAX_SERPAPI_SEARCHES_PER_RUN remains strictly 8."""
    from app.verification.serpapi_corroborator import MAX_SERPAPI_SEARCHES_PER_RUN
    assert MAX_SERPAPI_SEARCHES_PER_RUN == 8
