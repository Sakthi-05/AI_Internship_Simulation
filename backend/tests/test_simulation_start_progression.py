import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import (
    CompanyType, DifficultyLevel, ManagerPersonality, ReviewResult, ReviewSeverity,
    ReviewStatus, ScenarioStatus, SimulationStatus, SprintStatus,
    SubmissionStatus, TaskPriority, TaskStatus, TaskType,
)
from app.models.company import Company
from app.models.manager import Manager
from app.models.project import Project
from app.models.project_state import ProjectState
from app.models.sprint import Sprint
from app.models.task import Task
from app.models.task_path import SimulationTaskPath
from app.models.simulation import Simulation, Scenario
from app.models.submission import Submission, SubmissionVersion, AIReview, ReviewHistory
from app.core.security import CurrentUser
from app.api.routes.simulation import start_simulation, StartSimulationRequest, complete_simulation_task
from app.services.ai.review_service import ReviewService
from app.services.ai.ai_service import MockAIService
from app.core.database import SessionLocal


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def mock_sim_setup(db_session: Session):
    student_id = f"student_{uuid.uuid4().hex[:8]}"
    company = Company(
        name="Test Corp",
        industry="Software",
        department="Engineering",
        mission="Build software",
        company_type=CompanyType.PRODUCT_COMPANY,
        description="Testing",
        student_id=student_id,
    )
    db_session.add(company)
    db_session.flush()

    project = Project(
        company_id=company.id,
        title="Test Project",
        role="Backend Developer",
        technology_stack=["Python"],
        difficulty=DifficultyLevel.BEGINNER,
        duration_weeks=4,
    )
    db_session.add(project)
    db_session.flush()

    manager = Manager(
        company_id=company.id,
        name="Alex Manager",
        title="Engineering Manager",
        personality=ManagerPersonality.FRIENDLY,
    )
    db_session.add(manager)

    state = ProjectState(
        project_id=project.id,
        current_sprint_number=1,
    )
    db_session.add(state)
    db_session.flush()

    sprint = Sprint(
        project_id=project.id,
        sprint_number=1,
        name="Sprint 1",
        goal="Sprint 1 Goal",
        status=SprintStatus.ACTIVE,
    )
    db_session.add(sprint)
    db_session.flush()

    simulation = Simulation(
        student_id=student_id,
        company_id=company.id,
        project_id=project.id,
        title="Test Simulation",
        description="Testing simulation",
        role="Backend Developer",
        difficulty=DifficultyLevel.BEGINNER,
        duration_weeks=4,
        status=SimulationStatus.NOT_STARTED,
        progress=0,
    )
    db_session.add(simulation)
    db_session.flush()

    scenario = Scenario(
        simulation_id=simulation.id,
        sequence=1,
        sequence_order=1,
        title="Scenario 1",
        description="Scenario 1 desc",
        workplace_context="Context",
        role="Backend Developer",
        difficulty=DifficultyLevel.BEGINNER,
        status=ScenarioStatus.PENDING,
    )
    db_session.add(scenario)
    db_session.flush()

    user = CurrentUser(
        id=uuid.uuid4(),
        student_id=student_id,
        email="test@example.com",
        role="student",
        raw_claims={},
    )

    return {
        "student_id": student_id,
        "company": company,
        "project": project,
        "sprint": sprint,
        "simulation": simulation,
        "scenario": scenario,
        "user": user,
    }


@pytest.mark.asyncio
async def test_start_simulation_with_existing_path_entry_idempotent(db_session: Session, mock_sim_setup):
    """When a task path entry already exists, starting simulation sets IN_PROGRESS without duplicate key error."""
    sim = mock_sim_setup["simulation"]
    sprint = mock_sim_setup["sprint"]
    user = mock_sim_setup["user"]

    # Pre-create task and path entry (like what caused the bug)
    task = Task(
        sprint_id=sprint.id,
        sequence=1,
        title="Pre-existing Task 1",
        description="Description",
        status=TaskStatus.BACKLOG,
        priority=TaskPriority.MEDIUM,
        task_type=TaskType.CODING,
        difficulty=DifficultyLevel.BEGINNER,
    )
    db_session.add(task)
    db_session.flush()

    path_entry = SimulationTaskPath(
        simulation_id=sim.id,
        task_id=task.id,
        sequence=1,
        status=TaskStatus.BACKLOG,
    )
    db_session.add(path_entry)
    sim.current_task_id = task.id
    db_session.add(sim)
    db_session.commit()

    # Now call start_simulation
    res = await start_simulation(sim.id, StartSimulationRequest(), db_session, user)

    assert res["status"] == "in_progress"
    assert res["current_task_id"] == str(task.id)
    assert res["current_task"]["status"] == "todo"

    # Verify DB state
    db_session.refresh(sim)
    assert sim.status == SimulationStatus.IN_PROGRESS
    assert sim.current_task_id == task.id

    db_session.refresh(task)
    assert task.status == TaskStatus.TODO

    db_session.refresh(path_entry)
    assert path_entry.status == TaskStatus.TODO


@pytest.mark.asyncio
async def test_complete_simulation_task_idempotency_returns_next_task(db_session: Session, mock_sim_setup):
    """Calling complete on already completed task returns already_completed with next_task safely."""
    sim = mock_sim_setup["simulation"]
    sprint = mock_sim_setup["sprint"]
    user = mock_sim_setup["user"]

    sim.status = SimulationStatus.IN_PROGRESS
    task1 = Task(
        sprint_id=sprint.id,
        sequence=1,
        title="Task 1",
        description="Desc 1",
        status=TaskStatus.COMPLETED,
        priority=TaskPriority.MEDIUM,
        task_type=TaskType.CODING,
        difficulty=DifficultyLevel.BEGINNER,
        completed_at=datetime.now(timezone.utc),
    )
    task2 = Task(
        sprint_id=sprint.id,
        sequence=2,
        title="Task 2",
        description="Desc 2",
        status=TaskStatus.TODO,
        priority=TaskPriority.MEDIUM,
        task_type=TaskType.CODING,
        difficulty=DifficultyLevel.BEGINNER,
    )
    db_session.add_all([task1, task2])
    db_session.flush()

    p1 = SimulationTaskPath(simulation_id=sim.id, task_id=task1.id, sequence=1, status=TaskStatus.COMPLETED)
    p2 = SimulationTaskPath(simulation_id=sim.id, task_id=task2.id, sequence=2, status=TaskStatus.TODO)
    sim.current_task_id = task2.id
    db_session.add_all([p1, p2, sim])
    db_session.commit()

    res = await complete_simulation_task(sim.id, task1.id, True, db_session, user)
    assert res["status"] == "already_completed"
    assert res["next_task"]["task_id"] == str(task2.id)
    assert res["next_task"]["title"] == "Task 2"


@pytest.mark.asyncio
async def test_review_by_manager_progresses_when_current_task_id_desynced(db_session: Session, mock_sim_setup):
    """Manager approval progresses to next task even if simulation.current_task_id was None or stale."""
    sim = mock_sim_setup["simulation"]
    sprint = mock_sim_setup["sprint"]
    project = mock_sim_setup["project"]

    sim.status = SimulationStatus.IN_PROGRESS
    task1 = Task(
        sprint_id=sprint.id,
        sequence=1,
        title="Task 1",
        description="Desc 1",
        status=TaskStatus.MANAGER_APPROVAL,
        priority=TaskPriority.MEDIUM,
        task_type=TaskType.CODING,
        difficulty=DifficultyLevel.BEGINNER,
    )
    task2 = Task(
        sprint_id=sprint.id,
        sequence=2,
        title="Task 2",
        description="Desc 2",
        status=TaskStatus.BACKLOG,
        priority=TaskPriority.MEDIUM,
        task_type=TaskType.CODING,
        difficulty=DifficultyLevel.BEGINNER,
    )
    db_session.add_all([task1, task2])
    db_session.flush()

    p1 = SimulationTaskPath(simulation_id=sim.id, task_id=task1.id, sequence=1, status=TaskStatus.UNDER_REVIEW)
    p2 = SimulationTaskPath(simulation_id=sim.id, task_id=task2.id, sequence=2, status=TaskStatus.BACKLOG)
    # Deliberately desync current_task_id to None
    sim.current_task_id = None
    db_session.add_all([p1, p2, sim])
    db_session.flush()

    sub = Submission(
        task_id=task1.id,
        student_id=sim.student_id,
        status=SubmissionStatus.UNDER_REVIEW,
        current_version=1,
    )
    db_session.add(sub)
    db_session.flush()

    ver = SubmissionVersion(
        submission_id=sub.id,
        version_number=1,
        status=SubmissionStatus.UNDER_REVIEW,
        submitted_at=datetime.now(timezone.utc),
    )
    db_session.add(ver)
    db_session.flush()

    ai_review = AIReview(
        version_id=ver.id,
        status=ReviewStatus.COMPLETED,
        result=ReviewResult.APPROVED,
        severity=ReviewSeverity.INFO,
        summary="Approved",
        score=95,
        raw_payload={"manager_decision": {"decision": "approved", "severity": "info", "summary": "Approved", "feedback": "Good job"}},
    )
    db_session.add(ai_review)
    db_session.commit()

    service = ReviewService(MockAIService())
    res = await service.review_by_manager(db_session, ver)

    assert res["manager_result"] == "approved"
    assert res["final_task_status"] == "completed"

    db_session.refresh(sim)
    assert sim.current_task_id == task2.id

    db_session.refresh(task2)
    assert task2.status == TaskStatus.TODO

    db_session.refresh(p1)
    assert p1.status == TaskStatus.COMPLETED
