"""LangChain adapter — a `BaseCallbackHandler` that maps LangChain's
native callbacks onto SAFER's 9-hook lifecycle.

Usage:

    from safer import instrument
    from safer.adapters.langchain import SaferCallbackHandler

    instrument()
    handler = SaferCallbackHandler(agent_id="code_analyst", agent_name="Code Analyst")

    # Chain / AgentExecutor / LLM can all accept callbacks=[handler].
    result = agent_executor.invoke({"input": "..."}, config={"callbacks": [handler]})

LangChain is an optional dependency — the module imports the
BaseCallbackHandler base lazily so people without LangChain installed
can still import `safer`. If LangChain is missing the class raises on
construction with a helpful message instead of a generic `ImportError`.

Hook mapping (exact LangChain → SAFER):

  on_chain_start      → on_session_start (first time only)
  on_llm_start        → before_llm_call
  on_llm_end          → after_llm_call
  on_chat_model_start → before_llm_call
  on_tool_start       → before_tool_use
  on_tool_end         → after_tool_use
  on_agent_action     → on_agent_decision
  on_agent_finish     → on_final_output
  on_chain_end        → on_session_end (last chain finishes)
  on_*_error          → on_error

Cost is best-effort: LangChain's LLM `response.llm_output` varies per
provider, so we look for `token_usage` and a `model_name` hint.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

from ..client import SaferClient, get_client
from ..events import (
    AfterLLMCallPayload,
    AfterToolUsePayload,
    BeforeLLMCallPayload,
    BeforeToolUsePayload,
    Hook,
    OnAgentDecisionPayload,
    OnErrorPayload,
    OnFinalOutputPayload,
    OnSessionEndPayload,
    OnSessionStartPayload,
)

log = logging.getLogger("safer.adapters.langchain")


# LangChain's BaseCallbackHandler is imported lazily so `safer.adapters`
# stays import-safe even when `langchain_core` isn't installed.
def _import_base() -> type:
    try:
        from langchain_core.callbacks import BaseCallbackHandler

        return BaseCallbackHandler
    except ImportError:
        try:
            from langchain.callbacks.base import BaseCallbackHandler  # type: ignore

            return BaseCallbackHandler
        except ImportError as e:
            raise ImportError(
                "SaferCallbackHandler requires `langchain-core`. "
                "Install it with `pip install langchain-core` (or `safer-sdk[langchain]`)."
            ) from e


# ---------- pricing mirror (kept in sync with claude_sdk._PRICING) ----------

_PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-4-7": (15.0, 75.0, 1.5, 18.75),
    "claude-opus-4-5": (15.0, 75.0, 1.5, 18.75),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75),
    "claude-haiku-4-5": (0.80, 4.0, 0.08, 1.0),
    # Best-effort defaults for common non-Anthropic models users may
    # wire through LangChain. Cost is inaccurate for these; treat as
    # signal only.
    "gpt-4o": (2.5, 10.0, 0.0, 0.0),
    "gpt-4o-mini": (0.15, 0.60, 0.0, 0.0),
}


def _estimate_cost(
    model: str, tokens_in: int, tokens_out: int, cache_read: int = 0
) -> float:
    pricing = _PRICING.get(model) or _PRICING["claude-opus-4-7"]
    p_in, p_out, p_cr, _p_cw = pricing
    billable_in = max(0, tokens_in - cache_read)
    return (
        (billable_in * p_in) + (tokens_out * p_out) + (cache_read * p_cr)
    ) / 1_000_000


def _extract_tokens(llm_output: Any) -> tuple[int, int, int]:
    """Pull (tokens_in, tokens_out, cache_read) from a LangChain response."""
    if not isinstance(llm_output, dict):
        return 0, 0, 0
    usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
    if not isinstance(usage, dict):
        return 0, 0, 0
    tokens_in = int(
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or 0
    )
    tokens_out = int(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or 0
    )
    cache_read = int(
        usage.get("cache_read_input_tokens")
        or usage.get("cache_read")
        or 0
    )
    return tokens_in, tokens_out, cache_read


def _extract_model(serialized: Any, llm_output: Any) -> str:
    for source in (llm_output, serialized):
        if isinstance(source, dict):
            for key in ("model_name", "model", "name"):
                v = source.get(key)
                if isinstance(v, str) and v:
                    return v
            # `kwargs` nested case
            kwargs = source.get("kwargs") or {}
            if isinstance(kwargs, dict):
                for key in ("model", "model_name"):
                    v = kwargs.get(key)
                    if isinstance(v, str) and v:
                        return v
    return "unknown"


def _make_handler_cls() -> type:
    """Build the concrete SaferCallbackHandler class bound to LangChain's base."""
    Base = _import_base()

    class SaferCallbackHandler(Base):  # type: ignore[misc, valid-type]
        """Maps LangChain callbacks onto SAFER's 9-hook lifecycle."""

        raise_error = False
        ignore_llm = False
        ignore_chain = False
        ignore_agent = False
        ignore_retriever = True
        ignore_chat_model = False

        def __init__(
            self,
            *,
            agent_id: str,
            agent_name: str | None = None,
            session_id: str | None = None,
            client: SaferClient | None = None,
        ) -> None:
            super().__init__()
            self.agent_id = agent_id
            self.agent_name = agent_name or agent_id
            self.session_id = session_id or f"sess_{uuid.uuid4().hex[:16]}"
            self._client = client
            self._session_started = False
            self._sequence = 0
            self._llm_start_ts: dict[str, float] = {}
            self._tool_start_ts: dict[str, float] = {}
            self._step_count = 0
            self._profile_synced = False

        # ---------- internal helpers ----------

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
                log.debug("langchain adapter emit failed: %s", e)

        def _ensure_session_started(self, context: dict[str, Any] | None = None) -> None:
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

        # ---------- chain lifecycle ----------

        def on_chain_start(
            self,
            serialized: dict[str, Any],
            inputs: dict[str, Any],
            **kwargs: Any,
        ) -> None:
            self._ensure_session_started(context={"inputs": _safe_preview(inputs)})

        def on_chain_end(
            self, outputs: dict[str, Any], **kwargs: Any
        ) -> None:
            # LangChain fires on_chain_end for every sub-chain. Only close
            # the SAFER session when the PARENT chain finishes, which we
            # approximate by waiting for on_agent_finish — so here we
            # simply no-op.
            return None

        def on_chain_error(
            self, error: BaseException, **kwargs: Any
        ) -> None:
            self._emit_error(error)

        # ---------- llm lifecycle ----------

        def on_llm_start(
            self,
            serialized: dict[str, Any],
            prompts: list[str],
            *,
            run_id: Any | None = None,
            **kwargs: Any,
        ) -> None:
            self._ensure_session_started()
            rid = str(run_id or uuid.uuid4())
            self._llm_start_ts[rid] = time.monotonic()
            model = _extract_model(serialized, kwargs.get("invocation_params"))
            prompt = "\n\n".join(prompts) if prompts else ""
            self._emit(
                BeforeLLMCallPayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_sequence(),
                    model=model,
                    prompt=prompt[:8000],
                    tools=[],
                    temperature=(
                        (kwargs.get("invocation_params") or {}).get("temperature")
                    ),
                )
            )

        def on_chat_model_start(
            self,
            serialized: dict[str, Any],
            messages: list[list[Any]],
            *,
            run_id: Any | None = None,
            **kwargs: Any,
        ) -> None:
            # Convert chat messages to a flat prompt string for our hook.
            flat: list[str] = []
            system_parts: list[str] = []
            for turn in messages or []:
                for m in turn or []:
                    content = getattr(m, "content", None) or getattr(m, "text", None)
                    if content:
                        flat.append(str(content))
                    # Capture SystemMessage text so we can sync the agent
                    # profile back to the backend the first time it shows up.
                    if getattr(m, "type", None) == "system" and content:
                        system_parts.append(str(content))
            if system_parts and not self._profile_synced:
                client = self._get_client()
                if client is not None:
                    self._profile_synced = True
                    try:
                        client.schedule_profile_patch(
                            self.agent_id,
                            system_prompt="\n".join(system_parts).strip() or None,
                            name=self.agent_name,
                        )
                    except Exception as e:  # pragma: no cover — defensive
                        log.debug("langchain profile sync failed: %s", e)
            self.on_llm_start(
                serialized,
                ["\n".join(flat)] if flat else [""],
                run_id=run_id,
                **kwargs,
            )

        def on_llm_end(
            self,
            response: Any,
            *,
            run_id: Any | None = None,
            **kwargs: Any,
        ) -> None:
            rid = str(run_id or "")
            started = self._llm_start_ts.pop(rid, None)
            latency_ms = int((time.monotonic() - started) * 1000) if started else 0

            llm_output = getattr(response, "llm_output", None)
            tokens_in, tokens_out, cache_read = _extract_tokens(llm_output)

            # Try to build a response string from generations.
            text = ""
            try:
                generations = getattr(response, "generations", []) or []
                if generations and generations[0]:
                    first = generations[0][0]
                    text = getattr(first, "text", "") or getattr(
                        getattr(first, "message", None), "content", ""
                    )
            except Exception:  # pragma: no cover — defensive
                text = ""

            model = _extract_model({}, llm_output)
            cost = _estimate_cost(model, tokens_in, tokens_out, cache_read)

            self._emit(
                AfterLLMCallPayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_sequence(),
                    model=model,
                    response=str(text)[:8000],
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cache_read_tokens=cache_read,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                )
            )
            self._step_count += 1

        def on_llm_error(
            self, error: BaseException, **kwargs: Any
        ) -> None:
            self._emit_error(error)

        # ---------- tools ----------

        def on_tool_start(
            self,
            serialized: dict[str, Any],
            input_str: str,
            *,
            run_id: Any | None = None,
            **kwargs: Any,
        ) -> None:
            self._ensure_session_started()
            rid = str(run_id or uuid.uuid4())
            self._tool_start_ts[rid] = time.monotonic()
            tool_name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
            # LangChain passes the tool input as a string; keep a structured
            # view too for the Judge.
            args: dict[str, Any] = {"input": input_str}
            structured = kwargs.get("inputs")
            if isinstance(structured, dict):
                args = structured
            self._emit(
                BeforeToolUsePayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_sequence(),
                    tool_name=str(tool_name),
                    args=args,
                )
            )

        def on_tool_end(
            self,
            output: str,
            *,
            run_id: Any | None = None,
            **kwargs: Any,
        ) -> None:
            rid = str(run_id or "")
            started = self._tool_start_ts.pop(rid, None)
            duration_ms = int((time.monotonic() - started) * 1000) if started else 0
            tool_name = kwargs.get("name") or "tool"
            self._emit(
                AfterToolUsePayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_sequence(),
                    tool_name=str(tool_name),
                    result=str(output)[:4000],
                    duration_ms=duration_ms,
                )
            )
            self._step_count += 1

        def on_tool_error(
            self, error: BaseException, **kwargs: Any
        ) -> None:
            self._emit_error(error)

        # ---------- agent decisions / final ----------

        def on_agent_action(self, action: Any, **kwargs: Any) -> None:
            self._emit(
                OnAgentDecisionPayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_sequence(),
                    decision_type="agent_action",
                    reasoning=_safe_str(getattr(action, "log", None)),
                    chosen_action=_safe_str(getattr(action, "tool", None)),
                )
            )

        def on_agent_finish(self, finish: Any, **kwargs: Any) -> None:
            text = _safe_str(
                getattr(finish, "return_values", {}).get("output")
                if isinstance(getattr(finish, "return_values", None), dict)
                else None
            ) or _safe_str(getattr(finish, "log", None))
            self._emit(
                OnFinalOutputPayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_sequence(),
                    final_response=text[:4000],
                    total_steps=self._step_count,
                )
            )
            self._emit(
                OnSessionEndPayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_sequence(),
                    total_duration_ms=0,
                    total_cost_usd=0.0,
                    success=True,
                )
            )
            self._session_started = False

        # ---------- error helper ----------

        def _emit_error(self, err: BaseException) -> None:
            self._emit(
                OnErrorPayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_sequence(),
                    error_type=type(err).__name__,
                    message=str(err)[:2000],
                )
            )

    return SaferCallbackHandler


def _safe_preview(obj: Any, limit: int = 500) -> dict[str, Any]:
    if isinstance(obj, dict):
        return {k: _safe_str(v, limit) for k, v in list(obj.items())[:10]}
    return {"value": _safe_str(obj, limit)}


def _safe_str(obj: Any, limit: int = 500) -> str:
    if obj is None:
        return ""
    s = str(obj)
    return s if len(s) <= limit else s[:limit] + "…"


class SaferCallbackHandler:  # type: ignore[no-redef]
    """Thin wrapper that constructs the real class on first instantiation.

    This is what users import. It checks for LangChain at call time,
    not import time, so `from safer.adapters.langchain import
    SaferCallbackHandler` never fails when LangChain is missing.
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        real = _make_handler_cls()
        return real(*args, **kwargs)


__all__ = ["SaferCallbackHandler"]
