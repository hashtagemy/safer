"""Deterministic security pattern rules for Inspector.

Each rule scans Python source (text + AST) and yields `PatternMatch`
records. No Claude calls here — every hit is evidence-backed. The rule
IDs are stable so downstream consumers (reports, policy suggester) can
reference them.

Rule taxonomy (stable IDs):

| Rule ID                 | Severity | Flag                       |
|-------------------------|----------|----------------------------|
| hardcoded_credential    | CRITICAL | credential_hardcoded       |
| shell_injection         | CRITICAL | shell_injection            |
| eval_exec_usage         | CRITICAL | eval_exec_usage            |
| os_system_call          | HIGH     | shell_injection            |
| insecure_deserialization| HIGH     | insecure_deserialization   |
| yaml_unsafe_load        | HIGH     | insecure_deserialization   |
| ssl_verify_disabled     | HIGH     | ssl_bypass                 |
| sql_string_injection    | HIGH     | sql_injection              |
| path_traversal_open     | MEDIUM   | path_traversal             |
| weak_hash_algorithm     | MEDIUM   | insecure_crypto            |
| plaintext_http_url      | LOW      | ssl_bypass                 |
| debug_mode_enabled      | LOW      | config_mismatch            |
"""

from __future__ import annotations

import ast
import re
from typing import Iterable

from ..models.findings import Severity
from ..models.inspector import PatternMatch


# Credential patterns — kept in one place so both regex and inline string
# checks share them.
_CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
)


def scan_patterns_project(files: list[tuple[str, str]]) -> list[PatternMatch]:
    """Run `scan_patterns` on every file, tagging each hit with its path."""
    all_matches: list[PatternMatch] = []
    for path, source in files:
        for match in scan_patterns(source):
            all_matches.append(match.model_copy(update={"file_path": path}))
    return all_matches


def scan_patterns(source: str) -> list[PatternMatch]:
    """Run every deterministic rule against `source` and return matches.

    Returns an empty list if the source is empty or cannot be parsed.
    Pure-text rules still run even when AST parsing fails.
    """
    matches: list[PatternMatch] = []
    if not source:
        return matches

    lines = source.splitlines()

    # Text-only rules (run even if AST parse fails)
    matches.extend(_rule_hardcoded_credential(source, lines))
    matches.extend(_rule_plaintext_http(source, lines))

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return matches

    matches.extend(_rule_eval_exec_usage(tree, lines))
    matches.extend(_rule_os_system_call(tree, lines))
    matches.extend(_rule_shell_injection(tree, lines))
    matches.extend(_rule_insecure_deserialization(tree, lines))
    matches.extend(_rule_yaml_unsafe_load(tree, lines))
    matches.extend(_rule_ssl_verify_disabled(tree, lines))
    matches.extend(_rule_sql_string_injection(tree, lines))
    matches.extend(_rule_path_traversal_open(tree, lines))
    matches.extend(_rule_weak_hash_algorithm(tree, lines))
    matches.extend(_rule_debug_mode_enabled(tree, lines))

    return matches


# ---------- helpers ----------


def _snippet(lines: list[str], line: int) -> str:
    if line <= 0 or line > len(lines):
        return ""
    return lines[line - 1].strip()[:200]


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _is_string_concat(node: ast.expr) -> bool:
    """True if `node` looks like a user-built string via + or f-string or %."""
    if isinstance(node, ast.JoinedStr):
        # f-string with at least one interpolation
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return True
    if isinstance(node, ast.Call):
        # "...".format(...) pattern
        if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            return True
    return False


# ---------- rules ----------


def _rule_hardcoded_credential(
    source: str, lines: list[str]
) -> Iterable[PatternMatch]:
    for kind, regex in _CREDENTIAL_PATTERNS:
        for m in regex.finditer(source):
            line = source.count("\n", 0, m.start()) + 1
            yield PatternMatch(
                rule_id="hardcoded_credential",
                severity=Severity.CRITICAL,
                flag="credential_hardcoded",
                line=line,
                snippet=_snippet(lines, line),
                message=f"Hard-coded {kind.replace('_', ' ')} detected in source.",
            )


def _rule_plaintext_http(source: str, lines: list[str]) -> Iterable[PatternMatch]:
    http_re = re.compile(r"""["']http://(?!localhost|127\.0\.0\.1|0\.0\.0\.0)[^"'\s]+""")
    for m in http_re.finditer(source):
        line = source.count("\n", 0, m.start()) + 1
        yield PatternMatch(
            rule_id="plaintext_http_url",
            severity=Severity.LOW,
            flag="ssl_bypass",
            line=line,
            snippet=_snippet(lines, line),
            message="Plaintext http:// URL — traffic can be intercepted or modified.",
        )


def _rule_eval_exec_usage(
    tree: ast.AST, lines: list[str]
) -> Iterable[PatternMatch]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func)
        if name in {"eval", "exec"} or name.endswith(".eval") or name.endswith(".exec"):
            yield PatternMatch(
                rule_id="eval_exec_usage",
                severity=Severity.CRITICAL,
                flag="eval_exec_usage",
                line=getattr(node, "lineno", 0),
                snippet=_snippet(lines, getattr(node, "lineno", 0)),
                message=f"Use of {name}() — executes arbitrary code.",
            )


def _rule_os_system_call(
    tree: ast.AST, lines: list[str]
) -> Iterable[PatternMatch]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func)
        if name in {"os.system", "os.popen", "commands.getoutput"}:
            yield PatternMatch(
                rule_id="os_system_call",
                severity=Severity.HIGH,
                flag="shell_injection",
                line=getattr(node, "lineno", 0),
                snippet=_snippet(lines, getattr(node, "lineno", 0)),
                message=f"{name} spawns a shell — use subprocess with a list argv instead.",
            )


def _rule_shell_injection(
    tree: ast.AST, lines: list[str]
) -> Iterable[PatternMatch]:
    subprocess_methods = {
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.Popen",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func)
        if name not in subprocess_methods:
            continue
        shell_true = any(
            kw.arg == "shell"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in node.keywords
        )
        first_arg_is_str_concat = bool(
            node.args and _is_string_concat(node.args[0])
        )
        if shell_true or first_arg_is_str_concat:
            yield PatternMatch(
                rule_id="shell_injection",
                severity=Severity.CRITICAL,
                flag="shell_injection",
                line=getattr(node, "lineno", 0),
                snippet=_snippet(lines, getattr(node, "lineno", 0)),
                message=(
                    f"{name} invoked with "
                    + ("shell=True" if shell_true else "a built-up string command")
                    + " — command injection risk."
                ),
            )


def _rule_insecure_deserialization(
    tree: ast.AST, lines: list[str]
) -> Iterable[PatternMatch]:
    bad = {"pickle.load", "pickle.loads", "marshal.load", "marshal.loads", "shelve.open"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func)
        if name in bad:
            yield PatternMatch(
                rule_id="insecure_deserialization",
                severity=Severity.HIGH,
                flag="insecure_deserialization",
                line=getattr(node, "lineno", 0),
                snippet=_snippet(lines, getattr(node, "lineno", 0)),
                message=f"{name} can execute arbitrary code from untrusted input.",
            )


def _rule_yaml_unsafe_load(
    tree: ast.AST, lines: list[str]
) -> Iterable[PatternMatch]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func)
        if name != "yaml.load":
            continue
        has_safe_loader = any(
            kw.arg == "Loader"
            and isinstance(kw.value, (ast.Attribute, ast.Name))
            and "Safe" in _dotted_name(kw.value)
            for kw in node.keywords
        )
        if has_safe_loader:
            continue
        yield PatternMatch(
            rule_id="yaml_unsafe_load",
            severity=Severity.HIGH,
            flag="insecure_deserialization",
            line=getattr(node, "lineno", 0),
            snippet=_snippet(lines, getattr(node, "lineno", 0)),
            message="yaml.load without SafeLoader — use yaml.safe_load instead.",
        )


def _rule_ssl_verify_disabled(
    tree: ast.AST, lines: list[str]
) -> Iterable[PatternMatch]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if (
                kw.arg == "verify"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is False
            ):
                yield PatternMatch(
                    rule_id="ssl_verify_disabled",
                    severity=Severity.HIGH,
                    flag="ssl_bypass",
                    line=getattr(node, "lineno", 0),
                    snippet=_snippet(lines, getattr(node, "lineno", 0)),
                    message="TLS verification disabled — MITM attacks become trivial.",
                )


def _rule_sql_string_injection(
    tree: ast.AST, lines: list[str]
) -> Iterable[PatternMatch]:
    sql_methods = {"execute", "executemany", "executescript"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in sql_methods):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if _is_string_concat(first):
            yield PatternMatch(
                rule_id="sql_string_injection",
                severity=Severity.HIGH,
                flag="sql_injection",
                line=getattr(node, "lineno", 0),
                snippet=_snippet(lines, getattr(node, "lineno", 0)),
                message=f"{func.attr}() called with a built-up string — use parameterized queries.",
            )


def _rule_path_traversal_open(
    tree: ast.AST, lines: list[str]
) -> Iterable[PatternMatch]:
    """Flag open() calls whose path argument is a dynamic string build."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func)
        if name not in {"open", "io.open", "pathlib.Path", "os.path.join"}:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if _is_string_concat(first):
            yield PatternMatch(
                rule_id="path_traversal_open",
                severity=Severity.MEDIUM,
                flag="path_traversal",
                line=getattr(node, "lineno", 0),
                snippet=_snippet(lines, getattr(node, "lineno", 0)),
                message=f"{name}() receives a dynamically built path — sanitize to avoid traversal.",
            )


def _rule_weak_hash_algorithm(
    tree: ast.AST, lines: list[str]
) -> Iterable[PatternMatch]:
    weak = {"hashlib.md5", "hashlib.sha1", "md5", "sha1"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func)
        if name in weak:
            yield PatternMatch(
                rule_id="weak_hash_algorithm",
                severity=Severity.MEDIUM,
                flag="insecure_crypto",
                line=getattr(node, "lineno", 0),
                snippet=_snippet(lines, getattr(node, "lineno", 0)),
                message=f"{name} is cryptographically broken — use SHA-256 or better for security uses.",
            )


def _rule_debug_mode_enabled(
    tree: ast.AST, lines: list[str]
) -> Iterable[PatternMatch]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if (
                kw.arg == "debug"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
            ):
                yield PatternMatch(
                    rule_id="debug_mode_enabled",
                    severity=Severity.LOW,
                    flag="config_mismatch",
                    line=getattr(node, "lineno", 0),
                    snippet=_snippet(lines, getattr(node, "lineno", 0)),
                    message="debug=True — never enable in production.",
                )
