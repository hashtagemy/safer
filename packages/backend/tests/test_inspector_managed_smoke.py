"""End-to-end smoke test for the Managed-Agents Inspector path.

Skipped by default. Run it manually before a demo with:

    SAFER_INSPECTOR_E2E=1 uv run pytest \
        packages/backend/tests/test_inspector_managed_smoke.py -s

Requires:
    - ANTHROPIC_API_KEY with Managed Agents beta access
    - SAFER_INSPECTOR_E2E=1 to opt in

The test provisions the agent/memory-store/environment (or reuses
cached IDs), runs a scan over a tiny intentionally-risky snippet, and
asserts that a Verdict comes back with real content. It does NOT
assert exact scores — those come from the live model and will drift.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


RISKY_SOURCE = '''
import os
import pickle
from flask import Flask, request

app = Flask(__name__)

SECRET_KEY = "hardcoded-api-key-do-not-commit"

@app.route("/run")
def run():
    # Direct prompt injection sink + unsafe eval.
    user = request.args.get("cmd", "")
    result = eval(user)
    return str(result)

@app.route("/load")
def load():
    # Unsafe deserialization.
    data = request.get_data()
    return pickle.loads(data)
'''.strip()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("SAFER_INSPECTOR_E2E"),
    reason="set SAFER_INSPECTOR_E2E=1 to run the live Managed Agents smoke test",
)
async def test_managed_inspector_end_to_end(monkeypatch):
    # Isolate the managed_agents_config cache so a past run's IDs don't
    # leak in; we want this test to always create fresh resources OR
    # exercise the full read-then-reuse path deterministically.
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "smoke.db")
        monkeypatch.setenv("SAFER_DB_PATH", db_path)

        # Re-import modules that cache the DB path at import time.
        import importlib
        import safer_backend.storage.db as dbmod

        importlib.reload(dbmod)
        from safer_backend.storage import init_db

        await init_db(db_path)

        import safer_backend.inspector.managed_bootstrap as mb
        import safer_backend.inspector.managed as managed

        importlib.reload(mb)
        importlib.reload(managed)

        from safer_backend.inspector.ast_scanner import scan as scan_ast
        from safer_backend.inspector.pattern_rules import scan_patterns

        ast_summary = scan_ast(RISKY_SOURCE, module_name="smoke.agent")
        pattern_matches = scan_patterns(RISKY_SOURCE)

        verdict = await managed.review_managed(
            agent_id="smoke_agent",
            source=RISKY_SOURCE,
            system_prompt="You are a demo agent.",
            tools=[],
            ast_summary=ast_summary,
            pattern_matches=pattern_matches,
            active_policies=[],
            timeout_s=180,
        )

        # The live model should identify SOMETHING on this obviously
        # risky code. We assert on structure, not specific flags.
        assert verdict.agent_id == "smoke_agent"
        assert verdict.mode == "INSPECTOR"
        assert verdict.personas, "expected at least one persona verdict"
        assert verdict.overall.risk is not None
        # Risky code -> at least one persona should score below clean.
        min_score = min(p.score for p in verdict.personas.values())
        assert min_score < 90, (
            f"Expected at least one persona to flag risk on obviously "
            f"risky code; got min score {min_score}."
        )
