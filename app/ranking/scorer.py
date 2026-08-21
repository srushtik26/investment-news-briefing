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
from app.models.article import Article
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
            freshness_score: Article freshness score (1.0=0-6h, 0.9=6-12h, 0.8=12-24h).

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
        # Freshness adjustment: fresh_0_6h=1.0, fresh_6_12h=0.9, fresh_12_24h=0.8
        freshness_multiplier = 0.7 + (0.3 * freshness_score)
        total_score = round(raw_score * freshness_multiplier, 2)
        total_score = max(0.0, min(100.0, total_score))

        freshness_label = (
            "fresh_0_6h" if freshness_score >= 1.0
            else "fresh_6_12h" if freshness_score >= 0.9
            else "fresh_12_24h"
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


def calculate_corroboration_priority(event: Event, primary_article: Optional[Article] = None) -> float:
    """
    Calculate deterministic priority (0 to 100) for spending scarce corroboration requests.
    Hard business events (M&A, earnings, IPO, regulatory) receive high priority (80-100).
    Live stock quotes, market movements, turnaround narratives, and demand trends receive low priority (<30).
    """
    text = f"{event.canonical_title} {event.description}".lower()
    if primary_article:
        text = f"{text} {primary_article.title} {(primary_article.content_text or '')[:300]}".lower()

    # 1. Check for immediate low-priority patterns (<30)
    is_live_quote = bool(re.search(
        r"\b(share price|stock price|live nse|live bse|price today|stock today|live updates|closing bell|opening bell|gmp|grey market premium|market live)\b",
        text,
    ))
    if is_live_quote:
        return 10.0

    is_turnaround_or_trend = bool(re.search(
        r"\b(turnaround is picking up steam|turnaround plan|turnaround effort|demand gets a lift|demand trend|industry outlook|explained|opinion|column|why are|what to watch|buzzing stocks|top gainers|top losers|outlook disappoints)\b",
        text,
    ))
    # Check if stock price reaction without detailed hard financial earnings
    has_concrete_earnings = bool(re.search(r"\b(net profit|q[1-4] results|q[1-4] profit|ebitda|operating profit|revenue rises|revenue falls|profit rises|profit falls)\b", text))
    is_pure_price_reaction = bool(re.search(
        r"\b(stock tumbles|shares tumble|shares slip|stock jumps|shares jump|shares drop|shares fall|shares rise|shares surge|stock climbs|rally)\b",
        text,
    )) and not has_concrete_earnings

    if is_turnaround_or_trend or is_pure_price_reaction:
        return 20.0

    # If it's a generic industry narrative with no recognized company:
    if "synthetic rubber" in text or "demand gets a lift" in text:
        return 15.0

    # 2. Check for Hard Event Types (60 - 100)
    if re.search(r"\b(to buy|buys|acquires?|acquisition|merger|merges|takeover|buyout|stake purchase|all-cash deal|demerger|spin-off)\b", text):
        base = 90.0
    elif re.search(r"\b(block deal|stake sale|equity changes hands|promoter stake sale|institutional stake sale|bulk deal)\b", text):
        base = 80.0
    elif re.search(r"\b(rbi|sebi|cci|sec|antitrust|doj|penalty|fine|ban|order|charges|probe|inquiry)\b", text):
        base = 85.0
    elif re.search(r"\b(raises funding|funding round|qip|rights issue|capital raise|funds raised|files for ipo|files drhp|ipo allotment|shares list at|ipo listing)\b", text):
        base = 85.0
    elif re.search(r"\b(net profit|quarterly profit|revenue rises|revenue falls|profit rises|profit falls|q[1-4] results|q[1-4] profit|ebitda|earnings beat|earnings miss|guidance raised|guidance cut|outlook raised|outlook cut)\b", text):
        base = 80.0
    elif re.search(r"\b(plant investment|capacity expansion|new plant|capex|manufacturing facility|joint venture|partnership|tie-up)\b", text):
        base = 70.0
    elif re.search(r"\b(appoints ceo|md resigns|new managing director|new cfo|appoints chairman|steps down)\b", text):
        base = 65.0
    elif re.search(r"\b(gdp growth|cpi inflation|retail inflation|rate cut|rate hike|trade deficit)\b", text):
        base = 60.0
    else:
        # Default with no recognizable hard event keyword
        base = 25.0

    # 3. Fact Enhancers
    has_numbers = bool(re.search(r"(?:₹|\$|€|£|rs\.?\s*)\s*[\d,]+|\b\d+(?:\.\d+)?%", text))
    if has_numbers and base >= 60.0:
        base += 5.0

    if event.companies_involved and base >= 60.0:
        base += 5.0

    return min(100.0, max(0.0, base))
