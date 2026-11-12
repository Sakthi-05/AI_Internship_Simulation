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
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import DifficultyLevel, SimulationStatus, TaskPriority, TaskStatus, TaskType
from app.models.intelligence import InteractionEvaluation, StudentProfile
from app.models.simulation import Simulation
from app.models.sprint import Sprint
from app.models.submission import AIReview, Submission, SubmissionVersion
from app.models.task import Task
from app.models.task_path import SimulationTaskPath
from app.models.project import Project
from app.models.simulation_interaction import SimulationInteraction
from app.services.ai.ai_service import get_ai_service
from app.services.intelligence_service import calculate_performance_trend, get_or_create_profile
from app.services.technical_profile_service import get_student_technical_profile
from app.services.simulation.task_access_service import add_task_dependency

logger = logging.getLogger(__name__)


async def generate_dynamic_task(
    db: Session,
    simulation: Simulation,
    sprint: Sprint,
    student_id: str,
    previous_task: Optional[Task] = None,
    competency_focus: Optional[Dict[str, Any]] = None,
    provider_timeout_seconds: Optional[float] = None,
) -> Task:
    """Generate next task dynamically based on actual student technical work."""
    # A generation request is keyed by its predecessor (or by initial sprint
    # for the first task). Locking the simulation serializes retries so a
    # committed task is returned instead of generating a duplicate.
    persisted_status = db.execute(
        select(Simulation.status)
        .where(Simulation.id == simulation.id)
        .with_for_update()
    ).scalar_one()
    if persisted_status == SimulationStatus.COMPLETED:
        raise ValueError("Cannot generate tasks for a completed simulation")
    generation_key = f"{simulation.id}:{sprint.id}:{previous_task.id if previous_task else 'initial'}"
    existing_tasks = db.execute(
        select(Task).where(Task.sprint_id == sprint.id).order_by(Task.sequence)
    ).scalars().all()
    for existing in existing_tasks:
        metadata = existing.task_metadata or {}
        if metadata.get("dynamic_generation_key") == generation_key:
            return existing
        # Support tasks created before generation keys were introduced.
        if previous_task and metadata.get("previous_task_id") == str(previous_task.id):
            return existing

    profile = get_or_create_profile(db, student_id)
    trend_data = calculate_performance_trend(db, student_id, simulation.id)
    module3_evidence = _read_module3_progression_evidence(db, simulation, student_id)
    # Module 3 owns evaluation and its branch decision. Module 2 only consumes
    # that persisted result to shape the next task; it never writes scores or
    # competency values back to the profile.
    progression_trend = {
        "high_performance": "consistently_strong",
        "stable": "stable",
        "risk_escalation": "consistently_weak",
    }.get(module3_evidence.get("branch_selected"), trend_data.get("trend", "stable"))
    technical_profile = get_student_technical_profile(db, student_id)
    technology_ratings = technical_profile.get("technology_ratings") or []
    project = db.get(Project, simulation.project_id)
    technologies = list((project.technology_stack if project else None) or profile.skills or [])
    relevant_ratings = [
        item for item in technology_ratings
        if any(
            term and (term in str(item.get("technology_id", "")).casefold()
                      or term in str(item.get("name", "")).casefold())
            for technology in technologies
            for term in (str(technology).casefold(),)
        )
    ]
    familiarity = [
        f"{item.get('name') or item.get('technology_id')}: {item.get('self_rating')}/5"
        for item in technology_ratings if item.get("self_rating") is not None
    ]
    relevant_familiarity = [
        f"{item.get('name') or item.get('technology_id')}: {item.get('self_rating')}/5"
        for item in relevant_ratings if item.get("self_rating") is not None
    ]
    
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
    review_signals: list[str] = []
    submission_evidence: list[str] = []
    if previous_task:
        latest_sub = db.execute(
            select(Submission)
            .where(Submission.task_id == previous_task.id, Submission.student_id == student_id)
        ).scalar_one_or_none()
        if latest_sub and latest_sub.versions:
            reviews = []
            for ver in latest_sub.versions:
                filenames = [
                    str(item.get("filename") or item.get("name"))
                    for item in (ver.files or []) if isinstance(item, dict) and (item.get("filename") or item.get("name"))
                ]
                submission_evidence.append(
                    f"version={ver.version_number}, files={filenames[:8]}, metadata={ver.version_metadata or {}}"
                )
                for rev in ver.ai_reviews:
                    if rev.summary:
                        signals = [
                            f"score={rev.score}" if rev.score is not None else None,
                            f"strengths={'; '.join((rev.strengths or [])[:4])}" if rev.strengths else None,
                            f"risks={'; '.join((rev.risks or [])[:4])}" if rev.risks else None,
                            f"actions={'; '.join((rev.actions or [])[:4])}" if rev.actions else None,
                        ]
                        review_signals.extend(item for item in signals if item)
                        reviews.append(f"v{ver.version_number}: {rev.summary[:300]}")
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
        f"Technical Domain: {technical_profile.get('domain', {}).get('name') if technical_profile.get('domain') else profile.domain or 'unspecified'} ({technical_profile.get('technical_domain_id') or profile.technical_domain_id or 'no catalog id'})\n"
        f"Project Technologies: {technologies}\n"
        f"Student Technology Familiarity (1=beginner, 5=expert): {familiarity or 'unrated'}\n"
        f"Current Competencies (Module 1 / latest available profile): {profile.current_competencies or profile.baseline_competencies}\n"
        f"Student Strengths: {profile.strengths or []}\n"
        f"Student Weaknesses: {profile.weaknesses or []}\n"
        f"Project Objectives: {project.objectives if project else []}\n"
        f"Project Deliverables: {project.deliverables if project else []}\n"
        f"Completed Tasks in Sprint: {completed_titles}\n"
        f"Previous Task Worked: {previous_task.title if previous_task else 'None (First Task)'}\n"
        f"Previous Review History: {previous_review_summary or 'N/A'}\n"
        f"Previous Submission Evidence: {submission_evidence or 'N/A'}\n"
        f"Module 3 Review Evidence: {review_signals or 'N/A'}\n"
        f"Module 3 Evaluation Evidence (persisted by Module 3; read-only): {module3_evidence.get('evaluations') or 'N/A'}\n"
        f"Module 3 Progression Signal (authoritative branch; no result means use baseline/submission evidence): {module3_evidence.get('branch_selected') or 'unavailable'}\n"
        f"Performance Trend: {trend_data.get('trend', 'stable')} (Recent Avg: {trend_data.get('recent_avg', 60)})\n"
        f"Difficulty Level: {difficulty.value}\n\n"
        f"Rules:\n"
        f"1. Generate a task directly advancing the sprint objective and building on completed work.\n"
        f"2. Tailor technologies and prerequisite assumptions to the student's domain and familiarity ratings; teach low-rated technologies with scaffolding.\n"
        f"3. Use review evidence: reinforce demonstrated strengths and address concrete weaknesses/actions in the next appropriate task.\n"
        f"4. For declining/weak performance, reduce scope and add explicit steps; for stable/moderate performance, maintain scope and add one new integration; for strong/improving performance, increase architectural depth and ownership.\n"
        f"5. Vary the actual problem, objective, deliverable, acceptance criteria, and dependency from prior tasks; do not only change a difficulty label.\n"
        f"6. Select dependency_strategy as 'previous' for direct follow-up work, 'foundation' when it only needs the initial project task, or 'independent' for standalone design discovery.\n"
        f"7. Output valid JSON with keys: title, description, objectives (array), deliverables (array), acceptance_criteria (array), dependency_strategy, priority, task_type, difficulty, estimated_hours, hints, recommended_team_role, generation_reason."
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
    provider_started = time.monotonic()
    try:
        provider_call = ai_service.generate_json(prompt, system_prompt)
        task_payload = (
            await asyncio.wait_for(provider_call, timeout=provider_timeout_seconds)
            if provider_timeout_seconds is not None
            else await provider_call
        )
        logger.info(
            "Dynamic task provider stage complete generation_id=%s simulation_id=%s provider=%s elapsed_ms=%d",
            generation_key, simulation.id, getattr(ai_service, "provider_name", "mock"),
            int((time.monotonic() - provider_started) * 1000),
        )
    except Exception as exc:
        logger.warning(
            "Dynamic task fallback activated generation_id=%s simulation_id=%s reason=%s elapsed_ms=%d",
            generation_key, simulation.id, type(exc).__name__,
            int((time.monotonic() - provider_started) * 1000),
        )
        task_payload = None

    if not task_payload or not isinstance(task_payload, dict) or not task_payload.get("title"):
        task_payload = _build_fallback_dynamic_task(
            sprint=sprint,
            role=role,
            difficulty=difficulty,
            next_sequence=next_sequence,
            previous_task=previous_task,
            trend=trend_data.get("trend", "stable"),
            progression_signal=progression_trend,
            technologies=technologies,
            technical_domain=(technical_profile.get("domain") or {}).get("name") or profile.domain,
            familiarity=relevant_familiarity,
            review_signals=review_signals,
            strengths=profile.strengths or [],
            weaknesses=profile.weaknesses or [],
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

    hints = _as_text_list(task_payload.get("hints")) or [
        "Review project documentation and past submission feedback before implementing."
    ]
    if focus_dimensions:
        focus_hint = f"Practice {focus_dimensions[0]} while meeting the sprint objective."
        if focus_hint not in hints:
            hints.append(focus_hint)
    recommended_role = task_payload.get("recommended_team_role") or "Backend Developer"
    gen_reason = task_payload.get("generation_reason") or f"Generated for Sprint {sprint.sprint_number} objective."
    if focus_dimensions and focus_reason:
        gen_reason = f"{gen_reason} Competency focus: {focus_reason}"[:1000]

    start_time = datetime.now(timezone.utc)
    deadline = start_time + timedelta(days=3)

    task_difficulty = _resolve_task_difficulty(
        task_payload.get("difficulty"), difficulty, progression_trend, relevant_familiarity
    )
    dependency_strategy = _resolve_dependency_strategy(
        task_payload.get("dependency_strategy"), task_type_enum
    )
    task = Task(
        sprint_id=sprint.id,
        sequence=next_sequence,
        title=str(task_payload["title"] or "Dynamic task")[:250],
        description=str(task_payload.get("description") or f"Work on {task_payload['title']}.")[:10000],
        acceptance_criteria=_as_text_list(task_payload.get("acceptance_criteria")) or [
            "Implementation satisfies specified functional requirements.",
            "Automated unit or integration tests are included.",
            "Code adheres to clean architecture principles."
        ],
        priority=priority_enum,
        status=TaskStatus.BACKLOG,
        task_type=task_type_enum,
        difficulty=task_difficulty,
        estimated_hours=_estimated_hours(task_payload.get("estimated_hours", 4.0)),
        deadline=deadline,
        task_metadata={
            "hints": hints,
            "objectives": _as_text_list(task_payload.get("objectives")) or [sprint.goal],
            "deliverables": _as_text_list(task_payload.get("deliverables")) or [task_payload.get("title", "Completed implementation")],
            "technical_domain": (technical_profile.get("domain") or {}).get("name") or profile.domain,
            "technology_context": technologies,
            "technology_familiarity": familiarity,
            "recommended_team_role": recommended_role,
            "generation_reason": gen_reason,
            "performance_trend_at_generation": trend_data.get("trend", "stable"),
            "module3_progression_evidence": module3_evidence,
            "previous_task_id": str(previous_task.id) if previous_task else None,
            "dynamic_generation_key": generation_key,
            "dependency_strategy": dependency_strategy,
            "complexity_profile": {
                "difficulty": task_difficulty.value,
                "performance_trend": trend_data.get("trend", "stable"),
                "module3_branch": module3_evidence.get("branch_selected"),
                "recent_average": trend_data.get("recent_avg", 60),
                "familiarity_considered": bool(familiarity),
            },
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

    dependency_task = previous_task if dependency_strategy == "previous" else None
    if dependency_strategy == "foundation":
        dependency_task = db.execute(
            select(Task)
            .join(SimulationTaskPath, SimulationTaskPath.task_id == Task.id)
            .where(SimulationTaskPath.simulation_id == simulation.id)
            .order_by(SimulationTaskPath.sequence.asc())
        ).scalars().first()
    if dependency_task and dependency_task.id != task.id:
        add_task_dependency(db, task.id, dependency_task.id)

    return task


def _read_module3_progression_evidence(db: Session, simulation: Simulation, student_id: str) -> Dict[str, Any]:
    """Read Module 3 evaluation records for task adaptation without evaluating them."""
    rows = db.execute(
        select(InteractionEvaluation, SimulationInteraction)
        .join(SimulationInteraction, SimulationInteraction.id == InteractionEvaluation.interaction_id)
        .where(
            InteractionEvaluation.simulation_id == simulation.id,
            InteractionEvaluation.student_id == student_id,
            SimulationInteraction.simulation_id == simulation.id,
            SimulationInteraction.student_id == student_id,
        )
        .order_by(SimulationInteraction.sequence.desc())
        .limit(5)
    ).all()
    evaluations = []
    for evaluation, interaction in reversed(rows):
        evaluations.append({
            "evaluation_id": str(evaluation.id),
            "interaction_id": str(interaction.id),
            "task_id": str(interaction.task_id) if interaction.task_id else None,
            "branch_selected": evaluation.branch_selected,
            "competencies": evaluation.competencies or {},
            "strengths": evaluation.strengths or [],
            "weaknesses": evaluation.weaknesses or [],
            "feedback": (evaluation.feedback or "")[:500],
            "recommendations": (evaluation.recommendations or [])[:5],
        })
    latest_branch = next(
        (item["branch_selected"] for item in reversed(evaluations)
         if item["branch_selected"] in {"high_performance", "stable", "risk_escalation"}),
        None,
    )
    return {"branch_selected": latest_branch, "evaluations": evaluations}


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:500] for item in value if isinstance(item, (str, int, float)) and str(item).strip()]


def _estimated_hours(value: Any) -> float:
    try:
        hours = float(value)
    except (TypeError, ValueError):
        return 4.0
    if not 0.5 <= hours <= 40:
        return 4.0
    return hours


def _resolve_dependency_strategy(proposed: Any, task_type: TaskType) -> str:
    strategies = {"previous", "foundation", "independent"}
    if isinstance(proposed, str) and proposed.lower() in strategies:
        return proposed.lower()
    if task_type in (TaskType.DESIGN, TaskType.REQUIREMENT_ANALYSIS):
        return "independent"
    if task_type == TaskType.DOCUMENTATION:
        return "foundation"
    return "previous"


def _resolve_task_difficulty(
    proposed: Any,
    base: DifficultyLevel,
    trend: str,
    familiarity: list[str],
) -> DifficultyLevel:
    """Set real task complexity within one level of the simulation baseline."""
    levels = list(DifficultyLevel)
    try:
        index = levels.index(DifficultyLevel(proposed))
    except (ValueError, TypeError):
        index = levels.index(base)
    base_index = levels.index(base)
    low_familiarity = any(_rating_from_familiarity(value) <= 2 for value in familiarity)
    if trend in ("declining", "consistently_weak") or low_familiarity:
        index = min(index, max(base_index - 1, 0))
    elif trend in ("improving", "consistently_strong"):
        index = max(index, min(base_index + 1, len(levels) - 1))
    return levels[max(base_index - 1, min(index, base_index + 1))]


def _rating_from_familiarity(value: str) -> int:
    try:
        return int(value.split("/5")[0].rsplit(": ", 1)[-1])
    except (TypeError, ValueError):
        return 3


def _build_fallback_dynamic_task(
    sprint: Sprint,
    role: str,
    difficulty: DifficultyLevel,
    next_sequence: int,
    previous_task: Optional[Task],
    trend: str,
    technologies: Optional[list[str]] = None,
    technical_domain: Optional[str] = None,
    familiarity: Optional[list[str]] = None,
    review_signals: Optional[list[str]] = None,
    strengths: Optional[list[str]] = None,
    weaknesses: Optional[list[str]] = None,
    progression_signal: Optional[str] = None,
) -> Dict[str, Any]:
    """Deterministic fallback task generator when AI service is offline."""
    trend = progression_signal or trend
    weak = trend in ("declining", "consistently_weak") or any(
        _rating_from_familiarity(item) <= 2 for item in (familiarity or [])
    )
    strong = trend in ("improving", "consistently_strong")
    prefix = "[Scaffolded]" if weak else "[Advanced]" if strong else "[Core]"
    goal = sprint.goal or "project objective"
    technology_options = technologies or ["the project technology stack"]
    technology = technology_options[(next_sequence - 1) % len(technology_options)]
    phases = [
        ("Design and model", f"Define the data contract and boundary for {goal}", "A reviewed design note and schema or interface draft"),
        ("Implement and validate", f"Implement input validation and failure handling for {goal}", "Working validation path with focused tests"),
        ("Integrate and observe", f"Integrate {goal} with its adjacent service and add operational signals", "Integration evidence and useful logs or metrics"),
        ("Optimize and secure", f"Improve performance and security for {goal}", "Measured improvement with regression coverage"),
        ("Document and release", f"Prepare {goal} for maintainable release", "Release notes, runbook, and verification results"),
    ]
    phase, objective, deliverable = phases[(next_sequence - 1) % len(phases)]
    if weak:
        objective = f"Step by step, {objective.lower()} using {technology}; first reproduce the happy path, then handle one failure case."
        deliverable = f"Small, reviewable change: {deliverable}; include a short explanation of each step."
        if weaknesses:
            objective += f" Include a deliberate practice check for: {', '.join(weaknesses[:2])}."
    elif strong:
        objective = f"Own an end-to-end, production-ready solution: {objective}, including trade-off analysis for {technology}."
        deliverable = f"Production-ready {deliverable} with a documented trade-off and rollback plan."
        if strengths:
            objective += f" Apply a demonstrated strength: {', '.join(strengths[:2])}."
    title = f"{prefix} {role}: {phase} — {goal[:65]}"
    
    return {
        "title": title,
        "description": f"{objective}\n\nDomain: {technical_domain or 'general software engineering'}. Apply {technology} at a scope matched to the student's demonstrated familiarity. Build on {previous_task.title if previous_task else 'the project foundation'}. Review evidence to address: {'; '.join((review_signals or [])[:3]) or 'no prior technical review available'}.",
        "objectives": [objective],
        "deliverables": [deliverable],
        "acceptance_criteria": [
            f"The {phase.lower()} outcome satisfies the sprint goal: {goal}.",
            f"Deliverable includes concrete evidence for {technology} and is reviewable by another engineer.",
            "Automated tests cover the primary path and at least one relevant failure or boundary case.",
            "Document assumptions, known limitations, and how the result was verified.",
        ],
        "priority": "high" if next_sequence == 1 else "medium",
        "task_type": ["design", "coding", "api_development", "testing", "documentation"][(next_sequence - 1) % 5],
        "dependency_strategy": ["independent", "previous", "previous", "previous", "foundation"][(next_sequence - 1) % 5],
        "difficulty": ("beginner" if weak and difficulty != DifficultyLevel.BEGINNER else "expert" if strong and difficulty == DifficultyLevel.EXPERT else difficulty.value),
        "estimated_hours": 2.0 if weak else 7.0 if strong else 4.0 + ((next_sequence - 1) % 3),
        "hints": [
            "Check dependencies and existing database models before starting.",
            "Reach out to your team lead or manager if you encounter blockers."
        ],
        "recommended_team_role": "Backend Developer",
        "generation_reason": f"Deterministic fallback for {technical_domain or 'the technical domain'}; varied work phase '{phase}' and scope using performance trend '{trend}'."
    }
