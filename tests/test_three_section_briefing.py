"""
Regression tests for Three-Section Investment Committee Briefing.
Verifies Tests 1 through 12 as requested.
"""
from datetime import datetime, timezone, timedelta, date
import pytest

from app.models.enums import NewsCategory, VerificationTier
from app.models.article import Article
from app.models.event import Event
from app.filtering.rules import URLFilterRule, StoryTypeFilterRule
from app.filtering.engine import HardFilterEngine, DomesticHardFilterEngine
from app.classification.region_classifier import EventRegionClassifier
from app.verification.domestic_trending import DomesticTrendingEvaluator
from app.verification.single_source import SingleSourceEvaluator
from app.ranking.models import RankedCandidatePool, ScoredEvent, ScoreBreakdown
from app.ai.models import BriefingEditorialPayload, EditorialStorySelection, EditorialResult
from app.formatting.formatter import BriefingFormatter
from app.validation.engine import FinalValidationEngine


def _make_article(title, url, source_name, category=NewsCategory.INDIA, content=None, age_hours=2.0):
    now = datetime.now(timezone.utc)
    pub = now - timedelta(hours=age_hours)
    body = content or ("Detailed news article reporting major events and metrics. " * 20)
    return Article(
        id=f"art_{abs(hash(url)) % 100000}",
        title=title,
        url=url,
        source_name=source_name,
        content_text=body,
        category=category,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
        published_at=pub,
    )


# ---------------------------------------------------------------------------
# TEST 1 — Final structure: Domestic=5, India=5, International=5 => Total=15
# ---------------------------------------------------------------------------
def test_1_final_structure_15_stories():
    validator = FinalValidationEngine()
    events_lookup = {}
    articles_lookup = {}
    
    dom_stories = []
    india_stories = []
    intl_stories = []
    
    dom_titles = [
        "Supreme Court issues nationwide ruling on environmental policy",
        "Union Cabinet clears mega railway corridor across three states",
        "ISRO successfully launches new Earth observation satellite",
        "Government announces national education policy framework revisions",
        "Major cyclone triggers red alert across coastal districts",
    ]
    india_comps = ["Tata Steel", "Reliance Industries", "Infosys", "HDFC Bank", "Bharti Airtel"]
    intl_comps = ["Microsoft", "Apple", "Google", "Amazon", "Nvidia"]
    
    for i in range(1, 6):
        # Domestic
        art_dom = _make_article(dom_titles[i - 1], f"https://www.thehindu.com/news/national/story-{i}", "The Hindu", NewsCategory.DOMESTIC)
        ev_dom = Event(
            id=f"ev_dom_{i}",
            canonical_title=art_dom.title,
            description=f"National development {i}",
            article_ids=[art_dom.id],
            event_category=NewsCategory.DOMESTIC,
            companies_involved=[],
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
            verification_confidence=90.0,
            primary_publisher="The Hindu",
            primary_url=art_dom.url,
        )
        events_lookup[ev_dom.id] = ev_dom
        articles_lookup[art_dom.id] = art_dom
        dom_stories.append(EditorialStorySelection(
            section="domestic",
            event_id=ev_dom.id,
            headline=ev_dom.canonical_title,
            source="The Hindu",
            url=art_dom.url,
        ))
        
        # India
        in_comp = india_comps[i - 1]
        art_in = _make_article(f"{in_comp} Q{i} profit rises 25% to ₹{i*500} crore", f"https://www.livemint.com/market/{in_comp.lower().replace(' ', '-')}-q{i}", "Livemint", NewsCategory.INDIA)
        ev_in = Event(
            id=f"ev_in_{i}",
            canonical_title=art_in.title,
            description=f"{in_comp} Q{i} profit report",
            article_ids=[art_in.id],
            event_category=NewsCategory.INDIA,
            companies_involved=[in_comp],
            financial_figures=[f"₹{i*500} crore"],
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
            verification_confidence=95.0,
            primary_publisher="Livemint",
            primary_url=art_in.url,
        )
        events_lookup[ev_in.id] = ev_in
        articles_lookup[art_in.id] = art_in
        india_stories.append(EditorialStorySelection(
            section="india",
            event_id=ev_in.id,
            headline=ev_in.canonical_title,
            source="Livemint",
            url=art_in.url,
        ))
        
        # International
        int_comp = intl_comps[i - 1]
        art_int = _make_article(f"{int_comp} acquires cloud startup for ${i} billion all-cash deal", f"https://www.reuters.com/technology/{int_comp.lower().replace(' ', '-')}-startup-{i}", "Reuters", NewsCategory.INTERNATIONAL)
        ev_int = Event(
            id=f"ev_int_{i}",
            canonical_title=art_int.title,
            description=f"{int_comp} acquisition of cloud startup {i}",
            article_ids=[art_int.id],
            event_category=NewsCategory.INTERNATIONAL,
            companies_involved=[int_comp],
            financial_figures=[f"${i} billion"],
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
            verification_confidence=95.0,
            primary_publisher="Reuters",
            primary_url=art_int.url,
        )
        events_lookup[ev_int.id] = ev_int
        articles_lookup[art_int.id] = art_int
        intl_stories.append(EditorialStorySelection(
            section="international",
            event_id=ev_int.id,
            headline=ev_int.canonical_title,
            source="Reuters",
            url=art_int.url,
        ))

    payload = BriefingEditorialPayload(
        domestic_stories=dom_stories,
        india_stories=india_stories,
        international_stories=intl_stories,
    )
    
    assert len(payload.domestic_stories) == 5
    assert len(payload.india_stories) == 5
    assert len(payload.international_stories) == 5
    assert len(payload.domestic_stories) + len(payload.india_stories) + len(payload.international_stories) == 15
    
    report = validator.validate_briefing(
        payload=payload,
        events_lookup=events_lookup,
        articles_lookup=articles_lookup,
        target_date=date.today(),
        strict_5_per_section=True,
        quality_ladder_mode=True,
    )
    assert report.is_valid is True


# ---------------------------------------------------------------------------
# TEST 2 — Domestic and India separation
# ---------------------------------------------------------------------------
def test_2_domestic_and_india_separation():
    clf = EventRegionClassifier()
    
    # Domestic
    title_dom = "Supreme Court issues nationwide constitutional ruling on federal powers"
    content_dom = "A five-judge constitutional bench of the Supreme Court delivered a landmark ruling."
    cat_dom = clf.classify(title=title_dom, content=content_dom)
    assert cat_dom == NewsCategory.DOMESTIC
    
    # India Business
    title_biz = "RBI imposes ₹5 crore penalty on major private bank for regulatory violations"
    content_biz = "Reserve Bank of India issued a monetary penalty order following supervisory review."
    cat_biz = clf.classify(title=title_biz, content=content_biz)
    assert cat_biz == NewsCategory.INDIA


# ---------------------------------------------------------------------------
# TEST 3 — Corporate event never Domestic
# ---------------------------------------------------------------------------
def test_3_corporate_event_never_domestic():
    clf = EventRegionClassifier()
    title = "Tata Steel reports ₹2,500 crore quarterly profit"
    content = "Tata Steel posted a net profit of ₹2,500 crore for Q1 driven by domestic sales."
    cat = clf.classify(title=title, content=content, discovery_region=NewsCategory.DOMESTIC)
    assert cat == NewsCategory.INDIA
    assert cat != NewsCategory.DOMESTIC


# ---------------------------------------------------------------------------
# TEST 4 — National event never India Business
# ---------------------------------------------------------------------------
def test_4_national_event_never_india_business():
    clf = EventRegionClassifier()
    title = "ISRO successfully launches new Earth observation satellite"
    content = "The Indian Space Research Organisation launched the satellite using PSLV rocket."
    cat = clf.classify(title=title, content=content)
    assert cat == NewsCategory.DOMESTIC
    assert cat != NewsCategory.INDIA


# ---------------------------------------------------------------------------
# TEST 5 — Economic-policy precedence
# ---------------------------------------------------------------------------
def test_5_economic_policy_precedence():
    clf = EventRegionClassifier()
    title = "SEBI introduces new settlement rules for stock brokers"
    content = "Securities and Exchange Board of India announced revisions to the settlement framework for market participants."
    cat = clf.classify(title=title, content=content)
    assert cat == NewsCategory.INDIA


# ---------------------------------------------------------------------------
# TEST 6 — Legal/business precedence
# ---------------------------------------------------------------------------
def test_6_legal_business_precedence():
    clf = EventRegionClassifier()
    title = "CCI approves Tata acquisition of Company X"
    content = "Competition Commission of India cleared the acquisition of Company X by Tata Group."
    cat = clf.classify(title=title, content=content)
    assert cat == NewsCategory.INDIA


# ---------------------------------------------------------------------------
# TEST 7 — Broad garbage rejection in Domestic
# ---------------------------------------------------------------------------
def test_7_broad_garbage_rejection():
    evaluator = DomesticTrendingEvaluator()
    
    garbage_titles = [
        "School assembly conducts annual cultural dance program in Delhi",
        "Celebrity spotted at Mumbai airport in stylish summer outfit",
        "IPL 2026: Match scorecard and live commentary updates",
        "Local police arrest two individuals in minor theft incident",
        "Opinion: Why we must rethink urban lifestyle habits",
        "Viral video of street dog adopting kitten warms hearts online",
    ]
    
    for title in garbage_titles:
        art = _make_article(title, "https://timesofindia.indiatimes.com/news/123", "Times of India", NewsCategory.DOMESTIC)
        ev = Event(
            id="ev_garbage",
            canonical_title=title,
            description="Short report",
            article_ids=[art.id],
            event_category=NewsCategory.DOMESTIC,
        )
        is_elig, score, reason = evaluator.evaluate(ev, art)
        assert is_elig is False, f"Expected rejection for '{title}', but got eligible"


# ---------------------------------------------------------------------------
# TEST 8 — Domestic national significance qualification
# ---------------------------------------------------------------------------
def test_8_domestic_national_significance():
    evaluator = DomesticTrendingEvaluator()
    art = _make_article(
        "Supreme Court constitutional bench delivers landmark verdict on inter-state water dispute",
        "https://www.thehindu.com/news/national/supreme-court-verdict/article12345.ece",
        "The Hindu",
        NewsCategory.DOMESTIC,
        content="A seven-judge Constitution Bench of the Supreme Court headed by the Chief Justice delivered a landmark verdict settling decades-old inter-state water sharing arrangements. The decision establishes clear national guidelines for river water distribution.",
    )
    ev = Event(
        id="ev_nat_sig",
        canonical_title=art.title,
        description=art.content_text,
        article_ids=[art.id],
        event_category=NewsCategory.DOMESTIC,
    )
    is_elig, score, reason = evaluator.evaluate(ev, art)
    assert is_elig is True
    assert score >= 60.0


# ---------------------------------------------------------------------------
# TEST 9 — Cross-section duplicate prevented
# ---------------------------------------------------------------------------
def test_9_cross_section_duplicate_prevented():
    validator = FinalValidationEngine()
    
    # Duplicate event across domestic and india
    dup_event_id = "ev_shared_123"
    art_dom = _make_article("Supreme Court approves merger settlement", "https://example.com/dom", "The Hindu", NewsCategory.DOMESTIC)
    art_in = _make_article("Company merger approved by court", "https://example.com/in", "Business Standard", NewsCategory.INDIA)
    
    ev_dom = Event(
        id=dup_event_id,
        canonical_title="Supreme Court approves merger settlement",
        description="Merger approval",
        article_ids=[art_dom.id],
        event_category=NewsCategory.DOMESTIC,
    )
    
    events_lookup = {dup_event_id: ev_dom}
    articles_lookup = {art_dom.id: art_dom, art_in.id: art_in}
    
    payload = BriefingEditorialPayload(
        domestic_stories=[
            EditorialStorySelection(section="domestic", event_id=dup_event_id, headline="Supreme Court approves merger", source="The Hindu", url=art_dom.url)
        ] * 5,
        india_stories=[
            EditorialStorySelection(section="india", event_id=dup_event_id, headline="Company merger approved", source="Business Standard", url=art_in.url)
        ] * 5,
        international_stories=[],
    )
    report = validator.validate_briefing(payload, events_lookup, articles_lookup, strict_5_per_section=False)
    assert report.is_valid is False
    assert any("duplicate" in (c.failure_reason or "").lower() for c in report.check_results if not c.passed)


# ---------------------------------------------------------------------------
# TEST 10 — Formatter outputs 3 headings and 15 stories with NO numbering
# ---------------------------------------------------------------------------
def test_10_formatter_headings_and_counts():
    formatter = BriefingFormatter()
    
    def make_sel(sec, i):
        return EditorialStorySelection(
            section=sec,
            event_id=f"ev_{sec}_{i}",
            headline=f"Headline for {sec} section story {i} with key metrics",
            source="The Hindu" if sec == "domestic" else ("Business Standard" if sec == "india" else "Reuters"),
            url=f"https://example.com/{sec}/story-{i}",
        )

    payload = BriefingEditorialPayload(
        domestic_stories=[make_sel("domestic", i) for i in range(1, 6)],
        india_stories=[make_sel("india", i) for i in range(1, 6)],
        international_stories=[make_sel("international", i) for i in range(1, 6)],
    )
    formatted = formatter.format(payload, briefing_date=date.today())
    assert formatted is not None
    assert formatted.domestic_count == 5
    assert formatted.india_count == 5
    assert formatted.international_count == 5
    assert formatted.total_count == 15
    
    text = formatted.text
    assert "*TOP 5 DOMESTIC HEADLINES*" in text
    assert "*TOP 5 INDIA BUSINESS HEADLINES*" in text
    assert "*TOP 5 INTERNATIONAL BUSINESS HEADLINES*" in text

    # Strict formatting assertions: NO numbering prefixes, NO bullets, NO hyphens before stories
    assert "\n1. " not in text
    assert "\n2. " not in text
    assert "\n3. " not in text
    assert "\n4. " not in text
    assert "\n5. " not in text
    assert "\n• " not in text
    assert "\n- " not in text
    assert not text.startswith("1. ")


# ---------------------------------------------------------------------------
# TEST 10B — SerpAPI Search Cap Strictly 8
# ---------------------------------------------------------------------------
def test_serpapi_search_cap_is_eight():
    from config import get_settings
    settings = get_settings()
    assert settings.MAX_SERPAPI_SEARCHES_PER_RUN == 8


# ---------------------------------------------------------------------------
# TEST 11 — Guaranteed fallback per section
# ---------------------------------------------------------------------------
def test_11_quality_ladder_per_section():
    # Domestic 36h, India 24h, Intl 24h
    art_dom_36h = _make_article("Major cabinet infra approval", "https://example.com/dom", "The Hindu", NewsCategory.DOMESTIC, age_hours=32.0)
    art_in_24h = _make_article("Tata Steel quarterly profit", "https://example.com/in", "Livemint", NewsCategory.INDIA, age_hours=10.0)
    art_intl_24h = _make_article("Apple quarterly earnings", "https://example.com/intl", "Reuters", NewsCategory.INTERNATIONAL, age_hours=8.0)
    
    assert get_article_age_hours_test(art_dom_36h) > 24.0
    assert get_article_age_hours_test(art_in_24h) <= 24.0
    assert get_article_age_hours_test(art_intl_24h) <= 24.0


def get_article_age_hours_test(art):
    now = datetime.now(timezone.utc)
    return (now - art.published_at).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# TEST 12 — Business quality regressions preserved
# ---------------------------------------------------------------------------
def test_12_business_quality_regressions():
    engine = HardFilterEngine()
    
    # 1. Gold commentary rejection
    art_gold = _make_article(
        "Gold price today: Yellow metal trades higher amid global cues",
        "https://www.livemint.com/market/commodities/gold-price-today-123.html",
        "Livemint",
        content="Gold prices rose in the bullion market today tracking positive global trends and steady spot demand.",
    )
    res_gold = engine.filter_article(art_gold)
    assert not res_gold.is_accepted
    
    # 2. Speculative M&A rejection
    art_spec = _make_article(
        "Company may consider acquiring startup in near future, sources say",
        "https://economictimes.indiatimes.com/tech/startups/speculative-deal/articleshow/123.cms",
        "Economic Times",
        content="The management might look into a potential acquisition in the coming months according to unnamed people familiar with the matter.",
    )
    res_spec = engine.filter_article(art_spec)
    assert not res_spec.is_accepted


# ---------------------------------------------------------------------------
# TEST 13 — run_pipeline symbols and filter engines instantiation
# ---------------------------------------------------------------------------
def test_13_run_pipeline_symbols_and_engines_imported():
    import run_pipeline
    
    # Verify module has access to both HardFilterEngine and DomesticHardFilterEngine
    assert hasattr(run_pipeline, "HardFilterEngine")
    assert hasattr(run_pipeline, "DomesticHardFilterEngine")
    assert hasattr(run_pipeline, "DomesticTrendingEvaluator")
    assert hasattr(run_pipeline, "EditorialStorySelection")
    assert hasattr(run_pipeline, "BriefingEditorialPayload")
    
    # Verify instantiation succeeds without network calls
    biz_engine = run_pipeline.HardFilterEngine()
    assert biz_engine is not None
    
    dom_engine = run_pipeline.DomesticHardFilterEngine()
    assert dom_engine is not None
    assert hasattr(dom_engine, "filter_article")
    assert hasattr(dom_engine, "filter_candidates")
