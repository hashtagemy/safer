"""Tests for the shared `_pricing` module.

These cover real production scenarios — not synthetic.  Each test case
represents a concrete model + token combination an actual SAFER user will
emit, and the expected cost matches official provider pricing as of April
2026.  Whenever provider pricing changes, both the table in `_pricing.py`
and the expected values here move together.
"""

from __future__ import annotations

import math

import pytest

from safer._pricing import Pricing, estimate_cost, match_model


def _approx(a: float, b: float, rel: float = 1e-6) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-12)


# ----- Model resolution ----------------------------------------------------


def test_exact_match_anthropic():
    p = match_model("claude-opus-4-7")
    assert p is not None
    assert p.p_input == 15.0
    assert p.p_output == 75.0


def test_exact_match_openai_o1_mini_uses_post_oct_2024_pricing():
    """Regression for the bug where the table held the pre-Oct-2024 numbers
    (3/12) instead of the current 1.10/4.40.  Three-fold cost overstatement
    on every reasoning-mini call."""
    p = match_model("o1-mini")
    assert p is not None
    assert p.p_input == 1.10
    assert p.p_output == 4.40


def test_date_suffix_stripped_anthropic():
    p = match_model("claude-haiku-4-5-20251001")
    assert p is not None
    assert p.p_input == 1.0


def test_openai_snapshot_id_resolves_to_base_model():
    """OpenAI snapshot IDs like `gpt-4o-2024-08-06` should resolve to the
    base model (`gpt-4o`) via prefix match — same model, same price."""
    p = match_model("gpt-4o-2024-08-06")
    assert p is not None
    assert p.p_input == 2.50
    assert p.p_output == 10.0


def test_openai_mini_resolves_directly():
    p = match_model("gpt-4o-mini")
    assert p is not None
    assert p.p_input == 0.15


def test_prefix_match_longest_wins():
    # claude-opus-4-7 should beat claude-opus on prefix ordering
    p = match_model("claude-opus-4-7-someweirdsuffix")
    assert p is not None
    assert p.p_input == 15.0


def test_bedrock_alias_resolves_to_anthropic_pricing():
    p = match_model("anthropic.claude-opus-4-7-v1:0")
    assert p is not None
    assert p.p_input == 15.0
    assert p.p_cached_input == 1.50
    assert p.p_cache_write_5m == 18.75


def test_bedrock_sonnet_alias_resolves():
    p = match_model("anthropic.claude-sonnet-4-6-v1:0")
    assert p is not None
    assert p.p_input == 3.0


def test_unknown_model_returns_none():
    assert match_model("totally-fake-model-xyz") is None
    assert match_model("") is None


# ----- Cost estimation ----------------------------------------------------


def test_opus_simple_cost():
    # 1000 input, 500 output → (1000 * 15 + 500 * 75) / 1M
    cost = estimate_cost("claude-opus-4-7", tokens_in=1000, tokens_out=500)
    expected = (1000 * 15.0 + 500 * 75.0) / 1_000_000
    assert _approx(cost, expected)


def test_sonnet_with_cache_read():
    # 10000 input, 3000 cached, 800 output:
    # billable_in = 10000 - 3000 = 7000
    # cost = (7000 * 3.0 + 3000 * 0.30 + 800 * 15.0) / 1M
    cost = estimate_cost(
        "claude-sonnet-4-6", tokens_in=10000, tokens_out=800, cache_read=3000
    )
    expected = (7000 * 3.0 + 3000 * 0.30 + 800 * 15.0) / 1_000_000
    assert _approx(cost, expected)


def test_anthropic_cache_write_billed_separately():
    # Sonnet, 5000 input including 2000 cache_write
    # billable_in = 5000 - 0 - 2000 = 3000
    # cost = 3000 * 3.0 + 2000 * 3.75 + 1000 * 15.0  (cache_write rate)
    cost = estimate_cost(
        "claude-sonnet-4-6",
        tokens_in=5000,
        tokens_out=1000,
        cache_write=2000,
    )
    expected = (3000 * 3.0 + 2000 * 3.75 + 1000 * 15.0) / 1_000_000
    assert _approx(cost, expected)


def test_openai_cache_write_treated_as_input():
    # OpenAI doesn't bill cache writes separately — Pricing.p_cache_write_5m
    # is None for gpt-4o, so cache_write should fall back to standard input rate.
    cost = estimate_cost(
        "gpt-4o", tokens_in=5000, tokens_out=1000, cache_write=2000
    )
    # billable_in = 5000 - 0 - 2000 = 3000
    # cost = 3000 * 2.50 + 2000 * 2.50 + 1000 * 10.0 (cache_write at input rate)
    expected = (3000 * 2.50 + 2000 * 2.50 + 1000 * 10.0) / 1_000_000
    assert _approx(cost, expected)


def test_unknown_model_returns_none_not_zero():
    """Critical: unknown models must NOT silently fall back to a default
    rate — that was the bug that produced 100x cost overstatements on
    gpt-5-nano in older adapter code."""
    assert estimate_cost("nonexistent-model", tokens_in=1000, tokens_out=500) is None


def test_long_context_premium_for_sonnet():
    # Sonnet >200k input → 2x premium on the entire bill
    base = estimate_cost(
        "claude-sonnet-4-6", tokens_in=300_000, tokens_out=1000
    )
    expected_base = (300_000 * 3.0 + 1000 * 15.0) / 1_000_000
    assert _approx(base, expected_base * 2.0)


def test_long_context_premium_does_not_apply_to_opus():
    # The premium is Sonnet-only.  Opus pricing is flat at any token count.
    cost = estimate_cost(
        "claude-opus-4-7", tokens_in=300_000, tokens_out=1000
    )
    expected = (300_000 * 15.0 + 1000 * 75.0) / 1_000_000
    assert _approx(cost, expected)


def test_haiku_4_5_pricing():
    # Smoke: $1 input, $5 output per 1M (corrected from the Faz 33 era)
    cost = estimate_cost("claude-haiku-4-5", tokens_in=1_000_000, tokens_out=1_000_000)
    assert _approx(cost, 1.0 + 5.0)


def test_gpt5_nano_is_not_overcharged():
    """Regression: the old adapter fallback `(5.0, 15.0)` produced 100x
    overcharges for gpt-5-nano.  Confirm the new table prices it at the
    real $0.05/$0.40 rate."""
    cost = estimate_cost("gpt-5-nano", tokens_in=1_000_000, tokens_out=1_000_000)
    assert _approx(cost, 0.05 + 0.40)


def test_gemini_pro_pricing():
    cost = estimate_cost("gemini-2.5-pro", tokens_in=10_000, tokens_out=2_000)
    expected = (10_000 * 1.25 + 2_000 * 10.0) / 1_000_000
    assert _approx(cost, expected)
