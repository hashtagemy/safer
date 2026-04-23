"""Shell tool — intentionally unsafe so Inspector has something to flag.

Do not reuse this pattern in real agents. `shell=True` with a user-
supplied command string is the textbook shell-injection vector; this
file exists only to exercise the SAFER Inspector end-to-end.
"""

from __future__ import annotations

import subprocess
from typing import Any


def tool(fn):
    fn._is_tool = True  # noqa: SLF001
    return fn


@tool
def run_shell(cmd: str) -> dict[str, Any]:
    """Execute a shell command and return its stdout/stderr.

    WARNING (intentional): `shell=True` lets a malicious prompt chain
    commands with `;`, `&&`, or backticks. SAFER's Inspector pattern
    rule `shell_injection` will fire on this call.
    """
    try:
        proc = subprocess.run(
            cmd,
            shell=True,  # Inspector flags this.
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[:4096],
        "stderr": proc.stderr[:4096],
    }
