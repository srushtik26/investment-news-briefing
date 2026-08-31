# Automated Investment Committee News Briefing System

> **A production-ready, hybrid AI and deterministic Python intelligence pipeline that delivers verified daily executive intelligence across three independent sections: 5 India Business + 5 Domestic + 5 International Business (15 stories total). Features grounded factual summaries and direct canonical article URLs for Investment Committees, Fund Managers, and Corporate Executives.**

---

## Project Status

| Dimension | Current Production State |
|---|---|
| **Production Pipeline** | **Operational** (Structured modular pipeline under `app/pipeline/`) |
| **Section Yield** | **Validated 5 India + 5 Domestic + 5 International (15 Total)** |
| **Final Quality Validation** | **20/20 Deterministic Integrity Checks Passing** |
| **Automated Test Suite** | **689 passing tests** (0 failures, full local & CI regression coverage) |
| **GitHub Actions Automation** | **Manual & scheduled production runs validated** |
| **Automated Email Delivery** | **Validated via Gmail SMTP SSL** |
| **Automated Schedule** | **Daily at 06:40 AM IST (Primary) and 06:55 AM IST (Recovery)** |

---

## What is This Project?

In institutional investment and executive leadership, acting on unverified rumors, stale coverage, or clickbait commentary introduces severe risk. Conversely, manual news monitoring across fragmented outlets is time-prohibitive.

This automated intelligence system:
1. Discovers candidate coverage across global, Indian financial, and Indian national media outlets.
2. Extracts clean article text and validates publication timestamps with clock-drift protection.
3. Applies deterministic hard filters to reject speculative deal talks, commentary, opinions, and stale dates.
4. Classifies business events using Gemini AI and strict event schemas.
5. Verifies occurrences using a multi-tiered Quality Verification Ladder (independent two-source corroboration, high-confidence single-source evaluation, or domestic trending assessment).
6. Deduplicates against a 3-day SQLite lookback history to prevent repetition across days.
7. Ranks candidate pools deterministically using composite investment-relevance scoring.
8. Synthesizes grounded one-line factual summaries with complete grammatical structure.
9. Audits output through a 20-rule deterministic gatekeeper before delivering the final briefing via Gmail SMTP.

---

## Output Contract & The Three Briefing Sections

The briefing strictly enforces a **15-story daily quota (5 / 5 / 5)** across three mutually exclusive sections. India Business and Domestic stories are never merged.

| # | Section | Quota | Scope & Inclusion Criteria |
|---|---|---|---|
| 1 | **INDIA BUSINESS** | **5 Stories** | Hard Indian corporate actions: listed companies, earnings with concrete metrics, completed M&A/stake sales, IPO/DRHP filings, capex, and regulatory enforcement (SEBI, RBI, CCI). Evaluated via two-source verification or single-source score $\ge 80/100$. |
| 2 | **DOMESTIC** | **5 Stories** | Significant Indian national public affairs: Supreme Court and High Court verdicts, Union Cabinet decisions, parliamentary legislation, national security, space missions, infrastructure, and national alerts. Evaluated via `DomesticTrendingEvaluator` ($\ge 60/100$) to reject municipal civic trivia. |
| 3 | **INTERNATIONAL BUSINESS** | **5 Stories** | Major non-Indian commercial and macroeconomic developments: global earnings, multinational M&A, central bank rate actions (Federal Reserve, ECB, BOJ), enterprise technology/AI developments, and supply-chain shifts. |

---

## Canonical Briefing Format

The briefing uses full canonical article URLs preserved from original publishers (no shortened URLs). When independent dual-source verification succeeds, the secondary corroborating source is rendered.

```text
*INVESTMENT COMMITTEE BRIEFING*
*31 August 2026*

*TOP 5 INDIA BUSINESS HEADLINES*

*Reliance Retail Acquires 100% Stake in Technology Platform for Rs 500 Crore*
Reliance Retail completed the full buyout of the enterprise retail software developer to bolster its digital fulfillment stack.
Source: Business Standard
https://www.business-standard.com/companies/news/reliance-retail-deal-example-12345.html
Also verified by: The Economic Times
https://economictimes.indiatimes.com/industry/services/retail/reliance-retail-deal-example/articleshow/98765432.cms

*Tata Motors Q1 Net Profit Rises 15% to Rs 3500 Crore on Commercial Vehicle Margin Expansion*
Tata Motors reported a 15 percent year-over-year increase in quarterly net income driven by operational efficiency in domestic commercial fleets.
Source: The Financial Express
https://www.financialexpress.com/auto/tata-motors-q1-earnings-example-34567.html

...

*TOP 5 DOMESTIC HEADLINES*

*Supreme Court Directs High Courts to Formulate Standard Guidelines for Virtual Hearings*
A three-judge bench mandated uniform procedural protocols across all state jurisdictions to preserve public access to judicial records.
Source: The Hindu
https://www.thehindu.com/news/national/supreme-court-virtual-hearing-guidelines-example.html

...

*TOP 5 INTERNATIONAL BUSINESS HEADLINES*

*Nvidia Signs Definitive Agreement to Acquire Run:ai for $700 Million*
Nvidia concluded negotiations to purchase the workload orchestration developer in an all-cash transaction to optimize GPU cluster utilization.
Source: Reuters
https://www.reuters.com/technology/nvidia-runai-acquisition-example-56789.html
Also verified by: CNBC
https://www.cnbc.com/2026/08/31/nvidia-buys-runai-deal-example.html
```

---

## Pipeline Architecture & Modular Flow

The architecture decouples orchestration into focused modules located under `app/pipeline/`, while `run_pipeline.py` serves as the public entry-point and backward-compatible wrapper.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 PIPELINE FLOW                                          │
└────────────────────────────────────────────────────────────────────────────────────────┘
  1. Discovery Reserves   ► Multi-query Google News RSS reserve pool (Domestic, India, Intl)
  2. Resolution & Fetch   ► Batch URL resolution and HTML extraction with per-run caching
  3. Hard Filtering       ► Deterministic gate for whitelist sources, dates, and noise rejection
  4. Pre-Ranking          ► Financial and entity density prioritization before AI stages
  5. Event Classification ► Gemini AI structured classification into hard business schemas
  6. Quality Verification ► Quality Ladder: Two-Source, Single-Source (≥80), Domestic (≥60)
  7. Secondary Enrichment ► RSS-based second-source corroboration for top single-source items
  8. History Deduplication► 3-day SQLite lookback history to ensure multi-day story novelty
  9. Relevance Ranking    ► 5-factor mathematical score (Magnitude, Market, Investor, Corp, Freshness)
 10. Fallback Ladder      ► Section horizons (Strict 24h → 36h → 48h → Emergency 72h)
 11. Editorial Curation   ► Grounded 1-line summary synthesis with deterministic fallback
 12. Final 20-Check Audit ► 20-rule deterministic gatekeeper validates all constraints
 13. Output Delivery      ► Generates plain text and HTML briefing delivered via Gmail SMTP
```

### Module Responsibilities (`app/pipeline/`)

| Module | Role |
|---|---|
| `runner.py` | Orchestrates the end-to-end execution lifecycle and metric logging. |
| `context.py` | Manages shared state, runtime timers, pools, and service dependencies. |
| `discovery_stage.py` | Fetches balanced reserve pools across Domestic, India, and International. |
| `candidate_processing.py` | Coordinates bounded concurrent extraction and date/entity extraction. |
| `fallback_manager.py` | Manages quality level horizons (24h to 72h) and SerpAPI fallback budget. |
| `enrichment.py` | Searches Google News RSS to corroborates top single-source candidates. |
| `selection.py` | Executes cross-section and SQLite deduplication, ranking, and story selection. |
| `reporting.py` | Renders candidate audits, final story audits, and execution logs. |

---

## Quality Verification Ladder & Fallback Horizons

To maintain uncompromising editorial standards during slow news cycles or weekend sessions, the pipeline utilizes a deterministic **Quality Ladder**:

### Verification Tiers
1. **Tier 1 — Two-Source Independent Verification**: Pairs matching stories from separate publishing groups (excluding syndicated wire duplicates, republishers, and same-parent corporate media groups).
2. **Tier 2 — High-Confidence Single-Source Gate**: Requires single-publisher business reports to achieve a score $\ge 80/100$ based on trusted source pedigree, named corporate entity, recognized transaction action, and quantified monetary figures.
3. **Tier 3 — Domestic Trending Evaluator**: Evaluates national public affairs ($\ge 60/100$) focusing on governance, constitutional courts, and defense developments without requiring balance-sheet metrics.

### Quality Horizons
When initial 24-hour candidate reserves do not yield 5 verified stories for a section, the system steps down through explicit quality horizons without relaxing filtering rules:
- `STRICT_SUCCESS` (0–24 hours)
- `FALLBACK_SUCCESS_24H` (24 hours)
- `FALLBACK_SUCCESS_36H` (Up to 36 hours)
- `FALLBACK_SUCCESS_48H` (Up to 48 hours)
- `EMERGENCY_SUCCESS_72H` (Up to 72 hours)
- Anything beyond 72 hours terminates as `DATA_UNAVAILABLE` rather than admitting stale or low-quality articles.

---

## Performance Architecture & Optimizations

The pipeline incorporates production-grade performance safeguards:
- **Bounded Concurrency**: Thread pool bounded to 5 concurrent workers for HTTP fetches to prevent socket exhaustion and rate-limiting.
- **Dual-Tier Extraction Caching**: In-memory per-run cache tracks successful extractions and avoids repeating failed 404/403/timeout URLs.
- **Google News URL Resolution Cache**: Pre-resolves and caches Google News redirect paths to prevent redundant HTTP redirect hops.
- **Domain Circuit Breaker**: Identifies degraded domains after consecutive 401/403 failures (e.g., paywalled endpoints) and skips remaining URLs on that domain during the run.
- **Cheap Pre-Extraction Filtering**: Rejects invalid URL formats, degraded domains, and obvious non-article URLs before initiating network requests.
- **Targeted Second-Source Enrichment**: Limits RSS second-source search to the top 7 single-source candidate events per section, prioritized by investment relevance score.
- **Deterministic Component Execution**: Ranking, clustering, and SQLite history reads execute deterministically to ensure reproducible output.

---

## Daily Automation & Email Delivery

The daily runner script (`run_daily.py`) handles production execution and automated email delivery:

```
[GitHub Actions Schedule] (06:40 AM IST / 06:55 AM IST Recovery)
                │
                ▼
        [run_daily.py]
                │
                ├─► 1. Load environment variables (.env / GitHub Secrets)
                ├─► 2. Check last_email_date.txt (Idempotency Guard)
                │      └─► If today's date exists: Exit 0 (prevents duplicate delivery)
                ├─► 3. Validate Gmail SMTP credentials presence
                ├─► 4. Run production pipeline (run_pipeline.py)
                ├─► 5. Verify data/final_briefing.txt artifact exists & is non-empty
                ├─► 6. Confirm presence of India, Domestic, and International sections
                ├─► 7. Send multipart HTML/Text email via Gmail SMTP SSL (Port 465)
                └─► 8. Record delivery date in data/last_email_date.txt ONLY after SMTP success
```

### GitHub Actions Workflow (`.github/workflows/daily_briefing.yml`)

The workflow runs on `ubuntu-latest` every single day—including weekends, market holidays, and national holidays:
- **Primary Execution**: `06:40 AM IST` (`01:10 UTC` | `cron: '10 1 * * *'`)
- **Recovery Execution**: `06:55 AM IST` (`01:25 UTC` | `cron: '25 1 * * *'`)

> **Note on Scheduling**: GitHub Actions cron jobs are queued on shared runners; actual execution may begin a few minutes after the scheduled timestamp depending on runner load. The same-day idempotency check ensures that if the primary run succeeds, the recovery run exits cleanly without sending duplicate emails.

---

## GitHub Actions Secrets Setup

To enable automated delivery in GitHub Actions, configure the following secrets under **Repository Settings → Secrets and variables → Actions**:

| Secret Name | Required | Purpose | Example / Notes |
|---|---|---|---|
| `GEMINI_API_KEY` | **Yes** | Google Gemini API key for event classification and editorial summaries | `AIzaSy...` |
| `SERPAPI_API_KEY` | Optional | SerpAPI key used exclusively as secondary fallback when RSS corroboration is exhausted | `abc123...` |
| `GMAIL_SENDER` | **Yes** | Sending Gmail address | `briefings@gmail.com` |
| `GMAIL_RECIPIENT` | **Yes** | Primary recipient address (or comma-separated addresses in `GMAIL_RECIPIENTS`) | `investment-committee@example.com` |
| `GMAIL_APP_PASSWORD` | **Yes** | 16-character Google Account App Password (requires 2-Step Verification) | `abcd efgh ijkl mnop` |

---

## Tech Stack & Dependencies

All dependencies are defined in `requirements.txt`:

| Component | Library / Technology |
|---|---|
| **Runtime** | Python 3.11+ |
| **Data Modeling** | `pydantic>=2.7.0`, `pydantic-settings>=2.2.0` |
| **AI Synthesis** | `google-genai>=0.1.1` (Gemini 2.5/3.6 models) |
| **HTTP & Parsing** | `httpx>=0.27.0`, `requests>=2.31.0`, `beautifulsoup4>=4.12.3`, `googlenewsdecoder==0.1.7` |
| **Environment** | `python-dotenv>=1.0.1` |
| **Persistence** | Embedded `sqlite3` (schema tracking briefings, stories, and publisher history) |
| **Email Protocol** | Native Python `smtplib` and `email` over SSL (Port 465) |
| **Testing** | `pytest>=8.1.0`, `pytest-cov>=5.0.0`, `pytest-asyncio>=0.23.0` |

---

## Repository Structure

```
investment-news-briefing/
├── .github/
│   └── workflows/
│       └── daily_briefing.yml  # Automated daily scheduling (06:40 & 06:55 IST)
├── app/
│   ├── ai/                     # Gemini client, editorial curation, grounding checks
│   ├── classification/         # Business event schemas & regional provenance classification
│   ├── database/               # SQLite schema & execution history storage
│   ├── deduplication/          # Event clustering, entity sanitizer & 3-day history lookback
│   ├── discovery/              # Google News RSS provider & candidate reserve collection
│   ├── extraction/             # HTML fetcher, Google News decoder, date parsing
│   ├── filtering/              # Deterministic whitelist, freshness, and content rules
│   ├── formatting/             # Executive briefing formatters (Full canonical URLs)
│   ├── logging_config.py       # Rotating file and console logger
│   ├── models/                 # Pydantic models (Article, Event, Briefing, Enums)
│   ├── pipeline/               # Modular pipeline implementation
│   │   ├── candidate_processing.py
│   │   ├── context.py
│   │   ├── discovery_stage.py
│   │   ├── enrichment.py
│   │   ├── fallback_manager.py
│   │   ├── reporting.py
│   │   ├── runner.py           # Core pipeline orchestration
│   │   └── selection.py
│   ├── ranking/                # Pre-ranker & multi-factor investment relevance scorer
│   ├── utils/                  # Performance metrics, timer counters, sanitizers
│   ├── validation/             # 20-rule deterministic gatekeeping engine
│   └── verification/           # TwoSourceVerifier, DomesticTrending & SingleSource evaluators
├── data/                       # Local SQLite history database & output text artifacts
├── logs/                       # Rotating runtime execution logs
├── tests/                      # 689 automated unit, integration, and regression tests
├── .env.example                # Environment configuration template
├── config.py                   # Centralized Pydantic application settings
├── requirements.txt            # Production Python package requirements
├── run_daily.py                # Daily runner with idempotency & email dispatch
├── run_pipeline.py             # Public compatibility entry-point
└── README.md                   # System documentation
```

---

## Quick Start Guide

### 1. Environment Setup

```bash
# Clone the repository
git clone https://github.com/srushtik26/investment-news-briefing.git
cd investment-news-briefing

# Create virtual environment
python -m venv .venv
```

**Activate Virtual Environment:**
- **Windows (PowerShell)**:
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```
- **macOS / Linux**:
  ```bash
  source .venv/bin/activate
  ```

**Install Dependencies:**
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Create `.env` from `.env.example`:
```bash
cp .env.example .env
```

Configure required keys:
```env
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.6-flash

GMAIL_SENDER=your_address@gmail.com
GMAIL_RECIPIENT=recipient@example.com
GMAIL_APP_PASSWORD=your_16_digit_app_password
```

### 3. Execution Commands

**Run the Core Intelligence Pipeline:**
```bash
python run_pipeline.py
```
*(Windows PowerShell direct invocation without activating subshell:)*
```powershell
.\.venv\Scripts\python.exe run_pipeline.py
```

**Run Daily Automation with Delivery Guard:**
```bash
python run_daily.py
```
*(Windows PowerShell direct invocation:)*
```powershell
.\.venv\Scripts\python.exe run_daily.py
```

---

## Testing & Quality Assurance

The codebase contains **689 passing tests** validating all architectural components, deterministic edge cases, regional provenance, verification thresholds, and pipeline contracts:

```bash
# Run the complete test suite
python -m pytest

# Run with verbose output
pytest -v

# Windows direct venv execution
.\.venv\Scripts\python.exe -m pytest
```

---

## Security Best Practices

- **Never Commit Secrets**: Keep `.env` and sensitive credentials out of version control. The `.gitignore` file excludes `.env`, `data/`, and runtime logs.
- **Repository Secrets**: Use GitHub Actions Repository Secrets for CI/CD automation.
- **Google App Passwords**: Always generate a dedicated 16-character Google Account App Password for SMTP rather than your primary Google Account password.
- **Credential Rotation**: Rotate any credentials immediately if accidentally exposed or logged.
