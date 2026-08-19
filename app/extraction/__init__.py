"""
Article Extraction Package.

Provides robust HTML retrieval, structured metadata parsing (JSON-LD, OpenGraph, meta tags),
text cleaning, and date verification.
"""

from app.extraction.extractor import ArticleExtractor
from app.extraction.html_parser import HTMLArticleParser, ParsedArticleData
from app.extraction.http_client import ArticleFetcher
from app.extraction.models import ExtractionResult

__all__ = [
    "ArticleExtractor",
    "ArticleFetcher",
    "ExtractionResult",
    "HTMLArticleParser",
    "ParsedArticleData",
]
