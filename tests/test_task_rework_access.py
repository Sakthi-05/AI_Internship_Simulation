"""Current task must stay accessible through submit/review/rework.

Regression probe: submit → AI review (changes) → the same task must still
be the unlocked current task (openable, restartable, resubmittable), while
genuinely future tasks stay locked.
"""
import os
import uuid

from sqlalchemy import text

from app.main import app
from app.services.ai.ai_service import AIService, get_ai_service
from app.services.simulation import task_access_service as tas
from app.core.database import SessionLocal
from app.models.simulation import Simulation
from app.models.task import Task


class _StrictReviewer(AIService):
    async def generate_text(self, prompt, system_prompt=None):
        return "review"

    async def generate_json(self, prompt, system_prompt=None):
        return {
            "result": "changes_required",
            "score": 50,
            "severity": "warning",
            "summary": "Needs work, see required changes below for resubmission.",
            "strengths": [],
            "issues": ["thin"],
            "required_changes": ["Add more implementation detail and tests."],
            "acceptance_criteria": [],
            "code_quality": {"score": 50, "feedback": "ok"},
            "testing": {"score": 50, "feedback": "Add tests before resubmitting."},
            "security": {"score": 50, "feedback": "ok"},
            "performance": {"score": 50, "feedback": "ok"},
        }


class _ApprovingReviewer(AIService):
    async def generate_text(self, prompt, system_prompt=None):
        return "review"

    async def generate_json(self, prompt, system_prompt=None):
        return {
            "result": "approved",
            "score": 95,
            "severity": "info",
            "summary": "The implementation meets the task requirements and is ready to approve.",
            "strengths": ["Clear implementation"],
            "issues": [],
            "required_changes": [],
            "acceptance_criteria": [],
            "code_quality": {"score": 95, "feedback": "Clear and maintainable."},
            "testing": {"score": 95, "feedback": "Verification evidence is present."},
            "security": {"score": 95, "feedback": "No security issues found."},
            "performance": {"score": 95, "feedback": "Performance is appropriate."},
        }


class _UnavailableReviewer(AIService):
    async def generate_text(self, prompt, system_prompt=None):
        raise RuntimeError("Gemini unavailable")

    async def generate_json(self, prompt, system_prompt=None):
        raise RuntimeError("Gemini unavailable")


def _generate(client, auth_headers, student_id):
    resp = client.post(
        "/api/generate",
        headers=auth_headers,
        json={
            "student_id": student_id,
            "role": "Backend Developer",
            "technology_stack": ["Python"],
            "difficulty": "intermediate",
            "company_type": "startup",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _headers_for(student_id):
    from tests.conftest import make_token
    return {"Authorization": f"Bearer {make_token(student_id)}"}


def _access_snapshot(simulation_id, task_id):
    from uuid import UUID as _UUID

    task_uuid = task_id if isinstance(task_id, _UUID) else _UUID(str(task_id))
    db = SessionLocal()
    try:
        sim = db.get(Simulation, simulation_id)
        task = db.get(Task, task_uuid)
        cand = tas.get_next_available_path_entry(db, sim.id)
        return {
            "task_status": task.status.value,
            "current_task_id": str(sim.current_task_id) if sim.current_task_id else None,
            "candidate_id": str(cand.task_id) if cand else None,
            "unlocked": tas.is_current_unlocked_task(db, sim, task_uuid),
        }
    finally:
        db.close()


def test_current_task_unlocked_through_review_and_rework(client):
    student_id = f"rework_{uuid.uuid4().hex[:8]}"
    headers = _headers_for(student_id)
    data = _generate(client, headers, student_id)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=headers).status_code == 200
    task_id = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers).json()["task_id"]

    assert client.post(f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=headers).status_code == 200
    submitted = client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/submit",
        headers=headers,
        data={"metadata": '{"response": "substantial implementation writeup with enough length to be analyzed properly by the reviewer logic in this regression test"}'},
    )
    assert submitted.status_code == 201, submitted.text
    snap = _access_snapshot(sim_id, task_id)
    print("\nAFTER SUBMIT:", snap)
    assert snap["unlocked"], snap

    app.dependency_overrides[get_ai_service] = lambda: _StrictReviewer()
    try:
        reviewed = client.post(
            f"/api/projects/{data['project_id']}/reviews/versions/{submitted.json()['version_id']}",
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    assert reviewed.status_code == 201, reviewed.text
    assert reviewed.json()["result"] == "changes_required"

    snap = _access_snapshot(sim_id, task_id)
    print("AFTER REVIEW:", snap)
    assert snap["task_status"] == "changes_requested", snap
    assert snap["unlocked"], snap
    task_detail = client.get(f"/api/tasks/{task_id}", headers=headers)
    assert task_detail.status_code == 200, task_detail.text

    # Rework restart must succeed (not 403 locked).
    restart = client.post(f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=headers)
    print("REWORK START:", restart.status_code, restart.text[:200])
    assert restart.status_code == 200, restart.text


def test_changes_requested_can_be_resubmitted_directly(client):
    """Resubmit from CHANGES_REQUESTED restarts rework inline (no new task)."""
    student_id = f"resubmit_{uuid.uuid4().hex[:8]}"
    headers = _headers_for(student_id)
    data = _generate(client, headers, student_id)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=headers).status_code == 200
    task_id = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers).json()["task_id"]
    assert client.post(f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=headers).status_code == 200
    first = client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/submit",
        headers=headers,
        data={"metadata": '{"response": "substantial implementation writeup with enough length to be analyzed properly by the reviewer logic in this regression test"}'},
    )
    assert first.status_code == 201, first.text

    app.dependency_overrides[get_ai_service] = lambda: _StrictReviewer()
    try:
        reviewed = client.post(
            f"/api/projects/{data['project_id']}/reviews/versions/{first.json()['version_id']}",
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    assert reviewed.json()["result"] == "changes_required"

    # Direct resubmission from CHANGES_REQUESTED: same task, new version.
    second = client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/submit",
        headers=headers,
        data={"metadata": '{"response": "revised implementation writeup addressing every review point with additional detail and verification notes for the second attempt"}'},
    )
    assert second.status_code == 201, second.text
    assert second.json()["version_id"] != first.json()["version_id"]
    during_second_review = client.get(
        f"/api/simulations/{sim_id}/tasks/current", headers=headers
    )
    assert during_second_review.status_code == 200, during_second_review.text
    previous_feedback = during_second_review.json()["review_feedback"]
    assert previous_feedback["version_number"] == 1, previous_feedback
    assert previous_feedback["is_previous_review"] is True, previous_feedback
    app.dependency_overrides[get_ai_service] = lambda: _StrictReviewer()
    try:
        second_review = client.post(
            f"/api/projects/{data['project_id']}/reviews/versions/{second.json()['version_id']}",
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    assert second_review.status_code == 201, second_review.text
    assert second_review.json()["result"] == "changes_required"
    snap = _access_snapshot(sim_id, task_id)
    assert snap["task_status"] == "changes_requested", snap
    assert snap["unlocked"], snap


def test_approved_review_waits_for_completion_endpoint(client):
    """AI approval moves to MANAGER_APPROVAL without completing; only the
    completion endpoint marks COMPLETED and unlocks the next task."""
    student_id = f"approved_{uuid.uuid4().hex[:8]}"
    headers = _headers_for(student_id)
    data = _generate(client, headers, student_id)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=headers).status_code == 200
    task_id = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers).json()["task_id"]
    assert client.post(f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=headers).status_code == 200
    submitted = client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/submit",
        headers=headers,
        data={"metadata": '{"response": "I implemented the requested behavior in the task module, added input validation and clear error handling, and verified the normal and edge cases with automated tests. I ran the relevant test suite and confirmed it passes. The code follows the existing project conventions and preserves the current task workflow."}'},
    )
    assert submitted.status_code == 201, submitted.text

    app.dependency_overrides[get_ai_service] = lambda: _ApprovingReviewer()
    try:
        reviewed = client.post(
            f"/api/projects/{data['project_id']}/reviews/versions/{submitted.json()['version_id']}",
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    assert reviewed.status_code == 201, reviewed.text
    assert reviewed.json()["result"] == "approved"

    snap = _access_snapshot(sim_id, task_id)
    assert snap["task_status"] == "manager_approval", snap
    assert snap["current_task_id"] == task_id, snap
    assert snap["unlocked"], snap
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers)
    assert current.status_code == 200, current.text
    assert current.json()["task_id"] == task_id

    completed = client.post(f"/api/simulations/{sim_id}/tasks/{task_id}/complete", headers=headers)
    assert completed.status_code == 200, completed.text
    snap = _access_snapshot(sim_id, task_id)
    assert snap["task_status"] == "completed", snap
    assert snap["current_task_id"] != task_id, snap
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers)
    assert current.status_code == 200, current.text
    assert current.json()["task_id"] != task_id


def test_changes_requested_review_does_not_complete_path_entry(client):
    """A changes-requested review must keep the same path entry unfinished."""
    student_id = f"reworkpath_{uuid.uuid4().hex[:8]}"
    headers = _headers_for(student_id)
    data = _generate(client, headers, student_id)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=headers).status_code == 200
    task_id = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers).json()["task_id"]
    assert client.post(f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=headers).status_code == 200
    submitted = client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/submit",
        headers=headers,
        data={"metadata": '{"response": "substantial implementation writeup with enough length to be analyzed properly by the reviewer logic in this regression test"}'},
    )
    assert submitted.status_code == 201, submitted.text

    evaluated = client.patch(
        f"/api/tasks/{task_id}/status",
        headers=headers,
        json={"status": "changes_requested"},
    )
    assert evaluated.status_code == 200, evaluated.text

    path = client.get(f"/api/simulations/{sim_id}/task-path", headers=headers).json()
    entry = next(e for e in path if e["task_id"] == task_id)
    assert entry["status"] != "completed", entry

    snap = _access_snapshot(sim_id, task_id)
    assert snap["task_status"] == "changes_requested", snap
    assert snap["unlocked"], snap
    assert snap["current_task_id"] == task_id, snap


class _ApprovingReviewer(AIService):
    async def generate_text(self, prompt, system_prompt=None):
        return "review"

    async def generate_json(self, prompt, system_prompt=None):
        return {
            "result": "approved",
            "score": 90,
            "severity": "info",
            "summary": "Solid implementation meeting all acceptance criteria.",
        }


def _review_version(client, headers, project_id, version_id, reviewer):
    app.dependency_overrides[get_ai_service] = reviewer
    try:
        resp = client.post(
            f"/api/projects/{project_id}/reviews/versions/{version_id}",
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    print(f"\nREVIEW {version_id} -> {resp.status_code} result={(resp.json().get('result') if resp.status_code == 201 else resp.text[:200])}")
    return resp


def test_full_lifecycle_submit_review_rework_approve_complete(client):
    """Prescribed flow: submit→UNDER_REVIEW→changes→resubmit→UNDER_REVIEW
    →approved→COMPLETED→next task unlocks. Current never moves early."""
    student_id = f"lifecycle_{uuid.uuid4().hex[:8]}"
    headers = _headers_for(student_id)
    data = _generate(client, headers, student_id)
    sim_id, project_id = data["simulation_id"], data["project_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=headers).status_code == 200

    task1 = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers).json()["task_id"]
    path = client.get(f"/api/simulations/{sim_id}/task-path", headers=headers).json()
    task2 = next(e["task_id"] for e in path if e["task_id"] != task1)

    assert client.post(f"/api/simulations/{sim_id}/tasks/{task1}/start", headers=headers).status_code == 200
    first = client.post(
        f"/api/simulations/{sim_id}/tasks/{task1}/submit",
        headers=headers,
        data={"metadata": '{"response": "First implementation writeup describing the approach in auth.py, the files touched including handlers and validators, the null checks added for blank passwords, the 400 error branch wired to the JSON error format, and the manual verification steps performed in detail with curl."}'},
    )
    assert first.status_code == 201, first.text

    # 1. First submission: UNDER_REVIEW, not completed, current unmoved, next locked.
    snap = _access_snapshot(sim_id, task1)
    assert snap["task_status"] == "under_review", snap
    assert snap["current_task_id"] == task1, snap
    locked_next = client.post(f"/api/simulations/{sim_id}/tasks/{task2}/start", headers=headers)
    assert locked_next.status_code == 403, locked_next.text

    # 2. AI requests changes: same current task, feedback present.
    reviewed = _review_version(client, headers, project_id, first.json()["version_id"], lambda: _UnavailableReviewer())
    assert reviewed.json()["result"] == "changes_required"
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers).json()
    assert current["status"] == "changes_requested", current["status"]
    assert current["task_id"] == task1
    assert current["review_feedback"] is not None
    manager_rework = client.post(
        f"/api/projects/{project_id}/reviews/versions/{first.json()['version_id']}/manager-review",
        headers=headers,
    )
    assert manager_rework.status_code == 201, manager_rework.text
    assert manager_rework.json()["manager_result"] == "changes_required"
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers).json()
    assert current["status"] == "changes_requested", current["status"]

    # 3. Resubmission: new attempt, back to UNDER_REVIEW, same task.
    second = client.post(
        f"/api/simulations/{sim_id}/tasks/{task1}/submit",
        headers=headers,
        data={"metadata": '{"response": "Revised implementation writeup addressing every review point with additional detail: corrected the blank-password guard in auth.py to return the standard JSON error envelope, added wrong-password and blank-password unit tests in test_auth.py covering boundary inputs and edge cases, re-ran the full pytest suite green twice, and re-verified the 400 path manually with curl for the second attempt."}'},
    )
    assert second.status_code == 201, second.text
    assert second.json()["version_id"] != first.json()["version_id"]
    snap = _access_snapshot(sim_id, task1)
    assert snap["task_status"] == "under_review", snap
    assert snap["current_task_id"] == task1, snap

    # 4. Approved review moves to MANAGER_APPROVAL only: not completed,
    # current unmoved, next task still locked.
    approved = _review_version(client, headers, project_id, second.json()["version_id"], lambda: _UnavailableReviewer())
    assert approved.json()["result"] == "approved"
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers).json()
    assert current["status"] == "manager_approval", current["status"]
    assert current["task_id"] == task1
    still_locked = client.post(f"/api/simulations/{sim_id}/tasks/{task2}/start", headers=headers)
    assert still_locked.status_code == 403, still_locked.text

    # 5. Manager review completes the task and unlocks the next one.
    manager_approval = client.post(
        f"/api/projects/{project_id}/reviews/versions/{second.json()['version_id']}/manager-review",
        headers=headers,
    )
    assert manager_approval.status_code == 201, manager_approval.text
    assert manager_approval.json()["final_task_status"] == "completed"
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers).json()
    assert current["task_id"] == task2, current
    assert client.post(f"/api/simulations/{sim_id}/tasks/{task2}/start", headers=headers).status_code == 200


def test_stuck_path_entry_repaired_on_load(client):
    """A path entry wrongly left COMPLETED for an active task is healed."""
    from sqlalchemy import create_engine as _ce

    student_id = f"repair_{uuid.uuid4().hex[:8]}"
    headers = _headers_for(student_id)
    data = _generate(client, headers, student_id)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=headers).status_code == 200
    task_id = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers).json()["task_id"]
    assert client.post(f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=headers).status_code == 200
    submitted = client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/submit",
        headers=headers,
        data={"metadata": '{"response": "implementation writeup with enough substance to be analyzed properly by the reviewer logic in this repair test"}'},
    )
    assert submitted.status_code == 201, submitted.text

    engine = _ce(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE simulation_task_paths SET status = 'completed' "
                "WHERE simulation_id = :sim_id AND task_id = :task_id"
            ),
            {"sim_id": sim_id, "task_id": task_id},
        )
    corrupted = client.get(f"/api/simulations/{sim_id}/task-path", headers=headers).json()
    assert next(e for e in corrupted if e["task_id"] == task_id)["status"] == "completed"

    # Merely loading the current task repairs the divergence.
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers).json()
    assert current["task_id"] == task_id
    assert current["status"] == "under_review", current["status"]
    healed = client.get(f"/api/simulations/{sim_id}/task-path", headers=headers).json()
    assert next(e for e in healed if e["task_id"] == task_id)["status"] != "completed"


def test_bogus_completion_reopened_on_load(client):
    """A current task wrongly left COMPLETED without an approved review is
    reopened (not left locked), so revision can proceed after refresh."""
    from sqlalchemy import create_engine as _ce

    student_id = f"bogus_{uuid.uuid4().hex[:8]}"
    headers = _headers_for(student_id)
    data = _generate(client, headers, student_id)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=headers).status_code == 200
    task_id = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers).json()["task_id"]
    assert client.post(f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=headers).status_code == 200
    submitted = client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/submit",
        headers=headers,
        data={"metadata": '{"response": "implementation writeup with enough substance to be analyzed properly by the reviewer logic in this bogus-completion test"}'},
    )
    assert submitted.status_code == 201, submitted.text

    # Simulate the old bug: task force-completed with no approving review.
    engine = _ce(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tasks SET status = 'completed' WHERE id = :task_id"),
            {"task_id": task_id},
        )
        conn.execute(
            text("UPDATE simulation_task_paths SET status = 'completed' WHERE simulation_id = :sim_id AND task_id = :task_id"),
            {"sim_id": sim_id, "task_id": task_id},
        )

    # Loading the current task heals it back to an active review state.
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers).json()
    assert current["task_id"] == task_id, current
    assert current["status"] == "under_review", current["status"]
    healed = client.get(f"/api/simulations/{sim_id}/task-path", headers=headers).json()
    assert next(e for e in healed if e["task_id"] == task_id)["status"] != "completed"
    # ... the reopened task is accessible again, while a duplicate
    # submission of the already-submitted version is still rejected.
    snap = _access_snapshot(sim_id, task_id)
    assert snap["unlocked"], snap
    dupe = client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/submit",
        headers=headers,
        data={"metadata": '{"response": "duplicate submission attempt with enough substance to pass the evidence gate in this test"}'},
    )
    assert dupe.status_code == 403, dupe.text


def test_future_task_still_locked_during_rework(client):
    """Progression/locking intact: non-current tasks stay locked."""
    import uuid as _uuid

    student_id = f"stilllocked_{_uuid.uuid4().hex[:8]}"
    headers = _headers_for(student_id)
    data = _generate(client, headers, student_id)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=headers).status_code == 200
    fake_id = str(_uuid.uuid4())
    for method, kwargs in [
        ("post", {"url": f"/api/simulations/{sim_id}/tasks/{fake_id}/start"}),
        ("post", {"url": f"/api/simulations/{sim_id}/tasks/{fake_id}/submit"}),
    ]:
        resp = getattr(client, method)(kwargs["url"], headers=headers, data={"metadata": "{}"})
        assert resp.status_code == 403, resp.text
        assert "locked" in resp.json()["detail"].lower()
