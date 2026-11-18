"""
Comprehensive Live Integration and Demonstration Verification Script.
Executes against the real PostgreSQL test database (ai_internship_test).
Does not modify any development data.
"""
import os
import sys
import uuid
import json

# Ensure backend is on PYTHONPATH
sys.path.insert(0, os.path.abspath("backend"))

os.environ["TEST_DATABASE_URL"] = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/ai_internship_test"
os.environ["DATABASE_URL"] = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/ai_internship_test"
os.environ["ENVIRONMENT"] = "test"
os.environ["JWT_SECRET_KEY"] = "change-me-to-the-shared-signing-secret"

from fastapi.testclient import TestClient
from sqlalchemy import text
from app.core.database import engine, SessionLocal, Base
from app.main import app

def init_db():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS uq_workflow_events_one_requirement_change"))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_events_one_open_requirement_change
            ON workflow_events (simulation_id, task_id)
            WHERE event_type = 'REQUIREMENT_CHANGE'
              AND status IN ('pending', 'acknowledged')
              AND task_id IS NOT NULL
        """))
        from app.models.enums import ReviewStatus
        for value in ReviewStatus:
            conn.execute(text(f"ALTER TYPE ai_review_status_enum ADD VALUE IF NOT EXISTS '{value.value}'"))
        conn.execute(text("ALTER TYPE event_type_enum ADD VALUE IF NOT EXISTS 'SPRINT_COMPLETED'"))
        conn.execute(text("ALTER TYPE event_type_enum ADD VALUE IF NOT EXISTS 'sprint_completed'"))

def test_workflow():
    init_db()
    client = TestClient(app)
    results = {}
    print("=== Starting Live E2E Student Lifecycle Verification ===")
    
    # 1. Registration
    email = f"e2e_student_{uuid.uuid4().hex[:6]}@example.com"
    password = "SecurePassword123!"
    reg_resp = client.post("/api/auth/register", json={
        "email": email,
        "password": password,
        "full_name": "Alex Mercer",
        "role": "student"
    })
    print(f"1. Registration: status={reg_resp.status_code}")
    assert reg_resp.status_code in (200, 201), reg_resp.text
    token = reg_resp.json().get("access_token")
    headers = {"Authorization": f"Bearer {token}"}
    
    # Verify password hash in DB
    with SessionLocal() as session:
        user_row = session.execute(text("SELECT id, email, password_hash, role FROM users WHERE email = :e"), {"e": email}).fetchone()
        assert user_row is not None
        assert user_row[2].startswith("$2b$") or user_row[2].startswith("$2a$"), f"Password hash is not bcrypt: {user_row[2]}"
        student_id = str(user_row[0])
        print(f"   DB check: user {user_row[1]} registered with bcrypt hash ({user_row[2][:15]}...)")
    
    # 2. Login
    login_resp = client.post("/api/auth/login", data={"username": email, "password": password})
    print(f"2. Login: status={login_resp.status_code}")
    assert login_resp.status_code == 200
    
    # 3. Technical domain selection
    domain_resp = client.post("/api/students/me/technical-domain", headers=headers, json={
        "domain_id": "software_development",
        "ratings": [
            {"technology_id": "python", "self_rating": 4},
            {"technology_id": "fastapi", "self_rating": 3},
            {"technology_id": "postgresql", "self_rating": 3},
            {"technology_id": "react", "self_rating": 2}
        ]
    })
    print(f"3. Technical Domain Selection: status={domain_resp.status_code}")
    assert domain_resp.status_code == 200
    
    # 4. Baseline Assessment
    start_resp = client.post("/api/assessments/baseline/start", headers=headers)
    print(f"4a. Baseline Assessment Start: status={start_resp.status_code}")
    assert start_resp.status_code in (200, 201)
    attempt_data = start_resp.json()
    attempt_id = attempt_data["id"]
    questions = attempt_data["questions"]
    
    # Answer questions
    answers = []
    for q in questions:
        q_id = q["id"]
        q_type = q["question_type"]
        if q_type == "multiple_choice":
            answers.append({"question_id": q_id, "selected_option": q.get("options", [0])[0]})
        else:
            answers.append({
                "question_id": q_id,
                "text_answer": "I would follow clean code practices, write unit tests, handle errors gracefully, and document API contracts."
            })
    
    submit_resp = client.post(f"/api/assessments/baseline/attempts/{attempt_id}/submit", headers=headers, json={"answers": answers})
    print(f"4b. Baseline Assessment Submit: status={submit_resp.status_code}")
    assert submit_resp.status_code in (200, 201)
    eval_data = submit_resp.json().get("evaluation", {})
    overall_score = eval_data.get("overall_score")
    print(f"   Baseline overall score: {overall_score}")
    
    # 5. Internship Generation
    gen_resp = client.post("/api/generate", headers=headers, json={
        "student_id": student_id,
        "track": "backend",
        "experience_level": "intermediate"
    })
    print(f"5. Internship Generation: status={gen_resp.status_code}")
    assert gen_resp.status_code in (200, 201)
    sim_data = gen_resp.json()
    simulation_id = sim_data["simulation_id"]
    company_id = sim_data["company"]["id"]
    project_id = sim_data["project"]["id"]
    print(f"   Generated simulation={simulation_id}, company={company_id}, project={project_id}")
    
    # 6. Verify 4 Sprints created in DB
    with SessionLocal() as session:
        sprints = session.execute(text("SELECT id, sprint_number, status, goal FROM sprints WHERE simulation_id = :s ORDER BY sprint_number"), {"s": simulation_id}).fetchall()
        print(f"6. 4-Sprint Verification: count={len(sprints)}")
        assert len(sprints) == 4
        for s in sprints:
            print(f"   Sprint {s[1]}: status={s[2]}, goal={s[3]}")
    
    # 7. Start Simulation & Task Assignment
    start_sim_resp = client.post(f"/api/simulations/{simulation_id}/start", headers=headers)
    print(f"7a. Simulation Start: status={start_sim_resp.status_code}")
    assert start_sim_resp.status_code == 200
    
    curr_task_resp = client.get(f"/api/simulations/{simulation_id}/tasks/current", headers=headers)
    print(f"7b. Current Task fetch: status={curr_task_resp.status_code}")
    assert curr_task_resp.status_code == 200
    current_task = curr_task_resp.json()
    task_id = current_task["id"]
    print(f"   Current Task: id={task_id}, title={current_task.get('title')}, status={current_task.get('status')}")
    
    start_task_resp = client.post(f"/api/simulations/{simulation_id}/tasks/{task_id}/start", headers=headers)
    print(f"7c. Start Task: status={start_task_resp.status_code}")
    assert start_task_resp.status_code == 200
    
    # 8. Workplace Scenario Interaction (Test all 6 scenarios)
    print("8. Testing Workplace Scenarios...")
    scenarios = ["requirement_change", "collaboration", "time_management", "prioritization", "ownership", "feedback_response"]
    for sc in scenarios:
        trig_resp = client.post(f"/api/simulations/{simulation_id}/scenarios/trigger", headers=headers, json={
            "scenario_type": sc,
            "task_id": task_id
        })
        print(f"   Scenario '{sc}' trigger: status={trig_resp.status_code}")
        assert trig_resp.status_code in (200, 201), trig_resp.text
        trig_data = trig_resp.json()
        event_id = trig_data.get("event_id")
        
        # Student responds to scenario
        act_resp = client.post(f"/api/simulations/{simulation_id}/scenarios/{event_id}/respond", headers=headers, json={
            "action": "collaborate_and_adapt",
            "message": "I understand the situation and will coordinate with the team to prioritize high-value deliverables while maintaining quality."
        })
        print(f"   Scenario '{sc}' response: status={act_resp.status_code}")
        assert act_resp.status_code in (200, 200), act_resp.text
        
        # Verify DB records
        with SessionLocal() as session:
            ev = session.execute(text("SELECT event_type, status FROM workflow_events WHERE id = :id"), {"id": event_id}).fetchone()
            assert ev is not None
            assert ev[1] in ("resolved", "acknowledged", "completed", "in_progress")
            
            # Check soft skill evidence
            evidence = session.execute(text("SELECT competency, score, rationale FROM soft_skill_evidence WHERE simulation_id = :s ORDER BY created_at DESC LIMIT 1"), {"s": simulation_id}).fetchone()
            assert evidence is not None
            print(f"   -> DB Evidence logged: competency={evidence[0]}, score={evidence[1]}, rationale={evidence[2][:40]}...")

    # 9. Deliverable Submission
    sub_resp = client.post(f"/api/simulations/{simulation_id}/tasks/{task_id}/submit", headers=headers, json={
        "submission_type": "code_snippet",
        "content": "def calculate_totals(items):\n    return sum(item['price'] * item['quantity'] for item in items)\n",
        "notes": "Implemented calculate_totals with full test coverage and type hints."
    })
    print(f"9. Deliverable Submission: status={sub_resp.status_code}")
    assert sub_resp.status_code in (200, 201)
    sub_data = sub_resp.json()
    submission_id = sub_data.get("submission_id") or sub_data.get("id")
    
    # 10. Submission Evaluation
    eval_task_resp = client.post(f"/api/simulations/{simulation_id}/tasks/{task_id}/evaluate", headers=headers)
    print(f"10. Submission Evaluation: status={eval_task_resp.status_code}")
    assert eval_task_resp.status_code in (200, 201)
    review_data = eval_task_resp.json()
    print(f"    Evaluation result: status={review_data.get('status')}, score={review_data.get('score', review_data.get('overall_score'))}")
    
    # 11. Competency Score Updates
    comp_resp = client.get(f"/api/simulations/{simulation_id}/intelligence/competencies", headers=headers)
    print(f"11. Competency Scores Fetch: status={comp_resp.status_code}")
    assert comp_resp.status_code == 200
    comp_data = comp_resp.json()
    print(f"    Competencies returned: {len(comp_data.get('competencies', []))} items")
    
    # 12. AI Mentor Interaction
    mentor_resp = client.post(f"/api/simulations/{simulation_id}/chat/messages", headers=headers, json={
        "recipient": "mentor",
        "message": "Hi Mentor, can you give me feedback on my code architecture and advice for sprint 2?"
    })
    print(f"12. AI Mentor Interaction: status={mentor_resp.status_code}")
    assert mentor_resp.status_code in (200, 201)
    mentor_msg = mentor_resp.json()
    print(f"    Mentor response: {mentor_msg.get('reply', mentor_msg.get('content', mentor_msg.get('message', ''))[:60])}...")
    
    # 13. Student Readiness Snapshot
    readiness_resp = client.get(f"/api/simulations/{simulation_id}/intelligence/readiness", headers=headers)
    print(f"13. Student Readiness Snapshot: status={readiness_resp.status_code}")
    assert readiness_resp.status_code == 200
    readiness_data = readiness_resp.json()
    print(f"    Readiness score: {readiness_data.get('readiness_score')}, band={readiness_data.get('readiness_band')}")
    
    # 14. Subsequent Task Difficulty Adjustment
    adapt_resp = client.get(f"/api/simulations/{simulation_id}/intelligence/adaptation", headers=headers)
    print(f"14. Subsequent Task Difficulty Adjustment: status={adapt_resp.status_code}")
    assert adapt_resp.status_code == 200
    adapt_data = adapt_resp.json()
    print(f"    Adaptation recommendations: {adapt_data.get('recommendation', adapt_data.get('current_difficulty'))}")
    
    print("\n=== ALL 14 STAGES OF REAL E2E STUDENT LIFECYCLE PASSED! ===")
    return {
        "student_id": student_id,
        "email": email,
        "token": token,
        "simulation_id": simulation_id,
        "task_id": task_id
    }

if __name__ == "__main__":
    test_workflow()
