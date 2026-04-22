"""Inspector tests — AST, patterns, classifier, suggester, orchestrator.

Uses a fake Anthropic client for the persona review so tests are
hermetic and don't require ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from safer_backend.inspector import inspect
from safer_backend.inspector.ast_scanner import scan as scan_ast
from safer_backend.inspector.pattern_rules import scan_patterns
from safer_backend.inspector.policy_suggester import suggest_policies
from safer_backend.inspector.tool_classifier import classify_tool
from safer_backend.judge.engine import set_client
from safer_backend.models.findings import Severity
from safer_backend.models.inspector import PatternMatch, ToolRiskClass
from safer_backend.models.verdicts import PersonaVerdict


# ---------- fake Claude client ----------


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [SimpleNamespace(type="text", text=text)]
        self.usage = SimpleNamespace(
            input_tokens=1200,
            output_tokens=500,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=1200,
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


# ---------- AST scanner ----------


def test_ast_scanner_finds_decorated_tools():
    src = '''
@tool
def get_order(id: str) -> dict:
    """Fetch order."""
    return {}

@function_tool
async def send_email(to: str, body: str) -> None:
    pass

@app.tool
def list_products():
    pass

@staticmethod
def not_a_tool():
    pass

def plain_function():
    pass
'''
    summary = scan_ast(src)
    names = {t.name for t in summary.tools}
    assert names == {"get_order", "send_email", "list_products"}


def test_ast_scanner_detects_llm_calls_and_imports():
    src = """
import anthropic

client = anthropic.Anthropic()
resp = client.messages.create(model='claude', messages=[])
"""
    summary = scan_ast(src)
    assert "anthropic" in summary.imports
    providers = {c.provider for c in summary.llm_calls}
    assert "anthropic" in providers
    funcs = {c.function for c in summary.llm_calls}
    assert "client.messages.create" in funcs


def test_ast_scanner_detects_main_entry_point():
    src = "if __name__ == '__main__':\n    print('hi')\n"
    assert "__main__" in scan_ast(src).entry_points


def test_ast_scanner_records_syntax_error():
    summary = scan_ast("def :(invalid")
    assert summary.parse_error is not None
    assert summary.tools == []


def test_ast_scanner_ignores_toolkit_false_positive():
    """Decorators like @app.toolkit_method should not count as tools."""
    src = """
@app.toolbar
def not_a_tool():
    pass
"""
    summary = scan_ast(src)
    assert summary.tools == []


# ---------- tool classifier ----------


def test_classifier_buckets():
    assert classify_tool(name="get_order")[0] == ToolRiskClass.LOW
    assert classify_tool(name="send_email")[0] == ToolRiskClass.HIGH
    assert classify_tool(name="wire_transfer")[0] == ToolRiskClass.CRITICAL
    assert classify_tool(name="save_record")[0] == ToolRiskClass.MEDIUM
    # Unknown defaults to MEDIUM.
    assert classify_tool(name="mystery")[0] == ToolRiskClass.MEDIUM


# ---------- pattern rules ----------


@pytest.mark.parametrize(
    "source,expected_rule",
    [
        ('KEY = "sk-ant-' + "a" * 30 + '"', "hardcoded_credential"),
        ("eval(input())", "eval_exec_usage"),
        ("import os\nos.system('ls ' + x)", "os_system_call"),
        (
            "import subprocess\nsubprocess.run('rm -rf ' + x, shell=True)",
            "shell_injection",
        ),
        ("import pickle\npickle.loads(data)", "insecure_deserialization"),
        ("import yaml\nyaml.load(open('x'))", "yaml_unsafe_load"),
        ("import requests\nrequests.get('https://x', verify=False)", "ssl_verify_disabled"),
        ("cur.execute('SELECT * FROM u WHERE id=' + x)", "sql_string_injection"),
        ("open('/tmp/' + name, 'w')", "path_traversal_open"),
        ("import hashlib\nhashlib.md5(b'x').hexdigest()", "weak_hash_algorithm"),
        ('r = "http://evil.example.com/x"', "plaintext_http_url"),
        ("app.run(debug=True)", "debug_mode_enabled"),
    ],
)
def test_each_pattern_positive(source: str, expected_rule: str):
    rules = {m.rule_id for m in scan_patterns(source)}
    assert expected_rule in rules, f"{expected_rule} did not fire on {source!r}"


def test_patterns_clean_code_no_matches():
    src = '''
import logging

def greet(name: str) -> str:
    """Return a safe greeting."""
    return f"Hello, {name}!"

logging.info("starting")
'''
    assert scan_patterns(src) == []


def test_patterns_survive_syntax_error():
    # Pure-text rules (credential / http) still run even if AST fails.
    src = 'KEY = "sk-ant-' + "a" * 30 + '"\ndef :(invalid'
    rules = {m.rule_id for m in scan_patterns(src)}
    assert "hardcoded_credential" in rules


def test_yaml_safe_loader_not_flagged():
    src = "import yaml\nyaml.load(open('x'), Loader=yaml.SafeLoader)"
    rules = {m.rule_id for m in scan_patterns(src)}
    assert "yaml_unsafe_load" not in rules


# ---------- policy suggester ----------


def test_suggest_policies_from_pattern_matches_dedupes():
    matches = [
        PatternMatch(
            rule_id="hardcoded_credential",
            severity=Severity.CRITICAL,
            flag="credential_hardcoded",
            line=1,
        ),
        PatternMatch(
            rule_id="hardcoded_credential",
            severity=Severity.CRITICAL,
            flag="credential_hardcoded",
            line=4,
        ),
    ]
    suggestions = suggest_policies(pattern_matches=matches)
    assert len(suggestions) == 1
    assert suggestions[0].name == "credential-redaction"
    assert suggestions[0].severity == Severity.CRITICAL


def test_suggest_policies_rolls_up_severity():
    verdicts = {
        "security_auditor": PersonaVerdict(
            persona="security_auditor",
            score=20,
            confidence=0.9,
            flags=["shell_injection"],
        ),
    }
    matches = [
        PatternMatch(
            rule_id="ssl_verify_disabled",
            severity=Severity.HIGH,
            flag="ssl_bypass",
            line=1,
        )
    ]
    suggestions = suggest_policies(persona_verdicts=verdicts, pattern_matches=matches)
    names = {s.name for s in suggestions}
    assert "code-execution-block" in names
    assert "tls-enforcement" in names


def test_suggest_policies_empty_input():
    assert suggest_policies() == []


# ---------- orchestrator ----------


@pytest.mark.asyncio
async def test_inspect_skipped_persona_review_still_produces_report(reset_judge_client):
    src = 'API_KEY = "sk-ant-' + "a" * 30 + '"\n@tool\ndef get_user(id: str) -> dict:\n    return {}\n'
    report = await inspect(
        agent_id="agent_x",
        source=src,
        skip_persona_review=True,
    )
    assert report.agent_id == "agent_x"
    assert report.persona_review_skipped is True
    assert report.risk_level == Severity.CRITICAL
    assert any(p.name == "credential-redaction" for p in report.policy_suggestions)
    assert any(f.flag == "credential_hardcoded" for f in report.findings)
    assert any(t.name == "get_user" for t in report.ast_summary.tools)


@pytest.mark.asyncio
async def test_inspect_with_fake_persona_review(monkeypatch, reset_judge_client):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    persona_json = json.dumps(
        {
            "overall": {"risk": "HIGH", "confidence": 0.9, "block": False},
            "active_personas": [
                "security_auditor",
                "compliance_officer",
                "policy_warden",
            ],
            "personas": {
                "security_auditor": {
                    "persona": "security_auditor",
                    "score": 40,
                    "confidence": 0.9,
                    "flags": ["credential_hardcoded"],
                    "evidence": ["API_KEY = 'sk-ant-...'"],
                    "reasoning": "Key hard-coded at module scope.",
                    "recommended_mitigation": "Move key to env var.",
                },
                "compliance_officer": {
                    "persona": "compliance_officer",
                    "score": 85,
                    "confidence": 0.6,
                    "flags": [],
                    "evidence": [],
                    "reasoning": "No PII handling detected.",
                    "recommended_mitigation": None,
                },
                "policy_warden": {
                    "persona": "policy_warden",
                    "score": 100,
                    "confidence": 0.9,
                    "flags": [],
                    "evidence": [],
                    "reasoning": "No active user policies.",
                    "recommended_mitigation": None,
                },
            },
        }
    )
    fake = _FakeAnthropic(persona_json)
    set_client(fake)

    src = 'API_KEY = "sk-ant-' + "a" * 30 + '"\n@tool\ndef send_email(to: str): pass\n'
    report = await inspect(agent_id="agent_y", source=src)

    assert report.persona_review_skipped is False
    assert len(report.persona_verdicts) == 3
    # Persona flag adds a finding on top of the pattern match.
    credential_findings = [f for f in report.findings if f.flag == "credential_hardcoded"]
    assert len(credential_findings) >= 2
    # One Claude call total (persona review).
    assert len(fake.calls) == 1
    # Prompt cache requested.
    assert fake.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_inspect_survives_missing_client(reset_judge_client, monkeypatch):
    # No API key, no injected client → persona review is skipped gracefully.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    report = await inspect(
        agent_id="agent_z",
        source="@tool\ndef get_x(): pass\n",
    )
    assert report.persona_review_skipped is True
    assert report.persona_review_error is not None


def test_inspect_api_endpoint(reset_judge_client, monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("SAFER_DB_PATH", str(tmp_path / "test.db"))

    import importlib

    from fastapi.testclient import TestClient

    import safer_backend.main as main_mod
    import safer_backend.storage.db as db_mod

    importlib.reload(db_mod)
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as client:
        resp = client.post(
            "/v1/agents/agent_api/inspect",
            json={
                "source": "@tool\ndef get_x(id: str): return {}\n",
                "skip_persona_review": True,
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "agent_api"
    assert data["persona_review_skipped"] is True
    assert data["risk_score"] == 100  # no findings
