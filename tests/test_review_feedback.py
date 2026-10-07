"""AI task review feedback: specific, actionable resubmission guidance.

Covers: the pure payload shaper (unmet criteria, required changes, testing
guidance), the review prompt's specificity rules, and the end-to-end flow —
submit → AI review requesting changes → tasks/current exposes the feedback.
"""
import os

import pytest
from sqlalchemy import text

from app.main import app
from app.services.ai.ai_service import AIService, get_ai_service
from app.services.ai.prompt_builder import build_review_prompt
from app.services.ai.review_service import shape_review_feedback


CHANGES_PAYLOAD = {
    "result": "changes_required",
    "score": 62,
    "severity": "warning",
    "summary": "The login endpoint works for the happy path but error handling and tests are missing.",
    "strengths": ["Clean route structure"],
    "issues": ["No error handling on invalid input", "Missing tests"],
    "required_changes": [
        "Add input validation to the login function in auth.py so blank passwords return 400 instead of 500; this unblocks 'Returns 400 for invalid input'. Re-run the endpoint manually before resubmitting.",
        "Add unit tests covering wrong-password and blank-password cases in test_auth.py; run pytest and confirm green before resubmitting.",
    ],
    "acceptance_criteria": [
        {
            "criterion": "Returns 400 for invalid input",
            "met": False,
            "evidence": "No validation branch found in the submitted auth.py; blank password path untested",
        },
        {
            "criterion": "Happy-path login works",
            "met": True,
            "evidence": "Login function returns a token in the submission",
        },
    ],
    "code_quality": {"score": 70, "feedback": "Readable structure, missing guards."},
    "testing": {"score": 45, "feedback": "Add pytest cases for wrong-password and blank-password logins."},
    "security": {"score": 70, "feedback": "Hash passwords before compare."},
    "performance": {"score": 80, "feedback": "Fine for this scope."},
}


class _DemandingReviewer(AIService):
    """Deterministic reviewer requesting changes with specific guidance."""

    async def generate_text(self, prompt, system_prompt=None):
        return "review"

    async def generate_json(self, prompt, system_prompt=None):
        return dict(CHANGES_PAYLOAD)


def _generate(client, auth_headers, student_id="student_001"):
    resp = client.post(
        "/api/generate",
        headers=auth_headers,
        json={
            "student_id": student_id,
            "role": "Backend Developer",
            "technology_stack": ["Python", "FastAPI", "PostgreSQL"],
            "difficulty": "intermediate",
            "company_type": "startup",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_shaper_extracts_unmet_criteria_with_evidence():
    shaped = shape_review_feedback(CHANGES_PAYLOAD)
    assert shaped["result"] == "changes_required"
    assert shaped["score"] == 62
    assert len(shaped["unmet_criteria"]) == 1
    unmet = shaped["unmet_criteria"][0]
    assert unmet["criterion"] == "Returns 400 for invalid input"
    assert "auth.py" in unmet["evidence"]
    assert all("auth.py" in change or "test_auth.py" in change for change in shaped["required_changes"])
    assert "pytest" in (shaped["testing_feedback"] or "")


def test_shaper_falls_back_to_actions_and_ignores_met_criteria():
    payload = {
        "result": "changes_required",
        "score": 60,
        "summary": "Needs work, see actions.",
        "actions": ["Fix the null guard in utils.py"],
        "acceptance_criteria": [
            {"criterion": "Build passes", "met": True, "evidence": "CI log attached"},
        ],
    }
    shaped = shape_review_feedback(payload)
    assert shaped["required_changes"] == ["Fix the null guard in utils.py"]
    assert shaped["unmet_criteria"] == []
    assert shaped["test_criteria"] == []


def test_shaper_handles_empty_payload():
    shaped = shape_review_feedback({})
    assert shaped["required_changes"] == []
    assert shaped["unmet_criteria"] == []
    assert shaped["testing_feedback"] is None
    shaped_none = shape_review_feedback(None)
    assert shaped_none["summary"] == ""


def test_review_prompt_demands_actionable_changes():
    prompt = build_review_prompt({
        "task_title": "Login API",
        "task_description": "Implement login",
        "acceptance_criteria": ["Returns 400 for invalid input"],
        "submission_content": "def login(): pass",
        "technology_stack": ["Python"],
    })
    assert "which acceptance criterion it unblocks" in prompt
    assert "what to test before resubmitting" in prompt
    assert "Never return vague advice" in prompt


def _submit_task_version(client, auth_headers, sim_id, task_id, metadata='{"response": "Here is my login implementation"}'):
    submitted = client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/submit",
        headers=auth_headers,
        data={"metadata": metadata},
    )
    assert submitted.status_code == 201, submitted.text
    return submitted.json()["version_id"]


def _start_task(client, auth_headers):
    data = _generate(client, auth_headers)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=auth_headers).status_code == 200
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=auth_headers)
    assert current.status_code == 200, current.text
    task_id = current.json()["task_id"]
    assert client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=auth_headers
    ).status_code == 200
    return data, sim_id, task_id


def test_changes_requested_exposes_specific_feedback(client, auth_headers):
    data = _generate(client, auth_headers)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=auth_headers).status_code == 200
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=auth_headers)
    assert current.status_code == 200, current.text
    task_id = current.json()["task_id"]
    assert client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=auth_headers
    ).status_code == 200
    submitted = client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/submit",
        headers=auth_headers,
        data={"metadata": '{"response": "Here is my login implementation. I added null checks to the login handler in auth.py so blank passwords return a 400 response instead of crashing, wired the error branch to the existing JSON error format, and verified the 400 path manually with curl. I also added unit tests covering wrong-password and blank-password cases in test_auth.py and ran the full pytest suite green before submitting."}'},
    )
    assert submitted.status_code == 201, submitted.text
    version_id = submitted.json()["version_id"]

    app.dependency_overrides[get_ai_service] = lambda: _DemandingReviewer()
    try:
        reviewed = client.post(
            f"/api/projects/{data['project_id']}/reviews/versions/{version_id}",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    assert reviewed.status_code == 201, reviewed.text
    assert reviewed.json()["result"] == "changes_required"

    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=auth_headers)
    assert current.status_code == 200, current.text
    body = current.json()
    assert body["status"] == "changes_requested"
    feedback = body["review_feedback"]
    assert feedback is not None
    assert any("auth.py" in change for change in feedback["required_changes"])
    assert any(
        item["criterion"] == "Returns 400 for invalid input" and "auth.py" in item["evidence"]
        for item in feedback["unmet_criteria"]
    )
    assert "pytest" in (feedback["testing_feedback"] or "")
    assert feedback["score"] == 62


def test_metadata_response_is_analyzed_not_ignored(client, auth_headers):
    """A file-less submission with a written response must reach the reviewer."""

    class _RecordingReviewer(AIService):
        def __init__(self):
            self.prompts = []

        async def generate_text(self, prompt, system_prompt=None):
            return "review"

        async def generate_json(self, prompt, system_prompt=None):
            self.prompts.append(prompt)
            return dict(CHANGES_PAYLOAD)

    data, sim_id, task_id = _start_task(client, auth_headers)
    version_id = _submit_task_version(
        client, auth_headers, sim_id, task_id,
        metadata='{"response": "I added null checks to the login handler in auth.py so blank passwords return a 400 response instead of crashing, wired the error branch to the existing JSON error format, and verified the 400 path manually with curl. I also added unit tests covering wrong-password and blank-password cases in test_auth.py and ran the full pytest suite green before submitting."}',
    )
    recorder = _RecordingReviewer()
    app.dependency_overrides[get_ai_service] = lambda: recorder
    try:
        reviewed = client.post(
            f"/api/projects/{data['project_id']}/reviews/versions/{version_id}",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    assert reviewed.status_code == 201, reviewed.text
    assert recorder.prompts, "reviewer was never consulted"
    assert "null checks to the login handler" in recorder.prompts[0]
    assert "No work evidence submitted" not in recorder.prompts[0]


def test_empty_submission_gets_missing_evidence_review(client, auth_headers):
    """No files and no textual evidence: deterministic guidance, no invented code."""
    from sqlalchemy import create_engine as _ce

    data, sim_id, task_id = _start_task(client, auth_headers)
    version_id = _submit_task_version(client, auth_headers, sim_id, task_id)
    engine = _ce(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE submission_versions SET files = '[]'::jsonb, version_metadata = '{}'::jsonb WHERE id = :id"),
            {"id": version_id},
        )
    reviewed = client.post(
        f"/api/projects/{data['project_id']}/reviews/versions/{version_id}",
        headers=auth_headers,
    )
    assert reviewed.status_code == 201, reviewed.text
    body = reviewed.json()
    assert body["result"] == "changes_required"
    assert body["summary"] == (
        "No implementation was submitted. "
        "Please submit your implementation and automated tests for review."
    )
    assert any("Submit your implementation" in c for c in body["raw"]["required_changes"])

    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=auth_headers)
    assert current.status_code == 200, current.text
    feedback = current.json()["review_feedback"]
    assert feedback is not None
    assert feedback["missing_evidence"] is True
    assert all(item["evidence"].startswith("Not demonstrated") for item in feedback["unmet_criteria"])
    blob = " ".join([feedback["summary"]] + feedback["required_changes"])
    assert "def " not in blob and ".py" not in blob


def test_thin_submission_never_reaches_the_model(client, auth_headers):
    """A one-line note with no code: deterministic guidance, zero AI invention."""

    class _RecordingReviewer(AIService):
        def __init__(self):
            self.prompts = []

        async def generate_text(self, prompt, system_prompt=None):
            return "review"

        async def generate_json(self, prompt, system_prompt=None):
            self.prompts.append(prompt)
            return dict(CHANGES_PAYLOAD)

    data, sim_id, task_id = _start_task(client, auth_headers)
    version_id = _submit_task_version(
        client, auth_headers, sim_id, task_id,
        metadata='{"response": "done"}',
    )
    recorder = _RecordingReviewer()
    app.dependency_overrides[get_ai_service] = lambda: recorder
    try:
        reviewed = client.post(
            f"/api/projects/{data['project_id']}/reviews/versions/{version_id}",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    assert reviewed.status_code == 201, reviewed.text
    assert reviewed.json()["result"] == "changes_required"
    assert recorder.prompts == [], "model must not be consulted for thin submissions"
    assert reviewed.json()["summary"] == (
        "No implementation was submitted. "
        "Please submit your implementation and automated tests for review."
    )

    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=auth_headers)
    feedback = current.json()["review_feedback"]
    assert feedback is not None and feedback["missing_evidence"] is True
    blob = " ".join([feedback["summary"]] + feedback["required_changes"])
    assert "def " not in blob and ".py" not in blob


def test_submitted_file_content_reaches_the_reviewer(client, auth_headers):
    """Real file content must be what the AI analyzes."""

    class _RecordingReviewer(AIService):
        def __init__(self):
            self.prompts = []

        async def generate_text(self, prompt, system_prompt=None):
            return "review"

        async def generate_json(self, prompt, system_prompt=None):
            self.prompts.append(prompt)
            return dict(CHANGES_PAYLOAD)

    import io

    data, sim_id, task_id = _start_task(client, auth_headers)
    submitted = client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/submit",
        headers=auth_headers,
        files={"files": ("login.py", io.BytesIO(b"def login(user, password):\n    return None\n"), "text/x-python")},
        data={"metadata": '{"response": "Implemented the login handler"}'},
    )
    assert submitted.status_code == 201, submitted.text
    version_id = submitted.json()["version_id"]
    recorder = _RecordingReviewer()
    app.dependency_overrides[get_ai_service] = lambda: recorder
    try:
        reviewed = client.post(
            f"/api/projects/{data['project_id']}/reviews/versions/{version_id}",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    assert reviewed.status_code == 201, reviewed.text
    assert recorder.prompts, "reviewer was never consulted for substantive content"
    assert "def login" in recorder.prompts[0]


def test_review_feedback_is_stable_across_reads(client, auth_headers):
    data, sim_id, task_id = _start_task(client, auth_headers)
    version_id = _submit_task_version(client, auth_headers, sim_id, task_id)
    app.dependency_overrides[get_ai_service] = lambda: _DemandingReviewer()
    try:
        assert client.post(
            f"/api/projects/{data['project_id']}/reviews/versions/{version_id}",
            headers=auth_headers,
        ).status_code == 201
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    first = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=auth_headers).json()["review_feedback"]
    second = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=auth_headers).json()["review_feedback"]
    assert first is not None and first == second


def test_ai_failure_uses_deterministic_review_and_resolves_state(client, auth_headers):
    """A failed Gemini call falls back and never strands a task in UNDER_REVIEW."""
    from sqlalchemy import select as _select

    from app.core.database import SessionLocal
    from app.models.submission import AIReview, SubmissionVersion
    from app.models.task import Task
    from app.services.ai.ai_service import AIService

    class _BrokenReviewer(AIService):
        async def generate_text(self, prompt, system_prompt=None):
            raise RuntimeError("gemini down")

        async def generate_json(self, prompt, system_prompt=None):
            raise RuntimeError("gemini down")

    data, sim_id, task_id = _start_task(client, auth_headers)
    version_id = _submit_task_version(
        client, auth_headers, sim_id, task_id,
        metadata='{"response": "I added null checks to the login handler in auth.py so blank passwords return a 400 response instead of crashing, wired the error branch to the existing JSON error format, and verified the 400 path manually with curl. I also added unit tests covering wrong-password and blank-password cases in test_auth.py and ran the full pytest suite green before submitting."}',
    )
    app.dependency_overrides[get_ai_service] = lambda: _BrokenReviewer()
    try:
        reviewed = client.post(
            f"/api/projects/{data['project_id']}/reviews/versions/{version_id}",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    assert reviewed.status_code == 201, reviewed.text
    assert reviewed.json()["result"] == "changes_required"

    db = SessionLocal()
    try:
        rows = db.execute(
            _select(AIReview).where(AIReview.version_id == version_id)
        ).scalars().all()
        assert len(rows) == 1, "deterministic fallback review should be persisted"
        version = db.get(SubmissionVersion, version_id)
        assert version.status.value == "changes_requested"
        task = db.get(Task, version.submission.task_id)
        assert task.status.value == "changes_requested"
    finally:
        db.close()


def test_current_task_refresh_recovers_previously_stuck_review(client, auth_headers):
    """A committed UNDER_REVIEW task from a failed old request resumes on load."""
    from datetime import datetime, timedelta, timezone

    from app.core.database import SessionLocal
    from app.models.submission import SubmissionVersion
    from app.services.ai.ai_service import AIService

    class _BrokenReviewer(AIService):
        async def generate_text(self, prompt, system_prompt=None):
            raise RuntimeError("gemini down")

        async def generate_json(self, prompt, system_prompt=None):
            raise RuntimeError("gemini down")

    _, sim_id, task_id = _start_task(client, auth_headers)
    version_id = _submit_task_version(
        client,
        auth_headers,
        sim_id,
        task_id,
        metadata='{"response": "I added input validation to the login handler so blank passwords return the standard 400 JSON response rather than an internal error. I documented the new behavior, added unit coverage for blank and wrong passwords, and ran the full test suite to verify both normal and error cases."}',
    )
    db = SessionLocal()
    try:
        version = db.get(SubmissionVersion, version_id)
        version.submitted_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.add(version)
        db.commit()
    finally:
        db.close()
    app.dependency_overrides[get_ai_service] = lambda: _BrokenReviewer()
    try:
        resumed = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=auth_headers)
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "changes_requested", resumed.json()
    assert resumed.json()["review_feedback"] is not None


@pytest.mark.asyncio
async def test_review_runs_once_per_version(client, auth_headers):
    """A duplicate review call returns the saved review without re-running AI."""
    from sqlalchemy import select as _select

    from app.core.database import SessionLocal
    from app.models.submission import AIReview, SubmissionVersion
    from app.services.ai.ai_service import MockAIService
    from app.services.ai.review_service import ReviewService

    data, sim_id, task_id = _start_task(client, auth_headers)
    version_id = _submit_task_version(client, auth_headers, sim_id, task_id)
    db = SessionLocal()
    try:
        version = db.get(SubmissionVersion, version_id)
        service = ReviewService(MockAIService())
        first = await service.review_submission_version(db, version)
        second = await service.review_submission_version(db, version)
        assert first["review_id"] == second["review_id"]
        rows = db.execute(
            _select(AIReview).where(AIReview.version_id == version.id)
        ).scalars().all()
        assert len(rows) == 1
    finally:
        db.close()
