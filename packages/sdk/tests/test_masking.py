"""Credential masking tests."""

from __future__ import annotations

from safer.masking import mask, mask_payload


def test_mask_anthropic_key():
    text = "My key is sk-ant-api03-abcdefg1234567890hijklmnop_qrstuvwxy"
    out = mask(text)
    assert "sk-ant-" not in out
    assert "<REDACTED:ANTHROPIC_KEY>" in out


def test_mask_openai_key():
    text = "Key: sk-proj-abcdefg1234567890hijklmnop_qrstuvwxy"
    out = mask(text)
    assert "<REDACTED:OPENAI_KEY>" in out


def test_mask_aws_key():
    text = "use AKIAIOSFODNN7EXAMPLE in config"
    out = mask(text)
    assert "<REDACTED:AWS_KEY>" in out


def test_mask_github_token():
    text = "gh_token: ghp_abcdefghijklmnopqrstuvwxyz1234567890ABCD"
    out = mask(text)
    assert "<REDACTED:GITHUB_TOKEN>" in out


def test_mask_bearer():
    text = "Authorization: Bearer abcdef1234567890.XYZ_longenoughtoken=="
    out = mask(text)
    assert "<REDACTED:BEARER>" in out


def test_mask_generic_secret():
    text = 'password = "mysuper_secret_1234567890XYZ"'
    out = mask(text)
    # Either GENERIC_SECRET or the inner long string should be redacted.
    assert "mysuper_secret_1234567890XYZ" not in out or "<REDACTED" in out


def test_mask_idempotent():
    text = "sk-ant-abcdefghijklmnopqrstuvwxyz1234567890123456"
    once = mask(text)
    twice = mask(once)
    assert once == twice


def test_mask_payload_recursive():
    payload = {
        "prompt": "use sk-ant-abcdefghijklmnopqrstuvwxyz1234567890123456",
        "nested": {
            "aws": "AKIAIOSFODNN7EXAMPLE",
            "list": ["ghp_abcdefghijklmnopqrstuvwxyz1234567890ABCD"],
        },
    }
    out = mask_payload(payload)
    assert "sk-ant-" not in out["prompt"]
    assert "AKIA" not in out["nested"]["aws"]
    assert "ghp_" not in out["nested"]["list"][0]


def test_mask_preserves_non_secrets():
    text = "hello world"
    assert mask(text) == "hello world"
