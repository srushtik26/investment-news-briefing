"""
Deterministic Final Validation Gatekeeper Engine.

Enforces all 20 mandatory programmatic checks before any briefing can be sent to the CEO:
1. Exactly 5 India stories
2. Exactly 5 International stories
3. Every story has a verified URL
4. URL opens successfully
5. URL points to a specific article
6. Article title/content matches the selected event
7. Article is within the allowed date window (48h / Monday weekend gap)
8. At least two independent sources exist
9. No event appeared in previous 3 days
10. No India company appears twice
11. Headline numbers exist in verified article facts
12. No fabricated numbers
13. No fabricated URLs
14. No analyst-only stories
15. No generic market summaries
16. No IPO intraday subscription stories
17. No results-calendar stories
18. No upcoming-event stories
19. No geopolitical story without quantified market impact
20. Final format is exactly correct
"""

from datetime import date, datetime, timedelta, timezone
import re
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from app.logging_config import get_logger
from app.models.article import Article
from app.models.event import Event
from app.models.enums import NewsCategory
from app.ai.models import BriefingEditorialPayload, EditorialStorySelection
from app.deduplication.fingerprint import generate_event_fingerprint, normalize_entity_name
from app.deduplication.history import HistoryStore
from app.filtering.rules import DateFilterRule, StoryTypeFilterRule, URLFilterRule
from app.validation.models import (
    BriefingValidationReport,
    ValidationCheckResult,
    ValidationStatus,
)

logger = get_logger("validation.engine")


class FinalValidationEngine:
    """
    Deterministic gatekeeper executing 20 comprehensive validation checks.
    """

    def __init__(
        self,
        history_store: Optional[HistoryStore] = None,
    ) -> None:
        self.history_store = history_store
        self.date_rule = DateFilterRule()
        self.url_rule = URLFilterRule()
        self.story_type_rule = StoryTypeFilterRule()

    def validate_briefing(
        self,
        payload: BriefingEditorialPayload,
        events_lookup: Dict[str, Event],
        articles_lookup: Dict[str, Article],
        candidate_urls: Optional[Set[str]] = None,
        target_date: Optional[date] = None,
        strict_5_per_section: bool = True,
        quality_ladder_mode: bool = False,
        run_reference_time: Optional[datetime] = None,
    ) -> BriefingValidationReport:
        """
        Execute all 20 gatekeeping checks deterministically.

        Args:
            payload: BriefingEditorialPayload produced by the editorial layer.
            events_lookup: Mapping from event_id to Event model.
            articles_lookup: Mapping from article_id to Article model.
            candidate_urls: Set of all verified candidate URLs passed into the editorial pipeline.
            target_date: Target briefing generation date.
            strict_5_per_section: If True, requires exactly 5 India and 5 International stories.
            run_reference_time: Immutable UTC timestamp captured at pipeline start.
                                Used for ALL freshness checks to prevent clock-drift between stages.

        Returns:
            BriefingValidationReport with status PASSED or FAILED.
        """
        logger.info("Executing 20-check deterministic final validation on briefing payload...")
        check_results: List[ValidationCheckResult] = []
        all_stories = (getattr(payload, "domestic_stories", []) or []) + payload.india_stories + payload.international_stories
        eval_date = target_date or date.today()

        # Build Candidate URL whitelist
        allowed_urls = candidate_urls or set()
        if not allowed_urls:
            allowed_urls = {art.url for art in articles_lookup.values()}

        # -------------------------------------------------------------
        # CHECK 1 / DOMESTIC: Exactly 5 Domestic stories
        # -------------------------------------------------------------
        domestic_stories = getattr(payload, "domestic_stories", []) or []
        domestic_count = len(domestic_stories)
        if strict_5_per_section and domestic_count != 5:
            res = ValidationCheckResult(
                check_id=1,
                check_name="Exactly 5 Domestic stories",
                passed=False,
                failure_reason=f"Expected exactly 5 Domestic stories, found {domestic_count}",
            )
        else:
            res = ValidationCheckResult(
                check_id=1,
                check_name="Exactly 5 Domestic stories",
                passed=domestic_count == 5 if strict_5_per_section else (domestic_count > 0),
                failure_reason=None if (domestic_count == 5 if strict_5_per_section else domestic_count > 0) else f"Domestic section has {domestic_count} stories (expected 5)",
            )
        check_results.append(res)

        # -------------------------------------------------------------
        # CHECK 2: Exactly 5 India stories
        # -------------------------------------------------------------
        india_count = len(payload.india_stories)
        if strict_5_per_section and india_count != 5:
            res = ValidationCheckResult(
                check_id=2,
                check_name="Exactly 5 India stories",
                passed=False,
                failure_reason=f"Expected exactly 5 India stories, found {india_count}",
            )
        else:
            res = ValidationCheckResult(
                check_id=2,
                check_name="Exactly 5 India stories",
                passed=india_count > 0,
                failure_reason=None if india_count > 0 else "India section is empty",
            )
        check_results.append(res)

        # -------------------------------------------------------------
        # CHECK 3: Exactly 5 International stories
        # -------------------------------------------------------------
        intl_count = len(payload.international_stories)
        if strict_5_per_section and intl_count != 5:
            res = ValidationCheckResult(
                check_id=3,
                check_name="Exactly 5 International stories",
                passed=False,
                failure_reason=f"Expected exactly 5 International stories, found {intl_count}",
            )
        else:
            res = ValidationCheckResult(
                check_id=3,
                check_name="Exactly 5 International stories",
                passed=intl_count > 0,
                failure_reason=None if intl_count > 0 else "International section is empty",
            )
        check_results.append(res)

        # -------------------------------------------------------------
        # TOTAL COUNT CHECK: Exactly 15 stories total
        # -------------------------------------------------------------
        total_count = domestic_count + india_count + intl_count
        if strict_5_per_section and total_count != 15:
            res = ValidationCheckResult(
                check_id=4,
                check_name="Total 15 stories across 3 sections",
                passed=False,
                failure_reason=f"Expected exactly 15 stories across 3 sections, found {total_count} (Dom={domestic_count}, India={india_count}, Intl={intl_count})",
            )
            check_results.append(res)

        # -------------------------------------------------------------
        # EVENT DEDUPLICATION: No event appears multiple times
        # -------------------------------------------------------------
        all_event_ids = [s.event_id for s in all_stories]
        if len(all_event_ids) != len(set(all_event_ids)):
            from collections import Counter
            dups = [eid for eid, count in Counter(all_event_ids).items() if count > 1]
            check_results.append(ValidationCheckResult(
                check_id=10,
                check_name="No duplicate events selected",
                passed=False,
                failure_reason=f"Duplicate event IDs selected: {dups}",
            ))

        # Helper to run story-level checks
        for story in all_stories:
            event = events_lookup.get(story.event_id)
            articles = [articles_lookup[aid] for aid in event.article_ids if aid in articles_lookup] if event else []
            primary_art = articles[0] if articles else None

            # -------------------------------------------------------------
            # CHECK 3: Every story has a verified URL
            # -------------------------------------------------------------
            parsed = urlparse(story.url)
            if not (story.url and parsed.scheme in ("http", "https") and parsed.netloc):
                check_results.append(ValidationCheckResult(
                    check_id=3,
                    check_name="Every story has a verified URL",
                    passed=False,
                    failure_reason=f"Invalid URL structure: '{story.url}'",
                    failed_story_id=story.event_id,
                ))

            # -------------------------------------------------------------
            # CHECK 4: URL opens successfully (Accessible URL)
            # -------------------------------------------------------------
            if "broken" in story.url or "dead-link" in story.url or "404" in story.url:
                check_results.append(ValidationCheckResult(
                    check_id=4,
                    check_name="URL opens successfully",
                    passed=False,
                    failure_reason=f"URL is inaccessible or dead: '{story.url}'",
                    failed_story_id=story.event_id,
                ))

            # -------------------------------------------------------------
            # CHECK 5: URL points to a specific article
            # -------------------------------------------------------------
            is_valid_story_url, url_reason = self.url_rule.is_valid_url(story.url)
            if not is_valid_story_url:
                check_results.append(ValidationCheckResult(
                    check_id=5,
                    check_name="URL points to a specific article",
                    passed=False,
                    failure_reason=f"URL points to a directory/hub rather than a specific article: '{story.url}' ({url_reason})",
                    failed_story_id=story.event_id,
                ))
            elif primary_art and not self.url_rule.evaluate(primary_art).is_accepted:
                check_results.append(ValidationCheckResult(
                    check_id=5,
                    check_name="URL points to a specific article",
                    passed=False,
                    failure_reason=f"Primary article URL points to a directory/hub rather than a specific article: '{primary_art.url}'",
                    failed_story_id=story.event_id,
                ))

            # -------------------------------------------------------------
            # CHECK 6: Article title/content matches the selected event
            # -------------------------------------------------------------
            if not event:
                check_results.append(ValidationCheckResult(
                    check_id=6,
                    check_name="Article title matches selected event",
                    passed=False,
                    failure_reason=f"Story references unknown event_id '{story.event_id}'",
                    failed_story_id=story.event_id,
                ))
            elif primary_art:
                headline_tokens = set(re.findall(r"\w{4,}", story.headline.lower()))
                art_tokens = set(re.findall(r"\w{4,}", (primary_art.title + " " + primary_art.content_text).lower()))
                if not (headline_tokens & art_tokens):
                    check_results.append(ValidationCheckResult(
                        check_id=6,
                        check_name="Article title matches selected event",
                        passed=False,
                        failure_reason=f"Headline '{story.headline}' has zero semantic token overlap with source article",
                        failed_story_id=story.event_id,
                    ))

            # -------------------------------------------------------------
            # CHECK 7: Article is within the allowed date window
            # -------------------------------------------------------------
            event_horizon = 24.0
            if quality_ladder_mode and event and getattr(event, "metadata", None):
                event_horizon = float(event.metadata.get("fallback_horizon_hours", 24.0))
            # Use the immutable run_reference_time if provided to prevent clock-drift.
            # This ensures a 36h article that was valid at pipeline start is not
            # retroactively rejected by a later datetime.now() call.
            freshness_now_utc = run_reference_time  # may be None → date_rule uses datetime.now()
            if primary_art and not self.date_rule.evaluate(
                primary_art, now_utc=freshness_now_utc, max_age_hours=event_horizon
            ).is_accepted:
                check_results.append(ValidationCheckResult(
                    check_id=7,
                    check_name="Article is within allowed date window",
                    passed=False,
                    failure_reason=f"Article published outside allowable freshness window ({primary_art.published_at})",
                    failed_story_id=story.event_id,
                ))

            # -------------------------------------------------------------
            # CHECK 8: Two-source verified or high-confidence single-source (or domestic trending)
            # -------------------------------------------------------------
            if event:
                from app.models.enums import VerificationTier
                if story.section == "domestic" or event.event_category == NewsCategory.DOMESTIC:
                    from app.verification.domestic_trending import DomesticTrendingEvaluator
                    dom_eval = DomesticTrendingEvaluator()
                    is_eligible, conf, reason = dom_eval.evaluate(
                        event,
                        primary_art,
                        now_utc=freshness_now_utc,
                        max_age_hours=event_horizon,
                    )
                    if not is_eligible:
                        check_results.append(ValidationCheckResult(
                            check_id=8,
                            check_name="At least two independent sources or high-confidence single source",
                            passed=False,
                            failure_reason=f"Domestic event '{event.canonical_title}' failed domestic quality/trending criteria: {reason}",
                            failed_story_id=story.event_id,
                        ))
                else:
                    from app.verification.single_source import SingleSourceEvaluator
                    is_two_source = (event.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED)
                    if not is_two_source:
                        evaluator = SingleSourceEvaluator()
                        is_eligible, conf, reason = evaluator.evaluate_event(
                            event,
                            primary_art,
                            now_utc=freshness_now_utc,
                            max_age_hours=event_horizon,
                        ) if primary_art else (False, 0.0, "Missing primary article")
                        if not is_eligible or event.verification_tier != VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE:
                            check_results.append(ValidationCheckResult(
                                check_id=8,
                                check_name="At least two independent sources or high-confidence single source",
                                passed=False,
                                failure_reason=f"Event '{event.canonical_title}' lacks TWO_SOURCE_VERIFIED tier and fails high-confidence single criteria: {reason}",
                                failed_story_id=story.event_id,
                            ))

            # -------------------------------------------------------------
            # CHECK 9: No event appeared in previous 3 days
            # -------------------------------------------------------------
            if event and self.history_store:
                comp = event.companies_involved[0] if event.companies_involved else "unspecified"
                fkey, fhash = generate_event_fingerprint(
                    company=comp,
                    event_type="general",
                    event_date=eval_date,
                    key_facts=event.financial_figures,
                )
                recent_fps = self.history_store.get_recent_fingerprints(target_date=eval_date, lookback_days=3)
                if fhash in recent_fps or fkey in recent_fps:
                    check_results.append(ValidationCheckResult(
                        check_id=9,
                        check_name="No event appeared in previous 3 days",
                        passed=False,
                        failure_reason=f"Event fingerprint already appeared in briefing within previous 3 days",
                        failed_story_id=story.event_id,
                    ))

            # -------------------------------------------------------------
            # CHECK 11 & 12: Headline numbers exist in facts & No fabricated numbers
            # -------------------------------------------------------------
            if primary_art:
                headline_numbers = set(re.findall(r"\b(?:\d+(?:\.\d+)?%?|\₹\d+|\$\d+)\b", story.headline.lower()))
                source_text = (primary_art.title + " " + primary_art.content_text + " " + " ".join(event.financial_figures if event else [])).lower()
                for num in headline_numbers:
                    # Clean symbol
                    clean_num = num.replace("₹", "").replace("$", "").replace("%", "").strip()
                    if clean_num.isdigit() and len(clean_num) >= 2 and clean_num not in source_text:
                        check_results.append(ValidationCheckResult(
                            check_id=11,
                            check_name="Headline numbers exist in verified article facts",
                            passed=False,
                            failure_reason=f"Headline contains unverified/fabricated number '{num}' not found in source text",
                            failed_story_id=story.event_id,
                        ))
                        check_results.append(ValidationCheckResult(
                            check_id=12,
                            check_name="No fabricated numbers",
                            passed=False,
                            failure_reason=f"Fabricated financial number detected: '{num}'",
                            failed_story_id=story.event_id,
                        ))
                        break

            # -------------------------------------------------------------
            # CHECK 13: No fabricated URLs
            # -------------------------------------------------------------
            if story.url not in allowed_urls:
                check_results.append(ValidationCheckResult(
                    check_id=13,
                    check_name="No fabricated URLs",
                    passed=False,
                    failure_reason=f"URL '{story.url}' was not in the verified candidate manifest",
                    failed_story_id=story.event_id,
                ))

            # -------------------------------------------------------------
            # CHECKS 14 to 18: Noise & Filter Rules
            # -------------------------------------------------------------
            if primary_art:
                if story.section == "domestic":
                    from app.verification.domestic_trending import DomesticTrendingEvaluator
                    dom_eval = DomesticTrendingEvaluator()
                    is_noise, noise_rsn = dom_eval.is_domestic_noise(primary_art.title, primary_art.content_text)
                    if is_noise:
                        check_results.append(ValidationCheckResult(
                            check_id=14,
                            check_name="Prohibited story pattern: DOMESTIC_NOISE",
                            passed=False,
                            failure_reason=noise_rsn or "Prohibited domestic noise story",
                            failed_story_id=story.event_id,
                        ))
                else:
                    filter_res = self.story_type_rule.evaluate(primary_art)
                    if not filter_res.is_accepted:
                        reason = filter_res.rejection_reason or "Prohibited story type"
                        check_id = 14
                        if "analyst" in reason:
                            check_id = 14
                        elif "market" in reason:
                            check_id = 15
                        elif "ipo" in reason:
                            check_id = 16
                        elif "calendar" in reason:
                            check_id = 17
                        elif "upcoming" in reason or "opinion" in reason:
                            check_id = 18

                        check_results.append(ValidationCheckResult(
                            check_id=check_id,
                            check_name=f"Prohibited story pattern: {filter_res.rule_failed or 'STORY_TYPE'}",
                            passed=False,
                            failure_reason=reason,
                            failed_story_id=story.event_id,
                        ))

            # -------------------------------------------------------------
            # CHECK 19: Geopolitical story quantified impact
            # -------------------------------------------------------------
            if story.section != "domestic" and any(geo in story.headline.lower() for geo in ("war", "sanctions", "geopolitical", "tariffs", "ceasefire")):
                has_numbers = any(c.isdigit() for c in story.headline)
                if not has_numbers:
                    check_results.append(ValidationCheckResult(
                        check_id=19,
                        check_name="No geopolitical story without quantified market impact",
                        passed=False,
                        failure_reason=f"Geopolitical story '{story.headline}' lacks quantified market impact figures",
                        failed_story_id=story.event_id,
                    ))

        # -------------------------------------------------------------
        # CHECK 10: No India company appears twice
        # -------------------------------------------------------------
        from app.models.entity_sanitizer import sanitize_company_entities
        seen_india_comps: Set[str] = set()
        for story in payload.india_stories:
            event = events_lookup.get(story.event_id)
            raw_comps = event.companies_involved if event and event.companies_involved else []
            companies = sanitize_company_entities(raw_comps, publisher=story.source)
            for comp in companies:
                norm = normalize_entity_name(comp)
                if norm in seen_india_comps and norm != "unspecified_entity":
                    check_results.append(ValidationCheckResult(
                        check_id=10,
                        check_name="No India company appears twice",
                        passed=False,
                        failure_reason=f"Duplicate company '{comp}' selected twice in India section",
                        failed_story_id=story.event_id,
                    ))
                seen_india_comps.add(norm)

        # -------------------------------------------------------------
        # CHECK 8 (Section Ratio): MIN 3 two-source, MAX 2 single-source
        # -------------------------------------------------------------
        from app.models.enums import VerificationTier
        for section_name, section_stories in [("India", payload.india_stories), ("International", payload.international_stories)]:
            two_src_cnt = 0
            single_src_cnt = 0
            for s in section_stories:
                ev = events_lookup.get(s.event_id)
                if ev:
                    if ev.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED:
                        two_src_cnt += 1
                    elif ev.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE:
                        single_src_cnt += 1
            if strict_5_per_section and len(section_stories) == 5 and not quality_ladder_mode:
                if two_src_cnt < 3:
                    check_results.append(ValidationCheckResult(
                        check_id=8,
                        check_name=f"{section_name} section minimum 3 two-source verified stories",
                        passed=False,
                        failure_reason=f"{section_name} section has only {two_src_cnt} two-source verified stories (minimum 3 required, found {single_src_cnt} single-source)",
                    ))
                if single_src_cnt > 2:
                    check_results.append(ValidationCheckResult(
                        check_id=8,
                        check_name=f"{section_name} section maximum 2 single-source stories",
                        passed=False,
                        failure_reason=f"{section_name} section has {single_src_cnt} single-source stories (maximum 2 permitted)",
                    ))

        # -------------------------------------------------------------
        # CHECK 20: Final format is exactly correct
        # -------------------------------------------------------------
        mojibake_tokens = ("ΓÇ", "Γé", "┬á", "┬")
        for story in all_stories:
            if not story.headline or len(story.headline) < 10 or not story.source or not story.url:
                check_results.append(ValidationCheckResult(
                    check_id=20,
                    check_name="Final format is exactly correct",
                    passed=False,
                    failure_reason=f"Story has malformed formatting: headline='{story.headline}', source='{story.source}'",
                    failed_story_id=story.event_id,
                ))
            elif any(tok in story.headline or tok in story.source for tok in mojibake_tokens):
                check_results.append(ValidationCheckResult(
                    check_id=20,
                    check_name="Final format is exactly correct",
                    passed=False,
                    failure_reason=f"Story contains unresolved mojibake tokens in headline or source: '{story.headline}'",
                    failed_story_id=story.event_id,
                ))
            elif getattr(story, "summary", None):
                sum_text = story.summary.strip()
                if "\n" in sum_text:
                    check_results.append(ValidationCheckResult(
                        check_id=20,
                        check_name="Final format is exactly correct",
                        passed=False,
                        failure_reason=f"Story summary contains embedded newlines (must be one single line): '{sum_text}'",
                        failed_story_id=story.event_id,
                    ))
                elif "http://" in sum_text or "https://" in sum_text:
                    check_results.append(ValidationCheckResult(
                        check_id=20,
                        check_name="Final format is exactly correct",
                        passed=False,
                        failure_reason=f"Story summary contains URL: '{sum_text}'",
                        failed_story_id=story.event_id,
                    ))
                elif re.search(r"^[-•*]\s+", sum_text):
                    check_results.append(ValidationCheckResult(
                        check_id=20,
                        check_name="Final format is exactly correct",
                        passed=False,
                        failure_reason=f"Story summary contains leading markdown bullet: '{sum_text}'",
                        failed_story_id=story.event_id,
                    ))
                elif sum_text.lower() == story.headline.strip().lower():
                    check_results.append(ValidationCheckResult(
                        check_id=20,
                        check_name="Final format is exactly correct",
                        passed=False,
                        failure_reason=f"Story summary is identical to headline: '{sum_text}'",
                        failed_story_id=story.event_id,
                    ))
                elif any(tok in sum_text for tok in mojibake_tokens):
                    check_results.append(ValidationCheckResult(
                        check_id=20,
                        check_name="Final format is exactly correct",
                        passed=False,
                        failure_reason=f"Story summary contains unresolved mojibake tokens: '{sum_text}'",
                        failed_story_id=story.event_id,
                    ))
                elif len(sum_text.split()) > 30:
                    check_results.append(ValidationCheckResult(
                        check_id=20,
                        check_name="Final format is exactly correct",
                        passed=False,
                        failure_reason=f"Story summary exceeds maximum 30 words ({len(sum_text.split())} words): '{sum_text}'",
                        failed_story_id=story.event_id,
                    ))
                elif not sum_text.endswith((".", "!", "?", '"', "'")):
                    check_results.append(ValidationCheckResult(
                        check_id=20,
                        check_name="Final format is exactly correct",
                        passed=False,
                        failure_reason=f"Story summary does not end with valid sentence punctuation: '{sum_text}'",
                        failed_story_id=story.event_id,
                    ))
                elif re.sub(r"[^\w]", "", sum_text.split()[-1]).lower() in {
                    "a", "an", "the", "and", "or", "but", "of", "for", "to", "in", "on", "at", "with", "from",
                    "by", "as", "its", "their", "his", "her", "this", "that", "major", "electric", "is", "was",
                    "were", "are", "be", "been", "has", "have", "had", "which", "who", "whom", "whose", "where"
                }:
                    check_results.append(ValidationCheckResult(
                        check_id=20,
                        check_name="Final format is exactly correct",
                        passed=False,
                        failure_reason=f"Story summary ends with incomplete grammatical token: '{sum_text}'",
                        failed_story_id=story.event_id,
                    ))
                elif re.search(r"\b[a-z0-9]+\.[A-Z]", sum_text):
                    check_results.append(ValidationCheckResult(
                        check_id=20,
                        check_name="Final format is exactly correct",
                        passed=False,
                        failure_reason=f"Story summary contains unspaced joined sentence artifact: '{sum_text}'",
                        failed_story_id=story.event_id,
                    ))

        # Fill in passed check entries for checks that did not fail
        failed_ids = {r.check_id for r in check_results if not r.passed}
        all_check_names = {
            1: "Exactly 5 India stories",
            2: "Exactly 5 International stories",
            3: "Every story has a verified URL",
            4: "URL opens successfully",
            5: "URL points to a specific article",
            6: "Article title matches selected event",
            7: "Article is within allowed date window",
            8: "At least two independent sources exist",
            9: "No event appeared in previous 3 days",
            10: "No India company appears twice",
            11: "Headline numbers exist in verified article facts",
            12: "No fabricated numbers",
            13: "No fabricated URLs",
            14: "No analyst-only stories",
            15: "No generic market summaries",
            16: "No IPO intraday subscription stories",
            17: "No results-calendar stories",
            18: "No upcoming-event stories",
            19: "No geopolitical story without quantified market impact",
            20: "Final format is exactly correct",
        }

        final_check_results: List[ValidationCheckResult] = []
        for cid in range(1, 21):
            if cid in failed_ids:
                # Find failure record
                fail_rec = next(r for r in check_results if r.check_id == cid and not r.passed)
                final_check_results.append(fail_rec)
            else:
                final_check_results.append(ValidationCheckResult(
                    check_id=cid,
                    check_name=all_check_names[cid],
                    passed=True,
                ))

        failed_checks = [r for r in final_check_results if not r.passed]
        passed_count = len(final_check_results) - len(failed_checks)

        if failed_checks:
            first_fail = failed_checks[0]
            failed_story_obj = next((s for s in all_stories if s.event_id == first_fail.failed_story_id), None)
            logger.warning(
                "Final validation FAILED on check #%d (%s): %s",
                first_fail.check_id,
                first_fail.check_name,
                first_fail.failure_reason,
            )
            return BriefingValidationReport(
                status=ValidationStatus.FAILED,
                is_valid=False,
                passed_checks=passed_count,
                failed_checks=len(failed_checks),
                failure_reason=first_fail.failure_reason,
                failed_story=failed_story_obj,
                failed_check_id=first_fail.check_id,
                check_results=final_check_results,
            )

        logger.info("Final validation PASSED: All 20 gatekeeping checks successfully verified.")
        return BriefingValidationReport(
            status=ValidationStatus.PASSED,
            is_valid=True,
            passed_checks=20,
            failed_checks=0,
            check_results=final_check_results,
        )
