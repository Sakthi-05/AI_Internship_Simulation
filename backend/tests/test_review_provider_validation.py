"""Database-independent regression tests for genuine task review output."""
import pytest
import httpx

from app.services.ai.ai_service import (
    GeminiAIService,
    MalformedAIJSONError,
    AI_REVIEW_RESPONSE_SCHEMA,
    is_json_response_cacheable,
    parse_json_response,
)
from app.services.ai.prompt_builder import build_review_prompt
from app.services.ai.review_service import (
    REVIEW_PROVIDER_TIMEOUT_SECONDS,
    classify_provider_failure,
    validate_provider_review,
)
from app.services.ai.review_service import _candidate_followup_sprints
from app.models.enums import SprintStatus
from app.services.context.context_builder import metadata_evidence_text, _similarity_ratio


def _valid_payload(criteria):
    return {
        "result": "changes_required",
        "score": 68,
        "severity": "warning",
        "summary": "The implementation misses one required behavior and needs correction.",
        "strengths": ["The request parsing is readable."],
        "issues": ["The empty input path is not handled."],
        "required_changes": ["Handle empty input in parse_request and add a regression test."],
        "acceptance_criteria": [
            {
                "criterion": item,
                "met": False,
                "evidence": "The submitted code does not implement this behavior.",
                "score": 0,
                "missing_elements": ["The required behavior is absent."],
            }
            for item in criteria
        ],
        "code_quality": {"score": 75, "feedback": "The parsing function is clear but misses validation."},
        "testing": {"score": 55, "feedback": "Add a test for the empty input case."},
        "security": {"score": 80, "feedback": "No unsafe operation is evident in the submitted path."},
        "performance": {"score": 82, "feedback": "The current parsing path is linear in input length."},
    }


def _valid_provider_review_text():
    import json
    return json.dumps(_valid_payload(["The architecture defines retry behavior."]))


def test_provider_result_is_preserved_after_valid_schema_and_rubric_validation():
    criteria = ["Reject empty input", "Return a parsed request"]
    result = validate_provider_review(_valid_payload(criteria), {"acceptance_criteria": criteria})
    assert result["score"] == 68
    assert result["result"] == "changes_required"
    assert [item["criterion"] for item in result["acceptance_criteria"]] == criteria
    assert result["acceptance_criteria"][0]["score"] == 0
    assert result["acceptance_criteria"][0]["missing_elements"]


def test_task_specific_complete_submission_can_pass_with_requirement_evidence():
    criteria = ["Implement ERP inventory reconciliation"]
    payload = _valid_payload(criteria)
    payload.update(result="approved", score=92, required_changes=[])
    payload["acceptance_criteria"] = [{
        "criterion": criteria[0],
        "met": True,
        "evidence": "The reconciliation section maps receipt records to warehouse counts and describes mismatch resolution.",
        "score": 95,
        "missing_elements": [],
    }]
    validated = validate_provider_review(payload, {"acceptance_criteria": criteria})
    assert validated["result"] == "approved"
    assert validated["acceptance_criteria"][0]["score"] == 95


@pytest.mark.parametrize("mutation", [
    "missing_field", "extra_field", "missing_criterion", "approved_unmet",
    "no_score", "wrong_type", "out_of_range",
])
def test_malformed_or_inconsistent_provider_output_is_rejected(mutation):
    criteria = ["Reject empty input", "Return a parsed request"]
    payload = _valid_payload(criteria)
    if mutation == "missing_field":
        del payload["testing"]
    elif mutation == "extra_field":
        payload["invented"] = True
    elif mutation == "missing_criterion":
        payload["acceptance_criteria"].pop()
    elif mutation == "approved_unmet":
        payload["result"] = "approved"
    elif mutation == "no_score":
        payload["score"] = None
    elif mutation == "wrong_type":
        payload["score"] = "68"
    elif mutation == "out_of_range":
        payload["score"] = 101
    with pytest.raises((ValueError, TypeError)):
        validate_provider_review(payload, {"acceptance_criteria": criteria})


@pytest.mark.parametrize("raw", [
    '{"score": 88, "result": "approved"}',
    '```json\n{"score": 88, "result": "approved"}\n```',
    'Evaluator response follows: {"score": 88, "result": "approved"} End of response.',
])
def test_json_parser_accepts_object_fences_and_harmless_surrounding_text(raw):
    assert parse_json_response(raw) == {"score": 88, "result": "approved"}


def test_malformed_json_is_explicitly_rejected():
    with pytest.raises(MalformedAIJSONError):
        parse_json_response('{"score": 88')


def test_review_provider_output_is_never_cached_for_retry():
    assert is_json_response_cacheable("ordinary JSON prompt")
    assert not is_json_response_cacheable("You are a senior engineering reviewer")
    assert not is_json_response_cacheable("baseline assessment generation")


def test_gemini_review_schema_requires_evaluation_fields_and_score_range():
    assert {"result", "score", "strengths", "issues", "required_changes"}.issubset(
        AI_REVIEW_RESPONSE_SCHEMA["required"]
    )
    score = AI_REVIEW_RESPONSE_SCHEMA["properties"]["score"]
    assert score["minimum"] == 0 and score["maximum"] == 100


class _FakeGeminiResponse:
    def __init__(self, text=None, status_code=200):
        self.status_code = status_code
        self.text = text
        self.request = httpx.Request("POST", "https://provider.test/generate")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("provider error", request=self.request, response=httpx.Response(
                self.status_code, request=self.request
            ))

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": self.text}]}}]}


class _FakeGeminiClient:
    def __init__(self, outcomes, requests):
        self.outcomes = iter(outcomes)
        self.requests = requests

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, *, headers, json):
        self.requests.append(json)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _fake_provider(monkeypatch, outcomes):
    requests = []
    monkeypatch.setattr(
        "app.services.ai.ai_service.httpx.AsyncClient",
        lambda **_kwargs: _FakeGeminiClient(outcomes, requests),
    )

    async def no_wait(_delay):
        return None

    monkeypatch.setattr("app.services.ai.ai_service.asyncio.sleep", no_wait)
    return requests


def _run_immediate(coro):
    """Drive async provider tests whose mocked await points never suspend."""
    try:
        coro.send(None)
    except StopIteration as complete:
        return complete.value
    raise AssertionError("provider test unexpectedly suspended on external I/O")


def test_gemini_review_uses_structured_schema_and_returns_real_provider_evaluation(monkeypatch):
    payload = _valid_payload(["The architecture defines retry behavior."])
    requests = _fake_provider(monkeypatch, [_FakeGeminiResponse(f'```json\n{_valid_provider_review_text()}\n```')])
    provider = GeminiAIService("test-key", "gemini-3.5-flash-lite")
    result = _run_immediate(provider.generate_json("task-specific review prompt", "You are a senior engineering reviewer"))
    validated = validate_provider_review(result, {"acceptance_criteria": ["The architecture defines retry behavior."]})
    assert validated["score"] == payload["score"]
    assert validated["summary"] == payload["summary"]
    assert len(requests) == 1
    assert requests[0]["generationConfig"]["responseSchema"] == AI_REVIEW_RESPONSE_SCHEMA


def test_timeout_retries_once_then_accepts_success(monkeypatch):
    request = httpx.Request("POST", "https://provider.test/generate")
    requests = _fake_provider(monkeypatch, [
        httpx.ReadTimeout("provider timed out", request=request),
        _FakeGeminiResponse(_valid_provider_review_text()),
    ])
    provider = GeminiAIService("test-key", "gemini-3.5-flash-lite")
    response = _run_immediate(provider.generate_json("prompt", "You are a senior engineering reviewer"))
    assert validate_provider_review(response, {"acceptance_criteria": ["The architecture defines retry behavior."]})["score"] == 68
    assert len(requests) == 2


def test_rate_limit_retries_at_most_twice_then_succeeds(monkeypatch):
    requests = _fake_provider(monkeypatch, [
        _FakeGeminiResponse(status_code=429),
        _FakeGeminiResponse(status_code=429),
        _FakeGeminiResponse(_valid_provider_review_text()),
    ])
    provider = GeminiAIService("test-key", "gemini-3.5-flash-lite")
    response = _run_immediate(provider.generate_json("prompt", "You are a senior engineering reviewer"))
    assert validate_provider_review(response, {"acceptance_criteria": ["The architecture defines retry behavior."]})["score"] == 68
    assert len(requests) == 3


def test_malformed_json_is_not_repaired_or_retried(monkeypatch):
    requests = _fake_provider(monkeypatch, [_FakeGeminiResponse('{"score": 88')])
    provider = GeminiAIService("test-key", "gemini-3.5-flash-lite")
    with pytest.raises(MalformedAIJSONError):
        _run_immediate(provider.generate_json("prompt", "You are a senior engineering reviewer"))
    assert len(requests) == 1


def test_rate_limit_retry_exhaustion_is_bounded_to_three_requests(monkeypatch):
    requests = _fake_provider(monkeypatch, [
        _FakeGeminiResponse(status_code=429),
        _FakeGeminiResponse(status_code=429),
        _FakeGeminiResponse(status_code=429),
    ])
    provider = GeminiAIService("test-key", "gemini-3.5-flash-lite")
    with pytest.raises(httpx.HTTPStatusError):
        _run_immediate(provider.generate_json("prompt", "You are a senior engineering reviewer"))
    assert len(requests) == 3


def test_other_gemini_features_keep_their_existing_two_request_limit(monkeypatch):
    requests = _fake_provider(monkeypatch, [
        _FakeGeminiResponse(status_code=429),
        _FakeGeminiResponse(status_code=429),
    ])
    provider = GeminiAIService("test-key", "gemini-3.5-flash-lite")
    with pytest.raises(httpx.HTTPStatusError):
        _run_immediate(provider.generate_json("prompt", "generic JSON generator"))
    assert len(requests) == 2


@pytest.mark.parametrize(("error", "category"), [
    (TimeoutError(), "provider_timeout"),
    (httpx.ReadTimeout("request timed out"), "provider_timeout"),
    (ValueError("bad response"), "malformed_response"),
    (RuntimeError("provider unavailable"), "provider_error"),
])
def test_provider_failures_are_classified_without_fabricating_results(error, category):
    assert classify_provider_failure(error) == category


def test_review_timeout_allows_three_bounded_provider_requests_and_backoff():
    provider_budget = 3 * GeminiAIService.REQUEST_TIMEOUT_SECONDS + sum(
        GeminiAIService.OVERLOAD_RETRY_DELAYS_SECONDS
    )
    assert REVIEW_PROVIDER_TIMEOUT_SECONDS >= provider_budget
    assert REVIEW_PROVIDER_TIMEOUT_SECONDS < 90


def test_rate_limit_is_distinguished_from_invalid_response():
    request = httpx.Request("POST", "https://provider.test/generate")
    response = httpx.Response(429, request=request)
    error = httpx.HTTPStatusError("rate limited", request=request, response=response)
    assert classify_provider_failure(error) == "provider_rate_limited"


def test_prompt_includes_submission_files_test_results_and_exact_criteria():
    prompt = build_review_prompt({
        "task_title": "Backend parser",
        "task_description": "Parse requests",
        "acceptance_criteria": ["Reject empty input"],
        "technology_stack": ["Python"],
        "attached_filenames": ["parser.py", "test_parser.py"],
        "test_results": "pytest -q: 4 passed",
        "submission_content": "def parse_request(value): return value",
        "has_work_evidence": True,
    })
    assert "parser.py, test_parser.py" in prompt
    assert "pytest -q: 4 passed" in prompt
    assert "Reject empty input" in prompt
    assert "def parse_request(value): return value" in prompt


def test_review_prompt_does_not_include_conversation_or_previous_review_history():
    prompt = build_review_prompt({
        "task_title": "Design Scalable Document Parsing Pipeline Architecture",
        "acceptance_criteria": ["Define worker retry behavior"],
        "submission_content": "Use an asynchronous queue and durable object storage.",
        "previous_review": "This should not be sent as history.",
    })
    assert "This should not be sent as history" not in prompt


def test_review_prompt_is_grounded_in_complete_task_scope_and_similarity_alert():
    prompt = build_review_prompt({
        "task_id": "task-erp-2",
        "task_title": "Design ERP inventory reconciliation",
        "task_description": "Full instructions " * 80,
        "sprint_id": "sprint-1",
        "sprint_goal": "Reconcile receiving and warehouse inventory",
        "project_title": "ERP modernization",
        "project_objectives": ["Track inventory changes"],
        "expected_deliverables": ["Reconciliation architecture", "Exception workflow"],
        "task_instructions": ["Explain transaction boundaries"],
        "cross_task_similarity": {"matches": ["task unrelated (Architecture) similarity=0.99"]},
        "acceptance_criteria": ["Cover receiving", "Cover reconciliation", "Cover exception reporting"] * 4,
        "submission_content": "A reusable architecture document.",
    })
    assert "TASK ID: task-erp-2" in prompt
    assert "Reconcile receiving and warehouse inventory" in prompt
    assert "Exception workflow" in prompt
    assert "Explain transaction boundaries" in prompt
    assert "Full instructions " * 80 in prompt
    assert prompt.count("Cover receiving") == 4
    assert "not automatic rejection" in prompt


def test_substantial_submission_similarity_is_flagged_without_requiring_exact_identity():
    original = "Architecture: " + ("inventory reconciliation transaction boundaries and exception handling. " * 35)
    revised = original + "Task-specific receiving workflow is added."
    unrelated = "A different plan for user authentication and session management."
    assert _similarity_ratio(original, revised) >= 0.90
    assert _similarity_ratio(original, unrelated) < 0.90


def test_review_cannot_approve_below_threshold_or_with_weak_requirement_score():
    criteria = ["Implement ERP reconciliation"]
    payload = _valid_payload(criteria)
    payload["result"] = "approved"
    payload["score"] = 74
    payload["acceptance_criteria"] = [{
        "criterion": criteria[0], "met": True, "evidence": "Relevant content exists.",
        "score": 100, "missing_elements": [],
    }]
    with pytest.raises(ValueError, match="75-point"):
        validate_provider_review(payload, {"acceptance_criteria": criteria})

    payload["score"] = 90
    payload["acceptance_criteria"][0]["score"] = 60
    with pytest.raises(ValueError, match="weakly supported"):
        validate_provider_review(payload, {"acceptance_criteria": criteria})


def test_review_fails_closed_when_task_has_no_criteria():
    with pytest.raises(ValueError, match="no persisted acceptance criteria"):
        validate_provider_review(_valid_payload([]), {"acceptance_criteria": []})


def test_review_cannot_reuse_another_tasks_criteria():
    payload = _valid_payload(["Implement ERP reconciliation"])
    with pytest.raises(ValueError, match="each task acceptance criterion exactly once"):
        validate_provider_review(payload, {"acceptance_criteria": ["Implement identity provider SSO"]})


def test_completed_final_sprint_has_no_followup_candidate():
    class Sprint:
        def __init__(self, number, status):
            self.sprint_number = number
            self.status = status

    final = Sprint(4, SprintStatus.ACTIVE)
    assert _candidate_followup_sprints(final, [final], objective_complete=True) == []
    assert _candidate_followup_sprints(final, [final], objective_complete=False) == [final]
    earlier = Sprint(2, SprintStatus.COMPLETED)
    next_sprint = Sprint(3, SprintStatus.PLANNED)
    assert _candidate_followup_sprints(
        earlier, [earlier, next_sprint], objective_complete=True
    ) == [next_sprint]


def test_long_architecture_spec_and_sql_comment_format_are_preserved_for_evaluation():
    architecture = "```sql\n/*\n" + ("ARCHITECTURE DETAIL; " * 350) + "\n*/\n```"
    evidence = metadata_evidence_text({"code": architecture})
    assert len(evidence) > 6500
    prompt = build_review_prompt({
        "task_title": "Design Scalable Document Parsing Pipeline Architecture",
        "task_description": "Produce a technical architecture specification.",
        "acceptance_criteria": ["Define asynchronous workers and retry/DLQ behavior"],
        "submission_content": evidence,
        "has_work_evidence": True,
    })
    assert architecture in prompt
    assert "SQL block comments) are valid design-document formats" in prompt
    assert "do not require executable SQL or source code" in prompt


def test_evaluator_prompt_preserves_full_source_beyond_legacy_twelve_thousand_character_limit():
    source = "# ERP architecture\n" + ("Inventory transaction and reconciliation detail.\n" * 500) + "# FINAL_ARCHITECTURE_SECTION\n"
    prompt = build_review_prompt({
        "task_title": "ERP inventory reconciliation architecture",
        "acceptance_criteria": ["Define the reconciliation workflow"],
        "submission_content": source,
    })
    assert len(source) > 12_000
    assert source in prompt
    assert prompt.rstrip().endswith("- Output ONLY valid JSON, no markdown, no reasoning")
