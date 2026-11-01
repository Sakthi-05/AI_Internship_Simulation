"""
Simulation Progression Service - handles automatic task and scenario progression.

When a task becomes COMPLETED:
1. Find next task in current scenario
2. Assign next task (set to TODO)
3. Update simulation current_task
4. Update progress
5. If scenario is complete, move to next scenario
6. If all scenarios/tasks are complete, mark simulation COMPLETED
"""
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import DifficultyLevel, EventType, ScenarioStatus, SimulationStatus, SprintStatus, TaskStatus
from app.models.project import Project
from app.models.project_state import ProjectState
from app.models.sprint import Sprint
from app.models.simulation import Simulation, Scenario, ScenarioTask
from app.models.task import Task
from app.models.task_path import SimulationTaskPath
from app.services.activity_log_service import log_activity


async def progress_to_next_task(
    db: Session,
    simulation: Simulation,
    completed_task: Task,
    user_id: str,
) -> Optional[Task]:
    """
    Progress to the next task after current task is completed.
    Returns the next task if found, None if simulation is complete.
    """
    # Check if task paths are in use for this simulation
    completed_path_entry = db.execute(
        select(SimulationTaskPath)
        .where(
            SimulationTaskPath.simulation_id == simulation.id,
            SimulationTaskPath.task_id == completed_task.id,
        )
    ).scalar_one_or_none()

    if completed_path_entry:
        # All legitimate completion paths (API, manager review, or status route)
        # converge here, so keep the student path in sync with the task state.
        if simulation.current_task_id and simulation.current_task_id != completed_task.id:
            return None
        if completed_task.status != TaskStatus.COMPLETED:
            return None
        if completed_path_entry.status != TaskStatus.COMPLETED:
            completed_path_entry.status = TaskStatus.COMPLETED
            completed_path_entry.completed_at = completed_task.completed_at or datetime.now(timezone.utc)
            db.add(completed_path_entry)

        await _sync_sprint_completion(db, simulation, completed_task.sprint_id)

        # Use the shared path eligibility rule so progression cannot skip an
        # ordered task, dependency, or unfinished sprint.
        from app.services.simulation.task_access_service import get_next_available_path_entry
        next_path_entry = get_next_available_path_entry(db, simulation.id)

        if next_path_entry:
            next_task = db.get(Task, next_path_entry.task_id)
            if next_task:
                next_task = _materialize_difficulty_task(db, simulation, next_path_entry, next_task)
                if next_task.status == TaskStatus.BACKLOG:
                    next_task.status = TaskStatus.TODO
                if next_path_entry.status == TaskStatus.BACKLOG:
                    next_path_entry.status = TaskStatus.TODO

                _activate_sprint(db, next_task.sprint_id)

                simulation.current_task_id = next_task.id
                simulation.progress = _calculate_simulation_progress(db, simulation)

                # Update current_scenario_id if the next task belongs to a different scenario
                _sync_current_scenario(db, simulation, next_task)

                db.add(next_task)
                db.add(next_path_entry)
                db.add(simulation)
                db.flush()

                log_activity(
                    db,
                    company_id=simulation.company_id,
                    actor=user_id,
                    action="simulation_task_progressed",
                    detail=f"Progressed to next task '{next_task.title}' in task path",
                    simulation_id=str(simulation.id),
                    task_id=str(next_task.id),
                    previous_task_id=str(completed_task.id),
                )

                return next_task

        # If no static path entry exists, check if current sprint requires a dynamic next task
        # REMOVED: Fixed task-count logic (sprint_completed_count < 2)
        # NOW: Sprint completion is determined by sprint objective, not arbitrary task count
        # Dynamic tasks are now generated in the review pipeline after each approved task
        # This section is kept for backward compatibility with legacy simulations
        current_sprint = db.get(Sprint, completed_task.sprint_id)
        if current_sprint and current_sprint.status != SprintStatus.COMPLETED:
            # For dynamic mode, tasks are generated in review_service.py after approval
            # This path is only reached for legacy simulations or if review generation failed
            # We still provide a fallback task if the sprint is not complete
            from app.services.generators.dynamic_task_generator import generate_dynamic_task
            dyn_task = await generate_dynamic_task(db, simulation, current_sprint, user_id, completed_task)
            dyn_task.status = TaskStatus.TODO

            max_seq = db.execute(
                select(SimulationTaskPath.sequence)
                .where(SimulationTaskPath.simulation_id == simulation.id)
                .order_by(SimulationTaskPath.sequence.desc())
            ).scalars().first() or 0

            new_entry = SimulationTaskPath(
                simulation_id=simulation.id,
                task_id=dyn_task.id,
                sequence=max_seq + 1,
                status=TaskStatus.TODO,
            )
            db.add(dyn_task)
            db.add(new_entry)
            simulation.current_task_id = dyn_task.id
            simulation.progress = _calculate_simulation_progress(db, simulation)
            db.add(simulation)
            db.flush()

            log_activity(
                db,
                company_id=simulation.company_id,
                actor=user_id,
                action="simulation_task_progressed",
                detail=f"Fallback dynamic task '{dyn_task.title}' generated in sprint",
                simulation_id=str(simulation.id),
                task_id=str(dyn_task.id),
                previous_task_id=str(completed_task.id),
            )
            return dyn_task

        remaining_path_task = db.execute(
            select(SimulationTaskPath.id)
            .join(Task, Task.id == SimulationTaskPath.task_id)
            .where(
                SimulationTaskPath.simulation_id == simulation.id,
                Task.status != TaskStatus.COMPLETED,
            )
        ).first()
        if remaining_path_task:
            # A prerequisite not yet completed blocks the path. Do not mark the
            # simulation complete or skip to a later task.
            simulation.current_task_id = None
            db.add(simulation)
            db.flush()
            return None

        # No more tasks in path - simulation is complete
        return await _complete_simulation(db, simulation, user_id)

    # Fallback to original ScenarioTask-based progression
    if not simulation.current_scenario_id:
        return None
    
    current_scenario = db.get(Scenario, simulation.current_scenario_id)
    if not current_scenario:
        return None
    
    # Find the completed task's sequence in the scenario
    completed_scenario_task = db.execute(
        select(ScenarioTask)
        .where(
            ScenarioTask.scenario_id == current_scenario.id,
            ScenarioTask.task_id == completed_task.id
        )
    ).scalar_one_or_none()
    
    if not completed_scenario_task:
        return None
    
    completed_sequence = completed_scenario_task.sequence
    
    # Find next UNCOMPLETED task in the same scenario (skip already completed tasks)
    next_scenario_task = db.execute(
        select(ScenarioTask)
        .join(Task, Task.id == ScenarioTask.task_id)
        .where(
            ScenarioTask.scenario_id == current_scenario.id,
            ScenarioTask.sequence > completed_sequence,
            Task.status != TaskStatus.COMPLETED,
        )
        .order_by(ScenarioTask.sequence)
    ).scalars().first()
    
    if next_scenario_task:
        # Found next uncompleted task in current scenario
        next_task = db.get(Task, next_scenario_task.task_id)
        if next_task:
            # Assign next task (set to TODO)
            if next_task.status == TaskStatus.BACKLOG:
                next_task.status = TaskStatus.TODO
            
            simulation.current_task_id = next_task.id
            simulation.progress = _calculate_simulation_progress(db, simulation)
            
            db.add(next_task)
            db.add(simulation)
            db.flush()
            
            # Log activity
            log_activity(
                db,
                company_id=simulation.company_id,
                actor=user_id,
                action="simulation_task_progressed",
                detail=f"Progressed to next task '{next_task.title}' in scenario '{current_scenario.title}'",
                simulation_id=str(simulation.id),
                scenario_id=str(current_scenario.id),
                task_id=str(next_task.id),
                previous_task_id=str(completed_task.id),
            )
            
            return next_task
    
    # No more uncompleted tasks in current scenario - check if scenario is truly complete
    return await _progress_to_next_scenario(db, simulation, current_scenario, user_id)


def _materialize_difficulty_task(db: Session, simulation: Simulation, path_entry: SimulationTaskPath, source: Task) -> Task:
    """Give this simulation a task whose work scope matches its new level.

    Canonical tasks are shared across simulations, so adapting one in place
    would leak a student's difficulty to other students. Replace only this
    simulation's next path link with a scoped copy when the level changes.
    """
    level = simulation.difficulty
    if not level or source.difficulty == level:
        return source
    requirements = {
        DifficultyLevel.BEGINNER: [
            "Break the work into small, verifiable steps and document assumptions.",
            "Add a focused test for the main success case and one invalid-input case.",
        ],
        DifficultyLevel.INTERMEDIATE: [
            "Implement the complete behavior and cover boundary cases with automated tests.",
            "Document the design trade-offs and validate failure handling.",
        ],
        DifficultyLevel.ADVANCED: [
            "Design for edge cases, operational failure, and maintainability.",
            "Add tests for correctness and failure modes, and justify performance/security trade-offs.",
        ],
        DifficultyLevel.EXPERT: [
            "Design for scale, resilience, and measurable operational constraints.",
            "Provide comprehensive correctness, security, and performance validation with trade-off analysis.",
        ],
    }
    tier_criteria = requirements.get(level, requirements[DifficultyLevel.INTERMEDIATE])
    clone = Task(
        sprint_id=source.sprint_id,
        sequence=source.sequence,
        title=f"{source.title} — {level.value.title()}",
        description=(
            f"{source.description}\n\nDifficulty: {level.value.title()}. "
            "Complete the original objective at this level of independence and validation."
        ),
        acceptance_criteria=list(source.acceptance_criteria or []) + tier_criteria,
        priority=source.priority,
        status=TaskStatus.BACKLOG,
        task_type=source.task_type,
        difficulty=level,
        estimated_hours=source.estimated_hours,
        deadline=source.deadline,
    )
    db.add(clone)
    db.flush()
    path_entry.task_id = clone.id
    db.add(path_entry)
    db.flush()
    return clone


async def _progress_to_next_scenario(
    db: Session,
    simulation: Simulation,
    completed_scenario: Scenario,
    user_id: str,
) -> Optional[Task]:
    """
    Progress to the next scenario after current scenario is verified complete.
    
    IMPORTANT: This function does NOT assume the scenario is complete.
    It verifies that ALL tasks linked through scenario_tasks are COMPLETED
    before marking the scenario as done. If any task is not completed,
    it returns None (scenario stays ACTIVE, current_task_id stays unchanged).
    """
    # Verify ALL scenario tasks are actually completed before marking scenario done
    all_completed = True
    for scenario_task in completed_scenario.tasks:
        task = db.get(Task, scenario_task.task_id)
        if task and task.status != TaskStatus.COMPLETED:
            all_completed = False
            break
    
    if not all_completed:
        # Scenario is NOT complete - do not mark as completed, do not change current_task
        # The caller should not have called this function; return None to signal no progression
        return None
    
    # All tasks confirmed completed - mark scenario as completed
    completed_scenario.status = ScenarioStatus.COMPLETED
    db.add(completed_scenario)
    
    # Find next scenario
    next_scenario = db.execute(
        select(Scenario)
        .where(
            Scenario.simulation_id == simulation.id,
            Scenario.sequence > completed_scenario.sequence
        )
        .order_by(Scenario.sequence)
    ).scalar_one_or_none()
    
    if next_scenario:
        # Found next scenario
        next_scenario.status = ScenarioStatus.ACTIVE
        simulation.current_scenario_id = next_scenario.id
        db.add(next_scenario)
        
        # Find first UNCOMPLETED task in next scenario
        first_scenario_task = db.execute(
            select(ScenarioTask)
            .join(Task, Task.id == ScenarioTask.task_id)
            .where(
                ScenarioTask.scenario_id == next_scenario.id,
                Task.status != TaskStatus.COMPLETED,
            )
            .order_by(ScenarioTask.sequence)
        ).scalars().first()
        
        if first_scenario_task:
            first_task = db.get(Task, first_scenario_task.task_id)
            if first_task:
                # Assign first task of next scenario
                if first_task.status == TaskStatus.BACKLOG:
                    first_task.status = TaskStatus.TODO
                
                simulation.current_task_id = first_task.id
                simulation.progress = _calculate_simulation_progress(db, simulation)
                db.add(first_task)
                
                log_activity(
                    db,
                    company_id=simulation.company_id,
                    actor=user_id,
                    action="simulation_scenario_progressed",
                    detail=f"Progressed to next scenario '{next_scenario.title}' with task '{first_task.title}'",
                    simulation_id=str(simulation.id),
                    scenario_id=str(next_scenario.id),
                    task_id=str(first_task.id),
                    previous_scenario_id=str(completed_scenario.id),
                )
                
                return first_task
        
        # Next scenario has no uncompleted tasks - recursively check next scenario
        return await _progress_to_next_scenario(db, simulation, next_scenario, user_id)
    
    else:
        # No more scenarios - simulation is complete!
        return await _complete_simulation(db, simulation, user_id)


async def _complete_simulation(
    db: Session,
    simulation: Simulation,
    user_id: str,
) -> None:
    """Mark simulation as completed."""
    simulation.status = SimulationStatus.COMPLETED
    simulation.completed_at = datetime.now(timezone.utc)
    simulation.current_task_id = None
    simulation.current_scenario_id = None
    simulation.progress = 100
    db.add(simulation)
    
    # Calculate total duration
    duration_minutes = 0
    if simulation.started_at and simulation.completed_at:
        duration_seconds = (simulation.completed_at - simulation.started_at).total_seconds()
        duration_minutes = int(duration_seconds / 60)
    
    log_activity(
        db,
        company_id=simulation.company_id,
        actor=user_id,
        action="simulation_completed",
        detail=f"Completed simulation '{simulation.title}' in {duration_minutes} minutes",
        simulation_id=str(simulation.id),
        duration_minutes=duration_minutes,
    )


def _calculate_simulation_progress(db: Session, simulation: Simulation) -> int:
    """Calculate overall simulation progress percentage.
    
    When task paths exist (student-specific task subsets), count from those.
    Otherwise fall back to canonical scenario tasks.
    """
    from app.models.task_path import SimulationTaskPath

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


async def _sync_sprint_completion(db: Session, simulation: Simulation, sprint_id: UUID) -> None:
    """Complete a sprint only when every assigned path task is completed.
    
    Sprint completion is now objective-based rather than count-based.
    A sprint is complete when all its tasks are COMPLETED, not when an arbitrary
    number of tasks are done. This allows variable task counts per sprint.
    
    When a sprint completes:
    1. Trigger SPRINT_COMPLETED event
    2. Activate the next sprint if one exists
    3. Generate the first dynamic task for the next sprint so work continues
    """
    sprint = db.get(Sprint, sprint_id)
    if not sprint:
        return

    unfinished = db.execute(
        select(SimulationTaskPath.id)
        .join(Task, Task.id == SimulationTaskPath.task_id)
        .where(
            SimulationTaskPath.simulation_id == simulation.id,
            Task.sprint_id == sprint_id,
            Task.status != TaskStatus.COMPLETED,
        )
    ).first()
    if unfinished is None and sprint.status != SprintStatus.COMPLETED:
        sprint.status = SprintStatus.COMPLETED
        db.add(sprint)

        state = db.execute(
            select(ProjectState).where(ProjectState.project_id == sprint.project_id)
        ).scalar_one_or_none()
        if state:
            next_sprint = db.execute(
                select(Sprint)
                .where(Sprint.project_id == sprint.project_id, Sprint.sprint_number > sprint.sprint_number)
                .order_by(Sprint.sprint_number)
            ).scalars().first()
            if next_sprint:
                state.current_sprint_number = next_sprint.sprint_number
                db.add(state)

        project = db.execute(
            select(Project).where(Project.id == sprint.project_id)
        ).scalar_one_or_none()

        if project:
            from app.services.engines.events_engine import trigger_event
            sprint_progress = calculate_sprint_progress(db, simulation, sprint_id)
            await trigger_event(
                db, project, EventType.SPRINT_COMPLETED,
                f"{sprint.name} objective achieved — all tasks completed. "
                f"Sprint progress: {sprint_progress['progress_percentage']}%.",
                {
                    "sprint_id": str(sprint_id),
                    "sprint_number": sprint.sprint_number,
                    "sprint_goal": sprint.goal,
                    "completed_tasks": sprint_progress.get("completed_tasks", 0),
                    "total_tasks": sprint_progress.get("total_tasks", 0),
                }
            )

            from app.services.notification_service import notify
            notify(
                db,
                company_id=project.company_id,
                notification_type="sprint_completed",
                message=f"{sprint.name} complete! Objective achieved: {sprint.goal}",
            )


def calculate_sprint_progress(db: Session, simulation: Simulation, sprint_id: UUID) -> Dict[str, Any]:
    """Calculate sprint objective progress based on completed tasks.
    
    Returns progress percentage and objective completion status.
    This is used for UI display and to determine if more tasks are needed.
    """
    sprint = db.get(Sprint, sprint_id)
    if not sprint:
        return {"sprint_id": str(sprint_id), "progress_percentage": 0, "objective_complete": False}
    
    # Count total and completed tasks in this sprint for this simulation
    path_tasks = db.execute(
        select(SimulationTaskPath)
        .join(Task, Task.id == SimulationTaskPath.task_id)
        .where(
            SimulationTaskPath.simulation_id == simulation.id,
            Task.sprint_id == sprint_id,
        )
    ).scalars().all()
    
    if not path_tasks:
        return {
            "sprint_id": str(sprint_id),
            "sprint_number": sprint.sprint_number,
            "sprint_goal": sprint.goal,
            "progress_percentage": 0,
            "objective_complete": False,
            "total_tasks": 0,
            "completed_tasks": 0,
        }
    
    total_tasks = len(path_tasks)
    completed_tasks = sum(1 for entry in path_tasks if entry.task.status == TaskStatus.COMPLETED)
    progress_percentage = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
    
    # Sprint objective is complete when all tasks are done OR sprint is marked COMPLETED
    objective_complete = (completed_tasks == total_tasks) or sprint.status == SprintStatus.COMPLETED
    
    return {
        "sprint_id": str(sprint_id),
        "sprint_number": sprint.sprint_number,
        "sprint_goal": sprint.goal,
        "progress_percentage": round(progress_percentage, 2),
        "objective_complete": objective_complete,
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "status": sprint.status.value,
    }


def _activate_sprint(db: Session, sprint_id: UUID) -> None:
    """Expose only the sprint containing the newly unlocked current task."""
    sprint = db.get(Sprint, sprint_id)
    if sprint and sprint.status != SprintStatus.COMPLETED:
        sprint.status = SprintStatus.ACTIVE
        db.add(sprint)

        state = db.execute(
            select(ProjectState).where(ProjectState.project_id == sprint.project_id)
        ).scalar_one_or_none()
        if state:
            state.current_sprint_number = sprint.sprint_number
            db.add(state)


def _sync_current_scenario(db: Session, simulation: Simulation, task: Task) -> None:
    """Update current_scenario_id if the given task belongs to a different scenario."""
    scenario_task = db.execute(
        select(ScenarioTask)
        .join(Scenario, Scenario.id == ScenarioTask.scenario_id)
        .where(
            ScenarioTask.task_id == task.id,
            Scenario.simulation_id == simulation.id,
        )
    ).scalar_one_or_none()

    if scenario_task and scenario_task.scenario_id != simulation.current_scenario_id:
        simulation.current_scenario_id = scenario_task.scenario_id
        db.add(simulation)


async def trigger_requirement_change_for_task(
    db: Session,
    simulation: Simulation,
    task: Task,
    scenario: Scenario,
    original_requirement: str,
    updated_requirement: str,
) -> None:
    """Issue one requirement change through the workplace scenario writer.

    The supplied requirement strings are not stored separately. Callers must
    pass a task that is already in progress; this does not run on a timer.
    """
    del original_requirement, updated_requirement
    from app.services.engines import trigger_requirement_change_event
    await trigger_requirement_change_event(
        db,
        simulation,
        task,
        scenario,
        "",
        "",
        trigger_condition="recorded_work",
    )


async def check_scenario_completion(db: Session, simulation: Simulation, scenario: Scenario) -> bool:
    """Check if all tasks linked through scenario_tasks are completed."""
    for scenario_task in scenario.tasks:
        task = db.get(Task, scenario_task.task_id)
        if not task or task.status != TaskStatus.COMPLETED:
            return False
    return True


async def get_next_task_for_simulation(db: Session, simulation: Simulation) -> Optional[Task]:
    """Get the next task that should be worked on for a simulation."""
    if not simulation.current_task_id:
        # No current task - find first task of first active scenario
        if not simulation.current_scenario_id:
            # Find first pending/active scenario
            first_scenario = db.execute(
                select(Scenario)
                .where(Scenario.simulation_id == simulation.id)
                .order_by(Scenario.sequence)
            ).scalars().first()
            
            if first_scenario and first_scenario.status in [ScenarioStatus.PENDING, ScenarioStatus.ACTIVE]:
                first_scenario.status = ScenarioStatus.ACTIVE
                simulation.current_scenario_id = first_scenario.id
                db.add(first_scenario)
                db.add(simulation)
                db.flush()
        
        if simulation.current_scenario_id:
            # Find first UNCOMPLETED task in the scenario
            first_scenario_task = db.execute(
                select(ScenarioTask)
                .join(Task, Task.id == ScenarioTask.task_id)
                .where(
                    ScenarioTask.scenario_id == simulation.current_scenario_id,
                    Task.status != TaskStatus.COMPLETED,
                )
                .order_by(ScenarioTask.sequence)
            ).scalars().first()
            
            if first_scenario_task:
                task = db.get(Task, first_scenario_task.task_id)
                if task and task.status == TaskStatus.BACKLOG:
                    task.status = TaskStatus.TODO
                    db.add(task)
                    db.flush()
                simulation.current_task_id = task.id if task else None
                db.add(simulation)
                db.flush()
                return task
    else:
        # Return current task if it's not completed
        current_task = db.get(Task, simulation.current_task_id)
        if current_task and current_task.status != TaskStatus.COMPLETED:
            return current_task
    
    return None
