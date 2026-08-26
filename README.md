# 📰 Automated Investment Committee News Briefing System

> **A production-ready, hybrid AI + Deterministic Python pipeline that delivers zero-hallucination, strictly verified daily financial intelligence (5 India + 5 International) for Investment Committees, Fund Managers, and C-Suite Executives.**

---

## 💡 What is This Project?

In institutional investment and executive decision-making, missing a major market event is costly, but acting on unverified rumors, stale coverage, or clickbait commentary is catastrophic. 

This system automatically scans global and Indian financial media, extracts hard business events (earnings, M&A, regulatory actions, fundraises, IPOs, capex), enforces rigorous **≤24h freshness**, validates events via a **Tier-1 Quality Verification Ladder** (Two-Source Independent Verification or High-Confidence Single-Source Gate $\ge 80/100$), deduplicates across past briefings, and synthesizes a pristine, structured 5+5 executive briefing validated against 20 deterministic rules.

---

## ⚡ Key Highlights & Architecture

### 🧠 The Core Philosophy
**"Deterministic Python handles data integrity, verification gates, and rules; AI handles deep understanding, classification, and structured editorial synthesis."**

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   PIPELINE FLOW                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
  1. Discovery      ► Continuous RSS candidate discovery from Tier-1 India & Global media
  2. Extraction     ► Real-time URL resolution, Trafilatura/BS4 extraction & date validation
  3. Filtering      ► Hard deterministic rejection of commentary, opinion, noise & stale URLs
  4. Pre-Ranking    ► Offline entity & numerical density scoring to prioritize Tier-1 events
  5. AI Classify    ► Gemini 3.6 Flash identifies event types, entities & quantified figures
  6. Verification   ► Dual verification: Two-Source Verification + Single-Source Gate (≥80)
  7. Deduplication  ► 3-day SQLite lookback history & per-company exclusivity rules
  8. Relevance      ► Multi-factor scoring (Financial Scale + Market Impact + Freshness)
  9. Editorial      ► C-Suite structured synthesis (Gemini 3.6 Flash with offline fallback)
 10. 20-Check Audit ► 20-rule deterministic validation engine guarantees flawless output
```

---

## 🎯 Key Engineering Innovations

### 1. 🛡️ Quality Ladder Verification Model
* **Tier 1 — Two-Source Independent Verification**: Pairs matching articles from independent publisher families (excluding syndicated wires and same-parent groups).
* **Tier 2 — High-Confidence Single-Source Gate**: Evaluates single-publisher reports using a strict 100-point deterministic rubric (requires trusted publisher pedigree, recognized hard corporate action, named corporate entity, and quantified metric facts $\ge 80/100$).
* **Zero Rumors & Zero Speculation**: Rejects clickbait, share price commentary, analyst targets, broker advice, and billionaire lifestyle features.

### 2. 🔍 Multi-Pass Active Corroboration & Reserve Pool Expansion
* **Reserve Pool Processing**: Leverages unseen reserve candidates to reach full section sufficiency (5 India + 5 International).
* **Final-Mile RSS Search**: Deploys targeted keyword queries (`when:1d`) across approved publisher clusters when candidate pools need replenishment.
* **SerpAPI Secondary Fallback**: Budget-controlled fallback search for secondary sources when RSS budget is exhausted.
* **Emergency Seed Support**: Seamlessly ingests validated URLs from `submission_seed_india_urls.txt` and `submission_seed_international_urls.txt` without bypassing quality gates.

### 3. ⏱️ Strict 24-Hour Freshness Gate
* All accepted articles and events must have a verified publication timestamp within **$\le 24$ hours** of the pipeline run.
* Explicit timezone normalization (`UTC`) prevents stale stories from entering candidate pools.

### 4. 🌐 Robust Region Provenance & Entity Resolution
* Preserves original discovery region provenance from initial ingest through clustering and ranking.
* Domicile overrides and regex catalogs recognize key Indian conglomerates, startups, regulators (SEBI, RBI, CCI), and exchange reporting norms (`standalone net profit`, `crore`, `₹`).

### 5. 🛑 20-Check Deterministic Final Validation Engine
* Validates every generated briefing against 20 strict criteria before delivery:
  1. Section story counts (strictly 5 India + 5 International).
  2. Headline and publisher authenticity.
  3. Non-article and hub URL rejection (e.g. MarketWatch section fronts).
  4. Publication date freshness ($\le 24$h).
  5. Format and Markdown syntax compliance.

### 6. 🔄 Resilient Offline Fallback Engines
* If Gemini API experiences rate limits (HTTP 429/503), deterministic offline heuristics take over classification and editorial formatting. **Zero candidates are dropped.**

---

## 📁 Repository Structure

```
investment-news-briefing/
├── app/
│   ├── ai/              # Gemini 3.6 Flash editorial curation & structured output
│   ├── classification/  # Hard business event classification (AI + Offline heuristic)
│   ├── database/        # SQLite / SQLAlchemy persistence for historical briefings
│   ├── deduplication/   # Event clustering, entity sanitizer & 3-day SQLite lookback
│   ├── discovery/       # Multi-source RSS providers & query builders
│   ├── extraction/      # URL resolver, HTML parsing, date verification
│   ├── filtering/       # Hard deterministic noise, date freshness, & URL rules
│   ├── formatting/      # Executive WhatsApp & GitHub Markdown formatters
│   ├── logging_config.py# Centralized rotating file logger
│   ├── models/          # Pydantic v2 data models (Article, Event, Briefing)
│   ├── ranking/         # Multi-factor pre-ranker & post-verification relevance scorer
│   ├── validation/      # 20-point deterministic validation engine
│   └── verification/    # TwoSourceVerifier, ActiveCorroborator & SingleSourceEvaluator
├── data/                # SQLite briefings.db & output artifacts
├── logs/                # Application runtime log files
├── tests/               # 450+ comprehensive unit & integration tests
├── .env.example         # Environment variable template
├── config.py            # Type-safe Pydantic Settings application configuration
├── run_pipeline.py      # End-to-end pipeline runner
└── README.md            # Documentation
```

---

## 🛠️ Tech Stack

* **Language**: Python 3.11+
* **AI Model**: Google Gemini 3.6 Flash (`google-genai` / `pydantic`)
* **Data Validation**: Pydantic v2 & Pydantic Settings
* **Extraction & Resolution**: `trafilatura`, `BeautifulSoup4`, `httpx`, `feedparser`
* **Search & Corroboration**: Google News RSS Engine & SerpAPI Fallback
* **Database**: SQLite (via SQLAlchemy)
* **Testing & Quality**: Pytest (**457 passing tests**)
* **Package Manager**: `uv` (Fast Python package installer)

---

## 🚀 Quick Start Guide

### 1. Clone & Setup Environment

```bash
# Clone the repository
git clone https://github.com/srushtik26/investment-news-briefing.git
cd investment-news-briefing

# Create virtual environment and install dependencies using uv
uv venv --python 3.11 .venv
.venv\Scripts\activate

# Install dependencies
uv pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` with your API credentials:

```env
# Gemini AI Key (Required for live AI classification & editorial curation)
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.6-flash

# SerpAPI Key (Optional fallback for secondary source corroboration)
SERPAPI_API_KEY=your_serpapi_key_here
MAX_SERPAPI_SEARCHES_PER_RUN=8
```

---

## 🧪 Running Unit Tests

The test suite contains **457 automated unit and integration tests** covering extraction, filtering, region provenance, single/dual source verification, ranking, and final validation:

```bash
# Run full test suite
uv run pytest -q

# Run with py_compile check
uv run python -m py_compile run_pipeline.py
```

---

## 🏃 Running the Pipeline

Execute a full pipeline run:

```bash
uv run python run_pipeline.py
```

### Pipeline Execution Summary:
```text
=====================================================================
STARTING END-TO-END PIPELINE RUN (India: 5, Intl: 5)
=====================================================================
STAGE 1+2: Discovery → Resolution → Extraction
STAGE 3: Filtering — hard business event deterministic filter engine
STAGE 4: Classification — Gemini AI article classifier (Pre-ranked)
STAGE 5: Two-Source Verification & Single-Source Quality Evaluation
STAGE 6: Reserve Pool & Final-Mile Corroboration Expansion
STAGE 7: Deduplication — 3-day SQLite lookback and company restrictions
STAGE 8: Ranking — deterministic investment relevance scores
STAGE 9: Editorial Curation — Gemini AI Briefing Synthesizer
STAGE 10: Output Audit & 20-Check Deterministic Validation Engine
=====================================================================
BRIEFING GENERATED SUCCESSFULLY: 5 India + 5 International Verified Stories
=====================================================================
```

---

## 📊 Sample Briefing Output Format

```markdown
# 🏛️ INVESTMENT COMMITTEE DAILY BRIEFING
**Date**: August 26, 2026

## 🇮🇳 INDIA BUSINESS BRIEFING

### 1. Welspun Corp Promoter Group to Offload Stake via Rs 1,417 Crore Block Deal
- **Key Facts**: Rs 1,417 Crore Deal Scale | Floor Price Discount | Promoter Divestment
- **Impact**: Enhances public shareholding float while unlocking capital for promoter entities.
- **Sources**: [Business Standard](https://www.business-standard.com/...)

### 2. Honasa Consumer Mutually Calls Off Proposed Fluence Pharma Acquisition
- **Key Facts**: Acquisition Terminated | Zero Break-Fee Impact | Core Brand Focus
- **Impact**: Preserves cash reserves to focus on organic expansion of flagship beauty portfolios.
- **Sources**: [Livemint](https://www.livemint.com/...)

---

## 🌍 INTERNATIONAL BUSINESS BRIEFING

### 1. Nvidia Q2 Revenue Surges 122% YoY to $30.04 Billion on Accelerated AI Demand
- **Key Facts**: $30.04B Revenue (vs $28.7B est) | Data Center up 154% | $50B Buyback Added
- **Impact**: Reaffirms sustained multi-year enterprise capex in AI infrastructure hardware.
- **Sources**: [Reuters](https://www.reuters.com/...) | [CNBC](https://www.cnbc.com/...)

### 2. Broadcom Authorizes $5.0 Billion Common Stock Repurchase Program
- **Key Facts**: $5.0 Billion Authorization | Through FY2027 | Free Cash Flow Deployment
- **Impact**: Signals management confidence in semiconductor operating margins and cash conversion.
- **Sources**: [PR Newswire](https://www.prnewswire.com/...)
```

---

## 🤝 Contact & Contributing

Designed and built for automated financial research, deal flow tracking, and C-suite briefing automation. Feel free to open an issue or submit a pull request!
