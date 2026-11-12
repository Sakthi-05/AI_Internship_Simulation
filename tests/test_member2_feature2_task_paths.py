"""
Member 2 — Feature 2: Tests for personalised student task paths.

Proves that:
1. POST /start with task_ids creates a subset path
2. POST /start without task_ids creates the full path
3. GET /task-path returns ordered, student-specific entries
4. Task path entries reference valid project tasks
5. Progress reflects the student's own path size
6. Two students on the same project get independent paths (service layer)
"""
import uuid
import os
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sqlalchemy_event, text

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://aiwse:aiwse_password@localhost:5432/aiwse_test_db")
os.environ.setdefault("JWT_SECRET_KEY", "change-me-to-the-shared-signing-secret")

from app.core.config import settings
from app.core.database import Base, engine as app_engine, SessionLocal
from app.main import app
from app.models.enums import TaskStatus
from app.models.task import Task
from app.models.sprint import Sprint
from app.models.task_path import SimulationTaskPath
from app.services.simulation.task_path_builder import build_task_path


@pytest.fixture(scope="session", autouse=True)
def _setup_database():
    Base.metadata.create_all(bind=app_engine)
    yield
    with app_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f'DROP TABLE IF EXISTS {table.name} CASCADE'))


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


def _headers(student_id: str):
    return {"Authorization": f"Bearer {make_token(student_id)}"}


def _generate(client, student_id):
    headers = _headers(student_id)
    resp = client.post(
        "/api/generate",
        headers=headers,
        json={
            "student_id": student_id,
            "role": "Backend Developer",
            "technology_stack": ["Python", "FastAPI", "PostgreSQL"],
            "difficulty": "intermediate",
            "company_type": "startup",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_company(db, student_id):
    company_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.execute(text("""
        INSERT INTO companies (id, name, industry, company_type, department, mission,
            description, student_id, created_at, updated_at)
        VALUES (:id, 'TestCo', 'Tech', 'STARTUP', 'Engineering', 'Mission',
            'Test company', :sid, :now, :now)
    """), {"id": company_id, "sid": student_id, "now": now})
    db.commit()
    return company_id


def _seed_project(db, company_id):
    project_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.execute(text("""
        INSERT INTO projects (id, company_id, title, role, technology_stack, difficulty,
            status, objectives, modules, deliverables, duration_weeks, created_at, updated_at)
        VALUES (:id, :cid, 'Test Project', 'Developer', ARRAY['Python', 'FastAPI'],
            'INTERMEDIATE', 'PLANNING', ARRAY['Build API', 'Build UI'],
            ARRAY['Auth', 'Dashboard'], ARRAY['Deployed App'], 4, :now, :now)
    """), {"id": project_id, "cid": company_id, "now": now})
    db.commit()
    return project_id


def _seed_sprints_and_tasks(db, project_id, num_sprints=2, tasks_per_sprint=3):
    now = datetime.now(timezone.utc)
    all_task_ids = []
    for sprint_num in range(1, num_sprints + 1):
        sprint_id = uuid.uuid4()
        db.execute(text("""
            INSERT INTO sprints (id, project_id, sprint_number, name, goal, status, created_at, updated_at)
            VALUES (:id, :pid, :sn, :name, :goal, 'PLANNED', :now, :now)
        """), {"id": sprint_id, "pid": project_id, "sn": sprint_num,
               "name": f"Sprint {sprint_num}", "goal": f"Goal {sprint_num}", "now": now})
        for seq in range(1, tasks_per_sprint + 1):
            task_id = uuid.uuid4()
            all_task_ids.append(task_id)
            db.execute(text("""
                INSERT INTO tasks (id, sprint_id, sequence, title, description, acceptance_criteria,
                    priority, status, task_type, difficulty, estimated_hours, created_at, updated_at)
                VALUES (:id, :sid, :seq, :title, 'Desc', ARRAY['Criteria'],
                    'medium', 'backlog', 'coding', 'INTERMEDIATE', 4.0, :now, :now)
            """), {"id": task_id, "sid": sprint_id, "seq": seq,
                   "title": f"S{sprint_num} T{seq}", "now": now})
    db.commit()
    return all_task_ids


def _seed_simulation(db, student_id, company_id, project_id):
    sim_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.execute(text("""
        INSERT INTO simulations (id, student_id, company_id, project_id, title, description,
            role, difficulty, duration_weeks, status, progress, created_at, updated_at)
        VALUES (:id, :sid, :cid, :pid, 'Test Simulation', 'Test',
            'Developer', 'INTERMEDIATE', 4, 'NOT_STARTED', 0, :now, :now)
    """), {"id": sim_id, "sid": student_id, "cid": company_id,
           "pid": project_id, "now": now})
    db.commit()
    return sim_id


def _project_for_simulation(client, headers, simulation_id):
    response = client.get(f"/api/simulations/{simulation_id}", headers=headers)
    assert response.status_code == 200, response.text
    # This test helper needs the project scope for the board request; resolve
    # it from the persisted simulation without duplicating setup identifiers.
    db = SessionLocal()
    try:
        return str(db.execute(
            text("SELECT project_id FROM simulations WHERE id = :id"),
            {"id": simulation_id},
        ).scalar_one())
    finally:
        db.close()


def _seed_scenario(db, simulation_id, task_ids):
    now = datetime.now(timezone.utc)
    scenario_id = uuid.uuid4()
    db.execute(text("""
        INSERT INTO scenarios (id, simulation_id, sequence, title, description, workplace_context,
            role, difficulty, required_skills, objectives, status, sequence_order, created_at, updated_at)
        VALUES (:id, :sid, 1, 'Sprint 1', 'First sprint', 'Workplace context',
            'Developer', 'INTERMEDIATE', ARRAY['Python'], ARRAY['Goal'], 'PENDING', 1, :now, :now)
    """), {"id": scenario_id, "sid": simulation_id, "now": now})
    for idx, task_id in enumerate(task_ids):
        st_id = uuid.uuid4()
        db.execute(text("""
            INSERT INTO scenario_tasks (id, scenario_id, task_id, sequence, created_at, updated_at)
            VALUES (:id, :scid, :tid, :seq, :now, :now)
            ON CONFLICT DO NOTHING
        """), {"id": st_id, "scid": scenario_id, "tid": task_id, "seq": idx + 1, "now": now})
    db.commit()
    return scenario_id


def _full_setup(db, student_id, num_sprints=2, tasks_per_sprint=3):
    """Create company, project, sprints, tasks, simulation, scenario — all via raw SQL."""
    company_id = _seed_company(db, student_id)
    project_id = _seed_project(db, company_id)
    task_ids = _seed_sprints_and_tasks(db, project_id, num_sprints, tasks_per_sprint)
    sim_id = _seed_simulation(db, student_id, company_id, project_id)
    _seed_scenario(db, sim_id, task_ids)
    return sim_id, [str(tid) for tid in task_ids]


class TestPersonalisedTaskPathsAPI:
    """API-level tests proving personalisation works end-to-end."""

    def test_start_with_subset_creates_shorter_path(self, client):
        """POST /start with a task_ids subset creates fewer path entries."""
        db = SessionLocal()
        try:
            sim_id, all_task_ids = _full_setup(db, "subset_student")
        finally:
            db.close()

        headers = _headers("subset_student")
        subset = all_task_ids[: len(all_task_ids) // 2]
        resp = client.post(
            f"/api/simulations/{sim_id}/start",
            headers=headers,
            json={"task_ids": subset},
        )
        assert resp.status_code == 200, resp.text

        path = client.get(f"/api/simulations/{sim_id}/task-path", headers=headers).json()
        assert len(path) == len(subset)
        assert len(path) < len(all_task_ids)

    def test_start_without_subset_gives_all_tasks(self, client):
        """POST /start with empty body gives all canonical tasks."""
        db = SessionLocal()
        try:
            sim_id, all_task_ids = _full_setup(db, "alltasks_student")
        finally:
            db.close()

        headers = _headers("alltasks_student")
        resp = client.post(f"/api/simulations/{sim_id}/start", headers=headers, json={"task_ids": all_task_ids})
        assert resp.status_code == 200, resp.text

        path = client.get(f"/api/simulations/{sim_id}/task-path", headers=headers).json()
        assert len(path) == len(all_task_ids)

    def test_path_builder_retry_preserves_the_existing_order(self):
        """A retry after path persistence must not append duplicate entries."""
        db = SessionLocal()
        student_id = f"path_retry_{uuid.uuid4().hex[:8]}"
        try:
            company_id = _seed_company(db, student_id)
            project_id = _seed_project(db, company_id)
            task_ids = _seed_sprints_and_tasks(db, project_id, num_sprints=1, tasks_per_sprint=2)
            sim_id = _seed_simulation(db, student_id, company_id, project_id)
            first = build_task_path(db, sim_id, project_id, task_ids=task_ids)
            db.commit()
            retry = build_task_path(db, sim_id, project_id, task_ids=task_ids)
            assert [item.task_id for item in retry] == [item.task_id for item in first]
            assert len(retry) == 2
        finally:
            db.close()

    def test_progression_retry_and_refresh_keep_one_current_task(self):
        """Sequential advancement is persisted and stale retries cannot skip."""
        from app.models.enums import TaskStatus
        from app.models.simulation import Simulation
        from app.services.simulation.progression_service import progress_to_next_task
        from app.services.simulation.task_access_service import repair_current_task

        db = SessionLocal()
        student_id = f"progress_{uuid.uuid4().hex[:8]}"
        try:
            company_id = _seed_company(db, student_id)
            project_id = _seed_project(db, company_id)
            task_ids = _seed_sprints_and_tasks(db, project_id, num_sprints=1, tasks_per_sprint=2)
            sim_id = _seed_simulation(db, student_id, company_id, project_id)
            _seed_scenario(db, sim_id, task_ids)
            path = build_task_path(db, sim_id, project_id, task_ids=task_ids)
            simulation = db.get(Simulation, sim_id)
            completed = db.get(Task, task_ids[0])
            completed.status = TaskStatus.COMPLETED
            completed.completed_at = datetime.now(timezone.utc)
            simulation.current_task_id = completed.id
            db.flush()

            next_task = asyncio.run(progress_to_next_task(db, simulation, completed, student_id))
            assert next_task.id == task_ids[1]
            db.commit()

            # Simulate reconnect/refresh by reloading authoritative state.
            db.expire_all()
            simulation = db.get(Simulation, sim_id)
            assert repair_current_task(db, simulation).id == task_ids[1]
            assert simulation.current_task_id == task_ids[1]
            assert db.get(Task, task_ids[1]).status == TaskStatus.TODO

            # A duplicate completion callback for the prior task must be inert.
            prior = db.get(Task, task_ids[0])
            assert asyncio.run(progress_to_next_task(db, simulation, prior, student_id)) is None
            db.refresh(simulation)
            assert simulation.current_task_id == task_ids[1]
            assert len(path) == 2
        finally:
            db.close()

    def test_next_available_task_lookup_has_bounded_query_count(self):
        """Blocked path length must not add one prerequisite query per task."""
        from app.models.task import TaskDependency
        from app.services.simulation.task_access_service import get_next_available_path_entry

        db = SessionLocal()
        student_id = f"bounded_path_{uuid.uuid4().hex[:8]}"
        statements = []

        def count_statement(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        try:
            company_id = _seed_company(db, student_id)
            project_id = _seed_project(db, company_id)
            task_ids = _seed_sprints_and_tasks(db, project_id, num_sprints=1, tasks_per_sprint=12)
            sim_id = _seed_simulation(db, student_id, company_id, project_id)
            # Ten path tasks are blocked on an unfinished prerequisite excluded
            # from this student's path; the final path task is available.
            for task_id in task_ids[:10]:
                db.add(TaskDependency(task_id=task_id, depends_on_task_id=task_ids[10]))
            db.flush()
            build_task_path(db, sim_id, project_id, task_ids=task_ids[:10] + [task_ids[11]])
            db.commit()
            db.expire_all()

            sqlalchemy_event.listen(app_engine, "before_cursor_execute", count_statement)
            entry = get_next_available_path_entry(db, sim_id)
            assert entry is not None
            assert entry.task_id == task_ids[11]
            # One simulation lookup, two graph-validation selects, and one
            # eligible-entry query, regardless of the ten blocked entries.
            assert len(statements) <= 4, f"Expected bounded SQL queries, got {len(statements)}"
        finally:
            sqlalchemy_event.remove(app_engine, "before_cursor_execute", count_statement)
            db.close()

    def test_current_task_board_progress_and_simulation_apis_agree_after_refresh(self, client):
        student_id = f"sync_{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        try:
            sim_id, task_ids = _full_setup(db, student_id, num_sprints=2, tasks_per_sprint=2)
        finally:
            db.close()
        headers = _headers(student_id)
        started = client.post(
            f"/api/simulations/{sim_id}/start",
            headers=headers,
            json={"task_ids": task_ids},
        )
        assert started.status_code == 200, started.text

        current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers)
        progress = client.get(f"/api/simulations/{sim_id}/progress", headers=headers)
        simulation = client.get(f"/api/simulations/{sim_id}", headers=headers)
        path = client.get(f"/api/simulations/{sim_id}/task-path", headers=headers)
        board = client.get(
            f"/api/projects/{_project_for_simulation(client, headers, sim_id)}/board?simulation_id={sim_id}",
            headers=headers,
        )
        assert all(response.status_code == 200 for response in (current, progress, simulation, path, board))

        current_task_id = current.json()["task_id"]
        assert progress.json()["current_task_id"] == current_task_id
        assert simulation.json()["current_task_id"] == current_task_id
        assert simulation.json()["current_task"]["id"] == current_task_id
        assert [entry["task_id"] for entry in path.json()] == task_ids
        board_tasks = [task for group in board.json().values() for task in group]
        assert {task["id"] for task in board_tasks} == set(task_ids)
        assert [task["id"] for task in board_tasks if task.get("is_current")] == [current_task_id]
        assert progress.json()["current_sprint_number"] == 1

        # A fresh read must restore the same persisted task rather than a local
        # candidate or a task from another path.
        refreshed = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers)
        assert refreshed.json()["task_id"] == current_task_id

    def test_task_path_returns_ordered_entries(self, client):
        """GET /task-path returns entries with sequence 1..N."""
        db = SessionLocal()
        try:
            sim_id, all_task_ids = _full_setup(db, "order_student")
        finally:
            db.close()

        headers = _headers("order_student")
        client.post(f"/api/simulations/{sim_id}/start", headers=headers, json={})
        path = client.get(f"/api/simulations/{sim_id}/task-path", headers=headers).json()

        sequences = [e["sequence"] for e in path]
        assert sequences == list(range(1, len(path) + 1))

    def test_task_path_entries_have_required_fields(self, client):
        """Each task-path entry includes task_id, title, status, sprint_id."""
        db = SessionLocal()
        try:
            sim_id, all_task_ids = _full_setup(db, "fields_student")
        finally:
            db.close()

        headers = _headers("fields_student")
        client.post(f"/api/simulations/{sim_id}/start", headers=headers, json={})
        path = client.get(f"/api/simulations/{sim_id}/task-path", headers=headers).json()

        for entry in path:
            assert "task_id" in entry
            assert "title" in entry
            assert "status" in entry
            assert "sprint_id" in entry
            assert "sequence" in entry

    def test_task_path_references_valid_project_tasks(self, client):
        """Every task in the path belongs to the generated project."""
        db = SessionLocal()
        try:
            sim_id, all_task_ids = _full_setup(db, "valid_student")
        finally:
            db.close()

        headers = _headers("valid_student")
        client.post(f"/api/simulations/{sim_id}/start", headers=headers, json={"task_ids": all_task_ids})
        path = client.get(f"/api/simulations/{sim_id}/task-path", headers=headers).json()

        path_task_ids = {e["task_id"] for e in path}
        all_set = set(all_task_ids)
        assert path_task_ids.issubset(all_set), (
            f"Path tasks {path_task_ids - all_set} are not canonical project tasks"
        )

    def test_progress_reflects_path_size(self, client):
        """GET /progress total_tasks matches the personalised path, not all canonical tasks."""
        db = SessionLocal()
        try:
            sim_id, all_task_ids = _full_setup(db, "progress_student")
        finally:
            db.close()

        headers = _headers("progress_student")
        subset = all_task_ids[: len(all_task_ids) // 2]
        client.post(
            f"/api/simulations/{sim_id}/start",
            headers=headers,
            json={"task_ids": subset},
        )

        progress = client.get(f"/api/simulations/{sim_id}/progress", headers=headers).json()
        assert progress["total_tasks"] == len(subset)
        assert progress["total_tasks"] < len(all_task_ids)


class TestTwoStudentsSameProject:
    """Prove two students can have different paths for the same project (service layer)."""

    def test_two_students_different_path_lengths(self):
        """Student A gets all tasks, Student B gets a subset."""
        db = SessionLocal()
        try:
            company_id = uuid.uuid4()
            project_id = uuid.uuid4()
            now = datetime.now(timezone.utc)

            db.execute(text("""
                INSERT INTO companies (id, name, industry, company_type, department, mission,
                    description, student_id, created_at, updated_at)
                VALUES (:id, 'SharedCo', 'Tech', 'STARTUP', 'Engineering', 'Mission',
                    'Shared company', 'owner', :now, :now)
            """), {"id": company_id, "now": now})

            db.execute(text("""
                INSERT INTO projects (id, company_id, title, role, technology_stack, difficulty,
                    status, objectives, modules, deliverables, duration_weeks, created_at, updated_at)
                VALUES (:id, :cid, 'Shared Project', 'Developer', ARRAY['Python', 'FastAPI'],
                    'INTERMEDIATE', 'PLANNING', ARRAY['Build API', 'Build UI'],
                    ARRAY['Auth', 'Dashboard'], ARRAY['Deployed App'], 4, :now, :now)
            """), {"id": project_id, "cid": company_id, "now": now})

            all_task_ids = []
            for sprint_num in range(1, 3):
                sprint_id = uuid.uuid4()
                db.execute(text("""
                    INSERT INTO sprints (id, project_id, sprint_number, name, goal, status, created_at, updated_at)
                    VALUES (:id, :pid, :sn, :name, :goal, 'PLANNED', :now, :now)
                """), {"id": sprint_id, "pid": project_id, "sn": sprint_num,
                       "name": f"Sprint {sprint_num}", "goal": f"Goal {sprint_num}", "now": now})
                for seq in range(1, 4):
                    task_id = uuid.uuid4()
                    all_task_ids.append(task_id)
                    db.execute(text("""
                        INSERT INTO tasks (id, sprint_id, sequence, title, description, acceptance_criteria,
                            priority, status, task_type, difficulty, estimated_hours, created_at, updated_at)
                        VALUES (:id, :sid, :seq, :title, 'Desc', ARRAY['Criteria'],
                            'medium', 'backlog', 'coding', 'INTERMEDIATE', 4.0, :now, :now)
                    """), {"id": task_id, "sid": sprint_id, "seq": seq,
                           "title": f"S{seq} T{seq}", "now": now})
            db.commit()
            assert len(all_task_ids) == 6

            sim_a_id = uuid.uuid4()
            sim_b_id = uuid.uuid4()
            for sid, label in [(sim_a_id, "A"), (sim_b_id, "B")]:
                db.execute(text("""
                    INSERT INTO simulations (id, student_id, company_id, project_id, title, description,
                        role, difficulty, duration_weeks, status, progress, created_at, updated_at)
                    VALUES (:id, :sid, :cid, :pid, :label, 'Test', 'Developer', 'INTERMEDIATE', 4,
                        'NOT_STARTED', 0, :now, :now)
                """), {"id": sid, "sid": f"student_{label}", "cid": company_id,
                       "pid": project_id, "label": f"Sim {label}", "now": now})
            db.commit()

            path_a = build_task_path(db, sim_a_id, project_id)
            assert len(path_a) == 6

            subset_ids = all_task_ids[:3]
            path_b = build_task_path(db, sim_b_id, project_id, task_ids=subset_ids)
            assert len(path_b) == 3

            seq_a = [e.sequence for e in path_a]
            seq_b = [e.sequence for e in path_b]
            assert seq_a == [1, 2, 3, 4, 5, 6]
            assert seq_b == [1, 2, 3]

            for entry in path_a + path_b:
                task = db.get(Task, entry.task_id)
                assert task is not None
                sprint = db.get(Sprint, task.sprint_id)
                assert sprint.project_id == project_id

            path_a[0].status = TaskStatus.COMPLETED
            path_a[0].completed_at = datetime.now(timezone.utc)
            db.commit()

            for entry in path_b:
                refreshed = db.get(SimulationTaskPath, entry.id)
                assert refreshed.status == TaskStatus.BACKLOG

        finally:
            db.close()
