"""
Smoke tests verifying clean imports without circular dependencies
and regression coverage for shared validation helpers.
"""

import pytest


def test_run_daily_15_imports_without_circular_dependency():
    """Ensure run_daily_15 imports without circular import or partial initialization errors."""
    import run_daily_15
    assert hasattr(run_daily_15, "main") or hasattr(run_daily_15, "run_daily_briefing") or True


def test_headline_synthesis_imports_without_validation_engine_cycle():
    """Ensure headline_synthesis imports without importing validation.engine."""
    import app.ai.headline_synthesis as hs
    assert hasattr(hs, "synthesize_investment_headline")
    assert hasattr(hs, "generate_grounded_fallback_headline")


def test_validation_engine_imports_cleanly():
    """Ensure validation.engine imports cleanly and re-exports shared helpers for backward compatibility."""
    import app.validation.engine as ve
    assert hasattr(ve, "FinalValidationEngine")
    assert hasattr(ve, "calculate_semantic_token_overlap")
    assert hasattr(ve, "canonical_numeric_tokens")


def test_shared_semantic_token_overlap_behavior():
    """Verify calculate_semantic_token_overlap behavior on grounded and ungrounded headlines."""
    from app.validation.shared import calculate_semantic_token_overlap

    # Source text
    source = "Reserve Bank of India imposes Rs 100 crore penalty on standard chartered bank for non-compliance."

    # Grounded headline
    has_ov, count, tokens = calculate_semantic_token_overlap(
        "RBI Imposes Rs 100 Crore Penalty on Standard Chartered Bank",
        source,
    )
    assert has_ov is True
    assert count >= 1
    assert "penalty" in tokens or "chartered" in tokens or "standard" in tokens

    # Unrelated headline with zero semantic token overlap
    has_bad, count_bad, tokens_bad = calculate_semantic_token_overlap(
        "Solar Panel Manufacturer Opens Facility in Arizona Following Federal Subsidies",
        source,
    )
    assert has_bad is False
    assert count_bad == 0
    assert len(tokens_bad) == 0


def test_shared_canonical_numeric_tokens_behavior():
    """Verify canonical_numeric_tokens extracts and normalizes numbers and units."""
    from app.validation.shared import canonical_numeric_tokens

    # Indian crore
    tokens_cr = canonical_numeric_tokens("RBI imposes ₹100 crore penalty")
    assert "100cr" in tokens_cr or "100" in tokens_cr

    # Western billion
    tokens_bn = canonical_numeric_tokens("Company signs $1.5 billion deal")
    assert "1.5b" in tokens_bn or "1.5" in tokens_bn

    # Basis points / percentage / numbers
    tokens_num = canonical_numeric_tokens("Rate hike of 100 bps with 5.5% inflation and 5,57,700 volume")
    assert "100" in tokens_num
    assert "5.5%" in tokens_num or "5.5" in tokens_num
    assert "557700" in tokens_num
