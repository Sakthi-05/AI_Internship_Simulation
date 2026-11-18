"""
Dynamic Task Generator Service.

Generates next technical task dynamically inside a sprint based on:
- Internship role & project scope
- Current sprint objective & deliverables
- Student's baseline assessment & current competency profile
- Recent performance trend (improving, stable, declining, recovering, etc.)
- Previous submission versions & AI review feedback (strengths, weaknesses, mistakes)
- Resubmission improvements
- Task dependencies & contextual hints
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import DifficultyLevel, TaskPriority, TaskStatus, TaskType
from app.models.intelligence import StudentProfile
from app.models.simulation import Simulation
from app.models.sprint import Sprint
from app.models.submission import AIReview, Submission, SubmissionVersion
from app.models.task import Task, TaskDependency
from app.models.task_path import SimulationTaskPath
from app.core.config import settings
from app.services.ai.ai_service import get_ai_service
from app.services.intelligence_service import calculate_performance_trend, get_or_create_profile

logger = logging.getLogger(__name__)


class DynamicTaskLimitReached(ValueError):
    """No more configured dynamic tasks may be added to this sprint."""


async def generate_dynamic_task(
    db: Session,
    simulation: Simulation,
    sprint: Sprint,
    student_id: str,
    previous_task: Optional[Task] = None,
    competency_focus: Optional[Dict[str, Any]] = None,
) -> Task:
    """Generate next task dynamically based on actual student technical work."""
    # Serialize generation and give each predecessor a stable idempotency key.
    locked_simulation = db.execute(
        select(Simulation).where(Simulation.id == simulation.id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    simulation = locked_simulation
    sprint = db.execute(
        select(Sprint).where(Sprint.id == sprint.id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    generation_key = f"{simulation.id}:{sprint.id}:{previous_task.id if previous_task else 'initial'}"
    existing = db.execute(
        select(Task).where(
            Task.sprint_id == sprint.id,
            Task.task_metadata["generation_key"].as_string() == generation_key,
        )
    ).scalars().first()
    if existing is not None:
        return existing
    if sprint.status.value == "completed":
        raise DynamicTaskLimitReached("Cannot generate a task for a completed sprint.")
    generated_tasks = db.execute(
        select(Task)
        .join(SimulationTaskPath, SimulationTaskPath.task_id == Task.id)
        .where(
            Task.sprint_id == sprint.id,
            SimulationTaskPath.simulation_id == simulation.id,
        )
    ).scalars().all()
    generated_tasks = [
        item for item in generated_tasks
        if (item.task_metadata or {}).get("dynamic_generation") is True
    ]
    if len(generated_tasks) >= max(1, int(settings.MAX_DYNAMIC_TASKS_PER_SPRINT)):
        raise DynamicTaskLimitReached(
            f"Sprint {sprint.sprint_number} reached its configured dynamic task limit."
        )
    generation_kind = "planned" if not generated_tasks else "adaptive"
    sprint_metadata = dict(sprint.sprint_metadata or {})
    sprint_metadata.setdefault("generation_version", 1)
    sprint_metadata.setdefault("planned_task_count", 1)
    sprint_metadata.setdefault("dynamic_task_limit", max(1, int(settings.MAX_DYNAMIC_TASKS_PER_SPRINT)))
    sprint_metadata.setdefault(
        "adaptive_task_limit",
        max(0, int(sprint_metadata["dynamic_task_limit"]) - int(sprint_metadata["planned_task_count"])),
    )
    sprint.sprint_metadata = sprint_metadata
    db.add(sprint)
    profile = get_or_create_profile(db, student_id)
    trend_data = calculate_performance_trend(db, student_id, simulation.id)
    
    # Gather completed tasks in sprint
    completed_path_tasks = db.execute(
        select(Task)
        .join(SimulationTaskPath, SimulationTaskPath.task_id == Task.id)
        .where(
            SimulationTaskPath.simulation_id == simulation.id,
            Task.sprint_id == sprint.id,
            Task.status == TaskStatus.COMPLETED,
        )
        .order_by(Task.sequence.asc())
    ).scalars().all()

    completed_titles = [t.title for t in completed_path_tasks]
    
    # Gather previous submission version reviews if previous_task provided
    previous_review_summary = ""
    if previous_task:
        latest_sub = db.execute(
            select(Submission)
            .where(Submission.task_id == previous_task.id, Submission.student_id == student_id)
        ).scalar_one_or_none()
        if latest_sub and latest_sub.versions:
            reviews = []
            for ver in latest_sub.versions:
                for rev in ver.ai_reviews:
                    if rev.summary:
                        reviews.append(f"v{ver.version_number}: {rev.summary[:200]}")
            previous_review_summary = "; ".join(reviews)

    # Next task sequence
    current_max_seq = db.execute(
        select(Task.sequence)
        .where(Task.sprint_id == sprint.id)
        .order_by(Task.sequence.desc())
    ).scalars().first()
    next_sequence = (current_max_seq + 1) if current_max_seq is not None else 1

    role = simulation.role or profile.role or "Software Engineer"
    difficulty = simulation.difficulty or DifficultyLevel.INTERMEDIATE
    if isinstance(difficulty, str):
        try:
            difficulty = DifficultyLevel(difficulty)
        except ValueError:
            difficulty = DifficultyLevel.INTERMEDIATE

    prompt = (
        f"Generate next dynamic internship task for role '{role}'.\n"
        f"Sprint Goal: {sprint.goal}\n"
        f"Sprint Number: {sprint.sprint_number}\n"
        f"Completed Tasks in Sprint: {completed_titles}\n"
        f"Previous Task Worked: {previous_task.title if previous_task else 'None (First Task)'}\n"
        f"Previous Review History: {previous_review_summary or 'N/A'}\n"
        f"Performance Trend: {trend_data.get('trend', 'stable')} (Recent Avg: {trend_data.get('recent_avg', 60)})\n"
        f"Current Competencies: {profile.current_competencies}\n"
        f"Difficulty Level: {difficulty.value}\n\n"
        f"Rules:\n"
        f"1. Generate a task directly advancing the sprint objective and building on completed work.\n"
        f"2. If performance trend is 'declining' or 'consistently_weak', break down work into smaller scoped steps with extra scaffolding.\n"
        f"3. If performance trend is 'improving' or 'consistently_strong', increase technical depth, responsibility, and integration complexity.\n"
        f"4. Output valid JSON with keys: title, description, acceptance_criteria, priority, task_type, difficulty, estimated_hours, hints, recommended_team_role, generation_reason."
    )
    focus_dimensions = []
    focus_task_type = None
    focus_reason = None
    focus_evidence = []
    if isinstance(competency_focus, dict) and competency_focus.get("status") == "recommended":
        allowed_dimensions = {"technical_knowledge", "problem_solving", "decision_making",
                              "communication", "critical_thinking", "adaptability",
                              "time_management", "professionalism"}
        focus_dimensions = [value for value in (competency_focus.get("focus_competencies") or [])
                            if value in allowed_dimensions][:1]
        try:
            proposed_type = TaskType(competency_focus.get("proposed_task_type"))
        except (ValueError, TypeError):
            proposed_type = None
        focus_task_type = proposed_type.value if proposed_type else None
        focus_reason = str(competency_focus.get("reason") or "")[:500]
        focus_evidence = [item for item in (competency_focus.get("evidence_references") or [])
                          if isinstance(item, dict)][:12]
        if focus_dimensions:
            prompt += (
                "\nEvidence-backed competency practice focus for this next eligible task: "
                f"{focus_dimensions[0]}. Suggested existing task type: {focus_task_type or 'keep the generated type'}. "
                "Keep the task aligned with the sprint objective and do not change its difficulty."
            )
    system_prompt = (
        "You are an Adaptive Task Engine for an AI Internship Simulation. Generate contextual tasks tailored to student performance."
    )

    ai_service = get_ai_service()
    task_payload: Optional[Dict[str, Any]] = None
    try:
        task_payload = await ai_service.generate_json(prompt, system_prompt)
    except Exception as exc:
        logger.warning("AI dynamic task generation failed for simulation %s: %s; using deterministic task engine fallback", simulation.id, exc)
        task_payload = None

    if not task_payload or not isinstance(task_payload, dict) or not task_payload.get("title"):
        task_payload = _build_fallback_dynamic_task(
            sprint=sprint,
            role=role,
            difficulty=difficulty,
            next_sequence=next_sequence,
            previous_task=previous_task,
            trend=trend_data.get("trend", "stable"),
        )

    # Classify task_type enum safely
    task_type_val = str(task_payload.get("task_type", "coding")).lower()
    if focus_task_type:
        task_type_val = focus_task_type
    try:
        task_type_enum = TaskType(task_type_val)
    except ValueError:
        task_type_enum = TaskType.CODING

    # Classify priority enum safely
    priority_val = str(task_payload.get("priority", "medium")).lower()
    try:
        priority_enum = TaskPriority(priority_val)
    except ValueError:
        priority_enum = TaskPriority.MEDIUM

    hints = task_payload.get("hints") or [
        "Review project documentation and past submission feedback before implementing."
    ]
    if focus_dimensions:
        if not isinstance(hints, list):
            hints = [str(hints)]
        hints = list(hints)
        focus_hint = f"Practice {focus_dimensions[0]} while meeting the sprint objective."
        if focus_hint not in hints:
            hints.append(focus_hint)
    recommended_role = task_payload.get("recommended_team_role") or "Backend Developer"
    gen_reason = task_payload.get("generation_reason") or f"Generated for Sprint {sprint.sprint_number} objective."
    if focus_dimensions and focus_reason:
        gen_reason = f"{gen_reason} Competency focus: {focus_reason}"[:1000]

    start_time = datetime.now(timezone.utc)
    deadline = start_time + timedelta(days=3)

    task = Task(
        sprint_id=sprint.id,
        sequence=next_sequence,
        title=(task_payload["title"] or "Dynamic task")[:250],
        description=task_payload.get("description", f"Work on {task_payload['title']}."),
        acceptance_criteria=task_payload.get("acceptance_criteria") or [
            "Implementation satisfies specified functional requirements.",
            "Automated unit or integration tests are included.",
            "Code adheres to clean architecture principles."
        ],
        priority=priority_enum,
        status=TaskStatus.BACKLOG,
        task_type=task_type_enum,
        difficulty=difficulty,
        estimated_hours=float(task_payload.get("estimated_hours", 4.0)),
        deadline=deadline,
        task_metadata={
            "dynamic_generation": True,
            "generation_simulation_id": str(simulation.id),
            "generation_key": generation_key,
            "generation_version": 1,
            "generation_kind": generation_kind,
            "hints": hints,
            "recommended_team_role": recommended_role,
            "generation_reason": gen_reason,
            "performance_trend_at_generation": trend_data.get("trend", "stable"),
            "previous_task_id": str(previous_task.id) if previous_task else None,
            "competency_focus": ({
                "dimensions": focus_dimensions,
                "task_type": focus_task_type,
                "reason": focus_reason,
                "evidence_references": focus_evidence,
            } if focus_dimensions else None),
        }
    )
    db.add(task)
    db.flush()

    # If previous_task exists, register dependency
    if previous_task:
        db.add(TaskDependency(task_id=task.id, depends_on_task_id=previous_task.id))
        db.flush()

    return task


def _build_fallback_dynamic_task(
    sprint: Sprint,
    role: str,
    difficulty: DifficultyLevel,
    next_sequence: int,
    previous_task: Optional[Task],
    trend: str,
) -> Dict[str, Any]:
    """Deterministic fallback task generator when AI service is offline."""
    prefix = "[Scaffolded]" if trend in ("declining", "consistently_weak") else "[Advanced]" if trend in ("improving", "consistently_strong") else "[Core]"
    goal = (sprint.goal or "project objective")[:80]
    title = f"{prefix} {role} Task {next_sequence}: {goal}"
    
    return {
        "title": title,
        "description": f"Perform next logical step for '{sprint.goal}' in Sprint {sprint.sprint_number}. Target role: {role}. Difficulty: {difficulty.value}.",
        "acceptance_criteria": [
            "Implement module functionality matching sprint criteria.",
            "Add automated test cases for primary execution path.",
            "Verify error handling and response status codes."
        ],
        "priority": "high" if next_sequence == 1 else "medium",
        "task_type": "coding",
        "difficulty": difficulty.value,
        "estimated_hours": 4.0,
        "hints": [
            "Check dependencies and existing database models before starting.",
            "Reach out to your team lead or manager if you encounter blockers."
        ],
        "recommended_team_role": "Backend Developer",
        "generation_reason": f"Fallback dynamic task generated based on performance trend '{trend}'."
    }
