# Demo smoke-test (cold clone → green)

Run this checklist once end-to-end before recording the video. Target
time: **~15 minutes**. Any failure here is a demo-blocker.

## 1. Fresh clone

```bash
cd /tmp
rm -rf safer-smoke && git clone https://github.com/hashtagemy/safer.git safer-smoke
cd safer-smoke
cp .env.example .env
# Edit .env, set ANTHROPIC_API_KEY. Leave the rest at defaults.
```

## 2. Build with Docker

```bash
docker compose build
docker compose up -d
docker compose ps   # both services running, backend healthy
```

Dashboard at `http://localhost:5173`, backend at `http://localhost:8000`.

Smoke checks:
- `curl -s http://localhost:8000/health` → `{"status":"ok"}`.
- Dashboard loads, sidebar shows all 10 routes.
- `/settings` shows agent / session / event counters (should be zeros).

## 3. Seed demo data

```bash
docker compose exec backend uv run python /app/scripts/seed_demo.py
```

Expected output:
```
→ Resetting DB at /app/data/safer.db
→ Seeding agents
→ Seeding 5 scenarios
→ Generating Session Reports (deterministic; no Claude)
   sess_clean_*: overall_health=100 …
   sess_pii_*:   overall_health≈83 …
   sess_pi_*:    overall_health≈83 …
   sess_loop_*:  overall_health≈95 …
   sess_analyst_*: overall_health=100 …
✅ Seed complete. …
```

(If running the backend locally instead of inside Docker,
`uv run python scripts/seed_demo.py` from the repo root does the same.)

## 4. Walk every dashboard page

| URL | What to confirm |
|---|---|
| `/overview` | 4 KPIs populated, Recent activity list shows at least one entry |
| `/live` | Events filter works; agent/hook/risk dropdowns populate |
| `/agents` | Paste the default example and press **Run Inspector**. Risk Score gauge animates to ~72, pattern matches shown, policy suggestions list |
| `/sessions` | 5 rows sorted by started_at, clicking one opens detail |
| `/sessions/:id` (pick `sess_pii_*`) | Gauge animated, 7-category bars visible, 1 top finding, OWASP LLM06 highlighted |
| `/policies` | Left column lists built-in policies; compose something like "Block customer emails from leaving" and press **Compile** — preview with test cases appears |
| `/quality` | Average health bar + per-session category grid |
| `/redteam` | Enter `agent_support` in the agent_id field, open the modal, click Run (~30-60 s). Phase strip animates, OWASP grid populates |
| `/reports` | Pick **GDPR** + **HTML** + "last 7 days", press **Build** — iframe renders, Print-to-PDF works |
| `/settings` | Agent / session / event counters ≥ 5 |

## 5. Run a live agent

```bash
# Claude SDK demo
export ANTHROPIC_API_KEY=sk-ant-...
docker compose exec backend uv run python /app/examples/customer-support/main.py
```

Open `/live` — events should appear in real time, session appears in
`/sessions` within seconds of the agent finishing.

## 6. Tear down

```bash
docker compose down -v   # wipes the named volume; next run starts clean
```

---

## Backup plan (Red-Team edition)

If the Red-Team run hangs during the recording (Managed Agents API
flake, network flakey during the demo):

- The orchestrator already falls back to sub-agent mode.
- If sub-agent also fails, keep the pre-recorded Red-Team clip ready
  (see `docs/demo/script.md` step 5) and pivot.
