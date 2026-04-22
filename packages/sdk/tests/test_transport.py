"""Transport batching + backpressure tests.

These don't need a live backend — we set api_url to an unreachable host
and verify that the queue logic works without blocking the host.
"""

from __future__ import annotations

import asyncio

import pytest

from safer.config import SaferConfig
from safer.transport import AsyncTransport


@pytest.mark.asyncio
async def test_emit_is_nonblocking():
    cfg = SaferConfig(api_url="http://127.0.0.1:59999", batch_size=10, batch_interval_ms=50)
    t = AsyncTransport(cfg)
    await t.start()
    try:
        for i in range(100):
            t.emit({"hook": "before_llm_call", "session_id": "s", "sequence": i, "agent_id": "a"})
        assert t.dropped_count == 0
    finally:
        await t.stop()


@pytest.mark.asyncio
async def test_backpressure_drops_excess():
    cfg = SaferConfig(
        api_url="http://127.0.0.1:59999",
        batch_size=10,
        batch_interval_ms=5000,  # slow flush, let queue grow
        max_buffer=20,
    )
    t = AsyncTransport(cfg)
    # Don't start worker — queue will not drain.
    for i in range(50):
        t.emit({"hook": "before_llm_call", "session_id": "s", "sequence": i, "agent_id": "a"})
    # First 20 should queue, remaining 30 should drop.
    assert t.dropped_count >= 30


@pytest.mark.asyncio
async def test_masking_applied_on_emit():
    cfg = SaferConfig(api_url="http://127.0.0.1:59999", batch_size=10, batch_interval_ms=50)
    t = AsyncTransport(cfg)
    await t.start()
    try:
        t.emit(
            {
                "hook": "before_llm_call",
                "session_id": "s",
                "sequence": 0,
                "agent_id": "a",
                "prompt": "key: sk-ant-abcdefghijklmnopqrstuvwxyz1234567890123456",
            }
        )
        # Peek into queue (internal, testing-only).
        # The queued event should have the key masked.
        evt = await asyncio.wait_for(t._queue.get(), timeout=1.0)
        assert "sk-ant-" not in evt["prompt"]
        assert "<REDACTED" in evt["prompt"]
    finally:
        await t.stop()


@pytest.mark.asyncio
async def test_graceful_shutdown_flushes_pending():
    cfg = SaferConfig(
        api_url="http://127.0.0.1:59999",
        batch_size=100,
        batch_interval_ms=10_000,  # won't flush on time
        shutdown_timeout_s=0.5,
    )
    t = AsyncTransport(cfg)
    await t.start()
    for i in range(5):
        t.emit({"hook": "before_llm_call", "session_id": "s", "sequence": i, "agent_id": "a"})
    # Stop should flush quickly without raising.
    await t.stop()
