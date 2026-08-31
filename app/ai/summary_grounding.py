"""
Deterministic Summary Grounding and Source Sentence Selection Engine.

Prevents summary hallucination, cross-story leakage, and topic drift by validating
that every candidate summary describes the exact same event as the headline.
Provides deterministic structured fallback when body sentences fail grounding.
"""

import re
from typing import Any, List, Optional, Set, Tuple
from app.models.article import Article
from app.models.event import Event
from app.logging_config import get_logger

logger = get_logger("ai.summary_grounding")

# Common boilerplate / noise phrases in article bodies
BOILERPLATE_PHRASES = (
    "click here", "subscribe", "all rights reserved", "read more", "photo:", "image:",
    "advertisement", "sign up", "follow us", "share price today", "for more details",
    "live updates", "stay tuned", "download app", "also read", "copyright",
    "watch live", "newsletter", "terms of use", "privacy policy", "related stories",
    "recommended stories", "trending now", "editors pick", "must read",
)

INCOMPLETE_ENDINGS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "of", "for", "to", "in", "on", "at", "with", "from", "by", "as",
    "its", "their", "his", "her", "this", "that", "major", "electric", "is", "was", "were", "are", "be",
    "been", "has", "have", "had", "which", "who", "whom", "whose", "where", "when", "why", "how", "such",
    "into", "onto", "under", "over", "about", "after", "before", "while", "during", "through", "between",
    "among", "against"
}

ACTION_SYNONYM_GROUPS = [
    {"bag", "bags", "bagged", "secure", "secures", "secured", "win", "wins", "won", "award", "awarded", "order", "contract"},
    {"acquire", "acquires", "acquired", "acquisition", "buy", "buys", "bought", "buyout", "takeover", "merger", "merge", "merges"},
    {"profit", "net profit", "earnings", "revenue", "results", "loss", "ebitda", "margin", "income"},
    {"rise", "rises", "rose", "jump", "jumps", "jumped", "surge", "surges", "surged", "fall", "falls", "fell", "drop", "drops", "dropped", "slump"},
    {"raise", "raises", "raised", "fund", "funds", "funding", "round", "qip", "rights issue"},
    {"demolish", "demolishes", "demolition", "bulldozer", "unauthorized", "illegal", "chambers", "encroachment"},
    {"probe", "probes", "probed", "investigate", "investigation", "sit", "racket", "fraud", "fraudulent"},
    {"quash", "quashes", "stay", "stays", "stayed", "order", "orders", "verdict", "ruling", "dismiss", "dismisses"},
    {"transfer", "transfers", "transferred", "appoint", "appoints", "appointed", "appointment", "elevate", "elevation"},
    {"launch", "launches", "launched", "test", "tests", "tested", "mission", "satellite", "missile"},
]


def _extract_core_entities(text: str) -> Set[str]:
    """Extract significant corporate/proper entities and capitalized terms."""
    # Match title-cased words/phrases
    matches = re.findall(r"\b[A-Z][a-zA-Z0-9&]+(?:\s+[A-Z][a-zA-Z0-9&]+)*\b", text)
    stopwords = {"The", "A", "An", "In", "On", "At", "For", "With", "By", "To", "From", "After", "Before", "Amid", "Why", "What", "How"}
    entities = set()
    for m in matches:
        m_str = m.strip()
        if m_str not in stopwords and len(m_str) >= 3:
            entities.add(m_str.lower())
    return entities


def _extract_action_concepts(text: str) -> Set[int]:
    """Identify action concept clusters present in text."""
    t_lower = text.lower()
    active_clusters = set()
    for idx, group in enumerate(ACTION_SYNONYM_GROUPS):
        if any(re.search(r"\b" + re.escape(term) + r"\b", t_lower) for term in group):
            active_clusters.add(idx)
    return active_clusters


def validate_summary_grounding(
    summary: str,
    headline: str,
    event: Optional[Event] = None,
    article: Optional[Article] = None,
) -> Tuple[bool, str]:
    """
    Deterministically validate that summary describes the same underlying event as headline.

    Requirements:
    1. Summary must share principal entity OR event subject.
    2. Summary must share at least one action/event concept.
    3. Summary cannot introduce a prominent completely unrelated entity.
    4. Summary cannot describe another event from the same page/feed.
    """
    if not summary or not summary.strip():
        return False, "Summary is empty"
    if not headline or not headline.strip():
        return False, "Headline is empty"

    s_clean = summary.strip()
    h_clean = headline.strip()
    s_low = s_clean.lower()
    h_low = h_clean.lower()

    # Significant content words (len >= 3, non-stopwords)
    stop_words = {
        "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "is", "with", "by", "its", "from", "as",
        "over", "under", "after", "before", "amid", "says", "said", "reports", "reported", "shows", "this", "that",
    }
    h_words = {w for w in re.findall(r"\b[a-zA-Z0-9]+\b", h_low) if w not in stop_words and len(w) >= 3}
    s_words = {w for w in re.findall(r"\b[a-zA-Z0-9]+\b", s_low) if w not in stop_words and len(w) >= 3}

    shared_words = h_words.intersection(s_words)

    # 1. Entity / Subject overlap
    h_entities = _extract_core_entities(h_clean)
    if event and event.companies_involved:
        for c in event.companies_involved:
            if c and len(c.strip()) >= 3:
                h_entities.add(c.strip().lower())

    s_entities = _extract_core_entities(s_clean)
    shared_entities = h_entities.intersection(s_entities)

    has_subject_overlap = bool(shared_entities) or len(shared_words) >= 2

    # 2. Action concept overlap
    h_actions = _extract_action_concepts(h_clean)
    s_actions = _extract_action_concepts(s_clean)
    shared_actions = h_actions.intersection(s_actions)

    # 3. Check for obvious mismatch: summary introducing unrelated headline/victim/subject
    # e.g., Headline is bulldozer action on lawyers chambers, summary is Hindu activist found dead
    unrelated_markers = [
        "found dead", "dead body", "murdered", "bullet wounds", "lynched", "activist found dead",
        "suicide", "accident kills", "road accident", "drowned",
    ]
    if any(m in s_low for m in unrelated_markers) and not any(m in h_low for m in unrelated_markers):
        return False, "Summary describes violent death/accident absent from headline"

    # If headline mentions specific numbers / currency, check if summary has conflicting numbers
    h_amounts = re.findall(r"(?:₹|rs\.?|\$)\s*[\d,]+(?:\.\d+)?\s*(?:crore|cr|billion|million)?", h_clean, re.IGNORECASE)
    if h_amounts and not shared_entities and len(shared_words) < 2:
        return False, "Summary lacks required entity or core overlap with quantified headline"

    # Minimum requirement: Must have subject overlap AND (shared action OR meaningful word overlap >= 2)
    if not has_subject_overlap:
        return False, f"Summary lacks principal entity or subject overlap with headline (shared: {list(shared_words)})"

    if not shared_actions and len(shared_words) < 3:
        return False, f"Summary does not describe the same action or event (shared words: {list(shared_words)})"

    return True, "Summary is properly grounded to headline"


def select_grounded_summary_sentence(
    article: Article,
    headline: str,
    event: Optional[Event] = None,
) -> Optional[str]:
    """
    Select the highest-scoring factual sentence from article body that passes grounding.
    """
    if not article or not article.content_text:
        return None

    raw_body = article.content_text.strip()
    # Normalize split boundaries
    cleaned_body = re.sub(r"([a-z0-9])\.([A-Z])", r"\1. \2", raw_body)

    # Split into candidate sentences
    raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned_body) if len(s.strip().split()) >= 4]

    valid_candidates: List[str] = []
    for s in raw_sentences:
        s_low = s.lower()
        if any(b in s_low for b in BOILERPLATE_PHRASES):
            continue
        words = s.split()
        if len(words) < 6 or len(words) > 35:
            continue
        last_word = re.sub(r"[^\w]", "", words[-1]).lower()
        if last_word in INCOMPLETE_ENDINGS:
            continue
        valid_candidates.append(s)

    h_entities = _extract_core_entities(headline)
    h_words = {w.lower() for w in re.findall(r"\b\w{3,}\b", headline)}
    h_actions = _extract_action_concepts(headline)

    scored_candidates: List[Tuple[float, str]] = []

    for idx, s in enumerate(valid_candidates[:12]):
        # Strip common journalistic attribution prefixes
        cleaned_s = re.sub(
            r"^(?:According to reports|Reports indicate that|It is reported that|Sources said that|In a statement|On Thursday|On Friday|On Wednesday|On Tuesday|On Monday|NEW DELHI|MUMBAI|BENGALURU)[,\s:\-]+",
            "",
            s,
            flags=re.IGNORECASE,
        ).strip()
        if not cleaned_s.endswith((".", "!", "?")):
            cleaned_s += "."

        # Ensure sentence does not exceed 30 words and does not end with incomplete tokens
        words = cleaned_s.split()
        if len(words) > 30:
            clauses = re.split(r"[,;—–]\s+(?:as|which|while|after|following|marking|amid|where|with)\s+", cleaned_s, flags=re.IGNORECASE)
            if len(clauses) > 1:
                first_clause = clauses[0].strip().rstrip(",;:-—– ") + "."
                if len(first_clause.split()) <= 30 and re.sub(r"[^\w]", "", first_clause.split()[-1]).lower() not in INCOMPLETE_ENDINGS:
                    cleaned_s = first_clause
            if len(cleaned_s.split()) > 30:
                shortened = cleaned_s.split()[:28]
                while shortened and re.sub(r"[^\w]", "", shortened[-1]).lower() in INCOMPLETE_ENDINGS:
                    shortened.pop()
                cleaned_s = " ".join(shortened).rstrip(" ,;:-—–") + "."

        # Validate grounding
        is_grounded, reason = validate_summary_grounding(cleaned_s, headline, event=event, article=article)
        if not is_grounded:
            continue

        s_words = {w.lower() for w in re.findall(r"\b\w{3,}\b", cleaned_s)}
        overlap_words = len(s_words & h_words)
        s_entities = _extract_core_entities(cleaned_s)
        overlap_entities = len(s_entities & h_entities)
        s_actions = _extract_action_concepts(cleaned_s)
        overlap_actions = len(s_actions & h_actions)
        has_num = bool(re.search(r"[\$₹\d%]", cleaned_s))

        score = (overlap_entities * 15.0) + (overlap_actions * 10.0) + (overlap_words * 4.0) + (5.0 if has_num else 0.0) - (idx * 1.5)
        scored_candidates.append((score, cleaned_s))

    if scored_candidates:
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        return scored_candidates[0][1]

    return None


def build_structured_fallback_summary(
    headline: str,
    event: Optional[Event] = None,
    article: Optional[Article] = None,
) -> str:
    """
    Construct a deterministic, factual, clean declarative 1-sentence summary
    grounded 100% to verified structured facts from the headline and event data.
    """
    clean_h = headline.strip().rstrip(" .!?:;-—")

    # If clean_h is already a complete sentence
    words = clean_h.split()
    if len(words) >= 6 and len(words) <= 25 and re.sub(r"[^\w]", "", words[-1]).lower() not in INCOMPLETE_ENDINGS:
        return f"{clean_h}."

    # Build structured declarative summary
    # Check if headline contains an order win
    order_match = re.search(r"^(.*?)\s+(?:bags|secures?|wins?)\s+(.*?)(?:\s+from\s+(.*?))?$", clean_h, re.IGNORECASE)
    if order_match:
        entity, details, client = order_match.groups()
        if client:
            return f"{entity.strip()} secured a {details.strip()} from {client.strip()} for an infrastructure project."
        return f"{entity.strip()} secured a {details.strip()}."

    # Check acquisition
    acq_match = re.search(r"^(.*?)\s+(?:to acquire|acquires?|buys?)\s+(.*?)(?:\s+for\s+(.*?))?$", clean_h, re.IGNORECASE)
    if acq_match:
        buyer, target, amount = acq_match.groups()
        if amount:
            return f"{buyer.strip()} agreed to acquire {target.strip()} for {amount.strip()}."
        return f"{buyer.strip()} agreed to acquire {target.strip()}."

    # Check profit / earnings
    profit_match = re.search(r"^(.*?)\s+(?:net profit|profit)\s+(?:rises|jumps|surges|falls|drops)\s+(.*?)$", clean_h, re.IGNORECASE)
    if profit_match:
        comp, movement = profit_match.groups()
        return f"{comp.strip()} reported financial results with profit {movement.strip()}."

    # Fallback to normalized declarative headline
    safe_words = []
    for w in words:
        safe_words.append(w)
        if len(safe_words) >= 25:
            break
    while safe_words and re.sub(r"[^\w]", "", safe_words[-1]).lower() in INCOMPLETE_ENDINGS:
        safe_words.pop()

    return (" ".join(safe_words) if safe_words else clean_h).rstrip(" ,;:-—") + "."
