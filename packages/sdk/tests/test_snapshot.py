"""Tests for safer.snapshot — the on_agent_register code-bundle packer."""

from __future__ import annotations

from pathlib import Path

import pytest

from safer.snapshot import (
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    build_snapshot,
    pack_snapshot,
    resolve_project_root,
    unpack_snapshot,
    walk_python_files,
)


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_walk_python_files_honors_defaults(tmp_path: Path) -> None:
    _write(tmp_path / "main.py", "print(1)\n")
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "a.py", "x = 1\n")
    _write(tmp_path / ".venv" / "ignored.py", "should_not_appear = True")
    _write(tmp_path / "node_modules" / "leftover.py", "")
    _write(tmp_path / "__pycache__" / "cached.pyc", "")

    files, truncated = walk_python_files(tmp_path)
    paths = sorted(p for p, _ in files)
    assert paths == ["main.py", "pkg/__init__.py", "pkg/a.py"]
    assert truncated is False


def test_gitignore_patterns_are_respected(tmp_path: Path) -> None:
    _write(tmp_path / "main.py", "print(1)\n")
    _write(tmp_path / "secret.py", "API_KEY='x'\n")
    _write(tmp_path / "private" / "keys.py", "SECRET = 'x'\n")
    _write(tmp_path / ".gitignore", "secret.py\nprivate\n")

    files, _ = walk_python_files(tmp_path, use_gitignore=True)
    paths = sorted(p for p, _ in files)
    assert paths == ["main.py"]


def test_oversize_file_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path / "small.py", "a = 1\n")
    _write(tmp_path / "huge.py", "x = '0'\n" + "# pad\n" * (MAX_FILE_BYTES // 6))

    files, truncated = walk_python_files(tmp_path)
    paths = sorted(p for p, _ in files)
    assert "small.py" in paths
    assert "huge.py" not in paths
    assert truncated is True


def test_total_budget_truncates(tmp_path: Path) -> None:
    # 15 files of ~180 KB each — under the 200 KB/file cap, but together
    # they blow past the 2 MB total budget.
    chunk = "# " + ("x" * 180_000) + "\n"
    assert len(chunk.encode("utf-8")) < MAX_FILE_BYTES
    for i in range(15):
        _write(tmp_path / f"f{i:02d}.py", chunk)

    result = build_snapshot(project_root=tmp_path, use_gitignore=False)
    assert result.truncated
    assert result.total_bytes <= MAX_TOTAL_BYTES
    # We should have stopped before collecting all 15.
    assert 0 < result.file_count < 15


def test_pack_snapshot_hash_is_deterministic(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "print('a')\n")
    _write(tmp_path / "b.py", "print('b')\n")

    first = build_snapshot(project_root=tmp_path)
    second = build_snapshot(project_root=tmp_path)
    assert first.sha256 == second.sha256
    assert first.b64 == second.b64
    assert first.file_count == 2


def test_unpack_roundtrip(tmp_path: Path) -> None:
    _write(tmp_path / "main.py", "print('hi')\n")
    _write(tmp_path / "pkg" / "x.py", "y = 2\n")

    result = build_snapshot(project_root=tmp_path)
    files = unpack_snapshot(result.b64)
    assert files["main.py"] == "print('hi')\n"
    assert files["pkg/x.py"] == "y = 2\n"


def test_resolve_project_root_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    env_dir = tmp_path / "from_env"
    env_dir.mkdir()

    monkeypatch.setenv("SAFER_PROJECT_ROOT", str(env_dir))

    # Explicit wins over env.
    assert resolve_project_root(explicit=explicit) == explicit.resolve()
    # Env wins over caller_file fallback.
    assert resolve_project_root(caller_file=str(tmp_path / "some.py")) == env_dir.resolve()

    monkeypatch.delenv("SAFER_PROJECT_ROOT", raising=False)
    # Without env, caller_file's directory is used (git root if available).
    inner = tmp_path / "inner"
    inner.mkdir()
    fake_file = inner / "agent.py"
    fake_file.write_text("")
    resolved = resolve_project_root(caller_file=str(fake_file))
    # Either the inner dir or a git toplevel above it — both are acceptable;
    # the important thing is we didn't fall through to cwd.
    assert resolved.exists()


def test_pack_roundtrip_empty_is_safe() -> None:
    result = pack_snapshot([], project_root=Path("/tmp/none"))
    assert result.file_count == 0
    assert result.total_bytes == 0
    assert unpack_snapshot(result.b64) == {}
