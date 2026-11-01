"""Acceptance-criteria snapshots for requirement changes.

The task's ``acceptance_criteria`` array stays the current required texts so
existing readers keep working. History, item state, and the link to the
requirement-change event live on ``acceptance_criteria_versions``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.criteria_version import AcceptanceCriteriaVersion
from app.models.enums import WorkflowEventType
from app.models.simulation import WorkflowEvent
from app.models.task import Task

ITEM_ACTIVE = "active"
ITEM_MODIFIED = "modified"
ITEM_SUPERSEDED = "superseded"
VERSION_ACTIVE = "active"
VERSION_SUPERSEDED = "superseded"


def added_text_for_version(task: Task, version_number: int) -> str:
    """Text introduced by the requirement change that creates this version.

    Version 2 matches the original single requirement-change sentence.
    Later versions are distinct so successive changes are visible.
    """
    if version_number <= 2:
        return (
            f"Validate invalid input on '{task.title}' and return a clear error response, "
            "including what already completed work must change."
        )
    return (
        f"Criteria version {version_number} for '{task.title}': document the error cases "
        "added after the previous requirement change."
    )


def _clean_texts(values: Optional[List[Any]]) -> List[str]:
    cleaned: List[str] = []
    for item in values or []:
        text = str(item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def list_versions(db: Session, task: Task) -> List[AcceptanceCriteriaVersion]:
    return list(
        db.execute(
            select(AcceptanceCriteriaVersion)
            .where(AcceptanceCriteriaVersion.task_id == task.id)
            .order_by(AcceptanceCriteriaVersion.version_number)
        ).scalars().all()
    )


def active_version(db: Session, task: Task) -> Optional[AcceptanceCriteriaVersion]:
    return db.execute(
        select(AcceptanceCriteriaVersion)
        .where(
            AcceptanceCriteriaVersion.task_id == task.id,
            AcceptanceCriteriaVersion.status == VERSION_ACTIVE,
        )
        .order_by(AcceptanceCriteriaVersion.version_number.desc())
    ).scalars().first()


def required_texts(db: Session, task: Task) -> List[str]:
    """Texts the student must still meet. Falls back to the task column."""
    version = active_version(db, task)
    if version is None:
        return _clean_texts(task.acceptance_criteria)
    return [
        str(item.get("text")).strip()
        for item in (version.items or [])
        if isinstance(item, dict)
        and str(item.get("text") or "").strip()
        and item.get("state") != ITEM_SUPERSEDED
    ]


def criteria_identity(db: Session, task: Task) -> Dict[str, Any]:
    """Identity stored on evaluations. Existing tasks without a row stay version 1."""
    version = active_version(db, task)
    if version is None:
        return {"id": None, "version_number": 1, "status": VERSION_ACTIVE, "implicit": True}
    return {
        "id": str(version.id),
        "version_number": version.version_number,
        "status": version.status,
        "implicit": False,
    }


def _public_version(version: AcceptanceCriteriaVersion) -> Dict[str, Any]:
    return {
        "id": str(version.id),
        "version_number": version.version_number,
        "status": version.status,
        "workflow_event_id": str(version.workflow_event_id) if version.workflow_event_id else None,
        "items": [
            {"text": str(item.get("text")), "state": item.get("state") or ITEM_ACTIVE}
            for item in (version.items or [])
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ],
    }


def criteria_view(db: Session, task: Task) -> Dict[str, Any]:
    """Latest required texts plus history. ``criteria_version`` is null when unversioned."""
    texts = required_texts(db, task)
    versions = list_versions(db, task)
    if not versions:
        return {
            "expected_output": texts,
            "acceptance_criteria": list(texts),
            "criteria_version": None,
        }
    current = next((item for item in versions if item.status == VERSION_ACTIVE), versions[-1])
    return {
        "expected_output": texts,
        "acceptance_criteria": list(texts),
        "criteria_version": {
            "active_version_id": str(current.id),
            "active_version_number": current.version_number,
            "versions": [_public_version(item) for item in versions],
        },
    }


def plan_next_change(db: Session, task: Task, original_texts: List[str]) -> Dict[str, Any]:
    """Describe the next snapshot without writing it."""
    current = active_version(db, task)
    next_number = (current.version_number + 1) if current is not None else 2
    return {
        "original": _clean_texts(original_texts),
        "added": added_text_for_version(task, next_number),
        "next_number": next_number,
    }


def publish_criteria_version(db: Session, task: Task, event: WorkflowEvent, plan: Dict[str, Any]) -> AcceptanceCriteriaVersion:
    """Supersede the current snapshot and store the one this event created.

    The first change also stores version 1 from the texts that existed before
    the change, so the original criteria remain readable.
    """
    original = _clean_texts(plan.get("original"))
    added = str(plan.get("added") or "").strip()
    current = active_version(db, task)
    if current is None:
        baseline = AcceptanceCriteriaVersion(
            task_id=task.id,
            version_number=1,
            status=VERSION_SUPERSEDED,
            items=[{"text": text, "state": ITEM_ACTIVE} for text in original],
            workflow_event_id=None,
        )
        db.add(baseline)
        db.flush()
        next_number = 2
    else:
        current.status = VERSION_SUPERSEDED
        db.add(current)
        next_number = current.version_number + 1

    items = [{"text": text, "state": ITEM_ACTIVE} for text in original]
    if added and added not in original:
        items.append({"text": added, "state": ITEM_MODIFIED})
    version = AcceptanceCriteriaVersion(
        task_id=task.id,
        version_number=next_number,
        status=VERSION_ACTIVE,
        items=items,
        workflow_event_id=event.id,
    )
    db.add(version)
    db.flush()

    task.acceptance_criteria = [item["text"] for item in items if item["state"] != ITEM_SUPERSEDED]
    flag_modified(task, "acceptance_criteria")
    db.add(task)

    metadata = dict(event.metadata_json or {})
    metadata["criteria_version_id"] = str(version.id)
    metadata["criteria_version_number"] = version.version_number
    metadata["original_criteria"] = original
    metadata["added_criterion"] = added
    metadata["criteria_persisted"] = True
    event.metadata_json = metadata
    event.updated_requirement = added
    flag_modified(event, "metadata_json")
    db.add(event)
    db.flush()
    return version


def _event_original_texts(event: WorkflowEvent, fallback: List[str]) -> List[str]:
    metadata = event.metadata_json or {}
    stored = metadata.get("original_criteria")
    if isinstance(stored, list) and stored:
        return _clean_texts(stored)
    lines = [line.strip() for line in (event.original_requirement or "").split("\n") if line.strip()]
    return lines or list(fallback)


def _event_added_text(event: WorkflowEvent) -> str:
    metadata = event.metadata_json or {}
    added = metadata.get("added_criterion") or event.updated_requirement or ""
    return str(added).strip()


def backfill_missing_versions(db: Session) -> int:
    """Create snapshots for tasks that do not have them yet.

    Does not rewrite ``tasks.acceptance_criteria`` or delete events. Event
    metadata gains ``criteria_version_id`` only when that key is absent.
    Returns the number of tasks that received snapshots.
    """
    tasks = db.execute(select(Task)).scalars().all()
    written = 0
    for task in tasks:
        if list_versions(db, task):
            continue
        current = _clean_texts(task.acceptance_criteria)
        events = list(
            db.execute(
                select(WorkflowEvent)
                .where(
                    WorkflowEvent.task_id == task.id,
                    WorkflowEvent.event_type == WorkflowEventType.REQUIREMENT_CHANGE,
                )
                .order_by(WorkflowEvent.occurred_at)
            ).scalars().all()
        )
        if not events:
            db.add(AcceptanceCriteriaVersion(
                task_id=task.id,
                version_number=1,
                status=VERSION_ACTIVE,
                items=[{"text": text, "state": ITEM_ACTIVE} for text in current],
                workflow_event_id=None,
            ))
            written += 1
            continue

        original = _event_original_texts(events[0], current)
        db.add(AcceptanceCriteriaVersion(
            task_id=task.id,
            version_number=1,
            status=VERSION_SUPERSEDED,
            items=[{"text": text, "state": ITEM_ACTIVE} for text in original],
            workflow_event_id=None,
        ))
        running = list(original)
        for index, event in enumerate(events):
            added = _event_added_text(event)
            items = [{"text": text, "state": ITEM_ACTIVE} for text in running]
            if added and added not in running:
                items.append({"text": added, "state": ITEM_MODIFIED})
                running.append(added)
            version = AcceptanceCriteriaVersion(
                task_id=task.id,
                version_number=index + 2,
                status=VERSION_ACTIVE if index == len(events) - 1 else VERSION_SUPERSEDED,
                items=items,
                workflow_event_id=event.id,
            )
            db.add(version)
            db.flush()
            metadata = dict(event.metadata_json or {})
            if not metadata.get("criteria_version_id"):
                metadata["criteria_version_id"] = str(version.id)
                metadata["criteria_version_number"] = version.version_number
                event.metadata_json = metadata
                flag_modified(event, "metadata_json")
                db.add(event)
        written += 1
    db.flush()
    return written
