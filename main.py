"""
Automated Investment Committee News Briefing System.

Main Application Entry Point.
"""

import sys
from datetime import datetime, timezone

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from config import get_settings
from app.logging_config import setup_logging, get_logger
from app.models import Article
from app.discovery import (
    GoogleNewsRSSDiscoveryProvider,
    MockDiscoveryProvider,
    NewsDiscoveryService,
)


def main() -> int:
    """Initialize system and demonstrate news discovery."""
    # 1. Initialize Logging
    setup_logging()
    logger = get_logger("main")
    logger.info("Initializing Investment Committee News Briefing System...")

    # 2. Load and Validate Configuration
    try:
        settings = get_settings()
        logger.info("Configuration loaded: APP_ENV=%s, LOG_LEVEL=%s", settings.APP_ENV, settings.LOG_LEVEL)
        logger.info("Storage directories initialized: DATA=%s, LOGS=%s", settings.DATA_PATH, settings.LOGS_PATH)
    except Exception as exc:
        logger.critical("Failed to load application configuration: %s", exc, exc_info=True)
        return 1

    print("=" * 75)
    print(f" {settings.APP_NAME.upper()}")
    print("=" * 75)
    print(f" Status: Section 2 (News Discovery Complete)")
    print(f" Environment: {settings.APP_ENV} | Database: {settings.DATABASE_URL}")
    print("=" * 75)

    # 3. Execute Discovery using Mock Provider (Safe for offline & quota preservation)
    logger.info("Executing news discovery via MockDiscoveryProvider...")
    discovery_service = NewsDiscoveryService(provider=MockDiscoveryProvider())
    candidates = discovery_service.discover_all(max_india=10, max_international=10)

    india_candidates = candidates["india"]
    intl_candidates = candidates["international"]

    print(f"\n>>> DISCOVERED CANDIDATES SUMMARY:")
    print(f"    - India Candidates Found: {len(india_candidates)}")
    print(f"    - International Candidates Found: {len(intl_candidates)}")
    print(f"    - Total Candidates Found: {len(india_candidates) + len(intl_candidates)}")

    print("\n" + "=" * 75)
    print(" SAMPLE DISCOVERED ARTICLES (INDIA):")
    print("=" * 75)
    for idx, art in enumerate(india_candidates[:4], 1):
        print(f"\n[{idx}] {art.title}")
        print(f"    Source:       {art.source}")
        print(f"    Category Tag: {art.category_tag}")
        print(f"    Country:      {art.country}")
        print(f"    Published At: {art.published_at.strftime('%Y-%m-%d %H:%M UTC') if art.published_at else 'N/A'}")
        print(f"    Query:        {art.search_query}")
        print(f"    URL:          {art.url}")
        print(f"    Snippet:      {art.snippet}")

    print("\n" + "=" * 75)
    print(" SAMPLE DISCOVERED ARTICLES (INTERNATIONAL):")
    print("=" * 75)
    for idx, art in enumerate(intl_candidates[:4], 1):
        print(f"\n[{idx}] {art.title}")
        print(f"    Source:       {art.source}")
        print(f"    Category Tag: {art.category_tag}")
        print(f"    Country:      {art.country}")
        print(f"    Published At: {art.published_at.strftime('%Y-%m-%d %H:%M UTC') if art.published_at else 'N/A'}")
        print(f"    Query:        {art.search_query}")
        print(f"    URL:          {art.url}")
        print(f"    Snippet:      {art.snippet}")

    print("\n" + "=" * 75)
    print(" DEMONSTRATING SECTION 3 (ARTICLE EXTRACTION):")
    print("=" * 75)
    from tests.test_extraction import FIXTURE_JSON_LD_ARTICLE, FIXTURE_OPENGRAPH_META_ARTICLE
    from app.extraction import ArticleExtractor

    extractor = ArticleExtractor()
    sample_candidate = india_candidates[0]

    # Demonstrate extraction from realistic structured page
    logger.info("Extracting candidate article: %s", sample_candidate.url)
    extraction_res = extractor.extract_from_html(
        html=FIXTURE_JSON_LD_ARTICLE,
        url=sample_candidate.url,
        source_name=sample_candidate.source,
        candidate_title=sample_candidate.title,
        candidate_category=sample_candidate.country,
    )

    if extraction_res.success and extraction_res.article:
        art = extraction_res.article
        print(f"\n[EXTRACTION SUCCESS] Article Extracted:")
        print(f"    - Title:         {art.title}")
        print(f"    - Source:        {art.source_name}")
        print(f"    - Author:        {art.author}")
        print(f"    - Published At:  {art.published_at.strftime('%Y-%m-%d %H:%M UTC') if art.published_at else 'N/A'}")
        print(f"    - Date Verified: {art.date_verified}")
        print(f"    - Word Count:    {art.word_count} words")
        print(f"    - URL Preserved: {art.url}")
        print(f"    - Body Excerpt:  {art.content_text[:180]}...")
    else:
        print(f"\n[EXTRACTION FAILED]: {extraction_res.error_message}")

    print("\n" + "=" * 75)
    print(" DEMONSTRATING SECTION 4 (HARD FILTER ENGINE):")
    print("=" * 75)
    from datetime import timedelta
    from app.filtering import HardFilterEngine
    from app.models.enums import NewsCategory

    filter_engine = HardFilterEngine()

    # Create a batch with both valid articles and intentional noise/stale/hub articles
    test_batch = [
        # 1. Valid Hard Event (Recent earnings with figures)
        Article(
            title="HDFC Bank Q1 Net Profit Jumps 18% YoY to ₹16,175 Crore on Strong NII",
            url="https://www.business-standard.com/companies/results/hdfc-bank-q1-results-12345.html",
            source_name="Business Standard",
            published_at=datetime.now(timezone.utc) - timedelta(hours=4),
            content_text="HDFC Bank reported net profit of ₹16,175 crore for the first quarter, up 18% YoY.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        ),
        # 2. Valid Hard Event (Acquisition)
        Article(
            title="Rio Tinto Agrees $6.7 Billion Acquisition of Arcadium Lithium in All-Cash Deal",
            url="https://www.reuters.com/business/energy/rio-tinto-arcadium-lithium-deal-2026-08-18/",
            source_name="Reuters",
            published_at=datetime.now(timezone.utc) - timedelta(hours=8),
            content_text="Rio Tinto has signed a definitive agreement to acquire Arcadium Lithium for $6.7 billion.",
            category=NewsCategory.INTERNATIONAL,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        ),
        # 3. Rejected: Analyst Upgrade / Price Target (Noise)
        Article(
            title="Jefferies Upgrades Tata Motors to Buy with Revised Target Price of Rs 1,250",
            url="https://economictimes.indiatimes.com/markets/stocks/recs/jefferies-tata-motors-target/123.cms",
            source_name="The Economic Times",
            published_at=datetime.now(timezone.utc) - timedelta(hours=3),
            content_text="Brokerage firm Jefferies has upgraded the automaker citing strong margin expansion.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        ),
        # 4. Rejected: Generic Market Wrap (Noise)
        Article(
            title="Stock Market Today: Sensex Ends 320 Points Higher in Broad-Based Rally",
            url="https://www.livemint.com/market/stock-market-news/sensex-nifty-closing-bell-today-1234.html",
            source_name="Livemint",
            published_at=datetime.now(timezone.utc) - timedelta(hours=2),
            content_text="Closing bell summary: Domestic indices finished in the green led by IT and metal stocks.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        ),
        # 5. Rejected: Non-Article Topic URL
        Article(
            title="Automotive Sector Updates & Latest News Feed",
            url="https://www.financialexpress.com/topic/automotive-industry/",
            source_name="Financial Express",
            published_at=datetime.now(timezone.utc) - timedelta(hours=5),
            content_text="Explore recent automotive news, earnings reports, and regulatory updates.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        ),
        # 6. Rejected: Stale Publication Date (> 48h)
        Article(
            title="Reliance Retail Raises ₹8,500 Crore in QIP Offering",
            url="https://economictimes.indiatimes.com/markets/stocks/reliance-retail-qip/999.cms",
            source_name="The Economic Times",
            published_at=datetime.now(timezone.utc) - timedelta(hours=72),
            content_text="Reliance Retail opened its QIP offering with a floor price of ₹1,420 per share.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        ),
    ]

    accepted_articles, rejection_results = filter_engine.filter_candidates(test_batch)

    print(f"\n>>> HARD FILTER EVALUATION SUMMARY:")
    print(f"    - Total Candidates Evaluated: {len(test_batch)}")
    print(f"    - Accepted Hard Business Events: {len(accepted_articles)}")
    print(f"    - Rejected Noise/Invalid Items: {len(rejection_results)}")

    print("\n--- [ACCEPTED ARTICLES] ---")
    for idx, art in enumerate(accepted_articles, 1):
        print(f"[{idx}] PASS: {art.title}")
        print(f"     Source: {art.source_name} | Published: {art.published_at.strftime('%Y-%m-%d %H:%M UTC')}")

    print("\n--- [REJECTED CANDIDATES & DIAGNOSTIC REASONS] ---")
    for idx, rej in enumerate(rejection_results, 1):
        print(f"[{idx}] REJECT [{rej.rule_failed}]: {rej.article_title}")
        print(f"     Reason: {rej.rejection_reason}")

    print("\n" + "=" * 75)
    print(" DEMONSTRATING SECTION 5 (AI ARTICLE CLASSIFICATION):")
    print("=" * 75)
    from app.classification import AIArticleClassifier

    classifier = AIArticleClassifier()

    for idx, art in enumerate(accepted_articles, 1):
        print(f"\n[CLASSIFYING ARTICLE {idx}]: {art.title}")
        res = classifier.classify(art)
        if res.success and res.classification:
            c = res.classification
            print(f"    - Event Type:           {c.event_type.value}")
            print(f"    - Company Names:        {c.company_names}")
            print(f"    - Financial Numbers:    {c.financial_numbers}")
            print(f"    - Percentages:          {c.percentages}")
            print(f"    - Deal Value:           {c.deal_value}")
            print(f"    - Key Outcome:          {c.key_outcome}")
            print(f"    - Hard Business Event:  {c.is_hard_business_event}")
            print(f"    - Quantified Impact:    {c.has_specific_quantified_impact}")
            print(f"    - Investment Relevant:  {c.is_investment_relevant}")
            print(f"    - Extraction Attempts:  {res.attempts}")
        else:
            print(f"    - Classification Failed: {res.error_message}")

    print("\n" + "=" * 75)
    print(" DEMONSTRATING SECTION 6 (TWO-SOURCE VERIFICATION):")
    print("=" * 75)
    from app.models.event import Event
    from app.verification import TwoSourceVerifier, VerificationStatus

    verifier = TwoSourceVerifier()

    # Create test events for verification demonstration
    # Event A: Corroborated by 2 independent outlets (Business Standard & Economic Times)
    event_hdfc = Event(
        canonical_title="HDFC Bank Q1 Net Profit Jumps 18% YoY to ₹16,175 Crore on Strong NII",
        description="HDFC Bank reported net profit of ₹16,175 crore for Q1 with stable asset quality.",
        companies_involved=["HDFC Bank"],
        financial_figures=["₹16,175 crore", "18%"],
        event_category=NewsCategory.INDIA,
    )
    art_hdfc_et = Article(
        title="HDFC Bank Q1 Profit Rises 18% to ₹16,175 Crore; GNPA at 1.33%",
        url="https://economictimes.indiatimes.com/industry/banking/hdfc-q1-profit-1234.cms",
        source_name="The Economic Times",
        published_at=datetime.now(timezone.utc) - timedelta(hours=3),
        content_text="HDFC Bank on Tuesday posted an 18% YoY increase in net profit to ₹16,175 crore.",
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )

    # Event B: Single-source event (only Reuters reported Arcadium Lithium acquisition)
    event_rio = Event(
        canonical_title="Rio Tinto Agrees $6.7 Billion Acquisition of Arcadium Lithium",
        description="Rio Tinto signed a definitive agreement to acquire Arcadium Lithium for $6.7 billion in cash.",
        companies_involved=["Rio Tinto", "Arcadium Lithium"],
        financial_figures=["$6.7 billion"],
        event_category=NewsCategory.INTERNATIONAL,
    )

    # Evaluate Event A (2 Independent Sources)
    verif_a = verifier.verify_event(event_hdfc, [accepted_articles[0], art_hdfc_et])
    print(f"\n[EVENT 1 - TWO INDEPENDENT SOURCES]: {event_hdfc.canonical_title}")
    print(f"    - Status:          {verif_a.verification_status.value}")
    print(f"    - Is Verified:     {verif_a.is_verified}")
    print(f"    - Primary Source:  {verif_a.primary_source}")
    print(f"    - Secondary Source:{verif_a.secondary_source}")
    print(f"    - Source Count:    {verif_a.source_count}")
    print(f"    - Is Independent:  {verif_a.is_independent}")
    print(f"    - Details:         {verif_a.matching_details}")

    # Evaluate Event B (Single Source)
    verif_b = verifier.verify_event(event_rio, [accepted_articles[1]])
    print(f"\n[EVENT 2 - SINGLE SOURCE ONLY]: {event_rio.canonical_title}")
    print(f"    - Status:          {verif_b.verification_status.value}")
    print(f"    - Is Verified:     {verif_b.is_verified}")
    print(f"    - Primary Source:  {verif_b.primary_source}")
    print(f"    - Secondary Source:{verif_b.secondary_source}")
    print(f"    - Source Count:    {verif_b.source_count}")
    print(f"    - Is Independent:  {verif_b.is_independent}")
    print(f"    - Details:         {verif_b.matching_details}")

    print("\n" + "=" * 75)
    print(" DEMONSTRATING SECTION 7 (EVENT DEDUPLICATION & HISTORY):")
    print("=" * 75)
    from datetime import date
    from app.deduplication import DeduplicationEngine, HistoryStore

    history_store = HistoryStore(db_path=":memory:")

    # 1. Seed SQLite database with a historical briefing story from 2 days ago
    briefing_date_today = date.today()
    two_days_ago = briefing_date_today - timedelta(days=2)

    history_store.save_briefing(
        briefing_date=two_days_ago,
        stories=[
            {
                "event_id": "hist-evt-001",
                "event_fingerprint": "hdfc_bank:earnings:2026-08-18:16175cr_18pct",
                "headline": "HDFC Bank Q1 Net Profit Surges 18% YoY to ₹16,175 Crore",
                "company_name": "HDFC Bank",
                "category": "india",
                "published_date": two_days_ago,
            }
        ],
    )
    print(f"\n[SEEDED SQLITE HISTORY]: Recorded 1 story from {two_days_ago.isoformat()} in SQLite.")

    # 2. Run Deduplication Engine against a candidate batch
    dedup_engine = DeduplicationEngine(history_store=history_store)

    candidate_batch = [
        # Candidate 1: Same HDFC Bank story as 2 days ago (Should be REJECTED by 3-day lookback)
        {
            "headline": "HDFC Bank Q1 Net Profit Surges 18% YoY to ₹16,175 Crore",
            "company_name": "HDFC Bank",
            "event_type": "EARNINGS",
            "category": "india",
            "key_facts": ["16175cr", "18pct"],
        },
        # Candidate 2: Tata Motors Demerger (India - Should be ACCEPTED)
        {
            "headline": "Tata Motors Board Approves Demerger into CV and PV Entities",
            "company_name": "Tata Motors",
            "event_type": "M&A",
            "category": "india",
            "key_facts": [],
        },
        # Candidate 3: Tata Motors EV Investment (India - Same Company duplicate, Should be REJECTED)
        {
            "headline": "Tata Motors Plans ₹15,000 Crore EV Plant in Tamil Nadu",
            "company_name": "Tata Motors",
            "event_type": "POLICY",
            "category": "india",
            "key_facts": ["15000cr"],
        },
        # Candidate 4: Nvidia Q2 Revenue (International - Should be ACCEPTED)
        {
            "headline": "Nvidia Q2 Revenue Jumps 122% to $30 Billion",
            "company_name": "Nvidia",
            "event_type": "EARNINGS",
            "category": "international",
            "key_facts": ["30b", "122pct"],
        },
        # Candidate 5: Nvidia $50B Buyback (International - Different event for same company, Should be ACCEPTED)
        {
            "headline": "Nvidia Board Authorizes New $50 Billion Share Repurchase",
            "company_name": "Nvidia",
            "event_type": "FUNDRAISING",
            "category": "international",
            "key_facts": ["50b"],
        },
    ]

    accepted_dedup, rejected_dedup = dedup_engine.filter_stories(
        candidate_stories=candidate_batch,
        target_date=briefing_date_today,
        lookback_days=3,
    )

    print(f"\n>>> DEDUPLICATION EVALUATION SUMMARY:")
    print(f"    - Total Candidates Evaluated: {len(candidate_batch)}")
    print(f"    - Accepted Unique Stories:    {len(accepted_dedup)}")
    print(f"    - Rejected Duplicate Stories: {len(rejected_dedup)}")

    print("\n--- [ACCEPTED STORIES FOR TODAY'S BRIEFING] ---")
    for idx, s in enumerate(accepted_dedup, 1):
        print(f"[{idx}] PASS: {s['headline']} ({s['category'].upper()})")
        print(f"     Company: {s['company_name']} | Fingerprint: {s['event_fingerprint']}")

    print("\n--- [REJECTED STORIES & CONSTRAINTS ENFORCED] ---")
    for idx, r in enumerate(rejected_dedup, 1):
        print(f"[{idx}] REJECT [{r['rejection_rule']}]: {r['headline']}")
        print(f"     Reason: {r['rejection_reason']}")

    print("\n" + "=" * 75)
    print(" DEMONSTRATING SECTION 8 (INVESTMENT RELEVANCE SCORING):")
    print("=" * 75)
    from app.ranking import CandidatePoolRanker

    ranker = CandidatePoolRanker()

    # Build a realistic pool of verified events for India and International
    eval_events = [
        # India 1: HDFC Bank Earnings (Mega earnings scale)
        Event(
            canonical_title="HDFC Bank Q1 Net Profit Surges 18% YoY to ₹16,175 Crore on Strong NII",
            description="Standalone net profit jumped 18% with gross NPAs steady at 1.33%.",
            companies_involved=["HDFC Bank"],
            financial_figures=["₹16,175 crore", "18%"],
            event_category=NewsCategory.INDIA,
            article_ids=["art-in-1", "art-in-2"],
        ),
        # India 2: Tata Motors Demerger (Strategic restructuring)
        Event(
            canonical_title="Tata Motors Board Approves Demerger of CV and PV Businesses",
            description="Board approves splitting the automotive titan into two listed pure-play entities.",
            companies_involved=["Tata Motors"],
            financial_figures=[],
            event_category=NewsCategory.INDIA,
            article_ids=["art-in-3", "art-in-4"],
        ),
        # India 3: L&T Mega EPC Contract
        Event(
            canonical_title="L&T Hydrocarbon Secures ₹4,200 Crore Gas Processing Contract in Middle East",
            description="Major offshore energy EPC order won against international bidding competition.",
            companies_involved=["Larsen & Toubro"],
            financial_figures=["₹4,200 crore"],
            event_category=NewsCategory.INDIA,
            article_ids=["art-in-5", "art-in-6"],
        ),
        # India 4: Retail Inflation Macro Release
        Event(
            canonical_title="India July CPI Inflation Cools to 3.84%, Dropping Below RBI 4% Target",
            description="Retail price inflation eases significantly, expanding headroom for monetary policy easing.",
            companies_involved=["RBI"],
            financial_figures=["3.84%"],
            event_category=NewsCategory.INDIA,
            article_ids=["art-in-7", "art-in-8"],
        ),
        # International 1: Nvidia Q2 Record Revenue
        Event(
            canonical_title="Nvidia Q2 Revenue Jumps 122% to $30.0 Billion on Surging AI Data Center Demand",
            description="AI hardware demand propels data center sales to $26.3 billion with gross margins of 75.1%.",
            companies_involved=["Nvidia"],
            financial_figures=["$30.0 billion", "122%"],
            event_category=NewsCategory.INTERNATIONAL,
            article_ids=["art-intl-1", "art-intl-2"],
        ),
        # International 2: Rio Tinto M&A Deal
        Event(
            canonical_title="Rio Tinto Agrees $6.7 Billion Acquisition of Arcadium Lithium in All-Cash Deal",
            description="Mining giant secures critical lithium production footprint across the Americas.",
            companies_involved=["Rio Tinto", "Arcadium Lithium"],
            financial_figures=["$6.7 billion"],
            event_category=NewsCategory.INTERNATIONAL,
            article_ids=["art-intl-3", "art-intl-4"],
        ),
        # International 3: EU Antitrust Cloud Fine
        Event(
            canonical_title="European Commission Fines Tech Platform €1.15 Billion Over Cloud Antitrust Bundling",
            description="Regulators enforce landmark antitrust fine over uncompetitive cloud software practices.",
            companies_involved=["European Commission"],
            financial_figures=["€1.15 billion"],
            event_category=NewsCategory.INTERNATIONAL,
            article_ids=["art-intl-5", "art-intl-6"],
        ),
    ]

    candidate_pool = ranker.rank_events(events=eval_events, top_n=10)

    print(f"\n>>> CANDIDATE POOL RANKING (TOP CANDIDATES RESERVED):")
    print(f"    - Total Verified Events Evaluated: {candidate_pool.total_evaluated}")
    print(f"    - India Top Candidates:            {len(candidate_pool.india_candidates)}")
    print(f"    - International Top Candidates:    {len(candidate_pool.international_candidates)}")

    print("\n--- [TOP INDIA CANDIDATES (RANKED BY INVESTMENT SCORE)] ---")
    for scored in candidate_pool.india_candidates:
        b = scored.score_breakdown
        print(f"\nRank #{scored.rank}: [{scored.investment_score:.1f}/100] {scored.event.canonical_title}")
        print(f"    Breakdown => Magnitude(30%): {b.financial_magnitude:.0f} | Market(25%): {b.market_impact:.0f} | Investor(20%): {b.investor_relevance:.0f} | Corp(15%): {b.corporate_significance:.0f} | Source(10%): {b.source_quality:.0f}")

    print("\n--- [TOP INTERNATIONAL CANDIDATES (RANKED BY INVESTMENT SCORE)] ---")
    for scored in candidate_pool.international_candidates:
        b = scored.score_breakdown
        print(f"\nRank #{scored.rank}: [{scored.investment_score:.1f}/100] {scored.event.canonical_title}")
        print(f"    Breakdown => Magnitude(30%): {b.financial_magnitude:.0f} | Market(25%): {b.market_impact:.0f} | Investor(20%): {b.investor_relevance:.0f} | Corp(15%): {b.corporate_significance:.0f} | Source(10%): {b.source_quality:.0f}")

    print("\n" + "=" * 75)
    print(" DEMONSTRATING SECTION 9 (GEMINI FINAL EDITOR):")
    print("=" * 75)
    from app.ai import GeminiEditorialEngine

    # Build full article lookup map for the candidate pool
    articles_lookup = {
        "art-in-1": accepted_articles[0],
        "art-in-2": art_hdfc_et,
        "art-in-3": Article(
            id="art-in-3",
            title="Tata Motors Board Approves Demerger of CV and PV Businesses",
            url="https://economictimes.indiatimes.com/industry/auto/tata-motors-demerger/108192301.cms",
            source_name="The Economic Times",
            published_at=datetime.now(timezone.utc),
            content_text="Tata Motors on Tuesday approved the demerger into two separate listed entities.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        ),
        "art-in-5": Article(
            id="art-in-5",
            title="L&T Secures Mega ₹4,200 Crore EPC Contract in Middle East",
            url="https://www.business-standard.com/companies/news/lt-bags-epc-order-4200-cr-123.html",
            source_name="Business Standard",
            published_at=datetime.now(timezone.utc),
            content_text="Larsen & Toubro secured an offshore gas processing contract worth ₹4,200 crore.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        ),
        "art-in-7": Article(
            id="art-in-7",
            title="India July CPI Inflation Cools to 3.84%, Dropping Below RBI 4% Target",
            url="https://www.livemint.com/economy/india-cpi-inflation-july-3-84-percent-123.html",
            source_name="Livemint",
            published_at=datetime.now(timezone.utc),
            content_text="Retail inflation based on CPI eased to 3.84% in July, government data showed.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        ),
        "art-intl-1": Article(
            id="art-intl-1",
            title="Nvidia Q2 Revenue Jumps 122% to $30.0 Billion on Surging AI Data Center Demand",
            url="https://www.reuters.com/technology/nvidia-quarterly-revenue-surges-2026-08-18/",
            source_name="Reuters",
            published_at=datetime.now(timezone.utc),
            content_text="Nvidia reported revenue of $30.04 billion beating estimates with 75.1% gross margin.",
            category=NewsCategory.INTERNATIONAL,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        ),
        "art-intl-3": accepted_articles[1],
        "art-intl-5": Article(
            id="art-intl-5",
            title="European Commission Fines Tech Platform €1.15 Billion Over Cloud Antitrust Bundling",
            url="https://www.bloomberg.com/news/articles/2026-08-18/eu-imposes-1-15-billion-fine",
            source_name="Bloomberg",
            published_at=datetime.now(timezone.utc),
            content_text="European antitrust regulators imposed a landmark €1.15 billion fine on uncompetitive bundling.",
            category=NewsCategory.INTERNATIONAL,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        ),
    }

    editorial_engine = GeminiEditorialEngine()
    editorial_res = editorial_engine.select_and_synthesize_briefing(candidate_pool, articles_lookup)

    if editorial_res.success and editorial_res.selection:
        sel = editorial_res.selection
        print(f"\n>>> FINAL EDITORIAL SELECTION SUMMARY:")
        print(f"    - India Stories Selected:         {len(sel.india_stories)}")
        print(f"    - International Stories Selected: {len(sel.international_stories)}")

        print("\n" + "=" * 55)
        print(" [INDIA BUSINESS BRIEFING - EXECUTIVE SELECTION]")
        print("=" * 55)
        for idx, story in enumerate(sel.india_stories, 1):
            print(f"\n{idx}. {story.headline}")
            print(f"   Source: {story.source}")
            print(f"   URL:    {story.url}")

        print("\n" + "=" * 55)
        print(" [INTERNATIONAL BUSINESS BRIEFING - EXECUTIVE SELECTION]")
        print("=" * 55)
        for idx, story in enumerate(sel.international_stories, 1):
            print(f"\n{idx}. {story.headline}")
            print(f"   Source: {story.source}")
            print(f"   URL:    {story.url}")
    else:
        print(f"\n[EDITORIAL FAILED]: {editorial_res.error_message}")

    print("\n" + "=" * 75)
    print(" DEMONSTRATING SECTION 10 (FINAL VALIDATION ENGINE - 20 CHECKS):")
    print("=" * 75)
    from app.validation import FinalValidationEngine, ValidationStatus

    # Map candidate events for quick lookup
    events_lookup = {e.id: e for e in eval_events}

    validator = FinalValidationEngine(history_store=history_store)
    validation_report = validator.validate_briefing(
        payload=editorial_res.selection,
        events_lookup=events_lookup,
        articles_lookup=articles_lookup,
        target_date=briefing_date_today,
        strict_5_per_section=False,  # Allow demo sample size (4 India, 3 Intl)
    )

    print(f"\n>>> FINAL GATEKEEPING VALIDATION STATUS: [{validation_report.status.value}]")
    print(f"    - Passed Checks: {validation_report.passed_checks} / 20")
    print(f"    - Failed Checks: {validation_report.failed_checks} / 20")
    print(f"    - Is Approved To Send: {validation_report.is_valid}")

    print("\n--- [20 DETERMINISTIC GATEKEEPING CHECKS BREAKDOWN] ---")
    for chk in validation_report.check_results:
        symbol = "✅ PASS" if chk.passed else "❌ FAIL"
        reason = f" => Reason: {chk.failure_reason}" if not chk.passed else ""
        print(f"Check #{chk.check_id:02d} [{symbol}]: {chk.check_name}{reason}")

    if validation_report.status == ValidationStatus.PASSED:
        print("\n🎉 ALL 20 CRITICAL GATEKEEPING CHECKS PASSED. BRIEFING IS APPROVED FOR DELIVERY!")
    else:
        print(f"\n⛔ CRITICAL GATEKEEPING FAILURE: DO NOT SEND! Reason: {validation_report.failure_reason}")

    print("\n" + "=" * 75)
    logger.info("Discovery, Extraction, Filtering, Classification, Verification, Deduplication, Scoring, Editorial Selection, and Final Validation completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
