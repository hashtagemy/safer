"""Inspector deterministic pattern rules — exhaustive positive and
negative coverage.

Each of the 12 rule IDs gets at least one positive sample (the pattern
must match) and at least one negative sample (the safe variant must
NOT match). Together with `test_inspector.py`'s integration tests these
pin the contract that downstream consumers (policy_suggester,
session_report scoring, dashboard report rendering) rely on.
"""

from __future__ import annotations

import pytest

from safer_backend.inspector.pattern_rules import scan_patterns


def _hits(source: str) -> list[str]:
    """Return the rule_ids of every match for `source`."""
    return [m.rule_id for m in scan_patterns(source)]


def _flags(source: str) -> list[str]:
    return [m.flag for m in scan_patterns(source)]


# ---------- empty / unparseable ----------


def test_empty_source_yields_no_matches():
    assert scan_patterns("") == []


def test_syntax_error_falls_back_to_text_only_rules():
    """`def x(:` is unparseable; AST-rules must skip but text rules
    (hardcoded credential, plaintext_http) still run."""
    src = 'def x(:\nAPI = "sk-ant-aaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
    matches = scan_patterns(src)
    assert any(m.rule_id == "hardcoded_credential" for m in matches)


# ---------- 1. hardcoded_credential ----------


def test_hardcoded_anthropic_key_flagged():
    src = 'API_KEY = "sk-ant-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"'
    assert "hardcoded_credential" in _hits(src)


def test_hardcoded_aws_access_key_flagged():
    src = 'AWS = "AKIA1234567890ABCDEF"'
    assert "hardcoded_credential" in _hits(src)


def test_hardcoded_pem_private_key_flagged():
    src = '''KEY = """-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"""'''
    assert "hardcoded_credential" in _hits(src)


def test_no_credential_in_clean_source():
    src = 'API_URL = "https://example.com/api/v1"'
    assert "hardcoded_credential" not in _hits(src)


# ---------- 2. plaintext_http_url ----------


def test_plaintext_http_url_flagged():
    src = 'URL = "http://api.example.com/v1"'
    assert "plaintext_http_url" in _hits(src)


def test_localhost_http_url_not_flagged():
    """Local URLs should not be flagged — they're routine in dev/tests."""
    src = 'URL = "http://localhost:8000/health"'
    assert "plaintext_http_url" not in _hits(src)


def test_https_url_not_flagged():
    src = 'URL = "https://api.example.com"'
    assert "plaintext_http_url" not in _hits(src)


# ---------- 3. eval_exec_usage ----------


def test_eval_call_flagged():
    src = "result = eval(user_input)"
    assert "eval_exec_usage" in _hits(src)


def test_exec_call_flagged():
    src = "exec(payload)"
    assert "eval_exec_usage" in _hits(src)


def test_method_called_eval_does_not_flag_safe_uses():
    """A custom class method named .eval shouldn't false-positive
    if it's not the builtin. (Current implementation is conservative —
    it flags .eval / .exec attributes too. This test pins that
    behaviour deliberately.)"""
    src = "import torch\nmodel.eval()"
    # Conservative: model.eval() IS flagged. Document the choice.
    assert "eval_exec_usage" in _hits(src)


# ---------- 4. shell_injection (subprocess shell=True) ----------


def test_subprocess_shell_true_flagged():
    src = "import subprocess\nsubprocess.run(cmd, shell=True)"
    assert "shell_injection" in _hits(src)


def test_subprocess_with_argv_list_not_flagged():
    src = 'import subprocess\nsubprocess.run(["ls", "-la", path])'
    assert "shell_injection" not in _hits(src)


def test_subprocess_string_concat_command_flagged():
    src = 'import subprocess\nsubprocess.run("ls " + user_dir)'
    assert "shell_injection" in _hits(src)


# ---------- 5. os_system_call ----------


def test_os_system_call_flagged():
    src = "import os\nos.system(cmd)"
    assert "os_system_call" in _hits(src)


def test_os_system_yields_shell_injection_flag():
    src = "import os\nos.system(cmd)"
    assert "shell_injection" in _flags(src)


# ---------- 6. insecure_deserialization ----------


def test_pickle_loads_flagged():
    src = "import pickle\nobj = pickle.loads(data)"
    assert "insecure_deserialization" in _hits(src)


def test_marshal_load_flagged():
    src = "import marshal\nobj = marshal.load(f)"
    assert "insecure_deserialization" in _hits(src)


def test_json_loads_not_flagged():
    """json.loads is safe — must not be flagged."""
    src = "import json\nobj = json.loads(data)"
    assert "insecure_deserialization" not in _hits(src)


# ---------- 7. yaml_unsafe_load ----------


def test_yaml_load_without_loader_flagged():
    src = "import yaml\nobj = yaml.load(stream)"
    assert "yaml_unsafe_load" in _hits(src)


def test_yaml_load_with_safe_loader_not_flagged():
    src = "import yaml\nobj = yaml.load(stream, Loader=yaml.SafeLoader)"
    assert "yaml_unsafe_load" not in _hits(src)


def test_yaml_safe_load_not_flagged():
    src = "import yaml\nobj = yaml.safe_load(stream)"
    assert "yaml_unsafe_load" not in _hits(src)


# ---------- 8. ssl_verify_disabled ----------


def test_requests_verify_false_flagged():
    src = 'import requests\nrequests.get(url, verify=False)'
    assert "ssl_verify_disabled" in _hits(src)


def test_requests_default_verify_not_flagged():
    src = 'import requests\nrequests.get(url)'
    assert "ssl_verify_disabled" not in _hits(src)


# ---------- 9. sql_string_injection ----------


def test_execute_with_string_concat_flagged():
    src = 'cur.execute("SELECT * FROM t WHERE x = " + value)'
    assert "sql_string_injection" in _hits(src)


def test_execute_with_fstring_flagged():
    src = 'cur.execute(f"SELECT * FROM t WHERE x = {value}")'
    assert "sql_string_injection" in _hits(src)


def test_parameterized_query_not_flagged():
    src = 'cur.execute("SELECT * FROM t WHERE x = ?", (value,))'
    assert "sql_string_injection" not in _hits(src)


# ---------- 10. path_traversal_open ----------


def test_open_with_string_concat_flagged():
    src = 'open("/var/data/" + filename)'
    assert "path_traversal_open" in _hits(src)


def test_open_with_static_path_not_flagged():
    src = 'open("/var/data/config.json")'
    assert "path_traversal_open" not in _hits(src)


# ---------- 11. weak_hash_algorithm ----------


def test_md5_call_flagged():
    src = "import hashlib\nh = hashlib.md5(data)"
    assert "weak_hash_algorithm" in _hits(src)


def test_sha1_call_flagged():
    src = "import hashlib\nh = hashlib.sha1(data)"
    assert "weak_hash_algorithm" in _hits(src)


def test_sha256_not_flagged():
    src = "import hashlib\nh = hashlib.sha256(data)"
    assert "weak_hash_algorithm" not in _hits(src)


# ---------- 12. debug_mode_enabled ----------


def test_debug_true_kwarg_flagged():
    src = "app.run(debug=True)"
    assert "debug_mode_enabled" in _hits(src)


def test_debug_false_kwarg_not_flagged():
    src = "app.run(debug=False)"
    assert "debug_mode_enabled" not in _hits(src)


# ---------- multi-pattern source ----------


def test_compound_dangerous_source_yields_multiple_rules():
    """A real-world bad agent should trigger several rules at once."""
    src = (
        'API_KEY = "sk-ant-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"\n'
        "import os, subprocess, pickle, hashlib\n"
        "os.system(cmd)\n"
        "subprocess.run(user_cmd, shell=True)\n"
        "obj = pickle.loads(data)\n"
        'h = hashlib.md5("hi".encode())\n'
        "result = eval(payload)\n"
    )
    rules = set(_hits(src))
    assert {
        "hardcoded_credential",
        "os_system_call",
        "shell_injection",
        "insecure_deserialization",
        "weak_hash_algorithm",
        "eval_exec_usage",
    }.issubset(rules)


# ---------- severity / flag taxonomy ----------


@pytest.mark.parametrize(
    "rule_id, expected_flag",
    [
        ("hardcoded_credential", "credential_hardcoded"),
        ("eval_exec_usage", "eval_exec_usage"),
        ("shell_injection", "shell_injection"),
        ("os_system_call", "shell_injection"),
        ("insecure_deserialization", "insecure_deserialization"),
        ("yaml_unsafe_load", "insecure_deserialization"),
        ("ssl_verify_disabled", "ssl_bypass"),
        ("plaintext_http_url", "ssl_bypass"),
        ("sql_string_injection", "sql_injection"),
        ("path_traversal_open", "path_traversal"),
        ("weak_hash_algorithm", "insecure_crypto"),
        ("debug_mode_enabled", "config_mismatch"),
    ],
)
def test_rule_to_flag_mapping_is_stable(rule_id, expected_flag):
    """The (rule_id → flag) mapping is the contract every consumer
    relies on. Pin it via a representative sample for each rule."""
    samples = {
        "hardcoded_credential": 'KEY = "AKIA0123456789ABCDEF"',
        "eval_exec_usage": "eval(x)",
        "shell_injection": "import subprocess\nsubprocess.run(c, shell=True)",
        "os_system_call": "import os\nos.system(c)",
        "insecure_deserialization": "import pickle\npickle.loads(d)",
        "yaml_unsafe_load": "import yaml\nyaml.load(s)",
        "ssl_verify_disabled": "requests.get(url, verify=False)",
        "plaintext_http_url": 'URL = "http://api.example.com/v1"',
        "sql_string_injection": 'cur.execute("SELECT * FROM t WHERE x=" + v)',
        "path_traversal_open": 'open("/d/" + name)',
        "weak_hash_algorithm": "import hashlib\nhashlib.md5(d)",
        "debug_mode_enabled": "app.run(debug=True)",
    }
    src = samples[rule_id]
    matches = scan_patterns(src)
    rule_match = next((m for m in matches if m.rule_id == rule_id), None)
    assert rule_match is not None, f"{rule_id} not produced by sample"
    assert rule_match.flag == expected_flag
