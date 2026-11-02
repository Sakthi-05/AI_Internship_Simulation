"""Deterministic tests for evidence-only competency consolidation."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.agents import AgentContext, AgentOrchestrator, AgentRegistry, EvidenceReference
from app.agents.contracts import AgentRequest
from app.agents.specialists.competency_agent import (
    CompetencyAgent,
    CompetencyContextError,
    CompetencyAgentInput,
    invoke_competency_agent,
    register_competency_agent,
)
from app.services.intelligence_service import COMPETENCIES


async def _immediate_wait_for(awaitable, timeout):
    return await awaitable


@pytest.fixture(autouse=True)
def no_platform_event_loop(monkeypatch):
    monkeypatch.setattr(asyncio, "wait_for", _immediate_wait_for)


class FakeContextManager:
    def __init__(self, context):
        self.context = context
        self.calls = []

    def build(self, db, **kwargs):
        self.calls.append(kwargs)
        return self.context


class UnusedProvider:
    def __init__(self):
        self.calls = 0

    async def generate_json(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("Competency consolidation must not generate scores with a model")


def _ref(kind, record_id, student, simulation=None, task=None, **kwargs):
    return EvidenceReference(evidence_type=kind, evidence_record_id=record_id,
                             student_id=student, simulation_id=simulation,
                             task_id=task, source=kind, **kwargs)


def case():
    student, simulation, task = "student-c", uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    attempt_id, evaluation_id, soft_id = uuid4(), uuid4(), uuid4()
    references = [
        _ref("assessment_attempt", attempt_id, student),
        _ref("interaction_evaluation", evaluation_id, student, simulation, task),
        _ref("soft_skill_evidence", soft_id, student, simulation, task,
             workplace_event_id=uuid4(), occurred_at=now - timedelta(days=1)),
    ]
    context = AgentContext(
        student_id=student, simulation_id=simulation, task_id=task,
        current_task_id=task, profile="competency", evidence=references,
        assessment_history=[{"id": attempt_id, "status": "completed",
                             "completed_at": now - timedelta(days=2), "competencies": {
                                 "technical_knowledge": 76, "problem_solving": 68,
                                 "decision_making": 82, "critical_thinking": 73,
                             }}],
        interaction_history=[{"interaction_id": uuid4(), "occurred_at": now,
                              "evaluation": {"id": evaluation_id, "competencies": {
                                  "technical_knowledge": 80, "communication": 88,
                                  "adaptability": 77, "time_management": 71,
                                  "professionalism": 84,
                              }, "confidence": 0.82,
                                  "recommendations": ["Continue documenting trade-offs"]}}],
        competencies={"strengths": ["Communication"], "weaknesses": ["Problem Solving"],
                      "observations": [{"id": soft_id, "competency": "adaptability",
                                        "status": "scored", "score": 74,
                                        "summary": "Recorded adaptation after criteria changed."}]},
    )
    manager = FakeContextManager(context)
    return student, simulation, task, context, manager


def run(coroutine):
    try:
        return coroutine.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coroutine.close()


def test_registration_and_orchestrator_invocation_uses_competency_profile():
    student, simulation, task, context, manager = case()
    registry = AgentRegistry()
    register_competency_agent(registry, object(), context_manager=manager)
    response = run(AgentOrchestrator(registry).execute(AgentRequest(
        agent_id="competency", simulation_id=simulation,
        payload={"simulation_id": str(simulation), "student_id": student,
                 "task_id": str(task)})))
    assert response.status.value == "succeeded"
    assert manager.calls[0]["profile"] == "competency"
    assert len(response.result["dimensions"]) == len(COMPETENCIES)


def test_all_canonical_dimensions_and_existing_scores_are_reported_without_rescoring():
    student, simulation, task, context, manager = case()
    provider = UnusedProvider()
    output = run(CompetencyAgent(object(), context_manager=manager, ai_service=provider).execute(
        CompetencyAgentInput(simulation_id=simulation, student_id=student, task_id=task)))
    dimensions = {item.dimension: item for item in output.dimensions}
    assert set(dimensions) == set(COMPETENCIES)
    assert dimensions["technical_knowledge"].current_score == 80
    assert dimensions["communication"].current_score == 88
    assert dimensions["adaptability"].current_score == 77
    assert dimensions["communication"].strengths == ["communication"]
    assert dimensions["problem_solving"].gaps == ["problem_solving"]
    assert dimensions["communication"].recommendations == ["Continue documenting trade-offs"]
    assert dimensions["communication"].confidence == 0.82
    assert provider.calls == 0


def test_missing_and_insufficient_evidence_are_distinct_from_low_scores():
    student, simulation, task, context, manager = case()
    context.assessment_history = []
    context.interaction_history = []
    context.competencies["observations"] = [{"id": context.evidence[-1].evidence_record_id,
                                               "competency": "adaptability",
                                               "status": "insufficient_evidence",
                                               "score": None}]
    output = run(CompetencyAgent(object(), context_manager=manager).execute(
        CompetencyAgentInput(simulation_id=simulation, student_id=student, task_id=task)))
    dimensions = {item.dimension: item for item in output.dimensions}
    assert dimensions["adaptability"].evidence_status == "insufficient_evidence"
    assert dimensions["adaptability"].current_score is None
    assert dimensions["technical_knowledge"].evidence_status == "missing_evidence"
    assert dimensions["technical_knowledge"].current_score is None


def test_repeated_evidence_does_not_increase_counts_and_conflicting_duplicate_is_flagged():
    student, simulation, task, context, manager = case()
    attempt = context.assessment_history[0]
    duplicate = dict(attempt)
    duplicate["competencies"] = dict(attempt["competencies"])
    context.assessment_history.append(duplicate)
    output = run(CompetencyAgent(object(), context_manager=manager).execute(
        CompetencyAgentInput(simulation_id=simulation, student_id=student, task_id=task)))
    dims = {item.dimension: item for item in output.dimensions}
    assert dims["decision_making"].evidence_count == 1

    duplicate["competencies"]["decision_making"] = 15
    conflict = run(CompetencyAgent(object(), context_manager=manager).execute(
        CompetencyAgentInput(simulation_id=simulation, student_id=student, task_id=task)))
    conflicted = {item.dimension: item for item in conflict.dimensions}["decision_making"]
    assert conflicted.evidence_status == "conflicting_evidence"
    assert conflicted.current_score is None


def test_context_student_simulation_isolation_is_enforced():
    student, simulation, task, context, manager = case()
    context.student_id = "another-student"
    with pytest.raises(CompetencyContextError, match="does not match"):
        run(CompetencyAgent(object(), context_manager=manager).execute(
            CompetencyAgentInput(simulation_id=simulation, student_id=student, task_id=task)))


def test_evidence_from_another_simulation_is_excluded():
    student, simulation, task, context, manager = case()
    other_id = uuid4()
    context.evidence.append(_ref("interaction_evaluation", other_id, student, uuid4(), task))
    context.interaction_history.append({"occurred_at": datetime.now(timezone.utc),
                                        "evaluation": {"id": other_id,
                                                       "competencies": {"communication": 1}}})
    output = run(CompetencyAgent(object(), context_manager=manager).execute(
        CompetencyAgentInput(simulation_id=simulation, student_id=student, task_id=task)))
    communication = next(item for item in output.dimensions if item.dimension == "communication")
    assert communication.current_score == 88
    assert all(ref.evidence_record_id != other_id for ref in communication.supporting_evidence)


def test_uncompleted_review_is_not_used_and_historical_assessments_remain_unchanged():
    student, simulation, task, context, manager = case()
    prior = list(context.assessment_history)
    version_id, review_id = uuid4(), uuid4()
    context.submission_history = [{"version_id": version_id}]
    context.technical_reviews = [{"id": review_id, "version_id": version_id,
                                  "status": "failed", "score": 99}]
    context.evidence.append(_ref("technical_review", review_id, student, simulation, task,
                                 submission_id=uuid4(), submission_version_id=version_id))
    output = run(CompetencyAgent(object(), context_manager=manager).execute(
        CompetencyAgentInput(simulation_id=simulation, student_id=student, task_id=task)))
    assert output.assessment_history_count == len(prior)
    assert context.assessment_history == prior
    assert "technical_review" not in {
        ref.evidence_type for dim in output.dimensions for ref in dim.supporting_evidence
    }


def test_stale_review_without_matching_historical_submission_is_ignored():
    student, simulation, task, context, manager = case()
    stale_version, review_id = uuid4(), uuid4()
    context.submission_history = [{"version_id": uuid4(), "student_id": student}]
    context.technical_reviews = [{"id": review_id, "version_id": stale_version,
                                  "status": "completed", "score": 99}]
    context.evidence.append(_ref("technical_review", review_id, student, simulation, task,
                                 submission_id=uuid4(), submission_version_id=stale_version))
    context.evidence.append(_ref("submission_version", stale_version, student, simulation, task,
                                 submission_id=uuid4(), submission_version_id=stale_version))
    output = run(CompetencyAgent(object(), context_manager=manager).execute(
        CompetencyAgentInput(simulation_id=simulation, student_id=student, task_id=task)))
    assert "technical_review" not in {
        ref.evidence_type for dim in output.dimensions for ref in dim.supporting_evidence
    }


def test_registered_application_agent_can_be_invoked_with_request_scoped_db():
    student, simulation, task, context, manager = case()
    registry = AgentRegistry()
    register_competency_agent(registry, context_manager=manager)
    orchestrator = AgentOrchestrator(registry)
    response = run(invoke_competency_agent(orchestrator, object(), simulation_id=simulation,
                                           student_id=student, task_id=task))
    assert response.status.value == "succeeded"
    assert response.result["student_id"] == student


def test_application_owned_orchestrator_has_competency_registered():
    from app.main import app

    registry = app.state.agent_orchestrator.registry
    assert "competency" in registry
    assert "adaptation" in registry
    assert "technical_review" in registry
