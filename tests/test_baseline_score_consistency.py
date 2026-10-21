"""Regression checks for baseline totals and evidence-backed competencies."""
import json
import uuid

import pytest
from fastapi import HTTPException

from app.models.assessment import AssessmentAttempt
from app.services import assessment_service as service
from app.services.assessment_service import (
    baseline_difficulty,
    calculate_baseline_results,
    latest_result,
    start_attempt,
    submit_attempt,
)
from app.services.intelligence_service import COMPETENCIES


def test_equal_weight_question_total_is_arithmetic_mean_and_unrounded():
    values = [95, 90, 95, 90, 95, 90, 95]
    results = calculate_baseline_results([
        {"score": value, "competency": "technical_knowledge"}
        for value in values
    ])
    assert results["overall_score"] == pytest.approx(92.85714285714286)
    assert round(results["overall_score"], 2) == 92.86


def test_competencies_use_only_mapped_question_evidence():
    results = calculate_baseline_results([
        {"score": 95, "competency": "technical_knowledge"},
        {"score": 85, "competency": "technical_knowledge"},
        {"score": 60, "competency": "problem_solving"},
        {"score": 100, "competency": "communication"},
    ])
    assert results["competencies"] == {
        "technical_knowledge": 90,
        "problem_solving": 60,
    }
    assert results["strengths"] == ["Technical Knowledge"]
    assert results["focus_areas"] == ["Problem Solving"]
    assert results["unassessed_competencies"] == [
        key for key in COMPETENCIES if key not in {"technical_knowledge", "problem_solving"}
    ]


def test_baseline_difficulty_uses_existing_thresholds_once():
    assert baseline_difficulty(92.85714285714286) == "advanced"
    assert baseline_difficulty(77.999) == "intermediate"
    assert baseline_difficulty(57.999) == "beginner"


def _answers(attempt):
    return [
        {"question_id": question["id"], "response": "B" if question["question_type"] == "mcq" else "Technical validation and regression testing."}
        for question in attempt["questions"]
    ]


def test_submit_response_persisted_total_summary_and_difficulty_agree(db, mock_student, baseline_evaluator):
    attempt = start_attempt(db, mock_student["student_id"])
    answer_payload = _answers(attempt)
    result = submit_attempt(db, mock_student["student_id"], attempt["id"], answer_payload)
    question_scores = [answer["score"] for answer in result["evaluation"]["answers"]]
    expected = sum(question_scores) / len(question_scores)
    stored_attempt = db.get(AssessmentAttempt, uuid.UUID(attempt["id"]))

    assert result["overall_score"] == pytest.approx(expected)
    assert stored_attempt.overall_score == pytest.approx(expected)
    assert result["evaluation"]["overall_score"] == pytest.approx(expected)
    assert result["profile"]["assessment_summary"] == (
        f"Baseline assessment completed with an average question score of {expected:.2f}/100."
    )
    assert result["profile"]["recommended_parameters"]["assessment_overall_score"] == pytest.approx(expected)
    assert result["profile"]["recommended_difficulty"] == baseline_difficulty(expected)
    assert stored_attempt.competencies == result["competencies"]
    if all(score >= 70 for score in result["competencies"].values()):
        assert result["profile"]["recommended_parameters"]["focus_competencies"] == []


def test_unanswered_questions_are_rejected_and_do_not_complete_attempt(db, mock_student, baseline_evaluator):
    attempt = start_attempt(db, mock_student["student_id"])
    with pytest.raises(HTTPException) as incomplete:
        submit_attempt(db, mock_student["student_id"], attempt["id"], _answers(attempt)[:-1])
    assert incomplete.value.status_code == 400
    with pytest.raises(HTTPException) as blank:
        submit_attempt(db, mock_student["student_id"], attempt["id"], [
            *(_answers(attempt)[:-1]),
            {"question_id": attempt["questions"][-1]["id"], "response": "   "},
        ])
    assert blank.value.status_code == 400
    assert db.get(AssessmentAttempt, uuid.UUID(attempt["id"])).status == "in_progress"


def test_new_attempt_results_do_not_include_previous_attempt_answers(db, mock_student, monkeypatch):
    class AttemptScoringEvaluator:
        provider_name = "test-provider"
        model = "attempt-specific"

        async def generate_json(self, prompt, system_prompt=None):
            payload = json.loads(prompt.split("Input JSON follows:\n", 1)[1].split("\n", 1)[0])
            return {"evaluations": [
                {
                    "question_id": item["question_id"],
                    "score": 30 if "first attempt" in item["student_answer"] else 95,
                    "explanation": "Score reflects this attempt's own response.",
                    "feedback": "Review the evidence for this answer.",
                }
                for item in payload["items"] if item["evaluate_with_ai"]
            ]}

    monkeypatch.setattr(service, "get_ai_service", AttemptScoringEvaluator)
    first = start_attempt(db, mock_student["student_id"])
    first_answers = _answers(first)
    for answer in first_answers:
        answer["response"] += " first attempt"
    first_result = submit_attempt(db, mock_student["student_id"], first["id"], first_answers)
    second = start_attempt(db, mock_student["student_id"])
    second_answers = _answers(second)
    for answer in second_answers:
        answer["response"] += " second attempt"
    second_result = submit_attempt(db, mock_student["student_id"], second["id"], second_answers)

    latest = latest_result(db, mock_student["student_id"])
    assert first["id"] != second["id"]
    assert second_result["attempt_id"] == second["id"]
    assert latest["attempt_id"] == second["id"]
    assert latest["overall_score"] == pytest.approx(second_result["overall_score"])
    assert latest["evaluation"]["answers"] == second_result["evaluation"]["answers"]
    assert latest["overall_score"] != pytest.approx(first_result["overall_score"])
