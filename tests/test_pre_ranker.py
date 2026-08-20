"""
Unit tests for deterministic pre-classification candidate ranker.
"""

from datetime import datetime, timezone
import pytest

from app.models.article import Article
from app.models.enums import NewsCategory
from app.ranking.pre_ranker import ArticlePreRanker


@pytest.fixture
def strong_earnings_article() -> Article:
    """Strong India earnings article with financial figures and tier-1 publisher."""
    return Article(
        title="HDFC Bank Q1 Net Profit Surges 18% YoY to ₹16,175 Crore on Strong NII",
        url="https://www.business-standard.com/hdfc-q1-results",
        source_name="Business Standard",
        published_at=datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc),
        content_text="HDFC Bank reported an 18% YoY rise in standalone net profit to ₹16,175 crore.",
        category=NewsCategory.INDIA,
    )


@pytest.fixture
def generic_commentary_article() -> Article:
    """Generic commentary article with clickbait headline."""
    return Article(
        title="Top 5 Stocks to Watch Today: Why You Should Buy Shares Now",
        url="https://www.randomfinanceblog.com/top-stocks",
        source_name="Random Finance Blog",
        published_at=datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc),
        content_text="Here are top 5 stocks to watch in the market today.",
        category=NewsCategory.INDIA,
    )


def test_strong_candidate_ranks_above_generic_commentary(
    strong_earnings_article: Article, generic_commentary_article: Article
):
    """Test that a material earnings event ranks significantly above generic commentary."""
    ranker = ArticlePreRanker()
    score_strong = ranker.score_article(strong_earnings_article)
    score_generic = ranker.score_article(generic_commentary_article)

    assert score_strong > score_generic
    assert score_strong >= 70.0
    assert score_generic < 20.0

    selected = ranker.select_top_balanced_candidates(
        [generic_commentary_article, strong_earnings_article], max_total=2
    )
    assert selected[0].url == strong_earnings_article.url


def test_india_international_balance_preserved():
    """Test that candidate selection preserves balance between India and International sections."""
    ranker = ArticlePreRanker()

    india_articles = [
        Article(
            title=f"India Corporate Event {i} - Revenue Rises ₹{i*100} Crore",
            url=f"https://www.business-standard.com/india-{i}",
            source_name="Business Standard",
            category=NewsCategory.INDIA,
        )
        for i in range(1, 11)  # 10 India articles
    ]

    intl_articles = [
        Article(
            title=f"International Tech Merger {i} - $6.{i} Billion Deal",
            url=f"https://www.reuters.com/intl-{i}",
            source_name="Reuters",
            category=NewsCategory.INTERNATIONAL,
        )
        for i in range(1, 10)  # 9 Intl articles
    ]

    all_articles = india_articles + intl_articles
    selected = ranker.select_top_balanced_candidates(all_articles, max_total=15)

    assert len(selected) == 15
    india_selected = [a for a in selected if a.category == NewsCategory.INDIA]
    intl_selected = [a for a in selected if a.category == NewsCategory.INTERNATIONAL]

    # Target per section for max_total=15 is 8
    assert len(india_selected) == 8
    assert len(intl_selected) == 7


def test_gemini_input_count_never_exceeds_cap():
    """Test that selected candidates count never exceeds the configured max_total cap."""
    ranker = ArticlePreRanker()

    articles = [
        Article(
            title=f"News Event {i}",
            url=f"https://www.reuters.com/news-{i}",
            source_name="Reuters",
            category=NewsCategory.INDIA if i % 2 == 0 else NewsCategory.INTERNATIONAL,
        )
        for i in range(1, 31)  # 30 candidates
    ]

    selected_15 = ranker.select_top_balanced_candidates(articles, max_total=15)
    assert len(selected_15) == 15

    selected_5 = ranker.select_top_balanced_candidates(articles, max_total=5)
    assert len(selected_5) == 5
