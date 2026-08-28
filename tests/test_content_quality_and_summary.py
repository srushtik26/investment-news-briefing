"""
Regression tests for Content-Quality and One-Line Summary Patch.

Validates:
1. Part 1: Hard fix for stale explicit event dates (AVENIQUE 2019 reject vs Aug 2026 accept).
2. Part 2: Mojibake and Currency normalization in final briefing.
3. Part 3: Analyst speculation rejection vs genuine earnings acceptance.
4. Part 4: Stock picks / investment advice rejection vs corporate earnings acceptance.
5. Part 5: Domestic commentary & rhetorical question rejection vs hard government/court event acceptance.
6. Part 6: One-line factual summary synthesis, deterministic fallback, formatting, and validation.
"""

from datetime import date, datetime, timedelta, timezone
import pytest

from app.models.article import Article
from app.models.event import Event
from app.models.enums import NewsCategory, VerificationTier
from app.filtering.rules import DateFilterRule, StoryTypeFilterRule
from app.filtering.engine import HardFilterEngine
from app.verification.single_source import SingleSourceEvaluator, is_commentary_or_rumour
from app.verification.domestic_trending import DomesticTrendingEvaluator
from app.ai.models import BriefingEditorialPayload, EditorialStorySelection
from app.ai.editor import GeminiEditorialEngine, generate_deterministic_summary
from app.ranking.models import RankedCandidatePool, ScoredEvent
from app.formatting.formatter import BriefingFormatter
from app.validation.engine import FinalValidationEngine


# =========================================================================
# PART 1: STALE EXPLICIT EVENT DATE IN TITLE
# =========================================================================

def test_stale_explicit_event_date_rejection():
    """Structured financial results with old explicit dates (e.g. 2019) must be rejected."""
    ref_time = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    date_rule = DateFilterRule()

    # Stale 2019 event with recent crawler timestamp
    stale_art = Article(
        url="https://www.bseindia.com/results/avenique-2019",
        title="AVENIQUE LIMITED Quarterly Results, 18 Feb 2019 - BSE 3.65",
        source_name="BSE Corporate Announcements",
        published_at=ref_time,  # Crawler published_at timestamp might be recent
        date_verified=True,
        content_text="AVENIQUE LIMITED quarterly results for the period ended December 2018.",
    )

    res = date_rule.evaluate(stale_art, now_utc=ref_time, max_age_hours=24.0)
    assert not res.is_accepted
    assert res.rule_failed == "DATE"
    assert "Stale explicit event date" in res.rejection_reason

    # Single-source evaluator must also reject stale explicit date
    ev = Event(
        id="evt-avenique",
        canonical_title=stale_art.title,
        description="AVENIQUE LIMITED financial results.",
        event_category=NewsCategory.INDIA,
        article_ids=["art-avenique"],
    )
    evaluator = SingleSourceEvaluator()
    eligible, conf, rsn = evaluator.evaluate_event(ev, stale_art, now_utc=ref_time)
    assert not eligible
    assert "Stale explicit event date" in rsn


def test_fresh_explicit_event_date_acceptance():
    """Structured financial results with fresh explicit dates (e.g. 27 Aug 2026) must be accepted."""
    ref_time = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    date_rule = DateFilterRule()

    fresh_art1 = Article(
        url="https://www.nseindia.com/results/horizon-2026",
        title="Horizon Industrial Parks Ltd Quarterly Results, 27 Aug 2026 - NSE 56.23",
        source_name="NSE Corporate Announcements",
        published_at=datetime(2026, 8, 27, 18, 0, 0, tzinfo=timezone.utc),
        date_verified=True,
        content_text="Horizon Industrial Parks Ltd quarterly financial results for Q1 FY27.",
    )
    res1 = date_rule.evaluate(fresh_art1, now_utc=ref_time, max_age_hours=24.0)
    assert res1.is_accepted

    fresh_art2 = Article(
        url="https://www.nseindia.com/results/lalithaa-2026",
        title="Lalithaa Jewellery Mart Ltd Quarterly Results, 27 Aug 2026 - NSE 248.13",
        source_name="NSE Corporate Announcements",
        published_at=datetime(2026, 8, 27, 18, 0, 0, tzinfo=timezone.utc),
        date_verified=True,
        content_text="Lalithaa Jewellery Mart Ltd reports robust Q1 results with revenue growth of 22%.",
    )
    res2 = date_rule.evaluate(fresh_art2, now_utc=ref_time, max_age_hours=24.0)
    assert res2.is_accepted


# =========================================================================
# PART 2: MOJIBAKE AND INDIAN CURRENCY CLEANING
# =========================================================================

def test_mojibake_and_currency_cleaning():
    """Verify all known mojibake and currency representations are cleaned correctly."""
    dirty_text = (
        "Deepinder GoyalΓÇÖs Temple acquires stake for Γé╣994 crore┬áworth. "
        "SalesforceΓÇÖs stock gets an Anthropic boost ΓÇö while CrowdStrikeΓÇÖs revenue hits Rs 4,300 crore. "
        "ΓÇÿTwo systemsΓÇÖ exist: Rs. 994 crore and Rs 2,217 crore."
    )
    cleaned = BriefingFormatter.clean_text(dirty_text)

    assert "Deepinder Goyal’s" in cleaned
    assert "₹994 crore worth" in cleaned
    assert "Salesforce’s" in cleaned
    assert "—" in cleaned
    assert "CrowdStrike’s" in cleaned
    assert "₹4,300 crore" in cleaned
    assert "‘Two systems’" in cleaned
    assert "₹994 crore" in cleaned
    assert "₹2,217 crore" in cleaned
    assert "Γ" not in cleaned
    assert "┬" not in cleaned


# =========================================================================
# PART 3: ANALYST SPECULATION REJECTION
# =========================================================================

def test_analyst_speculation_rejection():
    """Reject analyst-only hypothetical speculation without concrete corporate events."""
    rule = StoryTypeFilterRule()

    spec_art = Article(
        url="https://www.cnbc.com/2026/08/27/is-nvidia-heading-for-1-trillion.html",
        title="Is Nvidia heading for $1 trillion in annual revenue? One analyst now thinks that’s possible.",
        source_name="CNBC",
        published_at=datetime(2026, 8, 27, 14, 0, 0, tzinfo=timezone.utc),
        content_text="Wall Street thinks Nvidia could hit higher revenue targets according to an analyst note.",
    )
    res = rule.evaluate(spec_art)
    assert not res.is_accepted
    assert "analyst_speculation" in res.rejection_reason or "analyst" in res.rejection_reason


def test_genuine_earnings_acceptance():
    """Do NOT reject genuine earnings reports containing estimates/forecast beats."""
    rule = StoryTypeFilterRule()

    good_art1 = Article(
        url="https://www.cnbc.com/2026/08/27/best-buy-earnings.html",
        title="Best Buy beats quarterly estimates and hikes its full-year outlook",
        source_name="CNBC",
        published_at=datetime(2026, 8, 27, 14, 0, 0, tzinfo=timezone.utc),
        content_text="Best Buy reported Q2 earnings per share of $1.34 beating Wall Street estimates of $1.16.",
    )
    res1 = rule.evaluate(good_art1)
    assert res1.is_accepted

    good_art2 = Article(
        url="https://www.reuters.com/2026/08/27/nvidia-q2-results.html",
        title="Strong AI chip demand powers Nvidia's Q2 results past Wall Street's expectations",
        source_name="Reuters",
        published_at=datetime(2026, 8, 27, 14, 0, 0, tzinfo=timezone.utc),
        content_text="Nvidia posted record quarterly revenue of $30.0 billion, up 122% YoY.",
    )
    res2 = rule.evaluate(good_art2)
    assert res2.is_accepted


# =========================================================================
# PART 4: STOCK PICKS / INVESTMENT ADVICE REJECTION
# =========================================================================

def test_stock_picks_advice_rejection():
    """Reject stock recommendation combinations and portfolio advice articles."""
    rule = StoryTypeFilterRule()

    advice_art = Article(
        url="https://www.cnbc.com/2026/08/27/these-dividend-stocks-portfolio-boost.html",
        title="These dividend stocks could give your portfolio a boost, says Bank of America",
        source_name="CNBC",
        published_at=datetime(2026, 8, 27, 14, 0, 0, tzinfo=timezone.utc),
        content_text="Bank of America recommends buying these top dividend stocks for income investors.",
    )
    res = rule.evaluate(advice_art)
    assert not res.is_accepted
    assert "investment_advice" in res.rejection_reason or "analyst" in res.rejection_reason


def test_genuine_bank_earnings_acceptance():
    """Do NOT reject corporate events where the bank itself is reporting."""
    rule = StoryTypeFilterRule()

    bank_art = Article(
        url="https://www.reuters.com/2026/08/27/bank-of-america-q2-earnings.html",
        title="Bank of America reports Q2 net profit of $6.9 billion as investment banking fees surge 29%",
        source_name="Reuters",
        published_at=datetime(2026, 8, 27, 14, 0, 0, tzinfo=timezone.utc),
        content_text="Bank of America posted Q2 profit of $6.9 billion compared to $7.4 billion a year earlier.",
    )
    res = rule.evaluate(bank_art)
    assert res.is_accepted


# =========================================================================
# PART 5: DOMESTIC COMMENTARY / RHETORICAL QUESTIONS
# =========================================================================

def test_domestic_commentary_rejection():
    """Domestic questions and debate commentary without concrete events must be rejected."""
    evaluator = DomesticTrendingEvaluator()

    is_noise, rsn = evaluator.is_domestic_noise(
        title="Big ticket infrastructure projects or white elephants?",
        text="Analysis on whether mega projects are yielding expected economic returns.",
        url="https://www.thehindu.com/opinion/open-page/infrastructure-projects/article.ece",
    )
    assert is_noise


def test_domestic_concrete_court_event_acceptance():
    """Hard court rulings or government actions must be accepted."""
    evaluator = DomesticTrendingEvaluator()

    is_noise1, _ = evaluator.is_domestic_noise(
        title="Supreme Court seeks DNA profiling of captive elephants across states",
        text="The Supreme Court on Thursday directed all states to submit comprehensive data.",
        url="https://www.thehindu.com/news/national/supreme-court-elephants/article.ece",
    )
    assert not is_noise1

    is_noise2, _ = evaluator.is_domestic_noise(
        title="Government approves ₹10,000 crore infrastructure project",
        text="The Union Cabinet on Wednesday cleared the mega infrastructure corridor project.",
        url="https://www.thehindu.com/news/national/cabinet-approves-infrastructure-project/article.ece",
    )
    assert not is_noise2

    is_noise3, _ = evaluator.is_domestic_noise(
        title="Supreme Court orders Centre to respond within two weeks",
        text="The bench headed by CJI directed the central government to file its compliance affidavit.",
        url="https://www.thehindu.com/news/national/supreme-court-orders-centre/article.ece",
    )
    assert not is_noise3


# =========================================================================
# PART 6: ONE-LINE FACTUAL SUMMARY SYNTHESIS & FORMATTING
# =========================================================================

def test_deterministic_summary_generation():
    """Generate concise factual summaries without Gemini API calls."""
    art = Article(
        url="https://www.business-standard.com/reliance-q1",
        title="Reliance Industries posts ₹19,878 cr Q1 net profit, up 12%",
        source_name="Business Standard",
        content_text="Reliance Industries reported a 12% rise in consolidated net profit to Rs 19,878 crore for the first quarter ended June 30, driven by steady oil-to-chemicals performance and retail growth.",
    )
    ev = Event(
        id="evt-rel",
        canonical_title=art.title,
        description="Reliance Industries reported a 12% rise in consolidated net profit.",
        event_category=NewsCategory.INDIA,
        article_ids=["art-rel"],
    )
    summary = generate_deterministic_summary(art, ev)
    assert summary.endswith(".")
    assert "₹19,878 crore" in summary or "Reliance" in summary
    assert len(summary.split()) >= 10
    assert len(summary.split()) <= 30
    assert "Γ" not in summary


def test_formatter_renders_summary_and_preserves_section_order():
    """Formatter must render 5 India -> 5 Domestic -> 5 International with summaries."""
    def make_story(sec, idx):
        return EditorialStorySelection(
            section=sec,
            event_id=f"evt-{sec}-{idx}",
            headline=f"Sample Headline for {sec.capitalize()} {idx}",
            summary=f"This is the factual one-line summary for {sec.capitalize()} story number {idx}.",
            source="Business Standard" if sec != "international" else "Reuters",
            url=f"https://www.example.com/{sec}-{idx}",
        )

    payload = BriefingEditorialPayload(
        india_stories=[make_story("india", i) for i in range(1, 6)],
        domestic_stories=[make_story("domestic", i) for i in range(1, 6)],
        international_stories=[make_story("international", i) for i in range(1, 6)],
    )

    formatter = BriefingFormatter()
    briefing = formatter.format(payload, briefing_date=date(2026, 8, 28), shorten_urls=False)
    text = briefing.text

    # Verify section order
    india_pos = text.find("*TOP 5 INDIA BUSINESS HEADLINES*")
    dom_pos = text.find("*TOP 5 DOMESTIC HEADLINES*")
    intl_pos = text.find("*TOP 5 INTERNATIONAL BUSINESS HEADLINES*")

    assert india_pos != -1
    assert dom_pos != -1
    assert intl_pos != -1
    assert india_pos < dom_pos < intl_pos

    # Verify summary lines appear
    assert "This is the factual one-line summary for India story number 1." in text
    assert "This is the factual one-line summary for Domestic story number 1." in text
    assert "This is the factual one-line summary for International story number 1." in text

    # Verify 15 stories formatted
    assert briefing.total_count == 15
    assert briefing.india_count == 5
    assert briefing.domestic_count == 5
    assert briefing.international_count == 5


def test_validation_engine_summary_checks():
    """Validation Check 20 passes with valid summaries and catches malformed ones."""
    validator = FinalValidationEngine()
    now_utc = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    target_d = now_utc.date()

    def make_story(sec, idx, summary_override=None):
        sum_val = summary_override if summary_override is not None else f"One-line factual summary for {sec} story {idx} with key figures."
        if sec == "domestic":
            h = f"Supreme Court Bench Issues Milestone Order {idx} on National Policy"
        elif sec == "india":
            h = f"CompanyIndia{idx} Q1 Net Profit Rises {idx * 5}% YoY to ₹{idx * 1000} Crore"
        else:
            h = f"GlobalFirm{idx} Signs ${idx * 2}.5 Billion Acquisition Agreement"

        return EditorialStorySelection(
            section=sec,
            event_id=f"evt-{sec}-{idx}",
            headline=h,
            summary=sum_val,
            source="Business Standard" if sec != "international" else "Reuters",
            url=f"https://www.business-standard.com/companies/{sec}-{idx}-deal.html" if sec != "international" else f"https://www.reuters.com/business/{sec}-{idx}-deal.html",
        )

    events_map = {}
    articles_map = {}
    candidate_urls = set()

    for sec in ("domestic", "india", "international"):
        for i in range(1, 6):
            eid = f"evt-{sec}-{i}"
            aid1 = f"art-{sec}-a-{i}"
            aid2 = f"art-{sec}-b-{i}"
            src1 = "Business Standard" if sec != "international" else "Reuters"
            src2 = "The Economic Times" if sec != "international" else "Bloomberg"
            u1 = f"https://www.business-standard.com/companies/{sec}-{i}-deal.html" if sec != "international" else f"https://www.reuters.com/business/{sec}-{i}-deal.html"
            u2 = f"https://economictimes.indiatimes.com/news/{sec}-{i}-deal.html" if sec != "international" else f"https://www.bloomberg.com/news/{sec}-{i}-deal.html"
            cat = NewsCategory.DOMESTIC if sec == "domestic" else (NewsCategory.INDIA if sec == "india" else NewsCategory.INTERNATIONAL)

            if sec == "domestic":
                title1 = f"Supreme Court Bench Issues Milestone Order {i} on National Policy"
                body1 = f"The Supreme Court of India on Thursday issued directives for national policy implementation across states."
                figs = ["Order 1"]
                comps = ["Supreme Court of India"]
            elif sec == "india":
                title1 = f"CompanyIndia{i} Q1 Net Profit Rises {i * 5}% YoY to ₹{i * 1000} Crore"
                body1 = f"CompanyIndia{i} reported a net profit increase of {i * 5}% to ₹{i * 1000} crore for Q1 FY27."
                figs = [f"₹{i * 1000} crore", f"{i * 5}%"]
                comps = [f"CompanyIndia{i}"]
            else:
                title1 = f"GlobalFirm{i} Signs ${i * 2}.5 Billion Acquisition Agreement"
                body1 = f"GlobalFirm{i} has announced a definitive agreement to acquire its peer in an all-cash deal worth ${i * 2}.5 billion."
                figs = [f"${i * 2}.5 billion"]
                comps = [f"GlobalFirm{i}"]

            art1 = Article(
                id=aid1,
                url=u1,
                title=title1,
                source_name=src1,
                published_at=now_utc - timedelta(hours=4),
                content_text=body1,
                category=cat,
                is_verified_url=True,
                date_verified=True,
                is_valid_date=True,
            )
            art2 = Article(
                id=aid2,
                url=u2,
                title=title1,
                source_name=src2,
                published_at=now_utc - timedelta(hours=5),
                content_text=body1,
                category=cat,
                is_verified_url=True,
                date_verified=True,
                is_valid_date=True,
            )
            ev = Event(
                id=eid,
                canonical_title=title1,
                description=body1,
                event_category=cat,
                companies_involved=comps,
                financial_figures=figs,
                article_ids=[aid1, aid2],
                verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
                verification_confidence=95.0,
            )
            articles_map[aid1] = art1
            articles_map[aid2] = art2
            events_map[eid] = ev
            candidate_urls.add(u1)
            candidate_urls.add(u2)

    payload = BriefingEditorialPayload(
        domestic_stories=[make_story("domestic", i) for i in range(1, 6)],
        india_stories=[make_story("india", i) for i in range(1, 6)],
        international_stories=[make_story("international", i) for i in range(1, 6)],
    )

    report = validator.validate_briefing(
        payload=payload,
        events_lookup=events_map,
        articles_lookup=articles_map,
        candidate_urls=candidate_urls,
        target_date=target_d,
        quality_ladder_mode=True,
        run_reference_time=now_utc,
    )
    assert report.is_valid, f"Validation failed on check #{report.failed_check_id}: {report.failure_reason}"
    assert report.passed_checks == 20

    # Test malformed summary detection
    bad_payload = BriefingEditorialPayload(
        domestic_stories=[make_story("domestic", 1, summary_override="Line 1\nLine 2")] + [make_story("domestic", i) for i in range(2, 6)],
        india_stories=[make_story("india", i) for i in range(1, 6)],
        international_stories=[make_story("international", i) for i in range(1, 6)],
    )
    bad_report = validator.validate_briefing(
        payload=bad_payload,
        events_lookup=events_map,
        articles_lookup=articles_map,
        candidate_urls=candidate_urls,
        target_date=target_d,
        quality_ladder_mode=True,
        run_reference_time=now_utc,
    )
    assert not bad_report.is_valid
    assert bad_report.failed_check_id == 20
    assert "embedded newlines" in bad_report.failure_reason


def test_internal_hyphen_summaries_are_valid():
    """Verify that normal internal hyphens in summaries are NOT rejected as markdown bullets."""
    validator = FinalValidationEngine()
    now_utc = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    target_d = now_utc.date()

    valid_hyphen_summaries = [
        "The Supreme Court gave the Centre a two-week deadline to respond.",
        "Best Buy raised its full-year outlook after beating quarterly estimates.",
        "The government proposed front-of-pack nutrition labelling requirements.",
        "The London-based company was acquired for an undisclosed amount.",
    ]

    for idx, hyp_sum in enumerate(valid_hyphen_summaries, 1):
        def make_story(sec, i):
            sum_val = hyp_sum if (sec == "domestic" and i == 1) else f"One-line factual summary for {sec} story {i}."
            h = f"CompanyIndia{i} Q1 Net Profit Rises {i * 5}% YoY to ₹{i * 1000} Crore" if sec == "india" else (
                f"Supreme Court Bench Issues Milestone Order {i} on Policy" if sec == "domestic" else f"GlobalFirm{i} Signs ${i * 2}.5 Billion Acquisition Deal"
            )
            return EditorialStorySelection(
                section=sec,
                event_id=f"evt-{sec}-{i}",
                headline=h,
                summary=sum_val,
                source="Business Standard" if sec != "international" else "Reuters",
                url=f"https://www.business-standard.com/{sec}-{i}.html" if sec != "international" else f"https://www.reuters.com/{sec}-{i}.html",
            )

        events_map = {}
        articles_map = {}
        candidate_urls = set()

        for sec in ("domestic", "india", "international"):
            for i in range(1, 6):
                eid = f"evt-{sec}-{i}"
                aid1 = f"art-{sec}-a-{i}"
                aid2 = f"art-{sec}-b-{i}"
                u1 = f"https://www.business-standard.com/{sec}-{i}.html" if sec != "international" else f"https://www.reuters.com/{sec}-{i}.html"
                u2 = f"https://economictimes.indiatimes.com/{sec}-{i}.html" if sec != "international" else f"https://www.bloomberg.com/{sec}-{i}.html"
                cat = NewsCategory.DOMESTIC if sec == "domestic" else (NewsCategory.INDIA if sec == "india" else NewsCategory.INTERNATIONAL)
                t = f"CompanyIndia{i} Q1 Net Profit Rises {i * 5}% YoY to ₹{i * 1000} Crore" if sec == "india" else (
                    f"Supreme Court Bench Issues Milestone Order {i} on Policy" if sec == "domestic" else f"GlobalFirm{i} Signs ${i * 2}.5 Billion Acquisition Deal"
                )
                b = f"Reported body text with financial figures ₹{i * 1000} crore or $1.5 billion."

                art1 = Article(id=aid1, url=u1, title=t, source_name="Business Standard", published_at=now_utc - timedelta(hours=4), content_text=b, category=cat, is_verified_url=True, date_verified=True, is_valid_date=True)
                art2 = Article(id=aid2, url=u2, title=t, source_name="The Economic Times", published_at=now_utc - timedelta(hours=5), content_text=b, category=cat, is_verified_url=True, date_verified=True, is_valid_date=True)
                ev = Event(id=eid, canonical_title=t, description=b, event_category=cat, companies_involved=[f"Comp{i}"], financial_figures=[f"₹{i * 1000} crore"], article_ids=[aid1, aid2], verification_tier=VerificationTier.TWO_SOURCE_VERIFIED, verification_confidence=95.0)
                articles_map[aid1] = art1
                articles_map[aid2] = art2
                events_map[eid] = ev
                candidate_urls.add(u1)
                candidate_urls.add(u2)

        payload = BriefingEditorialPayload(
            domestic_stories=[make_story("domestic", i) for i in range(1, 6)],
            india_stories=[make_story("india", i) for i in range(1, 6)],
            international_stories=[make_story("international", i) for i in range(1, 6)],
        )

        report = validator.validate_briefing(
            payload=payload,
            events_lookup=events_map,
            articles_lookup=articles_map,
            candidate_urls=candidate_urls,
            target_date=target_d,
            quality_ladder_mode=True,
            run_reference_time=now_utc,
        )
        assert report.is_valid, f"Hyphenated summary #{idx} '{hyp_sum}' failed check #{report.failed_check_id}: {report.failure_reason}"


def test_summary_word_count_validation_limits():
    """Verify 12-word summary passes and 31-word summary fails validation."""
    validator = FinalValidationEngine()
    now_utc = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    target_d = now_utc.date()

    # 12-word summary: must pass (non-empty, <= 30 words)
    twelve_word_sum = "The Supreme Court on Thursday directed all states to submit comprehensive data."
    assert len(twelve_word_sum.split()) == 12

    # 31-word summary: must fail (exceeds max 30 words)
    thirty_one_word_sum = "The Supreme Court on Thursday directed all states to submit comprehensive data regarding captive elephant welfare while ordering immediate compliance across regional authorities within a two-week period following extensive judicial hearings."
    assert len(thirty_one_word_sum.split()) == 31

    def make_payload(summary_val):
        def make_story(sec, i):
            sum_text = summary_val if (sec == "domestic" and i == 1) else f"One-line factual summary for {sec} story {i}."
            h = f"CompanyIndia{i} Q1 Net Profit Rises {i * 5}% YoY to ₹{i * 1000} Crore" if sec == "india" else (
                f"Supreme Court Bench Issues Milestone Order {i} on Policy" if sec == "domestic" else f"GlobalFirm{i} Signs ${i * 2}.5 Billion Acquisition Deal"
            )
            return EditorialStorySelection(
                section=sec,
                event_id=f"evt-{sec}-{i}",
                headline=h,
                summary=sum_text,
                source="Business Standard" if sec != "international" else "Reuters",
                url=f"https://www.business-standard.com/{sec}-{i}.html" if sec != "international" else f"https://www.reuters.com/{sec}-{i}.html",
            )
        return BriefingEditorialPayload(
            domestic_stories=[make_story("domestic", i) for i in range(1, 6)],
            india_stories=[make_story("india", i) for i in range(1, 6)],
            international_stories=[make_story("international", i) for i in range(1, 6)],
        )

    events_map = {}
    articles_map = {}
    candidate_urls = set()
    for sec in ("domestic", "india", "international"):
        for i in range(1, 6):
            eid = f"evt-{sec}-{i}"
            aid1 = f"art-{sec}-a-{i}"
            aid2 = f"art-{sec}-b-{i}"
            u1 = f"https://www.business-standard.com/{sec}-{i}.html" if sec != "international" else f"https://www.reuters.com/{sec}-{i}.html"
            u2 = f"https://economictimes.indiatimes.com/{sec}-{i}.html" if sec != "international" else f"https://www.bloomberg.com/{sec}-{i}.html"
            cat = NewsCategory.DOMESTIC if sec == "domestic" else (NewsCategory.INDIA if sec == "india" else NewsCategory.INTERNATIONAL)
            t = f"CompanyIndia{i} Q1 Net Profit Rises {i * 5}% YoY to ₹{i * 1000} Crore" if sec == "india" else (
                f"Supreme Court Bench Issues Milestone Order {i} on Policy" if sec == "domestic" else f"GlobalFirm{i} Signs ${i * 2}.5 Billion Acquisition Deal"
            )
            b = f"Reported body text with financial figures ₹{i * 1000} crore or $1.5 billion."

            art1 = Article(id=aid1, url=u1, title=t, source_name="Business Standard", published_at=now_utc - timedelta(hours=4), content_text=b, category=cat, is_verified_url=True, date_verified=True, is_valid_date=True)
            art2 = Article(id=aid2, url=u2, title=t, source_name="The Economic Times", published_at=now_utc - timedelta(hours=5), content_text=b, category=cat, is_verified_url=True, date_verified=True, is_valid_date=True)
            ev = Event(id=eid, canonical_title=t, description=b, event_category=cat, companies_involved=[f"Comp{i}"], financial_figures=[f"₹{i * 1000} crore"], article_ids=[aid1, aid2], verification_tier=VerificationTier.TWO_SOURCE_VERIFIED, verification_confidence=95.0)
            articles_map[aid1] = art1
            articles_map[aid2] = art2
            events_map[eid] = ev
            candidate_urls.add(u1)
            candidate_urls.add(u2)

    pass_report = validator.validate_briefing(
        payload=make_payload(twelve_word_sum),
        events_lookup=events_map,
        articles_lookup=articles_map,
        candidate_urls=candidate_urls,
        target_date=target_d,
        quality_ladder_mode=True,
        run_reference_time=now_utc,
    )
    assert pass_report.is_valid, f"12-word summary failed check #{pass_report.failed_check_id}: {pass_report.failure_reason}"

    fail_report = validator.validate_briefing(
        payload=make_payload(thirty_one_word_sum),
        events_lookup=events_map,
        articles_lookup=articles_map,
        candidate_urls=candidate_urls,
        target_date=target_d,
        quality_ladder_mode=True,
        run_reference_time=now_utc,
    )
    assert not fail_report.is_valid
    assert fail_report.failed_check_id == 20
    assert "exceeds maximum 30 words" in fail_report.failure_reason


# =========================================================================
# FOCUSED REGRESSIONS: 4 LIVE-OUTPUT ISSUES
# =========================================================================

def test_summary_does_not_end_with_incomplete_tokens():
    """Verify summaries for Ather, Orissa HC, Rahul Gandhi, Ludhiana, and SoftBank do not end in incomplete tokens."""
    # 1. Ather: must not end with 'electric.'
    ather_art = Article(
        url="https://www.moneycontrol.com/news/business/markets/ather-energy-shares-jump.html",
        title="Ather Energy shares jump 5% after ₹1,758 crore block deal",
        source_name="Moneycontrol",
        content_text="Shares of Ather Energy jumped more than 5 percent in early trade on August 28 after a 3 percent equity stake in the electric vehicle maker changed hands in a block deal worth ₹1,758 crore.",
    )
    ather_summary = generate_deterministic_summary(ather_art, headline=ather_art.title)
    assert not ather_summary.endswith("electric.")
    assert ather_summary.endswith(".")
    assert len(ather_summary.split()) <= 30

    # 2. Orissa HC: must not end with 'and.'
    orissa_art = Article(
        url="https://www.thehindu.com/news/national/orissa-hc-contempt/article.ece",
        title="Orissa HC issues contempt notice to retired DGP",
        source_name="The Hindu",
        content_text="The Orissa High Court has issued a contempt notice to retired DGP YB Khurania for his alleged deliberate disobedience in honouring a judicial order directing the reinstatement of a sub-inspector.",
    )
    orissa_summary = generate_deterministic_summary(orissa_art, headline=orissa_art.title)
    assert not orissa_summary.endswith("and.")
    assert orissa_summary.endswith(".")
    assert len(orissa_summary.split()) <= 30

    # 3. Rahul Gandhi: must not end with 'a.'
    rahul_art = Article(
        url="https://www.thehindu.com/news/national/rahul-attacks-govt/article.ece",
        title="Rahul attacks Modi government over NCLT settlement plan",
        source_name="The Hindu",
        content_text="Congress leader Rahul Gandhi on Thursday accused the Narendra Modi government of creating two systems in the country after lenders agreed to a settlement plan in NCLT.",
    )
    rahul_summary = generate_deterministic_summary(rahul_art, headline=rahul_art.title)
    assert not rahul_summary.endswith("a.")
    assert rahul_summary.endswith(".")
    assert len(rahul_summary.split()) <= 30

    # 4. Ludhiana: must not end with 'major.'
    ludhiana_art = Article(
        url="https://timesofindia.indiatimes.com/city/ludhiana/water-project/article.ece",
        title="48% of water project finished: IIT experts",
        source_name="The Times of India",
        content_text="IIT experts completed a two-day technical review of the Ludhiana Bulk Water Supply Scheme, confirming that forty-eight percent of the local civic pipeline infrastructure is finished.",
    )
    ludhiana_summary = generate_deterministic_summary(ludhiana_art, headline=ludhiana_art.title)
    assert not ludhiana_summary.endswith("major.")
    assert ludhiana_summary.endswith(".")
    assert len(ludhiana_summary.split()) <= 30

    # 5. SoftBank: no 'dollars.SoftBank'
    softbank_art = Article(
        url="https://www.reuters.com/business/softbank-1x-deal.html",
        title="SoftBank in talks to buy stake in OpenAI-backed 1X",
        source_name="Reuters",
        content_text="Investors are valuing the innovative humanoid robot company at around six billion dollars.SoftBank has held preliminary discussions to join the upcoming funding round.",
    )
    softbank_summary = generate_deterministic_summary(softbank_art, headline=softbank_art.title)
    assert "dollars.SoftBank" not in softbank_summary
    assert softbank_summary.endswith(".")
    assert len(softbank_summary.split()) <= 30


def test_utf8_mojibake_analysis_and_formatter_cleanliness():
    """Verify BriefingFormatter produces 100% valid UTF-8 symbols and no mojibake codepoints."""
    formatter = BriefingFormatter()
    raw_sample = "IPO-bound OYO parent Prism reports net profit of Rs 994 crore, that's a big win — ₹994 crore."
    cleaned = formatter.clean_text(raw_sample)
    assert "₹994 crore" in cleaned
    assert "that’s" in cleaned or "that's" in cleaned
    assert "—" in cleaned
    # Ensure no double-encoded mojibake tokens exist in output
    assert not any(tok in cleaned for tok in ("ΓÇ", "Γé", "┬á", "┬"))


def test_domestic_national_significance_municipal_vs_strategic():
    """Verify local municipal scope has no national significance, while strategic national projects do."""
    evaluator = DomesticTrendingEvaluator()

    # 1. Ordinary local municipal project progress: no national significance
    ludhiana_ev = Event(
        id="evt-ludhiana",
        canonical_title="48% of water project finished: IIT experts",
        description="IIT experts completed a review of the Ludhiana municipal bulk water supply scheme.",
        event_category=NewsCategory.DOMESTIC,
        article_ids=["art-ludhiana"],
    )
    ludhiana_art = Article(
        id="art-ludhiana",
        url="https://timesofindia.indiatimes.com/city/ludhiana/water-project/article.ece",
        title="48% of water project finished: IIT experts",
        source_name="The Times of India",
        content_text="IIT experts completed a two-day technical review of the Ludhiana Bulk Water Supply Scheme, confirming that forty-eight percent of the local civic pipeline infrastructure is finished.",
        category=NewsCategory.DOMESTIC,
    )
    l_qual, l_score, l_rsn = evaluator.evaluate(ludhiana_ev, primary_article=ludhiana_art)
    assert "national_significance(+20)" not in l_rsn
    assert not l_qual  # Should not qualify (score < 60)

    # 2. Bullet train strategic national project: national significance
    bullet_ev = Event(
        id="evt-bullet",
        canonical_title="India bullet train project achieves major milestone with undersea tunnel completion",
        description="The National High Speed Rail Corporation announced completion of the undersea tunnel for the bullet train corridor.",
        event_category=NewsCategory.DOMESTIC,
        article_ids=["art-bullet"],
    )
    bullet_art = Article(
        id="art-bullet",
        url="https://www.thehindu.com/news/national/bullet-train-tunnel/article.ece",
        title="India bullet train project achieves major milestone with undersea tunnel completion",
        source_name="The Hindu",
        content_text="The National High Speed Rail Corporation on Thursday announced the successful boring of the undersea tunnel for the bullet train corridor project.",
        category=NewsCategory.DOMESTIC,
    )
    b_qual, b_score, b_rsn = evaluator.evaluate(bullet_ev, primary_article=bullet_art)
    assert "national_significance(+20)" in b_rsn
    assert b_qual

    # 3. Supreme Court major event: national significance
    sc_ev = Event(
        id="evt-sc",
        canonical_title="Supreme Court orders Centre to respond within two weeks on national policy",
        description="The Supreme Court directed the central government to file its compliance affidavit.",
        event_category=NewsCategory.DOMESTIC,
        article_ids=["art-sc"],
    )
    sc_art = Article(
        id="art-sc",
        url="https://www.thehindu.com/news/national/supreme-court-orders-centre/article.ece",
        title="Supreme Court orders Centre to respond within two weeks on national policy",
        source_name="The Hindu",
        content_text="The Supreme Court on Thursday directed the Union government to submit a detailed status report on national education policy within two weeks.",
        category=NewsCategory.DOMESTIC,
    )
    s_qual, s_score, s_rsn = evaluator.evaluate(sc_ev, primary_article=sc_art)
    assert "national_significance(+20)" in s_rsn
    assert s_qual


def test_speculative_deal_talks_vs_completed_hard_events():
    """Verify prospective talks/discussions are rejected while completed events with secondary speculation survive."""
    rule = StoryTypeFilterRule()

    # 1. Aster/Advent: talks-only acquisition => REJECT
    aster_art = Article(
        url="https://www.moneycontrol.com/news/business/aster-advent-eye-stake.html",
        title="Aster, Advent eye controlling stake in Yatharth Hospital, says report",
        source_name="Moneycontrol",
        content_text="Advent International and Aster DM are in discussions to acquire a controlling stake in Yatharth Hospital.",
    )
    res_aster = rule.evaluate(aster_art)
    assert not res_aster.is_accepted
    assert "speculative_deal_talks" in res_aster.rejection_reason

    # 2. SoftBank: talks-only investment => REJECT
    softbank_art = Article(
        url="https://www.reuters.com/business/softbank-talks-1x.html",
        title="SoftBank in talks to buy stake in OpenAI-backed 1X at $6 billion valuation",
        source_name="Reuters",
        content_text="SoftBank is in talks to invest in humanoid robotics startup 1X at a valuation of around $6 billion.",
    )
    res_softbank = rule.evaluate(softbank_art)
    assert not res_softbank.is_accepted
    assert "speculative_deal_talks" in res_softbank.rejection_reason

    # 3. Ather: completed ₹1,758 crore block deal + secondary speculation => ACCEPT
    ather_art = Article(
        url="https://www.moneycontrol.com/news/business/ather-block-deal.html",
        title="Ather Energy shares jump 5% after ₹1,758 crore block deal; Hero likely raises stake by 3%",
        source_name="Moneycontrol",
        content_text="Ather Energy shares jumped 5 percent after a ₹1,758 crore block deal on Thursday as Hero MotoCorp is likely to increase its stake.",
    )
    res_ather = rule.evaluate(ather_art)
    assert res_ather.is_accepted

    # 4. Nvidia: agreed acquisition => ACCEPT
    nvidia_art = Article(
        url="https://www.cnbc.com/2026/08/28/nvidia-agrees-deal.html",
        title="Nvidia agrees to buy AI software firm in $1.2 billion all-cash deal",
        source_name="CNBC",
        content_text="Nvidia has signed a definitive agreement to acquire the AI software startup for $1.2 billion in cash.",
    )
    res_nvidia = rule.evaluate(nvidia_art)
    assert res_nvidia.is_accepted
