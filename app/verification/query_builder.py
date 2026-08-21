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
    "quarter", "quarterly", "results", "earnings", "annual report",
    "ceo", "cfo", "managing director", "analysts", "sources", "deal", "deals",
    "synthetic", "synthetic evs", "demand", "outlook", "turnaround", "trend",
    "india", "company", "companies", "group", "business", "industry", "sector",
    "tyres", "ev", "evs", "sales", "report", "reports", "today", "live", "update",
    "updates", "page", "exclusive", "says", "said", "first", "second", "third",
    "fourth", "q1", "q2", "q3", "q4", "fy24", "fy25", "fy26", "fy27",
    "billion", "million", "crore", "lakh", "percent", "pct", "rs", "inr", "usd",
    "reuters", "bloomberg", "cnbc", "journal", "associated press", "press trust",
    "ani", "pti", "the economic times", "business standard", "livemint", "financial express",
    "moneycontrol", "ndtv", "the hindu", "indian express", "fortune", "guardian",
    "financial times", "wall street journal", "wsj", "ft",
}

# Prefix tokens to strip from proper noun matches (e.g. 'Wall Street Walmart' -> 'Walmart')
PREFIXES_TO_STRIP: List[str] = [
    "wall street ", "stock market ", "market ", "shares of ", "shares in ",
    "reports of ", "exclusive: ", "view: ", "update: ", "breaking: ",
]

EVENT_ACTION_PATTERNS = [
    (r"\b(to buy|buys|acquires?|acquisition|takeover|merger|merges|buyout|stake purchase)\b", "acquisition"),
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
        tok = candidate.strip().strip("'\".,;:()[]{}")
        if not tok or len(tok) < 2:
            return None

        # Check blacklist
        if tok.lower() in GENERIC_ENTITY_BLACKLIST:
            return None

        # Strip generic prefixes
        tok_low = tok.lower()
        for prefix in PREFIXES_TO_STRIP:
            if tok_low.startswith(prefix):
                tok = tok[len(prefix):].strip()
                tok_low = tok.lower()

        if not tok or len(tok) < 2 or tok_low in GENERIC_ENTITY_BLACKLIST:
            return None

        # Discard multi-word phrases that are all lowercase or purely common dictionary words
        words = tok.split()
        if len(words) > 3:
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

        # 2. Extract multi-word and single capitalized proper nouns from title
        title = article.title or ""
        # Multi-word proper nouns (e.g., "Goldman Sachs", "LCN Capital", "Larsen & Toubro", "Rio Tinto")
        multi = re.findall(r"\b[A-Z][a-zA-Z0-9&]+(?:\s+[A-Z][a-zA-Z0-9&]+)+\b", title)
        for m in multi:
            c = cls.clean_entity(m)
            if c and c.lower() not in seen:
                clean_entities.append(c)
                seen.add(c.lower())

        # Single uppercase capitalized words (e.g., "Walmart", "Target", "Lowe's")
        singles = re.findall(r"\b[A-Z][a-zA-Z0-9']{2,}\b", title)
        for s in singles:
            c = cls.clean_entity(s)
            if c and c.lower() not in seen:
                clean_entities.append(c)
                seen.add(c.lower())

        return clean_entities

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
        entities = cls.extract_entities(article, event=event)
        action = cls.detect_event_action(article.title)
        numbers = cls.extract_numbers(article.title)

        primary_entity = entities[0] if entities else None
        second_entity = entities[1] if len(entities) >= 2 else None

        query_parts: List[str] = []
        if primary_entity:
            query_parts.append(f'"{primary_entity}"' if " " in primary_entity else primary_entity)
        if second_entity and second_entity != primary_entity:
            query_parts.append(f'"{second_entity}"' if " " in second_entity else second_entity)

        if action:
            query_parts.append(action)

        # Distinctive financial facts
        if numbers:
            query_parts.append(numbers[0])

        if not query_parts and article.title:
            # Fallback: take clean headline words
            stopwords = {"the", "a", "an", "and", "for", "with", "its", "in", "on", "at", "to", "of", "is", "by", "from", "as", "after", "says"}
            tokens = [
                w for w in re.findall(r"\b[a-zA-Z0-9']+\b", article.title)
                if w.lower() not in stopwords and w.lower() not in GENERIC_ENTITY_BLACKLIST and len(w) > 2
            ]
            query_parts = tokens[:5]

        return " ".join(query_parts)
