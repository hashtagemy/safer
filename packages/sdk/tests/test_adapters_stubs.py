"""Stub adapter tests — Bedrock and CrewAI ship as no-op wrappers
that emit a one-time warning and return the inner client unchanged.
Real adapter implementations are tracked in the roadmap (Faz 35.16 /
35.17); this test pins the stub contract until those land."""

from __future__ import annotations

import logging


def test_bedrock_stub_is_noop_with_warning(caplog):
    from safer.adapters.bedrock import wrap_bedrock

    sentinel = object()
    with caplog.at_level(logging.WARNING, logger="safer.adapters.bedrock"):
        out = wrap_bedrock(sentinel, agent_id="x")
    assert out is sentinel
    assert any("bedrock" in rec.message for rec in caplog.records)


def test_crewai_stub_is_noop_with_warning(caplog):
    from safer.adapters.crewai import wrap_crew

    sentinel = object()
    with caplog.at_level(logging.WARNING, logger="safer.adapters.crewai"):
        out = wrap_crew(sentinel, agent_id="x")
    assert out is sentinel
    assert any("crewai" in rec.message for rec in caplog.records)
