"""Deterministic unit coverage for private baseline attempt snapshots."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.models.assessment import AssessmentAttempt, AssessmentQuestion
from app.services import assessment_service as service
from app.services.technical_domain_catalog import TECHNICAL_DOMAINS


def _question(domain="software_development", kind="mcq"):
    return AssessmentQuestion(
        id=uuid4(),
        prompt="How should a service boundary validate a request?",
        question_type=kind,
        options=[{"id": "A", "text": "Validate it against the request schema."}] if kind == "mcq" else [],
        correct_option="A" if kind == "mcq" else None,
        rubric_keywords=["schema", "validation"] if kind == "short_answer" else [],
        competency="technical_knowledge",
        difficulty="intermediate",
        domain=domain,
    )


def _profile(domain):
    return {
        "technical_domain_id": domain,
        "technology_ratings": [
            {"technology_id": "python", "self_rating": 2},
            {"technology_id": "sql", "self_rating": None},
        ],
    }


def test_snapshot_preserves_order_exact_question_data_and_profile():
    first, second = _question(), _question(kind="short_answer")
    first.prompt = "Original prompt with exact capitalization."
    first.options = [{"id": "A", "text": "Original option"}, {"id": "B", "text": "Other option"}]
    first.correct_option = "B"
    second.prompt = "Original short-answer prompt."
    second.rubric_keywords = ["original", "rubric"]
    second.competency = "problem_solving"
    second.difficulty = "advanced"
    second.domain = "software_development"
    first._snapshot_generation_details = {
        "source": "ai", "provider": "test-provider", "model": "test-model", "prompt_version": "domain-baseline-v1"
    }
    snapshot = service._build_assessment_snapshot(
        [first, second], _profile("software_development"), source="ai",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc), attempt_seed="seed-for-attempt-1",
    )
    assert snapshot["schema_version"] == service.BASELINE_SNAPSHOT_SCHEMA_VERSION
    assert snapshot["technical_domain_id"] == "software_development"
    assert snapshot["technology_ratings"] == _profile("software_development")["technology_ratings"]
    assert [q["question_id"] for q in snapshot["questions"]] == [str(first.id), str(second.id)]
    assert snapshot["questions"][0]["prompt"] == first.prompt
    assert snapshot["questions"][0]["options"] == first.options
    assert snapshot["questions"][0]["correct_option"] == "B"
    assert snapshot["questions"][1]["rubric_keywords"] == ["original", "rubric"]
    assert snapshot["questions"][1]["competency"] == "problem_solving"
    assert snapshot["questions"][1]["difficulty"] == "advanced"
    assert snapshot["generation"] == {
        "source": "ai", "provider": "test-provider", "model": "test-model",
        "prompt_version": "domain-baseline-v1", "attempt_seed": "seed-for-attempt-1",
    }


def test_snapshot_captures_canonical_domain_for_all_five_domains():
    for domain in TECHNICAL_DOMAINS:
        snapshot = service._build_assessment_snapshot(
            [_question(domain=domain.id)], _profile(domain.id), source="fallback",
            created_at=datetime.now(timezone.utc), attempt_seed="deterministic-test-seed",
        )
        assert snapshot["technical_domain_id"] == domain.id
        assert snapshot["questions"][0]["domain"] == domain.id


def test_snapshot_public_projection_excludes_answer_key_rubric_and_private_snapshot():
    question = _question(kind="short_answer")
    snapshot = service._build_assessment_snapshot(
        [question], _profile("software_development"), source="fallback",
        created_at=datetime.now(timezone.utc), attempt_seed="private-seed",
    )
    attempt = SimpleNamespace(
        id=uuid4(), status="in_progress", started_at=datetime.now(timezone.utc),
        assessment_snapshot=snapshot, generation_metadata={"source": "fallback"},
    )
    payload = service._attempt_payload(None, attempt)
    assert set(payload) == {"id", "status", "started_at", "questions", "generation"}
    assert "correct_option" not in payload["questions"][0]
    assert "rubric_keywords" not in payload["questions"][0]
    assert "assessment_snapshot" not in payload
    assert "private-seed" not in str(payload)
    assert payload["questions"][0]["prompt"] == question.prompt


def test_snapshot_question_models_are_authoritative_for_mcq_and_short_answer(monkeypatch):
    mcq, short = _question(), _question(kind="short_answer")
    snapshot = service._build_assessment_snapshot(
        [mcq, short], _profile("software_development"), source="fallback",
        created_at=datetime.now(timezone.utc), attempt_seed="scoring-seed",
    )
    snapshot["questions"][0]["prompt"] = "Historical prompt"
    snapshot["questions"][0]["correct_option"] = "B"
    snapshot["questions"][0]["options"] = [
        {"id": "A", "text": "Changed distractor"}, {"id": "B", "text": "Historical correct answer"}
    ]
    snapshot["questions"][1]["prompt"] = "Historical short answer prompt"
    snapshot["questions"][1]["rubric_keywords"] = ["historical rubric"]
    models = service._snapshot_question_models(snapshot)
    assert models[str(mcq.id)].prompt == "Historical prompt"
    assert models[str(mcq.id)].correct_option == "B"
    assert models[str(short.id)].rubric_keywords == ["historical rubric"]

    class Evaluator:
        provider_name = "test-provider"
        model = "test-model"

        async def generate_json(self, prompt, system_prompt=None):
            assert "Historical short answer prompt" in prompt
            assert "historical rubric" in prompt
            return {"evaluations": [{
                "question_id": str(short.id), "score": 75,
                "explanation": "The response uses the historical rubric.",
                "feedback": "Add one more detail.",
            }]}

    monkeypatch.setattr(service, "get_ai_service", Evaluator)
    def run_inline(coroutine):
        try:
            coroutine.send(None)
        except StopIteration as completed:
            return completed.value
        raise AssertionError("deterministic evaluator unexpectedly yielded")
    monkeypatch.setattr(service.asyncio, "run", run_inline)
    results, _, _ = service._evaluate_attempt_answers(
        uuid4(), "student", models,
        [
            {"question_id": str(mcq.id), "response": "B"},
            {"question_id": str(short.id), "response": "A detailed answer."},
        ],
    )
    assert results[str(mcq.id)]["score"] == service.BASELINE_MCQ_CORRECT_SCORE
    assert results[str(mcq.id)]["question"] == "Historical prompt"
    assert results[str(short.id)]["question"] == "Historical short answer prompt"


def test_legacy_null_snapshot_remains_explicitly_unsnapshotted():
    assert AssessmentAttempt.__table__.c.assessment_snapshot.nullable is True
    legacy = SimpleNamespace(assessment_snapshot=None, question_ids=[])
    assert legacy.assessment_snapshot is None



