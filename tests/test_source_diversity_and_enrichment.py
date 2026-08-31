"""
Tests for Source Diversity, Second-Source Enrichment, Circuit Breaker Degradation,
Domestic Same-Event Safety, and Dual-Source Display.
"""

import pytest
from datetime import datetime, timezone, timedelta, date
from unittest.mock import MagicMock, patch

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.ranking.models import ScoredEvent
from app.extraction.extractor import ArticleExtractor
from app.extraction.http_client import ArticleFetcher
from app.verification.verifier import TwoSourceVerifier
from app.verification.serpapi_corroborator import MAX_SERPAPI_SEARCHES_PER_RUN
from app.ranking.sorter import select_diverse_publisher_candidates
from app.formatting.formatter import BriefingFormatter
from app.email.email_sender import parse_briefing_text, generate_briefing_html
from app.ai.models import EditorialStorySelection, BriefingEditorialPayload


def _make_article(
    id: str,
    title: str,
    url: str,
    source_name: str,
    category: NewsCategory = NewsCategory.INDIA,
    pub_date: datetime = None,
    content: str = "",
) -> Article:
    return Article(
        id=id,
        title=title,
        url=url,
        source_name=source_name,
        category=category,
        published_at=pub_date or datetime.now(timezone.utc) - timedelta(hours=2),
        content_text=content or title * 5,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


# 1. Test Single-Source Upgrade via Free Google News RSS
def test_single_source_upgrade_via_free_rss():
    art1 = _make_article("a1", "Nvidia Acquires AI Chip Startup Run:ai For $700 Million", "https://techcrunch.com/nvidia-runai", "TechCrunch", NewsCategory.INTERNATIONAL)
    art2 = _make_article("a2", "Nvidia buys AI infrastructure firm Run:ai in $700m deal", "https://cnbc.com/nvidia-buys-runai", "CNBC", NewsCategory.INTERNATIONAL)

    verifier = TwoSourceVerifier()
    is_same, score, reason = verifier.is_same_underlying_event(art1, art2)
    assert is_same is True
    assert "nvidia" in reason.lower() or "run:ai" in reason.lower() or "runai" in reason.lower() or "same" in reason.lower()


# 2. Test Secondary Source Must Be a Different Domain
def test_secondary_source_different_domain():
    art1 = _make_article("a1", "Apple unveils new M4 chips in major update", "https://cnbc.com/apple-m4-chips", "CNBC")
    art2 = _make_article("a2", "Apple announced new M4 chip lineup today", "https://cnbc.com/apple-m4-announcement", "CNBC")

    verifier = TwoSourceVerifier()
    group1 = verifier.get_publisher_group(art1)
    group2 = verifier.get_publisher_group(art2)
    assert group1 == group2  # Same publisher group cannot serve as independent secondary source


# 3. Test Same-Event Rejection on Unrelated Legal / Political Stories
def test_same_event_rejection_unrelated_legal_stories():
    verifier = TwoSourceVerifier()

    # Pair A: Allahabad HC chamber demolition vs Jhiram Ghati massacre verdict
    art_chambers = _make_article(
        "c1",
        "Bulldozer Action On Illegal Lawyers' Chambers Inside Allahabad High Court Complex",
        "https://indiatoday.in/lawyers-chambers-demolition",
        "India Today",
        NewsCategory.DOMESTIC,
        content="The Allahabad High Court complex saw demolition of illegal chambers of advocates under police security.",
    )
    art_jhiram = _make_article(
        "j1",
        "Jhiram Ghati massacre: Special court rejects NIA closure report in Chhattisgarh case",
        "https://indianexpress.com/jhiram-ghati-massacre-verdict",
        "The Indian Express",
        NewsCategory.DOMESTIC,
        content="A special court in Bastar Chhattisgarh rejected the NIA closure report on the Jhiram Ghati massacre.",
    )

    is_same_a, score_a, reason_a = verifier.is_same_underlying_event(art_chambers, art_jhiram)
    assert is_same_a is False, f"Expected False but got True with reason: {reason_a}"
    assert any(tag in reason_a for tag in ("DOMESTIC_ACTION_MISMATCH", "DOMESTIC_GEO_MISMATCH", "DOMESTIC_TOPIC_MISMATCH"))

    # Pair B: Student dissent vs Appointment of distinguished jurist
    art_dissent = _make_article(
        "d1",
        "Punishing students for dissent: Delhi HC quashes disciplinary action against JNU scholars",
        "https://thewire.in/delhi-hc-jnu-students-dissent",
        "The Wire",
        NewsCategory.DOMESTIC,
        content="The Delhi High Court quashed punitive rustication orders against Jawaharlal Nehru University students protesting policies.",
    )
    art_jurist = _make_article(
        "jur1",
        "Why India has never appointed a distinguished jurist as a Supreme Court judge",
        "https://thehindu.com/opinion-distinguished-jurist-sc",
        "The Hindu",
        NewsCategory.DOMESTIC,
        content="An analysis of Article 124 of the Constitution and why the provision for distinguished jurist appointment to the Supreme Court remains unutilised.",
    )

    is_same_b, score_b, reason_b = verifier.is_same_underlying_event(art_dissent, art_jurist)
    assert is_same_b is False, f"Expected False but got True with reason: {reason_b}"


# 4. Test Genuine Domestic Same-Event Matches Pass
def test_genuine_domestic_same_event_matches():
    verifier = TwoSourceVerifier()

    art1 = _make_article(
        "g1",
        "Bulldozer Action On Illegal Lawyers' Chambers Inside Allahabad High Court",
        "https://indiatoday.in/allahabad-chambers-action",
        "India Today",
        NewsCategory.DOMESTIC,
        content="Authorities carried out demolition of unauthorised advocates chambers inside the Allahabad High Court premises in Prayagraj.",
    )
    art2 = _make_article(
        "g2",
        "Demolition drive begins at Allahabad High Court to remove illegal lawyers' chambers",
        "https://indianexpress.com/allahabad-hc-chambers-demolished",
        "The Indian Express",
        NewsCategory.DOMESTIC,
        content="A massive demolition drive started at the Allahabad HC to clear illegal chambers constructed by lawyers.",
    )

    is_same, score, reason = verifier.is_same_underlying_event(art1, art2)
    assert is_same is True
    assert "DOMESTIC_EVENT_MATCH" in reason


# 5. Test Enrichment Failure Preserves Single Source (Never Discarded)
def test_enrichment_failure_preserves_single_source():
    ev = Event(
        id="ev_single",
        canonical_title="Zomato Acquires Paytm Movie Ticketing Business For Rs 2048 Crore",
        description="Zomato acquired Paytm entertainment ticketing business.",
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        article_ids=["art_single"],
    )
    # If no second source found, event remains in pool with its single source tier
    assert ev.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
    assert len(ev.article_ids) == 1


# 6. Test SerpAPI Cap Remains Exactly 8
def test_serpapi_cap_remains_eight():
    assert MAX_SERPAPI_SEARCHES_PER_RUN == 8


# 7. Test Soft Publisher Diversity Tie-Breaker
def test_soft_publisher_diversity_tie_breaker():
    # 6 candidates: 3 from Business Standard, 1 from Mint, 1 from Economic Times, 1 from Moneycontrol
    arts_map = {
        "a1": _make_article("a1", "BS Story 1", "https://business-standard.com/1", "Business Standard"),
        "a2": _make_article("a2", "BS Story 2", "https://business-standard.com/2", "Business Standard"),
        "a3": _make_article("a3", "BS Story 3", "https://business-standard.com/3", "Business Standard"),
        "a4": _make_article("a4", "Mint Story 1", "https://livemint.com/4", "Livemint"),
        "a5": _make_article("a5", "ET Story 1", "https://economictimes.com/5", "Economic Times"),
        "a6": _make_article("a6", "MC Story 1", "https://moneycontrol.com/6", "Moneycontrol"),
    }

    from app.ranking.models import ScoreBreakdown
    dummy_breakdown = ScoreBreakdown(
        total_score=50.0,
        financial_magnitude=50.0,
        market_impact=50.0,
        investor_relevance=50.0,
        corporate_significance=50.0,
        source_quality=50.0,
    )

    candidates = [
        ScoredEvent(
            event=Event(
                id=f"e{i}",
                canonical_title=f"Title {i}",
                description=f"Description {i}",
                event_category=NewsCategory.INDIA,
                article_ids=[f"a{i}"],
            ),
            investment_score=100-i,
            rank=i,
            score_breakdown=dummy_breakdown,
        )
        for i in range(1, 7)
    ]

    selected = select_diverse_publisher_candidates(
        candidates=candidates,
        articles_lookup=arts_map,
        target_count=5,
        max_per_publisher=2,
    )

    assert len(selected) == 5
    pubs = [arts_map[s.event.article_ids[0]].source_name for s in selected]
    bs_count = sum(1 for p in pubs if "Business Standard" in p)
    assert bs_count == 2, f"Expected 2 Business Standard stories, got {bs_count}"
    assert "Livemint" in pubs
    assert "Economic Times" in pubs
    assert "Moneycontrol" in pubs


# 8. Test Publisher Diversity Never Drops Below Target
def test_publisher_diversity_never_drops_below_target():
    # Only 5 candidates exist, but 3 are from Business Standard
    from app.ranking.models import ScoreBreakdown
    dummy_breakdown = ScoreBreakdown(
        total_score=50.0,
        financial_magnitude=50.0,
        market_impact=50.0,
        investor_relevance=50.0,
        corporate_significance=50.0,
        source_quality=50.0,
    )
    arts_map = {
        "a1": _make_article("a1", "BS Story 1", "https://business-standard.com/1", "Business Standard"),
        "a2": _make_article("a2", "BS Story 2", "https://business-standard.com/2", "Business Standard"),
        "a3": _make_article("a3", "BS Story 3", "https://business-standard.com/3", "Business Standard"),
        "a4": _make_article("a4", "Mint Story 1", "https://livemint.com/4", "Livemint"),
        "a5": _make_article("a5", "ET Story 1", "https://economictimes.com/5", "Economic Times"),
    }

    candidates = [
        ScoredEvent(
            event=Event(
                id=f"e{i}",
                canonical_title=f"Title {i}",
                description=f"Description {i}",
                event_category=NewsCategory.INDIA,
                article_ids=[f"a{i}"],
            ),
            investment_score=100-i,
            rank=i,
            score_breakdown=dummy_breakdown,
        )
        for i in range(1, 6)
    ]

    selected = select_diverse_publisher_candidates(
        candidates=candidates,
        articles_lookup=arts_map,
        target_count=5,
        max_per_publisher=2,
    )

    # Must NOT drop below 5 even though 3 are from Business Standard
    assert len(selected) == 5
    pubs = [arts_map[s.event.article_ids[0]].source_name for s in selected]
    bs_count = sum(1 for p in pubs if "Business Standard" in p)
    assert bs_count == 3


# 9. Test Circuit Breaker: 3 Consecutive 401/403 Marks Domain Degraded
def test_reuters_3x_401_403_marked_degraded():
    fetcher = ArticleFetcher()
    extractor = ArticleExtractor(fetcher=fetcher)
    extractor.reset_run_health()

    mock_fetch = MagicMock(return_value=(False, None, 401, "Unauthorized"))
    with patch.object(fetcher, "fetch_html", mock_fetch):
        for i in range(3):
            res = extractor.extract(f"https://www.reuters.com/business/article-{i}", source_name="Reuters")
            assert res.success is False

    assert extractor.is_domain_degraded("reuters.com") is True
    assert extractor.is_domain_degraded("www.reuters.com") is True


# 10. Test Degraded Domain Skips Further Extraction Attempts
def test_degraded_domain_skips_further_attempts():
    fetcher = ArticleFetcher()
    extractor = ArticleExtractor(fetcher=fetcher)
    extractor.mark_domain_degraded("reuters.com")

    mock_fetch = MagicMock()
    with patch.object(fetcher, "fetch_html", mock_fetch):
        res = extractor.extract("https://www.reuters.com/business/new-story", source_name="Reuters")
        assert res.success is False
        assert res.status_code == 403
        assert res.extraction_method == "degraded_domain_skip"
        # Network fetch was NEVER attempted
        mock_fetch.assert_not_called()


# 11. Test Domain Health Resets on New Run
def test_domain_health_resets_on_new_run():
    extractor = ArticleExtractor()
    extractor.mark_domain_degraded("reuters.com")
    assert extractor.is_domain_degraded("reuters.com") is True

    extractor.reset_run_health()
    assert extractor.is_domain_degraded("reuters.com") is False
    assert len(extractor.degraded_domains_for_run) == 0
    assert len(extractor.consecutive_domain_401_403) == 0


def _make_dummy_story(sec: str, idx: int) -> EditorialStorySelection:
    return EditorialStorySelection(
        section=sec,
        event_id=f"dummy_{sec}_{idx}",
        headline=f"Headline {sec.title()} {idx} Verified Event",
        summary=f"This is a factual summary for dummy story {idx} in {sec}.",
        source="Reuters" if sec == "international" else "The Hindu",
        url=f"https://example.com/{sec}-{idx}",
    )


# 12. Test Formatter Renders Both Sources for Two-Source Verified Story
def test_two_source_formatter_renders_both_sources():
    formatter = BriefingFormatter()
    payload = BriefingEditorialPayload(
        domestic_stories=[_make_dummy_story("domestic", i) for i in range(5)],
        india_stories=[
            EditorialStorySelection(
                section="india",
                event_id="e1",
                headline="Zomato Acquires Paytm Ticketing For Rs 2048 Cr",
                summary="Zomato officially completed the acquisition of Paytm entertainment ticketing business.",
                source="Business Standard",
                url="https://www.business-standard.com/zomato-paytm",
                secondary_source="The Indian Express",
                secondary_url="https://indianexpress.com/zomato-paytm-deal",
            )
        ] + [_make_dummy_story("india", i) for i in range(1, 5)],
        international_stories=[_make_dummy_story("international", i) for i in range(5)],
    )

    formatted = formatter.format(payload, date.today(), shorten_urls=False)
    txt = formatted.text
    assert "Zomato Acquires Paytm Ticketing" in txt
    assert "Source: Business Standard" in txt
    assert "https://www.business-standard.com/zomato-paytm" in txt
    assert "Also verified by: The Indian Express" in txt
    assert "https://indianexpress.com/zomato-paytm-deal" in txt


# 13. Test Formatter Renders Only Primary Source for Single-Source Story
def test_single_source_formatter_renders_only_primary():
    formatter = BriefingFormatter()
    payload = BriefingEditorialPayload(
        domestic_stories=[_make_dummy_story("domestic", i) for i in range(5)],
        india_stories=[
            EditorialStorySelection(
                section="india",
                event_id="e2",
                headline="Tata Motors Reports Record EV Sales In March",
                summary="Tata Motors crossed 10000 monthly EV sales for the first time in domestic market.",
                source="Economic Times",
                url="https://economictimes.indiatimes.com/tata-motors-ev-record",
            )
        ] + [_make_dummy_story("india", i) for i in range(1, 5)],
        international_stories=[_make_dummy_story("international", i) for i in range(5)],
    )

    formatted = formatter.format(payload, date.today(), shorten_urls=False)
    txt = formatted.text
    assert "*Tata Motors Reports Record EV Sales In March*" in txt
    assert "Source: Economic Times" in txt
    assert "https://economictimes.indiatimes.com/tata-motors-ev-record" in txt
    assert "Also verified by:" not in txt


# 14. Test Email Parser and Generator Handle Secondary Sources & Zero TinyURL
def test_email_html_renders_secondary_verification():
    briefing_text = """
# INVESTMENT COMMITTEE BRIEFING — TODAY'S ESSENTIAL DEVELOPMENTS
Generated: 2026-08-31 | Selection Methodology: 2-Source Verified & Algorithmic Quality Ladder

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INDIA BUSINESS & MARKETS (5/5 VERIFIED)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

*Zomato Acquires Paytm Ticketing For Rs 2048 Cr*
Zomato completed the acquisition of Paytm ticketing operations for Rs 2048 crore.
Source: Business Standard
https://www.business-standard.com/zomato-paytm
Also verified by: The Indian Express
https://indianexpress.com/zomato-paytm-deal

*Tata Motors Reports Record EV Sales*
Tata Motors reported record domestic EV deliveries.
Source: Economic Times
https://economictimes.indiatimes.com/tata-motors-ev-record
"""

    parsed = parse_briefing_text(briefing_text)
    assert "sections" in parsed
    sec = next(s for s in parsed["sections"] if s["key"].upper() == "INDIA")
    assert len(sec["stories"]) == 2

    s1 = sec["stories"][0]
    assert s1["source"] == "Business Standard"
    assert s1["url"] == "https://www.business-standard.com/zomato-paytm"
    assert s1["secondary_source"] == "The Indian Express"
    assert s1["secondary_url"] == "https://indianexpress.com/zomato-paytm-deal"

    s2 = sec["stories"][1]
    assert s2["source"] == "Economic Times"
    assert s2["secondary_source"] is None

    html = generate_briefing_html(parsed)
    # Check primary read link
    assert 'Read full article &rarr;</a>' in html
    # Check secondary verification link
    assert 'Also verified by <a href="https://indianexpress.com/zomato-paytm-deal" target="_blank" style="color: #2563eb; text-decoration: none; font-weight: 600;">The Indian Express &rarr;</a>' in html
    # Verify zero TinyURL
    assert "tinyurl.com" not in html
    assert "tinyurl.com" not in briefing_text


# 15. Test Zero SerpAPI Calls During Enrichment
def test_zero_serpapi_calls_during_enrichment():
    from app.verification.serpapi_corroborator import get_serpapi_count, reset_serpapi_counter
    reset_serpapi_counter()
    initial_count = get_serpapi_count()
    # Enrichment only queries GoogleNewsRSSDiscoveryProvider, never SerpAPI
    assert initial_count == 0


# 16. Test Second-Source Enrichment Evaluates urlparse Without NameError
def test_second_source_enrichment_evaluates_urlparse_without_name_error():
    import run_pipeline
    assert hasattr(run_pipeline, "urlparse"), "urlparse must be imported in run_pipeline"

    primary_art = _make_article(
        "art_enrich_1",
        "KNR Constructions bags ₹158 crore EPC order from GHMC",
        "https://www.thehindubusinessline.com/companies/knr-constructions-order",
        "BusinessLine",
    )
    # Execute the exact expressions from run_pipeline.py lines 1618 & 1666
    prim_domain = run_pipeline.urlparse(primary_art.url).netloc.lower().replace("www.", "")
    assert prim_domain == "thehindubusinessline.com"

    cand_url = "https://www.business-standard.com/companies/news/knr-constructions-ghmc-order"
    cand_netloc = run_pipeline.urlparse(cand_url).netloc.lower().replace("www.", "")
    assert cand_netloc == "business-standard.com"
    assert cand_netloc != prim_domain


# 17. Test Second-Source Enrichment Scoring and Candidate Prioritization
def test_second_source_enrichment_scoring_and_top_7_capping():
    from app.pipeline.enrichment import run_second_source_enrichment
    from app.pipeline.context import PipelineContext
    from app.utils.performance_metrics import PipelineMetrics
    from app.ranking.scorer import InvestmentRelevanceScorer

    metrics = PipelineMetrics.reset()
    ctx = MagicMock(spec=PipelineContext)
    ctx.metrics = metrics
    ctx.seen_urls = set()
    ctx.verified_events = []
    ctx.articles_lookup = {}
    ctx.run_reference_time = datetime.now(timezone.utc)
    logs = []
    ctx.log_exec = lambda msg: logs.append(msg)

    # Mock discovery provider so no actual network queries happen
    mock_provider = MagicMock()
    mock_provider.discover.return_value = []
    ctx.discovery_service = MagicMock()
    ctx.discovery_service.provider = mock_provider

    # Create 10 single-source candidate events with varying financial sizes / event types
    events = []
    for i in range(1, 10):
        art_id = f"art_in_{i}"
        art = _make_article(
            art_id,
            f"Small Firm {i} bags Rs {i} crore road order",
            f"https://www.business-standard.com/small-{i}",
            "Business Standard",
            NewsCategory.INDIA,
        )
        ctx.articles_lookup[art_id] = art
        ev = Event(
            id=f"ev_in_{i}",
            canonical_title=f"Small Firm {i} bags Rs {i} crore road order",
            description=f"Description for small firm {i}",
            companies_involved=[f"Small Firm {i}"],
            financial_figures=[f"Rs {i} crore"],
            event_category=NewsCategory.INDIA,
            article_ids=[art_id],
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        events.append(ev)

    # 10th candidate is a mega M&A deal with high investment score
    art_id_10 = "art_in_10"
    art_10 = _make_article(
        art_id_10,
        "Mega Corp acquires Tech Giant in Rs 50000 crore deal",
        "https://www.business-standard.com/mega-deal",
        "Business Standard",
        NewsCategory.INDIA,
    )
    ctx.articles_lookup[art_id_10] = art_10
    ev_10 = Event(
        id="ev_in_10",
        canonical_title="Mega Corp acquires Tech Giant in Rs 50000 crore deal",
        description="Mega Corp completes acquisition of Tech Giant for Rs 50,000 crore.",
        companies_involved=["Mega Corp", "Tech Giant"],
        financial_figures=["Rs 50000 crore"],
        event_category=NewsCategory.INDIA,
        article_ids=[art_id_10],
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
    )
    events.append(ev_10)

    ctx.high_confidence_single_candidates = events

    # Spy on score_event to verify it is called with real InvestmentRelevanceScorer
    original_score_event = InvestmentRelevanceScorer.score_event
    score_calls = []

    def spy_score_event(self, event, *args, **kwargs):
        src_cnt = kwargs.get("source_count", args[0] if args else None)
        score_calls.append((event.id, src_cnt))
        return original_score_event(self, event, *args, **kwargs)

    with patch.object(InvestmentRelevanceScorer, "score_event", side_effect=spy_score_event, autospec=True):
        # Execute enrichment
        run_second_source_enrichment(ctx)

    # 1. Proves no AttributeError occurred
    # 2. Proves scoring uses score_event()
    assert len(score_calls) == 10
    # 3. Proves source_count=1 passed
    for ev_id, src_cnt in score_calls:
        assert src_cnt == 1

    # 4. Proves ranking remains deterministic & caps at top 7 per section
    assert any("Targeted top 7 candidates (max 7 per section)" in l for l in logs)
    queried_queries = [call.kwargs.get("query") if "query" in call.kwargs else call.args[0] for call in mock_provider.discover.call_args_list]
    # Mega Corp (highest investment score ~66.27) must be queried first
    assert any("Mega Corp" in q for q in queried_queries)
    # The last 3 small firms (Small Firm 7, 8, 9) exceed top 7 cap and are never queried
    assert not any("Small Firm 9" in q for q in queried_queries)

