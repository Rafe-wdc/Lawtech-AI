"""Integration tests for the Lawtech-AI API.

Tests real HTTP behavior against a running server — auth, validation,
health check, SSE streaming, and rate limiting.  Does NOT assert on LLM
output content (those are in tests/test_agents.py); this file only checks
that the API layer behaves correctly.

Usage:
    # Against local dev server (open mode):
    pytest tests/integration/test_api.py -v

    # Against test server with auth:
    pytest tests/integration/test_api.py -v \
        --api-url https://tool.lawttorney.com \
        --api-key YOUR_KEY \
        --admin-key YOUR_ADMIN_KEY

Environment variables (alternative to CLI flags):
    API_URL      base URL of the server
    API_KEY      valid user API key
    ADMIN_KEY    valid admin API key
"""

from __future__ import annotations

import json
import os
import time
import pytest
import requests

# Fixtures (base_url, api_key, admin_key, user_headers) are in conftest.py


@pytest.fixture(scope="session")
def admin_headers(admin_key):
    return {"X-API-Key": admin_key} if admin_key else {}


# ── Helper ────────────────────────────────────────────────────────────────────

def collect_sse(response, max_events: int = 20, timeout: float = 30.0) -> list[dict]:
    """Read SSE events from a streaming response. Returns parsed data payloads."""
    events = []
    deadline = time.time() + timeout
    for line in response.iter_lines():
        if time.time() > deadline:
            break
        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                pass
        if len(events) >= max_events:
            break
    return events


# ── Test 1: Health check ──────────────────────────────────────────────────────

def test_health_returns_200(base_url):
    """GET /pyapi/health must return 200 with a JSON body."""
    r = requests.get(f"{base_url}/health", timeout=10)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert "status" in body
    assert body["status"] in ("healthy", "degraded", "unhealthy")


def test_health_has_required_keys(base_url):
    """Health response must include checks and uptime_seconds."""
    r = requests.get(f"{base_url}/health", timeout=10)
    body = r.json()
    assert "checks" in body
    assert "uptime_seconds" in body


def test_health_head_method(base_url):
    """HEAD /pyapi/health must work (used by load balancers)."""
    r = requests.head(f"{base_url}/health", timeout=10)
    assert r.status_code == 200


# ── Test 2: Frontend ──────────────────────────────────────────────────────────

def test_frontend_served(base_url):
    """GET / must serve the frontend HTML (local dev only)."""
    if "/pyapiv2" in base_url or "/pyapi" in base_url:
        pytest.skip("Frontend served by nginx, not through API prefix")
    r = requests.get(f"{base_url}/", timeout=10)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


# ── Test 3: Auth enforcement ──────────────────────────────────────────────────

def test_search_requires_auth_when_keys_configured(base_url, api_key):
    """POST /pyapi/search/stream with no key → 401 when auth is enabled."""
    if not api_key:
        pytest.skip("API_KEY not set — server running in open dev mode")

    r = requests.post(
        f"{base_url}/search/stream",
        json={"Promptquery": "test"},
        headers={},          # deliberately no X-API-Key
        stream=True,
        timeout=10,
    )
    assert r.status_code == 401, f"Expected 401 without key, got {r.status_code}"


def test_search_rejects_wrong_key(base_url, api_key):
    """POST /pyapi/search/stream with a wrong key → 401."""
    if not api_key:
        pytest.skip("API_KEY not set — server running in open dev mode")

    r = requests.post(
        f"{base_url}/search/stream",
        json={"Promptquery": "test"},
        headers={"X-API-Key": "definitely-wrong-key-xyz"},
        stream=True,
        timeout=10,
    )
    assert r.status_code == 401, f"Expected 401 for wrong key, got {r.status_code}"


def test_valid_key_accepted(base_url, user_headers):
    """POST /pyapi/search/stream with valid key → not 401/403."""
    r = requests.post(
        f"{base_url}/search/stream",
        json={"Promptquery": "What is Section 302 IPC?"},
        headers=user_headers,
        stream=True,
        timeout=15,
    )
    assert r.status_code not in (401, 403), (
        f"Valid key was rejected with {r.status_code}: {r.text[:200]}"
    )
    r.close()


# ── Test 4: Request validation ────────────────────────────────────────────────

def test_empty_query_rejected(base_url, user_headers):
    """Empty query string → 422 validation error."""
    r = requests.post(
        f"{base_url}/search/stream",
        json={"Promptquery": ""},
        headers=user_headers,
        timeout=10,
    )
    assert r.status_code == 422, f"Expected 422 for empty query, got {r.status_code}"


def test_missing_query_rejected(base_url, user_headers):
    """Missing query field → 422 validation error."""
    r = requests.post(
        f"{base_url}/search/stream",
        json={},
        headers=user_headers,
        timeout=10,
    )
    assert r.status_code == 422, f"Expected 422 for missing query, got {r.status_code}"


def test_overlength_query_rejected(base_url, user_headers):
    """Query exceeding max_length=200000 → 422.

    Schema allows up to 200000 chars; sending 200001 should be rejected.
    Uses stream=True and reads only headers so we never block on a
    stream body that a (wrongly) accepted query would start producing.
    """
    r = requests.post(
        f"{base_url}/search/stream",
        json={"Promptquery": "x" * 200001},
        headers=user_headers,
        stream=True,
        timeout=15,
    )
    try:
        assert r.status_code == 422, f"Expected 422 for over-length query, got {r.status_code}"
    finally:
        r.close()


# ── Test 5: SSE streaming ─────────────────────────────────────────────────────

def test_stream_returns_sse_events(base_url, user_headers):
    """A valid query must stream at least one SSE data event within 30s."""
    r = requests.post(
        f"{base_url}/search/stream",
        json={"Promptquery": "What is bail?"},
        headers=user_headers,
        stream=True,
        timeout=35,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert "text/event-stream" in r.headers.get("content-type", ""), (
        "Response must be SSE (text/event-stream)"
    )
    events = collect_sse(r, max_events=5, timeout=30)
    assert len(events) > 0, "No SSE events received within 30s"
    r.close()


def test_stream_has_status_event(base_url, user_headers):
    """SSE stream must include at least one status/agent event."""
    r = requests.post(
        f"{base_url}/search/stream",
        json={"Promptquery": "Define res judicata"},
        headers=user_headers,
        stream=True,
        timeout=35,
    )
    assert r.status_code == 200
    events = collect_sse(r, max_events=10, timeout=30)
    r.close()
    types = {e.get("type") for e in events}
    # Must have at least one of: status, agent, result, response, error
    assert types & {"status", "agent", "result", "response", "error"}, (
        f"Expected status/agent/result/response events, got types: {types}"
    )


def test_stream_final_event_has_result(base_url, user_headers):
    """SSE stream must eventually emit a 'response' type event with content."""
    r = requests.post(
        f"{base_url}/search/stream",
        json={"Promptquery": "What is Section 302 IPC?"},
        headers=user_headers,
        stream=True,
        timeout=60,
    )
    assert r.status_code == 200
    events = collect_sse(r, max_events=50, timeout=55)
    r.close()
    result_events = [e for e in events if e.get("type") in ("result", "response")]
    assert len(result_events) > 0, (
        f"No 'result'/'response' event found. Got: {[e.get('type') for e in events]}"
    )
    # Result must have non-empty response text
    evt = result_events[-1]
    assert evt.get("content") or evt.get("response") or evt.get("answer") or evt.get("data"), (
        f"Result event has no response text: {evt}"
    )


# ── Test 6: Error response format ────────────────────────────────────────────

def test_error_response_is_json(base_url, user_headers):
    """All error responses must be JSON with error/message fields."""
    r = requests.post(
        f"{base_url}/search/stream",
        json={"Promptquery": ""},
        headers=user_headers,
        timeout=10,
    )
    assert r.status_code == 422
    body = r.json()
    assert "error" in body or "detail" in body, (
        f"Error response missing 'error' or 'detail' field: {body}"
    )


# ── Test 7: Admin endpoints ───────────────────────────────────────────────────

def test_admin_requires_key(base_url, admin_key):
    """Admin endpoints reject requests with no key."""
    if not admin_key:
        pytest.skip("ADMIN_KEY not set")
    r = requests.get(f"{base_url}/admin/usage_stats", timeout=10)
    assert r.status_code in (401, 501), (
        f"Admin endpoint without key should return 401 or 501, got {r.status_code}"
    )


def test_admin_usage_with_key(base_url, admin_headers, admin_key):
    """GET /pyapi/admin/usage with valid admin key → 200 with stats."""
    if not admin_key:
        pytest.skip("ADMIN_KEY not set")
    r = requests.get(
        f"{base_url}/admin/usage_stats",
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert "period_days" in body or "totals" in body, (
        f"Unexpected usage response shape: {list(body.keys())}"
    )


# ── Test 8: Feedback endpoint ─────────────────────────────────────────────────

def test_feedback_validation(base_url, user_headers):
    """POST /pyapi/feedback with invalid rating → 422."""
    r = requests.post(
        f"{base_url}/feedback",
        json={"thread_id": "test-thread", "turn_number": 1, "rating": "meh"},
        headers=user_headers,
        timeout=10,
    )
    assert r.status_code == 422, f"Expected 422 for bad rating, got {r.status_code}"


# ── Test 9: Non-legal query is handled ───────────────────────────────────────

def test_non_legal_query_handled(base_url, user_headers):
    """Non-legal queries must be handled gracefully (not 500)."""
    r = requests.post(
        f"{base_url}/search/stream",
        json={"Promptquery": "What is the capital of France?"},
        headers=user_headers,
        stream=True,
        timeout=35,
    )
    assert r.status_code == 200, f"Non-legal query caused {r.status_code}"
    events = collect_sse(r, max_events=20, timeout=30)
    r.close()
    assert len(events) > 0, "No events returned for non-legal query"
    # Should NOT be a 500 error event
    error_events = [e for e in events if e.get("type") == "error" and
                    "500" in str(e.get("message", ""))]
    assert len(error_events) == 0, f"Got 500 error for non-legal query: {error_events}"


# ── Test 10: Thread persistence ───────────────────────────────────────────────

def test_thread_id_accepted(base_url, user_headers):
    """Providing a globalThreadId must not cause errors."""
    r = requests.post(
        f"{base_url}/search/stream",
        json={"Promptquery": "What is bail?", "globalThreadId": "test-integration-thread-001"},
        headers=user_headers,
        stream=True,
        timeout=35,
    )
    assert r.status_code == 200, f"Thread ID rejected with {r.status_code}"
    events = collect_sse(r, max_events=5, timeout=30)
    r.close()
    assert len(events) > 0
