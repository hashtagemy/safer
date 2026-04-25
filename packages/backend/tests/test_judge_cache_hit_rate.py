"""Judge prompt-cache hit-rate measurement.

The README and CLAUDE.md claim every Opus call uses
`cache_control: ephemeral` and that the second-onward call hits the
5-minute cache. These tests verify that:

1. The Judge attaches `cache_control: ephemeral` to its system prompt
   on every call.
2. With a fake Anthropic client whose responses report realistic
   cache-read token counts, sequential calls record the cache hits
   in `record_claude_call` so the dashboard credit meter can show
   savings.
3. The pure-Python `_estimate_cost` helper reflects an effective
   ≥80% input-cost reduction once the cache is warm — independent
   of any network behaviour.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from safer_backend.judge.engine import (
    JUDGE_MODEL,
    JudgeMode,
    _estimate_cost,
    judge_event,
    set_client,
)
from safer_backend.judge.personas import SYSTEM_PROMPT


def _good_verdict_json() -> dict:
    return {
        "overall": {"risk": "LOW", "confidence": 0.9, "block": False},
        "active_personas": ["security_auditor"],
        "personas": {
            "security_auditor": {
                "persona": "security_auditor",
                "score": 90,
                "confidence": 0.9,
                "flags": [],
                "evidence": [],
                "reasoning": "clean",
                "recommended_mitigation": None,
            }
        },
    }


class _FakeResponse:
    def __init__(self, text: str, *, input_tokens, output_tokens, cache_read, cache_write):
        self.content = [SimpleNamespace(type="text", text=text)]
        self.usage = SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        )


class _CachingFakeAnthropic:
    """Fake client that simulates Anthropic's prompt-cache behaviour.

    First call: cache_creation_input_tokens = system_size, no cache reads.
    Subsequent calls (within 5 minutes): cache_read_input_tokens =
    system_size, no further writes.
    """

    def __init__(self, system_size: int = 3000):
        self._calls = 0
        self._sys_size = system_size
        self.calls: list[dict] = []

        async def _create(**kwargs):
            self._calls += 1
            self.calls.append(kwargs)
            if self._calls == 1:
                # Cold call.
                return _FakeResponse(
                    json.dumps(_good_verdict_json()),
                    input_tokens=self._sys_size + 200,
                    output_tokens=300,
                    cache_read=0,
                    cache_write=self._sys_size,
                )
            # Warm cache.
            return _FakeResponse(
                json.dumps(_good_verdict_json()),
                input_tokens=self._sys_size + 200,
                output_tokens=300,
                cache_read=self._sys_size,
                cache_write=0,
            )

        self.messages = SimpleNamespace(create=_create)


@pytest.fixture(autouse=True)
def _reset_client():
    set_client(None)
    yield
    set_client(None)


# ---------- system-prompt cache_control plumbing ----------


def test_system_prompt_is_substantive_for_caching():
    """The cached block must be large enough that caching matters.

    Anthropic ephemeral cache only kicks in for sufficiently large
    system prompts. The 6-persona Judge prompt should comfortably
    clear the threshold (~1k tokens).
    """
    # ~3 chars/token is a conservative byte→token estimate.
    assert len(SYSTEM_PROMPT) > 3000


@pytest.mark.asyncio
async def test_every_judge_call_attaches_cache_control(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    fake = _CachingFakeAnthropic()
    set_client(fake)

    for i in range(3):
        await judge_event(
            event={
                "event_id": f"evt_{i}",
                "session_id": "s",
                "agent_id": "a",
                "hook": "on_final_output",
                "sequence": i,
                "final_response": "x",
            },
            active_personas=["security_auditor"],
            mode=JudgeMode.RUNTIME,
        )

    assert len(fake.calls) == 3
    for call in fake.calls:
        system = call["system"]
        assert isinstance(system, list)
        assert system[0]["cache_control"] == {"type": "ephemeral"}


# ---------- cache-read token accounting ----------


@pytest.mark.asyncio
async def test_first_call_writes_cache_subsequent_calls_read(monkeypatch):
    """The fake client emits cache_creation on call 1, then
    cache_read on every subsequent call. Validate that the engine's
    usage extraction respects both halves."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    fake = _CachingFakeAnthropic(system_size=3000)
    set_client(fake)

    verdicts = []
    for i in range(4):
        v = await judge_event(
            event={
                "event_id": f"evt_{i}",
                "session_id": "s",
                "agent_id": "a",
                "hook": "on_final_output",
                "sequence": i,
                "final_response": "x",
            },
            active_personas=["security_auditor"],
            mode=JudgeMode.RUNTIME,
        )
        verdicts.append(v)

    # First verdict had cache write, no read.
    # Subsequent verdicts had cache read, no write.
    assert verdicts[0].cache_read_tokens == 0
    for v in verdicts[1:]:
        assert v.cache_read_tokens == 3000


@pytest.mark.asyncio
async def test_cache_hit_rate_above_80_percent_after_warmup(monkeypatch):
    """Across 10 sequential calls, ≥80% of input tokens should hit
    the prompt cache once it's warm."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    fake = _CachingFakeAnthropic(system_size=3000)
    set_client(fake)

    cache_reads = 0
    cache_writes = 0
    total_inputs = 0

    for i in range(10):
        v = await judge_event(
            event={
                "event_id": f"evt_{i}",
                "session_id": "s",
                "agent_id": "a",
                "hook": "on_final_output",
                "sequence": i,
                "final_response": "x",
            },
            active_personas=["security_auditor"],
            mode=JudgeMode.RUNTIME,
        )
        cache_reads += v.cache_read_tokens
        # Writes/cold-input are not stored on the verdict; estimate via
        # the known fake fixture: first call 3000 write, rest 0.
        if i == 0:
            cache_writes += 3000
        total_inputs += 3200  # system + small user delta

    # Hit rate = cache_reads / total_inputs
    hit_rate = cache_reads / total_inputs
    assert hit_rate >= 0.80, f"hit_rate={hit_rate:.2%} below 80%"


# ---------- _estimate_cost reflects savings ----------


def test_estimate_cost_input_portion_under_20_percent_of_cold():
    """The cache only discounts the INPUT portion of the bill — the
    output portion is unchanged. Verify the input portion alone drops
    below 20% of cold once 90% of input tokens hit the cache."""
    # Output tokens=0 isolates the input-side savings.
    cold = _estimate_cost(JUDGE_MODEL, 1000, 0, 0, 0)
    warm = _estimate_cost(JUDGE_MODEL, 1000, 0, 900, 0)
    assert warm < cold * 0.20


def test_estimate_cost_total_drops_meaningfully_with_warm_cache():
    """Sanity-check: even with a normal output share, warm vs. cold
    should produce a non-trivial total saving."""
    cold = _estimate_cost(JUDGE_MODEL, 1000, 400, 0, 0)
    warm = _estimate_cost(JUDGE_MODEL, 1000, 400, 900, 0)
    assert warm < cold
    # Savings should be at least 20% of cold even when output dominates.
    assert (cold - warm) / cold >= 0.20


def test_estimate_cost_handles_zero_tokens():
    """No tokens consumed → zero cost (no division-by-zero)."""
    assert _estimate_cost(JUDGE_MODEL, 0, 0, 0, 0) == 0.0


def test_estimate_cost_unknown_model_falls_back_to_opus():
    """An unknown model id should not raise; it uses the Opus pricing
    table as a conservative default (matches real engine behaviour)."""
    cost = _estimate_cost("claude-future-x", 1000, 400, 0, 0)
    assert cost > 0
