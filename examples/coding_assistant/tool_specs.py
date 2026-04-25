"""Anthropic tool-use schemas for the coding-assistant worker."""

from __future__ import annotations

from typing import Any

from tools.filesystem import grep_code, read_file, write_file
from tools.git import find_test_files, git_diff, git_log
from tools.shell import run_shell
from tools.web import fetch_url, search_web

TOOL_FUNCS: dict[str, Any] = {
    "read_file": read_file,
    "write_file": write_file,
    "grep_code": grep_code,
    "search_web": search_web,
    "fetch_url": fetch_url,
    "run_shell": run_shell,
    "git_diff": git_diff,
    "git_log": git_log,
    "find_test_files": find_test_files,
}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file from disk. Returns the first 8 KB.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Overwrite a file with new content. Creates parent directories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "grep_code",
        "description": "Search a file for lines matching a regex. Up to 50 hits.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["pattern", "path"],
        },
    },
    {
        "name": "search_web",
        "description": "Mock web-search. Returns canned results; does not actually hit the network.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": "Fetch the body of a URL (first 4 KB). 5s timeout.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "run_shell",
        "description": "Run a shell command and return stdout/stderr. Dangerous — prefer read_file / grep_code.",
        "input_schema": {
            "type": "object",
            "properties": {"cmd": {"type": "string"}},
            "required": ["cmd"],
        },
    },
    {
        "name": "git_diff",
        "description": (
            "Return up to 200 lines of `git diff` for the working tree, "
            "optionally scoped to a path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    },
    {
        "name": "git_log",
        "description": (
            "Return the most recent commits (one-line) — optionally "
            "scoped to a path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
    },
    {
        "name": "find_test_files",
        "description": (
            "Find test files under `root` (default `tests`) whose path "
            "or contents match `query`."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "root": {"type": "string"},
            },
            "required": ["query"],
        },
    },
]
