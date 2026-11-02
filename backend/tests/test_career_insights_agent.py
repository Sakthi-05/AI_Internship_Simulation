"""Deterministic tests for evidence-backed career insights."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import asyncio

import pytest

from app.agents.context_manager import AgentContextManager
from app.agents.contracts import AgentContext, EvidenceReference
from app.agents.orchestrator import AgentOrchestrator
from app.agents.registry import AgentRegistry
from app.agents.specialists.career_insights_agent import register_career_insights_agent


@pytest.fixture(autouse=True)
def immediate_wait_for(monkeypatch):
    async def wait(awaitable, timeout):
        return await awaitable
    monkeypatch.setattr(asyncio, "wait_for", wait)


class Contexts:
    def __init__(self, ctx):
        self.ctx = ctx
        self.calls = []

    def build(self, db, **kwargs):
        self.calls.append(kwargs)
        return self.ctx


def run(awaitable):
    try:
        return awaitable.send(None)
    except StopIteration as result:
        return result.value
    finally:
        awaitable.close()


def setup():
    student, sim = "student-career", uuid4()
    now = datetime.now(timezone.utc)
    ids = [uuid4(), uuid4()]
    refs = [EvidenceReference(evidence_type="assessment_attempt", evidence_record_id=i,
        student_id=student, occurred_at=now - timedelta(days=7 * n), source="assessment_attempts")
        for n, i in enumerate(ids)]
    interaction_ids = [uuid4(), uuid4()]
    refs.extend(EvidenceReference(evidence_type="interaction_evaluation", evidence_record_id=i,
        student_id=student, simulation_id=sim, occurred_at=now - timedelta(days=7 * n),
        source="interaction_evaluations") for n, i in enumerate(interaction_ids))
    ctx = AgentContext(student_id=student, simulation_id=sim, evidence=refs,
        profile="career_insights", assessment_history=[
            {"id": ids[0], "competencies": {"technical_knowledge": 72,
                                                "problem_solving": 55}},
            {"id": ids[1], "competencies": {"technical_knowledge": 84,
                                                "problem_solving": 50}},
        ], interaction_history=[
            {"occurred_at": refs[2].occurred_at, "evaluation": {"id": interaction_ids[0],
                "competencies": {"technical_knowledge": 72}}},
            {"occurred_at": refs[3].occurred_at, "evaluation": {"id": interaction_ids[1],
                "competencies": {"technical_knowledge": 84}}},
        ], competencies={"strengths": ["technical_knowledge"],
                         "weaknesses": ["problem_solving"], "observations": []})
    contexts = Contexts(ctx)
    registry = AgentRegistry()
    register_career_insights_agent(registry, db=object(), context_manager=contexts)
    return AgentOrchestrator(registry), contexts, student, sim


def test_registered_agent_uses_shared_context_and_returns_scoped_evidence():
    orchestrator, contexts, student, sim = setup()
    result = run(orchestrator.execute(__import__("app.agents.contracts", fromlist=["AgentRequest"]).AgentRequest(
        agent_id="career_insights", simulation_id=sim,
        payload={"student_id": student, "simulation_id": str(sim),
                 "recommendations": ["Review feedback"],
                 "readiness": {"readiness_score": 74}})))
    assert result.status.value == "succeeded"
    output = result.result
    assert output["status"] == "available"
    assert {x["dimension"] for x in output["strengths"]} == {"technical_knowledge"}
    assert {x["dimension"] for x in output["competency_gaps"]} == {"problem_solving"}
    trend = next(x for x in output["trends"] if x["dimension"] == "technical_knowledge")
    assert trend["direction"] == "declining" and trend["observations"] == 2
    assert output["recommendations"] == ["Review feedback"]
    assert output["readiness"] == {"readiness_score": 74}
    assert all(ref["student_id"] == student for ref in output["evidence_references"])
    assert contexts.calls and contexts.calls[0]["profile"] == "career_insights"


def test_missing_and_unscored_evidence_is_not_a_gap_and_trend_is_withheld():
    orchestrator, contexts, student, sim = setup()
    contexts.ctx.assessment_history = []
    contexts.ctx.evidence = []
    contexts.ctx.competencies = {"strengths": [], "weaknesses": [], "observations": []}
    from app.agents.contracts import AgentRequest
    response = run(orchestrator.execute(AgentRequest(agent_id="career_insights", simulation_id=sim,
        payload={"student_id": student, "simulation_id": str(sim)})))
    assert response.status.value == "succeeded"
    assert response.result["status"] == "insufficient_evidence"
    assert response.result["strengths"] == [] and response.result["competency_gaps"] == []
    assert response.result["trends"] == []
    assert response.result["trend_status"] == "insufficient_history"


def test_single_observation_withholds_trend_and_keeps_existing_recommendations():
    orchestrator, contexts, student, sim = setup()
    contexts.ctx.assessment_history = contexts.ctx.assessment_history[:1]
    contexts.ctx.evidence = [contexts.ctx.evidence[2]]
    contexts.ctx.interaction_history = contexts.ctx.interaction_history[:1]
    from app.agents.contracts import AgentRequest
    response = run(orchestrator.execute(AgentRequest(agent_id="career_insights", simulation_id=sim,
        payload={"student_id": student, "simulation_id": str(sim), "recommendations": ["Existing"]})))
    assert response.result["trends"] == []
    assert response.result["trend_status"] == "insufficient_history"
    assert response.result["recommendations"] == ["Existing"]


def test_context_identity_mismatch_fails_closed():
    orchestrator, contexts, student, sim = setup()
    contexts.ctx.student_id = "different-student"
    from app.agents.contracts import AgentRequest
    response = run(orchestrator.execute(AgentRequest(agent_id="career_insights", simulation_id=sim,
        payload={"student_id": student, "simulation_id": str(sim)})))
    assert response.status.value == "failed"
    assert response.error.code == "agent_execution_failed"
