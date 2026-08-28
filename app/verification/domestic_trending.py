"""
Domestic Trending News Evaluator for General National India News.

Evaluates nationally important, general trending Indian news stories
(politics, courts, government policies, infrastructure, defence, science/ISRO,
disasters, education, health, and national public interest).
"""

from datetime import datetime, timezone
import re
from typing import List, Optional, Set, Tuple

from app.logging_config import get_logger
from app.models.article import Article
from app.models.enums import NewsCategory, VerificationTier
from app.models.event import Event

logger = get_logger("verification.domestic_trending")

# Trusted Domestic General News Publishers
TRUSTED_DOMESTIC_PUBLISHERS: Set[str] = {
    "the hindu",
    "hindu",
    "the indian express",
    "indian express",
    "hindustan times",
    "ndtv",
    "ndtv news",
    "ndtv profit",
    "india today",
    "times of india",
    "the times of india",
    "economic times",
    "the economic times",
    "business standard",
    "livemint",
    "mint",
    "the hindu businessline",
    "hindu businessline",
    "businessline",
    "financial express",
    "the financial express",
    "bbc",
    "bbc news",
    "bbc news hindi",
    "pti",
    "press trust of india",
    "ani",
    "asian news international",
    "pib",
    "press information bureau",
    "pmo",
    "prime minister's office",
    "isro",
    "imd",
    "supreme court",
    "supreme court of india",
    "sci",
}

# Domains of official/trusted domestic sources
TRUSTED_DOMESTIC_DOMAINS: Set[str] = {
    "thehindu.com",
    "indianexpress.com",
    "hindustantimes.com",
    "ndtv.com",
    "indiatoday.in",
    "timesofindia.indiatimes.com",
    "economictimes.indiatimes.com",
    "business-standard.com",
    "livemint.com",
    "thehindubusinessline.com",
    "financialexpress.com",
    "bbc.com",
    "pib.gov.in",
    "pmindia.gov.in",
    "isro.gov.in",
    "mausam.imd.gov.in",
    "sci.gov.in",
    "gov.in",
    "nic.in",
}

from enum import Enum

# Domestic Topic Classification Taxonomy
class DomesticTopic(str, Enum):
    COURT_JUDICIARY = "COURT_JUDICIARY"
    GOVERNMENT_POLICY = "GOVERNMENT_POLICY"
    POLITICS_ELECTIONS = "POLITICS_ELECTIONS"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    DEFENCE_SECURITY = "DEFENCE_SECURITY"
    SCIENCE_ISRO_TECH = "SCIENCE_ISRO_TECH"
    WEATHER_DISASTER = "WEATHER_DISASTER"
    HEALTH = "HEALTH"
    EDUCATION = "EDUCATION"
    ENVIRONMENT = "ENVIRONMENT"
    OTHER_NATIONAL = "OTHER_NATIONAL"


# Topic Detection Patterns
DOMESTIC_TOPIC_PATTERNS: List[Tuple[DomesticTopic, List[str]]] = [
    (
        DomesticTopic.COURT_JUDICIARY,
        [
            r"\b(supreme court|high court|chief justice|cji|sc bench|hc bench|quashes|stays order|plea in court|judiciary|collegium|bail granted|anticipatory bail|sessions court|magistrate|tribunal|trial court|judge seeks transfer|judge transferred|judge wants|contempt of court|law commission|constitution bench|judicial probe|orders sit|oppose bail|opposes bail|seeks bail|bail plea|bail hearing|delhi riots case)\b",
            r"\b(judge|justice|advocate|bench|court|magistrate)\b.*\b(transfer|transferred|hearing|verdict|ruling|orders|probe|sits|plea|ban)\b",
        ],
    ),
    (
        DomesticTopic.DEFENCE_SECURITY,
        [
            r"\b(indian army|indian navy|indian air force|iaf|drdo|missile test|border security|anti-terror|nia|crpf|bsf|loc|lac|defence procurement|warship|submarine|combat aircraft|ins vikrant|security forces|armed forces|anti-naxal)\b",
        ],
    ),
    (
        DomesticTopic.SCIENCE_ISRO_TECH,
        [
            r"\b(isro|chandrayaan|gaganyaan|aditya-l1|satellite launch|pslv|gslv|space mission|supercomputer|quantum mission|ai mission|iit|scientific discovery|nuclear reactor|bhabha atomic)\b",
        ],
    ),
    (
        DomesticTopic.WEATHER_DISASTER,
        [
            r"\b(cyclone|landslide|cloudburst|flood|floods|flooding|earthquake|imd alert|heatwave|red alert|rescue operation|ndrf|monsoon forecast|heavy rain|cold wave|avalanche|drowning)\b",
        ],
    ),
    (
        DomesticTopic.INFRASTRUCTURE,
        [
            r"\b(railway corridor|vande bharat|national highway|expressway|metro rail|bullet train|mega bridge|airport terminal|nhai|tunnel|port project|smart cities|freight corridor|railways|rail line)\b",
        ],
    ),
    (
        DomesticTopic.HEALTH,
        [
            r"\b(ayushman bharat|vaccination|icmr|who alert|aiims|hospital|medical college|disease outbreak|public health|dengue|mpox|generic medicine|health mission|neet pg)\b",
        ],
    ),
    (
        DomesticTopic.EDUCATION,
        [
            r"\b(national education policy|nep|ncert|ugc|neet|cbse|iim|university|board exams|school syllabus|scholarship|higher education|professors|admissions)\b",
        ],
    ),
    (
        DomesticTopic.ENVIRONMENT,
        [
            r"\b(pollution|air quality|aqi|forest cover|wildlife sanctuary|tiger reserve|western ghats|renewable energy|solar park|green hydrogen|climate action|smog|yamuna clean)\b",
        ],
    ),
    (
        DomesticTopic.POLITICS_ELECTIONS,
        [
            r"\b(election commission|ec|eci|assembly election|lok sabha election|bypoll|polling|voter turnout|political rally|seat sharing|opposition alliance|party president|bjp|congress|aap|tmc|election dates|campaigning)\b",
        ],
    ),
    (
        DomesticTopic.GOVERNMENT_POLICY,
        [
            r"\b(union cabinet|cabinet approves|cabinet clears|parliament|lok sabha|rajya sabha|bill passed|new national law|centre notifies|centre announces|pmo|prime minister|narendra modi|ministry of|gazette notification|scheme launched|central government|sub-quota|caste census)\b",
        ],
    ),
]


def classify_domestic_topic(title: str, text: Optional[str] = None) -> DomesticTopic:
    """Deterministically classify a domestic news story into its specific topic category."""
    comb = f"{title or ''} {(text or '')[:300]}".lower()
    for topic, patterns in DOMESTIC_TOPIC_PATTERNS:
        for pat in patterns:
            if re.search(pat, comb):
                return topic
    return DomesticTopic.OTHER_NATIONAL


# Hard noise rejection patterns for Domestic General News
DOMESTIC_NOISE_PATTERNS: List[str] = [
    # Celebrity gossip & Bollywood entertainment fluff
    r"\b(box office collection|ott release|trailer launch|spotted at airport|fashion goals|red carpet|dating rumours?|viral video of (?:actor|actress|influencer))\b",
    r"\b(bollywood (?:actor|actress|gossip)|star kid|bikini|swimsuit|paparazzi|trolled for|stuns in)\b",
    
    # Horoscopes, astrology, numerology
    r"\b(horoscope|zodiac sign|astrology|rashifal|daily prediction|tarot card|vastu shastra|numerology)\b",
    
    # Lifestyle, recipes, beauty & health tips
    r"\b(recipe|how to cook|weight loss tips|skincare routine|hair loss remedies|superfoods to eat|yoga poses for)\b",
    
    # Shopping guides & product reviews
    r"\b(best (?:phones|laptops|tvs|shoes|cars|watches|earphones|ac) to buy|buying guide|amazon sale|flipkart sale|deals of the day|discount on)\b",
    
    # Personal finance, mutual funds, stock recommendations (NOT general news)
    r"\b(best (?:.*?\s+)?(?:mutual funds?|gilt funds?|gilt mutual funds?|sip|fixed deposits?|smallcap funds?|elss|stocks to buy|penny stocks))\b",
    r"\b(where to invest in|how to invest in|funds to invest in|mutual funds to invest|portfolio review|multibagger stock)\b",
    
    # Local crime, petty theft, minor accidents
    r"\b(minor theft|chain snatching|petty theft|arrested for stealing|two held for theft|biker injured|accident in local|car overturns on|local police arrest)\b",
    
    # Sports match scores, commentary & match summaries
    r"\b(scorecard|live commentary|match highlights|playing xi|toss update|ipl 20\d\d|runs win|wickets win|live cricket score)\b",
    
    # School / college internal cultural events
    r"\b(school assembly|cultural dance|annual day function|fancy dress|school principal)\b",
    
    # Viral animal / human interest fluff
    r"\b(viral video|heartwarming video|street dog|cute video|social media user|netizens react)\b",
    
    # Political reactions / commentary on judgments and events (NOT distinct national hard events)
    r"\b(slams?|reacts? to|calls? (?:ruling|verdict|decision|order|judgment)|attacks? (?:ruling|verdict|decision|order|judgment)|criticises? (?:ruling|verdict|decision|order|judgment)|praises? (?:ruling|verdict|decision|order|judgment)|terms? (?:ruling|verdict|decision|order|judgment)|hits? out at (?:ruling|verdict|decision|order|judgment)|expresses? outrage|protests? against (?:ruling|verdict|decision))\b",
    r"\b(reaction to (?:ruling|verdict|judgment|decision|order)|calls? (?:allahabad hc|supreme court|high court|sc|hc) (?:ruling|verdict|decision|order|judgment))\b",

    # Generic explainers, listicles & evergreen features
    r"\b(top 10 (?:tourist|holiday|travel|places)|things to know before|explained: how does|a complete guide to|history of|all you need to know about (?:why|how))\b",
    
    # Exam preparation, study material, practice questions, quizzes (NOT trending news)
    r"\b(upsc mains answer practice|answer practice|upsc essentials|practice questions?|mock test|exam preparation|study material|current affairs quiz|daily quiz|question of the day|quiz of the day|sample papers?|previous year questions?|pyq|test series)\b",
    r"\b(school assembly news headlines|assembly headlines for school|thought for the day)\b",

    # Opinion columns, Editorials & Op-eds
    r"\b(opinion:|editorial:|column:|view:|analysis:|why we must|the need for|it is time to|op-ed|guest column|opinion piece|editorial board)\b",
    r"^(?:opinion|editorial|op-ed|column|analysis|view)\s*[:|-]",
    r"\b(and the (?:sir|citizenship|election|constitution) exercise)\b",

    # Rhetorical questions, debate framing & commentary
    r"\b(boom or bust\??|bane or boon\??|threat or opportunity\??)\b",
    r"\b(pros and cons of|myth or reality\??|need of the hour\??|what lies ahead for)\b",
]

# National Significance Markers
NATIONAL_SIGNIFICANCE_PATTERNS: List[str] = [
    # Government, Parliament, Cabinet, Law
    r"\b(union cabinet|cabinet approves|prime minister|narendra modi|parliament|lok sabha|rajya sabha|bill passed|supreme court|high court|chief justice|cji|election commission|ec|eci|polling|election result)\b",
    # Infrastructure, Transport, Energy
    r"\b(railway project|vande bharat|expressway|national highway|nhai|metro rail|airport inaugurat|bullet train|nuclear plant|solar park|power grid|border road|tunnel)\b",
    # Defence & National Security
    r"\b(indian army|indian navy|indian air force|iaf|drdo|defence acquisition|missile test|combat aircraft|ins |lac|loc|security forces|bsf|crpf|anti-terror|nia)\b",
    # Science, Space & Technology
    r"\b(isro|chandrayaan|gaganyaan|aditya-l1|satellite launch|pslv|gslv|quantum mission|supercomputer|ai mission|iit\s+(?:director|research|breakthrough|innovation|campus|ai)|aiims\s+(?:director|research|breakthrough|expansion)|drdo)\b",
    # Environment, Disasters & Weather
    r"\b(cyclone|landslide|flood|earthquake|imd alert|heatwave|monsoon forecast|red alert|rescue operation|ndrf)\b",
    # Health, Education & Public Policy
    r"\b(national education policy|nep|ncert|ugc|neet|ayushman bharat|vaccination drive|icmr|who alert|public health mission|food security)\b",
]

# Local Municipal & Civic Patterns (ordinary local scope without national impact)
LOCAL_MUNICIPAL_PATTERNS: List[str] = [
    r"\b(municipal corporation|civic body|city corporation|nagar nigam|municipality|ward council|district administration)\b",
    r"\b(bulk water supply|water supply scheme|sewage treatment plant|water pipeline|drainage project|civic project|local road repair|desilting work|pothole|waterlogging in (?:city|ward))\b",
    r"\b(?:civic|municipal|district)\s+(?:officials?|authorities|project|scheme|body|inspection|engineers?)\b",
]


class DomesticTrendingEvaluator:
    """
    Deterministic evaluator for General Trending Domestic India News.
    Evaluates events on a 0-100 quality/trending scale.
    Threshold for qualification: >= 60.
    """

    def is_trusted_domestic_source(self, source_name: Optional[str], url: Optional[str] = None) -> bool:
        """Check if publisher or domain is in approved domestic whitelist."""
        if source_name:
            norm = source_name.strip().lower()
            norm = re.sub(r"^(the\s+)", "", norm)
            if any(tp in norm or norm in tp for tp in TRUSTED_DOMESTIC_PUBLISHERS):
                return True
        if url:
            u_low = url.lower()
            if any(dom in u_low for dom in TRUSTED_DOMESTIC_DOMAINS):
                return True
        return False

    def is_domestic_noise(self, title: str, text: Optional[str] = None, url: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """Detect noise, gossip, horoscope, product reviews, personal finance, explainers, opinion/op-eds."""
        # 1. Check Opinion / Op-ed / Editorial in URL path
        if url:
            u_low = url.lower()
            if any(p in u_low for p in ("/opinion/", "/op-ed/", "/editorial/", "/columns/", "/commentary/", "/readers-editor/")):
                # Accept only if it is a concrete breaking hard event headline, otherwise reject commentary/op-eds
                has_hard_action = bool(re.search(
                    r"\b(approves?|clears?|orders?|rules?|directs?|launches?|inaugurates?|passes?|issues?|announces?|arrests?|bans?|quashes?|stays?)\b",
                    title.lower()
                ))
                is_explicit_opinion = bool(re.search(
                    r"\b(opinion|editorial|op-ed|column|view|why |the need for|analysis:|and the )\b",
                    title.lower()
                ))
                if not has_hard_action or is_explicit_opinion:
                    return True, "Opinion/Op-ed URL path without concrete breaking event"

        # 2. Check rhetorical questions / debate framing without concrete action verbs
        if "?" in title:
            has_action = bool(re.search(
                r"\b(approves?|clears?|orders?|rules?|directs?|launches?|inaugurates?|passes?|issues?|announces?|arrests?|bans?|quashes?|stays?|hears?|rejects?)\b",
                title.lower()
            ))
            if not has_action:
                return True, "Rhetorical question / commentary framing without concrete action event"

        comb = f"{title} {(text or '')[:300]}".lower()
        for pat in DOMESTIC_NOISE_PATTERNS:
            m = re.search(pat, comb)
            if m:
                return True, f"Noise pattern match: '{m.group(0)}'"
        return False, None

    def evaluate(
        self,
        event: Event,
        primary_article: Optional[Article] = None,
        now_utc: Optional[datetime] = None,
        discovery_mentions: int = 1,
    ) -> Tuple[bool, float, str]:
        """
        Evaluate domestic candidate event for quality and trending significance.

        Scoring Breakdown:
        - Freshness: <= 6h (+25), <= 12h (+20), <= 24h (+15)
        - Trusted major publication: +20
        - National significance: +20
        - Concrete current event: +15
        - Multiple discovery mentions / sources: +10
        - Strong named subject: +5
        - Successful body extraction: +5

        Qualification: Score >= 60, zero hard noise violations.
        """
        title = event.canonical_title or (primary_article.title if primary_article else "")
        body = (primary_article.content_text or "") if primary_article else (event.description or "")
        now = now_utc or datetime.now(timezone.utc)
        url = (primary_article.url if primary_article else None) or event.primary_url or ""

        # 1. Hard Noise Check
        is_noise, noise_rsn = self.is_domestic_noise(title, body, url=url)
        if is_noise:
            return False, 0.0, f"REJECT_NOISE: {noise_rsn}"

        # 2. Minimum content check
        word_count = len(body.split())
        if word_count < 25 and len(title.split()) < 6:
            return False, 0.0, "REJECT: Insufficient article extraction body (<25 words)"

        # 3. Compute Score
        score = 0.0
        reasons: List[str] = []

        # A. Freshness
        pub_time = getattr(primary_article, "published_at", None) if primary_article else None
        if pub_time:
            if pub_time.tzinfo is None:
                pub_time = pub_time.replace(tzinfo=timezone.utc)
            age_hours = max(0.0, (now - pub_time).total_seconds() / 3600.0)
            if age_hours > 24.0:
                return False, 0.0, f"REJECT_STALE: {age_hours:.1f}h old (>24h limit)"
            if age_hours <= 6.0:
                score += 25.0
                reasons.append("fresh_6h(+25)")
            elif age_hours <= 12.0:
                score += 20.0
                reasons.append("fresh_12h(+20)")
            else:
                score += 15.0
                reasons.append("fresh_24h(+15)")
        else:
            score += 15.0
            reasons.append("fresh_default(+15)")

        # B. Trusted Publication
        source_name = getattr(primary_article, "source_name", None) or event.primary_publisher or ""
        url = getattr(primary_article, "url", None) or event.primary_url or ""
        if self.is_trusted_domestic_source(source_name, url):
            score += 20.0
            reasons.append(f"trusted_source({source_name})(+20)")
        else:
            reasons.append(f"unknown_source({source_name})(+0)")

        # C. National Significance
        comb_text = f"{title} {body[:400]}".lower()
        has_national_sig = any(re.search(pat, comb_text) for pat in NATIONAL_SIGNIFICANCE_PATTERNS)

        # Check local municipal scope: local civic/municipal updates do not receive national significance
        # unless they have overriding national/state judicial, cabinet, disaster, or strategic infrastructure impact
        is_city_url = bool(re.search(r"/(?:city|cities)/[a-z0-9_-]+", url.lower()))
        is_local_scope = any(re.search(pat, comb_text) for pat in LOCAL_MUNICIPAL_PATTERNS)
        has_overriding_national_event = bool(re.search(
            r"\b(supreme court|high court|chief justice|cji|union cabinet|cabinet approves|prime minister|narendra modi|parliament|lok sabha|rajya sabha|bill passed|election commission|ec|eci|bullet train|vande bharat|national highway|expressway|nuclear plant|solar park|isro|gaganyaan|chandrayaan|satellite launch|indian army|indian navy|indian air force|drdo|missile test|cyclone|landslide|earthquake|imd alert|red alert|ndrf|cbi|nia|anti-terror|national education policy|ayushman bharat)\b",
            comb_text
        ))

        if (is_city_url or is_local_scope) and not has_overriding_national_event:
            has_national_sig = False

        if has_national_sig:
            score += 20.0
            reasons.append("national_significance(+20)")

        # D. Concrete Current Event (Action verb / occurrence)
        has_concrete_event = bool(re.search(
            r"\b(approves?|clears?|orders?|rules?|directs?|launches?|inaugurates?|strikes?|hits?|passes?|issues?|announces?|holds?|flags off|rescues?|tests?|declares?|arrests?|bans?|rejects?|dismisses?|upholds?|quashes?|stays?)\b",
            comb_text
        ))
        if has_concrete_event:
            score += 15.0
            reasons.append("concrete_event(+15)")

        # E. Multiple Discovery Mentions / Corroborating Sources
        total_mentions = max(discovery_mentions, len(event.article_ids))
        if total_mentions >= 2:
            score += 10.0
            reasons.append(f"trending_mentions({total_mentions})(+10)")

        # F. Strong Named Subject
        if event.companies_involved or re.search(r"\b(india|delhi|mumbai|karnataka|tamil nadu|odisha|kerala|uttar pradesh|gujarat|bengal|bengaluru|supreme court|centre|cabinet|parliament|isro|army|navy|air force|railways|modi)\b", comb_text):
            score += 5.0
            reasons.append("named_subject(+5)")

        # G. Successful Body Extraction
        if word_count >= 50:
            score += 5.0
            reasons.append("rich_body(+5)")

        qualifies = (score >= 60.0)
        summary_reason = f"{'QUALIFIED' if qualifies else 'REJECT_LOW_SCORE'}: score={score:.1f}/100 [{', '.join(reasons)}]"
        return qualifies, score, summary_reason
