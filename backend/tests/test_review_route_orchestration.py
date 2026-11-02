"""Route-level proof that the existing review API executes through orchestration."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.responses import JSONResponse

from app.agents import AgentContext
from app.agents.orchestrator import AgentOrchestrator
from app.agents.registry import AgentRegistry
from app.agents.specialists.technical_review_agent import register_technical_review_agent
from app.agents.contracts import ExecutionStatus, AgentResponse, AgentErrorInfo, ExecutionMetadata
from app.api.routes import reviews
from app.core.security import CurrentUser
from app.models.criteria_version import AcceptanceCriteriaVersion
from app.models.enums import ReviewStatus, SubmissionStatus
from app.models.project import Project
from app.models.simulation import Simulation
from app.models.sprint import Sprint
from app.models.submission import AIReview, Submission, SubmissionVersion
from app.models.task import Task
from app.services.ai.ai_service import MockAIService


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
    def __init__(self, rows, simulation):
        self.rows = rows
        self.simulation = simulation
        self.refreshes = []
        self.added = []
        self.commits = 0

    def get(self, model, key):
        return self.rows.get((model, key))

    def refresh(self, row, **kwargs):
        self.refreshes.append((row, kwargs))

    def execute(self, statement):
        query = str(statement)
        if "simulations" in query:
            value = self.simulation
        elif "submission_versions" in query:
            value = next((row for (model, _), row in self.rows.items()
                          if model is SubmissionVersion), None)
        else:
            value = None
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: value))

    def rollback(self):
        pass

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1


class CountingReviewService:
    calls = []
    response = None

    def __init__(self, provider, **kwargs):
        self.provider = provider
        self.orchestrator = kwargs.get("orchestrator")

    async def review_submission_version(self, db, version):
        self.calls.append((db, version, self.provider, self.orchestrator))
        return self.response


def make_route_case(monkeypatch, *, existing_review=None):
    from app.agents.specialists import technical_review_agent as agent_module

    sim_id, task_id, submission_id, version_id, project_id, sprint_id, criteria_id = [uuid4() for _ in range(7)]
    project = SimpleNamespace(id=project_id)
    task = SimpleNamespace(id=task_id, sprint_id=sprint_id)
    sprint = SimpleNamespace(id=sprint_id, project_id=project_id)
    submission = SimpleNamespace(id=submission_id, task_id=task_id, student_id="student-route",
                                 current_version=2, status=SubmissionStatus.UNDER_REVIEW)
    criteria = SimpleNamespace(id=criteria_id, task_id=task_id, version_number=5)
    version = SimpleNamespace(id=version_id, submission_id=submission_id, version_number=2,
                              status=SubmissionStatus.SUBMITTED,
                              version_metadata={
                                  "acceptance_criteria_snapshot": ["Handle transient failures"],
                                  "acceptance_criteria_version": {"id": str(criteria_id), "version_number": 5},
                              }, submitted_at=None, files=[], submission=submission)
    simulation = SimpleNamespace(id=sim_id, student_id="student-route", project_id=project_id,
                                 current_task_id=task_id)
    db = FakeDB({(Task, task_id): task, (Sprint, sprint_id): sprint,
                 (Project, project_id): project, (Simulation, sim_id): simulation,
                 (Submission, submission_id): submission, (SubmissionVersion, version_id): version,
                 (AcceptanceCriteriaVersion, criteria_id): criteria}, simulation)
    context = AgentContext(simulation_id=sim_id, student_id="student-route", task_id=task_id,
                           current_task_id=task_id, profile="technical_review")
    context_manager = FakeContextManager(context)
    registry = AgentRegistry()
    register_technical_review_agent(registry, context_manager=context_manager,
                                    ai_service=MockAIService())
    orchestrator = AgentOrchestrator(registry)
    user = CurrentUser(student_id="student-route", role="student", raw_claims={})

    monkeypatch.setattr(reviews, "_get_owned_project", lambda *_: project)
    monkeypatch.setattr(reviews, "_get_owned_version", lambda *_: version)
    monkeypatch.setattr(agent_module, "build_review_context", lambda *_: {
        "evidence_level": "full", "acceptance_criteria": ["Handle transient failures"]})
    monkeypatch.setattr(agent_module, "ReviewService", CountingReviewService)
    if existing_review is None:
        db.execute = FakeDB.execute.__get__(db, FakeDB)
    else:
        # The first (API idempotency) lookup sees an in-progress review and
        # returns before orchestration when its claim is still fresh.
        original_execute = db.execute

        def execute(statement):
            if "ai_reviews" in str(statement):
                return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: existing_review))
            return original_execute(statement)

        db.execute = execute

    result_id = existing_review.id if existing_review is not None else uuid4()
    CountingReviewService.calls = []
    CountingReviewService.response = {
        "review_id": str(result_id), "version_id": str(version_id),
        "project_id": str(project_id), "status": "completed", "result": "approved",
        "severity": "info", "summary": "All required behaviors are present.",
        "strengths": ["Bounded retry handling"], "risks": [], "actions": [],
        "score": 91, "raw": {"acceptance_criteria": [{
            "criterion": "Handle transient failures", "met": True,
            "evidence": "The retry path has a bounded attempt count."}]},
    }
    return db, project, version, simulation, user, orchestrator, context_manager


def run(coroutine):
    try:
        return coroutine.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coroutine.close()


def test_review_api_routes_through_orchestrator_agent_and_existing_service(monkeypatch):
    db, project, version, simulation, user, orchestrator, contexts = make_route_case(monkeypatch)
    response = run(reviews.create_review(project.id, version.id, db, user, MockAIService(), orchestrator))

    assert response["status"] == "completed"
    assert response["version_id"] == str(version.id)
    assert response["raw"]["acceptance_criteria"][0]["criterion"] == "Handle transient failures"
    assert contexts.calls == [{"simulation_id": simulation.id, "student_id": user.student_id,
                               "profile": "technical_review", "task_id": version.submission.task_id}]
    assert len(CountingReviewService.calls) == 1
    assert CountingReviewService.calls[0][0] is db
    assert CountingReviewService.calls[0][1] is version
    assert CountingReviewService.calls[0][3] is orchestrator
    assert len(db.refreshes) == 3  # route version lock + agent submission/version locks


def test_fresh_duplicate_in_progress_request_preserves_202_without_second_review(monkeypatch):
    now = reviews.datetime.now(reviews.timezone.utc)
    in_progress = SimpleNamespace(id=uuid4(), status=ReviewStatus.IN_PROGRESS,
                                   updated_at=now, created_at=now)
    db, project, version, _sim, user, orchestrator, _contexts = make_route_case(
        monkeypatch, existing_review=in_progress)
    response = run(reviews.create_review(project.id, version.id, db, user, MockAIService(), orchestrator))

    assert isinstance(response, JSONResponse)
    assert response.status_code == 202
    assert not CountingReviewService.calls


class FailedOrchestrator:
    async def execute(self, request):
        return AgentResponse(
            correlation_id=request.correlation_id, status=ExecutionStatus.FAILED,
            error=AgentErrorInfo(code="agent_execution_failed", message="Agent execution failed"),
            metadata=ExecutionMetadata(agent_id=request.agent_id),
        )


def test_orchestrator_failure_uses_existing_safe_503_error_path(monkeypatch):
    db, project, version, _simulation, user, _orchestrator, _contexts = make_route_case(monkeypatch)
    # The endpoint's established failure-recovery path reads the row after a
    # rollback and records a safe retryable failure; this mock has no row yet.
    with pytest.raises(Exception) as caught:
        run(reviews.create_review(project.id, version.id, db, user, MockAIService(), FailedOrchestrator()))
    assert getattr(caught.value, "status_code", None) == 503
    assert db.commits == 1
