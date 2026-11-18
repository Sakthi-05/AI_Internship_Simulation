"""Workplace soft-skill scenarios driven by student activity.

Only the requirement-change scenario is active. The catalog reserves the
other workplace scenarios so later work can add a trigger and rubric without
a new assessment path. Nothing here calls an AI provider or a timer.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.enums import MessageSenderType, SubmissionStatus, TaskStatus, WorkflowEventStatus, WorkflowEventType
from app.models.manager import Manager
from app.models.message import Message
from app.models.simulation import Scenario, Simulation, WorkflowEvent
from app.models.submission import Submission, SubmissionVersion
from app.models.soft_skill import SoftSkillEvidence
from app.models.task import Task

REQUIREMENT_CHANGE = "requirement_change"
COLLABORATION = "collaboration"
TIME_MANAGEMENT = "time_management"
PRIORITIZATION = "prioritization"
OWNERSHIP = "ownership"
FEEDBACK_RESPONSE = "feedback_response"
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
        True,
        "A teammate asks the student to coordinate on shared work.",
    ),
    ScenarioSpec(
        "time_management",
        ("time_management",),
        "schedule_conflict",
        True,
        "The student must re-plan work after a schedule conflict.",
    ),
    ScenarioSpec(
        "prioritization",
        ("decision_making",),
        "competing_work",
        True,
        "Two pieces of work compete and the student must choose.",
    ),
    ScenarioSpec(
        "ownership",
        ("professionalism",),
        "unowned_issue",
        True,
        "An issue has no owner and the student decides whether to take it.",
    ),
    ScenarioSpec(
        "feedback_response",
        ("professionalism", "communication"),
        "review_feedback",
        True,
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
    """Return the open requirement change, or the most recent historical one."""
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
        .order_by(WorkflowEvent.occurred_at.desc(), WorkflowEvent.created_at.desc())
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
    if event.event_type != WorkflowEventType.REQUIREMENT_CHANGE:
        metadata = _event_metadata(event)
        key = metadata.get("scenario_key") or EVENT_TYPE_TO_SCENARIO.get(event.event_type)
        if key and key in _CATALOG_BY_KEY:
            return acknowledge_scenario(db, simulation, task, event, student_id)
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
    if event.event_type != WorkflowEventType.REQUIREMENT_CHANGE:
        metadata = _event_metadata(event)
        key = metadata.get("scenario_key") or EVENT_TYPE_TO_SCENARIO.get(event.event_type)
        if key and key in _CATALOG_BY_KEY:
            return record_scenario_reply(db, simulation, task, event, student_id, response)
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
        "action_id": str(uuid.uuid4()),
        "action": kind,
        "detail": note,
        "at": datetime.now(timezone.utc).isoformat(),
        "student_id": student_id,
        "event_id": str(event.id),
        "criteria_version_id": metadata.get("criteria_version_id"),
        "criteria_version_number": metadata.get("criteria_version_number"),
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
    recorded_reply = _recorded_requirement_reply(db, simulation, event, student_id)
    later_actions = []
    seen_actions = set()
    for item in metadata.get("actions") or []:
        acted_at = _parse_time(item.get("at"))
        action_key = item.get("action_id") or (item.get("action"), item.get("at"), item.get("detail"))
        if action_key in seen_actions:
            continue
        seen_actions.add(action_key)
        if (
            item.get("action") == "adapted_work"
            and item.get("event_id") in (None, str(event.id))
            and item.get("criteria_version_id") in (None, metadata.get("criteria_version_id"))
            and item.get("criteria_version_number") in (None, metadata.get("criteria_version_number"))
            and replied_at is not None
            and acted_at is not None
            and acted_at >= replied_at
        ):
            later_actions.append(item)

    task_evidence = _supporting_submission(db, task, student_id, event, later_actions)
    communication = _communication_observation(reply, criterion, recorded=recorded_reply is not None)
    adaptability = _adaptability_observation(later_actions, criterion, task_evidence)
    rows = {
        "communication": _upsert_evidence(
            db, simulation, task, event, student_id, "communication", communication, reply, later_actions,
            supporting_evidence={
                "message_id": str(recorded_reply.id),
                "rubric": communication.get("rubric", {}),
            } if recorded_reply else {},
            preserve_existing=True,
        ),
        "adaptability": _upsert_evidence(
            db, simulation, task, event, student_id, "adaptability", adaptability, reply, later_actions,
            supporting_evidence={
                "action_id": task_evidence["action_id"],
                "submission_id": task_evidence["submission_id"],
                "submission_version_id": task_evidence["submission_version_id"],
                "criteria_version_id": task_evidence["criteria_version_id"],
                "criteria_version_number": task_evidence["criteria_version_number"],
            } if task_evidence else {},
        ),
    }
    db.flush()
    return _assessment_payload(rows)


def _recorded_requirement_reply(
    db: Session, simulation: Simulation, event: WorkflowEvent, student_id: str
) -> Optional[Message]:
    message_id = _event_metadata(event).get("student_message_id")
    if not message_id:
        return None
    try:
        message_uuid = uuid.UUID(str(message_id))
    except (ValueError, TypeError):
        return None
    message = db.get(Message, message_uuid)
    if (
        message is None
        or message.company_id != simulation.company_id
        or message.sender_type != MessageSenderType.STUDENT
        or message.sender_id != student_id
        or message.content.strip() != (event.response or "").strip()
    ):
        return None
    return message


def _supporting_submission(
    db: Session,
    task: Task,
    student_id: str,
    event: WorkflowEvent,
    actions: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Find a real submitted artifact for this exact criteria version, after the adaptation."""
    if not actions:
        return None
    metadata = _event_metadata(event)
    criteria_id = metadata.get("criteria_version_id")
    criteria_number = metadata.get("criteria_version_number")
    action_times = [_parse_time(item.get("at")) for item in actions]
    action_times = [value for value in action_times if value is not None]
    if not action_times or not criteria_id:
        return None
    versions = db.execute(
        select(SubmissionVersion, Submission)
        .join(Submission, Submission.id == SubmissionVersion.submission_id)
        .where(
            Submission.task_id == task.id,
            Submission.student_id == student_id,
            SubmissionVersion.status == SubmissionStatus.SUBMITTED,
            SubmissionVersion.submitted_at.is_not(None),
        )
        .order_by(SubmissionVersion.submitted_at.desc(), SubmissionVersion.version_number.desc())
    ).all()
    for version, submission in versions:
        submitted_at = _aware(version.submitted_at)
        if submitted_at is None or submitted_at < max(action_times):
            continue
        version_identity = (version.version_metadata or {}).get("acceptance_criteria_version") or {}
        if (
            version_identity.get("id") != criteria_id
            or version_identity.get("version_number") != criteria_number
        ):
            continue
        submitted_code = (version.version_metadata or {}).get("code")
        metadata_has_artifact = (
            isinstance(submitted_code, str)
            and len(submitted_code.strip()) >= 20
            and any(marker in submitted_code.lower() for marker in (
                "def ", "class ", "function ", "return ", "const ", "=>", "{", "</"
            ))
        )
        if not version.files and not metadata_has_artifact:
            continue
        return {
            "action_id": actions[-1].get("action_id"),
            "submission_id": str(submission.id),
            "submission_version_id": str(version.id),
            "criteria_version_id": criteria_id,
            "criteria_version_number": criteria_number,
            "submitted_at": submitted_at.isoformat(),
        }
    return None


def assess_requirement_changes_for_submission(
    db: Session, simulation: Simulation, task: Task, student_id: str
) -> List[Dict[str, Any]]:
    """Reassess only events with explicit adaptation actions after a new task artifact is stored."""
    events = db.execute(
        select(WorkflowEvent).where(
            WorkflowEvent.simulation_id == simulation.id,
            WorkflowEvent.task_id == task.id,
            WorkflowEvent.event_type == WorkflowEventType.REQUIREMENT_CHANGE,
            WorkflowEvent.response.is_not(None),
        ).order_by(WorkflowEvent.occurred_at, WorkflowEvent.id)
    ).scalars().all()
    updated = []
    for event in events:
        if not any(item.get("action") == "adapted_work" for item in (_event_metadata(event).get("actions") or [])):
            continue
        assessment = assess_requirement_change(db, simulation, task, event, student_id)
        _resolve_when_adaptation_scored(db, event, assessment)
        updated.append(serialize_workflow_event(db, event, task))
    return updated


def _communication_observation(reply: str, criterion: str, *, recorded: bool) -> Dict[str, Any]:
    if not recorded:
        return {
            "status": INSUFFICIENT,
            "score": None,
            "summary": "No verifiable student reply is linked to this requirement change.",
            "reason": "missing_recorded_reply",
        }
    words = _tokens(reply)
    if len(words) < 5 or words <= {"ok", "okay", "sure", "yes", "understood", "noted", "thanks", "got", "it"}:
        return {
            "status": INSUFFICIENT,
            "score": None,
            "summary": "The recorded reply is too brief to assess communication about the requirement.",
            "reason": "reply_too_superficial",
            "rubric": {"relevance": False, "clarity": False, "completeness": False, "responsiveness": False},
        }

    stopwords = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "i", "in", "is", "it",
        "of", "on", "or", "that", "the", "this", "to", "we", "will", "with", "you", "your",
        "new", "change", "changed", "requirement", "criteria", "criterion", "task", "work", "updated",
    }
    semantic_groups = {
        "validation": {"validate", "validation", "valid", "check", "sanitize", "reject", "guard"},
        "input": {"input", "payload", "request", "parameter", "parameters", "value", "data"},
        "error": {"error", "invalid", "failure", "exception", "malformed", "malform", "bad"},
        "response": {"response", "message", "return", "returning", "show", "display"},
    }
    aliases = {word: label for label, values in semantic_groups.items() for word in values}

    def normalize(token: str) -> str:
        for suffix in ("ing", "ed", "s"):
            if len(token) > len(suffix) + 3 and token.endswith(suffix):
                return token[:-len(suffix)]
        return token

    response_terms = {aliases.get(normalize(token), normalize(token)) for token in words if token not in stopwords}
    criterion_terms = {
        aliases.get(normalize(token), normalize(token))
        for token in _tokens(criterion) if token not in stopwords and len(token) > 2
    }
    overlap = response_terms & criterion_terms
    overlap_required = min(2, len(criterion_terms))
    relevant = bool(criterion_terms) and len(overlap) >= max(1, overlap_required)

    lower = reply.lower()
    has_plan_subject = bool(re.search(r"\b(i|we)\b", lower))
    has_action = bool(re.search(
        r"\b(update|add|implement|revise|validate|return|build|write|document|include|cover|check|test|verify|confirm|ensure|investigate|fix|deliver)\w*\b",
        lower,
    ))
    has_verification = bool(re.search(
        r"\b(test|verify|check|confirm|ensure|validate|failure|invalid|error|result|review|coverage|pass|fail)\w*\b",
        lower,
    ))
    has_sequence = bool(re.search(r"\b(and|then|before|after|so that)\b", lower))
    clarity = has_plan_subject and has_action and len(response_terms) >= 4
    completeness = clarity and relevant and has_verification and has_sequence
    responsiveness = relevant and has_plan_subject and has_action
    rubric = {
        "relevance": relevant,
        "clarity": clarity,
        "completeness": completeness,
        "responsiveness": responsiveness,
        "criterion_terms_matched": sorted(overlap),
    }

    if not relevant:
        return {
            "status": SCORED,
            "score": 35.0,
            "summary": "The recorded reply does not address the changed requirement.",
            "reason": "recorded_reply_unrelated_to_change",
            "rubric": rubric,
        }
    if completeness and responsiveness and len(words) >= 12:
        return {
            "status": SCORED,
            "score": 82.0,
            "summary": "The reply is relevant, clear, responsive, and includes a concrete verification step.",
            "reason": "complete_relevant_workplace_reply",
            "rubric": rubric,
        }
    if responsiveness and clarity:
        return {
            "status": SCORED,
            "score": 62.0,
            "summary": "The reply addresses the requirement and states an action, but leaves the plan incomplete.",
            "reason": "relevant_but_incomplete_workplace_reply",
            "rubric": rubric,
        }
    return {
        "status": SCORED,
        "score": 45.0,
        "summary": "The reply mentions the changed requirement but is too vague to show a clear response plan.",
        "reason": "relevant_but_vague_workplace_reply",
        "rubric": rubric,
    }


def _adaptability_observation(
    actions: Sequence[Dict[str, Any]], criterion: str, task_evidence: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    authored = [item for item in actions if item.get("action") == "adapted_work" and _references_change(item.get("detail") or "", criterion)]
    if authored and task_evidence:
        return {
            "status": SCORED,
            "score": 84.0,
            "summary": "A later action describes work changed for the new requirement.",
            "reason": "subsequent_action_references_change",
        }
    if actions and task_evidence:
        return {
            "status": SCORED,
            "score": 35.0,
            "summary": "A task artifact was submitted for the changed criteria, but the recorded action does not explain the adaptation.",
            "reason": "submitted_artifact_without_relevant_adaptation",
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
    supporting_evidence: Optional[Dict[str, Any]] = None,
    preserve_existing: bool = False,
    scenario_key: Optional[str] = None,
) -> SoftSkillEvidence:
    key = scenario_key or _event_metadata(event).get("scenario_key") or REQUIREMENT_CHANGE
    row = db.execute(
        select(SoftSkillEvidence).where(
            SoftSkillEvidence.event_id == event.id,
            SoftSkillEvidence.competency == competency,
        )
    ).scalar_one_or_none()
    # Immutable reply assessments must not be overwritten once scored.
    if row is not None and preserve_existing:
        return row
    if row is None:
        row = SoftSkillEvidence(
            student_id=student_id,
            simulation_id=simulation.id,
            task_id=task.id,
            event_id=event.id,
            scenario_key=key,
            competency=competency,
        )
        db.add(row)
    row.student_id = student_id
    row.simulation_id = simulation.id
    row.task_id = task.id
    row.scenario_key = key
    row.status = observation["status"]
    row.score = observation["score"]
    row.summary = observation["summary"]
    evidence_dict: Dict[str, Any] = {
        "reason": observation["reason"],
        "reply": reply,
        "scenario_key": key,
        "subsequent_actions": [dict(item) for item in actions],
        "supporting_evidence": supporting_evidence or {},
    }
    if key == REQUIREMENT_CHANGE:
        evidence_dict.update({
            "added_criterion": (_event_metadata(event).get("added_criterion") or event.updated_requirement),
            "requirement_change_event_id": str(event.id),
            "criteria_version_id": _event_metadata(event).get("criteria_version_id"),
            "criteria_version_number": _event_metadata(event).get("criteria_version_number"),
        })
    row.evidence = evidence_dict
    flag_modified(row, "evidence")
    db.add(row)
    db.flush()
    return row


upsert_soft_skill_observation = _upsert_evidence


def _assessment_payload(rows: Dict[str, SoftSkillEvidence]) -> Dict[str, Any]:
    communication = _public_observation(rows["communication"])
    adaptability = _public_observation(rows["adaptability"])
    status = SCORED if communication["status"] == SCORED and adaptability["status"] == SCORED else INSUFFICIENT
    return {
        "status": status,
        "communication": communication,
        "adaptability": adaptability,
    }


def _general_assessment_payload(rows: Dict[str, SoftSkillEvidence]) -> Dict[str, Any]:
    all_scored = bool(rows) and all(r.status == SCORED for r in rows.values())
    payload: Dict[str, Any] = {
        "status": SCORED if all_scored else INSUFFICIENT,
    }
    for comp, row in rows.items():
        payload[comp] = _public_observation(row)
    return payload


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
    metadata = _event_metadata(event)
    scenario_key = metadata.get("scenario_key")
    if (
        event.event_type == WorkflowEventType.REQUIREMENT_CHANGE
        or scenario_key == REQUIREMENT_CHANGE
    ):
        if "communication" in by_competency and "adaptability" in by_competency:
            return _assessment_payload(by_competency)
        empty = {"status": INSUFFICIENT, "score": None, "evidence_id": None, "summary": "No evidence recorded yet."}
        return {
            "status": INSUFFICIENT,
            "communication": _public_observation(by_competency["communication"]) if "communication" in by_competency else empty,
            "adaptability": _public_observation(by_competency["adaptability"]) if "adaptability" in by_competency else dict(empty),
        }

    spec = _CATALOG_BY_KEY.get(scenario_key or "")
    target_competencies = spec.competencies if spec else tuple(by_competency.keys())

    result: Dict[str, Any] = {}
    all_scored = bool(target_competencies) and all(
        c in by_competency and by_competency[c].status == SCORED for c in target_competencies
    )
    result["status"] = SCORED if all_scored else INSUFFICIENT
    for c in target_competencies:
        if c in by_competency:
            result[c] = _public_observation(by_competency[c])
        else:
            result[c] = {"status": INSUFFICIENT, "score": None, "evidence_id": None, "summary": "No evidence recorded yet."}
    return result


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
    is_workplace_scenario = is_requirement or (scenario_key in _CATALOG_BY_KEY)
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
        "assessment": assessment_for_event(db, event) if is_workplace_scenario else None,
    }
    payload["phase"] = workplace_phase(payload) if is_workplace_scenario else "idle"
    return payload


def workplace_phase(event: Dict[str, Any]) -> str:
    """Shared phase names for the workplace simulation UI."""
    if not event:
        return "idle"
    event_type = event.get("event_type")
    scenario_key = event.get("scenario_key")
    if event_type != REQUIREMENT_CHANGE and scenario_key not in _CATALOG_BY_KEY:
        return "idle"
    if event.get("status") == WorkflowEventStatus.PENDING.value:
        return "acknowledge"
    if not event.get("response"):
        return "reply"
    if scenario_key == REQUIREMENT_CHANGE or event_type == REQUIREMENT_CHANGE:
        described = any(item.get("action") == "adapted_work" for item in (event.get("subsequent_actions") or []))
        if not described:
            return "adapt"
        adaptability = ((event.get("assessment") or {}).get("adaptability") or {})
        return "assessed" if adaptability.get("status") == SCORED else "verify"
    assessment = event.get("assessment") or {}
    return "assessed" if assessment.get("status") == SCORED else "completed"


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


# =========================================================================
# ADDITIONAL WORKPLACE SCENARIOS: COLLABORATION, TIME MANAGEMENT,
# PRIORITIZATION, OWNERSHIP, FEEDBACK RESPONSE
# =========================================================================

SCENARIO_EVENT_TYPES: Dict[str, WorkflowEventType] = {
    REQUIREMENT_CHANGE: WorkflowEventType.REQUIREMENT_CHANGE,
    COLLABORATION: WorkflowEventType.COLLABORATION_REQUEST,
    TIME_MANAGEMENT: WorkflowEventType.SCHEDULE_CONFLICT,
    PRIORITIZATION: WorkflowEventType.COMPETING_WORK,
    OWNERSHIP: WorkflowEventType.UNOWNED_ISSUE,
    FEEDBACK_RESPONSE: WorkflowEventType.REVIEW_FEEDBACK,
}

EVENT_TYPE_TO_SCENARIO: Dict[WorkflowEventType, str] = {
    WorkflowEventType.REQUIREMENT_CHANGE: REQUIREMENT_CHANGE,
    WorkflowEventType.COLLABORATION_REQUEST: COLLABORATION,
    WorkflowEventType.SCHEDULE_CONFLICT: TIME_MANAGEMENT,
    WorkflowEventType.COMPETING_WORK: PRIORITIZATION,
    WorkflowEventType.UNOWNED_ISSUE: OWNERSHIP,
    WorkflowEventType.REVIEW_FEEDBACK: FEEDBACK_RESPONSE,
}


def _open_scenario_event(
    db: Session, simulation: Simulation, task: Task, scenario_key: str
) -> Optional[WorkflowEvent]:
    event_type = SCENARIO_EVENT_TYPES.get(scenario_key)
    events = db.execute(
        select(WorkflowEvent)
        .where(
            WorkflowEvent.simulation_id == simulation.id,
            WorkflowEvent.task_id == task.id,
            WorkflowEvent.status.in_([
                WorkflowEventStatus.PENDING,
                WorkflowEventStatus.ACKNOWLEDGED,
            ]),
        )
        .order_by(WorkflowEvent.occurred_at)
    ).scalars().all()
    for e in events:
        if (event_type and e.event_type == event_type) or _event_metadata(e).get("scenario_key") == scenario_key:
            return e
    return None


def _existing_scenario_event(
    db: Session, simulation: Simulation, task: Task, scenario_key: str
) -> Optional[WorkflowEvent]:
    event_type = SCENARIO_EVENT_TYPES.get(scenario_key)
    events = db.execute(
        select(WorkflowEvent)
        .where(
            WorkflowEvent.simulation_id == simulation.id,
            WorkflowEvent.task_id == task.id,
        )
        .order_by(WorkflowEvent.occurred_at.desc(), WorkflowEvent.created_at.desc())
    ).scalars().all()
    for e in events:
        if (event_type and e.event_type == event_type) or _event_metadata(e).get("scenario_key") == scenario_key:
            return e
    return None


def find_scenario_event(
    db: Session, simulation: Simulation, task: Task, scenario_key: str
) -> Optional[WorkflowEvent]:
    return _open_scenario_event(db, simulation, task, scenario_key) or _existing_scenario_event(db, simulation, task, scenario_key)


def _scenario_initial_message(
    db: Session,
    simulation: Simulation,
    task: Task,
    event: WorkflowEvent,
    scenario_key: str,
) -> Message:
    manager = db.execute(select(Manager).where(Manager.company_id == simulation.company_id)).scalars().first()
    manager_id = str(manager.id) if manager is not None else "manager"

    if scenario_key == COLLABORATION:
        return Message(
            company_id=simulation.company_id,
            sender_type=MessageSenderType.TEAM_MEMBER,
            sender_id="teammate_alex",
            content=(
                f"Hi! I am working on a feature that interfaces with '{task.title}'. "
                "Can we coordinate our API endpoints and integration schedule so neither of us gets blocked? "
                "Please reply with your planned approach and availability."
            ),
        )
    elif scenario_key == TIME_MANAGEMENT:
        return Message(
            company_id=simulation.company_id,
            sender_type=MessageSenderType.MANAGER,
            sender_id=manager_id,
            content=(
                f"Important update on '{task.title}': The client moved our milestone forward, creating a schedule conflict. "
                "How do you plan to adjust your timeline, prioritize tasks, and ensure delivery without compromising quality?"
            ),
        )
    elif scenario_key == PRIORITIZATION:
        return Message(
            company_id=simulation.company_id,
            sender_type=MessageSenderType.MANAGER,
            sender_id=manager_id,
            content=(
                f"An urgent blocker was escalated while you are working on '{task.title}'. "
                "We need to decide whether to pause your current task or finish it first. "
                "What is your decision, and what criteria did you use to prioritize?"
            ),
        )
    elif scenario_key == OWNERSHIP:
        return Message(
            company_id=simulation.company_id,
            sender_type=MessageSenderType.TEAM_MEMBER,
            sender_id="teammate_jordan",
            content=(
                f"We noticed an unassigned issue in the backlog impacting '{task.title}'. "
                "No one has claimed ownership of it yet. "
                "Would you be able to take ownership or outline an action plan to resolve it?"
            ),
        )
    elif scenario_key == FEEDBACK_RESPONSE:
        return Message(
            company_id=simulation.company_id,
            sender_type=MessageSenderType.MANAGER,
            sender_id=manager_id,
            content=(
                f"Code review feedback for '{task.title}': The reviewer requested changes regarding error handling, "
                "edge case validation, and test coverage. "
                "Please review the feedback and reply with how you plan to address these points."
            ),
        )
    else:
        return _manager_message(db, simulation, task, event)


def issue_scenario(
    db: Session,
    simulation: Simulation,
    task: Task,
    scenario_key: str,
    *,
    trigger_activity: Optional[str] = None,
    scenario: Optional[Scenario] = None,
) -> Optional[WorkflowEvent]:
    """Persist one open workplace scenario event for a task.

    Returns the new event, or None when this task already has an open
    event for this scenario.
    """
    spec = _CATALOG_BY_KEY.get(scenario_key)
    if spec is None:
        raise WorkplaceScenarioError(f"Unknown workplace scenario '{scenario_key}'", 404)
    if not spec.implemented:
        raise WorkplaceScenarioError(f"Workplace scenario '{scenario_key}' is not implemented yet.", 501)

    if scenario_key == REQUIREMENT_CHANGE:
        return issue_requirement_change(
            db, simulation, task,
            trigger_activity=trigger_activity or spec.trigger_activity,
            scenario=scenario,
        )

    activity = _require_activity(trigger_activity or spec.trigger_activity)
    if activity in _STATUS_ONLY_ACTIVITIES:
        raise WorkplaceScenarioError(
            f"{scenario_key} scenarios follow recorded student work, not task start or a status change.",
            400,
        )
    if task.status not in {TaskStatus.IN_PROGRESS, TaskStatus.CHANGES_REQUESTED, TaskStatus.UNDER_REVIEW}:
        raise WorkplaceScenarioError(
            f"A {scenario_key} scenario is issued only while the task is active or in progress.",
            409,
        )

    db.execute(
        select(Simulation).where(Simulation.id == simulation.id).with_for_update()
    )
    if _open_scenario_event(db, simulation, task, scenario_key) is not None:
        return None

    event_type = SCENARIO_EVENT_TYPES[scenario_key]
    title_map = {
        COLLABORATION: f"Collaboration request: {task.title}",
        TIME_MANAGEMENT: f"Schedule conflict: {task.title}",
        PRIORITIZATION: f"Competing work priorities: {task.title}",
        OWNERSHIP: f"Unowned issue: {task.title}",
        FEEDBACK_RESPONSE: f"Code review feedback: {task.title}",
    }
    message_map = {
        COLLABORATION: (
            f"A teammate reached out regarding '{task.title}' to coordinate interface contracts and timeline. "
            "Please acknowledge this request and coordinate your response."
        ),
        TIME_MANAGEMENT: (
            f"The release milestone for '{task.title}' has shifted forward, causing a schedule conflict. "
            "Review the timeline and acknowledge the situation."
        ),
        PRIORITIZATION: (
            f"A critical production issue arose while '{task.title}' is in progress. "
            "Acknowledge the competing priorities and choose which item to address first."
        ),
        OWNERSHIP: (
            f"An unassigned defect has been identified in the backlog impacting '{task.title}'. "
            "Acknowledge the notice and state how you will approach ownership."
        ),
        FEEDBACK_RESPONSE: (
            f"Code review feedback was submitted for '{task.title}' requesting changes before approval. "
            "Review the feedback notes and acknowledge the review."
        ),
    }

    event = WorkflowEvent(
        simulation_id=simulation.id,
        task_id=task.id,
        scenario_id=scenario.id if scenario is not None else simulation.current_scenario_id,
        event_type=event_type,
        title=title_map.get(scenario_key, f"{scenario_key}: {task.title}"),
        message=message_map.get(scenario_key, f"Workplace event for '{task.title}'."),
        description=spec.summary,
        trigger_condition=f"{scenario_key}:{activity}",
        action_required=True,
        occurred_at=datetime.now(timezone.utc),
        status=WorkflowEventStatus.PENDING,
        metadata_json={
            "scenario_key": scenario_key,
            "trigger_activity": activity,
            "actions": [],
        },
    )
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
    except IntegrityError:
        return _open_scenario_event(db, simulation, task, scenario_key) or _existing_scenario_event(db, simulation, task, scenario_key)
    return event


def acknowledge_scenario(
    db: Session,
    simulation: Simulation,
    task: Task,
    event: WorkflowEvent,
    student_id: str,
) -> Dict[str, Any]:
    if event.simulation_id != simulation.id or event.task_id != task.id:
        raise WorkplaceScenarioError("Event does not belong to this task.", 403)

    metadata = _event_metadata(event)
    scenario_key = metadata.get("scenario_key") or EVENT_TYPE_TO_SCENARIO.get(event.event_type)
    if not scenario_key:
        raise WorkplaceScenarioError("Not a recognized workplace scenario event.", 400)

    if scenario_key == REQUIREMENT_CHANGE:
        return acknowledge_requirement_change(db, simulation, task, event, student_id)

    current_status = _status_value(event)
    if current_status in (WorkflowEventStatus.RESOLVED.value, WorkflowEventStatus.DISMISSED.value):
        raise WorkplaceScenarioError("This scenario event is already completed.", 409)

    if current_status == WorkflowEventStatus.PENDING.value:
        event.status = WorkflowEventStatus.ACKNOWLEDGED
        metadata["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
        metadata["acknowledged_by"] = student_id
        if not metadata.get("manager_message_id"):
            message = _scenario_initial_message(db, simulation, task, event, scenario_key)
            db.add(message)
            db.flush()
            metadata["manager_message_id"] = str(message.id)
            metadata["manager_message"] = message.content
        _save_metadata(event, metadata)
        db.add(event)
        db.flush()
    return serialize_workflow_event(db, event, task)


def _recorded_student_reply(
    db: Session, simulation: Simulation, event: WorkflowEvent, student_id: str
) -> Optional[Message]:
    message_id = _event_metadata(event).get("student_message_id")
    if not message_id:
        return None
    try:
        message_uuid = uuid.UUID(str(message_id))
    except (ValueError, TypeError):
        return None
    message = db.get(Message, message_uuid)
    if (
        message is None
        or message.company_id != simulation.company_id
        or message.sender_type != MessageSenderType.STUDENT
        or message.sender_id != student_id
        or message.content.strip() != (event.response or "").strip()
    ):
        return None
    return message


def record_scenario_reply(
    db: Session,
    simulation: Simulation,
    task: Task,
    event: WorkflowEvent,
    student_id: str,
    response: str,
) -> Dict[str, Any]:
    if event.simulation_id != simulation.id or event.task_id != task.id:
        raise WorkplaceScenarioError("Event does not belong to this task.", 403)

    text = (response or "").strip()
    if not text:
        raise WorkplaceScenarioError("A reply is required.", 400)

    metadata = _event_metadata(event)
    scenario_key = metadata.get("scenario_key") or EVENT_TYPE_TO_SCENARIO.get(event.event_type)
    if not scenario_key:
        raise WorkplaceScenarioError("Not a recognized workplace scenario event.", 400)

    if scenario_key == REQUIREMENT_CHANGE:
        return record_requirement_reply(db, simulation, task, event, student_id, text)

    current_status = _status_value(event)
    if current_status == WorkflowEventStatus.PENDING.value:
        raise WorkplaceScenarioError("Acknowledge the scenario before replying.", 409)
    if event.response:
        raise WorkplaceScenarioError("A reply is already recorded for this scenario.", 409)
    if current_status in (WorkflowEventStatus.RESOLVED.value, WorkflowEventStatus.DISMISSED.value):
        raise WorkplaceScenarioError("This scenario event is already completed.", 409)

    event.response = text
    event.responded_at = datetime.now(timezone.utc)
    if current_status != WorkflowEventStatus.ACKNOWLEDGED.value:
        event.status = WorkflowEventStatus.ACKNOWLEDGED

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

    assessment = assess_scenario(db, simulation, task, event, student_id)
    if assessment.get("status") == SCORED:
        event.status = WorkflowEventStatus.RESOLVED
        db.add(event)
        db.flush()

    payload = serialize_workflow_event(db, event, task)
    payload["assessment"] = assessment
    return payload


# --- RUBRICS FOR THE 5 WORKPLACE SCENARIOS ---

def _collaboration_observation(reply: str, task: Task, *, recorded: bool) -> Dict[str, Any]:
    if not recorded:
        return {
            "status": INSUFFICIENT,
            "score": None,
            "summary": "No verifiable student reply is linked to this collaboration request.",
            "reason": "missing_recorded_reply",
        }
    words = _tokens(reply)
    if len(words) < 5 or words <= {"ok", "okay", "sure", "yes", "understood", "noted", "thanks", "got", "it"}:
        return {
            "status": INSUFFICIENT,
            "score": None,
            "summary": "The recorded reply is too brief to assess collaboration communication.",
            "reason": "reply_too_superficial",
            "rubric": {"coordination": False, "timeline": False, "actionable": False},
        }

    lower = reply.lower()
    coordination_tokens = {
        "coordinate", "sync", "share", "align", "collaborate", "api", "schema",
        "contract", "interface", "endpoint", "dependency", "branch", "pr", "merge",
        "meeting", "call", "slack", "discuss", "integrate", "team", "together",
    }
    timeline_tokens = {
        "today", "tomorrow", "soon", "time", "hour", "hours", "afternoon",
        "morning", "timeline", "schedule", "when", "by", "eod", "asap",
    }
    action_tokens = {
        "will", "plan", "let's", "can", "schedule", "send", "update", "document",
        "provide", "share", "push", "review", "start", "create",
    }

    has_coordination = bool(words & coordination_tokens)
    has_timeline = bool(words & timeline_tokens)
    has_action = bool(words & action_tokens)
    has_subject = bool(re.search(r"\b(i|we|let's)\b", lower))

    rubric = {
        "coordination": has_coordination,
        "timeline": has_timeline,
        "action": has_action and has_subject,
        "word_count": len(words),
    }

    if not has_coordination and not has_action:
        return {
            "status": SCORED,
            "score": 35.0,
            "summary": "The reply does not propose a collaborative approach or address coordination.",
            "reason": "unrelated_collaboration_reply",
            "rubric": rubric,
        }
    if has_coordination and has_timeline and has_action and len(words) >= 12:
        return {
            "status": SCORED,
            "score": 85.0,
            "summary": "The reply is highly collaborative, offering concrete coordination points, timeline alignment, and next steps.",
            "reason": "comprehensive_collaboration_reply",
            "rubric": rubric,
        }
    if has_coordination and (has_action or has_timeline):
        return {
            "status": SCORED,
            "score": 72.0,
            "summary": "The reply demonstrates willingness to coordinate and provides actionable next steps.",
            "reason": "actionable_collaboration_reply",
            "rubric": rubric,
        }
    return {
        "status": SCORED,
        "score": 52.0,
        "summary": "The reply acknowledges the collaboration request but lacks specific coordination details.",
        "reason": "vague_collaboration_reply",
        "rubric": rubric,
    }


def _time_management_observation(reply: str, task: Task, *, recorded: bool) -> Dict[str, Any]:
    if not recorded:
        return {
            "status": INSUFFICIENT,
            "score": None,
            "summary": "No verifiable student reply is linked to this schedule conflict.",
            "reason": "missing_recorded_reply",
        }
    words = _tokens(reply)
    if len(words) < 5 or words <= {"ok", "okay", "sure", "yes", "understood", "noted", "thanks", "got", "it"}:
        return {
            "status": INSUFFICIENT,
            "score": None,
            "summary": "The recorded reply is too brief to assess time management.",
            "reason": "reply_too_superficial",
            "rubric": {"replan": False, "prioritization": False, "timeline": False},
        }

    lower = reply.lower()
    time_tokens = {"deadline", "schedule", "timeline", "time", "hour", "hours", "day", "days", "milestone", "date", "calendar", "buffer"}
    replan_tokens = {"adjust", "replan", "reschedule", "prioritize", "break down", "focus", "split", "scope", "phase", "mvp", "deliver", "expedite", "finish", "reallocate", "optimize"}
    tradeoff_tokens = {"tradeoff", "compromise", "postpone", "defer", "simplify", "cut", "must-have", "nice-to-have", "critical", "core"}

    has_time = bool(words & time_tokens)
    has_replan = bool(words & replan_tokens)
    has_tradeoff = bool(words & tradeoff_tokens)
    has_subject_action = bool(re.search(r"\b(i|we)\b.*\b(will|can|plan|intend|focus|start|adjust)\b", lower))

    rubric = {
        "time_awareness": has_time,
        "replanning": has_replan,
        "tradeoffs": has_tradeoff,
        "concrete_plan": has_subject_action,
        "word_count": len(words),
    }

    if not has_time and not has_replan:
        return {
            "status": SCORED,
            "score": 35.0,
            "summary": "The reply does not address schedule adjustments or time management.",
            "reason": "unrelated_time_management_reply",
            "rubric": rubric,
        }
    if has_replan and has_time and (has_tradeoff or has_subject_action) and len(words) >= 12:
        return {
            "status": SCORED,
            "score": 88.0,
            "summary": "The reply presents a structured re-planning strategy with clear prioritization, timeline adjustments, and proactive communication.",
            "reason": "comprehensive_time_management_reply",
            "rubric": rubric,
        }
    if has_replan and (has_time or has_subject_action):
        return {
            "status": SCORED,
            "score": 72.0,
            "summary": "The reply acknowledges the time constraint and proposes realistic adjustments to the plan.",
            "reason": "actionable_time_management_reply",
            "rubric": rubric,
        }
    return {
        "status": SCORED,
        "score": 50.0,
        "summary": "The reply acknowledges the deadline change but lacks concrete re-planning steps.",
        "reason": "vague_time_management_reply",
        "rubric": rubric,
    }


def _prioritization_observation(reply: str, task: Task, *, recorded: bool) -> Dict[str, Any]:
    if not recorded:
        return {
            "status": INSUFFICIENT,
            "score": None,
            "summary": "No verifiable student reply is linked to this competing work scenario.",
            "reason": "missing_recorded_reply",
        }
    words = _tokens(reply)
    if len(words) < 5 or words <= {"ok", "okay", "sure", "yes", "understood", "noted", "thanks", "got", "it"}:
        return {
            "status": INSUFFICIENT,
            "score": None,
            "summary": "The recorded reply is too brief to assess decision making and prioritization.",
            "reason": "reply_too_superficial",
            "rubric": {"decision": False, "rationale": False, "impact": False},
        }

    lower = reply.lower()
    decision_tokens = {"prioritize", "choose", "first", "second", "focus", "decide", "defer", "postpone", "delegate", "handle", "address", "complete", "finish"}
    rationale_tokens = {"because", "since", "due", "as", "reason", "impact", "severity", "urgent", "critical", "risk", "blocker", "dependency", "customer", "business", "production", "important"}
    sequence_tokens = {"then", "after", "next", "subsequently", "once", "before", "meanwhile", "later"}

    has_decision = bool(words & decision_tokens)
    has_rationale = bool(words & rationale_tokens)
    has_sequence = bool(words & sequence_tokens)
    has_subject = bool(re.search(r"\b(i|we)\b", lower))

    rubric = {
        "decision_made": has_decision and has_subject,
        "rationale_provided": has_rationale,
        "sequencing": has_sequence,
        "word_count": len(words),
    }

    if not has_decision and not has_rationale:
        return {
            "status": SCORED,
            "score": 35.0,
            "summary": "The reply does not make a clear choice between competing priorities.",
            "reason": "unrelated_prioritization_reply",
            "rubric": rubric,
        }
    if has_decision and has_rationale and has_sequence and len(words) >= 12:
        return {
            "status": SCORED,
            "score": 86.0,
            "summary": "The reply makes a well-reasoned prioritization decision supported by impact analysis and sequencing.",
            "reason": "structured_prioritization_reply",
            "rubric": rubric,
        }
    if has_decision and has_rationale:
        return {
            "status": SCORED,
            "score": 74.0,
            "summary": "The reply clearly states which item to prioritize and provides justification.",
            "reason": "justified_prioritization_reply",
            "rubric": rubric,
        }
    return {
        "status": SCORED,
        "score": 52.0,
        "summary": "The reply indicates a preference but lacks substantive rationale or execution sequencing.",
        "reason": "vague_prioritization_reply",
        "rubric": rubric,
    }


def _ownership_observation(reply: str, task: Task, *, recorded: bool) -> Dict[str, Any]:
    if not recorded:
        return {
            "status": INSUFFICIENT,
            "score": None,
            "summary": "No verifiable student reply is linked to this unowned issue scenario.",
            "reason": "missing_recorded_reply",
        }
    words = _tokens(reply)
    if len(words) < 5 or words <= {"ok", "okay", "sure", "yes", "understood", "noted", "thanks", "got", "it"}:
        return {
            "status": INSUFFICIENT,
            "score": None,
            "summary": "The recorded reply is too brief to assess ownership and professionalism.",
            "reason": "reply_too_superficial",
            "rubric": {"ownership_stance": False, "action_plan": False, "professionalism": False},
        }

    lower = reply.lower()
    ownership_tokens = {"take", "own", "ownership", "responsible", "handle", "investigate", "fix", "reproduce", "resolve", "tackle", "lead", "help", "look", "check"}
    process_tokens = {"reproduce", "root cause", "debug", "test", "issue", "ticket", "log", "document", "escalate", "assign", "follow up", "verify", "diagnose", "solution"}
    accountability_tokens = {"i will", "i'll", "update the team", "keep posted", "commit", "ensure", "take care", "happy to"}

    has_ownership = bool(words & ownership_tokens)
    has_process = bool(words & process_tokens)
    has_accountability = any(phrase in lower for phrase in accountability_tokens) or bool(re.search(r"\b(i|we)\b.*\b(will|can|plan|am)\b", lower))

    rubric = {
        "ownership_stance": has_ownership,
        "process_awareness": has_process,
        "accountability": has_accountability,
        "word_count": len(words),
    }

    if not has_ownership and not has_accountability:
        return {
            "status": SCORED,
            "score": 35.0,
            "summary": "The reply demonstrates disengagement or refusal of responsibility without constructive delegation.",
            "reason": "unengaged_ownership_reply",
            "rubric": rubric,
        }
    if has_ownership and has_process and has_accountability and len(words) >= 12:
        return {
            "status": SCORED,
            "score": 88.0,
            "summary": "The student proactively takes ownership, outlines an investigation and resolution process, and communicates accountability.",
            "reason": "exemplary_ownership_reply",
            "rubric": rubric,
        }
    if has_ownership and (has_process or has_accountability):
        return {
            "status": SCORED,
            "score": 75.0,
            "summary": "The reply takes responsibility for the issue and proposes constructive next steps.",
            "reason": "proactive_ownership_reply",
            "rubric": rubric,
        }
    return {
        "status": SCORED,
        "score": 55.0,
        "summary": "The reply acknowledges the unowned issue but provides limited proactive engagement.",
        "reason": "vague_ownership_reply",
        "rubric": rubric,
    }


def _feedback_professionalism_observation(reply: str, task: Task, *, recorded: bool) -> Dict[str, Any]:
    if not recorded:
        return {
            "status": INSUFFICIENT,
            "score": None,
            "summary": "No verifiable student reply is linked to this feedback response.",
            "reason": "missing_recorded_reply",
        }
    words = _tokens(reply)
    if len(words) < 5 or words <= {"ok", "okay", "sure", "yes", "understood", "noted", "thanks", "got", "it"}:
        return {
            "status": INSUFFICIENT,
            "score": None,
            "summary": "The recorded reply is too brief to assess professional attitude toward feedback.",
            "reason": "reply_too_superficial",
            "rubric": {"receptive": False, "constructive": False},
        }

    lower = reply.lower()
    receptive_tokens = {"understand", "agree", "appreciate", "thanks", "thank you", "helpful", "point", "feedback", "valid", "makes sense", "good catch", "noted"}
    defensive_tokens = {"wrong", "disagree", "not my fault", "whatever", "ignore", "unfair", "annoying"}
    constructive_tokens = {"improve", "fix", "update", "refactor", "correct", "address", "resolve", "rework", "implement", "ensure"}

    has_receptive = bool(words & receptive_tokens)
    has_defensive = bool(words & defensive_tokens)
    has_constructive = bool(words & constructive_tokens)

    rubric = {
        "receptive": has_receptive,
        "constructive": has_constructive,
        "defensive": has_defensive,
        "word_count": len(words),
    }

    if has_defensive and not has_constructive:
        return {
            "status": SCORED,
            "score": 35.0,
            "summary": "The reply exhibits defensiveness rather than constructive receptiveness to review feedback.",
            "reason": "defensive_feedback_reply",
            "rubric": rubric,
        }
    if (has_receptive or has_constructive) and len(words) >= 12:
        return {
            "status": SCORED,
            "score": 85.0,
            "summary": "The reply demonstrates a mature, receptive, and constructive attitude toward code review feedback.",
            "reason": "constructive_receptive_feedback_reply",
            "rubric": rubric,
        }
    if has_receptive or has_constructive:
        return {
            "status": SCORED,
            "score": 70.0,
            "summary": "The reply constructively accepts the feedback and commits to resolving the issues.",
            "reason": "accepting_feedback_reply",
            "rubric": rubric,
        }
    return {
        "status": SCORED,
        "score": 50.0,
        "summary": "The reply acknowledges the feedback without clearly demonstrating receptiveness or improvement intent.",
        "reason": "vague_feedback_reply",
        "rubric": rubric,
    }


def _feedback_communication_observation(reply: str, task: Task, *, recorded: bool) -> Dict[str, Any]:
    if not recorded:
        return {
            "status": INSUFFICIENT,
            "score": None,
            "summary": "No verifiable student reply is linked to this feedback response.",
            "reason": "missing_recorded_reply",
        }
    words = _tokens(reply)
    if len(words) < 5 or words <= {"ok", "okay", "sure", "yes", "understood", "noted", "thanks", "got", "it"}:
        return {
            "status": INSUFFICIENT,
            "score": None,
            "summary": "The recorded reply is too brief to assess technical communication.",
            "reason": "reply_too_superficial",
            "rubric": {"clarity": False, "technical_detail": False},
        }

    lower = reply.lower()
    technical_tokens = {"test", "edge case", "validation", "error", "exception", "coverage", "handling", "check", "payload", "input", "null", "schema", "function", "refactor"}
    action_tokens = {"will", "plan", "add", "update", "modify", "implement", "include", "write", "rewrite"}

    has_technical = bool(words & technical_tokens)
    has_action = bool(words & action_tokens)
    has_subject = bool(re.search(r"\b(i|we)\b", lower))

    rubric = {
        "technical_detail": has_technical,
        "action_planned": has_action and has_subject,
        "word_count": len(words),
    }

    if not has_technical and not has_action:
        return {
            "status": SCORED,
            "score": 40.0,
            "summary": "The reply does not explain the technical changes planned to address the review.",
            "reason": "uninformative_feedback_communication",
            "rubric": rubric,
        }
    if has_technical and has_action and len(words) >= 12:
        return {
            "status": SCORED,
            "score": 86.0,
            "summary": "The reply communicates a specific and clear technical plan to address each review comment.",
            "reason": "detailed_feedback_communication",
            "rubric": rubric,
        }
    if has_technical or has_action:
        return {
            "status": SCORED,
            "score": 70.0,
            "summary": "The reply outlines the general direction of changes planned in response to feedback.",
            "reason": "adequate_feedback_communication",
            "rubric": rubric,
        }
    return {
        "status": SCORED,
        "score": 52.0,
        "summary": "The reply is somewhat vague regarding technical resolution steps.",
        "reason": "vague_feedback_communication",
        "rubric": rubric,
    }


def assess_scenario(
    db: Session,
    simulation: Simulation,
    task: Task,
    event: WorkflowEvent,
    student_id: str,
) -> Dict[str, Any]:
    metadata = _event_metadata(event)
    scenario_key = metadata.get("scenario_key") or EVENT_TYPE_TO_SCENARIO.get(event.event_type)
    if scenario_key == REQUIREMENT_CHANGE or event.event_type == WorkflowEventType.REQUIREMENT_CHANGE:
        return assess_requirement_change(db, simulation, task, event, student_id)

    reply = (event.response or "").strip()
    recorded_reply = _recorded_student_reply(db, simulation, event, student_id)
    is_recorded = recorded_reply is not None

    rows: Dict[str, SoftSkillEvidence] = {}
    supporting: Dict[str, Any] = {
        "message_id": str(recorded_reply.id) if recorded_reply else None,
        "scenario_key": scenario_key,
    }

    if scenario_key == COLLABORATION:
        obs = _collaboration_observation(reply, task, recorded=is_recorded)
        supporting["rubric"] = obs.get("rubric", {})
        rows["communication"] = _upsert_evidence(
            db, simulation, task, event, student_id, "communication", obs, reply, [],
            supporting_evidence=supporting, scenario_key=COLLABORATION,
        )
    elif scenario_key == TIME_MANAGEMENT:
        obs = _time_management_observation(reply, task, recorded=is_recorded)
        supporting["rubric"] = obs.get("rubric", {})
        rows["time_management"] = _upsert_evidence(
            db, simulation, task, event, student_id, "time_management", obs, reply, [],
            supporting_evidence=supporting, scenario_key=TIME_MANAGEMENT,
        )
    elif scenario_key == PRIORITIZATION:
        obs = _prioritization_observation(reply, task, recorded=is_recorded)
        supporting["rubric"] = obs.get("rubric", {})
        rows["decision_making"] = _upsert_evidence(
            db, simulation, task, event, student_id, "decision_making", obs, reply, [],
            supporting_evidence=supporting, scenario_key=PRIORITIZATION,
        )
    elif scenario_key == OWNERSHIP:
        obs = _ownership_observation(reply, task, recorded=is_recorded)
        supporting["rubric"] = obs.get("rubric", {})
        rows["professionalism"] = _upsert_evidence(
            db, simulation, task, event, student_id, "professionalism", obs, reply, [],
            supporting_evidence=supporting, scenario_key=OWNERSHIP,
        )
    elif scenario_key == FEEDBACK_RESPONSE:
        prof_obs = _feedback_professionalism_observation(reply, task, recorded=is_recorded)
        comm_obs = _feedback_communication_observation(reply, task, recorded=is_recorded)
        supporting["rubric_professionalism"] = prof_obs.get("rubric", {})
        supporting["rubric_communication"] = comm_obs.get("rubric", {})
        rows["professionalism"] = _upsert_evidence(
            db, simulation, task, event, student_id, "professionalism", prof_obs, reply, [],
            supporting_evidence=supporting, scenario_key=FEEDBACK_RESPONSE,
        )
        rows["communication"] = _upsert_evidence(
            db, simulation, task, event, student_id, "communication", comm_obs, reply, [],
            supporting_evidence=supporting, scenario_key=FEEDBACK_RESPONSE,
        )
    else:
        raise WorkplaceScenarioError(f"Unsupported scenario key: {scenario_key}", 400)

    db.flush()
    return _general_assessment_payload(rows)


# Convenience wrappers for specific scenarios

def issue_collaboration_scenario(
    db: Session, simulation: Simulation, task: Task, *, trigger_activity: str = "peer_request", scenario: Optional[Scenario] = None
) -> Optional[WorkflowEvent]:
    return issue_scenario(db, simulation, task, COLLABORATION, trigger_activity=trigger_activity, scenario=scenario)

def issue_time_management_scenario(
    db: Session, simulation: Simulation, task: Task, *, trigger_activity: str = "schedule_conflict", scenario: Optional[Scenario] = None
) -> Optional[WorkflowEvent]:
    return issue_scenario(db, simulation, task, TIME_MANAGEMENT, trigger_activity=trigger_activity, scenario=scenario)

def issue_prioritization_scenario(
    db: Session, simulation: Simulation, task: Task, *, trigger_activity: str = "competing_work", scenario: Optional[Scenario] = None
) -> Optional[WorkflowEvent]:
    return issue_scenario(db, simulation, task, PRIORITIZATION, trigger_activity=trigger_activity, scenario=scenario)

def issue_ownership_scenario(
    db: Session, simulation: Simulation, task: Task, *, trigger_activity: str = "unowned_issue", scenario: Optional[Scenario] = None
) -> Optional[WorkflowEvent]:
    return issue_scenario(db, simulation, task, OWNERSHIP, trigger_activity=trigger_activity, scenario=scenario)

def issue_feedback_response_scenario(
    db: Session, simulation: Simulation, task: Task, *, trigger_activity: str = "review_feedback", scenario: Optional[Scenario] = None
) -> Optional[WorkflowEvent]:
    return issue_scenario(db, simulation, task, FEEDBACK_RESPONSE, trigger_activity=trigger_activity, scenario=scenario)


def acknowledge_collaboration(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent, student_id: str) -> Dict[str, Any]:
    return acknowledge_scenario(db, simulation, task, event, student_id)

def record_collaboration_reply(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent, student_id: str, response: str) -> Dict[str, Any]:
    return record_scenario_reply(db, simulation, task, event, student_id, response)

def assess_collaboration(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent, student_id: str) -> Dict[str, Any]:
    return assess_scenario(db, simulation, task, event, student_id)

def acknowledge_time_management(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent, student_id: str) -> Dict[str, Any]:
    return acknowledge_scenario(db, simulation, task, event, student_id)

def record_time_management_reply(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent, student_id: str, response: str) -> Dict[str, Any]:
    return record_scenario_reply(db, simulation, task, event, student_id, response)

def assess_time_management(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent, student_id: str) -> Dict[str, Any]:
    return assess_scenario(db, simulation, task, event, student_id)

def acknowledge_prioritization(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent, student_id: str) -> Dict[str, Any]:
    return acknowledge_scenario(db, simulation, task, event, student_id)

def record_prioritization_reply(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent, student_id: str, response: str) -> Dict[str, Any]:
    return record_scenario_reply(db, simulation, task, event, student_id, response)

def assess_prioritization(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent, student_id: str) -> Dict[str, Any]:
    return assess_scenario(db, simulation, task, event, student_id)

def acknowledge_ownership(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent, student_id: str) -> Dict[str, Any]:
    return acknowledge_scenario(db, simulation, task, event, student_id)

def record_ownership_reply(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent, student_id: str, response: str) -> Dict[str, Any]:
    return record_scenario_reply(db, simulation, task, event, student_id, response)

def assess_ownership(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent, student_id: str) -> Dict[str, Any]:
    return assess_scenario(db, simulation, task, event, student_id)

def acknowledge_feedback_response(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent, student_id: str) -> Dict[str, Any]:
    return acknowledge_scenario(db, simulation, task, event, student_id)

def record_feedback_response_reply(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent, student_id: str, response: str) -> Dict[str, Any]:
    return record_scenario_reply(db, simulation, task, event, student_id, response)

def assess_feedback_response(db: Session, simulation: Simulation, task: Task, event: WorkflowEvent, student_id: str) -> Dict[str, Any]:
    return assess_scenario(db, simulation, task, event, student_id)
