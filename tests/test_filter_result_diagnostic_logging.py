"""
Regression Test: FilterResult diagnostic logging safety.

Proves that the [DOM_DIAG_TARGET_FILTER] diagnostic log path in
candidate_processing.py cannot raise AttributeError when a FilterResult
object has no ''reason'' attribute (which does not exist on the model).

Bug: AttributeError: ''FilterResult'' object has no attribute ''reason''
Fix: Use getattr(filt_res, "rejection_reason", None) or getattr(filt_res, "rule_failed", None)

FilterResult actual fields (app/filtering/models.py):
  - is_accepted: bool
  - article_url: str
  - article_title: str
  - rule_failed: Optional[str]
  - rejection_reason: Optional[str]
  - matched_patterns: List[str]
"""

import pytest
from app.filtering.models import FilterResult


class TestFilterResultDiagnosticLogging:
    """Proves the diagnostic logging expression is safe for all FilterResult states."""

    def _safe_reason(self, filt_res: FilterResult) -> str:
        """Mirrors the exact expression used in candidate_processing.py L239-242."""
        return (
            getattr(filt_res, "rejection_reason", None)
            or getattr(filt_res, "rule_failed", None)
            or ""
        )

    def test_filterresult_has_no_reason_field(self):
        """FilterResult must NOT have a ''reason'' field -- confirms the original bug was real."""
        filt_res = FilterResult(
            is_accepted=True,
            article_url="https://economictimes.com/some-article",
        )
        assert not hasattr(filt_res, "reason")

    def test_accepted_result_returns_empty_string(self):
        """A passing FilterResult with no rejection fields yields empty string, not crash."""
        filt_res = FilterResult(
            is_accepted=True,
            article_url="https://economictimes.com/some-article",
        )
        reason = self._safe_reason(filt_res)
        assert reason == ""

    def test_rejected_with_rule_failed_only(self):
        """FilterResult with rule_failed set returns the rule name."""
        filt_res = FilterResult(
            is_accepted=False,
            article_url="https://example.com/article",
            rule_failed="DATE",
        )
        assert self._safe_reason(filt_res) == "DATE"

    def test_rejected_with_rejection_reason_only(self):
        """FilterResult with rejection_reason set returns the human-readable reason."""
        filt_res = FilterResult(
            is_accepted=False,
            article_url="https://example.com/article",
            rejection_reason="Article is older than 24 hours",
        )
        assert self._safe_reason(filt_res) == "Article is older than 24 hours"

    def test_rejected_with_both_fields_prefers_rejection_reason(self):
        """When both fields are set, rejection_reason takes precedence."""
        filt_res = FilterResult(
            is_accepted=False,
            article_url="https://example.com/article",
            rule_failed="SOURCE",
            rejection_reason="Blocked: tabloid source not in whitelist",
        )
        assert self._safe_reason(filt_res) == "Blocked: tabloid source not in whitelist"

    def test_no_reason_fields_set_returns_empty_string(self):
        """FilterResult with neither reason field set yields empty string."""
        filt_res = FilterResult(
            is_accepted=False,
            article_url="https://example.com/article",
        )
        assert filt_res.rule_failed is None
        assert filt_res.rejection_reason is None
        assert self._safe_reason(filt_res) == ""

    def test_direct_reason_attr_access_raises(self):
        """Confirms the original bug: directly accessing .reason raises AttributeError."""
        filt_res = FilterResult(
            is_accepted=True,
            article_url="https://example.com/article",
        )
        with pytest.raises(AttributeError):
            _ = filt_res.reason  # type: ignore[attr-defined]

    def test_diagnostic_log_format_does_not_crash(self):
        """Full f-string as used in candidate_processing.py does not crash for all states."""
        cases = [
            (True, None, None),
            (False, "DATE", None),
            (False, None, "Article too old"),
            (False, "SOURCE", "Blocked publisher"),
            (False, None, None),
        ]
        for is_accepted, rule_failed, rejection_reason in cases:
            filt_res = FilterResult(
                is_accepted=is_accepted,
                article_url="https://example.com/x",
                rule_failed=rule_failed,
                rejection_reason=rejection_reason,
            )
            _filt_reason = (
                getattr(filt_res, "rejection_reason", None)
                or getattr(filt_res, "rule_failed", None)
                or ""
            )
            log_line = (
                f'[DOM_DIAG_TARGET_FILTER] title="Test Article" '
                f'passed={filt_res.is_accepted} engine="domestic_filter_engine" '
                f'reason="{_filt_reason}"'
            )
            assert "[DOM_DIAG_TARGET_FILTER]" in log_line
            assert f"passed={is_accepted}" in log_line
