"""Unified simulation interaction history — append-only chronological log."""
import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://aiwse:aiwse_password@localhost:5432/aiwse_test_db")
os.environ.setdefault("JWT_SECRET_KEY", "change-me-to-the-shared-signing-secret")

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine as app_engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _setup_database():
    Base.metadata.create_all(bind=app_engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def make_token(student_id: str = "test_student") -> str:
    from jose import jwt
    return jwt.encode(
        {"sub": student_id, "student_id": student_id, "role": "student"},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def _headers(student_id):
    return {"Authorization": f"Bearer {make_token(student_id)}"}


def _seed(db, student_id):
    cid = uuid.uuid4()
    pid = uuid.uuid4()
    sid = uuid.uuid4()
    tid = uuid.uuid4()
    sim_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.execute(text("""
        INSERT INTO companies (id, name, industry, company_type, department, mission,
            description, student_id, created_at, updated_at)
        VALUES (:id, 'TestCo', 'Tech', 'STARTUP', 'Engineering', 'Mission',
            'Test', :sid, :now, :now)
    """), {"id": cid, "sid": student_id, "now": now})
    db.execute(text("""
        INSERT INTO projects (id, company_id, title, role, technology_stack, difficulty,
            status, objectives, modules, deliverables, duration_weeks, created_at, updated_at)
        VALUES (:id, :cid, 'Test Project', 'Developer', ARRAY['Python'],
            'INTERMEDIATE', 'PLANNING', ARRAY['Build API'],
            ARRAY['Auth'], ARRAY['App'], 4, :now, :now)
    """), {"id": pid, "cid": cid, "now": now})
    db.execute(text("""
        INSERT INTO sprints (id, project_id, sprint_number, name, goal, status, created_at, updated_at)
        VALUES (:id, :pid, 1, 'Sprint 1', 'Goal', 'PLANNED', :now, :now)
    """), {"id": sid, "pid": pid, "now": now})
    db.execute(text("""
        INSERT INTO tasks (id, sprint_id, sequence, title, description, acceptance_criteria,
            priority, status, task_type, difficulty, estimated_hours, created_at, updated_at)
        VALUES (:id, :sid, 1, 'First task', 'Implement the login API', ARRAY['Works'],
            'medium', 'todo', 'coding', 'INTERMEDIATE', 1.0, :now, :now)
    """), {"id": tid, "sid": sid, "now": now})
    db.execute(text("""
        INSERT INTO simulations (id, student_id, company_id, project_id, title, description,
            role, difficulty, duration_weeks, status, progress, created_at, updated_at)
        VALUES (:id, :sid, :cid, :pid, 'Test Sim', 'Workplace onboarding simulation',
            'Developer', 'INTERMEDIATE', 4, 'NOT_STARTED', 0, :now, :now)
    """), {"id": sim_id, "sid": student_id, "cid": cid, "pid": pid, "now": now})
    db.commit()
    return sim_id, tid


class TestSimulationInteractionHistory:
    def test_start_records_chronological_interactions(self, client):
        student_id = f"ih_{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        try:
            sim_id, task_id = _seed(db, student_id)
        finally:
            db.close()

        headers = _headers(student_id)
        start = client.post(f"/api/simulations/{sim_id}/start", headers=headers, json={})
        assert start.status_code == 200, start.text

        begin = client.post(
            f"/api/simulations/{sim_id}/tasks/{task_id}/start",
            headers=headers,
        )
        assert begin.status_code == 200, begin.text

        history = client.get(f"/api/simulations/{sim_id}/interactions", headers=headers)
        assert history.status_code == 200, history.text
        payload = history.json()
        assert payload["simulation_id"] == str(sim_id)
        assert payload["student_id"] == student_id
        assert payload["count"] >= 2

        sequences = [row["sequence"] for row in payload["interactions"]]
        assert sequences == sorted(sequences)
        assert sequences == list(range(1, len(sequences) + 1))

        types = [row["interaction_type"] for row in payload["interactions"]]
        assert "simulation_started" in types
        assert "task_started" in types

        first = payload["interactions"][0]
        assert first["situation"]
        assert first["timestamp"]
        assert first["parent_interaction_id"] is None
        second = payload["interactions"][1]
        assert second["parent_interaction_id"] == first["id"]
        assert second["task_id"] == str(task_id)

    def test_submit_stores_student_response(self, client):
        student_id = f"ihsub_{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        try:
            sim_id, task_id = _seed(db, student_id)
        finally:
            db.close()

        headers = _headers(student_id)
        client.post(f"/api/simulations/{sim_id}/start", headers=headers, json={})
        client.post(f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=headers)

        submit = client.post(
            f"/api/simulations/{sim_id}/tasks/{task_id}/submit",
            headers=headers,
            data={"metadata": '{"response": "here is my solution"}'},
        )
        assert submit.status_code == 201, submit.text

        history = client.get(f"/api/simulations/{sim_id}/interactions", headers=headers)
        rows = history.json()["interactions"]
        submitted = [row for row in rows if row["interaction_type"] == "task_submitted"]
        assert len(submitted) == 1
        assert submitted[0]["student_response"] == "here is my solution"
        assert submitted[0]["decision"] == "submit"
        assert submitted[0]["task_id"] == str(task_id)

    def test_other_student_cannot_read_history(self, client):
        student_id = f"ihown_{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        try:
            sim_id, _task_id = _seed(db, student_id)
        finally:
            db.close()

        client.post(f"/api/simulations/{sim_id}/start", headers=_headers(student_id), json={})
        denied = client.get(
            f"/api/simulations/{sim_id}/interactions",
            headers=_headers("other_student"),
        )
        assert denied.status_code == 403
