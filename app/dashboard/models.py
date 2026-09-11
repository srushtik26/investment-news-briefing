"""
Models for the standalone Investment Committee Briefing dashboard.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Dict, Any


@dataclass
class DashboardStory:
    """Represents an individual story in a dashboard briefing."""
    section: str  # "domestic", "india", "international"
    position: int  # 1 to 5
    headline: str
    summary: Optional[str]
    source: str
    url: str
    id: Optional[int] = None
    briefing_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "briefing_id": self.briefing_id,
            "section": self.section.lower(),
            "position": self.position,
            "headline": self.headline,
            "summary": self.summary,
            "source": self.source,
            "url": self.url,
        }


@dataclass
class DashboardBriefing:
    """Represents a full 15-story daily briefing for the dashboard."""
    briefing_date: date
    generated_at: datetime = field(default_factory=datetime.utcnow)
    full_text: Optional[str] = None
    story_count: int = 0
    stories: List[DashboardStory] = field(default_factory=list)
    id: Optional[int] = None
    content_hash: Optional[str] = None
    sync_source: Optional[str] = None
    synced_at: Optional[datetime] = None

    @property
    def india_stories(self) -> List[DashboardStory]:
        return sorted([s for s in self.stories if s.section.lower() == "india"], key=lambda s: s.position)

    @property
    def domestic_stories(self) -> List[DashboardStory]:
        return sorted([s for s in self.stories if s.section.lower() == "domestic"], key=lambda s: s.position)

    @property
    def international_stories(self) -> List[DashboardStory]:
        return sorted([s for s in self.stories if s.section.lower() == "international"], key=lambda s: s.position)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "briefing_date": self.briefing_date.isoformat(),
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "story_count": self.story_count,
            "stories": [s.to_dict() for s in self.stories],
            "india": [s.to_dict() for s in self.india_stories],
            "domestic": [s.to_dict() for s in self.domestic_stories],
            "international": [s.to_dict() for s in self.international_stories],
        }
