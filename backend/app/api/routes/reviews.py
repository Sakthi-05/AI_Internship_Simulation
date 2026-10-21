from __future__ import annotations

import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser, get_current_user
from app.models.company import Company
from app.models.enums import ReviewResult, ReviewSeverity, ReviewStatus, SubmissionStatus, TaskStatus
from app.models.project import Project
from app.models.sprint import Sprint
from app.models.submission import AIReview, Submission, SubmissionVersion
from app.models.task import Task
from app.services.ai.ai_service import AIService, get_ai_service
from app.services.ai.review_service import ReviewEvaluationError, ReviewService

router = APIRouter(prefix="/api/projects/{project_id}/reviews", tags=["Reviews"])
logger = logging.getLogger(__name__)
# Must exceed the 68s provider budget plus persistence work, so a valid retry
# never races a still-running request for the same submission version.
REVIEW_STALE_CLAIM_SECONDS = 90


def _get_owned_project(project_id: uuid.UUID, db: Session, user: CurrentUser) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    company = db.get(Company, project.company_id)
    if company is None or company.student_id != user.student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your project")
    return project


def _get_owned_version(version_id: uuid.UUID, db: Session, user: CurrentUser) -> SubmissionVersion:
    version = db.get(SubmissionVersion, version_id)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission version not found")
    
    task = db.get(Task, version.submission.task_id)
    sprint = db.get(Sprint, task.sprint_id)
    project = db.get(Project, sprint.project_id)
    company = db.get(Company, project.company_id)
    
    if company is None or company.student_id != user.student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your submission")
    return version


@router.post("/versions/{version_id}", status_code=status.HTTP_201_CREATED)
async def create_review(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    ai_service: AIService = Depends(get_ai_service),
):
    project = _get_owned_project(project_id, db, user)
    version = _get_owned_version(version_id, db, user)
    logger.info(
        "Review request received project_id=%s version_id=%s submission_id=%s",
        project_id, version_id, version.submission_id,
    )
    # Serialize duplicate review requests with current-task recovery. If the
    # request waited for an in-flight review, the completed review below is
    # returned idempotently instead of leaving UNDER_REVIEW or running twice.
    db.refresh(version, with_for_update=True)

    # Verify version belongs to this project
    task = db.get(Task, version.submission.task_id)
    sprint = db.get(Sprint, task.sprint_id)
    if sprint.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Version does not belong to this project")

    existing_review = db.execute(
        select(AIReview)
        .where(AIReview.version_id == version.id)
        .order_by(AIReview.created_at.desc())
    ).scalars().first()
    if existing_review is not None and existing_review.status == ReviewStatus.IN_PROGRESS:
        started_at = existing_review.updated_at or existing_review.created_at
        if started_at is not None and started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if started_at is None or started_at > datetime.now(timezone.utc) - timedelta(seconds=REVIEW_STALE_CLAIM_SECONDS):
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={
                "review_id": str(existing_review.id),
                "version_id": str(version.id),
                "project_id": str(project.id),
                "status": ReviewStatus.IN_PROGRESS.value,
                "result": None,
                "score": None,
                "summary": "Technical review is in progress.",
            })
        # Recover a stale claim left by a terminated worker. The same review
        # row is retried; no duplicate final review is created.
        existing_review.status = ReviewStatus.FAILED
        existing_review.result = None
        existing_review.score = None
        existing_review.summary = "Technical review did not finish. Retry is available."
        existing_review.raw_payload = {
            "error": "stale_review_claim",
            "message": "The previous review worker stopped before persisting a result.",
            "provider": getattr(ai_service, "provider_name", "unconfigured"),
            "attempt_history": (existing_review.raw_payload or {}).get("attempt_history", []),
        }
        db.add(existing_review)
    # Only allow a new review for submitted work. Previously reviewed versions
    # may be retried as an idempotent read of their persisted result.
    if version.status != SubmissionStatus.SUBMITTED and existing_review is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Can only review submitted versions. Current status: {version.status.value}",
        )

    service = ReviewService(ai_service)
    try:
        result = await service.review_submission_version(db, version)
        return result
    except Exception as exc:
        # The submission has already committed. Make unexpected failures
        # visible and retryable instead of leaving an untracked UNDER_REVIEW
        # task. No score or review outcome is invented here.
        db.rollback()
        logger.error(
            "Technical review failed project_id=%s version_id=%s submission_id=%s category=%s exception_type=%s",
            project_id, version_id, version.submission_id,
            exc.category if isinstance(exc, ReviewEvaluationError) else "review_processing_failed",
            type(exc.__cause__).__name__ if isinstance(exc, ReviewEvaluationError) and exc.__cause__ else type(exc).__name__,
        )
        failed_version = db.get(SubmissionVersion, version_id)
        failed_task = db.get(Task, failed_version.submission.task_id) if failed_version else None
        review = db.execute(
            select(AIReview).where(AIReview.version_id == version_id)
        ).scalars().first()
        if review is not None and review.status == ReviewStatus.COMPLETED:
            return {
                "review_id": str(review.id),
                "version_id": str(version_id),
                "project_id": str(project_id),
                "status": review.status.value,
                "result": review.result.value if review.result else None,
                "severity": review.severity.value if review.severity else None,
                "summary": review.summary,
                "score": review.score,
                "raw": review.raw_payload,
            }
        if review is None:
            review = AIReview(version_id=version_id)
        review.status = ReviewStatus.FAILED
        review.result = None
        review.severity = ReviewSeverity.ERROR
        review.summary = "Technical review failed. Your submission is saved and can be reviewed again."
        review.score = None
        previous_payload = review.raw_payload or {}
        if isinstance(exc, ReviewEvaluationError):
            failure_category = exc.category
            failure_message = {
                "provider_timeout": "AI provider timed out. Your submission is saved and no score was assigned; retry the review.",
                "provider_rate_limited": "AI provider is rate limited. Your submission is saved and no score was assigned; retry shortly.",
                "provider_error": "AI provider request failed. No score was assigned.",
                "malformed_response": "AI provider returned an invalid evaluation response. Your submission is saved and no score was assigned; retry the review.",
                "scoring_error": "The evaluation could not be scored or applied. No result was persisted; retry is available.",
            }.get(exc.category, "Technical review could not complete. No score was assigned.")
            failed_provider = exc.provider
            failed_model = exc.model
        else:
            failure_category = "review_processing_failed"
            failure_message = "Technical review could not complete. No score was assigned."
            failed_provider = getattr(ai_service, "provider_name", "unconfigured")
            failed_model = getattr(ai_service, "model", None)
        review.raw_payload = {
            "error": failure_category,
            "message": failure_message,
            "failure_type": type(exc.__cause__).__name__ if isinstance(exc, ReviewEvaluationError) and exc.__cause__ else type(exc).__name__,
            "provider": failed_provider,
            "model": failed_model,
            "attempt_history": previous_payload.get("attempt_history", []),
        }
        db.add(review)
        if failed_version is not None:
            failed_version.status = SubmissionStatus.UNDER_REVIEW
            db.add(failed_version)
            failed_version.submission.status = SubmissionStatus.UNDER_REVIEW
            db.add(failed_version.submission)
        if failed_task is not None:
            failed_task.status = TaskStatus.UNDER_REVIEW
            db.add(failed_task)
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Could not persist technical review failure version_id=%s", version_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=failure_message,
        ) from exc


@router.get("/versions/{version_id}")
def get_review_status(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, user)
    version = _get_owned_version(version_id, db, user)
    task = db.get(Task, version.submission.task_id)
    sprint = db.get(Sprint, task.sprint_id)
    if sprint.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Version does not belong to this project")
    review = db.execute(
        select(AIReview).where(AIReview.version_id == version.id).order_by(AIReview.created_at.desc())
    ).scalars().first()
    if review is None:
        return {"version_id": str(version.id), "status": "not_started", "score": None}
    return {
        "review_id": str(review.id),
        "version_id": str(version.id),
        "status": review.status.value,
        "result": review.result.value if review.result else None,
        "severity": review.severity.value if review.severity else None,
        "summary": review.summary,
        "score": review.score,
        "error": (review.raw_payload or {}).get("message") if review.status == ReviewStatus.FAILED else None,
        "raw": review.raw_payload,
    }


@router.post("/versions/{version_id}/manager-review", status_code=status.HTTP_201_CREATED)
async def create_manager_review(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    ai_service: AIService = Depends(get_ai_service),
):
    project = _get_owned_project(project_id, db, user)
    version = _get_owned_version(version_id, db, user)
    db.refresh(version, with_for_update=True)

    sprint = db.get(Sprint, version.submission.task.sprint_id)
    if sprint.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Version does not belong to this project")

    # Only allow manager review after AI review (version should be in a reviewed state)
    if version.status not in (SubmissionStatus.APPROVED, SubmissionStatus.CHANGES_REQUESTED, SubmissionStatus.REJECTED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Manager review requires completed AI review. Current status: {version.status.value}",
        )

    technical_review = db.execute(
        select(AIReview).where(AIReview.version_id == version.id)
        .order_by(AIReview.created_at.desc())
    ).scalars().first()
    raw = technical_review.raw_payload or {} if technical_review else {}
    if (
        technical_review is None
        or technical_review.status != ReviewStatus.COMPLETED
        or raw.get("evaluation_source") in (None, "mock", "mock_fallback", "mock_fallback_after_provider_error", "insufficient_evidence", "unconfigured")
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A completed provider evaluation is required before manager approval.",
        )
    expected = [str(value).strip() for value in (version.submission.task.acceptance_criteria or []) if str(value).strip()]
    criteria = raw.get("acceptance_criteria") or []
    if expected and (
        len(criteria) != len(expected)
        or {str(item.get("criterion", "")).strip() for item in criteria if isinstance(item, dict)} != set(expected)
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Mandatory acceptance criteria are not all satisfied.")
    if expected and technical_review.result == ReviewResult.APPROVED and not all(
        isinstance(item, dict) and item.get("met") is True for item in criteria
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Mandatory acceptance criteria are not all satisfied.")

    service = ReviewService(ai_service)
    return await service.review_by_manager(db, version)


@router.get("/versions/{version_id}/history", response_model=List[Dict[str, Any]])
def get_review_history(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    ai_service: AIService = Depends(get_ai_service),
):
    project = _get_owned_project(project_id, db, user)
    version = _get_owned_version(version_id, db, user)

    sprint = db.get(Sprint, version.submission.task.sprint_id)
    if sprint.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Version does not belong to this project")

    service = ReviewService(ai_service)
    return service.get_review_history(db, version_id)
