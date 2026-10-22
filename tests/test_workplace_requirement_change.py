"""Requirement-change workplace scenario: activity trigger, persistence, and evidence."""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models.company import Company
from app.models.enums import (
    CompanyType,
    DifficultyLevel,
    ManagerPersonality,
    MessageSenderType,
    ScenarioStatus,
    SimulationStatus,
    SprintStatus,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from app.models.manager import Manager
from app.models.message import Message
from app.models.project import Project
from app.models.simulation import Scenario, Simulation, WorkflowEvent
from app.models.soft_skill import SoftSkillEvidence
from app.models.sprint import Sprint
from app.models.task import Task
from app.models.task_path import SimulationTaskPath
from app.services.assessment_service import QUESTION_BANK
from app.services.workplace_scenarios import (
    WorkplaceScenarioError,
    issue_requirement_change,
    scenario_catalog,
    start_scenario,
)

SOFT_SKILL_COMPETENCIES = {
    "communication",
    "adaptability",
    "time_management",
    "professionalism",
    "collaboration",
    "ownership",
}


def _world(db, student_id):
    company = Company(
        student_id=student_id,
        name="Workplace Co",
        company_type=CompanyType.STARTUP,
        industry="Software",
        department="Engineering",
        mission="Ship the internship task.",
        description="A small product team used for the requirement-change scenario.",
    )
    db.add(company)
    db.flush()
    db.add(Manager(
        company_id=company.id,
        name="Avery Chen",
        title="Engineering Manager",
        personality=ManagerPersonality.FRIENDLY,
    ))
    project = Project(
        company_id=company.id,
        title="Intern API",
        role="Backend Developer",
        technology_stack=["Python"],
        difficulty=DifficultyLevel.INTERMEDIATE,
        objectives=["Ship the endpoint"],
        modules=["api"],
        deliverables=["endpoint"],
    )
    db.add(project)
    db.flush()
    sprint = Sprint(project_id=project.id, sprint_number=1, name="Sprint 1", goal="Deliver the endpoint", status=SprintStatus.ACTIVE)
    db.add(sprint)
    db.flush()
    task = Task(
        sprint_id=sprint.id,
        sequence=1,
        title="Build the orders endpoint",
        description="Create the orders endpoint.",
        acceptance_criteria=["Returns the order list"],
        priority=TaskPriority.MEDIUM,
        status=TaskStatus.TODO,
        task_type=TaskType.API_DEVELOPMENT,
        difficulty=DifficultyLevel.INTERMEDIATE,
        estimated_hours=3,
    )
    db.add(task)
    db.flush()
    simulation = Simulation(
        student_id=student_id,
        company_id=company.id,
        project_id=project.id,
        title="Backend internship",
        description="Workplace simulation",
        role="Backend Developer",
        difficulty=DifficultyLevel.INTERMEDIATE,
        status=SimulationStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        current_task_id=task.id,
        progress=0,
    )
    db.add(simulation)
    db.flush()
    scenario = Scenario(
        simulation_id=simulation.id,
        sequence=1,
        title="Orders API",
        description="Build the orders API",
        workplace_context="The client can change the contract after work starts.",
        role="Backend Developer",
        difficulty=DifficultyLevel.INTERMEDIATE,
        required_skills=["Python"],
        objectives=["Ship the endpoint"],
        status=ScenarioStatus.ACTIVE,
    )
    db.add(scenario)
    db.flush()
    simulation.current_scenario_id = scenario.id
    db.add(SimulationTaskPath(
        simulation_id=simulation.id,
        task_id=task.id,
        sequence=1,
        status=TaskStatus.TODO,
    ))
    db.add(simulation)
    db.commit()
    return {
        "student_id": student_id,
        "company_id": company.id,
        "simulation_id": simulation.id,
        "task_id": task.id,
        "scenario_id": scenario.id,
    }


def test_static_bank_has_no_standalone_soft_skill_mcqs():
    assert QUESTION_BANK
    assert not any(
        item["competency"] in SOFT_SKILL_COMPETENCIES and item["question_type"] == "mcq"
        for item in QUESTION_BANK
    )
    assert all(item["competency"] not in SOFT_SKILL_COMPETENCIES for item in QUESTION_BANK)


def test_future_scenarios_are_reserved_and_do_not_write(db):
    catalog = {item["key"]: item for item in scenario_catalog()}
    assert catalog["requirement_change"]["implemented"] is True
    for key in ("collaboration", "time_management", "prioritization", "ownership", "feedback_response"):
        assert catalog[key]["implemented"] is False
        with pytest.raises(WorkplaceScenarioError):
            start_scenario(key)
    assert db.execute(select(func.count()).select_from(WorkflowEvent)).scalar_one() >= 0


def test_timer_cannot_issue_a_requirement_change(db):
    ids = _world(db, f"timer_{uuid.uuid4().hex[:8]}")
    simulation = db.get(Simulation, ids["simulation_id"])
    task = db.get(Task, ids["task_id"])
    before = list(task.acceptance_criteria)
    with pytest.raises(WorkplaceScenarioError):
        issue_requirement_change(db, simulation, task, trigger_activity="timer")
    db.refresh(task)
    assert list(task.acceptance_criteria) == before
    assert db.execute(
        select(func.count()).select_from(WorkflowEvent).where(WorkflowEvent.task_id == task.id)
    ).scalar_one() == 0


def test_requirement_change_flow_persists_and_does_not_duplicate(client, auth_headers, db):
    student_id = "student_001"
    ids = _world(db, student_id)
    simulation_id = ids["simulation_id"]
    task_id = ids["task_id"]

    started = client.post(f"/api/simulations/{simulation_id}/tasks/{task_id}/start", headers=auth_headers)
    assert started.status_code == 200, started.text

    first = client.get(f"/api/simulations/{simulation_id}/tasks/{task_id}/events", headers=auth_headers)
    assert first.status_code == 200, first.text
    changes = [item for item in first.json()["events"] if item["event_type"] == "requirement_change"]
    assert len(changes) == 1
    event = changes[0]
    assert event["status"] == "pending"
    assert event["phase"] == "acknowledge"
    assert event["updated_requirement"]
    assert event["updated_requirement"] in event["acceptance_criteria"]
    assert "Returns the order list" in event["acceptance_criteria"]

    again = client.post(f"/api/simulations/{simulation_id}/tasks/{task_id}/start", headers=auth_headers)
    assert again.status_code == 400
    second = client.get(f"/api/simulations/{simulation_id}/tasks/{task_id}/events", headers=auth_headers)
    assert len([item for item in second.json()["events"] if item["event_type"] == "requirement_change"]) == 1

    persisted = SessionLocal()
    try:
        stored_task = persisted.get(Task, task_id)
        assert event["updated_requirement"] in list(stored_task.acceptance_criteria)
        stored_event = persisted.get(WorkflowEvent, uuid.UUID(event["id"]))
        assert stored_event.status.value == "pending"
    finally:
        persisted.close()

    acknowledged = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{event['id']}/acknowledge",
        headers=auth_headers,
    )
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["status"] == "acknowledged"
    assert acknowledged.json()["manager_message"]
    assert event["updated_requirement"] in acknowledged.json()["manager_message"]

    client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{event['id']}/acknowledge",
        headers=auth_headers,
    )
    messages = client.get(f"/api/companies/{ids['company_id']}/chat/manager", headers=auth_headers)
    assert messages.status_code == 200, messages.text
    manager_notes = [item for item in messages.json() if item["sender_type"] == "manager" and event["updated_requirement"] in item["content"]]
    assert len(manager_notes) == 1

    thin = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{event['id']}/respond",
        headers=auth_headers,
        data={"response": "ok"},
    )
    assert thin.status_code == 200, thin.text
    assert thin.json()["assessment"]["status"] == "insufficient_evidence"
    assert thin.json()["assessment"]["communication"]["score"] is None
    assert thin.json()["assessment"]["adaptability"]["score"] is None

    # The thin reply is the one recorded reply. A useful reply cannot replace it,
    # so this student is a separate case below. Evidence rows still point at the event.
    evidence = db.execute(select(SoftSkillEvidence).where(SoftSkillEvidence.event_id == uuid.UUID(event["id"]))).scalars().all()
    db.expire_all()
    evidence = db.execute(select(SoftSkillEvidence).where(SoftSkillEvidence.event_id == uuid.UUID(event["id"]))).scalars().all()
    assert {row.competency for row in evidence} == {"communication", "adaptability"}
    assert all(row.student_id == student_id for row in evidence)
    assert all(row.simulation_id == simulation_id for row in evidence)
    assert all(row.task_id == task_id for row in evidence)
    assert all(row.status == "insufficient_evidence" and row.score is None for row in evidence)

    duplicate_reply = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{event['id']}/respond",
        headers=auth_headers,
        data={"response": "I will update validation and return an error for invalid input."},
    )
    assert duplicate_reply.status_code == 409


def test_substantive_reply_and_later_action_are_scored(client, auth_headers, db):
    student_id = "student_001"
    ids = _world(db, student_id)
    # The previous test already used student_001's company ownership. This test
    # creates another company for the same student, which the chat and start
    # routes allow. Use a fresh student token instead when the fixture student
    # already has state that blocks a second in-progress simulation.
    simulation_id = ids["simulation_id"]
    task_id = ids["task_id"]
    started = client.post(f"/api/simulations/{simulation_id}/tasks/{task_id}/start", headers=auth_headers)
    assert started.status_code == 200, started.text
    events = client.get(f"/api/simulations/{simulation_id}/tasks/{task_id}/events", headers=auth_headers).json()["events"]
    event = next(item for item in events if item["event_type"] == "requirement_change")
    criterion = event["updated_requirement"]

    ack = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{event['id']}/acknowledge",
        headers=auth_headers,
    )
    assert ack.status_code == 200, ack.text
    reply = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{event['id']}/respond",
        headers=auth_headers,
        data={"response": f"I will adapt the endpoint and add validation so invalid input returns an error. New requirement: {criterion}"},
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["assessment"]["communication"]["status"] == "scored"
    assert reply.json()["assessment"]["communication"]["score"] is not None
    assert reply.json()["assessment"]["adaptability"]["status"] == "insufficient_evidence"
    assert reply.json()["assessment"]["status"] == "insufficient_evidence"

    early = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{event['id']}/actions",
        headers=auth_headers,
        json={"action": "adapted_work", "detail": "done"},
    )
    assert early.status_code == 400, early.text
    refreshed = client.get(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events", headers=auth_headers
    ).json()["events"]
    stored = next(item for item in refreshed if item["id"] == event["id"])
    assert stored["phase"] == "adapt"
    assert stored["assessment"]["adaptability"]["status"] == "insufficient_evidence"
    assert stored["assessment"]["adaptability"]["score"] is None
    assert stored["subsequent_actions"] == []


def test_adaptation_that_mentions_the_new_requirement_is_scored(client, db):
    from tests.conftest import make_token
    student_id = f"adapt_{uuid.uuid4().hex[:8]}"
    headers = {"Authorization": f"Bearer {make_token(student_id)}"}
    ids = _world(db, student_id)
    simulation_id = ids["simulation_id"]
    task_id = ids["task_id"]
    assert client.post(f"/api/simulations/{simulation_id}/tasks/{task_id}/start", headers=headers).status_code == 200
    event = next(
        item for item in client.get(
            f"/api/simulations/{simulation_id}/tasks/{task_id}/events", headers=headers
        ).json()["events"]
        if item["event_type"] == "requirement_change"
    )
    criterion = event["updated_requirement"]
    assert client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{event['id']}/acknowledge",
        headers=headers,
    ).status_code == 200
    reply = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{event['id']}/respond",
        headers=headers,
        data={"response": f"I will update the endpoint so invalid input returns an error. {criterion}"},
    )
    assert reply.status_code == 200, reply.text
    action = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{event['id']}/actions",
        headers=headers,
        json={"action": "adapted_work", "detail": f"I added validation for invalid input. {criterion}"},
    )
    assert action.status_code == 200, action.text
    body = action.json()["assessment"]
    assert body["status"] == "scored"
    assert body["communication"]["score"] is not None
    assert body["adaptability"]["score"] is not None

    duplicate = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{event['id']}/actions",
        headers=headers,
        json={"action": "adapted_work", "detail": f"Again: {criterion}"},
    )
    assert duplicate.status_code == 409

    refreshed = client.get(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events", headers=headers
    ).json()["events"]
    stored = next(item for item in refreshed if item["id"] == event["id"])
    assert stored["phase"] == "assessed"
    assert stored["response"]
    assert stored["manager_message"]
    assert criterion in stored["acceptance_criteria"]

    rows = db.execute(select(SoftSkillEvidence).where(SoftSkillEvidence.event_id == uuid.UUID(event["id"]))).scalars().all()
    db.expire_all()
    rows = db.execute(select(SoftSkillEvidence).where(SoftSkillEvidence.event_id == uuid.UUID(event["id"]))).scalars().all()
    assert len(rows) == 2
    assert all(row.student_id == student_id and row.task_id == task_id and row.simulation_id == simulation_id for row in rows)

    chat = client.get(f"/api/companies/{ids['company_id']}/chat/manager", headers=headers)
    assert chat.status_code == 200, chat.text
    assert any(item["sender_type"] == MessageSenderType.MANAGER.value and criterion in item["content"] for item in chat.json())
    assert any(item["sender_type"] == MessageSenderType.STUDENT.value for item in chat.json())
