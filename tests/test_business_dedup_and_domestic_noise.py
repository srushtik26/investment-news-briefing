"""
Tests for Business Semantic Deduplication, Company Normalization, International Earnings Deduplication,
Sufficiency/Refill Logic, and Domestic Exam-Prep Noise Filtering.
"""

from datetime import datetime, timezone
import pytest

from app.deduplication.fingerprint import normalize_entity_name
from app.models.article import Article
from app.models.enums import NewsCategory, VerificationTier
from app.models.event import Event
from app.verification.domestic_trending import DomesticTrendingEvaluator
from app.verification.verifier import TwoSourceVerifier


def test_regression_test_a_dmart_same_event():
    """TEST A: Capital Group D-Mart stake sale vs DMart block deal share fall recognized as SAME event."""
    verifier = TwoSourceVerifier()

    art1 = Article(
        id="dmart_1",
        title="Capital Group sells 1% stake in D-Mart for Rs 2,537 crore",
        url="https://thehindu.com/business/capital-group-dmart-stake-sale",
        source_name="The Hindu",
        category=NewsCategory.INDIA,
        content_text="Capital Group offloaded a 1 percent equity stake in Avenue Supermarts (D-Mart) through a block deal valued at Rs 2,537 crore on Wednesday.",
    )
    art2 = Article(
        id="dmart_2",
        title="DMart shares fall after 1.06% equity stake sold in block deal worth around Rs 2,600 crore",
        url="https://business-standard.com/markets/dmart-block-deal-shares-fall",
        source_name="Business Standard",
        category=NewsCategory.INDIA,
        content_text="Shares of DMart operator Avenue Supermarts fell 3% after a 1.06% stake changed hands in a large block deal worth approximately Rs 2,600 crore.",
    )

    is_same, score, reason = verifier.is_same_underlying_event(art1, art2)
    assert is_same, f"Expected same underlying event, got False. Reason: {reason}"
    assert score >= 0.70


def test_regression_test_b_sufficiency_after_collapse():
    """TEST B: India pool with 5 raw candidates where 2 are same D-Mart event yields selectable count = 4."""
    verifier = TwoSourceVerifier()

    articles_map = {}
    ev1 = Event(
        id="ev_horizon",
        canonical_title="Horizon Aerospace bags $150M defence order",
        description="Defence contract details",
        article_ids=["art_horizon"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
    )
    articles_map["art_horizon"] = Article(
        id="art_horizon",
        title="Horizon Aerospace bags $150M defence order",
        url="https://thehindu.com/horizon",
        source_name="The Hindu",
        category=NewsCategory.INDIA,
        content_text="Horizon Aerospace received a major defence supply contract.",
    )

    ev2 = Event(
        id="ev_zerodha",
        canonical_title="Zerodha reports FY26 profit surge of 25%",
        description="Zerodha annual results",
        article_ids=["art_zerodha"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
    )
    articles_map["art_zerodha"] = Article(
        id="art_zerodha",
        title="Zerodha reports FY26 profit surge of 25%",
        url="https://business-standard.com/zerodha",
        source_name="Business Standard",
        category=NewsCategory.INDIA,
        content_text="Zerodha reported FY26 financial results with profit growth.",
    )

    ev3 = Event(
        id="ev_vikas",
        canonical_title="Vikas Ecotech secures Rs 100 crore debt restructuring",
        description="Vikas Ecotech corporate restructuring",
        article_ids=["art_vikas"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
    )
    articles_map["art_vikas"] = Article(
        id="art_vikas",
        title="Vikas Ecotech secures Rs 100 crore debt restructuring",
        url="https://economictimes.com/vikas",
        source_name="Economic Times",
        category=NewsCategory.INDIA,
        content_text="Vikas Ecotech finalized debt restructuring agreement.",
    )

    # 2 D-Mart duplicates
    ev4_dmart_a = Event(
        id="ev_dmart_a",
        canonical_title="Capital Group sells 1% stake in D-Mart for Rs 2,537 crore",
        description="D-Mart stake sale",
        article_ids=["art_dmart_a"],
        companies_involved=["Capital Group", "D-Mart"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
    )
    articles_map["art_dmart_a"] = Article(
        id="art_dmart_a",
        title="Capital Group sells 1% stake in D-Mart for Rs 2,537 crore",
        url="https://thehindu.com/dmart-a",
        source_name="The Hindu",
        category=NewsCategory.INDIA,
        content_text="Capital Group offloaded a 1% stake in D-Mart.",
    )

    ev5_dmart_b = Event(
        id="ev_dmart_b",
        canonical_title="DMart shares fall after 1.06% equity stake sold in block deal worth around Rs 2,600 crore",
        description="DMart block deal impact",
        article_ids=["art_dmart_b"],
        companies_involved=["DMart"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
    )
    articles_map["art_dmart_b"] = Article(
        id="art_dmart_b",
        title="DMart shares fall after 1.06% equity stake sold in block deal worth around Rs 2,600 crore",
        url="https://business-standard.com/dmart-b",
        source_name="Business Standard",
        category=NewsCategory.INDIA,
        content_text="DMart operator Avenue Supermarts saw block deal transaction.",
    )

    verified_events = [ev1, ev2, ev3, ev4_dmart_a, ev5_dmart_b]

    # Emulate get_final_selectable_unique_events
    selectable = []
    seen_comps = set()
    from app.models.entity_sanitizer import sanitize_company_entities
    from app.verification.query_builder import EventQueryBuilder

    for cand in verified_events:
        cand_art = articles_map[cand.article_ids[0]]
        is_dup = False
        for sel in selectable:
            sel_art = articles_map[sel.article_ids[0]]
            if verifier.is_same_underlying_event(cand_art, sel_art)[0]:
                is_dup = True
                break
        if is_dup:
            continue
        cand_entities = EventQueryBuilder.extract_entities(cand_art, event=cand)
        cand_comps = sanitize_company_entities((cand.companies_involved or []) + cand_entities, publisher=cand_art.source_name)
        norm_comps = {normalize_entity_name(c) for c in cand_comps if normalize_entity_name(c) not in ("unspecified_entity", "")}
        if norm_comps and norm_comps.intersection(seen_comps):
            continue
        selectable.append(cand)
        seen_comps.update(norm_comps)

    assert len(selectable) == 4, f"Expected 4 selectable events after collapsing DMart duplicates, got {len(selectable)}"


def test_regression_test_c_replacement_and_sufficiency():
    """TEST C: Starting from 4 selectable India events, adding a new 5th distinct company reaches 5/5."""
    verifier = TwoSourceVerifier()
    articles_map = {}

    # 4 distinct India events (after DMart collapse)
    ev_list = [
        Event(id="ev_1", canonical_title="Horizon Aerospace bags $150M defence order", description="Horizon defence order", article_ids=["art_1"], event_category=NewsCategory.INDIA, verification_tier=VerificationTier.TWO_SOURCE_VERIFIED, companies_involved=["Horizon Aerospace"]),
        Event(id="ev_2", canonical_title="Zerodha reports FY26 profit surge of 25%", description="Zerodha FY26 profit", article_ids=["art_2"], event_category=NewsCategory.INDIA, verification_tier=VerificationTier.TWO_SOURCE_VERIFIED, companies_involved=["Zerodha"]),
        Event(id="ev_3", canonical_title="Vikas Ecotech secures Rs 100 crore debt restructuring", description="Vikas debt restructuring", article_ids=["art_3"], event_category=NewsCategory.INDIA, verification_tier=VerificationTier.TWO_SOURCE_VERIFIED, companies_involved=["Vikas Ecotech"]),
        Event(id="ev_4", canonical_title="Capital Group sells 1% stake in D-Mart for Rs 2,537 crore", description="D-Mart stake sale", article_ids=["art_4"], event_category=NewsCategory.INDIA, verification_tier=VerificationTier.TWO_SOURCE_VERIFIED, companies_involved=["Capital Group", "D-Mart"]),
    ]
    for ev in ev_list:
        articles_map[ev.article_ids[0]] = Article(id=ev.article_ids[0], title=ev.canonical_title, url=f"https://example.com/{ev.id}", source_name="The Hindu", category=NewsCategory.INDIA, content_text=ev.canonical_title)

    # 5th distinct story: Tata Motors
    ev_5 = Event(id="ev_5", canonical_title="Tata Motors signs $1B electric bus supply agreement with state government", description="Tata Motors contract", article_ids=["art_5"], event_category=NewsCategory.INDIA, verification_tier=VerificationTier.TWO_SOURCE_VERIFIED, companies_involved=["Tata Motors"])
    articles_map["art_5"] = Article(id="art_5", title=ev_5.canonical_title, url="https://example.com/tata", source_name="Economic Times", category=NewsCategory.INDIA, content_text=ev_5.canonical_title)
    ev_list.append(ev_5)

    from app.models.entity_sanitizer import sanitize_company_entities
    from app.verification.query_builder import EventQueryBuilder

    selectable = []
    seen_comps = set()
    for cand in ev_list:
        cand_art = articles_map[cand.article_ids[0]]
        is_dup = False
        for sel in selectable:
            sel_art = articles_map[sel.article_ids[0]]
            if verifier.is_same_underlying_event(cand_art, sel_art)[0]:
                is_dup = True
                break
        if is_dup:
            continue
        cand_entities = EventQueryBuilder.extract_entities(cand_art, event=cand)
        cand_comps = sanitize_company_entities((cand.companies_involved or []) + cand_entities, publisher=cand_art.source_name)
        norm_comps = {normalize_entity_name(c) for c in cand_comps if normalize_entity_name(c) not in ("unspecified_entity", "")}
        if norm_comps and norm_comps.intersection(seen_comps):
            continue
        selectable.append(cand)
        seen_comps.update(norm_comps)

    assert len(selectable) == 5


def test_regression_test_d_company_name_normalization():
    """TEST D: D-Mart, DMart, D Mart, Avenue Supermarts normalize to the same canonical identity."""
    variants = ["D-Mart", "DMart", "D Mart", "Avenue Supermarts", "Avenue Supermart Limited"]
    normalized = {normalize_entity_name(v) for v in variants}
    assert len(normalized) == 1
    assert "avenue_supermarts" in normalized or "dmart" in normalized


def test_regression_test_e_different_companies():
    """TEST E: DMart block deal vs Zerodha FY26 results are distinct events."""
    verifier = TwoSourceVerifier()

    art_dmart = Article(
        id="art_dmart",
        title="Capital Group sells 1% stake in D-Mart for Rs 2,537 crore",
        url="https://thehindu.com/dmart",
        source_name="The Hindu",
        category=NewsCategory.INDIA,
        content_text="Capital Group offloaded stake in Avenue Supermarts.",
    )
    art_zerodha = Article(
        id="art_zerodha",
        title="Zerodha FY26 results: Net profit rises 25% to Rs 2,900 crore",
        url="https://business-standard.com/zerodha",
        source_name="Business Standard",
        category=NewsCategory.INDIA,
        content_text="Zerodha reported annual financial performance.",
    )

    is_same, score, reason = verifier.is_same_underlying_event(art_dmart, art_zerodha)
    assert not is_same
    assert "ENTITY_MISMATCH" in reason or "EVENT_TYPE_MISMATCH" in reason


def test_regression_test_f_nvidia_same_earnings_event():
    """TEST F: 3 articles on Nvidia Q2 earnings results collapse to ONE underlying earnings event."""
    verifier = TwoSourceVerifier()

    art1 = Article(
        id="nv_1",
        title="Strong AI chip demand powers Nvidia's Q2 results with record revenue",
        url="https://reuters.com/nvidia-q2-results",
        source_name="Reuters",
        category=NewsCategory.INTERNATIONAL,
        content_text="Nvidia reported record second-quarter earnings driven by high data center AI processor shipments.",
    )
    art2 = Article(
        id="nv_2",
        title="Nvidia's quarterly revenue doubles to nearly $100bn amid AI boom",
        url="https://ft.com/nvidia-revenue-doubles",
        source_name="Financial Times",
        category=NewsCategory.INTERNATIONAL,
        content_text="Nvidia quarterly revenue surged as tech giants expand artificial intelligence infrastructure.",
    )
    art3 = Article(
        id="nv_3",
        title="Nvidia issues year-ahead guidance and forecast during Q2 results announcement",
        url="https://wsj.com/nvidia-guidance-q2",
        source_name="Wall Street Journal",
        category=NewsCategory.INTERNATIONAL,
        content_text="During its Q2 earnings conference, Nvidia provided upbeat forecast for full-year revenue.",
    )

    # 1 vs 2
    is_same_1_2, _, _ = verifier.is_same_underlying_event(art1, art2)
    assert is_same_1_2, "Expected Nvidia Q2 results and revenue doubling to match as same earnings event"

    # 1 vs 3
    is_same_1_3, _, _ = verifier.is_same_underlying_event(art1, art3)
    assert is_same_1_3, "Expected Nvidia Q2 results and Q2 guidance to match as same earnings event"


def test_regression_test_g_distinct_nvidia_events():
    """TEST G: Nvidia Q2 earnings vs a separate Nvidia acquisition are distinct events."""
    verifier = TwoSourceVerifier()

    art_earnings = Article(
        id="nv_earn",
        title="Nvidia Q2 revenue beats expectations with $30 billion sales",
        url="https://reuters.com/nvidia-q2",
        source_name="Reuters",
        category=NewsCategory.INTERNATIONAL,
        content_text="Nvidia reported quarterly earnings for Q2.",
    )
    art_acquisition = Article(
        id="nv_acq",
        title="Nvidia completes acquisition of AI software startup Run:ai for $700 million",
        url="https://bloomberg.com/nvidia-acquires-runai",
        source_name="Bloomberg",
        category=NewsCategory.INTERNATIONAL,
        content_text="Nvidia finalized its buyout of workload management platform Run:ai.",
    )

    is_same, score, reason = verifier.is_same_underlying_event(art_earnings, art_acquisition)
    assert not is_same, f"Expected distinct events, got is_same=True ({reason})"


def test_regression_test_h_upsc_answer_practice_noise():
    """TEST H: UPSC Mains Answer Practice study content is rejected as noise."""
    evaluator = DomesticTrendingEvaluator()

    art_prep = Article(
        id="art_prep_1",
        title="UPSC Mains Answer Practice — GS 2 Week 169: India's engagement with Central Asia",
        url="https://indianexpress.com/article/upsc-current-affairs/upsc-mains-answer-practice-gs-2/",
        source_name="Indian Express",
        category=NewsCategory.DOMESTIC,
        content_text="Practice question for UPSC aspirants: Discuss India's strategic outreach in Central Asia.",
    )
    ev_prep = Event(
        id="ev_prep_1",
        canonical_title=art_prep.title,
        description=art_prep.content_text,
        article_ids=[art_prep.id],
        event_category=NewsCategory.DOMESTIC,
    )

    is_elig, score, reason = evaluator.evaluate(ev_prep, art_prep)
    assert not is_elig, "UPSC Mains Answer Practice must be rejected"
    assert "REJECT_NOISE" in reason or "upsc mains answer practice" in reason.lower()


def test_regression_test_i_real_upsc_news():
    """TEST I: Legitimate national news about UPSC policy changes is not rejected."""
    evaluator = DomesticTrendingEvaluator()

    now = datetime.now(timezone.utc)
    art_news = Article(
        id="art_upsc_news",
        title="UPSC announces major change to Civil Services examination rules and age limits",
        url="https://thehindu.com/news/national/upsc-exam-rules-change",
        source_name="The Hindu",
        published_at=now,
        category=NewsCategory.DOMESTIC,
        content_text="The Union Public Service Commission on Tuesday notified revised eligibility rules for the upcoming Civil Services examination.",
    )
    ev_news = Event(
        id="ev_upsc_news",
        canonical_title=art_news.title,
        description=art_news.content_text,
        article_ids=[art_news.id],
        event_category=NewsCategory.DOMESTIC,
    )

    is_elig, score, reason = evaluator.evaluate(ev_news, art_news, now_utc=now)
    assert is_elig, f"Legitimate UPSC news must qualify. Got score={score}, reason={reason}"
    assert score >= 60.0


def test_regression_test_j_obc_explained_story():
    """TEST J: OBC creamy-layer explained story based on concrete Supreme Court action is eligible."""
    evaluator = DomesticTrendingEvaluator()

    now = datetime.now(timezone.utc)
    art_obc = Article(
        id="art_obc",
        title="OBC creamy-layer income test: What did the Supreme Court rule, and why is Centre seeking clarification? | Explained",
        url="https://indianexpress.com/article/explained/obc-creamy-layer-income-test-supreme-court-explained/",
        source_name="Indian Express",
        published_at=now,
        category=NewsCategory.DOMESTIC,
        content_text="The Supreme Court ruled on the creamy-layer criteria for OBC reservations, leading the Centre to file a clarification plea.",
    )
    ev_obc = Event(
        id="ev_obc",
        canonical_title=art_obc.title,
        description=art_obc.content_text,
        article_ids=[art_obc.id],
        event_category=NewsCategory.DOMESTIC,
    )

    is_elig, score, reason = evaluator.evaluate(ev_obc, art_obc, now_utc=now)
    assert is_elig, f"OBC creamy-layer explained story must qualify. Got score={score}, reason={reason}"
    assert score >= 60.0
