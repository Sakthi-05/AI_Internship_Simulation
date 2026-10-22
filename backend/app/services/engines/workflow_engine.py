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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import EventType, SimulationStatus, TaskStatus
from app.models.project import Project
from app.models.project_state import ProjectState
from app.models.sprint import Sprint
from app.models.task import Task
from app.services.engines.events_engine import trigger_event

MISSED_DEADLINE_EMERGENCY_THRESHOLD = 3
BUG_EMERGENCY_THRESHOLD = 3
STRESS_HIGH_THRESHOLD = 70


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
    """Issue one requirement change when the student is actually working the task.

    The change is tied to this task, updates its acceptance criteria, and is
    skipped when one already exists. Elapsed time alone never creates it.
    """
    if task.status != TaskStatus.IN_PROGRESS:
        return False

    from app.models.simulation import Simulation
    from app.services.workplace_scenarios import issue_requirement_change

    simulation = db.execute(
        select(Simulation).where(
            Simulation.project_id == project.id,
            Simulation.student_id == student_id,
            Simulation.status == SimulationStatus.IN_PROGRESS,
        )
    ).scalars().first()
    if not simulation:
        return False

    event = issue_requirement_change(
        db,
        simulation,
        task,
        trigger_activity="task_in_progress",
    )
    if event is None:
        return False

    await trigger_event(
        db,
        project,
        EventType.REQUIREMENT_CHANGE,
        f"Contextual requirement change for task '{task.title}': {event.updated_requirement}",
        {
            "task_id": str(task.id),
            "event_id": str(event.id),
            "original": event.original_requirement,
            "updated": event.updated_requirement,
        },
    )
    if state is not None:
        state.stress_level = min(100, (state.stress_level or 0) + 5)
        db.add(state)
    db.commit()
    return True
