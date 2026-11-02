"""Technical review agent delegates evaluation to the established review service."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agents import AgentContext, AgentRequest, AgentRegistry, AgentOrchestrator, EvidenceReference
from app.agents.specialists.technical_review_agent import (
    TechnicalReviewAgent, TechnicalReviewContextError, TechnicalReviewInput,
    create_technical_review_orchestrator, register_technical_review_agent,
)
from app.models.project import Project
from app.models.criteria_version import AcceptanceCriteriaVersion
from app.models.simulation import Simulation
from app.models.sprint import Sprint
from app.models.submission import AIReview, Submission, SubmissionVersion
from app.models.task import Task


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


class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.refresh_calls = []

    def get(self, model, key):
        return self.rows.get((model, key))

    def refresh(self, instance, **kwargs):
        self.refresh_calls.append((instance, kwargs))

    def execute(self, _statement):
        statement_text = str(_statement)
        match = next((row for (model, _key), row in self.rows.items()
                      if model is SubmissionVersion and model.__tablename__ in statement_text), None)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: match))


class FakeReviewService:
    calls = []
    result = None

    def __init__(self, ai_service, **kwargs):
        self.ai_service = ai_service
        self.orchestrator = kwargs.get("orchestrator")

    async def review_submission_version(self, db, version):
        self.calls.append((db, version, self.ai_service))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeProvider:
    provider_name = "mock"


def make_case(monkeypatch, *, criteria_snapshot=True, current_version=3, version_number=3):
    from app.services.ai import review_service
    from app.agents.specialists import technical_review_agent as agent_module

    ids = [uuid4() for _ in range(7)]
    sim_id, student_id, task_id, submission_id, version_id, project_id, sprint_id = ids
    simulation = SimpleNamespace(id=sim_id, student_id="student-a", project_id=project_id)
    submission = SimpleNamespace(id=submission_id, task_id=task_id, student_id="student-a",
                                 current_version=current_version)
    criteria_id = uuid4()
    metadata = {"acceptance_criteria_snapshot": ["Use bounded retries"]} if criteria_snapshot else {}
    if criteria_snapshot:
        metadata["acceptance_criteria_version"] = {"id": str(criteria_id), "version_number": 4}
    version = SimpleNamespace(id=version_id, submission_id=submission_id,
                              version_number=version_number, version_metadata=metadata,
                              submitted_at=None, files=[], submission=submission)
    task = SimpleNamespace(id=task_id, sprint_id=sprint_id)
    sprint = SimpleNamespace(id=sprint_id, project_id=project_id)
    project = SimpleNamespace(id=project_id)
    criteria = SimpleNamespace(id=criteria_id, task_id=task_id, version_number=4)
    db = FakeDB({(Simulation, sim_id): simulation, (Submission, submission_id): submission,
                 (SubmissionVersion, version_id): version, (Task, task_id): task,
                 (Sprint, sprint_id): sprint, (Project, project_id): project,
                 (AcceptanceCriteriaVersion, criteria_id): criteria})
    evidence = EvidenceReference(evidence_type="submission_version", evidence_record_id=version_id,
                                 student_id="student-a", simulation_id=sim_id, task_id=task_id,
                                 submission_id=submission_id, submission_version_id=version_id,
                                 criteria_version_id=criteria_id, source="submission_versions")
    ctx = AgentContext(simulation_id=sim_id, student_id="student-a", task_id=task_id,
                       current_task_id=task_id, profile="technical_review", evidence=[evidence])
    manager = FakeContextManager(ctx)
    FakeReviewService.calls = []
    FakeReviewService.result = {
        "review_id": str(uuid4()), "version_id": str(version_id), "status": "completed",
        "result": "changes_required", "severity": "warning", "summary": "Add retry bounds.",
        "score": 72, "strengths": ["Clear structure"], "risks": ["Unbounded loop"],
        "actions": ["Add a retry cap"], "raw": {
            "evaluation_source": "mock", "acceptance_criteria": [
                {"criterion": "Use bounded retries", "met": False,
                 "evidence": "No upper bound found in submitted text."}]}}
    monkeypatch.setattr(review_service, "ReviewService", FakeReviewService)
    monkeypatch.setattr(agent_module, "ReviewService", FakeReviewService)
    monkeypatch.setattr(review_service, "build_review_context", lambda _db, _version: {
        "evidence_level": "full", "acceptance_criteria": ["Use bounded retries"]})
    monkeypatch.setattr(agent_module, "build_review_context", lambda _db, _version: {
        "evidence_level": "full", "acceptance_criteria": ["Use bounded retries"]})
    return db, manager, sim_id, task_id, submission_id, version_id


def run(coro):
    try:
        return coro.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coro.close()


def test_registered_agent_invokes_existing_review_service_and_propagates_provenance(monkeypatch):
    db, manager, sim_id, task_id, submission_id, version_id = make_case(monkeypatch)
    registry = AgentRegistry()
    register_technical_review_agent(registry, db, context_manager=manager, ai_service=FakeProvider())
    response = run(AgentOrchestrator(registry).execute(AgentRequest(
        agent_id="technical_review", simulation_id=sim_id,
        payload={"simulation_id": str(sim_id), "student_id": "student-a",
                 "submission_id": str(submission_id), "task_id": str(task_id)})))
    assert response.status.value == "succeeded"
    result = response.result
    assert result["submission_version_id"] == str(version_id)
    assert result["criteria_version_number"] == 4
    assert result["findings"][0]["criterion"] == "Use bounded retries"
    assert result["supporting_evidence"][0]["submission_version_id"] == str(version_id)
    assert manager.calls[0]["profile"] == "technical_review"
    assert FakeReviewService.calls[0][1].id == version_id


def test_missing_historical_criteria_blocks_current_criteria_fallback(monkeypatch):
    db, manager, sim_id, task_id, submission_id, _ = make_case(monkeypatch, criteria_snapshot=False)
    agent = TechnicalReviewAgent(db, context_manager=manager, ai_service=FakeProvider())
    with pytest.raises(TechnicalReviewContextError, match="criteria snapshot"):
        run(agent.execute(TechnicalReviewInput(simulation_id=sim_id, student_id="student-a",
                                               submission_id=submission_id, task_id=task_id)))
    assert not FakeReviewService.calls


def test_stale_unreviewed_version_cannot_be_evaluated(monkeypatch):
    db, manager, sim_id, task_id, submission_id, version_id = make_case(
        monkeypatch, current_version=4, version_number=3)
    agent = TechnicalReviewAgent(db, context_manager=manager, ai_service=FakeProvider())
    with pytest.raises(TechnicalReviewContextError, match="stale"):
        run(agent.execute(TechnicalReviewInput(simulation_id=sim_id, student_id="student-a",
                                               submission_id=submission_id, submission_version_id=version_id,
                                               task_id=task_id)))
    assert not FakeReviewService.calls


def test_missing_submission_student_context_is_rejected(monkeypatch):
    db, manager, sim_id, task_id, submission_id, _ = make_case(monkeypatch)
    db.rows.pop((Submission, submission_id))
    agent = TechnicalReviewAgent(db, context_manager=manager, ai_service=FakeProvider())
    with pytest.raises(TechnicalReviewContextError, match="Submission is unavailable"):
        run(agent.execute(TechnicalReviewInput(simulation_id=sim_id, student_id="student-a",
                                               submission_id=submission_id, task_id=task_id)))


def test_factory_registers_instance_owned_agent(monkeypatch):
    db, manager, *_ = make_case(monkeypatch)
    orchestrator = create_technical_review_orchestrator(db, context_manager=manager,
                                                        ai_service=FakeProvider())
    assert orchestrator.registry.registered_ids() == ["technical_review"]
