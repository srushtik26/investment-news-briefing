from __future__ import annotations
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

    INDIAN_CAPITAL_MARKETS_INFRASTRUCTURE: List[str] = [
        r"\b(national stock exchange|nse\b|nse's|bombay stock exchange|bse\b|bse's)\b",
        r"\b(indian listed exchanges?|indian stock exchange|indian exchanges?|india's national stock exchange)\b",
        r"\b(sebi|securities and exchange board of india)\b",
        r"\b(reserve bank of india|rbi\b)\b",
        r"\b(indian capital markets?|india capital markets?|indian ipo|drhp|draft red herring prospectus)\b",
    ]

    INDIAN_CURRENCY_AND_UNITS: List[str] = [
        r"₹",
        r"\b(rs\.?|inr|rupees?|crore|cr|lakh|lakhs)\b",
    ]

    INDIAN_ENTITIES: List[str] = [
        r"\b(tata|tata motors|tata steel|tata power|tcs|tata consultancy)\b",
        r"\b(reliance|reliance industries|ril|reliance retail|reliance jio|jio)\b",
        r"\b(adani|adani enterprises|adani ports|adani green|adani power)\b",
        r"\b(larsen & toubro|l&t|nxt-infra|nxt infra|nxt-infra trust)\b",
        r"\b(mahindra|mahindra & mahindra|m&m|bajaj|bajaj auto|bajaj finance|bajaj finserv)\b",
        r"\b(itc|maruti|maruti suzuki|hero motocorp|tvs motor|eicher)\b",
        r"\b(vedanta|jsw|jsw steel|hindalco|grasim|ultratech|shree cement)\b",
        r"\b(ntpc|ongc|coal india|power grid corp|power grid corporation|powergrid|pgcil|bharat petroleum|bpcl|ioc|iocl|hpcl|gail)\b|\bpower grid\b(?=\s+(?:corp|corporation|ltd|limited)\b)|\bpower grid\b(?=.*?\b(?:india|indian|bse|nse|shares|stocks?|transmission|substation|dividend|pat|capex|q[1-4])\b)",
        r"\b(bhel|bel|hal|hindustan aeronautics|mazagon dock)\b",
        r"\b(tube investments|tube investments of india|tii|shanthi gears)\b",
        r"\b(jbm auto|jbm|godrej|piramal|havells|voltas|polycab|kei industries)\b",
        r"\b(hdfc|hdfc bank|hdfc life|hdfc ergo|icici|icici bank|icici prudential)\b",
        r"\b(sbi|state bank of india|axis bank|kotak|kotak mahindra|indusind|yes bank|idfc|idfc first)\b",
        r"\b(idbi|idbi bank|kfin|kfin technologies|kfintech)\b",
        r"\b(punjab national bank|pnb|bank of baroda|canara bank|union bank of india)\b",
        r"\b(zerodha|groww|angel one|upstox|bandhan bank|federal bank|rbl bank|au small finance bank)\b",
        r"\b(infosys|wipro|hcl tech|hcl technologies|tech mahindra|l&t technology|ltimindtree|mphasis|coforge)\b",
        r"\b(sun pharma|dr reddy|dr\. reddy|cipla|lupin|aurobindo|divi's|zydus|mankind pharma|biocon|apollo hospitals)\b",
        r"\b(manipal|manipal health|manipal hospitals|max healthcare|fortis|narayana hrudayalaya|medanta|tynor|tynor orthotics|fluence pharma)\b",
        r"\b(zomato|swiggy|paytm|phonepe|zepto|blinkit|shiprocket|nykaa|ola|ola electric|oyo|byju's|delhivery|meesho|mamaearth|honasa|honasa consumer|lenskart|cred|urban company)\b",
        r"\b(bharti airtel|airtel|vodafone idea|vi\s+telecom|bsnl|mtnl)\b",
        r"\b(welspun|welspun corp|inorbit|inorbit malls|prozone|prozone malls|kedaara|kedaara capital|c2i|c2i semiconductors|airtel payments bank|ardee|ardee industries|ardee infrastructure)\b",
        r"\b(national stock exchange|nse\b|bombay stock exchange|bse\b)\b",
        r"\b\w+\s+(?:of\s+india|india\s+ltd|india\s+limited)\b",
    ]

    INTERNATIONAL_REGULATORY_AND_POLICY: List[str] = [
        r"\b(fed|federal reserve|jerome powell|fomc)\b",
        r"\b(ecb|european central bank|bank of england|boe|bank of japan|boj)\b",
        r"\b(us sec|u\.s\. sec|sec charges|sec investigation|sec approves|us securities and exchange commission|ftc|federal trade commission|doj)\b",
        r"\b(eu commission|european commission|european union|imf|world bank)\b",
        r"\b(wall street|s&p 500|nasdaq|dow jones|ftse 100|nikkei|dax|cac 40)\b",
        r"\b(us gdp|us inflation|us cpi|eurozone|us treasury|treasury yields)\b",
    ]

    FINANCIAL_SPONSORS: List[str] = [
        r"\b(general atlantic|bain capital|bain|fairfax|fairfax financial|temasek|gic|softbank|tiger global|peak xv|sequoia|warburg pincus|advent international|eqt|cpibb|blackstone|kkr|carlyle|brookfield)\b",
    ]

    INTERNATIONAL_ENTITIES: List[str] = [
        r"\b(salesforce|crowdstrike|okta|snowflake|palantir|oracle|ibm|cisco|dell|hp|servicenow|adobe|uber|airbnb|spotify)\b",
        r"\b(apple|microsoft|google|alphabet|meta|facebook|amazon|nvidia|tesla)\b",
        r"\b(openai|anthropic|stripe|openrouter|mistral|deepmind|arm holdings|arm|hugging face)\b",
        r"\b(tsmc|asml|intel|amd|qualcomm|broadcom|micron|samsung|sony|alibaba|tencent|bytedance)\b",
        r"\b(arctos|atlanta falcons|falcons|lakers|los angeles lakers|nfl|nba|mlb|f1|formula 1)\b",
        r"\b(goldman sachs|jpmorgan|morgan stanley|citigroup|citi|bank of america|bofa|wells fargo)\b",
        r"\b(blackrock|blackstone|kkr|carlyle|apollo global|lcn capital|lcn capital partners)\b",
        r"\b(hsbc|barclays|ubs|bnp paribas|deutsche bank|credit agricole|santander)\b",
        r"\b(rio tinto|arcadium lithium|bhp|glencore|anglo american|vale)\b",
        r"\b(boeing|airbus|lockheed martin|general electric|ge aerospace)\b",
        r"\b(pfizer|moderna|astrazeneca|novartis|roche|novo nordisk|eli lilly|leo pharma|mersana|mitsubishi tanabe)\b",
        r"\b(toyota|volkswagen|bmw|mercedes-benz|stellantis|ford|general motors|byd)\b",
        r"\b(nestle|unilever|procter & gamble|p&g|pepsico|coca-cola|lvmh|nike)\b",
        r"\b(dazn|espn|sky sports|discovery\+|peacock|paramount\+|hbo|warner bros|universal pictures|sony pictures)\b",
        r"\b(netflix|disney|disney\+|hulu|spotify|tencent music|iheartmedia)\b",
        r"\b(comcast|charter communications|at\&t|verizon|t-mobile|deutsche telekom)\b",
        r"\b(manchester united|manchester city|real madrid|barcelona|chelsea fc|arsenal fc|liverpool fc)\b",
    ]

    INDIAN_BUSINESS_POLICY_AND_REGULATORS: List[str] = [
        r"\b(sebi|securities and exchange board of india)\b",
        r"\b(rbi (?:imposes|penalizes|bars|bans|penalty|curbs|orders|mandates|repo rate|monetary policy|crr|slr))\b",
        r"\b(cci approves?|competition commission of india approves?|nclt approves?|files? drhp|sebi (?:nod|approves?|clears?)|bse|nse|mainboard listing)\b",
        r"\b(gst council|tax rate|customs duty|pli scheme|fdi policy|mining royalty|royalty taxation)\b",
        r"\b(nifty|nifty 50|sensex|bse|nse|bse sensex)\b",
    ]

    CORPORATE_HARD_ACTION_PATTERNS: List[str] = [
        r"\b(net profit|q[1-4] results|q[1-4] profit|q[1-4] revenue|standalone net profit|consolidated net profit|quarterly results|quarterly profit|quarterly revenue|quarterly earnings|profit rises|profit falls|profit jumps|profit surges|profit drops|revenue rises|revenue falls|profit up|profit down|pat rises|pat falls|pat up|pat down)\b",
        r"\b(acquires?|acquisition|to buy|to acquire|acquisition of|buys|bought|buyout|stake sale|stake purchase|block deal|divests|sells stake|calls off proposed acquisition)\b",
        r"\b(raises? (?:funds?|funding|capital|equity)|series [a-z]|files? drhp|launches? ipo|ipo open|listing on)\b",
        r"\b(announces? buyback|share buyback|board approves dividend|interim dividend|appointed ceo|named md|named ceo)\b",
        r"\b(signs? (?:₹|rs\.?|inr|crore|\$|usd)[\s\w]*(?:contract|deal|order|mou)|secures? (?:₹|rs\.?|inr|crore|\$|usd)[\s\w]*(?:contract|deal|order))\b",
        r"\b(semiconductor incentive|commercial agreement|joint venture|capex plan)\b",
    ]

    DOMESTIC_NATIONAL_NEWS_PATTERNS: List[str] = [
        r"\b(supreme court|high court|chief justice|cji|law commission|judiciary|constitutional bench|sc bench|quashes|stays order|nationwide ruling|orders probe)\b",
        r"\b(isro|chandrayaan|gaganyaan|aditya-l1|satellite launch|rocket launch|pslv|gslv|space mission)\b",
        r"\b(drdo|missile test|flight test|indian army|indian navy|indian air force|iaf|border security force|bsf\b|indo-tibetan border police|itbp\b|line of control|line of actual control|anti-terror|nia|defence procurement policy|armed forces)\b",
        r"\b(union cabinet|cabinet approves?|cabinet clears?|cabinet nod|parliament|lok sabha|rajya sabha|bill passed|new national law|centre notifies|centre announces|election commission|ec|eci|assembly election|bypoll|pmo)\b",
        r"\b(railway corridor|vande bharat|national highway|expressway|metro rail|bullet train|mega bridge|airport terminal|national infrastructure|smart cities)\b",
        r"\b(cyclone|landslide|cloudburst|flood|earthquake|imd alert|heatwave|red alert|rescue operation|ndrf|western ghats)\b",
        r"\b(national education policy|nep|ncert|ugc|neet|ayushman bharat|vaccination drive|icmr|who alert|public health|food security)\b",
    ]

    INDIAN_STATES_AND_CITIES: List[str] = [
        r"\b(andhra pradesh|arunachal pradesh|assam|bihar|chhattisgarh|goa|gujarat|haryana|himachal pradesh|himachal|jharkhand|karnataka|kerala|madhya pradesh|maharashtra|manipur|meghalaya|mizoram|nagaland|odisha|orissa|punjab|rajasthan|sikkim|tamil nadu|telangana|tripura|uttar pradesh|uttarakhand|bengal|west bengal|delhi|new delhi|jammu|kashmir|ladakh|puducherry|chandigarh)\b",
        r"\b(mumbai|bengaluru|bangalore|hyderabad|chennai|kolkata|ahmedabad|pune|surat|jaipur|lucknow|kanpur|nagpur|indore|bhopal|patna|vadodara|ghaziabad|ludhiana|agra|nashik|faridabad|meerut|rajkot|varanasi|srinagar|aurangabad|dhanbad|amritsar|navi mumbai|allahabad|prayagraj|ranchi|howrah|coimbatore|jabalpur|gwalior|vijayawada|jodhpur|madurai|raipur|kota|guwahati|solapur|hubli|dharwad|bareilly|moradabad|mysore|mysuru|tiruchirappalli|tiruppur|gurgaon|gurugram|aligarh|jalandhar|bhubaneswar|salem|warangal|noida|kochi|dehradun|mohali)\b",
        r"\b(bjp|congress|aap|aam aadmi party|tmc|trinamool|dmk|aiadmk|bsp|sp|samajwadi party|akhilesh yadav|mayawati|rahul gandhi|amit shah|narendra modi|yogi adityanath|kejriwal|mamata banerjee|sharad pawar|uddhav thackeray|nda|india bloc)\b",
        r"\b(chief minister|cm\b|deputy cm|governor|vidhan sabha|panchayat|municipal corporation|municipal corporations|local body polls?|local body elections?|assembly elections?|bypolls?|delimitation|dalit|tribal)\b",
    ]

    GENERIC_INDIAN_PRINCIPAL_PATTERNS: List[str] = [
        r"\b(?:india|indian)\s+(?:company|companies|firm|firms|group|groups|conglomerate|conglomerates|startup|startups|corp|corporation|corporations|bank|banks|lender|lenders|major|majors|player|players|it\s+major|operator|operators|entity|entities|pharma\s+(?:company|firm)|tech\s+(?:company|firm)|refiner|refiners|carmaker|carmakers|automaker|automakers)\b",
        r"\b(?:india-based|indian-origin|indian-owned)\b",
    ]

    INDIAN_PRINCIPAL_ACTION_PATTERNS: List[str] = [
        r"\b(?:acquires?|acquired|acquisition|to acquire|buys?|bought|buyout|takeover)\b",
        r"\b(?:invests?|invested|investment|investing|to invest|joint venture|jv)\b",
        r"\b(?:wins?|won|secures?|secured|signs?|signed|bags?|bagged)\s+(?:[\w\s\$₹€£\d\.,]+)?(?:contract|order|orders|deal|pact|mandate|project)\b",
        r"\b(?:raises?|raised|raising|fundraise|funding|capital|debt|equity|bonds?|notes?)\b",
        r"\b(?:reports?|reported|reporting)\s+(?:[\w\s]+)?(?:earnings|results|profit|revenue|pat|net income)\b",
        r"\b(?:expands?|expanded|expanding|expansion|sets? up|establishes?|launches?|opens?)\s+(?:[\w\s]+)?(?:abroad|overseas|globally|foreign|in\s+[a-z]+|operations|facility|plant|hub|office)\b",
    ]

    FOREIGN_GEOGRAPHY_AND_DEMONYMS: List[str] = [
        r"\b(australia|australian|australia's|canadian|canada|canada's|u\.s\.(?!\w)|us\b|united states|u\.k\.(?!\w)|uk\b|british|britain|european|europe|germany|german|france|french|japan|japanese|china|chinese|singapore|south korea|korean|sweden|swedish|switzerland|swiss|netherlands|dutch|new zealand|saudi arabia|saudi|riyadh|jeddah|cuba|cuban|havana|uae|dubai|brazil|brazilian|israel|israeli|mexico|mexican|nepal|tibet|russia|russian|ukraine|ukrainian|taiwan|taiwanese|pakistan|bangladesh|sri lanka|kuwait|qatar|bahrain|oman|venezuela|turkey|turkish|egypt|south africa|nigeria|kenya|indonesia|malaysia|thailand|vietnam|philippines)\b",
    ]


    @classmethod
    def has_positive_indian_nexus(cls, text: str) -> bool:
        """Check if text contains positive Indian domestic nexus."""
        if not text:
            return False
        text_lower = text.lower()
        if re.search(r"\b(india|indian|india's)\b", text_lower):
            return True
        for pat_list in (
            cls.INDIAN_REGULATORY_AND_POLICY,
            cls.INDIAN_CURRENCY_AND_UNITS,
            cls.INDIAN_ENTITIES,
            cls.INDIAN_BUSINESS_POLICY_AND_REGULATORS,
            cls.DOMESTIC_NATIONAL_NEWS_PATTERNS,
            cls.INDIAN_STATES_AND_CITIES,
        ):
            if any(re.search(pat, text_lower) for pat in pat_list):
                return True
        return False

    @classmethod
    def is_indian_principal_acting_abroad(
        cls,
        title: str,
        companies: Optional[List[str]] = None,
        content: Optional[str] = None,
    ) -> bool:
        """
        Generic rule: An Indian principal company acting abroad qualifies as India business.
        Examples that qualify:
        - Indian company acquires foreign target
        - Indian company invests overseas
        - Indian company wins overseas contract
        - Indian company raises capital / debt
        - Indian company reports earnings
        - Indian company expands abroad

        Still rejects:
        - Foreign company with only incidental India mention
        - Stories where India is not economically central
        """
        if not title:
            return False
        title_lower = title.lower()
        companies_text = " ".join(companies or []).lower()
        combined_text = f"{title_lower} {companies_text}"

        # 1. Check if an Indian entity or generic Indian principal is present in title or companies
        has_named_indian_entity = any(re.search(pat, combined_text) for pat in cls.INDIAN_ENTITIES)
        has_generic_indian_principal = any(re.search(pat, combined_text) for pat in cls.GENERIC_INDIAN_PRINCIPAL_PATTERNS)
        has_india_nexus = has_named_indian_entity or has_generic_indian_principal

        if not has_india_nexus:
            return False

        # 2. Check for incidental foreign company mention:
        # If an international entity is the primary grammatical subject and Indian entity/mention is incidental:
        # e.g., "Apple reports earnings in US, mentions India supply chain" -> Apple is subject, not Indian principal.
        has_intl_entity = any(re.search(pat, title_lower) for pat in cls.INTERNATIONAL_ENTITIES)
        if has_intl_entity:
            for intl_pat in cls.INTERNATIONAL_ENTITIES:
                m_intl = re.search(intl_pat, title_lower)
                if m_intl:
                    intl_start = m_intl.start()
                    indian_pos = -1
                    for ind_pat in (cls.INDIAN_ENTITIES + cls.GENERIC_INDIAN_PRINCIPAL_PATTERNS):
                        m_ind = re.search(ind_pat, title_lower)
                        if m_ind:
                            indian_pos = m_ind.start()
                            break
                    # If intl entity appears near beginning (pos < 25) and Indian mention is later (after intl entity)
                    if intl_start < 25 and (indian_pos == -1 or indian_pos > intl_start + 15):
                        return False

        # 3. Check for corporate business action
        has_action = any(re.search(pat, title_lower) for pat in cls.INDIAN_PRINCIPAL_ACTION_PATTERNS) or any(
            re.search(pat, title_lower) for pat in cls.CORPORATE_HARD_ACTION_PATTERNS
        )
        return bool(has_action)

    def verify_india_business_nexus(
        self,
        event: Event,
        article: Optional[Article] = None,
    ) -> Tuple[bool, str]:
        """
        Canonical deterministic India business nexus check.

        Accepts a story for the India section ONLY when at least ONE strong
        India signal is present (categories A–F below).

        Rejects with INDIA_NEXUS_REJECT when:
          - The primary entity is foreign
          - AND the event geography is foreign
          - AND no meaningful Indian business impact exists

        Keeps materiality SEPARATE from region eligibility:
        a high-materiality foreign story still fails this check.

        Signal categories accepted:
          A. Company/entity is Indian or India-listed (named entity OR generic Indian principal)
          B. Source explicitly states India as operating/transaction/regulatory geography
          C. BSE / NSE / SEBI / RBI / Indian ministry / Indian court / regulator materially involved
          D. Transaction involves Indian assets, company, or subsidiary
          E. Financial results belong to an Indian company
          F. Multinational story with quantified or concrete India business impact

        Reject conditions (INDIA_NEXUS_REJECT):
          - Foreign company + foreign geography + no meaningful Indian business impact
          - India appears only incidentally (one-word mention, generic "global/Asia" context)
          - US/Europe/China macro story with no Indian relevance
        """
        if not event:
            return False, "INDIA_NEXUS_REJECT: no event provided"

        if hasattr(event, "metadata") and isinstance(event.metadata, dict) and "india_nexus_verified" in event.metadata:
            return bool(event.metadata["india_nexus_verified"]), str(event.metadata.get("india_nexus_reason", ""))

        title_text = f"{event.canonical_title or ''} {article.title if article else ''}".lower().strip()
        body_text = (article.content_text or "")[:4000].lower() if article else ""
        desc_text = (event.description or "").lower()
        companies_text = " ".join(event.companies_involved or []).lower()
        context_text = f"{title_text} {body_text} {desc_text} {companies_text}"

        # ----------------------------------------------------------------
        # SIGNAL A: Named Indian entity or generic Indian principal in title/companies
        # ----------------------------------------------------------------
        try:
            from app.ranking.watchlist import is_watchlist_company
            is_watchlist = is_watchlist_company(context_text)[0]
        except Exception:
            is_watchlist = False

        has_named_indian_entity = any(re.search(pat, title_text) for pat in self.INDIAN_ENTITIES) or \
                                   any(re.search(pat, companies_text) for pat in self.INDIAN_ENTITIES) or \
                                   is_watchlist
        has_generic_indian_principal = any(re.search(pat, context_text) for pat in self.GENERIC_INDIAN_PRINCIPAL_PATTERNS)
        # Also check for Indian principal acting abroad (Indian co acquiring/investing overseas)
        has_indian_principal_abroad = self.is_indian_principal_acting_abroad(
            title_text,
            companies=event.companies_involved if event else None,
            content=body_text,
        )
        signal_a = has_named_indian_entity or has_generic_indian_principal or has_indian_principal_abroad

        # ----------------------------------------------------------------
        # SIGNAL B: India as operating/transaction/regulatory geography
        # (requires material mention — not just the word "India" incidentally)
        # ----------------------------------------------------------------
        has_foreign_geo = any(re.search(pat, title_text) for pat in self.FOREIGN_GEOGRAPHY_AND_DEMONYMS)
        has_intl_entity_title = any(re.search(pat, title_text) for pat in self.INTERNATIONAL_ENTITIES)
        has_intl_entity_companies = any(re.search(pat, companies_text) for pat in self.INTERNATIONAL_ENTITIES)
        has_intl_entity = has_intl_entity_title or has_intl_entity_companies

        india_material_patterns = [
            r"\bindia(?:'s)?\s+(?:operations?|business|market|subsidiary|unit|arm|division|revenue|capex|sales|manufacturing|plant|facility|presence|office|headquarters|acquisition|investment|deal|regulatory|banking)\b",
            r"\b(?:in|into|within|across|for)\s+india\b",
            r"\bindia-(?:based|focused|listed|domiciled|incorporated|specific|facing)\b",
            r"\bindian\s+(?:market|operations?|business|subsidiary|unit|assets?|customers?|revenue|regulator|law|court|entity|company|companies|bank|lender|borrower|investor|promoter|partner|manufacturing|plant)\b",
            r"\b(?:listed|incorporated|domiciled|registered|headquartered)\s+in\s+india\b",
            r"\bindia\s+(?:acquisition|merger|stake|investment|capex|plant|facility|deal|joint venture|jv)\b",
        ]
        has_indian_geo = any(re.search(pat, context_text) for pat in self.INDIAN_STATES_AND_CITIES)
        has_biz_infrastructure = bool(re.search(
            r"\b(?:plant|facility|factory|foundry|project|order|contract|operations?|business|presence|office|headquarters|unit|capex|investment|hub|port|rail|refinery|terminal|mine)\b",
            context_text, re.IGNORECASE
        ))
        signal_b = any(re.search(pat, context_text) for pat in india_material_patterns) or \
                   (has_indian_geo and has_biz_infrastructure and not (has_foreign_geo and has_intl_entity))

        # ----------------------------------------------------------------
        # SIGNAL C: BSE / NSE / SEBI / RBI / Indian regulatory body materially involved
        # ----------------------------------------------------------------
        signal_c = any(re.search(pat, context_text) for pat in self.INDIAN_REGULATORY_AND_POLICY) or \
                   any(re.search(pat, context_text) for pat in self.INDIAN_BUSINESS_POLICY_AND_REGULATORS) or \
                   any(re.search(pat, context_text) for pat in self.INDIAN_CAPITAL_MARKETS_INFRASTRUCTURE)

        has_intl_reg_title = any(re.search(pat, title_text) for pat in self.INTERNATIONAL_REGULATORY_AND_POLICY)
        if (has_intl_reg_title or has_foreign_geo) and not signal_a:
            # If headline is explicitly an international central bank / macro policy event or foreign geography,
            # and no Indian entity/principal is in title, an Indian regulator in body copy is merely comparative context.
            has_indian_reg_in_title = (
                any(re.search(pat, title_text) for pat in self.INDIAN_REGULATORY_AND_POLICY)
                or any(re.search(pat, title_text) for pat in self.INDIAN_BUSINESS_POLICY_AND_REGULATORS)
                or any(re.search(pat, title_text) for pat in self.INDIAN_CAPITAL_MARKETS_INFRASTRUCTURE)
            )
            if not has_indian_reg_in_title:
                signal_c = False

        # ----------------------------------------------------------------
        # SIGNAL D: Transaction involves Indian assets / company / subsidiary
        # ----------------------------------------------------------------
        has_indian_currency = any(re.search(pat, context_text) for pat in self.INDIAN_CURRENCY_AND_UNITS)
        has_corp_action = bool(re.search(
            r"\b(?:secures?|bags?|wins?|awarded|signs?|order|orders|contract|contracts|deal|deals|"
            r"capex|investment|invests?|invested|expansion|facility|plant|foundry|network|partnership|"
            r"acquisition|acquires?|acquired|merger|to buy|buyout|stake|funding|raises?|drhp|ipo|concession)\b",
            context_text, re.IGNORECASE
        ))
        signal_d_currency = has_indian_currency and has_corp_action and not (has_foreign_geo and has_intl_entity)

        india_transaction_patterns = [
            r"\b(?:acquires?|acquired|buys?|bought|purchases?|purchased|merger with|merges? with|takeover of|invest(?:s|ed|ing)?\s+in)\s+(?:[\w\s&]{1,40}?\s+)?\b(?:india|indian|bse|nse)\b",
            r"\b(?:india|indian)\s+(?:asset|assets|subsidiary|unit|arm|division|stake|shareholding|equity|plant|factory|property|land|portfolio)\b",
            r"\b(?:sell(?:s|ing)?|divest(?:s|ing)?|exit(?:s|ing)?)\s+(?:[\w\s&]{1,40}?\s+)?\b(?:india|indian)\s+(?:asset|assets|business|unit|subsidiary|stake)\b",
            r"\bstake\s+in\s+(?:[\w\s&]{1,40}?\s+)?\b(?:india|indian)\b",
            r"\b(?:india|indian)\s+(?:jv|joint\s+venture|partnership)\b",
        ]
        signal_d = any(re.search(pat, context_text) for pat in india_transaction_patterns) or signal_d_currency

        # ----------------------------------------------------------------
        # SIGNAL E: Financial results of an Indian company
        # ----------------------------------------------------------------
        has_results_pattern = bool(re.search(
            r"\b(?:q[1-4]\s+(?:fy\d{2,4}\s+)?(?:results?|profit|revenue|pat|earnings|net profit)|"
            r"standalone\s+(?:net\s+profit|revenue|results?)|"
            r"consolidated\s+(?:net\s+profit|revenue|results?)|"
            r"quarterly\s+(?:results?|profit|revenue|earnings)|"
            r"(?:profit|revenue|pat|ebitda)\s+(?:jumps?|surges?|rises?|falls?|drops?|up|down))\b",
            context_text, re.IGNORECASE
        ))
        signal_e = (
            (has_named_indian_entity or has_generic_indian_principal) and (has_results_pattern or has_indian_currency)
        ) or (
            has_results_pattern and has_indian_currency and not (has_foreign_geo and has_intl_entity)
        )

        # ----------------------------------------------------------------
        # SIGNAL F: Multinational story with quantified/concrete India impact
        # ----------------------------------------------------------------
        india_quantified_patterns = [
            r"\b(?:india|indian)\s+(?:revenue|sales|profit|capex|investment|orders?|contracts?|customers?)\s+(?:of|at|worth|totaling|amounting to)\s+(?:₹|rs\.?|inr|crore|lakh|\$|usd|\d)",
            r"\b(?:₹|rs\.?|inr|crore|lakh)\s*\d[\d,\.]*\s*(?:crore|lakh|million|billion)?\s+(?:in|for|from|to)\s+india\b",
            r"\b(?:india|indian)\s+(?:unit|arm|subsidiary)\s+(?:reports?|posts?|records?|logs?)\s+(?:₹|rs\.?|inr|\$)\b",
            r"\bindia\s+(?:operations?|business)\s+(?:grew?|grew|declines?|declined|surged?|tumbled?)\b",
        ]
        signal_f = any(re.search(pat, context_text) for pat in india_quantified_patterns)

        # ----------------------------------------------------------------
        # CHECK FOR FOREIGN-ONLY STORY SIGNALS
        # ----------------------------------------------------------------
        # Incidental India mention check (the word "India" appears in context but ONLY once
        # and without any of the strong signals A-F)
        india_raw_count = len(re.findall(r"\bindia\b", context_text))
        india_only_incidental = (india_raw_count <= 1) and not signal_a and not signal_b and not signal_c and not signal_d and not signal_e and not signal_f

        def _finish(is_pass: bool, reason_str: str) -> Tuple[bool, str]:
            if hasattr(event, "metadata") and isinstance(event.metadata, dict):
                event.metadata["india_nexus_verified"] = is_pass
                event.metadata["india_nexus_reason"] = reason_str
            return is_pass, reason_str

        # ----------------------------------------------------------------
        # INTERNATIONAL CENTRAL BANK / MACRO EVENT REJECTION
        # ----------------------------------------------------------------
        if has_intl_reg_title and not signal_a and not signal_d and not signal_e and not signal_f:
            reason = (
                f"INDIA_NEXUS_REJECT: primary headline is international central bank / macro policy event. "
                f"title='{event.canonical_title[:80]}'"
            )
            logger.info("INDIA_NEXUS_REJECT: title=\"%s\" reason=\"international central bank/policy in headline\"",
                        event.canonical_title[:80])
            return _finish(False, reason)

        # ----------------------------------------------------------------
        # FOREIGN-LEAKAGE REJECTION: primary entity foreign + event foreign + no India impact
        # ----------------------------------------------------------------
        if has_foreign_geo and has_intl_entity and not signal_a and not signal_b and not signal_c and not signal_d and not signal_e and not signal_f:
            reason = (
                f"INDIA_NEXUS_REJECT: foreign entity + foreign geography detected, no meaningful India business impact. "
                f"title='{event.canonical_title[:80]}'"
            )
            logger.info("INDIA_NEXUS_REJECT: title=\"%s\" reason=\"foreign entity + foreign geography, no India impact\"",
                        event.canonical_title[:80])
            return _finish(False, reason)

        if has_foreign_geo and not has_intl_entity and not signal_a and not signal_b and not signal_c and not signal_d and not signal_e and not signal_f:
            reason = (
                f"INDIA_NEXUS_REJECT: foreign geography, no India signals present. "
                f"title='{event.canonical_title[:80]}'"
            )
            logger.info("INDIA_NEXUS_REJECT: title=\"%s\" reason=\"foreign geography, no India signals\"",
                        event.canonical_title[:80])
            return _finish(False, reason)

        if india_only_incidental and (has_foreign_geo or has_intl_entity):
            reason = (
                f"INDIA_NEXUS_REJECT: India appears only incidentally (count={india_raw_count}), "
                f"primary story is foreign. title='{event.canonical_title[:80]}'"
            )
            logger.info("INDIA_NEXUS_REJECT: title=\"%s\" reason=\"India only incidental mention\"",
                        event.canonical_title[:80])
            return _finish(False, reason)

        # ----------------------------------------------------------------
        # DISTRIBUTION/MARKET-TARGET ONLY REJECTION
        # A foreign company describing India as a *distribution* or *sales target*
        # market is NOT an India-section story unless an Indian entity/asset is involved.
        # ----------------------------------------------------------------
        distribution_target_patterns = [
            r"\b(?:to launch|launched|launching|will launch|plans? to launch|expands? to|entering|enters?)\s+(?:in|into)\s+india\b",
            r"\bindia\s+(?:launch|debut|rollout|expansion)\b",
            r"\b(?:distribut|content|streaming|subscription|service)\s+(?:deal|agreement|tie-?up)\s+(?:for|in)\s+india\b",
            r"\bindia\s+(?:distribution|streaming|content)\s+deal\b",
        ]
        is_distribution_target_only = (
            has_intl_entity
            and any(re.search(pat, context_text) for pat in distribution_target_patterns)
            and not signal_a  # no Indian principal
            and not signal_c  # no Indian regulator
            and not signal_d  # no Indian asset/transaction
            and not signal_e  # no Indian financial results
        )
        if is_distribution_target_only:
            reason = (
                f"INDIA_NEXUS_REJECT: foreign entity expanding/distributing into India (market-target only), "
                f"no Indian principal/asset/regulator. title='{event.canonical_title[:80]}'"
            )
            logger.info("INDIA_NEXUS_REJECT: title=\"%s\" reason=\"distribution-target-only (foreign entity)\"",
                        event.canonical_title[:80])
            return _finish(False, reason)

        # ----------------------------------------------------------------
        # ACCEPT if at least one strong signal found
        # ----------------------------------------------------------------
        active_signals = []
        if signal_a:
            active_signals.append("A:indian_entity_or_principal")
        if signal_b:
            active_signals.append("B:india_geography")
        if signal_c:
            active_signals.append("C:indian_regulator")
        if signal_d:
            active_signals.append("D:indian_transaction")
        if signal_e:
            active_signals.append("E:indian_results")
        if signal_f:
            active_signals.append("F:india_quantified_impact")

        if active_signals:
            reason = f"INDIA_NEXUS_PASS: signals=[{', '.join(active_signals)}]"
            logger.info("INDIA_NEXUS_PASS: title=\"%s\" signals=%s",
                        event.canonical_title[:80], active_signals)
            return _finish(True, reason)

        # ----------------------------------------------------------------
        # FALLBACK: No strong India signal found — reject
        # ----------------------------------------------------------------
        reason = (
            f"INDIA_NEXUS_REJECT: no strong India business signal found (A-F). "
            f"title='{event.canonical_title[:80]}'"
        )
        logger.info("INDIA_NEXUS_REJECT: title=\"%s\" reason=\"no strong India business signal\"",
                    event.canonical_title[:80])
        return _finish(False, reason)

    def verify_region_eligibility(
        self,
        event: Event,
        article: Optional[Article] = None,
        requested_region: Any = NewsCategory.DOMESTIC,
    ) -> Tuple[bool, str]:
        """
        Verify whether an event/candidate is strictly eligible for the requested briefing section.
        Used BEFORE editorial selection to reject invalid region candidates and trigger backfill.
        Ensures perfect parity with Stage 9 Final Validation Checks 1 & 2.
        """
        req_reg_str = (
            requested_region.value.lower()
            if hasattr(requested_region, "value")
            else str(requested_region).lower()
        )
        title_text = f"{event.canonical_title} {article.title if article else ''}".lower()
        body_text = article.content_text[:3000].lower() if (article and article.content_text) else ""
        context_text = f"{title_text} {body_text} {(event.description or '').lower()}"

        has_foreign_geo = any(re.search(pat, title_text) for pat in self.FOREIGN_GEOGRAPHY_AND_DEMONYMS)
        has_indian_entity = any(re.search(pat, title_text) for pat in self.INDIAN_ENTITIES)
        has_indian_currency = any(re.search(pat, title_text) for pat in self.INDIAN_CURRENCY_AND_UNITS)

        if req_reg_str == "domestic":
            has_india_mention_dom = bool(re.search(r"\b(india|indian|india's|delhi|mumbai|bengaluru|isro|centre|parliament)\b", title_text))
            # Strict parity with Stage 9 Check 1
            if has_foreign_geo and not has_indian_entity and not has_indian_currency and not has_india_mention_dom:
                return False, "Domestic story has foreign country subject and lacks Indian domestic nexus"
            if not self.has_positive_indian_nexus(context_text):
                return False, "lacks Indian domestic nexus"
            if has_foreign_geo and not self.has_positive_indian_nexus(title_text):
                return False, "Domestic story has foreign country subject and lacks Indian domestic headline nexus"
            return True, "valid Indian domestic nexus"

        elif req_reg_str == "india":
            # Use the canonical India business nexus helper for all India eligibility checks
            return self.verify_india_business_nexus(event, article)

        elif req_reg_str == "international":
            from app.verification.international import is_geopolitical_market_impact_eligible
            is_geo_elig, geo_reason = is_geopolitical_market_impact_eligible(event, article)
            if not is_geo_elig:
                return False, geo_reason
            return True, "valid International candidate"


        return True, "unrestricted section"

    def classify_with_reason(
        self,
        title: str,
        content: Optional[str] = None,
        financial_figures: Optional[List[str]] = None,
        companies: Optional[List[str]] = None,
        discovery_region: Optional[NewsCategory] = None,
    ) -> Tuple[NewsCategory, str]:
        """Deterministically classify an event or article with explicit rationale."""
        title_lower = (title or "").lower()
        content_lower = (content or "").lower()
        context_text = f"{title_lower} {content_lower}"
        companies_text = " ".join(companies or []).lower()
        figures_text = " ".join(financial_figures or []).lower()

        # Bug 3: Indian capital markets infrastructure vs. Global central bank / regulatory policy
        # Classify by PRIMARY SUBJECT + EVENT GEOGRAPHY + BUSINESS IMPACT.
        for pat in self.INTERNATIONAL_REGULATORY_AND_POLICY:
            m = re.search(pat, title_lower)
            if m:
                has_indian_in_title = any(re.search(p, title_lower) for p in self.INDIAN_ENTITIES) or \
                                      any(re.search(p, title_lower) for p in self.INDIAN_CAPITAL_MARKETS_INFRASTRUCTURE)
                if not has_indian_in_title:
                    return NewsCategory.INTERNATIONAL, f"Global regulatory / macro policy match: '{m.group(0)}'"

        for pat in self.INDIAN_CAPITAL_MARKETS_INFRASTRUCTURE:
            m_cm = re.search(pat, title_lower)
            if m_cm:
                return NewsCategory.INDIA, f"Indian capital markets infrastructure / regulatory event: '{m_cm.group(0)}'"

        indian_entity_matches = [
            re.search(pat, title_lower).group(0)
            for pat in self.INDIAN_ENTITIES
            if re.search(pat, title_lower)
        ] or [
            re.search(pat, companies_text).group(0)
            for pat in self.INDIAN_ENTITIES
            if re.search(pat, companies_text)
        ]

        intl_entity_matches = [
            re.search(pat, title_lower).group(0)
            for pat in self.INTERNATIONAL_ENTITIES
            if re.search(pat, title_lower)
        ] or [
            re.search(pat, companies_text).group(0)
            for pat in self.INTERNATIONAL_ENTITIES
            if re.search(pat, companies_text)
        ]

        sponsor_matches = [
            re.search(pat, title_lower).group(0)
            for pat in self.FINANCIAL_SPONSORS
            if re.search(pat, title_lower)
        ] or [
            re.search(pat, companies_text).group(0)
            for pat in self.FINANCIAL_SPONSORS
            if re.search(pat, companies_text)
        ]

        local_text = f"{title_lower} {figures_text}"
        has_indian_currency_local = any(re.search(pat, local_text) for pat in self.INDIAN_CURRENCY_AND_UNITS)
        has_dollar_local = bool(re.search(r"(\$|\b(usd|us dollar|dollars?)\b)", local_text))

        is_corporate_hard_event = any(re.search(pat, title_lower) for pat in self.CORPORATE_HARD_ACTION_PATTERNS)
        is_business_policy_or_reg = any(re.search(pat, title_lower) for pat in self.INDIAN_BUSINESS_POLICY_AND_REGULATORS)
        is_domestic_national_news = any(re.search(pat, title_lower) for pat in self.DOMESTIC_NATIONAL_NEWS_PATTERNS)

        is_commercial_legal_event = is_domestic_national_news and any(
            w in title_lower for w in ["royalty", "taxation", "tax", "acquisition", "merger", "insolvency", "nclt", "penalty", "bank", "licence", "license fee", "telecom", "spectrum", "mining royalty"]
        )

        from app.ranking.watchlist import get_portfolio_company_role
        from app.verification.materiality import CONCRETE_PORTFOLIO_EVENT_PATTERNS
        is_pf, pf_company, pf_role, pf_eligible = get_portfolio_company_role(title_lower, companies_text)
        if is_pf and pf_eligible:
            has_corporate_business_event = (
                is_corporate_hard_event
                or is_business_policy_or_reg
                or is_commercial_legal_event
                or any(re.search(pat, title_lower) for _, pat in CONCRETE_PORTFOLIO_EVENT_PATTERNS)
                or bool(re.search(r"\b(?:commissioning|commissions?|commissioned|expansion|capex|orders?|contracts?|projects?|wins?|bags?|secures?|signs?|pact|deal|acquisition|merger|profit|revenue|shares|quarterly|earnings|dividend|buyback|qip|ipo|ncd|bonds?|issuance|plant|facility|terminal|concession|drhp|investment)\b", title_lower))
            )
            if has_corporate_business_event:
                return NewsCategory.INDIA, f"Portfolio company '{pf_company}' corporate/business event ({pf_role}) routed to INDIA"

        has_foreign_geo = any(re.search(pat, title_lower) for pat in self.FOREIGN_GEOGRAPHY_AND_DEMONYMS)
        has_india_nexus_title = self.has_positive_indian_nexus(title_lower)
        has_india_nexus_context = self.has_positive_indian_nexus(context_text)
        has_india_mention = has_india_nexus_title or has_india_nexus_context

        # Check if an Indian principal company is acting abroad (e.g. cross-border acquisition, investment, contract, debt, expansion)
        # Rescues cross-border transactions from being classified as INTERNATIONAL due to foreign geography, foreign counterparties, or dollar currency
        if (has_foreign_geo or intl_entity_matches or has_dollar_local) and self.is_indian_principal_acting_abroad(
            title_lower, companies=companies, content=content_lower
        ):
            return NewsCategory.INDIA, "Indian principal company acting abroad qualifies as India business"

        if has_foreign_geo and not has_india_nexus_title:
            return NewsCategory.INTERNATIONAL, "Explicit foreign geography / non-India subject routed to INTERNATIONAL: foreign subject without Indian headline nexus"

        if intl_entity_matches:
            matched_intl = intl_entity_matches[0]
            if not has_indian_currency_local or has_dollar_local or discovery_region == NewsCategory.INTERNATIONAL:
                return NewsCategory.INTERNATIONAL, f"International entity '{matched_intl}' with global context"

        if is_corporate_hard_event or is_business_policy_or_reg or is_commercial_legal_event:
            if indian_entity_matches:
                matched_name = indian_entity_matches[0]
                if sponsor_matches:
                    return NewsCategory.INDIA, f"Indian target/subject entity match: '{matched_name}' with financial sponsor '{sponsor_matches[0]}'"
                return NewsCategory.INDIA, f"Indian entity match (corporate hard event): '{matched_name}'"
            if has_indian_currency_local or "india" in title_lower or is_business_policy_or_reg or is_commercial_legal_event or has_india_nexus_title:
                return NewsCategory.INDIA, "Indian corporate action / financial market regulatory event"

        if is_domestic_national_news and not is_corporate_hard_event:
            return NewsCategory.DOMESTIC, "India national public affairs / policy / science / constitutional event"

        # Discovery is only a prior, not proof. A Domestic candidate must have
        # real positive Indian nexus.
        if discovery_region == NewsCategory.DOMESTIC and not is_corporate_hard_event:
            has_foreign_subject = any(
                re.search(pat, title_lower) for pat in self.FOREIGN_GEOGRAPHY_AND_DEMONYMS
            )
            # If the headline focuses on a foreign subject, India must be materially
            # involved in the headline itself to qualify as Domestic.
            if has_foreign_subject and not has_india_nexus_title:
                return NewsCategory.INTERNATIONAL, "Domestic discovery prior rejected: foreign subject without Indian headline nexus"

            if has_india_nexus_title or has_india_nexus_context:
                return NewsCategory.DOMESTIC, "Domestic discovery prior confirmed by Indian nexus"

            return NewsCategory.INTERNATIONAL, "Domestic discovery prior rejected: no Indian domestic nexus"

        if indian_entity_matches:
            matched_name = indian_entity_matches[0]
            if sponsor_matches:
                return NewsCategory.INDIA, f"Indian target/subject entity '{matched_name}' with financial sponsor '{sponsor_matches[0]}'"
            if intl_entity_matches:
                return NewsCategory.INDIA, f"Indian entity '{matched_name}' transacting with international entity '{intl_entity_matches[0]}'"
            return NewsCategory.INDIA, f"Indian entity match: '{matched_name}'"

        if re.search(r"\b(india|indian|india's)\b", title_lower):
            if not (intl_entity_matches and has_dollar_local and not has_indian_currency_local):
                return NewsCategory.INDIA, "Explicit Indian geography in headline"

        title_has_inr = any(re.search(pat, title_lower) for pat in self.INDIAN_CURRENCY_AND_UNITS)
        if title_has_inr:
            return NewsCategory.INDIA, "Indian currency in title / event headline (crore/₹/lakh)"

        if sponsor_matches:
            if has_indian_currency_local or "india" in title_lower:
                return NewsCategory.INDIA, f"Financial sponsor '{sponsor_matches[0]}' in Indian transaction context"
            if discovery_region == NewsCategory.INTERNATIONAL or has_dollar_local:
                return NewsCategory.INTERNATIONAL, f"Financial sponsor '{sponsor_matches[0]}' in international transaction context"

        if has_foreign_geo and not indian_entity_matches and not has_indian_currency_local and not has_india_mention:
            return NewsCategory.INTERNATIONAL, "Explicit foreign geography / company evidence overrides discovery prior"

        if discovery_region == NewsCategory.INTERNATIONAL:
            return NewsCategory.INTERNATIONAL, "Preserved International discovery pool prior"

        if discovery_region == NewsCategory.INDIA:
            return NewsCategory.INDIA, "Preserved Indian discovery pool prior"

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
        cat, _ = self.classify_with_reason(
            title=title,
            content=content,
            financial_figures=financial_figures,
            companies=companies,
            discovery_region=discovery_region,
        )
        return cat

    def classify_article(self, article: Article) -> NewsCategory:
        cat, reason = self.classify_with_reason(
            title=article.title,
            content=article.content_text,
            companies=[article.source_name] if article.source_name else [],
            discovery_region=article.category,
        )
        return cat

    def classify_event(self, event: Event, articles: Optional[List[Article]] = None) -> NewsCategory:
        combined_content = event.description or ""
        disc_region = None
        if articles:
            combined_content += " " + " ".join((a.content_text or "")[:3000] for a in articles)
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


_default_region_classifier = EventRegionClassifier()


def verify_region_eligibility(
    event: Event,
    article: Optional[Article] = None,
    requested_region: Any = NewsCategory.DOMESTIC,
) -> Tuple[bool, str]:
    return _default_region_classifier.verify_region_eligibility(
        event=event, article=article, requested_region=requested_region
    )


def has_positive_indian_nexus(text: str) -> bool:
    return _default_region_classifier.has_positive_indian_nexus(text)


def verify_india_business_nexus(
    event: Event,
    article: Optional[Article] = None,
) -> Tuple[bool, str]:
    """Module-level wrapper for the canonical India business nexus check."""
    return _default_region_classifier.verify_india_business_nexus(event, article)


