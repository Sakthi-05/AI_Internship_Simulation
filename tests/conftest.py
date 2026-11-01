import os
import uuid
import json

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://aiwse:aiwse_password@localhost:5432/aiwse_test_db")
os.environ.setdefault("JWT_SECRET_KEY", "change-me-to-the-shared-signing-secret")

from app.core.config import settings  # noqa: E402
from app.core.database import Base, engine as app_engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _setup_database():
    """Create a clean schema once per test session against a real Postgres DB."""
    Base.metadata.create_all(bind=app_engine)
    # A leftover test schema can still have the one-row requirement-change
    # index. Swap it for the open-event index without dropping stored rows.
    with app_engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS uq_workflow_events_one_requirement_change"))
        conn.execute(text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_events_one_open_requirement_change
            ON workflow_events (simulation_id, task_id)
            WHERE event_type = 'REQUIREMENT_CHANGE'
              AND status IN ('pending', 'acknowledged')
              AND task_id IS NOT NULL
            """
        ))
    # Review-failure support used SQL ALTER TYPE on migrated databases; the
    # test schema is built with create_all(), which never extends an existing
    # enum type. Mirror the migration so FAILED reviews can be persisted.
    from app.models.enums import ReviewStatus

    with app_engine.begin() as conn:
        for value in ReviewStatus:
            conn.execute(text(f"ALTER TYPE ai_review_status_enum ADD VALUE IF NOT EXISTS '{value.value}'"))
    # Enum types outlive the tables themselves between sessions, so extend the
    # event enum the same way the runtime migrations do.
    with app_engine.begin() as conn:
        conn.execute(text("ALTER TYPE event_type_enum ADD VALUE IF NOT EXISTS 'SPRINT_COMPLETED'"))
        conn.execute(text("ALTER TYPE event_type_enum ADD VALUE IF NOT EXISTS 'sprint_completed'"))
    yield
    with app_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f'DROP TABLE IF EXISTS {table.name} CASCADE'))


@pytest.fixture
def client():
    return TestClient(app)


def make_token(student_id: str = "student_001") -> str:
    return jwt.encode(
        {"sub": student_id, "student_id": student_id, "role": "student"},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


@pytest.fixture
def auth_headers():
    token = make_token("student_001")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def other_auth_headers():
    token = make_token("student_002")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def db():
    from app.core.database import SessionLocal
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def baseline_evaluator(monkeypatch):
    """Deterministic provider double for baseline submission tests."""
    from app.services import assessment_service

    class Evaluator:
        provider_name = "test-provider"
        model = "deterministic-test"

        async def generate_json(self, prompt, system_prompt=None):
            payload = json.loads(prompt.split("Input JSON follows:\n", 1)[1].split("\n", 1)[0])
            return {"evaluations": [
                {
                    "question_id": item["question_id"],
                    "score": 82,
                    "explanation": "The response addresses the technical prompt.",
                    "feedback": "Add one concrete validation or test detail.",
                }
                for item in payload["items"] if item["evaluate_with_ai"]
            ]}

    monkeypatch.setattr(assessment_service, "get_ai_service", Evaluator)
    return Evaluator


@pytest.fixture
def mock_student():
    student_id = f"student_{uuid.uuid4().hex[:8]}"
    token = make_token(student_id)
    return {
        "student_id": student_id,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }
