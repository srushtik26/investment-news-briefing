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
6. HEADLINE SYNTHESIS: Generate a concise, punchy, institutional headline for each selected event.
   - The headline MUST contain important figures/numbers where meaningful (e.g., 'HDFC Bank Q1 Net Profit Surges 18% YoY to ₹16,175 Cr', 'Rio Tinto Agrees $6.7B Acquisition of Arcadium Lithium').
   - Keep headlines factual, professional, and free of hype.
7. ONE-LINE FACTUAL SUMMARY: Provide exactly one concise sentence (target: 15-25 words, max 30 words) for the 'summary' field.
   - Strictly factual and neutral explaining what happened with key numbers.
   - No bullets, no markdown, no speculation, no filler.

JSON OUTPUT SCHEMA:
{
  "india_stories": [
    {
      "section": "india",
      "event_id": "event_id_from_input",
      "headline": "Concise headline with key numbers",
      "summary": "One-line factual summary explaining what happened and key figures.",
      "source": "Exact publisher name from input",
      "url": "Exact unchanged URL from input"
    }
  ],
  "international_stories": [
    {
      "section": "international",
      "event_id": "event_id_from_input",
      "headline": "Concise headline with key numbers",
      "summary": "One-line factual summary explaining what happened and key figures.",
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
