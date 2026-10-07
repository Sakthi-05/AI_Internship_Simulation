"""
POST /api/generate

The single entry point described in the spec: receives
student_id, role, technology_stack, difficulty, company_type
and generates an entire internship simulation (company, manager,
project, 4 sprints, and all their tasks with dependencies) in one
atomic transaction.
"""
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser, get_current_user
from app.models.enums import CompanyType, DifficultyLevel, ScenarioStatus, SimulationStatus
from app.models.intelligence import StudentProfile
from app.models.project_state import ProjectState
from app.models.simulation import Scenario, ScenarioTask, Simulation
from app.schemas.generation import GenerationRequest, GenerationResponse
from app.services.ai.ai_service import AIService, MockAIService, get_ai_service

logger = logging.getLogger(__name__)
from app.services.generators.company_generator import generate_company
from app.services.generators.meeting_generator import generate_meetings_for_sprints
from app.services.generators.project_generator import generate_project
from app.services.engines.sprint_engine import generate_sprints_and_tasks
from app.services.engines.task_engine import generate_tasks_for_project
from app.services.generators.team_generator import generate_team_members

router = APIRouter(prefix="/api/generate", tags=["Generation"])


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

    role, technology_stack, difficulty, company_type, focus, scores = _resolve_generation_inputs(db, payload)

    try:
        bundle = await _build_internship(
            db,
            student_id=payload.student_id,
            role=role,
            technology_stack=technology_stack,
            difficulty=difficulty,
            company_type=company_type,
            focus=focus,
            scores=scores,
            ai_service=ai_service,
        )
    except httpx.HTTPError as exc:
        # Gemini failed or is unreachable (503, timeout, connection error…).
        # The whole transaction is rolled back, then generation is retried
        # with the deterministic local fallback — the student still gets a
        # complete, working internship simulation (only the AI flavor text
        # differs). Anything else keeps propagating exactly as before.
        db.rollback()
        logger.warning("Gemini unavailable during /api/generate (%s); using local fallback", exc)
        try:
            bundle = await _build_internship(
                db,
                student_id=payload.student_id,
                role=role,
                technology_stack=technology_stack,
                difficulty=difficulty,
                company_type=company_type,
                focus=focus,
                scores=scores,
                ai_service=MockAIService(),
            )
        except Exception:
            db.rollback()
            raise
    except Exception:
        db.rollback()
        raise

    company = bundle["company"]
    project = bundle["project"]
    simulation = bundle["simulation"]
    sprints = bundle["sprints"]

    return GenerationResponse(
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
    )


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
    ai_service,
):
    """Build the full internship simulation in one atomic transaction.

    Commits on success; leaves rollback to the caller on failure. Only the
    company flavor text depends on the AI service — everything else is
    deterministic — so retrying with a local fallback AI reproduces a
    complete, valid simulation.
    """
    company, manager = await generate_company(
        db=db,
        student_id=student_id,
        company_type=company_type,
        role=role,
        ai_service=ai_service,
        technology_stack=technology_stack,
    )

    project = generate_project(
        db=db,
        company_id=company.id,
        role=role,
        technology_stack=technology_stack,
        difficulty=difficulty,
    )

    sprints = generate_sprints_and_tasks(db=db, project=project)

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

    team_members = generate_team_members(db=db, company_id=company.id)
    generate_meetings_for_sprints(
        db=db, project_id=project.id, sprints=sprints, manager=manager, team_members=team_members
    )

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
        company_id=company.id,
        project_id=project.id,
        title=f"{project.title} Internship",
        description=f"Internship simulation for {role} on {project.title}.",
        role=role,
        difficulty=difficulty,
        duration_weeks=project.duration_weeks,
        status=SimulationStatus.NOT_STARTED,
        progress=0,
    )
    db.add(simulation)
    db.flush()
    _link_workplace_scenario(db, simulation, project, tasks, role, difficulty, technology_stack)

    task_count = len(tasks)

    db.commit()
    return {
        "company": company,
        "project": project,
        "simulation": simulation,
        "sprints": sprints,
        "task_count": task_count,
    }


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
