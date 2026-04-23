"""Multi-file Inspector tests — covers scan_project, inspect_project,
and the `/v1/agents/{id}/scan` end-to-end flow with a fake Claude client."""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from safer_backend.inspector.ast_scanner import scan_project
from safer_backend.inspector.orchestrator import inspect_project
from safer_backend.inspector.pattern_rules import scan_patterns_project
from safer_backend.judge.engine import set_client
from safer_backend.storage.db import init_db_sync


# ---------- shared fixtures ----------


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [SimpleNamespace(type="text", text=text)]
        self.usage = SimpleNamespace(
            input_tokens=2000,
            output_tokens=500,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=2000,
        )


class _FakeAnthropic:
    def __init__(self, canned: str):
        self.calls: list[dict] = []
        self._canned = canned

        async def _create(**kwargs):
            self.calls.append(kwargs)
            return _FakeResponse(self._canned)

        self.messages = SimpleNamespace(create=_create)


@pytest.fixture
def reset_judge_client():
    set_client(None)
    yield
    set_client(None)


def _canned_verdict_json() -> str:
    return json.dumps(
        {
            "personas": {
                "security_auditor": {
                    "score": 30,
                    "flags": ["shell_injection"],
                    "reasoning": "Shell is invoked with untrusted input",
                    "evidence": ["main.py:subprocess.run(cmd, shell=True)"],
                    "recommended_mitigation": "Use argv list, no shell=True",
                },
                "compliance_officer": {
                    "score": 70,
                    "flags": [],
                    "reasoning": "No direct PII leakage paths",
                    "evidence": [],
                },
                "policy_warden": {
                    "score": 60,
                    "flags": [],
                    "reasoning": "No active policies conflict",
                    "evidence": [],
                },
            },
            "overall": {
                "risk": "HIGH",
                "confidence": 0.82,
                "block": False,
                "reason": "shell injection surface",
            },
        }
    )


# ---------- unit-level multi-file scanners ----------


def test_scan_project_merges_files_and_tags_paths():
    files = [
        (
            "main.py",
            "import subprocess\n"
            "@tool\n"
            "def shell(cmd):\n"
            "    return subprocess.run(cmd, shell=True)\n"
            "if __name__ == '__main__':\n"
            "    pass\n",
        ),
        (
            "tools/email.py",
            "@tool\ndef send(to, body):\n    return {'sent': True}\n",
        ),
    ]
    summary = scan_project(files)
    tool_paths = {(t.name, t.file_path) for t in summary.tools}
    assert ("shell", "main.py") in tool_paths
    assert ("send", "tools/email.py") in tool_paths
    assert "main.py:__main__" in summary.entry_points


def test_scan_patterns_project_tags_file_path():
    files = [
        (
            "main.py",
            'API = "sk-ant-fake-for-test-xxxxxxxxxxxxxxxxxxx"\n'
            "import subprocess\n"
            "subprocess.run('ls', shell=True)\n",
        ),
        ("clean.py", "x = 1\n"),
    ]
    matches = scan_patterns_project(files)
    rule_paths = {(m.rule_id, m.file_path) for m in matches}
    assert ("hardcoded_credential", "main.py") in rule_paths
    assert any(m.rule_id == "shell_injection" and m.file_path == "main.py" for m in matches)
    assert all(m.file_path == "main.py" for m in matches)


def test_inspect_project_without_persona_review(reset_judge_client):
    files = [
        (
            "main.py",
            'API = "sk-ant-test-xxxxxxxxxxxxxxxxxxxxxxxx"\n'
            "import subprocess\n"
            "subprocess.run('ls', shell=True)\n",
        ),
    ]
    report = asyncio.run(
        inspect_project(
            agent_id="agent_unit",
            files=files,
            skip_persona_review=True,
        )
    )
    assert report.scan_mode == "project"
    assert report.scanned_files == ["main.py"]
    assert report.persona_review_skipped is True
    file_paths = {f.file_path for f in report.findings}
    assert file_paths == {"main.py"}
    assert report.risk_level.value in {"HIGH", "CRITICAL"}


def test_inspect_project_with_fake_persona_review(monkeypatch, reset_judge_client):
    fake = _FakeAnthropic(_canned_verdict_json())
    set_client(fake)  # type: ignore[arg-type]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-token")

    files = [
        (
            "main.py",
            "import subprocess\n"
            "def shell(cmd):\n"
            "    subprocess.run(cmd, shell=True)\n",
        ),
    ]
    report = asyncio.run(
        inspect_project(agent_id="agent_unit_fake", files=files)
    )
    assert report.scan_mode == "project"
    assert report.persona_review_skipped is False
    persona_names = {p.value for p in report.persona_verdicts.keys()}
    assert {"security_auditor", "compliance_officer", "policy_warden"} <= persona_names
    # Exactly one Opus call — prompt cache friendly.
    assert len(fake.calls) == 1


# ---------- end-to-end /v1/agents/{id}/scan ----------


@pytest.fixture()
def app_client(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        monkeypatch.setenv("SAFER_DB_PATH", db_path)
        init_db_sync(db_path)
        import importlib

        import safer_backend.main as main_mod
        import safer_backend.storage.db as db_mod

        importlib.reload(db_mod)
        importlib.reload(main_mod)
        with TestClient(main_mod.app) as client:
            yield client


def _build_snapshot_b64(files: dict[str, str]) -> tuple[str, str, int]:
    ordered = {k: files[k] for k in sorted(files)}
    raw = json.dumps(ordered, separators=(",", ":")).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    gz = gzip.compress(raw, compresslevel=6, mtime=0)
    total = sum(len(v.encode("utf-8")) for v in ordered.values())
    return base64.b64encode(gz).decode("ascii"), sha, total


def _register(app_client: TestClient, agent_id: str, files: dict[str, str]) -> None:
    b64, sha, total = _build_snapshot_b64(files)
    evt = {
        "session_id": f"boot_{agent_id}",
        "agent_id": agent_id,
        "sequence": 0,
        "hook": "on_agent_register",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "risk_hint": "LOW",
        "source": "sdk",
        "agent_name": f"Agent {agent_id}",
        "framework": "anthropic",
        "system_prompt": None,
        "project_root": "/tmp",
        "code_snapshot_b64": b64,
        "code_snapshot_hash": sha,
        "file_count": len(files),
        "total_bytes": total,
        "snapshot_truncated": False,
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    r = app_client.post("/v1/events", json={"events": [evt]})
    assert r.status_code == 200


def test_scan_endpoint_runs_project_scan_and_persists(app_client: TestClient) -> None:
    files = {
        "main.py": (
            'API = "sk-ant-test-xxxxxxxxxxxxxxxxxxxxxxxx"\n'
            "import subprocess\n"
            "subprocess.run('ls', shell=True)\n"
        ),
        "tools/email.py": "@tool\ndef send(to, body):\n    return {'sent': True}\n",
    }
    _register(app_client, "agent_scan_e2e", files)

    r = app_client.post(
        "/v1/agents/agent_scan_e2e/scan", json={"skip_persona_review": True}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["scan_mode"] == "project"
    assert set(body["scanned_files"]) == {"main.py", "tools/email.py"}
    assert body["persona_review_skipped"] is True
    assert any(
        f["flag"] == "credential_hardcoded" and f["file_path"] == "main.py"
        for f in body["findings"]
    )

    # latest_scan_id on the agent got updated
    rec = app_client.get("/v1/agents/agent_scan_e2e").json()
    assert rec["latest_scan_id"] == body["report_id"]

    # GET returns the same report
    g = app_client.get("/v1/agents/agent_scan_e2e/scan")
    assert g.status_code == 200
    assert g.json()["report_id"] == body["report_id"]


def test_scan_endpoint_without_snapshot_returns_409(app_client: TestClient) -> None:
    # Insert an agent row by hand with no blob.
    import asyncio as _asyncio

    from safer_backend.storage.dao import ingest_agent_register

    _asyncio.run(
        ingest_agent_register(
            agent_id="agent_no_blob",
            agent_name="No Blob",
            framework="custom",
            version=None,
            system_prompt=None,
            project_root=None,
            code_snapshot_b64="",  # empty → no blob stored
            code_snapshot_hash="",
            file_count=0,
            total_bytes=0,
            truncated=False,
            registered_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    r = app_client.post(
        "/v1/agents/agent_no_blob/scan", json={"skip_persona_review": True}
    )
    assert r.status_code == 409


def test_get_scan_before_any_run_returns_404(app_client: TestClient) -> None:
    _register(app_client, "agent_empty_scan", {"main.py": "x = 1\n"})
    r = app_client.get("/v1/agents/agent_empty_scan/scan")
    assert r.status_code == 404
