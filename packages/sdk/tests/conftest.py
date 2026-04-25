"""Test infrastructure shared across `test_user_*.py` files.

The `captured_events` fixture installs an in-memory event recorder on
SAFER's transport so a user-pattern test can verify what SAFER captured
without polluting the user-facing test body with internal plumbing
(no `safer.instrument(...)`, no manual `transport.emit` patches in
the test).

A test's body should look exactly like what a user would copy-paste
from the README — adapter import + adapter line + the framework's own
agent code — and then assert on `captured_events` at the end."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def captured_events(monkeypatch) -> list[dict[str, Any]]:
    """Record every event SAFER emits during the test.

    Works by monkey-patching `safer.transport.Transport.emit` so that
    whenever the SAFER runtime is started — by `safer.instrument(...)`,
    by an adapter's `ensure_runtime(...)` bootstrap, or by anything
    else — its transport just appends to an in-memory list instead of
    POSTing to a backend.

    Tests using this fixture should never call `safer.instrument(...)`
    or touch `client.transport` themselves; the fixture keeps the test
    body limited to the public README integration line.
    """
    events: list[dict[str, Any]] = []

    from safer import client as client_mod
    from safer.transport import AsyncTransport

    # Drop any leftover client from a prior test so this run starts clean
    # (an adapter constructor will rebuild it).
    client_mod._client = None

    def _recording_emit(self: AsyncTransport, event: dict[str, Any]) -> None:
        events.append(event)

    monkeypatch.setattr(AsyncTransport, "emit", _recording_emit)
    yield events
    client_mod._client = None
