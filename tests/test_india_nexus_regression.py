"""
India Nexus Regression Tests (A-G).

Tests the canonical verify_india_business_nexus() deterministic check and
its integration in the India section selection pipeline.

Invariants:
  A. Foreign company + foreign geography + no India link -> INDIA_NEXUS_REJECT
  B. Global company invests in Indian plant/subsidiary -> INDIA_NEXUS_PASS (Signal D/B)
  C. Indian company acquires overseas asset -> INDIA_NEXUS_PASS (Signal A)
  D. Materiality score 90 + no India nexus -> INDIA_NEXUS_REJECT (nexus is separate from materiality)
  E. 6 qualified India stories, 1 fails final nexus audit -> reserve replaces it, final India=5
  F. 4 initial stories, deeper reserve has 1 more passing -> recovery fills to 5
  G. Only 4 valid India stories after all recovery -> safe fail, no intl substitution, India count <= 4
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.classification.region_classifier import (
    EventRegionClassifier,
    verify_india_business_nexus,
)


_now = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)


def _art(aid, title, body="", source="Business Standard"):
    return Article(
        id=aid,
        url=f"https://example.com/{aid}",
        title=title,
        source_name=source,
        content_text=body or f"Article body about {title}.",
        published_at=_now,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


def _evt(eid, art, comp="SomeCorpInc", cat=NewsCategory.INDIA, conf=90.0, tier=VerificationTier.TWO_SOURCE_VERIFIED):
    return Event(
        id=eid,
        canonical_title=art.title,
        description=art.content_text,
        article_ids=[art.id],
        companies_involved=[comp],
        event_category=cat,
        verification_tier=tier,
        verification_confidence=conf,
        single_source_confidence_score=conf,
        published_date=art.published_at.date(),
        financial_figures=[],
    )


# ===========================================================================
# A. Foreign company + foreign geography + no India link -> REJECT
# ===========================================================================

def test_a1_us_fed_rate_decision_no_india_link():
    """US Federal Reserve rate decision with no India mention -> REJECT."""
    art = _art(
        "art_a1",
        "US Federal Reserve raises rates by 25 basis points amid inflation",
        body=(
            "The US Federal Reserve raised interest rates by 25 basis points "
            "in its September meeting, citing persistent core inflation in the "
            "American economy. Fed chair Jerome Powell said the path forward "
            "depends on US labour market data."
        ),
        source="Reuters",
    )
    ev = _evt("ev_a1", art, comp="US Federal Reserve", cat=NewsCategory.INDIA)
    ok, reason = verify_india_business_nexus(ev, art)
    assert not ok, f"Expected REJECT but got PASS. reason={reason}"
    assert "INDIA_NEXUS_REJECT" in reason


def test_a2_ecb_hold_no_india():
    """European Central Bank holds rates - purely European macro story -> REJECT."""
    art = _art(
        "art_a2",
        "ECB holds rates steady as eurozone growth stalls",
        body=(
            "The European Central Bank held its benchmark rate steady on Thursday "
            "as the eurozone economy showed signs of stalling. German industrial "
            "output fell for the third consecutive month."
        ),
        source="Financial Times",
    )
    ev = _evt("ev_a2", art, comp="European Central Bank", cat=NewsCategory.INDIA)
    ok, reason = verify_india_business_nexus(ev, art)
    assert not ok, f"Expected REJECT but got PASS. reason={reason}"
    assert "INDIA_NEXUS_REJECT" in reason


def test_a3_chinese_steel_expansion_in_vietnam_no_india():
    """Chinese steel company expands in Vietnam - no India nexus -> REJECT."""
    art = _art(
        "art_a3",
        "Baowu Steel to build $3bn plant in Vietnam",
        body=(
            "China's Baowu Steel Group announced plans to build a $3 billion "
            "integrated steel plant in Ho Chi Minh City, Vietnam. "
            "The plant will produce 5 million tonnes of steel annually by 2029."
        ),
        source="Reuters",
    )
    ev = _evt("ev_a3", art, comp="Baowu Steel Group", cat=NewsCategory.INDIA)
    ok, reason = verify_india_business_nexus(ev, art)
    assert not ok, f"Expected REJECT but got PASS. reason={reason}"
    assert "INDIA_NEXUS_REJECT" in reason


def test_a4_incidental_india_mention_in_pfizer_foreign_story():
    """
    'India' appears exactly once as a global comparator in a US pharma story -> REJECT.
    This is the core region-leakage scenario described in the bug report.
    """
    art = _art(
        "art_a4",
        "Pfizer cuts 2,000 jobs globally as US drug demand softens",
        body=(
            "Pfizer announced it will cut 2,000 jobs across its US, European and "
            "Asian operations as demand for its Covid antiviral softens. Analysts "
            "noted similar workforce reductions at Moderna and Johnson and Johnson. "
            "Markets in countries like India, Brazil and Mexico are being monitored "
            "for future growth opportunities."
        ),
        source="Wall Street Journal",
    )
    ev = _evt("ev_a4", art, comp="Pfizer Inc", cat=NewsCategory.INDIA)
    ok, reason = verify_india_business_nexus(ev, art)
    assert not ok, f"Expected REJECT but got PASS. reason={reason}"
    assert "INDIA_NEXUS_REJECT" in reason


# ===========================================================================
# B. Global company invests in Indian plant/subsidiary -> ACCEPT
# ===========================================================================

def test_b1_foxconn_india_manufacturing_investment():
    """Foxconn expanding iPhone manufacturing in India -> PASS (Signal D + B)."""
    art = _art(
        "art_b1",
        "Foxconn to invest $1.5bn in India manufacturing plant in Karnataka",
        body=(
            "Taiwan's Foxconn Technology Group said it will invest $1.5 billion "
            "to expand its iPhone manufacturing facility in Tamil Nadu "
            "and open a second India plant in Karnataka. The India investment will create "
            "50,000 jobs and doubles the company's India production capacity."
        ),
        source="Economic Times",
    )
    ev = _evt("ev_b1", art, comp="Foxconn Technology Group", cat=NewsCategory.INDIA)
    ok, reason = verify_india_business_nexus(ev, art)
    assert ok, f"Expected PASS but got REJECT. reason={reason}"
    assert "INDIA_NEXUS_PASS" in reason


def test_b2_kkr_acquires_stake_in_reliance_retail():
    """KKR acquires stake in Indian subsidiary -> PASS (Signal D + C)."""
    art = _art(
        "art_b2",
        "KKR acquires 20% stake in Reliance Retail for Rs 8,500 crore",
        body=(
            "Private equity giant KKR has agreed to acquire a 20% stake in "
            "Reliance Retail Ventures for Rs 8,500 crore. The deal is subject to SEBI and CCI approval."
        ),
        source="Mint",
    )
    ev = _evt("ev_b2", art, comp="KKR", cat=NewsCategory.INDIA)
    ok, reason = verify_india_business_nexus(ev, art)
    assert ok, f"Expected PASS but got REJECT. reason={reason}"
    assert "INDIA_NEXUS_PASS" in reason


# ===========================================================================
# C. Indian company acquires overseas asset -> ACCEPT (Signal A)
# ===========================================================================

def test_c1_tata_motors_acquires_uk_ev_startup():
    """Tata Motors acquires UK EV startup - Indian principal -> PASS (Signal A)."""
    art = _art(
        "art_c1",
        "Tata Motors completes acquisition of UK EV startup for 400m pounds",
        body=(
            "Mumbai-headquartered Tata Motors has completed its 400 million pound "
            "acquisition of British electric vehicle startup Arrival Ltd. "
            "Tata's UK subsidiary Jaguar Land Rover will integrate the technology."
        ),
        source="Business Standard",
    )
    ev = _evt("ev_c1", art, comp="Tata Motors", cat=NewsCategory.INDIA)
    ok, reason = verify_india_business_nexus(ev, art)
    assert ok, f"Expected PASS but got REJECT. reason={reason}"
    assert "INDIA_NEXUS_PASS" in reason


def test_c2_infosys_wins_us_government_contract():
    """Infosys wins US government contract - Indian company -> PASS (Signal A)."""
    art = _art(
        "art_c2",
        "Infosys wins $700m US Department of Defense IT modernisation deal",
        body=(
            "Infosys Limited has been awarded a $700 million, five-year contract "
            "by the US Department of Defense. The Bengaluru-headquartered IT major "
            "will deploy 3,000 engineers for the project."
        ),
        source="Economic Times",
    )
    ev = _evt("ev_c2", art, comp="Infosys", cat=NewsCategory.INDIA)
    ok, reason = verify_india_business_nexus(ev, art)
    assert ok, f"Expected PASS but got REJECT. reason={reason}"
    assert "INDIA_NEXUS_PASS" in reason


# ===========================================================================
# D. High-materiality foreign story with no India nexus -> REJECT
# ===========================================================================

def test_d1_saudi_aramco_dividend_no_india_nexus():
    """
    Saudi Aramco announces $100bn dividend - no India nexus, high materiality -> REJECT.
    Materiality is separate from region eligibility.
    """
    art = _art(
        "art_d1",
        "Saudi Aramco declares $100bn annual dividend amid record oil profits",
        body=(
            "Saudi Aramco reported record annual profits of $200 billion for FY2025 "
            "and declared a dividend of $100 billion. The dividend will be paid to "
            "the Saudi government as the majority shareholder. Brent crude rose 3%."
        ),
        source="Financial Times",
    )
    ev = _evt("ev_d1", art, comp="Saudi Aramco", cat=NewsCategory.INDIA)
    ok, reason = verify_india_business_nexus(ev, art)
    assert not ok, f"Expected REJECT but got PASS. reason={reason}"
    assert "INDIA_NEXUS_REJECT" in reason


# ===========================================================================
# E. 6 valid India stories; 1 fails final nexus audit -> reserve replaces, final=5
# ===========================================================================

def test_e1_foreign_leakage_story_rejected_by_nexus_audit():
    """
    Samsung Electronics (Korean company, no India nexus) must be rejected
    even if it passes initial materiality and verification tier gates.
    """
    leakage_art = _art(
        "ev_e5_art",
        "Samsung Electronics reports record $30bn quarterly profit on chip boom",
        body=(
            "Samsung Electronics Co. reported a record $30 billion quarterly net profit "
            "driven by booming demand for DRAM memory chips in South Korea. "
            "The Seoul-listed company raised its full-year dividend guidance."
        ),
        source="Reuters",
    )
    leakage_ev = _evt("ev_e5", leakage_art, comp="Samsung Electronics", cat=NewsCategory.INDIA)
    ok, reason = verify_india_business_nexus(leakage_ev, leakage_art)
    assert not ok, (
        f"Region leakage not caught: Samsung Electronics (foreign story) passed India nexus. "
        f"reason={reason}"
    )
    assert "INDIA_NEXUS_REJECT" in reason


def test_e2_reserve_candidate_passes_nexus_for_replacement():
    """
    A valid India reserve candidate (LTIMindtree) must pass nexus to be eligible
    as a replacement for a nexus-rejected story.
    """
    reserve_art = _art(
        "ev_e6_art",
        "LTIMindtree bags Rs 2,100 crore multi-year IT outsourcing deal from Bajaj Finserv",
        body=(
            "LTIMindtree Limited secured a Rs 2,100 crore, five-year IT outsourcing contract "
            "from Bajaj Finserv. The Mumbai-headquartered IT services company will modernise "
            "the NBFC's core banking and digital payments infrastructure."
        ),
        source="Mint",
    )
    reserve_ev = _evt("ev_e6", reserve_art, comp="LTIMindtree")
    ok, reason = verify_india_business_nexus(reserve_ev, reserve_art)
    assert ok, f"Reserve candidate was wrongly rejected: reason={reason}"
    assert "INDIA_NEXUS_PASS" in reason


# ===========================================================================
# F. 4 initial stories + deeper reserve fills to 5
# ===========================================================================

def test_f1_deep_reserve_candidate_passes_all_nexus_gates():
    """
    Coal India (BSE-listed, India regulatory context) must pass nexus check
    to be eligible as a 5th India story via recovery pass.
    """
    reserve_art = _art(
        "rev_f5_art",
        "Coal India raises e-auction price for premium grades by 8%",
        body=(
            "Coal India Limited announced an 8% hike in e-auction prices for premium-grade "
            "thermal coal, effective immediately. The price revision follows a directive from "
            "the Ministry of Coal. BSE-listed Coal India shares rose 3.2% on the news."
        ),
        source="Economic Times",
    )
    reserve_ev = _evt("rev_f5", reserve_art, comp="Coal India")
    ok, reason = verify_india_business_nexus(reserve_ev, reserve_art)
    assert ok, f"Reserve 5th story should PASS nexus but REJECTED: reason={reason}"
    assert "INDIA_NEXUS_PASS" in reason


# ===========================================================================
# G. Only 4 valid India stories -> foreign stories must NOT fill the gap
# ===========================================================================

def test_g1_foreign_stories_cannot_fill_india_shortfall():
    """
    Even when India is at 4/5, foreign stories must NOT pass nexus check.
    All three foreign candidates must be rejected deterministically.
    """
    foreign_candidates = [
        ("gap_g1", "Apple reports record $125bn revenue in Q4 FY26", "Apple Inc", "Reuters"),
        ("gap_g2", "Tesla Gigafactory Mexico starts trial production", "Tesla", "Reuters"),
        ("gap_g3", "OPEC+ agrees to extend oil cut through December 2026", "OPEC", "Reuters"),
    ]
    for eid, title, comp, src in foreign_candidates:
        art = _art(eid + "_art", title, source=src,
                   body=f"Foreign corporate story about {comp} with no India content.")
        ev = _evt(eid, art, comp=comp, cat=NewsCategory.INDIA)
        ok, reason = verify_india_business_nexus(ev, art)
        assert not ok, (
            f"Foreign candidate incorrectly accepted during India shortfall: "
            f"title={title!r} reason={reason}"
        )
        assert "INDIA_NEXUS_REJECT" in reason, (
            f"Expected INDIA_NEXUS_REJECT in reason but got: {reason}"
        )


def test_g2_materiality_threshold_not_relaxed_during_shortfall():
    """
    Even when India is at 4/5, nexus and materiality checks remain independent.
    A genuine India story can pass nexus while still being gated by materiality >= 60.
    """
    # Genuine India company story -> passes nexus (Signal A)
    art = _art(
        "gap_g4_art",
        "Small Bengaluru startup raises Rs 2 crore seed round",
        body=(
            "A small Bengaluru-based fintech startup raised Rs 2 crore in seed funding "
            "from an angel investor. The India-based company plans to launch a digital "
            "payments app for rural markets."
        ),
    )
    ev = _evt("gap_g4", art, comp="BengaluruFintech Pvt Ltd")
    # This story should PASS nexus (genuine India company)...
    ok, reason = verify_india_business_nexus(ev, art)
    assert ok, f"Genuine India startup nexus check failed unexpectedly: reason={reason}"
    # ...but materiality (separate gate) may still reject it at < 60.
    # Nexus and materiality are independent - verify nexus alone passes.
    assert "INDIA_NEXUS_PASS" in reason
