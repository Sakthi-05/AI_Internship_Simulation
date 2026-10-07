"""
Proactive workplace chat: manager and teammates message the intern first.

The chat used to be a purely user-initiated chatbot — nothing ever happened
until the student typed something. This module flips that around so the chat
feels like an active workplace:

- Task updates, reminders, questions, feedback, meeting requests, and team
  discussions are generated from the student's *live* internship progress
  (current task, deadlines, sprint goal, review queue, meetings, ...).
- Messages are persisted as regular ``Message`` rows, so they remain visible
  when the student returns (logout/login, new tab, new device).
- Delivery happens through three paths that all funnel into
  :func:`maybe_send_proactive_message`:
  1. Lazy seeding — opening a chat thread generates at most one message when
     one is "due" (see timing model below), so threads never feel dead.
  2. A periodic APScheduler sweep (:func:`sweep_proactive_messages`) so new
     messages keep appearing over time, even when the student never opens
     the chat.
  3. Task-lifecycle triggers (simulation started, task started / submitted /
     completed) via the ``trigger`` argument, so progress gets a human
     reaction — still never more than one message per action.
- Timing model (feels like a real internship, never a burst):
  * At most one proactive message per action or per pacing window — multiple
    messages are never sent at once.
  * Event reactions respect a short global gap (``TRIGGER_MIN_GAP_SECONDS``)
    so rapid-fire actions don't produce rapid-fire messages.
  * Ambient messages (lazy seeding + sweep) respect a longer per-company gap
    (``AMBIENT_BASE_GAP_SECONDS`` plus deterministic per-company jitter), so
    arrivals are staggered across companies and over time.
  * Each category has its own minimum interval (``CATEGORY_MIN_INTERVAL``)
    and contextual age gates (:func:`_category_eligible`): greetings and
    task instructions go out early, follow-ups only after the intern has had
    time to work, feedback after review has had time to happen, reminders
    when deadlines approach.

Content is rendered from deterministic, context-filled templates, so the
service works with zero external dependencies or API keys. When
``GEMINI_API_KEY`` is configured, the async path asks Gemini for an
in-character rewrite of the same facts and falls back to the template on any
failure.
"""
import hashlib
import logging
import random
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.activity_log import ActivityLog
from app.models.company import Company
from app.models.enums import MessageSenderType, SimulationStatus, TaskStatus
from app.models.manager import Manager
from app.models.meeting import MeetingSchedule
from app.models.message import Message
from app.models.project import Project
from app.models.project_state import ProjectState
from app.models.simulation import Simulation
from app.models.sprint import Sprint
from app.models.task import Task
from app.models.team_member import TeamMember
from app.services.activity_log_service import log_activity

logger = logging.getLogger("aiwse.proactive_chat")

# --- Timing model -----------------------------------------------------------
# Event reactions (trigger=...) still send at most one message per action but
# respect this short global gap, so e.g. start → submit → complete in rapid
# succession does not produce a rapid sequence of messages.
TRIGGER_MIN_GAP_SECONDS = 60

# Base gap between two ambient (non-trigger) messages in one company. A
# deterministic per-company jitter (see ambient_gap_for) spreads companies out
# so arrivals feel staggered rather than scheduled.
AMBIENT_BASE_GAP_SECONDS = 360
AMBIENT_JITTER_SECONDS = 240

# Minimum spacing between two messages of the SAME category in one company.
# Greetings go out once and early; follow-ups, feedback, and reminders each
# have their own realistic cadence.
CATEGORY_MIN_INTERVAL: Dict[str, float] = {
    "welcome": float("inf"),  # once ever per company (downgraded afterwards)
    "task_update": 600,       # instructions land early, then rarely repeat
    "checkin": 900,           # follow-ups need breathing room
    "question": 900,
    "encouragement": 1800,
    "team_discussion": 1200,
    "meeting_request": 600,
    "feedback": 1200,         # reviews take time; never instant twice
    "reminder": 480,          # deadlines matter — shortest ambient cadence
}

# Contextual age gates: a follow-up only makes sense after the intern has had
# time to work; ambient feedback only after a submission has sat in review.
FOLLOWUP_MIN_TASK_AGE_SECONDS = 300
FEEDBACK_MIN_REVIEW_AGE_SECONDS = 300
# A reminder is only relevant when the deadline is near (or already missed).
REMINDER_DUE_WITHIN_SECONDS = 6 * 3600


def _proactive_action(category: str) -> str:
    return f"chat:proactive:{category}"

# Task states that mean "waiting on someone else" — prime feedback material.
_REVIEW_STATUSES = (
    TaskStatus.SUBMITTED,
    TaskStatus.UNDER_REVIEW,
    TaskStatus.MANAGER_APPROVAL,
)

_DONE_STATUSES = (TaskStatus.COMPLETED,)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Workplace context
# ---------------------------------------------------------------------------

def build_workplace_context(db: Session, company_id: uuid.UUID) -> Optional[Dict[str, Any]]:
    """Gather the live internship facts a proactive message is grounded in."""
    company = db.get(Company, company_id)
    if company is None:
        return None
    project = db.execute(
        select(Project).where(Project.company_id == company_id)
    ).scalars().first()
    if project is None:
        return None

    manager = db.execute(
        select(Manager).where(Manager.company_id == company_id)
    ).scalar_one_or_none()
    teammates = db.execute(
        select(TeamMember).where(TeamMember.company_id == company_id)
        .order_by(TeamMember.created_at)
    ).scalars().all()
    state = db.execute(
        select(ProjectState).where(ProjectState.project_id == project.id)
    ).scalar_one_or_none()

    # Active internship simulation for this company (newest in-progress wins).
    simulations = db.execute(
        select(Simulation).where(Simulation.company_id == company_id)
        .order_by(Simulation.created_at.desc())
    ).scalars().all()
    active_sim = next(
        (s for s in simulations if s.status == SimulationStatus.IN_PROGRESS),
        None,
    )

    current_task = None
    if active_sim is not None and active_sim.current_task_id:
        current_task = db.get(Task, active_sim.current_task_id)

    project_tasks: List[Task] = db.execute(
        select(Task).join(Sprint, Sprint.id == Task.sprint_id)
        .where(Sprint.project_id == project.id)
        .order_by(Sprint.sprint_number, Task.sequence)
    ).scalars().all()

    now = _now()
    in_progress = [t for t in project_tasks if t.status == TaskStatus.IN_PROGRESS]
    review_queue = [t for t in project_tasks if t.status in _REVIEW_STATUSES]
    overdue = [
        t for t in project_tasks
        if t.deadline and t.status not in _DONE_STATUSES
        and (_aware(t.deadline) or now) < now
    ]
    todo = [t for t in project_tasks if t.status == TaskStatus.TODO]
    completed = [t for t in project_tasks if t.status == TaskStatus.COMPLETED]
    # Not-yet-started work in path order (fresh internships sit in BACKLOG).
    upcoming = [t for t in project_tasks if t.status in (TaskStatus.BACKLOG, TaskStatus.TODO)]

    sprint = None
    if state is not None:
        sprint = db.execute(
            select(Sprint).where(
                Sprint.project_id == project.id,
                Sprint.sprint_number == state.current_sprint_number,
            )
        ).scalar_one_or_none()

    next_meeting = db.execute(
        select(MeetingSchedule).where(
            MeetingSchedule.project_id == project.id,
            MeetingSchedule.completed.is_(False),
            MeetingSchedule.scheduled_at >= now,
        ).order_by(MeetingSchedule.scheduled_at)
    ).scalars().first()

    last_proactive_at = db.execute(
        select(func.max(Message.created_at)).where(
            Message.company_id == company_id,
            Message.sender_type.in_(
                [MessageSenderType.MANAGER, MessageSenderType.TEAM_MEMBER]
            ),
        )
    ).scalar_one_or_none()
    last_proactive_at = _aware(last_proactive_at)
    proactive_count = db.execute(
        select(func.count(Message.id)).where(
            Message.company_id == company_id,
            Message.sender_type.in_(
                [MessageSenderType.MANAGER, MessageSenderType.TEAM_MEMBER]
            ),
        )
    ).scalar_one() or 0
    last_sender_type = None
    if last_proactive_at is not None:
        last_msg = db.execute(
            select(Message.sender_type).where(
                Message.company_id == company_id,
                Message.sender_type.in_(
                    [MessageSenderType.MANAGER, MessageSenderType.TEAM_MEMBER]
                ),
            ).order_by(Message.created_at.desc())
        ).scalars().first()
        last_sender_type = last_msg

    # Most recent send per category (from the namespaced activity log), so
    # each kind of message keeps its own realistic cadence.
    category_rows = db.execute(
        select(ActivityLog.action, func.max(ActivityLog.created_at)).where(
            ActivityLog.company_id == company_id,
            ActivityLog.action.like("chat:proactive:%"),
        ).group_by(ActivityLog.action)
    ).all()
    category_last_at = {
        action.split("chat:proactive:", 1)[1]: _aware(sent_at)
        for action, sent_at in category_rows
        if action and sent_at is not None
    }

    return {
        "company": company,
        "manager": manager,
        "teammates": list(teammates),
        "project": project,
        "state": state,
        "simulation": active_sim,
        "has_simulation": bool(simulations),
        "current_task": current_task,
        "in_progress": in_progress,
        "review_queue": review_queue,
        "overdue": overdue,
        "todo": todo,
        "upcoming": upcoming,
        "completed": completed,
        "total_tasks": len(project_tasks),
        "sprint": sprint,
        "next_meeting": next_meeting,
        "last_proactive_at": last_proactive_at,
        "last_sender_type": last_sender_type,
        "proactive_count": proactive_count,
        "category_last_at": category_last_at,
        "ambient_gap": ambient_gap_for(company_id),
        "now": now,
    }


def ambient_gap_for(company_id: uuid.UUID) -> float:
    """Per-company ambient gap: base plus deterministic jitter (6–10 min)."""
    digest = hashlib.sha256(f"ambient-gap:{company_id}".encode("utf-8")).hexdigest()
    return AMBIENT_BASE_GAP_SECONDS + (int(digest, 16) % (AMBIENT_JITTER_SECONDS + 1))


def _elapsed_since(moment: Optional[datetime], now: datetime) -> float:
    if moment is None:
        return float("inf")
    return (now - _aware(moment)).total_seconds()


# ---------------------------------------------------------------------------
# Message templates (deterministic, zero-dependency, context-filled)
# ---------------------------------------------------------------------------

def _fmt_when(deadline: Optional[datetime], now: datetime) -> str:
    deadline = _aware(deadline)
    if deadline is None:
        return "the deadline"
    delta = (deadline - now).total_seconds()
    if delta <= 0:
        overdue_by = -delta
        if overdue_by < 3600:
            return f"{int(overdue_by // 60)}m overdue"
        if overdue_by < 86400:
            return f"{int(overdue_by // 3600)}h overdue"
        return f"{int(overdue_by // 86400)}d overdue"
    if delta < 3600:
        return f"in ~{max(int(delta // 60), 1)}m"
    if delta < 86400:
        return f"in ~{int(delta // 3600)}h"
    return f"in ~{int(delta // 86400)}d"


def _fmt_meeting_when(scheduled_at: Optional[datetime], now: datetime) -> str:
    scheduled_at = _aware(scheduled_at)
    if scheduled_at is None:
        return "coming up"
    delta = (scheduled_at - now).total_seconds()
    if delta <= 0:
        return "starting now"
    if delta < 3600:
        return f"in ~{max(int(delta // 60), 1)}m"
    if delta < 86400:
        return f"in ~{int(delta // 3600)}h"
    return scheduled_at.strftime("on %a at %H:%M")


# Each category maps to several 1-3 sentence variants. Placeholders:
# {manager}, {mate}, {task}, {next}, {sprint}, {goal}, {project}, {company},
# {when}, {meeting}, {count}, {total}, {stack}
_MANAGER_TEMPLATES: Dict[str, List[str]] = {
    "reminder": [
        "Quick reminder — '{task}' is due {when}. Please push an update or flag any blockers before EOD.",
        "Heads up: '{task}' needs to land {when}. Let me know now if the scope needs adjusting.",
        "Checking on '{task}' — deadline is {when}. A short status note today would help.",
    ],
    "feedback": [
        "I saw your submission for '{task}' — nice work getting it in. I'll leave detailed review notes shortly; meanwhile, start scoping '{next}'.",
        "Thanks for submitting '{task}'. First pass looks solid — my full feedback is coming, keep momentum on '{next}' in the meantime.",
        "Reviewed '{task}' — good progress. A couple of follow-ups coming in the review; don't let '{next}' stall while you wait.",
    ],
    "meeting_request": [
        "Heads up — we have {meeting} {when}. Please come ready to walk through '{task}' and your blockers.",
        "Quick ask: {meeting} is {when}. I'd like a 2-minute update from you on '{task}' — where it stands and what's next.",
        "Putting {meeting} {when} on your radar. Bring your status on '{task}' plus one thing you need from the team.",
    ],
    "task_update": [
        "You're on '{task}' for {sprint} — {goal}. Ping me once you've started so I know it's in motion.",
        "Assigning '{task}' to you under {sprint}. {goal} Reply here when you kick it off.",
        "'{task}' is yours ({sprint}). {goal} Shout if anything in the spec looks off.",
    ],
    "checkin": [
        "Quick check-in — how is '{task}' going? Anything blocking you?",
        "How's '{task}' shaping up? No need for anything formal, just a line on where you are.",
        "Touching base on '{task}'. On track for {when}, or do we need to talk?",
    ],
    "question": [
        "Can you send me a quick status on '{task}' — where things stand and what's next?",
        "One question on '{task}': what's the biggest remaining risk, and how are you handling it?",
        "Walk me through your approach on '{task}' in a sentence or two when you get a moment.",
    ],
    "encouragement": [
        "Nice work — {count} task(s) closed on {project}. Keep that momentum going into {sprint}.",
        "Good progress on {project} ({count}/{total} done). The team notices — keep it up.",
        "Credit where it's due: '{task}' moving forward is exactly what {sprint} needed.",
    ],
    "welcome": [
        "Welcome to {company}! I'm {manager}, your manager for the {project} internship. Have a look at '{task}' to get started, and ask here any time.",
        "Hi — {manager} here. You'll be interning with us on {project}. Your first task is '{task}'; reply in this chat if anything is unclear.",
        "Welcome aboard! I'm {manager}. Take a look at '{task}' when you're settled — that's your starting point on {project}.",
    ],
}

_TEAMMATE_TEMPLATES: Dict[str, List[str]] = {
    "question": [
        "Hey, quick question on '{task}' — how are you approaching {stack}? I might be touching the same area.",
        "Curious how you handled {stack} in '{task}' — I hit something similar and I'm comparing notes.",
        "When you get a sec — for '{task}', did you go with the existing pattern or something new? Asking for my own task.",
    ],
    "task_update": [
        "FYI, I just {did} on my side — shouldn't block '{task}', but hollering in case it overlaps.",
        "Small update from me: {did}. Give a shout if it affects '{task}' and we can pair on it.",
        "Heads up — I {did} earlier. It touches the same module as '{task}', so let's sync before you submit.",
    ],
    "team_discussion": [
        "What does everyone think about {topic}? Dropping it here so we don't lose it before {meeting}.",
        "Random team thought: {topic}. Relevant to '{task}' too — curious about your take.",
        "Standup leftover: {topic}. {mate} and I were just discussing it — weigh in when you can.",
    ],
    "encouragement": [
        "Nice — heard '{task}' is moving. Holler if you want a second pair of eyes before review.",
        "Saw '{task}' progress — looking good! Happy to rubber-duck anything tricky.",
        "'{task}' is unblocked thanks to you — appreciate it. Let me know if I can return the favor.",
    ],
    "meeting_request": [
        "Are you joining {meeting} {when}? We need your update on '{task}'.",
        "Pinging the team: {meeting} is {when}. I'll cover my part — you good to present '{task}'?",
        "{meeting} {when} — don't miss it, your '{task}' update is on the agenda.",
    ],
}

_TEAM_DID_VARIANTS = [
    "merged the shared utils refactor",
    "updated the API contract docs",
    "fixed the flaky seed script",
    "pushed the migration for the staging DB",
    "cleaned up the old feature flags",
]

_TEAM_TOPICS = [
    "should we add contract tests for the new endpoints",
    "whether the deadline for the current sprint still looks realistic",
    "the plan for splitting review duties this week",
    "naming conventions for the new modules",
]


def _pick_variant(variants: List[str], seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return variants[int(digest, 16) % len(variants)]


def _manager_facts(ctx: Dict[str, Any]) -> Dict[str, str]:
    now = ctx["now"]
    task = ctx["current_task"] or (ctx["in_progress"] or ctx["overdue"] or ctx["todo"] or ctx["upcoming"] or [None])[0]
    nxt = next((t for t in ctx["upcoming"] if task is None or t.id != task.id), None)
    meeting = ctx["next_meeting"]
    return {
        "manager": ctx["manager"].name if ctx["manager"] else "Your manager",
        "task": task.title if task else "your current task",
        "next": nxt.title if nxt else "the next task in the sprint",
        "sprint": ctx["sprint"].name if ctx["sprint"] else "the current sprint",
        "goal": (ctx["sprint"].goal if ctx["sprint"] else "Let's keep the sprint goal in sight."),
        "project": ctx["project"].title,
        "company": ctx["company"].name,
        "when": _fmt_when(task.deadline if task else None, now),
        "meeting": (meeting.agenda or meeting.meeting_type.value.replace("_", " ")) if meeting else "standup",
        "count": str(len(ctx["completed"])),
        "total": str(ctx["total_tasks"]),
    }


def _teammate_facts(ctx: Dict[str, Any], mate: TeamMember) -> Dict[str, str]:
    now = ctx["now"]
    task = ctx["current_task"] or (ctx["in_progress"] or ctx["todo"] or ctx["upcoming"] or [None])[0]
    meeting = ctx["next_meeting"]
    stack = (ctx["project"].technology_stack or ["the stack"])[0]
    seed = f"{ctx['company'].id}:{mate.id}:{ctx['proactive_count']}"
    others = [t.name for t in ctx["teammates"] if t.id != mate.id]
    return {
        "mate": mate.name,
        "task": task.title if task else "your current task",
        "sprint": ctx["sprint"].name if ctx["sprint"] else "the current sprint",
        "project": ctx["project"].title,
        "stack": stack,
        "when": _fmt_when(task.deadline if task else None, now),
        "meeting": (meeting.agenda or meeting.meeting_type.value.replace("_", " ")) if meeting else "standup",
        "did": _pick_variant(_TEAM_DID_VARIANTS, seed + ":did"),
        "topic": _pick_variant(_TEAM_TOPICS, seed + ":topic"),
    }


def render_proactive_message(
    ctx: Dict[str, Any],
    sender_type: MessageSenderType,
    sender: Any,
    category: str,
    seed: Optional[str] = None,
) -> str:
    """Render a deterministic, context-filled proactive message."""
    seed = seed or f"{ctx['company'].id}:{category}:{ctx['proactive_count']}:{ctx['now'].strftime('%Y%m%d%H')}"
    if sender_type == MessageSenderType.MANAGER:
        variants = _MANAGER_TEMPLATES.get(category) or _MANAGER_TEMPLATES["checkin"]
        return _pick_variant(variants, seed).format(**_manager_facts(ctx))
    mate = sender if isinstance(sender, TeamMember) else None
    facts = _teammate_facts(ctx, mate) if mate is not None else {"task": "your current task"}
    variants = _TEAMMATE_TEMPLATES.get(category) or _TEAMMATE_TEMPLATES["team_discussion"]
    try:
        return _pick_variant(variants, seed).format(**facts)
    except (KeyError, IndexError):
        return _pick_variant(_TEAMMATE_TEMPLATES["encouragement"], seed).format(**facts)


# ---------------------------------------------------------------------------
# Choosing who speaks and about what
# ---------------------------------------------------------------------------

_TRIGGER_SLOTS: Dict[str, Tuple[str, str]] = {
    # trigger -> (channel, category)
    "task_started": ("team_member", "encouragement"),
    "task_submitted": ("manager", "feedback"),
    "task_completed": ("manager", "encouragement"),
    "task_rework": ("manager", "feedback"),
    "simulation_started": ("manager", "welcome"),
}

_MANAGER_ROTATION = ["checkin", "question", "encouragement", "meeting_request", "task_update"]
_TEAM_ROTATION = ["team_discussion", "question", "task_update", "encouragement"]


def _active_task(ctx: Dict[str, Any]) -> Optional[Task]:
    """The task the intern is (or should be) working on right now."""
    return ctx["current_task"] or (ctx["in_progress"] or [None])[0]


def _category_eligible(ctx: Dict[str, Any], category: str) -> bool:
    """Contextual age gates: is this kind of message realistic *right now*?"""
    now = ctx["now"]
    if category == "welcome":
        # Greetings go out once, early. Everything afterwards is a follow-up.
        return ctx["proactive_count"] == 0
    if category in ("checkin", "question"):
        # Follow-ups only after the intern has had time to work.
        task = _active_task(ctx)
        if task is None:
            return category == "question" and bool(ctx["upcoming"])
        if task.started_at is None:
            return True
        return _elapsed_since(task.started_at, now) >= FOLLOWUP_MIN_TASK_AGE_SECONDS
    if category == "feedback":
        # Ambient feedback needs something actually sitting in review — and it
        # should have sat there long enough to have been genuinely reviewed.
        if not ctx["review_queue"]:
            return False
        oldest = min(
            (_aware(t.updated_at) or now for t in ctx["review_queue"]),
            default=now,
        )
        return _elapsed_since(oldest, now) >= FEEDBACK_MIN_REVIEW_AGE_SECONDS
    if category == "reminder":
        # Reminders are deadline-driven: overdue, or due within hours.
        if ctx["overdue"]:
            return True
        deadlines = [
            _aware(t.deadline) for t in (ctx["in_progress"] or ctx["todo"])
            if t.deadline is not None
        ]
        return any(
            0 <= (d - now).total_seconds() <= REMINDER_DUE_WITHIN_SECONDS
            for d in deadlines if d is not None
        )
    if category == "task_update":
        # Instructions make sense while there is unstarted work to assign.
        return bool(ctx["upcoming"])
    if category == "meeting_request":
        meeting = ctx["next_meeting"]
        meeting_at = _aware(meeting.scheduled_at) if meeting is not None else None
        return (
            meeting is not None
            and meeting_at is not None
            and (meeting_at - now).total_seconds() <= 24 * 3600
        )
    # encouragement / team_discussion have no hard gates; pacing handles them.
    return True


def _ordered_candidates(ctx: Dict[str, Any], channel: str) -> List[str]:
    """Priority-ordered categories for a channel, most urgent first."""
    if channel == "manager":
        ordered = []
        if ctx["overdue"]:
            ordered.append("reminder")
        if ctx["review_queue"] or (ctx["state"] is not None and ctx["state"].missed_deadlines > 0):
            ordered.append("feedback")
        ordered.append("meeting_request")
        if ctx["proactive_count"] == 0:
            ordered.append("welcome")
        if not ctx["in_progress"] and ctx["upcoming"]:
            ordered.append("task_update")
        pool = _MANAGER_ROTATION
        start = (ctx["proactive_count"] + ctx["now"].hour) % len(pool)
        ordered.extend(pool[start:] + pool[:start])
        return ordered
    ordered = []
    if ctx["overdue"] or ctx["in_progress"]:
        pool = ["question", "task_update", "encouragement"]
        start = (ctx["proactive_count"] + ctx["now"].hour) % len(pool)
        ordered.extend(pool[start:] + pool[:start])
    if ctx["review_queue"]:
        ordered.append("team_discussion")
    pool = _TEAM_ROTATION
    start = (ctx["proactive_count"] + ctx["now"].hour) % len(pool)
    ordered.extend(pool[start:] + pool[:start])
    return ordered


def _resolve_sender(
    ctx: Dict[str, Any],
    channel: str,
    teammate_id: Optional[uuid.UUID] = None,
) -> Optional[Tuple[MessageSenderType, str, Any]]:
    if channel == "manager":
        if ctx["manager"] is None:
            return None
        return (MessageSenderType.MANAGER, str(ctx["manager"].id), ctx["manager"])
    teammates: List[TeamMember] = ctx["teammates"]
    if not teammates:
        return None
    mate = next((t for t in teammates if str(t.id) == str(teammate_id)), None) if teammate_id else None
    if mate is None:
        seed_int = int(hashlib.sha256(
            f"{ctx['company'].id}:{ctx['proactive_count']}:{ctx['now'].strftime('%Y%m%d%H')}".encode()
        ).hexdigest(), 16)
        mate = teammates[seed_int % len(teammates)]
    return (MessageSenderType.TEAM_MEMBER, str(mate.id), mate)


def choose_slot(
    ctx: Dict[str, Any],
    channel: str = "manager",
    teammate_id: Optional[uuid.UUID] = None,
    trigger: Optional[str] = None,
    category: Optional[str] = None,
) -> Optional[Tuple[MessageSenderType, str, str]]:
    """Decide (sender_type, sender_id, category) for the next proactive message.

    Trigger reactions resolve to their fixed slot (greetings downgrade to
    instructions once a greeting already went out). Ambient picks walk the
    priority-ordered candidates and take the first one whose contextual age
    gates pass — so instructions arrive early, follow-ups after real work
    time, feedback after review time, and reminders near deadlines.
    """
    if trigger in _TRIGGER_SLOTS:
        trigger_channel, trigger_category = _TRIGGER_SLOTS[trigger]
        resolved = _resolve_sender(ctx, trigger_channel, teammate_id)
        if resolved is None:
            return None
        if trigger_category == "welcome" and ctx["proactive_count"] > 0:
            # Greeting already delivered — point at the work instead.
            trigger_category = "task_update" if _category_eligible(ctx, "task_update") else None
            if trigger_category is None:
                return None
        sender_type, sender_id, _sender = resolved
        return (sender_type, sender_id, trigger_category)

    if category:
        # Explicit ambient category: still gated, so callers can't force an
        # unrealistic message (e.g. a follow-up seconds after task start).
        if not _category_eligible(ctx, category):
            return None
        resolved = _resolve_sender(ctx, channel, teammate_id)
        if resolved is None:
            return None
        sender_type, sender_id, _sender = resolved
        return (sender_type, sender_id, category)

    resolved = _resolve_sender(ctx, channel, teammate_id)
    if resolved is None:
        return None
    sender_type, sender_id, _sender = resolved
    for candidate in _ordered_candidates(ctx, channel):
        if _category_eligible(ctx, candidate):
            return (sender_type, sender_id, candidate)
    return None


# ---------------------------------------------------------------------------
# Persistence + delivery
# ---------------------------------------------------------------------------

def is_due(
    ctx: Dict[str, Any],
    force: bool = False,
    trigger: Optional[str] = None,
    category: Optional[str] = None,
) -> bool:
    """Decide whether a message may go out right now.

    - ``force`` bypasses everything (tests, admin resends).
    - Event reactions respect a short global gap so back-to-back actions
      never produce back-to-back messages.
    - Ambient messages respect the longer per-company gap plus the
      per-category minimum interval, which is what staggers arrivals
      naturally over time.
    """
    now = ctx["now"]
    if force:
        return True
    if trigger:
        return _elapsed_since(ctx.get("last_proactive_at"), now) >= TRIGGER_MIN_GAP_SECONDS
    if _elapsed_since(ctx.get("last_proactive_at"), now) < ctx.get("ambient_gap", AMBIENT_BASE_GAP_SECONDS):
        return False
    if category:
        interval = CATEGORY_MIN_INTERVAL.get(category, AMBIENT_BASE_GAP_SECONDS)
        last_same = (ctx.get("category_last_at") or {}).get(category)
        if _elapsed_since(last_same, now) < interval:
            return False
    return True


def persist_proactive_message(
    db: Session,
    company_id: uuid.UUID,
    sender_type: MessageSenderType,
    sender_id: str,
    content: str,
    category: str,
    actor: str = "system",
) -> Message:
    msg = Message(
        company_id=company_id,
        sender_type=sender_type,
        sender_id=sender_id,
        content=content,
    )
    db.add(msg)
    db.flush()
    # Namespaced action doubles as the per-category send history that paces
    # future messages of the same kind.
    log_activity(db, company_id, actor=actor, action=_proactive_action(category), detail=content[:200])
    db.commit()
    db.refresh(msg)
    return msg


def maybe_send_proactive_message(
    db: Session,
    company_id: uuid.UUID,
    channel: str = "manager",
    teammate_id: Optional[uuid.UUID] = None,
    force: bool = False,
    trigger: Optional[str] = None,
    category: Optional[str] = None,
    seed: Optional[str] = None,
) -> Optional[Message]:
    """Generate and persist one proactive message, or return None if not due.

    Template-based (no network, no API keys) so it is safe to call from sync
    request handlers and the scheduler alike.
    """
    ctx = build_workplace_context(db, company_id)
    if ctx is None:
        return None
    slot = choose_slot(ctx, channel=channel, teammate_id=teammate_id, trigger=trigger, category=category)
    if slot is None:
        return None
    sender_type, sender_id, chosen_category = slot
    if not is_due(ctx, force=force, trigger=trigger, category=chosen_category):
        return None
    sender: Any = ctx["manager"] if sender_type == MessageSenderType.MANAGER else next(
        (t for t in ctx["teammates"] if str(t.id) == sender_id), None
    )
    content = render_proactive_message(ctx, sender_type, sender, chosen_category, seed=seed)
    try:
        return persist_proactive_message(db, company_id, sender_type, sender_id, content, chosen_category)
    except Exception:
        logger.exception("Failed to persist proactive message for company %s", company_id)
        db.rollback()
        return None


async def maybe_send_proactive_message_async(
    db: Session,
    company_id: uuid.UUID,
    channel: str = "manager",
    teammate_id: Optional[uuid.UUID] = None,
    force: bool = False,
    trigger: Optional[str] = None,
    category: Optional[str] = None,
    seed: Optional[str] = None,
    ai_service: Any = None,
) -> Optional[Message]:
    """Async variant that lets Gemini rewrite the message in character.

    Falls back to the deterministic template whenever no API key is set or
    the rewrite fails, so behavior is identical offline.
    """
    ctx = build_workplace_context(db, company_id)
    if ctx is None:
        return None
    slot = choose_slot(ctx, channel=channel, teammate_id=teammate_id, trigger=trigger, category=category)
    if slot is None:
        return None
    sender_type, sender_id, chosen_category = slot
    if not is_due(ctx, force=force, trigger=trigger, category=chosen_category):
        return None
    sender = ctx["manager"] if sender_type == MessageSenderType.MANAGER else next(
        (t for t in ctx["teammates"] if str(t.id) == sender_id), None
    )
    content = render_proactive_message(ctx, sender_type, sender, chosen_category, seed=seed)

    if settings.GEMINI_API_KEY:
        try:
            from app.services.ai.ai_service import get_ai_service
            service = ai_service or get_ai_service()
            if sender_type == MessageSenderType.MANAGER and ctx["manager"] is not None:
                voice = (
                    f"You are {ctx['manager'].name}, {ctx['manager'].title}, "
                    f"messaging your intern in the company chat."
                )
            else:
                mate_name = sender.name if isinstance(sender, TeamMember) else "a teammate"
                mate_role = sender.role if isinstance(sender, TeamMember) else "teammate"
                voice = f"You are {mate_name}, a {mate_role} messaging your intern teammate."
            rewritten = await service.generate_text(
                prompt=(
                    f"Rewrite this workplace check-in in 1-3 natural sentences, keeping every "
                    f"fact (names, tasks, times) intact:\n\n\"{content}\""
                ),
                system_prompt=(
                    f"{voice} Keep it short, warm, and specific. Never break character "
                    f"or mention that you are an AI."
                ),
            )
            if rewritten and rewritten.strip():
                content = rewritten.strip()
        except Exception:
            logger.warning("Gemini proactive rewrite failed; using template", exc_info=True)

    try:
        return persist_proactive_message(db, company_id, sender_type, sender_id, content, chosen_category)
    except Exception:
        logger.exception("Failed to persist proactive message for company %s", company_id)
        db.rollback()
        return None


async def broadcast_message(company_id: uuid.UUID, msg: Message, channel: str) -> None:
    """Best-effort live push; persistence is what matters, this is garnish."""
    try:
        from app.websockets.manager import manager as ws_manager
        await ws_manager.broadcast(str(company_id), "chat_message", {
            "channel": channel,
            "id": str(msg.id),
            "sender_type": msg.sender_type.value,
            "sender_id": msg.sender_id,
            "content": msg.content,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        })
    except Exception:
        logger.warning("Proactive broadcast failed for company %s", company_id, exc_info=True)


def _sweep_defers(ctx: Dict[str, Any]) -> bool:
    """Probabilistic stagger so companies don't all chime on the same tick.

    Returns True when this sweep run should skip the company even though a
    message is technically due. First-ever messages and urgent deadline
    situations are never deferred; everything else sometimes waits for a
    later tick, which is what makes arrivals feel natural instead of
    scheduled.
    """
    if ctx["proactive_count"] == 0 or ctx["overdue"]:
        return False
    elapsed = _elapsed_since(ctx.get("last_proactive_at"), ctx["now"])
    if elapsed >= 2 * ctx.get("ambient_gap", AMBIENT_BASE_GAP_SECONDS):
        return False
    bucket = ctx["now"].strftime("%Y%m%d%H%M")
    roll = int(hashlib.sha256(f"sweep:{ctx['company'].id}:{bucket}".encode()).hexdigest(), 16) % 100
    return roll < 35


async def sweep_proactive_messages() -> int:
    """Scheduler entry: ambient messages keep arriving while interns work.

    Each company gets at most one message per run, only when its pacing
    windows allow it, with an extra stagger so simultaneous companies don't
    chime together. Returns the number of messages sent.
    """
    from app.core.database import SessionLocal
    db: Session = SessionLocal()
    sent = 0
    try:
        company_ids = db.execute(
            select(Simulation.company_id).where(
                Simulation.status == SimulationStatus.IN_PROGRESS
            ).distinct()
        ).scalars().all()
        rng = random.Random()
        for company_id in company_ids:
            try:
                ctx = build_workplace_context(db, company_id)
                if ctx is None or not is_due(ctx):
                    continue
                if _sweep_defers(ctx):
                    continue
                # Alternate voices so the workplace feels populated, not naggy.
                if ctx["last_sender_type"] == MessageSenderType.MANAGER:
                    channel: str = "team_member"
                elif ctx["last_sender_type"] == MessageSenderType.TEAM_MEMBER:
                    channel = "manager"
                else:
                    channel = rng.choice(["manager", "team_member"])
                msg = await maybe_send_proactive_message_async(db, company_id, channel=channel)
                if msg is not None:
                    sent += 1
                    await broadcast_message(company_id, msg, channel="manager" if msg.sender_type == MessageSenderType.MANAGER else "team")
            except Exception:
                logger.exception("Proactive sweep failed for company %s", company_id)
                db.rollback()
    finally:
        db.close()
    if sent:
        logger.info("Proactive sweep sent %d workplace message(s)", sent)
    return sent
