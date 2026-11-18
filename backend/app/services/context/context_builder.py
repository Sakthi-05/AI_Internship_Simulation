from __future__ import annotations

import logging
import hashlib
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.criteria_version import AcceptanceCriteriaVersion
from app.models.project import Project
from app.models.project_state import ProjectState
from app.models.sprint import Sprint
from app.models.submission import Submission, SubmissionVersion
from app.models.task import Task
from app.services.criteria_version_service import criteria_identity, required_texts

logger = logging.getLogger(__name__)


def _review_criteria(db: Session, task: Task) -> List[str]:
    return required_texts(db, task)


def _criteria_identity(db: Session, task: Task) -> Dict[str, Any]:
    return criteria_identity(db, task)


def _similarity_ratio(left: str, right: str) -> float:
    """Bounded normalized similarity used only to flag extra reviewer scrutiny."""
    left = re.sub(r"\s+", " ", left).strip().casefold()
    right = re.sub(r"\s+", " ", right).strip().casefold()
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if abs(len(left) - len(right)) / max(len(left), len(right)) > 0.15:
        return 0.0
    # Keep comparison bounded for large documents; exact SHA matching above
    # still detects large identical files and editor submissions.
    return SequenceMatcher(None, left[:100_000], right[:100_000], autojunk=True).ratio()


def build_project_context(db: Session, project: Project) -> Dict[str, Any]:
    """Build project-level context (without task-specific info)."""
    state = db.execute(select(ProjectState).where(ProjectState.project_id == project.id)).scalar_one_or_none()
    tasks = db.execute(
        select(Task).join(Sprint, Sprint.id == Task.sprint_id).where(Sprint.project_id == project.id)
    ).scalars().all()
    sprints = db.execute(select(Sprint).where(Sprint.project_id == project.id).order_by(Sprint.sprint_number)).scalars().all()
    company = db.get(Company, project.company_id)

    return {
        "project_id": str(project.id),
        "project_title": project.title,
        "company_name": company.name if company else "",
        "company_industry": company.industry if company else "",
        "company_type": company.company_type.value if company and company.company_type else "",
        "role": project.role,
        "technology_stack": list(project.technology_stack or []),
        "difficulty": project.difficulty.value if project.difficulty else None,
        "objectives": list(project.objectives or []),
        "modules": list(project.modules or []),
        "deliverables": list(project.deliverables or []),
        "status": project.status.value if project.status else None,
        "state": {
            "completed_tasks": state.completed_tasks if state else 0,
            "pending_tasks": state.pending_tasks if state else 0,
            "missed_deadlines": state.missed_deadlines if state else 0,
            "bug_count": state.bug_count if state else 0,
            "stress_level": state.stress_level if state else 0,
            "manager_satisfaction": state.manager_satisfaction if state else 0,
            "team_satisfaction": state.team_satisfaction if state else 0,
            "current_sprint_number": state.current_sprint_number if state else 1,
        },
        "sprints": [
            {
                "id": str(sprint.id),
                "name": sprint.name,
                "number": sprint.sprint_number,
                "goal": sprint.goal,
                "status": sprint.status.value if sprint.status else None,
            }
            for sprint in sprints
        ],
        "tasks": [
            {
                "id": str(task.id),
                "title": task.title,
                "status": task.status.value if task.status else None,
                "priority": task.priority.value if task.priority else None,
                "estimated_hours": task.estimated_hours,
                "deadline": task.deadline.isoformat() if task.deadline else None,
                "sprint_id": str(task.sprint_id),
            }
            for task in tasks
        ],
    }


def build_task_context(db: Session, task: Task) -> Dict[str, Any]:
    """Build task-specific context including sprint info and submission history."""
    sprint = db.get(Sprint, task.sprint_id)
    project = db.get(Project, sprint.project_id) if sprint else None
    company = db.get(Company, project.company_id) if project else None
    
    # Get submission history for this task
    submissions = db.execute(
        select(Submission).where(Submission.task_id == task.id)
    ).scalars().all()
    
    submission_history = []
    for submission in submissions:
        versions = db.execute(
            select(SubmissionVersion)
            .where(SubmissionVersion.submission_id == submission.id)
            .order_by(SubmissionVersion.version_number)
        ).scalars().all()
        
        for version in versions:
            # Get AI reviews for this version
            from app.models.submission import AIReview
            reviews = db.execute(
                select(AIReview).where(AIReview.version_id == version.id)
            ).scalars().all()
            
            review_feedback = []
            for review in reviews:
                review_feedback.append({
                    "result": review.result.value,
                    "severity": review.severity.value,
                    "summary": review.summary,
                    "strengths": review.strengths,
                    "issues": review.issues if hasattr(review, 'issues') else [],
                    "required_changes": review.required_changes if hasattr(review, 'required_changes') else [],
                    "score": review.score,
                    "created_at": review.created_at.isoformat(),
                })
            
            submission_history.append({
                "version_number": version.version_number,
                "status": version.status.value,
                "submitted_at": version.submitted_at.isoformat() if version.submitted_at else None,
                "files": version.files,
                "metadata": version.version_metadata,
                "reviews": review_feedback,
            })
    
    return {
        "task_id": str(task.id),
        "task_title": task.title,
        "task_description": task.description,
        "acceptance_criteria": _review_criteria(db, task),
        "acceptance_criteria_version": _criteria_identity(db, task),
        "task_priority": task.priority.value if task.priority else None,
        "task_deadline": task.deadline.isoformat() if task.deadline else None,
        "sprint": {
            "id": str(sprint.id) if sprint else None,
            "name": sprint.name if sprint else "",
            "number": sprint.sprint_number if sprint else 0,
            "goal": sprint.goal if sprint else "",
            "status": sprint.status.value if sprint and sprint.status else None,
        } if sprint else {},
        "project": {
            "id": str(project.id) if project else None,
            "title": project.title if project else "",
            "objectives": list(project.objectives or []) if project else [],
            "technology_stack": list(project.technology_stack or []) if project else [],
        } if project else {},
        "company": {
            "name": company.name if company else "",
            "industry": company.industry if company else "",
            "company_type": company.company_type.value if company and company.company_type else "",
        } if company else {},
        "previous_submissions": submission_history,
    }


#: Metadata keys that count as real work evidence alongside attached files.
#: Mirrors SubmissionService._has_submission_evidence so the reviewer sees
#: exactly what the student actually provided (written response, pasted code,
#: notes, links) — never an empty shell that invites invented analysis.
_METADATA_EVIDENCE_KEYS = ("response", "code", "evidence", "notes", "url", "deliverable_url")

#: Minimum extractable file text that counts as analyzable implementation.
MIN_FILE_CONTENT_CHARS = 50
#: Minimum written-evidence text that counts as analyzable on its own. Below
#: this a note like "done" cannot verify any requirement, so the reviewer
#: must ask for the missing detail instead of filling the gaps by invention.
MIN_TEXT_EVIDENCE_CHARS = 200


def metadata_evidence_text(metadata: Any) -> str:
    """Render textual work evidence from submission metadata (no files needed)."""
    parts = []
    if isinstance(metadata, dict):
        for key in _METADATA_EVIDENCE_KEYS:
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(f"{key.upper()}:\n{value}")
    return "\n\n".join(parts)


def build_review_context(db: Session, version: "SubmissionVersion") -> Dict[str, Any]:
    """Context for the current submission only: task, criteria, stack, and one prior review."""
    task = version.submission.task
    project = task.sprint.project
    from app.services.content_extraction import extract_all_submission_files
    from app.services.file_storage import FileStorageService

    storage_service = FileStorageService()
    file_content = ""
    if version.files:
        try:
            file_content = extract_all_submission_files(version.files, storage_service) or ""
        except Exception:
            logger.exception(
                "Submission file extraction failed submission_id=%s version_id=%s",
                version.submission_id, version.id,
            )
            raise
    metadata = version.version_metadata or {}
    sprint = task.sprint
    criteria_snapshot = metadata.get("acceptance_criteria_snapshot")
    criteria_version = metadata.get("acceptance_criteria_version")
    if not isinstance(criteria_snapshot, list):
        # Older submissions stored only the criteria-version identity. Recover
        # that immutable snapshot when possible instead of using today's
        # active criteria for a historical review retry.
        criteria_row = None
        if isinstance(criteria_version, dict) and criteria_version.get("id"):
            try:
                criteria_row = db.get(AcceptanceCriteriaVersion, UUID(criteria_version["id"]))
            except (TypeError, ValueError):
                criteria_row = None
        if criteria_row is None and isinstance(criteria_version, dict):
            version_number = criteria_version.get("version_number")
            if version_number is not None:
                try:
                    criteria_row = db.execute(
                        select(AcceptanceCriteriaVersion).where(
                            AcceptanceCriteriaVersion.task_id == task.id,
                            AcceptanceCriteriaVersion.version_number == int(version_number),
                        )
                    ).scalars().first()
                except (TypeError, ValueError):
                    criteria_row = None
        if criteria_row is not None:
            criteria_snapshot = [
                str(item.get("text")).strip()
                for item in (criteria_row.items or [])
                if isinstance(item, dict)
                and str(item.get("text") or "").strip()
                and item.get("state") != "superseded"
            ]
            criteria_version = criteria_version or {
                "id": str(criteria_row.id),
                "version_number": criteria_row.version_number,
                "status": criteria_row.status,
                "implicit": False,
            }
    meta_text = metadata_evidence_text(metadata)
    test_results = "\n".join(
        f"{key}: {metadata[key]}"
        for key in ("test_results", "test_output", "tests_run", "verification")
        if metadata.get(key) not in (None, "", [], {})
    )[:2000]
    file_chars = len(file_content.strip())
    meta_chars = len(meta_text.strip())
    attached_filenames = [
        str(item.get("filename") or item.get("original_filename") or "unnamed file")
        for item in (version.files or [])
        if isinstance(item, dict)
    ]
    # Evidence tiers: "full" is analyzable; "thin" is a real submission too
    # brief to verify (e.g. a one-line note with no code); "none" is empty.
    # Anything below "full" gets deterministic guidance — never model analysis
    # that would have to invent the missing substance.
    if file_chars >= MIN_FILE_CONTENT_CHARS or meta_chars >= MIN_TEXT_EVIDENCE_CHARS:
        evidence_level = "full"
    elif version.files or meta_chars > 0:
        evidence_level = "thin"
    else:
        evidence_level = "none"
    combined = "\n\n".join(part for part in (file_content, meta_text) if part.strip())
    has_work_evidence = evidence_level in ("full", "thin")
    current_content = combined if combined else "No work evidence submitted."
    metadata_code = metadata.get("code") if isinstance(metadata, dict) else None
    normalized_source = re.sub(r"\s+", " ", str(metadata_code or "")).strip().casefold()
    source_fingerprint = hashlib.sha256(normalized_source.encode("utf-8")).hexdigest() if normalized_source else None
    submitted_file_checksums = {
        str(item.get("checksum")) for item in (version.files or [])
        if isinstance(item, dict) and item.get("checksum")
    }
    cross_task_matches = []
    if source_fingerprint or submitted_file_checksums:
        prior_versions = db.execute(
            select(SubmissionVersion)
            .join(Submission, Submission.id == SubmissionVersion.submission_id)
            .where(
                Submission.student_id == version.submission.student_id,
                Submission.task_id != task.id,
                SubmissionVersion.id != version.id,
                SubmissionVersion.submitted_at.is_not(None),
            )
            .order_by(SubmissionVersion.submitted_at.desc())
            .limit(100)
        ).scalars().all()
        for prior in prior_versions:
            prior_metadata = prior.version_metadata or {}
            prior_source = re.sub(r"\s+", " ", str(prior_metadata.get("code") or "")).strip().casefold()
            same_source = bool(
                prior_source and source_fingerprint
                and hashlib.sha256(prior_source.encode("utf-8")).hexdigest() == source_fingerprint
            )
            similarity = _similarity_ratio(normalized_source, prior_source) if prior_source else 0.0
            prior_checksums = {
                str(item.get("checksum")) for item in (prior.files or [])
                if isinstance(item, dict) and item.get("checksum")
            }
            if same_source or similarity >= 0.90 or (submitted_file_checksums & prior_checksums):
                prior_task = prior.submission.task
                cross_task_matches.append(
                    f"task {prior_task.id} ({prior_task.title}) submission {prior.submission_id} similarity={similarity:.2f}"
                )
    if isinstance(metadata_code, str):
        logger.info(
            "Review source prepared submission_id=%s version_id=%s student_id=%s task_id=%s "
            "source_chars=%d source_bytes=%d stored_source_chars=%d evaluator_input_chars=%d "
            "evaluator_input_bytes=%d has_expected_end_marker=%s",
            version.submission_id, version.id, version.submission.student_id, task.id,
            len(metadata_code), len(metadata_code.encode("utf-8")), len(metadata_code),
            len(current_content), len(current_content.encode("utf-8")),
            (metadata.get("expected_end_marker") in metadata_code)
            if metadata.get("expected_end_marker") else None,
        )

    return {
        "task_id": str(task.id),
        "sprint_id": str(sprint.id) if sprint else None,
        "sprint_goal": sprint.goal if sprint else "",
        "task_title": task.title,
        "task_description": task.description or "",
        "task_instructions": (
            [str(item) for item in (task.task_metadata or {}).get("instructions")]
            if isinstance((task.task_metadata or {}).get("instructions"), list)
            else ([str((task.task_metadata or {}).get("instructions"))]
                  if (task.task_metadata or {}).get("instructions") else [])
        ),
        "project_title": project.title,
        "project_objectives": list(project.objectives or []),
        "expected_deliverables": list(
            (task.task_metadata or {}).get("expected_deliverables")
            or (task.task_metadata or {}).get("deliverables")
            or project.deliverables
            or []
        ),
        "cross_task_similarity": {"matches": cross_task_matches},
        # Review exactly the requirements visible when this version was
        # submitted. A later requirement change must not rewrite its rubric.
        "acceptance_criteria": (
            [str(item).strip() for item in criteria_snapshot if str(item).strip()]
            if isinstance(criteria_snapshot, list)
            else _review_criteria(db, task)
        ),
        "acceptance_criteria_version": (
            criteria_version if isinstance(criteria_version, dict) else _criteria_identity(db, task)
        ),
        "technology_stack": list(project.technology_stack or [])[:6],
        "has_work_evidence": has_work_evidence,
        "evidence_level": evidence_level,
        "attached_filenames": attached_filenames,
        "test_results": test_results,
        "current_submission": {
            "version_number": version.version_number,
            "content": current_content,
        },
        "submission_content": current_content,
    }
