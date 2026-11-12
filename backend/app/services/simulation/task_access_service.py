"""Eligibility rules for a student's ordered simulation task path.

The canonical task state machine remains the source of task lifecycle state.
This module only determines which path task is presently eligible to be worked
on, so every simulation endpoint applies the same ordering and dependency rule.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased, contains_eager, joinedload

from app.models.enums import ReviewResult, SimulationStatus, SprintStatus, TaskStatus
from app.models.simulation import Simulation
from app.models.sprint import Sprint
from app.models.submission import AIReview, Submission, SubmissionVersion
from app.models.task import Task, TaskDependency
from app.models.task_path import SimulationTaskPath

logger = logging.getLogger(__name__)


def get_next_available_path_entry(
    db: Session, simulation_id: UUID, *, validate_graph: bool = True
) -> Optional[SimulationTaskPath]:
    """Return the first path entry that is unfinished and currently unlocked.

    The path is ordered by sprint number and per-sprint sequence, so the first
    incomplete task whose dependencies are satisfied is the valid next task for
    the student. Later sprint entries remain blocked until the current sprint is
    complete.
    """
    simulation = db.get(Simulation, simulation_id) if validate_graph else None
    if simulation is not None:
        _ensure_project_dependency_graph(db, simulation.project_id)
    prerequisite = aliased(Task)
    unmet_prerequisite = exists(
        select(1)
        .select_from(TaskDependency)
        .join(prerequisite, prerequisite.id == TaskDependency.depends_on_task_id)
        .where(
            TaskDependency.task_id == Task.id,
            prerequisite.status != TaskStatus.COMPLETED,
        )
    )
    return db.execute(
        select(SimulationTaskPath)
        .join(Task, Task.id == SimulationTaskPath.task_id)
        .join(Sprint, Sprint.id == Task.sprint_id)
        .options(contains_eager(SimulationTaskPath.task).contains_eager(Task.sprint))
        .where(
            SimulationTaskPath.simulation_id == simulation_id,
            Task.status != TaskStatus.COMPLETED,
            ~unmet_prerequisite,
        )
        .order_by(Sprint.sprint_number, SimulationTaskPath.sequence)
        .limit(1)
    ).scalars().first()


def sync_path_entry_statuses(db: Session, simulation: Simulation) -> int:
    """Heal diverged path-entry statuses from the tasks' actual states.

    Repairs rows that an older code path marked COMPLETED while the task
    itself still needs work (or vice versa): without this, the remaining-work
    check treats a revision-pending task as permanently finished, the
    simulation can be closed early, and the task reads as locked. Runs on
    every current-task resolution, i.e. on refresh and login. Returns the
    number of entries repaired.
    """
    entries = db.execute(
        select(SimulationTaskPath)
        .options(joinedload(SimulationTaskPath.task))
        .where(SimulationTaskPath.simulation_id == simulation.id)
    ).scalars().all()
    repaired = 0
    for entry in entries:
        task_status = entry.task.status if entry.task else None
        if task_status is None or entry.status == task_status:
            continue
        entry.status = task_status
        if task_status == TaskStatus.COMPLETED:
            entry.completed_at = entry.completed_at or entry.task.completed_at
        else:
            entry.completed_at = None
        db.add(entry)
        repaired += 1
    if repaired:
        db.info["simulation_read_repair_changed"] = True
        db.flush()
    return repaired


def heal_bogus_completion(db: Session, simulation: Simulation) -> Optional[Task]:
    """Reopen the current task if it was marked COMPLETED without approval.

    Legitimate completion always leaves an APPROVED AI review on the latest
    submitted version. Without one, the COMPLETED flag could only come from a
    bypass — and it strands the student (locked task, lost progress). The
    task is reopened to the state its latest review implies (UNDER_REVIEW if
    still awaiting verdict, else CHANGES_REQUESTED) and kept current, so the
    revision loop can proceed. Runs on refresh/login via repair_current_task.
    """
    if not simulation.current_task_id:
        return None
    task = db.get(Task, simulation.current_task_id)
    if task is None or task.status != TaskStatus.COMPLETED:
        return None

    version = db.execute(
        select(SubmissionVersion)
        .join(Submission, Submission.id == SubmissionVersion.submission_id)
        .where(
            Submission.task_id == task.id,
            SubmissionVersion.submitted_at.isnot(None),
        )
        .order_by(SubmissionVersion.submitted_at.desc(), SubmissionVersion.version_number.desc())
    ).scalars().first()
    if version is None:
        return None
    review = db.execute(
        select(AIReview)
        .where(AIReview.version_id == version.id)
        .order_by(AIReview.created_at.desc())
    ).scalars().first()
    if review is not None and review.result == ReviewResult.APPROVED:
        return None

    if review is not None and review.result in (ReviewResult.CHANGES_REQUIRED, ReviewResult.REJECTED):
        target = TaskStatus.CHANGES_REQUESTED
    else:
        target = TaskStatus.UNDER_REVIEW
    # Direct assignment (not a state-machine transition): COMPLETED is
    # terminal, but this flag itself is the corruption being repaired. The
    # restored state is a legal workflow state the normal flow accepts.
    logger.warning(
        "Reopening task %s (simulation %s) wrongly marked completed; restoring %s",
        task.id, simulation.id, target.value,
    )
    task.status = target
    task.completed_at = None
    db.add(task)
    db.info["simulation_read_repair_changed"] = True
    db.flush()
    return task


def repair_current_task(db: Session, simulation: Simulation) -> Optional[Task]:
    """Set a missing or stale current task to the valid path task, if any."""
    _ensure_project_dependency_graph(db, simulation.project_id)
    heal_bogus_completion(db, simulation)
    sync_path_entry_statuses(db, simulation)
    if simulation.current_task_id:
        current_task = db.get(Task, simulation.current_task_id)
        if current_task and current_task.status != TaskStatus.COMPLETED:
            if _dependencies_completed(db, current_task.id):
                if current_task.status == TaskStatus.BACKLOG:
                    current_task.status = TaskStatus.TODO
                    db.add(current_task)
                    db.info["simulation_read_repair_changed"] = True
                return current_task

    candidate = get_next_available_path_entry(db, simulation.id, validate_graph=False)
    if candidate is None:
        return None

    task = candidate.task
    if simulation.current_task_id != task.id:
        simulation.current_task_id = task.id
        db.add(simulation)
        db.info["simulation_read_repair_changed"] = True
    if task.status == TaskStatus.BACKLOG:
        task.status = TaskStatus.TODO
        db.add(task)
        db.info["simulation_read_repair_changed"] = True
    if candidate.status == TaskStatus.BACKLOG:
        candidate.status = TaskStatus.TODO
        db.add(candidate)
        db.info["simulation_read_repair_changed"] = True
    if task.sprint and task.sprint.status != SprintStatus.COMPLETED:
        if task.sprint.status != SprintStatus.ACTIVE:
            db.info["simulation_read_repair_changed"] = True
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
    db: Session,
    simulation: Simulation,
    task_id: UUID,
    *,
    allow_resubmission: bool = False,
) -> None:
    """Reject locked tasks, with a narrow exception for resubmitting rework."""
    task = db.get(Task, task_id)
    if task is not None and task.status == TaskStatus.CHANGES_REQUESTED:
        if allow_resubmission and simulation.current_task_id == task_id:
            is_in_simulation_path = db.execute(
                select(SimulationTaskPath.id).where(
                    SimulationTaskPath.simulation_id == simulation.id,
                    SimulationTaskPath.task_id == task_id,
                )
            ).first()
            if (
                is_in_simulation_path is not None
                and _dependencies_completed(db, task_id)
            ):
                return

    elif is_current_unlocked_task(db, simulation, task_id):
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Task is locked. Only the current unlocked task can be accessed.",
    )


def require_current_task_for_active_project(
    db: Session,
    student_id: str,
    project_id: UUID,
    task_id: UUID,
    *,
    allow_resubmission: bool = False,
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
        require_current_task_access(
            db, simulation, task_id, allow_resubmission=allow_resubmission
        )


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


def _ensure_project_dependency_graph(db: Session, project_id: UUID) -> None:
    try:
        validate_project_dependency_graph(db, project_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task dependency graph is invalid: {exc}",
        ) from exc


def add_task_dependency(db: Session, task_id: UUID, prerequisite_id: UUID) -> TaskDependency:
    """Persist one same-project prerequisite edge only when it stays acyclic."""
    task = db.get(Task, task_id)
    prerequisite = db.get(Task, prerequisite_id)
    if task is None or prerequisite is None:
        raise ValueError("Dependency tasks must both exist")
    if task_id == prerequisite_id:
        raise ValueError("A task cannot depend on itself")
    if task.sprint.project_id != prerequisite.sprint.project_id:
        raise ValueError("Task dependencies must stay within the same project")

    graph = _project_dependency_graph(db, task.sprint.project_id)
    if _can_reach(graph, prerequisite_id, task_id):
        raise ValueError("Dependency would create a circular prerequisite chain")

    dependency = TaskDependency(task_id=task_id, depends_on_task_id=prerequisite_id)
    db.add(dependency)
    db.flush()
    validate_project_dependency_graph(db, task.sprint.project_id)
    return dependency


def validate_project_dependency_graph(db: Session, project_id: UUID) -> None:
    """Reject missing, cross-project, self, or cyclic task prerequisites."""
    graph = _project_dependency_graph(db, project_id)
    for task_id, prerequisites in graph.items():
        for prerequisite_id in prerequisites:
            if prerequisite_id == task_id:
                raise ValueError("A task cannot depend on itself")
            if prerequisite_id not in graph:
                raise ValueError("Task dependencies must stay within the same project")
    visiting: set[UUID] = set()
    visited: set[UUID] = set()

    def visit(task_id: UUID) -> None:
        if task_id in visiting:
            raise ValueError("Dependency graph contains a circular prerequisite chain")
        if task_id in visited:
            return
        visiting.add(task_id)
        for prerequisite_id in graph.get(task_id, []):
            visit(prerequisite_id)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in graph:
        visit(task_id)


def _project_dependency_graph(db: Session, project_id: UUID) -> dict[UUID, list[UUID]]:
    task_ids = db.execute(
        select(Task.id).join(Sprint, Sprint.id == Task.sprint_id)
        .where(Sprint.project_id == project_id)
    ).scalars().all()
    graph: dict[UUID, list[UUID]] = {task_id: [] for task_id in task_ids}
    if task_ids:
        for task_id, prerequisite_id in db.execute(
            select(TaskDependency.task_id, TaskDependency.depends_on_task_id)
            .where(TaskDependency.task_id.in_(task_ids))
        ).all():
            graph[task_id].append(prerequisite_id)
    return graph


def _can_reach(graph: dict[UUID, list[UUID]], start: UUID, target: UUID) -> bool:
    pending = [start]
    visited: set[UUID] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(graph.get(current, []))
    return False
