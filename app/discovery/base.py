"""
Discovery Provider Interface.

Defines the abstract interface for news search/feed providers, allowing
pluggable discovery implementations (e.g., Mock, Google News RSS, Bing, NewsAPI).
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from app.discovery.models import DiscoveredArticle


class DiscoveryProvider(ABC):
    """
    Abstract base class for all news discovery providers.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the discovery provider implementation."""
        pass

    @abstractmethod
    def discover(
        self,
        query: str,
        country: str,
        max_results: int = 10,
        category_tag: Optional[str] = None,
    ) -> List[DiscoveredArticle]:
        """
        Execute a search query and return candidate articles.

        Args:
            query: The search term or feed query string.
            country: 'India' or 'International'.
            max_results: Maximum candidate articles to return.
            category_tag: Optional business category tag for classification.

        Returns:
            List of DiscoveredArticle instances with exact source URLs.
        """
        pass
