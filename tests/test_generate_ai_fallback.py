"""Generate must succeed even when Gemini is down (local fallback).

Overrides the AI service dependency with one that always fails like an
unreachable Gemini endpoint, then asserts /api/generate still returns a
complete, startable internship simulation — never a 5xx.
"""
import httpx

from app.main import app
from app.services.ai.ai_service import AIService, get_ai_service


class _DeadGemini(AIService):
    """Simulates Gemini being down (connection-level failure)."""

    async def generate_text(self, prompt, system_prompt=None):
        raise httpx.ConnectError("gemini unreachable")

    async def generate_json(self, prompt, system_prompt=None):
        raise httpx.ConnectError("gemini unreachable")


class _OverloadedGemini(AIService):
    """Simulates Gemini returning HTTP 503 for every call."""

    async def generate_text(self, prompt, system_prompt=None):
        request = httpx.Request("POST", "https://generativelanguage.googleapis.com/test")
        raise httpx.HTTPStatusError(
            "overloaded", request=request, response=httpx.Response(503, request=request)
        )

    async def generate_json(self, prompt, system_prompt=None):
        return await self.generate_text(prompt, system_prompt)


def _payload(student_id="student_001"):
    return {
        "student_id": student_id,
        "role": "Backend Developer",
        "technology_stack": ["Python", "FastAPI", "PostgreSQL"],
        "difficulty": "intermediate",
        "company_type": "startup",
    }


def _assert_complete_simulation(client, auth_headers, data):
    assert data["task_count"] > 0
    assert len(data["sprint_ids"]) == 4
    assert data["title"]
    assert data["description"]
    assert data["duration_weeks"]
    # The simulation must be startable — the user journey continues.
    start = client.post(f"/api/simulations/{data['simulation_id']}/start", headers=auth_headers)
    assert start.status_code == 200, start.text
    company = client.get(f"/api/companies/{data['company_id']}", headers=auth_headers)
    assert company.status_code == 200, company.text
    assert company.json()["mission"]


def test_generate_succeeds_when_gemini_unreachable(client, auth_headers):
    app.dependency_overrides[get_ai_service] = lambda: _DeadGemini()
    try:
        resp = client.post("/api/generate", headers=auth_headers, json=_payload())
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    assert resp.status_code == 201, resp.text
    _assert_complete_simulation(client, auth_headers, resp.json())


def test_generate_succeeds_when_gemini_returns_503(client, auth_headers):
    app.dependency_overrides[get_ai_service] = lambda: _OverloadedGemini()
    try:
        resp = client.post("/api/generate", headers=auth_headers, json=_payload())
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    assert resp.status_code == 201, resp.text
    _assert_complete_simulation(client, auth_headers, resp.json())
