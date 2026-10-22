"""
Workflow Events Engine - deterministic event triggering for simulations.

Handles:
- REQUIREMENT_CHANGE events
- DEADLINE_WARNING events
- Event history and responses
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import WorkflowEventType, TaskStatus
from app.models.simulation import Simulation, Scenario, WorkflowEvent
from app.models.sprint import Sprint
from app.models.task import Task
from app.models.task_path import SimulationTaskPath
from app.services.engines.simulation_task_state_machine import SimulationTaskState


REQUIREMENT_CHANGE_TRIGGER_STAGE = 0.3  # Trigger at 30% of task duration
DEADLINE_WARNING_THRESHOLD_MINUTES = 5  # Warn 5 minutes before deadline


async def trigger_requirement_change_event(
    db: Session,
    simulation: Simulation,
    task: Task,
    scenario: Scenario,
    original_requirement: str,
    updated_requirement: str,
    trigger_condition: str = "task_progress",
) -> WorkflowEvent:
    """Create a REQUIREMENT_CHANGE workflow event."""
    event = WorkflowEvent(
        simulation_id=simulation.id,
        task_id=task.id,
        scenario_id=scenario.id if scenario else None,
        event_type=WorkflowEventType.REQUIREMENT_CHANGE,
        title="Requirement Change",
        message="The requirements for this task have been updated.",
        description=(
            f"Original requirement: {original_requirement}\n\n"
            f"Updated requirement: {updated_requirement}"
        ),
        original_requirement=original_requirement,
        updated_requirement=updated_requirement,
        trigger_condition=trigger_condition,
        action_required=True,
        occurred_at=datetime.now(timezone.utc),
        status="pending",
        metadata_json={
            "trigger_stage": REQUIREMENT_CHANGE_TRIGGER_STAGE,
            "task_type": task.task_type.value if task.task_type else None,
        },
    )
    db.add(event)
    db.flush()
    
    # Log activity
    from app.services.activity_log_service import log_activity
    log_activity(
        db,
        company_id=simulation.company_id,
        actor="system",
        action="requirement_change_event",
        detail=f"Requirement change event triggered for task '{task.title}'",
        simulation_id=str(simulation.id),
        task_id=str(task.id),
        event_id=str(event.id),
    )
    
    return event


async def trigger_deadline_warning_event(
    db: Session,
    simulation: Simulation,
    task: Task,
    scenario: Scenario,
    remaining_minutes: int,
) -> WorkflowEvent:
    """Create a DEADLINE_WARNING workflow event."""
    event = WorkflowEvent(
        simulation_id=simulation.id,
        task_id=task.id,
        scenario_id=scenario.id if scenario else None,
        event_type=WorkflowEventType.DEADLINE_WARNING,
        title="Deadline Warning",
        message=f"You have {remaining_minutes} minutes remaining to complete this task. Please submit your work before the deadline.",
        description=(
            f"Task: {task.title}\n"
            f"Deadline: {task.deadline.isoformat() if task.deadline else 'Not set'}\n"
            f"Time remaining: {remaining_minutes} minutes"
        ),
        trigger_condition=f"deadline_approaching_{remaining_minutes}_minutes",
        action_required=False,
        occurred_at=datetime.now(timezone.utc),
        status="pending",
        metadata_json={
            "remaining_minutes": remaining_minutes,
            "threshold_minutes": DEADLINE_WARNING_THRESHOLD_MINUTES,
        },
    )
    db.add(event)
    db.flush()
    
    # Log activity
    from app.services.activity_log_service import log_activity
    log_activity(
        db,
        company_id=simulation.company_id,
        actor="system",
        action="deadline_warning_event",
        detail=f"Deadline warning event triggered for task '{task.title}' ({remaining_minutes} minutes remaining)",
        simulation_id=str(simulation.id),
        task_id=str(task.id),
        event_id=str(event.id),
    )
    
    return event


async def trigger_stakeholder_message_event(
    db: Session,
    simulation: Simulation,
    task: Optional[Task],
    scenario: Optional[Scenario],
    message: str,
    title: str = "Stakeholder Update",
    trigger_condition: str = "stakeholder_message",
    action_required: bool = False,
    metadata: Optional[dict] = None,
) -> WorkflowEvent:
    """Create a STAKEHOLDER_MESSAGE workflow event with optional task/scenario context."""
    return await trigger_workflow_event(
        db,
        simulation_id=simulation.id,
        event_type=WorkflowEventType.STAKEHOLDER_MESSAGE,
        title=title,
        message=message,
        task_id=task.id if task else None,
        scenario_id=scenario.id if scenario else None,
        description=message,
        trigger_condition=trigger_condition,
        action_required=action_required,
        metadata=metadata or {},
    )


async def trigger_priority_change_event(
    db: Session,
    simulation: Simulation,
    task: Optional[Task],
    scenario: Optional[Scenario],
    old_priority: Optional[str],
    new_priority: str,
    reason: str,
    trigger_condition: str = "priority_change",
) -> WorkflowEvent:
    """Create a PRIORITY_CHANGE workflow event when the priority of a task changes."""
    return await trigger_workflow_event(
        db,
        simulation_id=simulation.id,
        event_type=WorkflowEventType.PRIORITY_CHANGE,
        title="Priority Change",
        message=f"Task priority changed from {old_priority or 'unspecified'} to {new_priority}.",
        task_id=task.id if task else None,
        scenario_id=scenario.id if scenario else None,
        description=reason,
        trigger_condition=trigger_condition,
        action_required=True,
        metadata={
            "old_priority": old_priority,
            "new_priority": new_priority,
            "reason": reason,
        },
    )


async def trigger_bug_report_event(
    db: Session,
    simulation: Simulation,
    task: Optional[Task],
    scenario: Optional[Scenario],
    summary: str,
    severity: str = "medium",
    trigger_condition: str = "bug_reported",
) -> WorkflowEvent:
    """Create a BUG_REPORT workflow event."""
    return await trigger_workflow_event(
        db,
        simulation_id=simulation.id,
        event_type=WorkflowEventType.BUG_REPORT,
        title="Bug Report",
        message=summary,
        task_id=task.id if task else None,
        scenario_id=scenario.id if scenario else None,
        description=summary,
        trigger_condition=trigger_condition,
        action_required=True,
        metadata={
            "severity": severity,
            "issue_summary": summary,
        },
    )


async def trigger_sprint_review_event(
    db: Session,
    simulation: Simulation,
    sprint: Sprint,
    completed_tasks: int,
    total_tasks: int,
    task: Optional[Task] = None,
    scenario: Optional[Scenario] = None,
) -> WorkflowEvent:
    """Create a stakeholder update summarising the sprint review once all assigned tasks are complete."""
    review_summary = (
        f"Sprint {sprint.sprint_number} is complete: {completed_tasks}/{total_tasks} tasks finished. "
        "The team can move on to the next sprint and capture the lessons learned."
    )
    return await trigger_workflow_event(
        db,
        simulation_id=simulation.id,
        event_type=WorkflowEventType.STAKEHOLDER_MESSAGE,
        title=f"Sprint {sprint.sprint_number} Review",
        message=review_summary,
        task_id=task.id if task else None,
        scenario_id=scenario.id if scenario else None,
        description=review_summary,
        trigger_condition="sprint_completed",
        action_required=False,
        metadata={
            "sprint_id": str(sprint.id),
            "sprint_number": sprint.sprint_number,
            "completed_tasks": completed_tasks,
            "total_tasks": total_tasks,
        },
    )


async def check_and_trigger_workflow_events(
    db: Session,
    simulation: Simulation,
    task: Task,
    scenario: Optional[Scenario] = None,
) -> list[WorkflowEvent]:
    """Check conditions and trigger appropriate workflow events deterministically."""
    triggered_events = []

    if task and task.started_at and task.deadline:
        now = datetime.now(timezone.utc)
        if task.deadline > now:
            remaining_seconds = (task.deadline - now).total_seconds()
            remaining_minutes = int(remaining_seconds / 60)
            if remaining_minutes <= DEADLINE_WARNING_THRESHOLD_MINUTES:
                existing_warning = db.execute(
                    select(WorkflowEvent)
                    .where(
                        WorkflowEvent.simulation_id == simulation.id,
                        WorkflowEvent.task_id == task.id,
                        WorkflowEvent.event_type == WorkflowEventType.DEADLINE_WARNING,
                        WorkflowEvent.metadata_json["remaining_minutes"].astext.cast(int) == remaining_minutes,
                    )
                ).scalar_one_or_none()

                if not existing_warning:
                    event = await trigger_deadline_warning_event(db, simulation, task, scenario, remaining_minutes)
                    triggered_events.append(event)

    # Requirement changes are issued by workplace_scenarios when the student
    # starts or is working the task. Progress percentage and the clock do not
    # create a second, generic change.

    sprint = db.get(Sprint, task.sprint_id) if task and task.sprint_id else None
    if sprint is not None:
        unfinished = db.execute(
            select(SimulationTaskPath.id)
            .join(Task, Task.id == SimulationTaskPath.task_id)
            .where(
                SimulationTaskPath.simulation_id == simulation.id,
                Task.sprint_id == sprint.id,
                Task.status != TaskStatus.COMPLETED,
            )
        ).first()

        if unfinished is None:
            sprint_review_exists = db.execute(
                select(WorkflowEvent.id)
                .where(
                    WorkflowEvent.simulation_id == simulation.id,
                    WorkflowEvent.event_type == WorkflowEventType.STAKEHOLDER_MESSAGE,
                    WorkflowEvent.trigger_condition == "sprint_completed",
                    WorkflowEvent.metadata_json["sprint_id"].astext == str(sprint.id),
                )
            ).first()

            if sprint_review_exists is None:
                completed_count = db.execute(
                    select(func.count(Task.id))
                    .where(Task.sprint_id == sprint.id, Task.status == TaskStatus.COMPLETED)
                ).scalar_one() or 0
                total_count = db.execute(
                    select(func.count(Task.id))
                    .where(Task.sprint_id == sprint.id)
                ).scalar_one() or 0
                event = await trigger_sprint_review_event(
                    db,
                    simulation,
                    sprint,
                    completed_tasks=completed_count,
                    total_tasks=total_count,
                    task=task,
                    scenario=scenario,
                )
                triggered_events.append(event)

    return triggered_events


async def trigger_workflow_event(
    db: Session,
    simulation_id: UUID,
    event_type: WorkflowEventType,
    title: str,
    message: str,
    task_id: Optional[UUID] = None,
    scenario_id: Optional[UUID] = None,
    description: Optional[str] = None,
    original_requirement: Optional[str] = None,
    updated_requirement: Optional[str] = None,
    trigger_condition: Optional[str] = None,
    action_required: bool = False,
    metadata: Optional[dict] = None,
) -> WorkflowEvent:
    """Generic function to trigger a workflow event."""
    simulation = db.get(Simulation, simulation_id)
    if not simulation:
        raise ValueError("Simulation not found")
    
    event = WorkflowEvent(
        simulation_id=simulation_id,
        task_id=task_id,
        scenario_id=scenario_id,
        event_type=event_type,
        title=title,
        message=message,
        description=description,
        original_requirement=original_requirement,
        updated_requirement=updated_requirement,
        trigger_condition=trigger_condition,
        action_required=action_required,
        occurred_at=datetime.now(timezone.utc),
        status="pending",
        metadata_json=metadata or {},
    )
    db.add(event)
    db.flush()
    
    # Log activity
    from app.services.activity_log_service import log_activity
    log_activity(
        db,
        company_id=simulation.company_id,
        actor="system",
        action=f"workflow_event_{event_type.value.lower()}",
        detail=f"{title} event triggered: {message}",
        simulation_id=str(simulation_id),
        task_id=str(task_id) if task_id else None,
        event_id=str(event.id),
    )
    
    return event


async def respond_to_workflow_event(
    db: Session,
    event_id: UUID,
    response: str,
    user_id: str,
) -> WorkflowEvent:
    """Record a student's response to a workflow event."""
    event = db.get(WorkflowEvent, event_id)
    if not event:
        raise ValueError("Workflow event not found")
    
    event.response = response
    event.responded_at = datetime.now(timezone.utc)
    event.status = "acknowledged"
    event.metadata_json = event.metadata_json or {}
    event.metadata_json["responded_by"] = user_id
    
    db.add(event)
    db.flush()
    
    # Log activity
    simulation = db.get(Simulation, event.simulation_id)
    if simulation:
        from app.services.activity_log_service import log_activity
        log_activity(
            db,
            company_id=simulation.company_id,
            actor=user_id,
            action="workflow_event_response",
            detail=f"Responded to {event.event_type.value} event: {response}",
            simulation_id=str(event.simulation_id),
            task_id=str(event.task_id) if event.task_id else None,
            event_id=str(event.id),
        )
    
    return event


async def get_task_workflow_events(
    db: Session,
    simulation_id: UUID,
    task_id: UUID,
) -> list[WorkflowEvent]:
    """Get all workflow events for a specific task."""
    return db.execute(
        select(WorkflowEvent)
        .where(
            WorkflowEvent.simulation_id == simulation_id,
            WorkflowEvent.task_id == task_id,
        )
        .order_by(WorkflowEvent.occurred_at)
    ).scalars().all()


async def get_simulation_workflow_events(
    db: Session,
    simulation_id: UUID,
) -> list[WorkflowEvent]:
    """Get all workflow events for a simulation."""
    return db.execute(
        select(WorkflowEvent)
        .where(WorkflowEvent.simulation_id == simulation_id)
        .order_by(WorkflowEvent.occurred_at)
    ).scalars().all()