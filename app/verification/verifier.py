"""
Two-Source Event Verification Service.

Verifies that business events are corroborated by at least two independent publications,
strictly rejecting duplicate articles from the same media group, syndicated wire feeds,
and unrelated articles.
"""

from datetime import datetime, timezone
import re
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from app.logging_config import get_logger
from app.models.article import Article
from app.models.event import Event
from app.verification.models import EventSourceVerification, VerificationStatus

logger = get_logger("verification.two_source")


class TwoSourceVerifier:
    """
    Deterministic corroboration engine enforcing the 2-independent-source rule.
    """

    # MEDIA GROUP / PUBLISHER MAPPING
    PUBLISHER_GROUPS: Dict[str, str] = {
        "economictimes.indiatimes.com": "times_group",
        "timesofindia.indiatimes.com": "times_group",
        "the economic times": "times_group",
        "economic times": "times_group",
        "business-standard.com": "business_standard",
        "business standard": "business_standard",
        "livemint.com": "ht_media",
        "mint": "ht_media",
        "hindustantimes.com": "ht_media",
        "financialexpress.com": "express_group",
        "financial express": "express_group",
        "indianexpress.com": "express_group",
        "reuters.com": "thomson_reuters",
        "reuters": "thomson_reuters",
        "bloomberg.com": "bloomberg_lp",
        "bloomberg": "bloomberg_lp",
        "cnbc.com": "nbc_universal",
        "cnbc": "nbc_universal",
        "ft.com": "nikkei_ft",
        "financial times": "nikkei_ft",
        "wsj.com": "news_corp_wsj",
        "wall street journal": "news_corp_wsj",
        "the wall street journal": "news_corp_wsj",
    }

    # WIRE AGENCY SYNDICATION PATTERNS
    WIRE_PATTERNS = [
        r"\b(pti|press trust of india)\b",
        r"\b(ani|asian news international)\b",
        r"\b(reuters news agency feed|reuters wire)\b",
        r"\b(bloomberg news service syndication)\b",
        r"\(pti\)",
        r"\(reuters\)\s*-",
        r"\(bloomberg\)\s*-",
    ]

    SYNDICATION_SIMILARITY_THRESHOLD = 0.70

    def get_publisher_group(self, article: Article) -> str:
        """Resolve publisher or URL domain to standardized media parent group."""
        source_key = (article.source_name or "").lower().strip()
        if source_key in self.PUBLISHER_GROUPS:
            return self.PUBLISHER_GROUPS[source_key]

        parsed = urlparse(article.url)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]

        for domain, group in self.PUBLISHER_GROUPS.items():
            if domain in netloc:
                return group

        return netloc or source_key or "unknown_publisher"

    def is_syndicated_republication(self, art1: Article, art2: Article) -> Tuple[bool, str]:
        """
        Check if two articles are syndicated duplicates of the same wire release.
        """
        text1 = f"{art1.title} {art1.content_text or ''}".lower()
        text2 = f"{art2.title} {art2.content_text or ''}".lower()

        # 1. Check for explicit wire agency bylines / credit in both
        wire_found1 = any(re.search(pat, text1) for pat in self.WIRE_PATTERNS)
        wire_found2 = any(re.search(pat, text2) for pat in self.WIRE_PATTERNS)

        # 2. Compute Jaccard word n-gram similarity on opening 150 words
        words1 = set(text1.split()[:150])
        words2 = set(text2.split()[:150])
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        jaccard = len(intersection) / len(union) if union else 0.0

        if wire_found1 and wire_found2:
            return True, f"Both articles contain wire agency credit (PTI/ANI/Reuters wire) with text similarity {jaccard:.2f}"

        if (wire_found1 or wire_found2) and jaccard >= 0.50:
            return True, f"Syndicated wire release detected with {jaccard:.2f} text overlap"

        if jaccard >= self.SYNDICATION_SIMILARITY_THRESHOLD:
            return True, f"Near-identical verbatim text duplication ({jaccard:.2f} overlap)"

        return False, f"Distinct editorial formulations (similarity {jaccard:.2f})"

    def is_same_underlying_event(self, art1: Article, art2: Article) -> Tuple[bool, float, str]:
        """
        Determine if two articles refer to the same corporate/economic event.
        """
        # Tokenize titles (allow 2-letter tokens like q1, q2, cr, 18, ai, it, ev)
        stopwords = {"the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "is", "with", "by", "its", "from", "as"}
        t1_words = {w for w in re.findall(r"[a-zA-Z0-9]+", art1.title.lower()) if w not in stopwords and len(w) >= 2}
        t2_words = {w for w in re.findall(r"[a-zA-Z0-9]+", art2.title.lower()) if w not in stopwords and len(w) >= 2}

        title_overlap = t1_words.intersection(t2_words)
        title_union = t1_words.union(t2_words)
        title_similarity = len(title_overlap) / len(title_union) if title_union else 0.0

        # Extract shared numbers / currency figures
        nums1 = set(re.findall(r"\b\d+(?:,\d+)*(?:\.\d+)?\b", art1.title))
        nums2 = set(re.findall(r"\b\d+(?:,\d+)*(?:\.\d+)?\b", art2.title))
        shared_nums = nums1.intersection(nums2)

        # Check shared key company words / stems
        major_org_words1 = {w for w in t1_words if w in ("hdfc", "tata", "reliance", "nvidia", "infosys", "lt", "larsen", "toubro", "rio", "tinto", "arcadium", "apple", "google", "meta", "microsoft")}
        major_org_words2 = {w for w in t2_words if w in ("hdfc", "tata", "reliance", "nvidia", "infosys", "lt", "larsen", "toubro", "rio", "tinto", "arcadium", "apple", "google", "meta", "microsoft")}
        shared_orgs = major_org_words1.intersection(major_org_words2)

        # Event matching logic
        if title_similarity >= 0.25 or (shared_orgs and (shared_nums or title_similarity >= 0.15)) or len(title_overlap) >= 3:
            score = max(title_similarity, 0.90 if shared_nums else 0.70)
            return True, score, f"Matching entities/words {title_overlap} with {title_similarity:.2f} title overlap"

        return False, title_similarity, f"Low semantic correlation (title overlap {title_similarity:.2f})"

    def verify_event(
        self,
        event: Event,
        articles: List[Article],
    ) -> EventSourceVerification:
        """
        Verify an event using its linked candidate articles.

        Args:
            event: Event model instance.
            articles: List of Article models linked to this event.

        Returns:
            EventSourceVerification detailing independence and verification outcome.
        """
        logger.info("Evaluating two-source verification for event '%s' (%d articles)", event.canonical_title[:45], len(articles))

        # 1. Single Source Check (Strict: No fabrication allowed)
        if not articles or len(articles) < 2:
            primary_src = articles[0].source_name if articles else "Unknown"
            url_list = [articles[0].url] if articles else []
            return EventSourceVerification(
                event_id=event.id,
                primary_source=primary_src,
                secondary_source=None,
                source_count=len(articles),
                is_independent=False,
                verification_status=VerificationStatus.UNVERIFIED_SINGLE_SOURCE,
                confidence_score=0.5 if articles else 0.0,
                article_urls=url_list,
                matching_details="Event has only 1 source. Minimum 2 independent sources required for verification.",
            )

        art1 = articles[0]
        art2 = articles[1]

        # 2. Check if both articles refer to the same event
        is_same_event, event_score, event_msg = self.is_same_underlying_event(art1, art2)
        if not is_same_event:
            return EventSourceVerification(
                event_id=event.id,
                primary_source=art1.source_name,
                secondary_source=art2.source_name,
                source_count=len(articles),
                is_independent=False,
                verification_status=VerificationStatus.REJECTED_UNRELATED,
                confidence_score=event_score,
                article_urls=[art1.url, art2.url],
                matching_details=f"Articles discuss different underlying events: {event_msg}",
            )

        # 3. Check Publisher Independence (Same media group / domain)
        group1 = self.get_publisher_group(art1)
        group2 = self.get_publisher_group(art2)
        if group1 == group2:
            return EventSourceVerification(
                event_id=event.id,
                primary_source=art1.source_name,
                secondary_source=art2.source_name,
                source_count=len(articles),
                is_independent=False,
                verification_status=VerificationStatus.REJECTED_SAME_PUBLISHER,
                confidence_score=0.4,
                article_urls=[art1.url, art2.url],
                matching_details=f"Both articles originate from the same publisher/media group ('{group1}').",
            )

        # 4. Check for Syndicated / Verbatim Wire Repetition
        is_syndicated, synd_reason = self.is_syndicated_republication(art1, art2)
        if is_syndicated:
            return EventSourceVerification(
                event_id=event.id,
                primary_source=art1.source_name,
                secondary_source=art2.source_name,
                source_count=len(articles),
                is_independent=False,
                verification_status=VerificationStatus.REJECTED_SYNDICATED,
                confidence_score=0.3,
                article_urls=[art1.url, art2.url],
                matching_details=f"Syndicated wire copy detected: {synd_reason}",
            )

        # 5. Passed all checks -> Verified!
        logger.info(
            "Event '%s' VERIFIED with independent sources: '%s' and '%s'",
            event.canonical_title[:40],
            art1.source_name,
            art2.source_name,
        )
        return EventSourceVerification(
            event_id=event.id,
            primary_source=art1.source_name,
            secondary_source=art2.source_name,
            source_count=len(articles),
            is_independent=True,
            verification_status=VerificationStatus.VERIFIED,
            confidence_score=0.95,
            article_urls=[art1.url, art2.url],
            matching_details=f"Corroborated by independent publishers ({art1.source_name} and {art2.source_name}).",
        )
