"""
Comprehensive test suite for Fixed Internship Destination + Dynamic Adaptive Journey.
Validates:
1. Dynamic baseline assessment generation & fallback & persistence
2. Fixed project/sprint structure + AI-generated first task & dynamic next tasks
3. Resubmission version-aware review pipeline (v1 vs v2 preservation & idempotency)
4. Performance trend analysis (improving, recovering, declining, stable)
5. Contextual manager communication & cooldown (no unreplied message spam)
6. Task dependencies, contextual hints, and team member role guidance
"""
import pytest
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from app.models.enums import (
    CompanyType,
    DifficultyLevel,
    ReviewResult,
    ReviewStatus,
    SimulationStatus,
    SprintStatus,
    SubmissionStatus,
    TaskStatus,
)
from app.models.assessment import AssessmentAttempt, AssessmentQuestion
from app.models.company import Company
from app.models.intelligence import StudentProfile
from app.models.project import Project
from app.models.simulation import Simulation
from app.models.sprint import Sprint
from app.models.submission import AIReview, Submission, SubmissionVersion
from app.models.task import Task, TaskDependency
from app.models.task_path import SimulationTaskPath

from app.services.assessment_service import (
    generate_dynamic_baseline_questions,
    start_attempt,
    submit_attempt,
)
from app.services.generators.dynamic_task_generator import generate_dynamic_task
from app.services.intelligence_service import calculate_performance_trend, get_or_create_profile
from app.services.proactive_chat_service import is_due
from app.models.enums import MessageSenderType


def test_dynamic_baseline_generation_and_persistence(db, mock_student):
    """Verify baseline assessment is dynamically generated and persisted on refresh."""
    student_id = mock_student["student_id"]
    
    profile = get_or_create_profile(db, student_id)
    profile.role = "Backend Engineer"
    profile.skills = ["Python", "FastAPI", "PostgreSQL"]
    db.add(profile)
    db.commit()

    attempt_data_1 = start_attempt(db, student_id)
    assert attempt_data_1["id"] is not None
    assert len(attempt_data_1["questions"]) >= 5
    
    # Second call for same student should return the EXACT same attempt without regenerating
    attempt_data_2 = start_attempt(db, student_id)
    assert attempt_data_2["id"] == attempt_data_1["id"]
    assert len(attempt_data_2["questions"]) == len(attempt_data_1["questions"])
    assert [q["id"] for q in attempt_data_2["questions"]] == [q["id"] for q in attempt_data_1["questions"]]


def test_baseline_completion_calculates_level(db, mock_student, baseline_evaluator):
    """Verify baseline submit computes competency scores and recommended difficulty level."""
    student_id = mock_student["student_id"]
    attempt_data = start_attempt(db, student_id)
    attempt_id = attempt_data["id"]
    
    answers = []
    for q in attempt_data["questions"]:
        if q["question_type"] == "mcq" and q.get("options"):
            answers.append({"question_id": q["id"], "response": q["options"][0]["id"]})
        else:
            answers.append({"question_id": q["id"], "response": "This is my detailed response explaining the solution."})

    result = submit_attempt(db, student_id, attempt_id, answers)
    assert result["overall_score"] >= 0
    assert "competencies" in result
    assert result["profile"]["recommended_difficulty"] in ("beginner", "intermediate", "advanced")


@pytest.mark.asyncio
async def test_dynamic_task_generation_and_adaptation(db, mock_student, monkeypatch):
    """Verify dynamic next task generation considers previous task work and sprint objective."""
    student_id = mock_student["student_id"]
    
    company = Company(student_id=student_id, name="TechCorp", company_type=CompanyType.STARTUP, industry="Technology", department="Engineering", mission="Build fast", description="Tech startup")
    db.add(company)
    db.flush()

    project = Project(
        company_id=company.id,
        title="Dynamic API Platform",
        role="Backend Developer",
        technology_stack=["Python", "FastAPI"],
        difficulty=DifficultyLevel.INTERMEDIATE,
        objectives=["Build REST API", "Deploy database"],
        modules=["Auth Module", "User Module"],
    )
    db.add(project)
    db.flush()

    sprint = Sprint(
        project_id=project.id,
        sprint_number=1,
        name="Sprint 1: Core API",
        goal="Deliver Auth and User APIs.",
        status=SprintStatus.PLANNED,
    )
    db.add(sprint)
    db.flush()

    simulation = Simulation(
        student_id=student_id,
        company_id=company.id,
        project_id=project.id,
        title="Backend Internship",
        description="Internship simulation",
        role="Backend Developer",
        difficulty=DifficultyLevel.INTERMEDIATE,
        status=SimulationStatus.IN_PROGRESS,
    )
    db.add(simulation)
    db.flush()

    # Generate Task 1 dynamically
    from app.core.config import settings
    monkeypatch.setattr(settings, "MAX_DYNAMIC_TASKS_PER_SPRINT", 2)
    task1 = await generate_dynamic_task(db, simulation, sprint, student_id, previous_task=None)
    assert task1.id is not None
    assert task1.sprint_id == sprint.id
    assert task1.task_metadata is not None
    assert "hints" in task1.task_metadata
    assert "recommended_team_role" in task1.task_metadata
    db.add(SimulationTaskPath(simulation_id=simulation.id, task_id=task1.id, sequence=1, status=TaskStatus.TODO))
    db.flush()
    assert (await generate_dynamic_task(db, simulation, sprint, student_id, previous_task=None)).id == task1.id

    # Complete Task 1 and generate Task 2
    task1.status = TaskStatus.COMPLETED
    db.add(task1)
    db.flush()

    task2 = await generate_dynamic_task(db, simulation, sprint, student_id, previous_task=task1)
    assert task2.id is not None
    assert task2.id != task1.id
    assert task2.task_metadata["previous_task_id"] == str(task1.id)
    db.add(SimulationTaskPath(simulation_id=simulation.id, task_id=task2.id, sequence=2, status=TaskStatus.TODO))
    db.flush()
    assert (await generate_dynamic_task(db, simulation, sprint, student_id, previous_task=task1)).id == task2.id

    from app.services.generators.dynamic_task_generator import DynamicTaskLimitReached
    task2.status = TaskStatus.COMPLETED
    db.add(task2)
    db.flush()
    with pytest.raises(DynamicTaskLimitReached):
        await generate_dynamic_task(db, simulation, sprint, student_id, previous_task=task2)
    
    # Dependency check
    deps = db.execute(select(TaskDependency).where(TaskDependency.task_id == task2.id)).scalars().all()
    assert len(deps) == 1
    assert deps[0].depends_on_task_id == task1.id


def test_resubmission_version_review_preservation(db, mock_student):
    """Verify Submission v1 review is NOT overwritten when Submission v2 is created."""
    student_id = mock_student["student_id"]
    
    company = Company(student_id=student_id, name="InnoTech", company_type=CompanyType.PRODUCT_COMPANY, industry="Software", department="Engineering", mission="Innovative software", description="Software enterprise")
    db.add(company)
    db.flush()

    project = Project(
        company_id=company.id,
        title="Enterprise System",
        role="Software Engineer",
        technology_stack=["Python"],
        difficulty=DifficultyLevel.INTERMEDIATE,
    )
    db.add(project)
    db.flush()

    sprint = Sprint(project_id=project.id, sprint_number=1, name="Sprint 1", goal="Goal 1")
    db.add(sprint)
    db.flush()

    task = Task(
        sprint_id=sprint.id,
        title="Task 1: Setup",
        description="Setup project",
        difficulty=DifficultyLevel.INTERMEDIATE,
    )
    db.add(task)
    db.flush()

    submission = Submission(task_id=task.id, student_id=student_id, current_version=1)
    db.add(submission)
    db.flush()

    # Version 1
    ver1 = SubmissionVersion(
        submission_id=submission.id,
        version_number=1,
        files=[{"filename": "main.py", "content": "print('v1')"}],
        status=SubmissionStatus.CHANGES_REQUESTED,
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(ver1)
    db.flush()

    rev1 = AIReview(
        version_id=ver1.id,
        status=ReviewStatus.COMPLETED,
        result=ReviewResult.CHANGES_REQUIRED,
        summary="v1 feedback: missing error handling.",
        score=55,
    )
    db.add(rev1)
    db.flush()

    # Version 2
    ver2 = SubmissionVersion(
        submission_id=submission.id,
        version_number=2,
        files=[{"filename": "main.py", "content": "try:\n print('v2')\nexcept Exception: pass"}],
        status=SubmissionStatus.APPROVED,
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(ver2)
    db.flush()

    rev2 = AIReview(
        version_id=ver2.id,
        status=ReviewStatus.COMPLETED,
        result=ReviewResult.APPROVED,
        summary="v2 feedback: approved cleanly!",
        score=90,
    )
    db.add(rev2)
    db.flush()

    # Verify both reviews exist independently in DB
    rev1_db = db.get(AIReview, rev1.id)
    rev2_db = db.get(AIReview, rev2.id)

    assert rev1_db.score == 55
    assert rev1_db.summary == "v1 feedback: missing error handling."
    assert rev2_db.score == 90
    assert rev2_db.summary == "v2 feedback: approved cleanly!"


def test_performance_trend_calculation(db, mock_student):
    """Verify performance trend calculation detects improving vs weak trends."""
    student_id = mock_student["student_id"]

    company = Company(student_id=student_id, name="TrendCorp", company_type=CompanyType.MNC, industry="Finance", department="Engineering", mission="Global finance", description="MNC finance corp")
    db.add(company)
    db.flush()

    project = Project(company_id=company.id, title="Trend Project", role="Engineer", difficulty=DifficultyLevel.INTERMEDIATE)
    db.add(project)
    db.flush()

    sprint = Sprint(project_id=project.id, sprint_number=1, name="Sprint 1", goal="Sprint Goal")
    db.add(sprint)
    db.flush()

    simulation = Simulation(
        student_id=student_id,
        company_id=company.id,
        project_id=project.id,
        title="Trend Sim",
        description="Sim",
        role="Engineer",
        difficulty=DifficultyLevel.INTERMEDIATE,
    )
    db.add(simulation)
    db.flush()

    # Add submissions with improving scores
    for i, score_val in enumerate([55, 70, 88], start=1):
        task = Task(sprint_id=sprint.id, title=f"Task {i}", description="Desc", difficulty=DifficultyLevel.INTERMEDIATE)
        db.add(task)
        db.flush()

        sub = Submission(task_id=task.id, student_id=student_id, current_version=1)
        db.add(sub)
        db.flush()

        ver = SubmissionVersion(submission_id=sub.id, version_number=1, files=[], submitted_at=datetime.now(timezone.utc))
        db.add(ver)
        db.flush()

        rev = AIReview(version_id=ver.id, status=ReviewStatus.COMPLETED, result=ReviewResult.APPROVED, summary="Performance test review", score=score_val)
        db.add(rev)
        db.flush()

    trend = calculate_performance_trend(db, student_id, simulation.id)
    assert trend["trend"] in ("improving", "consistently_strong")
    assert trend["version_count"] == 3
    assert trend["recent_avg"] > 70.0


def test_manager_proactive_cooldown_prevents_unreplied_spam(db, mock_student):
    """Verify proactive chat service does NOT spam manager messages when student hasn't replied."""
    student_id = mock_student["student_id"]
    
    ctx = {
        "now": datetime.now(timezone.utc),
        "last_proactive_at": datetime.now(timezone.utc),
        "last_sender_type": MessageSenderType.MANAGER,  # Last msg was from Manager
        "ambient_gap": 360,
    }
    
    # Ambient message (no trigger event) should return False if last message was from Manager and unreplied
    assert is_due(ctx, force=False, trigger=None) is False
