"""Safe result projection tests that do not require a database."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.services.assessment_service import _assessment_result_projection


def test_snapshot_projection_uses_historical_values_and_redacts_private_data():
    attempt = SimpleNamespace(
        id=uuid4(), status="completed", completed_at=datetime.now(timezone.utc),
        overall_score=76.0, competencies={"problem_solving": 80},
        assessment_snapshot={
            "technical_domain_id": "software_development",
            "technology_ratings": [
                {"technology_id": "python", "self_rating": 4, "private_note": "omit"},
                {"technology_id": "sql", "self_rating": None, "legacy_metadata": True},
                {"technology_id": "missing-rating"},
                {"technology_id": "bad-rating", "self_rating": "5"},
                "malformed-entry",
            ],
            "generation": {"attempt_seed": "must-not-escape", "prompt": "private"},
            "questions": [{"correct_option": "A", "rubric_keywords": ["secret"]}],
            "completion": {
                "strengths": ["problem_solving"], "focus_areas": ["communication"],
                "difficulty": "intermediate", "recommendation": {"summary": "continue"},
            },
        },
        generation_metadata={"evaluation": {
            "status": "completed", "provider": "mock", "model": "unit",
            "unassessed_competencies": ["adaptability"], "internal_prompt": "private",
            "answers": [{"question_id": "q1", "question": "Why?", "response": "Because",
                         "score": 80, "explanation": "Good reasoning", "feedback": "Add tests",
                         "correct_option": "A", "rubric_keywords": ["secret"]}],
        }},
    )
    result = _assessment_result_projection(attempt)
    assert result["technical_domain_id"] == "software_development"
    assert result["technology_ratings"] == [
        {"technology_id": "python", "self_rating": 4},
        {"technology_id": "sql", "self_rating": None},
    ]
    assert result["overall_score"] == 76.0
    assert result["competencies"] == {"problem_solving": 80}
    assert result["unassessed_competencies"] == ["adaptability"]
    assert result["strengths"] == ["problem_solving"]
    assert result["focus_areas"] == ["communication"]
    assert result["recommended_difficulty"] == "intermediate"
    serialized = str(result)
    for private in ("must-not-escape", "internal_prompt", "correct_option", "rubric_keywords", "assessment_snapshot"):
        assert private not in serialized
    assert result["evaluation"]["answers"][0]["feedback"] == "Add tests"


def test_legacy_projection_never_infers_current_profile_values():
    attempt = SimpleNamespace(
        id=uuid4(), completed_at=None, overall_score=60, competencies={"technical_knowledge": 60},
        assessment_snapshot=None,
        generation_metadata={"evaluation": {"status": "completed"}},
    )
    result = _assessment_result_projection(attempt)
    assert result["historical_data_status"] == "legacy"
    assert result["technical_domain_id"] is None
    assert result["technology_ratings"] is None
    assert result["strengths"] is None
    assert result["focus_areas"] is None
    assert result["recommended_difficulty"] is None
    assert result["recommendation"] is None
    assert result["unassessed_competencies"] is None


def test_missing_or_non_list_ratings_remain_unavailable():
    base = {
        "id": uuid4(), "completed_at": None, "overall_score": 50, "competencies": {},
        "assessment_snapshot": {"technical_domain_id": "software_development"},
        "generation_metadata": {},
    }
    assert _assessment_result_projection(SimpleNamespace(**base))["technology_ratings"] is None
    base["assessment_snapshot"] = {
        "technical_domain_id": "software_development", "technology_ratings": {"python": 4}
    }
    assert _assessment_result_projection(SimpleNamespace(**base))["technology_ratings"] is None


def test_rating_list_with_only_malformed_entries_projects_to_empty_list():
    attempt = SimpleNamespace(
        id=uuid4(), completed_at=None, overall_score=50, competencies={},
        assessment_snapshot={"technology_ratings": [None, {"technology_id": "python"}, {"technology_id": "sql", "self_rating": True}]},
        generation_metadata={},
    )
    assert _assessment_result_projection(attempt)["technology_ratings"] == []
