# 📰 Automated Investment Committee News Briefing System

> **A production-ready, hybrid AI + Deterministic Python pipeline delivering zero-hallucination, strictly verified daily intelligence across 3 independent sections (5 Domestic India + 5 India Business + 5 International Business = 15 Stories Total) for Investment Committees, Fund Managers, and C-Suite Executives.**

---

## 💡 What is This Project?

In institutional investment and executive decision-making, missing a major market event or national policy shift is costly, but acting on unverified rumors, stale coverage, or clickbait commentary is catastrophic. 

This system automatically scans global, Indian financial, and Indian national media, extracts hard corporate actions and significant national developments, enforces rigorous **freshness evaluation** with clock-drift protection, validates events via a **Tier-1 Quality Verification Ladder** (Two-Source Independent Verification, High-Confidence Single-Source Gate $\ge 80/100$, or Domestic Trending Evaluator $\ge 60/100$), deduplicates across past briefings, and synthesizes a pristine, structured **15-story executive briefing** validated against 20 deterministic gatekeeping rules.

---

## 🏛️ The Three Briefing Sections (Strictly Separated)

| # | Section | Target | Coverage & Criteria |
|---|---|---|---|
| 1 | **DOMESTIC INDIA** | **5 Stories** | General Trending National News: Supreme Court / High Court verdicts, Union Cabinet decisions, Parliament legislation, Defence & national security, Space & ISRO missions, Mega public infrastructure, Disaster & weather alerts, Public health & education. Evaluated via `DomesticTrendingEvaluator` (zero corporate requirement). |
| 2 | **INDIA BUSINESS** | **5 Stories** | Hard Corporate Events: Listed Indian companies, quarterly earnings with figures, M&A / block deals / stake sales, IPO / DRHP filings, Capex, SEBI / RBI / CCI regulatory enforcement, Indian currency signals (₹, crore, lakh). |
| 3 | **INTERNATIONAL BUSINESS** | **5 Stories** | Global Corporate & Macro: Global earnings reports, cross-border M&A, Central banks (Federal Reserve, ECB, BOJ), Big Tech & AI developments, global commodity & supply chain events. |

**Total Final Briefing Output: Exactly 15 Stories (5 + 5 + 5).**

---

## ⚡ Key Highlights & Architecture

### 🧠 The Core Philosophy
**"Deterministic Python handles data integrity, verification gates, and rules; AI handles deep understanding, classification, and structured editorial synthesis."**

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   PIPELINE FLOW                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
  1. Discovery      ► Continuous RSS candidate reserve pool (40 Domestic + 40 India + 40 Intl)
  2. Extraction     ► Real-time URL resolution, Trafilatura/BS4 extraction & date validation
  3. Filtering      ► Hard deterministic rejection of commentary, opinion, noise & stale URLs
  4. Pre-Ranking    ► Entity & numerical density scoring to prioritize Tier-1 events
  5. AI Classify    ► Gemini AI identifies event types, entities & quantified figures
  6. Verification   ► Two-Source Verification + Single-Source Gate (≥80) + Domestic Trending (≥60)
  7. Expansion      ► Automated multi-pass reserve processing with independent section freezing
  8. Deduplication  ► 3-day SQLite lookback history & per-company exclusivity rules
  9. Relevance      ► Multi-factor scoring (Financial Scale + Market Impact + Freshness)
 10. Quality Ladder ► Section-specific quality horizons (24h strict / 36h / 48h / 72h emergency)
 11. Editorial      ► C-Suite structured synthesis (Gemini with deterministic offline fallback)
 12. 20-Check Audit ► 20-rule deterministic validation engine guarantees flawless output
 13. Formatter      ► Executive formatting for Email, WhatsApp, and Markdown delivery
```

---

## 🎯 Key Engineering Innovations

### 1. 🛡️ Quality Ladder Verification Model
* **Tier 1 — Two-Source Independent Verification**: Pairs matching articles from independent publisher families (excluding syndicated wire copy and same-parent media groups).
* **Tier 2 — High-Confidence Single-Source Gate**: Evaluates single-publisher business reports using a strict 100-point deterministic rubric (requires trusted publisher pedigree, recognized hard corporate action, named corporate entity, and quantified metric facts $\ge 80/100$).
* **Tier 3 — Domestic Trending Evaluator**: Assesses national public affairs on a 100-point scale ($\ge 60/100$) filtering out political reaction noise, gossip, and lifestyle features without requiring commercial balance sheet data.
* **Section-Aware Quality Horizons**: Dynamically maps quality levels (`STRICT_SUCCESS`, `FALLBACK_SUCCESS_24H`, `FALLBACK_SUCCESS_36H`, `FALLBACK_SUCCESS_48H`, `EMERGENCY_SUCCESS_72H`) to story freshness windows.

### 2. ⏱️ Immutable Run Reference Clock (Clock-Drift Immunity)
* Captures an immutable `run_reference_time` (`UTC`) at pipeline start.
* Propagates this exact timestamp through all filtering, verification, ranking, and Stage 9 final validation checks, eliminating clock-drift rejections of valid fallback candidates.

### 3. 🔍 Multi-Pass Active Corroboration & Reserve Pool Expansion
* **Reserve Pool Processing**: 120 discovery reserve candidates (40 Domestic, 40 India, 40 International) processed incrementally until all 3 sections achieve 5/5 sufficiency.
* **Independent Section Freezing**: Once a section hits 5 quality candidates, it is frozen to preserve search budgets.
* **Final-Mile RSS Search**: Deploys targeted keyword queries (`when:1d`) across approved publisher clusters.
* **SerpAPI Production Cap**: Strictly capped at **8 searches per run** (`MAX_SERPAPI_SEARCHES_PER_RUN = 8`).

### 4. 🌐 Robust Region Provenance & Entity Resolution
* Strict separation between Domestic and India Business news.
* Regex entity sanitization strips metric noise (`FY26`, `₹84.5 crore`, `Q3`) and handles corporate names cleanly.
* Global entity recognition routes international brands and media firms (e.g. DAZN, ESPN, Disney, Boeing) to International Business.

### 5. 🛑 20-Check Deterministic Final Validation Engine
* Validates every generated briefing against 20 strict programmatic checks before delivery:
  1. Section story counts (strictly 5 Domestic + 5 India + 5 International).
  2. Headline and publisher authenticity.
  3. Non-article and hub URL rejection.
  4. Publication date freshness against section-specific horizon.
  5. Format and Markdown syntax compliance.
  6. No cross-section duplicate events or same-company repeats in India section.

### 6. 🔄 Resilient Offline Fallback Engines
* If Gemini API experiences rate limits (HTTP 429 / 503), deterministic offline heuristics take over classification and editorial formatting across all 15 stories. **Zero candidates are dropped.**

---

## 📁 Repository Structure

```
investment-news-briefing/
├── app/
│   ├── ai/              # Gemini editorial curation & deterministic offline fallback
│   ├── classification/  # Hard business event classification & EventRegionClassifier
│   ├── database/        # SQLite / SQLAlchemy persistence for historical briefings
│   ├── deduplication/   # Event clustering, entity sanitizer & 3-day SQLite lookback
│   ├── discovery/       # Multi-source RSS providers, reserve pools & query builders
│   ├── extraction/      # URL resolver, HTML parsing, date verification
│   ├── filtering/       # Hard deterministic noise, date freshness, & URL rules
│   ├── formatting/      # Executive WhatsApp, Email, & Markdown formatters
│   ├── logging_config.py# Centralized rotating file logger
│   ├── models/          # Pydantic v2 data models (Article, Event, Briefing)
│   ├── ranking/         # Multi-factor pre-ranker & post-verification relevance scorer
│   ├── validation/      # 20-point deterministic validation engine
│   └── verification/    # TwoSourceVerifier, DomesticTrendingEvaluator & SingleSourceEvaluator
├── data/                # SQLite briefings.db & output artifacts
├── logs/                # Application runtime log files
├── tests/               # 485+ comprehensive unit & integration tests
├── .env.example         # Environment variable template
├── config.py            # Type-safe Pydantic Settings application configuration
├── run_pipeline.py      # End-to-end pipeline runner
└── README.md            # Documentation
```

---

## 🛠️ Tech Stack

* **Language**: Python 3.11+
* **AI Model**: Google Gemini (`google-genai` / `pydantic`)
* **Data Validation**: Pydantic v2 & Pydantic Settings
* **Extraction & Resolution**: `trafilatura`, `BeautifulSoup4`, `httpx`, `feedparser`
* **Search & Corroboration**: Google News RSS Engine & SerpAPI Fallback
* **Database**: SQLite (via SQLAlchemy)
* **Testing & Quality**: Pytest (**487 passing tests**)
* **Package Manager**: `uv` / `pip`

---

## 🚀 Quick Start Guide

### 1. Clone & Setup Environment

```bash
# Clone the repository
git clone https://github.com/srushtik26/investment-news-briefing.git
cd investment-news-briefing

# Create virtual environment and install dependencies
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
GEMINI_MODEL=gemini-2.5-flash

# SerpAPI Key (Optional fallback for secondary source corroboration)
SERPAPI_API_KEY=your_serpapi_key_here
MAX_SERPAPI_SEARCHES_PER_RUN=8
```

---

## 🧪 Running Unit Tests

The test suite contains **487 automated unit and integration tests** covering extraction, filtering, region provenance, single/dual source verification, ranking, domestic trending evaluation, and final validation:

```bash
# Run full test suite
python -m pytest -q

# Run with py_compile check
python -m py_compile run_pipeline.py
```

---

## 🏃 Running the Pipeline

Execute a full pipeline run:

```bash
python run_pipeline.py
```

### Pipeline Execution Summary:
```text
=====================================================================
STARTING END-TO-END PIPELINE RUN (Domestic: 40, India: 40, Intl: 40)
=====================================================================
STAGE 1+2: Discovery Reserve Pool → Resolution → Extraction
STAGE 3: Filtering — deterministic filter engine (Domestic + Business)
STAGE 4: Classification — Gemini AI article classifier (Pre-ranked)
STAGE 5: Verification — Two-Source + Quality Evaluator (Domestic + Business)
STAGE 6: Deduplication — 3-day SQLite lookback and cross-section deduplication
STAGE 7: Ranking — deterministic relevance scores across 3 sections
STAGE 8: Gemini Editorial — final editorial curation across 3 sections
STAGE 9: Final Validation — 20-check deterministic gatekeeper
STAGE 10: Formatter — generating final briefing text
=====================================================================
BRIEFING GENERATED SUCCESSFULLY: 5 Domestic + 5 India + 5 International Verified Stories (15 Total)
=====================================================================
```

---

## 📊 Sample Briefing Output Format

```markdown
# 🏛️ INVESTMENT COMMITTEE DAILY BRIEFING
**Date**: August 27, 2026

## 🏛️ DOMESTIC INDIA HEADLINES

### 1. Supreme Court Directs States to Form SITs to Investigate Fraudulent Insurance Claims
- **Source**: The Hindu
- **URL**: https://www.thehindu.com/news/national/...

### 2. Union Cabinet Approves Mega Railway Corridor Project Connecting Three States
- **Source**: Hindustan Times
- **URL**: https://www.hindustantimes.com/india-news/...

---

## 🇮🇳 INDIA BUSINESS HEADLINES

### 1. Welspun Corp Promoter Group to Offload Stake via Rs 1,417 Crore Block Deal
- **Source**: Business Standard
- **URL**: https://www.business-standard.com/markets/...

### 2. Honasa Consumer Mutually Calls Off Proposed Fluence Pharma Acquisition
- **Source**: Livemint
- **URL**: https://www.livemint.com/companies/...

---

## 🌍 INTERNATIONAL BUSINESS HEADLINES

### 1. Nvidia Q2 Revenue Surges 122% YoY to $30.04 Billion on Accelerated AI Demand
- **Source**: Reuters
- **URL**: https://www.reuters.com/technology/...

### 2. Broadcom Authorizes $5.0 Billion Common Stock Repurchase Program
- **Source**: PR Newswire
- **URL**: https://www.prnewswire.com/news-releases/...
```

---

## 🤝 Contact & Contributing

Designed and built for automated financial research, deal flow tracking, and C-suite briefing automation. Feel free to open an issue or submit a pull request!

