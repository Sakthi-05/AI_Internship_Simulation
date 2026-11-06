"""Database-backed checks for snapshot persistence and legacy-safe API output."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.assessment import AssessmentAttempt, AssessmentQuestion
from app.models.intelligence import StudentProfile
from app.services import assessment_service
from app.services.assessment_service import start_attempt, submit_attempt
from app.services.technical_domain_catalog import get_technical_domain
from app.services.technical_profile_service import save_technical_domain_ratings


def _select_domain(db, student_id):
    domain = get_technical_domain("software_development")
    save_technical_domain_ratings(
        db,
        student_id,
        domain.id,
        [{"technology_id": item.id, "self_rating": 3} for item in domain.technologies],
    )
    db.commit()


def test_new_attempt_snapshot_survives_question_edits_and_completes_atomically(db, mock_student, monkeypatch, baseline_evaluator):
    student_id = mock_student["student_id"]
    _select_domain(db, student_id)

    class FailingGenerator:
        async def generate_json(self, *_):
            raise ValueError("deterministic fallback path")

    monkeypatch.setattr(assessment_service, "get_ai_service", FailingGenerator)
    started = start_attempt(db, student_id)
    attempt = db.get(AssessmentAttempt, uuid.UUID(started["id"]))
    assert attempt.assessment_snapshot["schema_version"] == assessment_service.BASELINE_SNAPSHOT_SCHEMA_VERSION
    assert len(attempt.assessment_snapshot["questions"]) == 7
    seed = attempt.assessment_snapshot["generation"]["attempt_seed"]
    assert seed
    assert seed not in str(started["generation"])
    original = {item["question_id"]: dict(item) for item in attempt.assessment_snapshot["questions"]}

    rows = db.execute(
        select(AssessmentQuestion).where(AssessmentQuestion.id.in_([uuid.UUID(key) for key in original]))
    ).scalars().all()
    for row in rows:
        row.prompt = "Administrator edited this after the attempt began."
        if row.question_type == "mcq":
            other = next(option["id"] for option in row.options if option["id"] != row.correct_option)
            row.correct_option = other
        else:
            row.rubric_keywords = ["post-attempt edit"]
    db.commit()
    monkeypatch.setattr(assessment_service, "get_ai_service", baseline_evaluator)

    reloaded = start_attempt(db, student_id)
    assert [item["prompt"] for item in reloaded["questions"]] == [
        item["prompt"] for item in attempt.assessment_snapshot["questions"]
    ]
    answers = []
    for public in reloaded["questions"]:
        frozen = original[public["id"]]
        answers.append({
            "question_id": public["id"],
            "response": frozen["correct_option"] if public["question_type"] == "mcq" else "A reasoned response with evidence and validation.",
        })
    result = submit_attempt(db, student_id, uuid.UUID(started["id"]), answers)
    assert all(
        answer["score"] == assessment_service.BASELINE_MCQ_CORRECT_SCORE
        for answer in result["evaluation"]["answers"] if answer["question_type"] == "mcq"
    )
    db.commit()
    completed = db.get(AssessmentAttempt, uuid.UUID(started["id"]))
    completion = completed.assessment_snapshot["completion"]
    assert completion["overall_score"] == result["overall_score"] == completed.overall_score
    assert completion["competencies"] == result["competencies"] == completed.competencies
    assert completion["strengths"] == result["profile"]["strengths"]
    assert completion["focus_areas"] == result["profile"]["weaknesses"]
    assert completion["difficulty"] == result["profile"]["recommended_difficulty"]
    assert completion["scoring_policy_version"] == assessment_service.BASELINE_SCORING_POLICY_VERSION
    assert completion["evaluation_provenance"] == {
        "provider": result["evaluation"]["provider"],
        "model": result["evaluation"]["model"],
        "evaluated_at": result["evaluation"]["evaluated_at"],
    }
    assert completion["recommendation"] == result["profile"]["recommended_parameters"]


def test_snapshot_failure_rolls_back_new_questions_and_attempt(db, mock_student, monkeypatch):
    student_id = mock_student["student_id"]
    _select_domain(db, student_id)

    class FailingGenerator:
        async def generate_json(self, *_):
            raise ValueError("deterministic fallback path")

    monkeypatch.setattr(assessment_service, "get_ai_service", FailingGenerator)
    monkeypatch.setattr(assessment_service, "_build_assessment_snapshot", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("snapshot failed")))
    assessment_service.ensure_question_bank(db)
    db.commit()
    profile = db.query(StudentProfile).filter_by(student_id=student_id).one()
    profile.role = "outer transaction value"
    db.add(profile)
    question_count_before = db.query(AssessmentQuestion).filter_by(domain="software_development").count()
    with pytest.raises(RuntimeError, match="snapshot failed"):
        start_attempt(db, student_id)
    # A caller may safely continue and commit unrelated outer-transaction work.
    db.commit()
    assert db.query(AssessmentAttempt).filter_by(student_id=student_id, status="in_progress").count() == 0
    assert db.query(AssessmentQuestion).filter_by(domain="software_development").count() == question_count_before
    assert db.query(StudentProfile).filter_by(student_id=student_id).one().role == "outer transaction value"


def test_legacy_null_snapshot_attempt_remains_submittable(db, mock_student, baseline_evaluator):
    student_id = mock_student["student_id"]
    question = AssessmentQuestion(
        prompt="Explain how a request schema validates an API payload.",
        competency="technical_knowledge",
        question_type="short_answer",
        options=[],
        correct_option=None,
        rubric_keywords=["schema", "validation"],
        difficulty="intermediate",
        domain="software",
        active=True,
    )
    db.add(question)
    db.flush()
    attempt = AssessmentAttempt(
        student_id=student_id,
        status="in_progress",
        question_ids=[str(question.id)],
        generation_metadata={},
        assessment_snapshot=None,
        started_at=datetime.now(timezone.utc),
    )
    db.add(attempt)
    db.flush()
    result = submit_attempt(
        db,
        student_id,
        attempt.id,
        [{"question_id": str(question.id), "response": "Validate the payload against a schema."}],
    )
    db.commit()
    assert result["overall_score"] == 82
    assert db.get(AssessmentAttempt, attempt.id).assessment_snapshot is None


def test_student_assessment_api_contract_never_returns_private_snapshot(db, client, mock_student, baseline_evaluator, monkeypatch):
    student_id = mock_student["student_id"]
    _select_domain(db, student_id)
    monkeypatch.setattr(assessment_service, "get_ai_service", type("Generator", (), {
        "generate_json": lambda self, *_: _raise_generation_error()
    }))
    started = client.post("/api/assessments/start", headers=mock_student["headers"])
    assert started.status_code == 200
    assert "assessment_snapshot" not in started.json()
    assert "attempt_seed" not in str(started.json())
    assert all("correct_option" not in item and "rubric_keywords" not in item for item in started.json()["questions"])
    answers = [
        {"question_id": item["id"], "response": "B" if item["question_type"] == "mcq" else "I would validate the behavior and add a regression test."}
        for item in started.json()["questions"]
    ]
    monkeypatch.setattr(assessment_service, "get_ai_service", baseline_evaluator)
    submitted = client.post(
        f"/api/assessments/{started.json()['id']}/submit",
        headers=mock_student["headers"], json={"answers": answers},
    )
    assert submitted.status_code == 200
    assert "assessment_snapshot" not in submitted.json()
    assert "attempt_seed" not in str(submitted.json())
    latest = client.get("/api/assessments/latest", headers=mock_student["headers"])
    assert latest.status_code == 200
    assert "assessment_snapshot" not in latest.json().get("result", {})
    assert "attempt_seed" not in str(latest.json())


def test_history_and_detail_are_paginated_owned_and_snapshot_safe(db, client, mock_student, other_auth_headers):
    student_id = mock_student["student_id"]
    now = datetime.now(timezone.utc)
    older = AssessmentAttempt(
        student_id=student_id, status="completed", question_ids=[], overall_score=61,
        competencies={"technical_knowledge": 61}, started_at=now,
        completed_at=now - timedelta(seconds=1),
        generation_metadata={"evaluation": {"unassessed_competencies": ["adaptability"]}},
        assessment_snapshot=None,
    )
    newer = AssessmentAttempt(
        student_id=student_id, status="completed", question_ids=[], overall_score=88,
        competencies={"problem_solving": 88}, started_at=now, completed_at=now,
        generation_metadata={"evaluation": {
            "status": "completed", "unassessed_competencies": [],
            "answers": [{"question_id": "q", "question": "Explain", "response": "Stored",
                         "score": 88, "feedback": "Good", "correct_option": "A"}],
            "private_prompt": "must-not-escape",
        }},
        assessment_snapshot={
            "technical_domain_id": "software_development",
            "technology_ratings": [{"technology_id": "python", "self_rating": 2}],
            "generation": {"attempt_seed": "private-seed"},
            "questions": [{"correct_option": "A", "rubric_keywords": ["private"]}],
            "completion": {"strengths": ["problem_solving"], "focus_areas": [],
                           "difficulty": "intermediate", "recommendation": {"kind": "practice"}},
        },
    )
    foreign = AssessmentAttempt(
        student_id=f"unrelated_{uuid.uuid4().hex}", status="completed", question_ids=[], overall_score=99,
        competencies={}, started_at=now, completed_at=now,
    )
    in_progress = AssessmentAttempt(
        student_id=student_id, status="in_progress", question_ids=[], overall_score=None,
        competencies={}, started_at=now,
    )
    db.add_all([older, newer, foreign, in_progress])
    db.commit()

    empty = client.get("/api/assessments/history", headers=other_auth_headers, params={"limit": 1})
    assert empty.status_code == 200
    assert empty.json() == {"items": [], "total": 0, "limit": 1, "offset": 0, "has_more": False}

    page = client.get("/api/assessments/history", headers=mock_student["headers"], params={"limit": 1, "offset": 0})
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert page.json()["has_more"] is True
    assert page.json()["items"][0]["attempt_id"] == str(newer.id)
    assert page.json()["items"][0]["technical_domain_id"] == "software_development"
    assert page.json()["items"][0]["technology_ratings"][0]["self_rating"] == 2
    assert "private-seed" not in str(page.json())
    assert "correct_option" not in str(page.json())
    assert "private_prompt" not in str(page.json())
    assert client.get("/api/assessments/history?limit=0", headers=mock_student["headers"]).status_code == 422
    assert client.get("/api/assessments/history?offset=-1", headers=mock_student["headers"]).status_code == 422

    second_page = client.get("/api/assessments/history", headers=mock_student["headers"], params={"limit": 1, "offset": 1})
    assert second_page.json()["items"][0]["historical_data_status"] == "legacy"
    assert second_page.json()["items"][0]["technical_domain_id"] is None
    assert second_page.json()["items"][0]["technology_ratings"] is None

    # Editing the live technical profile must not rewrite the completed attempt snapshot.
    replacement_domain = get_technical_domain("debugging_code_review")
    save_technical_domain_ratings(
        db,
        student_id,
        replacement_domain.id,
        [{"technology_id": item.id, "self_rating": 5} for item in replacement_domain.technologies],
    )
    db.commit()

    detail = client.get(f"/api/assessments/{newer.id}", headers=mock_student["headers"])
    assert detail.status_code == 200
    assert detail.json()["overall_score"] == 88
    assert detail.json()["recommendation"] == {"kind": "practice"}
    assert detail.json()["technical_domain_id"] == "software_development"
    assert detail.json()["technology_ratings"][0]["self_rating"] == 2
    assert "assessment_snapshot" not in detail.json()
    assert "private-seed" not in str(detail.json())
    assert client.get(f"/api/assessments/{foreign.id}", headers=mock_student["headers"]).status_code == 404
    assert client.get(f"/api/assessments/{in_progress.id}", headers=mock_student["headers"]).status_code == 404
    assert client.get(f"/api/assessments/{uuid.uuid4()}", headers=mock_student["headers"]).status_code == 404
    assert client.get("/api/assessments/history").status_code == 401

    # The latest endpoint keeps its established envelope and core keys while adding history fields.
    latest = client.get("/api/assessments/latest", headers=mock_student["headers"])
    assert latest.status_code == 200
    latest_result = latest.json()["result"]
    assert {"attempt_id", "overall_score", "competencies", "unassessed_competencies", "completed_at", "evaluation"} <= set(latest_result)
    assert latest_result["attempt_id"] == str(newer.id)


def _raise_generation_error():
    raise ValueError("deterministic fallback path")
