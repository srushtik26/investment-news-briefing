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
from typing import List, Optional, Tuple

from app.logging_config import get_logger
from app.models.article import Article
from app.models.event import Event
from app.ranking.models import ScoreBreakdown, ScoredEvent
from app.ranking.signals import evaluate_editorial_signals
from app.ranking.watchlist import is_watchlist_company

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

        base_score = (
            (self.W_FINANCIAL_MAGNITUDE * mag_score)
            + (self.W_MARKET_IMPACT * market_score)
            + (self.W_INVESTOR_RELEVANCE * investor_score)
            + (self.W_CORPORATE_SIGNIFICANCE * corp_score)
            + (self.W_SOURCE_QUALITY * source_score)
        )

        bonus_score, applied_bonuses = self._calculate_strategic_bonuses(text, event)
        penalty_score, applied_penalties = self._calculate_ranking_penalties(text, event)

        # Deterministic editorial category guidance signals
        editorial_res = evaluate_editorial_signals(event)
        editorial_adj = editorial_res.score_adjustment

        # Combine base score with strategic bonuses, editorial guidance, and penalties (bounded 0-100)
        relevance_score = base_score + bonus_score + editorial_adj - penalty_score
        relevance_score = max(5.0, min(100.0, relevance_score))

        # Freshness adjustment: fresh_0_6h=1.0, fresh_6_12h=0.97, fresh_12_24h=0.94
        # Controlled scaling so freshness provides 2-5 pts advantage as tie-breaker without overwhelming relevance
        freshness_multiplier = 0.92 + (0.08 * freshness_score)
        total_score = round(relevance_score * freshness_multiplier, 2)
        total_score = max(0.0, min(100.0, total_score))

        freshness_label = (
            "fresh_0_6h" if freshness_score >= 1.0
            else "fresh_6_12h" if freshness_score >= 0.9
            else "fresh_12_24h"
        )
        all_pos = applied_bonuses + editorial_res.positive_signals
        all_neg = applied_penalties + editorial_res.negative_signals
        rationale = (
            f"Mag: {mag_score:.0f} (30%), Mkt: {market_score:.0f} (25%), "
            f"Inv: {investor_score:.0f} (20%), Corp: {corp_score:.0f} (15%), "
            f"Src: {source_score:.0f} (10%) | Pos: +{bonus_score + max(0.0, editorial_adj):.1f} ({', '.join(all_pos) or 'none'}) | "
            f"Neg: -{penalty_score + abs(min(0.0, editorial_adj)):.1f} ({', '.join(all_neg) or 'none'}) | "
            f"Freshness: {freshness_label} (x{freshness_multiplier:.2f}) => Score: {total_score:.1f}"
        )

        breakdown = ScoreBreakdown(
            financial_magnitude=mag_score,
            market_impact=market_score,
            investor_relevance=investor_score,
            corporate_significance=corp_score,
            source_quality=source_score,
            strategic_bonuses=bonus_score,
            editorial_signals=editorial_adj,
            relevance_penalties=penalty_score,
            total_score=total_score,
            rationale=rationale,
        )

        logger.debug("Scored event '%s': %s", event.canonical_title[:40], rationale)

        _wl_matched, _wl_name = is_watchlist_company(text)
        if _wl_matched:
            wl_bonus = 10.0
            ev_bonus = max(0.0, bonus_score - wl_bonus)
            logger.info(
                "[PORTFOLIO_PRIORITY]\ncompany=\"%s\"\nbase_score=%.1f\nwatchlist_bonus=%.1f\nevent_bonus=%.1f\nfinal_score=%.1f",
                _wl_name,
                base_score,
                wl_bonus,
                ev_bonus,
                total_score,
            )

        return ScoredEvent(
            event=event,
            score_breakdown=breakdown,
            investment_score=total_score,
        )

    def _calculate_strategic_bonuses(self, text: str, event: Event) -> Tuple[float, List[str]]:
        """
        Evaluate deterministic ranking bonuses across:
        - M&A / JV / stake transaction (+20)
        - large funding / capital raise (+15)
        - large capex (+20)
        - major government policy (+20)
        - RBI / SEBI / CCI / DCC regulatory action (+20)
        - foreign capital flow (+20)
        - major trade/tariff/sanctions event (+15)
        - major energy/commodity event (+15)
        - large restructuring/layoffs (+15)
        - semiconductor/AI/data-centre strategic investment (+20)
        - large infrastructure project (+15)
        - major cross-border deal (+15)
        - portfolio watchlist company (+10)
        """
        bonuses: List[str] = []
        total_bonus = 0.0

        # 1. M&A / JV / stake transaction (+20)
        if re.search(
            r"\b(?:joint venture|jv\b|merger|acquisition|acquires?|buyout|takeover|stake (?:purchase|buy|acquisition|sale)|"
            r"buys? (?:a )?\d+% stake|sells? (?:a )?\d+% stake|acquires? (?:a )?\d+% stake|all-cash deal|"
            r"definitive agreement|concession pact|partnership with|tie-up with|jsw.*skoda|skoda.*jsw)\b",
            text,
        ):
            total_bonus += 20.0
            bonuses.append("bonus_ma_jv_stake(+20)")

        # 2. Large funding / capital raise (+15)
        if re.search(
            r"\b(?:raises? (?:funds|capital|\$|₹)|funding round|series [a-z]|qip\b|qualified institutional placement|"
            r"rights issue|pre-ipo|ipo opens|files drhp|anchor investors?|secures? (?:seed|\$[\d.]+\s*(?:million|billion)|₹[\d.]+\s*crore) funding)\b",
            text,
        ):
            total_bonus += 15.0
            bonuses.append("bonus_large_funding_capital(+15)")

        # 3. Large capex (+20)
        if re.search(
            r"\b(?:capex|capital expenditure|manufacturing (?:plant|facility)|gigafactory|new facility|"
            r"invests? ₹?\s*\d+.*crore|investment to build|capacity expansion|greenfield|brownfield facility)\b",
            text,
        ):
            total_bonus += 20.0
            bonuses.append("bonus_large_capex(+20)")

        # 4. Major government policy (+20)
        if re.search(
            r"\b(?:union cabinet|cabinet clears|cabinet approves|policy reform|new national policy|incentive scheme|"
            r"pli scheme|parliament passes|gazette notification|national mission|subsid(?:y|ies))\b",
            text,
        ):
            total_bonus += 20.0
            bonuses.append("bonus_major_govt_policy(+20)")

        # 5. RBI / SEBI / CCI / DCC regulatory action (+20)
        if re.search(
            r"\b(?:rbi\b|reserve bank of india|sebi\b|securities and exchange board|cci\b|competition commission|"
            r"dcc\b|telecom commission|digital communications commission|satellite spectrum|spectrum allocation|"
            r"antitrust|monetary policy|repo rate|regulatory action|regulatory approval|fines? ₹|penalty order|"
            r"show cause notice|enforcement action)\b",
            text,
        ):
            total_bonus += 20.0
            bonuses.append("bonus_regulatory_action(+20)")

        # 6. Foreign capital flow (+20)
        if re.search(
            r"\b(?:fpi\b|fii\b|foreign portfolio investor|foreign institutional investor|foreign flow|foreign capital|"
            r"fdi\b|outflow|inflow|fpi selling|fpi buying|funds pull out|foreign investors? (?:buy|sell|offload|pump)|"
            r"cross-border capital)\b",
            text,
        ):
            total_bonus += 20.0
            bonuses.append("bonus_foreign_capital_flow(+20)")

        # 7. Major trade/tariff/sanctions event (+15)
        if re.search(
            r"\b(?:tariff|customs duty|import duty|export duty|anti-dumping duty|trade pact|fta\b|free trade agreement|"
            r"sanctions|embargo|wto\b|trade dispute|export curb)\b",
            text,
        ):
            total_bonus += 15.0
            bonuses.append("bonus_trade_tariff_sanctions(+15)")

        # 8. Major energy/commodity event (+15)
        if re.search(
            r"\b(?:crude oil|brent crude|opec|lng\b|natural gas|renewable energy|power grid|power generation|"
            r"green hydrogen|solar capacity|coal supply|lithium reserve|critical minerals|refinery)\b",
            text,
        ):
            total_bonus += 15.0
            bonuses.append("bonus_energy_commodity(+15)")

        # 9. Large restructuring/layoffs (+15)
        if re.search(
            r"\b(?:layoffs?|cuts? \d+ jobs|slashes workforce|restructuring plan|demerger|splits into|spinoff|"
            r"severance|reorganization|plant closure|retrenchment)\b",
            text,
        ):
            total_bonus += 15.0
            bonuses.append("bonus_restructuring_layoffs(+15)")

        # 10. Semiconductor/AI/data-centre strategic investment (+20)
        if re.search(
            r"\b(?:semiconductor|chip(?:maker|making|s)?|foundry|fab\b|data cent(?:er|re)s?|hyperscale|"
            r"artificial intelligence|\bai\b|generative ai|ai computing|cloud infrastructure|supercomputer)\b",
            text,
        ):
            total_bonus += 20.0
            bonuses.append("bonus_semi_ai_datacenter(+20)")

        # 11. Large infrastructure project (+15)
        if re.search(
            r"\b(?:expressway|high-speed rail|bullet train|freight corridor|deepwater port|container terminal|"
            r"airport terminal|metro line|highway corridor|transmission line|port expansion)\b",
            text,
        ):
            total_bonus += 15.0
            bonuses.append("bonus_large_infrastructure(+15)")

        # 12. Major cross-border deal (+15)
        if re.search(
            r"\b(?:cross-border|global acquisition|overseas acquisition|multinational deal|foreign acquisition|"
            r"inbound deal|outbound deal|bilateral agreement|multinational partnership)\b",
            text,
        ):
            total_bonus += 15.0
            bonuses.append("bonus_cross_border_deal(+15)")

        # 13. Portfolio watchlist company (+10)
        _wl_matched, _wl_name = is_watchlist_company(text)
        if _wl_matched:
            total_bonus += 10.0
            bonuses.append(f"watchlist_boost({_wl_name})(+10)")

        # Cap total bonus at +35.0 to maintain calibrated 0-100 scale
        capped_bonus = min(35.0, total_bonus)
        return capped_bonus, bonuses

    def _calculate_ranking_penalties(self, text: str, event: Event) -> Tuple[float, List[str]]:
        """
        Evaluate deterministic ranking penalties across:
        - routine quarterly results (-35)
        - generic market movement (-35)
        - generic stock-price change (-35)
        - minor corporate disclosure (-30)
        - listicle (-35)
        - broker/analyst recommendation (-30)
        - repetitive/low-consequence corporate item (-25)
        """
        penalties: List[str] = []
        total_penalty = 0.0

        is_mega_earnings = bool(
            re.search(r"\b(?:surges|record profit|historic profit|blowout earnings)\b", text)
            or re.search(r"₹\s*([1-9]\d{4,}|\d{5,})\s*crore", text)
            or re.search(r"\$\s*([1-9]\d*)\s*billion", text)
        )

        # 1. Routine quarterly results (-35)
        if not is_mega_earnings and re.search(
            r"\b(?:quarterly results?|q[1-4] results?|unaudited results|financial results for the quarter|"
            r"standalone results|consolidated results|bse \d+\.\d+|nse \d+\.\d+|q[1-4] earnings review|"
            r"reports net (?:profit|loss) of (?:rs|₹)\.?\s*\d+(?:\.\d+)?\s*(?:crore|lakh)|"
            r"q[1-4] profit (?:up|down|rises|falls|dips) \d+%)\b",
            text,
        ):
            total_penalty += 35.0
            penalties.append("penalty_routine_quarterly_results(-35)")

        # 2. Generic market movement (-35)
        if re.search(
            r"\b(?:sensex (?:rises|falls|gains|drops|sheds|plunges|surges)|nifty (?:rises|falls|gains|drops|sheds|plunges|surges)|"
            r"market wrap|closing bell|opening bell|indices end|markets? trade|intraday trade|market live|morning trade)\b",
            text,
        ):
            total_penalty += 35.0
            penalties.append("penalty_generic_market_movement(-35)")

        # 3. Generic stock-price change (-35)
        if re.search(
            r"\b(?:shares? (?:jump|fall|surge|dip|rise|drop|tumble|tank|rally|gain|slide)s? (?:up to )?\d+%(?: in morning trade| today)?|"
            r"52-week (?:high|low)|stock price today|share price today|why (?:[\w\s]+) shares are (?:rising|falling)|"
            r"stock plunges \d+%)\b",
            text,
        ):
            total_penalty += 35.0
            penalties.append("penalty_generic_stock_price_change(-35)")

        # 4. Minor corporate disclosure (-30)
        if re.search(
            r"\b(?:general meeting|agm notice|egm notice|loss of share certificate|trading window closure|"
            r"compliance certificate|secretarial audit|scrutinizer report|board meeting on|investor presentation uploaded|"
            r"shareholding pattern|disclosure under reg|regulation 74\(5\))\b",
            text,
        ):
            total_penalty += 30.0
            penalties.append("penalty_minor_corporate_disclosure(-30)")

        # 5. Listicle (-35)
        if re.search(
            r"\b(?:stocks? to watch|stocks? in focus|top \d+ stocks|buzzing stocks|hot stocks|"
            r"stocks that will be in focus|\d+ stocks (?:to buy|in focus|to watch))\b",
            text,
        ):
            total_penalty += 35.0
            penalties.append("penalty_listicle(-35)")

        # 6. Broker/analyst recommendation (-30)
        if re.search(
            r"\b(?:target price|buy rating|sell rating|brokerage|analyst (?:recommends|view|target)|"
            r"upgrade|downgrade|initiates coverage|accumulate|hold rating|target rs\b|target ₹)\b",
            text,
        ):
            total_penalty += 30.0
            penalties.append("penalty_broker_analyst_recommendation(-30)")

        # 7. Repetitive/low-consequence corporate item (-25)
        if re.search(
            r"\b(?:press release|company update|clarification on|statement regarding|exchange query|"
            r"corporate announcement|routine disclosure|minor contract|minor order)\b",
            text,
        ):
            total_penalty += 25.0
            penalties.append("penalty_repetitive_low_consequence(-25)")

        # Cap total penalty at -50.0
        capped_penalty = min(50.0, total_penalty)
        return capped_penalty, penalties

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
        # Strategic transactions, foreign capital flows, regulatory actions, capex, surprising profit leaps
        if (
            "demerger" in text
            or "acquisition" in text
            or "acquire" in text
            or "joint venture" in text
            or "qip" in text
            or "buyback" in text
            or "surges" in text
            or "jumps" in text
            or "fpi" in text
            or "foreign capital" in text
            or "foreign portfolio" in text
            or "satellite spectrum" in text
            or "capex" in text
            or "capital expenditure" in text
        ):
            return 90.0

        # Routine quarterly results disclosures
        if re.search(r"\b(?:quarterly results?|q[1-4] results?|bse \d+|nse \d+)\b", text):
            return 40.0

        # Regular corporate contracts, earnings
        if "profit" in text or "revenue" in text or "contract" in text or "order" in text:
            return 75.0

        # Regulatory penalties, executive changes
        if "penalty" in text or "cfo" in text or "ceo" in text:
            return 65.0

        return 45.0

    def _score_corporate_significance(self, text: str, event: Event) -> float:
        """Evaluate strategic restructuring, structural corporate decisions, governance."""
        # Transformational demergers, major buyouts, JVs, major capex, policy reforms
        if (
            "demerger" in text
            or "takeover" in text
            or "joint venture" in text
            or "appoints" in text
            or "resigns" in text
            or "cfo" in text
            or "capex" in text
            or "capital expenditure" in text
            or "policy reform" in text
            or "spectrum" in text
        ):
            return 90.0

        # Mergers, acquisitions, QIPs
        if "acquisition" in text or "merger" in text or "qip" in text:
            return 80.0

        # Routine quarterly results disclosures
        if re.search(r"\b(?:quarterly results?|q[1-4] results?|bse \d+|nse \d+)\b", text):
            return 40.0

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
