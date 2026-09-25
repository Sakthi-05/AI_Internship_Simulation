"""Eligibility rules for a student's ordered simulation task path.

The canonical task state machine remains the source of task lifecycle state.
This module only determines which path task is presently eligible to be worked
on, so every simulation endpoint applies the same ordering and dependency rule.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import SimulationStatus, SprintStatus, TaskStatus
from app.models.simulation import Simulation
from app.models.sprint import Sprint
from app.models.task import Task, TaskDependency
from app.models.task_path import SimulationTaskPath


def get_next_available_path_entry(
    db: Session, simulation_id: UUID
) -> Optional[SimulationTaskPath]:
    """Return the first path entry that is unfinished and currently unlocked.

    The path is ordered by sprint number and per-sprint sequence, so the first
    incomplete task whose dependencies are satisfied is the valid next task for
    the student. Later sprint entries remain blocked until the current sprint is
    complete.
    """
    entries = db.execute(
        select(SimulationTaskPath)
        .join(Task, Task.id == SimulationTaskPath.task_id)
        .join(Sprint, Sprint.id == Task.sprint_id)
        .where(SimulationTaskPath.simulation_id == simulation_id)
        .order_by(Sprint.sprint_number, SimulationTaskPath.sequence)
    ).scalars().all()

    for entry in entries:
        if entry.task.status == TaskStatus.COMPLETED:
            continue
        if not _dependencies_completed(db, entry.task_id):
            continue
        return entry

    return None


def repair_current_task(db: Session, simulation: Simulation) -> Optional[Task]:
    """Set a missing or stale current task to the valid path task, if any."""
    if simulation.current_task_id:
        current_task = db.get(Task, simulation.current_task_id)
        if current_task and current_task.status != TaskStatus.COMPLETED:
            if _dependencies_completed(db, current_task.id):
                if current_task.status == TaskStatus.BACKLOG:
                    current_task.status = TaskStatus.TODO
                    db.add(current_task)
                return current_task

    candidate = get_next_available_path_entry(db, simulation.id)
    if candidate is None:
        return None

    task = candidate.task
    if simulation.current_task_id != task.id:
        simulation.current_task_id = task.id
        db.add(simulation)
    if task.status == TaskStatus.BACKLOG:
        task.status = TaskStatus.TODO
        db.add(task)
    if candidate.status == TaskStatus.BACKLOG:
        candidate.status = TaskStatus.TODO
        db.add(candidate)
    if task.sprint and task.sprint.status != SprintStatus.COMPLETED:
        task.sprint.status = SprintStatus.ACTIVE
        db.add(task.sprint)
    return task


def is_current_unlocked_task(
    db: Session, simulation: Simulation, task_id: UUID
) -> bool:
    """Whether task_id is the sole current, eligible path task."""
    candidate = get_next_available_path_entry(db, simulation.id)
    return bool(
        candidate
        and candidate.task_id == task_id
        and simulation.current_task_id == task_id
    )


def require_current_task_access(
    db: Session, simulation: Simulation, task_id: UUID
) -> None:
    """Reject attempts to work on a locked/non-path task."""
    if not is_current_unlocked_task(db, simulation, task_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Task is locked. Only the current unlocked task can be accessed.",
        )


def require_current_task_for_active_project(
    db: Session, student_id: str, project_id: UUID, task_id: UUID
) -> None:
    """Protect generic project/task APIs when a simulation is in progress."""
    simulation = db.execute(
        select(Simulation).where(
            Simulation.student_id == student_id,
            Simulation.project_id == project_id,
            Simulation.status == SimulationStatus.IN_PROGRESS,
        )
    ).scalars().first()
    if simulation:
        require_current_task_access(db, simulation, task_id)


def _dependencies_completed(db: Session, task_id: UUID) -> bool:
    dependency_ids = db.execute(
        select(TaskDependency.depends_on_task_id).where(TaskDependency.task_id == task_id)
    ).scalars().all()
    if not dependency_ids:
        return True
    incomplete = db.execute(
        select(Task.id).where(
            Task.id.in_(dependency_ids), Task.status != TaskStatus.COMPLETED
        )
    ).first()
    return incomplete is None
