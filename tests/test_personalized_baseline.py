"""Personalization, validation, and lifecycle coverage for baseline assessments."""
import hashlib

import pytest

from app.models.assessment import AssessmentAttempt
from app.services import assessment_service as service
from app.services.assessment_service import replace_student_skills, start_attempt, submit_attempt
from app.services.intelligence_service import get_or_create_profile
from app.services.technical_domain_catalog import get_technical_domain
from app.services.technical_profile_service import save_technical_domain_ratings


def _profile(db, student_id, role, skills):
    profile = get_or_create_profile(db, student_id)
    profile.role = role
    db.add(profile)
    replace_student_skills(db, student_id, skills)
    domain_id = "data_analysis_qa_testing" if "data" in role.lower() else "software_development"
    domain = get_technical_domain(domain_id)
    ratings_by_name = {item["name"].casefold(): item["self_rating"] for item in skills}
    save_technical_domain_ratings(
        db,
        student_id,
        domain_id,
        [
            {"technology_id": item.id, "self_rating": ratings_by_name.get(item.name.casefold(), 3)}
            for item in domain.technologies
        ],
    )
    db.commit()


def _force_baseline_fallback(monkeypatch):
    class OfflineBaselineGenerator:
        async def generate_json(self, *_):
            raise RuntimeError("deterministic test fallback")

    monkeypatch.setattr(service, "get_ai_service", OfflineBaselineGenerator)


def _valid_ai_questions():
    domain = get_technical_domain("software_development")
    technologies = {item.id: item.name for item in domain.technologies}
    concepts = [
        ("python", "how do data structures affect algorithm complexity?"),
        ("java", "how would encapsulation assign responsibilities between classes?"),
        ("fastapi", "how should an API endpoint handle request validation errors across a service boundary?"),
        ("sql", "how do schema and join semantics affect query results?"),
        ("mongodb", "how would you choose document references to preserve consistency across related records?"),
        ("node_js", "how would you separate request handling from persistence when designing a REST API?"),
        ("javascript", "how do type systems and function boundaries affect algorithm behavior?"),
    ]
    return {"questions": [
        {"prompt": f"In {technologies[technology_id]}, {prompt}", "competency": "technical_knowledge", "question_type": "short_answer", "options": [], "rubric_keywords": ["validation", "evidence"], "difficulty": "intermediate"}
        for technology_id, prompt in concepts
    ]}


def test_domain_specific_profiles_shape_fallback_questions(db, mock_student, monkeypatch):
    _force_baseline_fallback(monkeypatch)
    backend_id = mock_student["student_id"]
    data_id = f"data_{backend_id}"
    _profile(db, backend_id, "Backend Engineer", [{"name": "FastAPI", "self_rating": 1}, {"name": "SQL", "self_rating": 5}])
    _profile(db, data_id, "Data Scientist", [{"name": "Python", "self_rating": 1}, {"name": "SQL", "self_rating": 4}])
    backend = start_attempt(db, backend_id)
    data = start_attempt(db, data_id)
    backend_text = " ".join(q["prompt"] for q in backend["questions"]).lower()
    data_text = " ".join(q["prompt"] for q in data["questions"]).lower()
    assert "api endpoint" in backend_text and "classification" not in backend_text
    assert "correlation" in data_text and "data frame" in data_text
    assert "fastapi" in backend_text and "pandas" in data_text
    assert backend["generation"]["source"] == data["generation"]["source"] == "fallback"
    assert all(q["question_type"] != "mcq" for q in backend["questions"] + data["questions"])


def test_same_domain_and_ratings_get_deterministic_fallback_questions(db, mock_student, monkeypatch):
    _force_baseline_fallback(monkeypatch)
    ids = [f"same_role_{mock_student['student_id']}_{i}" for i in range(100)]
    ids.sort(key=lambda sid: int(hashlib.sha256(sid.encode()).hexdigest()[:8], 16) % 4)
    first_id, second_id = ids[0], ids[-1]
    for student_id in (first_id, second_id):
        _profile(db, student_id, "Backend Engineer", [{"name": "Python", "self_rating": 3}])
    first, second = start_attempt(db, first_id), start_attempt(db, second_id)
    assert [q["prompt"] for q in first["questions"]] == [q["prompt"] for q in second["questions"]]


def test_low_self_rated_skill_changes_assessment_focus(db, mock_student, monkeypatch):
    _force_baseline_fallback(monkeypatch)
    first_id, second_id = f"rating_a_{mock_student['student_id']}", f"rating_b_{mock_student['student_id']}"
    _profile(db, first_id, "Backend Engineer", [{"name": "Python", "self_rating": 1}, {"name": "FastAPI", "self_rating": 5}])
    _profile(db, second_id, "Backend Engineer", [{"name": "Python", "self_rating": 5}, {"name": "FastAPI", "self_rating": 1}])
    first, second = start_attempt(db, first_id), start_attempt(db, second_id)
    assert "python" in first["questions"][0]["prompt"].lower()
    assert "fastapi" in " ".join(q["prompt"] for q in second["questions"]).lower()


def test_valid_ai_questions_are_persisted_and_returned(db, mock_student, monkeypatch):
    class AI:
        async def generate_json(self, *_):
            return _valid_ai_questions()
    monkeypatch.setattr(service, "get_ai_service", lambda: AI())
    _profile(db, mock_student["student_id"], "Python Backend Engineer", [{"name": "Python", "self_rating": 4}])
    result = start_attempt(db, mock_student["student_id"])
    assert result["generation"]["source"] == "ai"
    assert [q["prompt"] for q in result["questions"]] == [q["prompt"] for q in _valid_ai_questions()["questions"]]


@pytest.mark.parametrize("response", [None, {"questions": [{"prompt": "A short question", "competency": "communication"}] * 7}])
def test_malformed_ai_uses_domain_aware_fallback(db, mock_student, monkeypatch, response):
    class AI:
        async def generate_json(self, *_):
            if response is None:
                raise TimeoutError("provider timeout")
            return response
    monkeypatch.setattr(service, "get_ai_service", lambda: AI())
    _profile(db, mock_student["student_id"], "Data Scientist", [{"name": "Python", "self_rating": 2}])
    result = start_attempt(db, mock_student["student_id"])
    assert result["generation"]["source"] == "fallback"
    assert "correlation" in " ".join(q["prompt"] for q in result["questions"]).lower()
    assert not any(q["competency"] in {"communication", "professionalism"} for q in result["questions"])


def test_retry_reuses_active_attempt_and_completed_attempt_can_restart(db, mock_student, baseline_evaluator):
    first = start_attempt(db, mock_student["student_id"])
    retry = start_attempt(db, mock_student["student_id"])
    assert retry["id"] == first["id"]
    answers = [{"question_id": q["id"], "response": "I would validate input, test boundaries, inspect errors, and verify stored data."} for q in first["questions"]]
    result = submit_attempt(db, mock_student["student_id"], first["id"], answers)
    assert result["overall_score"] >= 0 and "competencies" in result
    assert result["profile"]["baseline_competencies"] == result["competencies"]
    second = start_attempt(db, mock_student["student_id"])
    assert second["id"] != first["id"]
    assert db.query(AssessmentAttempt).filter_by(student_id=mock_student["student_id"]).count() == 2
