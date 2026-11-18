"""Tests for all workplace scenarios: triggers, transitions, evidence persistence, and duplicate prevention."""
import uuid
from types import SimpleNamespace
from datetime import datetime, timezone
import pytest

from app.models.enums import MessageSenderType, TaskStatus, WorkflowEventStatus, WorkflowEventType
from app.models.simulation import Simulation, WorkflowEvent
from app.models.task import Task
from app.models.manager import Manager
from app.models.soft_skill import SoftSkillEvidence
from app.models.message import Message
from app.services.workplace_scenarios import (
    COLLABORATION,
    FEEDBACK_RESPONSE,
    INSUFFICIENT,
    OWNERSHIP,
    PRIORITIZATION,
    REQUIREMENT_CHANGE,
    SCORED,
    TIME_MANAGEMENT,
    WorkplaceScenarioError,
    acknowledge_collaboration,
    acknowledge_feedback_response,
    acknowledge_ownership,
    acknowledge_prioritization,
    acknowledge_scenario,
    acknowledge_time_management,
    assess_collaboration,
    assess_feedback_response,
    assess_ownership,
    assess_prioritization,
    assess_scenario,
    assess_time_management,
    find_scenario_event,
    issue_collaboration_scenario,
    issue_feedback_response_scenario,
    issue_ownership_scenario,
    issue_prioritization_scenario,
    issue_scenario,
    issue_time_management_scenario,
    record_collaboration_reply,
    record_feedback_response_reply,
    record_ownership_reply,
    record_prioritization_reply,
    record_scenario_reply,
    record_time_management_reply,
    scenario_catalog,
    serialize_workflow_event,
    start_scenario,
    upsert_soft_skill_observation,
    workplace_phase,
)


class _NestedCtx:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeDB:
    def __init__(self):
        self.simulations = {}
        self.tasks = {}
        self.managers = {}
        self.events = {}
        self.messages = {}
        self.evidence = {}
        self.added = []
        self.commits = 0

    def begin_nested(self):
        return _NestedCtx()

    def get(self, model, key):
        k = uuid.UUID(str(key)) if isinstance(key, str) else key
        if model is Message:
            return self.messages.get(k)
        if model is Simulation:
            return self.simulations.get(k)
        if model is Task:
            return self.tasks.get(k)
        if model is WorkflowEvent:
            return self.events.get(k)
        if model is Manager:
            return self.managers.get(k)
        if model is SoftSkillEvidence:
            return self.evidence.get(k)
        return None

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)
        if isinstance(obj, Message):
            self.messages[obj.id] = obj
        elif isinstance(obj, Simulation):
            self.simulations[obj.id] = obj
        elif isinstance(obj, Task):
            self.tasks[obj.id] = obj
        elif isinstance(obj, WorkflowEvent):
            self.events[obj.id] = obj
        elif isinstance(obj, Manager):
            self.managers[obj.id] = obj
        elif isinstance(obj, SoftSkillEvidence):
            self.evidence[obj.id] = obj

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def execute(self, stmt):
        q = str(stmt).lower()
        if "from simulations" in q:
            sims = list(self.simulations.values())
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(first=lambda: sims[0] if sims else None, all=lambda: sims),
                scalar_one_or_none=lambda: sims[0] if sims else None,
            )
        if "from managers" in q:
            mgrs = list(self.managers.values())
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(first=lambda: mgrs[0] if mgrs else None, all=lambda: mgrs),
                scalar_one=lambda: mgrs[0] if mgrs else None,
                scalar_one_or_none=lambda: mgrs[0] if mgrs else None,
            )
        if "from soft_skill_evidence" in q:
            evs = list(self.evidence.values())
            try:
                params = stmt.compile().params
                for k, val in params.items():
                    if "competency" in k:
                        evs = [e for e in evs if e.competency == val]
                    elif "event_id" in k:
                        evs = [e for e in evs if str(e.event_id) == str(val)]
            except Exception:
                pass
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: evs, first=lambda: evs[0] if evs else None),
                scalar_one_or_none=lambda: evs[0] if evs else None,
            )
        if "from workflow_events" in q:
            evts = list(self.events.values())
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: evts, first=lambda: evts[0] if evts else None),
                scalar_one_or_none=lambda: evts[0] if evts else None,
            )
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [], first=lambda: None),
            scalar_one_or_none=lambda: None,
        )


@pytest.fixture
def test_setup():
    db = FakeDB()
    sim_id = uuid.uuid4()
    task_id = uuid.uuid4()
    company_id = uuid.uuid4()

    sim = Simulation(
        id=sim_id,
        student_id="student-100",
        company_id=company_id,
        title="Software Internship Simulation",
        description="Full adaptive simulation",
        role="Backend Engineer",
    )
    task = Task(
        id=task_id,
        sequence=1,
        title="Develop Authentication Service",
        description="Implement user authentication and JWT validation",
        status=TaskStatus.IN_PROGRESS,
        acceptance_criteria=["Valid credentials return JWT", "Invalid credentials return 401"],
    )
    manager = Manager(
        id=uuid.uuid4(),
        company_id=company_id,
        name="Alex Rivera",
        title="Engineering Manager",
    )

    db.add(sim)
    db.add(task)
    db.add(manager)
    return db, sim, task, "student-100"


# =========================================================================
# 1. CATALOG AND START SCENARIO TESTS
# =========================================================================

def test_all_catalog_scenarios_implemented():
    catalog = scenario_catalog()
    assert len(catalog) == 6
    keys = {item["key"] for item in catalog}
    assert keys == {
        REQUIREMENT_CHANGE,
        COLLABORATION,
        TIME_MANAGEMENT,
        PRIORITIZATION,
        OWNERSHIP,
        FEEDBACK_RESPONSE,
    }
    for item in catalog:
        assert item["implemented"] is True, f"Scenario {item['key']} should be implemented"


def test_start_scenario_for_all_valid_keys():
    for key in [REQUIREMENT_CHANGE, COLLABORATION, TIME_MANAGEMENT, PRIORITIZATION, OWNERSHIP, FEEDBACK_RESPONSE]:
        spec = start_scenario(key)
        assert spec.key == key
        assert spec.implemented is True


def test_start_scenario_unknown_key_raises_404():
    with pytest.raises(WorkplaceScenarioError) as exc_info:
        start_scenario("non_existent_scenario")
    assert exc_info.value.status_code == 404


# =========================================================================
# 2. TRIGGER AND DUPLICATE PREVENTION TESTS
# =========================================================================

@pytest.mark.parametrize("scenario_key,issue_fn,expected_type", [
    (COLLABORATION, issue_collaboration_scenario, WorkflowEventType.COLLABORATION_REQUEST),
    (TIME_MANAGEMENT, issue_time_management_scenario, WorkflowEventType.SCHEDULE_CONFLICT),
    (PRIORITIZATION, issue_prioritization_scenario, WorkflowEventType.COMPETING_WORK),
    (OWNERSHIP, issue_ownership_scenario, WorkflowEventType.UNOWNED_ISSUE),
    (FEEDBACK_RESPONSE, issue_feedback_response_scenario, WorkflowEventType.REVIEW_FEEDBACK),
])
def test_scenario_trigger_creates_pending_event(test_setup, scenario_key, issue_fn, expected_type):
    db, sim, task, _ = test_setup
    event = issue_fn(db, sim, task)
    assert event is not None
    assert event.event_type == expected_type
    assert event.status == WorkflowEventStatus.PENDING
    assert event.action_required is True
    assert event.simulation_id == sim.id
    assert event.task_id == task.id
    assert event.metadata_json.get("scenario_key") == scenario_key


def test_duplicate_trigger_returns_none(test_setup):
    db, sim, task, _ = test_setup
    first_event = issue_collaboration_scenario(db, sim, task)
    assert first_event is not None

    # Triggering again while still open returns None (prevents duplicates)
    duplicate_event = issue_collaboration_scenario(db, sim, task)
    assert duplicate_event is None


def test_trigger_rejects_timer_activity(test_setup):
    db, sim, task, _ = test_setup
    with pytest.raises(WorkplaceScenarioError) as exc_info:
        issue_scenario(db, sim, task, COLLABORATION, trigger_activity="timer_tick")
    assert exc_info.value.status_code == 400
    assert "timer" in exc_info.value.message.lower()


def test_trigger_rejects_status_only_activity(test_setup):
    db, sim, task, _ = test_setup
    with pytest.raises(WorkplaceScenarioError) as exc_info:
        issue_scenario(db, sim, task, TIME_MANAGEMENT, trigger_activity="task_started")
    assert exc_info.value.status_code == 400


def test_trigger_requires_active_task(test_setup):
    db, sim, task, _ = test_setup
    task.status = TaskStatus.TODO
    with pytest.raises(WorkplaceScenarioError) as exc_info:
        issue_scenario(db, sim, task, PRIORITIZATION)
    assert exc_info.value.status_code == 409


# =========================================================================
# 3. ACKNOWLEDGMENT AND MESSAGE GENERATION TESTS
# =========================================================================

def test_collaboration_acknowledgment_creates_teammate_message(test_setup):
    db, sim, task, student_id = test_setup
    event = issue_collaboration_scenario(db, sim, task)
    payload = acknowledge_collaboration(db, sim, task, event, student_id)

    assert payload["status"] == WorkflowEventStatus.ACKNOWLEDGED.value
    assert payload["acknowledged"] is True
    assert payload["manager_message"] is not None

    # Check that a teammate message was saved in Message table
    teammate_messages = [
        m for m in db.messages.values()
        if m.sender_type == MessageSenderType.TEAM_MEMBER
    ]
    assert len(teammate_messages) == 1
    assert "coordinate" in teammate_messages[0].content.lower()


def test_time_management_acknowledgment_creates_manager_message(test_setup):
    db, sim, task, student_id = test_setup
    event = issue_time_management_scenario(db, sim, task)
    payload = acknowledge_time_management(db, sim, task, event, student_id)

    assert payload["status"] == WorkflowEventStatus.ACKNOWLEDGED.value
    manager_messages = [
        m for m in db.messages.values()
        if m.sender_type == MessageSenderType.MANAGER
    ]
    assert len(manager_messages) >= 1
    assert any("milestone" in m.content.lower() or "conflict" in m.content.lower() for m in manager_messages)


def test_acknowledgment_is_idempotent(test_setup):
    db, sim, task, student_id = test_setup
    event = issue_prioritization_scenario(db, sim, task)
    payload1 = acknowledge_prioritization(db, sim, task, event, student_id)
    msg_count_1 = len(db.messages)

    # Acknowledge again: does not create duplicate messages
    payload2 = acknowledge_prioritization(db, sim, task, event, student_id)
    msg_count_2 = len(db.messages)
    assert msg_count_1 == msg_count_2
    assert payload1["id"] == payload2["id"]


def test_acknowledgment_wrong_task_forbidden(test_setup):
    db, sim, task, student_id = test_setup
    other_task = Task(id=uuid.uuid4(), sequence=2, title="Other", status=TaskStatus.IN_PROGRESS)
    event = issue_ownership_scenario(db, sim, task)

    with pytest.raises(WorkplaceScenarioError) as exc_info:
        acknowledge_scenario(db, sim, other_task, event, student_id)
    assert exc_info.value.status_code == 403


# =========================================================================
# 4. INVALID STATE TRANSITION TESTS
# =========================================================================

def test_reply_before_acknowledge_is_rejected(test_setup):
    db, sim, task, student_id = test_setup
    event = issue_collaboration_scenario(db, sim, task)
    assert event.status == WorkflowEventStatus.PENDING

    with pytest.raises(WorkplaceScenarioError) as exc_info:
        record_collaboration_reply(db, sim, task, event, student_id, "I will coordinate with the team.")
    assert exc_info.value.status_code == 409
    assert "acknowledge" in exc_info.value.message.lower()


def test_empty_reply_is_rejected(test_setup):
    db, sim, task, student_id = test_setup
    event = issue_ownership_scenario(db, sim, task)
    acknowledge_ownership(db, sim, task, event, student_id)

    with pytest.raises(WorkplaceScenarioError) as exc_info:
        record_ownership_reply(db, sim, task, event, student_id, "   ")
    assert exc_info.value.status_code == 400


def test_duplicate_reply_is_rejected(test_setup):
    db, sim, task, student_id = test_setup
    event = issue_feedback_response_scenario(db, sim, task)
    acknowledge_feedback_response(db, sim, task, event, student_id)

    reply1 = "I appreciate the review feedback. I will add error handling and unit tests for edge cases."
    record_feedback_response_reply(db, sim, task, event, student_id, reply1)

    # Second reply should be rejected
    with pytest.raises(WorkplaceScenarioError) as exc_info:
        record_feedback_response_reply(db, sim, task, event, student_id, "Here is another reply.")
    assert exc_info.value.status_code == 409


# =========================================================================
# 5. RESPONSE EVALUATION & EVIDENCE PERSISTENCE TESTS
# =========================================================================

def test_collaboration_response_scores_communication(test_setup):
    db, sim, task, student_id = test_setup
    event = issue_collaboration_scenario(db, sim, task)
    acknowledge_collaboration(db, sim, task, event, student_id)

    reply_text = (
        "I will coordinate our API endpoints today and share the JSON schema so we can integrate our branches. "
        "Let's sync up this afternoon to align our timelines."
    )
    result = record_collaboration_reply(db, sim, task, event, student_id, reply_text)

    assert result["status"] == WorkflowEventStatus.RESOLVED.value
    assessment = result["assessment"]
    assert assessment["status"] == SCORED
    assert assessment["communication"]["status"] == SCORED
    assert assessment["communication"]["score"] >= 80.0

    # Verify SoftSkillEvidence row persisted
    evidence_rows = [e for e in db.evidence.values() if e.event_id == event.id]
    assert len(evidence_rows) == 1
    evidence = evidence_rows[0]
    assert evidence.student_id == student_id
    assert evidence.competency == "communication"
    assert evidence.scenario_key == COLLABORATION
    assert evidence.status == SCORED
    assert evidence.score >= 80.0
    assert "coordinate" in evidence.evidence["reply"].lower()


def test_collaboration_superficial_reply_insufficient(test_setup):
    db, sim, task, student_id = test_setup
    event = issue_collaboration_scenario(db, sim, task)
    acknowledge_collaboration(db, sim, task, event, student_id)

    result = record_collaboration_reply(db, sim, task, event, student_id, "ok thanks")
    assessment = result["assessment"]
    assert assessment["status"] == INSUFFICIENT
    assert assessment["communication"]["status"] == INSUFFICIENT
    assert assessment["communication"]["score"] is None


def test_time_management_response_scores_time_management(test_setup):
    db, sim, task, student_id = test_setup
    event = issue_time_management_scenario(db, sim, task)
    acknowledge_time_management(db, sim, task, event, student_id)

    reply_text = (
        "I will adjust my schedule by breaking down the remaining work into core deliverables today. "
        "We can defer the non-critical cleanup to next sprint to meet the earlier milestone deadline safely."
    )
    result = record_time_management_reply(db, sim, task, event, student_id, reply_text)

    assert result["status"] == WorkflowEventStatus.RESOLVED.value
    assessment = result["assessment"]
    assert assessment["status"] == SCORED
    assert assessment["time_management"]["status"] == SCORED
    assert assessment["time_management"]["score"] >= 80.0

    # Verify SoftSkillEvidence
    evidence = next(e for e in db.evidence.values() if e.event_id == event.id)
    assert evidence.competency == "time_management"
    assert evidence.scenario_key == TIME_MANAGEMENT
    assert evidence.score >= 80.0


def test_prioritization_response_scores_decision_making(test_setup):
    db, sim, task, student_id = test_setup
    event = issue_prioritization_scenario(db, sim, task)
    acknowledge_prioritization(db, sim, task, event, student_id)

    reply_text = (
        "I decide to prioritize the critical production blocker first because it impacts existing users. "
        "Then I will resume this authentication task once the fix is deployed to staging."
    )
    result = record_prioritization_reply(db, sim, task, event, student_id, reply_text)

    assert result["status"] == WorkflowEventStatus.RESOLVED.value
    assessment = result["assessment"]
    assert assessment["status"] == SCORED
    assert assessment["decision_making"]["status"] == SCORED
    assert assessment["decision_making"]["score"] >= 80.0

    evidence = next(e for e in db.evidence.values() if e.event_id == event.id)
    assert evidence.competency == "decision_making"
    assert evidence.scenario_key == PRIORITIZATION


def test_ownership_response_scores_professionalism(test_setup):
    db, sim, task, student_id = test_setup
    event = issue_ownership_scenario(db, sim, task)
    acknowledge_ownership(db, sim, task, event, student_id)

    reply_text = (
        "I will take full ownership of this unassigned issue. I plan to reproduce the defect today, "
        "diagnose the root cause, and submit a pull request with tests while keeping the team updated."
    )
    result = record_ownership_reply(db, sim, task, event, student_id, reply_text)

    assert result["status"] == WorkflowEventStatus.RESOLVED.value
    assessment = result["assessment"]
    assert assessment["status"] == SCORED
    assert assessment["professionalism"]["status"] == SCORED
    assert assessment["professionalism"]["score"] >= 80.0

    evidence = next(e for e in db.evidence.values() if e.event_id == event.id)
    assert evidence.competency == "professionalism"
    assert evidence.scenario_key == OWNERSHIP


def test_feedback_response_scores_both_competencies(test_setup):
    db, sim, task, student_id = test_setup
    event = issue_feedback_response_scenario(db, sim, task)
    acknowledge_feedback_response(db, sim, task, event, student_id)

    reply_text = (
        "Thank you for the helpful code review feedback. I understand the concerns regarding error handling. "
        "I will update the controller to catch validation exceptions and add unit tests covering null payloads."
    )
    result = record_feedback_response_reply(db, sim, task, event, student_id, reply_text)

    assert result["status"] == WorkflowEventStatus.RESOLVED.value
    assessment = result["assessment"]
    assert assessment["status"] == SCORED
    assert assessment["professionalism"]["status"] == SCORED
    assert assessment["communication"]["status"] == SCORED
    assert assessment["professionalism"]["score"] >= 80.0
    assert assessment["communication"]["score"] >= 80.0

    # Two distinct evidence rows persisted for the two competencies
    event_evidence = [e for e in db.evidence.values() if e.event_id == event.id]
    assert len(event_evidence) == 2
    competencies = {e.competency for e in event_evidence}
    assert competencies == {"professionalism", "communication"}


# =========================================================================
# 6. EVIDENCE UPSERT & DUPLICATE PREVENTION
# =========================================================================

def test_evidence_upsert_prevents_duplicate_records(test_setup):
    db, sim, task, student_id = test_setup
    event = issue_collaboration_scenario(db, sim, task)

    obs1 = {"status": SCORED, "score": 75.0, "summary": "Initial score", "reason": "reason_1"}
    row1 = upsert_soft_skill_observation(
        db, sim, task, event, student_id, "communication", obs1, "reply 1", [],
        scenario_key=COLLABORATION,
    )

    # Calling upsert again with updated observation updates the existing row in place
    obs2 = {"status": SCORED, "score": 88.0, "summary": "Updated score", "reason": "reason_2"}
    row2 = upsert_soft_skill_observation(
        db, sim, task, event, student_id, "communication", obs2, "reply 2", [],
        scenario_key=COLLABORATION,
    )

    assert row1.id == row2.id
    assert row2.score == 88.0
    assert row2.summary == "Updated score"

    # Only one record in database for this event + competency
    matching_rows = [e for e in db.evidence.values() if e.event_id == event.id and e.competency == "communication"]
    assert len(matching_rows) == 1


# =========================================================================
# 7. SERIALIZATION AND PHASE INTEGRATION
# =========================================================================

def test_serialization_and_phase_progression(test_setup):
    db, sim, task, student_id = test_setup
    event = issue_prioritization_scenario(db, sim, task)

    # Phase 1: PENDING -> phase is "acknowledge"
    s1 = serialize_workflow_event(db, event, task)
    assert s1["phase"] == "acknowledge"
    assert s1["status"] == WorkflowEventStatus.PENDING.value

    # Phase 2: ACKNOWLEDGED -> phase is "reply"
    acknowledge_prioritization(db, sim, task, event, student_id)
    s2 = serialize_workflow_event(db, event, task)
    assert s2["phase"] == "reply"
    assert s2["status"] == WorkflowEventStatus.ACKNOWLEDGED.value

    # Phase 3: REPLIED & SCORED -> phase is "assessed"
    record_prioritization_reply(
        db, sim, task, event, student_id,
        "I choose the urgent bug first because of critical production impact, then finish the auth task."
    )
    s3 = serialize_workflow_event(db, event, task)
    assert s3["phase"] == "assessed"
    assert s3["status"] == WorkflowEventStatus.RESOLVED.value
    assert s3["assessment"]["decision_making"]["status"] == SCORED
