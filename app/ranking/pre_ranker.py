"""
Deterministic Pre-Classification Candidate Ranker.

Ranks raw accepted articles BEFORE sending them to Gemini AI classification.
Ensures limited free Gemini quota is spent on the highest-quality, most material
business news events while preserving balanced representation between India
and International sections.
"""

import re
from datetime import datetime, timezone
from typing import List, Tuple
from urllib.parse import urlparse

from app.logging_config import get_logger
from app.models.article import Article
from app.models.enums import NewsCategory

logger = get_logger("ranking.pre_ranker")

TIER_1_PUBLISHERS = {
    "reuters.com", "bloomberg.com", "economictimes.indiatimes.com",
    "business-standard.com", "livemint.com", "financialexpress.com",
    "moneycontrol.com", "cnbc.com", "wsj.com", "ft.com", "marketwatch.com",
}

TIER_2_PUBLISHERS = {
    "apnews.com", "bbc.com", "theguardian.com", "businesstoday.in",
    "ndtvprofit.com", "thehindu.com", "indianexpress.com", "fortune.com",
}

GENERIC_COMMENTARY_PATTERNS = [
    r"\btop\s+\d+\b",
    r"\bstocks?\s+to\s+watch\b",
    r"\bwhy\s+you\s+should\b",
    r"\bhow\s+to\b",
    r"\bopinion\b",
    r"\beditorial\b",
    r"\bview:\b",
    r"\bmarket\s+wrap\b",
    r"\bmarket\s+live\b",
    r"\bstock\s+market\s+today\b",
]

HIGH_IMPACT_EVENT_KEYWORDS = [
    "profit", "revenue", "earnings", "q1", "q2", "q3", "q4", "net profit",
    "acquisition", "acquires", "merger", "demerger", "takeover", "buyout",
    "qip", "ipo", "drhp", "buyback", "capital raise", "surges", "jumps", "soars",
]

MEDIUM_IMPACT_EVENT_KEYWORDS = [
    "penalty", "fine", "sebi", "rbi", "fed", "cpi", "gdp", "inflation",
    "contract", "order", "joint venture", "appoints", "resigns", "ceo", "cfo",
    "expansion", "capex", "plant",
]


class ArticlePreRanker:
    """
    Ranks accepted candidate articles deterministically prior to LLM classification.
    """

    def score_article(self, article: Article) -> float:
        """
        Compute a 0-100 quality/materiality score for a candidate article.
        """
        title = article.title or ""
        snippet = (article.content_text or "")[:300]
        combined = f"{title} {snippet}".lower()

        score = 0.0

        # 1. Headline Specificity & Generic Commentary Penalty (-25 or +10)
        is_generic = any(re.search(pat, combined) for pat in GENERIC_COMMENTARY_PATTERNS)
        if is_generic:
            score -= 25.0
        else:
            score += 10.0

        # 2. Financial Numbers & Deal Values (+25 pts)
        has_curr = bool(re.search(r"(?:₹|\$|rs\.?\s*)", combined))
        has_scale = bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:crore|cr|billion|b|million|m|%)\b", combined, re.IGNORECASE))
        if has_curr and has_scale:
            score += 25.0
        elif has_scale or has_curr:
            score += 15.0
        elif any(c.isdigit() for c in title):
            score += 5.0

        # 3. Hard Business Event Keywords (+30 pts)
        if any(kw in combined for kw in HIGH_IMPACT_EVENT_KEYWORDS):
            score += 30.0
        elif any(kw in combined for kw in MEDIUM_IMPACT_EVENT_KEYWORDS):
            score += 15.0

        # 4. Entity & Company Presence (+20 pts)
        proper = re.findall(r"\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*\b", title)
        acronyms = re.findall(r"\b[A-Z]{2,}\b", title)
        if proper or acronyms:
            score += 20.0

        # 5. Publisher Pedigree (+15 pts)
        netloc = urlparse(article.url).netloc.lower().replace("www.", "")
        if netloc in TIER_1_PUBLISHERS:
            score += 15.0
        elif netloc in TIER_2_PUBLISHERS:
            score += 10.0

        # 6. Freshness (+10 pts)
        if article.published_at:
            now = datetime.now(timezone.utc)
            pub = article.published_at if article.published_at.tzinfo else article.published_at.replace(tzinfo=timezone.utc)
            age_hours = (now - pub).total_seconds() / 3600.0
            if age_hours <= 24:
                score += 10.0
            elif age_hours <= 48:
                score += 5.0

        return max(0.0, min(100.0, score))

    def select_top_balanced_candidates(
        self,
        articles: List[Article],
        max_total: int = 15,
    ) -> List[Article]:
        """
        Sort articles deterministically by score and select a section-balanced subset
        of up to `max_total` articles (e.g., 8 India + 7 Intl for max_total=15).

        Returns:
            List of selected Article objects, ordered by rank.
        """
        if not articles:
            return []

        india_scored: List[Tuple[float, Article]] = []
        intl_scored: List[Tuple[float, Article]] = []

        for art in articles:
            sc = self.score_article(art)
            if art.category == NewsCategory.INDIA:
                india_scored.append((sc, art))
            else:
                intl_scored.append((sc, art))

        # Sort descending by score
        india_scored.sort(key=lambda x: x[0], reverse=True)
        intl_scored.sort(key=lambda x: x[0], reverse=True)

        target_per_section = max(1, (max_total + 1) // 2)  # e.g. 8 for max_total=15

        selected_india = india_scored[:target_per_section]
        selected_intl = intl_scored[:target_per_section]

        # Handle overflow if one section has fewer candidates than target_per_section
        remaining_cap = max_total - len(selected_india) - len(selected_intl)
        if remaining_cap > 0:
            if len(selected_india) < target_per_section and len(intl_scored) > len(selected_intl):
                extra_intl = intl_scored[len(selected_intl): len(selected_intl) + remaining_cap]
                selected_intl.extend(extra_intl)
            elif len(selected_intl) < target_per_section and len(india_scored) > len(selected_india):
                extra_india = india_scored[len(selected_india): len(selected_india) + remaining_cap]
                selected_india.extend(extra_india)

        # Merge selected candidates and sort overall by score descending
        combined = selected_india + selected_intl
        combined.sort(key=lambda x: x[0], reverse=True)

        selected_articles = [art for sc, art in combined[:max_total]]

        logger.info(
            "Pre-classification candidate selection: %d total accepted → selected %d (%d India, %d Intl) for Gemini stage",
            len(articles),
            len(selected_articles),
            sum(1 for a in selected_articles if a.category == NewsCategory.INDIA),
            sum(1 for a in selected_articles if a.category == NewsCategory.INTERNATIONAL),
        )

        return selected_articles
