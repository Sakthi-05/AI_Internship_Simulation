"""Baseline answer evaluation must use validated provider results."""
import uuid

import pytest
from fastapi import HTTPException

from app.models.assessment import AssessmentAttempt
from app.services import assessment_service as service
from app.services.assessment_service import latest_result, start_attempt, submit_attempt


def _answers(attempt):
    return [{"question_id": item["id"], "response": "B" if item["question_type"] == "mcq" else "I would validate the request, check edge cases, and add a regression test."} for item in attempt["questions"]]


def test_provider_evaluation_is_returned_and_persisted(db, mock_student, baseline_evaluator):
    attempt = start_attempt(db, mock_student["student_id"])
    result = submit_attempt(db, mock_student["student_id"], attempt["id"], _answers(attempt))
    evaluation = result["evaluation"]
    assert evaluation["provider"] == "test-provider"
    assert evaluation["model"] == "deterministic-test"
    assert len(evaluation["answers"]) == len(attempt["questions"])
    short = next(item for item in evaluation["answers"] if item["question_type"] == "short_answer")
    assert short["score"] == 82
    assert "validation" in short["explanation"]
    assert latest_result(db, mock_student["student_id"])["evaluation"] == evaluation


@pytest.mark.parametrize("response", [None, {"evaluations": []}, {"evaluations": [{"question_id": "unexpected", "score": 99, "explanation": "x", "feedback": "y"}]}])
def test_malformed_or_failed_provider_leaves_attempt_incomplete(db, mock_student, monkeypatch, response):
    class BrokenEvaluator:
        provider_name = "test-provider"
        model = "broken"

        async def generate_json(self, *_):
            if response is None:
                raise TimeoutError("simulated provider timeout")
            return response

    monkeypatch.setattr(service, "get_ai_service", BrokenEvaluator)
    attempt = start_attempt(db, mock_student["student_id"])
    with pytest.raises(HTTPException) as error:
        submit_attempt(db, mock_student["student_id"], attempt["id"], _answers(attempt))
    assert error.value.status_code == 503
    stored = db.get(AssessmentAttempt, uuid.UUID(attempt["id"]))
    assert stored.status == "in_progress"
    assert stored.overall_score is None
    assert not (stored.generation_metadata or {}).get("evaluation")


def test_unconfigured_mock_provider_does_not_claim_ai_success(db, mock_student, monkeypatch):
    from app.services.ai.ai_service import MockAIService
    monkeypatch.setattr(service, "get_ai_service", MockAIService)
    attempt = start_attempt(db, mock_student["student_id"])
    with pytest.raises(HTTPException) as error:
        submit_attempt(db, mock_student["student_id"], attempt["id"], _answers(attempt))
    assert error.value.status_code == 503
    assert db.get(AssessmentAttempt, uuid.UUID(attempt["id"])).status == "in_progress"


def test_duplicate_submit_is_rejected_without_changing_saved_evaluation(db, mock_student, baseline_evaluator):
    attempt = start_attempt(db, mock_student["student_id"])
    answers = _answers(attempt)
    result = submit_attempt(db, mock_student["student_id"], attempt["id"], answers)
    with pytest.raises(HTTPException) as error:
        submit_attempt(db, mock_student["student_id"], attempt["id"], answers)
    assert error.value.status_code == 409
    assert latest_result(db, mock_student["student_id"])["evaluation"] == result["evaluation"]


def test_mcq_feedback_is_objective_and_soft_skills_are_not_ai_graded(db, mock_student, baseline_evaluator):
    attempt = start_attempt(db, mock_student["student_id"])
    answers = _answers(attempt)
    result = submit_attempt(db, mock_student["student_id"], attempt["id"], answers)
    for question, evaluated in zip(attempt["questions"], result["evaluation"]["answers"]):
        assert evaluated["question_id"] == question["id"]
        assert evaluated["question_type"] == question["question_type"]
        if question["question_type"] == "mcq":
            assert "matches the answer key" in evaluated["explanation"] or "answer key is" in evaluated["explanation"]
        assert question["competency"] not in {"communication", "professionalism"}
