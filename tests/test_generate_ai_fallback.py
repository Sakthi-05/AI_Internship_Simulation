"""Generate must succeed even when Gemini is down (local fallback).

Overrides the AI service dependency with one that always fails like an
unreachable Gemini endpoint, then asserts /api/generate still returns a
complete, startable internship simulation — never a 5xx.
"""
import asyncio
import httpx
import uuid

from app.main import app
from app.services.ai.ai_service import AIService, get_ai_service
from app.api.routes import generate as generate_route


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


class _SlowGemini(AIService):
    async def generate_text(self, prompt, system_prompt=None):
        await asyncio.sleep(1)
        return "provider response"

    async def generate_json(self, prompt, system_prompt=None):
        await asyncio.sleep(1)
        return {}


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


def test_generation_request_retry_returns_same_simulation(client, auth_headers):
    request_id = str(uuid.uuid4())
    payload = {**_payload(), "generation_request_id": request_id}
    first = client.post("/api/generate", headers=auth_headers, json=payload)
    second = client.post("/api/generate", headers=auth_headers, json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["simulation_id"] == second.json()["simulation_id"]
    mine = client.get("/api/simulations/mine", headers=auth_headers)
    assert mine.status_code == 200, mine.text
    matching = [item for item in mine.json() if item["simulation_id"] == first.json()["simulation_id"]]
    assert len(matching) == 1


def test_slow_generation_ai_uses_bounded_fallback(client, auth_headers, monkeypatch):
    monkeypatch.setattr(generate_route, "GENERATION_AI_TIMEOUT_SECONDS", 0.01)
    app.dependency_overrides[get_ai_service] = lambda: _SlowGemini()
    try:
        resp = client.post("/api/generate", headers=auth_headers, json=_payload())
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    assert resp.status_code == 201, resp.text
    assert resp.json()["task_count"] > 0
