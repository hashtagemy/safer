"""Tests for the workspace-scoped snapshot path (Phase 26).

Covers `resolve_workspace_root` (pyproject/package.json walk-up),
`walk_imports` (transitive AST walk, workspace bound, cycle handling,
parse-error tolerance), and `build_snapshot` dispatch across
scan_mode values.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from safer.snapshot import (
    build_snapshot,
    resolve_workspace_root,
    unpack_snapshot,
    walk_imports,
)


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ------------------------------------------------------------------
# Workspace root
# ------------------------------------------------------------------


def test_resolve_workspace_root_finds_pyproject(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\nname='x'\n")
    inner = tmp_path / "src" / "deep"
    _write(inner / "entry.py", "")
    assert resolve_workspace_root(caller_file=str(inner / "entry.py")) == tmp_path.resolve()


def test_resolve_workspace_root_finds_package_json(tmp_path: Path) -> None:
    _write(tmp_path / "package.json", "{}")
    inner = tmp_path / "nested"
    _write(inner / "entry.py", "")
    assert resolve_workspace_root(caller_file=str(inner / "entry.py")) == tmp_path.resolve()


def test_resolve_workspace_root_falls_back_to_caller_dir(tmp_path: Path) -> None:
    inner = tmp_path / "solo"
    _write(inner / "entry.py", "")
    # No pyproject anywhere under tmp_path; walk should stop at caller dir.
    # tmp_path itself has no markers. resolve should return the caller dir.
    result = resolve_workspace_root(caller_file=str(inner / "entry.py"))
    # Any dir without a marker is acceptable; what we guarantee is it's
    # not cwd and it's an ancestor of (or equal to) inner.
    assert inner.resolve() == result or inner.resolve().is_relative_to(result)


def test_resolve_workspace_root_prefers_explicit_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    env_dir = tmp_path / "from_env"
    env_dir.mkdir()
    monkeypatch.setenv("SAFER_PROJECT_ROOT", str(env_dir))

    assert resolve_workspace_root(explicit=explicit) == explicit.resolve()
    assert resolve_workspace_root() == env_dir.resolve()


# ------------------------------------------------------------------
# Import graph walk
# ------------------------------------------------------------------


def test_walk_imports_follows_transitive_chain(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\nname='x'\n")
    _write(
        tmp_path / "main.py",
        "from helpers import greet\ngreet()\n",
    )
    _write(
        tmp_path / "helpers.py",
        "from utils import ts\n\ndef greet():\n    return ts()\n",
    )
    _write(tmp_path / "utils.py", "def ts():\n    return 0\n")

    files, truncated = walk_imports(tmp_path / "main.py", tmp_path)
    paths = sorted(p for p, _ in files)
    assert paths == ["helpers.py", "main.py", "utils.py"]
    assert truncated is False


def test_walk_imports_pulls_in_package_init(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\nname='x'\n")
    _write(tmp_path / "main.py", "from agents.worker import run\nrun()\n")
    _write(tmp_path / "agents" / "__init__.py", "")
    _write(tmp_path / "agents" / "worker.py", "def run():\n    return 1\n")

    files, _ = walk_imports(tmp_path / "main.py", tmp_path)
    paths = sorted(p for p, _ in files)
    assert paths == ["agents/__init__.py", "agents/worker.py", "main.py"]


def test_walk_imports_skips_outside_workspace(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\nname='x'\n")
    # Simulate an import to a "site-packages" module by creating a
    # sibling dir outside the workspace. find_spec won't find it;
    # the walk should quietly skip.
    _write(tmp_path / "main.py", "import requests\nimport os\n")

    files, _ = walk_imports(tmp_path / "main.py", tmp_path)
    paths = [p for p, _ in files]
    assert "main.py" in paths
    # Stdlib / third-party paths never leak into the bundle.
    assert not any(p.startswith("/") for p in paths)
    assert not any("site-packages" in p for p in paths)


def test_walk_imports_handles_cycle(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\nname='x'\n")
    _write(tmp_path / "a.py", "import b\n")
    _write(tmp_path / "b.py", "import a\n")

    files, _ = walk_imports(tmp_path / "a.py", tmp_path)
    paths = sorted(p for p, _ in files)
    # Each file only once despite mutual imports.
    assert paths == ["a.py", "b.py"]


def test_walk_imports_tolerates_parse_error(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\nname='x'\n")
    _write(tmp_path / "main.py", "def :( invalid syntax\n")

    files, _ = walk_imports(tmp_path / "main.py", tmp_path)
    paths = [p for p, _ in files]
    # The broken file is still captured so the Inspector can surface the
    # parse error downstream; we just can't follow any imports from it.
    assert paths == ["main.py"]


# ------------------------------------------------------------------
# build_snapshot dispatch
# ------------------------------------------------------------------


def test_build_snapshot_default_uses_imports(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\nname='x'\n")
    _write(tmp_path / "main.py", "from helpers import x\n")
    _write(tmp_path / "helpers.py", "x = 1\n")
    _write(tmp_path / "unrelated.py", "print('noise')\n")

    result = build_snapshot(
        caller_file=str(tmp_path / "main.py"),
        project_root=str(tmp_path),
    )
    assert result.scan_mode_used == "imports"
    files = unpack_snapshot(result.b64)
    # `unrelated.py` is not in the import graph; it should not leak in.
    assert set(files.keys()) == {"main.py", "helpers.py"}


def test_build_snapshot_directory_mode_includes_everything(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\nname='x'\n")
    _write(tmp_path / "main.py", "from helpers import x\n")
    _write(tmp_path / "helpers.py", "x = 1\n")
    _write(tmp_path / "unrelated.py", "print('noise')\n")

    result = build_snapshot(
        caller_file=str(tmp_path / "main.py"),
        project_root=str(tmp_path),
        scan_mode="directory",
    )
    assert result.scan_mode_used == "directory"
    files = unpack_snapshot(result.b64)
    assert "unrelated.py" in files


def test_build_snapshot_include_glob_adds_non_py_files(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\nname='x'\n")
    _write(tmp_path / "main.py", "pass\n")
    _write(tmp_path / "prompts" / "system.md", "You are helpful.\n")

    result = build_snapshot(
        caller_file=str(tmp_path / "main.py"),
        project_root=str(tmp_path),
        include=("prompts/*.md",),
    )
    files = unpack_snapshot(result.b64)
    assert "prompts/system.md" in files


def test_build_snapshot_exclude_drops_paths(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\nname='x'\n")
    _write(tmp_path / "main.py", "from helpers import x\nimport legacy.old\n")
    _write(tmp_path / "helpers.py", "x = 1\n")
    _write(tmp_path / "legacy" / "__init__.py", "")
    _write(tmp_path / "legacy" / "old.py", "# deprecated\n")

    result = build_snapshot(
        caller_file=str(tmp_path / "main.py"),
        project_root=str(tmp_path),
        exclude=("legacy",),
    )
    files = unpack_snapshot(result.b64)
    assert not any(p.startswith("legacy") for p in files)


def test_build_snapshot_falls_back_to_directory_when_import_walk_is_empty(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\nname='x'\n")
    # No caller_file → import walk can't start → directory fallback.
    _write(tmp_path / "main.py", "x = 1\n")
    _write(tmp_path / "helpers.py", "y = 2\n")

    result = build_snapshot(project_root=str(tmp_path))
    assert result.scan_mode_used == "directory"
    files = unpack_snapshot(result.b64)
    assert "main.py" in files and "helpers.py" in files


def test_build_snapshot_explicit_mode_takes_only_includes(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\nname='x'\n")
    _write(tmp_path / "main.py", "from helpers import x\n")
    _write(tmp_path / "helpers.py", "x = 1\n")
    _write(tmp_path / "prompts" / "system.md", "You are helpful.\n")

    result = build_snapshot(
        caller_file=str(tmp_path / "main.py"),
        project_root=str(tmp_path),
        scan_mode="explicit",
        include=("prompts/*.md",),
    )
    assert result.scan_mode_used == "explicit"
    files = unpack_snapshot(result.b64)
    assert list(files) == ["prompts/system.md"]
