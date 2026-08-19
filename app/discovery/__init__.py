"""
News Discovery Package.

Provides candidate news discovery interfaces and implementations for
Indian and International business media outlets.
"""

from app.discovery.base import DiscoveryProvider
from app.discovery.mock_provider import MockDiscoveryProvider, SAMPLE_MOCK_ARTICLES
from app.discovery.models import DiscoveredArticle
from app.discovery.queries import (
    INDIA_EVENT_CATEGORIES,
    INDIA_SOURCES,
    INTERNATIONAL_EVENT_CATEGORIES,
    INTERNATIONAL_SOURCES,
    SearchQueryBuilder,
    TargetSource,
)
from app.discovery.rss_provider import GoogleNewsRSSDiscoveryProvider
from app.discovery.service import NewsDiscoveryService

__all__ = [
    "DiscoveryProvider",
    "DiscoveredArticle",
    "GoogleNewsRSSDiscoveryProvider",
    "INDIA_EVENT_CATEGORIES",
    "INDIA_SOURCES",
    "INTERNATIONAL_EVENT_CATEGORIES",
    "INTERNATIONAL_SOURCES",
    "MockDiscoveryProvider",
    "NewsDiscoveryService",
    "SAMPLE_MOCK_ARTICLES",
    "SearchQueryBuilder",
    "TargetSource",
]
