"""
Event Clustering Engine.

Clusters multiple candidate articles referring to the same underlying business event
into unified canonical Event models.
"""

from datetime import datetime, timezone
from typing import List

from app.logging_config import get_logger
from app.models.article import Article
from app.models.event import Event
from app.deduplication.fingerprint import are_articles_same_event, normalize_metric_facts

logger = get_logger("deduplication.clusterer")


class EventClusterer:
    """
    Groups disparate news reports covering the same corporate development into unified Events.
    """

    def cluster_articles_into_events(self, articles: List[Article]) -> List[Event]:
        """
        Cluster a list of articles into distinct Event models.

        Args:
            articles: List of verified candidate articles.

        Returns:
            List of synthesized Event objects with linked article IDs.
        """
        logger.info("Clustering %d articles into unique business events...", len(articles))

        # Clusters is a list of lists: [[art1, art2], [art3], ...]
        clusters: List[List[Article]] = []

        for article in articles:
            matched_cluster = None
            for cluster in clusters:
                # Check against cluster representative (first article in cluster)
                if are_articles_same_event(article, cluster[0]):
                    matched_cluster = cluster
                    break

            if matched_cluster is not None:
                matched_cluster.append(article)
                logger.debug("Grouped '%s' with existing cluster '%s'", article.title[:35], matched_cluster[0].title[:35])
            else:
                clusters.append([article])

        # Synthesize Event models from clusters
        events: List[Event] = []
        for cluster in clusters:
            primary_art = cluster[0]
            article_ids = [a.id for a in cluster]

            # Aggregate financial facts
            all_facts = set()
            for art in cluster:
                all_facts.update(normalize_metric_facts(f"{art.title} {art.content_text or ''}"))

            # Synthesize canonical title and description with deterministic region classification
            from app.classification.region_classifier import EventRegionClassifier
            region_classifier = EventRegionClassifier()
            event_cat = region_classifier.classify(
                title=primary_art.title,
                content=primary_art.content_text,
                financial_figures=sorted(list(all_facts))[:5],
                companies=[primary_art.source_name] if not primary_art.author else [],
            )

            event = Event(
                canonical_title=primary_art.title,
                description=primary_art.content_text[:300] if primary_art.content_text else primary_art.title,
                companies_involved=[primary_art.source_name] if not primary_art.author else [],
                financial_figures=sorted(list(all_facts))[:5],
                event_category=event_cat,
                article_ids=article_ids,
                detected_at=datetime.now(timezone.utc),
            )
            events.append(event)

        logger.info("Clustering complete: Formed %d distinct Events from %d articles", len(events), len(articles))
        return events
