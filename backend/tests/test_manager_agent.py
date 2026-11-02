"""Manager Agent tests run with persisted-record-shaped context and the mock provider."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.agents import AgentContext, AgentOrchestrator, AgentRegistry, AgentRequest, EvidenceReference
from app.agents.context_manager import ContextAccessDenied
from app.agents.specialists.manager_agent import (
    ManagerAgent,
    ManagerAgentInput,
    ManagerCommunicationCategory,
    create_manager_orchestrator,
    invoke_manager_agent,
    register_manager_agent,
)
from app.services.ai.ai_service import MockAIService
from app.services.manager_communication_service import create_manager_response


async def _immediate_wait_for(awaitable, timeout):
    return await awaitable


def _run_immediate(coroutine):
    try:
        return coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    finally:
        coroutine.close()


@pytest.fixture(autouse=True)
def no_platform_event_loop(monkeypatch):
    monkeypatch.setattr(asyncio, "wait_for", _immediate_wait_for)


class FakeContextManager:
    def __init__(self, context=None, error=None):
        self.context = context
        self.error = error
        self.calls = []

    def build(self, db, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.context


class FailingProvider:
    async def generate_text(self, *_args, **_kwargs):
        raise RuntimeError("private provider detail")

    async def generate_json(self, *_args, **_kwargs):
        raise RuntimeError("not used")


class CountingProvider:
    def __init__(self):
        self.calls = 0

    async def generate_text(self, *_args, **_kwargs):
        self.calls += 1
        return "Thanks for the update. Let's review the current criterion together."

    async def generate_json(self, *_args, **_kwargs):
        raise AssertionError("manager reply should use the existing text API")


def manager_context():
    simulation_id, task_id, criteria_id, message_id, event_id, interaction_id = [uuid4() for _ in range(6)]
    now = datetime.now(timezone.utc)
    context = AgentContext(
        simulation_id=simulation_id, student_id="student-7", company_id=uuid4(),
        task_id=task_id, current_task_id=task_id, profile="manager",
        current_task={"id": task_id, "title": "Build retry policy",
                      "description": "Implement bounded retry handling", "status": "in_progress",
                      "task_status": "in_progress", "started_at": now, "difficulty": "intermediate"},
        current_sprint={"id": uuid4(), "name": "Sprint 2", "number": 2, "status": "active"},
        current_company={"id": uuid4(), "name": "Acme", "industry": "Software"},
        current_project={"id": uuid4(), "title": "Payments API", "role": "Backend Engineer"},
        manager_profile={"id": uuid4(), "name": "Morgan Lee", "title": "Engineering Manager",
                         "personality": "friendly"},
        project_state={"completed_tasks": 2, "pending_tasks": 3, "missed_deadlines": 0,
                       "bug_count": 1, "manager_satisfaction": 76, "team_satisfaction": 80,
                       "stress_level": 25, "current_sprint_number": 2},
        active_criteria={"id": criteria_id, "version_number": 4, "status": "active",
                         "items": [{"text": "Retry transient failures", "state": "active"}]},
        criteria_version_id=criteria_id,
        manager_communications=[{"id": message_id, "sender_type": "student", "sender_id": "student-7",
                                 "content": "Can you clarify the retry criterion?", "created_at": now}],
        workplace_events=[{"id": event_id, "task_id": task_id, "event_type": "requirement_change",
                           "status": "acknowledged", "title": "Requirement change",
                           "message": "Retry criteria changed", "updated_requirement": "Retry transient failures",
                           "criteria_version_id": str(criteria_id), "criteria_version_number": 4}],
        technical_reviews=[{"id": uuid4(), "result": "changes_required", "summary": "Retry limit is missing"}],
        competencies={"weaknesses": ["adaptability"]},
        interaction_history=[{"interaction_id": interaction_id, "interaction_type": "progress_update",
                              "occurred_at": now, "decision": "shared_progress"}],
        evidence=[
            EvidenceReference(evidence_type="manager_communication", evidence_record_id=message_id,
                              student_id="student-7", simulation_id=simulation_id, task_id=task_id,
                              occurred_at=now, source="messages"),
            EvidenceReference(evidence_type="workflow_event", evidence_record_id=event_id,
                              student_id="student-7", simulation_id=simulation_id, task_id=task_id,
                              workplace_event_id=event_id, criteria_version_id=criteria_id,
                              occurred_at=now, source="workflow_events"),
            EvidenceReference(evidence_type="simulation_interaction", evidence_record_id=interaction_id,
                              student_id="student-7", simulation_id=simulation_id, task_id=task_id,
                              occurred_at=now, source="simulation_interactions"),
        ],
    )
    return context, message_id, event_id, interaction_id, criteria_id


def input_for(context, *, message_id=None, event_id=None, interaction_id=None, category=None):
    return ManagerAgentInput(simulation_id=context.simulation_id, student_id=context.student_id,
                             task_id=context.current_task_id, student_message_id=message_id,
                             workplace_event_id=event_id, trigger_evidence_id=interaction_id,
                             category=category)


def test_registration_and_orchestrator_invocation():
    context, message_id, *_ = manager_context()
    registry = AgentRegistry()
    register_manager_agent(registry, object(), context_manager=FakeContextManager(context),
                           ai_service=MockAIService())
    assert registry.registered_ids() == ["manager"]
    response = _run_immediate(AgentOrchestrator(registry).execute(AgentRequest(
        agent_id="manager", payload=input_for(context, message_id=message_id).model_dump(mode="json")
    )))
    assert response.status.value == "succeeded"
    assert response.result["decision"] == "respond"
    assert response.result["manager_message"]


def test_manager_helper_reuses_application_owned_orchestrator_and_request_runtime():
    context, message_id, *_ = manager_context()
    contexts = FakeContextManager(context)
    provider = CountingProvider()
    registry = AgentRegistry()
    register_manager_agent(registry, context_manager=contexts)
    orchestrator = AgentOrchestrator(registry)
    output = _run_immediate(invoke_manager_agent(
        object(), provider, orchestrator=orchestrator,
        simulation_id=context.simulation_id, student_id=context.student_id,
        task_id=context.current_task_id, student_message_id=message_id,
    ))
    assert orchestrator.registry is registry
    assert output and output.decision == "respond"
    assert contexts.calls[0]["simulation_id"] == context.simulation_id
    assert provider.calls == 1


def test_factory_creates_an_isolated_manager_registry():
    context, *_ = manager_context()
    first = create_manager_orchestrator(object(), context_manager=FakeContextManager(context),
                                        ai_service=MockAIService())
    second = create_manager_orchestrator(object(), context_manager=FakeContextManager(context),
                                         ai_service=MockAIService())
    assert first.registry is not second.registry
    assert first.registry.registered_ids() == ["manager"]


@pytest.mark.parametrize(("text", "expected"), [
    ("Can you clarify the retry criterion?", ManagerCommunicationCategory.ANSWER_QUESTION),
    ("Please clarify the acceptance criteria", ManagerCommunicationCategory.TASK_CLARIFICATION),
    ("I reviewed the feedback and have a question", ManagerCommunicationCategory.WORKPLACE_FEEDBACK),
])
def test_manager_messages_use_context_and_classify_common_intents(text, expected):
    context, message_id, *_ = manager_context()
    context.manager_communications[0]["content"] = text
    output = _run_immediate(ManagerAgent(object(), context_manager=FakeContextManager(context),
                                         ai_service=MockAIService()).execute(
        input_for(context, message_id=message_id)
    ))
    assert output.communication_category is expected
    assert "Morgan Lee" not in output.manager_message  # in-character service need not name itself
    assert output.response_metadata.generation_source == "ai_provider"
    assert output.supporting_evidence[0].evidence_record_id == message_id


def test_requirement_change_communication_references_event_and_criteria_version():
    context, _message_id, event_id, _interaction_id, criteria_id = manager_context()
    output = _run_immediate(ManagerAgent(object(), context_manager=FakeContextManager(context),
                                         ai_service=MockAIService()).execute(
        input_for(context, event_id=event_id)
    ))
    assert output.communication_category is ManagerCommunicationCategory.REQUIREMENT_CHANGE
    assert output.workplace_event_id == event_id
    assert output.criteria_version_id == criteria_id
    assert any(item.workplace_event_id == event_id for item in output.supporting_evidence)


def test_progress_checkin_and_followup_use_persisted_history():
    context, message_id, _event_id, interaction_id, _criteria_id = manager_context()
    checkin = _run_immediate(ManagerAgent(object(), context_manager=FakeContextManager(context),
                                          ai_service=MockAIService()).execute(
        input_for(context, interaction_id=interaction_id)
    ))
    assert checkin.communication_category is ManagerCommunicationCategory.PROGRESS_CHECKIN
    assert checkin.response_metadata.history_messages_used == 1

    now = context.manager_communications[0]["created_at"]
    context.manager_communications.extend([
        {"id": uuid4(), "sender_type": "manager", "sender_id": "manager-id",
         "content": "What is your next step?", "created_at": now - timedelta(seconds=1)},
        {"id": message_id, "sender_type": "student", "sender_id": "student-7",
         "content": "Can you clarify the retry criterion?", "created_at": now},
    ])
    followup = _run_immediate(ManagerAgent(object(), context_manager=FakeContextManager(context),
                                           ai_service=MockAIService()).execute(
        input_for(context, message_id=message_id)
    ))
    assert followup.communication_category is ManagerCommunicationCategory.FOLLOW_UP


def test_evidence_and_duplicate_request_handling():
    context, message_id, event_id, _interaction_id, _criteria_id = manager_context()
    context.manager_communications.append({"id": uuid4(), "sender_type": "manager",
                                           "sender_id": "manager-id", "content": "Answered.",
                                           "created_at": context.manager_communications[0]["created_at"] + timedelta(seconds=1)})
    duplicate_message = _run_immediate(ManagerAgent(object(), context_manager=FakeContextManager(context),
                                                    ai_service=MockAIService()).execute(
        input_for(context, message_id=message_id)
    ))
    assert duplicate_message.reason_code == "duplicate_request"

    event = context.workplace_events[0]
    event["manager_response_message_id"] = str(uuid4())
    duplicate_event = _run_immediate(ManagerAgent(object(), context_manager=FakeContextManager(context),
                                                  ai_service=MockAIService()).execute(
        input_for(context, event_id=event_id)
    ))
    assert duplicate_event.reason_code == "duplicate_request"


def test_repeated_invocation_on_same_agent_returns_cached_response():
    context, message_id, *_ = manager_context()
    provider = CountingProvider()
    agent = ManagerAgent(object(), context_manager=FakeContextManager(context), ai_service=provider)
    payload = input_for(context, message_id=message_id)
    first = _run_immediate(agent.execute(payload))
    second = _run_immediate(agent.execute(payload))
    assert first.manager_message == second.manager_message
    assert first.response_metadata.idempotency_key == second.response_metadata.idempotency_key
    assert provider.calls == 1


def test_closed_event_stale_task_and_missing_trigger_are_skipped():
    context, _message_id, event_id, _interaction_id, _criteria_id = manager_context()
    event = context.workplace_events[0]
    event["status"] = "resolved"
    closed = _run_immediate(ManagerAgent(object(), context_manager=FakeContextManager(context),
                                         ai_service=MockAIService()).execute(input_for(context, event_id=event_id)))
    assert closed.reason_code == "event_closed"
    context.workplace_events[0]["status"] = "acknowledged"
    stale = ManagerAgentInput(simulation_id=context.simulation_id, student_id=context.student_id,
                              task_id=uuid4(), student_message_id=uuid4())
    stale_output = _run_immediate(ManagerAgent(object(), context_manager=FakeContextManager(context),
                                                ai_service=MockAIService()).execute(stale))
    assert stale_output.reason_code == "stale_task"
    no_trigger = ManagerAgentInput(simulation_id=context.simulation_id, student_id=context.student_id)
    skipped = _run_immediate(ManagerAgent(object(), context_manager=FakeContextManager(context),
                                          ai_service=MockAIService()).execute(no_trigger))
    assert skipped.reason_code == "trigger_required"


def test_provider_failure_uses_safe_fallback_without_claiming_completion():
    context, message_id, *_ = manager_context()
    output = _run_immediate(ManagerAgent(object(), context_manager=FakeContextManager(context),
                                         ai_service=FailingProvider()).execute(
        input_for(context, message_id=message_id)
    ))
    assert output.response_metadata.generation_source == "deterministic_fallback"
    assert output.manager_message
    assert "completed" not in output.manager_message.lower()
    assert "private" not in output.manager_message


def test_owner_isolation_and_event_lifecycle_are_not_bypassed():
    context, message_id, *_ = manager_context()
    manager = FakeContextManager(error=ContextAccessDenied("denied"))
    with pytest.raises(ContextAccessDenied):
        _run_immediate(ManagerAgent(object(), context_manager=manager,
                                    ai_service=MockAIService()).execute(
            input_for(context, message_id=message_id)
        ))
    # The agent only proposes text and has no db.add/commit capability in this test.
    class ReadOnlyDb:
        pass
    generated = _run_immediate(ManagerAgent(ReadOnlyDb(), context_manager=FakeContextManager(context),
                                             ai_service=MockAIService()).execute(
        input_for(context, event_id=context.workplace_events[0]["id"])
    ))
    assert generated.decision == "respond"
    assert generated.workplace_event_id == context.workplace_events[0]["id"]


def test_existing_manager_communication_service_still_persists_mock_reply():
    class RecordingSession:
        def __init__(self):
            self.added = []

        def add(self, item):
            self.added.append(item)

        def flush(self):
            return None

    context, *_ = manager_context()
    db = RecordingSession()
    manager = type("ManagerRow", (), {
        "id": context.manager_profile["id"], "company_id": context.company_id,
        "name": context.manager_profile["name"], "title": context.manager_profile["title"],
        "personality": type("Personality", (), {"value": "friendly"})(),
    })()
    project = type("ProjectRow", (), {"title": "Payments API"})()
    reply = _run_immediate(create_manager_response(
        db, MockAIService(), manager, project, None, [], "Can you help?", "Task clarification"
    ))
    assert reply in db.added
    assert reply.sender_type.value == "manager"
    assert reply.content
