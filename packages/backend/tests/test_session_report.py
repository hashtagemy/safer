"""Session Report tests — aggregator + orchestrator + API.

The aggregator is the heart of Phase 11 (deterministic Python; zero
Claude calls). Most of this file lives there. The orchestrator and API
tests use a fake Anthropic client where needed.
"""

from __future__ import annotations

import importlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


# ---------- fixtures ----------


@pytest.fixture(autouse=True)
def _reset_claude_clients():
    """Every Claude-powered component is singleton-based; reset per test."""
    from safer_backend.quality import reviewer as q_mod
    from safer_backend.reconstructor import chain as r_mod

    q_mod.set_client(None)
    r_mod.set_client(None)
    yield
    q_mod.set_client(None)
    r_mod.set_client(None)


@pytest.fixture()
def tmp_db(monkeypatch):
    """Fresh DB for every test; modules reloaded so env vars take effect."""
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "session_report.db")
        monkeypatch.setenv("SAFER_DB_PATH", db_path)

        import safer_backend.storage.db as db_mod

        importlib.reload(db_mod)
        from safer_backend.storage.db import init_db_sync

        init_db_sync(db_path)

        import safer_backend.storage.dao as dao_mod

        importlib.reload(dao_mod)

        import safer_backend.session_report.aggregator as agg_mod

        importlib.reload(agg_mod)
        import safer_backend.session_report.orchestrator as orch_mod

        importlib.reload(orch_mod)
        yield agg_mod, orch_mod, dao_mod


async def _seed_session(
    dao_mod,
    *,
    session_id: str = "sess_test",
    agent_id: str = "agent_test",
    agent_name: str = "Test Agent",
    total_steps: int = 5,
    duration_ms: int = 5_000,
) -> tuple[str, str]:
    await dao_mod.upsert_agent(agent_id, name=agent_name)
    started = datetime.now(timezone.utc) - timedelta(milliseconds=duration_ms)
    ended = started + timedelta(milliseconds=duration_ms)
    async with dao_mod.get_db() as db:
        await db.execute(
            """
            INSERT INTO sessions
            (session_id, agent_id, started_at, ended_at, total_steps, success)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (
                session_id,
                agent_id,
                started.isoformat(),
                ended.isoformat(),
                total_steps,
            ),
        )
        await db.commit()
    return session_id, agent_id


async def _insert_verdict(
    dao_mod,
    *,
    session_id: str,
    agent_id: str,
    overall_risk: str,
    personas: dict[str, dict],
    block: bool = False,
) -> None:
    verdict_id = f"vdt_{uuid4().hex[:12]}"
    event_id = f"evt_{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    async with dao_mod.get_db() as db:
        # Insert a fake event row to satisfy the FK on verdicts.
        await db.execute(
            """
            INSERT INTO events
            (event_id, session_id, agent_id, sequence, hook, timestamp, risk_hint, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, session_id, agent_id, 0, "before_tool_use", now, overall_risk, "{}"),
        )
        await db.execute(
            """
            INSERT INTO verdicts
            (verdict_id, event_id, session_id, agent_id, timestamp, mode,
             overall_risk, overall_confidence, overall_block, active_personas,
             personas_json, latency_ms, tokens_in, tokens_out, cache_read_tokens, cost_usd)
            VALUES (?, ?, ?, ?, ?, 'RUNTIME', ?, 0.9, ?, ?, ?, 0, 0, 0, 0, 0.0)
            """,
            (
                verdict_id,
                event_id,
                session_id,
                agent_id,
                now,
                overall_risk,
                1 if block else 0,
                json.dumps(list(personas.keys())),
                json.dumps(personas),
            ),
        )
        await db.commit()


async def _insert_cost(
    dao_mod,
    *,
    session_id: str,
    agent_id: str,
    model: str,
    cost_usd: float,
    tokens_in: int = 1000,
    tokens_out: int = 400,
) -> None:
    async with dao_mod.get_db() as db:
        await db.execute(
            """
            INSERT INTO claude_calls
            (call_id, timestamp, component, model, tokens_in, tokens_out,
             cache_read_tokens, cache_write_tokens, cost_usd, latency_ms,
             agent_id, session_id)
            VALUES (?, ?, 'judge', ?, ?, ?, 0, 0, ?, 0, ?, ?)
            """,
            (
                f"call_{uuid4().hex[:10]}",
                datetime.now(timezone.utc).isoformat(),
                model,
                tokens_in,
                tokens_out,
                cost_usd,
                agent_id,
                session_id,
            ),
        )
        await db.commit()


async def _insert_finding(
    dao_mod,
    *,
    session_id: str,
    agent_id: str,
    severity: str,
    flag: str,
    title: str,
) -> None:
    async with dao_mod.get_db() as db:
        await db.execute(
            """
            INSERT INTO findings
            (finding_id, agent_id, session_id, source, severity, category,
             flag, title, description, created_at)
            VALUES (?, ?, ?, 'judge', ?, 'SECURITY', ?, ?, ?, ?)
            """,
            (
                f"fnd_{uuid4().hex[:10]}",
                agent_id,
                session_id,
                severity,
                flag,
                title,
                title,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()


# ---------- aggregator ----------


@pytest.mark.asyncio
async def test_aggregator_clean_session_is_100(tmp_db):
    agg, _orch, dao = tmp_db
    await _seed_session(dao)
    report = await agg.aggregate("sess_test")
    assert report.overall_health == 100
    for c in report.categories:
        assert c.value == 100


@pytest.mark.asyncio
async def test_aggregator_single_critical_verdict(tmp_db):
    agg, _, dao = tmp_db
    sess, agent = await _seed_session(dao)
    await _insert_verdict(
        dao,
        session_id=sess,
        agent_id=agent,
        overall_risk="CRITICAL",
        personas={
            "security_auditor": {
                "persona": "security_auditor",
                "score": 20,
                "confidence": 0.9,
                "flags": ["credential_leak", "shell_injection"],
                "evidence": ["sk-ant-..."],
                "reasoning": "Hard-coded creds + shell exec on user input.",
            }
        },
        block=True,
    )
    report = await agg.aggregate(sess)
    # Security category receives two CRITICAL penalties (-35 each) → clamped.
    sec = next(c for c in report.categories if c.name == "security")
    assert sec.value <= 30
    assert sec.flag_count_by_severity["CRITICAL"] == 2
    # Other categories stay clean.
    comp = next(c for c in report.categories if c.name == "compliance")
    assert comp.value == 100


@pytest.mark.asyncio
async def test_aggregator_multi_mixed_severity(tmp_db):
    agg, _, dao = tmp_db
    sess, agent = await _seed_session(dao)
    # One CRITICAL security flag and one MEDIUM compliance flag.
    await _insert_verdict(
        dao,
        session_id=sess,
        agent_id=agent,
        overall_risk="CRITICAL",
        personas={
            "security_auditor": {
                "persona": "security_auditor",
                "score": 25,
                "confidence": 0.9,
                "flags": ["shell_injection"],
                "evidence": [],
                "reasoning": "",
            },
            "compliance_officer": {
                "persona": "compliance_officer",
                "score": 70,
                "confidence": 0.8,
                "flags": ["pii_logged"],
                "evidence": [],
                "reasoning": "",
            },
        },
    )
    report = await agg.aggregate(sess)
    sec = next(c for c in report.categories if c.name == "security")
    comp = next(c for c in report.categories if c.name == "compliance")
    assert sec.value == 100 - 35  # one CRITICAL
    assert comp.value == 100 - 8  # one MEDIUM
    assert report.overall_health < 100


@pytest.mark.asyncio
async def test_aggregator_owasp_map_from_flags(tmp_db):
    agg, _, dao = tmp_db
    sess, agent = await _seed_session(dao)
    await _insert_verdict(
        dao,
        session_id=sess,
        agent_id=agent,
        overall_risk="HIGH",
        personas={
            "security_auditor": {
                "persona": "security_auditor",
                "score": 40,
                "confidence": 0.9,
                "flags": [
                    "prompt_injection_direct",
                    "credential_leak",
                    "shell_injection",
                ],
                "evidence": [],
                "reasoning": "",
            }
        },
    )
    report = await agg.aggregate(sess)
    assert report.owasp_map.get("owasp_llm01_prompt_injection") == 1
    assert report.owasp_map.get("owasp_llm06_sensitive_info_disclosure") == 1
    assert report.owasp_map.get("owasp_llm07_insecure_plugin_design") == 1


@pytest.mark.asyncio
async def test_aggregator_direct_owasp_flag_counted(tmp_db):
    agg, _, dao = tmp_db
    sess, agent = await _seed_session(dao)
    await _insert_verdict(
        dao,
        session_id=sess,
        agent_id=agent,
        overall_risk="HIGH",
        personas={
            "security_auditor": {
                "persona": "security_auditor",
                "score": 40,
                "confidence": 0.9,
                "flags": ["owasp_llm01_prompt_injection"],
                "evidence": [],
                "reasoning": "",
            }
        },
    )
    report = await agg.aggregate(sess)
    assert report.owasp_map.get("owasp_llm01_prompt_injection") == 1


@pytest.mark.asyncio
async def test_aggregator_cost_summary_from_claude_calls(tmp_db):
    agg, _, dao = tmp_db
    sess, agent = await _seed_session(dao)
    await _insert_cost(
        dao,
        session_id=sess,
        agent_id=agent,
        model="claude-opus-4-7",
        cost_usd=0.02,
        tokens_in=1000,
        tokens_out=500,
    )
    await _insert_cost(
        dao,
        session_id=sess,
        agent_id=agent,
        model="claude-haiku-4-5",
        cost_usd=0.001,
        tokens_in=200,
        tokens_out=50,
    )
    report = await agg.aggregate(sess)
    assert report.cost.num_opus_calls == 1
    assert report.cost.num_haiku_calls == 1
    assert report.cost.total_usd == pytest.approx(0.021)
    assert report.cost.tokens_in == 1200
    assert report.cost.tokens_out == 550


@pytest.mark.asyncio
async def test_aggregator_top_findings_sorted_by_severity(tmp_db):
    agg, _, dao = tmp_db
    sess, agent = await _seed_session(dao)
    await _insert_finding(
        dao, session_id=sess, agent_id=agent, severity="LOW", flag="config_mismatch", title="Low one"
    )
    await _insert_finding(
        dao, session_id=sess, agent_id=agent, severity="CRITICAL", flag="credential_leak", title="Crit"
    )
    await _insert_finding(
        dao, session_id=sess, agent_id=agent, severity="HIGH", flag="shell_injection", title="High"
    )
    report = await agg.aggregate(sess)
    assert [f.severity for f in report.top_findings] == ["CRITICAL", "HIGH", "LOW"]


@pytest.mark.asyncio
async def test_aggregator_quality_folds_in(tmp_db):
    agg, _, dao = tmp_db
    sess, agent = await _seed_session(dao)
    from safer_backend.models.quality import QualitySummary

    quality = QualitySummary(
        session_id=sess,
        agent_id=agent,
        overall_quality_score=60,
        task_completion=55,
        hallucination_summary="One unsupported claim.",
        efficiency_report="Efficient.",
    )
    report = await agg.aggregate(sess, quality=quality)
    q_cat = next(c for c in report.categories if c.name == "quality")
    assert q_cat.value == 60
    # quality weight is 0.15, so 60 (quality) + 6 * 100 (others) * weights
    assert report.overall_health < 100


@pytest.mark.asyncio
async def test_aggregator_missing_session_raises(tmp_db):
    agg, _, _ = tmp_db
    with pytest.raises(ValueError):
        await agg.aggregate("sess_missing")


# ---------- orchestrator (no Claude client) ----------


@pytest.mark.asyncio
async def test_orchestrator_no_client_produces_deterministic_report(tmp_db, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _, orch, dao = tmp_db
    await _seed_session(dao)

    report = await orch.generate_report("sess_test")
    assert report.overall_health == 100
    # Persisted?
    cached = await orch.load_cached_report("sess_test")
    assert cached is not None
    assert cached.session_id == "sess_test"


@pytest.mark.asyncio
async def test_orchestrator_missing_session_raises(tmp_db, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _, orch, _ = tmp_db
    with pytest.raises(ValueError):
        await orch.generate_report("sess_nope")


# ---------- quality reviewer with fake client ----------


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [SimpleNamespace(type="text", text=text)]
        self.usage = SimpleNamespace(
            input_tokens=800,
            output_tokens=300,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=800,
        )


class _FakeAnthropic:
    def __init__(self, canned: str):
        self._canned = canned
        self.calls: list[dict] = []

        async def _create(**kwargs):
            self.calls.append(kwargs)
            return _FakeResponse(self._canned)

        self.messages = SimpleNamespace(create=_create)


@pytest.mark.asyncio
async def test_quality_reviewer_happy_path(tmp_db, monkeypatch):
    _, _, dao = tmp_db
    sess, agent = await _seed_session(dao)

    import safer_backend.quality.reviewer as q_mod

    importlib.reload(q_mod)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    fake = _FakeAnthropic(
        json.dumps(
            {
                "overall_quality_score": 78,
                "task_completion": 85,
                "hallucination_summary": "Clean.",
                "efficiency_report": "Efficient.",
                "goal_drift_timeline": [],
            }
        )
    )
    q_mod.set_client(fake)

    summary = await q_mod.review_session(sess)
    assert summary.overall_quality_score == 78
    assert summary.task_completion == 85
    # Prompt cache requested.
    assert fake.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_quality_reviewer_missing_session_raises(tmp_db, monkeypatch):
    import safer_backend.quality.reviewer as q_mod

    importlib.reload(q_mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    q_mod.set_client(_FakeAnthropic("{}"))
    with pytest.raises(ValueError):
        await q_mod.review_session("sess_none")


# ---------- reconstructor with fake client ----------


@pytest.mark.asyncio
async def test_reconstructor_happy_path(tmp_db, monkeypatch):
    _, _, dao = tmp_db
    sess, agent = await _seed_session(dao)
    await _insert_verdict(
        dao,
        session_id=sess,
        agent_id=agent,
        overall_risk="HIGH",
        personas={
            "security_auditor": {
                "persona": "security_auditor",
                "score": 40,
                "confidence": 0.9,
                "flags": ["shell_injection"],
                "evidence": [],
                "reasoning": "",
            }
        },
    )
    import safer_backend.reconstructor.chain as r_mod

    importlib.reload(r_mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    fake = _FakeAnthropic(
        json.dumps(
            {
                "narrative": "The agent called a shell tool with user input.",
                "timeline": [
                    {
                        "step": 0,
                        "hook": "before_tool_use",
                        "risk": "HIGH",
                        "summary": "Called exec with user-controlled argument.",
                    }
                ],
            }
        )
    )
    r_mod.set_client(fake)

    chain = await r_mod.reconstruct(sess)
    assert "shell" in chain.narrative
    assert len(chain.timeline) == 1
    assert chain.timeline[0].risk == "HIGH"


# ---------- API ----------


@pytest.fixture()
def app_client(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "api.db")
        monkeypatch.setenv("SAFER_DB_PATH", db_path)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        from safer_backend.storage.db import init_db_sync

        init_db_sync(db_path)

        import safer_backend.storage.db as db_mod
        import safer_backend.storage.dao as dao_mod
        import safer_backend.session_report.aggregator as agg_mod
        import safer_backend.session_report.orchestrator as orch_mod
        import safer_backend.session_report.api as api_mod
        import safer_backend.main as main_mod

        importlib.reload(db_mod)
        importlib.reload(dao_mod)
        importlib.reload(agg_mod)
        importlib.reload(orch_mod)
        importlib.reload(api_mod)
        importlib.reload(main_mod)

        with TestClient(main_mod.app) as client:
            yield client, dao_mod


def _run_sync(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_api_get_report_404_when_session_missing(app_client):
    client, _ = app_client
    resp = client.get("/v1/sessions/nope/report")
    assert resp.status_code == 404


def test_api_get_report_generates_on_demand(app_client):
    client, dao_mod = app_client
    _run_sync(_seed_session(dao_mod))

    resp = client.get("/v1/sessions/sess_test/report")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "sess_test"
    assert data["overall_health"] == 100


def test_api_post_generate_forces_regeneration(app_client):
    client, dao_mod = app_client
    _run_sync(_seed_session(dao_mod))

    resp = client.post("/v1/sessions/sess_test/report/generate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "agent_test"
