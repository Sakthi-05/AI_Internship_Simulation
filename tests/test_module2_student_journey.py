"""End-to-end Module 2 student journey against the guarded test database."""
from datetime import datetime, timezone
import io
import uuid

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.config import settings
from app.main import app
from app.models.enums import TaskStatus
from app.models.intelligence import StudentProfile
from app.models.simulation import Simulation
from app.models.sprint import Sprint
from app.models.task import Task, TaskDependency
from app.models.task_path import SimulationTaskPath
from app.services.ai.ai_service import AIService, get_ai_service


class _JourneyReviewer(AIService):
    """Deterministic provider-shaped reviewer: request one rework, then approve."""

    provider_name = "journey_test_provider"

    def __init__(self):
        self.request_rework = True

    async def generate_text(self, prompt, system_prompt=None):
        return "review"

    async def generate_json(self, prompt, system_prompt=None):
        marker = "ACCEPTANCE CRITERIA:\n"
        criteria = []
        if marker in prompt:
            section = prompt.split(marker, 1)[1]
            for boundary in ("\nSUBMITTED FILES:", "\nPREVIOUS REVIEW:", "\nSUBMISSION:"):
                section = section.split(boundary, 1)[0]
            criteria = [line[2:].strip() for line in section.splitlines() if line.startswith("- ")]
        changes = self.request_rework and "JOURNEY_INITIAL_ATTEMPT" in prompt
        self.request_rework = False
        return {
            "result": "changes_required" if changes else "approved",
            "score": 45 if changes else 94,
            "severity": "warning" if changes else "info",
            "summary": "Revise the first attempt." if changes else "All requirements are satisfied.",
            "strengths": ["Clear implementation plan"],
            "issues": ["Add an edge case test"] if changes else [],
            "required_changes": ["Add an edge case test."] if changes else [],
            "acceptance_criteria": [
                {"criterion": item, "met": not changes, "evidence": "Evidence included in submitted deliverable."}
                for item in criteria
            ],
            "code_quality": {"score": 94, "feedback": "Readable."},
            "testing": {"score": 94, "feedback": "Covered."},
            "security": {"score": 94, "feedback": "No issue found."},
            "performance": {"score": 94, "feedback": "Within scope."},
        }


def _headers(student_id):
    from jose import jwt

    token = jwt.encode(
        {"sub": student_id, "student_id": student_id, "role": "student"},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


def _submit(client, headers, simulation_id, task_id, marker):
    source = f"# {marker}\ndef deliverable():\n    return {'validated'}\n".encode()
    response = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/submit",
        headers=headers,
        files=[("files", ("deliverable.py", io.BytesIO(source), "text/x-python"))],
        data={"metadata": '{"response":"Implementation, edge cases, and verification evidence"}'},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_student_completes_persisted_four_sprint_simulation_normally(client, monkeypatch):
    student_id = f"module2_journey_{uuid.uuid4().hex[:10]}"
    headers = _headers(student_id)

    # Module 1's assessed profile is the source for simulation initialization.
    db = SessionLocal()
    try:
        db.add(StudentProfile(
            student_id=student_id,
            full_name="Journey Student",
            role="Backend Developer",
            domain="healthcare",
            skills=["Python", "FastAPI", "PostgreSQL"],
            baseline_competencies={"technical_knowledge": 82, "problem_solving": 41},
            current_competencies={"technical_knowledge": 82, "problem_solving": 41},
            recommended_difficulty="intermediate",
            recommended_parameters={
                "role": "Backend Developer", "technology_stack": ["Python", "FastAPI", "PostgreSQL"],
                "difficulty": "intermediate", "company_type": "healthcare",
                "focus_competencies": ["problem_solving"], "career_goal": "Backend internship",
            },
            last_assessed_at=datetime.now(timezone.utc),
        ))
        db.commit()
    finally:
        db.close()

    created = client.post("/api/generate", headers=headers, json={
        "student_id": student_id, "role": "Frontend Developer", "technology_stack": ["JavaScript"],
        "difficulty": "beginner", "company_type": "startup",
    })
    assert created.status_code == 201, created.text
    data = created.json()
    assert data["role"] == "Backend Developer"
    sim_id, project_id, company_id = data["simulation_id"], data["project_id"], data["company_id"]
    company = client.get(f"/api/companies/{company_id}", headers=headers)
    project = client.get(f"/api/projects/{project_id}", headers=headers)
    assert company.status_code == 200 and company.json()["name"]
    assert project.status_code == 200 and project.json()["title"]

    sprint_response = client.get(f"/api/projects/{project_id}/sprints", headers=headers)
    assert sprint_response.status_code == 200
    sprints = sprint_response.json()
    assert [(item["sprint_number"], item["name"]) for item in sprints] == [
        (1, "Sprint 1: Foundation and Onboarding"),
        (2, "Sprint 2: Core Implementation"),
        (3, "Sprint 3: Advanced Implementation and Workplace Challenges"),
        (4, "Sprint 4: Integration and Project Completion"),
    ]

    # Module 3 owns this evaluation. Its route persists the result consumed by
    # the existing Module 2 dynamic task generator below.
    import app.api.routes.intelligence as intelligence

    async def module3_evaluation(_db, _simulation, _interaction, _response):
        return {
            "overall_score": 91, "competencies": {"problem_solving": 91, "communication": 88},
            "strengths": ["risk analysis"], "weaknesses": ["documentation"], "mistakes": [],
            "feedback": "Strong, evidence-based response.", "recommendations": ["Document the decision"],
            "branch_selected": "high_performance", "difficulty_delta": 1, "confidence": 0.9,
        }

    monkeypatch.setattr(intelligence, "evaluate_interaction", module3_evaluation)
    situation = client.get(f"/api/intelligence/simulations/{sim_id}/adaptive/next", headers=headers)
    assert situation.status_code == 200, situation.text
    evaluated = client.post(
        f"/api/intelligence/simulations/{sim_id}/adaptive/respond", headers=headers,
        json={"response": "I will assess the impact, communicate the risk, test the change, and document the decision."},
    )
    assert evaluated.status_code == 200, evaluated.text
    assert evaluated.json()["evaluation"]["branch_selected"] == "high_performance"

    # Start in normal dynamic mode. The first persisted task must consume the
    # Module 3 evaluation already written through Module 3's endpoint.
    started = client.post(f"/api/simulations/{sim_id}/start", headers=headers, json={})
    assert started.status_code == 200, started.text
    path = client.get(f"/api/simulations/{sim_id}/task-path", headers=headers).json()
    assert len(path) == 1
    assert path[0]["status"] == "todo"
    current_task = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers)
    assert current_task.status_code == 200, current_task.text
    assert current_task.json()["description"]
    assert "objectives" in current_task.json()
    assert "deliverables" in current_task.json()
    assert current_task.json()["acceptance_criteria"]
    db = SessionLocal()
    try:
        first_dynamic_task = db.get(Task, uuid.UUID(path[0]["task_id"]))
        module3_evidence = first_dynamic_task.task_metadata["module3_progression_evidence"]
        assert module3_evidence["branch_selected"] == "high_performance"
        assert module3_evidence["evaluations"]
        assert first_dynamic_task.task_metadata["objectives"]
        assert first_dynamic_task.task_metadata["deliverables"]
    finally:
        db.close()

    # Persist actual chat, meeting response, notification and timeline data.
    chat = client.post(
        f"/api/companies/{company_id}/chat/manager", headers=headers,
        json={"content": "I have reviewed the project goal and will raise blockers promptly.", "idempotency_key": "journey-chat"},
    )
    assert chat.status_code == 201, chat.text
    meetings = client.get(f"/api/projects/{project_id}/meetings", headers=headers)
    assert meetings.status_code == 200
    meeting = next(item for item in meetings.json() if item["meeting_type"] == "sprint_planning")
    response = client.post(
        f"/api/projects/{project_id}/meetings/{meeting['id']}/respond", headers=headers,
        json={"response": "I will bring the implementation plan and risk list.", "idempotency_key": "journey-meeting"},
    )
    assert response.status_code == 200, response.text

    reviewer = _JourneyReviewer()
    monkeypatch.setattr(intelligence, "evaluate_interaction", module3_evaluation)

    # Rework the first task, then complete each task through submit -> technical
    # review -> manager review. Backend progression chooses the next unlocked task.
    for index in range(4):
        current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers)
        assert current.status_code == 200, current.text
        task_id = current.json()["task_id"]
        db = SessionLocal()
        try:
            active_task = db.get(Task, uuid.UUID(task_id))
            active_sprint = db.get(Sprint, active_task.sprint_id)
            assert active_sprint.sprint_number == index + 1
            if index:
                dependency_ids = db.execute(
                    select(TaskDependency.depends_on_task_id).where(TaskDependency.task_id == active_task.id)
                ).scalars().all()
                assert all(db.get(Task, dep_id).status == TaskStatus.COMPLETED for dep_id in dependency_ids)
        finally:
            db.close()
        task = client.post(f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=headers)
        assert task.status_code == 200, task.text
        # An event response is persisted with simulation/task context when the
        # active task has a contextual stakeholder message.
        events = client.get(f"/api/simulations/{sim_id}/tasks/{task_id}/events", headers=headers)
        assert events.status_code == 200, events.text
        stakeholder = next((item for item in events.json()["events"] if item["event_type"] == "stakeholder_message"), None)
        if index == 0:
            assert stakeholder is not None, "The active task should include a contextual stakeholder event"
        if stakeholder:
            event_reply = client.post(
                f"/api/simulations/{sim_id}/tasks/{task_id}/events/{stakeholder['id']}/respond",
                headers=headers, data={"response": "I will verify the requirement, document the impact, and share a safe implementation plan."},
            )
            assert event_reply.status_code == 200, event_reply.text
            if index == 0:
                refreshed_events = client.get(
                    f"/api/simulations/{sim_id}/tasks/{task_id}/events", headers=headers,
                ).json()["events"]
                requirement = next((item for item in refreshed_events if item["event_type"] == "requirement_change"), None)
                assert requirement is not None, "A substantive stakeholder response should raise a versioned requirement change"
                acknowledged = client.post(
                    f"/api/simulations/{sim_id}/tasks/{task_id}/events/{requirement['id']}/acknowledge", headers=headers,
                )
                assert acknowledged.status_code == 200, acknowledged.text
                changed_requirement = requirement["updated_requirement"]
                requirement_reply = client.post(
                    f"/api/simulations/{sim_id}/tasks/{task_id}/events/{requirement['id']}/respond",
                    headers=headers,
                    data={"response": f"I will implement this update and verify the impact: {changed_requirement}"},
                )
                assert requirement_reply.status_code == 200, requirement_reply.text
                adapted_work = client.post(
                    f"/api/simulations/{sim_id}/tasks/{task_id}/events/{requirement['id']}/actions",
                    headers=headers,
                    json={"action": "adapted_work", "detail": f"I implemented and tested: {changed_requirement}"},
                )
                assert adapted_work.status_code == 200, adapted_work.text
                assert adapted_work.json()["assessment"] is None
        attempt_marker = "JOURNEY_INITIAL_ATTEMPT" if index == 0 else f"JOURNEY_ATTEMPT_{index}"
        submitted = _submit(client, headers, sim_id, task_id, attempt_marker)
        app.dependency_overrides[get_ai_service] = lambda: reviewer
        try:
            reviewed = client.post(
                f"/api/projects/{project_id}/reviews/versions/{submitted['version_id']}", headers=headers,
            )
        finally:
            app.dependency_overrides.pop(get_ai_service, None)
        assert reviewed.status_code == 201, reviewed.text
        awaiting_manager_state = client.get(f"/api/simulations/{sim_id}/progress", headers=headers)
        assert awaiting_manager_state.status_code == 200
        assert awaiting_manager_state.json()["status"] == "in_progress"
        assert awaiting_manager_state.json()["completed_at"] is None
        if index > 0:
            awaiting_manager_path = client.get(f"/api/simulations/{sim_id}/task-path", headers=headers)
            assert awaiting_manager_path.status_code == 200
            assert len(awaiting_manager_path.json()) == index + 1
            still_current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers)
            assert still_current.json()["task_id"] == task_id
        manager = client.post(
            f"/api/projects/{project_id}/reviews/versions/{submitted['version_id']}/manager-review", headers=headers,
        )
        assert manager.status_code == 201, manager.text
        if index == 0:
            assert manager.json()["manager_result"] == "changes_required"
            assert len(client.get(f"/api/simulations/{sim_id}/task-path", headers=headers).json()) == 1
            assert client.post(f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=headers).status_code == 200
            resubmitted = _submit(client, headers, sim_id, task_id, "JOURNEY_RESUBMISSION")
            app.dependency_overrides[get_ai_service] = lambda: reviewer
            try:
                rereview = client.post(
                    f"/api/projects/{project_id}/reviews/versions/{resubmitted['version_id']}", headers=headers,
                )
            finally:
                app.dependency_overrides.pop(get_ai_service, None)
            assert rereview.status_code == 201, rereview.text
            manager = client.post(
                f"/api/projects/{project_id}/reviews/versions/{resubmitted['version_id']}/manager-review", headers=headers,
            )
            assert manager.status_code == 201, manager.text
            assert manager.json()["manager_result"] == "approved"

        expected_path_size = index + 2 if index < 3 else 4
        advanced_path = client.get(f"/api/simulations/{sim_id}/task-path", headers=headers)
        assert advanced_path.status_code == 200
        assert len(advanced_path.json()) == expected_path_size

        if index < 3:
            in_progress = client.get(f"/api/simulations/{sim_id}/progress", headers=headers)
            assert in_progress.status_code == 200, in_progress.text
            assert in_progress.json()["status"] == "in_progress"
            assert in_progress.json()["completed_at"] is None

    persisted = client.get(f"/api/simulations/{sim_id}", headers=headers)
    if persisted.status_code == 404:
        persisted = client.get(f"/api/simulations/{sim_id}/progress", headers=headers)
    assert persisted.status_code == 200, persisted.text
    state = client.get(f"/api/simulations/{sim_id}/progress", headers=headers)
    assert state.status_code == 200, state.text
    assert state.json()["status"] == "completed"
    assert state.json()["overall_progress"] == 100
    assert state.json()["completed_at"]
    completed_sprints = client.get(f"/api/projects/{project_id}/sprints", headers=headers).json()
    assert [item["status"] for item in completed_sprints] == ["completed"] * 4
    notifications = client.get(f"/api/companies/{company_id}/notifications", headers=headers)
    assert notifications.status_code == 200, notifications.text
    assert notifications.json()
    timeline = client.get(f"/api/projects/{project_id}/timeline", headers=headers)
    assert timeline.status_code == 200, timeline.text
    assert timeline.json()
    refreshed_path = client.get(f"/api/simulations/{sim_id}/task-path", headers=headers)
    assert refreshed_path.status_code == 200
    assert len(refreshed_path.json()) == 4
    assert all(item["status"] == "completed" for item in refreshed_path.json())
    assert client.post(f"/api/simulations/{sim_id}/start", headers=headers).status_code == 409

    db = SessionLocal()
    try:
        simulation = db.get(Simulation, uuid.UUID(sim_id))
        last_entry = db.execute(
            select(SimulationTaskPath)
            .where(SimulationTaskPath.simulation_id == simulation.id)
            .order_by(SimulationTaskPath.sequence.desc())
        ).scalars().first()
        from app.services.simulation.progression_service import progress_to_next_task
        assert __import__("asyncio").run(
            progress_to_next_task(db, simulation, last_entry.task, student_id)
        ) is None
        from app.services.generators.dynamic_task_generator import generate_dynamic_task
        with pytest.raises(ValueError, match="completed simulation"):
            __import__("asyncio").run(generate_dynamic_task(
                db, simulation, db.get(Sprint, last_entry.task.sprint_id), student_id, last_entry.task,
            ))
        db.refresh(simulation)
        assert simulation.status.value == "completed"
        assert simulation.completed_at is not None
    finally:
        db.close()
