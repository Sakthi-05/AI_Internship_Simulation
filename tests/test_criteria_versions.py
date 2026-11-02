"""Acceptance-criteria versions across successive requirement changes."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models.criteria_version import AcceptanceCriteriaVersion
from app.models.enums import TaskStatus, WorkflowEventStatus, WorkflowEventType
from app.models.project import Project
from app.models.simulation import Simulation, WorkflowEvent
from app.models.sprint import Sprint
from app.models.submission import SubmissionVersion
from app.models.task import Task
from app.services.context.context_builder import build_task_context
from app.services.criteria_version_service import (
    ITEM_ACTIVE,
    ITEM_MODIFIED,
    VERSION_ACTIVE,
    VERSION_SUPERSEDED,
    backfill_missing_versions,
    criteria_identity,
    criteria_view,
    list_versions,
    required_texts,
)
from app.services.submission.submission_service import SubmissionService
from app.services.workplace_scenarios import issue_requirement_change
from tests.conftest import make_token
from tests.test_workplace_requirement_change import _raise_change, _world


def _versions(db, task_id):
    db.expire_all()
    task = db.get(Task, task_id)
    return list_versions(db, task)


def test_unversioned_task_keeps_its_criteria_list(db):
    ids = _world(db, f"plain_{uuid.uuid4().hex[:8]}")
    task = db.get(Task, ids["task_id"])
    assert required_texts(db, task) == ["Returns the order list"]
    identity = criteria_identity(db, task)
    assert identity["version_number"] == 1
    assert identity["id"] is None
    assert identity["implicit"] is True
    assert criteria_view(db, task)["criteria_version"] is None
    assert list(task.acceptance_criteria) == ["Returns the order list"]


def test_backfill_preserves_the_task_array_and_links_the_event(db):
    ids = _world(db, f"backfill_{uuid.uuid4().hex[:8]}")
    task = db.get(Task, ids["task_id"])
    added = "Validate invalid input on the stored task."
    task.acceptance_criteria = ["Returns the order list", added]
    event = WorkflowEvent(
        simulation_id=ids["simulation_id"],
        task_id=task.id,
        scenario_id=ids["scenario_id"],
        event_type=WorkflowEventType.REQUIREMENT_CHANGE,
        title="Requirement change",
        message="Criteria changed",
        original_requirement="Returns the order list",
        updated_requirement=added,
        occurred_at=datetime.now(timezone.utc),
        status=WorkflowEventStatus.RESOLVED,
        metadata_json={
            "original_criteria": ["Returns the order list"],
            "added_criterion": added,
            "actions": [{"action": "adapted_work", "detail": "kept"}],
        },
    )
    db.add(event)
    db.commit()
    stored_before = list(db.get(Task, task.id).acceptance_criteria)

    written = backfill_missing_versions(db)
    db.commit()

    assert written >= 1
    task = db.get(Task, task.id)
    assert list(task.acceptance_criteria) == stored_before
    versions = _versions(db, task.id)
    assert [item.version_number for item in versions] == [1, 2]
    assert versions[0].status == VERSION_SUPERSEDED
    assert versions[0].items == [{"text": "Returns the order list", "state": ITEM_ACTIVE}]
    assert versions[0].workflow_event_id is None
    assert versions[1].status == VERSION_ACTIVE
    assert versions[1].workflow_event_id == event.id
    assert versions[1].items[-1] == {"text": added, "state": ITEM_MODIFIED}
    db.refresh(event)
    assert event.metadata_json["criteria_version_id"] == str(versions[1].id)
    assert event.metadata_json["actions"][0]["action"] == "adapted_work"
    assert event.response is None


def test_successive_requirement_changes_version_the_criteria(client, db):
    student_id = f"versions_{uuid.uuid4().hex[:8]}"
    headers = {"Authorization": f"Bearer {make_token(student_id)}"}
    ids = _world(db, student_id)
    simulation_id = ids["simulation_id"]
    task_id = ids["task_id"]
    first = _raise_change(client, headers, simulation_id, task_id)
    original = "Returns the order list"

    versions = _versions(db, task_id)
    assert [item.version_number for item in versions] == [1, 2]
    assert versions[0].status == VERSION_SUPERSEDED
    assert versions[0].items[0]["text"] == original
    assert versions[1].status == VERSION_ACTIVE
    assert versions[1].workflow_event_id == uuid.UUID(first["id"])
    assert first["criteria_version_id"] == str(versions[1].id)
    assert first["criteria_version_number"] == 2
    assert first["updated_requirement"] in first["acceptance_criteria"]
    assert original in first["acceptance_criteria"]
    modified = [item for item in versions[1].items if item["state"] == ITEM_MODIFIED]
    assert len(modified) == 1
    assert modified[0]["text"] == first["updated_requirement"]

    simulation = db.get(Simulation, simulation_id)
    task = db.get(Task, task_id)
    assert issue_requirement_change(db, simulation, task, trigger_activity="manager_message") is None
    db.commit()
    assert [item.version_number for item in _versions(db, task_id)] == [1, 2]

    assert client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{first['id']}/acknowledge",
        headers=headers,
    ).status_code == 200
    criterion = first["updated_requirement"]
    reply = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{first['id']}/respond",
        headers=headers,
        data={"response": f"I will update the endpoint so invalid input returns an error. {criterion}"},
    )
    assert reply.status_code == 200, reply.text
    action = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{first['id']}/actions",
        headers=headers,
        json={"action": "adapted_work", "detail": f"I added validation for invalid input. {criterion}"},
    )
    assert action.status_code == 200, action.text
    assert action.json()["status"] == "acknowledged"

    first_task = db.get(Task, task_id)
    first_project = db.get(Project, db.get(Sprint, first_task.sprint_id).project_id)
    first_submission = SubmissionService().store_submission(
        db,
        first_project,
        first_task,
        metadata={"code": f"# {criterion}\ndef validate_order(value):\n    return value"},
    )
    from app.services.workplace_scenarios import assess_requirement_changes_for_submission
    assess_requirement_changes_for_submission(db, db.get(Simulation, simulation_id), first_task, student_id)
    db.commit()
    first_event = db.get(WorkflowEvent, uuid.UUID(first["id"]))
    assert first_event.status.value == "resolved"
    assert db.get(SubmissionVersion, uuid.UUID(first_submission["version_id"])) is not None

    db.expire_all()
    simulation = db.get(Simulation, simulation_id)
    task = db.get(Task, task_id)
    task.status = TaskStatus.IN_PROGRESS
    db.add(task)
    db.commit()
    second = issue_requirement_change(db, simulation, task, trigger_activity="manager_message")
    db.commit()
    assert second is not None
    assert str(second.id) != first["id"]

    versions = _versions(db, task_id)
    assert [item.version_number for item in versions] == [1, 2, 3]
    assert [item.status for item in versions] == [VERSION_SUPERSEDED, VERSION_SUPERSEDED, VERSION_ACTIVE]
    assert versions[0].items[0]["text"] == original
    assert versions[1].workflow_event_id == uuid.UUID(first["id"])
    assert versions[2].workflow_event_id == second.id
    assert versions[2].items[-1]["state"] == ITEM_MODIFIED
    assert versions[2].items[-1]["text"] == second.updated_requirement
    assert criterion in [item["text"] for item in versions[2].items if item["state"] == ITEM_ACTIVE]
    active_texts = [item["text"] for item in versions[2].items if item["state"] != "superseded"]
    task = db.get(Task, task_id)
    assert list(task.acceptance_criteria) == active_texts
    assert original in list(task.acceptance_criteria)
    assert second.metadata_json["criteria_version_id"] == str(versions[2].id)
    assert second.metadata_json["criteria_version_number"] == 3

    view = criteria_view(db, task)
    assert view["acceptance_criteria"] == active_texts
    assert view["criteria_version"]["active_version_number"] == 3
    context = build_task_context(db, task)
    assert context["acceptance_criteria"] == active_texts
    assert context["acceptance_criteria_version"]["id"] == str(versions[2].id)
    assert context["acceptance_criteria_version"]["version_number"] == 3

    third = issue_requirement_change(db, db.get(Simulation, simulation_id), db.get(Task, task_id), trigger_activity="manager_message")
    assert third is None
    assert db.execute(
        select(func.count()).select_from(AcceptanceCriteriaVersion).where(AcceptanceCriteriaVersion.task_id == task_id)
    ).scalar_one() == 3
    db.commit()

    assert client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{second.id}/acknowledge",
        headers=headers,
    ).status_code == 200
    second_criterion = second.updated_requirement
    second_reply = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{second.id}/respond",
        headers=headers,
        data={"response": f"I will update validation to satisfy this new criterion: {second_criterion}"},
    )
    assert second_reply.status_code == 200, second_reply.text
    second_action = client.post(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events/{second.id}/actions",
        headers=headers,
        json={"action": "adapted_work", "detail": f"I revised the endpoint for {second_criterion}"},
    )
    assert second_action.status_code == 200, second_action.text
    assert second_action.json()["status"] == "acknowledged"
    assert second_action.json()["subsequent_actions"][0]["event_id"] == str(second.id)
    assert second_action.json()["subsequent_actions"][0]["criteria_version_id"] == str(versions[2].id)
    assert second_action.json()["subsequent_actions"][0]["criteria_version_number"] == 3
    db.expire_all()
    task = db.get(Task, task_id)
    sprint = db.get(Sprint, task.sprint_id)
    project = db.get(Project, sprint.project_id)
    stored = SubmissionService().store_submission(
        db,
        project,
        task,
        metadata={"code": f"# {second_criterion}\ndef validate_order(value):\n    return value"},
    )
    from app.services.workplace_scenarios import assess_requirement_changes_for_submission
    assess_requirement_changes_for_submission(db, db.get(Simulation, simulation_id), task, student_id)
    db.commit()
    version = db.get(SubmissionVersion, uuid.UUID(stored["version_id"]))
    assert version.version_metadata["acceptance_criteria_version"]["id"] == str(versions[2].id)
    assert version.version_metadata["acceptance_criteria_version"]["version_number"] == 3
    refreshed_events = client.get(
        f"/api/simulations/{simulation_id}/tasks/{task_id}/events", headers=headers
    ).json()["events"]
    restored_second = next(item for item in refreshed_events if item["id"] == str(second.id))
    restored_first = next(item for item in refreshed_events if item["id"] == first["id"])
    assert restored_second["status"] == "resolved"
    assert restored_second["assessment"]["adaptability"]["status"] == "scored"
    assert restored_second["subsequent_actions"][0]["criteria_version_number"] == 3
    assert restored_first["subsequent_actions"][0]["criteria_version_number"] == 2
