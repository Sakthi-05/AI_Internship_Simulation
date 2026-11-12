"""
POST /api/generate

The single entry point described in the spec: receives
student_id, role, technology_stack, difficulty, company_type
and generates an entire internship simulation (company, manager,
project, 4 sprints, and all their tasks with dependencies) in one
atomic transaction.
"""
import asyncio
import hashlib
import logging
import time
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser, get_current_user
from app.models.enums import CompanyType, DifficultyLevel, ScenarioStatus, SimulationStatus
from app.models.company import Company
from app.models.intelligence import StudentProfile
from app.models.project import Project
from app.models.project_state import ProjectState
from app.models.simulation import Scenario, ScenarioTask, Simulation
from app.models.sprint import Sprint
from app.models.task import Task
from app.schemas.generation import GenerationRequest, GenerationResponse
from app.services.ai.ai_service import (
    AIService,
    AIProviderResponseError,
    MockAIService,
    bind_ai_generation_context,
    get_ai_service,
    update_ai_simulation_context,
)

logger = logging.getLogger(__name__)
from app.services.generators.company_generator import generate_company
from app.services.generators.meeting_generator import generate_meetings_for_sprints
from app.services.generators.project_generator import generate_project
from app.services.engines.sprint_engine import generate_sprints_and_tasks
from app.services.engines.task_engine import generate_tasks_for_project
from app.services.generators.team_generator import generate_team_members

router = APIRouter(prefix="/api/generate", tags=["Generation"])
GENERATION_AI_TIMEOUT_SECONDS = 6.5


def _generation_lock_key(student_id: str, request_id: uuid.UUID) -> int:
    raw = f"{student_id}:{request_id}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)


def _acquire_generation_lock(db: Session, student_id: str, request_id: uuid.UUID) -> None:
    """Serialize same-key generation attempts without adding another table."""
    db.execute(select(func.pg_advisory_xact_lock(_generation_lock_key(student_id, request_id))))


@router.post("", response_model=GenerationResponse, status_code=status.HTTP_201_CREATED)
async def generate_internship_simulation(
    payload: GenerationRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    ai_service: AIService = Depends(get_ai_service),
):
    if payload.student_id != user.student_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="student_id in request body must match the authenticated token subject",
        )

    started_at = time.monotonic()
    generation_id = payload.generation_request_id or uuid.uuid4()
    generation_seed = str(generation_id)
    logger.info(
        "Internship generation received generation_id=%s student_id=%s request_id=%s",
        generation_id, user.student_id, payload.generation_request_id,
    )
    if payload.generation_request_id is not None:
        existing = _find_generation_request(db, user.student_id, payload.generation_request_id)
        if existing is not None:
            logger.info("Returning persisted generation generation_id=%s simulation_id=%s",
                        generation_id, existing.id)
            return _generation_response(db, existing)
        _acquire_generation_lock(db, user.student_id, payload.generation_request_id)
        # Another request may have completed while this request waited on the
        # transaction-scoped advisory lock.
        existing = _find_generation_request(db, user.student_id, payload.generation_request_id)
        if existing is not None:
            logger.info("Concurrent generation reused generation_id=%s simulation_id=%s",
                        generation_id, existing.id)
            return _generation_response(db, existing)

    role, technology_stack, difficulty, company_type, focus, scores = _resolve_generation_inputs(db, payload)
    generation_context_token = bind_ai_generation_context(str(generation_id))
    used_fallback = isinstance(ai_service, MockAIService)
    logger.info("Generation stage started generation_id=%s stage=company provider=%s",
                generation_id, "deterministic_fallback" if used_fallback else getattr(ai_service, "provider_name", "ai"))

    try:
        bundle = await asyncio.wait_for(_build_internship(
            db,
            student_id=user.student_id,
            role=role,
            technology_stack=technology_stack,
            difficulty=difficulty,
            company_type=company_type,
            focus=focus,
            scores=scores,
            generation_request_id=payload.generation_request_id,
            generation_seed=generation_seed,
            ai_service=ai_service,
        ), timeout=GENERATION_AI_TIMEOUT_SECONDS)
    except (httpx.HTTPError, TimeoutError, AIProviderResponseError) as exc:
        # Gemini failed or is unreachable (503, timeout, connection error…).
        # The whole transaction is rolled back, then generation is retried
        # with the deterministic local fallback — the student still gets a
        # complete, working internship simulation (only the AI flavor text
        # differs). Anything else keeps propagating exactly as before.
        db.rollback()
        if payload.generation_request_id is not None:
            _acquire_generation_lock(db, user.student_id, payload.generation_request_id)
            existing = _find_generation_request(db, user.student_id, payload.generation_request_id)
            if existing is not None:
                logger.info("Generation completed concurrently generation_id=%s simulation_id=%s",
                            generation_id, existing.id)
                return _generation_response(db, existing)
        used_fallback = True
        logger.warning(
            "Generation fallback activated generation_id=%s provider=%s reason=%s",
            generation_id, getattr(ai_service, "provider_name", "ai"), type(exc).__name__,
        )
        try:
            bundle = await _build_internship(
                db,
                student_id=user.student_id,
                role=role,
                technology_stack=technology_stack,
                difficulty=difficulty,
                company_type=company_type,
                focus=focus,
                scores=scores,
                generation_request_id=payload.generation_request_id,
                generation_seed=generation_seed,
                ai_service=MockAIService(),
            )
        except IntegrityError:
            db.rollback()
            existing = _find_generation_request(db, user.student_id, payload.generation_request_id)
            if existing is None:
                raise
            logger.info("Concurrent generation request deduplicated student_id=%s request_id=%s simulation_id=%s",
                        user.student_id, payload.generation_request_id, existing.id)
            return _generation_response(db, existing)
        except Exception:
            db.rollback()
            raise
    except IntegrityError:
        db.rollback()
        existing = _find_generation_request(db, user.student_id, payload.generation_request_id)
        if existing is None:
            raise
        logger.info("Concurrent generation request deduplicated student_id=%s request_id=%s simulation_id=%s",
                    user.student_id, payload.generation_request_id, existing.id)
        return _generation_response(db, existing)
    except Exception:
        db.rollback()
        raise
    finally:
        from app.services.ai.ai_service import AI_GENERATION_CONTEXT
        AI_GENERATION_CONTEXT.reset(generation_context_token)

    company = bundle["company"]
    project = bundle["project"]
    simulation = bundle["simulation"]
    sprints = bundle["sprints"]

    response = GenerationResponse(
        company_id=company.id,
        project_id=project.id,
        simulation_id=simulation.id,
        title=simulation.title,
        description=simulation.description,
        sprint_ids=[s.id for s in sprints],
        task_count=bundle["task_count"],
        duration_weeks=simulation.duration_weeks,
        difficulty=difficulty,
        role=role,
        company_type=company_type,
        focus_competencies=focus,
        generation_source=(simulation.simulation_metadata or {}).get("generation_source", "deterministic_fallback"),
    )
    logger.info("Internship generation completed generation_id=%s simulation_id=%s source=%s elapsed_ms=%d",
                generation_id, simulation.id, response.generation_source, int((time.monotonic() - started_at) * 1000))
    return response


def _find_generation_request(db, student_id: str, request_id: uuid.UUID | None):
    if request_id is None:
        return None
    return db.execute(select(Simulation).where(
        Simulation.student_id == student_id,
        Simulation.generation_request_id == request_id,
    )).scalar_one_or_none()


def _generation_response(db, simulation: Simulation) -> GenerationResponse:
    company = db.get(Company, simulation.company_id)
    project = db.get(Project, simulation.project_id)
    sprints = db.execute(select(Sprint).where(Sprint.project_id == project.id)
                         .order_by(Sprint.sprint_number)).scalars().all()
    task_count = db.execute(select(func.count()).select_from(Task)
                            .join(Sprint).where(Sprint.project_id == project.id)).scalar_one()
    profile = db.execute(select(StudentProfile).where(StudentProfile.student_id == simulation.student_id)).scalar_one_or_none()
    focus = ((profile.recommended_parameters or {}).get("focus_competencies") or []) if profile else []
    return GenerationResponse(company_id=company.id, project_id=project.id, simulation_id=simulation.id,
                              title=simulation.title, description=simulation.description,
                              sprint_ids=[s.id for s in sprints], task_count=task_count,
                              duration_weeks=simulation.duration_weeks, difficulty=simulation.difficulty,
                              role=simulation.role, company_type=company.company_type,
                              focus_competencies=focus,
                              generation_source=(simulation.simulation_metadata or {}).get(
                                  "generation_source", "deterministic_fallback"
                              ))


async def _build_internship(
    db,
    *,
    student_id,
    role,
    technology_stack,
    difficulty,
    company_type,
    focus,
    scores,
    generation_request_id,
    generation_seed,
    ai_service,
):
    """Build the full internship simulation in one atomic transaction.

    Commits on success; leaves rollback to the caller on failure. Only the
    company flavor text depends on the AI service — everything else is
    deterministic — so retrying with a local fallback AI reproduces a
    complete, valid simulation.
    """
    stage_started = time.monotonic()
    company, manager = await generate_company(
        db=db,
        student_id=student_id,
        company_type=company_type,
        role=role,
        ai_service=ai_service,
        technology_stack=technology_stack,
    )
    logger.info("Generation stage complete generation_id=%s stage=company student_id=%s elapsed_ms=%d",
                generation_request_id or generation_seed, student_id,
                int((time.monotonic() - stage_started) * 1000))

    stage_started = time.monotonic()
    project = generate_project(
        db=db,
        company_id=company.id,
        role=role,
        technology_stack=technology_stack,
        difficulty=difficulty,
        deterministic_seed=generation_seed,
    )
    logger.info("Generation stage complete generation_id=%s stage=project student_id=%s elapsed_ms=%d",
                generation_request_id or generation_seed, student_id,
                int((time.monotonic() - stage_started) * 1000))

    stage_started = time.monotonic()
    sprints = generate_sprints_and_tasks(db=db, project=project)
    logger.info("Generation stage complete generation_id=%s stage=sprints student_id=%s sprint_count=%d elapsed_ms=%d",
                generation_request_id or generation_seed, student_id, len(sprints),
                int((time.monotonic() - stage_started) * 1000))

    stage_started = time.monotonic()
    tasks = generate_tasks_for_project(
        db=db,
        project=project,
        role=role,
        technology_stack=technology_stack,
        difficulty=difficulty,
        student_id=student_id,
        focus_competencies=focus,
        competency_scores=scores,
    )
    logger.info("Generation stage complete generation_id=%s stage=tasks student_id=%s task_count=%d elapsed_ms=%d",
                generation_request_id or generation_seed, student_id, len(tasks),
                int((time.monotonic() - stage_started) * 1000))

    stage_started = time.monotonic()
    team_members = generate_team_members(db=db, company_id=company.id, deterministic_seed=generation_seed)
    generate_meetings_for_sprints(
        db=db, project_id=project.id, sprints=sprints, manager=manager, team_members=team_members
    )
    logger.info("Generation stage complete generation_id=%s stage=team_meetings student_id=%s elapsed_ms=%d",
                generation_request_id or generation_seed, student_id,
                int((time.monotonic() - stage_started) * 1000))

    # Ensure a project state exists and reflects the initial sprint context.
    state = db.execute(select(ProjectState).where(ProjectState.project_id == project.id)).scalar_one_or_none()
    if state is None:
        state = ProjectState(project_id=project.id)
        db.add(state)
        db.flush()
    state.current_sprint_number = 1
    state.pending_tasks = len(tasks)
    db.add(state)
    db.flush()

    simulation = Simulation(
        student_id=student_id,
        generation_request_id=generation_request_id,
        company_id=company.id,
        project_id=project.id,
        title=f"{project.title} Internship",
        description=f"Internship simulation for {role} on {project.title}.",
        role=role,
        difficulty=difficulty,
        duration_weeks=project.duration_weeks,
        status=SimulationStatus.NOT_STARTED,
        progress=0,
        simulation_metadata={
            "generation_status": "completed",
            "generation_source": "deterministic_fallback" if isinstance(ai_service, MockAIService)
            else getattr(ai_service, "provider_name", "ai"),
            "generation_id": str(generation_request_id or generation_seed),
        },
    )
    db.add(simulation)
    db.flush()
    update_ai_simulation_context(str(simulation.id))
    from app.models.meeting import MeetingSchedule
    from app.models.enums import MeetingType, NotificationType
    from app.services.notification_service import notify
    meetings = db.execute(select(MeetingSchedule).where(MeetingSchedule.project_id == project.id)).scalars().all()
    for meeting in meetings:
        meeting.simulation_id = simulation.id
        db.add(meeting)
        if meeting.meeting_type == MeetingType.SPRINT_PLANNING:
            notify(
                db, company.id, NotificationType.MEETING_SCHEDULED,
                f"Sprint planning: {meeting.agenda}",
                simulation_id=simulation.id,
                context={"meeting_id": str(meeting.id), "project_id": str(project.id)},
                dedupe_key=f"meeting-invite:{meeting.id}",
            )
    _link_workplace_scenario(db, simulation, project, tasks, role, difficulty, technology_stack)

    task_count = len(tasks)

    _validate_generation_bundle(db, company, manager, project, sprints, tasks, simulation)

    stage_started = time.monotonic()
    db.commit()
    logger.info("Generation stage complete generation_id=%s simulation_id=%s stage=persistence elapsed_ms=%d",
                generation_request_id or generation_seed, simulation.id,
                int((time.monotonic() - stage_started) * 1000))
    return {
        "company": company,
        "project": project,
        "simulation": simulation,
        "sprints": sprints,
        "task_count": task_count,
    }


def _validate_generation_bundle(db, company, manager, project, sprints, tasks, simulation) -> None:
    """Reject incomplete generator output before the atomic commit."""
    from app.services.engines.sprint_engine import SPRINT_NAMES
    from app.services.simulation.task_access_service import validate_project_dependency_graph

    if not company.name.strip() or not manager.name.strip() or not project.title.strip():
        raise ValueError("Generation produced an incomplete company, manager, or project")
    if len(sprints) != 4 or [s.sprint_number for s in sprints] != [1, 2, 3, 4]:
        raise ValueError("Generation must produce exactly four sequential sprints")
    if tuple(s.name for s in sprints) != SPRINT_NAMES:
        raise ValueError("Generation produced invalid sprint names")
    if not tasks:
        raise ValueError("Generation produced no project tasks")
    sprint_ids = {sprint.id for sprint in sprints}
    # Existing task engine sequences start at zero within each sprint.
    if any(task.sprint_id not in sprint_ids or task.sequence < 0 for task in tasks):
        raise ValueError("Generation produced a task with an invalid sprint or sequence")
    if len({(task.sprint_id, task.sequence) for task in tasks}) != len(tasks):
        raise ValueError("Generation produced duplicate task sequences")
    validate_project_dependency_graph(db, project.id)
    db.flush()
    persisted_task_count = db.execute(
        select(func.count()).select_from(Task).join(Sprint).where(Sprint.project_id == project.id)
    ).scalar_one()
    if persisted_task_count != len(tasks):
        raise ValueError("Persisted task count does not match generated task data")
    state = db.execute(
        select(ProjectState).where(ProjectState.project_id == project.id)
    ).scalar_one_or_none()
    if state is None or state.current_sprint_number != 1 or state.pending_tasks != len(tasks):
        raise ValueError("Project state was not persisted with the generated tasks")


def _link_workplace_scenario(db, simulation, project, tasks, role, difficulty, technology_stack):
    """Attach one workplace scenario so scenario events can run. Skip if one already exists."""
    existing = db.execute(
        select(Scenario).where(Scenario.simulation_id == simulation.id).order_by(Scenario.sequence)
    ).scalars().first()
    if existing is not None:
        if simulation.current_scenario_id is None:
            simulation.current_scenario_id = existing.id
            db.add(simulation)
            db.flush()
        return existing

    scenario = Scenario(
        simulation_id=simulation.id,
        sequence=1,
        sequence_order=1,
        title=f"{project.title} workplace",
        description=f"Workplace scenario for the {role} internship.",
        workplace_context=f"You are the {role} on {project.title}. Complete the assigned work and respond to workplace events.",
        role=role,
        difficulty=difficulty,
        required_skills=list(technology_stack or [])[:8],
        objectives=list(project.objectives or [])[:6] or [f"Complete the {role} internship tasks"],
        status=ScenarioStatus.PENDING,
    )
    db.add(scenario)
    db.flush()
    for index, task in enumerate(tasks, start=1):
        db.add(ScenarioTask(scenario_id=scenario.id, task_id=task.id, sequence=index))
    simulation.current_scenario_id = scenario.id
    db.add(simulation)
    db.flush()
    return scenario


def _resolve_generation_inputs(db, payload: GenerationRequest):
    """Prefer an assessed Module 1 profile over the manually posted difficulty."""
    profile = db.execute(
        select(StudentProfile).where(StudentProfile.student_id == payload.student_id)
    ).scalar_one_or_none()
    if profile is None or profile.last_assessed_at is None:
        return payload.role, payload.technology_stack, payload.difficulty, payload.company_type, [], {}

    params = profile.recommended_parameters or {}
    role = params.get("role") or profile.role or payload.role
    stack = params.get("technology_stack") or profile.skills or payload.technology_stack
    try:
        difficulty = DifficultyLevel(params.get("difficulty") or profile.recommended_difficulty or payload.difficulty.value)
    except ValueError:
        difficulty = payload.difficulty
    try:
        company_type = CompanyType(params.get("company_type") or payload.company_type.value)
    except ValueError:
        company_type = payload.company_type
    focus = [item for item in (params.get("focus_competencies") or []) if isinstance(item, str)]
    scores = {key: float(value) for key, value in (profile.baseline_competencies or {}).items()}
    return role, stack, difficulty, company_type, focus, scores
