"""Google ADK adapter — bridges the ADK plugin system onto SAFER's
9-hook lifecycle.

Google's Agent Development Kit (`pip install google-adk`) exposes two
extension layers:

* **Runner plugins** (`google.adk.plugins.base_plugin.BasePlugin`) —
  registered once via `Runner(plugins=[...])`, applied globally to every
  agent run through the runner. Twelve async callbacks cover the full
  invocation lifecycle (user message → run → agent → model → tool →
  errors → event stream). This is Google's recommended integration point
  for cross-cutting concerns — logging, monitoring, policy — and the
  layer SAFER uses.
* **Agent-level callback fields** on `LlmAgent` — six slots
  (`before/after_agent/model/tool_callback`), agent-local. Useful for
  per-agent logic but misses `on_model_error`, `on_tool_error`,
  `before/after_run`, and `on_event`.

### Two-line integration (recommended)

```python
from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from safer.adapters.google_adk import SaferAdkPlugin

agent = LlmAgent(model="gemini-2.5-pro", name="repo_analyst", ...)
runner = InMemoryRunner(
    agent=agent,
    app_name="repo_analyst",
    plugins=[SaferAdkPlugin(agent_id="repo_analyst",
                             agent_name="Repo Analyst")],
)
```

`SaferAdkPlugin.__init__` calls `ensure_runtime(...)` so the user does
not have to call `instrument()` separately (it is called automatically
when missing). All thirteen plugin callbacks are implemented as
`async def` because the ADK `PluginManager` awaits every callback.

### Legacy shim (`attach_safer` / `wrap_adk`)

For single-agent setups that bypass `Runner`, `attach_safer(agent, ...)`
binds a subset of the plugin's methods to the agent-level callback
fields. This covers six of the nine SAFER hooks; `on_error`,
`on_session_start`, and `on_session_end` require the `Runner` plugin
path to fire reliably. Prefer `Runner(plugins=[SaferAdkPlugin(...)])`
whenever possible.

Google ADK is an optional dependency — importing this module is safe
even when `google-adk` is missing. `BasePlugin` is imported lazily
inside `SaferAdkPlugin`'s construction so `from safer.adapters.google_adk
import SaferAdkPlugin` never fails.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from ..client import SaferClient, get_client
from ..events import (
    AfterLLMCallPayload,
    AfterToolUsePayload,
    BeforeLLMCallPayload,
    BeforeToolUsePayload,
    OnAgentDecisionPayload,
    OnErrorPayload,
    OnFinalOutputPayload,
    OnSessionEndPayload,
    OnSessionStartPayload,
)
from ._bootstrap import ensure_runtime

log = logging.getLogger("safer.adapters.google_adk")


# ---------- pricing — delegated to safer._pricing --------------------------


def _estimate_cost(
    model: str, tokens_in: int, tokens_out: int, cached: int = 0
) -> float:
    """Estimate USD cost via the shared pricing table; 0.0 for unknown models."""
    from .._pricing import estimate_cost

    cost = estimate_cost(
        model, tokens_in=tokens_in, tokens_out=tokens_out, cache_read=cached
    )
    return cost or 0.0


# ---------- content / usage helpers ----------


def _safe_str(obj: Any, limit: int = 8000) -> str:
    if obj is None:
        return ""
    s = str(obj)
    return s if len(s) <= limit else s[:limit] + "…"


def _flatten_contents(contents: Any) -> str:
    """Flatten a google.genai Content (or list of Content) into text."""
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    parts: list[str] = []
    try:
        iterable = contents if isinstance(contents, list) else [contents]
        for item in iterable:
            sub_parts = getattr(item, "parts", None)
            if sub_parts:
                for p in sub_parts:
                    text = getattr(p, "text", None)
                    if isinstance(text, str) and text:
                        parts.append(text)
                    else:
                        fn_call = getattr(p, "function_call", None)
                        if fn_call is not None:
                            parts.append(str(fn_call))
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
    except Exception:
        return _safe_str(contents)
    return "\n".join(parts)


def _extract_response_text(llm_response: Any) -> str:
    content = getattr(llm_response, "content", None)
    if content is None:
        return ""
    return _flatten_contents(content)


def _extract_usage(llm_response: Any) -> tuple[int, int, int]:
    um = getattr(llm_response, "usage_metadata", None)
    if um is None:
        return 0, 0, 0
    return (
        int(getattr(um, "prompt_token_count", 0) or 0),
        int(getattr(um, "candidates_token_count", 0) or 0),
        int(getattr(um, "cached_content_token_count", 0) or 0),
    )


def _extract_tools(llm_request: Any) -> list[dict[str, Any]]:
    tools_dict = getattr(llm_request, "tools_dict", None)
    if isinstance(tools_dict, dict) and tools_dict:
        return [{"name": str(k)} for k in tools_dict]
    config = getattr(llm_request, "config", None)
    cfg_tools = getattr(config, "tools", None) if config is not None else None
    if isinstance(cfg_tools, list):
        out: list[dict[str, Any]] = []
        for t in cfg_tools:
            name = getattr(t, "name", None) or (
                t.get("name") if isinstance(t, dict) else None
            )
            if name:
                out.append({"name": str(name)})
        return out
    return []


def _event_contains_tool_use(event: Any) -> tuple[bool, str | None]:
    """Does this ADK `Event` carry a `function_call` / tool_use block?

    Returns (flag, tool_name_or_none). Used from `on_event_callback` to
    synthesize `on_agent_decision`.
    """
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) if content else None
    if not parts:
        return False, None
    for p in parts:
        fn = getattr(p, "function_call", None)
        if fn is not None:
            return True, str(getattr(fn, "name", "") or None) or None
    return False, None


# ---------- the ADK Plugin base, loaded lazily ----------


def _load_base_plugin() -> type:
    """Import ADK's BasePlugin lazily so this module stays import-safe
    without `google-adk` installed."""
    try:
        from google.adk.plugins.base_plugin import BasePlugin
    except ImportError as e:
        raise ImportError(
            "SaferAdkPlugin requires `google-adk`. "
            "Install with `pip install google-adk` or "
            "`pip install 'safer-sdk[google-adk]'`."
        ) from e
    return BasePlugin


# ---------- the concrete plugin, built at first instantiation ----------


def _make_plugin_cls() -> type:
    BasePlugin = _load_base_plugin()

    class _SaferAdkPlugin(BasePlugin):  # type: ignore[misc, valid-type]
        """BasePlugin implementation that forwards ADK's 12 callbacks
        into SAFER's 9-hook event model.

        Session boundary: each `Runner.run_async()` call produces one ADK
        invocation with its own `invocation_id` (UUID).  We map one ADK
        invocation to one SAFER session, so a runner that handles multiple
        user messages produces multiple distinct SAFER sessions — even
        though the same plugin instance handles them all.
        """

        def __init__(
            self,
            *,
            agent_id: str,
            agent_name: str | None = None,
            session_id: str | None = None,
            client: SaferClient | None = None,
        ) -> None:
            ensure_runtime(agent_id, agent_name)
            super().__init__(name="safer")
            self.agent_id = agent_id
            self.agent_name = agent_name or agent_id
            # `session_id` constructor arg pins the FIRST invocation only;
            # subsequent invocations rotate to fresh UUIDs (see
            # `_begin_invocation`).  None means "auto-generate per invocation".
            self._initial_session_id = session_id
            self._current_session_id: str | None = None
            self._client = client
            self._session_started = False
            self._sequence = 0
            self._step_count = 0
            self._model_start_ts: dict[str, float] = {}
            self._tool_start_ts: dict[str, float] = {}
            self._last_model = "gemini"
            self._profile_synced = False

        # internal plumbing ----------------------------------------

        @property
        def session_id(self) -> str:
            """Current SAFER session id.  Rotates per ADK invocation.

            If no invocation is active yet, returns the constructor-supplied
            id (or auto-generates one).  This means tests that read
            `plugin.session_id` before any callback fires still get a stable
            value, while real runs always carry the per-invocation id."""
            if self._current_session_id is None:
                self._current_session_id = (
                    self._initial_session_id or f"sess_{uuid.uuid4().hex[:16]}"
                )
            return self._current_session_id

        def _begin_invocation(self, invocation_id: str | None = None) -> None:
            """Start a fresh SAFER session for a new ADK invocation.

            Called from `before_run_callback`.  Each `Runner.run_async()`
            call produces a new `invocation_id`; we map one ADK invocation
            to one SAFER session so that a runner serving multiple user
            messages produces distinct sessions on the dashboard."""
            # Rotate session id (use the ADK invocation_id if provided so
            # the SAFER and ADK ids correlate; otherwise auto-generate).
            if invocation_id:
                self._current_session_id = f"sess_{str(invocation_id)[:16]}"
            else:
                self._current_session_id = f"sess_{uuid.uuid4().hex[:16]}"
            # Reset per-invocation state
            self._session_started = False
            self._sequence = 0
            self._step_count = 0
            self._model_start_ts.clear()
            self._tool_start_ts.clear()

        def _get_client(self) -> SaferClient | None:
            return self._client or get_client()

        def _next_sequence(self) -> int:
            seq = self._sequence
            self._sequence += 1
            return seq

        def _emit(self, payload: Any) -> None:
            client = self._get_client()
            if client is None:
                return
            try:
                client.track_event(
                    payload.hook,
                    payload.model_dump(mode="json"),
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                )
            except Exception as e:  # pragma: no cover — transport errors
                log.debug("google-adk plugin emit failed: %s", e)

        def _emit_error(self, err: BaseException, kind: str = "error") -> None:
            try:
                self._emit(
                    OnErrorPayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_sequence(),
                        error_type=f"{type(err).__name__}:{kind}",
                        message=str(err)[:2000],
                    )
                )
            except Exception:  # pragma: no cover — never raise from a callback
                pass

        def _ensure_session_started(
            self, context: dict[str, Any] | None = None
        ) -> None:
            if self._session_started:
                return
            self._session_started = True
            self._emit(
                OnSessionStartPayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_sequence(),
                    agent_name=self.agent_name,
                    context=context or {},
                )
            )

        def _maybe_sync_profile(self, system_prompt: str | None) -> None:
            if self._profile_synced or not system_prompt:
                return
            client = self._get_client()
            if client is None:
                return
            self._profile_synced = True
            try:
                client.schedule_profile_patch(
                    self.agent_id,
                    system_prompt=system_prompt,
                    name=self.agent_name,
                )
            except Exception as e:  # pragma: no cover
                log.debug("google-adk profile sync failed: %s", e)

        # ADK plugin callbacks -------------------------------------

        async def on_user_message_callback(
            self, *, invocation_context: Any, user_message: Any
        ) -> None:
            # Purely observational — the backend session already
            # receives the user text via on_session_start context.
            try:
                text = _flatten_contents(user_message)[:500]
                if text:
                    self._ensure_session_started(context={"user_input": text})
            except Exception as e:
                self._emit_error(e, "user_message")
            return None

        async def before_run_callback(self, *, invocation_context: Any) -> None:
            try:
                inv_id = getattr(invocation_context, "invocation_id", None)
                # Rotate to a fresh SAFER session for this invocation; if the
                # plugin has been used for prior invocations, this is what
                # prevents the multi-turn collision bug where every call
                # writes to the same session_id.
                self._begin_invocation(invocation_id=inv_id)
                context: dict[str, Any] = {}
                if inv_id:
                    context["invocation_id"] = str(inv_id)
                self._ensure_session_started(context=context)
            except Exception as e:
                self._emit_error(e, "before_run")
            return None

        async def before_agent_callback(
            self, *, agent: Any, callback_context: Any
        ) -> None:
            # Observation-only: just ensure the session has started.  We do
            # NOT emit `on_agent_decision` here — that would double-fire
            # alongside `on_event_callback`'s tool-call decision and trigger
            # the Multi-Persona Judge twice per turn.  The model's actual
            # decision (which tool to call) surfaces in `on_event_callback`.
            try:
                self._ensure_session_started()
            except Exception as e:
                self._emit_error(e, "before_agent")
            return None

        async def after_agent_callback(
            self, *, agent: Any, callback_context: Any
        ) -> None:
            try:
                final_text = ""
                session = getattr(callback_context, "session", None)
                events = getattr(session, "events", None) if session else None
                if events:
                    for ev in reversed(list(events)):
                        content = getattr(ev, "content", None)
                        role = getattr(content, "role", None) if content else None
                        if role in ("model", "assistant"):
                            final_text = _flatten_contents(content)
                            if final_text:
                                break
                self._emit(
                    OnFinalOutputPayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_sequence(),
                        final_response=_safe_str(final_text, 4000),
                        total_steps=self._step_count,
                    )
                )
            except Exception as e:
                self._emit_error(e, "after_agent")
            return None

        async def before_model_callback(
            self, *, callback_context: Any, llm_request: Any
        ) -> None:
            try:
                self._ensure_session_started()
                model = str(getattr(llm_request, "model", "gemini") or "gemini")
                self._last_model = model
                prompt = _flatten_contents(getattr(llm_request, "contents", None))
                config = getattr(llm_request, "config", None)
                sys_inst = (
                    getattr(config, "system_instruction", None) if config else None
                )
                if isinstance(sys_inst, str):
                    self._maybe_sync_profile(sys_inst)
                elif sys_inst is not None:
                    self._maybe_sync_profile(_flatten_contents(sys_inst) or None)
                inv_id = str(
                    getattr(callback_context, "invocation_id", "") or uuid.uuid4()
                )
                self._model_start_ts[inv_id] = time.monotonic()
                self._emit(
                    BeforeLLMCallPayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_sequence(),
                        model=model,
                        prompt=prompt[:8000],
                        tools=_extract_tools(llm_request),
                    )
                )
            except Exception as e:
                self._emit_error(e, "before_model")
            return None

        async def after_model_callback(
            self, *, callback_context: Any, llm_response: Any
        ) -> None:
            try:
                inv_id = str(getattr(callback_context, "invocation_id", "") or "")
                started = self._model_start_ts.pop(inv_id, None)
                latency_ms = (
                    int((time.monotonic() - started) * 1000) if started else 0
                )
                model = str(
                    getattr(llm_response, "model_version", None)
                    or self._last_model
                )
                tokens_in, tokens_out, cached = _extract_usage(llm_response)
                text = _extract_response_text(llm_response)
                cost = _estimate_cost(model, tokens_in, tokens_out, cached)
                self._emit(
                    AfterLLMCallPayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_sequence(),
                        model=model,
                        response=text[:8000],
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        cache_read_tokens=cached,
                        cost_usd=cost,
                        latency_ms=latency_ms,
                    )
                )
                self._step_count += 1
            except Exception as e:
                self._emit_error(e, "after_model")
            return None

        async def on_model_error_callback(
            self, *, callback_context: Any, llm_request: Any, error: BaseException
        ) -> None:
            self._emit_error(error, "model_error")
            return None

        async def before_tool_callback(
            self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any
        ) -> None:
            try:
                self._ensure_session_started()
                name = str(getattr(tool, "name", None) or "tool")
                key = (
                    f"{name}:{getattr(tool_context, 'function_call_id', '') or uuid.uuid4()}"
                )
                self._tool_start_ts[key] = time.monotonic()
                self._emit(
                    BeforeToolUsePayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_sequence(),
                        tool_name=name,
                        args=dict(tool_args or {}),
                    )
                )
            except Exception as e:
                self._emit_error(e, "before_tool")
            return None

        async def after_tool_callback(
            self,
            *,
            tool: Any,
            tool_args: dict[str, Any],
            tool_context: Any,
            result: dict[str, Any],
        ) -> None:
            try:
                name = str(getattr(tool, "name", None) or "tool")
                key = f"{name}:{getattr(tool_context, 'function_call_id', '') or ''}"
                started = self._tool_start_ts.pop(key, None)
                if started is None:
                    for k in list(self._tool_start_ts):
                        if k.startswith(name + ":"):
                            started = self._tool_start_ts.pop(k)
                            break
                duration_ms = (
                    int((time.monotonic() - started) * 1000) if started else 0
                )
                self._emit(
                    AfterToolUsePayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_sequence(),
                        tool_name=name,
                        result=_safe_str(result, 4000),
                        duration_ms=duration_ms,
                    )
                )
                self._step_count += 1
            except Exception as e:
                self._emit_error(e, "after_tool")
            return None

        async def on_tool_error_callback(
            self,
            *,
            tool: Any,
            tool_args: dict[str, Any],
            tool_context: Any,
            error: BaseException,
        ) -> None:
            self._emit_error(error, "tool_error")
            return None

        async def on_event_callback(
            self, *, invocation_context: Any, event: Any
        ) -> None:
            try:
                has_tool, tool_name = _event_contains_tool_use(event)
                if has_tool:
                    self._emit(
                        OnAgentDecisionPayload(
                            session_id=self.session_id,
                            agent_id=self.agent_id,
                            sequence=self._next_sequence(),
                            decision_type="tool_call",
                            chosen_action=tool_name,
                        )
                    )
            except Exception as e:
                self._emit_error(e, "on_event")
            return None

        async def after_run_callback(self, *, invocation_context: Any) -> None:
            try:
                self._emit(
                    OnSessionEndPayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_sequence(),
                        success=True,
                    )
                )
                self._session_started = False
            except Exception as e:
                self._emit_error(e, "after_run")
            return None

        async def close(self) -> None:  # type: ignore[override]
            return None

    return _SaferAdkPlugin


# ---------- public wrapper that builds the real class on first use ----------


class SaferAdkPlugin:  # type: ignore[no-redef]
    """BasePlugin subclass for Google ADK that bridges the plugin
    system to SAFER's 9-hook lifecycle.

    Construct once and pass via `Runner(plugins=[...])`. The real
    subclass is built the first time someone instantiates this wrapper
    so that importing `safer.adapters.google_adk` does not require
    `google-adk` to be installed.
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        real = _make_plugin_cls()
        return real(*args, **kwargs)


# ---------- legacy single-agent shim ----------


def _sync_from_async(async_method: Any) -> Any:
    """Return a sync callable that runs `async_method(**kwargs)` to
    completion. Used to bridge the plugin's async callbacks into ADK's
    agent-level callback fields which accept sync-or-async callables."""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        coro = async_method(*args, **kwargs)
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            # ADK is probably already inside an async context. Let the
            # caller await the coroutine directly by returning it — the
            # agent field accepts `Awaitable[...]` as well as sync.
            return coro
        return asyncio.run(coro)

    return wrapper


def attach_safer(
    adk_agent: Any,
    *,
    agent_id: str,
    agent_name: str | None = None,
    session_id: str | None = None,
    client: SaferClient | None = None,
) -> Any:
    """Attach SAFER to a single ADK agent *without* a Runner.

    Prefer `Runner(plugins=[SaferAdkPlugin(...)])` — that path covers
    all nine SAFER hooks including `on_error` and `on_session_end`.
    This shim binds the plugin's six per-agent methods to the agent's
    callback fields; the run-level hooks (`before/after_run`,
    `on_event`) do not fire via this path.
    """
    plugin = SaferAdkPlugin(
        agent_id=agent_id,
        agent_name=agent_name,
        session_id=session_id,
        client=client,
    )
    adk_agent.before_agent_callback = _sync_from_async(plugin.before_agent_callback)
    adk_agent.after_agent_callback = _sync_from_async(plugin.after_agent_callback)
    adk_agent.before_model_callback = _sync_from_async(plugin.before_model_callback)
    adk_agent.after_model_callback = _sync_from_async(plugin.after_model_callback)
    adk_agent.before_tool_callback = _sync_from_async(plugin.before_tool_callback)
    adk_agent.after_tool_callback = _sync_from_async(plugin.after_tool_callback)
    log.info(
        "safer.adapters.google_adk: attached SAFER callbacks at agent level for %r "
        "(prefer Runner(plugins=[SaferAdkPlugin(...)]) for full hook coverage)",
        agent_id,
    )
    return adk_agent


def wrap_adk(client: Any, *, agent_id: str, **kwargs: Any) -> Any:
    """Backward-compatible shim. Delegates to `attach_safer`."""
    return attach_safer(client, agent_id=agent_id, **kwargs)


__all__ = ["SaferAdkPlugin", "attach_safer", "wrap_adk"]
