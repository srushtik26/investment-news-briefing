"""
Candidate Pool Ranking and Sorting Service.

Sorts verified events separately for India and International sections,
and selects the top 8–10 scored candidates for each pool.
"""

from typing import List, Optional

from app.logging_config import get_logger
from app.models.enums import NewsCategory
from app.models.event import Event
from app.ranking.models import RankedCandidatePool, ScoredEvent
from app.ranking.scorer import InvestmentRelevanceScorer

logger = get_logger("ranking.sorter")


class CandidatePoolRanker:
    """
    Ranks verified events by investment relevance and maintains top 8–10 candidate pools.
    """

    def __init__(self, scorer: Optional[InvestmentRelevanceScorer] = None) -> None:
        self.scorer = scorer or InvestmentRelevanceScorer()

    def rank_events(
        self,
        events: List[Event],
        top_n: int = 10,
    ) -> RankedCandidatePool:
        """
        Score and rank events separately into India and International pools.

        Args:
            events: List of verified Event model instances.
            top_n: Maximum candidate pool size per section (default: 10, typical range: 8–10).

        Returns:
            RankedCandidatePool containing top 8–10 candidates per region.
        """
        logger.info("Ranking %d verified events into candidate pools (top %d per section)...", len(events), top_n)

        india_scored: List[ScoredEvent] = []
        intl_scored: List[ScoredEvent] = []

        for event in events:
            # Score the event
            source_count = len(event.article_ids) or 2
            scored = self.scorer.score_event(
                event=event,
                source_count=source_count,
                is_multi_source_verified=source_count >= 2,
            )

            # Separate into India and International
            cat = event.event_category
            if cat == NewsCategory.INDIA:
                india_scored.append(scored)
            else:
                intl_scored.append(scored)

        # Sort descending by investment_score
        india_sorted = sorted(india_scored, key=lambda s: s.investment_score, reverse=True)
        intl_sorted = sorted(intl_scored, key=lambda s: s.investment_score, reverse=True)

        # Assign ranks
        for idx, scored in enumerate(india_sorted, 1):
            scored.rank = idx
        for idx, scored in enumerate(intl_sorted, 1):
            scored.rank = idx

        # Select top 8–10 candidates for each section (without selecting final 5 yet)
        top_india = india_sorted[:top_n]
        top_intl = intl_sorted[:top_n]

        logger.info(
            "Ranking complete: Kept top %d India candidates (out of %d) and top %d International candidates (out of %d)",
            len(top_india),
            len(india_scored),
            len(top_intl),
            len(intl_scored),
        )

        return RankedCandidatePool(
            india_candidates=top_india,
            international_candidates=top_intl,
            total_evaluated=len(events),
        )
