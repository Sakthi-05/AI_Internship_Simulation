"""
Task Path Builder - creates personalized task paths for each student.

Canonical tasks in sprints are shared templates. This service creates a
per-student subset of those tasks, ordered by sprint and sequence, allowing
different students to receive different numbers of project-relevant tasks.

Personalisation is based on two signals:
1. **Difficulty level** — the student's chosen difficulty filters which task
   difficulties are included (beginners get all tasks for practice; experts
   skip basic tasks).
2. **Learning pace** — the student's ``duration_weeks`` and each task's
   ``estimated_hours`` set a total hour budget.  Tasks are greedily
   included in sprint/sequence order until the budget is exhausted.
   Dependency prerequisites are always pulled in, even if they slightly
   exceed the budget, because skipping them would break the task chain.
"""
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import TaskStatus, DifficultyLevel
from app.models.sprint import Sprint
from app.models.task import Task, TaskDependency
from app.models.task_path import SimulationTaskPath
from app.models.simulation import Simulation

_HOURS_PER_WEEK = 20


def get_canonical_tasks_for_project(db: Session, project_id: UUID) -> List[Task]:
    """Fetch all canonical tasks belonging to a project, ordered by sprint then sequence."""
    return db.execute(
        select(Task)
        .join(Sprint, Sprint.id == Task.sprint_id)
        .where(Sprint.project_id == project_id)
        .order_by(Sprint.sprint_number, Task.sequence)
    ).scalars().all()


def build_task_path(
    db: Session,
    simulation_id: UUID,
    project_id: UUID,
    task_ids: Optional[List[UUID]] = None,
) -> List[SimulationTaskPath]:
    """Create a personalized task path for a simulation.

    When *task_ids* is provided the path is exactly those tasks.  Otherwise the
    path is built automatically by combining difficulty filtering with a
    learning-pace budget derived from ``duration_weeks`` and
    ``estimated_hours``.

    Args:
        db: Database session
        simulation_id: The student's simulation
        project_id: The project whose tasks to draw from
        task_ids: Optional explicit list of canonical task IDs to include.
                  If None, tasks are selected automatically.

    Returns:
        List of created SimulationTaskPath entries, ordered by sequence.
    """
    if task_ids is not None:
        tasks = _build_explicit_path(db, project_id, task_ids)
    else:
        tasks = _build_personalised_path(db, simulation_id, project_id)

    path_entries: List[SimulationTaskPath] = []
    for idx, task in enumerate(tasks, start=1):
        entry = SimulationTaskPath(
            simulation_id=simulation_id,
            task_id=task.id,
            sequence=idx,
            status=TaskStatus.BACKLOG,
        )
        db.add(entry)
        path_entries.append(entry)

    db.flush()
    return path_entries


def _build_explicit_path(
    db: Session, project_id: UUID, task_ids: List[UUID]
) -> List[Task]:
    """Return the explicitly requested tasks, validated and sorted."""
    tasks = []
    for tid in task_ids:
        task = db.get(Task, tid)
        if task and task.sprint_id:
            sprint = db.get(Sprint, task.sprint_id)
            if sprint and sprint.project_id == project_id:
                tasks.append(task)
    tasks.sort(key=lambda t: (
        _get_sprint_number(db, t.sprint_id),
        t.sequence,
    ))
    return tasks


def _build_personalised_path(
    db: Session, simulation_id: UUID, project_id: UUID
) -> List[Task]:
    """Build a task path personalised by difficulty *and* learning pace."""
    all_tasks = get_canonical_tasks_for_project(db, project_id)
    simulation = db.get(Simulation, simulation_id)

    if not simulation or not simulation.difficulty:
        return all_tasks

    # --- Step 1: difficulty filter ---
    difficulty_weights = {
        DifficultyLevel.BEGINNER: 1,
        DifficultyLevel.INTERMEDIATE: 2,
        DifficultyLevel.ADVANCED: 3,
        DifficultyLevel.EXPERT: 4,
    }
    sim_weight = difficulty_weights.get(simulation.difficulty, 1)
    difficulty_ids = {
        t.id for t in all_tasks
        if difficulty_weights.get(t.difficulty, 1) >= sim_weight or _is_adaptive_task(t)
    }

    # --- Step 2: learning-pace budget ---
    duration_weeks = simulation.duration_weeks or 4
    available_hours = duration_weeks * _HOURS_PER_WEEK

    ordered_candidates = sorted(
        [t for t in all_tasks if t.id in difficulty_ids],
        key=lambda t: (_get_sprint_number(db, t.sprint_id), t.sequence),
    )

    pace_ids: set = set()
    running_hours = 0.0
    for t in ordered_candidates:
        task_hours = t.estimated_hours if t.estimated_hours and t.estimated_hours > 0 else 4.0
        if running_hours + task_hours <= available_hours:
            pace_ids.add(t.id)
            running_hours += task_hours

    # --- Step 3: dependency resolution ---
    for task in all_tasks:
        if _is_adaptive_task(task):
            pace_ids.add(task.id)

    if pace_ids:
        _ensure_dependencies(db, all_tasks, pace_ids)

    return [t for t in all_tasks if t.id in pace_ids]


def _ensure_dependencies(
    db: Session, all_tasks: List[Task], selected_ids: set
) -> None:
    """Recursively add missing dependency prerequisites for selected tasks."""
    project_task_ids = {t.id for t in all_tasks}
    deps = db.execute(
        select(TaskDependency.task_id, TaskDependency.depends_on_task_id)
        .where(TaskDependency.task_id.in_(project_task_ids))
    ).all()

    dep_map: dict = {}
    for t_id, d_id in deps:
        dep_map.setdefault(t_id, []).append(d_id)

    def _add(t_id: UUID) -> None:
        for d_id in dep_map.get(t_id, []):
            if d_id not in selected_ids:
                selected_ids.add(d_id)
                _add(d_id)

    for t_id in list(selected_ids):
        _add(t_id)


def get_task_path(db: Session, simulation_id: UUID) -> List[SimulationTaskPath]:
    """Get the ordered task path for a simulation."""
    return db.execute(
        select(SimulationTaskPath)
        .where(SimulationTaskPath.simulation_id == simulation_id)
        .order_by(SimulationTaskPath.sequence)
    ).scalars().all()


def get_current_path_entry(
    db: Session, simulation_id: UUID
) -> Optional[SimulationTaskPath]:
    """Get the first non-completed task path entry for a simulation."""
    return db.execute(
        select(SimulationTaskPath)
        .where(
            SimulationTaskPath.simulation_id == simulation_id,
            SimulationTaskPath.status != TaskStatus.COMPLETED,
        )
        .order_by(SimulationTaskPath.sequence)
    ).scalars().first()


def _is_adaptive_task(task: Task) -> bool:
    """Reinforcement and stretch tasks stay on the path at every difficulty."""
    title = task.title or ""
    return title.startswith("[Focus]") or title.startswith("[Stretch]")


def _get_sprint_number(db: Session, sprint_id: UUID) -> int:
    sprint = db.get(Sprint, sprint_id)
    return sprint.sprint_number if sprint else 0
