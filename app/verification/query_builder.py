"""
Shared High-Precision Event Query Builder for RSS & SerpAPI Corroboration.

Extracts structured anchors (primary company, counterparty, event action, numeric facts)
while strictly eliminating generic market noise (e.g., 'Wall Street', 'Stock Market', 'Synthetic EVs').
"""

import re
from typing import List, Optional, Set, Tuple

from app.models.article import Article
from app.models.event import Event


# Strict blacklist of words/phrases that must NEVER be treated as corporate entities
GENERIC_ENTITY_BLACKLIST: Set[str] = {
    "wall street", "stock market", "share market", "markets", "investors",
    "shares", "stock", "stocks", "nse", "bse", "sensex", "nifty", "street",
    "quarter", "quarterly", "results", "earnings", "annual report", "quarterly results", "annual results", "financial results",
    "ceo", "cfo", "managing director", "analysts", "sources", "deal", "deals", "block deal", "block deals", "bulk deal", "bulk deals", "block", "bulk",
    "synthetic", "synthetic evs", "demand", "outlook", "turnaround", "trend", "lost",
    "india", "company", "companies", "group", "business", "industry", "sector",
    "tyres", "ev", "evs", "sales", "report", "reports", "today", "live", "update",
    "updates", "page", "exclusive", "says", "said", "first", "second", "third",
    "fourth", "q1", "q2", "q3", "q4", "fy24", "fy25", "fy26", "fy27",
    "billion", "million", "crore", "lakh", "percent", "pct", "rs", "inr", "usd",
    "reuters", "bloomberg", "cnbc", "journal", "associated press", "press trust",
    "ani", "pti", "the economic times", "business standard", "livemint", "financial express",
    "moneycontrol", "ndtv", "the hindu", "indian express", "fortune", "guardian",
    "financial times", "wall street journal", "wsj", "ft",
    "controlling stake", "majority stake", "minority stake", "stake", "promoter stake",
    "controlling", "majority", "minority", "promoter", "promoters",
    "stake sale", "acquisition", "transaction", "promoter stake sale",
    "institutional stake sale", "equity", "completes acquisition", "completes",
    "ai", "revenue", "bank", "banking", "technology", "tech", "technologies",
    "chip", "chips", "centres", "data centres", "strong ai", "ai chip",
    "energy", "green", "green energy", "jewellery", "jewellers", "jewels",
    "retail", "gold", "metals", "commodities", "crypto", "bitcoin", "etf", "etfs",
    "bonds", "fund", "funds", "capital", "ventures", "finance", "financial",
    "properties", "property", "developer", "developers",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "january", "february", "march", "april", "june", "july", "august", "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
}

# Prefix tokens to strip from proper noun matches (e.g. 'Wall Street Walmart' -> 'Walmart')
PREFIXES_TO_STRIP: List[str] = [
    "block deals: ", "block deal: ", "bulk deals: ", "bulk deal: ", "block deals ", "block deal ", "bulk deals ", "bulk deal ",
    "promoter ", "promoters ", "promoter's ", "promoters' ",
    "wall street ", "stock market ", "market ", "shares of ", "shares in ",
    "reports of ", "exclusive: ", "view: ", "update: ", "breaking: ",
    "completes acquisition of controlling stake in ", "completes acquisition of majority stake in ",
    "completes acquisition of ", "acquires ", "buys ", "sale of ", "controlling stake in ",
    "strong ai chip demand powers ", "ai chip demand powers ",
]

EVENT_ACTION_PATTERNS = [
    (r"\b(to buy|buys|acquires?|acquisition|takeover|merger|merges|buyout|stake purchase)\b", "acquisition"),
    (r"\b(block deal|stake sale|equity changes hands|promoter stake sale|institutional stake sale|bulk deal)\b", "block deal"),
    (r"\b(net profit|profit rises|profit falls|revenue rises|revenue falls|earnings beat|earnings miss|q[1-4] results|quarterly profit)\b", "results"),
    (r"\b(raises funding|funding round|qip|rights issue|capital raise|funds raised)\b", "fundraise"),
    (r"(?:ipo|drhp|listing)", "ipo"),
    (r"\b(rbi|sebi|sec|doj|cci|penalty|fine|ban|order|charges|probe|inquiry)\b", "regulatory"),
    (r"\b(plant investment|capacity expansion|new plant|capex|manufacturing facility)\b", "capex"),
    (r"\b(appoints|ceo|md|managing director|resigns|steps down)\b", "leadership"),
    (r"\b(joint venture|partnership|tie-up|collaboration)\b", "partnership"),
    (r"\b(outlook|guidance|forecast)\b", "outlook"),
    (r"\b(turnaround)\b", "turnaround"),
]


class EventQueryBuilder:
    """
    Deterministic anchor extractor and query builder shared between
    ActiveCorroborator (RSS) and SerpAPICorroborator.
    """

    @classmethod
    def clean_entity(cls, candidate: str) -> Optional[str]:
        """Sanitize and validate an extracted entity candidate."""
        if not candidate:
            return None
        tok = candidate.strip().strip("'\".,;:()[]{}")
        if not tok or len(tok) < 2 or tok.lower() in GENERIC_ENTITY_BLACKLIST:
            return None
        numeric_units = {"b", "m", "cr", "crore", "billion", "million", "percent", "pct"}
        if all(
            word.isdigit() or word.lower() in numeric_units
            for word in tok.split()
        ):
            return None

        # Strip generic prefixes
        tok_low = tok.lower()
        for prefix in PREFIXES_TO_STRIP:
            if tok_low.startswith(prefix):
                tok = tok[len(prefix):].strip()
                tok_low = tok.lower()

        # Strip possessive 's or '
        tok = re.sub(r"('s|’s|')$", "", tok).strip()
        tok_low = tok.lower()

        # Strip trailing connector words / metric words
        for trailing in (
            " revenue and profit", " revenue and", " profit and", " results and",
            " revenue", " profit", " net profit", " results", " shares", " stock", " deals", " deal", " stake",
        ):
            if tok_low.endswith(trailing):
                tok = tok[:-len(trailing)].strip()
                tok_low = tok.lower()

        # Strip reporting quarters/years from end of entity (e.g. 'Company X Q1' -> 'Company X')
        for period in (" q1", " q2", " q3", " q4", " fy24", " fy25", " fy26", " fy27"):
            if tok_low.endswith(period):
                tok = tok[:-len(period)].strip()
                tok_low = tok.lower()

        # Strip legal company suffixes to extract clean corporate roots
        for suffix in (" private limited", " pvt ltd", " limited", " ltd", " private", " pvt", " inc", " corp", " corporation"):
            if tok_low.endswith(suffix):
                tok = tok[:-len(suffix)].strip()
                tok_low = tok.lower()

        # Check if entity is a date pattern or contains years / calendar months
        if re.search(r"\b(19\d\d|20\d\d)\b", tok) or re.search(r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b", tok_low):
            return None

        # Check if entity contains generic results / reporting phrases
        if re.search(r"\b(quarterly results|annual results|financial results|q[1-4] results|fy\d{2} results|board meeting|share price)\b", tok_low):
            return None

        # Check blacklist
        if not tok or len(tok) < 2 or tok_low in GENERIC_ENTITY_BLACKLIST:
            return None

        # Check if all constituent words belong to GENERIC_ENTITY_BLACKLIST
        tok_words = [w for w in re.findall(r"[a-zA-Z0-9]+", tok_low) if not w.isdigit()]
        if tok_words and all(w in GENERIC_ENTITY_BLACKLIST for w in tok_words):
            return None

        # Discard multi-word phrases that are all lowercase or purely common dictionary words
        words = tok.split()
        if len(words) > 4:
            return None

        return tok

    @classmethod
    def extract_entities(cls, article: Article, event: Optional[Event] = None) -> List[str]:
        """
        Extract clean company/corporate entities from event metadata and article title.
        """
        clean_entities: List[str] = []
        seen = set()

        # 1. Prefer known companies from event model
        if event and event.companies_involved:
            for comp in event.companies_involved:
                c = cls.clean_entity(comp)
                if c and c.lower() not in seen:
                    clean_entities.append(c)
                    seen.add(c.lower())

        # 2. Extract multi-word proper nouns and alphanumeric corporate names from title
        title = article.title or ""

        # Check leading subject before quarterly results / action verbs
        subject_match = re.match(
            r"^([A-Z0-9&'\.\-]+(?:\s+[A-Z0-9&'\.\-]+){0,4})\s+(?:Q[1-4]|FY\d{2}|Quarterly Results|Annual Results|net profit|profit|revenue|shares?|stock|falls|jumps|surges|slumps|tumbles|gains|drops|posts|misses|beats|reports?|announces?|acquires?|buys|sells|raises?|files?|appoints?|resigns?|merges?|secures?|wins?|bags?|gets?|faces?|plans?|to acquire|to buy|to sell|to raise)\b",
            title,
            flags=re.IGNORECASE,
        )
        if subject_match:
            lead_sub = subject_match.group(1).strip(" :,;()-")
            c = cls.clean_entity(lead_sub)
            if c and c.lower() not in seen:
                clean_entities.append(c)
                seen.add(c.lower())

        # Pre-split title by major transaction verbs and prepositions to isolate corporate entity phrases
        split_pattern = r"(?:\s+(?:completes acquisition of controlling stake in|completes acquisition of majority stake in|completes acquisition of minority stake in|completes acquisition of stake in|completes acquisition of|acquisition of controlling stake in|acquisition of majority stake in|acquisition of minority stake in|acquisition of stake in|acquisition of|deal to buy|agrees to buy|to acquire|to buy|acquires|buys|sells|offloads|invests in|in|of|for|from|via|with|and|by)\s+)"
        segments = re.split(split_pattern, title, flags=re.IGNORECASE)

        for seg in segments:
            seg_clean = seg.strip(" :,;()-")
            # Look for 1-4 capitalized or alphanumeric word sequences in segment (supporting apostrophes & hyphens)
            for matched_str in re.findall(r"\b(?:[0-9]+[a-zA-Z0-9&'\.\-]+|[A-Z][a-zA-Z0-9&'\.\-]*)(?:\s+(?:[0-9]+[a-zA-Z0-9&'\.\-]+|[A-Z][a-zA-Z0-9&'\.\-]*)){0,3}\b", seg_clean):
                c = cls.clean_entity(matched_str)
                if c and c.lower() not in seen:
                    clean_entities.append(c)
                    seen.add(c.lower())

        # Check known major corporate entity keywords in title
        KNOWN_TITLE_ENTITIES = [
            "Salesforce", "CrowdStrike", "Okta", "Snowflake", "Palantir", "Oracle",
            "Nvidia", "Broadcom", "Apple", "Microsoft", "Google", "Alphabet", "Amazon", "Tesla",
            "Meta", "Intel", "AMD", "Qualcomm", "TSMC", "ASML", "Samsung", "Sony", "OpenAI", "Anthropic", "Hugging Face",
            "Target", "Walmart", "Boeing", "Airbus", "Pfizer", "Moderna", "AstraZeneca", "Novartis",
            "BHP", "Rio Tinto", "Glencore", "Vale", "Shein", "nVent", "Peet", "Ingenia",
            "General Atlantic", "Rubicon Research", "Ribbit Capital", "Temple", "Longevous", "Deepinder Goyal",
            "Reliance", "Tata", "Adani", "Infosys", "Wipro", "HDFC", "ICICI", "SBI", "ITC",
            "Maruti", "Mahindra", "Bajaj", "L&T", "KFin", "Piramal", "Welspun", "DMart",
            "Groww", "Honasa", "Inorbit", "Zerodha", "boAt", "Vikas Ecotech", "Juniper Green Energy",
        ]
        for ent in KNOWN_TITLE_ENTITIES:
            if re.search(rf"\b{re.escape(ent)}\b", title, re.IGNORECASE):
                c = cls.clean_entity(ent)
                if c and c.lower() not in seen:
                    clean_entities.append(c)
                    seen.add(c.lower())

        # Fallback multi-word regex across full title
        for matched_str in re.findall(r"\b(?:[0-9]+[a-zA-Z0-9&'\.\-]+|[A-Z][a-zA-Z0-9&'\.\-]*)(?:\s+(?:[0-9]+[a-zA-Z0-9&'\.\-]+|[A-Z][a-zA-Z0-9&'\.\-]*)){1,3}\b", title):
            c = cls.clean_entity(matched_str)
            if c and c.lower() not in seen:
                clean_entities.append(c)
                seen.add(c.lower())

        return clean_entities[:6]

    @classmethod
    def extract_numbers(cls, text: str) -> List[str]:
        """Extract distinctive numeric facts/percentages/deal figures."""
        matches = re.findall(
            r"(?:₹|\$|€|£|rs\.?\s*)?[\d,]+(?:\.\d+)?\s*(?:%|crore|cr|billion|b|million|m)?",
            text,
            re.IGNORECASE,
        )
        cleaned = []
        for m in matches:
            tok = m.strip()
            # Discard trivial numbers
            if any(c.isdigit() for c in tok) and tok.lower() not in ("1", "2", "3", "4", "5", "10", "100"):
                cleaned.append(tok)
        return cleaned[:2]

    @classmethod
    def detect_event_action(cls, title: str) -> Optional[str]:
        """Identify canonical action keyword from title."""
        t_low = title.lower()
        for pat, canonical in EVENT_ACTION_PATTERNS:
            if re.search(pat, t_low):
                return canonical
        return None

    @classmethod
    def build_anchor_query(cls, article: Article, event: Optional[Event] = None) -> str:
        """
        Construct a concise, high-precision anchor query.
        Used by both SerpAPICorroborator and ActiveCorroborator.
        """
        title = article.title or ""
        entities = cls.extract_entities(article, event=event)
        action = cls.detect_event_action(title)
        numbers = cls.extract_numbers(title)

        primary_entity = entities[0] if entities else None
        second_entity = entities[1] if len(entities) >= 2 else None

        query_parts: List[str] = []
        if primary_entity:
            query_parts.append(f'"{primary_entity}"' if " " in primary_entity else primary_entity)
        if second_entity and second_entity != primary_entity:
            query_parts.append(f'"{second_entity}"' if " " in second_entity else second_entity)

        # Check for specific quarter in title (e.g. "Q1", "Q2", "Q3", "Q4")
        quarter_match = re.search(r"\b(Q[1-4])\b", title, re.IGNORECASE)
        if quarter_match:
            query_parts.append(f'"{quarter_match.group(1).upper()}"')

        # Check for specific earnings phrase in title (e.g. "adjusted profit", "net profit", "quarterly profit")
        earnings_match = re.search(r"\b(adjusted profit|net profit|operating profit|quarterly profit|revenue rises|profit jumps|profit rises)\b", title, re.IGNORECASE)
        if earnings_match:
            query_parts.append(f'"{earnings_match.group(1).lower()}"')
        elif action:
            query_parts.append(action)

        # Distinctive financial facts / percentages
        if numbers:
            for num in numbers[:2]:
                if num not in query_parts:
                    query_parts.append(num)

        if not query_parts and title:
            # Fallback: take clean headline words
            stopwords = {"the", "a", "an", "and", "for", "with", "its", "in", "on", "at", "to", "of", "is", "by", "from", "as", "after", "says"}
            tokens = [
                w for w in re.findall(r"\b[a-zA-Z0-9']+\b", title)
                if w.lower() not in stopwords and w.lower() not in GENERIC_ENTITY_BLACKLIST and len(w) > 2
            ]
            query_parts = tokens[:4]

        return " ".join(query_parts)
