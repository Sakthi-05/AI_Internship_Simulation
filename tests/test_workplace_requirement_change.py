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
    WorkflowEventType,
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


def _changes(client, headers, simulation_id, task_id):
    response = client.get(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return [item for item in response.json()["events"] if item["event_type"] == "requirement_change"]


def _raise_change(client, headers, simulation_id, task_id):
    started = client.post(f"/api/simulations/{simulation_id}/tasks/{task_id}/start", headers=headers)
    assert started.status_code == 200, started.text
    assert _changes(client, headers, simulation_id, task_id) == []
    noted = _send_approach_note(client, headers, simulation_id, task_id)
    assert noted.status_code == 200, noted.text
    changes = _changes(client, headers, simulation_id, task_id)
    assert len(changes) == 1
    return changes[0]


def _send_approach_note(client, headers, simulation_id, task_id):
    events = client.get(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events",
        headers=headers,
    ).json()["events"]
    note = next(item for item in events if item["event_type"] == "stakeholder_message")
    return client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{note['id']}/respond",
        headers=headers,
        data={"response": "I have started the orders endpoint and will return the order list from the handler."},
    )


def test_requirement_change_flow_persists_and_does_not_duplicate(client, auth_headers, db):
    student_id = "student_001"
    ids = _world(db, student_id)
    simulation_id = ids["simulation_id"]
    task_id = ids["task_id"]

    started = client.post(f"/api/simulations/{simulation_id}/tasks/{task_id}/start", headers=auth_headers)
    assert started.status_code == 200, started.text
    assert _changes(client, auth_headers, simulation_id, task_id) == []

    noted = _send_approach_note(client, auth_headers, simulation_id, task_id)
    assert noted.status_code == 200, noted.text
    changes = _changes(client, auth_headers, simulation_id, task_id)
    assert len(changes) == 1
    event = changes[0]
    assert event["status"] == "pending"
    assert event["phase"] == "acknowledge"
    assert event["updated_requirement"]
    assert event["updated_requirement"] in event["acceptance_criteria"]
    assert "Returns the order list" in event["acceptance_criteria"]

    again = client.post(f"/api/simulations/{simulation_id}/tasks/{task_id}/start", headers=auth_headers)
    assert again.status_code == 400
    repeated = _send_approach_note(client, auth_headers, simulation_id, task_id)
    assert repeated.status_code == 200, repeated.text
    assert len(_changes(client, auth_headers, simulation_id, task_id)) == 1

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
    assert thin.json()["status"] == "acknowledged"
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
    event = _raise_change(client, auth_headers, simulation_id, task_id)
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
    assert reply.json()["status"] == "acknowledged"
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
    event = _raise_change(client, headers, simulation_id, task_id)
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
    assert action.json()["status"] == "resolved"
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
    assert stored["status"] == "resolved"
    assert stored["subsequent_actions"][0]["action"] == "adapted_work"
    assert criterion in stored["subsequent_actions"][0]["detail"]
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
    assert stored["status"] == "resolved"


def test_status_transition_cannot_issue_a_requirement_change(db):
    ids = _world(db, f"status_{uuid.uuid4().hex[:8]}")
    simulation = db.get(Simulation, ids["simulation_id"])
    task = db.get(Task, ids["task_id"])
    task.status = TaskStatus.IN_PROGRESS
    db.add(task)
    db.commit()
    with pytest.raises(WorkplaceScenarioError):
        issue_requirement_change(db, simulation, task, trigger_activity="task_in_progress")
    assert db.execute(
        select(func.count()).select_from(WorkflowEvent).where(
            WorkflowEvent.task_id == task.id,
            WorkflowEvent.event_type == WorkflowEventType.REQUIREMENT_CHANGE,
        )
    ).scalar_one() == 0


def test_manager_message_issues_one_requirement_change(client, db):
    from app.main import app
    from app.models.project_state import ProjectState
    from app.services.ai.ai_service import MockAIService, get_ai_service
    from tests.conftest import make_token

    student_id = f"chat_{uuid.uuid4().hex[:8]}"
    headers = {"Authorization": f"Bearer {make_token(student_id)}"}
    ids = _world(db, student_id)
    project_id = db.get(Simulation, ids["simulation_id"]).project_id
    db.add(ProjectState(project_id=project_id))
    db.commit()
    app.dependency_overrides[get_ai_service] = lambda: MockAIService()
    try:
        assert client.post(
            f"/api/simulations/{ids['simulation_id']}/tasks/{ids['task_id']}/start",
            headers=headers,
        ).status_code == 200
        assert _changes(client, headers, ids["simulation_id"], ids["task_id"]) == []
        first = client.post(
            f"/api/companies/{ids['company_id']}/chat/manager",
            headers=headers,
            json={"content": "I am implementing the orders endpoint and returning the order list."},
        )
        assert first.status_code == 201, first.text
        changes = _changes(client, headers, ids["simulation_id"], ids["task_id"])
        assert len(changes) == 1
        assert changes[0]["status"] == "pending"
        second = client.post(
            f"/api/companies/{ids['company_id']}/chat/manager",
            headers=headers,
            json={"content": "I am still implementing the orders endpoint."},
        )
        assert second.status_code == 201, second.text
        assert len(_changes(client, headers, ids["simulation_id"], ids["task_id"])) == 1
    finally:
        app.dependency_overrides.pop(get_ai_service, None)


def test_concurrent_work_creates_one_requirement_change(db):
    import threading

    ids = _world(db, f"race_{uuid.uuid4().hex[:8]}")
    setup = SessionLocal()
    try:
        task = setup.get(Task, ids["task_id"])
        task.status = TaskStatus.IN_PROGRESS
        setup.add(task)
        setup.commit()
    finally:
        setup.close()

    barrier = threading.Barrier(2)
    errors = []

    def _issue():
        local = SessionLocal()
        try:
            barrier.wait(timeout=5)
            simulation = local.get(Simulation, ids["simulation_id"])
            task = local.get(Task, ids["task_id"])
            issue_requirement_change(local, simulation, task, trigger_activity="approach_note")
            local.commit()
        except Exception as exc:
            errors.append(exc)
            local.rollback()
        finally:
            local.close()

    workers = [threading.Thread(target=_issue), threading.Thread(target=_issue)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert errors == []
    check = SessionLocal()
    try:
        count = check.execute(
            select(func.count()).select_from(WorkflowEvent).where(
                WorkflowEvent.simulation_id == ids["simulation_id"],
                WorkflowEvent.task_id == ids["task_id"],
                WorkflowEvent.event_type == WorkflowEventType.REQUIREMENT_CHANGE,
            )
        ).scalar_one()
        assert count == 1
    finally:
        check.close()
