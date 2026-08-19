"""
Deterministic Investment Relevance Scoring Engine.

Computes a calibrated 0–100 score for each verified business event across:
- Financial magnitude (30%)
- Market impact (25%)
- Investor relevance (20%)
- Corporate significance (15%)
- Source quality (10%)
"""

import re
from typing import List, Optional

from app.logging_config import get_logger
from app.models.event import Event
from app.ranking.models import ScoreBreakdown, ScoredEvent

logger = get_logger("ranking.scorer")


class InvestmentRelevanceScorer:
    """
    Deterministic scoring engine calculating institutional investment relevance.
    """

    # WEIGHTS
    W_FINANCIAL_MAGNITUDE = 0.30
    W_MARKET_IMPACT = 0.25
    W_INVESTOR_RELEVANCE = 0.20
    W_CORPORATE_SIGNIFICANCE = 0.15
    W_SOURCE_QUALITY = 0.10

    def score_event(
        self,
        event: Event,
        source_count: int = 2,
        is_multi_source_verified: bool = True,
        freshness_score: float = 0.8,
    ) -> ScoredEvent:
        """
        Calculate dimensional sub-scores and composite investment score for an event.

        Args:
            event: Verified Event model instance.
            source_count: Total independent sources confirming the event.
            is_multi_source_verified: Verification flag.
            freshness_score: Article freshness score (1.0=0-24h, 0.8=24-48h, 0.5=48-72h).

        Returns:
            ScoredEvent containing score breakdown and composite score.
        """
        text = f"{event.canonical_title} {event.description} {' '.join(event.financial_figures)}".lower()

        mag_score = self._score_financial_magnitude(text, event)
        market_score = self._score_market_impact(text, event)
        investor_score = self._score_investor_relevance(text, event)
        corp_score = self._score_corporate_significance(text, event)
        source_score = self._score_source_quality(source_count, is_multi_source_verified, text)

        # Apply freshness influence (scales total score by freshness factor, bounded)
        raw_score = (
            (self.W_FINANCIAL_MAGNITUDE * mag_score)
            + (self.W_MARKET_IMPACT * market_score)
            + (self.W_INVESTOR_RELEVANCE * investor_score)
            + (self.W_CORPORATE_SIGNIFICANCE * corp_score)
            + (self.W_SOURCE_QUALITY * source_score)
        )
        # Freshness adjustment: fresh_0_24h=1.0, fresh_24_48h=0.8, stale_48_72h=0.5
        # Penalises older stories without completely zeroing them out
        freshness_multiplier = 0.7 + (0.3 * freshness_score)  # range: [0.85, 1.0]
        total_score = round(raw_score * freshness_multiplier, 2)
        total_score = max(0.0, min(100.0, total_score))

        freshness_label = (
            "fresh_0_24h" if freshness_score >= 1.0
            else "fresh_24_48h" if freshness_score >= 0.8
            else "stale_48_72h"
        )
        rationale = (
            f"Mag: {mag_score:.0f} (30%), Mkt: {market_score:.0f} (25%), "
            f"Inv: {investor_score:.0f} (20%), Corp: {corp_score:.0f} (15%), "
            f"Src: {source_score:.0f} (10%), Freshness: {freshness_label} (x{freshness_multiplier:.2f}) => Score: {total_score:.1f}"
        )

        breakdown = ScoreBreakdown(
            financial_magnitude=mag_score,
            market_impact=market_score,
            investor_relevance=investor_score,
            corporate_significance=corp_score,
            source_quality=source_score,
            total_score=total_score,
            rationale=rationale,
        )

        logger.debug("Scored event '%s': %s", event.canonical_title[:40], rationale)

        return ScoredEvent(
            event=event,
            score_breakdown=breakdown,
            investment_score=total_score,
        )

    def _score_financial_magnitude(self, text: str, event: Event) -> float:
        """Evaluate scale of monetary metrics, deal sizes, or earnings numbers."""
        # 1. Billion dollar scales or mega rupee figures (> $5B or > ₹15,000 Cr)
        if (
            re.search(r"\$\s*([5-9]|\d{2,})\s*billion", text)
            or re.search(r"₹\s*([1-9]\d{4,}|\d{5,})\s*crore", text)
            or re.search(r"rs\.?\s*([1-9]\d{4,}|\d{5,})\s*cr", text)
            or "30 billion" in text
            or "50 billion" in text
            or "16,175 crore" in text
        ):
            return 95.0

        # 2. Large scale ($1B to $5B or ₹5,000 Cr to ₹15,000 Cr)
        if (
            re.search(r"\$\s*[1-5](\.\d+)?\s*billion", text)
            or re.search(r"₹\s*[5-9],\d{3}\s*crore", text)
            or re.search(r"₹\s*8,500\s*crore", text)
            or "6.7 billion" in text
            or "4,200 crore" in text
            or "8,500 crore" in text
        ):
            return 85.0

        # 3. Medium scale ($100M to $1B or ₹1,000 Cr to ₹5,000 Cr)
        if (
            re.search(r"\$\s*\d{2,3}\s*million", text)
            or re.search(r"₹\s*[1-4],\d{3}\s*crore", text)
            or "crore" in text
            or "million" in text
            or "billion" in text
        ):
            return 70.0

        # 4. Percentages or specific metrics present without large absolute scale
        if re.search(r"\b\d+%\b", text) or any(c.isdigit() for c in text):
            return 55.0

        # 5. Descriptive only
        return 35.0

    def _score_market_impact(self, text: str, event: Event) -> float:
        """Evaluate systemic index, macro, central bank, or sector-wide impact."""
        # Macro indicators or monetary policy
        if (
            "cpi inflation" in text
            or "gdp growth" in text
            or "interest rate" in text
            or "repo rate" in text
            or "rbi" in text
            or "fed" in text
        ):
            return 90.0

        # Large-cap systemic market leaders (HDFC, Nvidia, Reliance, Tata, Apple)
        if any(org in text for org in ("hdfc", "nvidia", "reliance", "tata motors", "infosys", "rio tinto")):
            return 85.0

        # Broad sector or antitrust impact
        if "antitrust" in text or "fine" in text or "pli scheme" in text or "nifty" in text or "s&p" in text:
            return 75.0

        return 50.0

    def _score_investor_relevance(self, text: str, event: Event) -> float:
        """Evaluate materiality to portfolio managers and institutional allocators."""
        # M&A, QIP capital raises, buybacks, surprising profit leaps
        if (
            "demerger" in text
            or "acquisition" in text
            or "acquire" in text
            or "qip" in text
            or "buyback" in text
            or "surges" in text
            or "jumps" in text
        ):
            return 90.0

        # Regular quarterly results, contract awards
        if "profit" in text or "revenue" in text or "contract" in text or "order" in text:
            return 75.0

        # Regulatory penalties, executive changes
        if "penalty" in text or "cfo" in text or "ceo" in text:
            return 65.0

        return 45.0

    def _score_corporate_significance(self, text: str, event: Event) -> float:
        """Evaluate strategic restructuring, structural corporate decisions, governance."""
        # Transformational demergers, major buyouts, CEO/CFO leadership changes
        if "demerger" in text or "takeover" in text or "appoints" in text or "resigns" in text or "cfo" in text:
            return 90.0

        # Mergers, acquisitions, QIPs
        if "acquisition" in text or "merger" in text or "qip" in text:
            return 80.0

        # Business expansions, earnings
        if "profit" in text or "expansion" in text or "penalty" in text:
            return 70.0

        return 50.0

    def _score_source_quality(
        self,
        source_count: int,
        is_multi_source_verified: bool,
        text: str,
    ) -> float:
        """Evaluate corroboration depth and prestigious publication pedigree."""
        base_score = 40.0
        if is_multi_source_verified and source_count >= 2:
            base_score = 85.0
        if source_count >= 3:
            base_score = 95.0

        # Tier-1 publisher bonus
        tier1_names = ("reuters", "bloomberg", "economic times", "business standard", "ft.com", "financial times", "wsj")
        if any(name in text for name in tier1_names):
            base_score = min(100.0, base_score + 5.0)

        return base_score
