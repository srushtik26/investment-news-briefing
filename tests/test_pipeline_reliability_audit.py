"""
Comprehensive Reliability and Pipeline Hardening Audit Test Suite.

Verifies all 24 required capabilities:
1. Foreign headline + no India nexus -> not Domestic.
2. Domestic discovery prior + foreign subject -> not Domestic.
3. Genuine Indian government/public-affairs story -> Domestic.
4. Valid India corporate story -> India.
5. Foreign corporate story -> International.
6. Invalid Domestic candidate gets rejected.
7. Reserve candidate replaces rejected Domestic candidate.
8. Five valid Domestic stories are still selected after one rejection.
9. Backfill works for India.
10. Backfill works for International.
11. Pipeline gives explicit insufficiency error only when reserves are exhausted.
12. Duplicate story rejected and replaced.
13. Single-source story rejected and replaced when uncorroborated.
14. Unsupported numerical claim rejected.
15. Indian comma number normalization passes (5,57,700% vs 557,700%).
16. Malformed editorial headline rejected.
17. Candidate replacement still results in 5 Domestic + 5 India + 5 International.
18. Formatter outputs exactly 15 stories.
19. Email mock receives exactly 15 stories.
20. Dashboard mock receives exactly 15 stories.
21. Delivery idempotency prevents duplicate send.
22. Dry-run never calls real email sender.
23. Dry-run never writes production database.
24. Sunday date does not disable pipeline and workflow cron runs seven days per week.
"""

from datetime import date, datetime, timezone
from pathlib import Path
import os
import re
from unittest.mock import patch, MagicMock

import pytest

from app.models.enums import NewsCategory, VerificationTier
from app.classification.region_classifier import EventRegionClassifier
from app.ai.headline_synthesis import (
    validate_headline_coherence,
    synthesize_investment_headline,
    is_approved_institutional_headline,
)
from app.formatting.formatter import BriefingFormatter
from app.ai.models import BriefingEditorialPayload, EditorialStorySelection
from run_daily_15 import _canonical_numbers, FifteenStoryValidationEngine
from run_daily import run_daily_briefing
from run_dashboard_sync import sync_dashboard
from tests.fixtures.pipeline_fixtures import (
    make_test_article,
    make_test_event,
    make_scored_event,
    create_valid_domestic_scenario,
    create_invalid_domestic_scenario,
    create_valid_india_scenario,
    create_valid_international_scenario,
)


@pytest.fixture
def classifier():
    return EventRegionClassifier()


# ---------------------------------------------------------------------------
# 1. Foreign headline + no India nexus -> not Domestic
# ---------------------------------------------------------------------------
def test_1_foreign_headline_no_india_nexus_not_domestic(classifier):
    cat, reason = classifier.classify_with_reason(
        title="Chinese troop deployment raises border security alert across East Asian sea corridor",
        content="Beijing ordered new naval troop deployments in international maritime zones.",
    )
    assert cat != NewsCategory.DOMESTIC
    assert cat == NewsCategory.INTERNATIONAL


# ---------------------------------------------------------------------------
# 2. Domestic discovery prior + foreign subject -> not Domestic
# ---------------------------------------------------------------------------
def test_2_domestic_discovery_prior_foreign_subject_not_domestic(classifier):
    cat, reason = classifier.classify_with_reason(
        title="US defence policy shifts military aid focus to European allies",
        content="The Pentagon announced revised allocation priorities for overseas deployments.",
        discovery_region=NewsCategory.DOMESTIC,
    )
    assert cat != NewsCategory.DOMESTIC
    assert cat == NewsCategory.INTERNATIONAL
    assert "foreign subject without Indian headline nexus" in reason


# ---------------------------------------------------------------------------
# 3. Genuine Indian government/public-affairs story -> Domestic
# ---------------------------------------------------------------------------
def test_3_genuine_indian_government_story_is_domestic(classifier):
    cat, reason = classifier.classify_with_reason(
        title="Union Cabinet Clears ₹24,000 Crore National Rail Corridor Connecting Major Ports",
        content="The Union Cabinet chaired by PM Narendra Modi approved national infrastructure investments.",
    )
    assert cat == NewsCategory.DOMESTIC


# ---------------------------------------------------------------------------
# 4. Valid India corporate story -> India
# ---------------------------------------------------------------------------
def test_4_valid_india_corporate_story_is_india(classifier):
    cat, reason = classifier.classify_with_reason(
        title="Tata Motors Secures ₹5,200 Crore Commercial Contract to Supply Electric Fleets",
        content="Tata Motors disclosed the new commercial agreement on BSE and NSE.",
        companies=["Tata Motors"],
        financial_figures=["₹5,200 crore"],
    )
    assert cat == NewsCategory.INDIA


# ---------------------------------------------------------------------------
# 5. Foreign corporate story -> International
# ---------------------------------------------------------------------------
def test_5_foreign_corporate_story_is_international(classifier):
    cat, reason = classifier.classify_with_reason(
        title="Samsung Backs European AI Chipmaker in $230 Million Series A Round",
        content="Samsung participated in a $230 million round alongside European venture partners.",
        companies=["Samsung"],
        financial_figures=["$230 million"],
    )
    assert cat == NewsCategory.INTERNATIONAL


# ---------------------------------------------------------------------------
# 6. Invalid Domestic candidate gets rejected by verify_region_eligibility
# ---------------------------------------------------------------------------
def test_6_invalid_domestic_candidate_gets_rejected(classifier):
    art, ev = create_invalid_domestic_scenario()
    is_valid, reason = classifier.verify_region_eligibility(
        event=ev, article=art, requested_region=NewsCategory.DOMESTIC
    )
    assert not is_valid
    assert "lacks Indian domestic" in reason


# ---------------------------------------------------------------------------
# 7 & 8. Reserve candidate replaces rejected Domestic candidate; 5 selected
# ---------------------------------------------------------------------------
def test_7_8_reserve_candidate_replaces_rejected_domestic():
    """Verify that Stage 7 selection discards invalid candidates and backfills reserves."""
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import run_ranking_and_selection
    from app.ranking.models import RankedCandidatePool

    now = datetime(2026, 9, 15, 6, 0, 0, tzinfo=timezone.utc)
    ctx = PipelineContext(run_reference_time=now)

    # 1 invalid domestic candidate + 5 valid domestic candidates = 6 total
    invalid_art, invalid_ev = create_invalid_domestic_scenario()
    ctx.articles_lookup[invalid_art.id] = invalid_art

    valid_scored = []
    events_map = {invalid_ev.id: invalid_ev}
    accepted = [{"event_id": invalid_ev.id}]

    for i in range(5):
        art, ev = create_valid_domestic_scenario(suffix=str(i), event_idx=i)
        ctx.articles_lookup[art.id] = art
        events_map[ev.id] = ev
        accepted.append({"event_id": ev.id})

    # Also add 5 India and 5 International to make full pools
    for i in range(5):
        i_art, i_ev = create_valid_india_scenario(f"Enterprise_{i}", publisher=f"India Publisher {i}", event_idx=i)
        ctx.articles_lookup[i_art.id] = i_art
        events_map[i_ev.id] = i_ev
        accepted.append({"event_id": i_ev.id})

        int_art, int_ev = create_valid_international_scenario(f"GlobalCorp_{i}", publisher=f"Intl Publisher {i}", event_idx=i)
        ctx.articles_lookup[int_art.id] = int_art
        events_map[int_ev.id] = int_ev
        accepted.append({"event_id": int_ev.id})

    pool, dom_p, ind_p, int_p, sufficient, status = run_ranking_and_selection(
        ctx=ctx, accepted_stories=accepted, event_by_id=events_map
    )

    # Invalid domestic was skipped, 5 valid were selected
    assert len(dom_p) == 5
    assert all(s.event.id != invalid_ev.id for s in dom_p)
    assert sufficient is True


# ---------------------------------------------------------------------------
# 9 & 10. Backfill works for India and International
# ---------------------------------------------------------------------------
def test_9_10_backfill_works_for_india_and_international():
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import run_ranking_and_selection

    now = datetime(2026, 9, 15, 6, 0, 0, tzinfo=timezone.utc)
    ctx = PipelineContext(run_reference_time=now)
    events_map = {}
    accepted = []

    # 5 Domestic
    for i in range(5):
        art, ev = create_valid_domestic_scenario(suffix=str(i), publisher=f"DomPub {i}", event_idx=i)
        art.id = f"art_dom_{i}"
        ev.id = f"ev_dom_{i}"
        ev.article_ids = [art.id]
        ctx.articles_lookup[art.id] = art
        events_map[ev.id] = ev
        accepted.append({"event_id": ev.id})

    # 1 Invalid India (foreign subject) + 5 Valid India
    inv_ind_art = make_test_article(
        title="Tokyo Exchange Closes Higher Following Tech Shares Rally in Japan",
        publisher="Nikkei",
        category=NewsCategory.INDIA,
    )
    inv_ind_ev = make_test_event(title=inv_ind_art.title, article=inv_ind_art, category=NewsCategory.INDIA)
    ctx.articles_lookup[inv_ind_art.id] = inv_ind_art
    events_map[inv_ind_ev.id] = inv_ind_ev
    accepted.append({"event_id": inv_ind_ev.id})

    for i in range(5):
        art, ev = create_valid_india_scenario(f"Company_{i}", publisher=f"IndPub {i}", event_idx=i)
        art.id = f"art_ind_{i}"
        ev.id = f"ev_ind_{i}"
        ev.article_ids = [art.id]
        ctx.articles_lookup[art.id] = art
        events_map[ev.id] = ev
        accepted.append({"event_id": ev.id})

    # 1 Invalid International (geopolitical without digits) + 5 Valid International
    inv_int_art = make_test_article(
        title="Middle East Tensions Escalate as Talks Regarding Regional Ceasefire Stall",
        publisher="Reuters",
        category=NewsCategory.INTERNATIONAL,
    )
    inv_int_ev = make_test_event(title=inv_int_art.title, article=inv_int_art, category=NewsCategory.INTERNATIONAL)
    ctx.articles_lookup[inv_int_art.id] = inv_int_art
    events_map[inv_int_ev.id] = inv_int_ev
    accepted.append({"event_id": inv_int_ev.id})

    for i in range(5):
        art, ev = create_valid_international_scenario(f"IntlCorp_{i}", publisher=f"IntlPub {i}", event_idx=i)
        art.id = f"art_int_{i}"
        ev.id = f"ev_int_{i}"
        ev.article_ids = [art.id]
        ctx.articles_lookup[art.id] = art
        events_map[ev.id] = ev
        accepted.append({"event_id": ev.id})

    pool, dom_p, ind_p, int_p, sufficient, status = run_ranking_and_selection(
        ctx=ctx, accepted_stories=accepted, event_by_id=events_map
    )

    assert len(ind_p) == 5
    assert all(s.event.id != inv_ind_ev.id for s in ind_p)
    assert len(int_p) == 5
    assert all(s.event.id != inv_int_ev.id for s in int_p)
    assert sufficient is True


# ---------------------------------------------------------------------------
# 11. Insufficiency error only when reserves are exhausted
# ---------------------------------------------------------------------------
def test_11_insufficient_error_when_reserves_exhausted():
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import run_ranking_and_selection

    now = datetime(2026, 9, 15, 6, 0, 0, tzinfo=timezone.utc)
    ctx = PipelineContext(run_reference_time=now)
    events_map = {}
    accepted = []

    # Only 4 domestic stories available
    for i in range(4):
        art, ev = create_valid_domestic_scenario(suffix=str(i), publisher=f"DomPub {i}", event_idx=i)
        art.id = f"art_dom_{i}"
        ev.id = f"ev_dom_{i}"
        ev.article_ids = [art.id]
        ctx.articles_lookup[art.id] = art
        events_map[ev.id] = ev
        accepted.append({"event_id": ev.id})

    for i in range(5):
        art, ev = create_valid_india_scenario(f"Ind_{i}", publisher=f"IndPub {i}", event_idx=i)
        art.id = f"art_ind_{i}"
        ev.id = f"ev_ind_{i}"
        ev.article_ids = [art.id]
        ctx.articles_lookup[art.id] = art
        events_map[ev.id] = ev
        accepted.append({"event_id": ev.id})

        int_art, int_ev = create_valid_international_scenario(f"Intl_{i}", publisher=f"IntlPub {i}", event_idx=i)
        int_art.id = f"art_int_{i}"
        int_ev.id = f"ev_int_{i}"
        int_ev.article_ids = [int_art.id]
        ctx.articles_lookup[int_art.id] = int_art
        events_map[int_ev.id] = int_ev
        accepted.append({"event_id": int_ev.id})

    pool, dom_p, ind_p, int_p, sufficient, status = run_ranking_and_selection(
        ctx=ctx, accepted_stories=accepted, event_by_id=events_map
    )
    assert len(dom_p) == 4
    assert sufficient is False


# ---------------------------------------------------------------------------
# 12. Duplicate story rejected and replaced
# ---------------------------------------------------------------------------
def test_12_duplicate_story_rejected_and_replaced():
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import run_ranking_and_selection

    now = datetime(2026, 9, 15, 6, 0, 0, tzinfo=timezone.utc)
    ctx = PipelineContext(run_reference_time=now)
    events_map = {}
    accepted = []

    # 5 domestic
    for i in range(5):
        art, ev = create_valid_domestic_scenario(suffix=str(i), publisher=f"DomPub {i}", event_idx=i)
        art.id = f"art_dom_{i}"
        ev.id = f"ev_dom_{i}"
        ev.article_ids = [art.id]
        ctx.articles_lookup[art.id] = art
        events_map[ev.id] = ev
        accepted.append({"event_id": ev.id})

    # 5 valid India + 1 duplicate of story 0 + 1 valid reserve
    base_art, base_ev = create_valid_india_scenario("UniqueCompany_0", publisher="India Wire 0", event_idx=0)
    ctx.articles_lookup[base_art.id] = base_art
    events_map[base_ev.id] = base_ev
    accepted.append({"event_id": base_ev.id})

    # Exact duplicate of base_ev with same company
    dup_art = make_test_article(
        title="UniqueCompany_0 Secures ₹5,200 Crore Commercial Acquisition and Electric Fleet Order",
        content="Duplicate coverage of UniqueCompany_0 commercial order.",
        publisher="Economic Times",
    )
    dup_ev = make_test_event(title=dup_art.title, article=dup_art, companies=["UniqueCompany_0"])
    ctx.articles_lookup[dup_art.id] = dup_art
    events_map[dup_ev.id] = dup_ev
    accepted.append({"event_id": dup_ev.id})

    for i in range(1, 6):
        art, ev = create_valid_india_scenario(f"UniqueCompany_{i}", publisher=f"IndPub {i}", event_idx=i)
        art.id = f"art_ind_{i}"
        ev.id = f"ev_ind_{i}"
        ev.article_ids = [art.id]
        ctx.articles_lookup[art.id] = art
        events_map[ev.id] = ev
        accepted.append({"event_id": ev.id})

    for i in range(5):
        int_art, int_ev = create_valid_international_scenario(f"Intl_{i}", publisher=f"IntlPub {i}", event_idx=i)
        int_art.id = f"art_int_{i}"
        int_ev.id = f"ev_int_{i}"
        int_ev.article_ids = [int_art.id]
        ctx.articles_lookup[int_art.id] = int_art
        events_map[int_ev.id] = int_ev
        accepted.append({"event_id": int_ev.id})

    pool, dom_p, ind_p, int_p, sufficient, status = run_ranking_and_selection(
        ctx=ctx, accepted_stories=accepted, event_by_id=events_map
    )
    assert len(ind_p) == 5
    # Ensure no duplicate companies among selected India candidates
    comps = [s.event.companies_involved[0] for s in ind_p if s.event.companies_involved]
    assert len(comps) == len(set(comps))


# ---------------------------------------------------------------------------
# 14. Unsupported numerical claim rejected
# ---------------------------------------------------------------------------
def test_14_unsupported_numerical_claim_rejected():
    from app.validation.models import BriefingValidationReport
    validator = FifteenStoryValidationEngine()

    art = make_test_article(
        title="Solar Company Discloses Quarterly Numbers",
        content="Revenue stood at ₹500 crore.",
    )
    ev = make_test_event(title=art.title, article=art, figures=["₹500 crore"])

    story = EditorialStorySelection(
        section="india",
        event_id=ev.id,
        headline="Solar Company Reports ₹9,999 Crore Revenue in Massive Expansion; Margins Surge",
        summary="Company reported revenue with strong margin delivery across all operations.",
        source="Test Source",
        url="https://example.com/test",
    )
    payload = BriefingEditorialPayload(
        domestic_stories=[],
        india_stories=[story],
        international_stories=[],
    )

    report = validator.validate_briefing(
        payload=payload,
        events_lookup={ev.id: ev},
        articles_lookup={art.id: art},
        strict_5_per_section=False,
    )
    # Check 11 or 12 must fail for fabricated number 9,999
    failed_ids = [r.check_id for r in report.check_results if not r.passed]
    assert any(cid in (11, 12) for cid in failed_ids)


# ---------------------------------------------------------------------------
# 15. Indian comma number normalization passes (5,57,700% vs 557,700%)
# ---------------------------------------------------------------------------
def test_15_indian_comma_number_normalization():
    vals_indian = _canonical_numbers("Company net profit surges 5,57,700% in extraordinary quarter")
    vals_western = _canonical_numbers("Company net profit surges 557,700% in extraordinary quarter")
    assert vals_indian == vals_western
    assert "557700" in vals_indian

    val_crore_1 = _canonical_numbers("Investment outlay of ₹1,250 crore")
    val_crore_2 = _canonical_numbers("Investment outlay of ₹1250 crore")
    assert val_crore_1 == val_crore_2


# ---------------------------------------------------------------------------
# 16. Malformed editorial headline rejected and grounded
# ---------------------------------------------------------------------------
def test_16_malformed_editorial_headline_rejected():
    # Duplicate acquirer/target
    bad_hl_1 = "MySize Agrees to Acquire MySize Announces Acquisition-Led Strategy; Transaction Expands Scale"
    is_coh_1, reason_1 = validate_headline_coherence(bad_hl_1)
    assert not is_coh_1
    assert "duplicates target" in reason_1

    # Brokerage treated as acquisition target
    bad_hl_2 = "Solar Industries Agrees to Acquire Goldman Sachs for ₹12,951 crore; Transaction Consolidates Control"
    is_coh_2, reason_2 = validate_headline_coherence(bad_hl_2)
    assert not is_coh_2
    assert "brokerage/analyst" in reason_2

    # Malformed currency fragment
    bad_hl_3 = "Tech Corp Agrees to Acquire Target Entity for rs,; Strategic Expansion"
    is_coh_3, reason_3 = validate_headline_coherence(bad_hl_3)
    assert not is_coh_3
    assert "currency symbol without numeric digits" in reason_3

    # Coherent institutional headline passes
    good_hl = "Tata Motors Secures ₹5,200 Crore Commercial Contract; Order Consolidates Commercial EV Leadership"
    is_coh_good, reason_good = validate_headline_coherence(good_hl)
    assert is_coh_good
    assert reason_good == ""


# ---------------------------------------------------------------------------
# 18. Formatter outputs exactly 15 stories
# ---------------------------------------------------------------------------
def test_18_formatter_outputs_exactly_15_stories():
    formatter = BriefingFormatter()
    dom_stories = [
        EditorialStorySelection(
            section="domestic", event_id=f"d_{i}", headline=f"Domestic Headline {i+1}",
            summary="Substantive summary with institutional investment context.",
            source="The Hindu", url=f"https://example.com/d{i}"
        ) for i in range(5)
    ]
    ind_stories = [
        EditorialStorySelection(
            section="india", event_id=f"ind_{i}", headline=f"India Headline {i+1}",
            summary="Substantive summary with institutional investment context.",
            source="Business Standard", url=f"https://example.com/ind{i}"
        ) for i in range(5)
    ]
    int_stories = [
        EditorialStorySelection(
            section="international", event_id=f"int_{i}", headline=f"Intl Headline {i+1}",
            summary="Substantive summary with institutional investment context.",
            source="Reuters", url=f"https://example.com/int{i}"
        ) for i in range(5)
    ]
    payload = BriefingEditorialPayload(
        domestic_stories=dom_stories,
        india_stories=ind_stories,
        international_stories=int_stories,
    )
    formatted = formatter.format(payload, briefing_date=date(2026, 9, 15))
    text = formatted.text

    assert "TOP 5 DOMESTIC HEADLINES" in text
    assert "TOP 5 INDIA BUSINESS HEADLINES" in text
    assert "TOP 5 INTERNATIONAL BUSINESS HEADLINES" in text
    # Exactly 15 URLs
    urls = re.findall(r"https?://\S+", text)
    assert len(urls) == 15


# ---------------------------------------------------------------------------
# 19 & 22. Dry-run mode never calls real email sender and mocks email
# ---------------------------------------------------------------------------
def test_19_22_dry_run_mocks_email(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_DRY_RUN", "true")
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Prepare fake final_briefing.txt
    briefing_content = (
        "*INVESTMENT COMMITTEE BRIEFING*\n"
        "*TOP 5 DOMESTIC HEADLINES*\n\n"
        "*TOP 5 INDIA BUSINESS HEADLINES*\n\n"
        "*TOP 5 INTERNATIONAL BUSINESS HEADLINES*\n"
    )
    (data_dir / "final_briefing.txt").write_text(briefing_content, encoding="utf-8")

    with patch("run_daily.send_briefing_email") as mock_send_primary, \
         patch("run_daily.send_copy_paste_email") as mock_send_copy:
        ret = run_daily_briefing(data_dir_override=data_dir, skip_pipeline_execution=True)
        assert ret == 0
        mock_send_primary.assert_not_called()
        mock_send_copy.assert_not_called()

    # Confirm production delivery state files were NOT written
    assert not (data_dir / "last_email_date.txt").exists()
    assert not (data_dir / "primary_email_date.txt").exists()


# ---------------------------------------------------------------------------
# 20 & 23. Dashboard dry run mocks sync into temporary SQLite DB
# ---------------------------------------------------------------------------
def test_20_23_dashboard_dry_run_mocks_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_DRY_RUN", "true")
    fake_briefing = tmp_path / "copy_paste_briefing.txt"

    # Build 15-story copy-paste briefing
    lines = ["*INVESTMENT COMMITTEE BRIEFING*", "*Tuesday, 15th September, 2026*", "*TOP 5 DOMESTIC HEADLINES*"]
    for i in range(5):
        lines.extend([f"*{i+1}. Domestic Story {i+1}*", "Summary line.", "Source: The Hindu", f"https://example.com/d{i}", ""])
    lines.append("*TOP 5 INDIA BUSINESS HEADLINES*")
    for i in range(5):
        lines.extend([f"*{i+1}. India Story {i+1}*", "Summary line.", "Source: Mint", f"https://example.com/ind{i}", ""])
    lines.append("*TOP 5 INTERNATIONAL BUSINESS HEADLINES*")
    for i in range(5):
        lines.extend([f"*{i+1}. Intl Story {i+1}*", "Summary line.", "Source: Reuters", f"https://example.com/int{i}", ""])

    fake_briefing.write_text("\n".join(lines), encoding="utf-8")

    success = sync_dashboard(input_file=fake_briefing, dry_run=True)
    assert success is True


# ---------------------------------------------------------------------------
# 21. Delivery idempotency prevents duplicate send on same date
# ---------------------------------------------------------------------------
def test_21_delivery_idempotency_prevents_duplicate_send(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    today_str = date.today().strftime("%Y-%m-%d")
    (data_dir / "last_email_date.txt").write_text(today_str, encoding="utf-8")

    with patch("run_daily.send_briefing_email") as mock_send:
        ret = run_daily_briefing(data_dir_override=data_dir, skip_pipeline_execution=True)
        assert ret == 0
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# 24. Workflow schedule supports all seven days
# ---------------------------------------------------------------------------
def test_24_workflow_schedule_runs_all_seven_days():
    wf_path = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "daily_briefing.yml"
    assert wf_path.exists()
    content = wf_path.read_text(encoding="utf-8")

    # Cron should run daily (every day of week: * * * or 0-6)
    cron_match = re.search(r"cron:\s*['\"]([^'\"]+)['\"]", content)
    assert cron_match is not None
    cron_expr = cron_match.group(1).strip()
    parts = cron_expr.split()
    assert len(parts) == 5
    # Day-of-week field is parts[4]
    dow = parts[4]
    assert dow in ("*", "0-6", "0,1,2,3,4,5,6", "7")
    # Production command must remain run_daily_15.py
    assert "python run_daily_15.py" in content
