"""
Unit test for candidate publication date propagation in ArticleExtractor.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.extraction.extractor import ArticleExtractor
from app.extraction.html_parser import ParsedArticleData


def test_candidate_pub_date_propagated_when_html_has_no_meta_date():
    """Verify candidate_pub_date is preserved when extract() delegates to extract_from_html()."""
    # Sample HTML without any date meta tags or time elements
    html_without_date = """
    <html>
      <head><title>Test Business Results Article</title></head>
      <body>
        <article>
          <h1>Company Reports 25% Increase in Quarterly Revenue</h1>
          <p>Company XYZ announced today that its net profit for the first quarter rose by 25 percent compared to last year.</p>
          <p>The strong performance was driven by robust consumer demand and cost optimization initiatives across key markets.</p>
        </article>
      </body>
    </html>
    """

    fetcher = MagicMock()
    fetcher.fetch_html.return_value = (True, html_without_date, 200, None)

    resolver = MagicMock()
    resolver.resolve.return_value = MagicMock(
        is_google_news=False,
        success=True,
        resolved_url="https://www.business-standard.com/companies/news/test-article.html",
    )
    resolver.is_google_news_url.return_value = False

    extractor = ArticleExtractor(fetcher=fetcher, resolver=resolver)

    expected_pub_date = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)

    result = extractor.extract(
        url="https://www.business-standard.com/companies/news/test-article.html",
        source_name="Business Standard",
        candidate_title="Test Business Results Article",
        candidate_category="India",
        candidate_pub_date=expected_pub_date,
    )

    assert result.success is True
    assert result.article is not None
    assert result.article.published_at == expected_pub_date
    assert result.article.date_verified is True
    assert result.article.is_valid_date is True
