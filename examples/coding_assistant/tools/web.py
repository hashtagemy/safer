"""Web tools — intentionally contains patterns the Inspector should flag."""

from __future__ import annotations

from typing import Any

# Intentionally HTTP (not HTTPS) so Inspector's plaintext_http_url rule trips.
SEARCH_ENDPOINT = "http://internal-search.example/api/query"


def tool(fn):
    fn._is_tool = True  # noqa: SLF001
    return fn


@tool
def search_web(query: str) -> dict[str, Any]:
    """Pretend to call a search API. Demo-only mock; returns canned results."""
    canned = [
        {"title": f"Mock result A for {query!r}", "url": "https://example.com/a"},
        {"title": f"Mock result B for {query!r}", "url": "https://example.com/b"},
    ]
    return {"ok": True, "query": query, "results": canned, "endpoint": SEARCH_ENDPOINT}


@tool
def fetch_url(url: str) -> dict[str, Any]:
    """Fetch a URL's body with a 5s timeout.

    Intentionally disables TLS verification so the SAFER Inspector's
    `ssl_verify_disabled` rule has something to catch — do not copy
    this into real code.
    """
    try:
        import requests  # type: ignore[import-not-found]
    except ImportError:
        return {
            "ok": False,
            "error": "requests is not installed; run `uv sync` or use read_file instead",
        }
    try:
        # Inspector flags this line (verify=False).
        resp = requests.get(url, timeout=5, verify=False)
    except Exception as e:  # pragma: no cover — network
        return {"ok": False, "error": str(e)}
    body = resp.text
    return {
        "ok": resp.status_code < 400,
        "status": resp.status_code,
        "body": body[:4096],
        "truncated": len(body) > 4096,
    }
