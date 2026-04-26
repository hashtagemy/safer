"""OTel bridge — zero-config full observability for raw LLM SDKs.

Raw client libraries like `anthropic` and `openai` don't expose a
framework hook surface (no BaseCallbackHandler, no BasePlugin). The
OpenTelemetry project ships auto-instrumentors for them
(`opentelemetry-instrumentation-anthropic`,
`opentelemetry-instrumentation-openai`) that monkey-patch
`messages.create` / `chat.completions.create` and emit GenAI
semantic-convention spans. SAFER's backend parses those spans on
`/v1/traces` (see `safer_backend.ingestion.otlp`) and fans them out
into the 9-hook model.

This module wires the two ends together with one function:

```python
from safer.adapters.otel import configure_otel_bridge
configure_otel_bridge(agent_id="support", agent_name="Support")
# Every Anthropic() / OpenAI() call from here on is observed by SAFER.
```

`configure_otel_bridge` is idempotent: calling it twice does not
reinstall the tracer provider or double-instrument. Instrumentors are
opt-in via the `instrument=` argument (default: auto-detect installed
instrumentor packages).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable

from ._bootstrap import ensure_runtime

log = logging.getLogger("safer.adapters.otel")

_BRIDGE_CONFIGURED: bool = False
_INSTRUMENTED: set[str] = set()

# Instrumentor package registry: safer name → (import path, class name).
_INSTRUMENTORS: dict[str, tuple[str, str]] = {
    "anthropic": (
        "opentelemetry.instrumentation.anthropic",
        "AnthropicInstrumentor",
    ),
    "openai": (
        "opentelemetry.instrumentation.openai",
        "OpenAIInstrumentor",
    ),
}


def _import_otel_core() -> tuple[Any, Any, Any, Any]:
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:
        raise ImportError(
            "configure_otel_bridge requires OpenTelemetry. Install with "
            "`pip install 'safer-sdk[otel]'` (or the `otel-anthropic` / "
            "`otel-openai` extras to also pull in the instrumentor)."
        ) from e
    return trace, OTLPSpanExporter, TracerProvider, BatchSpanProcessor


def _resolve_endpoint(endpoint: str | None) -> str:
    """Pick the SAFER backend trace endpoint.

    Precedence: explicit `endpoint` arg → `SAFER_OTEL_ENDPOINT` env →
    `SAFER_API_URL + /v1/traces` env → local default.
    """
    if endpoint:
        return endpoint
    env_ep = os.environ.get("SAFER_OTEL_ENDPOINT")
    if env_ep:
        return env_ep
    base = os.environ.get("SAFER_API_URL", "http://localhost:8000").rstrip("/")
    return f"{base}/v1/traces"


def _auto_detect_instrumentors() -> list[str]:
    """Return names of instrumentor packages that happen to be
    installed. Used when the caller does not pass `instrument=...`."""
    import importlib.util

    detected: list[str] = []
    for name, (module, _cls) in _INSTRUMENTORS.items():
        if importlib.util.find_spec(module) is not None:
            detected.append(name)
    return detected


def _install_instrumentor(name: str) -> bool:
    """Import and run the named instrumentor's `.instrument()` method.

    Returns True on success, False if the package is missing. Raises
    only on genuine instrumentation errors."""
    if name in _INSTRUMENTED:
        return True
    spec = _INSTRUMENTORS.get(name)
    if spec is None:
        raise ValueError(f"unknown instrumentor: {name!r}")
    module_path, class_name = spec
    try:
        module = __import__(module_path, fromlist=[class_name])
    except ImportError:
        log.info(
            "safer.adapters.otel: instrumentor for %s not installed "
            "(pip install 'safer-sdk[otel-%s]')",
            name,
            name,
        )
        return False
    instr_cls = getattr(module, class_name)
    instr_cls().instrument()
    _INSTRUMENTED.add(name)
    log.info("safer.adapters.otel: %s instrumentor enabled", name)
    return True


def configure_otel_bridge(
    *,
    agent_id: str,
    agent_name: str | None = None,
    endpoint: str | None = None,
    instrument: Iterable[str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> None:
    """Wire OpenTelemetry auto-instrumentors to SAFER's OTLP endpoint.

    Idempotent: tracer provider is installed once per process; repeated
    calls only register additional instrumentors requested.

    Args:
        agent_id: stable identifier for this agent; appears on
            every span as `safer.agent_id` so SAFER can route events.
        agent_name: human-readable label (defaults to `agent_id`).
        endpoint: OTLP/HTTP trace endpoint. Defaults to
            `$SAFER_OTEL_ENDPOINT` or `$SAFER_API_URL + /v1/traces` or
            `http://localhost:8000/v1/traces`.
        instrument: explicit list of instrumentor names
            (`"anthropic"`, `"openai"`). If `None`, auto-detects the
            packages installed in the environment.
        headers: extra HTTP headers for the exporter (e.g. auth).
        timeout: exporter request timeout (seconds).

    Raises:
        ImportError: if `opentelemetry-sdk` /
            `opentelemetry-exporter-otlp-proto-http` are missing.
    """
    global _BRIDGE_CONFIGURED

    ensure_runtime(agent_id, agent_name, framework="otel-bridge")

    trace, OTLPSpanExporter, TracerProvider, BatchSpanProcessor = (
        _import_otel_core()
    )
    from opentelemetry.sdk.resources import Resource

    if not _BRIDGE_CONFIGURED:
        resolved_endpoint = _resolve_endpoint(endpoint)
        resource = Resource.create(
            {
                "service.name": agent_id,
                "service.instance.id": agent_name or agent_id,
                "safer.agent_id": agent_id,
                "safer.agent_name": agent_name or agent_id,
            }
        )
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(
            endpoint=resolved_endpoint,
            headers=headers,
            timeout=int(timeout),
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _BRIDGE_CONFIGURED = True
        log.info(
            "safer.adapters.otel: bridge configured → %s (agent=%s)",
            resolved_endpoint,
            agent_id,
        )

    # Resolve which instrumentors to run.
    if instrument is None:
        to_enable = _auto_detect_instrumentors()
        if not to_enable:
            log.info(
                "safer.adapters.otel: no OTel GenAI instrumentor detected. "
                "Install one of: "
                "`opentelemetry-instrumentation-anthropic`, "
                "`opentelemetry-instrumentation-openai`."
            )
    else:
        to_enable = list(instrument)

    for name in to_enable:
        _install_instrumentor(name)


def _reset_for_tests() -> None:
    """Test hook — clear the 'bridge configured' memoization so each
    test starts from a clean TracerProvider state."""
    global _BRIDGE_CONFIGURED
    _BRIDGE_CONFIGURED = False
    _INSTRUMENTED.clear()


__all__ = ["configure_otel_bridge"]
