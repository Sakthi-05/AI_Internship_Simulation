"""Opening a simulation must succeed when a task has more than one open workplace event."""
import uuid
from datetime import datetime, timedelta, timezone

from tests.conftest import make_token
from tests.test_workplace_requirement_change import _world
from app.models.enums import TaskStatus, WorkflowEventStatus, WorkflowEventType
from app.models.simulation import WorkflowEvent
from app.models.task import Task


def test_multiple_open_events_do_not_fail_simulation_reads(client, db):
    student_id = f"open_events_{uuid.uuid4().hex[:8]}"
    headers = {"Authorization": f"Bearer {make_token(student_id)}"}
    ids = _world(db, student_id)
    task = db.get(Task, ids["task_id"])
    task.status = TaskStatus.IN_PROGRESS
    now = datetime.now(timezone.utc)
    older = WorkflowEvent(
        simulation_id=ids["simulation_id"],
        task_id=task.id,
        event_type=WorkflowEventType.STAKEHOLDER_MESSAGE,
        title="Manager check-in",
        message="Send a short note before you submit.",
        status=WorkflowEventStatus.PENDING,
        action_required=True,
        occurred_at=now,
    )
    newer = WorkflowEvent(
        simulation_id=ids["simulation_id"],
        task_id=task.id,
        event_type=WorkflowEventType.REQUIREMENT_CHANGE,
        title="Requirement change",
        message="The acceptance criteria changed.",
        status=WorkflowEventStatus.ACKNOWLEDGED,
        action_required=True,
        occurred_at=now + timedelta(seconds=1),
    )
    db.add_all([task, older, newer])
    db.commit()

    detail = client.get(f"/api/simulations/{ids['simulation_id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["active_workplace_event"]["id"] == str(newer.id)

    current = client.get(f"/api/simulations/{ids['simulation_id']}/tasks/current", headers=headers)
    assert current.status_code == 200, current.text
    assert current.json()["active_workplace_event"]["id"] == str(newer.id)
