"""
Discovery stage: initial candidate reserve pool loading and query scoring.
"""

import re
from typing import List, Tuple, Any

from app.pipeline.context import PipelineContext

DISCOVERY_STEPS = [20, 30, 40, 50]  # candidate budgets per section per expansion step


def _score_discovery_candidate(title: str) -> float:
    """Score candidate discovery headline to prioritize corporate actions and hard events."""
    t_low = title.lower()
    score = 50.0
    if re.search(r"\b(crore|cr|billion|million|\$|₹|\d+%)\b", t_low):
        score += 20.0
    if re.search(r"\b(acquires?|acquisition|stake|block deal|bought|buys|takeover|merger|amalgamation)\b", t_low):
        score += 25.0
    if re.search(r"\b(net profit|q[1-4] results|earnings|ebitda|quarterly profit)\b", t_low):
        score += 20.0
    if re.search(r"\b(ipo|drhp|anchor investors|raises funding|funding round)\b", t_low):
        score += 20.0
    if re.search(r"\b(rbi|sebi|cci|penalty|order|fine|probe)\b", t_low):
        score += 20.0
    if re.search(r"\b(stock to buy|target price|brokerage|recommendation|share price today|market wrap|sensex|nifty|live updates)\b", t_low):
        score -= 40.0
    return score


def get_fallback_search_window(expansion_pass: int = 1) -> str:
    """Return the search window for discovery queries (strictly when:1d)."""
    return "when:1d"


def discover_initial_reserves(
    ctx: PipelineContext,
    max_domestic: int,
    max_india: int,
    max_international: int,
) -> Tuple[List[Tuple[Any, str]], int, int, int]:
    """
    Fetch initial discovery reserve pools across Domestic, India, and International.
    Returns (pass1_candidates, initial_dom, initial_india, initial_intl).
    """
    ctx.metrics.start_timer("discovery_seconds")
    ctx.log_exec(f"Fetching discovery reserve pools (up to {max_domestic} Domestic, {max_india} India, {max_international} International)...")
    initial_discovery = ctx.discovery_service.discover_all(
        max_india=max_india,
        max_international=max_international,
        max_domestic=max_domestic,
    )
    ctx.metrics.stop_timer("discovery_seconds")

    ctx.domestic_reserve_pool = initial_discovery.get("domestic", [])
    ctx.india_reserve_pool    = initial_discovery.get("india", [])
    ctx.intl_reserve_pool     = initial_discovery.get("international", [])

    discovered_total = len(ctx.domestic_reserve_pool) + len(ctx.india_reserve_pool) + len(ctx.intl_reserve_pool)
    ctx.log_exec(f"Discovery Reserve Pool loaded: {len(ctx.domestic_reserve_pool)} Domestic + {len(ctx.india_reserve_pool)} India Business + {len(ctx.intl_reserve_pool)} International (Total: {discovered_total})")

    # Pass 1: Extract top candidates from reserve pool (e.g. up to 20 each)
    initial_dom      = min(len(ctx.domestic_reserve_pool), DISCOVERY_STEPS[0])
    initial_india    = min(len(ctx.india_reserve_pool), DISCOVERY_STEPS[0])
    initial_intl     = min(len(ctx.intl_reserve_pool), DISCOVERY_STEPS[0])

    pass1_candidates = (
        [(c, "domestic") for c in ctx.domestic_reserve_pool[:initial_dom]] +
        [(c, "india") for c in ctx.india_reserve_pool[:initial_india]] +
        [(c, "international") for c in ctx.intl_reserve_pool[:initial_intl]]
    )

    return pass1_candidates, initial_dom, initial_india, initial_intl
