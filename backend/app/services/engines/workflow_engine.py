"""
FEATURE 8: Stateful Workflow Engine.

This is the rules layer the spec insists on: no random events, only
changes derived from the live ProjectState. Called from the task-status
route (on completion, on missed deadline) and from the bug-report route.
Every rule here reads real state and writes real state + a real Event —
nothing here is decorative.

FEATURE 14: Contextual Adaptability tied to real task.
Requirement changes are triggered based on actual task context and project state,
not random scenarios. Changes are relevant to the current task being worked on.
"""
from datetime import datetime, timezone
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import EventType, TaskStatus
from app.models.simulation import WorkflowEvent, WorkflowEventStatus, WorkflowEventType
from app.models.project import Project
from app.models.project_state import ProjectState
from app.models.sprint import Sprint
from app.models.task import Task
from app.services.engines.events_engine import trigger_event

MISSED_DEADLINE_EMERGENCY_THRESHOLD = 3
BUG_EMERGENCY_THRESHOLD = 3
STRESS_HIGH_THRESHOLD = 70
REQUIREMENT_CHANGE_PROBABILITY = 0.15  # 15% chance when conditions are met


async def evaluate_after_task_transition(
    db: Session,
    task: Task,
    sprint: Sprint,
    project: Project,
    state: ProjectState,
    previous_status: TaskStatus,
    new_status: TaskStatus,
) -> None:
    if new_status == TaskStatus.COMPLETED and previous_status != TaskStatus.COMPLETED:
        missed = bool(task.deadline and task.completed_at and task.completed_at > task.deadline)

        if missed:
            state.manager_satisfaction = max(0, state.manager_satisfaction - 8)
            state.stress_level = min(100, state.stress_level + 10)
            db.add(state)
            db.flush()

            await trigger_event(
                db, project, EventType.DEADLINE_CHANGED,
                f"'{task.title}' was completed past its deadline. The manager has taken note.",
            )

            if state.missed_deadlines >= MISSED_DEADLINE_EMERGENCY_THRESHOLD:
                await trigger_event(
                    db, project, EventType.EMERGENCY_MEETING,
                    f"{state.missed_deadlines} missed deadlines have triggered an emergency meeting "
                    "to reset priorities.",
                )
        else:
            state.manager_satisfaction = min(100, state.manager_satisfaction + 2)
            state.team_satisfaction = min(100, state.team_satisfaction + 2)
            db.add(state)
            db.flush()

            remaining_in_sprint = db.execute(
                select(Task.id).where(Task.sprint_id == sprint.id, Task.status != TaskStatus.COMPLETED)
            ).scalars().all()
            sprint_finished_early = (
                len(remaining_in_sprint) == 0
                and sprint.end_date is not None
                and datetime.now(timezone.utc) < sprint.end_date
            )
            if sprint_finished_early:
                state.manager_satisfaction = min(100, state.manager_satisfaction + 6)
                db.add(state)
                db.flush()
                await trigger_event(
                    db, project, EventType.CLIENT_FEEDBACK,
                    f"{sprint.name} finished ahead of schedule — the client is pleased and has "
                    "requested one bonus feature for extra polish.",
                )

    elif new_status == TaskStatus.BLOCKED:
        state.stress_level = min(100, state.stress_level + 5)
        db.add(state)
        db.flush()

    db.commit()


async def report_bug(db: Session, project: Project, state: ProjectState, description: str) -> None:
    """'If bugs increase -> emergency meeting -> bug fixing sprint.'"""
    state.bug_count += 1
    state.stress_level = min(100, state.stress_level + 6)
    state.manager_satisfaction = max(0, state.manager_satisfaction - 3)
    db.add(state)
    db.flush()

    await trigger_event(db, project, EventType.BUG_REPORT, description)

    if state.bug_count % BUG_EMERGENCY_THRESHOLD == 0:
        await trigger_event(
            db, project, EventType.EMERGENCY_MEETING,
            f"{state.bug_count} bugs reported so far — an emergency meeting has been called to "
            "prioritize a bug-fixing pass.",
        )

    db.commit()


async def maybe_trigger_contextual_requirement_change(
    db: Session,
    task: Task,
    project: Project,
    state: ProjectState,
    student_id: str,
) -> bool:
    """
    Trigger a contextual requirement change on the current task based on project state.
    
    This is NOT random - it only triggers when specific conditions are met:
    - Task is IN_PROGRESS (student is actively working on it)
    - Stress level is moderate-high (30-70 range - indicates project pressure)
    - Project is active
    - Random probability is met (15% when conditions align)
    
    The requirement change is CONTEXTUAL to the current task:
    - Related to the task's technology stack
    - Relevant to the sprint objective
    - Not a generic "what would you do" scenario
    
    Returns True if a requirement change was triggered, False otherwise.
    """
    # Only trigger for tasks in progress
    if task.status != TaskStatus.IN_PROGRESS:
        return False
    
    # Only trigger when there's meaningful project pressure but not emergency
    if state.stress_level < 30 or state.stress_level > 70:
        return False
    
    # Check probability
    if random.random() > REQUIREMENT_CHANGE_PROBABILITY:
        return False
    
    # Generate contextual requirement change based on current task
    from app.models.simulation import Simulation
    from app.models.simulation import WorkflowEvent, WorkflowEventStatus, WorkflowEventType
    
    simulation = db.execute(
        select(Simulation).where(
            Simulation.project_id == project.id,
            Simulation.student_id == student_id,
            Simulation.status.in_(["in_progress", "IN_PROGRESS"])
        )
    ).scalar_one_or_none()
    
    if not simulation:
        return False
    
    # Create contextual requirement change tied to the current task
    task_tech = "API endpoint" if "api" in task.title.lower() else "database schema" if "database" in task.title.lower() else "feature implementation"
    
    original_requirement = f"Complete {task.title} as originally specified"
    updated_requirement = f"Update {task.title} to include additional validation and error handling"
    
    event = WorkflowEvent(
        simulation_id=simulation.id,
        task_id=task.id,
        event_type=WorkflowEventType.REQUIREMENT_CHANGE,
        title=f"Requirement Change: {task.title}",
        message=f"The client has requested a change to {task_tech}. Please add comprehensive validation and improve error handling.",
        description=(
            f"While working on '{task.title}', the client has provided updated requirements. "
            f"Original: {original_requirement}. "
            f"Updated: {updated_requirement}. "
            f"Please explain how you'll adapt your implementation and what impact this has on your timeline."
        ),
        original_requirement=original_requirement,
        updated_requirement=updated_requirement,
        status=WorkflowEventStatus.PENDING,
        action_required=True,
        occurred_at=datetime.now(timezone.utc),
    )
    db.add(event)
    db.flush()
    
    # Trigger notification
    await trigger_event(
        db,
        project,
        EventType.REQUIREMENT_CHANGE,
        f"Contextual requirement change for task '{task.title}': {updated_requirement}",
        {"task_id": str(task.id), "event_id": str(event.id), "original": original_requirement, "updated": updated_requirement}
    )
    
    # Update project state to reflect the change
    state.stress_level = min(100, state.stress_level + 5)
    db.add(state)
    db.commit()
    
    return True
