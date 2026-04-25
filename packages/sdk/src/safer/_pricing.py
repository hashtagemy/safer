"""Shared pricing table — single source of truth for all SAFER adapters.

Adapter modules (claude_sdk, openai_client, langchain, strands, google_adk)
import `estimate_cost` from this module instead of keeping their own pricing
tables.  Backend OTLP parser uses the same module so OTel-bridge events have
real cost numbers (no more hardcoded $0).

Prices are USD per 1M tokens, sourced from public pricing pages and verified
April 2026.  When a model lookup misses, `estimate_cost` returns `None` so the
caller can decide whether to log unknown-model warnings or store `cost=0`.
We do NOT silently fall back to Opus pricing — that produced 5x – 100x cost
errors on Sonnet/Haiku/Mini models in earlier adapter versions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Pricing:
    """USD per 1M tokens for one model."""

    p_input: float
    p_cached_input: float
    p_output: float
    # 5-minute ephemeral cache write tier (Anthropic only).  None means the
    # provider does not bill cache writes separately.
    p_cache_write_5m: float | None = None


# ---------- Anthropic Claude ------------------------------------------------
# Anthropic prices: https://www.anthropic.com/pricing#api
# Cache writes are 1.25x the input rate for the default 5-minute TTL; cache
# reads are 0.10x the input rate.  >200k-token inputs on Sonnet are billed at
# 2x the standard tier — handled via `estimate_cost`'s `tier_long_context`.

_ANTHROPIC: dict[str, Pricing] = {
    "claude-opus-4-7":   Pricing(15.0, 1.50, 75.0, 18.75),
    "claude-opus-4-6":   Pricing(15.0, 1.50, 75.0, 18.75),
    "claude-opus-4-5":   Pricing(15.0, 1.50, 75.0, 18.75),
    "claude-opus-4-1":   Pricing(15.0, 1.50, 75.0, 18.75),
    "claude-opus-4-0":   Pricing(15.0, 1.50, 75.0, 18.75),
    "claude-sonnet-4-6": Pricing(3.0,  0.30, 15.0, 3.75),
    "claude-sonnet-4-5": Pricing(3.0,  0.30, 15.0, 3.75),
    "claude-sonnet-4-0": Pricing(3.0,  0.30, 15.0, 3.75),
    "claude-haiku-4-5":  Pricing(1.0,  0.10, 5.0,  1.25),
    "claude-3-5-haiku":  Pricing(0.80, 0.08, 4.0,  1.0),
    "claude-3-haiku":    Pricing(0.25, 0.025, 1.25, 0.30),
}

# Sonnet >200k input tier (Anthropic 2x premium)
_ANTHROPIC_LONG_CONTEXT_THRESHOLD = 200_000
_ANTHROPIC_LONG_CONTEXT_MULTIPLIER = 2.0


# ---------- OpenAI ----------------------------------------------------------
# OpenAI prices: https://platform.openai.com/docs/pricing
# Verified April 2026.  Cache reads cost ~50% of standard input.

_OPENAI: dict[str, Pricing] = {
    # GPT-5 family (Aug 2025 release)
    "gpt-5":           Pricing(1.25, 0.125, 10.0),
    "gpt-5-mini":      Pricing(0.25, 0.025, 2.0),
    "gpt-5-nano":      Pricing(0.05, 0.005, 0.40),
    # 4.1 family
    "gpt-4.1":         Pricing(2.0,  0.50,  8.0),
    "gpt-4.1-mini":    Pricing(0.40, 0.10,  1.60),
    "gpt-4.1-nano":    Pricing(0.10, 0.025, 0.40),
    # 4o family
    "gpt-4o":          Pricing(2.50, 1.25,  10.0),
    "gpt-4o-mini":     Pricing(0.15, 0.075, 0.60),
    # legacy
    "gpt-4-turbo":     Pricing(10.0, 10.0,  30.0),  # no cache discount
    "gpt-4":           Pricing(30.0, 30.0,  60.0),
    "gpt-3.5-turbo":   Pricing(0.50, 0.50,  1.50),
    # o-series (reasoning)
    "o1":              Pricing(15.0, 7.50,  60.0),
    "o1-preview":      Pricing(15.0, 7.50,  60.0),
    "o1-mini":         Pricing(1.10, 0.55,  4.40),  # reduced Oct 2024 from 3/12
    "o3":              Pricing(2.0,  0.50,  8.0),
    "o3-mini":         Pricing(1.10, 0.55,  4.40),
    "o4-mini":         Pricing(1.10, 0.275, 4.40),
}


# ---------- Google Gemini ---------------------------------------------------
# Gemini prices: https://ai.google.dev/pricing

_GEMINI: dict[str, Pricing] = {
    "gemini-2.5-pro":   Pricing(1.25, 0.3125, 10.0),
    "gemini-2.5-flash": Pricing(0.30, 0.075,  2.50),
    "gemini-2.0-flash": Pricing(0.10, 0.025,  0.40),
    "gemini-1.5-pro":   Pricing(1.25, 0.3125, 5.0),
    "gemini-1.5-flash": Pricing(0.075, 0.01875, 0.30),
}


# ---------- AWS Bedrock model-id aliases -----------------------------------
# Bedrock exposes Claude models under `anthropic.<name>-v1:0` IDs.  Map to
# the canonical Anthropic pricing — same model, same cost.

_BEDROCK_CLAUDE_ALIASES: dict[str, str] = {
    "anthropic.claude-opus-4-7-v1:0":   "claude-opus-4-7",
    "anthropic.claude-opus-4-5-v1:0":   "claude-opus-4-5",
    "anthropic.claude-sonnet-4-6-v1:0": "claude-sonnet-4-6",
    "anthropic.claude-sonnet-4-5-v1:0": "claude-sonnet-4-5",
    "anthropic.claude-haiku-4-5-v1:0":  "claude-haiku-4-5",
}


# Combined table — _ANTHROPIC + _OPENAI + _GEMINI form the lookup.
_ALL: dict[str, Pricing] = {**_ANTHROPIC, **_OPENAI, **_GEMINI}


def match_model(name: str) -> Pricing | None:
    """Resolve a free-form model identifier to a `Pricing` entry.

    Tries exact match, then strips date suffixes (e.g.
    `claude-opus-4-7-20251101` → `claude-opus-4-7`), then prefix match against
    every known model id.  Bedrock aliases are resolved before the lookup.
    Returns `None` if no entry matches — caller decides what to do (typically
    log a warning and store `cost=0`).
    """
    if not name:
        return None
    name = name.lower().strip()

    # Bedrock alias
    if name in _BEDROCK_CLAUDE_ALIASES:
        name = _BEDROCK_CLAUDE_ALIASES[name]

    # Exact
    if name in _ALL:
        return _ALL[name]

    # Strip date suffix (Anthropic uses YYYYMMDD; OpenAI uses YYYY-MM-DD)
    parts = name.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 8:
        if parts[0] in _ALL:
            return _ALL[parts[0]]

    # Prefix match — longest first so claude-opus-4-7 wins over claude-opus
    for known in sorted(_ALL.keys(), key=len, reverse=True):
        if name.startswith(known):
            return _ALL[known]

    return None


def estimate_cost(
    model: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
) -> float | None:
    """Estimate USD cost for one LLM call.  Returns `None` for unknown models.

    `tokens_in` is the TOTAL input token count; cached + cache-write tokens
    are subtracted from the billable input portion since they are billed at
    different rates.  `cache_write` only applies to providers that bill cache
    writes separately (Anthropic 5-minute ephemeral); for OpenAI / Gemini we
    treat `cache_write` as standard input.

    Anthropic's >200k-token long-context tier (2x premium) applies if the
    raw `tokens_in` exceeds 200,000.
    """
    pricing = match_model(model)
    if pricing is None:
        return None

    # Anthropic long-context premium
    multiplier = 1.0
    if model.lower().startswith("claude-sonnet") and tokens_in > _ANTHROPIC_LONG_CONTEXT_THRESHOLD:
        multiplier = _ANTHROPIC_LONG_CONTEXT_MULTIPLIER

    # Billable input = total - cached - cache_write_part
    billable_in = max(0, tokens_in - cache_read - cache_write)

    cost = (
        billable_in * pricing.p_input
        + cache_read * pricing.p_cached_input
        + (
            cache_write * pricing.p_cache_write_5m
            if pricing.p_cache_write_5m is not None
            else cache_write * pricing.p_input  # treat as standard input
        )
        + tokens_out * pricing.p_output
    )
    return (cost * multiplier) / 1_000_000


__all__ = [
    "Pricing",
    "match_model",
    "estimate_cost",
]
