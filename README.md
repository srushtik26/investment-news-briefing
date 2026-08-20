# 📰 Automated Investment Committee News Briefing System

> **A production-ready, hybrid AI + Deterministic Python pipeline that delivers zero-hallucination, double-verified daily financial intelligence for Investment Committees, Fund Managers, and C-Suite Executives.**

---

## 💡 What is This Project?

In executive decision-making, missing a major market event is risky, but acting on unverified rumors or getting bogged down by news noise is equally dangerous. 

This system automatically scans global and Indian financial media, extracts hard business events (earnings, M&A, regulatory actions, fundraises, IPOs), **independently verifies every story across two separate news publishers**, deduplicates against past briefings, and delivers a pristine, executive-formatted briefing.

---

## ⚡ Key Highlights & Architecture

### 🧠 The Core Philosophy
**"Deterministic Python handles integrity, rules, and verification; AI handles deep understanding, classification, and synthesis."**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             PIPELINE FLOW                                   │
└─────────────────────────────────────────────────────────────────────────────┘
  1. Discovery     ► Fetch candidates from 20+ Tier-1 India & Global publishers
  2. Extraction    ► Resolve Google News URLs & extract clean full text
  3. Filtering     ► Rejects clickbait, market commentary, analyst opinions & noise
  4. Pre-Ranking   ► Deterministic scoring to send strongest stories to AI first
  5. AI Classify   ► Gemini 3.6 Flash identifies hard business events & entities
  6. Verification  ► Enforces 2 independent publishers (RSS + SerpAPI fallback)
  7. Deduplication ► 3-day SQLite lookback & company uniqueness rules
  8. Relevance     ► Multi-factor scoring (Freshness + Scale + Financial impact)
  9. Curation      ► C-Suite executive synthesis & automated 10-rule validation
```

---

## 🎯 Key Engineering Innovations

### 1. 🛡️ Two-Source Independent Verification Gate
* **Zero Rumors**: No story enters the briefing based on a single news report.
* **Smart Publisher Grouping**: Ensures Reuters and Bloomberg count as distinct, but two articles from the same media group (e.g., Livemint and Economic Times) do not bypass verification.
* **Wire Release Detection**: Rejects syndicated wire duplicates (PTI, ANI, Reuters wire feeds) to guarantee true independent reporting.

### 2. 🔍 Active Corroboration Engine (RSS + SerpAPI Fallback)
* When a high-impact single-source story is discovered, the pipeline actively launches targeted search queries for a second independent publisher.
* **Multi-Tiered Budgeting**: Primary search runs on Google News RSS; if missed, an optional budget-controlled **SerpAPI fallback engine** executes targeted searches without exceeding strict free-tier quotas.

### 3. ⚖️ Deterministic Pre-Classification Ranking
* Before invoking AI models, an offline scoring engine evaluates articles based on financial figures (`₹`/`$` crore/billion), hard event keywords, and publisher pedigree.
* Balances candidate pools (e.g., 8 India + 7 International) to ensure AI classification budgets are spent on the highest-value stories.

### 4. 🛑 Zero-Hallucination Sufficiency Gate
* Requires exactly **5 Verified India + 5 Verified International** stories.
* If verification yields fewer stories, the pipeline **skips AI summary generation** rather than fabricating fallbacks or padding the briefing with unverified noise.

### 5. 🔄 Graceful API Rate-Limit Fallback
* Built for production reliability: if Gemini returns a 429/503 error after retry, the pipeline seamlessly transitions remaining candidates to an offline heuristic classifier. **Zero candidate articles are dropped.**

---

## 📁 Repository Structure

```
Automation/
├── app/
│   ├── ai/              # Gemini 3.6 Flash editorial curation & structured output
│   ├── classification/  # Hard business event classification (AI + Offline heuristic)
│   ├── database/        # SQLite / PostgreSQL persistence for historical briefings
│   ├── deduplication/   # Fingerprint hashing & 3-day SQLite lookback history
│   ├── discovery/       # Multi-source RSS providers & query builders
│   ├── extraction/      # URL resolver, HTML parsing, date verification
│   ├── filtering/       # Hard deterministic noise, date freshness, & URL rules
│   ├── formatting/      # Executive WhatsApp & GitHub Markdown formatters
│   ├── logging_config.py# Centralized rotating file logger
│   ├── models/          # Pydantic v2 data models (Article, Event, Briefing)
│   ├── ranking/         # Multi-factor pre-ranker & post-verification relevance scorer
│   ├── validation/      # 10-point deterministic validation engine
│   └── verification/    # TwoSourceVerifier, ActiveCorroborator & SerpAPICorroborator
├── data/                # SQLite briefings.db & execution log outputs
├── logs/                # Application runtime log files
├── tests/               # 250+ comprehensive unit & integration tests
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
* **Search & Corroboration**: Google News RSS Engine & SerpAPI Fallback
* **Database**: SQLite (via SQLAlchemy)
* **Testing & Quality**: Pytest (258 passing tests)
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

The codebase includes **258 automated tests** covering models, filtering rules, deduplication fingerprints, verification logic, and API rate-limit fallbacks:

```bash
# Run full test suite
uv run pytest
```

---

## 🏃 Running the Pipeline

Execute a full pipeline run:

```bash
uv run python run_pipeline.py
```

### Expected Output Summary:
```text
=====================================================================
STARTING END-TO-END PIPELINE RUN (India: 5, Intl: 5)
=====================================================================
STAGE 1+2: Discovery → Resolution → Extraction
STAGE 3: Filtering — hard business event deterministic filter engine
STAGE 4: Classification — Gemini AI article classifier (Pre-ranked)
STAGE 5: Two-Source Verification & Active Corroboration
STAGE 6: Deduplication — 3-day SQLite lookback and company restrictions
STAGE 7: Ranking — deterministic investment relevance scores
STAGE 8: Editorial Curation — Gemini AI Briefing Synthesizer
STAGE 9: Output Audit & Deterministic Validation Engine
=====================================================================
BRIEFING GENERATED SUCCESSFULLY: 5 India + 5 International Verified Stories
=====================================================================
```

---

## 📊 Sample Briefing Output Format

```markdown
# 🏛️ INVESTMENT COMMITTEE DAILY BRIEFING
**Date**: August 20, 2026

## 🇮🇳 INDIA BUSINESS BRIEFING

### 1. HDFC Bank Q1 Net Profit Surges 18% YoY to ₹16,175 Crore
- **Key Facts**: ₹16,175 Cr Net Profit | 18% YoY Growth | NII up 21%
- **Impact**: Demonstrates resilient net interest margins despite tight liquidity conditions.
- **Sources**: [Business Standard](https://...) | [The Economic Times](https://...)

---

## 🌍 INTERNATIONAL BUSINESS BRIEFING

### 1. Rio Tinto Agrees $6.7 Billion All-Cash Acquisition of Arcadium Lithium
- **Key Facts**: $6.7 Billion Deal | 90% Premium | Energy Transition Expansion
- **Impact**: Establishes Rio Tinto as a top-tier global lithium producer.
- **Sources**: [Reuters](https://...) | [CNBC](https://...)
```

---

## 🤝 Contact & Contributing

Designed and built for automated financial research and C-suite briefing automation. Feel free to open an issue or submit a pull request!
