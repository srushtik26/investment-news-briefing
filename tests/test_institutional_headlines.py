"""
Tests for Institutional Investment Committee Headlines.

Verifies:
1. JV headline contains ownership/action.
2. Capex headline includes value + purpose.
3. Regulatory headline includes decision + market implication.
4. No unsupported inference (falls back to factual second clause if no implication).
5. Routine title copying is avoided (bad examples like M3M and Complete Sports transformed).
6. URLs and sources unchanged.
7. Story count unchanged (5/5/5).
"""

from datetime import datetime, timezone
import pytest

from app.models.article import Article
from app.models.event import Event
from app.models.enums import NewsCategory, VerificationTier
from app.ranking.models import ScoredEvent, RankedCandidatePool
from app.ai.editor import GeminiEditorialEngine
from app.ai.headline_synthesis import (
    synthesize_investment_headline,
    is_approved_institutional_headline,
)


def make_article(
    art_id: str,
    title: str,
    body: str,
    url: str = "https://example.com/article",
    source: str = "Reuters",
) -> Article:
    return Article(
        id=art_id,
        source_name=source,
        feed_section="india",
        title=title,
        url=url,
        published_at=datetime.now(timezone.utc),
        scraped_at=datetime.now(timezone.utc),
        content_text=body,
        content_hash=f"hash_{art_id}",
    )


def make_event(
    ev_id: str,
    title: str,
    article_ids: list,
    companies: list = None,
    figures: list = None,
    cat: NewsCategory = NewsCategory.INDIA,
) -> Event:
    return Event(
        id=ev_id,
        canonical_title=title,
        description=title,
        article_ids=article_ids,
        companies_involved=companies or [],
        financial_figures=figures or [],
        event_category=cat,
        created_at=datetime.now(timezone.utc),
        first_published_at=datetime.now(timezone.utc),
        last_published_at=datetime.now(timezone.utc),
    )


def test_1_jv_headline_contains_ownership_and_action():
    """1. JV headline contains ownership/action."""
    art_body = (
        "JSW Group and Skoda-Volkswagen India have entered exclusive valuation talks "
        "in a move that would reshape India's passenger vehicle landscape under a 51:49 joint venture."
    )
    art = make_article("art_jv", "JSW, Skoda-VW sign MoU", art_body)
    ev = make_event(
        "ev_jv",
        "JSW, Skoda-VW sign MoU",
        ["art_jv"],
        companies=["JSW Group", "Skoda-Volkswagen India"],
    )

    headline = synthesize_investment_headline(ev.canonical_title, event=ev, article=art)
    assert ";" in headline, f"Expected semicolon in headline: {headline}"
    parts = headline.split(";", 1)
    c1, c2 = parts[0].strip(), parts[1].strip()

    # Must contain ownership ratio and action
    assert "51:49" in c1 or "51:49" in headline
    assert "Joint Venture" in c1 or "MoU" in c1
    assert "JSW" in c1
    # Check word count target (roughly 18-32 words)
    words = headline.split()
    assert 16 <= len(words) <= 34, f"Headline word count {len(words)} outside target range: {headline}"


def test_2_capex_headline_includes_value_and_purpose():
    """2. capex headline includes value + purpose."""
    art_body = (
        "Adani Green Energy will invest ₹12,000 crore to construct a 500 MW renewable solar facility. "
        "Project adds 500 MW clean generation capacity in Gujarat to expand regional footprint."
    )
    art = make_article("art_capex", "Adani Green plans ₹12,000 crore capex", art_body)
    ev = make_event(
        "ev_capex",
        "Adani Green plans ₹12,000 crore capex",
        ["art_capex"],
        companies=["Adani Green Energy"],
        figures=["₹12,000 crore"],
    )

    headline = synthesize_investment_headline(ev.canonical_title, event=ev, article=art)
    assert ";" in headline
    assert "₹12,000 crore" in headline or "12,000 crore" in headline
    assert "Capital Expenditure" in headline or "Renewable Facility" in headline or "500 MW" in headline
    words = headline.split()
    assert 16 <= len(words) <= 34, f"Headline word count {len(words)} outside target range: {headline}"


def test_3_regulatory_headline_includes_decision_and_market_implication():
    """3. regulatory headline includes decision + market implication."""
    art_body = (
        "The Competition Commission of India has imposed ₹213 crore penalty on digital platform operators. "
        "The antitrust authority orders operational adjustments and sets critical sector precedent across tech markets."
    )
    art = make_article("art_reg", "CCI fines tech companies ₹213 crore", art_body)
    ev = make_event(
        "ev_reg",
        "CCI fines tech companies ₹213 crore",
        ["art_reg"],
        figures=["₹213 crore"],
    )

    headline = synthesize_investment_headline(ev.canonical_title, event=ev, article=art)
    assert ";" in headline
    assert "CCI" in headline or "Competition Commission of India" in headline
    assert "Penalty" in headline or "₹213 crore" in headline
    assert "Precedent" in headline or "Operational Adjustments" in headline or "precedent" in headline.lower()
    words = headline.split()
    assert 16 <= len(words) <= 34, f"Headline word count {len(words)} outside target range: {headline}"


def test_4_no_unsupported_inference():
    """4. no unsupported inference - uses factual second clause if body lacks implication."""
    # Plain announcement with no speculative/strategic commentary in body
    art_body = (
        "Tata Power commissioned a high-voltage substation in Bengaluru yesterday afternoon. "
        "The facility was energized after standard grid synchronization tests were conducted."
    )
    art = make_article("art_fact", "Tata Power substations commissioned", art_body)
    ev = make_event(
        "ev_fact",
        "Tata Power substations commissioned",
        ["art_fact"],
        companies=["Tata Power"],
    )

    headline = synthesize_investment_headline(ev.canonical_title, event=ev, article=art)
    assert ";" in headline
    # Must not contain wild hallucinations or buy/sell recommendations
    assert "buy" not in headline.lower() and "sell" not in headline.lower() and "target price" not in headline.lower()
    # Does not invent arbitrary financial numbers not in text
    assert "$" not in headline and "₹" not in headline
    words = headline.split()
    assert 16 <= len(words) <= 34


def test_5_routine_title_copying_is_avoided():
    """5. routine title copying is avoided (bad examples are upgraded)."""
    # BAD 1: M3M acquires Noida land for record Rs 2,000 crore
    art1_body = "M3M India has acquired prime land in Noida for Rs 2,000 crore to construct mixed-use commercial space."
    art1 = make_article("art_m3m", "M3M acquires Noida land for record Rs 2,000 crore", art1_body)
    ev1 = make_event(
        "ev_m3m",
        "M3M acquires Noida land for record Rs 2,000 crore",
        ["art_m3m"],
        companies=["M3M India"],
        figures=["Rs 2,000 crore"],
    )

    h1 = synthesize_investment_headline(ev1.canonical_title, event=ev1, article=art1)
    assert h1 != ev1.canonical_title
    assert ";" in h1
    assert "M3M" in h1
    assert "Noida" in h1
    assert len(h1.split()) >= 16

    # BAD 2: Complete Sports and Management India Limited Quarterly Results...
    art2_body = "Complete Sports and Management India Limited declared quarterly financial results with BSE shares at 157.80."
    art2 = make_article("art_cs", "Complete Sports and Management India Limited Quarterly Results, 08 Sept 2026 - BSE 157.80", art2_body)
    ev2 = make_event(
        "ev_cs",
        "Complete Sports and Management India Limited Quarterly Results, 08 Sept 2026 - BSE 157.80",
        ["art_cs"],
        companies=["Complete Sports and Management India Limited"],
    )

    h2 = synthesize_investment_headline(ev2.canonical_title, event=ev2, article=art2)
    assert h2 != ev2.canonical_title
    assert ";" in h2
    assert "Quarterly Results" in h2
    assert len(h2.split()) >= 16


def test_6_and_7_urls_sources_and_story_counts_unchanged():
    """6. URLs/source unchanged & 7. story count unchanged (5/5/5)."""
    from app.ranking.models import ScoreBreakdown

    breakdown = ScoreBreakdown(
        financial_magnitude=75.0,
        market_impact=75.0,
        investor_relevance=75.0,
        corporate_significance=75.0,
        source_quality=80.0,
        total_score=75.0,
    )

    india_events = []
    articles_map = {}
    for i in range(5):
        art_id = f"art_in_{i}"
        ev_id = f"ev_in_{i}"
        url = f"https://economictimes.com/india-story-{i}"
        source = "Economic Times"
        art = make_article(art_id, f"Company {i} Commits ₹{1000*(i+1)} Crore Capex - Economic Times", "Company capex details.", url=url, source=source)
        articles_map[art_id] = art
        ev = make_event(
            ev_id,
            art.title,
            [art_id],
            companies=[f"Company {i}"],
            figures=[f"₹{1000*(i+1)} Crore"],
            cat=NewsCategory.INDIA,
        )
        india_events.append(ScoredEvent(
            event=ev,
            article=art,
            score=90.0 - i,
            composite_score=90.0 - i,
            investment_score=90.0 - i,
            score_breakdown=breakdown,
        ))

    intl_events = []
    for i in range(5):
        art_id = f"art_intl_{i}"
        ev_id = f"ev_intl_{i}"
        url = f"https://reuters.com/world/intl-story-{i}"
        source = "Reuters"
        art = make_article(art_id, f"FPIs Sell ${i+1}.0 Billion in Equities - Reuters", "Foreign fund outflows continued.", url=url, source=source)
        articles_map[art_id] = art
        ev = make_event(
            ev_id,
            art.title,
            [art_id],
            cat=NewsCategory.INTERNATIONAL,
        )
        intl_events.append(ScoredEvent(
            event=ev,
            article=art,
            score=85.0 - i,
            composite_score=85.0 - i,
            investment_score=85.0 - i,
            score_breakdown=breakdown,
        ))

    dom_events = []
    for i in range(5):
        art_id = f"art_dom_{i}"
        ev_id = f"ev_dom_{i}"
        url = f"https://thehindu.com/national/domestic-story-{i}"
        source = "The Hindu"
        art = make_article(art_id, f"Supreme Court Issues Directive {i} - The Hindu", "Court issued order.", url=url, source=source)
        articles_map[art_id] = art
        ev = make_event(
            ev_id,
            art.title,
            [art_id],
            cat=NewsCategory.DOMESTIC,
        )
        dom_events.append(ScoredEvent(
            event=ev,
            article=art,
            score=80.0 - i,
            composite_score=80.0 - i,
            investment_score=80.0 - i,
            score_breakdown=breakdown,
        ))

    pool = RankedCandidatePool(
        india_candidates=india_events,
        international_candidates=intl_events,
        domestic_candidates=dom_events,
    )

    editor = GeminiEditorialEngine(api_key=None)
    res = editor.select_and_synthesize_briefing(pool, articles_map)
    assert res.success is True
    assert res.selection is not None

    sel = res.selection
    # 7. Story count unchanged: 5/5/5
    assert len(sel.domestic_stories) == 5
    assert len(sel.india_stories) == 5
    assert len(sel.international_stories) == 5

    # 6. URLs and sources unchanged
    for i, s in enumerate(sel.india_stories):
        expected_url = f"https://economictimes.com/india-story-{i}"
        assert s.url == expected_url, f"Expected URL {expected_url}, got {s.url}"
        assert s.source == "Economic Times"
        assert ";" in s.headline
        assert is_approved_institutional_headline(s.headline)

    for i, s in enumerate(sel.international_stories):
        expected_url = f"https://reuters.com/world/intl-story-{i}"
        assert s.url == expected_url, f"Expected URL {expected_url}, got {s.url}"
        assert s.source == "Reuters"
        assert ";" in s.headline
        assert is_approved_institutional_headline(s.headline)

    for i, s in enumerate(sel.domestic_stories):
        expected_url = f"https://thehindu.com/national/domestic-story-{i}"
        assert s.url == expected_url, f"Expected URL {expected_url}, got {s.url}"
        assert s.source == "The Hindu"
        assert ";" in s.headline
        assert is_approved_institutional_headline(s.headline)
