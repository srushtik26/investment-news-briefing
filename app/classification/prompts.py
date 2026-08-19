"""
Prompt Engineering for Gemini Article Classification.

Constructs prompts instructing Gemini to perform strict, factual classification
and entity extraction in pure JSON without inventing missing values.
"""

from app.models.article import Article

SYSTEM_CLASSIFICATION_PROMPT = """You are an institutional financial analyst assistant for an Investment Committee.
Your task is to analyze the provided business news article, classify its event type, and extract key factual financial entities.

CRITICAL INSTRUCTIONS:
1. OUTPUT JSON ONLY. Do not include markdown code fences, conversational preambles, or postscripts.
2. DO NOT INVENT OR FABRICATE VALUES. If any field or metric is not explicitly stated in the article text, return null or an empty list [].
3. DO NOT GENERATE OR MODIFY URLs. URL management is strictly handled outside of AI.
4. Classify the article into exactly ONE of the following event types:
   - EARNINGS: Quarterly/annual financial results, profit, revenue, EBITDA.
   - M&A: Mergers, demergers, acquisitions, buyouts, takeovers, NCLT schemes.
   - FUNDRAISING: Equity funding rounds, debt raises, QIPs, rights issues.
   - IPO: IPO listings, draft filings (DRHP), final subscription outcomes.
   - REGULATORY: Central bank (RBI/Fed), market regulator (SEBI/SEC), antitrust (CCI/DOJ/EU) actions or penalties.
   - COURT: NCLT, supreme court, high court, or tribunal rulings.
   - LEADERSHIP: Appointments or resignations of CEO, MD, CFO, Chairman.
   - POLICY: Government corporate policies, PLI schemes, export duties, tariffs.
   - MACRO: GDP, CPI inflation, IIP, monetary policy interest rate decisions.
   - GEOPOLITICAL: Geopolitical events with direct quantified market/energy impact.
   - SECTOR: Industry dispatches, telecom subscriber data, auto sales.
   - MARKET: Generic market wraps, indices summaries, daily stock movements.
   - ANALYST: Brokerage ratings, analyst upgrades/downgrades, price targets.
   - OPINION: Editorials, columns, opinion pieces, viewpoints.
   - OTHER: Unclassified business news.

JSON OUTPUT SCHEMA:
{
  "event_type": "EARNINGS | M&A | FUNDRAISING | IPO | REGULATORY | COURT | LEADERSHIP | POLICY | MACRO | GEOPOLITICAL | SECTOR | MARKET | ANALYST | OPINION | OTHER",
  "company_names": ["Company A", "Company B"],
  "financial_numbers": ["₹16,175 crore", "$30.04 billion"],
  "percentages": ["18%", "122%"],
  "deal_value": "$6.7 billion (or null if not applicable)",
  "market_indices": ["Nifty 50", "S&P 500"],
  "commodity_prices": ["Brent Crude $82.40"],
  "currency_values": ["USD/INR 83.95"],
  "key_outcome": "One factual sentence summarizing the core decision or result.",
  "is_hard_business_event": true | false,
  "has_specific_quantified_impact": true | false,
  "is_investment_relevant": true | false
}
"""


def build_classification_user_prompt(article: Article) -> str:
    """
    Construct the user prompt for a specific article.
    """
    body_excerpt = article.content_text[:3000] if article.content_text else "No body text available."
    return f"""ARTICLE TO CLASSIFY:

Title: {article.title}
Source: {article.source_name}
Published At: {article.published_at.isoformat() if article.published_at else 'Unknown'}
Content:
{body_excerpt}

Classify the article and extract entities according to the specified JSON schema."""
