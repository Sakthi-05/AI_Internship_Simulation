"""Requirement-change workplace scenario: activity trigger, persistence, and evidence."""
import json
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
    _communication_observation,
    assess_requirement_change,
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
    assert thin.json()["assessment"] is None
    assert thin.json()["evidence"]["response"] == "ok"

    # The thin reply is the one recorded reply. A useful reply cannot replace it,
    # so this student is a separate case below. Evidence rows still point at the event.
    evidence = db.execute(select(SoftSkillEvidence).where(SoftSkillEvidence.event_id == uuid.UUID(event["id"]))).scalars().all()
    db.expire_all()
    evidence = db.execute(select(SoftSkillEvidence).where(SoftSkillEvidence.event_id == uuid.UUID(event["id"]))).scalars().all()
    assert evidence == []

    duplicate_reply = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{event['id']}/respond",
        headers=auth_headers,
        data={"response": "I will update validation and return an error for invalid input."},
    )
    assert duplicate_reply.status_code == 409


def test_substantive_reply_and_later_action_are_saved_as_raw_evidence(client, auth_headers, db):
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
    manager_response = reply.json().get("manager_response")
    assert manager_response and manager_response["sender_type"] == "manager"
    assert manager_response["sender_id"]
    assert manager_response["created_at"]
    assert manager_response["id"]
    linked_messages = client.get(f"/api/companies/{ids['company_id']}/chat/manager", headers=auth_headers)
    assert linked_messages.status_code == 200
    linked_messages = linked_messages.json()
    assert [message["created_at"] for message in linked_messages] == sorted(
        message["created_at"] for message in linked_messages
    )
    assert any(message["id"] == manager_response["id"] for message in linked_messages)
    stored_event = db.get(WorkflowEvent, uuid.UUID(event["id"]))
    assert stored_event.metadata_json["manager_response_message_id"] == manager_response["id"]
    assert stored_event.metadata_json["manager_response_criteria_version_number"] == event["criteria_version_number"]
    # A fresh history request (the same path used after refresh/reconnect) returns
    # both sides of the exchange exactly once.
    reloaded = client.get(f"/api/companies/{ids['company_id']}/chat/manager", headers=auth_headers).json()
    assert sum(message["id"] == manager_response["id"] for message in reloaded) == 1
    assert reply.json()["status"] == "acknowledged"
    assert reply.json()["assessment"] is None
    assert reply.json()["evidence"]["response"].startswith("I will adapt the endpoint")

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
    assert stored["assessment"] is None
    assert stored["evidence"]["actions"] == []
    assert stored["subsequent_actions"] == []


def test_unlinked_reply_is_insufficient_and_recorded_irrelevant_reply_is_poor(client, db):
    from tests.conftest import make_token

    student_id = f"evidence_{uuid.uuid4().hex[:8]}"
    headers = {"Authorization": f"Bearer {make_token(student_id)}"}
    ids = _world(db, student_id)
    event_data = _raise_change(client, headers, ids["simulation_id"], ids["task_id"])
    event = db.get(WorkflowEvent, uuid.UUID(event_data["id"]))
    simulation = db.get(Simulation, ids["simulation_id"])
    task = db.get(Task, ids["task_id"])
    event.response = "I will update the handler and verify the changed requirement before submission."
    event.responded_at = datetime.now(timezone.utc)
    event.metadata_json = {**event.metadata_json, "student_message_id": "not-a-message-id"}
    db.add(event)
    db.commit()
    missing = assess_requirement_change(db, simulation, task, event, student_id)
    assert missing["communication"]["status"] == "insufficient_evidence"
    assert missing["communication"]["score"] is None


def test_recorded_unrelated_reply_is_saved_without_module_two_scoring(client, db):
    from tests.conftest import make_token

    student_id = f"poor_comm_{uuid.uuid4().hex[:8]}"
    headers = {"Authorization": f"Bearer {make_token(student_id)}"}
    ids = _world(db, student_id)
    event = _raise_change(client, headers, ids["simulation_id"], ids["task_id"])
    assert client.post(
        f"/api/simulations/{ids['simulation_id']}/tasks/{ids['task_id']}/events/{event['id']}/acknowledge",
        headers=headers,
    ).status_code == 200
    reply = client.post(
        f"/api/simulations/{ids['simulation_id']}/tasks/{ids['task_id']}/events/{event['id']}/respond",
        headers=headers,
        data={"response": "The weather is nice and I enjoyed the coffee with everyone this morning."},
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["assessment"] is None
    assert reply.json()["evidence"]["response"].startswith("The weather")


def test_communication_rubric_distinguishes_relevance_vagueness_and_detail():
    from app.services.workplace_scenarios import SCORED, INSUFFICIENT

    criterion = "Validate invalid input and return a clear error response."
    relevant = _communication_observation(
        "I will check malformed request data, return a clear error message, and add a test for the invalid payload.",
        criterion,
        recorded=True,
    )
    assert relevant["status"] == SCORED
    assert relevant["score"] == 82
    assert all(relevant["rubric"][key] for key in ("relevance", "clarity", "completeness", "responsiveness"))

    vague = _communication_observation(
        "I will handle malformed request data somehow.", criterion, recorded=True
    )
    assert vague["status"] == SCORED
    assert vague["score"] == 45
    assert vague["rubric"]["relevance"] is True
    assert vague["rubric"]["completeness"] is False

    keyword_only = _communication_observation(
        "Validate invalid input and return clear error response.", criterion, recorded=True
    )
    assert keyword_only["status"] == SCORED
    assert keyword_only["score"] == 45
    assert keyword_only["rubric"]["clarity"] is False

    unrelated = _communication_observation(
        "The weather is nice and I enjoyed coffee with everyone this morning.", criterion, recorded=True
    )
    assert unrelated["status"] == SCORED
    assert unrelated["score"] == 35
    assert unrelated["rubric"]["relevance"] is False

    missing = _communication_observation("I will validate the input and return errors.", criterion, recorded=False)
    assert missing["status"] == INSUFFICIENT
    assert missing["score"] is None


def test_duplicate_chat_messages_do_not_duplicate_raw_event_evidence(client, db):
    from tests.conftest import make_token

    student_id = f"duplicate_comm_{uuid.uuid4().hex[:8]}"
    headers = {"Authorization": f"Bearer {make_token(student_id)}"}
    ids = _world(db, student_id)
    event = _raise_change(client, headers, ids["simulation_id"], ids["task_id"])
    assert client.post(
        f"/api/simulations/{ids['simulation_id']}/tasks/{ids['task_id']}/events/{event['id']}/acknowledge",
        headers=headers,
    ).status_code == 200
    reply_text = (
        "I will validate malformed request data, return a clear error message, "
        "and add a test for the invalid payload."
    )
    reply = client.post(
        f"/api/simulations/{ids['simulation_id']}/tasks/{ids['task_id']}/events/{event['id']}/respond",
        headers=headers,
        data={"response": reply_text},
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["assessment"] is None
    db.expire_all()
    stored_event = db.get(WorkflowEvent, uuid.UUID(event["id"]))
    original_message_id = stored_event.metadata_json["student_message_id"]
    db.add(Message(
        company_id=ids["company_id"],
        sender_type=MessageSenderType.STUDENT,
        sender_id=student_id,
        content=reply_text,
    ))
    db.commit()

    evidence_rows = db.execute(
        select(SoftSkillEvidence).where(
            SoftSkillEvidence.event_id == uuid.UUID(event["id"]),
            SoftSkillEvidence.competency == "communication",
        )
    ).scalars().all()
    assert evidence_rows == []
    assert stored_event.metadata_json["student_message_id"] == original_message_id

    duplicate_reply = client.post(
        f"/api/simulations/{ids['simulation_id']}/tasks/{ids['task_id']}/events/{event['id']}/respond",
        headers=headers,
        data={"response": reply_text},
    )
    assert duplicate_reply.status_code == 409


def test_adaptation_and_submission_keep_requirement_version_evidence(client, db):
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
    ordinary_submission = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{event['id']}/actions",
        headers=headers,
        json={"action": "task_submitted", "detail": f"Submitted work containing {criterion}"},
    )
    assert ordinary_submission.status_code == 400
    action = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{event['id']}/actions",
        headers=headers,
        json={"action": "adapted_work", "detail": f"I added validation for invalid input. {criterion}"},
    )
    assert action.status_code == 200, action.text
    assert action.json()["status"] == "acknowledged"
    assert action.json()["assessment"] is None
    assert action.json()["subsequent_actions"][0]["criteria_version_id"] == event["criteria_version_id"]

    submitted = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/submit",
        headers=headers,
        data={"metadata": json.dumps({"code": f"# {criterion}\ndef validate_order(value):\n    return value"})},
    )
    assert submitted.status_code == 201, submitted.text
    assert submitted.json()["version_id"]
    assert submitted.json()["requirement_change_assessments"] == []

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
    assert stored["phase"] == "verify"
    assert stored["status"] == "acknowledged"
    assert stored["assessment"] is None
    assert stored["subsequent_actions"][0]["action"] == "adapted_work"
    assert criterion in stored["subsequent_actions"][0]["detail"]
    assert stored["subsequent_actions"][0]["event_id"] == event["id"]
    assert stored["subsequent_actions"][0]["criteria_version_id"] == event["criteria_version_id"]
    assert stored["subsequent_actions"][0]["criteria_version_number"] == event["criteria_version_number"]
    assert stored["response"]
    assert stored["manager_message"]
    assert criterion in stored["acceptance_criteria"]

    rows = db.execute(select(SoftSkillEvidence).where(SoftSkillEvidence.event_id == uuid.UUID(event["id"]))).scalars().all()
    db.expire_all()
    rows = db.execute(select(SoftSkillEvidence).where(SoftSkillEvidence.event_id == uuid.UUID(event["id"]))).scalars().all()
    assert rows == []
    assert stored["subsequent_actions"][1]["version_id"] == submitted.json()["version_id"]

    chat = client.get(f"/api/companies/{ids['company_id']}/chat/manager", headers=headers)
    assert chat.status_code == 200, chat.text
    assert any(item["sender_type"] == MessageSenderType.MANAGER.value and criterion in item["content"] for item in chat.json())
    assert any(item["sender_type"] == MessageSenderType.STUDENT.value for item in chat.json())
    assert stored["status"] == "acknowledged"


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
        refreshed_history = client.get(
            f"/api/companies/{ids['company_id']}/chat/manager", headers=headers
        )
        assert refreshed_history.status_code == 200
        conversation = refreshed_history.json()
        assert len(conversation) == 4
        assert [message["sender_type"] for message in conversation] == [
            "student", "manager", "student", "manager"
        ]
        assert [message["created_at"] for message in conversation] == sorted(
            message["created_at"] for message in conversation
        )
        assert len({message["id"] for message in conversation}) == 4
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
