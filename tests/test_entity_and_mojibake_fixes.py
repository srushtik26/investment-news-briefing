"""
Regression unit tests for:
1. Display-layer mojibake and encoding fixes (CP437, NBSP, currency display).
2. Primary corporate business entity extraction and region identification.
"""

from datetime import datetime, timezone
import pytest

from app.models.article import Article
from app.models.enums import NewsCategory
from app.verification.query_builder import EventQueryBuilder
from app.models.entity_sanitizer import sanitize_company_entities
from app.classification.region_classifier import EventRegionClassifier
from app.formatting.formatter import BriefingFormatter
from app.verification.single_source import is_valid_named_company_entity


def test_mojibake_and_currency_display_cleaning():
    """Verify BriefingFormatter.clean_text repairs live CP437 mojibake strings cleanly."""
    samples = [
        ("Deepinder Goyal\u0393\xc7\xd6s Temple acquires...", "Deepinder Goyal’s Temple acquires..."),
        ("\u0393\xc7\xffNo change in circumstances\u0393\xc7\xd6: Police oppose...", "‘No change in circumstances’: Police oppose..."),
        ("Mumbai\u0393\xc7\xd6s next big underground road", "Mumbai’s next big underground road"),
        ("Salesforce\u0393\xc7\xd6s stock gets an Anthropic boost \u0393\xc7\xf6 ...", "Salesforce’s stock gets an Anthropic boost — ..."),
        ("crore\u252c\u00e1worth", "crore worth"),
        ("crore\u252c\xa0worth", "crore worth"),
        ("crore┬áworth", "crore worth"),
        ("reports net profit of Rs 4,300 crore", "reports net profit of ₹4,300 crore"),
        ("reports net profit of Rs. 4,300 crore", "reports net profit of ₹4,300 crore"),
        ("offloads Rs 2,217 crore worth of Groww shares", "offloads ₹2,217 crore worth of Groww shares"),
    ]
    for raw_text, expected in samples:
        cleaned = BriefingFormatter.clean_text(raw_text)
        assert cleaned == expected, f"Failed for {raw_text!r}: got {cleaned!r}, expected {expected!r}"


def test_primary_entity_extraction_regressions():
    """Verify primary entity extraction prefers explicit named company in headline."""
    clf = EventRegionClassifier()
    cases = [
        (
            "Salesforce\u2019s stock gets an Anthropic boost \u2014 and more highlights from earnings",
            "MarketWatch",
            "Salesforce",
            NewsCategory.INTERNATIONAL,
            "salesforce",
        ),
        (
            "Block Deals: Promoter General Atlantic sells over 8% Rubicon Research stake; Ribbit Capital offloads Rs 2,217 crore worth of Groww shares",
            "Moneycontrol",
            "General Atlantic",
            NewsCategory.INDIA,
            "general atlantic",
        ),
        (
            "Nvidia agrees to buy Hugging Face for $12.9 billion, reports",
            "Fortune",
            "Nvidia",
            NewsCategory.INTERNATIONAL,
            "nvidia",
        ),
        (
            "CrowdStrike\u2019s quarter shows AI is a cybersecurity tailwind, not a threat",
            "CNBC",
            "CrowdStrike",
            NewsCategory.INTERNATIONAL,
            "crowdstrike",
        ),
        (
            "Zerodha revenue and profit remain flat in FY 26; reports net profit of Rs 4,300 crore- Moneycontrol.com",
            "Moneycontrol",
            "Zerodha",
            NewsCategory.INDIA,
            "zerodha",
        ),
    ]

    for title, pub, expected_lead, expected_cat, expected_reason_fragment in cases:
        art = Article(
            id="test_art",
            title=title,
            url="https://example.com",
            source_name=pub,
            content_text=title,
            published_at=datetime.now(timezone.utc),
            category=NewsCategory.INDIA,
        )
        extracted = EventQueryBuilder.extract_entities(art)
        sanitized = sanitize_company_entities(extracted, publisher=pub)
        assert len(sanitized) > 0, f"No sanitized entities extracted for title: {title}"
        assert sanitized[0] == expected_lead, f"Expected lead entity {expected_lead!r}, got {sanitized[0]!r}"
        
        # Verify generic phrases are rejected as company entity
        for generic_phrase in ["Block Deals", "Block", "Bulk Deals", "Quarterly Results", "AI", "Wall Street", "Lost", "Today", "Shares", "Stock", "Markets"]:
            assert not is_valid_named_company_entity(generic_phrase), f"Generic phrase {generic_phrase!r} should NOT be a valid company entity"

        # Verify region classification
        cat, reason = clf.classify_with_reason(title=title, content=title, companies=sanitized)
        assert cat == expected_cat, f"Expected category {expected_cat}, got {cat} for {title}"
        assert expected_reason_fragment.lower() in reason.lower(), f"Expected reason to mention '{expected_reason_fragment}', got '{reason}'"
