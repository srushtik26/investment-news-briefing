# 📰 Automated Investment Committee News Briefing System

> **A production-ready, hybrid AI + Deterministic Python pipeline delivering zero-hallucination, strictly verified daily intelligence across 3 independent sections (5 India Business + 5 Domestic + 5 International Business = 15 Stories Total) with one-line factual summaries and shortened URLs for Investment Committees, Fund Managers, and C-Suite Executives.**

---

## 💡 What is This Project?

In institutional investment and executive decision-making, missing a major market event or national policy shift is costly, but acting on unverified rumors, stale coverage, or clickbait commentary is catastrophic. 

This system automatically scans global, Indian financial, and Indian national media, extracts hard corporate actions and significant national developments, enforces rigorous **freshness evaluation** with clock-drift protection, validates events via a **Tier-1 Quality Verification Ladder** (Two-Source Independent Verification, High-Confidence Single-Source Gate $\ge 80/100$, or Domestic Trending Evaluator $\ge 60/100$), deduplicates across past briefings, generates concise **one-line factual summaries**, shortens display URLs, and synthesizes a pristine, structured **15-story executive briefing** validated against 20 deterministic gatekeeping rules.

---

## 🏛️ The Three Briefing Sections (Strictly Ordered & Separated)

The briefing delivers exactly 15 verified stories in canonical presentation order:

| # | Section | Target | Coverage & Criteria |
|---|---|---|---|
| 1 | **INDIA BUSINESS** | **5 Stories** | Hard Corporate Events: Listed Indian companies, quarterly earnings with verified figures, M&A / block deals / stake sales, IPO / DRHP filings, Capex, SEBI / RBI / CCI regulatory enforcement, Indian currency signals (₹, crore, lakh). |
| 2 | **DOMESTIC** | **5 Stories** | General Trending National News: Supreme Court / High Court verdicts, Union Cabinet decisions, Parliament legislation, Defence & national security, Space & ISRO missions, Strategic national infrastructure, Disaster & weather alerts, Public health & education. Evaluated via `DomesticTrendingEvaluator` (filters local municipal trivia). |
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
  3. Filtering      ► Hard deterministic rejection of commentary, opinion, noise, stale dates & rumors
  4. Pre-Ranking    ► Entity & numerical density scoring to prioritize Tier-1 events
  5. AI Classify    ► Gemini AI identifies event types, entities & quantified figures
  6. Verification   ► Two-Source Verification + Single-Source Gate (≥80) + Domestic Trending (≥60)
  7. Expansion      ► Automated multi-pass reserve processing with independent section freezing
  8. Deduplication  ► 3-day SQLite lookback history & per-company exclusivity rules
  9. Relevance      ► Multi-factor scoring (Financial Scale + Market Impact + Freshness)
 10. Quality Ladder ► Section-specific quality horizons (24h strict / 36h / 48h / 72h emergency)
 11. Editorial      ► Factual 1-line summary synthesis (Gemini with deterministic offline fallback)
 12. 20-Check Audit ► 20-rule deterministic validation engine guarantees flawless output
 13. URL Shortening ► Async TinyURL display shortening with fallback to direct canonical URLs
 14. Formatter      ► Executive formatting for Email, WhatsApp, and Markdown delivery
```

---

## 🎯 Key Engineering Innovations

### 1. 🛡️ Quality Ladder Verification Model
* **Tier 1 — Two-Source Independent Verification**: Pairs matching articles from independent publisher families (excluding syndicated wire copy and same-parent media groups).
* **Tier 2 — High-Confidence Single-Source Gate**: Evaluates single-publisher business reports using a strict 100-point deterministic rubric (requires trusted publisher pedigree, recognized hard corporate action, named corporate entity, and quantified metric facts $\ge 80/100$).
* **Tier 3 — Domestic Trending Evaluator**: Assesses national public affairs on a 100-point scale ($\ge 60/100$) filtering out political reaction noise, gossip, and local municipal civic updates without requiring commercial balance sheet data.
* **Section-Aware Quality Horizons**: Dynamically maps quality levels (`STRICT_SUCCESS`, `FALLBACK_SUCCESS_24H`, `FALLBACK_SUCCESS_36H`, `FALLBACK_SUCCESS_48H`, `EMERGENCY_SUCCESS_72H`) to story freshness windows.

### 2. 📝 Complete One-Line Factual Summaries
* Every story includes a single-line factual summary ($\le 30$ words) capturing the core transaction or event details with exact figures.
* **Grammatical Completeness Guarantee**: Uses sentence boundary normalization (`word.CapitalizedWord` $\to$ `word. CapitalizedWord`) and an `INCOMPLETE_ENDINGS` guard to eliminate cut-off fragments or dangling prepositions/conjunctions.
* **Zero Quota Dependency**: High-precision deterministic fallback engine generates complete summaries with zero extra Gemini API calls.

### 3. 🔗 Short Display URLs (TinyURL Integration)
* Display URLs in the final briefing are shortened via TinyURL (e.g. `https://tinyurl.com/xxxxx`) for sleek executive presentation across mobile and email.
* Full canonical direct URLs are preserved internally throughout verification, deduplication, and SQLite history tracking.

### 4. 🛑 20-Check Deterministic Final Validation Engine
* Validates every generated briefing against 20 strict programmatic checks before delivery:
  1. Section story counts (strictly 5 India Business + 5 Domestic + 5 International Business).
  2. Section presentation ordering (India Business $\to$ Domestic $\to$ International Business).
  3. Headline and publisher authenticity.
  4. Non-article and hub URL rejection.
  5. Publication date freshness against section-specific horizon.
  6. No cross-section duplicate events or same-company repeats in India section.
  7. Summary validation (single line, $\le 30$ words, no markdown bullets, no URLs, no mojibake, grammatically complete ending).

### 5. 🔍 Multi-Pass Active Corroboration & Reserve Pool Expansion
* **Reserve Pool Processing**: 120 discovery reserve candidates (40 Domestic, 40 India, 40 International) processed incrementally until all 3 sections achieve 5/5 sufficiency.
* **Independent Section Freezing**: Once a section hits 5 quality candidates, it is frozen to preserve search budgets.
* **SerpAPI Production Cap**: Strictly capped at **8 searches per run** (`MAX_SERPAPI_SEARCHES_PER_RUN = 8`).

### 6. 🌐 Robust Region Provenance & Content Quality Filtering
* **Stale Explicit Date Filtering**: Extracts and verifies structured dates in titles/tables (e.g. `18 Feb 2019`), rejecting stale quarterly reports.
* **Speculative Deal Talks Gate**: Rejects prospective discussions (`in talks to buy`, `eyeing stake`, `mulls acquisition`) while preserving verified completed block deals and signed agreements.
* **Local Municipal vs National Scope**: Distinguishes local civic works from national public affairs.

---

## 📁 Repository Structure

```
investment-news-briefing/
├── app/
│   ├── ai/              # Gemini editorial curation & deterministic summary engine
│   ├── classification/  # Hard business event classification & EventRegionClassifier
│   ├── database/        # SQLite / SQLAlchemy persistence for historical briefings
│   ├── deduplication/   # Event clustering, entity sanitizer & 3-day SQLite lookback
│   ├── discovery/       # Multi-source RSS providers, reserve pools & query builders
│   ├── extraction/      # URL resolver, HTML parsing, date verification
│   ├── filtering/       # Hard deterministic noise, date freshness, & URL rules
│   ├── formatting/      # Executive WhatsApp, Email, & Markdown formatters with TinyURL
│   ├── logging_config.py# Centralized rotating file logger
│   ├── models/          # Pydantic v2 data models (Article, Event, Briefing)
│   ├── ranking/         # Multi-factor pre-ranker & post-verification relevance scorer
│   ├── validation/      # 20-point deterministic validation engine
│   └── verification/    # TwoSourceVerifier, DomesticTrendingEvaluator & SingleSourceEvaluator
├── data/                # SQLite briefings.db & output artifacts
├── logs/                # Application runtime log files
├── tests/               # 560 comprehensive unit & integration tests
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
* **Testing & Quality**: Pytest (**560 passing tests**)
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

The test suite contains **560 automated unit and integration tests** covering extraction, filtering, region provenance, single/dual source verification, ranking, domestic trending evaluation, summary completeness, and 20-check validation:

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
STAGE 10: Formatter — generating final briefing text with TinyURLs
=====================================================================
BRIEFING GENERATED SUCCESSFULLY: 5 India + 5 Domestic + 5 International Verified Stories (15 Total)
=====================================================================
```

---

## 📊 Sample Briefing Output Format

```text
🏛️ INVESTMENT COMMITTEE DAILY BRIEFING
Date: August 28, 2026

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🇮🇳 INDIA BUSINESS HEADLINES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Ather Energy shares jump 5% after ₹1,758 crore block deal
   Summary: Shares of Ather Energy surged over 5 percent following a ₹1,758 crore block deal on Thursday.
   Source: Moneycontrol
   URL: https://tinyurl.com/3h8f8j9k

2. boAt FY26 net profit rises 38% to ₹84.5 crore, wearables turn profitable
   Summary: Audio and wearables brand boAt reported a 38 percent increase in annual net profit to ₹84.5 crore.
   Source: The Economic Times
   URL: https://tinyurl.com/2p9x4m7b

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏛️ DOMESTIC HEADLINES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Supreme Court orders Centre to respond within two weeks on national policy
   Summary: The Supreme Court directed the central government to file a detailed status affidavit within two weeks.
   Source: The Hindu
   URL: https://tinyurl.com/5n7v2x8a

2. Orissa HC issues contempt notice to retired DGP over SI's reinstatement
   Summary: The High Court issued a contempt notice to the retired DGP regarding the compliance order for a sub-inspector.
   Source: The Hindu
   URL: https://tinyurl.com/4b8c9d2f

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌍 INTERNATIONAL BUSINESS HEADLINES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Nvidia agrees to buy AI software firm in $1.2 billion all-cash deal
   Summary: Nvidia signed a definitive agreement to acquire the AI software developer for $1.2 billion in cash.
   Source: CNBC
   URL: https://tinyurl.com/y8k3m9pw

2. National Bank of Canada quarterly earnings rise on back of broad growth
   Summary: National Bank of Canada reported higher third-quarter net income driven by strength across all operating divisions.
   Source: Reuters
   URL: https://tinyurl.com/7v9x2m4k
```

---

## 🤝 Contact & Contributing

Designed and built for automated financial research, deal flow tracking, and C-suite briefing automation. Feel free to open an issue or submit a pull request!
