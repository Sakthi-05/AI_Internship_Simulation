"""Database-independent tests for domain-aware baseline question generation."""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import assessment_service as service
from app.services.technical_domain_catalog import TECHNICAL_DOMAINS
from app.services import technical_profile_service
from app.models.assessment import AssessmentQuestion


class FakeSession:
    """Small savepoint fake; verifies service control flow, not SQLAlchemy internals."""

    def __init__(self, *, fail_first_nested_flush=False):
        self.added = []
        self.flush_count = 0
        self.nested_depth = 0
        self.nested_rollbacks = 0
        self.fail_first_nested_flush = fail_first_nested_flush

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flush_count += 1
        if self.nested_depth and self.fail_first_nested_flush:
            self.fail_first_nested_flush = False
            raise RuntimeError("simulated database write failure")
        for row in self.added:
            if isinstance(row, AssessmentQuestion) and getattr(row, "id", None) is None:
                row.id = uuid4()

    @contextmanager
    def begin_nested(self):
        before = len(self.added)
        self.nested_depth += 1
        try:
            yield
        except Exception:
            del self.added[before:]
            self.nested_rollbacks += 1
            raise
        finally:
            self.nested_depth -= 1


class FakeAI:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.prompts = []

    async def generate_json(self, prompt, system):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.payload



def run_inline(coroutine):
    """Drive a fake-provider coroutine that has no external await points."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("deterministic fake unexpectedly suspended")


def technical_profile(domain):
    return {
        "selected": True,
        "status": "active",
        "technical_domain_id": domain.id,
        "domain": {"id": domain.id, "name": domain.name},
        "technology_ratings": [
            {"technology_id": technology.id, "name": technology.name, "self_rating": 3}
            for technology in domain.technologies
        ],
    }


def install_generation_fakes(monkeypatch, profile, ai):
    monkeypatch.setattr(service, "ensure_question_bank", lambda db: None)
    monkeypatch.setattr(service, "get_or_create_profile", lambda db, student_id: SimpleNamespace(role="Analyst", career_goal=None, degree=None, college=None))
    monkeypatch.setattr(service, "get_ai_service", lambda: ai)


def valid_ai_questions(domain, profile):
    topic_rows = list(service.DOMAIN_COVERAGE_GUIDANCE[domain.id].items())
    technologies = [item["name"] for item in profile["technology_ratings"]]
    result = []
    for index in range(7):
        topic, markers = topic_rows[index % len(topic_rows)]
        marker = markers[0]
        anchor = service.DOMAIN_CONCEPT_ANCHORS[domain.id][topic][0]
        technology = technologies[index % len(technologies)]
        item = {
            "prompt": f"How would you identify {anchor} while using {technology} for {marker}, then validate the result against an explicit technical requirement {index}?",
            "competency": ("technical_knowledge", "problem_solving", "critical_thinking", "decision_making")[index % 4],
            "question_type": "mcq" if index % 2 == 0 else "short_answer",
            "difficulty": "intermediate",
        }
        if item["question_type"] == "mcq":
            item["options"] = [
                {"id": f"{index}-{j}", "text": text}
                for j, text in enumerate((
                    "Check the observed runtime state",
                    "Compare inputs with the expected contract",
                    "Validate the relevant system constraints",
                    "Measure the result against a known baseline",
                ))
            ]
            item["correct_option"] = f"{index}-0"
            item["rubric_keywords"] = []
        else:
            item["options"] = []
            item["correct_option"] = None
            item["rubric_keywords"] = ["concept", "evidence"]
        result.append(item)
    return result


@pytest.mark.parametrize("domain", TECHNICAL_DOMAINS, ids=lambda d: d.id)
def test_fallback_is_relevant_to_each_selected_domain(monkeypatch, domain):
    profile = technical_profile(domain)
    ai = FakeAI(error=RuntimeError("provider offline"))
    install_generation_fakes(monkeypatch, profile, ai)
    db = FakeSession()
    questions, source = run_inline(service.generate_dynamic_baseline_questions(db, "student-1", "fixed-seed", technical_profile=profile))
    assert source == "fallback"
    assert len(questions) == 7
    assert len({question.prompt for question in questions}) == 7
    assert all(question.domain == domain.id for question in questions)
    assert all(question.competency in service.BASELINE_COMPETENCY_MAP for question in questions)
    assert all(question.difficulty in service.BASELINE_ALLOWED_DIFFICULTIES for question in questions)
    assert all(question.question_type in {"mcq", "short_answer"} for question in questions)
    assert all(question.question_type != "short_answer" or question.rubric_keywords for question in questions)
    assert all(service._meaningfully_tests_domain_concept(question.prompt, domain.id) for question in questions)
    assert all(any(technology.id in {row["technology_id"] for row in profile["technology_ratings"]} and service._contains_phrase(question.prompt, technology.name) for technology in domain.technologies) for question in questions)
    assert all(any(service._contains_phrase(question.prompt, marker) for markers in service.DOMAIN_COVERAGE_GUIDANCE[domain.id].values() for marker in markers) for question in questions)
    assert all(any(service._contains_phrase(question.prompt, tech.name) for tech in domain.technologies) for question in questions)
    covered_topics = {
        topic for question in questions
        for topic, markers in service.DOMAIN_COVERAGE_GUIDANCE[domain.id].items()
        if any(service._contains_phrase(question.prompt, marker) for marker in markers)
    }
    covered_technologies = {
        technology.id for question in questions for technology in domain.technologies
        if service._contains_phrase(question.prompt, technology.name)
    }
    assert len(covered_topics) >= 3
    assert len(covered_technologies) >= 4
    assert [q.prompt for q in questions] == [q.prompt for q in service._personalized_fallback(profile)]
    assert len(db.added) == 7


def test_malformed_ai_response_uses_domain_fallback_without_ai_rows(monkeypatch):
    domain = TECHNICAL_DOMAINS[2]
    profile = technical_profile(domain)
    install_generation_fakes(monkeypatch, profile, FakeAI(payload={"questions": "not-a-question-list"}))
    db = FakeSession()
    questions, source = run_inline(service.generate_dynamic_baseline_questions(db, "student-1", technical_profile=profile))
    assert source == "fallback"
    assert len(questions) == len(db.added) == service.BASELINE_QUESTION_COUNT
    assert all(question.domain == domain.id for question in questions)


@pytest.mark.parametrize("domain", TECHNICAL_DOMAINS, ids=lambda d: d.id)
def test_ai_questions_are_validated_and_saved_only_after_complete_set(monkeypatch, domain):
    profile = technical_profile(domain)
    payload = {"questions": valid_ai_questions(domain, profile)}
    ai = FakeAI(payload=payload)
    install_generation_fakes(monkeypatch, profile, ai)
    db = FakeSession()
    questions, source = run_inline(service.generate_dynamic_baseline_questions(db, "student-1", "fixed-seed", technical_profile=profile))
    assert source == "ai"
    assert len(questions) == 7
    assert len(db.added) == 7
    assert all(q.domain == domain.id for q in questions)
    assert "self-reported" in ai.prompts[0]


def test_missing_or_unavailable_profile_is_rejected_before_generation(monkeypatch):
    monkeypatch.setattr(technical_profile_service, "get_student_technical_profile", lambda *args: {"selected": False})
    with pytest.raises(HTTPException) as exc:
        service._validated_technical_profile(FakeSession(), "student-1")
    assert exc.value.status_code == 409


def test_stale_profile_is_rejected(monkeypatch):
    monkeypatch.setattr(technical_profile_service, "get_student_technical_profile", lambda *args: {"selected": True, "status": "unavailable", "technical_domain_id": "stale"})
    with pytest.raises(HTTPException) as exc:
        service._validated_technical_profile(FakeSession(), "student-1")
    assert exc.value.status_code == 409


def test_invalid_or_generic_ai_set_falls_back_without_partial_question_persistence(monkeypatch):
    domain = TECHNICAL_DOMAINS[0]
    profile = technical_profile(domain)
    raw = valid_ai_questions(domain, profile)
    raw[-1]["prompt"] = "Explain generic workplace expectations and professional conduct in an office environment."
    ai = FakeAI(payload={"questions": raw})
    install_generation_fakes(monkeypatch, profile, ai)
    db = FakeSession()
    questions, source = run_inline(service.generate_dynamic_baseline_questions(db, "student-1", technical_profile=profile))
    assert source == "fallback"
    assert len(db.added) == 7
    assert all(q in questions for q in db.added)


def test_topic_and_technology_tokens_do_not_validate_an_unrelated_question():
    domain = TECHNICAL_DOMAINS[0]
    profile = technical_profile(domain)
    payload = valid_ai_questions(domain, profile)
    payload[-1]["prompt"] = "What is your favorite holiday? Python and programming fundamentals are mentioned here."
    with pytest.raises(ValueError, match="meaningfully test"):
        service._validate_generated_baseline_questions(payload, profile)


def test_valid_conceptual_question_with_alternate_wording_is_accepted():
    domain = TECHNICAL_DOMAINS[0]
    profile = technical_profile(domain)
    payload = valid_ai_questions(domain, profile)
    payload[1]["prompt"] = "For Java, how would you assign responsibilities to a class to preserve encapsulation while applying object-oriented design?"
    assert len(service._validate_generated_baseline_questions(payload, profile)) == 7


def test_mcq_rejects_placeholder_or_duplicate_option_text():
    domain = TECHNICAL_DOMAINS[0]
    profile = technical_profile(domain)
    payload = valid_ai_questions(domain, profile)
    payload[0]["options"] = [{"id": f"a-{i}", "text": "Option A"} for i in range(4)]
    with pytest.raises(ValueError, match="options or answer key"):
        service._validate_generated_baseline_questions(payload, profile)


def test_ai_persistence_savepoint_failure_rolls_back_only_ai_questions(monkeypatch):
    domain = TECHNICAL_DOMAINS[0]
    profile = technical_profile(domain)
    install_generation_fakes(monkeypatch, profile, FakeAI(payload={"questions": valid_ai_questions(domain, profile)}))
    db = FakeSession(fail_first_nested_flush=True)
    outer_change = object()
    db.add(outer_change)

    questions, source = run_inline(service.generate_dynamic_baseline_questions(db, "student-1", technical_profile=profile))

    persisted_questions = [row for row in db.added if isinstance(row, AssessmentQuestion)]
    assert source == "fallback"
    assert db.nested_rollbacks == 1
    assert len(questions) == len(persisted_questions) == 7
    assert all(row in questions for row in persisted_questions)
    assert outer_change in db.added


def test_fallback_technology_selection_tracks_lower_and_higher_familiarity():
    domain = TECHNICAL_DOMAINS[0]
    low_java, high_java = technical_profile(domain), technical_profile(domain)
    ratings = {"java": 1, "python": 4, "javascript": 5}
    for profile in (low_java, high_java):
        for row in profile["technology_ratings"]:
            row["self_rating"] = ratings.get(row["technology_id"], 3)
    low_java["technology_ratings"][0]["self_rating"] = 1
    high_java["technology_ratings"][0]["self_rating"] = 5
    assert service._personalized_fallback(low_java)[0].prompt.startswith("In Java,")
    assert not service._personalized_fallback(high_java)[0].prompt.startswith("In Java,")


def test_null_familiarity_is_unknown_not_beginner():
    profile = technical_profile(TECHNICAL_DOMAINS[0])
    next(row for row in profile["technology_ratings"] if row["technology_id"] == "sql")["self_rating"] = None
    sql_question = next(q for q in service._personalized_fallback(profile) if q.prompt.startswith("When using SQL"))
    assert sql_question.difficulty == "intermediate"


def test_familiarity_guides_question_difficulty_without_changing_competency():
    domain = TECHNICAL_DOMAINS[0]
    low, high = technical_profile(domain), technical_profile(domain)
    for profile, rating in ((low, 1), (high, 5)):
        next(row for row in profile["technology_ratings"] if row["technology_id"] == "sql")["self_rating"] = rating
    low_sql = next(q for q in service._personalized_fallback(low) if q.prompt.startswith("When using SQL"))
    high_sql = next(q for q in service._personalized_fallback(high) if q.prompt.startswith("When using SQL"))
    assert low_sql.difficulty == "beginner"
    assert high_sql.difficulty == "advanced"
    assert low_sql.competency == high_sql.competency


def test_familiarity_never_changes_score_or_competency_aggregation():
    domain = TECHNICAL_DOMAINS[0]
    low, high = technical_profile(domain), technical_profile(domain)
    for profile, rating in ((low, 1), (high, 5)):
        for row in profile["technology_ratings"]:
            row["self_rating"] = rating
    low_questions = service._personalized_fallback(low)
    high_questions = service._personalized_fallback(high)
    assert [q.competency for q in low_questions] == [q.competency for q in high_questions]
    low_results = [{"score": 70, "competency": question.competency} for question in low_questions]
    high_results = [{"score": 70, "competency": question.competency} for question in high_questions]
    assert service.calculate_baseline_results(low_results) == service.calculate_baseline_results(high_results)


def test_fallback_fails_clearly_if_a_configured_topic_has_no_catalog_technology(monkeypatch):
    domain = TECHNICAL_DOMAINS[0]
    specs = list(service._DOMAIN_FALLBACK_SPECS[domain.id])
    specs[0] = (specs[0][0], specs[0][1], ("not_in_catalog",), specs[0][3])
    monkeypatch.setitem(service._DOMAIN_FALLBACK_SPECS, domain.id, specs)
    with pytest.raises(ValueError, match="no technology"):
        service._personalized_fallback(technical_profile(domain))
