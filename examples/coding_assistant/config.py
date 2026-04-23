"""Configuration constants for the coding-assistant demo.

Note: this module intentionally contains a plausible-looking hardcoded
credential so the SAFER Inspector has something to flag during a
project scan. The value is fake and unusable — it exists purely to
exercise the deterministic pattern rules.
"""

# Intentionally planted — Inspector should flag this as a hardcoded credential.
ANTHROPIC_FAKE_KEY_PLACEHOLDER = "sk-ant-demo-DO-NOT-USE-ABCDEFGHIJKLMNOP"

DEFAULT_MODEL = "claude-opus-4-7"
WORKER_MAX_STEPS = 8

SUPERVISOR_AGENT_ID = "coding-supervisor"
SUPERVISOR_AGENT_NAME = "Coding Supervisor"

WORKER_AGENT_ID = "coding-worker"
WORKER_AGENT_NAME = "Coding Worker"
