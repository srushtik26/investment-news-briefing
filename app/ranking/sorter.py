"""
Candidate Pool Ranking and Sorting Service.

Sorts verified events separately for India and International sections,
and selects the top 8–10 scored candidates for each pool.
"""

import re
from typing import List, Optional, Tuple

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

        domestic_scored: List[ScoredEvent] = []
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

            # Separate into Domestic, India, and International
            cat = event.event_category
            if cat == NewsCategory.DOMESTIC:
                domestic_scored.append(scored)
            elif cat == NewsCategory.INDIA:
                india_scored.append(scored)
            else:
                intl_scored.append(scored)

        from app.models.enums import VerificationTier

        def _ranking_key(s: ScoredEvent):
            tier = getattr(s.event, "verification_tier", None)
            is_two_source = 1 if (tier == VerificationTier.TWO_SOURCE_VERIFIED) else 0
            return (is_two_source, s.investment_score)

        # Sort descending: two-source verified first, then by investment_score
        domestic_sorted = sorted(domestic_scored, key=_ranking_key, reverse=True)
        india_sorted = sorted(india_scored, key=_ranking_key, reverse=True)
        intl_sorted = sorted(intl_scored, key=_ranking_key, reverse=True)

        # Assign ranks
        for idx, scored in enumerate(domestic_sorted, 1):
            scored.rank = idx
        for idx, scored in enumerate(india_sorted, 1):
            scored.rank = idx
        for idx, scored in enumerate(intl_sorted, 1):
            scored.rank = idx

        # Select top 8–10 candidates for each section (without selecting final 5 yet)
        top_domestic = domestic_sorted[:top_n]
        top_india = india_sorted[:top_n]
        top_intl = intl_sorted[:top_n]

        logger.info(
            "Ranking complete: Kept top %d Domestic (out of %d), top %d India (out of %d), and top %d International (out of %d)",
            len(top_domestic),
            len(domestic_scored),
            len(top_india),
            len(india_scored),
            len(top_intl),
            len(intl_scored),
        )

        return RankedCandidatePool(
            domestic_candidates=top_domestic,
            india_candidates=top_india,
            international_candidates=top_intl,
            total_evaluated=len(events),
        )


def select_diverse_domestic_candidates(
    domestic_candidates: List[ScoredEvent],
    articles_lookup: Optional[dict] = None,
    target_count: int = 5,
    max_court_stories: int = 1,
    max_same_topic: int = 2,
) -> List[ScoredEvent]:
    """
    Select top domestic candidate stories enforcing topic diversity and duplicate-event suppression.

    Rules:
    1. Deduplicate: If multiple candidates cover the same underlying event, keep only higher-ranked one.
    2. Topic Priority & Diversity:
       - Priority order: Major economic crises / Cabinet policy / Election results (VERY HIGH +20),
         macro economy / infrastructure / defence / landmark courts (HIGH +10 to +15),
         science / ISRO / education / environment (NORMAL +0), routine court (LOW -20).
       - Maximum 1 story from COURT_JUDICIARY in Pass 1.
       - Prefer diverse topics (politics, economy, government, security, infrastructure, science).
       - If enough eligible candidates exist, do not allow one topic category to dominate (>2 stories).
    3. Quality Precedence:
       - If fewer than 5 stories qualify across distinct topics, remaining slots are filled
         from the next best available quality candidates (Pass 2 backfill).
       - Never fail or reduce the section count below 5 because of diversity if total qualified candidates >= 5.
    """
    if not domestic_candidates:
        return []

    articles_map = articles_lookup or {}

    # Step 1: Duplicate Event Suppression across candidate events
    from app.verification.verifier import TwoSourceVerifier
    verifier = TwoSourceVerifier()
    
    deduped: List[ScoredEvent] = []
    for scored in domestic_candidates:
        ev = scored.event
        art = articles_map.get(ev.article_ids[0]) if ev.article_ids else None
        
        is_duplicate = False
        for ex in deduped:
            ex_ev = ex.event
            ex_art = articles_map.get(ex_ev.article_ids[0]) if ex_ev.article_ids else None
            if art and ex_art:
                if verifier.is_same_underlying_event(art, ex_art)[0]:
                    is_duplicate = True
                    break
        if not is_duplicate:
            deduped.append(scored)

    # Step 2: Classify Topics and Apply Domestic Quality Adjustment
    from app.verification.domestic_trending import (
        classify_domestic_topic,
        DomesticTopic,
        LANDMARK_COURT_IMPACT_PATTERNS,
    )
    
    candidate_topics = []
    for scored in deduped:
        ev = scored.event
        art = articles_map.get(ev.article_ids[0]) if ev.article_ids else None
        title = ev.canonical_title or (art.title if art else "")
        body = (art.content_text if art else "") or ev.description or ""
        topic = classify_domestic_topic(title, body)
        ev.metadata = getattr(ev, "metadata", {}) or {}
        ev.metadata["domestic_topic"] = topic.value

        # Deterministic domestic priority weighting:
        # VERY HIGH (+20.0): major economic crisis/disruption, major Cabinet/Parliament policy, major election/political development, major security crisis, severe disaster
        # HIGH (+10.0 to +15.0): macro economy / inflation / GDP / fiscal / jobs, major strategic infrastructure, major public health, landmark court ruling (+10.0)
        # NORMAL (+0.0): science/ISRO, education, environment
        # LOW (-20.0): routine court/judiciary
        comb = f"{title} {body[:400]}".lower()
        quality_adjustment = 0.0

        if re.search(r"\b(\d+\s+questions|\d+\s+bills|assembly session agenda)\b", comb):
            quality_adjustment -= 25.0

        is_landmark_court = any(re.search(pat, comb) for pat in LANDMARK_COURT_IMPACT_PATTERNS)

        if topic == DomesticTopic.COURT_JUDICIARY:
            if is_landmark_court:
                quality_adjustment += 10.0  # Landmark court rulings move back to HIGH
            else:
                quality_adjustment -= 20.0  # Routine court de-prioritized
        elif topic == DomesticTopic.ECONOMY_NATIONAL:
            is_crisis = bool(re.search(
                r"\b(economic crisis|recession|slowdown|inflation shock|rupee (?:crash|slump|plunge)|banking crisis|liquidity crisis|nationwide strike|bharat bandh|power crisis|energy crisis|supply disruption|tariff shock)\b",
                comb
            ))
            quality_adjustment += (20.0 if is_crisis else 15.0)
        elif topic in (DomesticTopic.GOVERNMENT_POLICY, DomesticTopic.POLITICS_ELECTIONS):
            is_major_event = bool(re.search(
                r"\b(union cabinet|cabinet approves|cabinet clears|parliament passes|new national law|notifies act|election result|election commission announces|government formation|coalition formed|cabinet reshuffle)\b",
                comb
            ))
            quality_adjustment += (20.0 if is_major_event else 12.0)
        elif topic == DomesticTopic.DEFENCE_SECURITY:
            is_security_crisis = bool(re.search(r"\b(terror attack|border clash|standoff|missile test|warship commissioned|security alert)\b", comb))
            quality_adjustment += (20.0 if is_security_crisis else 10.0)
        elif topic == DomesticTopic.WEATHER_DISASTER:
            is_severe_disaster = bool(re.search(r"\b(red alert|cyclone hits|landslide kills|flood havoc|cloudburst|earthquake|ndrf deployed)\b", comb))
            quality_adjustment += (20.0 if is_severe_disaster else 5.0)
        elif topic == DomesticTopic.INFRASTRUCTURE:
            quality_adjustment += 10.0
        elif topic == DomesticTopic.HEALTH:
            is_public_health = bool(re.search(r"\b(outbreak|epidemic|ayushman bharat|health mission|who alert)\b", comb))
            quality_adjustment += (10.0 if is_public_health else 5.0)
        elif topic in (DomesticTopic.SCIENCE_ISRO_TECH, DomesticTopic.EDUCATION, DomesticTopic.ENVIRONMENT):
            quality_adjustment += 0.0
        else:
            quality_adjustment += 0.0

        candidate_topics.append((scored, topic, quality_adjustment))

    # Sort candidates by (adjusted_score, rank)
    candidate_topics.sort(
        key=lambda x: (x[0].investment_score + x[2]),
        reverse=True,
    )

    # Step 3: Pass 1 — Diversity-aware Selection
    selected: List[ScoredEvent] = []
    selected_topic_counts: dict[DomesticTopic, int] = {}
    remaining_pool: List[ScoredEvent] = []

    for scored, topic, _ in candidate_topics:
        court_count = selected_topic_counts.get(DomesticTopic.COURT_JUDICIARY, 0)
        topic_count = selected_topic_counts.get(topic, 0)

        # Cap COURT_JUDICIARY at max_court_stories (default 2)
        if topic == DomesticTopic.COURT_JUDICIARY and court_count >= max_court_stories:
            remaining_pool.append(scored)
            continue

        # Cap other categories at max_same_topic if other candidates exist
        if topic_count >= max_same_topic:
            remaining_pool.append(scored)
            continue

        selected.append(scored)
        selected_topic_counts[topic] = topic_count + 1

        if len(selected) == target_count:
            break

    # Step 4: Pass 2 — Quality Fallback (Quality outranks diversity)
    if len(selected) < target_count:
        for scored in remaining_pool:
            if scored not in selected:
                selected.append(scored)
                if len(selected) == target_count:
                    break

    # Re-assign ranks 1..N
    for rank, scored in enumerate(selected, 1):
        scored.rank = rank

    return selected


def select_diverse_topic_candidates(
    candidates: List[ScoredEvent],
    articles_lookup: Optional[dict] = None,
    target_count: int = 5,
    max_per_topic: int = 2,
) -> List[ScoredEvent]:
    """
    Select candidate stories enforcing soft business topic diversity.

    PASS 1: Take the highest-quality story from each distinct topic bucket.
    PASS 2: If fewer than target_count (5) distinct topics exist, allow up to
            max_per_topic (2) stories from already-used topics.
    PASS 3 (Quality Backfill): If still under target_count, backfill from remaining
            candidates in quality order so the section NEVER drops below target_count.
    """
    if not candidates:
        return []

    articles_map = articles_lookup or {}
    from app.ranking.topic_classifier import classify_topic_bucket

    candidate_with_topics: List[Tuple[ScoredEvent, str]] = []
    for scored in candidates:
        ev = scored.event
        meta = getattr(ev, "metadata", {}) or {}
        topic = meta.get("topic_bucket")
        if not topic:
            art = articles_map.get(ev.article_ids[0]) if ev.article_ids else None
            title = ev.canonical_title or (art.title if art else "")
            body = (art.content_text if art else "") or ev.description or ""
            topic = classify_topic_bucket(headline=title, body=body)
            ev.metadata = getattr(ev, "metadata", {}) or {}
            ev.metadata["topic_bucket"] = topic
        candidate_with_topics.append((scored, topic))

    selected: List[ScoredEvent] = []
    selected_topics: dict[str, int] = {}
    deferred_pool: List[Tuple[ScoredEvent, str]] = []

    # Pass 1: One story per distinct topic bucket
    for scored, topic in candidate_with_topics:
        if topic not in selected_topics:
            selected.append(scored)
            selected_topics[topic] = 1
            if len(selected) == target_count:
                break
        else:
            deferred_pool.append((scored, topic))

    # Pass 2: Allow up to max_per_topic (default 2)
    if len(selected) < target_count:
        still_deferred: List[ScoredEvent] = []
        for scored, topic in deferred_pool:
            if selected_topics.get(topic, 0) < max_per_topic:
                selected.append(scored)
                selected_topics[topic] = selected_topics.get(topic, 0) + 1
                if len(selected) == target_count:
                    break
            else:
                still_deferred.append(scored)

        # Pass 3: Backfill from remaining candidates in quality order if still under target_count
        if len(selected) < target_count:
            for scored in still_deferred:
                if scored not in selected:
                    selected.append(scored)
                    if len(selected) == target_count:
                        break

    # Re-assign ranks 1..N
    for rank, scored in enumerate(selected, 1):
        scored.rank = rank

    return selected


def select_diverse_publisher_candidates(
    candidates: List[ScoredEvent],
    articles_lookup: Optional[dict] = None,
    target_count: int = 5,
    max_per_publisher: int = 2,
) -> List[ScoredEvent]:
    """
    Apply a SOFT publisher-diversity preference to ranked candidates within a section.

    Target:
        Prefer maximum 2 stories from the same publisher within each section
        WHEN equally qualified alternatives exist.

    Important:
        This is NOT a hard cap.
        If only 5 legitimate stories exist and 3 come from one publisher, keep all 5.
        Never replace a strong hard event with a weaker story merely for diversity.

    Ranking preference order:
        1. hard-event quality
        2. verification tier
        3. freshness
        4. relevance
        5. publisher diversity (tie-breaker / secondary ranking preference)
    """
    if not candidates:
        return []

    articles_map = articles_lookup or {}

    def _get_publisher_key(scored: ScoredEvent) -> str:
        ev = scored.event
        art = articles_map.get(ev.article_ids[0]) if ev.article_ids else None
        pub = ev.primary_publisher or (art.source_name if art else "")
        p_clean = pub.lower().replace("www.", "").strip()
        for brand in (
            "business standard", "times of india", "economic times", "cnbc",
            "reuters", "bloomberg", "the hindu", "mint", "livemint",
            "financial express", "moneycontrol", "ndtv", "indian express",
            "ap news", "ap", "bbc", "fortune", "marketwatch", "wall street journal", "wsj"
        ):
            if brand in p_clean:
                return brand
        return p_clean or "unknown_publisher"

    selected: List[ScoredEvent] = []
    publisher_counts: dict[str, int] = {}
    deferred_pool: List[ScoredEvent] = []

    # Pass 1: Select candidates respecting max_per_publisher
    for scored in candidates:
        pub_key = _get_publisher_key(scored)
        count = publisher_counts.get(pub_key, 0)
        if count < max_per_publisher:
            selected.append(scored)
            publisher_counts[pub_key] = count + 1
            if len(selected) == target_count:
                break
        else:
            deferred_pool.append(scored)

    # Pass 2: Backfill from deferred candidates in quality order if target_count not yet met
    if len(selected) < target_count:
        for scored in deferred_pool:
            if scored not in selected:
                selected.append(scored)
                if len(selected) == target_count:
                    break

    # Re-assign ranks 1..N
    for rank, scored in enumerate(selected, 1):
        scored.rank = rank

    return selected

