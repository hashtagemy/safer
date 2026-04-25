"""Project code snapshot — used by the `on_agent_register` hook.

Default behavior walks the agent's **import graph** starting from the
caller of `instrument()` and stays bounded inside the resolved
workspace (the nearest `pyproject.toml` / `package.json` ancestor). That
gives the Inspector exactly the files that execute as part of this
agent — no unrelated monorepo code, no third-party packages.

Power users can override via:
- `scan_mode="directory"` → recursive walk of the workspace root (the
  pre-26 behavior, still available as a fallback)
- `scan_mode="explicit"` → only patterns in `include`
- `include=[...glob...]` / `exclude=[...glob...]` — add or subtract
  paths; `include` also accepts non-`.py` extensions (e.g., prompt
  `.md` files)

The on-the-wire snapshot shape is `{file_path: source_text, ...}`
where `file_path` is relative to the workspace root and uses forward
slashes.
"""

from __future__ import annotations

import ast
import base64
import fnmatch
import gzip
import hashlib
import importlib.util
import json
import logging
import os
import subprocess
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
    "tests",
    "test",
)


ScanMode = Literal["imports", "directory", "explicit"]


@dataclass
class SnapshotResult:
    """Outcome of packing a project into an on-the-wire snapshot."""

    b64: str
    sha256: str
    file_count: int
    total_bytes: int
    project_root: str
    truncated: bool
    scan_mode_used: str = "directory"


_WORKSPACE_MARKERS: tuple[str, ...] = ("pyproject.toml", "package.json")


def resolve_workspace_root(
    explicit: str | os.PathLike[str] | None = None,
    env_var: str = "SAFER_PROJECT_ROOT",
    caller_file: str | os.PathLike[str] | None = None,
) -> Path:
    """Pick the narrowest sensible workspace root.

    Priority:
    1. `explicit` arg
    2. `env_var` (default `SAFER_PROJECT_ROOT`)
    3. Walk **up** from the caller file's directory until we find a
       `pyproject.toml` or `package.json`; return that directory.
    4. If no marker is found, return the caller's directory.
    5. Last resort: `cwd()`.

    This deliberately does NOT return the git toplevel — that pulls in
    unrelated monorepo code. A workspace marker is a much tighter
    bound for "this agent's code base".
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
        marker_dir = _nearest_workspace_marker(start)
        if marker_dir is not None:
            return marker_dir
        return start

    return Path.cwd().resolve()


# Backwards-compatible alias — older call sites import this name.
resolve_project_root = resolve_workspace_root


def _nearest_workspace_marker(start: Path) -> Path | None:
    """Walk up from `start` looking for a pyproject.toml / package.json."""
    current = start
    # Cap the walk so we don't pick something absurdly far up the tree.
    for _ in range(20):
        for marker in _WORKSPACE_MARKERS:
            if (current / marker).exists():
                return current
        if current.parent == current:
            return None
        current = current.parent
    return None


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
    scan_mode_used: str = "directory",
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
        scan_mode_used=scan_mode_used,
    )


def walk_imports(
    entry_file: Path,
    workspace_root: Path,
    extra_ignore_patterns: tuple[str, ...] = (),
    use_gitignore: bool = True,
) -> tuple[list[tuple[str, str]], bool]:
    """AST-based transitive import walk, bounded by the workspace root.

    Returns `(files, truncated)` using the same contract as
    `walk_python_files`. Every returned file lives under
    `workspace_root`; anything that resolves outside (site-packages,
    another checkout, stdlib) is skipped silently.

    Files that fail to parse are still included — the Inspector reports
    `parse_error` on them so the user sees the signal.
    """
    if not entry_file.exists():
        return [], False

    patterns = tuple(extra_ignore_patterns)
    if use_gitignore:
        patterns = patterns + tuple(_parse_gitignore(workspace_root))

    collected: list[tuple[str, str]] = []
    running = 0
    truncated = False
    visited: set[Path] = set()
    queue: deque[Path] = deque([entry_file.resolve()])
    workspace_resolved = workspace_root.resolve()

    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)

        # Bound to workspace root.
        try:
            rel_path = current.relative_to(workspace_resolved)
        except ValueError:
            continue  # outside the workspace — skip (site-packages / other repo)

        rel_posix = rel_path.as_posix()
        parts = rel_path.parts
        if _is_ignored(rel_posix, parts, patterns):
            continue
        try:
            size = current.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            log.info("snapshot: skipping %s (%d bytes > %d)", rel_posix, size, MAX_FILE_BYTES)
            truncated = True
            continue
        if running + size > MAX_TOTAL_BYTES:
            log.info(
                "snapshot: total size budget reached at %s (%d bytes so far)",
                rel_posix,
                running,
            )
            truncated = True
            continue

        try:
            text = current.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log.debug("snapshot: could not read %s: %s", rel_posix, e)
            continue

        collected.append((rel_posix, text))
        running += size

        # Expand imports (best-effort; syntax errors don't stop the walk).
        for dep in _resolve_imports_from_source(text, current, workspace_resolved):
            if dep not in visited:
                queue.append(dep)

    return collected, truncated


def _resolve_imports_from_source(
    source: str, source_file: Path, workspace_root: Path
) -> list[Path]:
    """Return the workspace-local files that `source` imports."""
    try:
        tree = ast.parse(source, filename=str(source_file))
    except SyntaxError:
        return []

    module_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    module_names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                module_names.append(node.module)
            elif node.level > 0:
                # Relative import — resolve against the source file's package.
                base_parts = list(source_file.relative_to(workspace_root).parent.parts)
                if node.level - 1 < len(base_parts):
                    anchor = base_parts[: len(base_parts) - (node.level - 1)]
                else:
                    anchor = []
                if node.module:
                    module_names.append(".".join([*anchor, node.module]))
                else:
                    # `from . import X` — each imported name is a submodule.
                    for alias in node.names:
                        module_names.append(".".join([*anchor, alias.name]))

    resolved: list[Path] = []
    for module in module_names:
        path = _find_workspace_module(module, workspace_root)
        if path is not None:
            resolved.append(path)
        # Also pull in the `__init__.py` of every ancestor package — those
        # run when Python imports a submodule and may contain their own
        # side-effectful imports that shape the agent's behavior.
        resolved.extend(_ancestor_package_inits(module, workspace_root))
    return resolved


def _ancestor_package_inits(module: str, workspace_root: Path) -> list[Path]:
    parts = module.split(".")
    paths: list[Path] = []
    # For `a.b.c` enqueue `a/__init__.py`, `a/b/__init__.py`.
    for i in range(1, len(parts)):
        prefix = parts[:i]
        init = workspace_root.joinpath(*prefix, "__init__.py")
        if init.exists() and init.is_file():
            paths.append(init.resolve())
    return paths


def _find_workspace_module(module: str, workspace_root: Path) -> Path | None:
    """Map a dotted module name to a `.py` file under the workspace root.

    Tries the workspace-root-relative layout first (most common:
    `my_pkg/foo.py` or `my_pkg/foo/__init__.py`), then falls back to
    `importlib.util.find_spec` but only accepts results that live
    inside the workspace — stdlib and site-packages hits are dropped.
    """
    parts = module.split(".")
    # Try workspace-root-relative layouts.
    candidates = [
        workspace_root.joinpath(*parts).with_suffix(".py"),
        workspace_root.joinpath(*parts, "__init__.py"),
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return c.resolve()

    # Fall back to importlib for packages installed in editable mode, but
    # only accept results that live under the workspace root.
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError, ModuleNotFoundError):
        return None
    if spec is None or not spec.origin:
        return None
    origin = Path(spec.origin).resolve()
    try:
        origin.relative_to(workspace_root.resolve())
    except ValueError:
        return None
    return origin


def walk_explicit_patterns(
    workspace_root: Path, include_globs: tuple[str, ...]
) -> tuple[list[tuple[str, str]], bool]:
    """Pick up files matching user-supplied glob patterns.

    Patterns are rooted at `workspace_root` and may reference any
    extension (e.g., `prompts/**/*.md`). Size limits are the same as
    the other walkers.
    """
    collected: list[tuple[str, str]] = []
    running = 0
    truncated = False
    seen: set[Path] = set()

    for pattern in include_globs:
        # Handle absolute paths (and absolute glob patterns) gracefully:
        # `Path.glob` rejects them on most Python versions ("Non-relative
        # patterns are unsupported"). For users running outside a
        # workspace — `instrument(include=[__file__])` from a third-
        # party project — we want this to Just Work, so we fall back to
        # treating the pattern as a literal filesystem path.
        pattern_path = Path(pattern)
        if pattern_path.is_absolute():
            matches: list[Path] = []
            if pattern_path.is_file():
                matches = [pattern_path]
            elif pattern_path.is_dir():
                matches = [p for p in pattern_path.rglob("*") if p.is_file()]
            else:
                # Try as an absolute glob — anchor to its drive root.
                try:
                    anchor = Path(pattern_path.anchor or "/")
                    rel_pat = pattern_path.relative_to(anchor).as_posix()
                    matches = [m for m in anchor.glob(rel_pat) if m.is_file()]
                except (ValueError, NotImplementedError):
                    matches = []
        else:
            matches = [m for m in workspace_root.glob(pattern) if m.is_file()]

        for match in matches:
            resolved = match.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                size = resolved.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                truncated = True
                continue
            if running + size > MAX_TOTAL_BYTES:
                truncated = True
                continue
            try:
                text = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                rel = resolved.relative_to(workspace_root).as_posix()
            except ValueError:
                # The file is outside workspace_root (only happens when
                # the user passed an absolute path that escapes the
                # detected workspace). Fall back to the file name so
                # the snapshot still records the content with a stable
                # identifier.
                rel = resolved.name
            collected.append((rel, text))
            running += size

    return collected, truncated


def build_snapshot(
    project_root: str | os.PathLike[str] | None = None,
    *,
    env_var: str = "SAFER_PROJECT_ROOT",
    caller_file: str | os.PathLike[str] | None = None,
    extra_ignore_patterns: tuple[str, ...] = (),
    use_gitignore: bool = True,
    scan_mode: ScanMode | None = None,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
) -> SnapshotResult:
    """Resolve workspace, pick a scan mode, walk, pack.

    `scan_mode` selects the strategy:
    - `"imports"` (default): transitive import graph walk from the caller
      file, bounded to the workspace root. Falls back to `"directory"`
      if the walk resolves to zero files (e.g., caller has a syntax
      error, or the entry point wasn't provided).
    - `"directory"`: recursive `.py` walk under the workspace root.
    - `"explicit"`: just the patterns in `include`.

    `include` patterns are appended on top of whichever strategy ran.
    `exclude` patterns are applied as extra ignore rules.
    """
    root = resolve_workspace_root(
        explicit=project_root, env_var=env_var, caller_file=caller_file
    )
    effective_excludes = tuple(extra_ignore_patterns) + tuple(exclude)
    effective_includes = tuple(include)
    entry = Path(caller_file).expanduser().resolve() if caller_file else None

    mode_used: str
    files: list[tuple[str, str]] = []
    truncated = False

    strategy = scan_mode or "imports"

    if strategy == "imports" and entry is not None and entry.exists():
        files, truncated = walk_imports(
            entry,
            root,
            extra_ignore_patterns=effective_excludes,
            use_gitignore=use_gitignore,
        )
        mode_used = "imports"
        if not files:
            log.info("snapshot: import walk returned 0 files, falling back to directory")
            files, truncated = walk_python_files(
                root,
                extra_ignore_patterns=effective_excludes,
                use_gitignore=use_gitignore,
            )
            mode_used = "directory"
    elif strategy == "explicit":
        mode_used = "explicit"
    else:
        files, truncated = walk_python_files(
            root,
            extra_ignore_patterns=effective_excludes,
            use_gitignore=use_gitignore,
        )
        mode_used = "directory"

    if effective_includes:
        extra_files, extra_truncated = walk_explicit_patterns(root, effective_includes)
        seen = {p for p, _ in files}
        for path, text in extra_files:
            if path not in seen:
                files.append((path, text))
        truncated = truncated or extra_truncated

    return pack_snapshot(
        files, project_root=root, truncated=truncated, scan_mode_used=mode_used
    )


def unpack_snapshot(b64: str) -> dict[str, str]:
    """Inverse of `pack_snapshot` — used in tests and by the backend."""
    raw = gzip.decompress(base64.b64decode(b64))
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("snapshot payload is not a dict")
    return {str(k): str(v) for k, v in data.items()}
