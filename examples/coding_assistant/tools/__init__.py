"""Tool implementations for the coding-assistant demo.

Each function carries a `@tool` decorator so the SAFER Inspector picks
them up during an AST scan. A subset is intentionally unsafe (shell
injection, disabled SSL verification, plaintext HTTP) so the demo
exercises the Inspector's deterministic pattern rules end-to-end.
"""

from coding_assistant.tools.filesystem import grep_code, read_file, write_file
from coding_assistant.tools.shell import run_shell
from coding_assistant.tools.web import fetch_url, search_web

__all__ = [
    "read_file",
    "write_file",
    "grep_code",
    "search_web",
    "fetch_url",
    "run_shell",
]
