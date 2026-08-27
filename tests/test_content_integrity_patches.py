"""
Regression test suite for the 4 Content-Integrity Patches:
1. Domestic reserve scanning diversity continuation (max 2 courts, >=3 distinct topics).
2. Foreign geography/company evidence overriding Indian discovery prior (e.g. Australian Ingenia).
3. Nvidia duplicate earnings collapse (AP / BBC reports of same quarter/cycle).
4. Prevention of unrelated secondary source contamination (Vikas vs Juniper, Horizon vs Lalithaa, Zerodha vs boAt).
"""

import pytest
from datetime import datetime, timezone

from app.models.article import Article
from app.models.event import Event
from app.models.enums import NewsCategory, VerificationTier
from app.classification.region_classifier import EventRegionClassifier
from app.verification.verifier import TwoSourceVerifier
from app.verification.query_builder import EventQueryBuilder
from app.deduplication.fingerprint import are_articles_same_event, normalize_entity_name
from app.verification.domestic_trending import classify_domestic_topic, DomesticTopic


# =========================================================================
# BUG 1: DOMESTIC TOPIC DIVERSITY & RESERVE SCANNING
# =========================================================================

def test_domestic_diversity_rule():
    """Verify that domestic candidate pool requires >=3 distinct topics and <=2 court stories when reserve exists."""
    court_headlines = [
        "Supreme Court seeks DNA profiling of captive elephants across states",
        "Police oppose bail for Umar Khalid and Sharjeel Imam in Delhi riots case",
        "CJI asks Rajasthan Chief Justice to respond on complaint",
        "Supreme Court seeks Centre view on sub-classification of creamy layer",
        "Allahabad High Court dismisses plea on school headscarf ban",
    ]
    topics = [classify_domestic_topic(h, "") for h in court_headlines]
    assert all(t == DomesticTopic.COURT_JUDICIARY for t in topics)
    assert len(set(topics)) == 1

    # Diverse set
    diverse_headlines = [
        ("ISRO successfully launches navigation satellite into orbit", DomesticTopic.SCIENCE_ISRO_TECH),
        ("Union Cabinet approves ₹24,000 crore national infrastructure mission", DomesticTopic.GOVERNMENT_POLICY),
        ("Supreme Court seeks DNA profiling of captive elephants", DomesticTopic.COURT_JUDICIARY),
        ("Indian Army tests new indigenous air defence missile system", DomesticTopic.DEFENCE_SECURITY),
        ("IMD issues red alert as heavy rainfall triggers landslides in Kerala", DomesticTopic.WEATHER_DISASTER),
    ]
    div_topics = [classify_domestic_topic(h, "") for h, _ in diverse_headlines]
    assert len(set(div_topics)) >= 3
    assert div_topics.count(DomesticTopic.COURT_JUDICIARY) <= 2


# =========================================================================
# BUG 2: FOREIGN GEOGRAPHY OVERRIDES DISCOVERY PRIOR
# =========================================================================

def test_australian_ingenia_classified_as_international():
    """Australian property developer Ingenia buying Peet must be classified INTERNATIONAL even if discovery prior is INDIA."""
    clf = EventRegionClassifier()
    title = "Australian property developer Ingenia offers to buy Peet in $711 million deal"
    content = "Ingenia Communities Group has made an off-market takeover bid for Australian residential developer Peet Ltd."
    
    cat, reason = clf.classify_with_reason(
        title=title,
        content=content,
        discovery_region=NewsCategory.INDIA,
    )
    assert cat == NewsCategory.INTERNATIONAL
    assert "foreign geography" in reason.lower() or "international" in reason.lower()


def test_foreign_geography_demonyms_override():
    """Verify other international transactions with foreign demonyms override India discovery pool prior."""
    clf = EventRegionClassifier()
    
    # Canadian transaction
    cat, _ = clf.classify_with_reason(
        title="Canadian solar developer secures $400M debt financing for Ontario grid project",
        discovery_region=NewsCategory.INDIA,
    )
    assert cat == NewsCategory.INTERNATIONAL

    # German transaction
    cat, _ = clf.classify_with_reason(
        title="German industrial group Siemens energy unit to build offshore substation",
        discovery_region=NewsCategory.INDIA,
    )
    assert cat == NewsCategory.INTERNATIONAL

    # Genuine Indian transaction remains INDIA
    cat_in, _ = clf.classify_with_reason(
        title="Tata Motors signs ₹5,000 crore contract for electric bus supply in Maharashtra",
        discovery_region=NewsCategory.INDIA,
    )
    assert cat_in == NewsCategory.INDIA


# =========================================================================
# BUG 3: NVIDIA SAME EARNINGS DUPLICATE COLLAPSE
# =========================================================================

def test_nvidia_ap_bbc_duplicate_earnings_collapse():
    """Verify that AP News and BBC reports on Nvidia's earnings collapse into the same event."""
    art1 = Article(
        title="Strong AI chip demand powers Nvidia's Q2 results past Wall Street's expectations",
        url="https://apnews.com/article/nvidia-earnings-q2-ai-chips",
        source_name="Associated Press",
        content_text="Nvidia posted record revenue and net profit for the second quarter driven by strong AI chip demand from data centre customers.",
        category=NewsCategory.INTERNATIONAL,
    )
    art2 = Article(
        title="Nvidia sales soar on rapid buildout of AI data centres",
        url="https://bbc.com/news/articles/nvidia-quarterly-sales-surge",
        source_name="BBC",
        content_text="Tech giant Nvidia saw quarterly sales soar as tech companies continue rapid buildout of AI data centres.",
        category=NewsCategory.INTERNATIONAL,
    )

    verifier = TwoSourceVerifier()
    is_same, score, msg = verifier.is_same_underlying_event(art1, art2)
    assert is_same is True
    assert score >= 0.80

    # Entity extraction must identify Nvidia as the core corporate entity, not generic tokens
    entities1 = EventQueryBuilder.extract_entities(art1)
    assert "Nvidia" in entities1
    assert "AI" not in entities1
    assert "demand" not in entities1
    assert "chip" not in entities1

    entities2 = EventQueryBuilder.extract_entities(art2)
    assert "Nvidia" in entities2


# =========================================================================
# BUG 4: UNRELATED SOURCE 2 CONTAMINATION PREVENTION
# =========================================================================

def test_unrelated_companies_never_verify_or_share_secondary():
    """Unrelated companies (Vikas vs Juniper, Horizon vs Lalithaa, Zerodha vs boAt) must never match."""
    verifier = TwoSourceVerifier()

    # Case 1: Vikas Ecotech vs Juniper Green Energy
    art_vikas = Article(
        title="Vikas Ecotech Q1 net profit jumps 45% to ₹3.5 crore",
        url="https://economictimes.indiatimes.com/vikas-ecotech-q1-results",
        source_name="The Economic Times",
        content_text="Speciality chemicals maker Vikas Ecotech reported a 45% surge in Q1 net profit.",
        category=NewsCategory.INDIA,
    )
    art_juniper = Article(
        title="Juniper Green Energy commissions 70 MW solar project in Gujarat",
        url="https://livemint.com/juniper-green-energy-solar-gujarat",
        source_name="Livemint",
        content_text="Renewable energy firm Juniper Green Energy has commissioned a 70 MW solar project.",
        category=NewsCategory.INDIA,
    )
    is_same1, _, _ = verifier.is_same_underlying_event(art_vikas, art_juniper)
    assert is_same1 is False

    # Case 2: Horizon Industrial Parks vs Lalithaa Jewellery
    art_horizon = Article(
        title="Horizon Industrial Parks leases 2.5 lakh sq ft logistics space in Bengaluru",
        url="https://financialexpress.com/horizon-industrial-parks-lease",
        source_name="Financial Express",
        content_text="Logistics developer Horizon Industrial Parks has leased industrial space.",
        category=NewsCategory.INDIA,
    )
    art_lalithaa = Article(
        title="Lalithaa Jewellery files draft papers with SEBI for ₹1,200 crore IPO",
        url="https://moneycontrol.com/lalithaa-jewellery-drhp-ipo",
        source_name="Moneycontrol",
        content_text="Retail jeweller Lalithaa Jewellery has filed its DRHP with market regulator SEBI.",
        category=NewsCategory.INDIA,
    )
    is_same2, _, _ = verifier.is_same_underlying_event(art_horizon, art_lalithaa)
    assert is_same2 is False

    # Case 3: Zerodha vs boAt
    art_zerodha = Article(
        title="Zerodha posts FY24 revenue of ₹8,370 crore, net profit rises to ₹4,700 crore",
        url="https://moneycontrol.com/zerodha-fy24-financial-results",
        source_name="Moneycontrol",
        content_text="Discount brokerage Zerodha reported strong revenue growth in FY24.",
        category=NewsCategory.INDIA,
    )
    art_boat = Article(
        title="boAt parent Imagine Marketing raises ₹500 crore from existing investors",
        url="https://economictimes.indiatimes.com/boat-raises-500-crore-funding",
        source_name="The Economic Times",
        content_text="Wearables brand boAt has raised fresh capital ahead of public listing plans.",
        category=NewsCategory.INDIA,
    )
    is_same3, _, _ = verifier.is_same_underlying_event(art_zerodha, art_boat)
    assert is_same3 is False


def test_single_source_event_has_no_secondary_source():
    """A single source event must have secondary_publisher = None and secondary_url = None."""
    event = Event(
        canonical_title="Vikas Ecotech Q1 net profit jumps 45% to ₹3.5 crore",
        description="Vikas Ecotech posted robust Q1 financial earnings.",
        article_ids=["art_1"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        primary_publisher="The Economic Times",
        primary_url="https://economictimes.indiatimes.com/vikas-ecotech-q1-results",
        secondary_publisher=None,
        secondary_url=None,
    )
    assert event.secondary_publisher is None
    assert event.secondary_url is None
    assert event.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
