# safer-backend

The Agent Control Plane backend for [SAFER](https://github.com/hashtagemy/safer)
— FastAPI + SQLite WAL + Multi-Persona Judge (Opus 4.7) + Gateway +
Inspector + Red-Team Squad (Claude Managed Agents) + Compliance Pack.

```bash
pip install safer-backend
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn safer_backend.main:app --host 0.0.0.0 --port 8000
```

A SAFER-instrumented agent (`pip install safer-sdk`) running anywhere
on your network can ship its 9-hook lifecycle events to this backend,
which then runs:

- **Inspector** — onboarding-phase code review (3-persona Opus call,
  optionally as a Claude Managed Agent with a shared memory store).
- **Multi-Persona Judge** — runtime evaluation with dynamic per-event
  routing across 6 personas (Security Auditor, Compliance Officer,
  Trust Guardian, Scope Enforcer, Ethics Reviewer, Policy Warden).
- **Gateway** — deterministic pre-call PII regex + 4 built-in
  policies + 3 guard modes (monitor / intervene / enforce).
- **Red-Team Squad** — manual 3-stage adversarial test (Strategist
  → Attacker → Analyst, real Claude Managed Agents path with a
  sub-agent fallback).
- **Session Report** — per-session 7-category health card,
  deterministic Python aggregator, zero Claude calls at aggregation
  time.
- **Compliance Pack** — GDPR / SOC 2 / OWASP LLM Top 10 reports
  exported as HTML / PDF (WeasyPrint) / JSON.

For the full architecture, framework matrix, dashboard walkthrough,
and demo, see the [main README](https://github.com/hashtagemy/safer#readme).

## Companion package

`safer-backend` ships alongside [`safer-sdk`](https://pypi.org/project/safer-sdk/),
the lightweight client library you install in your agent project to
emit hook events to this backend. Versions stay in lockstep —
`safer-backend>=0.1.0` requires `safer-sdk>=0.1.0,<0.2`.

## License

Apache 2.0.
