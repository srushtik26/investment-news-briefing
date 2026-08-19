# Automated Investment Committee News Briefing System

A production-ready Python application designed to generate automated, high-precision daily business news briefings for an Investment Committee and CEO.

## Core Architectural Principle

**Deterministic Python handles rules, validation, and integrity; AI handles understanding, ranking, and synthesis.**

- **Deterministic Python Components**: URL reachability validation, publication date checks, publisher domain deduplication, 3-day lookback story deduplication, single-company constraint enforcement for the India section, two-source corroboration verification, financial fact checking, and WhatsApp-ready formatting.
- **Gemini AI Components**: Article content classification, event extraction, investment relevance scoring, and concise headline/bullet synthesis.

---

## Project Structure

```
Automation/
│
├── app/
│   ├── __init__.py
│   ├── logging_config.py          # Centralized structured & rotating file logging
│   ├── models/                    # Pydantic v2 domain schemas
│   │   ├── __init__.py
│   │   ├── enums.py               # NewsCategory, BriefingStatus, RelevanceGrade
│   │   ├── article.py             # Article data model with URL/date validation
│   │   ├── event.py               # Event data model with financial figures & entity tracking
│   │   ├── verification.py        # SourceVerification model for multi-source checks
│   │   └── briefing.py           # BriefingStory and Briefing models
│   ├── discovery/                 # News discovery (RSS, APIs, search)
│   ├── extraction/                # Full-text content & metadata extraction
│   ├── filtering/                 # Unwanted/clickbait/stale news filtering
│   ├── classification/            # Topic and sector classification
│   ├── verification/              # Multi-source corroboration verification
│   ├── deduplication/             # 3-day lookback & company duplication prevention
│   ├── ranking/                   # Investment committee relevance scoring
│   ├── ai/                        # Gemini API integration
│   ├── validation/                # Deterministic output & fact validation
│   ├── formatting/                # WhatsApp & markdown briefing generation
│   └── database/                  # Storage layer (SQLite / PostgreSQL)
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                # Pytest fixtures and mock objects
│   ├── test_config.py             # Settings & environment validation tests
│   ├── test_logging.py            # Logging initialization tests
│   └── test_models.py             # Data model constraint & serialization tests
│
├── data/                          # Persistent SQLite database & cache storage
├── logs/                          # Rotating log files (app.log)
├── .env.example                   # Environment variable template
├── .gitignore                     # Git tracking exclusions
├── requirements.txt               # Pinned project dependencies
├── config.py                      # Pydantic Settings application configuration
├── main.py                        # Application entry point
└── README.md                      # Documentation
```

---

## Data Models

The following core Pydantic v2 data models have been created in `app/models/`:

1. **`Article`** ([app/models/article.py](file:///c:/Users/SrushtiKadam/OneDrive%20-%20Plutus%20Wealth%20Management%20LLP%20NEW/Desktop/Automation/app/models/article.py)):
   Represents an extracted news article, including source name, URL validation, publication timestamp, content text, summary, and freshness verification flags.

2. **`Event`** ([app/models/event.py](file:///c:/Users/SrushtiKadam/OneDrive%20-%20Plutus%20Wealth%20Management%20LLP%20NEW/Desktop/Automation/app/models/event.py)):
   Synthesizes a distinct business event across multiple articles, tracking involved companies, impacted sectors, financial figures, and deduplication flags.

3. **`SourceVerification`** ([app/models/verification.py](file:///c:/Users/SrushtiKadam/OneDrive%20-%20Plutus%20Wealth%20Management%20LLP%20NEW/Desktop/Automation/app/models/verification.py)):
   Evaluates multi-source corroboration metrics to ensure major stories have at least 2 independent publisher sources.

4. **`BriefingStory`** ([app/models/briefing.py](file:///c:/Users/SrushtiKadam/OneDrive%20-%20Plutus%20Wealth%20Management%20LLP%20NEW/Desktop/Automation/app/models/briefing.py)):
   Represents a curated briefing story with investment impact analysis, bullet points, citations, ranking, and primary company tracking.

5. **`Briefing`** ([app/models/briefing.py](file:///c:/Users/SrushtiKadam/OneDrive%20-%20Plutus%20Wealth%20Management%20LLP%20NEW/Desktop/Automation/app/models/briefing.py)):
   The complete daily briefing issue containing India stories, International stories, company uniqueness checks, issue date, and formatted WhatsApp briefing text.

---

## Installation & Setup

### 1. Prerequisites
- Python 3.11 or higher
- `uv` (recommended) or standard `python -m venv`

### 2. Create and Activate Virtual Environment

Using standard Python:
```bash
python -m venv .venv
# On Windows PowerShell:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate
```

Using `uv`:
```bash
uv venv --python 3.11 .venv
.venv\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
# Or using uv:
uv pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env` and fill in any required variables:
```bash
cp .env.example .env
```

---

## Running Tests

Run the full pytest suite with code coverage:
```bash
pytest -v --cov=app --cov=config
```

Run specific test modules:
```bash
pytest tests/test_models.py -v
pytest tests/test_config.py -v
pytest tests/test_logging.py -v
```

---

## Running the Application

To execute the application foundation check:
```bash
python main.py
```

Console output and rotating log files will be written to `logs/app.log`.
