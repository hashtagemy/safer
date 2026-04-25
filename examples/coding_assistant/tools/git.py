"""Read-only git helpers for the coding-assistant demo."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from tools.filesystem import tool


def _git(args: list[str], cwd: str | None = None, timeout: int = 10) -> dict[str, Any]:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": f"git failed: {e}"}
    if out.returncode != 0:
        return {
            "ok": False,
            "error": (out.stderr or out.stdout or "").strip()
            or f"git exited {out.returncode}",
        }
    return {"ok": True, "stdout": out.stdout}


@tool
def git_diff(path: str | None = None) -> dict[str, Any]:
    """Return up to 200 lines of `git diff` (optionally scoped to a path)."""
    args = ["diff", "--no-color"]
    if path:
        args.extend(["--", path])
    r = _git(args)
    if not r.get("ok"):
        return r
    lines = r["stdout"].splitlines()
    truncated = len(lines) > 200
    return {
        "ok": True,
        "diff": "\n".join(lines[:200]),
        "truncated": truncated,
        "path": path or "(working tree)",
    }


@tool
def git_log(path: str | None = None, limit: int = 10) -> dict[str, Any]:
    """Return the most recent commits (one-line) optionally scoped to a path."""
    n = max(1, min(limit, 50))
    args = ["log", "--oneline", f"-n{n}"]
    if path:
        args.extend(["--", path])
    r = _git(args)
    if not r.get("ok"):
        return r
    return {
        "ok": True,
        "commits": r["stdout"].splitlines(),
        "path": path or "(repo)",
    }


@tool
def find_test_files(query: str, root: str = "tests") -> dict[str, Any]:
    """Find test files under `root` whose path or contents match `query`."""
    base = Path(root).expanduser()
    if not base.exists() or not base.is_dir():
        # Fall back to any directory matching `tests` under the cwd.
        candidates = [p for p in Path(".").rglob("tests") if p.is_dir()]
        if not candidates:
            return {"ok": False, "error": f"no `{root}` directory found"}
        base = candidates[0]

    matches: list[dict[str, Any]] = []
    for path in base.rglob("test_*.py"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if query in path.name or query in text:
            line_no = next(
                (i for i, line in enumerate(text.splitlines(), 1) if query in line),
                None,
            )
            matches.append(
                {
                    "path": str(path),
                    "line": line_no,
                }
            )
        if len(matches) >= 25:
            break
    return {
        "ok": True,
        "root": str(base),
        "query": query,
        "matches": matches,
        "count": len(matches),
    }
