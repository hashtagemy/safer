# SAFER demo script — 3 minutes, 7 segments

Target length: **2:55 – 3:10**. Each segment has a timecap + voiceover
line + on-screen action. All timings assume the seed script (`scripts/
seed_demo.py`) has been run beforehand so every page has real data.

Pre-flight checks (before the recording starts):
- `docker compose up -d` — both services healthy.
- `uv run python scripts/seed_demo.py` — 5 sessions + 1 red-team run.
- Dashboard open at `/overview`, zoomed to 110 % so labels are legible.
- Claude Agent SDK demo ready to run in a second terminal.

---

## 0 · Hook (0:00 – 0:15)

**Voiceover:**

> "Agents are shipping faster than we can audit them. We built SAFER —
> an open-source **agent control plane** that watches the entire
> lifecycle of any AI agent. Powered by Claude, self-hosted, framework-
> agnostic."

**Screen:** open `/overview`, 4 KPI cards filled, a recent-activity
row glowing.

---

## 1 · Onboarding — Inspector (0:15 – 0:45)

**Voiceover:**

> "Before an agent even ships, SAFER's Inspector scans its code in one
> Opus 4.7 call. Three personas — Security Auditor, Compliance Officer,
> Policy Warden — give you a risk score and auto-suggested policies."

**Screen:** navigate to `/agents`. The textarea is pre-filled with a
deliberately-risky customer-support agent. Press **Run Inspector**.

- Gauge animates from 0 → ~72.
- Pattern matches list slides in with a CRITICAL hard-coded credential
  hit and a HIGH subprocess shell=True hit.
- Policy suggestions card shows `credential-redaction` +
  `tool-allowlist`.

Say:

> "12 deterministic patterns already caught this one. And the personas
> added three OWASP-aligned findings on top."

---

## 2 · Runtime — Multi-Persona Judge (0:45 – 1:15)

**Voiceover:**

> "At runtime, SAFER catches every critical step and sends it to a
> single Judge call with only the personas relevant for that hook.
> Cheap, fast, and auditable."

**Screen:** switch to a terminal and run:

```bash
docker compose exec backend \
  uv run python /app/examples/customer-support/main.py
```

Flip back to `/live`. Events start streaming in real time. Click any
`before_tool_use` row. The PersonaDrawer slides in.

Say:

> "Policy Warden + Scope Enforcer + Security + Compliance fired on this
> step. Trust Guardian and Ethics Reviewer are idle — they only matter
> at final-output time. Dynamic routing."

---

## 3 · Gateway — blocked egress (1:15 – 1:45)

**Voiceover:**

> "Gateway is the pre-call enforcement layer. It runs deterministic PII
> regex + natural-language policies you write in English."

**Screen:** go to `/policies`, type:

> "Never let this agent email customer addresses to an external domain."

Press **Compile** — preview slides in with `pii_guard` rule kind +
`ENFORCE` guard mode + two concrete test cases. Press **Activate**.

Then flip to `/live` — trigger the PII demo (pick the `sess_pii_*`
session from `/sessions` if the live one is slow). The **block
toast** fires bottom-right; click **Explain** to open the drawer.

Say:

> "Compile, activate, enforce. No YAML, no config files — English in,
> Gateway rule out."

---

## 4 · Pre-deploy — Red-Team Squad (1:45 – 2:15)

**Voiceover:**

> "Pre-deploy, flip the Red-Team switch. Three Claude agents —
> Strategist, Attacker, Analyst — probe your agent with 10 tailored
> attacks mapped to OWASP LLM Top 10."

**Screen:** `/redteam`. Open the **Run Red-Team** modal with the
pre-saved agent id. Submit. The phase strip animates through Strategist
→ Attacker → Analyst → Done (~60 s). OWASP grid lights up.

*If the live run flakes:* cut to the pre-recorded clip (`docs/demo-
assets/redteam-fallback.mp4`). The final dashboard frame is identical.

Say:

> "If the Managed Agents API is unavailable, SAFER transparently falls
> back to plain Opus sub-agents. Same three stages, same output."

---

## 5 · Post-run — Session Report (2:15 – 2:40)

**Voiceover:**

> "When a session ends, SAFER writes a single deterministic health
> card. No extra Claude calls — just aggregation over what the Judge
> already said."

**Screen:** click any seeded session — ideally `sess_pi_*`. The Risk
Score gauge animates to 83 (dropdown a cat.), seven category bars fill
in, top findings show the prompt-injection entry, OWASP LLM01 lights up.
Then scroll to the Thought-Chain narrative — the typewriter animation
reveals a paragraph summarising the session.

Say:

> "Narrative reconstruction runs automatically when something bad
> happens. For clean sessions it's one click."

---

## 6 · Assure — Compliance Pack (2:40 – 3:00)

**Voiceover:**

> "Finally, SAFER ships a Compliance Pack. Pick a date range, pick
> GDPR, SOC 2, or OWASP LLM Top 10, pick PDF / HTML / JSON — click
> build, and you're done."

**Screen:** `/reports`. Last 7 days + OWASP LLM + HTML. The iframe
renders the multi-page report instantly. Hit **Print / Save as PDF**
to show the deal-close moment.

Say:

> "Self-hosted. Framework-agnostic. Claude-powered. SAFER — the agent
> control plane."

---

## Voiceover totals

| Segment | Target | Words (≈150 wpm) |
|---|---|---|
| 0 Hook | 15 s | 38 |
| 1 Inspector | 30 s | 75 |
| 2 Judge | 30 s | 75 |
| 3 Gateway | 30 s | 75 |
| 4 Red-Team | 30 s | 75 |
| 5 Session Report | 25 s | 63 |
| 6 Compliance | 20 s | 50 |
| **Total** | **3:00** | **~450** |

±15 s tolerance per submission guidelines.

## Red-Team fallback checklist

- `docs/demo-assets/redteam-fallback.mp4` — 60-90 s, 1080p, no audio.
- Record during phase-4 dry run #1; keep the clip that has the cleanest
  phase strip animation.

## Hand-off notes

- Dashboard dark theme — do not record in light mode.
- Cursor visible; use `1920×1080` window.
- Mouse movements slow enough to read.
- Avoid hovering the sidebar during voiceover (focus stays on content).
