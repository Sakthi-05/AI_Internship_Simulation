from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import SubmissionStatus, TaskStatus
from app.models.project import Project
from app.models.project_state import ProjectState
from app.models.submission import Submission, SubmissionVersion
from app.models.task import Task
from app.services.file_storage import FileStorageService, FileValidationError
from app.services.engines.task_state_machine import InvalidTransitionError, validate_transition


class SubmissionService:
    def __init__(self, storage_service: Optional[FileStorageService] = None):
        self.storage_service = storage_service or FileStorageService()

    def store_submission(
        self,
        db: Session,
        project: Project,
        task: Task,
        files: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        metadata = dict(metadata or {})
        from app.services.criteria_version_service import criteria_identity

        metadata["acceptance_criteria_version"] = criteria_identity(db, task)
        files = files or []

        try:
            validate_transition(task.status, TaskStatus.SUBMITTED)
        except InvalidTransitionError as exc:
            raise ValueError(str(exc)) from exc
        if not files and not self._has_submission_evidence(metadata):
            raise ValueError("Submit at least one file or meaningful work evidence.")

        # Find or create the submission for this task
        submission = self._get_or_create_submission(db, task)

        # Determine the next version number
        next_version = submission.current_version + 1

        # Store files using the file storage service
        stored_files: List[Dict[str, Any]] = []
        if files:
            try:
                # Convert file data format for storage service
                file_objects = []
                for file_data in files:
                    # Handle file-like objects, bytes, and string content
                    file_obj = file_data.get("file")
                    if file_obj is None:
                        content = file_data.get("content")
                        if content is not None:
                            if isinstance(content, str):
                                content = content.encode("utf-8")
                            if isinstance(content, bytes):
                                import io
                                file_obj = io.BytesIO(content)
                    
                    if file_obj:
                        file_objects.append({
                            "file": file_obj,
                            "filename": file_data.get("name") or file_data.get("filename") or "unnamed",
                            "content_type": file_data.get("content_type"),
                        })
                
                if file_objects:
                    stored_files = self.storage_service.store_files(
                        files=file_objects,
                        project_id=project.id,
                        submission_id=submission.id,
                        version=next_version,
                    )
            except FileValidationError as e:
                raise FileValidationError(f"File validation failed: {e}")

        # Create the new version
        version = SubmissionVersion(
            submission_id=submission.id,
            version_number=next_version,
            files=stored_files,
            version_metadata=metadata,
            status=SubmissionStatus.SUBMITTED,
            submitted_at=datetime.now(timezone.utc),
        )
        db.add(version)

        # Update submission
        submission.current_version = next_version
        submission.status = SubmissionStatus.SUBMITTED
        db.add(submission)

        task.status = TaskStatus.SUBMITTED
        db.add(task)

        # Update project state stress level
        state = db.execute(
            select(ProjectState).where(ProjectState.project_id == project.id)
        ).scalar_one_or_none()
        if state is not None:
            state.stress_level = min(100, state.stress_level + 3)
            db.add(state)

        db.flush()

        return {
            "submission_id": str(submission.id),
            "version_id": str(version.id),
            "task_id": str(task.id),
            "project_id": str(project.id),
            "version": next_version,
            "stored_files": stored_files,
            "metadata": metadata,
            "status": submission.status.value,
            "created_at": version.created_at.isoformat(),
        }


    @staticmethod
    def _has_submission_evidence(metadata: Dict[str, Any]) -> bool:
        """Accept textual or URL evidence but reject an empty metadata object."""
        evidence_keys = {"response", "url", "deliverable_url", "evidence", "code", "notes"}
        return any(
            key in evidence_keys and isinstance(value, str) and value.strip()
            for key, value in metadata.items()
        )

    def _get_or_create_submission(self, db: Session, task: Task) -> Submission:
        existing = db.execute(
            select(Submission).where(Submission.task_id == task.id)
        ).scalar_one_or_none()

        if existing:
            return existing

        submission = Submission(
            task_id=task.id,
            student_id=task.sprint.project.company.student_id,
            status=SubmissionStatus.DRAFT,
            current_version=0,
        )
        db.add(submission)
        db.flush()
        return submission

    def get_submission_history(self, db: Session, project: Project) -> List[Dict[str, Any]]:
        # Get all tasks for this project
        tasks = db.execute(
            select(Task)
            .join(Task.sprint)
            .where(Task.sprint.has(project_id=project.id))
        ).scalars().all()

        if not tasks:
            return []

        task_ids = [t.id for t in tasks]

        # Get all submissions for these tasks
        submissions = db.execute(
            select(Submission).where(Submission.task_id.in_(task_ids))
        ).scalars().all()

        result: List[Dict[str, Any]] = []
        for submission in submissions:
            versions = db.execute(
                select(SubmissionVersion)
                .where(SubmissionVersion.submission_id == submission.id)
                .order_by(SubmissionVersion.version_number)
            ).scalars().all()

            submission_data = {
                "submission_id": str(submission.id),
                "task_id": str(submission.task_id),
                "student_id": submission.student_id,
                "status": submission.status.value,
                "current_version": submission.current_version,
                "versions": [
                    {
                        "version_id": str(v.id),
                        "version_number": v.version_number,
                        "status": v.status.value,
                        "files": v.files,
                        "metadata": v.version_metadata,
                        "submitted_at": v.submitted_at.isoformat() if v.submitted_at else None,
                        "created_at": v.created_at.isoformat(),
                    }
                    for v in versions
                ],
                "created_at": submission.created_at.isoformat(),
            }
            result.append(submission_data)

        return result

    def get_submission_by_task(self, db: Session, task_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        submission = db.execute(
            select(Submission).where(Submission.task_id == task_id)
        ).scalar_one_or_none()

        if not submission:
            return None

        versions = db.execute(
            select(SubmissionVersion)
            .where(SubmissionVersion.submission_id == submission.id)
            .order_by(SubmissionVersion.version_number)
        ).scalars().all()

        return {
            "submission_id": str(submission.id),
            "task_id": str(submission.task_id),
            "student_id": submission.student_id,
            "status": submission.status.value,
            "current_version": submission.current_version,
            "versions": [
                {
                    "version_id": str(v.id),
                    "version_number": v.version_number,
                    "status": v.status.value,
                    "files": v.files,
                    "metadata": v.version_metadata,
                    "submitted_at": v.submitted_at.isoformat() if v.submitted_at else None,
                    "created_at": v.created_at.isoformat(),
                }
                for v in versions
            ],
            "created_at": submission.created_at.isoformat(),
        }
