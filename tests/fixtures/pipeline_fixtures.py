"""
Pipeline Test Fixtures for Reliability and Hardening Tests.

Provides canonical test articles, events, and scored event fixtures covering:
- Valid Domestic (Indian government / regulation / national infrastructure)
- Invalid Domestic (Foreign-country subject passed through Domestic discovery)
- Valid India corporate business transactions
- Valid International business transactions
- Duplicate events and single-source events
- Numeric claims (valid Indian comma grouping vs unsupported numbers)
- Malformed editorial headlines
"""

from datetime import datetime, timezone
import uuid
from typing import List, Optional

from app.models.article import Article
from app.models.briefing import Briefing, BriefingStory
from app.models.enums import NewsCategory, VerificationTier
from app.models.event import Event
from app.ranking.models import ScoredEvent, ScoreBreakdown


def make_test_briefing_story(
    headline: str,
    category: NewsCategory,
    primary_company: Optional[str] = None,
    rank: int = 1,
) -> BriefingStory:
    """Create a canonical test BriefingStory."""
    return BriefingStory(
        event_id=f"evt_{uuid.uuid4().hex[:10]}",
        headline=headline,
        category=category,
        key_points=[f"Strategic development for {headline}."],
        primary_company=primary_company,
        investment_relevance_score=8.5,
        rank=rank,
    )


def make_test_article(
    title: str,
    content: str = "",
    url: Optional[str] = None,
    publisher: str = "Test Times",
    published_at: Optional[datetime] = None,
    category: NewsCategory = NewsCategory.INDIA,
) -> Article:
    """Create a canonical test article."""
    art_id = str(uuid.uuid4())
    now = published_at or datetime(2026, 9, 15, 6, 0, 0, tzinfo=timezone.utc)
    return Article(
        id=art_id,
        url=url or f"https://example.com/article/{art_id}",
        source_name=publisher,
        title=title,
        content_text=content or f"{title}. Full article body detailing key developments and institutional investment context.",
        published_at=now,
        extracted_at=now,
        category=category,
        language="en",
    )


def make_test_event(
    title: str,
    article: Article,
    second_article: Optional[Article] = None,
    category: NewsCategory = NewsCategory.INDIA,
    companies: Optional[List[str]] = None,
    figures: Optional[List[str]] = None,
    tier: VerificationTier = VerificationTier.TWO_SOURCE_VERIFIED,
) -> Event:
    """Create a canonical test event linked to test articles."""
    ev_id = f"ev_{uuid.uuid4().hex[:12]}"
    article_ids = [article.id]
    if second_article:
        article_ids.append(second_article.id)
    return Event(
        id=ev_id,
        canonical_title=title,
        description=f"Detailed factual report covering: {title}.",
        article_ids=article_ids,
        primary_publisher=article.source_name,
        primary_url=article.url,
        secondary_publisher=second_article.source_name if second_article else None,
        secondary_url=second_article.url if second_article else None,
        event_category=category,
        verification_tier=tier,
        verification_confidence=95.0 if tier == VerificationTier.TWO_SOURCE_VERIFIED else 82.0,
        companies_involved=companies or [],
        financial_figures=figures or [],
        published_at=article.published_at,
    )


def make_scored_event(
    event: Event,
    score: float = 85.0,
) -> ScoredEvent:
    """Wrap an event into a ScoredEvent."""
    return ScoredEvent(
        event=event,
        investment_score=score,
        rank=1,
        score_breakdown=ScoreBreakdown(
            financial_magnitude=80.0,
            market_impact=80.0,
            investor_relevance=80.0,
            corporate_significance=80.0,
            source_quality=90.0,
            total_score=score,
        ),
    )


# ---------------------------------------------------------------------------
# Pre-built scenarios
# ---------------------------------------------------------------------------

def create_valid_domestic_scenario(suffix: str = "", publisher: Optional[str] = None, event_idx: int = 0) -> (Article, Event):
    """Valid Indian government / regulation / national infrastructure event."""
    distinct_domestic_events = [
        ("Union Cabinet Clears ₹24,000 Crore National High-Speed Rail Corridor Connecting Key Industrial Hubs",
         "The Union Cabinet chaired by Prime Minister Narendra Modi cleared the new rail infrastructure corridor project today with ₹24,000 crore outlay.",
         ["₹24,000 crore"]),
        ("Supreme Court Constitutional Bench Orders Nationwide Judicial Framework for Tribunal Appointments",
         "The Supreme Court of India directed the Union Ministry of Law and Justice to implement standard nationwide guidelines for tribunal appointments.",
         []),
        ("ISRO Successfully Launches Advanced Earth Observation Satellite EOS-09 from Sriharikota",
         "The Indian Space Research Organisation ISRO completed the launch of EOS-09 satellite onboard PSLV rocket from Satish Dhawan Space Centre.",
         []),
        ("Ministry of Road Transport Inaugurates 600-Kilometer Green Expressway Linking Major Ports",
         "The Union Minister for Road Transport and Highways dedicated the 600-kilometer national green expressway connecting industrial ports to freight hubs.",
         []),
        ("Election Commission of India Issues Comprehensive Notification for Upcoming State Assembly Elections",
         "The Election Commission of India issued formal notifications for upcoming assembly elections following state-level logistical reviews.",
         []),
        ("Union Ministry of Power Mandates Nationwide Smart Grid Integration for State Electricity Boards",
         "The Union Ministry of Power notified national regulations requiring smart grid infrastructure upgrades across all state power utilities.",
         []),
        ("DRDO Conducts Successful Flight Test of Indigenous Long-Range Glide Bomb Off Odisha Coast",
         "The Defence Research and Development Organisation DRDO accomplished the flight test of its long-range glide bomb from a frontline air platform.",
         []),
    ]
    title, content, figures = distinct_domestic_events[event_idx % len(distinct_domestic_events)]
    trusted_pubs = ["The Hindu", "The Indian Express", "Hindustan Times", "Times of India", "NDTV", "PIB", "ANI"]
    pub = publisher or trusted_pubs[event_idx % len(trusted_pubs)]
    art = make_test_article(
        title=title,
        content=content,
        publisher=pub,
        category=NewsCategory.DOMESTIC,
    )
    ev = make_test_event(
        title=art.title,
        article=art,
        category=NewsCategory.DOMESTIC,
        figures=figures,
    )
    return art, ev


def create_invalid_domestic_scenario(publisher: Optional[str] = None) -> (Article, Event):
    """Foreign-country subject passed through Domestic discovery."""
    pub = publisher or "Reuters International"
    art = make_test_article(
        title="US Defence Department Deploys New Aircraft Carrier Strike Group to Red Sea",
        content="The United States Pentagon ordered an additional aircraft carrier strike group to the Red Sea following regional naval alerts.",
        publisher=pub,
        category=NewsCategory.DOMESTIC,
    )
    ev = make_test_event(
        title=art.title,
        article=art,
        category=NewsCategory.DOMESTIC,
    )
    return art, ev


def create_valid_india_scenario(company: str = "Tata Motors", publisher: Optional[str] = None, event_idx: int = 0) -> (Article, Event):
    """Valid Indian corporate transaction / earnings event."""
    trusted_india_pubs = ["Business Standard", "Economic Times", "Livemint", "Financial Express", "The Hindu BusinessLine"]
    pub = publisher or trusted_india_pubs[event_idx % len(trusted_india_pubs)]
    actions = [
        "Secures ₹5,200 Crore Commercial Acquisition and Electric Fleet Order",
        "Signs ₹5,200 Crore Semiconductor Foundry Expansion Agreement",
        "Commissions ₹5,200 Crore Mega Solar Clean Energy Facility",
        "Wins ₹5,200 Crore Deepwater Port Concession Contract",
        "Completes ₹5,200 Crore Strategic Freight Rail Network",
        "Secures ₹5,200 Crore Strategic AI Enterprise Cloud Partnership",
        "Launches ₹5,200 Crore Advanced Battery Gigafactory Operations",
    ]
    act = actions[event_idx % len(actions)]
    art = make_test_article(
        title=f"{company} {act}",
        content=f"{company} signed a definitive acquisition agreement and won a ₹5,200 crore commercial contract to expand manufacturing capacity and infrastructure.",
        publisher=pub,
        category=NewsCategory.INDIA,
    )
    ev = make_test_event(
        title=art.title,
        article=art,
        category=NewsCategory.INDIA,
        companies=[company],
        figures=["₹5,200 crore"],
    )
    return art, ev


def create_valid_international_scenario(company: str = "Samsung Electronics", publisher: Optional[str] = None, event_idx: int = 0) -> (Article, Event):
    """Valid International business transaction."""
    pub = publisher or f"Global News {company}"
    actions = [
        "Backs AI Chipmaker in $230 Million Series A Funding Round",
        "Acquires Cloud Security Leader in $230 Million Cash Transaction",
        "Commissions $230 Million European Semiconductor Foundry",
        "Secures $230 Million Enterprise Quantum Computing Contract",
        "Expands Global Satellite Connectivity with $230 Million Deployment",
        "Invests $230 Million in Autonomous Clean Transportation",
    ]
    act = actions[event_idx % len(actions)]
    art = make_test_article(
        title=f"{company} {act}",
        content=f"{company} joined institutional funds in committing $230 million investment into strategic manufacturing capacity expansion.",
        publisher=pub,
        category=NewsCategory.INTERNATIONAL,
    )
    ev = make_test_event(
        title=art.title,
        article=art,
        category=NewsCategory.INTERNATIONAL,
        companies=[company],
        figures=["$230 million"],
    )
    return art, ev
