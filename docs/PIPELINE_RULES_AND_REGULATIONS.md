# Institutional News Briefing Pipeline: Rules, Regulations & Architecture Guide

**Document Version:** 2.0.0  
**Effective Date:** September 30, 2026  
**Audience:** Investment Committee, Engineering, Reliability & Data Teams  
**Target Output:** 15-Story Institutional Daily Executive Briefing (Email + Dashboard Sync)

---

## 1. Executive Mission & Overview

The Automated Investment News Briefing Pipeline ingests, filters, corroborates, ranks, synthesizes, validates, and delivers an executive-grade 15-story financial and macro intelligence briefing every business morning prior to market open.

The output is segmented into three distinct sections of exactly 5 stories each:
1. **Domestic Trending News (5 stories):** National macroeconomy, Cabinet decisions, landmark judiciary, defense, infrastructure, space/science.
2. **India Corporate & Business (5 stories):** Material private and public sector corporate developments, earnings, M&A, capital expenditure, and portfolio watchlist actions.
3. **International Business & Geopolitics (5 stories):** Global cross-border economic shifts, central bank policies, multinational corporate finance, and market-moving geopolitical developments.

Every single story delivered to leadership must be grounded in verified, real-world factual reporting with complete mathematical and sourcing integrity.

---

## 2. Section Specifications & Structural Invariants

### 2.1 Domestic Trending News (Section 1)
- **Target Count:** Exactly 5 stories.
- **Geographic Nexus:** Strictly Indian domestic topics (Parliament, Supreme Court/High Courts, Union Cabinet, ministries, regulators like RBI/SEBI, ISRO, national security). Foreign events without domestic Indian implications are prohibited.
- **Judiciary / Court Story Limit:** Maximum **1 story** from `COURT_JUDICIARY` (unless major landmark constitutional bench). Routine bail hearings, magistrate disputes, and local crime are completely rejected.
- **Topic Diversity Requirement:** At least 3 distinct domestic topic categories across the 5 stories (e.g., Policy, Infrastructure, Science/Tech, Judiciary, National Security).

### 2.2 India Corporate & Business (Section 2)
- **Target Count:** Exactly 5 stories.
- **Materiality Threshold:** Must represent genuine strategic corporate actions:
  - Quarterly / Annual earnings with verified financial figures.
  - Mergers, acquisitions, demergers, and joint ventures.
  - Capital expenditures, plant commissioning, and capacity expansion.
  - Fund-raising (QIP, rights issues, private equity, debt issuances).
  - Material commercial contracts and EPC order wins.
- **Company Uniqueness Constraint:** **Strictly one story per corporate entity.** No company may appear twice in the India section under any circumstance.
- **Portfolio Watchlist Priority:** Qualified corporate actions involving companies on the internal portfolio watchlist are prioritized in candidate pools.

### 2.3 International Business & Geopolitical (Section 3)
- **Target Count:** Exactly 5 stories.
- **Scope:** Global corporate finance, technological leadership, energy transitions, global trade policy, and macroeconomic trends.
- **Company Uniqueness Constraint:** **Strictly one story per corporate entity.** No international company (e.g., Anthropic, Nvidia, Microsoft) may appear twice in the International section.
- **Quantified Market Impact Gate:** Geopolitical news (conflicts, trade restrictions, treaties) must include quantifiable economic consequences (e.g., oil price movements, trade volumes, currency volatility, supply chain disruptions). Speculative commentary or political opinions are barred.

---

## 3. Verification & Sourcing Integrity

### 3.1 Verification Tiers
1. **Two-Source Verified (`TWO_SOURCE_VERIFIED`):** The story is independently reported by at least two distinct, reputable publishing domains within the allowable freshness window.
2. **High-Confidence Single-Source (`HIGH_CONFIDENCE_SINGLE_SOURCE`):** A story from an approved tier-1 financial authority (e.g., Reuters, Bloomberg, Exchange regulatory filings, BSE/NSE corporate announcements) with verified primary corporate quotes, full attribution, and quantified figures.

### 3.2 Corroboration Ratios
- Under normal operating conditions, each section must contain:
  - **Minimum 3** two-source verified stories.
  - **Maximum 2** high-confidence single-source stories.
- Under weekend/holiday recovery mode ("Quality Ladder"), thresholds adjust dynamically based on market volume while maintaining source provenance.

---

## 4. Deduplication & History Protection Rules

### 4.1 3-Day History Lookback
- **Rule:** No event that appeared in an official briefing within the previous **3 business days** may be included in today's briefing.
- **Canonical Fingerprint Formula:**
  $$\text{Fingerprint} = \text{MD5}(\text{Normalized Company} \parallel \text{Event Type} \parallel \text{Key Numbers/Facts})$$
- Stories matching prior hashes or identical underlying events reported by alternative outlets are automatically discarded during Stage 6.

### 4.2 Intra-Day Candidate & Section Deduplication
- **India Section:** Rejects any story whose normalized corporate entity matches an already selected story in today's India section.
- **International Section:** Rejects any story whose normalized corporate entity matches an already selected story in today's International section.
- **Pipeline Stage Sync:** This rule is enforced at three sequential levels:
  1. Candidate selection (`app/pipeline/selection.py`)
  2. Candidate pool ranking (`app/ranking/sorter.py`)
  3. AI editorial synthesis & fallback (`app/ai/editor.py`)
  4. Final Gatekeeping Check 10 (`app/validation/engine.py`)

---

## 5. SerpAPI Budget Management & Quota Preservation

To protect API budgets and adhere to strict operational limits, the following policies are mandatory:

### 5.1 Quota & Query Caps
- **Batching & Caching:** All search queries must check the local discovery cache and URL history before executing any live SerpAPI request.
- **Zero Unbounded Queries:** Search loops are strictly bound by configured query caps per run (maximum 8–12 targeted queries per section during normal execution).
- **Corroboration Throttling:** Multi-source verification searches are only fired for candidates that have already cleared date, domain, and keyword relevance filters.
- **No Secondary Refill on Clean Runs:** If candidate quotas are fulfilled, secondary discovery refill routines are completely disabled.

### 5.2 Unit & Integration Testing Constraint
- **Zero Live Calls During Tests:** Live SerpAPI calls must **NEVER** be made during automated unit tests, integration tests, or CI/CD workflow runs.
- All test fixtures must utilize mock HTTP responses (`MockSerpAPIClient`, recorded fixtures, or offline responders).

### 5.3 Zero-News Early Exit
- When running over a weekend or holiday with no active trading or material corporate disclosures, if zero stories pass Stage 4 filtering, the pipeline must **NOT** fire additional exploratory search queries or hallucinate filler stories.
- It must log `"No News Found"`, write a sentinel record (`tmp/no_news_found.txt`), and exit cleanly with code `0`.

---

## 6. Editorial Standards & AI Synthesis

### 6.1 Institutional Two-Clause Headlines
- **Format:** `[Entity/Event + key quantified action]; [verified strategic/business implication]`
- **Target Word Count:** 18–32 words.
- **Core Elements:**
  1. Primary corporate entity or governing institution.
  2. Verified numeric metric (transaction value, revenue percentage, capacity, valuation).
  3. Immediate operational outcome.
  4. Institutional market or strategic implication grounded in article facts.
- **Prohibitions:**
  - No sensationalism, clickbait, or speculative buzzwords ("skyrockets", "bloodbath").
  - No investment advice ("strong buy", "sell signal").
  - No ungrounded strategic speculation.

### 6.2 Descriptive Investment Committee Summaries
- **Target Word Count:** 35–55 words (strict ceiling: 65 words).
- **Three-Part Institutional Answer:**
  1. *What happened?* (Transaction, regulatory ruling, capex expansion, earnings).
  2. *What is the scale/magnitude?* (Deal value, stake %, capacity, YoY growth).
  3. *Why does it matter?* (Strategic positioning, market share, operational scaling).
- **Figure Sanitization Regulations:**
  - Bare currency symbols (`"rs"`, `"$"`, `"₹"`) without numeric values are strictly prohibited.
  - Bare unitless numbers below 10 (e.g., `"0.6"`, `"1"`) are stripped from figure lists.
  - Spacing around currency prefixes is standardized (`"$18 billion"`, `"₹1,500 crore"`).
- **Archetype Coverage:** Specific narrative structures are defined for:
  - M&A / Strategic Partnerships
  - Capital Expenditure / Infrastructure Expansion
  - Quarterly Earnings & Operating Margins
  - Fundraising / QIP / Equity Dilution
  - Regulatory, Legal & Tax Decisions
  - Commercial Contracts & Order Book Wins
  - IPO Filings / Prospectus Disclosures / Public Listings
  - Corporate Governance / Executive Leadership Realignment

---

## 7. The 20 Mandatory Final Gatekeeping Checks

Before any briefing can be delivered or published, `FinalValidationEngine` executes 20 programmatic pass/fail checks:

| Check # | Check Name | Description & Failure Conditions |
|---|---|---|
| **1** | Domestic Section Story Count | Exactly 5 stories in the Domestic section. |
| **2** | India Section Story Count | Exactly 5 stories in the India section. |
| **3** | International Story Count | Exactly 5 stories in the International section. |
| **4** | Accessible Verified URLs | URLs must resolve with HTTP 200; no 404s, broken links, or expired tokens. |
| **5** | Specific Article URLs | URLs must link directly to the specific article, not a category/hub page. |
| **6** | Headline-Article Semantic Overlap | Selected headline must share verified semantic entities with the source text. |
| **7** | Freshness Window Compliance | Articles must fall within the 24h–48h freshness horizon (72h on Mondays). |
| **8** | Two-Source Corroboration Ratio | Minimum 3 two-source verified stories per section (max 2 single-source). |
| **9** | 3-Day History Deduplication | No event fingerprint or hash may have appeared in the prior 3 days. |
| **10** | Intra-Day Company Uniqueness | No company may appear more than once in either the India or International section. |
| **11** | Numeric Token Grounding | Every number in a headline must exist verbatim in verified source article text. |
| **12** | Zero Fabricated Figures | No synthetic, uncorroborated financial or operational metrics. |
| **13** | Zero Fabricated URLs | Every URL must exist verbatim in the upstream candidate manifest. |
| **14** | Prohibited Content: Analyst Noise | Rejects price targets, broker recommendations, technical chart commentary. |
| **15** | Prohibited Content: Generic Market Wraps | Rejects routine market indices roundups ("Sensex slips 50 pts"). |
| **16** | Prohibited Content: IPO Intraday Subscriptions | Rejects minute-by-minute retail bidding tally articles. |
| **17** | Prohibited Content: Results Calendars | Rejects preview schedules of future earnings announcement dates. |
| **18** | Prohibited Content: Upcoming Speculation | Rejects rumors or events scheduled for future dates without formal filing. |
| **19** | Geopolitical Market Impact Gate | Geopolitical stories must quantify market, commodity, or trade impacts. |
| **20** | Format & Text Hygiene | Clean layout, correct punctuation, zero UTF-8 mojibake tokens (`ΓÇ`, `┬á`). |

---

## 8. Idempotency, Delivery & Safe Execution

### 8.1 Email Delivery Idempotency
- Before sending the morning email, the pipeline checks the persistent delivery log (`data/email_delivery_log.json` / SQLite store).
- If an email has already been dispatched for the current date:
  - The delivery runner logs `"Briefing email already sent today"`.
  - It cleanly exits with code `0`.
  - Under no circumstances is a duplicate email dispatched to subscribers.
  - The GitHub Actions workflow and Render Cron job detect this status and pass successfully.

### 8.2 Zero-News Integrity
- On quiet weekends or trading holidays where zero candidate stories pass materiality and freshness gates:
  - An informational log `"No News Found for [Date]"` is emitted.
  - No empty or dummy email is sent.
  - The web dashboard is not overwritten with empty data.
  - The job exits cleanly with code `0`.

### 8.3 Web Dashboard Synchronization
- The web dashboard server (`app/dashboard/web.py`) operates as a decoupled viewer.
- The web server must **never** trigger the pipeline on startup or import.
- Synchronization is handled exclusively through explicit sync scripts (`run_dashboard_sync.py`) or via the daily briefing runner after validation passes.
