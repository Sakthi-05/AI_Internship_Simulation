"""Deterministic Scenario Agent tests without provider or database calls."""
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.agents import AgentContext, AgentOrchestrator, AgentRegistry, AgentRequest, EvidenceReference
from app.agents.specialists.scenario_agent import (
    ScenarioAgent,
    ScenarioAgentInput,
    ScenarioCategory,
    create_scenario_orchestrator,
    register_scenario_agent,
)
from app.services.ai.ai_service import MockAIService


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
    # Agent/provider fakes complete immediately; avoid runner's blocked Windows socketpair.
    monkeypatch.setattr(asyncio, "wait_for", _immediate_wait_for)


class FakeContextManager:
    def __init__(self, context):
        self.context = context
        self.calls = []

    def build(self, db, **kwargs):
        self.calls.append(kwargs)
        return self.context


class FailingProvider:
    async def generate_json(self, *_args, **_kwargs):
        raise RuntimeError("provider secret/internal details")

    async def generate_text(self, *_args, **_kwargs):
        raise RuntimeError("not used")


def make_case(category: ScenarioCategory):
    simulation_id, task_id, criteria_id, trigger_id = [uuid4() for _ in range(4)]
    now = datetime.now(timezone.utc)
    activity = {
        ScenarioCategory.REQUIREMENT_CHANGE: "recorded_work",
        ScenarioCategory.COLLABORATION: "teammate_request",
        ScenarioCategory.TIME_MANAGEMENT: "deadline_warning",
        ScenarioCategory.PRIORITIZATION: "competing_work",
        ScenarioCategory.OWNERSHIP: "unowned_issue",
        ScenarioCategory.FEEDBACK_RESPONSE: "review_feedback",
    }[category]
    evidence_type = {
        ScenarioCategory.REQUIREMENT_CHANGE: "submission_version",
        ScenarioCategory.COLLABORATION: "manager_communication",
        ScenarioCategory.TIME_MANAGEMENT: "workflow_event",
        ScenarioCategory.PRIORITIZATION: "workflow_event",
        ScenarioCategory.OWNERSHIP: "workflow_event",
        ScenarioCategory.FEEDBACK_RESPONSE: "technical_review",
    }[category]
    context = AgentContext(
        simulation_id=simulation_id,
        student_id="student-1",
        task_id=task_id,
        current_task_id=task_id,
        profile="scenario",
        current_task={"id": task_id, "title": "Implement queue retries",
                      "description": "Add bounded retries", "status": "in_progress",
                      "task_status": "in_progress", "started_at": now,
                      "deadline": now, "difficulty": "intermediate"},
        current_sprint={"id": uuid4(), "number": 2, "status": "active"},
        active_criteria={"id": criteria_id, "version_number": 3, "status": "active",
                         "items": [{"text": "Retry transient failures", "state": "active"}]},
        criteria_version_id=criteria_id,
        evidence=[EvidenceReference(evidence_type=evidence_type,
                                    evidence_record_id=trigger_id,
                                    student_id="student-1", simulation_id=simulation_id,
                                    task_id=task_id, submission_id=uuid4() if category == ScenarioCategory.REQUIREMENT_CHANGE else None,
                                    submission_version_id=trigger_id if category == ScenarioCategory.REQUIREMENT_CHANGE else None,
                                    criteria_version_id=criteria_id if category == ScenarioCategory.REQUIREMENT_CHANGE else None,
                                    occurred_at=now, source=evidence_type)],
        submission_history=[{"id": uuid4(), "task_id": task_id, "version_id": trigger_id,
                             "version_number": 2}] if category == ScenarioCategory.REQUIREMENT_CHANGE else [],
        manager_communications=[{"id": trigger_id, "sender_type": "team_member",
                                  "content": "Can you coordinate this dependency?"}]
        if category == ScenarioCategory.COLLABORATION else [],
        scenario_history=[{"id": trigger_id, "event_type": {
            ScenarioCategory.TIME_MANAGEMENT: "deadline_warning",
            ScenarioCategory.PRIORITIZATION: "priority_change",
            ScenarioCategory.OWNERSHIP: "bug_report",
        }.get(category), "scenario_key": None, "status": "pending"}]
        if category in {ScenarioCategory.TIME_MANAGEMENT, ScenarioCategory.PRIORITIZATION,
                        ScenarioCategory.OWNERSHIP} else [],
        technical_reviews=[{"id": trigger_id, "result": "changes_required",
                            "summary": "Add retry handling"}]
        if category == ScenarioCategory.FEEDBACK_RESPONSE else [],
        competencies={"weaknesses": ["adaptability"]},
    )
    payload = ScenarioAgentInput(simulation_id=simulation_id, student_id="student-1",
                                 task_id=task_id, trigger_activity=activity,
                                 trigger_evidence_id=trigger_id,
                                 preferred_category=category)
    return context, payload, criteria_id, trigger_id


@pytest.mark.parametrize("category", list(ScenarioCategory))
def test_agent_proposes_each_supported_category_from_matching_persisted_evidence(category):
    context, payload, criteria_id, trigger_id = make_case(category)
    manager = FakeContextManager(context)
    agent = ScenarioAgent(object(), context_manager=manager, ai_service=MockAIService())
    output = _run_immediate(agent.execute(payload))
    assert output.decision == "proposed"
    assert output.scenario_category is category
    assert output.scenario_proposal.category is category
    assert output.task_id == payload.task_id
    assert output.simulation_id == payload.simulation_id
    assert output.criteria_version_id == criteria_id
    assert output.supporting_evidence[0].evidence_record_id == trigger_id
    assert output.competency_dimensions
    assert manager.calls == [{"simulation_id": payload.simulation_id,
                              "student_id": payload.student_id,
                              "profile": "scenario", "task_id": payload.task_id}]


def test_registry_registration_and_orchestrator_invocation():
    context, payload, _criteria_id, _trigger_id = make_case(ScenarioCategory.REQUIREMENT_CHANGE)
    registry = AgentRegistry()
    register_scenario_agent(registry, object(), context_manager=FakeContextManager(context),
                            ai_service=MockAIService())
    assert registry.registered_ids() == ["scenario"]
    orchestrator = AgentOrchestrator(registry)
    response = _run_immediate(orchestrator.execute(
        AgentRequest(agent_id="scenario", payload=payload.model_dump(mode="json"))))
    assert response.status.value == "succeeded"
    assert response.result["decision"] == "proposed"


def test_factory_creates_instance_owned_scenario_registry():
    context, _payload, _criteria_id, _trigger_id = make_case(ScenarioCategory.FEEDBACK_RESPONSE)
    first = create_scenario_orchestrator(object(), context_manager=FakeContextManager(context),
                                         ai_service=MockAIService())
    second = create_scenario_orchestrator(object(), context_manager=FakeContextManager(context),
                                          ai_service=MockAIService())
    assert first.registry is not second.registry
    assert first.registry.registered_ids() == ["scenario"]


def test_status_only_or_unstarted_requirement_change_is_rejected():
    context, payload, *_ = make_case(ScenarioCategory.REQUIREMENT_CHANGE)
    context.current_task["task_status"] = "todo"
    output = _run_immediate(ScenarioAgent(object(), context_manager=FakeContextManager(context),
                                         ai_service=MockAIService()).execute(payload))
    assert output.decision == "not_proposed"
    assert output.reason_code == "task_not_started"


def test_missing_or_mismatched_evidence_is_rejected():
    context, payload, *_ = make_case(ScenarioCategory.FEEDBACK_RESPONSE)
    payload.trigger_evidence_id = uuid4()
    output = _run_immediate(ScenarioAgent(object(), context_manager=FakeContextManager(context),
                                         ai_service=MockAIService()).execute(payload))
    assert output.reason_code == "trigger_evidence_missing"


@pytest.mark.parametrize("category", list(ScenarioCategory))
def test_duplicate_unresolved_and_contradictory_requirement_events_are_rejected(category):
    context, payload, *_ = make_case(category)
    context.scenario_history.append({"event_type": "requirement_change",
                                     "scenario_key": "requirement_change", "status": "pending"})
    output = _run_immediate(ScenarioAgent(object(), context_manager=FakeContextManager(context),
                                         ai_service=MockAIService()).execute(payload))
    assert output.decision == "not_proposed"
    assert output.reason_code in {"unresolved_requirement_change", "conflicting_unresolved_scenario"}


def test_same_category_unresolved_event_is_deduplicated():
    context, payload, *_ = make_case(ScenarioCategory.COLLABORATION)
    context.scenario_history.append({"event_type": "stakeholder_message",
                                     "scenario_key": "collaboration", "status": "acknowledged"})
    output = _run_immediate(ScenarioAgent(object(), context_manager=FakeContextManager(context),
                                         ai_service=MockAIService()).execute(payload))
    assert output.reason_code == "duplicate_unresolved_scenario"


def test_provider_failure_returns_bounded_deterministic_proposal():
    context, payload, *_ = make_case(ScenarioCategory.OWNERSHIP)
    output = _run_immediate(ScenarioAgent(object(), context_manager=FakeContextManager(context),
                                         ai_service=FailingProvider()).execute(payload))
    assert output.decision == "proposed"
    assert output.generation_source == "deterministic_fallback"
    assert output.scenario_proposal is not None
    assert "secret" not in output.reason
