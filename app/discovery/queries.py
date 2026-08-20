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
        "quarterly results net profit revenue crore",
        "Q1 Q2 Q3 Q4 results profit rises falls",
        "annual results profit revenue billion crore",
    ],
    "acquisitions": [
        "acquires acquisition deal buyout stake",
        "company acquires majority stake crore",
        "acquires buys out merger deal India",
    ],
    "mergers": [
        "merger approved NCLT demerger board",
        "board approves merger scheme amalgamation",
    ],
    "stake_purchases": [
        "buys stake block deal crore investment",
        "strategic investment stake acquisition India",
    ],
    "fundraises": [
        "raises funds equity capital funding round crore",
        "board approves capital raise QIP rights issue",
        "secures funding venture capital private equity India",
    ],
    "qips": [
        "Qualified Institutional Placement QIP floor price",
        "launches QIP issue shares crore",
    ],
    "bond_issuances": [
        "issues NCDs non-convertible debentures crore",
        "raises funds dollar bonds debt issuance",
    ],
    "ipo_listings": [
        "IPO debut listing day shares gain NSE BSE",
        "files DRHP draft IPO papers SEBI crore",
    ],
    "regulatory_actions": [
        "RBI penalty order bank NBFC crore",
        "SEBI order ban penalty insider trading",
        "CCI approves antitrust order investigation",
    ],
    "government_policy": [
        "PLI scheme approval investment subsidy crore",
        "customs duty tariff export tax revision India",
    ],
    "macroeconomic_data": [
        "India GDP growth data percentage",
        "retail inflation CPI food inflation percentage",
        "IIP industrial production output data",
    ],
    "leadership_changes": [
        "appoints new CEO Managing Director India company",
        "MD CEO steps down resigns company India",
    ],
    "sector_figures": [
        "auto sales volumes dispatch monthly units",
        "steel production cement dispatches quarterly data",
    ],
    "joint_ventures": [
        "joint venture JV agreement signs India",
        "strategic partnership agreement crore India",
    ],
    "asset_sales": [
        "divests asset sale stake exits business crore",
        "monetises unit sells stake crore India",
    ],
}


# Targeted Hard Business Event Categories (International)
INTERNATIONAL_EVENT_CATEGORIES: Dict[str, List[str]] = {
    "us_earnings": [
        "quarterly earnings net income revenue beats guidance",
        "S&P 500 company results beats revenue billion",
        "annual earnings profit revenue billion guidance",
    ],
    "european_earnings": [
        "European company quarterly results profit billion",
        "European corporate results beats revenue guidance",
    ],
    "acquisitions": [
        "agrees to acquire billion dollar takeover deal",
        "cross-border acquisition merger billion buyout",
        "company acquires rival billion transaction",
    ],
    "mergers": [
        "merger agreement antitrust regulatory review billion",
        "mega merger billion transaction agreed",
    ],
    "fundraising": [
        "secures debt financing credit facility billion",
        "closes billion funding round capital raise",
        "private equity investment billion stake",
    ],
    "regulatory_decisions": [
        "SEC enforcement penalty settlement billion",
        "EU antitrust commission fine billion ruling",
        "DOJ antitrust lawsuit regulatory block",
    ],
    "fed_decisions": [
        "Federal Reserve FOMC interest rate decision",
        "Fed interest rate policy statement basis points",
    ],
    "corporate_policy": [
        "corporate restructuring job cuts layoffs billion",
        "capital expenditure capex guidance billion",
        "guidance reaffirms raises narrows full year",
    ],
    "asian_market_moves": [
        "Bank of Japan interest rate decision yen",
        "China economy GDP growth output data",
    ],
    "geopolitical_quantified": [
        "crude oil price surge supply disruption barrel",
        "tariffs trade sanctions quantified billion market impact",
    ],
    "joint_ventures_intl": [
        "joint venture agreement billion global company",
        "strategic partnership agreement billion",
    ],
}


class SearchQueryBuilder:
    """
    Constructs search query strings combining category keywords with publisher site filters.
    """

    @classmethod
    def get_sources_for_country(cls, country: str) -> List[TargetSource]:
        """Return list of target sources for a given country."""
        norm = country.strip().title()
        if norm in ("India", "In"):
            return INDIA_SOURCES
        return INTERNATIONAL_SOURCES

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
