"""Safe-ish filesystem helpers for the coding-assistant demo."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def tool(fn):
    """Identity decorator — lets the Inspector AST scanner detect tools.

    The scanner matches any decorator whose name contains "tool". We
    keep our own minimal version so the demo doesn't drag in a framework
    just to get the attribute.
    """
    fn._is_tool = True  # noqa: SLF001
    return fn


@tool
def read_file(path: str) -> dict[str, Any]:
    """Read a UTF-8 text file from disk. Truncates to 8 KB."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": f"not a file: {path}"}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "error": str(e)}
    truncated = len(text) > 8192
    return {"ok": True, "content": text[:8192], "truncated": truncated, "path": str(p)}


@tool
def write_file(path: str, content: str) -> dict[str, Any]:
    """Overwrite a file with the given content. Creates parent dirs."""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(content, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "path": str(p), "bytes": len(content.encode("utf-8"))}


@tool
def grep_code(pattern: str, path: str) -> dict[str, Any]:
    """Find lines in a file matching a regex. Returns up to 50 hits."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": f"not a file: {path}"}
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return {"ok": False, "error": f"bad regex: {e}"}
    hits: list[dict[str, Any]] = []
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if rx.search(line):
            hits.append({"line": i, "text": line[:200]})
            if len(hits) >= 50:
                break
    return {"ok": True, "path": str(p), "hits": hits, "count": len(hits)}
