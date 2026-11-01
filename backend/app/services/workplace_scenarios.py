"""Workplace soft-skill scenarios driven by student activity.

Only the requirement-change scenario is active. The catalog reserves the
other workplace scenarios so later work can add a trigger and rubric without
a new assessment path. Nothing here calls an AI provider or a timer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.enums import MessageSenderType, TaskStatus, WorkflowEventStatus, WorkflowEventType
from app.models.manager import Manager
from app.models.message import Message
from app.models.simulation import Scenario, Simulation, WorkflowEvent
from app.models.soft_skill import SoftSkillEvidence
from app.models.task import Task

REQUIREMENT_CHANGE = "requirement_change"
INSUFFICIENT = "insufficient_evidence"
SCORED = "scored"

_TIMER_TRIGGERS = {"timer", "elapsed", "schedule", "timeout", "interval"}
# Opening a task or flipping it to in progress is not student work.
_STATUS_ONLY_ACTIVITIES = {
    "task_started",
    "task_in_progress",
    "in_progress",
    "status_change",
    "status_transition",
    "mid_task_progress",
    "task_progress",
}
_CHANGE_TOKENS = {"change", "requirement", "criteria", "adapt", "update", "validation", "invalid", "error"}
_PLAN_TOKENS = {"will", "plan", "add", "update", "change", "adjust", "revise"}


class WorkplaceScenarioError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ScenarioSpec:
    key: str
    competencies: tuple[str, ...]
    trigger_activity: str
    implemented: bool
    summary: str


SCENARIO_CATALOG: tuple[ScenarioSpec, ...] = (
    ScenarioSpec(
        REQUIREMENT_CHANGE,
        ("communication", "adaptability"),
        "approach_note",
        True,
        "Acceptance criteria change after the student records work on an in-progress task.",
    ),
    ScenarioSpec(
        "collaboration",
        ("communication",),
        "peer_request",
        False,
        "A teammate asks the student to coordinate on shared work.",
    ),
    ScenarioSpec(
        "time_management",
        ("time_management",),
        "schedule_conflict",
        False,
        "The student must re-plan work after a schedule conflict.",
    ),
    ScenarioSpec(
        "prioritization",
        ("decision_making",),
        "competing_work",
        False,
        "Two pieces of work compete and the student must choose.",
    ),
    ScenarioSpec(
        "ownership",
        ("professionalism",),
        "unowned_issue",
        False,
        "An issue has no owner and the student decides whether to take it.",
    ),
    ScenarioSpec(
        "feedback_response",
        ("professionalism", "communication"),
        "review_feedback",
        False,
        "The student responds to specific review feedback.",
    ),
)

_CATALOG_BY_KEY = {item.key: item for item in SCENARIO_CATALOG}


def scenario_catalog() -> List[Dict[str, Any]]:
    return [
        {
            "key": item.key,
            "competencies": list(item.competencies),
            "trigger_activity": item.trigger_activity,
            "implemented": item.implemented,
            "summary": item.summary,
        }
        for item in SCENARIO_CATALOG
    ]


def _require_activity(trigger_activity: str) -> str:
    activity = (trigger_activity or "").strip().lower()
    if not activity or activity in _TIMER_TRIGGERS or "timer" in activity:
        raise WorkplaceScenarioError(
            "Workplace events are triggered by student activity, not by a timer.",
            400,
        )
    return activity


def _status_value(event: WorkflowEvent) -> str:
    raw = event.status
    return raw.value if isinstance(raw, WorkflowEventStatus) else str(raw or "")


def _event_metadata(event: WorkflowEvent) -> Dict[str, Any]:
    return dict(event.metadata_json or {})


def _save_metadata(event: WorkflowEvent, metadata: Dict[str, Any]) -> None:
    event.metadata_json = metadata
    flag_modified(event, "metadata_json")


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", (text or "").lower()))


def _references_change(text: str, criterion: str) -> bool:
    tokens = _tokens(text)
    meaningful = {token for token in _tokens(criterion) if len(token) > 4}
    return len(tokens & meaningful) >= 2 or len(tokens & _CHANGE_TOKENS) >= 2


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def find_requirement_change(db: Session, simulation: Simulation, task: Task) -> Optional[WorkflowEvent]:
    """Return the open requirement change, or the earliest one when none is open."""
    return _open_requirement_change(db, simulation, task) or _existing_requirement_change(db, simulation, task)


def _open_requirement_change(db: Session, simulation: Simulation, task: Task) -> Optional[WorkflowEvent]:
    return db.execute(
        select(WorkflowEvent)
        .where(
            WorkflowEvent.simulation_id == simulation.id,
            WorkflowEvent.task_id == task.id,
            WorkflowEvent.event_type == WorkflowEventType.REQUIREMENT_CHANGE,
            WorkflowEvent.status.in_([
                WorkflowEventStatus.PENDING,
                WorkflowEventStatus.ACKNOWLEDGED,
            ]),
        )
        .order_by(WorkflowEvent.occurred_at)
    ).scalars().first()


def _existing_requirement_change(db: Session, simulation: Simulation, task: Task) -> Optional[WorkflowEvent]:
    return db.execute(
        select(WorkflowEvent)
        .where(
            WorkflowEvent.simulation_id == simulation.id,
            WorkflowEvent.task_id == task.id,
            WorkflowEvent.event_type == WorkflowEventType.REQUIREMENT_CHANGE,
        )
        .order_by(WorkflowEvent.occurred_at)
    ).scalars().first()


def _added_criterion(task: Task) -> str:
    from app.services.criteria_version_service import added_text_for_version

    return added_text_for_version(task, 2)


def issue_requirement_change(
    db: Session,
    simulation: Simulation,
    task: Task,
    *,
    trigger_activity: str,
    scenario: Optional[Scenario] = None,
) -> Optional[WorkflowEvent]:
    """Persist one open requirement change and a new criteria version.

    This is the only writer for requirement-change rows. Returns the new
    event, or None when this task already has a pending or acknowledged
    change. A resolved change stays in history and a later work activity
    can create the next one. Task start, a bare in-progress transition,
    and timer triggers are rejected before any row is written. The
    simulation row is locked so concurrent callers share that decision.
    """
    activity = _require_activity(trigger_activity)
    if activity in _STATUS_ONLY_ACTIVITIES:
        raise WorkplaceScenarioError(
            "Requirement changes follow recorded student work, not task start or a status change.",
            400,
        )
    if task.status != TaskStatus.IN_PROGRESS:
        raise WorkplaceScenarioError(
            "A requirement change is issued only while the task is in progress.",
            409,
        )
    spec = _CATALOG_BY_KEY[REQUIREMENT_CHANGE]
    if not spec.implemented:
        raise WorkplaceScenarioError("requirement_change is not implemented", 501)

    db.execute(
        select(Simulation).where(Simulation.id == simulation.id).with_for_update()
    )
    if _open_requirement_change(db, simulation, task) is not None:
        return None

    from app.services.criteria_version_service import plan_next_change, publish_criteria_version, required_texts

    original = required_texts(db, task)
    if not original:
        original = [f"Complete {task.title} as originally specified"]
    plan = plan_next_change(db, task, original)
    added = plan["added"]
    event = WorkflowEvent(
        simulation_id=simulation.id,
        task_id=task.id,
        scenario_id=scenario.id if scenario is not None else simulation.current_scenario_id,
        event_type=WorkflowEventType.REQUIREMENT_CHANGE,
        title=f"Requirement change: {task.title}",
        message=(
            f"The acceptance criteria for '{task.title}' changed while you were working on it. "
            "Review the new requirement and acknowledge it before you continue."
        ),
        description=(
            f"While working on '{task.title}', the acceptance criteria changed. "
            f"New requirement: {added}."
        ),
        original_requirement="\n".join(original),
        updated_requirement=added,
        trigger_condition=f"requirement_change:{activity}",
        action_required=True,
        occurred_at=datetime.now(timezone.utc),
        status=WorkflowEventStatus.PENDING,
        metadata_json={
            "scenario_key": REQUIREMENT_CHANGE,
            "trigger_activity": activity,
            "original_criteria": original,
            "added_criterion": added,
            "criteria_persisted": True,
            "actions": [],
        },
    )
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
            publish_criteria_version(db, task, event, plan)
            task_meta = dict(task.task_metadata or {})
            task_meta["requirement_change_event_id"] = str(event.id)
            task.task_metadata = task_meta
            flag_modified(task, "task_metadata")
            db.add(task)
            db.flush()
    except IntegrityError:
        return _open_requirement_change(db, simulation, task) or _existing_requirement_change(db, simulation, task)
    return event


async def announce_requirement_change(db: Session, simulation: Simulation, event: Optional[WorkflowEvent]) -> None:
    """Publish the project notification for a requirement change that was just created."""
    if event is None:
        return
    from app.models.enums import EventType
    from app.models.project import Project
    from app.services.engines.events_engine import trigger_event

    project = db.get(Project, simulation.project_id)
    if project is None:
        return
    await trigger_event(
        db,
        project,
        EventType.REQUIREMENT_CHANGE,
        f"Requirement change for '{event.title}': {event.updated_requirement}",
        {
            "task_id": str(event.task_id) if event.task_id else None,
            "event_id": str(event.id),
            "original": event.original_requirement,
            "updated": event.updated_requirement,
        },
    )


def start_scenario(key: str) -> ScenarioSpec:
    """Entry point for future scenarios. Unimplemented keys do no work."""
    spec = _CATALOG_BY_KEY.get(key)
    if spec is None:
        raise WorkplaceScenarioError(f"Unknown workplace scenario '{key}'", 404)
    if not spec.implemented:
        raise WorkplaceScenarioError(f"Workplace scenario '{key}' is not implemented yet.", 501)
    return spec


def acknowledge_requirement_change(
    db: Session,
    simulation: Simulation,
    task: Task,
    event: WorkflowEvent,
    student_id: str,
) -> Dict[str, Any]:
    _require_requirement_event(event, simulation, task)
    metadata = _event_metadata(event)
    if _status_value(event) == WorkflowEventStatus.PENDING.value:
        event.status = WorkflowEventStatus.ACKNOWLEDGED
        metadata["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
        metadata["acknowledged_by"] = student_id
        if not metadata.get("manager_message_id"):
            message = _manager_message(db, simulation, task, event)
            db.add(message)
            db.flush()
            metadata["manager_message_id"] = str(message.id)
            metadata["manager_message"] = message.content
        _save_metadata(event, metadata)
        db.add(event)
        db.flush()
    return serialize_workflow_event(db, event, task)


def record_requirement_reply(
    db: Session,
    simulation: Simulation,
    task: Task,
    event: WorkflowEvent,
    student_id: str,
    response: str,
) -> Dict[str, Any]:
    _require_requirement_event(event, simulation, task)
    text = (response or "").strip()
    if not text:
        raise WorkplaceScenarioError("A reply is required.", 400)
    if _status_value(event) == WorkflowEventStatus.PENDING.value:
        raise WorkplaceScenarioError("Acknowledge the requirement change before replying.", 409)
    if event.response:
        raise WorkplaceScenarioError("A reply is already recorded for this requirement change.", 409)

    event.response = text
    event.responded_at = datetime.now(timezone.utc)
    # A reply records communication. The event stays acknowledged until a later
    # action is stored and adaptability has been evaluated from it.
    if _status_value(event) != WorkflowEventStatus.ACKNOWLEDGED.value:
        event.status = WorkflowEventStatus.ACKNOWLEDGED
    metadata = _event_metadata(event)
    if not metadata.get("student_message_id"):
        message = Message(
            company_id=simulation.company_id,
            sender_type=MessageSenderType.STUDENT,
            sender_id=student_id,
            content=text,
        )
        db.add(message)
        db.flush()
        metadata["student_message_id"] = str(message.id)
    _save_metadata(event, metadata)
    db.add(event)
    db.flush()
    assessment = assess_requirement_change(db, simulation, task, event, student_id)
    payload = serialize_workflow_event(db, event, task)
    payload["assessment"] = assessment
    return payload


def record_subsequent_action(
    db: Session,
    simulation: Simulation,
    task: Task,
    event: WorkflowEvent,
    student_id: str,
    action: str,
    detail: str,
) -> Dict[str, Any]:
    _require_requirement_event(event, simulation, task)
    if not event.response or event.responded_at is None:
        raise WorkplaceScenarioError("Reply to the manager before recording what you changed.", 409)
    kind = (action or "").strip()
    if kind not in {"adapted_work", "task_submitted"}:
        raise WorkplaceScenarioError("Unsupported workplace action.", 400)
    note = (detail or "").strip()
    if not note:
        raise WorkplaceScenarioError("Describe the action you took.", 400)
    criterion = _event_metadata(event).get("added_criterion") or event.updated_requirement or ""
    if kind == "adapted_work" and not _references_change(note, criterion):
        raise WorkplaceScenarioError(
            "Describe how the task changed for the new requirement. Unrelated notes are not saved.",
            400,
        )

    metadata = _event_metadata(event)
    actions = list(metadata.get("actions") or [])
    if any(item.get("action") == kind for item in actions):
        raise WorkplaceScenarioError("That action is already recorded for this requirement change.", 409)
    actions.append({
        "action": kind,
        "detail": note,
        "at": datetime.now(timezone.utc).isoformat(),
        "student_id": student_id,
    })
    metadata["actions"] = actions
    _save_metadata(event, metadata)
    db.add(event)
    db.flush()
    assessment = assess_requirement_change(db, simulation, task, event, student_id)
    _resolve_when_adaptation_scored(db, event, assessment)
    payload = serialize_workflow_event(db, event, task)
    payload["assessment"] = assessment
    return payload


def record_task_submission_action(
    db: Session,
    simulation: Simulation,
    task: Task,
    student_id: str,
    detail: str,
) -> None:
    """Attach a submission to an open requirement change without failing the submit."""
    event = _existing_requirement_change(db, simulation, task)
    if event is None or not event.response:
        return
    metadata = _event_metadata(event)
    actions = list(metadata.get("actions") or [])
    if any(item.get("action") == "task_submitted" for item in actions):
        return
    actions.append({
        "action": "task_submitted",
        "detail": (detail or "Submitted the task after the requirement change.").strip(),
        "at": datetime.now(timezone.utc).isoformat(),
        "student_id": student_id,
    })
    metadata["actions"] = actions
    _save_metadata(event, metadata)
    db.add(event)
    db.flush()
    assessment = assess_requirement_change(db, simulation, task, event, student_id)
    _resolve_when_adaptation_scored(db, event, assessment)


def _resolve_when_adaptation_scored(db: Session, event: WorkflowEvent, assessment: Dict[str, Any]) -> None:
    """Resolve only after an adaptation was saved and adaptability was scored."""
    actions = list(_event_metadata(event).get("actions") or [])
    if not any(item.get("action") == "adapted_work" for item in actions):
        return
    adaptability = (assessment or {}).get("adaptability") or {}
    if adaptability.get("status") != SCORED:
        return
    if _status_value(event) == WorkflowEventStatus.RESOLVED.value:
        return
    event.status = WorkflowEventStatus.RESOLVED
    db.add(event)
    db.flush()


def assess_requirement_change(
    db: Session,
    simulation: Simulation,
    task: Task,
    event: WorkflowEvent,
    student_id: str,
) -> Dict[str, Any]:
    metadata = _event_metadata(event)
    criterion = metadata.get("added_criterion") or event.updated_requirement or ""
    reply = (event.response or "").strip()
    replied_at = _aware(event.responded_at)
    later_actions = []
    for item in metadata.get("actions") or []:
        acted_at = _parse_time(item.get("at"))
        if replied_at is not None and acted_at is not None and acted_at >= replied_at:
            later_actions.append(item)

    communication = _communication_observation(reply, criterion)
    adaptability = _adaptability_observation(later_actions, criterion)
    rows = {
        "communication": _upsert_evidence(
            db, simulation, task, event, student_id, "communication", communication, reply, later_actions
        ),
        "adaptability": _upsert_evidence(
            db, simulation, task, event, student_id, "adaptability", adaptability, reply, later_actions
        ),
    }
    db.flush()
    return _assessment_payload(rows)


def _communication_observation(reply: str, criterion: str) -> Dict[str, Any]:
    if len(reply.split()) >= 8 and _references_change(reply, criterion):
        stated_plan = bool(_tokens(reply) & _PLAN_TOKENS)
        return {
            "status": SCORED,
            "score": 82.0 if stated_plan else 68.0,
            "summary": "The reply addresses the changed requirement.",
            "reason": "message_references_change",
        }
    return {
        "status": INSUFFICIENT,
        "score": None,
        "summary": "The reply does not yet show how the student communicated about the change.",
        "reason": "message_missing_or_unrelated",
    }


def _adaptability_observation(actions: Sequence[Dict[str, Any]], criterion: str) -> Dict[str, Any]:
    authored = [item for item in actions if item.get("action") == "adapted_work" and _references_change(item.get("detail") or "", criterion)]
    if authored:
        return {
            "status": SCORED,
            "score": 84.0,
            "summary": "A later action describes work changed for the new requirement.",
            "reason": "subsequent_action_references_change",
        }
    submitted = [item for item in actions if item.get("action") == "task_submitted" and _references_change(item.get("detail") or "", criterion)]
    if submitted:
        return {
            "status": SCORED,
            "score": 74.0,
            "summary": "The submission after the reply addresses the changed requirement.",
            "reason": "submission_references_change",
        }
    return {
        "status": INSUFFICIENT,
        "score": None,
        "summary": "No later action shows the student adapted the task to the new requirement.",
        "reason": "no_subsequent_adaptation",
    }


def _upsert_evidence(
    db: Session,
    simulation: Simulation,
    task: Task,
    event: WorkflowEvent,
    student_id: str,
    competency: str,
    observation: Dict[str, Any],
    reply: str,
    actions: Sequence[Dict[str, Any]],
) -> SoftSkillEvidence:
    row = db.execute(
        select(SoftSkillEvidence).where(
            SoftSkillEvidence.event_id == event.id,
            SoftSkillEvidence.competency == competency,
        )
    ).scalar_one_or_none()
    if row is None:
        row = SoftSkillEvidence(
            student_id=student_id,
            simulation_id=simulation.id,
            task_id=task.id,
            event_id=event.id,
            scenario_key=REQUIREMENT_CHANGE,
            competency=competency,
        )
        db.add(row)
    row.student_id = student_id
    row.simulation_id = simulation.id
    row.task_id = task.id
    row.status = observation["status"]
    row.score = observation["score"]
    row.summary = observation["summary"]
    row.evidence = {
        "reason": observation["reason"],
        "reply": reply,
        "subsequent_actions": [dict(item) for item in actions],
        "added_criterion": (_event_metadata(event).get("added_criterion") or event.updated_requirement),
    }
    flag_modified(row, "evidence")
    db.add(row)
    db.flush()
    return row


def _assessment_payload(rows: Dict[str, SoftSkillEvidence]) -> Dict[str, Any]:
    communication = _public_observation(rows["communication"])
    adaptability = _public_observation(rows["adaptability"])
    status = SCORED if communication["status"] == SCORED and adaptability["status"] == SCORED else INSUFFICIENT
    return {
        "status": status,
        "communication": communication,
        "adaptability": adaptability,
    }


def _public_observation(row: SoftSkillEvidence) -> Dict[str, Any]:
    return {
        "status": row.status,
        "score": row.score if row.status == SCORED else None,
        "evidence_id": str(row.id),
        "summary": row.summary,
    }


def assessment_for_event(db: Session, event: WorkflowEvent) -> Dict[str, Any]:
    rows = db.execute(
        select(SoftSkillEvidence).where(SoftSkillEvidence.event_id == event.id)
    ).scalars().all()
    by_competency = {row.competency: row for row in rows}
    if "communication" in by_competency and "adaptability" in by_competency:
        return _assessment_payload(by_competency)
    empty = {"status": INSUFFICIENT, "score": None, "evidence_id": None, "summary": "No evidence recorded yet."}
    return {
        "status": INSUFFICIENT,
        "communication": _public_observation(by_competency["communication"]) if "communication" in by_competency else empty,
        "adaptability": _public_observation(by_competency["adaptability"]) if "adaptability" in by_competency else dict(empty),
    }


def _serialized_criteria(db: Session, task: Optional[Task]) -> List[str]:
    if task is None:
        return []
    from app.services.criteria_version_service import required_texts

    return required_texts(db, task)


def serialize_workflow_event(db: Session, event: WorkflowEvent, task: Optional[Task] = None) -> Dict[str, Any]:
    metadata = _event_metadata(event)
    scenario_key = metadata.get("scenario_key")
    status = _status_value(event)
    is_requirement = event.event_type == WorkflowEventType.REQUIREMENT_CHANGE or scenario_key == REQUIREMENT_CHANGE
    actions = list(metadata.get("actions") or [])
    payload = {
        "id": str(event.id),
        "event_type": event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type),
        "scenario_key": scenario_key,
        "title": event.title,
        "message": event.message,
        "description": event.description,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "status": status,
        "action_required": event.action_required,
        "original_requirement": event.original_requirement,
        "updated_requirement": event.updated_requirement,
        "original_criteria": metadata.get("original_criteria") or [],
        "added_criterion": metadata.get("added_criterion") or event.updated_requirement,
        "criteria_version_id": metadata.get("criteria_version_id"),
        "criteria_version_number": metadata.get("criteria_version_number"),
        "acceptance_criteria": _serialized_criteria(db, task),
        "acknowledged": status != WorkflowEventStatus.PENDING.value,
        "manager_message": metadata.get("manager_message"),
        "responded": event.response is not None,
        "response": event.response,
        "responded_at": event.responded_at.isoformat() if event.responded_at else None,
        "subsequent_actions": actions,
        "assessment": assessment_for_event(db, event) if is_requirement else None,
    }
    payload["phase"] = workplace_phase(payload) if is_requirement else "idle"
    return payload


def workplace_phase(event: Dict[str, Any]) -> str:
    """Shared phase names for the workplace simulation UI."""
    if not event or event.get("event_type") != REQUIREMENT_CHANGE:
        return "idle"
    if event.get("status") == WorkflowEventStatus.PENDING.value:
        return "acknowledge"
    if not event.get("response"):
        return "reply"
    described = any(item.get("action") == "adapted_work" for item in (event.get("subsequent_actions") or []))
    if not described:
        return "adapt"
    return "assessed"


def _require_requirement_event(event: WorkflowEvent, simulation: Simulation, task: Task) -> None:
    if event.simulation_id != simulation.id or event.task_id != task.id:
        raise WorkplaceScenarioError("Event does not belong to this task.", 403)
    if event.event_type != WorkflowEventType.REQUIREMENT_CHANGE:
        raise WorkplaceScenarioError("This action only applies to a requirement change.", 400)


def _manager_message(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent) -> Message:
    manager = db.execute(select(Manager).where(Manager.company_id == simulation.company_id)).scalars().first()
    criterion = event.updated_requirement or _added_criterion(task)
    content = (
        f"I updated the acceptance criteria for '{task.title}'. "
        f"The new requirement is: {criterion} "
        "Reply with how you will adapt the work you already started, and what you will change."
    )
    return Message(
        company_id=simulation.company_id,
        sender_type=MessageSenderType.MANAGER,
        sender_id=str(manager.id) if manager is not None else "manager",
        content=content,
    )
