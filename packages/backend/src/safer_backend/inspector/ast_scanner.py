"""Deterministic AST scan of Python agent source.

Extracts structural facts that the Inspector's pattern rules and persona
review rely on:

- Tool definitions: functions carrying a decorator whose name contains
  "tool" (matches `@tool`, `@function_tool`, `@app.tool`, `@safer.tool`,
  `@anthropic_tool`, ...). Decorated class methods count too.
- LLM call sites: calls whose attribute chain references a known LLM
  provider (anthropic, openai, google.generativeai, bedrock) OR whose
  tail method is a standard entry point (`.messages.create`,
  `.chat.completions.create`, `.generate_content`, `.invoke`).
- Entry points: top-level `if __name__ == "__main__":` and public
  `main`/`run` functions at module level.
- Imports: module names imported by the script.

Everything here is pure Python — no Claude calls.
"""

from __future__ import annotations

import ast
from typing import Iterable

from ..models.inspector import ASTSummary, LLMCallSite, ToolSpec
from .tool_classifier import classify_tool


_LLM_MODULE_HINTS: tuple[str, ...] = (
    "anthropic",
    "openai",
    "google.generativeai",
    "genai",
    "bedrock",
    "litellm",
    "langchain",
    "langchain_anthropic",
    "langchain_openai",
)

_LLM_METHOD_HINTS: tuple[str, ...] = (
    "messages.create",
    "chat.completions.create",
    "completions.create",
    "generate_content",
    "invoke",
    "ainvoke",
    "stream",
    "astream",
)


def scan(source: str, *, module_name: str = "") -> ASTSummary:
    """Parse `source` and return deterministic structural facts.

    If parsing fails, returns a mostly-empty summary with `parse_error`
    populated so callers can surface the error without crashing.
    """
    loc = source.count("\n") + 1 if source else 0
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return ASTSummary(module=module_name, parse_error=f"SyntaxError: {e}", loc=loc)

    imports = sorted(set(_collect_imports(tree)))
    tools = list(_collect_tools(tree))
    llm_calls = list(_collect_llm_calls(tree, imports))
    entry_points = sorted(set(_collect_entry_points(tree)))

    return ASTSummary(
        module=module_name,
        tools=tools,
        llm_calls=llm_calls,
        entry_points=entry_points,
        imports=imports,
        loc=loc,
    )


def scan_project(files: list[tuple[str, str]]) -> ASTSummary:
    """Merge per-file AST summaries into one project-level summary.

    Every tool and llm_call_site is tagged with its originating
    `file_path` so the UI can link findings back to source locations.
    `parse_error` surfaces the first file that failed to parse (plus a
    count in the module name) so we don't lose the signal but also don't
    stop scanning the rest of the project.
    """
    if not files:
        return ASTSummary(module="")

    merged_tools: list[ToolSpec] = []
    merged_llm_calls: list[LLMCallSite] = []
    merged_entry_points: list[str] = []
    merged_imports: list[str] = []
    total_loc = 0
    parse_errors: list[str] = []

    for path, source in files:
        summary = scan(source, module_name=path)
        total_loc += summary.loc
        merged_imports.extend(summary.imports)
        for ep in summary.entry_points:
            merged_entry_points.append(f"{path}:{ep}")
        for tool in summary.tools:
            merged_tools.append(tool.model_copy(update={"file_path": path}))
        for call in summary.llm_calls:
            merged_llm_calls.append(call.model_copy(update={"file_path": path}))
        if summary.parse_error:
            parse_errors.append(f"{path}: {summary.parse_error}")

    return ASTSummary(
        module=f"project ({len(files)} files)",
        tools=merged_tools,
        llm_calls=merged_llm_calls,
        entry_points=sorted(set(merged_entry_points)),
        imports=sorted(set(merged_imports)),
        loc=total_loc,
        parse_error=("; ".join(parse_errors[:3]) + ("; ..." if len(parse_errors) > 3 else "")) if parse_errors else None,
    )


# ---------- imports ----------


def _collect_imports(tree: ast.AST) -> Iterable[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.module


# ---------- tools ----------


def _decorator_label(dec: ast.expr) -> str:
    """Render a decorator node as a dotted string for matching."""
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Attribute):
        base = _decorator_label(dec.value)
        return f"{base}.{dec.attr}" if base else dec.attr
    if isinstance(dec, ast.Call):
        return _decorator_label(dec.func)
    return ""


def _is_tool_decorator(label: str) -> bool:
    """Heuristic: any decorator whose label contains 'tool'."""
    if not label:
        return False
    lowered = label.lower()
    # Avoid matching common non-tool words that happen to contain "tool".
    if lowered.endswith(".toolbar") or "toolkit" in lowered:
        return False
    return "tool" in lowered


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    parts: list[str] = []
    args = node.args
    positional = list(args.args)
    for i, arg in enumerate(positional):
        rendered = arg.arg
        if arg.annotation is not None:
            rendered += f": {ast.unparse(arg.annotation)}"
        default_offset = len(positional) - len(args.defaults)
        if i >= default_offset:
            default = args.defaults[i - default_offset]
            rendered += f" = {ast.unparse(default)}"
        parts.append(rendered)
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    for kw in args.kwonlyargs:
        rendered = kw.arg
        if kw.annotation is not None:
            rendered += f": {ast.unparse(kw.annotation)}"
        parts.append(rendered)
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    return f"{node.name}({', '.join(parts)})"


def _collect_tools(tree: ast.AST) -> Iterable[ToolSpec]:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        labels = [_decorator_label(d) for d in node.decorator_list]
        matching = [label for label in labels if _is_tool_decorator(label)]
        if not matching:
            continue
        signature = _signature(node)
        docstring = ast.get_docstring(node) or None
        risk_class, reason = classify_tool(
            name=node.name, signature=signature, docstring=docstring
        )
        yield ToolSpec(
            name=node.name,
            signature=signature,
            docstring=docstring,
            decorators=labels,
            risk_class=risk_class,
            risk_reason=reason,
        )


# ---------- LLM call sites ----------


def _attr_chain(node: ast.expr) -> list[str]:
    """Flatten an attribute chain (left-to-right)."""
    parts: list[str] = []
    cur: ast.expr | None = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    parts.reverse()
    return parts


def _collect_llm_calls(
    tree: ast.AST, imports: list[str]
) -> Iterable[LLMCallSite]:
    has_anthropic = any(i.startswith("anthropic") or i.startswith("langchain_anthropic") for i in imports)
    has_openai = any(i.startswith("openai") or i.startswith("langchain_openai") for i in imports)
    has_google = any(i.startswith("google.generativeai") or i.startswith("genai") for i in imports)
    has_bedrock = any("bedrock" in i for i in imports)
    has_langchain = any(i.startswith("langchain") for i in imports)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        chain = _attr_chain(func) if isinstance(func, ast.Attribute) else []
        if not chain:
            continue
        dotted = ".".join(chain)
        tail2 = ".".join(chain[-2:]) if len(chain) >= 2 else dotted
        tail3 = ".".join(chain[-3:]) if len(chain) >= 3 else dotted

        method_match = any(
            tail == hint for tail in (tail2, tail3) for hint in _LLM_METHOD_HINTS
        )
        module_match = any(hint in dotted.lower() for hint in _LLM_MODULE_HINTS)

        if not (method_match or module_match):
            continue

        provider = _guess_provider(
            dotted=dotted,
            has_anthropic=has_anthropic,
            has_openai=has_openai,
            has_google=has_google,
            has_bedrock=has_bedrock,
            has_langchain=has_langchain,
        )
        yield LLMCallSite(
            provider=provider,
            function=dotted,
            line=getattr(node, "lineno", 0),
        )


def _guess_provider(
    *,
    dotted: str,
    has_anthropic: bool,
    has_openai: bool,
    has_google: bool,
    has_bedrock: bool,
    has_langchain: bool,
) -> str:
    lowered = dotted.lower()
    if "anthropic" in lowered or (has_anthropic and "messages.create" in lowered):
        return "anthropic"
    if "openai" in lowered or (has_openai and "chat.completions" in lowered):
        return "openai"
    if "google" in lowered or "genai" in lowered or "generate_content" in lowered:
        return "google"
    if "bedrock" in lowered or has_bedrock:
        return "bedrock"
    if has_langchain and ("invoke" in lowered or "stream" in lowered):
        return "langchain"
    return "unknown"


# ---------- entry points ----------


def _collect_entry_points(tree: ast.AST) -> Iterable[str]:
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if isinstance(node, ast.If) and _is_main_guard(node.test):
            yield "__main__"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in {"main", "run"} and not node.name.startswith("_"):
                yield node.name


def _is_main_guard(test: ast.expr) -> bool:
    """Match `__name__ == "__main__"` (either side)."""
    if not isinstance(test, ast.Compare):
        return False
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    left = test.left
    right = test.comparators[0]
    names = {_name_of(left), _name_of(right)}
    strings = {_str_of(left), _str_of(right)}
    return "__name__" in names and "__main__" in strings


def _name_of(node: ast.expr) -> str | None:
    return node.id if isinstance(node, ast.Name) else None


def _str_of(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None
