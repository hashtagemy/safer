"""Deterministic tool risk classification.

Given a tool's name / signature / docstring, bucket it into Low / Medium
/ High / Critical. This is pure Python — no Claude calls. The rules are
intentionally conservative: better to over-flag a tool and let the user
downgrade than to miss a dangerous capability.

Heuristic buckets (first match wins):

- CRITICAL — code execution, payments, destructive infra
  (exec, eval, subprocess, shell, run_code, charge, wire_transfer,
   delete_user, drop_table, migrate, deploy)
- HIGH    — writes to sensitive state or sends data externally
  (send_email, send_sms, post_message, upload, publish,
   write_file, update_record, invoke_webhook)
- MEDIUM  — file system writes / database writes / third-party APIs
  (save_, store_, insert_, put_, patch_, fetch_external, call_api)
- LOW     — read-only or trivial side effects
  (get_, read_, list_, search_, lookup_, fetch_, find_)
"""

from __future__ import annotations

from ..models.inspector import ToolRiskClass

_CRITICAL_KEYWORDS: tuple[str, ...] = (
    "exec",
    "eval",
    "subprocess",
    "shell",
    "run_code",
    "run_command",
    "charge",
    "refund",
    "wire_transfer",
    "payment",
    "delete_user",
    "drop_table",
    "drop_database",
    "migrate_schema",
    "deploy",
    "rm_rf",
    "purge",
    "terminate_instance",
)

_HIGH_KEYWORDS: tuple[str, ...] = (
    "send_email",
    "send_sms",
    "send_slack",
    "send_message",
    "post_message",
    "post_to_",
    "notify_",
    "publish",
    "upload",
    "write_file",
    "update_record",
    "update_user",
    "invoke_webhook",
    "trigger_webhook",
    "create_issue",
    "create_pr",
    "merge_pr",
    "share_",
    "grant_",
    "revoke_",
)

_MEDIUM_KEYWORDS: tuple[str, ...] = (
    "save_",
    "store_",
    "insert_",
    "put_",
    "patch_",
    "update_",
    "fetch_external",
    "call_api",
    "http_post",
    "db_write",
    "enqueue",
    "schedule_",
    "write_",
)

_LOW_KEYWORDS: tuple[str, ...] = (
    "get_",
    "read_",
    "list_",
    "search_",
    "lookup_",
    "fetch_",
    "find_",
    "query_",
    "describe_",
    "show_",
    "count_",
    "exists_",
)


def classify_tool(
    *,
    name: str,
    signature: str = "",
    docstring: str | None = None,
) -> tuple[ToolRiskClass, str]:
    """Return (risk_class, reason) for a tool."""
    haystack = " ".join(
        filter(None, [name.lower(), signature.lower(), (docstring or "").lower()])
    )

    for kw in _CRITICAL_KEYWORDS:
        if kw in haystack:
            return ToolRiskClass.CRITICAL, f"matches critical keyword '{kw}'"
    for kw in _HIGH_KEYWORDS:
        if kw in haystack:
            return ToolRiskClass.HIGH, f"matches high-risk keyword '{kw}'"
    for kw in _MEDIUM_KEYWORDS:
        if kw in haystack:
            return ToolRiskClass.MEDIUM, f"matches medium-risk keyword '{kw}'"
    for kw in _LOW_KEYWORDS:
        if kw in haystack:
            return ToolRiskClass.LOW, f"matches read-only keyword '{kw}'"

    return ToolRiskClass.MEDIUM, "no strong keyword signal — defaulting to MEDIUM"
