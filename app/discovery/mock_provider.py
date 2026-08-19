"""
Mock Discovery Provider.

Provides static, high-quality candidate business articles across Indian and
International publishers for offline testing, CI/CD pipelines, and quota preservation.
"""

from datetime import datetime, timezone
from typing import List, Optional

from app.discovery.base import DiscoveryProvider
from app.discovery.models import DiscoveredArticle


# Sample hard business event articles across all target publishers
SAMPLE_MOCK_ARTICLES: List[DiscoveredArticle] = [
    # --- INDIA ARTICLES ---
    DiscoveredArticle(
        title="Tata Motors Board Approves Demerger into Two Listed Entities for CV and PV",
        url="https://economictimes.indiatimes.com/industry/auto/auto-news/tata-motors-board-approves-demerger-into-two-listed-companies/articleshow/108192301.cms",
        source="The Economic Times",
        snippet="Tata Motors on Tuesday approved the demerger of commercial vehicles and passenger vehicles into two separate listed entities to unlock value.",
        published_at=datetime(2026, 8, 18, 8, 30, tzinfo=timezone.utc),
        search_query="merger approved NCLT demerger",
        country="India",
        category_tag="mergers",
    ),
    DiscoveredArticle(
        title="Tata Motors demerger: Commercial and passenger vehicle businesses to split into separate listed firms",
        url="https://www.livemint.com/companies/news/tata-motors-demerger-split-into-two-listed-companies-commercial-passenger-vehicles-11709553892011.html",
        source="Livemint",
        snippet="The demerger will be implemented through an NCLT scheme of arrangement, allocating identical shareholding in both listed entities.",
        published_at=datetime(2026, 8, 18, 8, 45, tzinfo=timezone.utc),
        search_query="merger approved NCLT demerger",
        country="India",
        category_tag="mergers",
    ),
    DiscoveredArticle(
        title="HDFC Bank Q1 Net Profit Surges 18% YoY to ₹16,175 Crore on Strong NII Growth",
        url="https://www.business-standard.com/companies/results/hdfc-bank-q1-results-net-profit-rises-18-percent-to-16175-crore-124072000542_1.html",
        source="Business Standard",
        snippet="HDFC Bank reported an 18% year-on-year rise in net profit for the first quarter, driven by healthy net interest income and improving asset quality metrics.",
        published_at=datetime(2026, 8, 18, 9, 15, tzinfo=timezone.utc),
        search_query="quarterly results profit revenue",
        country="India",
        category_tag="earnings_results",
    ),
    DiscoveredArticle(
        title="HDFC Bank Q1 profit rises 18% to Rs 16,175 crore; asset quality remains stable",
        url="https://www.financialexpress.com/market/hdfc-bank-q1-results-fy27-net-profit-up-18-pc-at-rs-16175-crore-asset-quality-stable-3561234/",
        source="Financial Express",
        snippet="Gross non-performing assets (GNPA) stood at 1.33% compared to 1.34% in the previous quarter, with loan growth continuing across retail and commercial books.",
        published_at=datetime(2026, 8, 18, 9, 20, tzinfo=timezone.utc),
        search_query="quarterly results profit revenue",
        country="India",
        category_tag="earnings_results",
    ),
    DiscoveredArticle(
        title="Reliance Retail Launches ₹8,500 Crore QIP to Fund Omnichannel Logistics Expansion",
        url="https://economictimes.indiatimes.com/markets/stocks/news/reliance-retail-launches-qip-to-raise-8500-crore/articleshow/109876543.cms",
        source="The Economic Times",
        snippet="Reliance Retail Ventures has opened a Qualified Institutional Placement (QIP) with an indicative floor price of ₹1,420 per share.",
        published_at=datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc),
        search_query="Qualified Institutional Placement QIP floor price",
        country="India",
        category_tag="qips",
    ),
    DiscoveredArticle(
        title="RBI Imposes ₹2.5 Crore Penalty on Leading NBFC for Non-Compliance with Fair Lending Guidelines",
        url="https://www.livemint.com/industry/banking/rbi-imposes-penalty-on-nbfc-over-fair-lending-practices-11710987654321.html",
        source="Livemint",
        snippet="The Reserve Bank of India found irregularities in penal interest calculation and customer disclosures during statutory inspection.",
        published_at=datetime(2026, 8, 18, 10, 30, tzinfo=timezone.utc),
        search_query="RBI imposes penalty order bank NBFC",
        country="India",
        category_tag="regulatory_actions",
    ),
    DiscoveredArticle(
        title="Larsen & Toubro Bags Mega ₹4,200 Crore EPC Order in Middle East for Gas Processing Facility",
        url="https://www.business-standard.com/companies/news/lt-bags-mega-epc-order-worth-rs-4200-cr-in-middle-east-124081800123_1.html",
        source="Business Standard",
        snippet="The hydrocarbon business of L&T has secured a major offshore gas contract involving engineering, procurement, and construction.",
        published_at=datetime(2026, 8, 18, 11, 0, tzinfo=timezone.utc),
        search_query="acquires acquisition deal buyout",
        country="India",
        category_tag="sector_figures",
    ),
    DiscoveredArticle(
        title="India July CPI Inflation Cools to 3.84%, Dropping Below RBI 4% Target",
        url="https://www.financialexpress.com/economy/india-cpi-inflation-july-cools-below-rbi-target-3569871/",
        source="Financial Express",
        snippet="Retail inflation based on the consumer price index eased to a multi-month low of 3.84% in July, supported by softening food prices.",
        published_at=datetime(2026, 8, 18, 11, 30, tzinfo=timezone.utc),
        search_query="retail inflation CPI food inflation data",
        country="India",
        category_tag="macroeconomic_data",
    ),

    # --- INTERNATIONAL ARTICLES ---
    DiscoveredArticle(
        title="Nvidia Q2 Revenue Jumps 122% to $30.0 Billion on Surging AI Data Center Demand",
        url="https://www.reuters.com/technology/nvidia-quarterly-revenue-surges-ai-chip-demand-2026-08-18/",
        source="Reuters",
        snippet="Nvidia reported second-quarter revenue of $30.04 billion, beating Wall Street estimates of $28.7 billion, with data center revenue reaching $26.3 billion.",
        published_at=datetime(2026, 8, 18, 7, 0, tzinfo=timezone.utc),
        search_query="US earnings quarterly net income beats revenue guidance",
        country="International",
        category_tag="us_earnings",
    ),
    DiscoveredArticle(
        title="Nvidia reports record $30B quarterly revenue as AI infrastructure buildout accelerates",
        url="https://www.bloomberg.com/news/articles/2026-08-18/nvidia-reports-record-quarterly-revenue-driven-by-ai-hardware",
        source="Bloomberg",
        snippet="Gross margins expanded to 75.1% while the semiconductor titan approved an additional $50 billion share buyback authorization.",
        published_at=datetime(2026, 8, 18, 7, 10, tzinfo=timezone.utc),
        search_query="US earnings quarterly net income beats revenue guidance",
        country="International",
        category_tag="us_earnings",
    ),
    DiscoveredArticle(
        title="Nvidia beats earnings expectations with $30 billion in Q2 sales, unveils $50B buyback",
        url="https://www.cnbc.com/2026/08/18/nvidia-nvda-q2-earnings-report-2026.html",
        source="CNBC",
        snippet="Adjusted earnings per share came in at 68 cents versus 64 cents expected, continuing the company's streak of outperformance.",
        published_at=datetime(2026, 8, 18, 7, 15, tzinfo=timezone.utc),
        search_query="US earnings quarterly net income beats revenue guidance",
        country="International",
        category_tag="us_earnings",
    ),
    DiscoveredArticle(
        title="Federal Reserve Holds Benchmark Rate at 5.25%-5.50%, Signals Readiness for September Policy Pivot",
        url="https://www.wsj.com/economy/central-banking/fed-holds-rates-steady-signals-possible-cut-ahead-11723987123",
        source="Wall Street Journal",
        snippet="The Federal Open Market Committee left its benchmark interest rate unchanged while noting that progress toward its 2% inflation target continues.",
        published_at=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
        search_query="Federal Reserve FOMC interest rate decision",
        country="International",
        category_tag="fed_decisions",
    ),
    DiscoveredArticle(
        title="Fed keeps rates unchanged at 5.25%-5.50% as Powell cites cooling labor market and inflation progress",
        url="https://www.reuters.com/markets/us/fed-holds-interest-rates-signals-upcoming-rate-decision-2026-08-18/",
        source="Reuters",
        snippet="Fed Chair Jerome Powell noted during the press briefing that a reduction in the policy rate could be on the table at the upcoming meeting.",
        published_at=datetime(2026, 8, 18, 8, 15, tzinfo=timezone.utc),
        search_query="Federal Reserve FOMC interest rate decision",
        country="International",
        category_tag="fed_decisions",
    ),
    DiscoveredArticle(
        title="Rio Tinto Agrees $6.7 Billion Acquisition of Arcadium Lithium to Bolster Battery Metal Assets",
        url="https://www.ft.com/content/89b1c7a2-rio-tinto-arcadium-lithium-acquisition-deal",
        source="Financial Times",
        snippet="Mining giant Rio Tinto will acquire Arcadium Lithium in an all-cash $6.7 billion deal, paying a 90% premium to secure key lithium brine assets.",
        published_at=datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc),
        search_query="cross-border acquisition billion buyout deal",
        country="International",
        category_tag="acquisitions",
    ),
    DiscoveredArticle(
        title="European Commission Fines Tech Platform €1.15 Billion Over Cloud Antitrust Bundling",
        url="https://www.bloomberg.com/news/articles/2026-08-18/eu-imposes-1-15-billion-euro-fine-on-cloud-antitrust-practices",
        source="Bloomberg",
        snippet="European antitrust regulators concluded that uncompetitive bundling practices harmed cloud infrastructure rivals across the European Single Market.",
        published_at=datetime(2026, 8, 18, 9, 30, tzinfo=timezone.utc),
        search_query="EU antitrust commission fine ruling",
        country="International",
        category_tag="regulatory_decisions",
    ),
    DiscoveredArticle(
        title="Brent Crude Gains 2.8% to $82.40 on Red Sea Tanker Diversions and Inventory Drawdown",
        url="https://www.reuters.com/business/energy/oil-prices-rise-supply-concerns-shipping-routes-2026-08-18/",
        source="Reuters",
        snippet="Global crude benchmarks jumped as commercial tankers rerouted around Africa, adding shipping delays and tightening European supply balance.",
        published_at=datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc),
        search_query="crude oil price surge supply disruption barrel",
        country="International",
        category_tag="geopolitical_quantified",
    ),
]


class MockDiscoveryProvider(DiscoveryProvider):
    """
    Discovery provider backed by static curated mock articles.
    """

    def __init__(self, custom_articles: Optional[List[DiscoveredArticle]] = None) -> None:
        self._articles = list(custom_articles or SAMPLE_MOCK_ARTICLES)

    @property
    def provider_name(self) -> str:
        return "MockDiscoveryProvider"

    def discover(
        self,
        query: str,
        country: str,
        max_results: int = 10,
        category_tag: Optional[str] = None,
    ) -> List[DiscoveredArticle]:
        """
        Filter mock articles based on country, search query keywords, or category.
        """
        normalized_country = country.strip().title()
        if normalized_country in ("In", "India"):
            target_country = "India"
        else:
            target_country = "International"

        # 1. Filter by country
        candidates = [a for a in self._articles if a.country == target_country]

        # 2. Filter by category tag if provided
        if category_tag:
            filtered_by_cat = [a for a in candidates if a.category_tag == category_tag]
            if filtered_by_cat:
                return filtered_by_cat[:max_results]

        # 3. Filter by search query words if present
        query_words = set(query.lower().split())
        # Strip site filter words
        query_words = {w for w in query_words if not w.startswith("site:") and w not in ("or", "and")}

        if query_words:
            scored: List[tuple[int, DiscoveredArticle]] = []
            for art in candidates:
                text_corpus = f"{art.title} {art.snippet or ''} {art.search_query}".lower()
                matches = sum(1 for w in query_words if w in text_corpus)
                scored.append((matches, art))

            scored.sort(key=lambda item: item[0], reverse=True)
            matched_candidates = [art for score, art in scored if score > 0]
            if matched_candidates:
                return matched_candidates[:max_results]

        return candidates[:max_results]
