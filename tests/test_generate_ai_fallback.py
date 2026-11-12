"""Generate must succeed even when Gemini is down (local fallback).

Overrides the AI service dependency with one that always fails like an
unreachable Gemini endpoint, then asserts /api/generate still returns a
complete, startable internship simulation — never a 5xx.
"""
import asyncio
import httpx
import uuid

from app.main import app
from app.services.ai.ai_service import AIService, MockAIService, get_ai_service
from app.api.routes import generate as generate_route
from app.api.routes import simulation as simulation_route
from app.services.generators import dynamic_task_generator


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


class _SuccessfulTaskGemini(AIService):
    async def generate_text(self, prompt, system_prompt=None):
        return "Provider generated"

    async def generate_json(self, prompt, system_prompt=None):
        return {
            "title": "Provider-specific onboarding task",
            "description": "Implement the requested project foundation.",
            "objectives": ["Create the foundation"],
            "deliverables": ["Working foundation"],
            "acceptance_criteria": ["Foundation is validated"],
            "dependency_strategy": "none",
            "priority": "medium",
            "task_type": "coding",
            "difficulty": "intermediate",
            "estimated_hours": 4,
            "hints": ["Follow the project conventions"],
            "recommended_team_role": "Backend Developer",
            "generation_reason": "Provider task",
        }


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


def _create_mock_generated_simulation(client, auth_headers):
    app.dependency_overrides[get_ai_service] = MockAIService
    try:
        response = client.post("/api/generate", headers=auth_headers, json=_payload())
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    assert response.status_code == 201, response.text
    return response.json()


def test_start_503_uses_persisted_deterministic_fallback(client, auth_headers, monkeypatch):
    generated = _create_mock_generated_simulation(client, auth_headers)
    monkeypatch.setattr(dynamic_task_generator, "get_ai_service", lambda: _OverloadedGemini())
    response = client.post(f"/api/simulations/{generated['simulation_id']}/start", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "in_progress"
    assert response.json()["current_task_id"]
    persisted = client.get(f"/api/simulations/{generated['simulation_id']}", headers=auth_headers)
    assert persisted.status_code == 200
    assert persisted.json()["current_task_id"] == response.json()["current_task_id"]


def test_start_provider_timeout_uses_fallback(client, auth_headers, monkeypatch):
    generated = _create_mock_generated_simulation(client, auth_headers)
    monkeypatch.setattr(simulation_route, "START_TASK_AI_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(dynamic_task_generator, "get_ai_service", lambda: _SlowGemini())
    response = client.post(f"/api/simulations/{generated['simulation_id']}/start", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["current_task_id"]


def test_start_successful_ai_uses_provider_task(client, auth_headers, monkeypatch):
    generated = _create_mock_generated_simulation(client, auth_headers)
    monkeypatch.setattr(dynamic_task_generator, "get_ai_service", lambda: _SuccessfulTaskGemini())
    response = client.post(f"/api/simulations/{generated['simulation_id']}/start", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["current_task"]["title"] == "Provider-specific onboarding task"
