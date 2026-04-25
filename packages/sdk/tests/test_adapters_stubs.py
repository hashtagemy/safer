"""Adapter back-compat tests — verify the old `wrap_bedrock(...)` and
`wrap_crew(...)` entry points still work after both adapters graduated
from stubs to full implementations.

The original module-level test pinned the stub contract (no-op +
warning); now that Bedrock and CrewAI are real, these tests pin the
new contract so old code that does
`client = wrap_bedrock(boto3_client, agent_id="x")` keeps working."""

from __future__ import annotations


def test_bedrock_wrap_returns_a_proxy_with_session_id():
    from safer.adapters.bedrock import wrap_bedrock

    class _Inner:
        meta = {"region": "us-east-1"}

    proxied = wrap_bedrock(_Inner(), agent_id="bedrock_compat", agent_name="X")
    # Proxy exposes a session_id (proves emitter wiring is alive).
    assert proxied.session_id.startswith("sess_")
    # And forwards unknown attributes through to the wrapped client.
    assert proxied.meta == {"region": "us-east-1"}


def test_crewai_wrap_crew_returns_crew_unchanged_after_listener():
    """`wrap_crew(crew, agent_id=...)` is a back-compat shim now that
    the real integration is event-bus based. It must still return the
    crew object unchanged so legacy code keeps running."""
    import pytest

    pytest.importorskip("crewai")
    from crewai.events import crewai_event_bus

    from safer.adapters.crewai import wrap_crew

    class _Crew:
        name = "demo"
        fingerprint = None

    inner = _Crew()
    with crewai_event_bus.scoped_handlers():
        out = wrap_crew(inner, agent_id="crewai_compat", agent_name="X")
    assert out is inner
