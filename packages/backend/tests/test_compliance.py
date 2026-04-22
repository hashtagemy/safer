"""Compliance Pack tests — loader, renderers, API endpoint."""

from __future__ import annotations

import importlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


# ---------- fixtures ----------


@pytest.fixture()
def tmp_db(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "compliance.db")
        monkeypatch.setenv("SAFER_DB_PATH", db_path)

        from safer_backend.storage.db import init_db_sync

        init_db_sync(db_path)

        import safer_backend.storage.db as db_mod
        import safer_backend.storage.dao as dao_mod
        import safer_backend.compliance.data as data_mod
        import safer_backend.compliance.renderer as render_mod
        import safer_backend.compliance.api as api_mod
        import safer_backend.main as main_mod

        for m in (db_mod, dao_mod, data_mod, render_mod, api_mod, main_mod):
            importlib.reload(m)
        yield data_mod, render_mod, api_mod, dao_mod, main_mod


# ---------- seed helpers ----------


async def _seed(dao_mod, *, agent="agent_demo", with_findings=True):
    await dao_mod.upsert_agent(agent, name="Demo Agent")
    now = datetime.now(timezone.utc)
    session_id = f"sess_{uuid4().hex[:10]}"
    async with dao_mod.get_db() as db:
        await db.execute(
            """
            INSERT INTO sessions
            (session_id, agent_id, started_at, ended_at, total_steps, success, total_cost_usd)
            VALUES (?, ?, ?, ?, 3, 1, 0.02)
            """,
            (session_id, agent, now.isoformat(), (now + timedelta(seconds=5)).isoformat()),
        )
        if with_findings:
            for sev, flag in [
                ("CRITICAL", "pii_sent_external"),
                ("HIGH", "prompt_injection_direct"),
                ("MEDIUM", "loop_detected"),
            ]:
                await db.execute(
                    """
                    INSERT INTO findings
                    (finding_id, agent_id, session_id, source, severity, category,
                     flag, title, description, evidence_json, reproduction_steps_json,
                     owasp_id, created_at)
                    VALUES (?, ?, ?, 'judge', ?, 'COMPLIANCE', ?, ?, ?, '[]', '[]', ?, ?)
                    """,
                    (
                        f"fnd_{uuid4().hex[:10]}",
                        agent,
                        session_id,
                        sev,
                        flag,
                        f"{flag} demo",
                        f"Synthetic {sev} finding for {flag}",
                        "owasp_llm01_prompt_injection"
                        if flag == "prompt_injection_direct"
                        else None,
                        now.isoformat(),
                    ),
                )

            # One HIGH verdict with overall_block=True for SOC2 coverage.
            event_id = f"evt_{uuid4().hex[:10]}"
            await db.execute(
                """
                INSERT INTO events
                (event_id, session_id, agent_id, sequence, hook, timestamp, risk_hint, payload_json)
                VALUES (?, ?, ?, 0, 'before_tool_use', ?, 'HIGH', '{}')
                """,
                (event_id, session_id, agent, now.isoformat()),
            )
            await db.execute(
                """
                INSERT INTO verdicts
                (verdict_id, event_id, session_id, agent_id, timestamp, mode,
                 overall_risk, overall_confidence, overall_block, active_personas,
                 personas_json, latency_ms, tokens_in, tokens_out, cache_read_tokens, cost_usd)
                VALUES (?, ?, ?, ?, ?, 'RUNTIME', 'HIGH', 0.9, 1, '["security_auditor"]',
                        ?, 0, 0, 0, 0, 0.0)
                """,
                (
                    f"vdt_{uuid4().hex[:10]}",
                    event_id,
                    session_id,
                    agent,
                    now.isoformat(),
                    json.dumps(
                        {
                            "security_auditor": {
                                "persona": "security_auditor",
                                "score": 30,
                                "confidence": 0.9,
                                "flags": ["shell_injection", "credential_leak"],
                                "evidence": [],
                                "reasoning": "",
                            }
                        }
                    ),
                ),
            )
        await db.commit()
    return session_id


# ---------- loader ----------


@pytest.mark.asyncio
async def test_loader_aggregates_counts(tmp_db):
    data_mod, _r, _api, dao, _main = tmp_db
    await _seed(dao)

    now = datetime.now(timezone.utc)
    data = await data_mod.load_range(
        start=now - timedelta(hours=1),
        end=now + timedelta(hours=1),
        standard=data_mod.Standard.GDPR,
    )
    assert data.total_agents == 1
    assert data.total_sessions == 1
    assert data.total_findings == 3
    assert data.findings_by_severity["CRITICAL"] == 1
    assert data.findings_by_severity["HIGH"] == 1
    assert data.findings_by_severity["MEDIUM"] == 1
    # OWASP counts derive from both direct owasp_ids and flag-mapping.
    assert data.owasp_counts["owasp_llm01_prompt_injection"] >= 1
    assert data.owasp_counts["owasp_llm07_insecure_plugin_design"] >= 1  # from shell_injection
    assert data.owasp_counts["owasp_llm06_sensitive_info_disclosure"] >= 1  # pii_sent_external + credential_leak


@pytest.mark.asyncio
async def test_loader_empty_range(tmp_db):
    data_mod, _r, _api, dao, _main = tmp_db
    now = datetime.now(timezone.utc)
    data = await data_mod.load_range(
        start=now - timedelta(days=30),
        end=now - timedelta(days=29),
        standard=data_mod.Standard.SOC2,
    )
    assert data.total_sessions == 0
    assert data.total_findings == 0


@pytest.mark.asyncio
async def test_loader_rejects_inverted_range(tmp_db):
    data_mod, _r, _api, dao, _main = tmp_db
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        await data_mod.load_range(
            start=now,
            end=now - timedelta(days=1),
            standard=data_mod.Standard.GDPR,
        )


# ---------- renderers ----------


@pytest.mark.asyncio
async def test_render_html_per_standard(tmp_db):
    data_mod, render_mod, _api, dao, _main = tmp_db
    await _seed(dao)
    now = datetime.now(timezone.utc)

    for std, title_fragment in [
        (data_mod.Standard.GDPR, "GDPR"),
        (data_mod.Standard.SOC2, "SOC 2"),
        (data_mod.Standard.OWASP_LLM, "OWASP LLM"),
    ]:
        data = await data_mod.load_range(
            start=now - timedelta(hours=1),
            end=now + timedelta(hours=1),
            standard=std,
        )
        html = render_mod.render_html(data)
        assert "<!DOCTYPE html>" in html
        assert title_fragment in html
        # KPI card for agents is always rendered in the base layout.
        assert "Agents" in html


@pytest.mark.asyncio
async def test_render_json_lossless(tmp_db):
    data_mod, render_mod, _api, dao, _main = tmp_db
    await _seed(dao)
    now = datetime.now(timezone.utc)
    data = await data_mod.load_range(
        start=now - timedelta(hours=1),
        end=now + timedelta(hours=1),
        standard=data_mod.Standard.OWASP_LLM,
    )
    payload = render_mod.render_json(data)
    assert payload["total_findings"] == 3
    assert payload["standard"] == "owasp_llm"
    # Contains every OWASP row as a key.
    assert "owasp_llm01_prompt_injection" in payload["owasp_counts"]


@pytest.mark.asyncio
async def test_render_pdf_produces_pdf_bytes(tmp_db):
    data_mod, render_mod, _api, dao, _main = tmp_db
    await _seed(dao)
    now = datetime.now(timezone.utc)
    data = await data_mod.load_range(
        start=now - timedelta(hours=1),
        end=now + timedelta(hours=1),
        standard=data_mod.Standard.GDPR,
    )
    try:
        pdf = render_mod.render_pdf(data)
    except RuntimeError as e:
        if str(e) == "weasyprint_unavailable":
            pytest.skip("WeasyPrint native libs not available in this env")
        raise
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1000


# ---------- API ----------


def test_api_build_html(tmp_db):
    _d, _r, _api, dao, main_mod = tmp_db
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_seed(dao))
    finally:
        loop.close()

    client = TestClient(main_mod.app)
    with client:
        resp = client.post(
            "/v1/reports/build",
            json={
                "standard": "gdpr",
                "start_date": (datetime.utcnow() - timedelta(hours=1)).date().isoformat(),
                "end_date": (datetime.utcnow() + timedelta(hours=1)).date().isoformat(),
                "format": "html",
            },
        )
    assert resp.status_code == 200
    assert "<!DOCTYPE html>" in resp.text
    assert "GDPR" in resp.text
    assert "filename=" in (resp.headers.get("content-disposition") or "")


def test_api_build_json(tmp_db):
    _d, _r, _api, dao, main_mod = tmp_db
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_seed(dao))
    finally:
        loop.close()

    client = TestClient(main_mod.app)
    with client:
        resp = client.post(
            "/v1/reports/build",
            json={
                "standard": "soc2",
                "start_date": (datetime.utcnow() - timedelta(hours=1)).date().isoformat(),
                "end_date": (datetime.utcnow() + timedelta(hours=1)).date().isoformat(),
                "format": "json",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["standard"] == "soc2"
    assert body["total_findings"] >= 0


def test_api_build_pdf_or_501(tmp_db):
    _d, _r, _api, dao, main_mod = tmp_db
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_seed(dao))
    finally:
        loop.close()

    client = TestClient(main_mod.app)
    with client:
        resp = client.post(
            "/v1/reports/build",
            json={
                "standard": "owasp_llm",
                "start_date": (datetime.utcnow() - timedelta(hours=1)).date().isoformat(),
                "end_date": (datetime.utcnow() + timedelta(hours=1)).date().isoformat(),
                "format": "pdf",
            },
        )
    # Either WeasyPrint is installed and we get a PDF, or we get a clean 501.
    if resp.status_code == 200:
        assert resp.content.startswith(b"%PDF-")
        assert resp.headers["content-type"] == "application/pdf"
    else:
        assert resp.status_code == 501


def test_api_rejects_inverted_range(tmp_db):
    _d, _r, _api, _dao, main_mod = tmp_db
    client = TestClient(main_mod.app)
    with client:
        resp = client.post(
            "/v1/reports/build",
            json={
                "standard": "gdpr",
                "start_date": "2026-04-20",
                "end_date": "2026-04-10",
                "format": "html",
            },
        )
    assert resp.status_code == 400
