"""Focused, database-free tests for competency-aware task-focus proposals."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agents.contracts import AgentContext, AgentRequest, EvidenceReference
from app.agents.orchestrator import AgentOrchestrator
from app.agents.registry import AgentRegistry
from app.agents.specialists.adaptation_agent import (
    AdaptationAgentInput,
    register_adaptation_agent,
)
from app.agents.specialists.competency_agent import register_competency_agent
from app.services.intelligence_service import COMPETENCIES
from app.services.engines.task_engine import _FOCUS_TASKS
from app.services.ai.review_service import ReviewService
from app.models.enums import ReviewResult, ReviewSeverity, ReviewStatus


async def _immediate_wait_for(awaitable, timeout):
    return await awaitable


@pytest.fixture(autouse=True)
def no_platform_event_loop(monkeypatch):
    monkeypatch.setattr(asyncio, "wait_for", _immediate_wait_for)


class ContextRouter:
    def __init__(self, adaptation, competency, error_profiles=None):
        self.adaptation = adaptation
        self.competency = competency
        self.error_profiles = error_profiles or set()
        self.calls = []

    def build(self, db, **kwargs):
        self.calls.append(kwargs)
        if kwargs["profile"] in self.error_profiles:
            raise RuntimeError("private database detail")
        return self.adaptation if kwargs["profile"] == "adaptation" else self.competency


def reference(kind, record, student, simulation, task, **kwargs):
    return EvidenceReference(evidence_type=kind, evidence_record_id=record,
                             student_id=student, simulation_id=simulation,
                             task_id=task, source=kind, **kwargs)


def make_case(*, target="problem_solving", score=62, state="sufficient_evidence",
              review_task=None, owner="intern-7", status="completed", other_sim=False):
    student, sim_id, task_id, review_id, version_id, criteria_id, attempt_id = [
        owner, uuid4(), uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    ]
    actual_task = review_task or task_id
    other_sim_id = uuid4() if other_sim else sim_id
    now = datetime.now(timezone.utc)
    review_ref = reference("technical_review", review_id, student, sim_id, actual_task,
                           submission_id=uuid4(), submission_version_id=version_id,
                           criteria_version_id=criteria_id)
    version_ref = reference("submission_version", version_id, student, sim_id, actual_task,
                            submission_id=review_ref.submission_id,
                            submission_version_id=version_id, criteria_version_id=criteria_id)
    adaptation_context = AgentContext(
        simulation_id=sim_id, student_id=student, task_id=actual_task,
        current_task_id=task_id, profile="adaptation",
        technical_reviews=[{"id": review_id, "version_id": version_id, "status": status}],
        submission_history=[{"student_id": student, "version_id": version_id}],
        progression_history=[{"task_id": task_id, "sequence": 2, "status": "in_progress"}],
        evidence=[review_ref, version_ref],
    )
    scored_ref = reference("assessment_attempt", attempt_id, student,
                           None if not other_sim else other_sim_id, actual_task)
    competency_context = AgentContext(
        simulation_id=sim_id, student_id=student, task_id=actual_task,
        current_task_id=task_id, profile="competency", evidence=[scored_ref],
        assessment_history=[{"id": attempt_id, "status": "completed", "completed_at": now,
                             "competencies": {key: (score if key == target else 86)
                                              for key in COMPETENCIES}}],
    )
    contexts = ContextRouter(adaptation_context, competency_context)
    registry = AgentRegistry()
    register_competency_agent(registry, object(), context_manager=contexts)
    adaptation_agent = register_adaptation_agent(registry, object(), context_manager=contexts)
    orchestrator = AgentOrchestrator(registry)
    adaptation_agent.orchestrator = orchestrator
    payload = AdaptationAgentInput(
        simulation_id=sim_id, student_id=student, task_id=task_id,
        review_id=review_id,
        technical_adaptation={"status": "applied", "next_difficulty": "intermediate"},
    )
    return orchestrator, contexts, payload, target, state


def run(coroutine):
    try:
        return coroutine.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coroutine.close()


@pytest.mark.parametrize("dimension", COMPETENCIES)
def test_each_canonical_dimension_can_become_a_supported_task_focus(dimension):
    orchestrator, contexts, payload, _target, _state = make_case(target=dimension)
    response = run(orchestrator.execute(AgentRequest(
        agent_id="adaptation", simulation_id=payload.simulation_id,
        payload=payload.model_dump(mode="json"))))
    assert response.status.value == "succeeded"
    result = response.result
    assert result["status"] == "recommended"
    assert result["focus_competencies"] == [dimension]
    assert result["difficulty_changed_by_agent"] is False
    assert result["existing_next_difficulty"] == "intermediate"
    assert any(ref["evidence_type"] == "technical_review" for ref in result["evidence_references"])
    expected = _FOCUS_TASKS.get(dimension)
    assert result["proposed_task_type"] == (expected[0].value if expected else None)
    assert contexts.calls[0]["profile"] == "adaptation"


@pytest.mark.parametrize("state", ["missing_evidence", "insufficient_evidence", "conflicting_evidence"])
def test_missing_insufficient_or_conflicting_competency_evidence_falls_back(state):
    orchestrator, contexts, payload, *_ = make_case(score=52, state=state)
    manager = orchestrator.registry.get("competency").context_manager
    competency_context = manager.competency
    competency_context.assessment_history = []
    if state == "missing_evidence":
        competency_context.evidence = []
        competency_context.competencies = {"observations": []}
    elif state == "insufficient_evidence":
        observation_id = uuid4()
        competency_context.evidence = [reference(
            "soft_skill_evidence", observation_id, payload.student_id,
            payload.simulation_id, payload.task_id,
        )]
        competency_context.competencies = {"observations": [{
            "id": observation_id, "competency": "problem_solving",
            "status": "insufficient_evidence", "score": None,
        }]}
    else:
        attempt_id = uuid4()
        competency_context.evidence = [reference("assessment_attempt", attempt_id,
                                                payload.student_id, None, payload.task_id)]
        competency_context.assessment_history = [
            {"id": attempt_id, "completed_at": datetime.now(timezone.utc),
             "competencies": {"problem_solving": 51}},
            {"id": attempt_id, "completed_at": datetime.now(timezone.utc),
             "competencies": {"problem_solving": 99}},
        ]
    response = run(orchestrator.execute(AgentRequest(
        agent_id="adaptation", simulation_id=payload.simulation_id,
        payload=payload.model_dump(mode="json"))))
    assert response.status.value == "succeeded"
    assert response.result["status"] == "fallback"
    assert response.result["focus_competencies"] == []


def test_high_dimension_is_reported_as_strength_without_increasing_difficulty():
    orchestrator, _contexts, payload, *_ = make_case(target="technical_knowledge", score=90)
    response = run(orchestrator.execute(AgentRequest(
        agent_id="adaptation", simulation_id=payload.simulation_id,
        payload=payload.model_dump(mode="json"))))
    assert response.result["status"] == "fallback"
    assert response.result["focus_competencies"] == []
    assert "technical_knowledge" in response.result["strength_competencies"]
    assert response.result["difficulty_changed_by_agent"] is False
    assert response.result["existing_next_difficulty"] == "intermediate"


def test_repeated_invocation_is_deterministic_and_does_not_persist_or_inflate():
    orchestrator, _contexts, payload, *_ = make_case()
    request = AgentRequest(agent_id="adaptation", simulation_id=payload.simulation_id,
                           payload=payload.model_dump(mode="json"))
    first = run(orchestrator.execute(request))
    second = run(orchestrator.execute(request))
    assert first.result == second.result


def test_student_and_simulation_context_mismatch_is_rejected_or_safely_skipped():
    orchestrator, contexts, payload, *_ = make_case()
    contexts.adaptation.student_id = "other-student"
    response = run(orchestrator.execute(AgentRequest(
        agent_id="adaptation", simulation_id=payload.simulation_id,
        payload=payload.model_dump(mode="json"))))
    assert response.status.value == "failed"
    assert response.error.code == "agent_execution_failed"


def test_evidence_from_another_simulation_cannot_trigger_task_focus():
    orchestrator, contexts, payload, *_ = make_case(other_sim=True)
    contexts.competency.assessment_history = [{
        "id": contexts.competency.assessment_history[0]["id"],
        "completed_at": datetime.now(timezone.utc),
        "competencies": {key: 55 for key in COMPETENCIES},
    }]
    response = run(orchestrator.execute(AgentRequest(
        agent_id="adaptation", simulation_id=payload.simulation_id,
        payload=payload.model_dump(mode="json"))))
    assert response.status.value == "succeeded"
    assert response.result["status"] == "fallback"
    assert response.result["focus_competencies"] == []


def test_context_failure_uses_safe_technical_fallback():
    orchestrator, contexts, payload, *_ = make_case()
    contexts.error_profiles.add("adaptation")
    response = run(orchestrator.execute(AgentRequest(
        agent_id="adaptation", simulation_id=payload.simulation_id,
        payload=payload.model_dump(mode="json"))))
    assert response.status.value == "succeeded"
    assert response.result["status"] == "fallback"
    assert response.result["reason"] == "simulation_context_unavailable"


def test_review_service_requests_agent_focus_before_dynamic_task_generation():
    orchestrator, _contexts, payload, *_ = make_case()
    review = type("Review", (), {
        "id": payload.review_id,
        "version": type("Version", (), {
            "submission": type("Submission", (), {"student_id": payload.student_id})()
        })(),
    })()
    service = ReviewService(object(), orchestrator=orchestrator)
    focus, recorded = run(service._competency_task_focus(
        object(), simulation=type("Simulation", (), {"id": payload.simulation_id})(),
        task=type("Task", (), {"id": payload.task_id})(), review=review,
        adaptation={"status": "applied", "next_difficulty": "intermediate"},
    ))
    assert focus["status"] == "recommended"
    assert focus["focus_competencies"] == ["problem_solving"]
    assert recorded["proposed_task_type"] == _FOCUS_TASKS["problem_solving"][0].value


def test_completed_review_retry_returns_existing_result_without_repeating_adaptation(monkeypatch):
    from app.services.ai import review_service as review_module

    review_id, version_id, project_id = uuid4(), uuid4(), uuid4()
    project = SimpleNamespace(id=project_id)
    version = SimpleNamespace(
        id=version_id, version_number=2, submission_id=uuid4(),
        submission=SimpleNamespace(current_version=2, task_id=uuid4(), task=SimpleNamespace(
            sprint=SimpleNamespace(project=project))),
    )
    existing = SimpleNamespace(
        id=review_id, status=ReviewStatus.COMPLETED, result=ReviewResult.APPROVED,
        severity=SimpleNamespace(value="info"), summary="Already reviewed.",
        strengths=[], risks=[], actions=[], score=90, raw_payload={"evaluation_source": "mock"},
    )
    db = SimpleNamespace(execute=lambda *_: SimpleNamespace(
        scalars=lambda: SimpleNamespace(first=lambda: existing)))

    class SpyOrchestrator:
        calls = 0

        async def execute(self, request):
            self.calls += 1
            raise AssertionError("completed review retry must not request another adaptation")

    orchestrator = SpyOrchestrator()
    monkeypatch.setattr(review_module, "build_review_context", lambda *_: {})
    result = run(ReviewService(object(), orchestrator=orchestrator).review_submission_version(db, version))
    assert result["review_id"] == str(review_id)
    assert result["status"] == ReviewStatus.COMPLETED.value
    assert orchestrator.calls == 0
