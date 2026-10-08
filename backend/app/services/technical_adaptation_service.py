"""Turn persisted technical reviews into competency and difficulty evidence."""
from __future__ import annotations

import statistics
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import DifficultyLevel
from app.models.intelligence import InteractionEvaluation, StudentProfile
from app.models.simulation import Simulation
from app.models.simulation_interaction import SimulationInteraction
from app.models.submission import AIReview, SubmissionVersion
from app.models.task import Task
from app.services.intelligence_service import DEFAULT_COMPETENCIES, calculate_readiness, create_feedback_report
from app.services.simulation.interaction_history_service import record_interaction


TECHNICAL_SCORE_THRESHOLDS = {"decrease_below": 58.0, "increase_at_or_above": 78.0}
TECHNICAL_SCORE_WEIGHTS = {
    "correctness": 0.30,
    "requirements_coverage": 0.25,
    "solution_quality": 0.20,
    "testing_validation": 0.15,
    "efficiency": 0.05,
    "time_efficiency": 0.05,
}
ADAPTATION_WINDOW = 3
TECHNICAL_DIFFICULTY_LEVELS = (
    DifficultyLevel.BEGINNER,
    DifficultyLevel.INTERMEDIATE,
    DifficultyLevel.ADVANCED,
)


def score_technical_review(review_payload: dict, task: Task, version: SubmissionVersion) -> tuple[float, dict]:
    """Compute a weighted score from the review rubric and submitted evidence."""
    criteria = review_payload.get("acceptance_criteria") or []
    criterion_scores = [100.0 if item.get("met") else 0.0 for item in criteria if isinstance(item, dict) and "met" in item]
    elapsed = None
    if task.started_at and version.submitted_at:
        elapsed = max(0.0, (version.submitted_at - task.started_at).total_seconds() / 3600)
    expected_hours = float(task.estimated_hours or 0)
    def rubric_score(name: str) -> float | None:
        raw = (review_payload.get(name) or {}).get("score")
        return float(raw) if raw is not None else None

    dimensions = {
        "correctness": float(review_payload["score"]),
        "requirements_coverage": statistics.mean(criterion_scores) if criterion_scores else None,
        "solution_quality": rubric_score("code_quality"),
        "testing_validation": rubric_score("testing"),
        "efficiency": rubric_score("performance"),
        "time_efficiency": min(100.0, expected_hours / elapsed * 100.0) if elapsed and expected_hours > 0 else None,
    }
    available_weight = sum(TECHNICAL_SCORE_WEIGHTS[key] for key, value in dimensions.items() if value is not None)
    score = sum(TECHNICAL_SCORE_WEIGHTS[key] * value for key, value in dimensions.items() if value is not None) / available_weight
    return round(score, 2), {key: round(value, 2) if value is not None else None for key, value in dimensions.items()}


def _reason(score: float, previous: str, next_level: str, delta: int, window: list[float]) -> str:
    evidence = f"Recent technical average {score:.1f}/100 across {len(window)} evaluated task(s)."
    if previous == next_level and delta != 0:
        return f"{evidence} The score crossed the adaptation threshold, but {previous.title()} is the difficulty boundary, so the level stays unchanged."
    if delta > 0:
        return f"{evidence} Correctness, requirement coverage, quality, and validation meet the {TECHNICAL_SCORE_THRESHOLDS['increase_at_or_above']:.0f}+ threshold; the next task increases from {previous.title()} to {next_level.title()}."
    if delta < 0:
        return f"{evidence} The result is below {TECHNICAL_SCORE_THRESHOLDS['decrease_below']:.0f}; the next task is scaffolded at {next_level.title()} rather than {previous.title()}."
    return f"{evidence} The score is within the {TECHNICAL_SCORE_THRESHOLDS['decrease_below']:.0f}–{TECHNICAL_SCORE_THRESHOLDS['increase_at_or_above']:.0f} hold range, so difficulty remains {next_level.title()}."


async def apply_technical_review_adaptation(
    db: Session,
    *,
    simulation: Simulation,
    task: Task,
    version: SubmissionVersion,
    review: AIReview,
    review_payload: dict,
    evaluation_source: str,
) -> dict:
    """Persist technical evaluation, update competencies, and adapt one level.

    Call only after a full-evidence review has completed. The enclosing review
    transaction makes the evaluation and adaptation idempotent with its AIReview.
    """
    from app.services.intelligence_service import get_or_create_profile

    technical_score, dimensions = score_technical_review(review_payload, task, version)
    profile = get_or_create_profile(db, version.submission.student_id)
    competencies = dict(profile.current_competencies or profile.baseline_competencies or DEFAULT_COMPETENCIES)
    criterion_score = dimensions["requirements_coverage"]
    assessed_quality = [value for value in (dimensions["solution_quality"], dimensions["testing_validation"]) if value is not None]
    technical_competencies = {
        "technical_knowledge": statistics.mean(assessed_quality) if assessed_quality else technical_score,
        "problem_solving": dimensions["correctness"],
        "critical_thinking": criterion_score if criterion_score is not None else dimensions["correctness"],
    }
    for key, value in technical_competencies.items():
        competencies[key] = round(0.70 * float(competencies.get(key, 60.0)) + 0.30 * value, 2)
    profile.current_competencies = competencies
    profile.strengths = [key.replace("_", " ").title() for key, _ in sorted(competencies.items(), key=lambda item: item[1], reverse=True)[:3]]
    profile.weaknesses = [key.replace("_", " ").title() for key, _ in sorted(competencies.items(), key=lambda item: item[1])[:3]]

    occurred_at = datetime.now(timezone.utc)
    interaction = record_interaction(
        db,
        simulation,
        situation=f"Technical evaluation of {task.title}",
        student_response=None,
        decision="technical_review_completed",
        interaction_type="task_evaluated",
        task_id=task.id,
        occurred_at=occurred_at,
        metadata={
            "evidence_kind": "technical",
            "evaluation_source": evaluation_source,
            "technical_score": technical_score,
            "dimensions": dimensions,
            "submission_id": str(version.submission_id),
            "version_id": str(version.id),
            "review_id": str(review.id),
        },
    )
    evaluation = InteractionEvaluation(
        interaction_id=interaction.id,
        simulation_id=simulation.id,
        student_id=version.submission.student_id,
        overall_score=technical_score,
        competencies=technical_competencies,
        strengths=list(review_payload.get("strengths") or []),
        weaknesses=list(review_payload.get("issues") or review_payload.get("risks") or []),
        mistakes=list(review_payload.get("issues") or []),
        feedback=str(review_payload.get("summary") or "Technical review completed."),
        recommendations=list(review_payload.get("required_changes") or []),
        branch_selected="technical_performance",
        difficulty_delta=0,
        confidence=1.0 if evaluation_source == "gemini" else 0.5,
    )
    db.add(evaluation)
    db.flush()

    prior_rows = db.execute(
        select(InteractionEvaluation, SimulationInteraction)
        .join(SimulationInteraction, SimulationInteraction.id == InteractionEvaluation.interaction_id)
        .where(
            InteractionEvaluation.simulation_id == simulation.id,
            InteractionEvaluation.id != evaluation.id,
        )
        .order_by(SimulationInteraction.occurred_at.desc())
    ).all()
    prior_scores = [
        float(row.overall_score)
        for row, event in prior_rows
        if (event.metadata_json or {}).get("evidence_kind") == "technical"
    ][: ADAPTATION_WINDOW - 1]
    window = list(reversed(prior_scores)) + [technical_score]
    adaptation_score = round(statistics.mean(window), 2)

    levels = list(TECHNICAL_DIFFICULTY_LEVELS)
    current_level = simulation.difficulty
    if current_level not in levels:
        current_level = DifficultyLevel.ADVANCED
    previous = current_level.value
    if adaptation_score < TECHNICAL_SCORE_THRESHOLDS["decrease_below"]:
        delta = -1
    elif adaptation_score >= TECHNICAL_SCORE_THRESHOLDS["increase_at_or_above"]:
        delta = 1
    else:
        delta = 0
    current_index = levels.index(current_level)
    next_level = levels[max(0, min(len(levels) - 1, current_index + delta))]
    simulation.difficulty = next_level
    profile.recommended_difficulty = next_level.value
    params = dict(profile.recommended_parameters or {})
    params["difficulty"] = next_level.value
    profile.recommended_parameters = params
    db.add(simulation)
    db.add(profile)

    reason = _reason(adaptation_score, previous, next_level.value, delta, window)
    adaptation = {
        "status": "applied",
        "previous_difficulty": previous,
        "evaluated_task_score": technical_score,
        "technical_score": technical_score,
        "behavioral_score": None,
        "adaptation_score": adaptation_score,
        "difficulty_delta": next_level.value != previous and (1 if levels.index(next_level) > current_index else -1) or 0,
        "next_difficulty": next_level.value,
        "reason": reason,
        "task_id": str(task.id),
        "submission_id": str(version.submission_id),
        "evaluation_id": str(evaluation.id),
        "review_id": str(review.id),
        "timestamp": occurred_at.isoformat(),
        "evaluation_source": evaluation_source,
        "recent_technical_scores": window,
    }
    evaluation.difficulty_delta = adaptation["difficulty_delta"]
    db.add(evaluation)
    interaction.metadata_json = {**(interaction.metadata_json or {}), "evaluation_id": str(evaluation.id), "adaptive_decision": adaptation}
    review.raw_payload = {
        **(review.raw_payload or {}),
        "technical_evaluation": {"score": technical_score, "dimensions": dimensions, "source": evaluation_source},
        "adaptive_decision": adaptation,
    }
    db.add(interaction)
    db.add(review)
    await create_feedback_report(db, version.submission.student_id, simulation.id)
    readiness = calculate_readiness(db, version.submission.student_id, simulation.id)
    adaptation["readiness"] = readiness
    interaction.metadata_json = {**(interaction.metadata_json or {}), "adaptive_decision": adaptation}
    review.raw_payload = {**(review.raw_payload or {}), "adaptive_decision": adaptation}
    db.add(interaction)
    db.add(review)
    db.flush()
    return adaptation
