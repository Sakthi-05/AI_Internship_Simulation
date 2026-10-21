"""Database-independent coverage for the baseline scoring policy."""
import pytest

from app.services.assessment_service import (
    baseline_difficulty,
    calculate_baseline_results,
)
from app.services.intelligence_service import COMPETENCIES


def test_question_total_is_an_unrounded_equal_weight_mean():
    scores = [95, 90, 95, 90, 95, 90, 95]
    result = calculate_baseline_results([
        {"score": score, "competency": "technical_knowledge"}
        for score in scores
    ])
    assert result["overall_score"] == pytest.approx(92.85714285714286)
    assert f"{result['overall_score']:.2f}" == "92.86"


def test_competency_aggregation_uses_only_supported_question_mapping():
    result = calculate_baseline_results([
        {"score": 95, "competency": "technical_knowledge"},
        {"score": 85, "competency": "technical_knowledge"},
        {"score": 60, "competency": "problem_solving"},
        {"score": 20, "competency": "communication"},
    ])
    assert result["competencies"] == {"technical_knowledge": 90, "problem_solving": 60}
    assert result["strengths"] == ["Technical Knowledge"]
    assert result["focus_areas"] == ["Problem Solving"]
    assert result["unassessed_competencies"] == [
        key for key in COMPETENCIES if key not in result["competencies"]
    ]


def test_answer_scores_are_validated_and_missing_answers_are_not_scored():
    with pytest.raises(ValueError, match="requires at least one answered question"):
        calculate_baseline_results([])
    with pytest.raises(ValueError, match="numeric values"):
        calculate_baseline_results([{"score": True, "competency": "technical_knowledge"}])


def test_difficulty_uses_the_existing_baseline_thresholds():
    assert baseline_difficulty(92.85714285714286) == "advanced"
    assert baseline_difficulty(77.999) == "intermediate"
    assert baseline_difficulty(57.999) == "beginner"
