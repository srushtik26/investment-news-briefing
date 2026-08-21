"""
Event Region Classification Engine.

Deterministically classifies news events into INDIA or INTERNATIONAL based on:
1. Primary affected company / entity jurisdiction.
2. Transaction focus (Indian asset, rupee currency ₹/crore/lakh).
3. Regulatory / Macro policy jurisdiction (RBI, SEBI, Nifty vs Fed, ECB, SEC).

CRITICAL RULE: Publisher location / domain does NOT determine event geography.
"""

from typing import Any, List, Optional, Set
import re

from app.models.article import Article
from app.models.enums import NewsCategory
from app.models.event import Event


class EventRegionClassifier:
    """
    Deterministic rule-based classifier determining whether an event belongs to the
    INDIA or INTERNATIONAL briefing section.
    """

    # Indian Regulators, Government Bodies, Indices & Policy Keywords
    INDIAN_REGULATORY_AND_POLICY: List[str] = [
        r"\b(rbi|reserve bank of india)\b",
        r"\b(sebi|securities and exchange board of india)\b",
        r"\b(nifty|nifty 50|sensex|bse|nse|bse sensex)\b",
        r"\b(cci|competition commission of india)\b",
        r"\b(union budget|finance ministry|nirmala sitharaman|gst council|gst)\b",
        r"\b(dgca|trai|irdai|ed|enforcement directorate|cbdt|cbic)\b",
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
        
        # Financial & Banking
        r"\b(hdfc|hdfc bank|hdfc life|hdfc ergo|icici|icici bank|icici prudential)\b",
        r"\b(sbi|state bank of india|axis bank|kotak|kotak mahindra|indusind|yes bank|idfc)\b",
        r"\b(punjab national bank|pnb|bank of baroda|canara bank|union bank of india)\b",
        r"\b(zerodha|groww|angel one|upstox)\b",
        
        # IT & Tech Services
        r"\b(infosys|wipro|hcl tech|hcl technologies|tech mahindra|l&t technology|ltimindtree|mphasis|coforge)\b",
        
        # Pharma & Healthcare
        r"\b(sun pharma|dr reddy|dr\. reddy|cipla|lupin|aurobindo|divi's|zydus|mankind pharma|biocon|apollo hospitals)\b",
        
        # Consumer, Retail & Digital Startups
        r"\b(zomato|swiggy|paytm|phonepe|zepto|blinkit|shiprocket|nykaa|ola|ola electric|oyo|byju's|delhivery|meesho|mamaearth|lenskart|cred|urban company)\b",
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

    # Major Global / International Companies & Entities
    INTERNATIONAL_ENTITIES: List[str] = [
        # Big Tech & AI
        r"\b(apple|microsoft|google|alphabet|meta|facebook|amazon|nvidia|tesla)\b",
        r"\b(openai|anthropic|stripe|openrouter|mistral|deepmind|arm holdings|arm)\b",
        r"\b(tsmc|asml|intel|amd|qualcomm|broadcom|micron|samsung|sony)\b",
        
        # Global Finance & Banking
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

    def classify(
        self,
        title: str,
        content: Optional[str] = None,
        financial_figures: Optional[List[str]] = None,
        companies: Optional[List[str]] = None,
    ) -> NewsCategory:
        """
        Deterministically classify an event or article into NewsCategory.INDIA or NewsCategory.INTERNATIONAL.
        """
        full_text = f"{title} {(content or '')[:400]}".lower()
        facts_text = " ".join(financial_figures or []).lower()
        companies_text = " ".join(companies or []).lower()
        combined = f"{full_text} {facts_text} {companies_text}"

        # 1. Check Explicit Indian Regulators / Policy / Macro Indicators
        for pat in self.INDIAN_REGULATORY_AND_POLICY:
            if re.search(pat, full_text):
                return NewsCategory.INDIA

        # 2. Check Explicit Global Regulators / Policy / Macro Indicators
        for pat in self.INTERNATIONAL_REGULATORY_AND_POLICY:
            if re.search(pat, full_text):
                return NewsCategory.INTERNATIONAL

        # 3. Check for Indian Entities mentioned in title
        title_has_indian = any(re.search(pat, title.lower()) for pat in self.INDIAN_ENTITIES)
        title_has_intl = any(re.search(pat, title.lower()) for pat in self.INTERNATIONAL_ENTITIES)

        # 4. Check for Currency Signals (₹, crore, lakh, INR)
        has_indian_currency = any(re.search(pat, title.lower()) for pat in self.INDIAN_CURRENCY_AND_UNITS) or \
                              any(re.search(pat, facts_text) for pat in self.INDIAN_CURRENCY_AND_UNITS)

        # If Indian company is in title AND NO global company is the primary subject:
        if title_has_indian and not title_has_intl:
            return NewsCategory.INDIA

        # If Global company is in title AND NO Indian company is in title:
        if title_has_intl and not title_has_indian:
            # Special case: Global company investing in Indian entity / Indian rupee asset
            if has_indian_currency and ("india" in combined or any(re.search(pat, combined) for pat in self.INDIAN_ENTITIES)):
                return NewsCategory.INDIA
            return NewsCategory.INTERNATIONAL

        # If BOTH Indian and Global entities are in the title:
        if title_has_indian and title_has_intl:
            if has_indian_currency or "india" in combined:
                return NewsCategory.INDIA
            return NewsCategory.INDIA

        # 5. Check Body / Context signals if title has neither specific entity list match
        body_has_indian_entity = any(re.search(pat, combined) for pat in self.INDIAN_ENTITIES)
        body_has_intl_entity = any(re.search(pat, combined) for pat in self.INTERNATIONAL_ENTITIES)

        if has_indian_currency and (body_has_indian_entity or "india" in combined):
            return NewsCategory.INDIA

        if body_has_intl_entity and not body_has_indian_entity:
            return NewsCategory.INTERNATIONAL

        if body_has_indian_entity and not body_has_intl_entity:
            return NewsCategory.INDIA

        if has_indian_currency:
            return NewsCategory.INDIA

        # Default fallback
        return NewsCategory.INTERNATIONAL

    def classify_article(self, article: Article) -> NewsCategory:
        """Classify an Article instance."""
        return self.classify(
            title=article.title,
            content=article.content_text,
            companies=[article.source_name] if article.source_name else [],
        )

    def classify_event(self, event: Event, articles: Optional[List[Article]] = None) -> NewsCategory:
        """Classify an Event instance."""
        combined_content = event.description or ""
        if articles:
            combined_content += " " + " ".join((a.content_text or "")[:200] for a in articles)
        return self.classify(
            title=event.canonical_title,
            content=combined_content,
            financial_figures=event.financial_figures,
            companies=event.companies_involved,
        )
