"""Project code snapshot — used by the `on_agent_register` hook.

Walks the agent's source tree, collects Python files under strict size
limits, gzips the bundle, and base64-encodes it so it can ride on a JSON
event. Honors `.gitignore` and a small default exclude list so we don't
ship `.venv/` or `node_modules/` with every register event.

The snapshot shape is `{file_path: source_text, ...}` where `file_path`
is relative to the resolved project root and uses forward slashes.
"""

from __future__ import annotations

import base64
import fnmatch
import gzip
import hashlib
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("safer.snapshot")

MAX_FILE_BYTES = 200 * 1024  # 200 KB per file
MAX_TOTAL_BYTES = 2 * 1024 * 1024  # 2 MB uncompressed total

DEFAULT_IGNORES: tuple[str, ...] = (
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".tox",
    ".eggs",
    "*.egg-info",
)


@dataclass
class SnapshotResult:
    """Outcome of packing a project into an on-the-wire snapshot."""

    b64: str
    sha256: str
    file_count: int
    total_bytes: int
    project_root: str
    truncated: bool


def resolve_project_root(
    explicit: str | os.PathLike[str] | None = None,
    env_var: str = "SAFER_PROJECT_ROOT",
    caller_file: str | os.PathLike[str] | None = None,
) -> Path:
    """Pick the most specific project root available.

    Priority: explicit arg → env var → git toplevel of caller's file → cwd.
    Returns an absolute, resolved `Path`.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()

    env_val = os.environ.get(env_var)
    if env_val:
        return Path(env_val).expanduser().resolve()

    start: Path | None = None
    if caller_file:
        start = Path(caller_file).expanduser().resolve().parent
    else:
        main = sys.modules.get("__main__")
        main_file = getattr(main, "__file__", None)
        if main_file:
            start = Path(main_file).expanduser().resolve().parent

    if start is not None:
        git_root = _git_toplevel(start)
        if git_root is not None:
            return git_root
        return start

    return Path.cwd().resolve()


def _git_toplevel(start: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    path = out.stdout.strip()
    if not path:
        return None
    return Path(path).resolve()


def _parse_gitignore(root: Path) -> list[str]:
    gi = root / ".gitignore"
    if not gi.exists():
        return []
    patterns: list[str] = []
    for raw in gi.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Negations and root-anchored rules are simplified: we drop the
        # leading "!" / "/" since we match by basename/relative glob.
        if line.startswith("!"):
            continue
        if line.startswith("/"):
            line = line[1:]
        patterns.append(line)
    return patterns


def _is_ignored(
    relative_posix: str,
    parts: tuple[str, ...],
    extra_patterns: tuple[str, ...],
) -> bool:
    for part in parts:
        for pat in DEFAULT_IGNORES:
            if fnmatch.fnmatch(part, pat):
                return True
    for pat in extra_patterns:
        if fnmatch.fnmatch(relative_posix, pat):
            return True
        if any(fnmatch.fnmatch(part, pat) for part in parts):
            return True
    return False


def walk_python_files(
    root: Path,
    extra_ignore_patterns: tuple[str, ...] = (),
    use_gitignore: bool = True,
) -> tuple[list[tuple[str, str]], bool]:
    """Walk `.py` files under `root` respecting size/ignore rules.

    Returns `(files, truncated)`. The `truncated` flag is set when either
    the total-bytes budget tripped or an oversized file was encountered —
    both cases mean the returned list is not the full picture.
    """
    patterns = tuple(extra_ignore_patterns)
    if use_gitignore:
        patterns = patterns + tuple(_parse_gitignore(root))

    collected: list[tuple[str, str]] = []
    running = 0
    truncated = False

    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        rel_dir = dp.relative_to(root)
        rel_dir_posix = rel_dir.as_posix() if str(rel_dir) != "." else ""

        # Prune ignored directories in-place to skip descent.
        pruned: list[str] = []
        for d in list(dirnames):
            child_rel = f"{rel_dir_posix}/{d}".lstrip("/")
            child_parts = tuple(child_rel.split("/")) if child_rel else (d,)
            if _is_ignored(child_rel, child_parts, patterns):
                pruned.append(d)
        for d in pruned:
            dirnames.remove(d)

        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            rel = f"{rel_dir_posix}/{fn}".lstrip("/")
            parts = tuple(rel.split("/"))
            if _is_ignored(rel, parts, patterns):
                continue
            full = dp / fn
            try:
                size = full.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                log.info("snapshot: skipping %s (%d bytes > %d)", rel, size, MAX_FILE_BYTES)
                truncated = True
                continue
            if running + size > MAX_TOTAL_BYTES:
                log.info(
                    "snapshot: total size budget reached at %s (%d bytes so far)",
                    rel,
                    running,
                )
                return collected, True
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                log.debug("snapshot: could not read %s: %s", rel, e)
                continue
            collected.append((rel, text))
            running += size

    return collected, truncated


def pack_snapshot(
    files: list[tuple[str, str]],
    *,
    project_root: Path,
    truncated: bool = False,
) -> SnapshotResult:
    """Serialize, gzip, base64 — return a `SnapshotResult` ready for the wire."""
    # Sort so the hash is deterministic.
    ordered = {path: source for path, source in sorted(files, key=lambda x: x[0])}
    raw_json = json.dumps(ordered, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    sha256 = hashlib.sha256(raw_json).hexdigest()
    # mtime=0 keeps gzip header stable so callers can diff encoded bytes too.
    gz = gzip.compress(raw_json, compresslevel=6, mtime=0)
    b64 = base64.b64encode(gz).decode("ascii")
    return SnapshotResult(
        b64=b64,
        sha256=sha256,
        file_count=len(ordered),
        total_bytes=sum(len(s.encode("utf-8")) for s in ordered.values()),
        project_root=str(project_root),
        truncated=truncated,
    )


def build_snapshot(
    project_root: str | os.PathLike[str] | None = None,
    *,
    env_var: str = "SAFER_PROJECT_ROOT",
    caller_file: str | os.PathLike[str] | None = None,
    extra_ignore_patterns: tuple[str, ...] = (),
    use_gitignore: bool = True,
) -> SnapshotResult:
    """One-call helper: resolve root, walk, pack."""
    root = resolve_project_root(
        explicit=project_root, env_var=env_var, caller_file=caller_file
    )
    files, truncated = walk_python_files(
        root,
        extra_ignore_patterns=extra_ignore_patterns,
        use_gitignore=use_gitignore,
    )
    return pack_snapshot(files, project_root=root, truncated=truncated)


def unpack_snapshot(b64: str) -> dict[str, str]:
    """Inverse of `pack_snapshot` — used in tests and by the backend."""
    raw = gzip.decompress(base64.b64decode(b64))
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("snapshot payload is not a dict")
    return {str(k): str(v) for k, v in data.items()}
