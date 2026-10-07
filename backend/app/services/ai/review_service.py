from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import ReviewResult, ReviewStatus, ReviewSeverity, SubmissionStatus, TaskStatus, EventType
from app.models.manager import Manager
from app.models.project import Project
from app.models.project_state import ProjectState
from app.models.submission import AIReview, ReviewHistory, SubmissionVersion
from app.models.task import Task
from app.schemas.review import AIManagerDecision, AIReviewResponse
from app.services.ai.ai_service import AIService, MockAIService
from app.services.ai.prompt_builder import build_compact_review_prompt, build_review_prompt
from app.services.content_extraction import extract_all_submission_files
from app.services.context.context_builder import build_review_context
from app.services.engines.events_engine import trigger_event
from app.services.file_storage import FileStorageService
from app.services.notification_service import notify
from app.services.engines.task_state_machine import validate_transition


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
    try:
        return AIManagerDecision.model_validate({
            "decision": decision,
            "severity": severity,
            "summary": summary[:300],
            "feedback": feedback[:1000],
        }).model_dump()
    except Exception:
        from app.services.ai.ai_service import MockAIService
        return AIManagerDecision.model_validate(MockAIService()._generate_mock_manager_review()).model_dump()


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
    criteria = [str(item) for item in (context.get("acceptance_criteria") or [])[:8] if str(item).strip()]
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
        "score": 30 if thin else 25,
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
        "code_quality": {"score": 30, "feedback": "Cannot assess code quality without submitted code."},
        "testing": {
            "score": 30,
            "feedback": "No tests or verification evidence were submitted. Run the relevant tests and include the results.",
        },
        "security": {"score": 30, "feedback": "Cannot assess security without submitted code."},
        "performance": {"score": 30, "feedback": "Cannot assess performance without submitted code."},
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
    def __init__(self, ai_service: AIService):
        self.ai_service = ai_service
        self.storage_service = FileStorageService()

    async def review_submission_version(
        self,
        db: Session,
        version: SubmissionVersion,
    ) -> Dict[str, Any]:
        # Build complete context for the review (includes project, task, submission history, current content)
        context = build_review_context(db, version)

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

        # Set version and task to UNDER_REVIEW when review starts
        version.status = SubmissionStatus.UNDER_REVIEW
        task = version.submission.task
        validate_transition(task.status, TaskStatus.UNDER_REVIEW)
        task.status = TaskStatus.UNDER_REVIEW
        db.add(version)
        db.add(task)
        db.flush()

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
        system_prompt = (
            "You are a senior engineering reviewer. Return only the JSON review. No reasoning."
        )

        review_payload = None
        if context.get("evidence_level", "full") in ("thin", "none"):
            # Too little (or nothing) to analyze: never let the model fill
            # the vacuum with invented findings. Store a deterministic review
            # that states exactly what evidence must be provided instead.
            review_payload = build_missing_evidence_payload(context)
            review_payload["missing_evidence"] = True
        elif isinstance(self.ai_service, MockAIService):
            # Make local decisions stable per task attempt rather than
            # dependent on unrelated reviews processed earlier in this app.
            fallback = MockAIService()
            fallback._technical_review_calls = max(0, version.version_number - 1)
            review_payload = AIReviewResponse.model_validate(
                fallback._generate_mock_review()
            ).model_dump()
        else:
            prompts = [prompt, build_compact_review_prompt(context)]
            for current_prompt in prompts:
                try:
                    raw = await self.ai_service.generate_json(prompt=current_prompt, system_prompt=system_prompt)
                    review_payload = AIReviewResponse.model_validate(raw).model_dump()
                    break
                except Exception:
                    review_payload = None
            if review_payload is None:
                # A provider outage must not leave the committed task in
                # UNDER_REVIEW forever. Use the same deterministic local
                # decision sequence as no-key development mode: the first
                # attempt requests changes; a resubmission is approved.
                fallback = MockAIService()
                fallback._technical_review_calls = max(0, version.version_number - 1)
                review_payload = AIReviewResponse.model_validate(
                    fallback._generate_mock_review()
                ).model_dump()

        # Create the AIReview record
        ai_review = AIReview(
            version_id=version.id,
            status=ReviewStatus.COMPLETED,
            result=ReviewResult(review_payload["result"]),
            severity=ReviewSeverity(review_payload["severity"]),
            summary=review_payload["summary"],
            strengths=review_payload.get("strengths", []),
            risks=review_payload.get("risks", review_payload.get("issues", [])),
            actions=review_payload.get("actions", review_payload.get("required_changes", [])),
            score=review_payload["score"],
            raw_payload=review_payload,
            completed_at=None,  # will be set after commit
        )
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

        await _apply_review_to_competencies(db, version.submission.student_id, review_payload, project.id)

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

        db.commit()
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
            return {
                "review_id": str(ai_review.id),
                "version_id": str(version.id),
                "project_id": str(project.id),
                "ai_result": prior_manager_review.previous_result.value if prior_manager_review.previous_result else ai_review.result.value,
                "manager_result": prior_manager_review.new_result.value if prior_manager_review.new_result else ai_review.result.value,
                "manager_severity": decision.get("severity", ai_review.severity.value),
                "manager_summary": decision.get("summary", ai_review.summary),
                "manager_feedback": decision.get("feedback", ""),
                "final_task_status": task.status.value,
            }

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
                await progress_to_next_task(db, simulation, task, "ai_manager")

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
