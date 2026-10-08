"""End-to-end proof that submitted technical work drives task difficulty."""
import uuid

from app.main import app
from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.models.user import User
from app.services.ai.ai_service import AIService, get_ai_service


class _EvidenceReviewer(AIService):
    async def generate_text(self, prompt, system_prompt=None):
        return "rule-based test evaluator"

    async def generate_json(self, prompt, system_prompt=None):
        score = 35 if "E2E_WEAK" in prompt else 95
        return {
            "result": "approved",
            "score": score,
            "severity": "info",
            "summary": f"Deterministic integration evaluator scored the submitted evidence {score}/100.",
            "strengths": ["Submission evidence was evaluated by the test reviewer."],
            "issues": [],
            "required_changes": [],
            "acceptance_criteria": [],
            "code_quality": {"score": score, "feedback": "Deterministic integration rubric."},
            "testing": {"score": score, "feedback": "Deterministic integration rubric."},
            "security": {"score": score, "feedback": "Deterministic integration rubric."},
            "performance": {"score": score, "feedback": "Deterministic integration rubric."},
        }


def test_fresh_student_technical_reviews_change_difficulty_and_next_task(client):
    registration = client.post(
        "/api/auth/register",
        json={
            "email": f"adaptive-{uuid.uuid4().hex}@example.test",
            "password": "integration-pass",
            "full_name": "Adaptive Flow Test",
        },
    )
    assert registration.status_code == 201, registration.text
    student_id = registration.json()["student_id"]
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}

    app.dependency_overrides[get_ai_service] = lambda: _EvidenceReviewer()
    try:
        generated = client.post(
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
        assert generated.status_code == 201, generated.text
        simulation_id = generated.json()["simulation_id"]
        project_id = generated.json()["project_id"]
        assert generated.json()["difficulty"] == "intermediate"
        assert client.post(f"/api/simulations/{simulation_id}/start", headers=headers).status_code == 200

        observed = []
        for kind in ("weak", "weak", "strong", "strong", "strong", "strong", "strong"):
            current = client.get(f"/api/simulations/{simulation_id}/tasks/current", headers=headers)
            assert current.status_code == 200, current.text
            task = current.json()
            assert client.post(f"/api/simulations/{simulation_id}/tasks/{task['task_id']}/start", headers=headers).status_code == 200
            marker = "E2E_WEAK" if kind == "weak" else "E2E_STRONG"
            response_text = (
                f"{marker}: implementation details, requirements considered, validation plan, "
                "edge cases, test evidence, and debugging notes for this delivered task. "
                "The solution describes input handling, expected outputs, failure behavior, "
                "test cases, and how the implementation was checked against the stated acceptance criteria."
            )
            submitted = client.post(
                f"/api/simulations/{simulation_id}/tasks/{task['task_id']}/submit",
                headers=headers,
                data={"metadata": __import__("json").dumps({"response": response_text})},
            )
            assert submitted.status_code == 201, submitted.text
            version_id = submitted.json()["version_id"]
            reviewed = client.post(
                f"/api/projects/{project_id}/reviews/versions/{version_id}",
                headers=headers,
            )
            assert reviewed.status_code == 201, reviewed.text
            adaptation = reviewed.json()["adaptive_decision"]
            assert adaptation["status"] == "applied", adaptation
            observed.append((kind, adaptation["technical_score"], adaptation["previous_difficulty"], adaptation["next_difficulty"]))

            manager = client.post(
                f"/api/projects/{project_id}/reviews/versions/{version_id}/manager-review",
                headers=headers,
            )
            assert manager.status_code == 201, manager.text

        assert [entry[1] for entry in observed] == [39.33, 39.33, 95.33, 95.33, 95.33, 95.33, 95.33], observed
        assert (observed[0][2], observed[0][3]) == ("intermediate", "beginner"), observed
        assert (observed[1][2], observed[1][3]) == ("beginner", "beginner"), observed
        assert (observed[4][2], observed[4][3]) == ("beginner", "intermediate"), observed
        assert (observed[5][2], observed[5][3]) == ("intermediate", "advanced"), observed
        assert (observed[6][2], observed[6][3]) == ("advanced", "advanced"), observed
        next_task = client.get(f"/api/simulations/{simulation_id}/tasks/current", headers=headers)
        assert next_task.status_code == 200, next_task.text
        assert next_task.json()["difficulty"] == "advanced", next_task.json()
        assert "difficulty: advanced" in next_task.json()["description"].lower(), next_task.json()

        progress = client.get(f"/api/simulations/{simulation_id}/progress", headers=headers)
        assert progress.status_code == 200, progress.text
        assert progress.json()["total_tasks"] >= 4, progress.json()
        board = client.get(
            f"/api/projects/{project_id}/board?simulation_id={simulation_id}", headers=headers
        )
        assert board.status_code == 200, board.text
        assert sum(len(column) for column in board.json().values()) == progress.json()["total_tasks"]
        timeline = client.get(f"/api/projects/{project_id}/timeline", headers=headers)
        assert timeline.status_code == 200, timeline.text
        assert any(item["kind"] == "task_evaluated" for item in timeline.json())
        assert any(item["kind"] == "adaptive_decision" for item in timeline.json())

        analytics = client.get(f"/api/intelligence/analytics?simulation_id={simulation_id}", headers=headers)
        assert analytics.status_code == 200, analytics.text
        assert analytics.json()["summary"]["technical_score"] == observed[-1][1]
        assert analytics.json()["latest_adaptation"]["next_difficulty"] == "advanced"

        admin_id = f"admin-{uuid.uuid4().hex}"
        admin_db = SessionLocal()
        try:
            admin_db.add(User(email=f"{admin_id}@example.test", password_hash="test-only", full_name="Test Admin", role="admin", student_id=admin_id))
            admin_db.commit()
        finally:
            admin_db.close()
        admin_headers = {"Authorization": f"Bearer {create_access_token(student_id=admin_id, role='admin')}"}
        monitored = client.get(f"/api/admin/students/{student_id}", headers=admin_headers)
        assert monitored.status_code == 200, monitored.text
        detail = monitored.json()
        assert detail["simulation_summary"]["technical_score"] == observed[-1][1]
        assert detail["internship"]["difficulty"] == "advanced"
        assert detail["internship"]["latest_task"]
        assert detail["internship"]["latest_evaluation_status"] == "completed"
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
