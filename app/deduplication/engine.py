"""
Deduplication Engine.

Coordinates event deduplication, 3-day SQLite lookback history enforcement,
and India same-company restrictions.
"""

from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple

from app.logging_config import get_logger
from app.models.enums import NewsCategory
from app.deduplication.fingerprint import (
    generate_event_fingerprint,
    normalize_entity_name,
)
from app.deduplication.history import HistoryStore

logger = get_logger("deduplication.engine")


class DeduplicationEngine:
    """
    Coordinator executing 3-day historical lookback filtering and region-specific
    company deduplication rules.
    """

    def __init__(self, history_store: Optional[HistoryStore] = None) -> None:
        self.history_store = history_store or HistoryStore()

    def filter_stories(
        self,
        candidate_stories: List[Dict[str, Any]],
        target_date: Optional[date] = None,
        lookback_days: int = 3,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Filter candidate stories against historical 3-day records and intra-briefing constraints.

        Args:
            candidate_stories: List of story dictionaries with keys:
                - headline: str
                - company_name: str
                - event_type: str
                - category: NewsCategory or str ("india" or "international")
                - key_facts: Optional[List[str]]
                - event_id: Optional[str]
            target_date: Date of the current briefing.
            lookback_days: Number of previous days to inspect (default 3).

        Returns:
            Tuple of (accepted_stories, rejected_stories).
        """
        current_date = target_date or date.today()
        logger.info(
            "Running deduplication for %d stories on %s (lookback: %d days)...",
            len(candidate_stories),
            current_date,
            lookback_days,
        )

        # 1. Fetch historical fingerprints for previous 3 days
        historical_fps = self.history_store.get_recent_fingerprints(
            lookback_days=lookback_days,
            target_date=current_date,
        )

        accepted_stories: List[Dict[str, Any]] = []
        rejected_stories: List[Dict[str, Any]] = []

        selected_india_companies: Set[str] = set()
        selected_intl_fingerprints: Set[str] = set()

        for story in candidate_stories:
            company = story.get("company_name", "unspecified")
            event_type = story.get("event_type", "OTHER")
            category_raw = story.get("category", "india")
            category = category_raw.value if hasattr(category_raw, "value") else str(category_raw).lower()
            key_facts = story.get("key_facts", [])

            # Compute canonical fingerprint
            fp_key, fp_hash = generate_event_fingerprint(
                company=company,
                event_type=event_type,
                event_date=current_date,
                key_facts=key_facts,
            )
            story["event_fingerprint"] = fp_key
            story["fingerprint_hash"] = fp_hash

            # CHECK 1: Previous 3-Day History Lookback
            if fp_key in historical_fps:
                logger.info(
                    "Rejected story (3-day history repeat): '%s' [Fingerprint: %s]",
                    story.get("headline", "")[:45],
                    fp_key,
                )
                rejection = {
                    **story,
                    "rejection_reason": f"Event already appeared in briefing within previous {lookback_days} days ({fp_key})",
                    "rejection_rule": "3_DAY_HISTORY",
                }
                rejected_stories.append(rejection)
                continue

            # CHECK 2: India Same-Company Restriction
            if category == "india":
                all_raw_companies = story.get("all_companies") or [company]
                norm_companies = {
                    normalize_entity_name(c) for c in all_raw_companies
                    if normalize_entity_name(c) not in ("unspecified_entity", "unspecified", "unknown", "n_a", "na", "n/a", "none", "")
                }
                if norm_companies and norm_companies.intersection(selected_india_companies):
                    logger.info(
                        "Rejected India story (duplicate company in %s): '%s'",
                        norm_companies,
                        story.get("headline", "")[:45],
                    )
                    rejection = {
                        **story,
                        "rejection_reason": f"Same company ('{company}') already selected in today's India section",
                        "rejection_rule": "INDIA_SAME_COMPANY",
                    }
                    rejected_stories.append(rejection)
                    continue

                if norm_companies:
                    selected_india_companies.update(norm_companies)
                accepted_stories.append(story)
                logger.debug("Accepted India story for companies '%s'", norm_companies)

            # CHECK 3: International Section (Allow multiple companies, prevent duplicate event)
            else:
                if fp_key in selected_intl_fingerprints:
                    logger.info(
                        "Rejected International story (duplicate event): '%s'",
                        story.get("headline", "")[:45],
                    )
                    rejection = {
                        **story,
                        "rejection_reason": f"Duplicate international event in today's selection ({fp_key})",
                        "rejection_rule": "INTL_DUPLICATE_EVENT",
                    }
                    rejected_stories.append(rejection)
                    continue

                selected_intl_fingerprints.add(fp_key)
                accepted_stories.append(story)
                logger.debug("Accepted International story for '%s'", company)

        logger.info(
            "Deduplication complete: %d accepted, %d rejected",
            len(accepted_stories),
            len(rejected_stories),
        )
        return accepted_stories, rejected_stories
