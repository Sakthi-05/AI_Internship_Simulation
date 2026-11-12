"""Module 2 uses the Module 1 competency profile when generating an internship."""
from datetime import datetime, timezone

from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.intelligence import StudentProfile
from app.models.simulation import Simulation


def _headers(student_id):
    token = jwt.encode(
        {"sub": student_id, "student_id": student_id, "role": "student"},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}, token


def test_assessed_profile_controls_generation_and_task_path(client):
    student_id = "module2_profile_student"
    headers, _token = _headers(student_id)
    db = SessionLocal()
    try:
        profile = StudentProfile(
            student_id=student_id,
            full_name="Module Two",
            role="Backend Developer",
            domain="healthcare",
            skills=["Python", "FastAPI", "PostgreSQL"],
            baseline_competencies={
                "technical_knowledge": 90,
                "problem_solving": 32,
                "decision_making": 70,
                "communication": 40,
                "critical_thinking": 75,
                "adaptability": 70,
                "time_management": 70,
                "professionalism": 80,
            },
            current_competencies={
                "technical_knowledge": 90,
                "problem_solving": 32,
                "decision_making": 70,
                "communication": 40,
                "critical_thinking": 75,
                "adaptability": 70,
                "time_management": 70,
                "professionalism": 80,
            },
            recommended_difficulty="advanced",
            recommended_parameters={
                "role": "Backend Developer",
                "technology_stack": ["Python", "FastAPI", "PostgreSQL"],
                "difficulty": "advanced",
                "company_type": "healthcare",
                "focus_competencies": ["problem_solving", "communication"],
                "career_goal": "Backend internship",
            },
            last_assessed_at=datetime.now(timezone.utc),
        )
        db.add(profile)
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
    body = created.json()
    assert body["difficulty"] == "advanced"
    assert body["company_type"] == "healthcare"
    assert body["role"] == "Backend Developer"
    assert "problem_solving" in body["focus_competencies"]
    assert body["simulation_id"]

    db = SessionLocal()
    try:
        saved_simulation = db.get(Simulation, body["simulation_id"])
        assert saved_simulation.student_id == student_id
    finally:
        db.close()

    company = client.get(f"/api/companies/{body['company_id']}", headers=headers)
    assert company.status_code == 200
    assert company.json()["size"]
    assert "Python" in company.json()["technology_stack"]

    project = client.get(f"/api/projects/{body['project_id']}", headers=headers)
    assert project.status_code == 200
    assert project.json()["difficulty"] == "advanced"

    sprints = client.get(f"/api/projects/{body['project_id']}/sprints", headers=headers)
    assert sprints.status_code == 200
    assert len(sprints.json()) == 4
    assert [sprint["name"] for sprint in sprints.json()] == [
        "Sprint 1: Foundation and Onboarding",
        "Sprint 2: Core Implementation",
        "Sprint 3: Advanced Implementation and Workplace Challenges",
        "Sprint 4: Integration and Project Completion",
    ]
    assert [sprint["sprint_number"] for sprint in sprints.json()] == [1, 2, 3, 4]
    assert sprints.json()[0]["goal"]
    assert sprints.json()[0]["start_date"]

    board = client.get(f"/api/projects/{body['project_id']}/board", headers=headers).json()
    titles = [task["title"] for column in board.values() for task in column]
    assert any(title.startswith("[Focus]") and "Debug" in title for title in titles)
    assert any(title.startswith("[Stretch]") for title in titles)

    started = client.post(f"/api/simulations/{body['simulation_id']}/start", headers=headers, json={})
    assert started.status_code == 200, started.text
    current = client.get(f"/api/simulations/{body['simulation_id']}/tasks/current", headers=headers)
    assert current.status_code == 200, current.text
    assert current.json()["task_id"]
    assert current.json()["objectives"]
    assert current.json()["deliverables"]
    assert current.json()["acceptance_criteria"]
    assert "dependencies" in current.json()

    db = SessionLocal()
    try:
        saved = db.execute(select(StudentProfile).where(StudentProfile.student_id == student_id)).scalar_one()
        assert saved.recommended_parameters["difficulty"] == "advanced"
    finally:
        db.close()


def test_websocket_accepts_the_owning_student(client):
    student_id = "module2_ws_student"
    headers, token = _headers(student_id)
    created = client.post(
        "/api/generate",
        headers=headers,
        json={
            "student_id": student_id,
            "role": "Backend Developer",
            "technology_stack": ["Python", "FastAPI"],
            "difficulty": "intermediate",
            "company_type": "startup",
        },
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company_id"]

    with client.websocket_connect(f"/ws/companies/{company_id}?token={token}") as socket:
        socket.send_json({"type": "ping"})

    other, _other_token = _headers("module2_ws_other")
    denied = client.websocket_connect(f"/ws/companies/{company_id}?token={other['Authorization'].split()[1]}")
    try:
        with denied as socket:
            socket.receive_text()
        assert False, "another student must not subscribe to this company"
    except Exception:
        pass


def test_websocket_receives_task_update_for_owning_student(client):
    student_id = "module2_ws_event_student"
    headers, token = _headers(student_id)
    created = client.post(
        "/api/generate",
        headers=headers,
        json={
            "student_id": student_id,
            "role": "Backend Developer",
            "technology_stack": ["Python", "FastAPI"],
            "difficulty": "intermediate",
            "company_type": "startup",
        },
    )
    assert created.status_code == 201, created.text
    simulation_id = created.json()["simulation_id"]
    company_id = created.json()["company_id"]
    started = client.post(f"/api/simulations/{simulation_id}/start", headers=headers, json={})
    assert started.status_code == 200, started.text
    current = client.get(f"/api/simulations/{simulation_id}/tasks/current", headers=headers)
    assert current.status_code == 200, current.text
    task_id = current.json()["task_id"]

    with client.websocket_connect(f"/ws/companies/{company_id}?token={token}") as socket:
        updated = client.patch(
            f"/api/tasks/{task_id}/status",
            headers=headers,
            json={"status": "in_progress"},
        )
        assert updated.status_code == 200, updated.text
        event = socket.receive_json()
        assert event["type"] == "task_updated", event
        assert event["data"]["task_id"] == task_id, event
        assert event["data"]["status"] == "in_progress", event
