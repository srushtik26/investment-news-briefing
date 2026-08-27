"""
Regression test suite for the three remaining live production bugs:
1. Mojibake cleaning on the public formatter.format() path for exact live log strings:
   - "ΓÇÿNo change in circumstancesΓÇÖ" -> "‘No change in circumstances’"
   - "MumbaiΓÇÖs next big underground road" -> "Mumbai’s next big underground road"
   - "SalesforceΓÇÖs stock gets an Anthropic boost ΓÇö and more highlights" -> "Salesforce’s stock gets an Anthropic boost — and more highlights"
2. CrowdStrike Q2 generic earnings duplicate collapse in is_same_underlying_event().
3. HCSS (High Confidence Single Source) secondary metadata detachment for Lalithaa Jewellery + Hirect.
"""

from datetime import date, datetime, timezone
import pytest

from app.models.article import Article
from app.models.enums import NewsCategory, VerificationTier
from app.models.event import Event
from app.ai.models import BriefingEditorialPayload, EditorialStorySelection
from app.formatting.formatter import BriefingFormatter
from app.verification.verifier import TwoSourceVerifier


# ──────────────────────────────────────────────────────────────────────────────
# BUG 1: Mojibake on Public format() Path
# ──────────────────────────────────────────────────────────────────────────────

class TestBug1MojibakeOnFormatPath:
    """Verify that exact live log mojibake strings are cleaned through format()."""

    def test_live_log_mojibake_strings_in_full_briefing(self):
        formatter = BriefingFormatter()

        # Build 5 Domestic stories containing live mojibake strings
        dom_stories = [
            EditorialStorySelection(
                section="domestic",
                event_id="dom-1",
                headline="ΓÇÿNo change in circumstancesΓÇÖ: Supreme Court denies relief",
                source="@The_Hindu",
                url="https://www.thehindu.com/news/national/sc-hearing",
            ),
            EditorialStorySelection(
                section="domestic",
                event_id="dom-2",
                headline="MumbaiΓÇÖs next big underground road gets clearance",
                source="The Indian Express",
                url="https://indianexpress.com/article/cities/mumbai/road-clearance",
            ),
            EditorialStorySelection(
                section="domestic",
                event_id="dom-3",
                headline="Cabinet approves ₹10,000 crore infra plan",
                source="The Hindu",
                url="https://thehindu.com/dom-3",
            ),
            EditorialStorySelection(
                section="domestic",
                event_id="dom-4",
                headline="ISRO successfully launches new Earth observation satellite",
                source="The Hindu",
                url="https://thehindu.com/dom-4",
            ),
            EditorialStorySelection(
                section="domestic",
                event_id="dom-5",
                headline="Parliament passes digital infrastructure amendment bill",
                source="The Indian Express",
                url="https://indianexpress.com/dom-5",
            ),
        ]

        # Build 5 India stories containing mojibake rupee and apostrophes
        india_stories = [
            EditorialStorySelection(
                section="india",
                event_id="ind-1",
                headline="Reliance Industries posts net profit of Γé╣19,878 crore in Q1",
                source="@bsindia",
                url="https://www.business-standard.com/article/reliance-q1-2025",
            ),
            EditorialStorySelection(
                section="india",
                event_id="ind-2",
                headline="HDFC BankΓÇÖs net profit rises 35% YoY to ₹16,175 cr in Q1 FY26",
                source="@economictimes",
                url="https://economictimes.indiatimes.com/hdfc-bank-q1-2025",
            ),
            EditorialStorySelection(
                section="india",
                event_id="ind-3",
                headline="Adani Green raises $1.2 bn via dollar bonds",
                source="@livemint",
                url="https://www.livemint.com/adani-green-bonds-2025",
            ),
            EditorialStorySelection(
                section="india",
                event_id="ind-4",
                headline="SEBI bans Quant Mutual Fund for 30 days",
                source="@financialxpress",
                url="https://www.financialexpress.com/sebi-quant-ban-2025",
            ),
            EditorialStorySelection(
                section="india",
                event_id="ind-5",
                headline="Tata Motors EV sales jump 48% in June quarter",
                source="@bsindia",
                url="https://www.business-standard.com/tata-motors-ev-june-2025",
            ),
        ]

        # Build 5 International stories containing live Salesforce mojibake headline
        intl_stories = [
            EditorialStorySelection(
                section="international",
                event_id="intl-1",
                headline="SalesforceΓÇÖs stock gets an Anthropic boost ΓÇö and more highlights",
                source="@cnbc",
                url="https://www.cnbc.com/salesforce-anthropic-boost",
            ),
            EditorialStorySelection(
                section="international",
                event_id="intl-2",
                headline="Apple Q3 revenue $94.9 bn beats estimates; iPhone sales up 6%",
                source="@reuters",
                url="https://www.reuters.com/apple-q3-2025",
            ),
            EditorialStorySelection(
                section="international",
                event_id="intl-3",
                headline="Microsoft acquires Suki AI for $3.4 bn",
                source="@bloomberg",
                url="https://www.bloomberg.com/microsoft-suki-acquisition-2025",
            ),
            EditorialStorySelection(
                section="international",
                event_id="intl-4",
                headline="Fed holds rates at 5.25–5.50%; Powell signals two cuts",
                source="@wsj",
                url="https://www.wsj.com/fed-holds-rates-2025",
            ),
            EditorialStorySelection(
                section="international",
                event_id="intl-5",
                headline="Chevron posts $6.3 bn Q2 profit as oil prices stabilise",
                source="@reuters",
                url="https://www.reuters.com/chevron-q2-2025",
            ),
        ]

        payload = BriefingEditorialPayload(
            domestic_stories=dom_stories,
            india_stories=india_stories,
            international_stories=intl_stories,
        )

        res = formatter.format(payload, briefing_date=date(2026, 8, 27), shorten_urls=False)
        text = res.text

        # Assert all mojibake sequences are completely gone from the briefing output
        assert "ΓÇÿ" not in text
        assert "ΓÇÖ" not in text
        assert "ΓÇö" not in text
        assert "Γé╣" not in text

        # Assert exact expected clean text appears
        assert "‘No change in circumstances’" in text
        assert "Mumbai’s next big underground road" in text
        assert "Salesforce’s stock gets an Anthropic boost — and more highlights" in text
        assert "₹19,878 crore" in text
        assert "HDFC Bank’s net profit" in text

        # Assert URLs are preserved verbatim
        assert "https://www.thehindu.com/news/national/sc-hearing" in text
        assert "https://www.cnbc.com/salesforce-anthropic-boost" in text

        # Assert source names cleaned
        assert "Source: The Hindu" in text
        assert "Source: Business Standard" in text
        assert "Source: CNBC" in text


# ──────────────────────────────────────────────────────────────────────────────
# BUG 2: CrowdStrike Q2 Earnings Duplicate Collapse
# ──────────────────────────────────────────────────────────────────────────────

class TestBug2CrowdStrikeDuplicate:
    """Verify that CrowdStrike Q2 headlines collapse to the same underlying event."""

    def test_crowdstrike_q2_live_headlines_match(self):
        verifier = TwoSourceVerifier()
        now_utc = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

        art1 = Article(
            id="cs-01",
            title="CrowdStrike jumps 11% on record second quarter as 'Mythos moment' drives AI cyber wave",
            url="https://www.cnbc.com/2026/08/27/crowdstrike-q2-earnings.html",
            source_name="CNBC",
            content_text="CrowdStrike reported record second quarter revenue and strong cybersecurity demand.",
            published_at=datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc),
            category=NewsCategory.INTERNATIONAL,
        )

        art2 = Article(
            id="cs-02",
            title="CrowdStrike's quarter shows AI is a cybersecurity tailwind, not a threat",
            url="https://www.cnbc.com/2026/08/27/crowdstrike-quarter-analysis.html",
            source_name="CNBC",
            content_text="CrowdStrike's latest quarter proves AI expansion is driving cybersecurity tailwinds.",
            published_at=datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc),
            category=NewsCategory.INTERNATIONAL,
        )

        is_same, score, msg = verifier.is_same_underlying_event(art1, art2, now_utc=now_utc)
        assert is_same is True, f"CrowdStrike Q2 stories should match as same event. Msg: {msg}"
        assert score >= 0.80

    def test_crowdstrike_earnings_vs_separate_acquisition_are_distinct(self):
        verifier = TwoSourceVerifier()
        now_utc = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

        art1 = Article(
            id="cs-01",
            title="CrowdStrike jumps 11% on record second quarter as 'Mythos moment' drives AI cyber wave",
            url="https://www.cnbc.com/2026/08/27/crowdstrike-q2-earnings.html",
            source_name="CNBC",
            content_text="CrowdStrike reported record second quarter revenue and strong cybersecurity demand.",
            published_at=datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc),
            category=NewsCategory.INTERNATIONAL,
        )

        art_acq = Article(
            id="cs-03",
            title="CrowdStrike acquires cloud security firm SentryAI for $650 million",
            url="https://www.reuters.com/technology/crowdstrike-acquires-sentryai-2026",
            source_name="Reuters",
            content_text="CrowdStrike announced the acquisition of SentryAI for $650 million in cash.",
            published_at=datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc),
            category=NewsCategory.INTERNATIONAL,
        )

        is_same, score, msg = verifier.is_same_underlying_event(art1, art_acq, now_utc=now_utc)
        assert is_same is False, "CrowdStrike earnings vs acquisition must be distinct events."


# ──────────────────────────────────────────────────────────────────────────────
# BUG 3: HCSS Secondary Metadata Detachment
# ──────────────────────────────────────────────────────────────────────────────

class TestBug3HCSSSecondaryDetachment:
    """Verify that HCSS events have no secondary fields or contaminated article_ids."""

    def test_lalithaa_jewellery_and_hirect_not_same_event(self):
        verifier = TwoSourceVerifier()
        now_utc = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

        art_lalithaa = Article(
            id="art-lalithaa-01",
            title="Lalithaa Jewellery Mart Ltd Quarterly Results, 27 Aug 2026",
            url="https://www.moneycontrol.com/news/business/earnings/lalithaa-results.html",
            source_name="Moneycontrol",
            content_text="Lalithaa Jewellery Mart reported quarterly results for June 2026.",
            published_at=datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc),
            category=NewsCategory.INDIA,
        )

        art_hirect = Article(
            id="art-hirect-02",
            title="Hind Rectifiers (Hirect) Ltd Quarterly Results, 27 Aug 2026",
            url="https://www.moneycontrol.com/news/business/earnings/hirect-results.html",
            source_name="Moneycontrol",
            content_text="Hind Rectifiers (Hirect) Ltd reported its Q1 financial results.",
            published_at=datetime(2026, 8, 27, 8, 30, tzinfo=timezone.utc),
            category=NewsCategory.INDIA,
        )

        is_same, score, msg = verifier.is_same_underlying_event(art_lalithaa, art_hirect, now_utc=now_utc)
        assert is_same is False, "Lalithaa Jewellery and Hirect are distinct entities and must not match."

    def test_verify_event_clears_secondary_fields_on_unrelated_rejection(self):
        verifier = TwoSourceVerifier()
        now_utc = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

        art_lalithaa = Article(
            id="art-lalithaa-01",
            title="Lalithaa Jewellery Mart Ltd Quarterly Results, 27 Aug 2026",
            url="https://www.moneycontrol.com/news/business/earnings/lalithaa-results.html",
            source_name="Moneycontrol",
            content_text="Lalithaa Jewellery Mart reported quarterly results for June 2026.",
            published_at=datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc),
            category=NewsCategory.INDIA,
        )

        art_hirect = Article(
            id="art-hirect-02",
            title="Hind Rectifiers (Hirect) Ltd Quarterly Results, 27 Aug 2026",
            url="https://www.moneycontrol.com/news/business/earnings/hirect-results.html",
            source_name="Moneycontrol",
            content_text="Hind Rectifiers (Hirect) Ltd reported its Q1 financial results.",
            published_at=datetime(2026, 8, 27, 8, 30, tzinfo=timezone.utc),
            category=NewsCategory.INDIA,
        )

        ev = Event(
            id="ev-lalithaa-01",
            canonical_title="Lalithaa Jewellery Mart Ltd Quarterly Results",
            description="Lalithaa Jewellery financial results for June 2026",
            article_ids=[art_lalithaa.id, art_hirect.id],
            event_category=NewsCategory.INDIA,
            primary_publisher="Moneycontrol",
            primary_url=art_lalithaa.url,
            secondary_publisher="Moneycontrol",
            secondary_url=art_hirect.url,
        )

        verif_result = verifier.verify_event(ev, [art_lalithaa, art_hirect], now_utc=now_utc)
        assert verif_result.is_independent is False
        assert ev.secondary_publisher is None
        assert ev.secondary_url is None
        assert ev.article_ids == [art_lalithaa.id]

    def test_unrelated_source_regressions(self):
        """Retain unrelated-source pair regressions."""
        verifier = TwoSourceVerifier()
        now_utc = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

        pairs = [
            ("Vikas Ecotech quarterly profit up 25%", "Juniper Green Energy secures 200MW project"),
            ("Horizon Industrial Parks secures ₹1,200 cr funding", "Lalithaa Jewellery expands retail footprint"),
            ("Zerodha reports ₹4,000 cr profit", "boAt Lifestyle files DRHP for ₹2,000 cr IPO"),
        ]

        for idx, (t1, t2) in enumerate(pairs):
            a1 = Article(
                id=f"a1_{idx}",
                title=t1,
                url=f"https://source1.com/story-{idx}",
                source_name="Publisher One",
                content_text=t1,
                published_at=now_utc,
                category=NewsCategory.INDIA,
            )
            a2 = Article(
                id=f"a2_{idx}",
                title=t2,
                url=f"https://source2.com/story-{idx}",
                source_name="Publisher Two",
                content_text=t2,
                published_at=now_utc,
                category=NewsCategory.INDIA,
            )
            is_same, _, msg = verifier.is_same_underlying_event(a1, a2, now_utc=now_utc)
            assert is_same is False, f"Unrelated pair '{t1}' and '{t2}' must not match. Msg: {msg}"

    def test_idempotent_article_id_removal_scenario(self):
        """
        Verify that when verifier/cleanup removes a candidate article ID before caller cleanup,
        subsequent removal does not raise ValueError and preserves the primary article ID.
        """
        verifier = TwoSourceVerifier()
        now_utc = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

        art_zerodha = Article(
            id="art-zerodha-01",
            title="Zerodha posts record ₹4,000 crore annual profit",
            url="https://www.moneycontrol.com/news/zerodha-results",
            source_name="Moneycontrol",
            content_text="Zerodha reported FY26 financial results.",
            published_at=now_utc,
            category=NewsCategory.INDIA,
        )

        art_boat = Article(
            id="art-boat-02",
            title="boAt Lifestyle files DRHP for ₹2,000 crore IPO",
            url="https://economictimes.indiatimes.com/boat-drhp",
            source_name="Economic Times",
            content_text="boAt files DRHP with SEBI for its public listing.",
            published_at=now_utc,
            category=NewsCategory.INDIA,
        )

        ev = Event(
            id="ev-zerodha-01",
            canonical_title="Zerodha posts record ₹4,000 crore annual profit",
            description="Zerodha FY26 results",
            article_ids=[art_zerodha.id, art_boat.id],
            event_category=NewsCategory.INDIA,
            primary_publisher="Moneycontrol",
            primary_url=art_zerodha.url,
            secondary_publisher="Economic Times",
            secondary_url=art_boat.url,
        )

        # 1. Verifier inspects articles, rejects corroboration, and cleans secondary article ID
        rv = verifier.verify_event(ev, [art_zerodha, art_boat], now_utc=now_utc)
        assert rv.is_verified is False
        assert ev.article_ids == [art_zerodha.id]

        # 2. Caller (run_pipeline.py process_candidate_item) executes idempotent removal
        try:
            if art_boat.id in ev.article_ids:
                ev.article_ids.remove(art_boat.id)
        except ValueError:
            pytest.fail("Idempotent article removal raised ValueError unexpectedly")

        # 3. Verify final state
        assert art_boat.id not in ev.article_ids
        assert ev.article_ids == [art_zerodha.id]
        assert ev.primary_url == art_zerodha.url

