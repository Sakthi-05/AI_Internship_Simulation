import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, status, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser, get_current_user
from app.models.company import Company
from app.models.enums import MessageSenderType, SimulationStatus, ScenarioStatus, TaskStatus, SubmissionStatus, WorkflowEventStatus, WorkflowEventType
from app.models.message import Message
from app.models.project import Project
from app.models.sprint import Sprint
from app.models.simulation import Simulation, Scenario, ScenarioTask, WorkflowEvent
from app.models.soft_skill import SoftSkillEvidence  # noqa: F401
from app.models.task import Task
from app.models.task_path import SimulationTaskPath
from app.models.submission import Submission, SubmissionVersion
from app.services.engines import trigger_workflow_event
from app.services.engines.simulation_task_state_machine import (
    SimulationTaskState,
    validate_simulation_task_transition,
    can_start_simulation_task,
    get_simulation_task_transition_action,
    get_project_status_for_simulation_state,
)
from app.services.submission.submission_service import SubmissionService
from app.services.file_storage import FileValidationError
from app.services.activity_log_service import log_activity
from app.services.ai.ai_service import AIService, get_ai_service
from app.services.criteria_version_service import criteria_identity, criteria_view
from app.services.simulation.interaction_history_service import (
    list_interactions,
    record_interaction,
    serialize_interaction,
)

router = APIRouter(prefix="/api/simulations", tags=["Simulations"])


class CreateSimulationRequest(BaseModel):
    company_id: uuid.UUID
    project_id: uuid.UUID


class StartSimulationRequest(BaseModel):
    task_ids: Optional[List[uuid.UUID]] = None


@router.get("/mine")
def list_my_simulations(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """List the authenticated student's internship simulations (newest first).

    The frontend uses this after login to decide between
    "Start Internship Simulation" (no existing progress) and
    "Resume Internship Simulation" (existing progress to restore),
    so progress survives logout/login and browser-storage loss.
    """
    rows = db.execute(
        select(Simulation)
        .where(Simulation.student_id == user.student_id)
        .order_by(Simulation.created_at.desc())
    ).scalars().all()
    return [
        {
            "simulation_id": str(sim.id),
            "company_id": str(sim.company_id),
            "project_id": str(sim.project_id),
            "title": sim.title,
            "description": sim.description,
            "role": sim.role,
            "difficulty": sim.difficulty.value,
            "duration_weeks": sim.duration_weeks,
            "status": sim.status.value,
            "progress": sim.progress,
            "current_task_id": str(sim.current_task_id) if sim.current_task_id else None,
            "current_scenario_id": str(sim.current_scenario_id) if sim.current_scenario_id else None,
            "started_at": sim.started_at.isoformat() if sim.started_at else None,
            "completed_at": sim.completed_at.isoformat() if sim.completed_at else None,
            "created_at": sim.created_at.isoformat() if sim.created_at else None,
        }
        for sim in rows
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_simulation(
    body: CreateSimulationRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Create a new simulation for the given company and project."""
    company = db.get(Company, body.company_id)
    if company is None or company.student_id != user.student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your company")

    project = db.get(Project, body.project_id)
    if project is None or project.company_id != body.company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Check if a simulation already exists for this student + project
    existing = db.execute(
        select(Simulation).where(
            Simulation.student_id == user.student_id,
            Simulation.project_id == body.project_id,
        )
    ).scalar_one_or_none()
    if existing:
        return {
            "simulation_id": str(existing.id),
            "title": existing.title,
            "status": existing.status.value,
        }

    simulation = Simulation(
        student_id=user.student_id,
        company_id=body.company_id,
        project_id=body.project_id,
        title=f"{project.title} Simulation",
        description=f"Internship simulation for {project.title}",
        role=project.role,
        difficulty=project.difficulty,
        duration_weeks=project.duration_weeks,
        status=SimulationStatus.NOT_STARTED,
        progress=0,
    )
    db.add(simulation)
    db.flush()
    db.refresh(simulation)

    # Scenarios are no longer created as duplicate versions of Sprints.
    # The student-specific path is generated via SimulationTaskPath during start_simulation.
    # Sprints remain the sole canonical source of project task groupings.


    return {
        "simulation_id": str(simulation.id),
        "title": simulation.title,
        "description": simulation.description,
        "role": simulation.role,
        "difficulty": simulation.difficulty.value,
        "duration_weeks": simulation.duration_weeks,
        "status": simulation.status.value,
    }


def _get_owned_simulation(
    simulation_id: uuid.UUID,
    db: Session,
    user: CurrentUser,
) -> Simulation:
    simulation = db.get(Simulation, simulation_id)
    if simulation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found")
    company = db.get(Company, simulation.company_id)
    if simulation.student_id != user.student_id or company is None or company.student_id != user.student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your simulation")
    return simulation


@router.get("/{simulation_id}")
def get_simulation(
    simulation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Get simulation with current scenario, task, and progress information."""
    simulation = _get_owned_simulation(simulation_id, db, user)
    # Recover stale/missing state before serialising the workspace.  This makes
    # opening an existing simulation land on its valid task, not a wait screen.
    if simulation.status == SimulationStatus.IN_PROGRESS:
        from app.services.simulation.task_access_service import repair_current_task
        repair_current_task(db, simulation)
        db.commit()
        db.refresh(simulation)
    
    current_scenario = None
    if simulation.current_scenario_id:
        current_scenario = db.get(Scenario, simulation.current_scenario_id)
    
    current_task = None
    if simulation.current_task_id:
        current_task = db.get(Task, simulation.current_task_id)
    
    # Count total and completed tasks
    total_tasks = 0
    completed_tasks = 0
    
    path_entries = db.execute(
        select(SimulationTaskPath).where(SimulationTaskPath.simulation_id == simulation.id)
    ).scalars().all()
    
    if path_entries:
        total_tasks = len(path_entries)
        completed_tasks = sum(1 for entry in path_entries if entry.task.status == TaskStatus.COMPLETED)
    elif simulation.scenarios:
        for scenario in simulation.scenarios:
            for scenario_task in scenario.tasks:
                total_tasks += 1
                if scenario_task.task.status == TaskStatus.COMPLETED:
                    completed_tasks += 1
    
    # Get active workflow event
    active_event = None
    if current_task:
        active_event = db.execute(
            select(WorkflowEvent)
            .where(
                WorkflowEvent.simulation_id == simulation_id,
                WorkflowEvent.task_id == current_task.id,
                WorkflowEvent.status.in_([WorkflowEventStatus.PENDING, WorkflowEventStatus.ACKNOWLEDGED])
            )
            .order_by(WorkflowEvent.occurred_at.desc())
        ).scalars().first()
    
    # Calculate time remaining if task is in progress
    remaining_time = None
    if current_task and current_task.started_at and current_task.deadline:
        now = datetime.now(timezone.utc)
        if current_task.deadline > now:
            remaining_seconds = (current_task.deadline - now).total_seconds()
            remaining_time = {
                "seconds": int(remaining_seconds),
                "minutes": int(remaining_seconds / 60),
                "hours": int(remaining_seconds / 3600),
                "formatted": _format_remaining_time(remaining_seconds)
            }
        else:
            remaining_time = {
                "seconds": 0,
                "minutes": 0,
                "hours": 0,
                "formatted": "Expired",
                "deadline_missed": True
            }
    
    return {
        "simulation_id": str(simulation.id),
        "title": simulation.title,
        "description": simulation.description,
        "role": simulation.role,
        "difficulty": simulation.difficulty.value,
        "duration_weeks": simulation.duration_weeks,
        "status": simulation.status.value,
        "progress": simulation.progress,
        "current_task_id": str(simulation.current_task_id) if simulation.current_task_id else None,
        "current_sprint_number": current_task.sprint.sprint_number if current_task and current_task.sprint else None,
        "started_at": simulation.started_at.isoformat() if simulation.started_at else None,
        "completed_at": simulation.completed_at.isoformat() if simulation.completed_at else None,
        "created_at": simulation.created_at.isoformat(),
        "current_scenario": {
            "id": str(current_scenario.id) if current_scenario else "",
            "title": current_scenario.title if current_scenario else "Simulation Context",
            "description": current_scenario.description if current_scenario else simulation.description,
            "workplace_context": current_scenario.workplace_context if current_scenario else (f"{simulation.project.title}" if simulation.project else "Simulation Context"),
            "role": current_scenario.role if current_scenario else simulation.role,
            "difficulty": current_scenario.difficulty.value if current_scenario and current_scenario.difficulty else simulation.difficulty.value,
            "required_skills": current_scenario.required_skills if current_scenario else (simulation.project.technology_stack if simulation.project else []),
            "objectives": current_scenario.objectives if current_scenario else [],
            "status": current_scenario.status.value if current_scenario else "active",
            "sequence": current_scenario.sequence if current_scenario else 1,
        } if current_scenario or current_task else None,
        "current_task": {
            "id": str(current_task.id),
            "title": current_task.title,
            "description": current_task.description,
            "instructions": current_task.description,
            "difficulty": current_task.difficulty.value if current_task.difficulty else simulation.difficulty.value,
            "task_type": current_task.task_type.value if current_task.task_type else "coding",
            "required_skills": simulation.project.technology_stack if simulation.project else [],
            "expected_output": criteria_view(db, current_task)["expected_output"],
            "criteria_version": criteria_view(db, current_task)["criteria_version"],
            "deadline": current_task.deadline.isoformat() if current_task.deadline else None,
            "status": current_task.status.value,
            "started_at": current_task.started_at.isoformat() if current_task.started_at else None,
            "remaining_time": remaining_time,
        } if current_task else None,
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "progress_percentage": (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0,
        "active_workplace_event": {
            "id": str(active_event.id),
            "event_type": active_event.event_type.value,
            "title": active_event.title,
            "message": active_event.message,
            "description": active_event.description,
            "occurred_at": active_event.occurred_at.isoformat(),
            "status": active_event.status,
            "action_required": active_event.action_required,
            "original_requirement": active_event.original_requirement,
            "updated_requirement": active_event.updated_requirement,
        } if active_event else None,
    }


@router.post("/{simulation_id}/start", status_code=status.HTTP_200_OK)
async def start_simulation(
    simulation_id: uuid.UUID,
    body: StartSimulationRequest = StartSimulationRequest(),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Start a simulation: generate first task dynamically, set current task, open workspace.

    Pass ``task_ids`` to use the legacy canonical task path mode.
    Omit task_ids to enable dynamic task generation based on baseline/competency.
    """
    simulation = _get_owned_simulation(simulation_id, db, user)

    if simulation.status != SimulationStatus.NOT_STARTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Simulation already {simulation.status.value}. Cannot start again."
        )

    # Change status to IN_PROGRESS
    simulation.status = SimulationStatus.IN_PROGRESS
    simulation.started_at = datetime.now(timezone.utc)
    simulation.progress = 0

    # Find the first scenario (by sequence) — lenient if missing
    first_scenario = db.execute(
        select(Scenario)
        .where(Scenario.simulation_id == simulation_id)
        .order_by(Scenario.sequence)
    ).scalars().first()
    
    if first_scenario:
        first_scenario.status = ScenarioStatus.ACTIVE
        simulation.current_scenario_id = first_scenario.id
        db.add(first_scenario)

    # DYNAMIC FIRST TASK GENERATION
    # If task_ids are explicitly provided, use the canonical path (legacy mode)
    # Otherwise, generate the first task dynamically based on baseline/competency
    if body.task_ids:
        # Legacy mode: use explicit task path
        from app.services.simulation.task_path_builder import build_task_path
        path_entries = build_task_path(
            db, simulation_id, simulation.project_id, task_ids=body.task_ids
        )
        if path_entries:
            from app.services.simulation.task_access_service import repair_current_task
            repair_current_task(db, simulation)
    else:
        # Dynamic mode: generate first task based on student's baseline and competency
        from app.services.generators.dynamic_task_generator import generate_dynamic_task
        from app.models.sprint import Sprint
        
        # Get the first sprint (Foundation & Setup)
        first_sprint = db.execute(
            select(Sprint)
            .where(Sprint.project_id == simulation.project_id)
            .order_by(Sprint.sprint_number)
        ).scalars().first()
        
        if first_sprint:
            # Generate first dynamic task (previous_task=None indicates first task)
            first_task = await generate_dynamic_task(
                db=db,
                simulation=simulation,
                sprint=first_sprint,
                student_id=user.student_id,
                previous_task=None  # First task has no predecessor
            )
            first_task.status = TaskStatus.TODO
            db.add(first_task)
            
            # Create path entry for the dynamic task
            from app.models.task_path import SimulationTaskPath
            path_entry = SimulationTaskPath(
                simulation_id=simulation.id,
                task_id=first_task.id,
                sequence=1,
                status=TaskStatus.TODO,
            )
            db.add(path_entry)
            
            # Set as current task
            simulation.current_task_id = first_task.id
            db.add(simulation)
            db.flush()

    if first_scenario and simulation.current_scenario_id != first_scenario.id:
        simulation.current_scenario_id = first_scenario.id

    db.add(simulation)
    db.flush()
    # Log activity
    from app.services.activity_log_service import log_activity
    log_activity(
        db,
        company_id=simulation.company_id,
        actor=user.student_id,
        action="simulation_started",
        detail=f"Started simulation '{simulation.title}'",
        simulation_id=str(simulation.id),
        scenario_id=str(first_scenario.id) if first_scenario else None,
        task_id=str(simulation.current_task_id) if simulation.current_task_id else None,
    )
    record_interaction(
        db,
        simulation,
        situation=simulation.description or simulation.title,
        student_response=None,
        decision="start",
        interaction_type="simulation_started",
        scenario_id=first_scenario.id if first_scenario else simulation.current_scenario_id,
        task_id=simulation.current_task_id,
        metadata={"title": simulation.title},
    )
    
    db.commit()
    db.refresh(simulation)

    # The manager greets the intern as the simulation begins. Gated by the
    # same pacing as everything else: first start gets the greeting, later
    # restarts fall back to task instructions instead of repeating it.
    try:
        from app.services import proactive_chat_service as proactive
        msg = proactive.maybe_send_proactive_message(
            db, simulation.company_id, channel="manager", trigger="simulation_started",
            event_id=f"simulation_started:{simulation.id}",
            task_id=simulation.current_task_id,
        )
        if msg is not None:
            await proactive.broadcast_message(simulation.company_id, msg, channel="manager")
    except Exception:
        import logging as _logging
        _logging.getLogger("aiwse.simulation").warning("Simulation-start chat greeting failed", exc_info=True)

    # Return full simulation state
    return get_simulation(simulation_id, db, user)


@router.get("/{simulation_id}/progress")
def get_simulation_progress(
    simulation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Get detailed progress information for a simulation."""
    simulation = _get_owned_simulation(simulation_id, db, user)
    
    # Calculate progress by scenario
    scenario_progress = []
    total_tasks = 0
    completed_tasks = 0
    
    # Prefer task path (student-specific subset) when available
    path_entries = db.execute(
        select(SimulationTaskPath).where(SimulationTaskPath.simulation_id == simulation.id)
    ).scalars().all()
    
    if path_entries:
        total_tasks = len(path_entries)
        completed_tasks = sum(1 for entry in path_entries if entry.task.status == TaskStatus.COMPLETED)
        
        for entry in path_entries:
            task = entry.task
            is_completed = task.status == TaskStatus.COMPLETED
            scenario_progress.append({
                "task_id": str(task.id),
                "title": task.title,
                "status": task.status.value,
                "sequence": entry.sequence,
                "completed": is_completed,
            })
    else:
        for scenario in simulation.scenarios:
            scenario_total = 0
            scenario_completed = 0
            task_details = []
            
            for scenario_task in scenario.tasks:
                scenario_total += 1
                total_tasks += 1
                task = scenario_task.task
                is_completed = task.status == TaskStatus.COMPLETED
                if is_completed:
                    scenario_completed += 1
                    completed_tasks += 1
                
                task_details.append({
                    "task_id": str(task.id),
                    "title": task.title,
                    "status": task.status.value,
                    "sequence": scenario_task.sequence,
                    "completed": is_completed,
                })
            
            scenario_progress.append({
                "scenario_id": str(scenario.id),
                "title": scenario.title,
                "status": scenario.status.value,
                "sequence": scenario.sequence,
                "total_tasks": scenario_total,
                "completed_tasks": scenario_completed,
                "progress_percentage": (scenario_completed / scenario_total * 100) if scenario_total > 0 else 0,
                "tasks": task_details,
            })
    
    overall_progress = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
    current_task = db.get(Task, simulation.current_task_id) if simulation.current_task_id else None
    
    return {
        "simulation_id": str(simulation.id),
        "status": simulation.status.value,
        "overall_progress": overall_progress,
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "remaining_tasks": total_tasks - completed_tasks,
        "current_scenario_id": str(simulation.current_scenario_id) if simulation.current_scenario_id else None,
        "current_task_id": str(simulation.current_task_id) if simulation.current_task_id else None,
        "current_sprint_number": current_task.sprint.sprint_number if current_task and current_task.sprint else None,
        "started_at": simulation.started_at.isoformat() if simulation.started_at else None,
        "completed_at": simulation.completed_at.isoformat() if simulation.completed_at else None,
        "scenarios": scenario_progress,
    }


@router.get("/{simulation_id}/task-path")
def get_task_path(
    simulation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Return the student's personalised ordered task path for this simulation."""
    simulation = _get_owned_simulation(simulation_id, db, user)

    from app.services.simulation.task_path_builder import get_task_path as _get_task_path

    entries = _get_task_path(db, simulation_id)
    return [
        {
            "sequence": entry.sequence,
            "task_id": str(entry.task_id),
            "status": entry.status.value,
            "title": entry.task.title,
            "sprint_id": str(entry.task.sprint_id),
            "estimated_hours": entry.task.estimated_hours,
            "priority": entry.task.priority.value,
        }
        for entry in entries
    ]


@router.get("/{simulation_id}/interactions")
def get_simulation_interactions(
    simulation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Return chronological interaction history for this simulation."""
    simulation = _get_owned_simulation(simulation_id, db, user)
    entries = list_interactions(db, simulation.id)
    return {
        "simulation_id": str(simulation.id),
        "student_id": simulation.student_id,
        "count": len(entries),
        "interactions": [serialize_interaction(entry) for entry in entries],
    }


def _sync_current_scenario_from_task(db: Session, simulation, task) -> None:
    """Update current_scenario_id if the task belongs to a different scenario."""
    from app.models.simulation import ScenarioTask as ST
    scenario_link = db.execute(
        select(ST)
        .join(Scenario, Scenario.id == ST.scenario_id)
        .where(ST.task_id == task.id, Scenario.simulation_id == simulation.id)
    ).scalar_one_or_none()
    if scenario_link and scenario_link.scenario_id != simulation.current_scenario_id:
        simulation.current_scenario_id = scenario_link.scenario_id


def _format_remaining_time(seconds: float) -> str:
    """Format remaining seconds into human-readable string."""
    if seconds <= 0:
        return "Expired"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"


@router.get("/{simulation_id}/scenario")
def get_current_scenario(
    simulation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Get the current active scenario with full details."""
    simulation = _get_owned_simulation(simulation_id, db, user)
    
    if not simulation.current_scenario_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active scenario for this simulation"
        )
    
    scenario = db.get(Scenario, simulation.current_scenario_id)
    if not scenario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Current scenario not found"
        )
    
    # Get current task
    current_task = None
    if simulation.current_task_id:
        current_task = db.get(Task, simulation.current_task_id)
    
    # Get all tasks for this scenario
    scenario_tasks = []
    for st in scenario.tasks:
        task = st.task
        is_completed = task.status == TaskStatus.COMPLETED
        scenario_tasks.append({
            "task_id": str(task.id),
            "title": task.title,
            "description": task.description,
            "task_type": task.task_type.value if task.task_type else "coding",
            "difficulty": scenario.difficulty.value,
            "status": task.status.value,
            "sequence": st.sequence,
            "deadline": task.deadline.isoformat() if task.deadline else None,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "is_current": task.id == simulation.current_task_id,
            "locked": not is_completed and task.id != simulation.current_task_id,
        })
    
    return {
        "scenario_id": str(scenario.id),
        "simulation_id": str(simulation.id),
        "title": scenario.title,
        "description": scenario.description,
        "workplace_context": scenario.workplace_context,
        "role": scenario.role,
        "difficulty": scenario.difficulty.value,
        "required_skills": scenario.required_skills,
        "objectives": scenario.objectives,
        "status": scenario.status.value,
        "sequence": scenario.sequence,
        "current_task": {
            "task_id": str(current_task.id),
            "title": current_task.title,
            "description": current_task.description,
            "instructions": current_task.description,
            "difficulty": scenario.difficulty.value,
            "task_type": current_task.task_type.value if current_task.task_type else "coding",
            "required_skills": scenario.required_skills,
            "expected_output": criteria_view(db, current_task)["expected_output"],
            "criteria_version": criteria_view(db, current_task)["criteria_version"],
            "deadline": current_task.deadline.isoformat() if current_task.deadline else None,
            "status": current_task.status.value,
            "started_at": current_task.started_at.isoformat() if current_task.started_at else None,
        } if current_task else None,
        "all_tasks": scenario_tasks,
        "total_tasks": len(scenario_tasks),
        "completed_tasks": sum(1 for t in scenario_tasks if t["status"] == "completed"),
    }


@router.get("/{simulation_id}/scenario/{scenario_id}")
def get_scenario_by_id(
    simulation_id: uuid.UUID,
    scenario_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Get a specific scenario by ID."""
    simulation = _get_owned_simulation(simulation_id, db, user)
    
    scenario = db.get(Scenario, scenario_id)
    if not scenario or scenario.simulation_id != simulation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scenario not found"
        )
    
    # Get all tasks for this scenario
    scenario_tasks = []
    for st in scenario.tasks:
        task = st.task
        scenario_tasks.append({
            "task_id": str(task.id),
            "title": task.title,
            "description": task.description,
            "task_type": task.task_type.value if task.task_type else "coding",
            "difficulty": scenario.difficulty.value,
            "status": task.status.value,
            "sequence": st.sequence,
            "deadline": task.deadline.isoformat() if task.deadline else None,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        })
    
    return {
        "scenario_id": str(scenario.id),
        "simulation_id": str(simulation.id),
        "title": scenario.title,
        "description": scenario.description,
        "workplace_context": scenario.workplace_context,
        "role": scenario.role,
        "difficulty": scenario.difficulty.value,
        "required_skills": scenario.required_skills,
        "objectives": scenario.objectives,
        "status": scenario.status.value,
        "sequence": scenario.sequence,
        "all_tasks": scenario_tasks,
        "total_tasks": len(scenario_tasks),
        "completed_tasks": sum(1 for t in scenario_tasks if t["status"] == "completed"),
    }


@router.get("/{simulation_id}/tasks")
def get_simulation_tasks(
    simulation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Get all tasks for a simulation grouped by scenario."""
    simulation = _get_owned_simulation(simulation_id, db, user)
    
    all_tasks = []
    current_task_id = simulation.current_task_id
    current_scenario_id = simulation.current_scenario_id
    
    for scenario in simulation.scenarios:
        scenario_is_active = scenario.id == current_scenario_id
        scenario_is_completed = scenario.status == ScenarioStatus.COMPLETED
        
        for scenario_task in scenario.tasks:
            task = scenario_task.task
            is_current = str(task.id) == str(current_task_id) if current_task_id else False
            is_completed = task.status == TaskStatus.COMPLETED
            is_future_scenario = not scenario_is_active and not scenario_is_completed
            
            locked = False
            if is_current:
                locked = False
            elif is_completed:
                locked = False
            elif is_future_scenario:
                locked = True
            elif scenario_is_active:
                locked = not is_current
            
            all_tasks.append({
                "task_id": str(task.id),
                "scenario_id": str(scenario.id),
                "scenario_title": scenario.title,
                "title": task.title,
                "description": task.description,
                "task_type": task.task_type.value if task.task_type else None,
                "priority": task.priority.value,
                "status": task.status.value,
                "sequence": scenario_task.sequence,
                "deadline": task.deadline.isoformat() if task.deadline else None,
                "started_at": task.started_at.isoformat() if task.started_at else None,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                "estimated_hours": task.estimated_hours,
                "acceptance_criteria": criteria_view(db, task)["acceptance_criteria"],
                "criteria_version": criteria_view(db, task)["criteria_version"],
                "is_current": is_current,
                "locked": locked,
            })
    
    return {
        "simulation_id": str(simulation.id),
        "total_tasks": len(all_tasks),
        "tasks": all_tasks,
    }


@router.get("/{simulation_id}/tasks/current")
async def get_current_task(
    simulation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Get the current active task with full details."""
    simulation = _get_owned_simulation(simulation_id, db, user)
    
    # Reuse the path eligibility service rather than selecting any unfinished
    # task (which could otherwise be a dependency-blocked or future-sprint task).
    from app.services.simulation.task_access_service import repair_current_task
    has_task_path = db.execute(
        select(SimulationTaskPath.id).where(SimulationTaskPath.simulation_id == simulation.id)
    ).first() is not None
    task = repair_current_task(db, simulation)
    if task:
        _sync_current_scenario_from_task(db, simulation, task)
        db.commit()
        db.refresh(simulation)
    else:
        if has_task_path:
            remaining = db.execute(
                select(SimulationTaskPath)
                .where(
                    SimulationTaskPath.simulation_id == simulation.id,
                    SimulationTaskPath.status != TaskStatus.COMPLETED,
                )
            ).scalars().all()
            if remaining:
                # There are still unfinished path tasks but none are currently unlocked.
                # This is a temporary dependency/sprint transition; the task will be
                # resolved by the regular progression service on the next refresh.
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No unlocked task is currently available for this simulation",
                )
            if simulation.status == SimulationStatus.IN_PROGRESS:
                simulation.status = SimulationStatus.COMPLETED
                simulation.completed_at = datetime.now(timezone.utc)
                simulation.progress = 100
                simulation.current_task_id = None
                db.add(simulation)
                record_interaction(
                    db,
                    simulation,
                    situation=simulation.title,
                    student_response=None,
                    decision="complete",
                    interaction_type="simulation_completed",
                    metadata={"source": "current_task_lookup"},
                )
                db.commit()
                db.refresh(simulation)
        if not has_task_path:
            # Fallback preserves legacy scenario-only simulations without paths.
            from app.services.simulation.progression_service import get_next_task_for_simulation
            next_task = await get_next_task_for_simulation(db, simulation)
            if next_task:
                task = next_task
                simulation.current_task_id = next_task.id
                db.add(simulation)
                db.flush()
            else:
                # No more tasks — check if simulation is complete
                if simulation.status == SimulationStatus.IN_PROGRESS:
                    simulation.status = SimulationStatus.COMPLETED
                    simulation.completed_at = datetime.now(timezone.utc)
                    simulation.progress = 100
                    db.add(simulation)
                    record_interaction(
                        db,
                        simulation,
                        situation=simulation.title,
                        student_response=None,
                        decision="complete",
                        interaction_type="simulation_completed",
                        metadata={"source": "current_task_lookup"},
                    )
                    db.flush()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No active task for this simulation"
                )

    # Get current scenario
    current_scenario = None
    if simulation.current_scenario_id:
        current_scenario = db.get(Scenario, simulation.current_scenario_id)
    
    # Calculate remaining time
    remaining_time = None
    deadline_missed = False
    if task.started_at and task.deadline:
        now = datetime.now(timezone.utc)
        if task.deadline > now:
            remaining_seconds = (task.deadline - now).total_seconds()
            remaining_time = {
                "seconds": int(remaining_seconds),
                "minutes": int(remaining_seconds / 60),
                "hours": int(remaining_seconds / 3600),
                "formatted": _format_remaining_time(remaining_seconds)
            }
        else:
            remaining_seconds = (now - task.deadline).total_seconds()
            remaining_time = {
                "seconds": 0,
                "minutes": 0,
                "hours": 0,
                "formatted": "Expired",
                "overdue_seconds": int(remaining_seconds),
                "overdue_formatted": _format_remaining_time(remaining_seconds)
            }
            deadline_missed = True
    
    # Get active workflow event for this task
    active_event = db.execute(
        select(WorkflowEvent)
        .where(
            WorkflowEvent.simulation_id == simulation_id,
            WorkflowEvent.task_id == task.id,
            WorkflowEvent.status.in_([WorkflowEventStatus.PENDING, WorkflowEventStatus.ACKNOWLEDGED])
        )
        .order_by(WorkflowEvent.occurred_at.desc())
    ).scalars().first()

    # Latest AI review feedback for resubmission guidance (None until a
    # submitted version has actually been reviewed). Read-only; the
    # frontend renders it in the Changes Requested section.
    from app.services.ai.review_service import get_task_review_feedback
    review_feedback = get_task_review_feedback(db, task)
    latest_version = db.execute(
        select(SubmissionVersion)
        .join(Submission, Submission.id == SubmissionVersion.submission_id)
        .where(Submission.task_id == task.id)
        .order_by(SubmissionVersion.version_number.desc())
        .limit(1)
    ).scalars().first()
    from app.models.submission import AIReview
    latest_review = None
    if latest_version is not None:
        latest_review = db.execute(
            select(AIReview)
            .where(AIReview.version_id == latest_version.id)
            .order_by(AIReview.created_at.desc())
            .limit(1)
        ).scalars().first()

    return {
        "task_id": str(task.id),
        "simulation_id": str(simulation.id),
        "scenario_id": str(current_scenario.id) if current_scenario else None,
        "scenario_title": current_scenario.title if current_scenario else None,
        "title": task.title,
        "description": task.description,
        "instructions": task.description,
        "difficulty": task.difficulty.value if task.difficulty else (current_scenario.difficulty.value if current_scenario else "intermediate"),
        "task_type": task.task_type.value if task.task_type else "coding",
        "required_skills": current_scenario.required_skills if current_scenario else [],
        "expected_output": criteria_view(db, task)["expected_output"],
        "criteria_version": criteria_view(db, task)["criteria_version"],
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "status": task.status.value,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "estimated_hours": task.estimated_hours,
        "remaining_time": remaining_time,
        "deadline_missed": deadline_missed,
        "review_feedback": review_feedback,
        "review_version_id": str(latest_version.id) if latest_version else None,
        "review_status": latest_review.status.value if latest_review else ("not_started" if latest_version else None),
        "review_error": (latest_review.raw_payload or {}).get("message") if latest_review and latest_review.status.value == "failed" else None,
        "active_workplace_event": {
            "id": str(active_event.id),
            "event_type": active_event.event_type.value,
            "title": active_event.title,
            "message": active_event.message,
            "description": active_event.description,
            "occurred_at": active_event.occurred_at.isoformat(),
            "status": active_event.status,
            "action_required": active_event.action_required,
            "original_requirement": active_event.original_requirement,
            "updated_requirement": active_event.updated_requirement,
        } if active_event else None,
    }


def _check_task_unlocked(
    db: Session,
    simulation: Simulation,
    task_id: uuid.UUID,
    *,
    allow_resubmission: bool = False,
) -> None:
    """Enforce current-task-only access. Raises 403 if task is locked."""
    from app.services.simulation.task_access_service import require_current_task_access
    require_current_task_access(
        db, simulation, task_id, allow_resubmission=allow_resubmission
    )


@router.post("/{simulation_id}/tasks/{task_id}/start", status_code=status.HTTP_200_OK)
async def start_simulation_task(
    simulation_id: uuid.UUID,
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Start a simulation task: change state to STARTED, record started_at, calculate deadline."""
    simulation = _get_owned_simulation(simulation_id, db, user)
    
    _check_task_unlocked(db, simulation, task_id, allow_resubmission=True)
    
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )

    if task.status in {
        TaskStatus.SUBMITTED,
        TaskStatus.UNDER_REVIEW,
        TaskStatus.MANAGER_APPROVAL,
        TaskStatus.COMPLETED,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Task is locked. Only the current unlocked task can be accessed.",
        )

    # CHANGES_REQUESTED tasks may be restarted for rework (CHANGES_REQUESTED -> IN_PROGRESS).
    if task.status not in (TaskStatus.TODO, TaskStatus.CHANGES_REQUESTED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot start task from '{task.status.value}'. Task must be in TODO state.",
        )

    from app.services.engines.task_state_machine import InvalidTransitionError, validate_transition
    try:
        validate_transition(task.status, TaskStatus.IN_PROGRESS)
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    
    # Check dependencies
    from app.api.routes.tasks import _check_dependencies
    _check_dependencies(db, task)
    
    # Calculate deadline based on estimated hours (or use existing deadline)
    now = datetime.now(timezone.utc)
    if not task.deadline:
        # Default deadline: estimated_hours from now, minimum 30 minutes
        import math
        deadline_hours = max(task.estimated_hours, 0.5)
        from datetime import timedelta
        task.deadline = now + timedelta(hours=deadline_hours)
    
    # Transition: TODO -> IN_PROGRESS (which maps to STARTED -> IN_PROGRESS in simulation state machine)
    task.status = TaskStatus.IN_PROGRESS
    if task.started_at is None:
        task.started_at = now
    
    # Update simulation progress
    simulation.current_task_id = task.id
    
    db.add(task)
    db.add(simulation)
    db.flush()
    
    # Log activity
    log_activity(
        db,
        company_id=simulation.company_id,
        actor=user.student_id,
        action="simulation_task_started",
        detail=f"Started task '{task.title}' in simulation '{simulation.title}'",
        simulation_id=str(simulation.id),
        task_id=str(task.id),
        started_at=now.isoformat(),
        deadline=task.deadline.isoformat(),
    )
    record_interaction(
        db,
        simulation,
        situation=task.description or task.title,
        student_response=None,
        decision="start_task",
        interaction_type="task_started",
        task_id=task.id,
        metadata={"task_title": task.title},
    )
    
    # Trigger TASK_STARTED event. A requirement change is not part of start.
    from app.services.engines.events_engine import trigger_event
    from app.models.enums import EventType
    project = db.get(Project, simulation.project_id)
    if project:
        await trigger_event(
            db,
            project,
            EventType.TASK_STARTED,
            f"Task '{task.title}' started",
            {"task_id": str(task.id), "task_title": task.title},
        )

    existing_checkin = db.execute(
        select(WorkflowEvent.id).where(
            WorkflowEvent.simulation_id == simulation.id,
            WorkflowEvent.task_id == task.id,
            WorkflowEvent.trigger_condition == "task_started",
        )
    ).first()
    if existing_checkin is None:
        await trigger_workflow_event(
            db,
            simulation_id=simulation.id,
            event_type=WorkflowEventType.STAKEHOLDER_MESSAGE,
            title="Manager check-in",
            message=(
                f"Please start '{task.title}' and send a short note on your approach "
                "before you submit the work."
            ),
            task_id=task.id,
            description="Manager message for the task that was just started.",
            trigger_condition="task_started",
            action_required=True,
            metadata={"source": "task_start"},
        )
    
    db.commit()
    db.refresh(task)
    db.refresh(simulation)
    
    # Check and trigger workflow events (deadline warning, etc.)
    current_scenario = None
    if simulation.current_scenario_id:
        current_scenario = db.get(Scenario, simulation.current_scenario_id)
    
    from app.services.engines import check_and_trigger_workflow_events
    await check_and_trigger_workflow_events(db, simulation, task, current_scenario)

    # A teammate reacts to the kickoff so the workplace feels alive. Forced
    # (a user action deserves one reaction) but never breaks the start flow.
    try:
        from app.services import proactive_chat_service as proactive
        msg = proactive.maybe_send_proactive_message(
            db, simulation.company_id, channel="team_member", trigger="task_started",
            event_id=f"task_started:{simulation.id}:{task.id}",
            task_id=task.id,
        )
        if msg is not None:
            await proactive.broadcast_message(simulation.company_id, msg, channel="team")
    except Exception:
        import logging as _logging
        _logging.getLogger("aiwse.simulation").warning("Task-start chat reaction failed", exc_info=True)

    # Return updated task with time tracking info
    remaining_time = None
    if task.started_at and task.deadline:
        remaining_seconds = (task.deadline - now).total_seconds()
        remaining_time = {
            "seconds": int(remaining_seconds),
            "minutes": int(remaining_seconds / 60),
            "hours": int(remaining_seconds / 3600),
            "formatted": _format_remaining_time(remaining_seconds)
        }
    
    return {
        "task_id": str(task.id),
        "simulation_id": str(simulation.id),
        "scenario_id": str(current_scenario.id) if current_scenario else None,
        "title": task.title,
        "description": task.description,
        "instructions": task.description,
        "difficulty": task.difficulty.value if task.difficulty else (current_scenario.difficulty.value if current_scenario else "intermediate"),
        "task_type": task.task_type.value if task.task_type else "coding",
        "required_skills": current_scenario.required_skills if current_scenario else [],
        "expected_output": criteria_view(db, task)["expected_output"],
        "criteria_version": criteria_view(db, task)["criteria_version"],
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "status": task.status.value,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "estimated_hours": task.estimated_hours,
        "remaining_time": remaining_time,
        "message": "Task started successfully. Timer is running."
    }


@router.post("/{simulation_id}/tasks/{task_id}/submit", status_code=status.HTTP_201_CREATED)
async def submit_simulation_task(
    simulation_id: uuid.UUID,
    task_id: uuid.UUID,
    files: List[UploadFile] = File(default=[]),
    metadata: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Submit a simulation task for evaluation - captures evidence for Member 3."""
    simulation = _get_owned_simulation(simulation_id, db, user)
    
    _check_task_unlocked(db, simulation, task_id, allow_resubmission=True)
    
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )

    if task.status in {
        TaskStatus.SUBMITTED,
        TaskStatus.UNDER_REVIEW,
        TaskStatus.MANAGER_APPROVAL,
        TaskStatus.COMPLETED,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Task is locked. Only the current unlocked task can be accessed.",
        )

    # Resubmission after review: restart rework through the same legal
    # CHANGES_REQUESTED -> IN_PROGRESS transition as the start endpoint, so
    # the current task can be revised and resubmitted with no new task and
    # no workflow change. Everything below runs the normal submit flow.
    if task.status == TaskStatus.CHANGES_REQUESTED:
        from app.services.engines.task_state_machine import InvalidTransitionError, validate_transition
        try:
            validate_transition(task.status, TaskStatus.IN_PROGRESS)
        except InvalidTransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        task.status = TaskStatus.IN_PROGRESS
        db.add(task)
        db.flush()

    # Validate task state - must be IN_PROGRESS to submit
    if task.status != TaskStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot submit task from '{task.status.value}'. Task must be IN_PROGRESS."
        )
    
    if not task.started_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task was not started. Cannot submit without start time."
        )
    
    now = datetime.now(timezone.utc)
    submitted_at = now
    
    # Calculate time taken
    time_taken_seconds = (submitted_at - task.started_at).total_seconds()
    time_taken_minutes = int(time_taken_seconds / 60)
    
    # Check if on time
    on_time = True
    deadline_missed = False
    if task.deadline and submitted_at > task.deadline:
        on_time = False
        deadline_missed = True
    
    # Parse metadata
    import json
    meta_dict = {}
    if metadata:
        try:
            meta_dict = json.loads(metadata)
        except json.JSONDecodeError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid metadata JSON")
    
    # Prepare file data for storage service
    file_data = []
    for upload_file in files:
        content = await upload_file.read()
        import io
        file_data.append({
            "file": io.BytesIO(content),
            "filename": upload_file.filename,
            "content_type": upload_file.content_type,
        })
    
    # Get project for submission service
    project = db.get(Project, simulation.project_id)
    
    # Store submission using existing submission service
    service = SubmissionService()
    try:
        result = service.store_submission(
            db=db,
            project=project,
            task=task,
            files=file_data,
            metadata=meta_dict,
        )
    except (FileValidationError, ValueError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    
    if not task.deliverable_url and result.get("submission_id"):
        task.deliverable_url = f"submission://{result['submission_id']}"
    db.add(task)

    # The submission is now under evaluation: IN_PROGRESS -> SUBMITTED (done
    # by SubmissionService above) -> UNDER_REVIEW here. The task is NOT
    # completed, current_task_id does NOT move, and the next task stays
    # locked — completion and progression happen only through the complete
    # endpoint after an approved review. The version/submission rows stay
    # SUBMITTED, which is the state the review endpoint expects.
    from app.services.engines.task_state_machine import InvalidTransitionError as _SubmitTransitionError
    from app.services.engines.task_state_machine import validate_transition as _validate_submit_transition
    try:
        _validate_submit_transition(task.status, TaskStatus.UNDER_REVIEW)
    except _SubmitTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    task.status = TaskStatus.UNDER_REVIEW
    db.add(task)

    path_entry = db.execute(
        select(SimulationTaskPath).where(
            SimulationTaskPath.simulation_id == simulation_id,
            SimulationTaskPath.task_id == task_id,
        )
    ).scalar_one_or_none()
    if path_entry:
        path_entry.status = TaskStatus.UNDER_REVIEW
        db.add(path_entry)

    simulation.progress = _calculate_simulation_progress(db, simulation)
    simulation.current_task_id = task.id
    db.add(simulation)

    db.flush()
    
    # Collect workflow events encountered during this task
    from app.services.workplace_scenarios import assess_requirement_changes_for_submission
    requirement_change_assessments = assess_requirement_changes_for_submission(
        db, simulation, task, user.student_id
    )
    from app.services.engines import get_task_workflow_events
    workflow_events = await get_task_workflow_events(db, simulation_id, task_id)
    
    events_encountered = []
    requirement_changes_encountered = []
    event_responses = []
    
    for event in workflow_events:
        event_data = {
            "event_id": str(event.id),
            "event_type": event.event_type.value,
            "title": event.title,
            "message": event.message,
            "occurred_at": event.occurred_at.isoformat(),
            "status": event.status,
            "responded": event.response is not None,
            "response": event.response,
            "responded_at": event.responded_at.isoformat() if event.responded_at else None,
        }
        events_encountered.append(event_data)
        
        if event.event_type == WorkflowEventType.REQUIREMENT_CHANGE:
            requirement_changes_encountered.append({
                "original_requirement": event.original_requirement,
                "updated_requirement": event.updated_requirement,
                "trigger_condition": event.trigger_condition,
            })
        
        if event.response:
            event_responses.append({
                "event_id": str(event.id),
                "event_type": event.event_type.value,
                "response": event.response,
                "responded_at": event.responded_at.isoformat() if event.responded_at else None,
            })
    
    # Log activity
    log_activity(
        db,
        company_id=simulation.company_id,
        actor=user.student_id,
        action="simulation_task_submitted",
        detail=f"Submitted task '{task.title}' for evaluation",
        simulation_id=str(simulation.id),
        task_id=str(task.id),
        submission_id=result.get("submission_id"),
        version_id=result.get("version_id"),
        time_taken_minutes=time_taken_minutes,
        on_time=on_time,
        deadline_missed=deadline_missed,
    )
    student_response = meta_dict.get("response")
    if student_response is None and meta_dict:
        student_response = json.dumps(meta_dict)
    stored_files = result.get("stored_files") or []
    file_names = [f.get("filename") or f.get("original_filename") for f in stored_files if isinstance(f, dict)]
    if not student_response and file_names:
        student_response = ", ".join(name for name in file_names if name)
    record_interaction(
        db,
        simulation,
        situation=task.description or task.title,
        student_response=student_response,
        decision="submit",
        interaction_type="task_submitted",
        task_id=task.id,
        metadata={
            "submission_id": result.get("submission_id"),
            "version_id": result.get("version_id"),
            "on_time": on_time,
        },
    )
    
    # Trigger SUBMISSION_CREATED event
    from app.services.engines.events_engine import trigger_event
    from app.models.enums import EventType
    if project:
        await trigger_event(
            db,
            project,
            EventType.SUBMISSION_CREATED,
            f"Submission created for task '{task.title}'",
            {"task_id": str(task.id), "submission_id": result.get("submission_id"), "version_id": result.get("version_id")},
        )
        await trigger_event(
            db,
            project,
            EventType.SUBMISSION_VERSION_CREATED,
            f"Submission version {result.get('version')} created for task '{task.title}'",
            {"task_id": str(task.id), "version_id": result.get("version_id"), "version": result.get("version")},
        )
        if result.get("version", 1) > 1:
            # Resubmission after review feedback: announce it as its own event so
            # the student sees a new attempt (and a notification) instead of a
            # silent second submission row.
            await trigger_event(
                db,
                project,
                EventType.SUBMISSION_RESUBMITTED,
                f"Task '{task.title}' resubmitted as version {result.get('version')}",
                {
                    "task_id": str(task.id),
                    "version_id": result.get("version_id"),
                    "new_version": result.get("version"),
                },
            )
    
    db.commit()
    db.refresh(task)
    db.refresh(simulation)

    # The manager acknowledges the submission in chat — visible progress that
    # persists for the student's next visit.
    try:
        from app.services import proactive_chat_service as proactive
        msg = proactive.maybe_send_proactive_message(
            db, simulation.company_id, channel="manager", trigger="task_submitted",
            event_id=f"task_submitted:{simulation.id}:{result.get('version_id') or result.get('submission_id')}",
            task_id=task.id,
        )
        if msg is not None:
            await proactive.broadcast_message(simulation.company_id, msg, channel="manager")
    except Exception:
        import logging as _logging
        _logging.getLogger("aiwse.simulation").warning("Task-submit chat reaction failed", exc_info=True)

    # Build evaluation-ready evidence for Member 3
    current_scenario = None
    if simulation.current_scenario_id:
        current_scenario = db.get(Scenario, simulation.current_scenario_id)
    
    evaluation_evidence = {
        "submission_id": result.get("submission_id"),
        "version_id": result.get("version_id"),
        "task_id": str(task.id),
        "simulation_id": str(simulation.id),
        "user_id": user.student_id,
        "response": meta_dict.get("response", ""),
        "files": result.get("stored_files", []),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "submitted_at": submitted_at.isoformat(),
        "time_taken_minutes": time_taken_minutes,
        "time_taken_seconds": int(time_taken_seconds),
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "on_time": on_time,
        "deadline_missed": deadline_missed,
        "attempt_number": result.get("version", 1),
        "task_version": result.get("version", 1),
        "events_encountered": events_encountered,
        "requirement_changes_encountered": requirement_changes_encountered,
        "event_responses": event_responses,
        "task_difficulty": task.difficulty.value if task.difficulty else (current_scenario.difficulty.value if current_scenario else "intermediate"),
        "task_type": task.task_type.value if task.task_type else "coding",
        "required_skills": current_scenario.required_skills if current_scenario else [],
        "acceptance_criteria": criteria_view(db, task)["acceptance_criteria"],
        "acceptance_criteria_version": criteria_identity(db, task),
        "task_state_history": [
            {"status": "TODO", "timestamp": task.started_at.isoformat() if task.started_at else None},
            {"status": "IN_PROGRESS", "timestamp": task.started_at.isoformat() if task.started_at else None},
            {"status": "SUBMITTED", "timestamp": submitted_at.isoformat()},
        ],
    }

    return {
        "status": "submitted",
        "task_id": str(task.id),
        "submission_id": result.get("submission_id"),
        "version_id": result.get("version_id"),
        "submitted_at": submitted_at.isoformat(),
        "time_taken_minutes": time_taken_minutes,
        "on_time": on_time,
        "deadline_missed": deadline_missed,
        "requirement_change_assessments": requirement_change_assessments,
        "progress": simulation.progress,
        "current_scenario": {
            "id": str(current_scenario.id),
            "title": current_scenario.title,
            "status": current_scenario.status.value,
        } if current_scenario else None,
        "message": "Task submitted successfully. Awaiting evaluation/review.",
    }


class WorkplaceActionRequest(BaseModel):
    action: str = Field(default="adapted_work", min_length=1, max_length=80)
    detail: str = Field(..., min_length=1, max_length=4000)


def _owned_workflow_event(
    db: Session,
    simulation: Simulation,
    task_id: uuid.UUID,
    event_id: uuid.UUID,
) -> tuple[Task, WorkflowEvent]:
    task = db.get(Task, task_id)
    event = db.get(WorkflowEvent, event_id)
    if task is None or event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    if event.simulation_id != simulation.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Event does not belong to this simulation")
    if event.task_id != task_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Event does not belong to this task")
    return task, event


@router.post("/{simulation_id}/tasks/{task_id}/events/{event_id}/acknowledge", status_code=status.HTTP_200_OK)
async def acknowledge_simulation_event(
    simulation_id: uuid.UUID,
    task_id: uuid.UUID,
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Student confirms they saw a requirement change. The manager then writes."""
    simulation = _get_owned_simulation(simulation_id, db, user)
    task, event = _owned_workflow_event(db, simulation, task_id, event_id)
    from app.services.workplace_scenarios import WorkplaceScenarioError, acknowledge_requirement_change
    try:
        payload = acknowledge_requirement_change(db, simulation, task, event, user.student_id)
    except WorkplaceScenarioError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    db.commit()
    return payload


@router.post("/{simulation_id}/tasks/{task_id}/events/{event_id}/actions", status_code=status.HTTP_200_OK)
async def record_simulation_event_action(
    simulation_id: uuid.UUID,
    task_id: uuid.UUID,
    event_id: uuid.UUID,
    payload: WorkplaceActionRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Record work the student did after replying to a requirement change."""
    simulation = _get_owned_simulation(simulation_id, db, user)
    task, event = _owned_workflow_event(db, simulation, task_id, event_id)
    if payload.action != "adapted_work":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submit an explicit adapted_work action for a requirement change.",
        )
    from app.services.workplace_scenarios import WorkplaceScenarioError, record_subsequent_action
    try:
        result = record_subsequent_action(
            db, simulation, task, event, user.student_id, payload.action, payload.detail,
        )
    except WorkplaceScenarioError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    db.commit()
    return result


@router.post("/{simulation_id}/tasks/{task_id}/events/{event_id}/respond", status_code=status.HTTP_200_OK)
async def respond_to_simulation_event(
    simulation_id: uuid.UUID,
    task_id: uuid.UUID,
    event_id: uuid.UUID,
    response: str = Form(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    ai_service: AIService = Depends(get_ai_service),
):
    """Respond to a workflow event (requirement change, deadline warning, etc.)."""
    simulation = _get_owned_simulation(simulation_id, db, user)
    
    event = db.get(WorkflowEvent, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    
    if event.simulation_id != simulation_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Event does not belong to this simulation")
    
    if event.task_id != task_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Event does not belong to this task")

    if event.event_type == WorkflowEventType.REQUIREMENT_CHANGE:
        task = db.get(Task, task_id)
        from app.services.workplace_scenarios import WorkplaceScenarioError, record_requirement_reply
        try:
            result = record_requirement_reply(db, simulation, task, event, user.student_id, response)
        except WorkplaceScenarioError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        # Requirement-change replies share the persisted manager chat stream.
        # Keep the response id on the event so retries/reconnects never create
        # a second manager response for the same event.
        metadata = dict(event.metadata_json or {})
        manager_response_id = metadata.get("manager_response_message_id")
        manager_response = db.get(Message, uuid.UUID(manager_response_id)) if manager_response_id else None
        if manager_response is None:
            from app.models.manager import Manager
            from app.models.project import Project
            from app.models.project_state import ProjectState
            from app.services.manager_communication_service import create_manager_response

            manager = db.execute(select(Manager).where(Manager.company_id == simulation.company_id)).scalar_one()
            project = db.get(Project, simulation.project_id)
            state = db.execute(select(ProjectState).where(ProjectState.project_id == project.id)).scalar_one_or_none()
            history = db.execute(
                select(Message).where(
                    Message.company_id == simulation.company_id,
                    Message.sender_type.in_([MessageSenderType.STUDENT, MessageSenderType.MANAGER]),
                ).order_by(Message.created_at, Message.id)
            ).scalars().all()
            version_number = metadata.get("criteria_version_number", "unknown")
            context = (
                f"Task: {task.title}. Requirement change event: {event.id}. "
                f"Criteria version: {version_number}. Original requirement: {event.original_requirement}. "
                f"Updated requirement: {event.updated_requirement}. Student response: {response}"
            )
            manager_response = await create_manager_response(
                db, ai_service, manager, project, state, history, response, context
            )
            metadata["manager_response_message_id"] = str(manager_response.id)
            metadata["manager_response_criteria_version_number"] = version_number
            event.metadata_json = metadata
            db.add(event)
            db.flush()
            result["manager_response"] = {
                "id": str(manager_response.id), "sender_type": "manager",
                "sender_id": str(manager.id), "content": manager_response.content,
                "created_at": manager_response.created_at.isoformat() if manager_response.created_at else None,
            }
        else:
            result["manager_response"] = {
                "id": str(manager_response.id), "sender_type": "manager",
                "sender_id": manager_response.sender_id, "content": manager_response.content,
                "created_at": manager_response.created_at.isoformat() if manager_response.created_at else None,
            }
        record_interaction(
            db,
            simulation,
            situation=event.message or event.title,
            student_response=response,
            decision="reply_requirement_change",
            interaction_type="requirement_change_reply",
            scenario_id=event.scenario_id,
            task_id=task.id,
            metadata={"event_id": str(event.id), "event_type": "requirement_change"},
        )
        db.commit()
        from app.websockets.manager import manager as ws_manager
        student_message_id = metadata.get("student_message_id")
        for message in (db.get(Message, uuid.UUID(student_message_id)) if student_message_id else None, manager_response):
            if message is not None:
                await ws_manager.broadcast(str(simulation.company_id), "chat_message", {
                    "channel": "manager", "id": str(message.id),
                    "sender_type": message.sender_type.value, "sender_id": message.sender_id,
                    "content": message.content, "created_at": message.created_at.isoformat(),
                })
        await ws_manager.broadcast(str(simulation.company_id), "event", {"event_id": str(event.id)})
        return result
    
    # Record response
    from app.services.engines import respond_to_workflow_event
    updated_event = await respond_to_workflow_event(db, event_id, response, user.student_id)
    record_interaction(
        db,
        simulation,
        situation=updated_event.message or updated_event.title,
        student_response=response,
        decision="respond_event",
        interaction_type="event_responded",
        scenario_id=updated_event.scenario_id,
        task_id=updated_event.task_id or task_id,
        metadata={
            "event_id": str(updated_event.id),
            "event_type": updated_event.event_type.value,
        },
    )
    
    if (
        updated_event.event_type == WorkflowEventType.STAKEHOLDER_MESSAGE
        and updated_event.trigger_condition == "task_started"
        and updated_event.task_id is not None
    ):
        work_task = db.get(Task, updated_event.task_id)
        if work_task is not None and work_task.status == TaskStatus.IN_PROGRESS:
            from app.services.workplace_scenarios import announce_requirement_change, issue_requirement_change
            created = issue_requirement_change(
                db,
                simulation,
                work_task,
                trigger_activity="approach_note",
                scenario=db.get(Scenario, updated_event.scenario_id) if updated_event.scenario_id else None,
            )
            await announce_requirement_change(db, simulation, created)

    db.commit()
    db.refresh(updated_event)
    
    return {
        "event_id": str(updated_event.id),
        "status": updated_event.status,
        "response": updated_event.response,
        "responded_at": updated_event.responded_at.isoformat() if updated_event.responded_at else None,
        "message": "Response recorded successfully"
    }


@router.get("/{simulation_id}/tasks/{task_id}/events")
def get_task_events(
    simulation_id: uuid.UUID,
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Get all workflow events encountered during a task."""
    simulation = _get_owned_simulation(simulation_id, db, user)
    
    events = db.execute(
        select(WorkflowEvent)
        .where(
            WorkflowEvent.simulation_id == simulation_id,
            WorkflowEvent.task_id == task_id,
        )
        .order_by(WorkflowEvent.occurred_at)
    ).scalars().all()
    task = db.get(Task, task_id)
    from app.services.workplace_scenarios import serialize_workflow_event

    return {
        "task_id": str(task_id),
        "simulation_id": str(simulation_id),
        "events": [serialize_workflow_event(db, event, task) for event in events],
    }


def _calculate_simulation_progress(db: Session, simulation: Simulation) -> int:
    """Calculate overall simulation progress percentage.
    
    When task paths exist (student-specific task subsets), count from those.
    Otherwise fall back to canonical scenario tasks.
    """
    path_entries = db.execute(
        select(SimulationTaskPath).where(SimulationTaskPath.simulation_id == simulation.id)
    ).scalars().all()

    if path_entries:
        total_tasks = len(path_entries)
        completed_tasks = sum(1 for entry in path_entries if entry.task.status == TaskStatus.COMPLETED)
    else:
        total_tasks = 0
        completed_tasks = 0
        if simulation.scenarios:
            for scenario in simulation.scenarios:
                for scenario_task in scenario.tasks:
                    total_tasks += 1
                    if scenario_task.task.status == TaskStatus.COMPLETED:
                        completed_tasks += 1

    return int((completed_tasks / total_tasks * 100)) if total_tasks > 0 else 0


@router.post("/{simulation_id}/tasks/{task_id}/complete", status_code=status.HTTP_200_OK)
async def complete_simulation_task(
    simulation_id: uuid.UUID,
    task_id: uuid.UUID,
    approved: bool = True,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Complete a simulation task (called after evaluation).
    This triggers automatic progression if approved, or rework if not.
    """
    simulation = _get_owned_simulation(simulation_id, db, user)
    
    _check_task_unlocked(db, simulation, task_id)
    
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    
    if task.status == TaskStatus.COMPLETED:
        return {
            "status": "already_completed",
            "task_id": str(task.id),
            "message": "Task was already completed",
        }
    
    if task.status != TaskStatus.MANAGER_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot complete task from '{task.status.value}'. Manager approval is required."
        )

    if not approved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A task cannot be completed without manager approval.",
        )

    from app.services.engines.task_state_machine import validate_transition

    previous_status = task.status
    validate_transition(task.status, TaskStatus.COMPLETED)
    task.status = TaskStatus.COMPLETED
    task.completed_at = datetime.now(timezone.utc)

    # Mark task path entry as completed only after manager approval.
    path_entry = db.execute(
        select(SimulationTaskPath).where(
            SimulationTaskPath.simulation_id == simulation_id,
            SimulationTaskPath.task_id == task_id,
        )
    ).scalar_one_or_none()
    if path_entry:
        path_entry.status = TaskStatus.COMPLETED
        path_entry.completed_at = task.completed_at
        db.add(path_entry)
    db.add(task)

    # Update simulation progress
    simulation.progress = _calculate_simulation_progress(db, simulation)
    db.add(simulation)
    db.flush()
    
    # Log activity
    log_activity(
        db,
        company_id=simulation.company_id,
        actor=user.student_id,
        action="simulation_task_completed",
        detail=f"Completed task '{task.title}' in simulation '{simulation.title}'",
        simulation_id=str(simulation.id),
        task_id=str(task.id),
        previous_status=previous_status.value,
        new_status=task.status.value,
    )
    evaluation_decision = "completed"
    record_interaction(
        db,
        simulation,
        situation=task.description or task.title,
        student_response=None,
        decision=evaluation_decision,
        interaction_type="task_evaluated",
        task_id=task.id,
        metadata={
            "approved": approved,
            "previous_status": previous_status.value,
            "new_status": task.status.value,
        },
    )
    
    next_task = None
    # Completion events and progression belong only to manager-approved work.
    from app.services.engines.events_engine import trigger_event
    from app.models.enums import EventType
    project = db.get(Project, simulation.project_id)
    if project:
        await trigger_event(
            db,
            project,
            EventType.TASK_COMPLETED,
            f"Task '{task.title}' completed",
            {"task_id": str(task.id), "task_title": task.title},
        )

    from app.services.simulation.progression_service import progress_to_next_task
    next_task = await progress_to_next_task(db, simulation, task, user.student_id)
    if simulation.status == SimulationStatus.COMPLETED:
        record_interaction(
            db,
            simulation,
            situation=simulation.title,
            student_response=None,
            decision="complete",
            interaction_type="simulation_completed",
            task_id=task.id,
        )
    
    db.commit()
    db.refresh(simulation)

    # The manager reacts to the verdict in chat (praise or rework steer).
    try:
        from app.services import proactive_chat_service as proactive
        msg = proactive.maybe_send_proactive_message(
            db,
            simulation.company_id,
            channel="manager",
            trigger="task_completed" if approved else "task_rework",
            event_id=f"task_verdict:{simulation.id}:{task.id}:{task.updated_at.isoformat() if task.updated_at else task.status.value}",
            task_id=task.id,
        )
        if msg is not None:
            await proactive.broadcast_message(simulation.company_id, msg, channel="manager")
    except Exception:
        import logging as _logging
        _logging.getLogger("aiwse.simulation").warning("Task-complete chat reaction failed", exc_info=True)

    # Check if simulation is complete
    if simulation.status == SimulationStatus.COMPLETED:
        return {
            "status": "simulation_completed",
            "simulation_id": str(simulation.id),
            "completed_at": simulation.completed_at.isoformat() if simulation.completed_at else None,
            "message": "Simulation completed successfully! All scenarios and tasks are done.",
        }
    
    # Return next task info
    next_task_info = None
    if next_task:
        db.refresh(next_task)
        remaining_time = None
        if next_task.deadline:
            now = datetime.now(timezone.utc)
            if next_task.deadline > now:
                remaining_seconds = (next_task.deadline - now).total_seconds()
                remaining_time = {
                    "seconds": int(remaining_seconds),
                    "minutes": int(remaining_seconds / 60),
                    "hours": int(remaining_seconds / 3600),
                    "formatted": _format_remaining_time(remaining_seconds)
                }
        
        next_task_info = {
            "task_id": str(next_task.id),
            "title": next_task.title,
            "description": next_task.description,
            "instructions": next_task.description,
            "difficulty": next_task.difficulty.value if next_task.difficulty else simulation.difficulty.value,
            "task_type": next_task.task_type.value if next_task.task_type else "coding",
            "required_skills": simulation.project.technology_stack if simulation.project else [],
            "expected_output": criteria_view(db, next_task)["expected_output"],
            "criteria_version": criteria_view(db, next_task)["criteria_version"],
            "deadline": next_task.deadline.isoformat() if next_task.deadline else None,
            "status": next_task.status.value,
            "estimated_hours": next_task.estimated_hours,
            "remaining_time": remaining_time,
        }
    
    return {
        "status": "task_completed",
        "simulation_id": str(simulation.id),
        "completed_task_id": str(task.id),
        "progress": simulation.progress,
        "next_task": next_task_info,
        "current_scenario": {
            "id": str(next_task.sprint_id) if next_task and next_task.sprint_id else "",
            "title": next_task.sprint.name if next_task and next_task.sprint else "Simulation Context",
            "status": "active",
        } if next_task else None,
        "message": "Task completed. Progressed to next task." if next_task else "All tasks in scenario completed. Moving to next scenario.",
    }
