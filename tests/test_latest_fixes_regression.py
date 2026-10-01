import pytest
from app.models.entity_sanitizer import clean_company_name, sanitize_company_entities
from app.utils.text_patterns import TITLE_SUFFIX_PATTERN
from app.filtering.rules import StoryTypeFilterRule
from app.models.article import Article
from app.ai.summary_grounding import build_descriptive_investment_summary
from datetime import datetime, timezone


def test_tenor_and_civic_terms_rejected_as_companies():
    """Verify that durations, maturities, yield basis points, and civic terms are rejected."""
    # Tenors / maturities
    assert clean_company_name("25-yr") is None
    assert clean_company_name("10-year") is None
    assert clean_company_name("30-year") is None
    assert clean_company_name("100 bps") is None
    assert clean_company_name("25 bps") is None

    # Civic / political non-companies
    assert clean_company_name("Attacks") is None
    assert clean_company_name("Opposition") is None
    assert clean_company_name("Voters") is None
    assert clean_company_name("Scientists") is None

    # Valid companies are preserved
    assert clean_company_name("Infosys") == "Infosys"
    assert clean_company_name("Tata Motors") == "Tata Motors"
    assert clean_company_name("boAt") == "boAt"
    assert clean_company_name("3M") == "3M"

    sanitized = sanitize_company_entities(["25-yr", "Attacks", "Infosys", "Opposition", "Tata Motors"])
    assert sanitized == ["Infosys", "Tata Motors"]


def test_title_suffix_pattern_strips_additional_publishers():
    """Verify TITLE_SUFFIX_PATTERN strips newly added publishers cleanly."""
    t1 = "Opposition March, Electoral Rolls and Attacks on Constitutional Bodies - India Today"
    t2 = "Cracking the Gen Z Code: What young voters seek from political parties - India Today"
    t3 = "GDP growth projected at 7.3% - The Indian Express"
    t4 = "Market rally continues on festive demand - BusinessLine"
    t5 = "TMB Q2 advances up 29% - CNBCTV18"

    assert TITLE_SUFFIX_PATTERN.sub("", t1).strip() == "Opposition March, Electoral Rolls and Attacks on Constitutional Bodies"
    assert TITLE_SUFFIX_PATTERN.sub("", t2).strip() == "Cracking the Gen Z Code: What young voters seek from political parties"
    assert TITLE_SUFFIX_PATTERN.sub("", t3).strip() == "GDP growth projected at 7.3%"
    assert TITLE_SUFFIX_PATTERN.sub("", t4).strip() == "Market rally continues on festive demand"
    assert TITLE_SUFFIX_PATTERN.sub("", t5).strip() == "TMB Q2 advances up 29%"


def test_ipo_intraday_filter_catches_retail_advice():
    """Verify StoryTypeFilterRule rejects intraday IPO GMP and 'should you subscribe' articles."""
    rule = StoryTypeFilterRule()

    art1 = Article(
        id="a1",
        title="Nityas Gems & Jewellery IPO Day 2: Check GMP, subscription status; should you subscribe?",
        url="https://example.com/ipo1",
        source_name="Economic Times",
        published_at=datetime.now(timezone.utc),
    )
    res1 = rule.evaluate(art1)
    assert res1.is_accepted is False
    assert "ipo_intraday" in res1.rejection_reason

    art2 = Article(
        id="a2",
        title="Orient Cables IPO: Should you subscribe?",
        url="https://example.com/ipo2",
        source_name="Mint",
        published_at=datetime.now(timezone.utc),
    )
    res2 = rule.evaluate(art2)
    assert res2.is_accepted is False
    assert "ipo_intraday" in res2.rejection_reason

    art3 = Article(
        id="a3",
        title="Orient Cables IPO GMP today rises 15%",
        url="https://example.com/ipo3",
        source_name="Financial Express",
        published_at=datetime.now(timezone.utc),
    )
    res3 = rule.evaluate(art3)
    assert res3.is_accepted is False
    assert "ipo_intraday" in res3.rejection_reason


def test_summary_grounding_no_hallucinated_corporate_initiatives():
    """Verify build_descriptive_investment_summary does not produce corporate boilerplate for non-corporate news."""
    h1 = "Opposition March, Electoral Rolls and Attacks on Constitutional Bodies"
    s1 = build_descriptive_investment_summary(h1)
    assert "Attacks advanced a strategic operational initiative" not in s1
    assert "strategic operational initiative" not in s1

    h2 = "US Treasury yields at 25-yr high: What are bond yields, what happens when they rise"
    s2 = build_descriptive_investment_summary(h2)
    assert "25-yr advanced a strategic operational initiative" not in s2
    assert "strategic operational initiative" not in s2
