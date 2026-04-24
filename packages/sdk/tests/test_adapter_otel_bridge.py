"""Tests for `safer.adapters.otel.configure_otel_bridge`.

The bridge wires OpenTelemetry's TracerProvider + OTLPSpanExporter to
SAFER's `/v1/traces` endpoint. We verify:
  * core OTel deps (`opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`) are resolved;
  * the bridge installs a TracerProvider with a BatchSpanProcessor
    pointing at the right endpoint;
  * calling configure twice is idempotent (no double provider);
  * instrumentor auto-detection reports correctly when no instrumentor
    package is installed;
  * explicit `instrument=["anthropic"]` on a system without the
    instrumentor is handled gracefully (log + skip, no raise).
"""

from __future__ import annotations

import pytest

from safer import client as client_mod
from safer.instrument import _reset_registered_agents_for_tests

pytest.importorskip("opentelemetry.sdk")
pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")


@pytest.fixture(autouse=True)
def _reset_runtime_and_bridge(monkeypatch):
    from safer.adapters import otel as otel_mod

    client_mod._client = None
    _reset_registered_agents_for_tests()
    otel_mod._reset_for_tests()
    monkeypatch.setenv("SAFER_API_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("SAFER_TRANSPORT_MODE", "http")
    # Ensure endpoint env doesn't leak across tests
    monkeypatch.delenv("SAFER_OTEL_ENDPOINT", raising=False)
    yield
    client_mod._client = None
    _reset_registered_agents_for_tests()
    otel_mod._reset_for_tests()


def test_bridge_installs_tracer_provider_with_batch_exporter():
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    from safer.adapters.otel import configure_otel_bridge

    configure_otel_bridge(agent_id="otel_x", agent_name="OTel X")
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    processors = provider._active_span_processor._span_processors  # type: ignore[attr-defined]
    assert len(processors) >= 1


def test_bridge_endpoint_defaults_to_safer_api_url(monkeypatch):
    monkeypatch.setenv("SAFER_API_URL", "http://my-saf.example:9000")

    from safer.adapters.otel import _resolve_endpoint

    assert _resolve_endpoint(None) == "http://my-saf.example:9000/v1/traces"


def test_explicit_endpoint_overrides_env(monkeypatch):
    monkeypatch.setenv("SAFER_API_URL", "http://should-be-ignored:9000")
    from safer.adapters.otel import _resolve_endpoint

    result = _resolve_endpoint("http://custom.example/otel/v1/traces")
    assert result == "http://custom.example/otel/v1/traces"


def test_safer_otel_endpoint_env_wins_over_api_url(monkeypatch):
    monkeypatch.setenv("SAFER_API_URL", "http://api.example:8000")
    monkeypatch.setenv(
        "SAFER_OTEL_ENDPOINT", "http://otel.example:4318/v1/traces"
    )
    from safer.adapters.otel import _resolve_endpoint

    assert _resolve_endpoint(None) == "http://otel.example:4318/v1/traces"


def test_bridge_is_idempotent_across_calls():
    from opentelemetry import trace

    from safer.adapters.otel import configure_otel_bridge

    configure_otel_bridge(agent_id="same", agent_name="Same")
    provider_1 = trace.get_tracer_provider()

    configure_otel_bridge(agent_id="same", agent_name="Same")
    provider_2 = trace.get_tracer_provider()

    assert provider_1 is provider_2


def test_bridge_also_starts_safer_runtime():
    """Calling configure_otel_bridge on a pristine system must also
    start the SAFER runtime (via ensure_runtime)."""
    assert client_mod._client is None

    from safer.adapters.otel import configure_otel_bridge

    configure_otel_bridge(agent_id="auto_run", agent_name="Auto Run")
    assert client_mod._client is not None
    assert client_mod._client.config.agent_id == "auto_run"


def test_instrument_missing_package_is_logged_not_raised(caplog):
    """Explicit `instrument=["anthropic"]` without the instrumentor
    package installed should log a hint, not raise. (If the instrumentor
    IS installed in CI we just assert the call returned cleanly.)"""
    import importlib.util

    from safer.adapters.otel import configure_otel_bridge

    with caplog.at_level("INFO", logger="safer.adapters.otel"):
        configure_otel_bridge(
            agent_id="inst_test",
            agent_name="Inst Test",
            instrument=["anthropic"],
        )

    have_anthropic = (
        importlib.util.find_spec("opentelemetry.instrumentation.anthropic")
        is not None
    )
    if not have_anthropic:
        assert any(
            "anthropic" in rec.message and "not installed" in rec.message
            for rec in caplog.records
        )


def test_unknown_instrumentor_name_raises():
    from safer.adapters.otel import configure_otel_bridge

    with pytest.raises(ValueError, match="unknown instrumentor"):
        configure_otel_bridge(
            agent_id="x",
            agent_name="X",
            instrument=["made-up-vendor"],
        )


def test_auto_detect_returns_installed_instrumentors_only():
    import importlib.util

    from safer.adapters.otel import _auto_detect_instrumentors

    detected = _auto_detect_instrumentors()
    # Must reflect whichever instrumentor packages happen to be installed.
    for name, (mod_path, _) in {
        "anthropic": (
            "opentelemetry.instrumentation.anthropic",
            "AnthropicInstrumentor",
        ),
        "openai": (
            "opentelemetry.instrumentation.openai",
            "OpenAIInstrumentor",
        ),
    }.items():
        if importlib.util.find_spec(mod_path) is not None:
            assert name in detected
        else:
            assert name not in detected
