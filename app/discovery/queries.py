"""
Search Queries and Category Definitions for Hard Business News Events.

Specifies targeted search keywords, category groupings, and source domains
for Indian and International financial publications.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class TargetSource:
    """Represents an approved news publisher outlet and its primary domain."""
    name: str
    domain: str
    country: str


# Approved Publishers (primary discovery sources)
INDIA_SOURCES: List[TargetSource] = [
    TargetSource(name="The Economic Times", domain="economictimes.indiatimes.com", country="India"),
    TargetSource(name="Business Standard", domain="business-standard.com", country="India"),
    TargetSource(name="Livemint", domain="livemint.com", country="India"),
    TargetSource(name="Financial Express", domain="financialexpress.com", country="India"),
    TargetSource(name="Moneycontrol", domain="moneycontrol.com", country="India"),
    TargetSource(name="Business Today", domain="businesstoday.in", country="India"),
    TargetSource(name="NDTV Profit", domain="ndtvprofit.com", country="India"),
]

INTERNATIONAL_SOURCES: List[TargetSource] = [
    TargetSource(name="CNBC", domain="cnbc.com", country="International"),
    TargetSource(name="AP News", domain="apnews.com", country="International"),
    TargetSource(name="BBC News", domain="bbc.com", country="International"),
    TargetSource(name="MarketWatch", domain="marketwatch.com", country="International"),
    TargetSource(name="The Guardian", domain="theguardian.com", country="International"),
    TargetSource(name="Fortune", domain="fortune.com", country="International"),
    TargetSource(name="Reuters", domain="reuters.com", country="International"),
    TargetSource(name="Bloomberg", domain="bloomberg.com", country="International"),
    TargetSource(name="Financial Times", domain="ft.com", country="International"),
    TargetSource(name="Wall Street Journal", domain="wsj.com", country="International"),
]


# Targeted Hard Business Event Categories (India)
INDIA_EVENT_CATEGORIES: Dict[str, List[str]] = {
    "earnings_results": [
        "quarterly results net profit revenue crore when:1d",
        "Q1 Q2 Q3 Q4 results profit rises falls when:1d",
        "annual results profit revenue billion crore when:1d",
    ],
    "acquisitions": [
        "acquires acquisition deal buyout stake when:1d",
        "company acquires majority stake crore when:1d",
        "acquires buys out merger deal India when:1d",
    ],
    "mergers": [
        "merger approved NCLT demerger board when:1d",
        "board approves merger scheme amalgamation when:1d",
    ],
    "stake_purchases": [
        "buys stake block deal crore investment when:1d",
        "strategic investment stake acquisition India when:1d",
    ],
    "fundraises": [
        "raises funds equity capital funding round crore when:1d",
        "board approves capital raise QIP rights issue when:1d",
        "secures funding venture capital private equity India when:1d",
    ],
    "qips": [
        "Qualified Institutional Placement QIP floor price when:1d",
        "launches QIP issue shares crore when:1d",
    ],
    "bond_issuances": [
        "issues NCDs non-convertible debentures crore when:1d",
        "raises funds dollar bonds debt issuance when:1d",
    ],
    "ipo_listings": [
        "IPO debut listing day shares gain NSE BSE when:1d",
        "files DRHP draft IPO papers SEBI crore when:1d",
    ],
    "regulatory_actions": [
        "RBI penalty order bank NBFC crore when:1d",
        "SEBI order ban penalty insider trading when:1d",
        "CCI approves antitrust order investigation when:1d",
    ],
    "government_policy": [
        "PLI scheme approval investment subsidy crore when:1d",
        "customs duty tariff export tax revision India when:1d",
    ],
    "macroeconomic_data": [
        "India GDP growth data percentage when:1d",
        "retail inflation CPI food inflation percentage when:1d",
        "IIP industrial production output data when:1d",
    ],
    "leadership_changes": [
        "appoints new CEO Managing Director India company when:1d",
        "MD CEO steps down resigns company India when:1d",
    ],
    "sector_figures": [
        "auto sales volumes dispatch monthly units when:1d",
        "steel production cement dispatches quarterly data when:1d",
    ],
    "joint_ventures": [
        "joint venture JV agreement signs India when:1d",
        "strategic partnership agreement crore India when:1d",
    ],
    "asset_sales": [
        "divests asset sale stake exits business crore when:1d",
        "monetises unit sells stake crore India when:1d",
    ],
}


# Targeted Hard Business Event Categories (International)
INTERNATIONAL_EVENT_CATEGORIES: Dict[str, List[str]] = {
    "us_earnings": [
        "quarterly earnings net income revenue beats guidance when:1d",
        "S&P 500 company results beats revenue billion when:1d",
        "annual earnings profit revenue billion guidance when:1d",
    ],
    "european_earnings": [
        "European company quarterly results profit billion when:1d",
        "European corporate results beats revenue guidance when:1d",
    ],
    "acquisitions": [
        "agrees to acquire billion dollar takeover deal when:1d",
        "cross-border acquisition merger billion buyout when:1d",
        "company acquires rival billion transaction when:1d",
    ],
    "mergers": [
        "merger agreement antitrust regulatory review billion when:1d",
        "mega merger billion transaction agreed when:1d",
    ],
    "fundraising": [
        "secures debt financing credit facility billion when:1d",
        "closes billion funding round capital raise when:1d",
        "private equity investment billion stake when:1d",
    ],
    "regulatory_decisions": [
        "SEC enforcement penalty settlement billion when:1d",
        "EU antitrust commission fine billion ruling when:1d",
        "DOJ antitrust lawsuit regulatory block when:1d",
    ],
    "fed_decisions": [
        "Federal Reserve FOMC interest rate decision when:1d",
        "Fed interest rate policy statement basis points when:1d",
    ],
    "corporate_policy": [
        "corporate restructuring job cuts layoffs billion when:1d",
        "capital expenditure capex guidance billion when:1d",
        "guidance reaffirms raises narrows full year when:1d",
    ],
    "asian_market_moves": [
        "Bank of Japan interest rate decision yen when:1d",
        "China economy GDP growth output data when:1d",
    ],
    "geopolitical_quantified": [
        "crude oil price surge supply disruption barrel when:1d",
        "tariffs trade sanctions quantified billion market impact when:1d",
    ],
    "joint_ventures_intl": [
        "joint venture agreement billion global company when:1d",
        "strategic partnership agreement billion when:1d",
    ],
}


ACCESSIBLE_INTERNATIONAL_SOURCES: List[TargetSource] = [
    TargetSource(name="CNBC", domain="cnbc.com", country="International"),
    TargetSource(name="AP News", domain="apnews.com", country="International"),
    TargetSource(name="BBC News", domain="bbc.com", country="International"),
    TargetSource(name="MarketWatch", domain="marketwatch.com", country="International"),
    TargetSource(name="The Guardian", domain="theguardian.com", country="International"),
    TargetSource(name="Fortune", domain="fortune.com", country="International"),
]

SECONDARY_SIGNALLING_SOURCES: List[TargetSource] = [
    TargetSource(name="Reuters", domain="reuters.com", country="International"),
    TargetSource(name="Bloomberg", domain="bloomberg.com", country="International"),
    TargetSource(name="Financial Times", domain="ft.com", country="International"),
    TargetSource(name="Wall Street Journal", domain="wsj.com", country="International"),
]


class SearchQueryBuilder:
    """
    Constructs search query strings for Google News RSS.
    """

    @classmethod
    def get_sources_for_country(cls, country: str) -> List[TargetSource]:
        """Return approved sources for country."""
        norm = country.strip().title()
        if norm in ("India", "In"):
            return INDIA_SOURCES
        return INTERNATIONAL_SOURCES

    @classmethod
    def get_accessible_sources_for_country(cls, country: str) -> List[TargetSource]:
        """Return extractable primary sources (accessible without aggressive paywalls)."""
        norm = country.strip().title()
        if norm in ("India", "In"):
            return INDIA_SOURCES
        return ACCESSIBLE_INTERNATIONAL_SOURCES

    @classmethod
    def get_categories_for_country(cls, country: str) -> Dict[str, List[str]]:
        """Return event category dictionary for country."""
        norm = country.strip().title()
        if norm in ("India", "In"):
            return INDIA_EVENT_CATEGORIES
        return INTERNATIONAL_EVENT_CATEGORIES

    @classmethod
    def build_query_strings(
        cls,
        country: str,
        category: Optional[str] = None,
        include_site_filters: bool = True,
    ) -> List[str]:
        """
        Generate search query strings for a country and optional specific category.
        """
        sources = cls.get_sources_for_country(country)
        categories = cls.get_categories_for_country(country)

        selected_categories = (
            {category: categories[category]}
            if category and category in categories
            else categories
        )

        site_clause = ""
        if include_site_filters and sources:
            site_terms = [f"site:{s.domain}" for s in sources]
            site_clause = " (" + " OR ".join(site_terms) + ")"

        queries: List[str] = []
        for cat_name, phrase_list in selected_categories.items():
            for phrase in phrase_list:
                query_str = f"{phrase}{site_clause}"
                queries.append(query_str.strip())

        return queries
