"""The `instrument()` one-liner.

Detects installed frameworks (Claude Agent SDK, LangChain, ...) and
registers the appropriate adapter. Users who use vanilla Python can
still emit events via `safer.track_event()`.

On the first successful call in a process, `instrument()` also emits a
single `on_agent_register` event carrying a gzip+base64 snapshot of the
agent's Python source tree. This is what lets the backend populate the
Agents dashboard automatically — no paste-source step required.
"""

from __future__ import annotations

import atexit
import inspect
import logging
import threading
from typing import Any

from .client import SaferClient, clear_client, get_client, set_client
from .config import SaferConfig
from .events import Hook, OnAgentRegisterPayload
from .snapshot import ScanMode, SnapshotResult, build_snapshot

log = logging.getLogger("safer")

_DEFAULT_AGENT_ID = "agent_default"

# Remembers whether we've already emitted on_agent_register in this
# process so repeated calls to instrument() stay idempotent.
_registered_agents: set[str] = set()
_register_lock = threading.Lock()

# Framework detection is cached on the first instrument() call so that
# subsequent calls (for a second agent_id in the same process) carry the
# correct framework label instead of falling back to "custom".
_detected_framework: str | None = None


def instrument(
    *,
    api_url: str | None = None,
    api_key: str | None = None,
    guard_mode: str | None = None,
    agent_id: str | None = None,
    agent_name: str | None = None,
    agent_version: str | None = None,
    system_prompt: str | None = None,
    project_root: str | None = None,
    scan_mode: ScanMode | None = None,
    include: list[str] | tuple[str, ...] | None = None,
    exclude: list[str] | tuple[str, ...] | None = None,
    auto_register: bool = True,
    **extra_config: Any,
) -> SaferClient:
    """Initialize SAFER. Idempotent — subsequent calls return the same client.

    Auto-detects installed frameworks:
        - anthropic Agent SDK → claude_sdk adapter
        - langchain → langchain adapter

    Framework not detected? Use `safer.track_event(...)` manually.

    If `auto_register=True` (default), the first call in a process also
    emits an `on_agent_register` event with a project code snapshot so
    the backend can populate the Agents dashboard.

    Snapshot scope controls:
    - `scan_mode` — "imports" (default): walk the caller file's import
      graph bounded to the workspace root. "directory": recursive `.py`
      walk under the workspace root (the pre-26 behavior). "explicit":
      only patterns in `include`.
    - `include` — extra glob patterns to add on top of the chosen mode.
      Also the way to pull in non-`.py` files (e.g., `prompts/**/*.md`).
    - `exclude` — glob patterns to drop.
    - `project_root` — override the workspace root detection.
    """
    global _detected_framework
    include_tuple = tuple(include or ())
    exclude_tuple = tuple(exclude or ())

    existing = get_client()
    if existing is not None:
        # Idempotent — but if someone calls instrument() a second time
        # for a *different* agent_id, still emit a register for that one.
        if auto_register and agent_id:
            _maybe_register(
                existing,
                agent_id=agent_id,
                agent_name=agent_name,
                agent_version=agent_version,
                system_prompt=system_prompt,
                project_root=project_root,
                caller_file=_caller_file(),
                framework_hint=_detected_framework,
                scan_mode=scan_mode,
                include=include_tuple,
                exclude=exclude_tuple,
            )
        return existing

    overrides: dict[str, Any] = {}
    if api_url is not None:
        overrides["api_url"] = api_url
    if api_key is not None:
        overrides["api_key"] = api_key
    if guard_mode is not None:
        overrides["guard_mode"] = guard_mode
    if agent_id is not None:
        overrides["agent_id"] = agent_id
    if agent_name is not None:
        overrides["agent_name"] = agent_name
    overrides.update(extra_config)

    config = SaferConfig.from_env(**overrides)
    client = SaferClient(config)
    client.start()
    set_client(client)

    # Graceful shutdown at process exit.
    atexit.register(clear_client)

    # Register framework adapters (best-effort, non-fatal).
    framework = _register_adapters(client)
    _detected_framework = framework

    # Onboarding hook — fires once per process per agent_id.
    if auto_register:
        _maybe_register(
            client,
            agent_id=config.agent_id or agent_id,
            agent_name=config.agent_name or agent_name,
            agent_version=agent_version,
            system_prompt=system_prompt,
            project_root=project_root,
            caller_file=_caller_file(),
            framework_hint=framework,
            scan_mode=scan_mode,
            include=include_tuple,
            exclude=exclude_tuple,
        )

    return client


def _caller_file() -> str | None:
    """Best-effort: the file of whoever triggered instrument().

    Walks the call stack past any `safer/` package frame (including
    adapter constructors and `_bootstrap.ensure_runtime`) so the
    onboarding snapshot anchors on the user's entry point, not our
    internals.
    """
    try:
        stack = inspect.stack()
    except Exception:
        return None
    for frame in stack:
        filename = frame.filename
        if not filename:
            continue
        # Skip any frame inside the safer package itself.
        normalized = filename.replace("\\", "/")
        if "/safer/" in normalized and (
            normalized.endswith(".py") or normalized.endswith(".pyc")
        ):
            continue
        return filename
    return None


def _register_adapters(client: SaferClient) -> str:
    """Detect installed frameworks and register matching adapters.

    Returns a short framework label suitable for the on_agent_register
    payload. Priority order matches SAFER's "framework vs client-proxy
    vs otel-bridge" story — framework-native adapters (LangChain,
    Google ADK, Strands) win over raw LLM SDKs (anthropic, openai), and
    OpenTelemetry is the fallback label when only OTel is installed.

    Possible labels: `langchain`, `google-adk`, `strands`, `anthropic`,
    `openai`, `otel-bridge`, `custom`.
    """
    import importlib.util

    def _has_module(name: str) -> bool:
        """find_spec on a dotted name raises ModuleNotFoundError on
        Python 3.13+ when the *parent* package is missing (e.g.
        looking for `google.adk` when `google` itself isn't installed).
        Catch that so detection stays a soft probe — never a fatal."""
        try:
            return importlib.util.find_spec(name) is not None
        except (ModuleNotFoundError, ValueError, ImportError):
            return False

    detected: list[str] = []

    # Framework-native first — these imply a native hook adapter is in
    # the picture (SaferCallbackHandler, SaferAdkPlugin, SaferHookProvider).
    for label, module in (
        ("langchain", "langchain"),
        ("google-adk", "google.adk"),
        ("strands", "strands"),
        ("crewai", "crewai"),
        ("bedrock", "boto3"),
    ):
        if _has_module(module):
            detected.append(label)

    # Raw LLM SDKs — usually paired with the OTel bridge or wrap_* shims.
    for label, module in (
        ("anthropic", "anthropic"),
        ("openai", "openai"),
    ):
        if _has_module(module):
            detected.append(label)

    # OpenTelemetry as a fallback label when the user is likely using
    # the OTel bridge path but none of the framework-native deps above
    # are in the picture.
    has_otel = _has_module("opentelemetry.sdk")

    if detected:
        log.info("SAFER: detected frameworks: %s", ", ".join(detected))
        return detected[0]
    if has_otel:
        log.info("SAFER: only OpenTelemetry detected; label = otel-bridge")
        return "otel-bridge"
    log.info(
        "SAFER: no framework detected; use safer.track_event() for manual instrumentation"
    )
    return "custom"


def _maybe_register(
    client: SaferClient,
    *,
    agent_id: str | None,
    agent_name: str | None,
    agent_version: str | None,
    system_prompt: str | None,
    project_root: str | None,
    caller_file: str | None,
    framework_hint: str | None,
    scan_mode: ScanMode | None = None,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
) -> None:
    """Emit `on_agent_register` once per agent_id per process."""
    aid = agent_id or _DEFAULT_AGENT_ID
    with _register_lock:
        if aid in _registered_agents:
            return
        _registered_agents.add(aid)

    try:
        snap: SnapshotResult = build_snapshot(
            project_root=project_root,
            caller_file=caller_file,
            scan_mode=scan_mode,
            include=include,
            exclude=exclude,
        )
    except Exception as e:
        log.warning("SAFER: snapshot failed, skipping on_agent_register: %s", e)
        return

    payload = OnAgentRegisterPayload(
        session_id=f"boot_{aid}",
        agent_id=aid,
        sequence=0,
        hook=Hook.ON_AGENT_REGISTER,
        agent_name=agent_name or aid,
        agent_version=agent_version,
        framework=framework_hint or "custom",
        system_prompt=system_prompt,
        project_root=snap.project_root,
        code_snapshot_b64=snap.b64,
        code_snapshot_hash=snap.sha256,
        file_count=snap.file_count,
        total_bytes=snap.total_bytes,
        snapshot_truncated=snap.truncated,
        source="sdk",
    )
    client.emit(payload)
    log.info(
        "SAFER: registered agent %s (%d files, %d bytes, hash %s, mode=%s)",
        aid,
        snap.file_count,
        snap.total_bytes,
        snap.sha256[:12],
        snap.scan_mode_used,
    )


def _reset_registered_agents_for_tests() -> None:
    """Test hook — forget which agents we've registered this process."""
    global _detected_framework
    with _register_lock:
        _registered_agents.clear()
    _detected_framework = None
