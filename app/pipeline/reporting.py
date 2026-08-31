"""
Reporting and artifact logging helpers for pipeline executions.
"""

import json
from typing import Dict, List, Any, Optional

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.pipeline.context import PipelineContext


def print_candidate_audit(
    domestic_pool: List[Any],
    india_pool: List[Any],
    intl_pool: List[Any],
    articles_lookup: Dict[str, Article],
) -> None:
    """Print candidate audit manifest to standard output."""
    print("\n" + "=" * 60)
    print("=== VERIFIED CANDIDATE AUDIT ===")
    print("=" * 60)
    for scored in domestic_pool + india_pool + intl_pool:
        ev = scored.event
        sec_name = "DOMESTIC" if ev.event_category == NewsCategory.DOMESTIC else (
            "INDIA" if ev.event_category == NewsCategory.INDIA else "INTERNATIONAL"
        )
        tier_str = ev.verification_tier.value if ev.verification_tier else "TWO_SOURCE_VERIFIED"
        prim_pub = ev.primary_publisher or (articles_lookup[ev.article_ids[0]].source_name if ev.article_ids and ev.article_ids[0] in articles_lookup else "N/A")
        prim_u   = ev.primary_url or (articles_lookup[ev.article_ids[0]].url if ev.article_ids and ev.article_ids[0] in articles_lookup else "N/A")
        print(f"\n[{sec_name}] {ev.canonical_title}")
        print(f"  Tier:       {tier_str} (Confidence: {ev.verification_confidence:.1f}/100)")
        print(f"  Primary:    {prim_pub} ({prim_u})")
        if ev.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED and ev.secondary_publisher:
            print(f"  Secondary:  {ev.secondary_publisher} ({ev.secondary_url})")
        elif ev.verification_reason:
            print(f"  Reason:     {ev.verification_reason}")


def print_final_story_audit(
    all_final: List[Any],
    event_by_id: Dict[str, Event],
    articles_lookup: Dict[str, Article],
) -> None:
    """Print detailed audit of final stories."""
    if not all_final:
        return
    print("=== FINAL STORY AUDIT ===")
    for idx, story in enumerate(all_final, 1):
        ev = event_by_id.get(story.event_id)
        art1_id = ev.article_ids[0] if (ev and ev.article_ids) else None
        art2_id = ev.article_ids[1] if (ev and len(ev.article_ids) > 1 and ev.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED) else None
        art1 = articles_lookup.get(art1_id) if art1_id else None
        art2 = articles_lookup.get(art2_id) if art2_id else None
        orig = art1.metadata.get("original_url", story.url) if art1 else story.url
        freshness_score = art1.metadata.get("freshness_score", "N/A") if art1 else "N/A"
        freshness_bucket = art1.metadata.get("freshness_bucket", "N/A") if art1 else "N/A"
        age_hours = art1.metadata.get("age_hours", "N/A") if art1 else "N/A"
        tier_val = ev.verification_tier.value if (ev and ev.verification_tier) else "UNKNOWN"

        print(f"\n[{idx}] SECTION: {story.section.upper()}")
        print(f"  TITLE:               {story.headline}")
        print(f"  PUBLISHER:           {story.source}")
        print(f"  ORIGINAL RSS URL:    {orig}")
        print(f"  RESOLVED URL:        {story.url}")
        print(f"  EVENT TYPE:          {ev.event_category.value if ev else 'N/A'}")
        print(f"  VERIFICATION TIER:   {tier_val}")
        print(f"  FRESHNESS:           {freshness_bucket} ({age_hours}h, score={freshness_score})")
        if art1:
            print(f"  SOURCE 1:")
            print(f"    Publisher:         {art1.source_name}")
            print(f"    URL:               {art1.url}")
            print(f"    Date:              {art1.published_at.isoformat() if art1.published_at else 'N/A'}")
        if art2 and ev and ev.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED:
            print(f"  SOURCE 2:")
            print(f"    Publisher:         {art2.source_name}")
            print(f"    URL:               {art2.url}")
            print(f"    Date:              {art2.published_at.isoformat() if art2.published_at else 'N/A'}")


def save_json_artifact(file_path, data: Any) -> None:
    """Safely persist JSON artifact with UTF-8 encoding."""
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
