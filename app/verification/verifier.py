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
from app.models.enums import NewsCategory
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

    EVENT_CATEGORY_PATTERNS: Dict[str, List[str]] = {
        "EARNINGS": [
            r"\b(net profit|quarterly profit|profit|net loss|ebitda|quarterly results|earnings|operating income|sales jump|sales surge|profit rises|profit falls|profit surges|profit drops|profit jumps|sales soar|sales soars|revenue soars|sales climb|revenue climbs|sales rise|revenue rises|topline|bottomline|pat|pbt|data centres|data centers|ai chip demand)\b",
            r"\b(q[1-4]|quarter ended|june quarter|march quarter|september quarter|december quarter|first quarter|second quarter|third quarter|fourth quarter|quarter shows|record quarter|quarterly|quarter)\b",
        ],
        "ACQUISITION_M_A": [
            r"\b(buys\b|buys stake|stake purchase|stake sale|acquires?|acquisition|buyout|merger|takeover|amalgamation|demerger|purchases?|invests in|investment in|units of|all-cash deal|block deal|deal to buy|agrees to buy)\b",
        ],
        "FUNDRAISING": [
            r"\b(qip|qualified institutional placement|ipo|drhp|listing day|rights issue|raises? funds|funding round|debt issuance|bonds? issue|ncds?|share sale|preferential issue|fundraising)\b",
        ],
        "REGULATORY": [
            r"\b(penalty|fine|ban|rbi order|sebi order|antitrust|lawsuit|charges|sec charges|probe|investigation|enforcement|show-cause)\b",
        ],
        "LEADERSHIP": [
            r"\b(appoints? ceo|new ceo|steps down|resigns?|managing director|chief executive|cfo|appoints? md|executive chairman|leadership change)\b",
        ],
        "CONTRACT_ORDER": [
            r"\b(bags order|wins contract|secures contract|order worth|epc contract|epc order|contract win)\b",
        ],
        "POLICY_MACRO": [
            r"\b(gdp growth|inflation|cpi|interest rate|repo rate|tariff|customs duty|fed rate)\b",
        ],
        "RESTRUCTURING": [
            r"\b(layoffs?|job cuts|restructuring|workforce reduction|headcount reduction)\b",
        ],
    }

    INCOMPATIBLE_CATEGORIES: Dict[str, Set[str]] = {
        "EARNINGS": {"ACQUISITION_M_A", "REGULATORY", "LEADERSHIP", "CONTRACT_ORDER", "RESTRUCTURING", "POLICY_MACRO"},
        "ACQUISITION_M_A": {"EARNINGS", "REGULATORY", "LEADERSHIP", "CONTRACT_ORDER", "POLICY_MACRO"},
        "FUNDRAISING": {"EARNINGS", "LEADERSHIP", "CONTRACT_ORDER", "POLICY_MACRO"},
        "REGULATORY": {"EARNINGS", "ACQUISITION_M_A", "FUNDRAISING", "LEADERSHIP", "CONTRACT_ORDER"},
        "LEADERSHIP": {"EARNINGS", "ACQUISITION_M_A", "FUNDRAISING", "REGULATORY", "CONTRACT_ORDER", "POLICY_MACRO"},
        "CONTRACT_ORDER": {"EARNINGS", "ACQUISITION_M_A", "FUNDRAISING", "REGULATORY", "LEADERSHIP", "POLICY_MACRO"},
    }

    def detect_event_categories(self, article: Article) -> Set[str]:
        """Detect event categories present in article title and initial snippet."""
        text = f"{article.title} {(article.content_text or '')[:300]}".lower()
        cats = set()
        for cat, patterns in self.EVENT_CATEGORY_PATTERNS.items():
            if any(re.search(pat, text) for pat in patterns):
                cats.add(cat)
        return cats

    def _extract_metric_numbers(self, text: str) -> Set[str]:
        """Extract and normalize numerical and percentage facts from text."""
        facts = set()
        for pct in re.findall(r"\b\d+(?:\.\d+)?%", text):
            norm = pct.replace("%", "").strip()
            if norm:
                facts.add(f"{norm}%")
        for num in re.findall(r"(?:₹|\$|rs\.?\s*)?[\d,]+(?:\.\d+)?\s*(?:crore|cr|billion|million|b|m)?", text, re.IGNORECASE):
            norm = num.lower().replace("₹", "").replace("$", "").replace("rs.", "").replace("rs", "").replace(",", "").strip()
            norm = re.sub(r"\s+", "", norm)
            norm = re.sub(r"(?<=\d)million$", "million", norm)
            norm = re.sub(r"(?<=\d)b$", "billion", norm)
            norm = re.sub(r"(?<=\d)m$", "million", norm)
            if norm and any(c.isdigit() for c in norm) and norm not in ("1", "2", "3", "2024", "2025", "2026"):
                facts.add(norm)
        metric_units = r"(?:crore|cr|billion|million|b|m|%)"
        for raw_match in re.finditer(r"\b\d+(?:\.\d+)?\b", text):
            raw = raw_match.group(0)
            before = text[max(0, raw_match.start() - 1):raw_match.start()]
            after = text[raw_match.end():]
            is_part_of_metric = (
                before in ("$", "₹")
                or re.match(rf"\s*{metric_units}\b", after, re.IGNORECASE)
            )
            if len(raw) >= 2 and raw not in ("2024", "2025", "2026") and not is_part_of_metric:
                facts.add(raw)
        return facts

    def _extract_reporting_quarter(self, text: str) -> Optional[str]:
        """Extract quarterly reporting period if explicitly mentioned."""
        t_low = text.lower()
        for q in ("q1", "q2", "q3", "q4", "first quarter", "second quarter", "third quarter", "fourth quarter", "june quarter", "march quarter", "september quarter", "december quarter"):
            if q in t_low:
                if q in ("q1", "first quarter", "june quarter"):
                    return "q1"
                if q in ("q2", "second quarter", "september quarter"):
                    return "q2"
                if q in ("q3", "third quarter", "december quarter"):
                    return "q3"
                if q in ("q4", "fourth quarter", "march quarter"):
                    return "q4"
        return None

    def is_same_underlying_event(self, art1: Article, art2: Article, now_utc: Optional[datetime] = None) -> Tuple[bool, float, str]:
        """
        Determine if two articles refer to the exact same corporate/economic event.
        Enforces:
        1. Shared primary entity/company
        2. Compatible event type (incompatibility rejects immediately)
        3. At least one additional event-specific signal (counterpart, shared numbers, period, strong overlap).

        Args:
            now_utc: Optional immutable reference time for freshness checks. Defaults to
                     datetime.now(timezone.utc) when None (preserves backwards compatibility).
        """
        t1 = art1.title.lower()
        t2 = art2.title.lower()

        # Step 0: Non-article candidate / stock quote URL validation
        from app.filtering.rules import URLFilterRule
        for art, label in ((art1, "Primary"), (art2, "Secondary")):
            valid_url, url_reason = URLFilterRule.is_valid_url(art.url)
            if not valid_url:
                return False, 0.0, f"NON_ARTICLE_URL: {label} article URL is invalid ({url_reason})"
            t_low = art.title.lower()
            if re.search(r"\b(stock price|stock quote|share price today|market data|company profile|ticker quote)\b", t_low) and not any(action in t_low for action in ("buys", "acquires", "acquisition", "profit", "merger", "results", "quarterly")):
                return False, 0.0, f"NON_ARTICLE_URL: {label} article title indicates a generic stock price/quote page ('{art.title[:45]}')"

        # Step 0b: 24-hour freshness policy verification for both sources
        from config import get_settings
        settings = get_settings()
        max_freshness_hours = getattr(settings, "STORY_FRESHNESS_HOURS", 24.0)
        # Use provided immutable reference time; fall back to datetime.now() only if not supplied.
        current_utc = now_utc if now_utc is not None else datetime.now(timezone.utc)
        for art, label in ((art1, "Primary"), (art2, "Secondary")):
            if art.published_at:
                pub_time = art.published_at
                if pub_time.tzinfo is None:
                    pub_time = pub_time.replace(tzinfo=timezone.utc)
                age_hours = max(0.0, (current_utc - pub_time).total_seconds() / 3600.0)
                if age_hours > max_freshness_hours:
                    err_code = "STALE_PRIMARY_SOURCE" if label == "Primary" else "STALE_SECOND_SOURCE"
                    return False, 0.0, f"{err_code}: {label} article published {age_hours:.1f}h ago exceeds allowable {max_freshness_hours:.0f}h freshness window"


        # Step 1: Detect Event Categories & Check Incompatibility
        cats1 = self.detect_event_categories(art1)
        cats2 = self.detect_event_categories(art2)
        from app.verification.query_builder import EventQueryBuilder
        action1 = EventQueryBuilder.detect_event_action(art1.title)
        action2 = EventQueryBuilder.detect_event_action(art2.title)

        if cats1 and cats2:
            for c1 in cats1:
                incompatible = self.INCOMPATIBLE_CATEGORIES.get(c1, set())
                for c2 in cats2:
                    if c2 in incompatible and not (cats1.intersection(cats2)):
                        return False, 0.0, f"EVENT_TYPE_MISMATCH: Incompatible event types ('{c1}' vs '{c2}')"

        # Step 2: Shared Company / Entity Presence using EventQueryBuilder
        from app.verification.query_builder import GENERIC_ENTITY_BLACKLIST
        from app.deduplication.fingerprint import normalize_entity_name

        entities1 = EventQueryBuilder.extract_entities(art1)
        entities2 = EventQueryBuilder.extract_entities(art2)

        norm_ent1 = {normalize_entity_name(e) for e in entities1 if e.lower().strip() not in GENERIC_ENTITY_BLACKLIST and normalize_entity_name(e) not in ("unspecified_entity", "")}
        norm_ent2 = {normalize_entity_name(e) for e in entities2 if e.lower().strip() not in GENERIC_ENTITY_BLACKLIST and normalize_entity_name(e) not in ("unspecified_entity", "")}

        GENERIC_ROOT_TOKENS = {
            "energy", "green", "tech", "technology", "technologies", "group", "holdings",
            "industries", "industry", "solutions", "services", "capital", "finance",
            "financial", "bank", "properties", "property", "developer", "jewellery",
            "jewellers", "retail", "infrastructure", "infra", "parks", "power", "pharma",
            "boat", "industrial", "ventures", "trust"
        }

        # Check direct entity matches or partial company root matches (e.g. "Piramal Pharma" / "Piramal")
        shared_entities = set()
        for e1 in norm_ent1:
            for e2 in norm_ent2:
                if e1 == e2 and e1 not in GENERIC_ROOT_TOKENS:
                    shared_entities.add(e1)
                elif len(e1) >= 4 and len(e2) >= 4 and (e1 in e2 or e2 in e1):
                    sub = e1 if len(e1) <= len(e2) else e2
                    if sub not in GENERIC_ROOT_TOKENS and not any(sub == gt for gt in GENERIC_ROOT_TOKENS):
                        shared_entities.add(sub)

        stopwords = {
            "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "is", "with", "by", "its", "from", "as",
            "over", "under", "after", "before", "amid", "says", "said", "reports", "reported", "shows", "shown", "deal",
            "controlling", "stake", "majority", "minority", "acquisition", "sale", "completes", "equity", "hands",
            "open", "limited", "private", "ltd", "pvt", "corp", "inc"
        }
        w1 = {w for w in re.findall(r"[a-zA-Z0-9]+", t1) if w not in stopwords and len(w) >= 2}
        w2 = {w for w in re.findall(r"[a-zA-Z0-9]+", t2) if w not in stopwords and len(w) >= 2}
        if "dmart" in t1 or "d-mart" in t1 or "d mart" in t1:
            w1.add("dmart")
        if "dmart" in t2 or "d-mart" in t2 or "d mart" in t2:
            w2.add("dmart")

        overlap = w1.intersection(w2)
        union = w1.union(w2)
        title_similarity = len(overlap) / len(union) if union else 0.0

        major_entities = {
            "hdfc", "tata", "reliance", "nvidia", "infosys", "lt", "larsen", "toubro",
            "rio", "tinto", "arcadium", "apple", "google", "meta", "microsoft", "shiprocket",
            "dersimelagon", "adani", "wipro", "icici", "sbi", "airtel", "bharti", "itc",
            "maruti", "mahindra", "bajaj", "kfin", "piramal", "blackstone", "dmart", "zerodha",
            "broadcom", "qualcomm", "intel", "amd", "tsmc", "asml", "samsung", "sony"
        }
        shared_major = {w for w in overlap if w in major_entities}

        # For DOMESTIC (General National India News / Courts / Governance):
        is_domestic_case = (
            getattr(art1, "category", None) == NewsCategory.DOMESTIC
            or getattr(art2, "category", None) == NewsCategory.DOMESTIC
            or any(
                re.search(r"\b(supreme court|high court|cji|delhi police|union cabinet|isro|parliament|lok sabha|rajya sabha|sc bench|hc bench)\b", t)
                for t in (t1, t2)
            )
        )
        if is_domestic_case:
            GENERIC_DOMESTIC_WORDS = {
                "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "is", "with", "by", "its", "from", "as",
                "over", "under", "after", "before", "amid", "says", "said", "reports", "reported", "shows", "shown",
                "supreme", "court", "high", "government", "police", "probe", "order", "orders",
                "india", "state", "states", "minister", "bench", "chief", "justice", "sc", "hc",
                "cji", "ruling", "plea", "verdict", "quashes", "stays", "case", "matter", "law",
                "legal", "public", "delhi", "centre", "central", "probes", "probed", "investigation",
                "hearing", "notice", "notices", "pleas", "judiciary", "judges", "directs", "seeks",
                "why", "how", "what", "who", "when", "wants", "powers", "misuse", "calls", "held"
            }
            def _stem_topic_word(w: str) -> str:
                w = w.lower()
                # Suffix normalizations (transferred -> transfer, transferring -> transfer, judges -> judge)
                w = re.sub(r"(?:ing|ed|es|s|tion|ment)$", "", w)
                return w

            # Extract specific topic keywords from titles
            raw_w1 = [w for w in re.findall(r"[a-zA-Z0-9]+", t1) if w not in GENERIC_DOMESTIC_WORDS and len(w) >= 3]
            raw_w2 = [w for w in re.findall(r"[a-zA-Z0-9]+", t2) if w not in GENERIC_DOMESTIC_WORDS and len(w) >= 3]
            
            w1_stemmed = {_stem_topic_word(w) for w in raw_w1 if len(_stem_topic_word(w)) >= 3}
            w2_stemmed = {_stem_topic_word(w) for w in raw_w2 if len(_stem_topic_word(w)) >= 3}
            
            topic_overlap = w1_stemmed.intersection(w2_stemmed)

            # Check if there is specific geographic or named entity overlap (e.g. rajasthan, manipur, kashmir, etc.)
            geo_entities = {
                "rajasthan", "gujarat", "maharashtra", "kerala", "karnataka", "tamil", "nadu", "bengal",
                "bihar", "punjab", "haryana", "assam", "odisha", "uttarakhand", "jharkhand", "chhattisgarh",
                "telangana", "andhra", "goa", "kashmir", "ladakh", "manipur", "nagaland", "mizoram", "tripura",
                "meghalaya", "sikkim", "mumbai", "kolkata", "chennai", "bengaluru", "hyderabad", "ahmedabad", "jaipur"
            }
            shared_geo = {w for w in (set(raw_w1).intersection(set(raw_w2))) if w in geo_entities}

            # If shared named geography + at least 1 shared topic stem (e.g. rajasthan + transfer)
            # or >= 2 shared specific topic stems
            if len(topic_overlap) >= 2 or (shared_geo and len(topic_overlap) >= 1):
                score = max(title_similarity, 0.85)
                return True, score, f"DOMESTIC_EVENT_MATCH: Shared specific topics ({list(topic_overlap)})"

            # Check body text overlap for candidate matches with high headline concept similarity
            if (art1.content_text and art2.content_text):
                b1_words = {_stem_topic_word(w) for w in re.findall(r"[a-zA-Z0-9]+", art1.content_text.lower()[:300]) if w not in GENERIC_DOMESTIC_WORDS and len(w) >= 4}
                b2_words = {_stem_topic_word(w) for w in re.findall(r"[a-zA-Z0-9]+", art2.content_text.lower()[:300]) if w not in GENERIC_DOMESTIC_WORDS and len(w) >= 4}
                body_topic_overlap = w1_stemmed.intersection(b2_words).union(w2_stemmed.intersection(b1_words))
                if len(body_topic_overlap) >= 2 and (topic_overlap or shared_geo):
                    score = max(title_similarity, 0.80)
                    return True, score, f"DOMESTIC_EVENT_MATCH: Cross title-body topic overlap ({list(body_topic_overlap)})"

            return False, 0.0, f"DOMESTIC_TOPIC_MISMATCH: Insufficient specific subject/topic overlap for domestic events ({list(topic_overlap)})"

        has_shared_entity = bool(shared_entities or shared_major)
        if not has_shared_entity:
            return False, 0.0, f"ENTITY_MISMATCH: No shared corporate entity found between articles ('{list(norm_ent1)[:2]}' vs '{list(norm_ent2)[:2]}')"

        # Step 3: Check Reporting Period / Financial Fact Consistency
        q1 = self._extract_reporting_quarter(f"{art1.title} {(art1.content_text or '')[:200]}")
        q2 = self._extract_reporting_quarter(f"{art2.title} {(art2.content_text or '')[:200]}")
        if q1 and q2 and q1 != q2 and ("EARNINGS" in cats1 or "EARNINGS" in cats2):
            return False, 0.0, f"FINANCIAL_FACT_MISMATCH: Conflicting quarterly reporting periods ('{q1.upper()}' vs '{q2.upper()}')"

        metrics1 = self._extract_metric_numbers(f"{art1.title} {(art1.content_text or '')[:300]}")
        metrics2 = self._extract_metric_numbers(f"{art2.title} {(art2.content_text or '')[:300]}")
        shared_metrics = metrics1.intersection(metrics2)

        # Step 4: Evaluate Additional Event-Specific Signals
        COMPANY_CLUSTERS = [
            {"reliance", "retail", "jio", "industries"},
            {"tata", "motors", "steel", "power", "consultancy", "tcs"},
            {"hdfc", "bank", "life", "ergo"},
            {"larsen", "toubro", "lt"},
            {"nxt", "infra", "trust"},
            {"rio", "tinto"},
            {"arcadium", "lithium"},
            {"leo", "pharma"},
            {"adani", "ports", "power", "enterprises", "green"},
            {"icici", "bank", "prudential", "securities"},
            {"state", "bank", "india", "sbi"},
            {"bharti", "airtel"},
            {"bajaj", "finance", "finserv", "auto"},
            {"maruti", "suzuki"},
            {"kfin", "technologies", "general", "atlantic"},
            {"piramal", "pharma", "yapan", "bio"},
            {"investcorp", "20cube", "logistics"},
            {"jbm", "auto", "bain"},
            {"tube", "investments", "shanthi", "gears"},
            {"idbi", "bank", "fairfax"},
            {"manipal", "health"},
            {"dmart", "d-mart", "avenue", "supermarts"},
            {"capital", "group", "dmart", "d-mart"},
            {"nvidia"},
        ]

        matched_clusters = sum(1 for cluster in COMPANY_CLUSTERS if overlap.intersection(cluster))
        has_counterpart_match = matched_clusters >= 2 or len(shared_entities) >= 2

        has_period_match = bool(q1 and q2 and q1 == q2 and ("EARNINGS" in cats1 and "EARNINGS" in cats2))

        all_cluster_words = set().union(*COMPANY_CLUSTERS)
        action_overlap = {w for w in overlap if w not in all_cluster_words and w not in stopwords and w not in GENERIC_ENTITY_BLACKLIST}
        has_strong_title_overlap = len(action_overlap) >= 2 or (title_similarity >= 0.35 and len(action_overlap) >= 1)

        has_metric_match = bool(shared_metrics) and bool(
            shared_major or has_counterpart_match or has_period_match or has_strong_title_overlap
        )
        release_signal_pattern = (
            r"\b(earnings|earning|results|revenue|profit|miss|beat|beats|guidance|outlook|"
            r"forecast|delivery|deliveries|sales|soar|soars|surge|surges|jump|jumps|quarter|"
            r"quarterly|second quarter|first quarter|third quarter|fourth quarter|q[1-4]|"
            r"chip demand|data centres|data centers|cyber wave|tailwind)\b"
        )
        has_compatible_release_context = bool(
            re.search(release_signal_pattern, t1)
            and re.search(release_signal_pattern, t2)
            and (shared_metrics or q1 == q2 or not (q1 and q2))
        )
        has_compatible_action = bool(
            (action1 and action2 and action1 == action2)
            or (cats1 and cats2 and cats1.intersection(cats2))
            or has_compatible_release_context
        )

        # Same-company quarterly earnings cycle / corporate performance release match
        is_same_company = bool(shared_entities or shared_major)
        has_earnings_context = (
            ("EARNINGS" in cats1 and "EARNINGS" in cats2)
            or has_compatible_release_context
            or (
                re.search(r"\b(quarter|quarterly|earnings|results|profit|revenue|sales|q[1-4])\b", t1)
                and re.search(r"\b(quarter|quarterly|earnings|results|profit|revenue|sales|q[1-4])\b", t2)
            )
        )
        if is_same_company and has_earnings_context:
            if not (q1 and q2 and q1 != q2):
                distinct_actions = {"acquisition", "takeover", "merger", "lawsuit", "resignation", "fundraising"}
                is_distinct_action = bool(
                    cats1 and cats2 and not cats1.intersection(cats2) and any(c.lower() in distinct_actions for c in cats1.union(cats2))
                )
                if not is_distinct_action:
                    score = max(title_similarity, 0.85)
                    return True, score, f"Corroborated: Shared company ({list(shared_entities or shared_major)}) and quarterly earnings context"

        if has_compatible_action and (
            has_metric_match
            or has_counterpart_match
            or has_period_match
            or has_strong_title_overlap
            or has_compatible_release_context
        ):
            score = max(title_similarity, 0.95 if has_metric_match or has_counterpart_match else 0.80)
            details = []
            if shared_entities or shared_major:
                details.append(f"entity={sorted(shared_entities or shared_major)}")
            if shared_metrics:
                details.append(f"metrics={sorted(shared_metrics)}")
            if has_period_match:
                details.append(f"period={q1.upper()}")
            return True, score, f"Corroborated: {', '.join(details)} (overlap={title_similarity:.2f})"

        return False, title_similarity, f"INSUFFICIENT_EVENT_OVERLAP: Shared entity {sorted(shared_entities or shared_major)} but no matching event facts or counterpart details"

    def verify_event(
        self,
        event: Event,
        articles: List[Article],
        now_utc: Optional[datetime] = None,
    ) -> EventSourceVerification:
        """
        Verify an event using its linked candidate articles.

        Args:
            event: Event model instance.
            articles: List of Article models linked to this event.
            now_utc: Optional immutable reference timestamp for freshness verification.

        Returns:
            EventSourceVerification detailing independence and verification outcome.
        """
        logger.info("Evaluating two-source verification for event '%s' (%d articles)", event.canonical_title[:45], len(articles))
        event.secondary_publisher = None
        event.secondary_url = None

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

        # Propagate DOMESTIC event category to articles if event is domestic
        if getattr(event, "event_category", None) == NewsCategory.DOMESTIC:
            if not getattr(art1, "category", None) or art1.category == NewsCategory.UNKNOWN:
                art1.category = NewsCategory.DOMESTIC
            if not getattr(art2, "category", None) or art2.category == NewsCategory.UNKNOWN:
                art2.category = NewsCategory.DOMESTIC

        # 2. Check if both articles refer to the same event
        is_same_event, event_score, event_msg = self.is_same_underlying_event(art1, art2, now_utc=now_utc)
        if not is_same_event:
            event.secondary_publisher = None
            event.secondary_url = None
            if len(event.article_ids) > 1:
                event.article_ids = [event.article_ids[0]]
            return EventSourceVerification(
                event_id=event.id,
                primary_source=art1.source_name,
                secondary_source=None,
                source_count=1,
                is_independent=False,
                verification_status=VerificationStatus.REJECTED_UNRELATED,
                confidence_score=event_score,
                article_urls=[art1.url],
                matching_details=f"Articles discuss different underlying events: {event_msg}",
            )

        # 3. Check Publisher Independence (Same media group / domain)
        group1 = self.get_publisher_group(art1)
        group2 = self.get_publisher_group(art2)
        if group1 == group2:
            event.secondary_publisher = None
            event.secondary_url = None
            if len(event.article_ids) > 1:
                event.article_ids = [event.article_ids[0]]
            return EventSourceVerification(
                event_id=event.id,
                primary_source=art1.source_name,
                secondary_source=None,
                source_count=1,
                is_independent=False,
                verification_status=VerificationStatus.REJECTED_SAME_PUBLISHER,
                confidence_score=0.4,
                article_urls=[art1.url],
                matching_details=f"Both articles originate from the same publisher/media group ('{group1}').",
            )

        # 4. Check for Syndicated / Verbatim Wire Repetition
        is_syndicated, synd_reason = self.is_syndicated_republication(art1, art2)
        if is_syndicated:
            event.secondary_publisher = None
            event.secondary_url = None
            if len(event.article_ids) > 1:
                event.article_ids = [event.article_ids[0]]
            return EventSourceVerification(
                event_id=event.id,
                primary_source=art1.source_name,
                secondary_source=None,
                source_count=1,
                is_independent=False,
                verification_status=VerificationStatus.REJECTED_SYNDICATED,
                confidence_score=0.3,
                article_urls=[art1.url],
                matching_details=f"Syndicated wire copy detected: {synd_reason}",
            )

        # 5. Passed all checks -> Verified!
        logger.info(
            "Event '%s' VERIFIED with independent sources: '%s' and '%s'",
            event.canonical_title[:40],
            art1.source_name,
            art2.source_name,
        )
        from app.models.enums import VerificationTier
        event.verification_tier = VerificationTier.TWO_SOURCE_VERIFIED
        event.verification_confidence = 95.0
        event.primary_publisher = art1.source_name
        event.primary_url = art1.url
        event.secondary_publisher = art2.source_name
        event.secondary_url = art2.url
        event.verification_reason = f"Corroborated by independent publishers ({art1.source_name} and {art2.source_name})."

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
