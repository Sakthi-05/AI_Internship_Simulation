"""
AIService abstraction.

Every AI-driven feature in this codebase (company generation flavor text,
project generation, and — in later phases — the AI Project Manager and AI
teammates chat) talks to this interface, never to Gemini directly. That
means:

  - Today, with no GEMINI_API_KEY set, MockAIService produces deterministic,
    still-varied, still-real output (no external calls, nothing "fake" in
    the sense of broken — it's a legitimate rule-based generator).
  - The moment GEMINI_API_KEY is set in the environment, get_ai_service()
    starts returning GeminiAIService instead, with zero call-site changes.

This is the abstraction FEATURE 6 (AI Project Manager) and FEATURE 7
(AI Teammates) will build on in the next phase.
"""
import asyncio
import hashlib
import json
import logging
import random
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings


_MOCK_AI_SERVICE: Optional["MockAIService"] = None
_GEMINI_AI_SERVICE: Optional["GeminiAIService"] = None
_JSON_CACHE: Dict[str, Dict[str, Any]] = {}
_JSON_CACHE_LIMIT = 64
logger = logging.getLogger(__name__)


class MalformedAIJSONError(ValueError):
    """Provider returned text that could not be decoded as JSON after repair."""


class AIProviderResponseError(RuntimeError):
    """Provider request succeeded but returned no usable candidate."""


def parse_json_response(raw: str) -> Any:
    """Parse a JSON object/array with optional fences or surrounding prose."""
    text = str(raw or "").strip()
    if not text:
        raise MalformedAIJSONError("empty provider response")
    # Prefer an explicitly fenced JSON block when present.
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(text)
    decoder = json.JSONDecoder()
    for candidate in candidates:
        for index, char in enumerate(candidate):
            if char not in "{[":
                continue
            try:
                value, _end = decoder.raw_decode(candidate[index:])
                return value
            except json.JSONDecodeError:
                continue
    raise MalformedAIJSONError("no complete JSON object or array found")


def is_json_response_cacheable(system_prompt: Optional[str]) -> bool:
    prompt = (system_prompt or "").lower()
    return "baseline assessment" not in prompt and "senior engineering reviewer" not in prompt


def _redacted_response_debug(value: Any) -> Any:
    """Keep response shape/types and fingerprints while omitting all text values."""
    if isinstance(value, dict):
        return {str(key): _redacted_response_debug(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted_response_debug(item) for item in value]
    if isinstance(value, str):
        return {
            "redacted_string": True,
            "length": len(value),
            "sha256": hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest(),
        }
    return value


def _log_provider_json_response(model: str, raw: str, parsed: Any = None, error: Optional[str] = None) -> None:
    # Never log prompt/request bodies, keys, or raw string values. This record
    # captures response structure, string lengths/fingerprints, and parse error.
    logger.debug(
        "Gemini JSON response model=%s raw_chars=%d raw_sha256=%s parsed_redacted=%s parse_error=%s",
        model,
        len(raw),
        hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest(),
        json.dumps(_redacted_response_debug(parsed), ensure_ascii=True) if parsed is not None else None,
        error,
    )


AI_REVIEW_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "result": {"type": "STRING", "enum": ["approved", "changes_required", "rejected"]},
        "score": {"type": "INTEGER", "minimum": 0, "maximum": 100},
        "severity": {"type": "STRING", "enum": ["info", "warning", "error", "critical"]},
        "summary": {"type": "STRING"},
        "strengths": {"type": "ARRAY", "items": {"type": "STRING"}},
        "issues": {"type": "ARRAY", "items": {"type": "STRING"}},
        "required_changes": {"type": "ARRAY", "items": {"type": "STRING"}},
        "acceptance_criteria": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "criterion": {"type": "STRING"},
                    "met": {"type": "BOOLEAN"},
                    "evidence": {"type": "STRING"},
                },
                "required": ["criterion", "met", "evidence"],
                "propertyOrdering": ["criterion", "met", "evidence"],
            },
        },
        **{
            section: {
                "type": "OBJECT",
                "properties": {
                    "score": {"type": "INTEGER", "minimum": 0, "maximum": 100},
                    "feedback": {"type": "STRING"},
                },
                "required": ["score", "feedback"],
                "propertyOrdering": ["score", "feedback"],
            }
            for section in ("code_quality", "testing", "security", "performance")
        },
    },
    "required": [
        "result", "score", "severity", "summary", "strengths", "issues",
        "required_changes", "acceptance_criteria", "code_quality", "testing",
        "security", "performance",
    ],
    "propertyOrdering": [
        "result", "score", "severity", "summary", "strengths", "issues",
        "required_changes", "acceptance_criteria", "code_quality", "testing",
        "security", "performance",
    ],
}


class AIService(ABC):
    @abstractmethod
    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Return a free-form text completion."""
        raise NotImplementedError

    @abstractmethod
    async def generate_json(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return a completion parsed as JSON. Caller defines the expected shape."""
        raise NotImplementedError


import re


class MockAIService(AIService):
    """
    Deterministic-but-varied rule-based provider. Used automatically whenever
    GEMINI_API_KEY is empty, so the whole engine is fully runnable with zero
    external dependencies or API keys.
    """

    def __init__(self) -> None:
        self._technical_review_calls = 0

    _MISSION_TEMPLATES = [
        "To {verb} {domain} through relentless focus on {value}.",
        "Empowering {audience} by {verb_ing} {domain} at scale.",
        "Building the future of {domain} — one {value}-driven release at a time.",
    ]
    _VERBS = ["transform", "simplify", "reinvent", "accelerate", "modernize"]
    _VALUES = ["quality", "customer trust", "engineering excellence", "innovation", "reliability"]
    _AUDIENCES = ["businesses", "developers", "patients", "learners", "consumers", "enterprises"]

    _MANAGER_REPLIES = {
        "strict": [
            "Noted. {stress_clause}Keep the pace up — {sprint_clause}",
            "Fine, but I expect this closed out properly. {deadline_clause}",
            "Good. Don't let it slip like the last {missed} missed deadline(s) did.",
        ],
        "friendly": [
            "Thanks for the update! {stress_clause}You're doing solid work — {sprint_clause}",
            "Appreciate you flagging that. {deadline_clause} Let me know if you're stuck on anything.",
            "Nice progress so far — {completed} tasks down. Keep it up!",
        ],
        "corporate": [
            "Understood. Let's ensure this stays aligned with our sprint {sprint_num} objectives.",
            "Thank you for the visibility. {deadline_clause} I'll loop in stakeholders as needed.",
            "Acknowledged. Current velocity shows {completed} tasks completed — on track overall.",
        ],
        "startup_founder": [
            "Love it, let's ship it. {sprint_clause}",
            "Keep moving — {completed} down, more to go. We don't slow down here.",
            "Cool cool. {deadline_clause} Momentum is everything right now.",
        ],
    }

    _TEAMMATE_REPLIES = [
        "Got it — I'll take a look and circle back with what I find.",
        "Makes sense. Want me to pair on this or are you good solo?",
        "Heads up, I hit something similar last sprint — happy to compare notes.",
        "Nice, that unblocks me too. Thanks for the update.",
        "I can review that once it's up — just tag me.",
    ]

    def _extract_int(self, text: str, key: str, default: int = 0) -> int:
        match = re.search(rf"{key}=(\-?\d+)", text)
        return int(match.group(1)) if match else default

    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        sp = system_prompt or ""

        # Manager chat context (see app/services/ai_manager.py)
        if "the manager on project" in sp:
            rng = random.Random(hash(prompt) & 0xFFFFFFFF)
            personality = "corporate"
            for key in self._MANAGER_REPLIES:
                if key.replace("_", " ") in sp.lower() or key in sp.lower():
                    personality = key
                    break
            missed = self._extract_int(prompt, "missed_deadlines")
            completed = self._extract_int(prompt, "completed_tasks")
            stress = self._extract_int(prompt, "stress_level")
            sprint_num = self._extract_int(prompt, "current_sprint")

            template = rng.choice(self._MANAGER_REPLIES[personality])
            return template.format(
                stress_clause="I can tell things are getting intense. " if stress >= 60 else "",
                deadline_clause=(
                    f"We've missed {missed} deadline(s) so far — let's not add to that."
                    if missed > 0
                    else "Deadlines are holding steady, which is good."
                ),
                sprint_clause=f"we're in sprint {sprint_num} of 4, stay focused.",
                missed=missed,
                completed=completed,
                sprint_num=sprint_num,
            )

        # AI teammate chat context (see app/services/ai_teammates.py)
        if "teammate" in sp.lower() and "messaging" in sp.lower():
            rng = random.Random(hash(prompt) & 0xFFFFFFFF)
            return rng.choice(self._TEAMMATE_REPLIES)

        # Default: company mission/description flavor text generation.
        rng = random.Random(hash(prompt) & 0xFFFFFFFF)
        template = rng.choice(self._MISSION_TEMPLATES)
        return template.format(
            verb=rng.choice(self._VERBS),
            verb_ing=rng.choice(self._VERBS) + "ing",
            domain=self._extract_domain(prompt),
            value=rng.choice(self._VALUES),
            audience=rng.choice(self._AUDIENCES),
        )

    async def generate_json(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        sp = (system_prompt or "").lower()
        p = prompt.lower()
        if "scenario proposal" in sp:
            return self._generate_mock_scenario_proposal(prompt)
        if "baseline" in sp or "baseline" in p or "question bank" in p:
            return self._generate_mock_baseline_questions(prompt)
        if "generate_task" in sp or "generate next task" in p or "first task" in p or "dynamic task" in p:
            return self._generate_mock_dynamic_task(prompt)
        if "adaptability" in sp or "requirement change" in p:
            return self._generate_mock_adaptability_change(prompt)
        if "Provide your final manager review decision as JSON" in prompt or "You are reviewing an AI code review" in sp:
            return self._generate_mock_manager_review()
        return self._generate_mock_review()

    def _generate_mock_scenario_proposal(self, prompt: str) -> Dict[str, Any]:
        """Produce a deterministic narrative for the Scenario Agent contract."""
        context: Dict[str, Any] = {}
        marker = "Context:\n"
        if marker in prompt:
            try:
                context = json.loads(prompt.split(marker, 1)[1])
            except (TypeError, ValueError):
                context = {}
        category = str(context.get("scenario_category") or "workplace")
        task = str(context.get("task_title") or "the current task")
        templates = {
            "requirement_change": (
                "Review an updated criterion", f"While you work on {task}, a recorded change adds a validation requirement. "
                "Compare the current and previous criteria before deciding what to revise.",
                "Explain how you will adapt the in-progress work to meet the added criterion.",
                "The proposal is grounded in the task's persisted criteria history and supports adaptation and communication."),
            "collaboration": (
                "Coordinate a teammate dependency", f"A teammate's request affects progress on {task}. "
                "Clarify the dependency and agree who owns the next step.",
                "Reply with an owner, a coordination step, and the information the teammate needs.",
                "The recorded teammate request is directly related to the current task and practices collaboration."),
            "time_management": (
                "Re-plan after a deadline warning", f"A deadline warning has arrived while you work on {task}. "
                "Assess remaining work and select the most valuable next action.",
                "Give a short ordered plan and state any schedule risk you would communicate.",
                "The persisted deadline warning makes time management relevant to this task."),
            "prioritization": (
                "Choose between competing work", f"A recorded priority conflict affects {task}. "
                "Compare impact, urgency, and dependencies before committing to the next action.",
                "State what you will do first and explain the trade-off to the team.",
                "The proposal follows a persisted priority event and exercises decision making."),
            "ownership": (
                "Take ownership of an open issue", f"An issue related to {task} has no confirmed owner. "
                "Clarify impact and make the next action explicit.",
                "Say whether you will take ownership or coordinate an owner, and name the next step.",
                "The issue is recorded in the workflow and provides evidence for a professional ownership scenario."),
            "feedback_response": (
                "Turn review feedback into an improvement", f"A persisted review of work on {task} asks for a change. "
                "Identify the feedback that matters and plan a focused revision.",
                "Explain what you will revise and how the next submission will show the change.",
                "The scenario refers to an actual review outcome and exercises feedback response."),
        }
        title, situation, student_action, relevance = templates.get(
            category, ("Respond to a workplace situation", f"Review the next step for {task}.",
                      "Describe a clear next action.", "The proposal is tied to the current task."))
        return {"title": title, "situation": situation,
                "student_action": student_action, "relevance": relevance}

    def _generate_mock_baseline_questions(self, prompt: str) -> Dict[str, Any]:
        return {
            "questions": [
                {
                    "competency": "technical_knowledge",
                    "question_type": "mcq",
                    "prompt": "When building scalable backend services, which architecture pattern best isolates domain logic?",
                    "options": [
                        {"id": "A", "text": "Tight coupling inside single monolith files"},
                        {"id": "B", "text": "Layered clean architecture separating controllers, services, and repositories"},
                        {"id": "C", "text": "Direct database queries from raw route handlers"},
                        {"id": "D", "text": "Global variable mutation across requests"}
                    ],
                    "correct_option": "B",
                    "difficulty": "intermediate"
                },
                {
                    "competency": "problem_solving",
                    "question_type": "mcq",
                    "prompt": "An API endpoint begins returning intermittent HTTP 500 errors after a high-traffic release. What is your first step?",
                    "options": [
                        {"id": "A", "text": "Restart the application server without checking logs"},
                        {"id": "B", "text": "Inspect server logs and error stack traces to pinpoint failure root cause"},
                        {"id": "C", "text": "Delete recent migration scripts"},
                        {"id": "D", "text": "Increase API timeout limits"}
                    ],
                    "correct_option": "B",
                    "difficulty": "intermediate"
                },
                {
                    "competency": "critical_thinking",
                    "question_type": "mcq",
                    "prompt": "Which indexing strategy is most appropriate for a database table with frequent write operations?",
                    "options": [
                        {"id": "A", "text": "Index every column to speed up all queries"},
                        {"id": "B", "text": "Index selectively on foreign keys and frequent WHERE/JOIN columns"},
                        {"id": "C", "text": "Avoid indexes entirely"},
                        {"id": "D", "text": "Use full-text search indexes for numeric fields"}
                    ],
                    "correct_option": "B",
                    "difficulty": "intermediate"
                },
                {
                    "competency": "communication",
                    "question_type": "short_answer",
                    "prompt": "Describe how you communicate a technical blocker to your manager and team when a critical dependency is delayed.",
                    "rubric_keywords": ["blocker", "impact", "options", "timeline", "notify"],
                    "difficulty": "intermediate"
                },
                {
                    "competency": "adaptability",
                    "question_type": "mcq",
                    "prompt": "If a stakeholder requests a late scope change during a sprint, how should an engineer respond?",
                    "options": [
                        {"id": "A", "text": "Accept the change silently without assessing effort or timeline impact"},
                        {"id": "B", "text": "Assess the impact on scope and timeline, then discuss trade-offs with the lead/manager"},
                        {"id": "C", "text": "Reject the request outright without discussion"},
                        {"id": "D", "text": "Abandon current tasks to work exclusively on the new request"}
                    ],
                    "correct_option": "B",
                    "difficulty": "intermediate"
                },
                {
                    "competency": "decision_making",
                    "question_type": "mcq",
                    "prompt": "When choosing between a quick hardcoded fix and a refactored modular solution, what guides the decision?",
                    "options": [
                        {"id": "A", "text": "Always choose hardcoded fixes to save time"},
                        {"id": "B", "text": "Balance urgency, technical debt risk, system stability, and future maintainability"},
                        {"id": "C", "text": "Refactor the entire codebase every time a minor bug occurs"},
                        {"id": "D", "text": "Ignore the bug"}
                    ],
                    "correct_option": "B",
                    "difficulty": "intermediate"
                },
                {
                    "competency": "professionalism",
                    "question_type": "short_answer",
                    "prompt": "How do you incorporate constructive critical feedback on your code during a peer code review?",
                    "rubric_keywords": ["feedback", "understand", "refactor", "test", "collaborate"],
                    "difficulty": "intermediate"
                }
            ]
        }

    def _generate_mock_dynamic_task(self, prompt: str) -> Dict[str, Any]:
        return {
            "title": "Implement API Rate Limiting & Validation Layer",
            "description": "Design and implement middleware for input validation and rate limiting to protect backend services.",
            "acceptance_criteria": [
                "Validate incoming request parameters against defined schemas",
                "Enforce rate limits per IP/client token",
                "Include unit tests covering valid and invalid payloads"
            ],
            "priority": "high",
            "task_type": "coding",
            "difficulty": "intermediate",
            "estimated_hours": 4.0,
            "dependencies": [],
            "hints": [
                "Review request validation schemas before writing custom parsers.",
                "Consult with the DevOps engineer if configuring environment rate limits."
            ],
            "recommended_team_role": "Backend Developer",
            "generation_reason": "Generated dynamically based on student's baseline proficiency and current sprint objectives."
        }

    def _generate_mock_adaptability_change(self, prompt: str) -> Dict[str, Any]:
        return {
            "title": "Requirement Change: Add Strict Token Authentication",
            "description": "Due to updated compliance standards, your current task requires enforcing Bearer token verification on all endpoints.",
            "original_requirement": "Implement standard session validation.",
            "updated_requirement": "Enforce stateless JWT Bearer token authentication with scope validation.",
            "reason": "Compliance requirement update mid-sprint."
        }

    def _generate_mock_review(self) -> Dict[str, Any]:
        """Generate a deterministic mock review response for submission workflows."""
        self._technical_review_calls += 1
        result = "changes_required" if self._technical_review_calls == 1 else "approved"
        
        return {
            "result": result,
            "score": 82,
            "severity": "warning",
            "summary": "The submission implements the required functionality with good code structure. Tests are included and pass.",
            "strengths": [
                "Clean code organization",
                "Proper error handling",
                "Good test coverage"
            ],
            "issues": [
                "Missing docstrings in some functions",
                "Could add more edge case tests"
            ],
            "risks": [
                "Potential performance bottlenecks under load",
                "Limited error handling for edge cases"
            ],
            "required_changes": [
                "Add docstrings to public functions",
                "Add tests for edge cases"
            ],
            "acceptance_criteria": [
                {
                    "criterion": "Implementation meets functional requirements",
                    "met": True,
                    "evidence": "Code implements all required functions"
                },
                {
                    "criterion": "Automated tests are included and passing",
                    "met": True,
                    "evidence": "Test file present with passing tests"
                },
                {
                    "criterion": "Code follows project architecture and review standards",
                    "met": True,
                    "evidence": "Code structure matches project conventions"
                }
            ],
            "code_quality": {
                "score": 85,
                "feedback": "Well-structured code with good separation of concerns"
            },
            "testing": {
                "score": 80,
                "feedback": "Tests cover main functionality, could add edge cases"
            },
            "security": {
                "score": 90,
                "feedback": "No security issues found"
            },
            "performance": {
                "score": 85,
                "feedback": "Efficient implementation"
            }
        }

    def _generate_mock_manager_review(self) -> Dict[str, Any]:
        """Generate a deterministic mock manager review response."""
        return {
            "decision": "approved",
            "severity": "info",
            "summary": "Manager review confirms the AI assessment. The submission meets requirements.",
            "feedback": "Good work on the implementation. The code is clean and well-tested. Continue following the project standards."
        }

    @staticmethod
    def _extract_domain(prompt: str) -> str:
        lowered = prompt.lower()
        for keyword in [
            "healthcare", "banking", "e-commerce", "ecommerce", "education",
            "logistics", "retail", "fintech", "manufacturing",
        ]:
            if keyword in lowered:
                return keyword
        return "technology"


def is_transient_ai_overload(exc: BaseException) -> bool:
    """True when an exception is a transient Gemini overload (HTTP 503).

    Used to decide between a quick retry / friendly error and letting any
    other failure propagate exactly as before.
    """
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) == 503


class GeminiAIService(AIService):
    """Real Gemini-backed implementation, used once GEMINI_API_KEY is configured."""

    provider_name = "gemini"

    # Three total requests means at most two retries. This matches the review
    # endpoint's total timeout (3 * 20s plus 1s + 2s backoff, with headroom).
    OVERLOAD_MAX_ATTEMPTS = 3
    REQUEST_TIMEOUT_SECONDS = 20.0
    OVERLOAD_RETRY_DELAYS_SECONDS = (1.0, 2.0)

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.base_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        )

    async def _call(self, prompt: str, system_prompt: Optional[str], *, json_mode: bool = False) -> str:
        contents: List[Dict[str, Any]] = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": f"[SYSTEM]\n{system_prompt}"}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        is_baseline = "baseline assessment" in (system_prompt or "").lower()
        is_evaluation = "answer evaluator" in (system_prompt or "").lower()
        generation: Dict[str, Any] = {
            # Review JSON contains a full rubric plus criterion-by-criterion
            # evidence; 512 tokens can truncate valid reviews before parsing.
            "maxOutputTokens": 4096 if is_baseline else (2048 if json_mode else 256),
        }
        # Gemini 3.5+ deprecates sampling controls; omit temperature for those
        # models to avoid forward-incompatible request errors.
        if not re.match(r"gemini-3\.(5|6|8)-", self.model):
            generation["temperature"] = 0.2 if is_evaluation else (0.7 if is_baseline else 0.2)
        if json_mode:
            generation["responseMimeType"] = "application/json"
            if "senior engineering reviewer" in (system_prompt or "").lower():
                generation["responseSchema"] = AI_REVIEW_RESPONSE_SCHEMA

        is_review_request = "senior engineering reviewer" in (system_prompt or "").lower()
        max_attempts = self.OVERLOAD_MAX_ATTEMPTS if is_review_request else 2
        async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT_SECONDS) as client:
            last_error: Optional[Exception] = None
            for attempt in range(max_attempts):
                request_started = time.perf_counter()
                try:
                    response = await client.post(
                        self.base_url,
                        headers={"x-goog-api-key": self.api_key},
                        json={"contents": contents, "generationConfig": generation},
                    )
                    response.raise_for_status()
                    data = response.json()
                    candidates = data.get("candidates") if isinstance(data, dict) else None
                    if not candidates:
                        feedback = data.get("promptFeedback") if isinstance(data, dict) else None
                        block_reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
                        raise AIProviderResponseError(
                            f"provider returned no candidate (block_reason={block_reason or 'none'})"
                        )
                    candidate = candidates[0]
                    content = candidate.get("content") if isinstance(candidate, dict) else None
                    parts = content.get("parts") if isinstance(content, dict) else None
                    text = parts[0].get("text") if parts and isinstance(parts[0], dict) else None
                    if not isinstance(text, str) or not text.strip():
                        raise AIProviderResponseError("provider candidate did not contain text")
                    logger.info(
                        "Gemini request completed model=%s attempt=%d duration_ms=%d",
                        self.model,
                        attempt + 1,
                        int((time.perf_counter() - request_started) * 1000),
                    )
                    return text
                except Exception as exc:
                    last_error = exc
                    response = getattr(exc, "response", None)
                    status_code = getattr(response, "status_code", None)
                    transient = (
                        is_transient_ai_overload(exc)
                        or status_code == 429
                        or (status_code is not None and status_code >= 500)
                        or isinstance(exc, httpx.RequestError)
                    )
                    if not transient or attempt + 1 >= max_attempts:
                        raise
                    status_code = getattr(getattr(exc, "response", None), "status_code", None)
                    delay = self.OVERLOAD_RETRY_DELAYS_SECONDS[min(attempt, len(self.OVERLOAD_RETRY_DELAYS_SECONDS) - 1)]
                    logger.warning(
                        "Gemini transient request failure model=%s status_code=%s attempt=%d/%d duration_ms=%d; retrying after %.1fs",
                        self.model,
                        status_code,
                        attempt + 1,
                        max_attempts,
                        int((time.perf_counter() - request_started) * 1000),
                        delay,
                    )
                    await asyncio.sleep(delay)
            assert last_error is not None
            raise last_error

    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        return await self._call(prompt, system_prompt, json_mode=False)

    async def generate_json(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        json_instruction = (
            "Respond ONLY with valid JSON. No markdown fences, no preamble, no commentary, no reasoning."
        )
        full_system = f"{system_prompt}\n{json_instruction}" if system_prompt else json_instruction
        cache_key = hashlib.sha256(
            f"{self.model}|json|{full_system}|{prompt}".encode("utf-8")
        ).hexdigest()
        # A malformed review must not be replayed from this response cache on
        # a retry. Each attempt should get a fresh provider evaluation.
        cached = _JSON_CACHE.get(cache_key) if is_json_response_cacheable(system_prompt) else None
        if cached is not None:
            return dict(cached)
        raw = await self._call(prompt, full_system, json_mode=True)
        try:
            parsed = parse_json_response(raw)
            _log_provider_json_response(self.model, raw, parsed)
        except MalformedAIJSONError as parse_error:
            _log_provider_json_response(self.model, raw, error=str(parse_error))
            # Structured output is enabled for reviews. Malformed output is a
            # validation failure, not a reason to spend another model call.
            raise
        if not isinstance(parsed, dict):
            # Keep the interface contract explicit; arrays/scalars are not an
            # evaluation object and must never reach score persistence.
            raise MalformedAIJSONError("provider JSON root must be an object")
        if is_json_response_cacheable(system_prompt):
            if len(_JSON_CACHE) >= _JSON_CACHE_LIMIT:
                _JSON_CACHE.pop(next(iter(_JSON_CACHE)))
            _JSON_CACHE[cache_key] = parsed
        return dict(parsed)


def get_ai_service() -> AIService:
    if settings.GEMINI_API_KEY:
        global _GEMINI_AI_SERVICE
        if _GEMINI_AI_SERVICE is None or _GEMINI_AI_SERVICE.model != settings.GEMINI_MODEL:
            _GEMINI_AI_SERVICE = GeminiAIService(api_key=settings.GEMINI_API_KEY, model=settings.GEMINI_MODEL)
        return _GEMINI_AI_SERVICE

    global _MOCK_AI_SERVICE
    if _MOCK_AI_SERVICE is None:
        _MOCK_AI_SERVICE = MockAIService()
    return _MOCK_AI_SERVICE
