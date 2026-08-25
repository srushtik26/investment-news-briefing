"""
Unit tests for deterministic Hard Filter Engine and individual filter rules.
"""

from datetime import datetime, timedelta, timezone
import pytest

from app.models.article import Article
from app.models.enums import NewsCategory
from app.filtering import (
    DateFilterRule,
    HardFilterEngine,
    SourceFilterRule,
    StoryTypeFilterRule,
    URLFilterRule,
)


@pytest.fixture
def base_article() -> Article:
    """Fixture providing a standard valid article."""
    return Article(
        title="Tata Motors Board Approves Demerger into Two Listed Entities",
        url="https://economictimes.indiatimes.com/industry/auto/tata-motors-demerger/articleshow/108192301.cms",
        source_name="The Economic Times",
        published_at=datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc),
        content_text=(
            "Tata Motors on Tuesday approved the demerger of commercial and passenger vehicle businesses "
            "into two separate listed companies. The company reported quarterly net profit growth of 24% "
            "and plans to complete the NCLT scheme within 12 months."
        ),
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


class TestDateFilterRule:
    """Tests for DateFilterRule."""

    def test_fresh_article_accepted(self, base_article: Article):
        """Test article within 48h is accepted."""
        eval_time = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
        rule = DateFilterRule()
        result = rule.evaluate(base_article, now_utc=eval_time)
        assert result.is_accepted is True

    def test_stale_article_rejected(self, base_article: Article):
        """Test article older than 24h is rejected."""
        eval_time = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
        # Article published 25 hours prior — exceeds the 24h window
        base_article.published_at = eval_time - timedelta(hours=25)

        rule = DateFilterRule()
        result = rule.evaluate(base_article, now_utc=eval_time)
        assert result.is_accepted is False
        assert result.rule_failed == "DATE"
        assert "exceeds allowable 24h freshness window" in result.rejection_reason

    def test_missing_or_unverified_date_rejected(self, base_article: Article):
        """Test unverified or missing publication date is rejected."""
        base_article.published_at = None
        base_article.date_verified = False

        rule = DateFilterRule()
        result = rule.evaluate(base_article)
        assert result.is_accepted is False
        assert "Missing or unverified publication date" in result.rejection_reason

    def test_future_publication_date_rejected(self, base_article: Article):
        """Test impossible future dates are rejected."""
        eval_time = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
        base_article.published_at = eval_time + timedelta(hours=10)

        rule = DateFilterRule()
        result = rule.evaluate(base_article, now_utc=eval_time)
        assert result.is_accepted is False
        assert "in the future" in result.rejection_reason


class TestSourceFilterRule:
    """Tests for SourceFilterRule."""

    @pytest.mark.parametrize("source_name, domain", [
        ("The Economic Times", "economictimes.indiatimes.com"),
        ("Business Standard", "business-standard.com"),
        ("Livemint", "livemint.com"),
        ("Financial Express", "financialexpress.com"),
        ("Reuters", "reuters.com"),
        ("CNBC", "cnbc.com"),
        ("Bloomberg", "bloomberg.com"),
        ("Financial Times", "ft.com"),
        ("Wall Street Journal", "wsj.com"),
    ])
    def test_allowed_sources_accepted(self, base_article: Article, source_name: str, domain: str):
        """Test all approved Indian and International publishers are accepted."""
        base_article.source_name = source_name
        base_article.url = f"https://www.{domain}/news/article-12345"

        rule = SourceFilterRule()
        result = rule.evaluate(base_article)
        assert result.is_accepted is True

    def test_unapproved_source_rejected(self, base_article: Article):
        """Test aggregator or unapproved source is rejected."""
        base_article.source_name = "TechBuzz News Blog"
        base_article.url = "https://www.techbuzznews.com/post/tata-motors"

        rule = SourceFilterRule()
        result = rule.evaluate(base_article)
        assert result.is_accepted is False
        assert result.rule_failed == "SOURCE"
        assert "not in approved publisher whitelist" in result.rejection_reason


class TestURLFilterRule:
    """Tests for URLFilterRule."""

    @pytest.mark.parametrize("bad_url, expected_reason", [
        ("https://economictimes.indiatimes.com/", "non-article"),
        ("https://economictimes.indiatimes.com/index.html", "non-article"),
        ("https://www.livemint.com/topic/tata-motors", "non-article"),
        ("https://www.livemint.com/agency/pti/page/2", "non-article"),
        ("https://www.cnbc.com/tags/tech-investing", "non-article"),
        ("https://www.business-standard.com/category/companies", "non-article"),
        ("https://www.reuters.com/newsletters/daily-brief", "non-article"),
        ("https://example.com/newsletter/unsubscribe", "non-article"),
        ("https://example.com/financial-report.pdf", "non-article"),
        ("https://www.bloomberg.com/live-blog/markets-today", "non-article"),
        ("https://www.reuters.com/search/news?q=hdfc+bank", "search/query"),
        ("https://example.com/search?s=reliance", "search/query"),
    ])
    def test_rejected_url_patterns(self, base_article: Article, bad_url: str, expected_reason: str):
        """Test rejection of topic pages, homepages, newsletters, live blogs, and search URLs."""
        base_article.url = bad_url
        rule = URLFilterRule()
        result = rule.evaluate(base_article)
        assert result.is_accepted is False
        assert result.rule_failed == "URL"

    def test_valid_article_url_accepted(self, base_article: Article):
        """Test valid article URL is accepted."""
        rule = URLFilterRule()
        result = rule.evaluate(base_article)
        assert result.is_accepted is True


class TestStoryTypeFilterRule:
    """Tests for StoryTypeFilterRule."""

    @pytest.mark.parametrize("noisy_title, noisy_text, noise_category", [
        (
            "Jefferies Upgrades Tata Motors to Buy With Target Price of Rs 1,200",
            "Brokerage firm Jefferies has upgraded the stock from hold to buy citing strong margin outlook.",
            "analyst_rating",
        ),
        (
            "Morgan Stanley Sees Target Price of Rs 3,400 for Reliance Industries",
            "The global brokerage maintains an overweight rating with a revised share target price.",
            "price_target",
        ),
        (
            "Stock Market Today: Sensex Ends 340 Points Higher, Nifty Tops 24,800 in Broad Rally",
            "Markets wrap: Domestic indices ended higher today led by IT and banking stocks. Closing bell summary.",
            "market_summary",
        ),
        (
            "Upcoming Earnings: Companies Announcing Results Today Include RIL, TCS, and HDFC Bank",
            "Earnings calendar: Check the full list of quarterly results scheduled for announcement today.",
            "results_calendar",
        ),
        (
            "Brainbees Solutions IPO Day 2 Subscription Status: Issue Subscribed 1.4x",
            "The IPO day 2 subscription status shows retail quota oversubscribed while institutional bidding remains slow.",
            "ipo_intraday",
        ),
        (
            "Opinion: Why India Needs Bolder Manufacturing Policy Reforms",
            "Editorial column: A strategic analysis on economic growth drivers and private capex trends.",
            "opinion_editorial",
        ),
    ])
    def test_noise_and_commentary_rejected(
        self,
        base_article: Article,
        noisy_title: str,
        noisy_text: str,
        noise_category: str,
    ):
        """Test that analyst ratings, price targets, market wraps, calendars, intraday IPO updates, and opinions are rejected."""
        base_article.title = noisy_title
        base_article.content_text = noisy_text

        rule = StoryTypeFilterRule()
        result = rule.evaluate(base_article)
        assert result.is_accepted is False
        assert result.rule_failed == "STORY_TYPE"
        assert any(
            noise in (result.rejection_reason or "")
            for noise in (noise_category, "noise", "prohibited", "commentary")
        )

    @pytest.mark.parametrize("title", [
        "Who is Dali Rajic, OpenAI's new chief revenue officer?",
        "Nvidia's dependence on hyperscalers faces a big test in earnings report",
        "Federal Bank shares fall 4% as lender likely to acquire a rival",
    ])
    def test_profile_preview_and_speculative_transactions_rejected(self, base_article: Article, title: str):
        base_article.title = title
        base_article.content_text = "Detailed article content describing market developments and company commentary."

        result = StoryTypeFilterRule().evaluate(base_article)

        assert result.is_accepted is False
        assert result.rule_failed == "STORY_TYPE"

    def test_fallback_date_horizon_is_explicit_without_changing_default(self, base_article: Article):
        eval_time = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
        base_article.published_at = eval_time - timedelta(hours=30)
        rule = DateFilterRule()

        assert rule.evaluate(base_article, now_utc=eval_time).is_accepted is False
        assert rule.evaluate(base_article, now_utc=eval_time, max_age_hours=36).is_accepted is True

    @pytest.mark.parametrize("event_title, event_text", [
        (
            "HDFC Bank Q1 Net Profit Surges 18% to ₹16,175 Crore on Strong Net Interest Income",
            "HDFC Bank reported net profit of ₹16,175 crore for Q1, rising 18% YoY with stable asset quality.",
        ),
        (
            "Rio Tinto Agrees $6.7 Billion Acquisition of Arcadium Lithium in All-Cash Deal",
            "Mining titan Rio Tinto has signed a definitive agreement to acquire Arcadium Lithium for $6.7 billion.",
        ),
        (
            "Reliance Retail Launches ₹8,500 Crore QIP to Expand Logistics Footprint",
            "Reliance Retail Ventures opened its Qualified Institutional Placement (QIP) with floor price of ₹1,420.",
        ),
        (
            "RBI Imposes ₹2.5 Crore Penalty on Leading NBFC for Regulatory Violations",
            "The Reserve Bank of India announced a monetary penalty order of ₹2.5 crore for compliance lapses.",
        ),
        (
            "L&T Secures Mega ₹4,200 Crore EPC Contract in Middle East",
            "The hydrocarbon division of Larsen & Toubro has bagged an offshore gas processing contract worth ₹4,200 crore.",
        ),
        (
            "Infosys Appoints New Chief Financial Officer Following Board Approval",
            "Infosys on Tuesday announced that its board has appointed a new CFO effective immediately.",
        ),
        (
            "India July CPI Inflation Cools to 3.84%, Dropping Below RBI 4% Target",
            "Retail inflation based on the consumer price index eased to 3.84% in July, government data showed.",
        ),
        (
            "Goldman Sachs to buy LCN Capital Partners in up to $410 million deal",
            "Goldman Sachs Asset Management announced an agreement to buy real estate firm LCN Capital Partners in a deal worth up to $410 million.",
        ),
        (
            "Stripe acquires AI model routing startup OpenRouter",
            "Fintech company Stripe has acquired AI routing provider OpenRouter to expand developer tools.",
        ),
        (
            "Ola Electric files for IPO with SEBI to raise ₹5,500 crore",
            "EV maker Ola Electric has filed draft red herring prospectus for its initial public offering.",
        ),
        (
            "Tata Steel approves ₹2,000 crore plant investment and capacity expansion",
            "Tata Steel board approved strategic plant investment and capacity expansion in Odisha.",
        ),
        (
            "Infosys declares interim dividend of ₹28 per share and announces ₹9,300 crore share buyback",
            "The board approved an interim dividend and a major share buyback program.",
        ),
        (
            "Tech Mahindra revenue rises 4.5% as margins improve",
            "Tech Mahindra posted higher quarterly revenue driven by enterprise software deal wins.",
        ),
    ])
    def test_hard_business_events_accepted(
        self,
        base_article: Article,
        event_title: str,
        event_text: str,
    ):
        """Test that genuine hard business events are accepted."""
        base_article.title = event_title
        base_article.content_text = event_text

        rule = StoryTypeFilterRule()
        result = rule.evaluate(base_article)
        assert result.is_accepted is True, f"Failed for '{event_title}': {result.rejection_reason}"


class TestHardFilterEngine:
    """Tests for HardFilterEngine batch coordinator."""

    def test_filter_candidates_batch(self, base_article: Article):
        """Test batch processing separating valid articles from rejected articles."""
        # 1. Valid article
        valid_art = base_article.model_copy(deep=True)
        valid_art.published_at = datetime.now(timezone.utc) - timedelta(hours=2)

        # 2. Stale article (>72h)
        stale_art = base_article.model_copy(deep=True)
        stale_art.published_at = datetime.now(timezone.utc) - timedelta(hours=80)

        # 3. Analyst upgrade noise article
        noise_art = base_article.model_copy(deep=True)
        noise_art.published_at = datetime.now(timezone.utc) - timedelta(hours=2)
        noise_art.title = "Jefferies Upgrades HDFC Bank to Buy with Target of Rs 2000"

        # 4. Non-article topic URL
        topic_art = base_article.model_copy(deep=True)
        topic_art.published_at = datetime.now(timezone.utc) - timedelta(hours=2)
        topic_art.url = "https://economictimes.indiatimes.com/topic/banking"

        engine = HardFilterEngine()
        accepted, rejections = engine.filter_candidates([valid_art, stale_art, noise_art, topic_art])

        assert len(accepted) == 1
        assert accepted[0].title == valid_art.title

        assert len(rejections) == 3
        failed_rules = {r.rule_failed for r in rejections}
        assert "DATE" in failed_rules
        assert "STORY_TYPE" in failed_rules
        assert "URL" in failed_rules
