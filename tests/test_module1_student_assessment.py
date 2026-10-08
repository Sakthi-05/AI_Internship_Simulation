"""Module 1: registration, profile, skills, question bank, and baseline assessment."""
from jose import jwt

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.user import User


def _admin_headers():
    # Admin authorization now checks the persisted role as well as the JWT.
    db = SessionLocal()
    try:
        account = db.query(User).filter(User.student_id == "admin_module1").first()
        if account is None:
            db.add(User(email="admin_module1@example.com", password_hash="test-only", full_name="Test Admin", role="admin", student_id="admin_module1"))
            db.commit()
    finally:
        db.close()
    token = jwt.encode(
        {"sub": "admin_module1", "student_id": "admin_module1", "role": "admin"},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


def test_register_login_and_reject_duplicates(client):
    payload = {
        "email": "module1.student@example.com",
        "password": "student-pass",
        "full_name": "Module One",
    }
    created = client.post("/api/auth/register", json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["role"] == "student"
    assert body["access_token"]
    assert body["student_id"]

    duplicate = client.post("/api/auth/register", json=payload)
    assert duplicate.status_code == 409

    bad = client.post("/api/auth/login", json={"email": payload["email"], "password": "wrong-pass"})
    assert bad.status_code == 401

    logged = client.post("/api/auth/login", json={"email": payload["email"], "password": payload["password"]})
    assert logged.status_code == 200
    token = logged.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == payload["email"]
    assert me.json()["full_name"] == "Module One"


def test_profile_skills_assessment_and_recommendation(client):
    created = client.post(
        "/api/auth/register",
        json={"email": "module1.assess@example.com", "password": "student-pass", "full_name": "Asha Rao"},
    )
    assert created.status_code == 201, created.text
    headers = {"Authorization": f"Bearer {created.json()['access_token']}"}

    blocked = client.get("/api/students/me/recommendation", headers=headers)
    assert blocked.status_code == 409

    updated = client.put(
        "/api/students/me",
        headers=headers,
        json={
            "full_name": "Asha Rao",
            "domain": "startup",
            "role": "Backend Developer",
            "college": "Example Institute",
            "degree": "B.Tech CSE",
            "year_of_study": 3,
            "career_goal": "Backend internship",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["profile_complete"] is True
    assert updated.json()["college"] == "Example Institute"

    catalog = client.get("/api/skills", headers=headers)
    assert catalog.status_code == 200
    names = {item["name"] for item in catalog.json()["skills"]}
    assert "Python" in names

    saved = client.put(
        "/api/students/me/skills",
        headers=headers,
        json={"skills": [{"name": "Python", "self_rating": 4}, {"name": "FastAPI", "self_rating": 3}]},
    )
    assert saved.status_code == 200, saved.text
    assert {item["name"] for item in saved.json()["skills"]} == {"Python", "FastAPI"}

    unknown = client.put(
        "/api/students/me/skills",
        headers=headers,
        json={"skills": [{"name": "Not A Real Skill", "self_rating": 2}]},
    )
    assert unknown.status_code == 400

    started = client.post("/api/assessments/start", headers=headers)
    assert started.status_code == 200, started.text
    attempt = started.json()
    assert attempt["questions"]
    assert "correct_option" not in attempt["questions"][0]

    forbidden = client.get("/api/admin/questions", headers=headers)
    assert forbidden.status_code == 403

    bank = client.get("/api/admin/questions", headers=_admin_headers())
    assert bank.status_code == 200, bank.text
    by_id = {item["id"]: item for item in bank.json()["questions"]}
    answers = []
    for question in attempt["questions"]:
        source = by_id[question["id"]]
        if source["question_type"] == "mcq":
            answers.append({"question_id": question["id"], "response": source["correct_option"]})
        else:
            answers.append({"question_id": question["id"], "response": "Thanks, I agree and will fix it, add a test, and send it back for review. I am blocked on the API and the impact is the next step."})

    submitted = client.post(f"/api/assessments/{attempt['id']}/submit", headers=headers, json={"answers": answers})
    assert submitted.status_code == 200, submitted.text
    result = submitted.json()
    assert result["overall_score"] >= 70
    assert "problem_solving" in result["competencies"]
    assert result["profile"]["recommended_parameters"]["role"] == "Backend Developer"
    assert "Python" in result["profile"]["recommended_parameters"]["technology_stack"]

    recommendation = client.get("/api/students/me/recommendation", headers=headers)
    assert recommendation.status_code == 200
    assert recommendation.json()["recommendation"]["company_type"] == "startup"
    assert recommendation.json()["recommendation"]["difficulty"] in {"beginner", "intermediate", "advanced", "expert"}

    again = client.post(f"/api/assessments/{attempt['id']}/submit", headers=headers, json={"answers": answers})
    assert again.status_code == 409
