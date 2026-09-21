# Automated Investment Committee News Briefing System

> An open-source, automated business news intelligence pipeline built to discover, extract, verify, rank, validate, and distribute high-signal economic intelligence across three balanced sections: **5 India Business + 5 Domestic Policy/Affairs + 5 International Business (15 stories total)**.

The system combines deterministic Python data-quality validation, AI-assisted event classification and editorial curation, multi-source corroboration, and automated email and web distribution—backed by extensive regression testing.

> **Disclaimer**: This project is an automated news aggregation and information processing pipeline designed for informational and research purposes. It is **not** a source of financial, investment, legal, or tax advice.

---

## Project Status

| Metric / Dimension | Current Repository State | Verification Source |
|---|---|---|
| **Production CI/CD Entry Point** | `python run_daily_15.py` | [`.github/workflows/daily_briefing.yml`](.github/workflows/daily_briefing.yml) |
| **Pipeline CLI Entry Point** | `python run_pipeline.py` | [`run_pipeline.py`](run_pipeline.py) |
| **Automated Execution Schedule** | Daily at **02:30 AM IST** (`21:00 UTC` previous day) with automatic recovery at **03:15 AM IST** (`21:45 UTC`) | [`.github/workflows/daily_briefing.yml`](.github/workflows/daily_briefing.yml) |
| **Coverage Schedule** | 7 days / week (including market holidays & weekends) | [`.github/workflows/daily_briefing.yml`](.github/workflows/daily_briefing.yml) |
| **Daily Section Contract** | Exactly **15 Stories**: 5 India Business + 5 Domestic + 5 International | [`app/validation/engine.py`](app/validation/engine.py) |
| **Integrity Gatekeeper** | **20/20 Deterministic Pre-Publication Validation Checks** | [`app/validation/engine.py`](app/validation/engine.py) |
| **Automated Test Suite** | **1,083 automated tests**, 0 failures (100% pass rate) | `pytest tests -q` |
| **Email Delivery** | Idempotent dispatch via Gmail SMTP over SSL (Port 465) | [`run_daily.py`](run_daily.py) |
| **Live Web Dashboard** | FastAPI web application: [https://plutus-news.onrender.com/](https://plutus-news.onrender.com/) | [`run_dashboard_sync.py`](run_dashboard_sync.py) |

---

## Why This Project Exists

Financial and market intelligence monitoring faces severe signal-to-noise problems:
- **Fragmented Coverage**: Important corporate developments and regulatory actions are scattered across hundreds of regional, national, and international outlets.
- **Unverified Noise & Rumors**: Raw news feeds and RSS streams frequently circulate uncorroborated rumors, speculative deal talks, sponsored advertorials, and clickbait commentary.
- **Fragile AI Summarizers**: Many automated news pipelines rely entirely on LLMs to ingest raw text and generate summaries. Without rigid external constraints, generative models hallucinate events, conflate currencies and units (e.g. converting Indian Crores to Western Billions), invent generic entity names, and cite broken or fabricated URLs.
- **Stale Information**: Standard scrapers frequently re-surface multi-day-old stories or allow time-drift to accept outdated reporting.

This project addresses these challenges by implementing a **hybrid architecture** that uses AI strictly where language understanding is beneficial (entity and event categorization, concise phrasing), but constrains all inputs and outputs through **deterministic Python gates** (hard filtering, arithmetic verification, multi-source matching, token overlap prechecks, and SQLite lookback deduplication).

### Who Benefits from This Repository
- **Developers building news intelligence systems**: Real-world reference for multi-stage ingestion, deduplication, and quality control.
- **Researchers experimenting with grounded AI**: Practical example of anchoring generative AI outputs to source text using token overlap and numeric validation.
- **Maintainers building scheduled workflows**: Reference implementation for idempotent cron automation, multi-tier fallback horizons, and resilient CI/CD runners.
- **FinTech prototypers**: Production-ready data models for business events, multi-publisher corroboration, and investment relevance ranking.

---

## Open-Source Value

### Why This Project Is Useful to the Open-Source Ecosystem
This repository provides reusable architectural patterns for production data pipelines:
- **Hybrid AI + Deterministic Pattern**: Generative AI models are placed between deterministic pre-filters and post-validation gates. Output that fails validation is rejected or routed through progressive deterministic fallback ladders.
- **Multi-Source Corroboration Engine**: Cross-references matching coverage across independent publishing groups while discarding wire-service syndication duplicates, republishers, and same-parent media networks.
- **Web Content Extraction & URL Resolution**: Resolves obfuscated aggregator redirect URLs, strips tracking metadata, respects circuit breakers on degraded domains, and caches extraction results within execution runs.
- **Quality Fallback Horizons**: Expands publication lookback windows progressively (24h → 36h → 48h → 72h max) during slow news sessions or weekend market closures without weakening quality criteria, terminating gracefully rather than admitting stale articles.
- **Strict Output Grounding**: Pre-checks synthetic headlines and summaries against article text to ensure every corporate entity, monetary figure, and percentage is anchored in verified source copy.
- **Zero-Cost Local Development**: The entire test suite executes offline with mocked responses, enabling contributors to develop and test without paid API keys.

---

## System Architecture

The pipeline processes news through thirteen modular stages, cleanly separated into AI-assisted and deterministic components:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   PIPELINE FLOW                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
  1. Multi-Query Discovery  [Deterministic] ► Collects candidate reserves via Google News RSS
  2. URL Resolution & Fetch [Deterministic] ► Decodes aggregator URLs, extracts clean HTML
  3. Hard Filtering         [Deterministic] ► Enforces source whitelist, freshness, hard filters
  4. Pre-Ranking            [Deterministic] ► Scores entity & financial density before AI calls
  5. Event Classification   [AI - Gemini]   ► Structures raw stories into validated Event schemas
  6. Quality Verification   [Deterministic] ► Quality Ladder: Two-Source, Single-Source, Domestic
  7. Secondary Enrichment   [Deterministic] ► Corroborates top single-source events via RSS
  8. History Deduplication  [Deterministic] ► 3-day SQLite lookback prevents inter-day duplication
  9. Relevance Ranking      [Deterministic] ► 5-factor scoring (Magnitude, Market, Investor, Corp, Freshness)
 10. Fallback Management    [Deterministic] ► Controls quality horizons (24h → 36h → 48h → 72h)
 11. Editorial Curation     [AI + Fallback] ► Synthesizes grounded summaries; 3-tier fallback
 12. Final 20-Check Audit   [Deterministic] ► 20 programmatic validation gates audit briefing
 13. Output Distribution    [Deterministic] ► Plaintext/HTML via Gmail SMTP & FastAPI dashboard sync
```

### AI Stages vs. Deterministic Stages

| Stage | Mode | Technology | Primary Function |
|---|---|---|---|
| **Event Classification** | AI-Assisted | Google Gemini Flash | Identifies corporate events, sectors, and transaction structures into strict Pydantic schemas. |
| **Editorial Curation** | AI-Assisted (with Deterministic Fallback) | Google Gemini Flash + Deterministic Rules | Drafts concise, active-voice executive summaries. If the API limits or fails, a 3-tier deterministic fallback ladder generates grounded copy. |
| **Discovery & Extraction** | Purely Deterministic | `httpx`, `beautifulsoup4`, `googlenewsdecoder` | Fetches RSS feeds, resolves redirect hops, and parses semantic article text. |
| **Filtering & Verification** | Purely Deterministic | Python regex, Pydantic, heuristics | Filters opinion/commentary, applies source whitelists, verifies corroboration tiers. |
| **Deduplication & Ranking** | Purely Deterministic | SQLite, mathematical scoring | Clusters related articles, queries 3-day SQLite history, and calculates composite relevance scores. |
| **Final Validation** | Purely Deterministic | Python (`app/validation/`) | 20 mandatory programmatic checks that verify URLs, dates, numbers, entity overlap, and quotas. |
| **Delivery & Synchronization**| Purely Deterministic | `smtplib`, SQLAlchemy, FastAPI | Sends formatted email via SSL and syncs records to dashboard database. |

### Module Responsibilities (`app/pipeline/`)

| Module | Role |
|---|---|
| [`runner.py`](app/pipeline/runner.py) | Orchestrates the end-to-end execution lifecycle, metric tracking, and stage handoffs. |
| [`context.py`](app/pipeline/context.py) | Manages shared execution state, runtime timers, candidate lookups, and service dependencies. |
| [`discovery_stage.py`](app/pipeline/discovery_stage.py) | Coordinates multi-query Google News RSS collection across Domestic, India, and International. |
| [`candidate_processing.py`](app/pipeline/candidate_processing.py) | Manages bounded concurrent extraction, URL decoding, and timestamp validation. |
| [`fallback_manager.py`](app/pipeline/fallback_manager.py) | Governs quality level horizons (24h to 72h) and optional SerpAPI corroboration budgets. |
| [`enrichment.py`](app/pipeline/enrichment.py) | Searches Google News RSS to corroborate top single-source candidates. |
| [`selection.py`](app/pipeline/selection.py) | Executes cross-section and SQLite history deduplication, scoring, and candidate filtering. |
| [`reporting.py`](app/pipeline/reporting.py) | Generates candidate audits, story selection logs, and execution metric summaries. |

---

## Output Contract & The Three Briefing Sections

The briefing enforces an uncompromised **15-story daily contract (5 / 5 / 5)** across three mutually exclusive sections:

| # | Section | Daily Quota | Editorial Criteria & Verification Standard |
|---|---|---|---|
| 1 | **INDIA BUSINESS** | **5 Stories** | Major Indian corporate transactions, quarterly earnings with quantified metrics, listed entity capex, completed M&A/stake sales, and regulatory enforcement (SEBI, RBI, CCI). Verified via independent dual-source corroboration or high-confidence single-source scoring ($\ge 80/100$). |
| 2 | **DOMESTIC** | **5 Stories** | National macroeconomic governance, Supreme Court and High Court rulings, Union Cabinet decisions, parliamentary legislation, strategic infrastructure, and central regulatory alerts. Verified via `DomesticTrendingEvaluator` ($\ge 60/100$) to reject municipal noise. |
| 3 | **INTERNATIONAL BUSINESS** | **5 Stories** | Global corporate and macroeconomic developments: multinational earnings, cross-border M&A, central bank interest rate policies (Fed, ECB, BOJ), enterprise technology initiatives, and international supply-chain shifts. |

---

## Canonical Briefing Format

Briefings preserve direct canonical article URLs (no link shorteners). When independent dual-source verification succeeds, the corroborating secondary outlet is explicitly attributed:

```text
*INVESTMENT COMMITTEE BRIEFING*
*21 September 2026*

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

## Safety and Reliability Design

The codebase adheres to strict software reliability principles implemented directly in Python:

1. **Untrusted Content as Pure Data**: External web content is never evaluated as code or instructions. HTML input is stripped of tags, normalized, and decoded before being passed to classifiers.
2. **Deterministic Gatekeeping (Check-Before-Publish)**: Stage 9 executes 20 programmatic validation checks. If any single check fails, the pipeline halts with status `FAILED` rather than distributing corrupted or incomplete briefings.
3. **Progressive Fallback Ladder**: If the generative AI service experiences rate-limiting (HTTP 429) or transient outages, the system automatically falls back through a 3-tier deterministic safety ladder:
   - *Level 1*: Institutional template synthesis validated against token overlap and numeric grounding.
   - *Level 2*: Source-grounded headline rewrite with extracted strategic consequence clauses.
   - *Level 3*: Cleaned original publisher canonical title.
4. **Numeric & Unit Preservation**: Numeric tokens are parsed and grounded against source article text. The pipeline explicitly prevents unit corruption (e.g. converting Indian Crores into Western Billions, ambiguous `100b` suffixes, or treating basis points as monetary penalties).
5. **Bounded Concurrency & Network Throttling**: HTTP operations use a bounded thread pool (maximum 5 concurrent workers) with strict connection timeouts to prevent socket exhaustion and publisher rate-limiting.
6. **Domain Circuit Breaker**: Tracks consecutive HTTP 401/403/timeout responses from external domains. If a domain demonstrates persistent degradation or aggressive paywalls, subsequent URLs from that domain are skipped for the remainder of the run.
7. **Extraction Caching**: In-memory per-run cache prevents duplicate HTTP requests to identical URLs across discovery queries.
8. **Inter-Day Deduplication**: Maintains a local SQLite history store with event fingerprints to prevent identical stories from reappearing across consecutive days (3-day lookback window).
9. **Idempotent Email Automation**: Records successful delivery dates in a local state file (`data/last_email_date.txt`). Duplicate execution triggers on the same calendar date exit cleanly with code `0` without resending emails.
10. **Clean Failure Mode**: If news inventory is insufficient to satisfy the 5/5/5 contract after exhausting the 72-hour emergency horizon, the pipeline fails cleanly with diagnostic metrics rather than fabricating stories or relaxing verification rules.

---

## Security

- **Secrets Isolation**: All sensitive credentials (`GEMINI_API_KEY`, `GMAIL_APP_PASSWORD`, `SERPAPI_API_KEY`, `DASHBOARD_DATABASE_URL`) are loaded from environment variables or GitHub Actions Secrets. None are committed to the codebase.
- **Git Exclusion Guard**: The repository `.gitignore` strictly excludes `.env`, `data/`, `logs/`, SQLite database files, and local artifacts from version control.
- **Application-Specific Email Passwords**: Email dispatch requires a dedicated 16-character Google Account App Password under 2-Step Verification rather than primary account credentials.
- **URL & Input Validation**: Inbound URLs are validated against allowed schemes (`http`, `https`), valid network locations, and structural article indicators, discarding directory roots, calendar hubs, and dead links.
- **Dependency Pinning**: All production packages are explicitly pinned in `requirements.txt` to mitigate supply-chain drift.
- **Vulnerability Reporting**: If you discover a security issue or credential exposure, please contact the maintainer directly and privately. **Do not create public GitHub issues containing credentials, tokens, or sensitive logs.**
  *(Note: A formal `SECURITY.md` policy file will be added as open-source governance expands.)*

---

## Testing and Quality Assurance

Testing is a core architectural pillar of the project. The test suite comprises **1,083 automated tests** with a **100% pass rate** across all components:

```bash
# Execute the complete automated test suite
python -m pytest

# Run tests in quiet mode
python -m pytest tests -q

# Run with verbose output
pytest -v
```

### Test Suite Coverage Areas
- **Import Smoke & Architecture**: Confirms modular imports execute cleanly without circular dependencies (`tests/test_import_smoke.py`).
- **Deterministic Editorial Fallback**: Verifies 3-level safety ladder, numeric grounding, generic entity rejection, and offline 15-story generation (`tests/test_deterministic_editorial_fallback.py`).
- **Final Validation Engine**: Exhaustively tests all 20 programmatic gatekeeper checks against valid and invalid payloads (`tests/test_validation.py`).
- **Institutional Headline Synthesis**: Validates headline structure, active verbs, and non-routine formatting (`tests/test_institutional_headlines.py`).
- **Topic Diversity & Summary Grounding**: Ensures soft topic diversity across sections and validates summary entity overlap (`tests/test_topic_diversity_and_summary_grounding.py`).
- **Weekend & Horizon Recovery**: Tests multi-tier horizon expansions (24h → 36h → 48h → 72h) and weekend state persistence (`tests/test_weekend_recovery.py`).
- **Deduplication & Clustering**: Validates fingerprint generation, SQLite history stores, and cross-section company deduplication.
- **Extraction & URL Resolution**: Tests Google News decoder integration, HTML semantic parsing, and timestamp parsing with clock-drift protection.
- **Filtering & Provenance**: Tests hard filtering rules, source whitelisting, and regional provenance classification (India vs. International vs. Domestic).

---

## Maintainer Information

This project is currently maintained by:

**Srushti Kadam**
- **GitHub**: [@srushtik26](https://github.com/srushtik26)

Maintenance responsibilities currently include:
- System architecture and modular pipeline design
- CI/CD workflow automation and GitHub Actions monitoring
- Pipeline reliability, rate-limit recovery, and fallback engineering
- Comprehensive test suite expansion and regression prevention
- AI integration, grounding prompts, and deterministic validation rules
- Documentation, technical specifications, and repository health

---

## Contributing

Contributions from the open-source community are welcome. Because this pipeline operates under strict reliability contracts, contributors should adhere to the following workflow:

### Contribution Workflow
1. **Fork the Repository**: Create a fork of `srushtik26/investment-news-briefing` on GitHub.
2. **Create a Feature Branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Make Focused Changes**: Keep pull requests modular and focused on a single capability or bug fix.
4. **Maintain Test Coverage**: If modifying or adding logic, add accompanying tests under `tests/`.
5. **Run the Full Test Suite**:
   ```bash
   python -m pytest tests -q
   ```
   *All 1,083 tests must pass before submitting a pull request.*
6. **Do Not Commit Secrets**: Ensure no API keys, personal emails, or credentials exist in code or commits.
7. **Submit a Pull Request**: Provide a clear description of the problem, implementation rationale, and testing evidence.

### Areas for Contribution
- **Publisher Adapters & RSS Queries**: Enhancing query definitions and extraction heuristics for major financial outlets.
- **Extraction Robustness**: Expanding parsing resiliency against dynamic HTML structures and content layouts.
- **Test Fixtures & Edge Cases**: Contributing real-world HTML fixtures and edge-case regression scenarios.
- **Observability & Metrics**: Improving structured logging, performance telemetry, and diagnostic counters.
- **Documentation**: Refining technical documentation, architectural diagrams, and developer setup guides.
- **Dashboard Accessibility**: Improving mobile responsiveness and accessibility on the FastAPI web dashboard.

> **Local Development Note**: Contributors can run all 1,083 automated tests locally without any paid API keys (Gemini, SerpAPI) using offline mocks and deterministic test fixtures.

---

## Good First Issues / Contribution Ideas

If you are looking for an initial way to contribute, consider these starter ideas:
- **New Extraction Test Fixtures**: Add offline HTML test fixtures for new financial publication layouts in `tests/fixtures/`.
- **Source Normalization Expansion**: Expand domain-to-source-name mapping rules in `app/utils/text_patterns.py`.
- **Logging Improvements**: Standardize execution log prefixes and diagnostic counters across pipeline stages.
- **Documentation & Diagrams**: Enhance inline docstrings and architectural workflow documentation.
- **Date Parsing Resiliency**: Add unit test cases for unusual timestamp and timezone formats.

---

## Technology Stack

The project relies strictly on established, production-grade Python libraries defined in `requirements.txt`:

| Component / Layer | Technology | Version | Purpose |
|---|---|---|---|
| **Language Runtime** | Python | `3.11+` | Core programming language runtime |
| **Data Validation & Settings** | Pydantic | `2.13.4` | Strict data modeling, runtime type enforcement, and schema validation |
| **Settings Management** | Pydantic Settings | `2.15.0` | Environment configuration loading (`.env` and environment variables) |
| **AI Integration** | Google GenAI SDK | `2.19.0` | Structured event classification and editorial summary generation |
| **HTTP Client (Async/Sync)** | HTTPX | `0.28.1` | Concurrent network requests, connection pooling, and timeouts |
| **HTTP Client (Fallback)** | Requests | `2.34.2` | Secondary HTTP client for specific provider adapters |
| **HTML Parsing** | BeautifulSoup4 | `4.15.0` | Clean semantic extraction of article text, titles, and metadata |
| **Aggregator Decoder** | GoogleNewsDecoder | `0.1.7` | Decodes obfuscated Google News RSS redirect URLs to canonical publisher links |
| **Web Dashboard Framework** | FastAPI | `0.141.1` | High-performance asynchronous web server for the public dashboard |
| **ASGI Server** | Uvicorn | `0.52.4` | Production web server runner for FastAPI |
| **Database ORM** | SQLAlchemy | `2.0.52` | Database modeling and queries for dashboard state |
| **PostgreSQL Driver** | Psycopg (Binary) | `3.3.5` | PostgreSQL driver for production dashboard storage (e.g. Neon) |
| **Template Engine** | Jinja2 | `3.1.6` | HTML rendering for executive email and dashboard templates |
| **Testing Framework** | Pytest | `9.1.1` | Automated test execution and discovery |
| **Test Coverage** | Pytest-Cov | `7.1.0` | Test coverage reporting and metrics |
| **Async Testing** | Pytest-Asyncio | `1.4.0` | Testing support for asynchronous functions |
| **Local Persistence** | SQLite | Built-in | Embedded local storage for 3-day history deduplication and tracking |
| **Email Protocol** | smtplib & email | Built-in | Native Python SMTP transmission over SSL |

---

## Current Limitations

To provide complete transparency for open-source users and maintainers, the following operational limitations exist:
- **Third-Party HTML Dependency**: Article extraction relies on external publishers maintaining standard semantic HTML structures. Major structural redesigns by publishers may require updating extraction rules.
- **External API Rate Limits**: Generative AI APIs (e.g., Google Gemini) can experience transient HTTP 429 rate limits during peak usage. While mitigated by our 3-tier deterministic fallback ladder, AI summaries during outages will rely on deterministic templates.
- **Corroboration Availability**: Independent dual-source corroboration depends on multiple publishers covering the same event within the lookback window. Slow news days or weekends rely on high-confidence single-source scoring and domestic trending evaluations.
- **Paywalls & Anti-Scraping Defenses**: Certain financial news outlets enforce strict paywalls or anti-bot protections. The pipeline uses domain circuit breakers to skip degraded domains rather than hanging or failing.
- **GitHub Actions Runner Scheduling**: GitHub Actions cron jobs are queued on shared infrastructure and may not trigger at the exact second configured.
- **Information Pipeline Only**: The system is designed strictly for information extraction, classification, and summarization; it does not evaluate asset valuation or provide trading signals.

---

## Roadmap

Planned architectural improvements for upcoming iterations:
- [ ] **Observability & OpenTelemetry**: Introduce structured tracing and latency profiling across extraction and AI stages.
- [ ] **Automated Dependency Auditing**: Integrate automated security scanning (e.g., Dependabot, Bandit) into the CI pipeline.
- [ ] **Configurable Section Profiles**: Support customizable section quotas (e.g., custom story counts or specialized industry sectors).
- [ ] **Enhanced Dashboard Analytics**: Historical search filters, sector distribution breakdowns, and export formats.
- [ ] **Dedicated Contributor Guidelines**: Add standalone `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md` documents.

---

## License

*A formal open-source license (such as MIT or Apache-2.0) is currently pending selection for this repository. Until a license file is formally committed, all rights are reserved by the maintainer.*

---

## Local Development Quick Start

### 1. Clone & Set Up Virtual Environment

```bash
git clone https://github.com/srushtik26/investment-news-briefing.git
cd investment-news-briefing

# Create and activate virtual environment
python -m venv .venv

# Windows PowerShell:
.\.venv\Scripts\Activate.ps1

# macOS / Linux:
source .venv/bin/activate
```

### 2. Install Dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Verify Local Installation

Run the complete test suite to verify everything functions properly offline:

```bash
python -m pytest tests -q
```
*(Expected: 1,083 passed)*

### 4. Configuration (Optional for Local Pipeline Execution)

Copy the configuration template:

```bash
cp .env.example .env
```

Configure your API keys in `.env` if testing real external extraction or AI synthesis:
```env
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.6-flash
GMAIL_SENDER=your_email@gmail.com
GMAIL_RECIPIENT=your_recipient@example.com
GMAIL_APP_PASSWORD=your_16_digit_app_password
```

### 5. Execution Commands

- **Run the Core Intelligence Pipeline** (No email dispatch):
  ```bash
  python run_pipeline.py
  ```
- **Run the Daily Automation Workflow** (With delivery guard and email dispatch):
  ```bash
  python run_daily_15.py
  ```
- **Sync Authoritative Briefing to Dashboard**:
  ```bash
  python run_dashboard_sync.py --file data/copy_paste_briefing.txt
  ```
