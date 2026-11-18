from __future__ import annotations

import uuid
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
import httpx

from app.models.enums import ReviewResult, ReviewStatus, ReviewSeverity, SubmissionStatus, TaskStatus, EventType, SprintStatus
from app.models.manager import Manager
from app.models.project import Project
from app.models.project_state import ProjectState
from app.models.submission import AIReview, ReviewHistory, SubmissionVersion
from app.models.task import Task
from app.schemas.review import AIManagerDecision, AIReviewResponse
from app.services.ai.ai_service import AIProviderResponseError, AIService, MalformedAIJSONError, MockAIService
from app.services.ai.prompt_builder import build_review_prompt
from app.services.content_extraction import extract_all_submission_files
from app.services.context.context_builder import build_review_context
from app.services.engines.events_engine import trigger_event
from app.services.file_storage import FileStorageService
from app.services.notification_service import notify
from app.services.engines.task_state_machine import validate_transition

logger = logging.getLogger(__name__)
# 3 provider requests * 20s plus 1s and 2s backoff = 63s. Leave 5s for
# parsing/persistence; the route's stale-claim lease is longer than this.
REVIEW_PROVIDER_TIMEOUT_SECONDS = 68.0


def _candidate_followup_sprints(current_sprint, project_sprints, objective_complete: bool):
    """Return only the current open sprint or later planned sprints.

    A completed final sprint intentionally yields no candidate; it must never
    fall back to generating another task in itself.
    """
    if not objective_complete:
        return [current_sprint] if current_sprint.status not in (SprintStatus.COMPLETED, SprintStatus.BLOCKED) else []
    return sorted(
        (
            sprint for sprint in project_sprints
            if sprint.sprint_number > current_sprint.sprint_number
            and sprint.status not in (SprintStatus.COMPLETED, SprintStatus.BLOCKED)
        ),
        key=lambda sprint: sprint.sprint_number,
    )


class ReviewEvaluationError(RuntimeError):
    """Provider/evaluator failure with safe, non-secret diagnostic metadata."""

    def __init__(self, category: str, provider: str, model: Optional[str], cause: Exception):
        self.category = category
        self.provider = provider
        self.model = model
        super().__init__(f"{category}: {type(cause).__name__}")


def classify_provider_failure(exc: Exception) -> str:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
        return "provider_timeout"
    if getattr(getattr(exc, "response", None), "status_code", None) == 429:
        return "provider_rate_limited"
    if isinstance(exc, (MalformedAIJSONError, AIProviderResponseError, json.JSONDecodeError, ValueError, KeyError, IndexError)):
        return "malformed_response"
    return "provider_error"


def _manager_decision_from_technical_review(ai_review) -> Dict[str, Any]:
    """Reuse the technical review. No second model call."""
    result = ai_review.result.value if ai_review.result is not None else "changes_required"
    decision = "approved" if result == "approved" else "changes_required"
    severity = ai_review.severity.value if ai_review.severity is not None else "info"
    summary = (ai_review.summary or "").strip()
    if len(summary) < 10:
        summary = "Manager accepted the technical review."
    notes = []
    strengths = [str(item) for item in (ai_review.strengths or [])[:3]]
    actions = [str(item) for item in (ai_review.actions or [])[:3]]
    if strengths:
        notes.append("Strengths: " + "; ".join(strengths))
    if actions:
        notes.append("Next: " + "; ".join(actions))
    feedback = " ".join(notes).strip() or summary
    return AIManagerDecision.model_validate({
        "decision": decision,
        "severity": severity,
        "summary": summary[:300],
        "feedback": feedback[:1000],
    }).model_dump()


def build_missing_evidence_payload(context: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic review for submissions with no analyzable work evidence.

    Used instead of calling the model when the version's evidence tier is
    "none" (nothing submitted) or "thin" (a note too brief to verify, e.g. a
    one-liner with no code): the reviewer states that plainly and says
    exactly what must be provided — never invents code findings. Attached
    filenames are quoted when present so the message references real context.
    Validated against the same AIReviewResponse schema as model output.
    """
    from app.schemas.review import AIReviewResponse

    task_title = str(context.get("task_title") or "this task")
    criteria = [str(item) for item in (context.get("acceptance_criteria") or []) if str(item).strip()]
    focus = "; ".join(criteria[:3]) if criteria else "each acceptance criterion"
    filenames = [str(name) for name in (context.get("attached_filenames") or []) if str(name).strip()]
    thin = context.get("evidence_level") == "thin"

    # Exact user-facing message for missing implementation: no analysis is
    # claimed, and it states precisely what must be submitted.
    summary = (
        "No implementation was submitted. "
        "Please submit your implementation and automated tests for review."
    )
    if thin:
        provide = (
            f"What was submitted for '{task_title}' is only a brief note with no analyzable "
            f"implementation. Expand it into real evidence: attach the code files you created "
            f"or modified, or paste the code plus an explanation of your approach."
        )
    else:
        provide = (
            f"Submit your implementation for '{task_title}': attach the code files you created "
            f"or modified, or paste the code into the submission response."
        )
    if filenames:
        provide += f" (Attached files seen: {', '.join(filenames[:5])} — no readable content could be extracted from them.)"

    required_changes = [
        provide,
        f"In the submission response, explain your approach for {focus}.",
        "Show verification: paste the test commands you ran and their results, "
        "or describe the manual checks you performed.",
    ]
    return AIReviewResponse.model_validate({
        "result": "changes_required",
        "score": None,
        "severity": "warning",
        "summary": summary,
        "strengths": [],
        "issues": ["Insufficient work evidence submitted for analysis"],
        "required_changes": required_changes[:5],
        "acceptance_criteria": [
            {
                "criterion": item,
                "met": False,
                "evidence": "Not demonstrated in the submitted content.",
            }
            for item in criteria
        ],
        "code_quality": {"score": None, "feedback": "Cannot assess code quality without submitted code."},
        "testing": {
            "score": None,
            "feedback": "No tests or verification evidence were submitted. Run the relevant tests and include the results.",
        },
        "security": {"score": None, "feedback": "Cannot assess security without submitted code."},
        "performance": {"score": None, "feedback": "Cannot assess performance without submitted code."},
    }).model_dump()


def _clean_str_list(values: Any) -> List[str]:
    """Coerce a stored JSON list into non-empty strings."""
    cleaned = []
    if isinstance(values, list):
        for item in values:
            text = str(item or "").strip()
            if text:
                cleaned.append(text)
    return cleaned


def validate_provider_review(raw: Any, context: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a provider decision against the complete task rubric."""
    required = {
        "result", "score", "severity", "summary", "strengths", "issues",
        "required_changes", "acceptance_criteria", "code_quality", "testing",
        "security", "performance",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValueError("AI review fields do not match the required schema")
    # Accept legacy in-process provider doubles during rollout, but normalize
    # every persisted evaluation to explicit per-requirement score/details.
    normalized_raw = dict(raw) if isinstance(raw, dict) else raw
    if isinstance(normalized_raw, dict) and isinstance(normalized_raw.get("acceptance_criteria"), list):
        normalized_criteria = []
        for entry in normalized_raw["acceptance_criteria"]:
            if not isinstance(entry, dict):
                normalized_criteria.append(entry)
                continue
            item = dict(entry)
            item.setdefault("score", 100 if item.get("met") is True else 0)
            item.setdefault("missing_elements", [] if item.get("met") is True else [str(item.get("evidence") or "Requirement is not demonstrated.")])
            normalized_criteria.append(item)
        normalized_raw["acceptance_criteria"] = normalized_criteria
    payload = AIReviewResponse.model_validate(normalized_raw, strict=True).model_dump()
    if payload["score"] is None:
        raise ValueError("AI review must include a numeric score")
    for section in ("code_quality", "testing", "security", "performance"):
        data = raw.get(section)
        if (
            not isinstance(data, dict)
            or set(data) != {"score", "feedback"}
            or data.get("score") is None
            or not str(data.get("feedback") or "").strip()
        ):
            raise ValueError(f"AI review must include scored, specific {section} feedback")
    expected = [str(item).strip() for item in context.get("acceptance_criteria", []) if str(item).strip()]
    if not expected:
        raise ValueError("Task has no persisted acceptance criteria; evaluation cannot approve without a task-specific rubric")
    criteria = payload["acceptance_criteria"]
    if any(not isinstance(entry, dict) or set(entry) not in (
        {"criterion", "met", "evidence"},
        {"criterion", "met", "evidence", "score", "missing_elements"},
    ) for entry in raw["acceptance_criteria"]):
        raise ValueError("AI review criterion entries do not match the required schema")
    actual = [entry["criterion"].strip() for entry in criteria]
    if len(actual) != len(set(actual)) or set(actual) != set(expected) or len(actual) != len(expected):
        raise ValueError("AI review must evaluate each task acceptance criterion exactly once")
    for entry in criteria:
        if entry["met"] and (entry["score"] < 80 or entry["missing_elements"]):
            raise ValueError("A satisfied requirement must be strongly supported (not weakly supported) and have no missing elements")
        if not entry["met"] and (entry["score"] >= 80 or not entry["missing_elements"]):
            raise ValueError("An unmet requirement must score below 80 and name its missing elements")
    if payload["result"] == "approved" and expected and not all(entry["met"] for entry in criteria):
        raise ValueError("AI review cannot approve work without meeting every mandatory criterion")
    if payload["result"] == "approved" and payload["score"] < 75:
        raise ValueError("AI review cannot approve work below the 75-point acceptance threshold")
    if payload["result"] == "approved" and any(entry["score"] < 80 for entry in criteria):
        raise ValueError("AI review cannot approve work with an unmet or weakly supported task requirement")
    if payload["result"] != "approved" and not payload["required_changes"]:
        raise ValueError("Non-approved AI review must include actionable required corrections")
    for field in ("strengths", "issues", "required_changes"):
        if any(not isinstance(item, str) or not item.strip() for item in payload[field]):
            raise ValueError(f"AI review {field} must contain only non-empty text")
    return payload


def shape_review_feedback(review_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Shape stored AI review output into actionable resubmission guidance.

    Pure transform over the persisted payload (no DB, no AI calls), so the
    exact feedback a reviewer produced is what the student sees:
    - required_changes: concrete code/functionality changes (falls back to the
      stored actions list when the model used that key instead).
    - unmet_criteria: acceptance criteria flagged met=false with the evidence
      explaining what was missing or incorrect.
    - testing_feedback / test_criteria: what must be tested before resubmit.
    """
    raw = review_payload or {}
    required = _clean_str_list(raw.get("required_changes")) or _clean_str_list(raw.get("actions"))

    unmet_criteria = []
    for entry in raw.get("acceptance_criteria") or []:
        if not isinstance(entry, dict) or entry.get("met"):
            continue
        criterion = str(entry.get("criterion") or "").strip()
        if not criterion:
            continue
        unmet_criteria.append({
            "criterion": criterion,
            "evidence": str(entry.get("evidence") or "").strip(),
        })

    testing = raw.get("testing") if isinstance(raw.get("testing"), dict) else {}
    testing_feedback = str(testing.get("feedback") or "").strip() or None
    test_criteria = [
        item["criterion"]
        for item in unmet_criteria
        if "test" in item["criterion"].lower()
    ]

    return {
        "result": raw.get("result"),
        "score": raw.get("score"),
        "summary": str(raw.get("summary") or "").strip(),
        "required_changes": required,
        "unmet_criteria": unmet_criteria,
        "testing_feedback": testing_feedback,
        "test_criteria": test_criteria,
        "strengths": _clean_str_list(raw.get("strengths")),
        "missing_evidence": bool(raw.get("missing_evidence", False)),
        "evaluation_source": raw.get("evaluation_source"),
        "technical_evaluation": raw.get("technical_evaluation"),
        "adaptive_decision": raw.get("adaptive_decision"),
    }


def get_task_review_feedback(db: Session, task) -> Optional[Dict[str, Any]]:
    """Latest review feedback for a task's most recent submission, if reviewed.

    Read-only lookup over persisted submissions/versions/reviews. Keep the
    latest completed review visible while a newer resubmission is awaiting AI
    review, and mark it as previous feedback so it is not confused with the
    pending review.
    """
    from app.models.submission import AIReview, Submission, SubmissionVersion

    latest_version = db.execute(
        select(SubmissionVersion)
        .join(Submission, Submission.id == SubmissionVersion.submission_id)
        .where(
            Submission.task_id == task.id,
            SubmissionVersion.submitted_at.isnot(None),
        )
        .order_by(SubmissionVersion.submitted_at.desc(), SubmissionVersion.version_number.desc())
    ).scalars().first()
    if latest_version is None:
        return None

    version = db.execute(
        select(SubmissionVersion)
        .join(Submission, Submission.id == SubmissionVersion.submission_id)
        .where(
            Submission.task_id == task.id,
            SubmissionVersion.submitted_at.isnot(None),
            SubmissionVersion.status.in_(
                (
                    SubmissionStatus.CHANGES_REQUESTED,
                    SubmissionStatus.REJECTED,
                    SubmissionStatus.APPROVED,
                )
            ),
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
    if review is None:
        return None

    shaped = shape_review_feedback(review.raw_payload or {})
    shaped.update({
        "result": review.result.value if review.result is not None else shaped.get("result"),
        "score": review.score,
        "version_number": version.version_number,
        "reviewed_at": review.created_at.isoformat() if review.created_at else None,
        "is_previous_review": version.id != latest_version.id,
    })
    return shaped


class ReviewService:
    def __init__(self, ai_service: AIService, *, orchestrator=None):
        self.ai_service = ai_service
        self.orchestrator = orchestrator
        self.storage_service = FileStorageService()

    async def _competency_task_focus(self, db, *, simulation, task, review, adaptation):
        """Ask the registered agent for additive task focus; never block baseline generation."""
        if self.orchestrator is None:
            return None, None
        try:
            from app.agents.specialists.adaptation_agent import (
                AdaptationAgentInput,
                invoke_adaptation_agent,
            )
            response = await invoke_adaptation_agent(
                self.orchestrator,
                db,
                payload=AdaptationAgentInput(
                    simulation_id=simulation.id,
                    student_id=review.version.submission.student_id,
                    task_id=task.id,
                    review_id=review.id,
                    technical_adaptation=adaptation,
                ),
            )
            if response.status.value != "succeeded" or not response.result:
                logger.info("Competency adaptation unavailable review_id=%s code=%s",
                            review.id, response.error.code if response.error else "unknown")
                return None, None
            proposed = response.result
            recorded = {
                "status": proposed.get("status"),
                "reason": proposed.get("reason"),
                "focus_competencies": proposed.get("focus_competencies", []),
                "strength_competencies": proposed.get("strength_competencies", []),
                "proposed_task_type": proposed.get("proposed_task_type"),
                "evidence_references": proposed.get("evidence_references", []),
            }
            if proposed.get("status") == "recommended" and proposed.get("focus_competencies"):
                return proposed, recorded
            return None, recorded
        except Exception as exc:
            logger.warning("Competency adaptation fallback review_id=%s error_type=%s",
                           review.id, type(exc).__name__)
            return None, None

    async def review_submission_version(
        self,
        db: Session,
        version: SubmissionVersion,
    ) -> Dict[str, Any]:
        # Build complete context for the review (includes project, task, submission history, current content)
        context = build_review_context(db, version)
        logger.info(
            "Technical review started submission_id=%s version_id=%s task_id=%s",
            version.submission_id, version.id, version.submission.task_id,
        )

        # Idempotent: each submitted version is reviewed exactly once. A
        # retried/duplicate request returns the saved review instead of
        # running the reviewer again (which would regenerate feedback).
        existing_review = db.execute(
            select(AIReview)
            .where(AIReview.version_id == version.id)
            .order_by(AIReview.created_at.desc())
        ).scalars().first()
        if existing_review is not None and existing_review.status == ReviewStatus.COMPLETED:
            return {
                "review_id": str(existing_review.id),
                "version_id": str(version.id),
                "project_id": str(version.submission.task.sprint.project.id),
                "status": existing_review.status.value,
                "result": existing_review.result.value,
                "severity": existing_review.severity.value,
                "summary": existing_review.summary,
                "strengths": existing_review.strengths,
                "risks": existing_review.risks,
                "actions": existing_review.actions,
                "score": existing_review.score,
                "raw": existing_review.raw_payload,
            }
        if existing_review is not None and existing_review.status == ReviewStatus.IN_PROGRESS:
            return {
                "review_id": str(existing_review.id),
                "version_id": str(version.id),
                "project_id": str(version.submission.task.sprint.project.id),
                "status": ReviewStatus.IN_PROGRESS.value,
                "result": None,
                "score": None,
                "summary": "Technical review is in progress.",
            }
        if version.version_number != version.submission.current_version:
            raise ValueError("This submission version is no longer current and cannot affect task progression.")

        # Persist the claim before awaiting the provider so retries can observe
        # the in-flight state and avoid evaluating the same version twice.
        version.status = SubmissionStatus.UNDER_REVIEW
        task = version.submission.task
        validate_transition(task.status, TaskStatus.UNDER_REVIEW)
        task.status = TaskStatus.UNDER_REVIEW
        submission = version.submission
        submission.status = SubmissionStatus.UNDER_REVIEW
        ai_review = existing_review or AIReview(version_id=version.id)
        attempt_history = list((ai_review.raw_payload or {}).get("attempt_history") or [])
        if existing_review is not None and existing_review.status == ReviewStatus.FAILED:
            previous_failure = existing_review.raw_payload or {}
            attempt_history.append({
                "status": "failed",
                "error": previous_failure.get("error"),
                "failure_type": previous_failure.get("failure_type"),
                "message": previous_failure.get("message"),
                "provider": previous_failure.get("provider"),
                "model": previous_failure.get("model"),
                "failed_at": existing_review.updated_at.isoformat() if existing_review.updated_at else None,
            })
        ai_review.status = ReviewStatus.IN_PROGRESS
        ai_review.result = None
        ai_review.score = None
        ai_review.summary = "Technical review is in progress."
        ai_review.raw_payload = {
            "evaluation_source": getattr(self.ai_service, "provider_name", "unconfigured"),
            "attempt_history": attempt_history,
        }
        ai_review.completed_at = None
        db.add(ai_review)
        db.add(version)
        db.add(task)
        db.add(submission)
        db.commit()
        db.refresh(version)
        db.refresh(ai_review)

        # Trigger REVIEW_STARTED event
        project = task.sprint.project
        await trigger_event(
            db,
            project,
            EventType.REVIEW_STARTED,
            f"AI review started for task '{task.title}' (version {version.version_number})",
            {"task_id": str(task.id), "version_id": str(version.id), "version": version.version_number},
        )

        prompt = build_review_prompt(context)
        source = str(context.get("submission_content") or "")
        logger.info(
            "Review evaluator input submission_id=%s version_id=%s student_id=%s task_id=%s "
            "source_chars=%d source_bytes=%d evaluator_input_chars=%d evaluator_input_bytes=%d "
            "prompt_chars=%d prompt_bytes=%d expected_end_marker_present=%s",
            version.submission_id, version.id, version.submission.student_id, task.id,
            len(source), len(source.encode("utf-8")), len(source), len(source.encode("utf-8")),
            len(prompt), len(prompt.encode("utf-8")),
            (version.version_metadata or {}).get("expected_end_marker") in source
            if (version.version_metadata or {}).get("expected_end_marker") else None,
        )
        system_prompt = (
            "You are a senior engineering reviewer. Return only the JSON review. No reasoning."
        )

        review_payload = None
        evaluation_source = "insufficient_evidence"
        if context.get("evidence_level", "full") in ("thin", "none"):
            # Too little (or nothing) to analyze: never let the model fill
            # the vacuum with invented findings. Store a deterministic review
            # that states exactly what evidence must be provided instead.
            review_payload = build_missing_evidence_payload(context)
            review_payload["missing_evidence"] = True
        elif isinstance(self.ai_service, MockAIService):
            raise RuntimeError("AI review provider is not configured; no review decision was made")
        else:
            logger.info("Technical review AI call started version_id=%s", version.id)
            provider = getattr(self.ai_service, "provider_name", "ai_provider")
            model = getattr(self.ai_service, "model", None)
            provider_started = time.perf_counter()
            try:
                raw = await asyncio.wait_for(
                    self.ai_service.generate_json(prompt=prompt, system_prompt=system_prompt),
                    timeout=REVIEW_PROVIDER_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                category = classify_provider_failure(exc)
                logger.warning(
                    "Technical review provider request failed category=%s provider=%s model=%s version_id=%s duration_ms=%d",
                    category, provider, model, version.id,
                    int((time.perf_counter() - provider_started) * 1000),
                )
                raise ReviewEvaluationError(category, provider, model, exc) from exc
            try:
                review_payload = validate_provider_review(raw, context)
                evaluation_source = provider
                logger.info(
                    "Technical review AI call completed version_id=%s provider=%s model=%s duration_ms=%d",
                    version.id, provider, model,
                    int((time.perf_counter() - provider_started) * 1000),
                )
            except Exception as exc:
                if hasattr(exc, "errors"):
                    try:
                        validation_diagnostic = [
                            {key: item.get(key) for key in ("loc", "type", "msg")}
                            for item in exc.errors(include_input=False)
                        ]
                    except TypeError:
                        validation_diagnostic = [{"type": type(exc).__name__}]
                else:
                    validation_diagnostic = {"type": type(exc).__name__}
                logger.warning(
                    "Technical review provider response rejected category=malformed_response provider=%s model=%s version_id=%s",
                    provider, model, version.id,
                )
                logger.debug(
                    "Technical review response validation details version_id=%s errors=%s",
                    version.id,
                    json.dumps(validation_diagnostic, ensure_ascii=True, default=str),
                )
                raise ReviewEvaluationError("malformed_response", provider, model, exc) from exc

        review_payload["evaluation_source"] = evaluation_source
        review_payload["evaluation_metadata"] = {
            "task_id": str(task.id),
            "submission_id": str(version.submission_id),
            "version_id": str(version.id),
            "evaluator_version": "task-grounded-v2",
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }
        review_payload["acceptance_criteria_version"] = context.get("acceptance_criteria_version")
        if attempt_history:
            review_payload["attempt_history"] = attempt_history

        # Create the AIReview record
        ai_review.status = ReviewStatus.COMPLETED
        ai_review.result = ReviewResult(review_payload["result"])
        ai_review.severity = ReviewSeverity(review_payload["severity"])
        ai_review.summary = review_payload["summary"]
        ai_review.strengths = review_payload.get("strengths", [])
        ai_review.risks = review_payload.get("risks", review_payload.get("issues", []))
        ai_review.actions = review_payload.get("actions", review_payload.get("required_changes", []))
        ai_review.score = review_payload["score"]
        ai_review.raw_payload = review_payload
        ai_review.raw_payload["provider"] = evaluation_source
        ai_review.raw_payload["model"] = getattr(self.ai_service, "model", None)
        ai_review.completed_at = datetime.now(timezone.utc)
        db.add(ai_review)
        db.flush()

        # Create initial ReviewHistory entry
        history = ReviewHistory(
            review_id=ai_review.id,
            action="created",
            previous_status=None,
            new_status=ReviewStatus.COMPLETED,
            previous_result=None,
            new_result=ai_review.result,
            changed_by="ai_reviewer",
            notes="Initial AI review completed",
            payload_snapshot=review_payload,
        )
        db.add(history)

        # Update the version status based on the review result
        if ai_review.result == ReviewResult.APPROVED:
            # Approved work waits for explicit completion: the task moves to
            # MANAGER_APPROVAL but is NOT completed here and the next task is
            # NOT unlocked. Only the completion endpoint may mark it COMPLETED
            # (which then progresses). This keeps Submit -> Review -> Approve
            # -> Complete strictly ordered.
            version.status = SubmissionStatus.APPROVED
            validate_transition(task.status, TaskStatus.MANAGER_APPROVAL)
            task.status = TaskStatus.MANAGER_APPROVAL
        elif ai_review.result == ReviewResult.CHANGES_REQUIRED:
            version.status = SubmissionStatus.CHANGES_REQUESTED
            validate_transition(task.status, TaskStatus.CHANGES_REQUESTED)
            task.status = TaskStatus.CHANGES_REQUESTED
        else:  # REJECTED
            version.status = SubmissionStatus.REJECTED
            validate_transition(task.status, TaskStatus.CHANGES_REQUESTED)
            task.status = TaskStatus.CHANGES_REQUESTED

        db.add(version)
        db.add(task)

        # Also update the parent submission status
        submission = version.submission
        submission.status = version.status
        db.add(submission)

        # Update project state stress level
        project = task.sprint.project
        state = db.execute(
            select(ProjectState).where(ProjectState.project_id == project.id)
        ).scalar_one_or_none()
        if state is not None:
            state.stress_level = min(100, state.stress_level + 5)
            db.add(state)

        db.flush()

        adaptation = {
            "status": "not_evaluated",
            "reason": "Insufficient implementation evidence; no technical score or difficulty change was applied.",
            "task_id": str(task.id),
            "submission_id": str(version.submission_id),
            "review_id": str(ai_review.id),
            "evaluation_source": evaluation_source,
        }
        simulation = None
        if context.get("evidence_level") == "full" and evaluation_source not in ("unconfigured", "mock"):
            from app.models.simulation import Simulation
            from app.services.technical_adaptation_service import apply_technical_review_adaptation

            simulation = db.execute(
                select(Simulation).where(
                    Simulation.project_id == project.id,
                    Simulation.student_id == version.submission.student_id,
                )
            ).scalars().first()
            if simulation is not None:
                logger.info("Technical adaptation started review_id=%s task_id=%s", ai_review.id, task.id)
                try:
                    adaptation = await apply_technical_review_adaptation(
                        db,
                        simulation=simulation,
                        task=task,
                        version=version,
                        review=ai_review,
                        review_payload=review_payload,
                        evaluation_source=evaluation_source,
                    )
                except Exception as exc:
                    logger.exception(
                        "Technical review scoring/adaptation failed review_id=%s task_id=%s",
                        ai_review.id, task.id,
                    )
                    raise ReviewEvaluationError(
                        "scoring_error", evaluation_source, getattr(self.ai_service, "model", None), exc
                    ) from exc
                logger.info(
                    "Technical adaptation completed review_id=%s difficulty=%s score=%s",
                    ai_review.id, adaptation.get("next_difficulty"), adaptation.get("technical_score"),
                )
            else:
                adaptation["reason"] = "No student-owned simulation was linked to this project; technical review was stored without changing difficulty."
        ai_review.raw_payload = {
            **(ai_review.raw_payload or {}),
            "technical_evaluation": (
                ai_review.raw_payload.get("technical_evaluation")
                if adaptation.get("status") == "applied"
                else {"status": "not_scored", "source": evaluation_source}
            ),
            "adaptive_decision": adaptation,
        }
        db.add(ai_review)

        # Activity log for AI review completion
        from app.services.activity_log_service import log_activity
        log_activity(
            db,
            company_id=project.company_id,
            actor="ai_reviewer",
            action="ai_review_completed",
            detail=f"AI review completed for task '{task.title}' (version {version.version_number}): {ai_review.result.value}",
            task_id=str(task.id),
            review_id=str(ai_review.id),
            result=ai_review.result.value,
        )

        # Trigger AI_REVIEW_COMPLETED event
        await trigger_event(
            db,
            project,
            EventType.AI_REVIEW_COMPLETED,
            f"AI review completed for task '{task.title}': {ai_review.result.value}",
            {"task_id": str(task.id), "review_id": str(ai_review.id), "result": ai_review.result.value, "score": ai_review.score},
        )

        # If CHANGES_REQUESTED or REJECTED, trigger CHANGES_REQUESTED event
        if ai_review.result in (ReviewResult.CHANGES_REQUIRED, ReviewResult.REJECTED):
            await trigger_event(
                db,
                project,
                EventType.CHANGES_REQUESTED,
                f"Changes requested for task '{task.title}': {ai_review.result.value}",
                {"task_id": str(task.id), "review_id": str(ai_review.id), "result": ai_review.result.value},
            )

        # Generate a contextual follow-up only when this task has no saved
        # successor. The review service may be retried, so the predecessor
        # marker is the idempotency key. Current-task ownership remains with
        # progression_service, which consumes this ordered path entry.
        if ai_review.result == ReviewResult.APPROVED and simulation is not None:
            from app.models.task_path import SimulationTaskPath
            from app.models.sprint import Sprint
            from app.services.generators.dynamic_task_generator import generate_dynamic_task
            from app.services.simulation.progression_service import calculate_sprint_progress, _activate_sprint

            db.execute(
                select(Simulation).where(Simulation.id == simulation.id).with_for_update()
            ).scalar_one()
            prior_generated = db.execute(
                select(SimulationTaskPath.id)
                .join(Task, Task.id == SimulationTaskPath.task_id)
                .where(
                    SimulationTaskPath.simulation_id == simulation.id,
                    Task.task_metadata["previous_task_id"].as_string() == str(task.id),
                )
            ).first()
            unfinished_successor = db.execute(
                select(SimulationTaskPath.id)
                .join(Task, Task.id == SimulationTaskPath.task_id)
                .where(
                    SimulationTaskPath.simulation_id == simulation.id,
                    Task.id != task.id,
                    Task.status != TaskStatus.COMPLETED,
                )
            ).first()
            current_sprint = db.get(Sprint, task.sprint_id)
            if not prior_generated and not unfinished_successor and current_sprint:
                sprint_progress = calculate_sprint_progress(db, simulation, current_sprint.id)
                unfinished_other_task = db.execute(
                    select(SimulationTaskPath.id)
                    .join(Task, Task.id == SimulationTaskPath.task_id)
                    .where(
                        SimulationTaskPath.simulation_id == simulation.id,
                        Task.sprint_id == current_sprint.id,
                        Task.id != task.id,
                        Task.status != TaskStatus.COMPLETED,
                    )
                ).first()
                # Reviews precede manager completion. Plan the next sprint
                # once the approved task is the only remaining sprint task.
                objective_complete_after_approval = (
                    sprint_progress["objective_complete"] or unfinished_other_task is None
                )
                from app.core.config import settings
                project_sprints = db.execute(
                    select(Sprint)
                    .where(Sprint.project_id == current_sprint.project_id)
                    .order_by(Sprint.sprint_number.asc())
                ).scalars().all()
                candidate_sprints = _candidate_followup_sprints(
                    current_sprint, project_sprints, objective_complete_after_approval
                )
                target_sprint = None
                for candidate in candidate_sprints:
                    candidate_tasks = db.execute(
                        select(Task).where(Task.sprint_id == candidate.id)
                    ).scalars().all()
                    dynamic_count = sum(
                        1 for candidate_task in candidate_tasks
                        if (candidate_task.task_metadata or {}).get("dynamic_generation") is True
                        and (candidate_task.task_metadata or {}).get("generation_simulation_id") == str(simulation.id)
                    )
                    configured_limit = int(
                        (candidate.sprint_metadata or {}).get("dynamic_task_limit")
                        or settings.MAX_DYNAMIC_TASKS_PER_SPRINT
                    )
                    if candidate.status not in (SprintStatus.COMPLETED, SprintStatus.BLOCKED) and dynamic_count < configured_limit:
                        target_sprint = candidate
                        break
                if target_sprint and target_sprint.id != current_sprint.id:
                    _activate_sprint(db, target_sprint.id)
                if target_sprint:
                    try:
                        competency_focus, recorded_focus = await self._competency_task_focus(
                            db, simulation=simulation, task=task, review=ai_review,
                            adaptation=adaptation,
                        )
                        if recorded_focus is not None:
                            adaptation["competency_adaptation"] = recorded_focus
                        next_task = await generate_dynamic_task(
                            db, simulation, target_sprint, version.submission.student_id, task,
                            competency_focus=competency_focus,
                        )
                        max_seq = db.execute(
                            select(SimulationTaskPath.sequence)
                            .where(SimulationTaskPath.simulation_id == simulation.id)
                            .order_by(SimulationTaskPath.sequence.desc())
                        ).scalars().first() or 0
                        db.add(SimulationTaskPath(
                            simulation_id=simulation.id,
                            task_id=next_task.id,
                            sequence=max_seq + 1,
                            status=TaskStatus.BACKLOG,
                        ))
                        db.flush()
                    except Exception as exc:
                        logger.warning(
                            "Failed to generate follow-up task review_id=%s task_id=%s: %s",
                            ai_review.id, task.id, exc, exc_info=True,
                        )

        db.commit()
        logger.info(
            "Technical review persisted review_id=%s version_id=%s status=%s evaluation_source=%s",
            ai_review.id, version.id, ai_review.status.value, evaluation_source,
        )
        db.refresh(version)
        db.refresh(task)
        db.refresh(submission)
        db.refresh(ai_review)

        return {
            "review_id": str(ai_review.id),
            "version_id": str(version.id),
            "project_id": str(project.id),
            "status": ai_review.status.value,
            "result": ai_review.result.value,
            "severity": ai_review.severity.value,
            "summary": ai_review.summary,
            "strengths": ai_review.strengths,
            "risks": ai_review.risks,
            "actions": ai_review.actions,
            "score": ai_review.score,
            "raw": review_payload,
            "technical_evaluation": ai_review.raw_payload.get("technical_evaluation"),
            "adaptive_decision": adaptation,
        }

    async def review_by_manager(
        self,
        db: Session,
        version: SubmissionVersion,
    ) -> Dict[str, Any]:
        """
        AI Engineering Manager reviews the AI evaluation and makes final decision.
        This is the second layer of review - only proceeds if AI review exists.
        """
        project = version.submission.task.sprint.project
        task = version.submission.task

        # Get the latest AI review for this version
        ai_review = db.execute(
            select(AIReview)
            .where(AIReview.version_id == version.id)
            .order_by(AIReview.created_at.desc())
        ).scalar_one_or_none()

        if not ai_review:
            raise ValueError("No AI review found for this version. Run AI review first.")

        prior_manager_review = db.execute(
            select(ReviewHistory)
            .where(
                ReviewHistory.review_id == ai_review.id,
                ReviewHistory.action == "manager_review",
            )
            .order_by(ReviewHistory.created_at.desc())
        ).scalars().first()
        if prior_manager_review is not None:
            decision = prior_manager_review.payload_snapshot or {}
            manager_result = prior_manager_review.new_result or ai_review.result
            next_task = None
            # A previous manager-review request may have committed approval but
            # failed while progressing (for example, transient task generation
            # failure). Replaying the structured decision must also retry
            # progression; progress_to_next_task is serialized and idempotent.
            if manager_result == ReviewResult.APPROVED and task.status == TaskStatus.COMPLETED:
                from app.models.simulation import Simulation
                from app.services.simulation.progression_service import progress_to_next_task
                simulation = db.execute(
                    select(Simulation).where(Simulation.current_task_id == task.id)
                ).scalar_one_or_none()
                if simulation is not None:
                    next_task = await progress_to_next_task(db, simulation, task, "ai_manager")
                    db.commit()
            return {
                "review_id": str(ai_review.id),
                "version_id": str(version.id),
                "project_id": str(project.id),
                "ai_result": prior_manager_review.previous_result.value if prior_manager_review.previous_result else ai_review.result.value,
                "manager_result": manager_result.value,
                "manager_severity": decision.get("severity", ai_review.severity.value),
                "manager_summary": decision.get("summary", ai_review.summary),
                "manager_feedback": decision.get("feedback", ""),
                "final_task_status": task.status.value,
                "next_task": ({
                    "task_id": str(next_task.id),
                    "title": next_task.title,
                    "description": next_task.description,
                    "status": next_task.status.value,
                } if next_task else None),
            }

        if version.version_number != version.submission.current_version:
            raise ValueError("This submission version is no longer current and cannot affect task progression.")

        # Get manager and project state
        manager = db.execute(
            select(Manager).where(Manager.company_id == project.company_id)
        ).scalar_one_or_none()

        state = db.execute(
            select(ProjectState).where(ProjectState.project_id == project.id)
        ).scalar_one_or_none()

        if not manager or not state:
            raise ValueError("Manager or project state not found")

        manager_decision = _manager_decision_from_technical_review(ai_review)

        # Create ReviewHistory entry for manager review
        history = ReviewHistory(
            review_id=ai_review.id,
            action="manager_review",
            previous_status=ReviewStatus.COMPLETED,
            new_status=ReviewStatus.COMPLETED,
            previous_result=ai_review.result,
            new_result=ReviewResult(manager_decision["decision"]),
            changed_by="ai_manager",
            notes=manager_decision.get("feedback", "Manager review completed"),
            payload_snapshot=manager_decision,
        )
        db.add(history)

        # Update AI review with manager's decision (overrides AI if different)
        final_result = ReviewResult(manager_decision["decision"])
        ai_review.result = final_result
        ai_review.severity = ReviewSeverity(manager_decision["severity"])
        ai_review.summary = manager_decision["summary"]
        ai_review.raw_payload = {**ai_review.raw_payload, "manager_review": manager_decision}
        db.add(ai_review)

        # Update version and task status based on FINAL manager decision
        if final_result == ReviewResult.APPROVED:
            version.status = SubmissionStatus.APPROVED
            validate_transition(task.status, TaskStatus.COMPLETED)
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc)
        elif final_result == ReviewResult.CHANGES_REQUIRED:
            version.status = SubmissionStatus.CHANGES_REQUESTED
            validate_transition(task.status, TaskStatus.CHANGES_REQUESTED)
            task.status = TaskStatus.CHANGES_REQUESTED
        else:  # REJECTED
            version.status = SubmissionStatus.REJECTED
            validate_transition(task.status, TaskStatus.CHANGES_REQUESTED)
            task.status = TaskStatus.CHANGES_REQUESTED

        db.add(version)
        db.add(task)

        # Update parent submission status
        submission = version.submission
        submission.status = version.status
        db.add(submission)

        db.flush()

        # If task was approved and completed, trigger simulation progression
        if final_result == ReviewResult.APPROVED:
            from app.models.simulation import Simulation
            from app.services.simulation.progression_service import progress_to_next_task
            simulation = db.execute(
                select(Simulation).where(Simulation.current_task_id == task.id)
            ).scalar_one_or_none()
            if simulation:
                next_task = await progress_to_next_task(db, simulation, task, "ai_manager")
            else:
                next_task = None
        else:
            next_task = None

        # Create notifications, events, activity logs for manager review completion
        company_id = project.company_id
        
        # Activity log
        from app.services.activity_log_service import log_activity
        log_activity(
            db,
            company_id=company_id,
            actor="ai_manager",
            action="manager_review_completed",
            detail=f"Manager review completed for task '{task.title}' (version {version.version_number}): {final_result.value}",
            task_id=str(task.id),
            review_id=str(ai_review.id),
            result=final_result.value,
        )
        
        # Notification based on decision
        if final_result == ReviewResult.APPROVED:
            notify(
                db,
                company_id=company_id,
                notification_type="task_approved",
                message=f"Task '{task.title}' approved by manager. Task completed!",
            )
            # Trigger MANAGER_APPROVED event
            await trigger_event(
                db,
                project,
                EventType.MANAGER_APPROVED,
                f"Manager approved submission for '{task.title}'",
                {"task_id": str(task.id), "review_id": str(ai_review.id), "result": final_result.value},
            )
            # Trigger TASK_COMPLETED event
            await trigger_event(
                db,
                project,
                EventType.TASK_COMPLETED,
                f"Task '{task.title}' completed after manager approval",
                {"task_id": str(task.id), "review_id": str(ai_review.id)},
            )
        else:
            notify(
                db,
                company_id=company_id,
                notification_type="changes_requested",
                message=f"Changes requested for '{task.title}': {manager_decision.get('feedback', '')}",
            )
            await trigger_event(
                db,
                project,
                EventType.CHANGES_REQUESTED,
                f"Manager requested changes for '{task.title}'",
                {"task_id": str(task.id), "review_id": str(ai_review.id), "result": final_result.value},
            )

        db.commit()
        db.refresh(version)
        db.refresh(task)
        db.refresh(submission)
        db.refresh(ai_review)

        return {
            "review_id": str(ai_review.id),
            "version_id": str(version.id),
            "project_id": str(project.id),
            "ai_result": ai_review.result.value,
            "manager_result": final_result.value,
            "manager_severity": manager_decision["severity"],
            "manager_summary": manager_decision["summary"],
            "manager_feedback": manager_decision.get("feedback", ""),
            "final_task_status": task.status.value,
            "next_task": ({
                "task_id": str(next_task.id),
                "title": next_task.title,
                "description": next_task.description,
                "status": next_task.status.value,
            } if next_task else None),
        }

    def _coerce_review_payload(self, raw: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": str(raw.get("status", ReviewStatus.COMPLETED.value)).lower(),
            "severity": str(raw.get("severity", ReviewSeverity.WARNING.value)).lower(),
            "summary": str(raw.get("summary", "Review completed successfully.")),
            "strengths": [str(item) for item in raw.get("strengths", []) or []],
            "risks": [str(item) for item in raw.get("risks", []) or []],
            "actions": [str(item) for item in raw.get("actions", []) or []],
            "score": int(raw.get("score", 78)),
            "result": str(raw.get("result", ReviewResult.CHANGES_REQUIRED.value)).lower(),
        }

    def get_review_history(self, db: Session, version_id: uuid.UUID) -> List[Dict[str, Any]]:
        version = db.get(SubmissionVersion, version_id)
        if not version:
            return []

        reviews = db.execute(
            select(AIReview).where(AIReview.version_id == version_id)
        ).scalars().all()

        result = []
        for review in reviews:
            history = db.execute(
                select(ReviewHistory).where(ReviewHistory.review_id == review.id)
            ).scalars().all()

            result.append({
                "review_id": str(review.id),
                "version_id": str(review.version_id),
                "status": review.status.value,
                "result": review.result.value,
                "severity": review.severity.value,
                "summary": review.summary,
                "strengths": review.strengths,
                "risks": review.risks,
                "actions": review.actions,
                "score": review.score,
                "raw_payload": review.raw_payload,
                "completed_at": review.completed_at.isoformat() if review.completed_at else None,
                "created_at": review.created_at.isoformat(),
                "history": [
                    {
                        "action": h.action,
                        "previous_status": h.previous_status.value if h.previous_status else None,
                        "new_status": h.new_status.value,
                        "previous_result": h.previous_result.value if h.previous_result else None,
                        "new_result": h.new_result.value,
                        "changed_by": h.changed_by,
                        "notes": h.notes,
                        "payload_snapshot": h.payload_snapshot,
                        "created_at": h.created_at.isoformat(),
                    }
                    for h in history
                ],
            })
        return result


async def _apply_review_to_competencies(db: Session, student_id: str, review_payload: Dict[str, Any], project_id) -> None:
    """Map technical review scores into the existing competency profile."""
    from app.models.simulation import Simulation
    from app.services.intelligence_service import (
        calculate_readiness,
        create_feedback_report,
        get_or_create_profile,
        update_competencies,
    )

    overall = float(review_payload.get("score") or 0)
    quality = float((review_payload.get("code_quality") or {}).get("score", overall))
    testing = float((review_payload.get("testing") or {}).get("score", overall))
    security = float((review_payload.get("security") or {}).get("score", overall))
    performance = float((review_payload.get("performance") or {}).get("score", overall))
    mapped = {
        "technical_knowledge": (quality + testing) / 2,
        "problem_solving": overall,
        "critical_thinking": security,
        "decision_making": performance,
    }
    profile = get_or_create_profile(db, student_id)
    update_competencies(db, profile, mapped)
    simulation = db.execute(select(Simulation).where(Simulation.project_id == project_id)).scalars().first()
    simulation_id = simulation.id if simulation else None
    await create_feedback_report(db, student_id, simulation_id)
    calculate_readiness(db, student_id, simulation_id)
