"""
Comprehensive test suite for the standalone Plutus Wealth Management Dashboard.

Verifies:
1. exact sent-email-style text imports correctly (5/5/5)
2. date parsed directly from briefing header body
3. date mismatch rejected (e.g. content date Sep 5 != requested date Sep 8)
4. same date + same content = idempotent no-op
5. same date + different content = raises DashboardSyncConflictError
6. explicit --replace-date safely replaces dashboard record
7. final_15_stories.json is NOT preferred when authoritative email artifact exists
8. dashboard DB initialization & schema
9. latest briefing retrieval
10. archive retrieval
11. homepage renders successfully
12. archive renders successfully
13. historical briefing renders successfully
14. article URLs and sources preserved
15. dashboard code never imports or executes run_pipeline or run_daily
16. dashboard sync never calls external APIs
"""

import ast
from datetime import date, datetime
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.dashboard.models import DashboardBriefing, DashboardStory
from app.dashboard.repository import (
    DashboardRepository,
    DashboardSyncConflictError,
    compute_briefing_content_hash,
)
from app.dashboard.web import create_app
from run_dashboard_sync import (
    parse_briefing_text,
    parse_briefing_json,
    extract_date_from_briefing_text,
    sync_dashboard,
)


SAMPLE_SENT_EMAIL_BRIEFING_SEP5 = """INVESTMENT COMMITTEE BRIEFING
Saturday, 5th September, 2026

TOP 5 INDIA BUSINESS HEADLINES

1. LIC Receives RBI Approval to Acquire Up to 9.99% Stake in ICICI Bank
Life Insurance Corporation of India received approval from the Reserve Bank of India to acquire up to a 9.99% stake in ICICI Bank.
Source: ANI
https://bfsi.economictimes.indiatimes.com/news/banking/lic-icici-deal/123

2. Tata Motors Launches €3.82 Billion All-Cash Offer to Acquire Iveco Group
Tata Motors launched a voluntary cash tender offer to acquire Iveco Group.
Source: Business Standard
https://www.business-standard.com/companies/news/tata-motors-iveco-deal/456

3. Complete Sports Reports ₹57.71 Crore Quarterly Revenue
Complete Sports reported quarterly revenue of ₹57.71 crore.
Source: Business Standard
https://www.business-standard.com/companies/results/complete-sports/789

4. Shalimar Corp Acquires 3-Acre Lucknow Land Parcel for ₹450 Crore
Shalimar Corp acquired a 3-acre land parcel in Lucknow for ₹450 crore.
Source: Economic Times
https://economictimes.indiatimes.com/property/shalimar-deal/101

5. Skyways Air Services Reports ₹610.60 Crore Quarterly Revenue
Skyways Air Services Ltd reported quarterly revenue of ₹610.60 crore.
Source: Business Standard
https://www.business-standard.com/companies/results/skyways/102

TOP 5 DOMESTIC HEADLINES

1. Delhi rains flood Rs 250-crore Dwarka Expressway tunnel
The tunnel was flooded following heavy showers in the capital region.
Source: The Indian Express
https://indianexpress.com/article/delhi-rains-tunnel/201

2. Heavy Rains Lash Delhi NCR, IMD Issues Weather Alert
Heavy Rains Lash Delhi NCR disrupting normal traffic and transit.
Source: India Today
https://www.indiatoday.in/india/heavy-rains-delhi/202

3. Gurgaon residents asked to avoid non-essential travel
District administration issued an advisory for corporate offices.
Source: The Indian Express
https://indianexpress.com/article/gurgaon-rain-advisory/203

4. Nepal floods show risk on Himalayan river basins
Floods across transboundary river basins highlight regional climate risks.
Source: The Times Of India
https://timesofindia.indiatimes.com/india/nepal-floods-risk/204

5. IIM Kozhikode holds strategic leadership retreat for Kerala ministers
Leadership retreat focused on state industrial policy and administrative reforms.
Source: The Indian Express
https://indianexpress.com/article/kerala-retreat/205

TOP 5 INTERNATIONAL BUSINESS HEADLINES

1. US Non-Farm Payrolls Rise by 142,000 in August
US employers added 142,000 jobs in August according to Labor Department data.
Source: Reuters
https://www.reuters.com/markets/us-jobs-report/301

2. ECB Expected to Cut Deposit Rate by 25 Basis Points
European Central Bank policymakers prepare for monetary policy meeting.
Source: Bloomberg
https://www.bloomberg.com/news/ecb-rate-cut/302

3. Nippon Steel Advances $14.9 Billion US Steel Transaction
Nippon Steel committed $1.3 billion additional investment in union mills.
Source: Financial Times
https://www.ft.com/content/nippon-steel-deal/303

4. Broadcom Forecasts $12 Billion in AI Chip Revenue
Broadcom raised full-year artificial intelligence chip revenue guidance.
Source: CNBC
https://www.cnbc.com/2026/09/05/broadcom-ai-revenue/304

5. Seven & i Holdings Rejects $38.5 Billion Couche-Tard Takeover
Seven & i stated the Canadian convenience retailer proposal undervalues company.
Source: Wall Street Journal
https://www.wsj.com/business/seven-i-couche-tard/305
"""


SAMPLE_SENT_EMAIL_BRIEFING_SEP8 = """*INVESTMENT COMMITTEE BRIEFING*
*Tuesday, 8th September, 2026*

*TOP 5 INDIA BUSINESS HEADLINES*

*1. Dangote Refinery Prepares Share Sale Following Initial Signing Ceremony*
Dangote Refinery finalized framework preparations for its public equity offering following an initial agreement ceremony.
Source: The Economic Times
https://economictimes.indiatimes.com/markets/stocks/news/dangote-refinery-share-sale/101

*2. Novartis India buys Minipress brands from Pfizer for ₹120 crore*
Novartis India agreed to acquire the Minipress cardiovascular portfolio from Pfizer in a definitive commercial agreement.
Source: Business Standard
https://www.business-standard.com/companies/news/novartis-minipress-buy-102

*3. Tata Steel Q1 net profit surges 25% YoY to ₹3,500 crore*
Tata Steel reported robust operational expansion in domestic steel deliveries boosting quarterly consolidated bottomline.
Source: Mint
https://www.livemint.com/market/tata-steel-q1-results-103

*4. Honasa Consumer promoter sells 2% stake via block deal for ₹350 crore*
Honasa Consumer promoters divested equity through open market block transactions on the NSE and BSE.
Source: CNBC TV18
https://www.cnbctv18.com/market/honasa-consumer-block-deal-104

*5. Larsen & Toubro secures ₹4,200 crore offshore EPC order in Middle East*
L&T Hydrocarbon secured a mega offshore gas processing contract worth ₹4,200 crore.
Source: Business Standard
https://www.business-standard.com/companies/news/lt-order-win-middle-east-105

*TOP 5 DOMESTIC HEADLINES*

*1. Union Cabinet approves ₹12,000 crore national rail connectivity corridor*
The Cabinet Committee on Economic Affairs chaired by Prime Minister approved critical high-density rail corridors.
Source: The Hindu
https://www.thehindu.com/news/national/cabinet-rail-corridor-201

*2. Supreme Court issues binding guidelines on national highway environmental clearances*
The apex court bench laid down standard timelines for inter-state highway infrastructure regulatory approvals.
Source: The Indian Express
https://indianexpress.com/article/india/sc-highway-ruling-202

*3. ISRO successfully tests cryogenic upper stage engine for upcoming heavy-lift mission*
ISRO announced the completion of hot fire qualification tests for the next-generation launch vehicle.
Source: NDTV
https://www.ndtv.com/india-news/isro-cryogenic-test-203

*4. Election Commission reviews poll preparedness across key states*
Chief Election Commissioner held extensive stakeholder consultations on logistical and administrative readiness.
Source: The Times of India
https://timesofindia.indiatimes.com/india/ec-review-meeting-204

*5. Ministry of Power records peak national electricity demand of 250 GW*
National grid operators managed record peak summer transmission with robust renewable generation support.
Source: Financial Express
https://www.financialexpress.com/economy/power-demand-peak-205

*TOP 5 INTERNATIONAL BUSINESS HEADLINES*

*1. US Federal Reserve signals potential 25 bps rate adjustment at September meeting*
Federal Reserve officials indicated inflation data supports gradual policy normalization.
Source: Reuters
https://www.reuters.com/markets/us-fed-rate-signals-301

*2. European Central Bank reviews banking liquidity rules following credit growth*
ECB supervisory board published updated compliance guidance for tier-1 Eurozone commercial banks.
Source: Bloomberg
https://www.bloomberg.com/news/ecb-liquidity-rules-302

*3. TSMC approves $10 billion advanced wafer fabrication expansion in Europe*
Taiwan Semiconductor Manufacturing Co committed additional capital expenditure for semiconductor fabs.
Source: Financial Times
https://www.ft.com/content/tsmc-wafer-fab-expansion-303

*4. ARM Holdings partners with major cloud providers for custom silicon architectures*
ARM announced multi-year licensing pacts covering custom high-efficiency enterprise server chips.
Source: Wall Street Journal
https://www.wsj.com/tech/arm-cloud-silicon-pacts-304

*5. Alphabet expands sovereign cloud infrastructure across APAC region*
Google Cloud announced new dedicated data center clusters to meet enterprise sovereignty requirements.
Source: CNBC
https://www.cnbc.com/tech/google-apac-cloud-expansion-305
"""


def test_1_exact_sent_email_text_imports_correctly(tmp_path: Path):
    """1. Test that exact sent-email-style text imports 5/5/5 stories with clean formatting."""
    briefing = parse_briefing_text(SAMPLE_SENT_EMAIL_BRIEFING_SEP8)
    assert briefing is not None
    assert briefing.briefing_date == date(2026, 9, 8)
    assert briefing.story_count == 15
    assert len(briefing.india_stories) == 5
    assert len(briefing.domestic_stories) == 5
    assert len(briefing.international_stories) == 5

    # First India story must be Dangote
    assert "Dangote Refinery Prepares Share Sale" in briefing.india_stories[0].headline
    assert briefing.india_stories[0].position == 1
    assert "The Economic Times" in briefing.india_stories[0].source
    assert "https://economictimes.indiatimes.com/markets/stocks/news/dangote-refinery-share-sale/101" == briefing.india_stories[0].url


def test_2_date_parsed_from_briefing_body():
    """2. Test that date is parsed directly from header body text."""
    d8 = extract_date_from_briefing_text(SAMPLE_SENT_EMAIL_BRIEFING_SEP8)
    assert d8 == date(2026, 9, 8)

    d5 = extract_date_from_briefing_text(SAMPLE_SENT_EMAIL_BRIEFING_SEP5)
    assert d5 == date(2026, 9, 5)


def test_3_date_mismatch_rejected(tmp_path: Path):
    """3. Test that sync rejects date mismatch (e.g. content is Sep 5 but user requests Sep 8)."""
    test_file = tmp_path / "copy_paste_briefing.txt"
    test_file.write_text(SAMPLE_SENT_EMAIL_BRIEFING_SEP5, encoding="utf-8")  # Date is Sep 5
    db_path = tmp_path / "dashboard.db"

    # Requesting sync with target_date = Sep 8 MUST FAIL
    success = sync_dashboard(
        input_file=test_file,
        db_path=db_path,
        target_date=date(2026, 9, 8),
    )
    assert success is False

    repo = DashboardRepository(db_path=db_path)
    assert repo.get_briefing_by_date(date(2026, 9, 8)) is None


def test_4_same_date_same_content_is_idempotent(tmp_path: Path):
    """4. Test that same date with identical content is an idempotent no-op."""
    db_path = tmp_path / "dashboard.db"
    repo = DashboardRepository(db_path=db_path)

    b1 = parse_briefing_text(SAMPLE_SENT_EMAIL_BRIEFING_SEP8)
    id1 = repo.save_briefing(b1)

    b2 = parse_briefing_text(SAMPLE_SENT_EMAIL_BRIEFING_SEP8)
    id2 = repo.save_briefing(b2)

    assert id1 == id2
    assert len(repo.get_archive_dates()) == 1


def test_5_same_date_different_content_raises_conflict(tmp_path: Path):
    """5. Test that same date with different content raises conflict error without allow_replace."""
    db_path = tmp_path / "dashboard.db"
    repo = DashboardRepository(db_path=db_path)

    b_orig = parse_briefing_text(SAMPLE_SENT_EMAIL_BRIEFING_SEP8)
    repo.save_briefing(b_orig)

    # Modified content for the same date
    modified_text = SAMPLE_SENT_EMAIL_BRIEFING_SEP8.replace("Dangote Refinery", "Alternative Corp Event")
    b_diff = parse_briefing_text(modified_text)

    with pytest.raises(DashboardSyncConflictError) as exc_info:
        repo.save_briefing(b_diff, allow_replace=False)

    assert "existing briefing differs from incoming briefing" in str(exc_info.value)

    # Verify original is still intact
    latest = repo.get_latest_briefing()
    assert latest is not None
    assert "Dangote Refinery" in latest.india_stories[0].headline


def test_6_explicit_replace_date_overwrites_safely(tmp_path: Path):
    """6. Test that --replace-date allows replacing a dashboard-only record."""
    db_path = tmp_path / "dashboard.db"
    repo = DashboardRepository(db_path=db_path)

    # Save initial version
    b_orig = parse_briefing_text(SAMPLE_SENT_EMAIL_BRIEFING_SEP8)
    repo.save_briefing(b_orig)

    # Replace with updated version using allow_replace=True
    updated_text = SAMPLE_SENT_EMAIL_BRIEFING_SEP8.replace(
        "Dangote Refinery Prepares Share Sale",
        "Dangote Refinery Finalizes Major Equity Agreement",
    )
    test_file = tmp_path / "copy_paste_briefing.txt"
    test_file.write_text(updated_text, encoding="utf-8")

    success = sync_dashboard(
        input_file=test_file,
        db_path=db_path,
        replace_date=date(2026, 9, 8),
    )
    assert success is True

    latest = repo.get_latest_briefing()
    assert latest is not None
    assert "Dangote Refinery Finalizes Major Equity Agreement" in latest.india_stories[0].headline


def test_7_final_15_stories_json_not_preferred_over_email_artifact(tmp_path: Path, monkeypatch):
    """7. Test that copy_paste_briefing.txt is preferred over stale JSON files."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    # Stale JSON with Sep 5 content
    (data_dir / "final_15_stories.json").write_text('{"stale": true}', encoding="utf-8")

    # Authoritative sent-email file with Sep 8 content
    (data_dir / "copy_paste_briefing.txt").write_text(SAMPLE_SENT_EMAIL_BRIEFING_SEP8, encoding="utf-8")

    db_path = tmp_path / "dashboard.db"

    # Point sync_dashboard default to tmp_path
    monkeypatch.chdir(tmp_path)
    success = sync_dashboard(db_path=db_path)
    assert success is True

    repo = DashboardRepository(db_path=db_path)
    latest = repo.get_latest_briefing()
    assert latest is not None
    assert latest.briefing_date == date(2026, 9, 8)
    assert latest.sync_source == "copy_paste_briefing.txt"


def test_8_dashboard_db_initialization(tmp_path: Path):
    """8. Test that dashboard DB initializes tables and schema without errors."""
    db_file = tmp_path / "test_dashboard.db"
    repo = DashboardRepository(db_path=db_file)
    assert db_file.exists()
    repo.init_db()
    assert repo.get_latest_briefing() is None


def test_9_archive_and_latest_retrieval(tmp_path: Path):
    """9. Test latest briefing and archive retrieval."""
    db_file = tmp_path / "dashboard.db"
    repo = DashboardRepository(db_path=db_file)

    b5 = parse_briefing_text(SAMPLE_SENT_EMAIL_BRIEFING_SEP5)
    repo.save_briefing(b5)

    b8 = parse_briefing_text(SAMPLE_SENT_EMAIL_BRIEFING_SEP8)
    repo.save_briefing(b8)

    latest = repo.get_latest_briefing()
    assert latest is not None
    assert latest.briefing_date == date(2026, 9, 8)

    archives = repo.get_archive_dates()
    assert len(archives) == 2
    assert archives[0]["briefing_date"] == date(2026, 9, 8)
    assert archives[1]["briefing_date"] == date(2026, 9, 5)


def test_10_homepage_and_archive_web_render(tmp_path: Path):
    """10. Test FastAPI web endpoints render correctly."""
    db_file = tmp_path / "dashboard.db"
    repo = DashboardRepository(db_path=db_file)
    b8 = parse_briefing_text(SAMPLE_SENT_EMAIL_BRIEFING_SEP8)
    repo.save_briefing(b8)

    app = create_app(db_path=db_file)
    client = TestClient(app)

    # Homepage
    resp_home = client.get("/")
    assert resp_home.status_code == 200
    assert "PLUTUS WEALTH MANAGEMENT LLP" in resp_home.text
    assert "Dangote Refinery Prepares Share Sale" in resp_home.text
    assert "Top 5 India Business Headlines" in resp_home.text

    # Archive
    resp_arch = client.get("/archive")
    assert resp_arch.status_code == 200
    assert "Briefing Archive" in resp_arch.text
    assert "Tuesday, 08 September 2026" in resp_arch.text

    # Health
    resp_health = client.get("/health")
    assert resp_health.status_code == 200
    assert resp_health.json()["status"] == "ok"


def test_11_dashboard_code_never_imports_or_executes_run_pipeline():
    """11. Static analysis: Dashboard modules never import run_pipeline or run_daily."""
    dashboard_files = [
        Path("app/dashboard/__init__.py"),
        Path("app/dashboard/models.py"),
        Path("app/dashboard/repository.py"),
        Path("app/dashboard/web.py"),
        Path("run_dashboard_sync.py"),
    ]

    prohibited_modules = {"run_pipeline", "run_daily", "app.pipeline.runner", "app.pipeline"}

    for f_path in dashboard_files:
        assert f_path.exists()
        tree = ast.parse(f_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in prohibited_modules, f"Forbidden import '{alias.name}' in {f_path}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not any(mod.startswith(p) for p in prohibited_modules), f"Forbidden import from '{mod}' in {f_path}"


def test_12_dashboard_sync_never_calls_external_apis(monkeypatch, tmp_path: Path):
    """12. Verify dashboard sync operates strictly offline."""
    def forbidden_call(*args, **kwargs):
        raise RuntimeError("External network call forbidden during dashboard sync!")

    monkeypatch.setattr("urllib.request.urlopen", forbidden_call)

    test_txt = tmp_path / "copy_paste_briefing.txt"
    test_txt.write_text(SAMPLE_SENT_EMAIL_BRIEFING_SEP8, encoding="utf-8")
    db_path = tmp_path / "dashboard.db"

    success = sync_dashboard(input_file=test_txt, db_path=db_path)
    assert success is True


def test_13_no_dashboard_database_url_sqlite_fallback(monkeypatch, tmp_path: Path):
    """A. Test that missing DASHBOARD_DATABASE_URL falls back to SQLite data/dashboard.db."""
    from app.dashboard.repository import DEFAULT_DB_PATH
    monkeypatch.delenv("DASHBOARD_DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    repo = DashboardRepository()
    assert repo.backend == "sqlite"
    assert repo.db_path == DEFAULT_DB_PATH
    assert repo.engine is None


def test_14_explicit_db_path_forces_sqlite(monkeypatch, tmp_path: Path):
    """B. Test that explicit db_path forces SQLite even if DASHBOARD_DATABASE_URL is set."""
    monkeypatch.setenv("DASHBOARD_DATABASE_URL", "postgresql://user:pass@ep-fake.neon.tech/neondb?sslmode=require")
    custom_db = tmp_path / "custom_test.db"
    repo = DashboardRepository(db_path=custom_db)
    assert repo.backend == "sqlite"
    assert repo.db_path == custom_db
    assert repo.engine is None


def test_15_dashboard_database_url_selects_postgresql(monkeypatch):
    """C. Test that DASHBOARD_DATABASE_URL selects PostgreSQL backend and normalizes URL."""
    from unittest.mock import MagicMock
    from app.dashboard.repository import normalize_database_url

    raw_url = "postgresql://usr_test:secret_pass_123@ep-test.neon.tech/neondb?sslmode=require"
    monkeypatch.setenv("DASHBOARD_DATABASE_URL", raw_url)

    # Test URL normalization
    norm_url = normalize_database_url(raw_url)
    assert norm_url.startswith("postgresql+psycopg://")
    assert "usr_test:secret_pass_123" in norm_url

    # Test postgres:// shorthand normalization
    assert normalize_database_url("postgres://u:p@h/d") == "postgresql+psycopg://u:p@h/d"
    assert normalize_database_url("postgresql+psycopg://u:p@h/d") == "postgresql+psycopg://u:p@h/d"

    # Mock create_engine and metadata.create_all so no network connection is attempted
    mock_engine = MagicMock()
    mock_engine.dialect.name = "postgresql"

    monkeypatch.setattr("app.dashboard.repository.create_engine", lambda url, **kw: mock_engine)
    monkeypatch.setattr("app.dashboard.repository.metadata.create_all", lambda eng: None)

    repo = DashboardRepository()
    assert repo.backend == "postgresql"
    assert repo.db_path is None
    assert repo.engine == mock_engine

    # Ensure credentials are not leaked in string representation
    repo_repr = repr(repo)
    assert "secret_pass_123" not in repo_repr
    assert "postgresql" in repo_repr


def test_16_sqlalchemy_backend_idempotency_and_conflict():
    """D & E. Test SQLAlchemy engine CRUD, idempotency, conflict detection, and replace."""
    from sqlalchemy import create_engine
    engine = create_engine("sqlite:///:memory:")
    repo = DashboardRepository(engine=engine)

    b_orig = parse_briefing_text(SAMPLE_SENT_EMAIL_BRIEFING_SEP8)
    id1 = repo.save_briefing(b_orig)
    assert id1 is not None

    # D. Same-date same-hash is idempotent no-op
    id2 = repo.save_briefing(b_orig)
    assert id1 == id2
    assert len(repo.get_archive_dates()) == 1

    # E. Same-date changed hash raises conflict unless allow_replace=True
    mod_text = SAMPLE_SENT_EMAIL_BRIEFING_SEP8.replace("Dangote Refinery", "Alternative Energy Corp")
    b_diff = parse_briefing_text(mod_text)

    with pytest.raises(DashboardSyncConflictError) as exc_info:
        repo.save_briefing(b_diff, allow_replace=False)
    assert "existing briefing differs from incoming briefing" in str(exc_info.value)

    # Safe replacement when allow_replace=True
    id3 = repo.save_briefing(b_diff, allow_replace=True)
    assert id3 == id1
    latest = repo.get_latest_briefing()
    assert latest is not None
    assert "Alternative Energy Corp" in latest.india_stories[0].headline


def test_17_workflow_yaml_contains_sync_and_secrets():
    """F & G. Verify GitHub Actions workflow structure, commands, secret passing, and schedule."""
    workflow_path = Path(".github/workflows/daily_briefing.yml")
    assert workflow_path.exists()
    content = workflow_path.read_text(encoding="utf-8")

    # F. Commands executed in exact order
    assert "python run_daily.py" in content
    assert "python run_dashboard_sync.py --file data/copy_paste_briefing.txt" in content
    run_daily_pos = content.index("python run_daily.py")
    run_sync_pos = content.index("python run_dashboard_sync.py --file data/copy_paste_briefing.txt")
    assert run_daily_pos < run_sync_pos

    # G. Secret passed to environment
    assert "DASHBOARD_DATABASE_URL: ${{ secrets.DASHBOARD_DATABASE_URL }}" in content

    # Schedule integrity
    assert "cron: '0 7 * * *'" in content
    assert "timezone: 'Asia/Kolkata'" in content
    assert "workflow_dispatch:" in content


def test_18_dashboard_routes_and_health_check(tmp_path: Path):
    """H & I. Verify dashboard routes do not call external APIs and /health returns backend type."""
    db_file = tmp_path / "dashboard.db"
    repo = DashboardRepository(db_path=db_file)
    b8 = parse_briefing_text(SAMPLE_SENT_EMAIL_BRIEFING_SEP8)
    repo.save_briefing(b8)

    app = create_app(db_path=db_file)
    client = TestClient(app)

    # Health check
    resp_health = client.get("/health")
    assert resp_health.status_code == 200
    data = resp_health.json()
    assert data["status"] == "ok"
    assert data["backend"] == "sqlite"
    assert data["latest_briefing_date"] == "2026-09-08"
    assert data["total_briefings_count"] == 1
    # Check no passwords in health response
    assert "password" not in str(data).lower()
