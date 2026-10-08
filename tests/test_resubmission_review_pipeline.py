"""Resubmission pipeline: a second attempt must get its OWN AI review.

Mandatory regression suite for: task requested changes -> student resubmits ->
a NEW submission version is stored -> a NEW version-scoped AI review runs ->
the first attempt's review stays intact -> duplicate requests stay idempotent
-> review failures are stored unscored and retryable -> adaptation/feedback
always follow the latest version -> the resubmission and its review raise
their own event/notification.

Tests a)-i) map to the nine functions below.
"""
import uuid

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models.enums import EventType, NotificationType, ReviewStatus
from app.models.event import Event
from app.models.notification import Notification
from app.models.submission import AIReview, Submission, SubmissionVersion
from app.models.task import Task
from app.services.ai.ai_service import AIService, get_ai_service


def _payload(score, summary, marker=None):
    return {
        "result": "changes_required",
        "score": score,
        "severity": "warning",
        "summary": summary,
        "strengths": ["Structure is readable"],
        "issues": ["Missing error handling"],
        "required_changes": ["Handle invalid input and add tests before resubmitting."],
        "acceptance_criteria": [],
        "code_quality": {"score": score, "feedback": "Acceptable for this scope."},
        "testing": {"score": score, "feedback": "Add coverage for the failing path."},
        "security": {"score": score, "feedback": "No issues found."},
        "performance": {"score": score, "feedback": "Fine for this scope."},
    }


class _ChangesReviewer(AIService):
    """Deterministic reviewer that always requests changes."""

    async def generate_text(self, prompt, system_prompt=None):
        return "review"

    async def generate_json(self, prompt, system_prompt=None):
        return _payload(41, "First attempt needs the listed changes before approval.")


class _CountingReviewer(_ChangesReviewer):
    """Counts AI calls so idempotency can be proven, not assumed."""

    def __init__(self):
        self.calls = 0

    async def generate_json(self, prompt, system_prompt=None):
        self.calls += 1
        return await super().generate_json(prompt, system_prompt)


class _MarkerReviewer(AIService):
    """Scores by attempt marker so 'latest review wins' is observable."""

    async def generate_text(self, prompt, system_prompt=None):
        return "review"

    async def generate_json(self, prompt, system_prompt=None):
        score = 30 if "FIRST_ATTEMPT" in prompt else 92
        return _payload(score, f"Deterministic evaluation scored this attempt {score}/100.")


def _headers_for(student_id):
    from tests.conftest import make_token

    return {"Authorization": f"Bearer {make_token(student_id)}"}


def _generate(client, headers, student_id):
    resp = client.post(
        "/api/generate",
        headers=headers,
        json={
            "student_id": student_id,
            "role": "Backend Developer",
            "technology_stack": ["Python", "FastAPI"],
            "difficulty": "intermediate",
            "company_type": "startup",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _open_current_task(client, headers, data):
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=headers).status_code == 200
    task_id = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=headers).json()["task_id"]
    assert client.post(f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=headers).status_code == 200
    return task_id


def _submit(client, headers, sim_id, task_id, marker):
    import io as _io

    files = [(
        "files",
        ("solution.py", _io.BytesIO(
            (
                f"# {marker}\n"
                "import logging\n\n"
                "logger = logging.getLogger(__name__)\n\n"
                "class FlaggingPipeline:\n"
                "    def __init__(self, config):\n"
                "        self.threshold = config.get('threshold', 0.8)\n"
                "        self.handled_cases = 0\n\n"
                "    def evaluate(self, item):\n"
                "        score = self._score(item)\n"
                "        logger.info('evaluated item=%s score=%s', item, score)\n"
                "        if score >= self.threshold:\n"
                "            self.handled_cases += 1\n"
                "            return True\n"
                "        return False\n\n"
                "    def _score(self, item):\n"
                "        content = item.get('content', '').lower()\n"
                "        normalized = sum(1 for ch in content if ch.isalnum())\n"
                "        return min(1.0, normalized / 200.0)\n\n"
                "def build_pipeline(config):\n"
                "    return FlaggingPipeline(config)\n"
            ).encode()
        ), "text/x-python"),
    )]
    resp = client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/submit",
        headers=headers,
        files=files,
        data={"metadata": f'{{"response": "{marker}: validation plan, edge cases, and test evidence"}}'},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _review(client, headers, data, version_id, reviewer=None):
    if reviewer is not None:
        app.dependency_overrides[get_ai_service] = lambda: reviewer
    try:
        return client.post(
            f"/api/projects/{data['project_id']}/reviews/versions/{version_id}",
            headers=headers,
        )
    finally:
        if reviewer is not None:
            app.dependency_overrides.pop(get_ai_service, None)


def _two_attempt_flow(client, reviewer=None, marker_reviewer=False):
    """submit -> review(changes) -> resubmit. Returns the flow handles."""
    student_id = f"resub_{uuid.uuid4().hex[:8]}"
    headers = _headers_for(student_id)
    data = _generate(client, headers, student_id)
    task_id = _open_current_task(client, headers, data)
    sim_id = data["simulation_id"]

    first = _submit(client, headers, sim_id, task_id, "FIRST_ATTEMPT")
    reviewer = reviewer or (_MarkerReviewer() if marker_reviewer else _ChangesReviewer())
    app.dependency_overrides[get_ai_service] = lambda: reviewer
    try:
        reviewed = _review(client, headers, data, first["version_id"])
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    assert reviewed.status_code == 201, reviewed.text

    second = _submit(client, headers, sim_id, task_id, "SECOND_ATTEMPT")
    return {
        "student_id": student_id,
        "headers": headers,
        "data": data,
        "sim_id": sim_id,
        "task_id": task_id,
        "reviewer": reviewer,
        "v1": first["version_id"],
        "v1_review": reviewed.json(),
        "v2": second["version_id"],
    }


def test_a_resubmission_stores_a_new_submission_version(client):
    flow = _two_attempt_flow(client)
    db = SessionLocal()
    try:
        v1 = db.get(SubmissionVersion, flow["v1"])
        v2 = db.get(SubmissionVersion, flow["v2"])
        submission = db.query(Submission).filter(Submission.task_id == flow["task_id"]).one()
        assert v1.id != v2.id
        assert v2.version_number == v1.version_number + 1 == 2
        assert v1.submission_id == v2.submission_id == submission.id
        assert submission.task_id == uuid.UUID(str(flow["task_id"]))
        assert v2.status.value in ("submitted", "under_review", "changes_requested")
        task = db.get(Task, flow["task_id"])
        assert task.id == uuid.UUID(str(flow["task_id"]))
    finally:
        db.close()


def test_b_resubmission_triggers_a_new_version_scoped_review(client):
    flow = _two_attempt_flow(client)
    second = _review(client, flow["headers"], flow["data"], flow["v2"], reviewer=flow["reviewer"])
    assert second.status_code == 201, second.text
    body = second.json()
    assert body["review_id"] != flow["v1_review"]["review_id"]
    assert body["version_id"] == flow["v2"]
    assert body["status"] == ReviewStatus.COMPLETED.value
    assert body["result"] == "changes_required"
    assert body["score"] == 41

    db = SessionLocal()
    try:
        rows = db.execute(select(AIReview)).scalars().all()
        for version_id in (flow["v1"], flow["v2"]):
            scoped = [r for r in rows if r.version_id == uuid.UUID(str(version_id))]
            assert len(scoped) == 1, f"expected exactly one review for {version_id}, got {len(scoped)}"
            assert scoped[0].status == ReviewStatus.COMPLETED
    finally:
        db.close()


def test_c_first_review_is_preserved_after_the_second_review(client):
    flow = _two_attempt_flow(client)
    before = dict(flow["v1_review"])
    second = _review(client, flow["headers"], flow["data"], flow["v2"], reviewer=flow["reviewer"])
    assert second.status_code == 201, second.text

    again = client.get(
        f"/api/projects/{flow['data']['project_id']}/reviews/versions/{flow['v1']}",
        headers=flow["headers"],
    )
    assert again.status_code == 200, again.text
    persisted = again.json()
    for key in ("review_id", "status", "result", "score", "summary"):
        assert persisted.get(key) == before.get(key), f"{key} changed: {before.get(key)!r} -> {persisted.get(key)!r}"
    # The stored payload may gain adaptation bookkeeping, but the review
    # content itself (result, score, guidance) must not have been rewritten.
    raw_before, raw_after = before.get("raw") or {}, persisted.get("raw") or {}
    for key in ("result", "score", "summary", "strengths", "issues", "required_changes"):
        assert raw_after.get(key) == raw_before.get(key), f"raw.{key} changed"

    db = SessionLocal()
    try:
        v1_row = db.get(AIReview, uuid.UUID(str(before["review_id"])))
        assert v1_row.result.value == "changes_required"
        assert v1_row.score == before["score"]
        assert v1_row.status == ReviewStatus.COMPLETED
    finally:
        db.close()


def test_d_duplicate_review_request_for_the_same_version_stays_idempotent(client):
    reviewer = _CountingReviewer()
    flow = _two_attempt_flow(client, reviewer=reviewer)
    first_call = _review(client, flow["headers"], flow["data"], flow["v2"], reviewer=flow["reviewer"])
    assert first_call.status_code == 201, first_call.text
    calls_after_first = reviewer.calls

    repeat = _review(client, flow["headers"], flow["data"], flow["v2"], reviewer=flow["reviewer"])
    assert repeat.status_code == 201, repeat.text
    assert repeat.json()["review_id"] == first_call.json()["review_id"]
    assert reviewer.calls == calls_after_first, "duplicate request re-ran the AI reviewer"

    db = SessionLocal()
    try:
        scoped = db.execute(
            select(AIReview).where(AIReview.version_id == uuid.UUID(str(flow["v2"])))
        ).scalars().all()
        assert len(scoped) == 1
    finally:
        db.close()


def test_e_already_reviewed_task_does_not_block_the_new_versions_review(client):
    """Idempotency is scoped to the version, never to 'this task was reviewed'."""
    flow = _two_attempt_flow(client)

    # The old, already reviewed version answers as an idempotent read...
    stale = _review(client, flow["headers"], flow["data"], flow["v1"])
    assert stale.status_code == 201, stale.text
    assert stale.json()["review_id"] == flow["v1_review"]["review_id"]

    # ...and the NEW version still runs its own review instead of being
    # rejected with an 'already reviewed' style error.
    fresh = _review(client, flow["headers"], flow["data"], flow["v2"], reviewer=flow["reviewer"])
    assert fresh.status_code == 201, fresh.text
    assert fresh.json()["version_id"] == flow["v2"]
    assert fresh.json()["review_id"] != flow["v1_review"]["review_id"]
    assert "already reviewed" not in fresh.text.lower()


def test_f_review_failure_is_stored_unscored_and_retryable(client, monkeypatch):
    flow = _two_attempt_flow(client)

    def _explode(*args, **kwargs):
        raise RuntimeError("review context builder unavailable")

    monkeypatch.setattr("app.services.ai.review_service.build_review_context", _explode)
    failed = _review(client, flow["headers"], flow["data"], flow["v2"])
    assert failed.status_code == 503, failed.text
    assert "saved" in failed.text.lower()

    db = SessionLocal()
    try:
        rows = db.execute(
            select(AIReview).where(AIReview.version_id == uuid.UUID(str(flow["v2"])))
        ).scalars().all()
        assert len(rows) == 1, rows
        review = rows[0]
        assert review.status == ReviewStatus.FAILED
        assert review.score is None, "a failed review must never invent a score"
        assert review.result is None
        assert (review.raw_payload or {}).get("error") == "review_processing_failed"
        version = db.get(SubmissionVersion, flow["v2"])
        assert version.status.value == "under_review"
        task = db.get(Task, flow["task_id"])
        assert task.status.value == "under_review"
    finally:
        db.close()

    current = client.get(f"/api/simulations/{flow['sim_id']}/tasks/current", headers=flow["headers"])
    assert current.status_code == 200, current.text
    assert current.json()["review_status"] == "failed"
    assert current.json()["review_version_id"] == flow["v2"]
    assert current.json()["review_error"]

    monkeypatch.undo()
    retried = _review(client, flow["headers"], flow["data"], flow["v2"], reviewer=flow["reviewer"])
    assert retried.status_code == 201, retried.text
    assert retried.json()["status"] == ReviewStatus.COMPLETED.value
    assert retried.json()["score"] is not None

    db = SessionLocal()
    try:
        rows = db.execute(
            select(AIReview).where(AIReview.version_id == uuid.UUID(str(flow["v2"])))
        ).scalars().all()
        assert len(rows) == 1, "retry must reuse the failed row, not duplicate it"
        assert rows[0].status == ReviewStatus.COMPLETED
    finally:
        db.close()


def test_g_adaptation_and_feedback_follow_the_latest_review_only(client):
    flow = _two_attempt_flow(client, marker_reviewer=True)

    v1_raw = flow["v1_review"]["raw"]
    v1_decision = flow["v1_review"]["adaptive_decision"]
    assert v1_decision["status"] == "applied"
    v1_technical = v1_decision["technical_score"]

    second = _review(client, flow["headers"], flow["data"], flow["v2"], reviewer=flow["reviewer"])
    assert second.status_code == 201, second.text
    v2_decision = second.json()["adaptive_decision"]
    assert v2_decision["status"] == "applied"
    assert v2_decision["technical_score"] != v1_technical, "second attempt should be scored independently"

    # The v1 payload must still carry the v1 decision (nothing overwritten).
    stale = client.get(
        f"/api/projects/{flow['data']['project_id']}/reviews/versions/{flow['v1']}",
        headers=flow["headers"],
    ).json()
    assert stale["raw"]["adaptive_decision"]["technical_score"] == v1_technical

    # The task view and the analytics view both follow the v2 decision.
    current = client.get(f"/api/simulations/{flow['sim_id']}/tasks/current", headers=flow["headers"]).json()
    assert current["review_feedback"]["version_number"] == 2
    assert current["review_feedback"]["adaptive_decision"]["technical_score"] == v2_decision["technical_score"]

    analytics = client.get(
        f"/api/intelligence/analytics?simulation_id={flow['sim_id']}",
        headers=flow["headers"],
    )
    assert analytics.status_code == 200, analytics.text
    assert analytics.json()["summary"]["technical_score"] == v2_decision["technical_score"]


def test_h_resubmission_and_second_review_raise_their_own_event_and_notification(client):
    flow = _two_attempt_flow(client)

    db = SessionLocal()
    try:
        resubmitted = db.execute(
            select(Event).where(
                Event.project_id == flow["data"]["project_id"],
                Event.event_type == EventType.SUBMISSION_RESUBMITTED,
            )
        ).scalars().all()
        assert resubmitted, "resubmission must raise a submission_resubmitted event"
        assert any(int(str(e.payload.get("new_version"))) >= 2 for e in resubmitted)

        company_id = db.get(Task, flow["task_id"]).sprint.project.company_id
        notifications_before = db.execute(
            select(Notification).where(
                Notification.company_id == company_id,
                Notification.notification_type == NotificationType.REVIEW_COMPLETED,
            )
        ).scalars().all()
        assert notifications_before, "first review should already have notified the student"
        seen_before = {n.id for n in notifications_before}
    finally:
        db.close()

    second = _review(client, flow["headers"], flow["data"], flow["v2"], reviewer=flow["reviewer"])
    assert second.status_code == 201, second.text
    v2_review_id = second.json()["review_id"]

    db = SessionLocal()
    try:
        events = db.execute(
            select(Event).where(
                Event.project_id == flow["data"]["project_id"],
                Event.event_type == EventType.AI_REVIEW_COMPLETED,
            )
        ).scalars().all()
        assert any(str(e.payload.get("review_id")) == v2_review_id for e in events), \
            "second review must emit its own ai_review_completed event"

        notifications = db.execute(
            select(Notification).where(
                Notification.company_id == company_id,
                Notification.notification_type == NotificationType.REVIEW_COMPLETED,
            )
        ).scalars().all()
        fresh = [n for n in notifications if n.id not in seen_before]
        assert fresh, "second review must notify the student again"
    finally:
        db.close()


def test_i_current_task_feedback_switches_to_the_second_version(client):
    flow = _two_attempt_flow(client)

    pending = client.get(f"/api/simulations/{flow['sim_id']}/tasks/current", headers=flow["headers"]).json()
    assert pending["status"] == "under_review"
    assert pending["review_version_id"] == flow["v2"]
    assert pending["review_status"] in ("not_started", "failed", "completed")
    feedback = pending["review_feedback"]
    assert feedback is not None and feedback["version_number"] == 1
    assert feedback["is_previous_review"] is True, "v1 feedback stays visible while v2 awaits review"

    second = _review(client, flow["headers"], flow["data"], flow["v2"], reviewer=flow["reviewer"])
    assert second.status_code == 201, second.text

    done = client.get(f"/api/simulations/{flow['sim_id']}/tasks/current", headers=flow["headers"]).json()
    assert done["status"] == "changes_requested"
    assert done["review_status"] == "completed"
    assert done["review_version_id"] == flow["v2"]
    assert done["review_feedback"]["version_number"] == 2
    assert done["review_feedback"]["is_previous_review"] is False
    assert done["review_feedback"]["summary"] == second.json()["summary"]
