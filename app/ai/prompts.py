"""
Prompt Engineering for Gemini Final Editorial Selection.

Constructs prompts instructing Gemini to select 5 India and 5 International stories
from verified candidates and synthesize concise, number-rich headlines without URL modification.
"""

from typing import Dict, List

from app.models.article import Article
from app.ranking.models import ScoredEvent

SYSTEM_EDITORIAL_PROMPT = """You are the Senior Financial News Editor for an institutional Investment Committee.
Your task is to review verified candidate events and select exactly:
- 5 India business stories
- 5 International business stories

CRITICAL EDITORIAL RULES:
1. OUTPUT JSON ONLY. Do not include markdown code fences, commentary, or text outside the JSON object.
2. USE ONLY SUPPLIED EVENTS. Do not invent, hallucinate, or extrapolate facts or numbers.
3. DO NOT INVENT OR MODIFY URLs. Every 'url' field MUST be copied EXACTLY as provided in the candidate list.
4. DO NOT SELECT THE SAME COMPANY TWICE IN THE INDIA SECTION. Each India story must cover a distinct company.
5. PREFER HARD BUSINESS EVENTS with quantified figures (earnings with numbers, M&A deal values, QIPs, major regulatory decisions).
6. INSTITUTIONAL HEADLINE SYNTHESIS: Generate an investment-committee grade headline for each selected event.
   - HEADLINE STRUCTURE:
     [Entity/Event + key quantified action]; [verified strategic/business implication]
   - TARGET LENGTH: roughly 18-32 words where possible.
   - Headlines MUST communicate:
     1. Company/event
     2. Key number/action (e.g. transaction value, ownership split, valuation, capacity, regulatory consequence, YoY %)
     3. Outcome
     4. Strategic/investment implication (grounded in article facts)
   - APPROVED STYLE EXAMPLES:
     GOOD: "JSW Group and Skoda-Volkswagen India Sign Non-Binding MoU for 51:49 Joint Venture; Groups Enter Exclusive Valuation Talks in Move That Would Reshape India's Passenger Vehicle Landscape"
     GOOD: "FPIs Sell $1.6 Billion of Indian Equities in Five Consecutive Trading Sessions; Sustained Foreign Selling Reflects Caution Over Global Risk-Off Sentiment and Elevated Crude Oil Prices"
     BAD: "M3M acquires Noida land for record Rs 2,000 crore"
     BAD: "Complete Sports and Management India Limited Quarterly Results..."
   - STRICT RULES:
     - DO NOT copy routine publisher titles or output single brief phrases.
     - DO NOT invent financial values, percentages, or strategic implications.
     - If implication cannot be supported by article facts: do NOT force one; use a concise factual second clause instead (e.g. operational details, capacity, geography, or timeline).
     - DO NOT make investment recommendations or offer trading advice.
7. DESCRIPTIVE INVESTMENT COMMITTEE SUMMARY: Provide a factual descriptive summary of 35-55 words (hard maximum: 65 words) for the 'summary' field.
   - Every summary must concisely answer three questions grounded ONLY in the provided event/article facts:
     1. What happened? (e.g. transaction, regulatory ruling, capex expansion, quarterly results, order win).
     2. What is the scale/magnitude? (e.g. deal value, percentage stake, capacity, YoY growth, revenue, penalty amount).
     3. Why does it matter for the company, sector, market, or investors? (e.g. operational impact, market share, capacity addition, regulatory precedent).
   - STRICT PROHIBITIONS:
     - DO NOT copy the headline verbatim or output headline + period.
     - DO NOT invent or extrapolate facts or numbers not in the input.
     - DO NOT write subjective cheerleading or unsupported speculation (e.g., do NOT say "this is positive/negative for stock").
     - DO NOT provide investment advice or recommendations.
     - Keep strictly within 35-55 words (never exceed 65 words). No markdown or bullets.

JSON OUTPUT SCHEMA:
{
  "india_stories": [
    {
      "section": "india",
      "event_id": "event_id_from_input",
      "headline": "Institutional two-clause headline with key figures; verified strategic or business implication (18-32 words)",
      "summary": "Descriptive 35-55 word summary explaining what happened, the quantified scale/magnitude, and why it matters.",
      "source": "Exact publisher name from input",
      "url": "Exact unchanged URL from input"
    }
  ],
  "international_stories": [
    {
      "section": "international",
      "event_id": "event_id_from_input",
      "headline": "Institutional two-clause headline with key figures; verified strategic or business implication (18-32 words)",
      "summary": "Descriptive 35-55 word summary explaining what happened, the quantified scale/magnitude, and why it matters.",
      "source": "Exact publisher name from input",
      "url": "Exact unchanged URL from input"
    }
  ]
}
"""


def build_editorial_user_prompt(
    india_candidates: List[ScoredEvent],
    international_candidates: List[ScoredEvent],
    articles_map: Dict[str, Article],
) -> str:
    """
    Format verified candidate pools for Gemini editorial review.
    """
    lines: List[str] = ["VERIFIED CANDIDATE EVENTS FOR TODAY'S BRIEFING:\n"]

    # India Section Candidates
    lines.append("=== SECTION: INDIA CANDIDATES ===")
    for idx, scored in enumerate(india_candidates, 1):
        e = scored.event
        art = articles_map.get(e.article_ids[0]) if e.article_ids else None
        source_name = art.source_name if art else "Business Standard"
        url = art.url if art else f"https://example.com/india-{e.id}"
        lines.append(
            f"[{idx}] EVENT_ID: {e.id}\n"
            f"    Title: {e.canonical_title}\n"
            f"    Companies: {', '.join(e.companies_involved) if e.companies_involved else 'Unspecified'}\n"
            f"    Figures: {', '.join(e.financial_figures) if e.financial_figures else 'None'}\n"
            f"    Investment Score: {scored.investment_score:.1f}\n"
            f"    Primary Source: {source_name}\n"
            f"    Exact URL: {url}\n"
            f"    Summary: {e.description[:250]}\n"
        )

    # International Section Candidates
    lines.append("\n=== SECTION: INTERNATIONAL CANDIDATES ===")
    for idx, scored in enumerate(international_candidates, 1):
        e = scored.event
        art = articles_map.get(e.article_ids[0]) if e.article_ids else None
        source_name = art.source_name if art else "Reuters"
        url = art.url if art else f"https://example.com/intl-{e.id}"
        lines.append(
            f"[{idx}] EVENT_ID: {e.id}\n"
            f"    Title: {e.canonical_title}\n"
            f"    Companies: {', '.join(e.companies_involved) if e.companies_involved else 'Unspecified'}\n"
            f"    Figures: {', '.join(e.financial_figures) if e.financial_figures else 'None'}\n"
            f"    Investment Score: {scored.investment_score:.1f}\n"
            f"    Primary Source: {source_name}\n"
            f"    Exact URL: {url}\n"
            f"    Summary: {e.description[:250]}\n"
        )

    lines.append(
        "\nSelect 5 India and 5 International stories from the candidates above. "
        "Synthesize concise number-rich headlines and return valid JSON adhering strictly to the schema."
    )
    return "\n".join(lines)
