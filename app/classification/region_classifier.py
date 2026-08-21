import re
from typing import Any, List, Optional, Set, Tuple
from app.logging_config import get_logger
from app.models.article import Article
from app.models.enums import NewsCategory
from app.models.event import Event

logger = get_logger("classification.region_classifier")


class EventRegionClassifier:
    """
    Deterministic rule-based classifier determining whether an event belongs to the
    INDIA or INTERNATIONAL briefing section.
    
    Priority Rules for Corporate Transactions:
    1. Target / Subject company domicile (e.g. KFin Technologies, IDBI Bank, Shanthi Gears, JBM Auto)
    2. Listed market / regulator / asset geography (BSE/NSE, SEBI, RBI, ₹, crore, lakh)
    3. Primary operating company (e.g. Tube Investments of India)
    4. Discovery section prior (article.category / candidate_category)
    5. Buyer / Seller financial sponsor domicile (foreign sponsor buying Indian target = INDIA)
    """

    # Indian Regulators, Government Bodies, Indices & Policy Keywords
    INDIAN_REGULATORY_AND_POLICY: List[str] = [
        r"\b(rbi|reserve bank of india)\b",
        r"\b(sebi|securities and exchange board of india)\b",
        r"\b(nifty|nifty 50|sensex|bse|nse|bse sensex)\b",
        r"\b(cci|competition commission of india)\b",
        r"\b(union budget|finance ministry|nirmala sitharaman|gst council|gst)\b",
        r"\b(dgca|trai|irdai|ed|enforcement directorate|cbdt|cbic|nclt|drhp)\b",
        r"\b(government of india|centre|central government|cabinet approves?|pli scheme)\b",
        r"\b(india gdp|indian economy|retail inflation in india|monsoon)\b",
    ]

    # Indian Currencies & Financial Scale Units
    INDIAN_CURRENCY_AND_UNITS: List[str] = [
        r"₹",
        r"\b(rs\.?|inr|rupees?|crore|cr|lakh|lakhs)\b",
    ]

    # Major Indian Companies, Startups, Financial Institutions, Conglomerates
    INDIAN_ENTITIES: List[str] = [
        # Major Conglomerates & Industrial
        r"\b(tata|tata motors|tata steel|tata power|tcs|tata consultancy)\b",
        r"\b(reliance|reliance industries|ril|reliance retail|reliance jio|jio)\b",
        r"\b(adani|adani enterprises|adani ports|adani green|adani power)\b",
        r"\b(larsen & toubro|l&t|nxt-infra|nxt infra|nxt-infra trust)\b",
        r"\b(mahindra|mahindra & mahindra|m&m|bajaj|bajaj auto|bajaj finance|bajaj finserv)\b",
        r"\b(itc|maruti|maruti suzuki|hero motocorp|tvs motor|eicher)\b",
        r"\b(vedanta|jsw|jsw steel|hindalco|grasim|ultratech|shree cement)\b",
        r"\b(ntpc|ongc|coal india|power grid|bharat petroleum|bpcl|ioc|iocl|hpcl|gail)\b",
        r"\b(bhel|bel|hal|hindustan aeronautics|mazagon dock)\b",
        r"\b(tube investments|tube investments of india|tii|shanthi gears)\b",
        r"\b(jbm auto|jbm|godrej|piramal|havells|voltas|polycab|kei industries)\b",
        
        # Financial & Banking
        r"\b(hdfc|hdfc bank|hdfc life|hdfc ergo|icici|icici bank|icici prudential)\b",
        r"\b(sbi|state bank of india|axis bank|kotak|kotak mahindra|indusind|yes bank|idfc|idfc first)\b",
        r"\b(idbi|idbi bank|kfin|kfin technologies|kfintech)\b",
        r"\b(punjab national bank|pnb|bank of baroda|canara bank|union bank of india)\b",
        r"\b(zerodha|groww|angel one|upstox|bandhan bank|federal bank|rbl bank|au small finance bank)\b",
        
        # IT & Tech Services
        r"\b(infosys|wipro|hcl tech|hcl technologies|tech mahindra|l&t technology|ltimindtree|mphasis|coforge)\b",
        
        # Pharma & Healthcare
        r"\b(sun pharma|dr reddy|dr\. reddy|cipla|lupin|aurobindo|divi's|zydus|mankind pharma|biocon|apollo hospitals)\b",
        r"\b(manipal|manipal health|manipal hospitals|max healthcare|fortis|narayana hrudayalaya|medanta)\b",
        
        # Consumer, Retail & Digital Startups
        r"\b(zomato|swiggy|paytm|phonepe|zepto|blinkit|shiprocket|nykaa|ola|ola electric|oyo|byju's|delhivery|meesho|mamaearth|lenskart|cred|urban company)\b",
        
        # Explicit Indian name indicators
        r"\b\w+\s+(?:of\s+india|india\s+ltd|india\s+limited)\b",
    ]

    # Global / International Regulators & Macro Bodies
    INTERNATIONAL_REGULATORY_AND_POLICY: List[str] = [
        r"\b(fed|federal reserve|jerome powell|fomc)\b",
        r"\b(ecb|european central bank|bank of england|boe|bank of japan|boj)\b",
        r"\b(sec|us sec|securities and exchange commission|ftc|federal trade commission|doj)\b",
        r"\b(eu commission|european commission|european union|imf|world bank)\b",
        r"\b(wall street|s&p 500|nasdaq|dow jones|ftse 100|nikkei|dax|cac 40)\b",
        r"\b(us gdp|us inflation|us cpi|eurozone|us treasury|treasury yields)\b",
    ]

    # Global Financial Sponsors / PE / Asset Managers (often transact in Indian companies)
    FINANCIAL_SPONSORS: List[str] = [
        r"\b(general atlantic|bain capital|bain|fairfax|fairfax financial|temasek|gic|softbank|tiger global|peak xv|sequoia|warburg pincus|advent international|eqt|cpibb|blackstone|kkr|carlyle|brookfield)\b",
    ]

    # Major Global / International Companies & Entities
    INTERNATIONAL_ENTITIES: List[str] = [
        # Big Tech & AI
        r"\b(apple|microsoft|google|alphabet|meta|facebook|amazon|nvidia|tesla)\b",
        r"\b(openai|anthropic|stripe|openrouter|mistral|deepmind|arm holdings|arm)\b",
        r"\b(tsmc|asml|intel|amd|qualcomm|broadcom|micron|samsung|sony|alibaba|tencent|bytedance)\b",
        
        # Global Finance, Sponsors, Sports Franchises & Private Equity
        r"\b(arctos|atlanta falcons|falcons|lakers|los angeles lakers|nfl|nba|mlb|f1|formula 1)\b",
        r"\b(goldman sachs|jpmorgan|morgan stanley|citigroup|citi|bank of america|bofa|wells fargo)\b",
        r"\b(blackrock|blackstone|kkr|carlyle|apollo global|lcn capital|lcn capital partners)\b",
        r"\b(hsbc|barclays|ubs|bnp paribas|deutsche bank|credit agricole|santander)\b",
        
        # Global Industrial, Mining, Pharma, Auto
        r"\b(rio tinto|arcadium lithium|bhp|glencore|anglo american|vale)\b",
        r"\b(boeing|airbus|lockheed martin|general electric|ge aerospace)\b",
        r"\b(pfizer|moderna|astrazeneca|novartis|roche|novo nordisk|eli lilly|leo pharma|mersana|mitsubishi tanabe)\b",
        r"\b(toyota|volkswagen|bmw|mercedes-benz|stellantis|ford|general motors|byd)\b",
        r"\b(nestle|unilever|procter & gamble|p&g|pepsico|coca-cola|lvmh|nike)\b",
    ]

    def classify_with_reason(
        self,
        title: str,
        content: Optional[str] = None,
        financial_figures: Optional[List[str]] = None,
        companies: Optional[List[str]] = None,
        discovery_region: Optional[NewsCategory] = None,
    ) -> Tuple[NewsCategory, str]:
        """
        Deterministically classify an event or article with explicit rationale.
        Precedence:
        1. Indian / Global Regulators & Policies
        2. Target / Subject entity domicile (Indian entity vs International entity)
        3. Localized Title / Facts Currency ($ vs ₹) with Discovery Prior safety
        4. Discovery Region Prior
        """
        title_lower = (title or "").lower()
        companies_text = " ".join(companies or []).lower()
        figures_text = " ".join(financial_figures or []).lower()
        title_and_entities = f"{title_lower} {companies_text}"

        # 1. Check Indian Regulators / Policy / Exchange Indicators (Title priority)
        for pat in self.INDIAN_REGULATORY_AND_POLICY:
            m = re.search(pat, title_lower)
            if m:
                return NewsCategory.INDIA, f"Indian regulatory / market policy match: '{m.group(0)}'"

        # 2. Check Global Regulators / Macro Policy
        for pat in self.INTERNATIONAL_REGULATORY_AND_POLICY:
            m = re.search(pat, title_lower)
            if m:
                return NewsCategory.INTERNATIONAL, f"Global regulatory / macro policy match: '{m.group(0)}'"

        # 3. Entity Matches in Title or Companies Involved
        indian_entity_matches = [
            re.search(pat, title_and_entities).group(0)
            for pat in self.INDIAN_ENTITIES
            if re.search(pat, title_and_entities)
        ]

        intl_entity_matches = [
            re.search(pat, title_and_entities).group(0)
            for pat in self.INTERNATIONAL_ENTITIES
            if re.search(pat, title_and_entities)
        ]

        sponsor_matches = [
            re.search(pat, title_and_entities).group(0)
            for pat in self.FINANCIAL_SPONSORS
            if re.search(pat, title_and_entities)
        ]

        # 4. Currency and Unit Signals from Title and Structured Figures Only
        local_text = f"{title_lower} {figures_text}"
        has_indian_currency_local = any(re.search(pat, local_text) for pat in self.INDIAN_CURRENCY_AND_UNITS)
        has_dollar_local = bool(re.search(r"(\$|\b(usd|us dollar|dollars?)\b)", local_text))

        # RULE A: Target / Subject entity domicile
        if indian_entity_matches:
            matched_name = indian_entity_matches[0]
            if sponsor_matches:
                return NewsCategory.INDIA, f"Indian target/subject entity '{matched_name}' with financial sponsor '{sponsor_matches[0]}'"
            if intl_entity_matches:
                return NewsCategory.INDIA, f"Indian entity '{matched_name}' transacting with international entity '{intl_entity_matches[0]}'"
            return NewsCategory.INDIA, f"Indian entity match: '{matched_name}'"

        # RULE B: International entity with dollar or international discovery prior
        if intl_entity_matches:
            matched_intl = intl_entity_matches[0]
            if not has_indian_currency_local or has_dollar_local or discovery_region == NewsCategory.INTERNATIONAL:
                return NewsCategory.INTERNATIONAL, f"International entity '{matched_intl}' with global context"

        # RULE C: Financial Sponsor transactions
        if sponsor_matches:
            # Foreign sponsor buying/selling Indian asset (e.g. General Atlantic + KFin, Fairfax + IDBI, Bain + JBM)
            if has_indian_currency_local or "india" in title_lower:
                return NewsCategory.INDIA, f"Financial sponsor '{sponsor_matches[0]}' in Indian transaction context"
            if discovery_region == NewsCategory.INTERNATIONAL or has_dollar_local:
                return NewsCategory.INTERNATIONAL, f"Financial sponsor '{sponsor_matches[0]}' in international transaction context"

        # RULE D: Strong Currency Signals in Title/Headline
        title_has_inr = any(re.search(pat, title_lower) for pat in self.INDIAN_CURRENCY_AND_UNITS)
        if title_has_inr:
            return NewsCategory.INDIA, "Indian currency in title / event headline (crore/₹/lakh)"

        # RULE E: Discovery Region Prior Protection (Weak body tokens must not flip discovery prior)
        if discovery_region == NewsCategory.INTERNATIONAL:
            # Strong proof required to flip from International to India
            if "india" in title_lower or has_indian_currency_local:
                return NewsCategory.INDIA, "Indian currency / market reference overriding international discovery"
            return NewsCategory.INTERNATIONAL, "Preserved International discovery pool prior"

        if discovery_region == NewsCategory.INDIA:
            # Strong proof required to flip from India to International
            if has_dollar_local and not has_indian_currency_local and intl_entity_matches:
                return NewsCategory.INTERNATIONAL, "International entity and dollar currency overriding Indian discovery"
            return NewsCategory.INDIA, "Preserved Indian discovery pool prior"

        # Default fallback
        if has_dollar_local:
            return NewsCategory.INTERNATIONAL, "Dollar currency transaction"
        if has_indian_currency_local:
            return NewsCategory.INDIA, "Indian currency / numerical units (crore/₹/lakh)"

        return NewsCategory.INTERNATIONAL, "Default fallback (no Indian signals found)"

    def classify(
        self,
        title: str,
        content: Optional[str] = None,
        financial_figures: Optional[List[str]] = None,
        companies: Optional[List[str]] = None,
        discovery_region: Optional[NewsCategory] = None,
    ) -> NewsCategory:
        """Classify into NewsCategory."""
        cat, _ = self.classify_with_reason(
            title=title,
            content=content,
            financial_figures=financial_figures,
            companies=companies,
            discovery_region=discovery_region,
        )
        return cat

    def classify_article(self, article: Article) -> NewsCategory:
        """Classify an Article instance."""
        cat, reason = self.classify_with_reason(
            title=article.title,
            content=article.content_text,
            companies=[article.source_name] if article.source_name else [],
            discovery_region=article.category,
        )
        return cat

    def classify_event(self, event: Event, articles: Optional[List[Article]] = None) -> NewsCategory:
        """Classify an Event instance with observability logging."""
        combined_content = event.description or ""
        disc_region = event.event_category
        if articles:
            combined_content += " " + " ".join((a.content_text or "")[:200] for a in articles)
            if not disc_region and articles[0].category:
                disc_region = articles[0].category

        region, reason = self.classify_with_reason(
            title=event.canonical_title,
            content=combined_content,
            financial_figures=event.financial_figures,
            companies=event.companies_involved,
            discovery_region=disc_region,
        )

        disc_str = disc_region.value.upper() if disc_region else "UNKNOWN"
        logger.info(
            "REGION_DECISION: event=\"%s\" region=%s reason=\"%s\" discovery_region=%s",
            event.canonical_title[:50],
            region.value.upper(),
            reason,
            disc_str,
        )
        return region
