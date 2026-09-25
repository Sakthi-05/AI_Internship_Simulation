"""
POST /api/generate

The single entry point described in the spec: receives
student_id, role, technology_stack, difficulty, company_type
and generates an entire internship simulation (company, manager,
project, 4 sprints, and all their tasks with dependencies) in one
atomic transaction.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser, get_current_user
from app.models.enums import CompanyType, DifficultyLevel, SimulationStatus
from app.models.intelligence import StudentProfile
from app.models.project_state import ProjectState
from app.models.simulation import Simulation
from app.schemas.generation import GenerationRequest, GenerationResponse
from app.services.ai.ai_service import AIService, get_ai_service
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
        company, manager = await generate_company(
            db=db,
            student_id=payload.student_id,
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
            student_id=payload.student_id,
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
            student_id=payload.student_id,
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

        task_count = len(tasks)

        db.commit()
    except Exception:
        db.rollback()
        raise

    return GenerationResponse(
        company_id=company.id,
        project_id=project.id,
        simulation_id=simulation.id,
        sprint_ids=[s.id for s in sprints],
        task_count=task_count,
        difficulty=difficulty,
        role=role,
        company_type=company_type,
        focus_competencies=focus,
    )


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
