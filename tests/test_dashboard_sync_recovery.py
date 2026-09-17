"""
Regression tests for dashboard sync recovery and copy_paste briefing validation:
A. Current final_15 JSON list (15 story dicts -> 15 stories, 5/5/5)
B. Empty/malformed JSON (copy_paste generation fails, header-only file NOT produced)
C. Broken copy_paste but valid final_briefing (fallback to final_briefing succeeds)
D. Valid copy_paste (uses copy_paste normally, no fallback required)
E. Both invalid (fails explicitly)
"""

import json
from datetime import date
from pathlib import Path
import pytest

from app.formatting.formatter import build_copy_paste_text
from app.dashboard.repository import DashboardRepository
from run_dashboard_sync import parse_briefing_text, sync_dashboard
from run_daily import validate_copy_paste_briefing


VALID_15_STORY_BRIEFING = """INVESTMENT COMMITTEE BRIEFING
Thursday, 17th September, 2026

TOP 5 INDIA BUSINESS HEADLINES

1. NSE IPO Market Debut Preparation
The National Stock Exchange of India is preparing for its public listing.
Source: Mint
https://www.livemint.com/market/nse-ipo-1

2. Yes Bank Shares Surge on UPI Commission Structure
Yes Bank gained 4% as brokerages flagged margin expansion.
Source: Economic Times
https://economictimes.indiatimes.com/yes-bank-2

3. MUFG Dragon Fund Eyes $600 Million Target
MUFG-backed Dragon Fund plans capital deployment in Asia-Pacific.
Source: Reuters
https://www.reuters.com/business/mufg-fund-3

4. Solar Industries Acquires Omnia Holdings
Industrial explosives major completes strategic cross-border transaction.
Source: Business Standard
https://www.business-standard.com/solar-inds-4

5. Purple Style Labs Commits ₹420 Crore Capex
Luxury retail arm approves manufacturing expansion in Western India.
Source: CNBCTV18
https://www.cnbctv18.com/purple-style-5

TOP 5 DOMESTIC HEADLINES

1. Smart Cities Urban Infrastructure Review
Urban development ministry assesses project completion milestones across tier-2 hubs.
Source: The Hindu
https://www.thehindu.com/news/smart-cities-6

2. Bilateral Dialogue Stresses Mutual Sensitivities
Leadership talks conclude on border management and trade normalisation.
Source: Hindustan Times
https://www.hindustantimes.com/india-china-7

3. Cochin Shipyard Inks Defence Strategic Venture
Naval engineering facility expands dry dock capabilities for naval vessels.
Source: Times of India
https://timesofindia.indiatimes.com/cochin-shipyard-8

4. Retail Food Inflation Moderates in Latest Reading
Consumer price index remains within RBI tolerance band for third straight month.
Source: Financial Express
https://www.financialexpress.com/inflation-reading-9

5. Heavy Industries Mission Expands Domestic Footprint
Union minister outlines production-linked incentive outcomes for capital goods.
Source: The Hindu
https://www.thehindu.com/heavy-industries-10

TOP 5 INTERNATIONAL BUSINESS HEADLINES

1. US Non-Farm Payrolls Signal Labor Market Resilience
Employment report shows steady private sector hiring and wage trajectory.
Source: Reuters
https://www.reuters.com/markets/us-payrolls-11

2. European Central Bank Evaluates Key Deposit Rate
Policymakers deliberate macroeconomic projections ahead of Frankfurt meeting.
Source: Bloomberg
https://www.bloomberg.com/news/ecb-rate-12

3. Nippon Steel Advances North American Transaction
Steelmaker submits updated investment commitments for industrial assets.
Source: Financial Times
https://www.ft.com/content/nippon-steel-13

4. Semiconductor Foundry Raises Annual AI Guidance
Chip designer projects enterprise accelerator revenue expansion through FY27.
Source: CNBC
https://www.cnbc.com/tech/chip-guidance-14

5. Brent Crude Stabilizes Amid Global Demand Balances
Energy benchmarks find support as inventory withdrawals offset demand headwinds.
Source: Wall Street Journal
https://www.wsj.com/market-data/commodities-oil-15
"""

HEADER_ONLY_BROKEN_COPY_PASTE = """INVESTMENT COMMITTEE BRIEFING
Thursday, 17th September, 2026

TOP 5 INDIA BUSINESS HEADLINES

TOP 5 DOMESTIC HEADLINES

TOP 5 INTERNATIONAL BUSINESS HEADLINES
"""


def _make_15_story_dict_list():
    """Create a valid list of 15 story dicts matching final_15_stories.json schema."""
    stories = []
    sections = [("india", "India Corp Story"), ("domestic", "Domestic Policy Story"), ("international", "Global Market Story")]
    for section_name, title_prefix in sections:
        for pos in range(1, 6):
            stories.append({
                "section": section_name,
                "position": pos,
                "headline": f"{title_prefix} {pos} Event",
                "summary": f"Factual verified summary description for {section_name} story number {pos}.",
                "source": "Financial Chronicle",
                "url": f"https://example.com/{section_name}/story-{pos}",
                "secondary_source": "Business Day",
                "secondary_url": f"https://verified.example.com/{section_name}/story-{pos}",
            })
    return stories


def test_a_current_final_15_json_list():
    """A. Current final_15 JSON list (15 story dicts) produces exactly 15 stories (5/5/5)."""
    stories_data = _make_15_story_dict_list()
    assert len(stories_data) == 15

    today = date(2026, 9, 17)
    copy_paste_text = build_copy_paste_text(stories_data, briefing_date=today)

    assert "INVESTMENT COMMITTEE BRIEFING" in copy_paste_text
    assert "TOP 5 INDIA BUSINESS HEADLINES" in copy_paste_text
    assert "TOP 5 DOMESTIC HEADLINES" in copy_paste_text
    assert "TOP 5 INTERNATIONAL BUSINESS HEADLINES" in copy_paste_text

    # Validate with validator
    assert validate_copy_paste_briefing(copy_paste_text) is True

    # Parse and inspect counts
    briefing = parse_briefing_text(copy_paste_text)
    assert briefing is not None
    assert briefing.story_count == 15

    india_stories = [s for s in briefing.stories if s.section == "india"]
    domestic_stories = [s for s in briefing.stories if s.section == "domestic"]
    intl_stories = [s for s in briefing.stories if s.section == "international"]

    assert len(india_stories) == 5
    assert len(domestic_stories) == 5
    assert len(intl_stories) == 5

    # Check that URLs and second sources are preserved
    assert "https://example.com/india/story-1" in copy_paste_text
    assert "Also verified by: Business Day" in copy_paste_text
    assert "https://verified.example.com/india/story-1" in copy_paste_text


def test_b_empty_malformed_json_fails_and_header_only_not_produced():
    """B. Empty/malformed JSON raises error and does NOT produce header-only artifact."""
    today = date(2026, 9, 17)

    # Empty list
    with pytest.raises(ValueError, match="0 stories found"):
        build_copy_paste_text([], briefing_date=today)

    # Incomplete list (only 3 stories)
    incomplete_list = [
        {
            "section": "india",
            "position": 1,
            "headline": "Incomplete Headline",
            "summary": "Summary",
            "source": "Source",
            "url": "https://example.com/1",
        }
    ]
    with pytest.raises(ValueError, match="expected exactly 5 India, 5 Domestic, and 5 International"):
        build_copy_paste_text(incomplete_list, briefing_date=today)

    # Empty dict
    with pytest.raises(ValueError, match="0 stories found"):
        build_copy_paste_text({}, briefing_date=today)

    # Header-only text is rejected by validator
    assert validate_copy_paste_briefing(HEADER_ONLY_BROKEN_COPY_PASTE) is False


def test_c_broken_copy_paste_fallback_to_final_briefing(tmp_path: Path, monkeypatch):
    """C. Broken copy_paste falls back to valid final_briefing and sync succeeds."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    # Broken copy_paste_briefing.txt (0 stories)
    (data_dir / "copy_paste_briefing.txt").write_text(HEADER_ONLY_BROKEN_COPY_PASTE, encoding="utf-8")

    # Valid final_briefing.txt (15 stories)
    (data_dir / "final_briefing.txt").write_text(VALID_15_STORY_BRIEFING, encoding="utf-8")

    db_file = tmp_path / "dashboard.db"

    monkeypatch.chdir(tmp_path)
    success = sync_dashboard(db_path=db_file)
    assert success is True

    repo = DashboardRepository(db_path=db_file)
    latest = repo.get_latest_briefing()
    assert latest is not None
    assert latest.story_count == 15
    assert latest.sync_source == "final_briefing.txt"
    assert latest.briefing_date == date(2026, 9, 17)


def test_d_valid_copy_paste_uses_normally_without_fallback(tmp_path: Path, monkeypatch):
    """D. Valid copy_paste is used directly without fallback."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    # Valid copy_paste_briefing.txt
    (data_dir / "copy_paste_briefing.txt").write_text(VALID_15_STORY_BRIEFING, encoding="utf-8")
    # Also valid final_briefing.txt
    (data_dir / "final_briefing.txt").write_text(VALID_15_STORY_BRIEFING, encoding="utf-8")

    db_file = tmp_path / "dashboard.db"

    monkeypatch.chdir(tmp_path)
    success = sync_dashboard(db_path=db_file)
    assert success is True

    repo = DashboardRepository(db_path=db_file)
    latest = repo.get_latest_briefing()
    assert latest is not None
    assert latest.story_count == 15
    assert latest.sync_source == "copy_paste_briefing.txt"


def test_e_both_invalid_fails_explicitly(tmp_path: Path, monkeypatch, capsys):
    """E. Both candidate artifacts invalid fails dashboard sync explicitly."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    # Both are header-only / broken
    (data_dir / "copy_paste_briefing.txt").write_text(HEADER_ONLY_BROKEN_COPY_PASTE, encoding="utf-8")
    (data_dir / "final_briefing.txt").write_text("INVALID TEXT WITH NO STORIES", encoding="utf-8")

    db_file = tmp_path / "dashboard.db"

    monkeypatch.chdir(tmp_path)
    success = sync_dashboard(db_path=db_file)
    assert success is False

    captured = capsys.readouterr()
    assert "Could not extract a complete dashboard briefing" in captured.out

    repo = DashboardRepository(db_path=db_file)
    assert repo.get_latest_briefing() is None
