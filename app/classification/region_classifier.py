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
        r"\b(standalone net profit|standalone profit|standalone revenue|standalone results)\b",
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
        r"\b(manipal|manipal health|manipal hospitals|max healthcare|fortis|narayana hrudayalaya|medanta|tynor|tynor orthotics|fluence pharma)\b",
        
        # Consumer, Retail & Digital Startups & Retail/Malls & Manufacturing
        r"\b(zomato|swiggy|paytm|phonepe|zepto|blinkit|shiprocket|nykaa|ola|ola electric|oyo|byju's|delhivery|meesho|mamaearth|honasa|honasa consumer|lenskart|cred|urban company)\b",
        r"\b(welspun|welspun corp|inorbit|inorbit malls|prozone|prozone malls|kedaara|kedaara capital|c2i|c2i semiconductors|airtel payments bank|ardee|ardee industries|ardee infrastructure)\b",
        
        # Explicit Indian name indicators
        r"\b\w+\s+(?:of\s+india|india\s+ltd|india\s+limited)\b",
    ]

    # Global / International Regulators & Macro Bodies
    INTERNATIONAL_REGULATORY_AND_POLICY: List[str] = [
        r"\b(fed|federal reserve|jerome powell|fomc)\b",
        r"\b(ecb|european central bank|bank of england|boe|bank of japan|boj)\b",
        r"\b(us sec|u\.s\. sec|sec charges|sec investigation|sec approves|us securities and exchange commission|ftc|federal trade commission|doj)\b",
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
        # Global Media, Sports & Entertainment
        r"\b(dazn|espn|sky sports|discovery\+|peacock|paramount\+|hbo|warner bros|universal pictures|sony pictures)\b",
        r"\b(netflix|disney|disney\+|hulu|spotify|tencent music|iheartmedia)\b",
        r"\b(comcast|charter communications|at\&t|verizon|t-mobile|deutsche telekom)\b",
        r"\b(manchester united|manchester city|real madrid|barcelona|chelsea fc|arsenal fc|liverpool fc)\b",
    ]

    # Indian Regulators, Government Bodies & Financial Market Keywords -> INDIA BUSINESS
    INDIAN_BUSINESS_POLICY_AND_REGULATORS: List[str] = [
        r"\b(sebi|securities and exchange board of india)\b",
        r"\b(rbi (?:imposes|penalizes|bars|bans|penalty|curbs|orders|mandates|repo rate|monetary policy|crr|slr))\b",
        r"\b(cci approves?|competition commission of india approves?|nclt approves?|drhp|listing|ipo|qip)\b",
        r"\b(gst council|tax rate|customs duty|pli scheme|fdi policy|mining royalty|royalty taxation)\b",
        r"\b(nifty|nifty 50|sensex|bse|nse|bse sensex)\b",
    ]

    # Corporate hard event markers (belong strictly to INDIA BUSINESS, never DOMESTIC)
    CORPORATE_HARD_ACTION_PATTERNS: List[str] = [
        r"\b(net profit|q[1-4] results|q[1-4] profit|q[1-4] revenue|standalone net profit|consolidated net profit|quarterly results|quarterly profit|quarterly revenue|quarterly earnings|profit rises|profit falls|profit jumps|profit surges|profit drops|revenue rises|revenue falls|profit up|profit down|pat rises|pat falls|pat up|pat down)\b",
        r"\b(acquires?|acquisition|to buy|to acquire|acquisition of|buys|bought|buyout|stake sale|stake purchase|block deal|divests|sells stake|calls off proposed acquisition)\b",
        r"\b(raises? (?:funds?|funding|capital|equity)|series [a-z]|files? drhp|launches? ipo|ipo open|listing on)\b",
        r"\b(announces? buyback|share buyback|board approves dividend|interim dividend|appointed ceo|named md|named ceo)\b",
        r"\b(signs? (?:₹|rs\.?|inr|crore|\$|usd)[\s\w]*(?:contract|deal|order|mou)|secures? (?:₹|rs\.?|inr|crore|\$|usd)[\s\w]*(?:contract|deal|order))\b",
        r"\b(semiconductor incentive|commercial agreement|joint venture|capex plan)\b",
    ]

    # Domestic general national news, government, politics, courts, science, defense, infrastructure, weather -> DOMESTIC
    DOMESTIC_NATIONAL_NEWS_PATTERNS: List[str] = [
        # Courts & Constitutional / Public Law
        r"\b(supreme court|high court|chief justice|cji|law commission|judiciary|constitutional bench|sc bench|quashes|stays order|nationwide ruling|orders probe)\b",
        # Space, Science & Technology
        r"\b(isro|chandrayaan|gaganyaan|aditya-l1|satellite launch|rocket launch|pslv|gslv|space mission)\b",
        # Defence, Armed Forces & National Security (policy/systems, not commercial company contracts)
        r"\b(drdo|missile test|flight test|indian army|indian navy|indian air force|iaf|border security|anti-terror|nia|defence procurement policy|armed forces)\b",
        # Politics, Parliament & Central Government Public Policy
        r"\b(union cabinet|cabinet approves?|cabinet clears?|cabinet nod|parliament|lok sabha|rajya sabha|bill passed|new national law|centre notifies|centre announces|election commission|ec|eci|assembly election|bypoll|pmo)\b",
        # Public Infrastructure & Public Transport
        r"\b(railway corridor|vande bharat|national highway|expressway|metro rail|bullet train|mega bridge|airport terminal|national infrastructure|smart cities)\b",
        # Weather, Environment & Disasters
        r"\b(cyclone|landslide|cloudburst|flood|earthquake|imd alert|heatwave|red alert|rescue operation|ndrf|western ghats)\b",
        # Health, Education & Social Policy
        r"\b(national education policy|nep|ncert|ugc|neet|ayushman bharat|vaccination drive|icmr|who alert|public health|food security)\b",
    ]

    # Foreign Geographies, Demonyms & International Transaction Keywords
    FOREIGN_GEOGRAPHY_AND_DEMONYMS: List[str] = [
        r"\b(australia|australian|australia's|canadian|canada|canada's|u\.s\.|us\b|united states|u\.k\.|uk\b|british|britain|european|europe|germany|german|france|french|japan|japanese|china|chinese|singapore|south korea|korean|sweden|swedish|switzerland|swiss|netherlands|dutch|new zealand|saudi|uae|dubai|brazil|brazilian|israel|israeli|mexico|mexican)\b",
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
        Deterministically classify an event or article with explicit rationale across DOMESTIC, INDIA, INTERNATIONAL.
        Precedence:
        1. Global Regulators & International Macro -> INTERNATIONAL
        2. International Entities with Global/USD context -> INTERNATIONAL
        3. Indian Corporate Hard Event (earnings/M&A/deals/contracts/corporate regulatory enforcement) -> INDIA BUSINESS
        4. Indian National Public Affairs (ISRO/Supreme Court/Defence/Cabinet Policy/Disaster) -> DOMESTIC
        5. General Indian Business / Currency Signals -> INDIA BUSINESS
        6. Discovery Region Prior
        7. Default Fallback
        """
        title_lower = (title or "").lower()
        companies_text = " ".join(companies or []).lower()
        figures_text = " ".join(financial_figures or []).lower()
        title_and_entities = f"{title_lower} {companies_text}"

        # 1. Check Global Regulators / Macro Policy
        for pat in self.INTERNATIONAL_REGULATORY_AND_POLICY:
            m = re.search(pat, title_lower)
            if m:
                return NewsCategory.INTERNATIONAL, f"Global regulatory / macro policy match: '{m.group(0)}'"

        # 2. Entity Matches in Title or Companies Involved
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

        # 3. Currency and Unit Signals
        local_text = f"{title_lower} {figures_text}"
        has_indian_currency_local = any(re.search(pat, local_text) for pat in self.INDIAN_CURRENCY_AND_UNITS)
        has_dollar_local = bool(re.search(r"(\$|\b(usd|us dollar|dollars?)\b)", local_text))

        # Check action patterns
        is_corporate_hard_event = any(re.search(pat, title_lower) for pat in self.CORPORATE_HARD_ACTION_PATTERNS)
        is_business_policy_or_reg = any(re.search(pat, title_lower) for pat in self.INDIAN_BUSINESS_POLICY_AND_REGULATORS)
        is_domestic_national_news = any(re.search(pat, title_lower) for pat in self.DOMESTIC_NATIONAL_NEWS_PATTERNS)

        # Special business-precedence check: commercial court ruling / royalty / tax / business impact
        is_commercial_legal_event = is_domestic_national_news and any(
            w in title_lower for w in ["royalty", "taxation", "tax", "acquisition", "merger", "insolvency", "nclt", "penalty", "bank", "licence", "license fee", "telecom", "spectrum", "mining royalty"]
        )

        # RULE A: International entity with dollar or international discovery prior
        if intl_entity_matches:
            matched_intl = intl_entity_matches[0]
            if not has_indian_currency_local or has_dollar_local or discovery_region == NewsCategory.INTERNATIONAL:
                return NewsCategory.INTERNATIONAL, f"International entity '{matched_intl}' with global context"

        # RULE B: Corporate Hard Events & Business Regulatory Enforcement by/affecting Indian Entities -> INDIA BUSINESS
        if is_corporate_hard_event or is_business_policy_or_reg or is_commercial_legal_event:
            if indian_entity_matches:
                matched_name = indian_entity_matches[0]
                if sponsor_matches:
                    return NewsCategory.INDIA, f"Indian target/subject entity match: '{matched_name}' with financial sponsor '{sponsor_matches[0]}'"
                return NewsCategory.INDIA, f"Indian entity match (corporate hard event): '{matched_name}'"
            if has_indian_currency_local or "india" in title_lower or is_business_policy_or_reg or is_commercial_legal_event:
                return NewsCategory.INDIA, "Indian corporate action / financial market regulatory event"

        # RULE C: Indian National Public Affairs (ISRO, Supreme Court constitutional, Defence, Cabinet Policy, Disasters) -> DOMESTIC
        if is_domestic_national_news and not is_corporate_hard_event:
            return NewsCategory.DOMESTIC, "India national public affairs / policy / science / constitutional event"

        if discovery_region == NewsCategory.DOMESTIC and not is_corporate_hard_event:
            return NewsCategory.DOMESTIC, "Preserved Domestic discovery pool prior"

        # RULE D: Target / Subject entity domicile (Corporate India general)
        if indian_entity_matches:
            matched_name = indian_entity_matches[0]
            if sponsor_matches:
                return NewsCategory.INDIA, f"Indian target/subject entity '{matched_name}' with financial sponsor '{sponsor_matches[0]}'"
            if intl_entity_matches:
                return NewsCategory.INDIA, f"Indian entity '{matched_name}' transacting with international entity '{intl_entity_matches[0]}'"
            return NewsCategory.INDIA, f"Indian entity match: '{matched_name}'"

        # RULE E: Explicit Indian geography in title
        if re.search(r"\b(india|indian|india's)\b", title_lower):
            if not (intl_entity_matches and has_dollar_local and not has_indian_currency_local):
                return NewsCategory.INDIA, "Explicit Indian geography in headline"

        # RULE F: Strong Currency Signals in Title/Headline
        title_has_inr = any(re.search(pat, title_lower) for pat in self.INDIAN_CURRENCY_AND_UNITS)
        if title_has_inr:
            return NewsCategory.INDIA, "Indian currency in title / event headline (crore/₹/lakh)"

        # RULE G: Financial Sponsor transactions
        if sponsor_matches:
            if has_indian_currency_local or "india" in title_lower:
                return NewsCategory.INDIA, f"Financial sponsor '{sponsor_matches[0]}' in Indian transaction context"
            if discovery_region == NewsCategory.INTERNATIONAL or has_dollar_local:
                return NewsCategory.INTERNATIONAL, f"Financial sponsor '{sponsor_matches[0]}' in international transaction context"

        # RULE G2: Explicit Foreign Geography & Corporate Context Override (Prior Overriding)
        has_foreign_geo = any(re.search(pat, title_lower) for pat in self.FOREIGN_GEOGRAPHY_AND_DEMONYMS)
        if has_foreign_geo and not indian_entity_matches and not has_indian_currency_local and "india" not in title_lower:
            return NewsCategory.INTERNATIONAL, "Explicit foreign geography / company evidence overrides discovery prior"

        # RULE H: Discovery Region Prior Protection
        if discovery_region == NewsCategory.INTERNATIONAL:
            return NewsCategory.INTERNATIONAL, "Preserved International discovery pool prior"

        if discovery_region == NewsCategory.INDIA:
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
        disc_region = None
        if articles:
            combined_content += " " + " ".join((a.content_text or "")[:200] for a in articles)
            for a in articles:
                if a.category and a.category != NewsCategory.UNKNOWN:
                    disc_region = a.category
                    break
        if not disc_region and event.metadata and "discovery_region" in event.metadata:
            disc_region = event.metadata["discovery_region"]
        if not disc_region:
            disc_region = event.event_category

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
