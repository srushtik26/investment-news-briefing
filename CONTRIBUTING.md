# Contributing Guidelines

Thank you for your interest in contributing to the **Investment News Briefing Pipeline**! Contributions that improve pipeline robustness, extraction coverage, test coverage, and documentation are welcome.

---

## Development Workflow

### 1. Fork and Clone
Fork the repository on GitHub and clone your fork locally:
```bash
git clone https://github.com/<your-username>/investment-news-briefing.git
cd investment-news-briefing
```

### 2. Set Up a Virtual Environment
Create and activate an isolated Python 3.11+ virtual environment:
```bash
python -m venv venv

# On Linux/macOS:
source venv/bin/activate

# On Windows (PowerShell):
.\venv\Scripts\Activate.ps1
```

### 3. Install Dependencies
Install all pinned project dependencies:
```bash
pip install -r requirements.txt
```

### 4. Create a Feature or Fix Branch
Always create a dedicated, descriptive branch based off `main`:
```bash
git checkout -b fix/article-date-parsing
# or
git checkout -b feat/reuters-source-adapter
```

### 5. Make Focused Changes
* Keep changes modular and focused on a single issue or improvement.
* **Preserve Invariants**: Do not bypass or weaken deterministic validation checks (e.g. 5/5/5 contract, numeric token grounding, semantic overlap, source corroboration).
* **Maintain Fail-Safe Behavior**: If a failure or edge case occurs, the system must fail safe rather than emitting degraded or ungrounded data.
* **Local Development without Paid APIs**: All core logic, scrapers, deduplication, scoring, and fallback headline synthesizers can be tested offline using fixtures and mocked responses.

### 6. Run the Test Suite
Before committing, ensure that the entire test suite passes cleanly:
```bash
python -m pytest
```
If you add new functionality, please add corresponding unit or integration tests under `tests/`.

### 7. Avoid Committing Sensitive Data
* Never commit `.env` files, API keys, email credentials, or app passwords.
* Never commit local runtime artifacts, temporary databases, test logs, or generated briefing HTML files.
* Ensure all files follow the existing `.gitignore`.

### 8. Submit a Pull Request
Commit your changes with clear, concise commit messages, push your branch to your fork, and open a Pull Request against `main`.

In your Pull Request description, clearly articulate:
* **The Problem**: What bug are you resolving or what capability are you introducing?
* **The Solution**: How did you implement the change?
* **Tests Performed**: Which test commands were run and what new test cases were added?

---

## Areas for Contribution

We actively welcome contributions in the following areas:

* **Source Adapters**: Adding or refining clean ingestion adapters for credible global and regional business publishers.
* **Extraction Reliability**: Improving HTML boilerplate removal, date parsing, paywall detection, and canonical URL resolution across various publisher structures.
* **Verification Logic**: Enhancing domain-matching heuristics, ticker detection, and corroboration scoring algorithms.
* **Regression Tests**: Adding edge-case fixtures and test cases for unusual date formats, malformed HTML, network timeout recovery, and duplicate detection.
* **Observability & Logging**: Improving structured telemetry, metric collection, and pipeline diagnostics without logging sensitive information.
* **Dashboard Accessibility**: Improving keyboard navigation, ARIA semantics, color contrast, and responsive styling on the monitoring dashboard.
* **Documentation**: Clarifying architecture guides, docstrings, schema contracts, and setup instructions.
* **Security Hardening**: Enhancing input sanitization, safe HTTP header defaults, and rate-limiting safeguards.
* **Performance**: Optimizing parsing speeds, memory usage during extraction, and deduplication efficiency.

---

## Core Tenet: Preserving Quality Contracts

This repository maintains strict data-quality and grounded-intelligence standards. Pull requests that weaken validation thresholds, disable check gates, or allow ungrounded AI hallucinations will not be accepted. All changes must respect the deterministic boundary that guarantees safe, verified output.
