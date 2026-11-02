"""Context projection tests use isolated ORM-shaped records and no database."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agents import AgentRequest
from app.agents.context_manager import AgentContextManager, ContextAccessDenied
from app.models.assessment import AssessmentAttempt, StudentSkill
from app.models.company import Company
from app.models.criteria_version import AcceptanceCriteriaVersion
from app.models.event import Event
from app.models.intelligence import InteractionEvaluation, StudentProfile
from app.models.message import Message
from app.models.project import Project
from app.models.simulation import Simulation, WorkflowEvent
from app.models.simulation_interaction import SimulationInteraction
from app.models.soft_skill import SoftSkillEvidence
from app.models.sprint import Sprint
from app.models.submission import AIReview, Submission, SubmissionVersion
from app.models.task import Task
from app.models.task_path import SimulationTaskPath


class Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self.first()


class FakeSession:
    def __init__(self, records):
        self.records = records
        self.queries = []
        self.counts = {}

    def execute(self, statement):
        model = statement.column_descriptions[0]["entity"]
        self.queries.append(model)
        self.counts[model] = self.counts.get(model, 0) + 1
        if model is Simulation and self.counts[model] > 1:
            return Result(self.records.get("completed_simulations", []))
        return Result(self.records.get(model, []))


def records(profile="scenario", owner="student-a"):
    sim_id, company_id, project_id, sprint_id, task_id = [uuid4() for _ in range(5)]
    now = datetime.now(timezone.utc)
    sim = SimpleNamespace(id=sim_id, student_id=owner, company_id=company_id,
                          project_id=project_id, current_task_id=task_id,
                          status="in_progress", title="Simulation", progress=30,
                          completed_at=None)
    company = SimpleNamespace(id=company_id, student_id=owner, name="Example Co", industry="Software")
    project = SimpleNamespace(id=project_id, company_id=company_id, title="Project",
                              role="Engineer", status="active")
    sprint = SimpleNamespace(id=sprint_id, project_id=project_id, sprint_number=1,
                             name="Sprint 1", status="active")
    task = SimpleNamespace(id=task_id, sprint_id=sprint_id, title="Build API",
                           description="Implement an API", status="in_progress",
                           difficulty="intermediate", acceptance_criteria=["Current criterion"])
    path = SimpleNamespace(id=uuid4(), simulation_id=sim_id, task_id=task_id,
                           sequence=1, status="in_progress", started_at=now, completed_at=None)
    criteria_old = SimpleNamespace(id=uuid4(), task_id=task_id, version_number=1,
                                   status="superseded", items=[{"text": "Old criterion", "state": "active"}],
                                   workflow_event_id=None, created_at=now)
    criteria_active = SimpleNamespace(id=uuid4(), task_id=task_id, version_number=2,
                                      status="active", items=[{"text": "Current criterion", "state": "active"}],
                                      workflow_event_id=None, created_at=now)
    event = SimpleNamespace(id=uuid4(), simulation_id=sim_id, task_id=task_id,
                            event_type="requirement_change", title="Changed requirement",
                            message="Review the new requirement", description="", status="pending",
                            action_required=True, occurred_at=now, response=None, responded_at=None,
                            created_at=now)
    msg = SimpleNamespace(id=uuid4(), company_id=company_id, sender_type="manager",
                          sender_id="manager-1", content="How is the work going?", created_at=now)
    sub = SimpleNamespace(id=uuid4(), task_id=task_id, student_id=owner,
                          status="submitted", created_at=now)
    criteria_id = criteria_old.id
    version1 = SimpleNamespace(id=uuid4(), submission_id=sub.id, version_number=1,
                               files=[{"name": "v1.py"}], status="submitted",
                               submitted_at=now, created_at=now,
                               version_metadata={"acceptance_criteria_version": {"id": str(criteria_id), "version_number": 1},
                                                 "acceptance_criteria_snapshot": ["Old criterion"]})
    version2 = SimpleNamespace(id=uuid4(), submission_id=sub.id, version_number=2,
                               files=[{"name": "v2.py"}], status="submitted",
                               submitted_at=now, created_at=now,
                               version_metadata={"acceptance_criteria_version": {"id": str(criteria_active.id), "version_number": 2},
                                                 "acceptance_criteria_snapshot": ["Current criterion"]})
    review = SimpleNamespace(id=uuid4(), version_id=version1.id, status="completed",
                             result="changes_required", severity="warning", score=65,
                             summary="Needs work", strengths=[], risks=["Missing edge case"],
                             actions=["Add validation"], completed_at=now, created_at=now)
    profile_row = SimpleNamespace(id=uuid4(), student_id=owner, baseline_competencies={},
                                  current_competencies={"technical": 71}, strengths=[], weaknesses=[])
    skill = SimpleNamespace(id=uuid4(), student_id=owner, skill_id=uuid4(), self_rating=3)
    assessment = SimpleNamespace(id=uuid4(), student_id=owner, status="completed", overall_score=70,
                                 competencies={"technical": 70}, started_at=now,
                                 completed_at=now, created_at=now)
    soft = SimpleNamespace(id=uuid4(), student_id=owner, simulation_id=sim_id,
                           task_id=task_id, event_id=event.id, competency="communication",
                           status="insufficient_evidence", score=None, summary="",
                           evidence={}, created_at=now)
    interaction = SimpleNamespace(id=uuid4(), simulation_id=sim_id, student_id=owner,
                                  task_id=task_id, occurred_at=now, interaction_type="decision",
                                  decision="ask_for_help", sequence=1)
    evaluation = SimpleNamespace(id=uuid4(), simulation_id=sim_id, student_id=owner,
                                 interaction_id=interaction.id, overall_score=72,
                                 competencies={"communication": 72}, feedback="Clear response",
                                 strengths=[], weaknesses=[])
    completed = SimpleNamespace(id=uuid4(), student_id=owner, status="completed", title="Earlier",
                                progress=100, completed_at=now, company_id=company_id,
                                project_id=project_id)
    rows = {
        Simulation: [sim], Company: [company], Project: [project], Sprint: [sprint],
        Task: [task], SimulationTaskPath: [path],
        AcceptanceCriteriaVersion: [criteria_active, criteria_old],
        WorkflowEvent: [event], Event: [], Message: [msg], Submission: [sub],
        SubmissionVersion: [version2, version1], AIReview: [review],
        StudentProfile: [profile_row], StudentSkill: [skill],
        AssessmentAttempt: [assessment], SoftSkillEvidence: [soft],
        SimulationInteraction: [interaction], InteractionEvaluation: [evaluation],
        "completed_simulations": [completed],
    }
    return {"ids": (sim_id, company_id, project_id, sprint_id, task_id),
            "records": rows, "expected": locals()}


def test_context_construction_and_evidence_reference_integrity():
    data = records()
    session = FakeSession(data["records"])
    sim_id, *_ = data["ids"]
    context = AgentContextManager().build(session, simulation_id=sim_id,
                                          student_id="student-a", profile="scenario")
    assert context.current_task["title"] == "Build API"
    assert context.current_sprint["number"] == 1
    known_ids = {row.id for model_rows in data["records"].values() for row in model_rows if hasattr(row, "id")}
    assert context.evidence
    assert all(reference.evidence_record_id in known_ids for reference in context.evidence)
    assert all(reference.student_id == "student-a" and reference.simulation_id in {None, sim_id}
               for reference in context.evidence)


def test_missing_simulation_or_task_is_handled_without_fabricated_context():
    data = records()
    sim_id, *_ = data["ids"]
    data["records"][Simulation] = []
    with pytest.raises(ContextAccessDenied):
        AgentContextManager().build(FakeSession(data["records"]), simulation_id=sim_id,
                                    student_id="student-a")

    data = records()
    sim_id, *_ = data["ids"]
    data["records"][SimulationTaskPath] = []
    context = AgentContextManager().build(FakeSession(data["records"]), simulation_id=sim_id,
                                          student_id="student-a", profile="scenario")
    assert context.current_task is None
    assert all(reference.evidence_type != "task" for reference in context.evidence)


def test_technical_review_uses_exact_historical_criteria_and_submission_versions():
    data = records()
    sim_id, *_ = data["ids"]
    context = AgentContextManager(submission_context_loader=lambda _db, version: {
        "submission_content": f"source for version {version.version_number}",
        "evidence_level": "full",
        "acceptance_criteria": ["Criteria used for this version"],
        "acceptance_criteria_version": {
            "id": str(data["expected"]["criteria_active"].id), "version_number": 2,
            "status": "active", "implicit": False,
        },
    }).build(FakeSession(data["records"]), simulation_id=sim_id,
             student_id="student-a", profile="technical_review")
    expected = data["expected"]
    versions = context.submission_history
    assert [v["version_number"] for v in versions] == [1, 2]
    assert versions[0]["criteria_snapshot"]["items"][0]["text"] == "Old criterion"
    assert versions[1]["criteria_snapshot"]["items"][0]["text"] == "Current criterion"
    assert context.active_criteria["id"] == expected["criteria_active"].id
    assert context.submission_content == "source for version 2"
    assert context.submission_evidence_level == "full"
    assert {ref.submission_version_id for ref in context.evidence if ref.evidence_type == "submission_version"} == {
        expected["version1"].id, expected["version2"].id}
    assert context.technical_reviews[0]["summary"] == "Needs work"


def test_role_profiles_filter_irrelevant_records_and_manager_sees_open_events():
    data = records()
    sim_id, *_ = data["ids"]
    db = FakeSession(data["records"])
    scenario = AgentContextManager().build(db, simulation_id=sim_id, student_id="student-a", profile="scenario")
    assert scenario.manager_communications[0]["content"] == "How is the work going?"
    assert scenario.assessment_history == []
    assert scenario.interaction_history
    assert Message in db.queries  # scenario role loads bounded communication evidence for collaboration triggers
    manager = AgentContextManager().build(FakeSession(data["records"]), simulation_id=sim_id,
                                          student_id="student-a", profile="manager")
    assert manager.manager_communications[0]["content"] == "How is the work going?"
    assert manager.workplace_events[0]["status"] == "pending"


def test_owner_and_company_mismatches_are_denied():
    data = records(owner="student-a")
    sim_id, *_ = data["ids"]
    with pytest.raises(ContextAccessDenied):
        AgentContextManager().build(FakeSession(data["records"]), simulation_id=sim_id,
                                    student_id="student-b")
    data = records(owner="student-a")
    sim_id, *_ = data["ids"]
    data["records"][Company][0].student_id = "student-b"
    with pytest.raises(ContextAccessDenied):
        AgentContextManager().build(FakeSession(data["records"]), simulation_id=sim_id,
                                    student_id="student-a")


def test_missing_soft_skill_evidence_is_not_encoded_as_a_low_score():
    data = records()
    sim_id, *_ = data["ids"]
    context = AgentContextManager().build(FakeSession(data["records"]), simulation_id=sim_id,
                                          student_id="student-a", profile="competency")
    observation = context.competencies["observations"][0]
    assert observation["status"] == "insufficient_evidence"
    assert observation["score"] is None
    assert context.competencies["current"] == {"technical": 71}
    assessment_reference = next(ref for ref in context.evidence if ref.evidence_type == "assessment_attempt")
    assert assessment_reference.simulation_id is None  # baseline record is student-scoped, not simulation-scoped


def test_role_profiles_include_assessment_and_longitudinal_career_history():
    data = records()
    sim_id, *_ = data["ids"]
    context = AgentContextManager().build(FakeSession(data["records"]), simulation_id=sim_id,
                                          student_id="student-a", profile="career_insights")
    assert context.assessment_history  # completed assessment history
    assert context.interaction_history  # persisted interaction and evaluation history
    assert context.simulation_outcomes[0]["title"] == "Earlier"
    assert context.competencies["current"] == {"technical": 71}


def test_agent_context_remains_compatible_with_orchestration_contracts():
    data = records()
    sim_id, *_ = data["ids"]
    context = AgentContextManager().build(FakeSession(data["records"]), simulation_id=sim_id,
                                          student_id="student-a", profile="scenario")
    request = AgentRequest(agent_id="scenario-agent", context=context)
    assert request.context.profile == "scenario"
    assert request.context.simulation_id == sim_id
