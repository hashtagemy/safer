"""Compliance data loader — deterministic SQL → ComplianceData dataclass.

One loader, three reports. Each template picks the subset it cares
about (GDPR → PII findings + policy decisions; SOC2 → control failures
+ audit trail; OWASP → flag taxonomy) so callers never have to run
three similar queries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..storage.db import get_db


class Standard(str, Enum):
    GDPR = "gdpr"
    SOC2 = "soc2"
    OWASP_LLM = "owasp_llm"


@dataclass
class FindingRow:
    finding_id: str
    agent_id: str
    session_id: str | None
    source: str
    severity: str
    category: str
    flag: str
    title: str
    description: str
    evidence: list[str]
    owasp_id: str | None
    created_at: str


@dataclass
class SessionRow:
    session_id: str
    agent_id: str
    agent_name: str
    started_at: str
    ended_at: str | None
    overall_health: int | None
    total_cost_usd: float


@dataclass
class AgentRow:
    agent_id: str
    name: str
    framework: str | None
    first_seen: str
    last_seen: str | None


@dataclass
class GatewayBlockRow:
    event_id: str
    session_id: str
    agent_id: str
    timestamp: str
    reason: str
    flags: list[str]


@dataclass
class VerdictRow:
    event_id: str
    session_id: str
    agent_id: str
    timestamp: str
    overall_risk: str
    overall_block: bool
    flags: list[str]  # union of flags across personas


@dataclass
class ComplianceData:
    standard: Standard
    start: datetime
    end: datetime

    agents: list[AgentRow] = field(default_factory=list)
    sessions: list[SessionRow] = field(default_factory=list)
    findings: list[FindingRow] = field(default_factory=list)
    verdicts: list[VerdictRow] = field(default_factory=list)
    gateway_blocks: list[GatewayBlockRow] = field(default_factory=list)

    # Derived (filled by loader)
    findings_by_severity: dict[str, int] = field(default_factory=dict)
    findings_by_category: dict[str, int] = field(default_factory=dict)
    flags_by_count: dict[str, int] = field(default_factory=dict)
    owasp_counts: dict[str, int] = field(default_factory=dict)

    total_cost_usd: float = 0.0
    total_sessions: int = 0
    total_agents: int = 0
    total_findings: int = 0
    total_blocks: int = 0

    generated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def range_label(self) -> str:
        return f"{self.start.date().isoformat()} → {self.end.date().isoformat()}"


# ---------- OWASP LLM Top 10 rows the templates always show ----------

OWASP_ROWS: list[tuple[str, str, str]] = [
    ("owasp_llm01_prompt_injection", "LLM01", "Prompt Injection"),
    ("owasp_llm02_insecure_output_handling", "LLM02", "Insecure Output Handling"),
    ("owasp_llm03_training_data_poisoning", "LLM03", "Training Data Poisoning"),
    ("owasp_llm04_model_denial_of_service", "LLM04", "Model Denial of Service"),
    ("owasp_llm05_supply_chain", "LLM05", "Supply Chain Vulnerabilities"),
    ("owasp_llm06_sensitive_info_disclosure", "LLM06", "Sensitive Information Disclosure"),
    ("owasp_llm07_insecure_plugin_design", "LLM07", "Insecure Plugin Design"),
    ("owasp_llm08_excessive_agency", "LLM08", "Excessive Agency"),
    ("owasp_llm09_overreliance", "LLM09", "Overreliance"),
    ("owasp_llm10_model_theft", "LLM10", "Model Theft"),
]


# Same flag → OWASP map the Session Report aggregator uses, kept in sync.
_FLAG_TO_OWASP: dict[str, str] = {
    "prompt_injection_direct": "owasp_llm01_prompt_injection",
    "prompt_injection_indirect": "owasp_llm01_prompt_injection",
    "jailbreak_attempt": "owasp_llm01_prompt_injection",
    "eval_exec_usage": "owasp_llm02_insecure_output_handling",
    "insecure_deserialization": "owasp_llm02_insecure_output_handling",
    "xss_risk": "owasp_llm02_insecure_output_handling",
    "excessive_retries": "owasp_llm04_model_denial_of_service",
    "loop_detected": "owasp_llm04_model_denial_of_service",
    "credential_leak": "owasp_llm06_sensitive_info_disclosure",
    "credential_hardcoded": "owasp_llm06_sensitive_info_disclosure",
    "pii_exposure": "owasp_llm06_sensitive_info_disclosure",
    "pii_sent_external": "owasp_llm06_sensitive_info_disclosure",
    "pii_stored_insecure": "owasp_llm06_sensitive_info_disclosure",
    "pii_logged": "owasp_llm06_sensitive_info_disclosure",
    "data_exfiltration": "owasp_llm06_sensitive_info_disclosure",
    "hipaa_phi_leak": "owasp_llm06_sensitive_info_disclosure",
    "tool_abuse": "owasp_llm07_insecure_plugin_design",
    "unauthorized_tool_call": "owasp_llm07_insecure_plugin_design",
    "shell_injection": "owasp_llm07_insecure_plugin_design",
    "sql_injection": "owasp_llm07_insecure_plugin_design",
    "path_traversal": "owasp_llm07_insecure_plugin_design",
    "ssrf_risk": "owasp_llm07_insecure_plugin_design",
    "ssl_bypass": "owasp_llm07_insecure_plugin_design",
    "off_task": "owasp_llm08_excessive_agency",
    "scope_creep": "owasp_llm08_excessive_agency",
    "tool_misuse": "owasp_llm08_excessive_agency",
    "unnecessary_step": "owasp_llm08_excessive_agency",
    "hallucination": "owasp_llm09_overreliance",
    "unsupported_claim": "owasp_llm09_overreliance",
    "fabricated_evidence": "owasp_llm09_overreliance",
    "false_success": "owasp_llm09_overreliance",
    "missing_citation": "owasp_llm09_overreliance",
    "prompt_extraction": "owasp_llm10_model_theft",
}


async def load_range(
    *,
    start: datetime,
    end: datetime,
    standard: Standard,
    agent_id: str | None = None,
) -> ComplianceData:
    """Build a `ComplianceData` snapshot for the given range."""
    if end < start:
        raise ValueError("end must be >= start")

    start_iso = start.isoformat()
    end_iso = end.isoformat()

    data = ComplianceData(standard=standard, start=start, end=end)

    async with get_db() as db:
        agent_filter = ""
        params: list[Any] = [start_iso, end_iso]
        if agent_id:
            agent_filter = "AND s.agent_id = ?"
            params.append(agent_id)

        # Sessions in range (use started_at).
        async with db.execute(
            f"""
            SELECT s.session_id, s.agent_id, a.name, s.started_at, s.ended_at,
                   s.overall_health, s.total_cost_usd
            FROM sessions s
            JOIN agents a ON s.agent_id = a.agent_id
            WHERE s.started_at >= ? AND s.started_at <= ?
            {agent_filter}
            ORDER BY s.started_at ASC
            """,
            params,
        ) as cur:
            async for row in cur:
                data.sessions.append(
                    SessionRow(
                        session_id=row[0],
                        agent_id=row[1],
                        agent_name=row[2],
                        started_at=row[3],
                        ended_at=row[4],
                        overall_health=int(row[5]) if row[5] is not None else None,
                        total_cost_usd=float(row[6] or 0.0),
                    )
                )

        # Distinct agents with activity.
        agent_ids = sorted({s.agent_id for s in data.sessions})
        if agent_ids:
            placeholders = ",".join("?" * len(agent_ids))
            async with db.execute(
                f"""
                SELECT agent_id, name, framework, created_at, last_seen_at
                FROM agents
                WHERE agent_id IN ({placeholders})
                """,
                agent_ids,
            ) as cur:
                async for row in cur:
                    data.agents.append(
                        AgentRow(
                            agent_id=row[0],
                            name=row[1],
                            framework=row[2],
                            first_seen=row[3],
                            last_seen=row[4],
                        )
                    )

        # Findings created in range.
        agent_filter_f = ""
        params_f: list[Any] = [start_iso, end_iso]
        if agent_id:
            agent_filter_f = "AND agent_id = ?"
            params_f.append(agent_id)
        async with db.execute(
            f"""
            SELECT finding_id, agent_id, session_id, source, severity, category,
                   flag, title, description, evidence_json, owasp_id, created_at
            FROM findings
            WHERE created_at >= ? AND created_at <= ?
            {agent_filter_f}
            ORDER BY
                CASE severity
                    WHEN 'CRITICAL' THEN 0
                    WHEN 'HIGH' THEN 1
                    WHEN 'MEDIUM' THEN 2
                    ELSE 3
                END,
                created_at DESC
            """,
            params_f,
        ) as cur:
            async for row in cur:
                try:
                    evidence = json.loads(row[9] or "[]")
                except json.JSONDecodeError:
                    evidence = []
                data.findings.append(
                    FindingRow(
                        finding_id=row[0],
                        agent_id=row[1],
                        session_id=row[2],
                        source=row[3],
                        severity=row[4],
                        category=row[5],
                        flag=row[6],
                        title=row[7],
                        description=row[8],
                        evidence=evidence,
                        owasp_id=row[10],
                        created_at=row[11],
                    )
                )

        # Verdicts (for flag counts).
        agent_filter_v = ""
        params_v: list[Any] = [start_iso, end_iso]
        if agent_id:
            agent_filter_v = "AND agent_id = ?"
            params_v.append(agent_id)
        async with db.execute(
            f"""
            SELECT event_id, session_id, agent_id, timestamp, overall_risk,
                   overall_block, personas_json
            FROM verdicts
            WHERE timestamp >= ? AND timestamp <= ? AND mode = 'RUNTIME'
            {agent_filter_v}
            """,
            params_v,
        ) as cur:
            async for row in cur:
                try:
                    personas = json.loads(row[6] or "{}")
                except json.JSONDecodeError:
                    personas = {}
                flags: list[str] = []
                for pv in personas.values():
                    flags.extend(list(pv.get("flags") or []))
                data.verdicts.append(
                    VerdictRow(
                        event_id=row[0],
                        session_id=row[1],
                        agent_id=row[2],
                        timestamp=row[3],
                        overall_risk=row[4],
                        overall_block=bool(row[5]),
                        flags=flags,
                    )
                )

    # Gateway blocks are a subset of events — the Gateway records them by
    # stamping a risk_hint change + emitting block broadcasts. For the MVP
    # we infer blocks from verdict rows with overall_block=True.
    for v in data.verdicts:
        if v.overall_block:
            data.gateway_blocks.append(
                GatewayBlockRow(
                    event_id=v.event_id,
                    session_id=v.session_id,
                    agent_id=v.agent_id,
                    timestamp=v.timestamp,
                    reason=f"risk={v.overall_risk}",
                    flags=v.flags,
                )
            )

    # ---------- aggregates ----------
    data.total_sessions = len(data.sessions)
    data.total_agents = len(data.agents)
    data.total_findings = len(data.findings)
    data.total_blocks = len(data.gateway_blocks)
    data.total_cost_usd = round(sum(s.total_cost_usd for s in data.sessions), 6)

    sev_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
    cat_counts: dict[str, int] = {}
    flag_counts: dict[str, int] = {}
    for f in data.findings:
        sev_counts[f.severity] = sev_counts.get(f.severity, 0) + 1
        cat_counts[f.category] = cat_counts.get(f.category, 0) + 1
        flag_counts[f.flag] = flag_counts.get(f.flag, 0) + 1
    data.findings_by_severity = sev_counts
    data.findings_by_category = cat_counts
    data.flags_by_count = dict(
        sorted(flag_counts.items(), key=lambda kv: -kv[1])
    )

    owasp_counts: dict[str, int] = {row[0]: 0 for row in OWASP_ROWS}
    for f in data.findings:
        if f.owasp_id and f.owasp_id in owasp_counts:
            owasp_counts[f.owasp_id] += 1
    for v in data.verdicts:
        for flag in v.flags:
            if flag.startswith("owasp_llm") and flag in owasp_counts:
                owasp_counts[flag] += 1
                continue
            mapped = _FLAG_TO_OWASP.get(flag)
            if mapped:
                owasp_counts[mapped] += 1
    data.owasp_counts = owasp_counts

    return data
