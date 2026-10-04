"""Profile-driven simulations get a workplace scenario. Manager review reuses the technical review."""
import io
from datetime import datetime, timezone

import pytest
from jose import jwt
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models.intelligence import StudentProfile
from app.models.simulation import Scenario, ScenarioTask, WorkflowEvent
from app.services.ai.ai_service import MockAIService, get_ai_service
from app.services.engines.workflow_events_engine import trigger_requirement_change_event


def _headers(student_id):
    token = jwt.encode(
        {"sub": student_id, "student_id": student_id, "role": "student"},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_profile_generation_links_a_scenario_for_workflow_events(client):
    student_id = "profile_scenario_student"
    headers = _headers(student_id)
    db = SessionLocal()
    try:
        db.add(StudentProfile(
            student_id=student_id,
            role="Backend Developer",
            skills=["Python", "FastAPI"],
            baseline_competencies={"problem_solving": 40, "technical_knowledge": 80},
            current_competencies={"problem_solving": 40, "technical_knowledge": 80},
            recommended_difficulty="intermediate",
            recommended_parameters={
                "role": "Backend Developer",
                "technology_stack": ["Python", "FastAPI"],
                "difficulty": "intermediate",
                "company_type": "startup",
                "focus_competencies": ["problem_solving"],
            },
            last_assessed_at=datetime.now(timezone.utc),
        ))
        db.commit()
    finally:
        db.close()

    created = client.post(
        "/api/generate",
        headers=headers,
        json={
            "student_id": student_id,
            "role": "Frontend Developer",
            "technology_stack": ["JavaScript"],
            "difficulty": "beginner",
            "company_type": "startup",
        },
    )
    assert created.status_code == 201, created.text
    simulation_id = created.json()["simulation_id"]

    db = SessionLocal()
    try:
        scenarios = db.execute(select(Scenario).where(Scenario.simulation_id == simulation_id)).scalars().all()
        assert len(scenarios) == 1
        links = db.execute(select(func.count()).select_from(ScenarioTask).where(ScenarioTask.scenario_id == scenarios[0].id)).scalar_one()
        assert links > 0
    finally:
        db.close()

    started = client.post(f"/api/simulations/{simulation_id}/start", headers=headers, json={})
    assert started.status_code == 200, started.text
    scenario = client.get(f"/api/simulations/{simulation_id}/scenario", headers=headers)
    assert scenario.status_code == 200, scenario.text
    payload = scenario.json()
    assert payload["simulation_id"] == simulation_id
    assert payload["all_tasks"]

    db = SessionLocal()
    try:
        from app.models.simulation import Simulation
        from app.models.task import Task
        simulation = db.get(Simulation, simulation_id)
        task = db.get(Task, payload["all_tasks"][0]["task_id"])
        linked = db.get(Scenario, payload["scenario_id"])
        event = await trigger_requirement_change_event(
            db, simulation, task, linked, "Ship the original API.", "Also document the error cases.",
        )
        db.commit()
        stored = db.get(WorkflowEvent, event.id)
        assert stored is not None
        assert stored.scenario_id == linked.id
    finally:
        db.close()

    events = client.get(
        f"/api/simulations/{simulation_id}/tasks/{payload['all_tasks'][0]['task_id']}/events",
        headers=headers,
    )
    assert events.status_code == 200, events.text
    assert any(item["event_type"] == "requirement_change" for item in events.json()["events"])


def test_manager_review_does_not_call_gemini_again(client):
    student_id = "manager_reuse_student"
    headers = _headers(student_id)

    class CountingAI(MockAIService):
        json_calls = 0

        async def generate_json(self, prompt, system_prompt=None):
            CountingAI.json_calls += 1
            return await super().generate_json(prompt, system_prompt)

    counter = CountingAI()
    app.dependency_overrides[get_ai_service] = lambda: counter
    try:
        created = client.post(
            "/api/generate",
            headers=headers,
            json={
                "student_id": student_id,
                "role": "Backend Developer",
                "technology_stack": ["Python"],
                "difficulty": "intermediate",
                "company_type": "startup",
            },
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["project_id"]
        board = client.get(f"/api/projects/{project_id}/board", headers=headers).json()
        task_id = next(task["id"] for column in board.values() for task in column if not task["depends_on_task_ids"])
        started = client.post(f"/api/projects/{project_id}/submissions/tasks/{task_id}/start", headers=headers)
        assert started.status_code == 200, started.text
        submitted = client.post(
            f"/api/projects/{project_id}/submissions/tasks/{task_id}/submit",
            headers=headers,
            files={"files": ("main.py", io.BytesIO(b"def hello():\n    return 'ok'\n"), "text/x-python")},
        )
        assert submitted.status_code == 201, submitted.text
        version_id = submitted.json()["version_id"]
        before = CountingAI.json_calls
        reviewed = client.post(f"/api/projects/{project_id}/reviews/versions/{version_id}", headers=headers)
        assert reviewed.status_code == 201, reviewed.text
        managed = client.post(
            f"/api/projects/{project_id}/reviews/versions/{version_id}/manager-review",
            headers=headers,
        )
        assert managed.status_code == 201, managed.text
        assert CountingAI.json_calls - before == 1
        assert managed.json()["manager_result"] == reviewed.json()["result"] or (
            reviewed.json()["result"] == "rejected" and managed.json()["manager_result"] == "changes_required"
        )
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
