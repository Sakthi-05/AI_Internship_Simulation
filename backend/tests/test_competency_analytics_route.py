"""Simulation analytics route integration with the registered competency agent."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.agents.contracts import AgentContext, EvidenceReference
from app.agents.orchestrator import AgentOrchestrator
from app.agents.registry import AgentRegistry
from app.agents.specialists.competency_agent import register_competency_agent
from app.api.routes import intelligence
from app.core.security import CurrentUser
from app.models.simulation import Simulation


async def _immediate_wait_for(awaitable, timeout):
    return await awaitable


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


class FakeDB:
    def __init__(self, simulation):
        self.simulation = simulation

    def get(self, model, key):
        return self.simulation if model is Simulation and key == self.simulation.id else None


def run(coroutine):
    try:
        return coroutine.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coroutine.close()


def setup(monkeypatch, *, owns_simulation=True, context_error=None):
    student_id, simulation_id, task_id, attempt_id = "student-api", uuid4(), uuid4(), uuid4()
    simulation = SimpleNamespace(id=simulation_id,
                                 student_id=student_id if owns_simulation else "other-student",
                                 current_task_id=task_id)
    db = FakeDB(simulation)
    reference = EvidenceReference(
        evidence_type="assessment_attempt", evidence_record_id=attempt_id,
        student_id=student_id, occurred_at=datetime.now(timezone.utc),
        source="assessment_attempts",
    )
    context = AgentContext(
        simulation_id=simulation_id, student_id=student_id, task_id=task_id,
        current_task_id=task_id, profile="competency", evidence=[reference],
        assessment_history=[{"id": attempt_id, "status": "completed",
                             "completed_at": reference.occurred_at,
                             "competencies": {"technical_knowledge": 78}}],
    )
    context_manager = FakeContextManager(context, context_error)
    registry = AgentRegistry()
    register_competency_agent(registry, context_manager=context_manager)
    orchestrator = AgentOrchestrator(registry)
    user = CurrentUser(student_id=student_id, role="student", raw_claims={})
    monkeypatch.setattr(intelligence, "get_analytics", lambda *_args: {
        "summary": {"simulations": 1}, "competencies": {"technical_knowledge": 78},
    })
    return db, user, simulation_id, orchestrator, context_manager


def test_simulation_analytics_invokes_orchestrator_agent_and_adds_optional_profile(monkeypatch):
    db, user, simulation_id, orchestrator, context_manager = setup(monkeypatch)
    result = run(intelligence.analytics(simulation_id, db, user, orchestrator))

    assert result["summary"] == {"simulations": 1}
    assert result["competencies"] == {"technical_knowledge": 78}
    assessment = result["competency_assessment"]
    technical = next(item for item in assessment["dimensions"]
                     if item["dimension"] == "technical_knowledge")
    assert technical["current_score"] == 78
    assert technical["evidence_status"] == "sufficient_evidence"
    assert technical["supporting_evidence"][0]["evidence_type"] == "assessment_attempt"
    assert result["career_insights"]["status"] == "unavailable"
    assert result["career_insights"]["readiness"] is None
    assert context_manager.calls[0]["profile"] == "competency"
    assert context_manager.calls[0]["simulation_id"] == simulation_id


def test_unscoped_analytics_preserves_legacy_response_without_agent_call(monkeypatch):
    db, user, _simulation_id, _orchestrator, context_manager = setup(monkeypatch)
    result = run(intelligence.analytics(None, db, user, _orchestrator))
    assert result == {"summary": {"simulations": 1},
                     "competencies": {"technical_knowledge": 78}}
    assert context_manager.calls == []


def test_unavailable_simulation_context_does_not_break_analytics(monkeypatch):
    db, user, simulation_id, orchestrator, _context_manager = setup(
        monkeypatch, context_error=RuntimeError("private db/provider detail"))
    result = run(intelligence.analytics(simulation_id, db, user, orchestrator))
    assert result["summary"] == {"simulations": 1}
    assert result["competency_assessment"] == {
        "status": "unavailable", "error_code": "agent_execution_failed", "dimensions": []}
    assert result["career_insights"]["status"] == "unavailable"
    assert "private" not in str(result)


def test_non_owner_cannot_request_simulation_competency_assessment(monkeypatch):
    db, user, simulation_id, orchestrator, context_manager = setup(monkeypatch, owns_simulation=False)
    with pytest.raises(HTTPException) as exc:
        run(intelligence.analytics(simulation_id, db, user, orchestrator))
    assert exc.value.status_code == 403
    assert context_manager.calls == []
