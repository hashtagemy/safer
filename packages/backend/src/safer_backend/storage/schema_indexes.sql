-- SAFER SQLite schema — indexes only.
-- Must be executed AFTER schema_tables.sql and AFTER _apply_additive_migrations,
-- because some indexes reference columns that are added via ALTER TABLE
-- (e.g. idx_sessions_parent → sessions.parent_session_id).

CREATE INDEX IF NOT EXISTS idx_agents_last_seen ON agents(last_seen_at);

CREATE INDEX IF NOT EXISTS idx_sessions_agent_started ON sessions(agent_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_ended ON sessions(ended_at);
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id);

CREATE INDEX IF NOT EXISTS idx_events_session_seq ON events(session_id, sequence);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_hook ON events(hook);
CREATE INDEX IF NOT EXISTS idx_events_agent_timestamp ON events(agent_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_risk_hint ON events(risk_hint);

CREATE INDEX IF NOT EXISTS idx_verdicts_event ON verdicts(event_id);
CREATE INDEX IF NOT EXISTS idx_verdicts_session ON verdicts(session_id);
CREATE INDEX IF NOT EXISTS idx_verdicts_risk ON verdicts(overall_risk);
CREATE INDEX IF NOT EXISTS idx_verdicts_agent_ts ON verdicts(agent_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_findings_agent ON findings(agent_id);
CREATE INDEX IF NOT EXISTS idx_findings_session ON findings(session_id);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
CREATE INDEX IF NOT EXISTS idx_findings_flag ON findings(flag);
CREATE INDEX IF NOT EXISTS idx_findings_source ON findings(source);
CREATE INDEX IF NOT EXISTS idx_findings_created ON findings(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_policies_agent ON policies(agent_id);
CREATE INDEX IF NOT EXISTS idx_policies_active ON policies(active);

CREATE INDEX IF NOT EXISTS idx_redteam_agent ON red_team_runs(agent_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_redteam_phase ON red_team_runs(phase);

CREATE INDEX IF NOT EXISTS idx_inspector_reports_agent ON inspector_reports(agent_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_claude_calls_ts ON claude_calls(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_claude_calls_component ON claude_calls(component);
