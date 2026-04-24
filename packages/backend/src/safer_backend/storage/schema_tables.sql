-- SAFER SQLite schema — tables only (WAL mode).
-- Append-only events. Schema change = migration (see CLAUDE.md).
--
-- Split from schema.sql so init_db() can run in three ordered steps:
--   1. executescript(schema_tables.sql)   -- create tables
--   2. _apply_additive_migrations(db)     -- ALTER TABLE ADD COLUMN for new columns
--   3. executescript(schema_indexes.sql)  -- create indexes (may reference migrated columns)

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

-- ============================================================
-- Agents (user's agents, discovered via `instrument()` or Add Agent)
-- ============================================================
CREATE TABLE IF NOT EXISTS agents (
    agent_id             TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    framework            TEXT,
    version              TEXT,
    created_at           TEXT NOT NULL,
    last_seen_at         TEXT,
    risk_score           INTEGER DEFAULT 0,
    metadata_json        TEXT DEFAULT '{}',
    -- Onboarding (on_agent_register) columns
    system_prompt        TEXT,
    project_root         TEXT,
    code_snapshot_blob   BLOB,
    code_snapshot_hash   TEXT,
    file_count           INTEGER DEFAULT 0,
    total_bytes          INTEGER DEFAULT 0,
    snapshot_truncated   INTEGER DEFAULT 0,
    registered_at        TEXT,
    latest_scan_id       TEXT
);

-- ============================================================
-- Sessions
-- ============================================================
CREATE TABLE IF NOT EXISTS sessions (
    session_id       TEXT PRIMARY KEY,
    agent_id         TEXT NOT NULL REFERENCES agents(agent_id),
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    total_steps      INTEGER DEFAULT 0,
    total_cost_usd   REAL DEFAULT 0.0,
    success          INTEGER DEFAULT 1,  -- boolean
    overall_health   INTEGER,            -- from SessionReport, null until session ends
    thought_chain_narrative TEXT,
    report_json      TEXT,               -- serialized SessionReport snapshot
    parent_session_id TEXT               -- session that triggered this one (supervisor → worker)
);

-- ============================================================
-- Events (append-only, 9-hook payloads)
-- ============================================================
CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    agent_id     TEXT NOT NULL REFERENCES agents(agent_id),
    sequence     INTEGER NOT NULL,
    hook         TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    risk_hint    TEXT DEFAULT 'LOW',
    source       TEXT DEFAULT 'sdk',
    payload_json TEXT NOT NULL          -- full pydantic event
);

-- ============================================================
-- Verdicts (Multi-Persona Judge outputs)
-- ============================================================
CREATE TABLE IF NOT EXISTS verdicts (
    verdict_id         TEXT PRIMARY KEY,
    event_id           TEXT NOT NULL REFERENCES events(event_id),
    session_id         TEXT NOT NULL REFERENCES sessions(session_id),
    agent_id           TEXT NOT NULL REFERENCES agents(agent_id),
    timestamp          TEXT NOT NULL,
    mode               TEXT NOT NULL,  -- RUNTIME | INSPECTOR
    overall_risk       TEXT NOT NULL,  -- LOW/MEDIUM/HIGH/CRITICAL
    overall_confidence REAL NOT NULL,
    overall_block      INTEGER DEFAULT 0,  -- boolean
    active_personas    TEXT NOT NULL,  -- JSON array
    personas_json      TEXT NOT NULL,  -- JSON map persona → PersonaVerdict
    latency_ms         INTEGER DEFAULT 0,
    tokens_in          INTEGER DEFAULT 0,
    tokens_out         INTEGER DEFAULT 0,
    cache_read_tokens  INTEGER DEFAULT 0,
    cost_usd           REAL DEFAULT 0.0
);

-- ============================================================
-- Findings (from Inspector, Red-Team, or Judge flag elevation)
-- ============================================================
CREATE TABLE IF NOT EXISTS findings (
    finding_id              TEXT PRIMARY KEY,
    agent_id                TEXT NOT NULL REFERENCES agents(agent_id),
    session_id              TEXT REFERENCES sessions(session_id),
    source                  TEXT NOT NULL,  -- inspector | red_team | judge | gateway
    severity                TEXT NOT NULL,
    category                TEXT NOT NULL,
    flag                    TEXT NOT NULL,
    title                   TEXT NOT NULL,
    description             TEXT NOT NULL,
    evidence_json           TEXT DEFAULT '[]',
    reproduction_steps_json TEXT DEFAULT '[]',
    recommended_mitigation  TEXT,
    owasp_id                TEXT,
    created_at              TEXT NOT NULL
);

-- ============================================================
-- Policies (Policy Studio — NL compiled rules)
-- ============================================================
CREATE TABLE IF NOT EXISTS policies (
    policy_id        TEXT PRIMARY KEY,
    agent_id         TEXT REFERENCES agents(agent_id),  -- NULL = global
    name             TEXT NOT NULL,
    nl_text          TEXT NOT NULL,
    rule_json        TEXT NOT NULL,
    code_snippet     TEXT,
    flag_category    TEXT,
    severity         TEXT DEFAULT 'MEDIUM',
    active           INTEGER DEFAULT 1,
    guard_mode       TEXT DEFAULT 'intervene',  -- monitor | intervene | enforce
    created_at       TEXT NOT NULL,
    test_cases_json  TEXT DEFAULT '[]'
);

-- ============================================================
-- Red-Team runs (manual button, Managed Agents or sub-agent fallback)
-- ============================================================
CREATE TABLE IF NOT EXISTS red_team_runs (
    run_id            TEXT PRIMARY KEY,
    agent_id          TEXT NOT NULL REFERENCES agents(agent_id),
    mode              TEXT NOT NULL,  -- managed | subagent
    phase             TEXT NOT NULL,  -- planning | attacking | analyzing | done | failed
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    attack_specs_json TEXT DEFAULT '[]',
    attempts_json     TEXT DEFAULT '[]',
    findings_count    INTEGER DEFAULT 0,
    safety_score      INTEGER DEFAULT 0,
    owasp_map_json    TEXT DEFAULT '{}',
    error             TEXT
);

-- ============================================================
-- Inspector reports (onboarding-phase scans, keyed by agent)
-- ============================================================
CREATE TABLE IF NOT EXISTS inspector_reports (
    report_id        TEXT PRIMARY KEY,
    agent_id         TEXT NOT NULL REFERENCES agents(agent_id),
    created_at       TEXT NOT NULL,
    scan_mode        TEXT NOT NULL DEFAULT 'single',
    risk_score       INTEGER NOT NULL,
    risk_level       TEXT NOT NULL,
    duration_ms      INTEGER DEFAULT 0,
    persona_skipped  INTEGER DEFAULT 0,
    persona_error    TEXT,
    report_json      TEXT NOT NULL
);

-- ============================================================
-- Cost tracking (per Claude call, for live credit counter)
-- ============================================================
CREATE TABLE IF NOT EXISTS claude_calls (
    call_id           TEXT PRIMARY KEY,
    timestamp         TEXT NOT NULL,
    component         TEXT NOT NULL,  -- judge | inspector | reconstructor | quality | policy_compiler | redteam | haiku_prestep
    model             TEXT NOT NULL,  -- claude-opus-4-7 | claude-haiku-4-5
    tokens_in         INTEGER DEFAULT 0,
    tokens_out        INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    cost_usd          REAL DEFAULT 0.0,
    latency_ms        INTEGER DEFAULT 0,
    agent_id          TEXT,
    session_id        TEXT,
    event_id          TEXT
);
