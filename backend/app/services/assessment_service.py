"""Question bank, baseline assessment scoring, and skill catalog."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.assessment import (
    AssessmentAnswer,
    AssessmentAttempt,
    AssessmentQuestion,
    Skill,
    StudentSkill,
)
from app.services.ai.ai_service import get_ai_service
from app.services.intelligence_service import COMPETENCIES, get_or_create_profile, serialize_profile
from app.services.recommendation import (
    BASELINE_FOCUS_THRESHOLD,
    BASELINE_STRENGTH_THRESHOLD,
    build_simulation_recommendation,
)

logger = logging.getLogger(__name__)

BASELINE_COMPETENCY_MAP = {
    "technical_knowledge": ("technical_knowledge",),
    "problem_solving": ("problem_solving",),
    "critical_thinking": ("critical_thinking",),
    "decision_making": ("decision_making",),
}
BASELINE_MCQ_CORRECT_SCORE = 100.0
BASELINE_MCQ_INCORRECT_SCORE = 35.0


def calculate_baseline_results(
    question_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Calculate baseline totals and evidence-backed competencies from question scores."""
    if not question_results:
        raise ValueError("A baseline result requires at least one answered question")
    scores_by_competency: Dict[str, List[float]] = {}
    question_scores = []
    for result in question_results:
        score = result.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
            raise ValueError("Question scores must be numeric values from 0 to 100")
        question_scores.append(float(score))
        for competency in BASELINE_COMPETENCY_MAP.get(result.get("competency"), ()):
            scores_by_competency.setdefault(competency, []).append(float(score))

    overall = statistics.fmean(question_scores)
    competencies = {
        key: statistics.fmean(values)
        for key, values in scores_by_competency.items()
        if values
    }
    strengths = [
        key.replace("_", " ").title()
        for key, score in sorted(competencies.items(), key=lambda item: (-item[1], item[0]))
        if score >= BASELINE_STRENGTH_THRESHOLD
    ]
    focus_areas = [
        key.replace("_", " ").title()
        for key, score in sorted(competencies.items(), key=lambda item: (item[1], item[0]))
        if score < BASELINE_FOCUS_THRESHOLD
    ]
    return {
        "overall_score": overall,
        "competencies": competencies,
        "strengths": strengths,
        "focus_areas": focus_areas,
        "unassessed_competencies": [key for key in COMPETENCIES if key not in competencies],
    }


def baseline_difficulty(overall_score: float) -> str:
    """Apply the established baseline thresholds to its one overall score."""
    return "advanced" if overall_score >= 78 else "intermediate" if overall_score >= 58 else "beginner"

SKILL_CATALOG = [
    ("Python", "technical", "General-purpose language used across backend and data work."),
    ("FastAPI", "technical", "Python API framework."),
    ("PostgreSQL", "technical", "Relational database used by the internship simulation."),
    ("JavaScript", "technical", "Language of the browser and many full-stack services."),
    ("React", "technical", "Component UI library."),
    ("SQL", "technical", "Querying and shaping relational data."),
    ("Git", "technical", "Version control and collaborative change history."),
    ("Debugging", "technical", "Finding and isolating defects from evidence."),
    ("REST APIs", "technical", "Designing and consuming HTTP interfaces."),
    ("Docker", "technical", "Packaging and running services consistently."),
    ("Testing", "technical", "Proving behavior with automated checks."),
    ("Communication", "workplace", "Explaining decisions to teammates and managers."),
]

QUESTION_BANK = [
    {
        "competency": "technical_knowledge",
        "question_type": "mcq",
        "prompt": "A teammate asks you to store user passwords. Which approach is appropriate?",
        "options": [
            {"id": "A", "text": "Store the raw password so support can read it back."},
            {"id": "B", "text": "Store a salted password hash and never keep the original password."},
            {"id": "C", "text": "Email the password to the manager after each login."},
            {"id": "D", "text": "Encode the password with Base64 and store that string."},
        ],
        "correct_option": "B",
    },
    {
        "competency": "problem_solving",
        "question_type": "mcq",
        "prompt": "A feature works on your machine and fails in the shared environment. What do you do first?",
        "options": [
            {"id": "A", "text": "Rewrite the feature before reading the error."},
            {"id": "B", "text": "Compare the error, inputs, and environment differences, then form a hypothesis."},
            {"id": "C", "text": "Mark the task done and wait for the client to complain."},
            {"id": "D", "text": "Delete the failing test so the pipeline is green."},
        ],
        "correct_option": "B",
    },
    {
        "competency": "decision_making",
        "question_type": "mcq",
        "prompt": "The sprint deadline is tomorrow and a new requirement conflicts with a security fix. What is the stronger decision?",
        "options": [
            {"id": "A", "text": "Ship the requirement and leave the security fix undocumented."},
            {"id": "B", "text": "State the trade-off, protect the security fix, and ask the manager which scope can slip."},
            {"id": "C", "text": "Ignore both and start a new feature."},
            {"id": "D", "text": "Disable authentication so the requirement is easier to demo."},
        ],
        "correct_option": "B",
    },
    {
        "competency": "communication",
        "question_type": "short_answer",
        "prompt": "You are blocked by another team's API. Write the message you would send to your manager.",
        "rubric_keywords": ["blocked", "api", "impact", "need", "next"],
    },
    {
        "competency": "critical_thinking",
        "question_type": "mcq",
        "prompt": "A dashboard shows a sudden drop in sign-ups. Which conclusion is justified?",
        "options": [
            {"id": "A", "text": "The product is failing, so the team should pivot immediately."},
            {"id": "B", "text": "The chart proves a competitor launched today."},
            {"id": "C", "text": "The drop is a signal to check tracking, releases, and the data source before deciding."},
            {"id": "D", "text": "Charts are never useful, so ignore it."},
        ],
        "correct_option": "C",
    },
    {
        "competency": "adaptability",
        "question_type": "mcq",
        "prompt": "Mid-sprint, the client changes the acceptance criteria. What should you do?",
        "options": [
            {"id": "A", "text": "Keep building the old criteria so your original plan stays intact."},
            {"id": "B", "text": "Confirm the new criteria, identify what changes, and update the task with the team."},
            {"id": "C", "text": "Delete the work already done."},
            {"id": "D", "text": "Hide the change from the manager until the demo."},
        ],
        "correct_option": "B",
    },
    {
        "competency": "time_management",
        "question_type": "mcq",
        "prompt": "You have three tasks and only enough time for two. How do you choose?",
        "options": [
            {"id": "A", "text": "Start all three and finish none."},
            {"id": "B", "text": "Pick the highest-priority unblocked work, say what will slip, and protect a review buffer."},
            {"id": "C", "text": "Work only on the task you enjoy."},
            {"id": "D", "text": "Wait until someone assigns a fourth task."},
        ],
        "correct_option": "B",
    },
    {
        "competency": "professionalism",
        "question_type": "short_answer",
        "prompt": "A code review points out a mistake in your pull request. How do you respond?",
        "rubric_keywords": ["thanks", "fix", "test", "review", "agree"],
    },
]


def ensure_skill_catalog(db: Session) -> List[Skill]:
    existing = {skill.name.lower(): skill for skill in db.execute(select(Skill)).scalars().all()}
    for name, category, description in SKILL_CATALOG:
        if name.lower() in existing:
            continue
        skill = Skill(name=name, category=category, description=description)
        db.add(skill)
        existing[name.lower()] = skill
    db.flush()
    return list(existing.values())


def ensure_question_bank(db: Session) -> None:
    count = db.execute(select(AssessmentQuestion.id).limit(1)).first()
    if count:
        return
    for item in QUESTION_BANK:
        db.add(
            AssessmentQuestion(
                prompt=item["prompt"],
                competency=item["competency"],
                question_type=item["question_type"],
                options=item.get("options", []),
                correct_option=item.get("correct_option"),
                rubric_keywords=item.get("rubric_keywords", []),
                difficulty="intermediate",
                domain="software",
                active=True,
            )
        )
    db.flush()


def list_skills(db: Session) -> List[Dict[str, Any]]:
    ensure_skill_catalog(db)
    skills = db.execute(select(Skill).order_by(Skill.category, Skill.name)).scalars().all()
    return [_skill_public(skill) for skill in skills]


def _skill_public(skill: Skill) -> Dict[str, Any]:
    return {
        "id": str(skill.id),
        "name": skill.name,
        "category": skill.category,
        "description": skill.description,
    }


def list_student_skills(db: Session, student_id: str) -> List[Dict[str, Any]]:
    ensure_skill_catalog(db)
    rows = db.execute(select(StudentSkill).where(StudentSkill.student_id == student_id)).scalars().all()
    return [
        {
            "id": str(row.id),
            "skill_id": str(row.skill_id),
            "name": row.skill.name if row.skill else None,
            "category": row.skill.category if row.skill else None,
            "self_rating": row.self_rating,
        }
        for row in rows
    ]


def replace_student_skills(db: Session, student_id: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ensure_skill_catalog(db)
    catalog = {skill.name.lower(): skill for skill in db.execute(select(Skill)).scalars().all()}
    db.query(StudentSkill).filter(StudentSkill.student_id == student_id).delete()
    names: List[str] = []
    for item in items:
        skill = catalog.get(str(item["name"]).strip().lower())
        if skill is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown skill: {item['name']}")
        rating = int(item.get("self_rating", 3))
        if rating < 1 or rating > 5:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "self_rating must be between 1 and 5")
        db.add(StudentSkill(student_id=student_id, skill_id=skill.id, self_rating=rating))
        names.append(skill.name)
    profile = get_or_create_profile(db, student_id)
    profile.skills = names
    db.add(profile)
    db.flush()
    return list_student_skills(db, student_id)


def _public_question(question: AssessmentQuestion) -> Dict[str, Any]:
    return {
        "id": str(question.id),
        "prompt": question.prompt,
        "competency": question.competency,
        "question_type": question.question_type,
        "options": question.options or [],
        "difficulty": question.difficulty,
    }


def _admin_question(question: AssessmentQuestion) -> Dict[str, Any]:
    payload = _public_question(question)
    payload.update(
        {
            "correct_option": question.correct_option,
            "rubric_keywords": question.rubric_keywords or [],
            "domain": question.domain,
            "active": question.active,
        }
    )
    return payload


def list_questions_admin(db: Session) -> List[Dict[str, Any]]:
    ensure_question_bank(db)
    rows = db.execute(select(AssessmentQuestion).order_by(AssessmentQuestion.created_at)).scalars().all()
    return [_admin_question(row) for row in rows]


def create_question(db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
    competency = payload["competency"]
    if competency not in COMPETENCIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"competency must be one of {COMPETENCIES}")
    question_type = payload.get("question_type", "mcq")
    if question_type not in {"mcq", "short_answer"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "question_type must be mcq or short_answer")
    if question_type == "mcq" and not payload.get("correct_option"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MCQ questions require correct_option")
    question = AssessmentQuestion(
        prompt=payload["prompt"],
        competency=competency,
        question_type=question_type,
        options=payload.get("options") or [],
        correct_option=payload.get("correct_option"),
        rubric_keywords=payload.get("rubric_keywords") or [],
        difficulty=payload.get("difficulty") or "intermediate",
        domain=payload.get("domain") or "software",
        active=payload.get("active", True),
    )
    db.add(question)
    db.flush()
    return _admin_question(question)


def update_question(db: Session, question_id: UUID, payload: Dict[str, Any]) -> Dict[str, Any]:
    question = db.get(AssessmentQuestion, question_id)
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    for field in ("prompt", "competency", "question_type", "options", "correct_option", "rubric_keywords", "difficulty", "domain", "active"):
        if field in payload and payload[field] is not None:
            setattr(question, field, payload[field])
    if question.competency not in COMPETENCIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"competency must be one of {COMPETENCIES}")
    db.add(question)
    db.flush()
    return _admin_question(question)


def delete_question(db: Session, question_id: UUID) -> None:
    question = db.get(AssessmentQuestion, question_id)
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    question.active = False
    db.add(question)
    db.flush()


async def generate_dynamic_baseline_questions(
    db: Session, student_id: str, attempt_seed: Optional[str] = None
) -> tuple[List[AssessmentQuestion], str]:
    ensure_question_bank(db)
    profile = get_or_create_profile(db, student_id)
    student_skills = list_student_skills(db, student_id)

    role = profile.role or profile.career_goal or "Software Engineer"
    attempt_seed = attempt_seed or str(uuid4())
    role_context = f"{role} {profile.domain or ''}".lower()
    skills_context = [
        {"name": s.get("name"), "rating": s.get("self_rating")}
        for s in student_skills if s.get("name")
    ]

    prompt = (
        f"Generate a dynamic baseline assessment for student ID '{student_id}'.\n"
        f"Target Role: {role}\n"
        f"Fresh attempt variation seed: {attempt_seed}\n"
        f"Self-Rated Skills & Proficiency: {skills_context}\n"
        f"Education: Degree={profile.degree or 'N/A'}, College={profile.college or 'N/A'}\n\n"
        f"Existing assessment state: {profile.assessment_summary or 'no completed baseline'}; "
        f"prior competency evidence: {profile.current_competencies or {}}.\n"
        "Requirements: return exactly 7 role-specific technical questions, using only "
        "technical_knowledge, problem_solving, critical_thinking, and decision_making. "
        "Use concrete engineering/data scenarios, code, queries, or constraints. Mix MCQ and "
        "short_answer. Every MCQ needs four distinct options and correct_option; every short "
        "answer needs rubric_keywords. Avoid workplace/soft-skill questions. Return JSON {questions:[...]}."
    )
    system_prompt = (
        "You are an AI Assessment Engine. Create dynamic baseline assessment questions tailored to the student's target role and self-rated skills."
    )

    ai_service = get_ai_service()
    questions_list: List[AssessmentQuestion] = []
    source = "ai"
    seen: set[str] = set()
    allowed_competencies = {"technical_knowledge", "problem_solving", "critical_thinking", "decision_making"}
    try:
        data = await ai_service.generate_json(prompt, system_prompt)
        raw_questions = data.get("questions") if isinstance(data, dict) else None
        if not isinstance(raw_questions, list) or len(raw_questions) != 7:
            raise ValueError("AI response must contain exactly 7 questions")
        for item in raw_questions:
            if not isinstance(item, dict):
                raise ValueError("Question must be an object")
            text = str(item.get("prompt", "")).strip()
            normalized = re.sub(r"\W+", " ", text.lower()).strip()
            kind = item.get("question_type")
            comp = item.get("competency")
            difficulty = item.get("difficulty", "intermediate")
            if len(text) < 20 or normalized in seen or comp not in allowed_competencies or kind not in {"mcq", "short_answer"} or difficulty not in {"beginner", "intermediate", "advanced"}:
                raise ValueError("Invalid, duplicate, or nontechnical question")
            options = item.get("options") or []
            if re.search(r"\b(manager|teammate|stakeholder|communicat(?:e|ion)|deadline|professionalism|feedback)\b", text, re.I) or any(
                re.search(r"\b(manager|teammate|stakeholder|communicat(?:e|ion)|deadline|professionalism|feedback)\b", str(option.get("text", "")), re.I)
                for option in options if isinstance(option, dict)
            ):
                raise ValueError("Soft-skill question rejected")
            correct = item.get("correct_option")
            rubric = item.get("rubric_keywords") or []
            if kind == "mcq":
                ids = [o.get("id") for o in options if isinstance(o, dict)]
                if len(options) != 4 or len(ids) != 4 or len(set(ids)) != 4 or correct not in ids or any(not o.get("text") for o in options):
                    raise ValueError("MCQ options or answer key are invalid")
            elif options or not isinstance(rubric, list) or not rubric or any(not isinstance(keyword, str) or not keyword.strip() for keyword in rubric):
                raise ValueError("Short-answer evaluation rubric is missing")
            seen.add(normalized)
            questions_list.append(AssessmentQuestion(
                prompt=text, competency=comp, question_type=kind, options=options,
                correct_option=correct, rubric_keywords=rubric,
                difficulty=difficulty, domain=role, active=True,
            ))
        # The set must contain material for this learner's role or rated stack.
        relevance = set(re.findall(r"[a-zA-Z+#.]{3,}", role.lower()))
        relevance.update(str(s["name"]).lower() for s in skills_context if s.get("name"))
        if not any(any(term in q.prompt.lower() for term in relevance) for q in questions_list):
            raise ValueError("AI questions are not relevant to the role or skills")
    except Exception as exc:
        logger.warning("AI baseline generation failed for student %s: %s; using personalized deterministic fallback", student_id, exc)
        questions_list = []
        source = "fallback"

    if not questions_list:
        questions_list = _personalized_fallback(role, role_context, skills_context, f"{student_id}:{attempt_seed}")
        for question in questions_list:
            db.add(question)
        db.flush()

    db.flush()
    return questions_list, source


def _personalized_fallback(role: str, role_context: str, skills: List[Dict[str, Any]], student_id: str) -> List[AssessmentQuestion]:
    """Build a role/skill-specific, per-student technical set without global bank reuse."""
    data_role = any(word in role_context for word in ("data", "ml", "machine learning", "scientist", "analyst"))
    relevant = [s for s in skills if s.get("name")]
    stack = [s["name"] for s in relevant]
    low = [s["name"] for s in relevant if int(s.get("rating") or 3) <= 2]
    focus = low or stack or (["Python", "SQL"] if data_role else ["Python", "REST APIs"])
    seed = int(hashlib.sha256(student_id.encode()).hexdigest()[:8], 16)
    variants = ["a delayed job is retried", "a batch has duplicate records", "a request fails under load", "a deployment exposes a regression"]
    scenario = variants[seed % len(variants)]
    technology = focus[seed % len(focus)]
    if data_role:
        prompts = [
            f"Using {technology}, how would you find and handle missing values before fitting a model? Include a validation check.",
            f"A data pipeline sees {scenario}. Which check best detects a row-count or schema regression?",
            f"Given imbalanced labels in a classification dataset, which metric would you report and why?",
            f"Write the SQL approach to select the latest event per customer without losing ties unexpectedly.",
            f"How would you prevent leakage when splitting time-ordered data for model evaluation?",
            f"A model's offline score is high but production precision falls after {scenario}. Name two slices or checks to investigate.",
            f"For {technology}, describe a reproducible preprocessing pipeline and how you would test it.",
        ]
    else:
        prompts = [
            f"In {technology}, where would you validate this request payload before persisting it, and what invalid case would you test?",
            f"A backend endpoint encounters {scenario}. Which log, metric, and code path would you inspect first?",
            f"How would you make a database write idempotent when a client retries after a timeout?",
            f"Given a slow SQL query joining orders to customers, what evidence guides your index choice?",
            f"How should an API distinguish malformed input, missing resources, and an unexpected server failure?",
            f"Describe a test for a race condition when two requests update the same record concurrently after {scenario}.",
            f"For {technology}, explain how you would trace a request across validation, business logic, and storage.",
        ]
    comps = ["technical_knowledge", "problem_solving", "critical_thinking", "decision_making", "problem_solving", "technical_knowledge", "critical_thinking"]
    return [AssessmentQuestion(prompt=p, competency=comps[i], question_type="short_answer", options=[], rubric_keywords=["validation", "test", "data", "error"], difficulty="intermediate", domain=role, active=True) for i, p in enumerate(prompts)]


def start_attempt(db: Session, student_id: str) -> Dict[str, Any]:
    ensure_question_bank(db)
    existing = db.execute(
        select(AssessmentAttempt).where(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.status == "in_progress",
        )
    ).scalars().first()
    if existing:
        return _attempt_payload(db, existing)

    try:
        attempt_seed = str(uuid4())
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            raise RuntimeError("start_attempt must run outside an async event loop")
        questions, source = asyncio.run(generate_dynamic_baseline_questions(db, student_id, attempt_seed))
    except Exception as exc:
        logger.warning("Error running dynamic baseline generation for %s: %s; using fallback bank", student_id, exc)
        profile = get_or_create_profile(db, student_id)
        role = profile.role or profile.career_goal or "Software Engineer"
        skills_context = [{"name": s.get("name"), "rating": s.get("self_rating")} for s in list_student_skills(db, student_id)]
        questions = _personalized_fallback(role, f"{role} {profile.domain or ''}".lower(), skills_context, f"{student_id}:{attempt_seed}")
        for question in questions:
            db.add(question)
        db.flush()
        source = "fallback"

    if not questions:
        raise HTTPException(status.HTTP_409_CONFLICT, "The question bank is empty")

    attempt = AssessmentAttempt(
        student_id=student_id,
        status="in_progress",
        question_ids=[str(q.id) for q in questions],
        generation_metadata={
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "question_count": len(questions),
            "dynamic": source == "ai",
            "source": source,
        },
        started_at=datetime.now(timezone.utc),
    )
    db.add(attempt)
    db.flush()
    return _attempt_payload(db, attempt)


def _attempt_payload(db: Session, attempt: AssessmentAttempt) -> Dict[str, Any]:
    ids = [UUID(value) for value in (attempt.question_ids or [])]
    questions = []
    if ids:
        rows = db.execute(select(AssessmentQuestion).where(AssessmentQuestion.id.in_(ids))).scalars().all()
        order = {value: index for index, value in enumerate(ids)}
        questions = [_public_question(row) for row in sorted(rows, key=lambda row: order.get(row.id, 0))]
    return {
        "id": str(attempt.id),
        "status": attempt.status,
        "started_at": attempt.started_at.isoformat(),
        "questions": questions,
        "generation": attempt.generation_metadata or {},
    }


def _provider_failure_category(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if code in (401, 403):
        return "provider_authentication"
    if code == 429:
        return "provider_rate_limit"
    if code is not None and code >= 500:
        return "provider_server_error"
    if isinstance(exc, asyncio.TimeoutError) or exc.__class__.__name__.lower().endswith("timeoutexception"):
        return "provider_timeout"
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "malformed_provider_response"
    return "provider_request_error"


def _parse_baseline_evaluation(data: Any, short_answer_ids: set[str]) -> Dict[str, Dict[str, Any]]:
    """Validate the provider's per-short-answer evaluations; never accept a total score."""
    if not isinstance(data, dict) or set(data) != {"evaluations"} or not isinstance(data.get("evaluations"), list):
        raise ValueError("evaluation response must contain an evaluations array")
    parsed: Dict[str, Dict[str, Any]] = {}
    for item in data["evaluations"]:
        if not isinstance(item, dict):
            raise ValueError("each evaluation must be an object")
        if set(item) != {"question_id", "score", "explanation", "feedback"}:
            raise ValueError("evaluation contains missing or unexpected fields")
        question_id = item.get("question_id")
        score = item.get("score")
        explanation = item.get("explanation")
        feedback = item.get("feedback")
        if question_id not in short_answer_ids or question_id in parsed:
            raise ValueError("evaluation question IDs are unexpected or duplicated")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
            raise ValueError("evaluation score must be numeric and between 0 and 100")
        if not isinstance(explanation, str) or not explanation.strip() or len(explanation) > 1000:
            raise ValueError("evaluation explanation is missing or invalid")
        if not isinstance(feedback, str) or not feedback.strip() or len(feedback) > 1000:
            raise ValueError("evaluation feedback is missing or invalid")
        parsed[question_id] = {
            "score": float(score),
            "explanation": explanation.strip(),
            "feedback": feedback.strip(),
        }
    if set(parsed) != short_answer_ids:
        raise ValueError("evaluation response is incomplete")
    return parsed


def _evaluate_attempt_answers(attempt_id: UUID, role: Optional[str], questions: Dict[str, AssessmentQuestion], answers: List[Dict[str, str]]):
    """Run one strict provider evaluation; MCQ correctness remains objectively graded here."""
    ai_service = get_ai_service()
    from app.services.ai.ai_service import MockAIService
    provider = getattr(ai_service, "provider_name", ai_service.__class__.__name__)
    model = getattr(ai_service, "model", None)
    if isinstance(ai_service, MockAIService):
        logger.error(
            "Baseline AI evaluation unavailable provider=mock model=none category=provider_not_configured attempt_id=%s",
            attempt_id,
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AI evaluation is not configured. Your attempt remains active; retry after a provider is configured.",
        )

    answer_by_id = {item["question_id"]: item["response"] for item in answers}
    short_ids = {qid for qid, question in questions.items() if question.question_type == "short_answer"}
    evaluation_input = []
    answer_results: Dict[str, Dict[str, Any]] = {}
    for question_id, question in questions.items():
        response = answer_by_id[question_id].strip()
        if question.question_type == "mcq":
            correct = response.upper() == (question.correct_option or "").upper()
            option_text = {str(option.get("id", "")).upper(): option.get("text", "") for option in (question.options or [])}
            correct_answer = option_text.get((question.correct_option or "").upper(), question.correct_option or "")
            explanation = "The selected option matches the answer key." if correct else f"The answer key is {question.correct_option}: {correct_answer}."
            answer_results[question_id] = {
                "score": BASELINE_MCQ_CORRECT_SCORE if correct else BASELINE_MCQ_INCORRECT_SCORE,
                "explanation": explanation,
                "feedback": "Correct." if correct else f"Review the concept behind the correct option: {correct_answer}.",
                "question_id": question_id,
                "competency": question.competency,
                "question": question.prompt,
                "response": response,
                "question_type": "mcq",
            }
        evaluation_input.append({
            "question_id": question_id,
            "question": question.prompt,
            "question_type": question.question_type,
            "options": question.options or [],
            "correct_option": question.correct_option,
            "rubric_keywords": question.rubric_keywords or [],
            "student_answer": response,
            "evaluate_with_ai": question.question_type == "short_answer",
        })

    prompt = (
        "Evaluate this baseline assessment answer set. Input JSON follows:\n"
        f"{json.dumps({'target_role': role or 'Software Engineer', 'items': evaluation_input}, ensure_ascii=False)}\n"
        "For each item where evaluate_with_ai is true, return one evaluation with exactly these fields: "
        "question_id, score (0-100), explanation, feedback. Judge the answer against that item's actual question and rubric_keywords; "
        "score correctness, relevance, completeness, and demonstrated technical understanding. Be specific to what the student wrote. "
        "Do not use a single generic score or feedback across answers. For MCQ items, do not grade them and do not include them in evaluations; "
        "their answer key is supplied only as context and they are graded objectively by the application. "
        "Return JSON only: {\"evaluations\":[...]}. Return exactly one entry for every short_answer question and no others. "
        "Do not return an overall or competency score."
    )
    try:
        data = asyncio.run(ai_service.generate_json(
            prompt,
            "You are the rubric-based evaluator for a baseline assessment. Return only strict JSON. System rubric: assess each short response independently.",
        ))
        short_results = _parse_baseline_evaluation(data, short_ids)
    except HTTPException:
        raise
    except Exception as exc:
        category = _provider_failure_category(exc)
        logger.error(
            "Baseline AI evaluation failed provider=%s model=%s category=%s attempt_id=%s",
            provider, model or "unknown", category, attempt_id,
            exc_info=True,
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AI evaluation is temporarily unavailable or returned an invalid result. Your attempt is unchanged; retry submission.",
        ) from exc

    for question_id, item in short_results.items():
        question = questions[question_id]
        answer_results[question_id] = {
            **item,
            "question_id": question_id,
            "competency": question.competency,
            "question": question.prompt,
            "response": answer_by_id[question_id],
            "question_type": "short_answer",
        }
    return answer_results, provider, model


def submit_attempt(db: Session, student_id: str, attempt_id: UUID, answers: List[Dict[str, str]]) -> Dict[str, Any]:
    attempt = db.execute(
        select(AssessmentAttempt).where(AssessmentAttempt.id == attempt_id).with_for_update()
    ).scalar_one_or_none()
    if attempt is None or attempt.student_id != student_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment attempt not found")
    if attempt.status != "in_progress":
        raise HTTPException(status.HTTP_409_CONFLICT, "This assessment is already submitted")
    expected = set(attempt.question_ids or [])
    provided = {item["question_id"] for item in answers}
    if len(answers) != len(expected) or provided != expected:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Submit one answer for every question in this attempt")
    if any(not isinstance(item.get("response"), str) or not item["response"].strip() for item in answers):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Every question needs a non-empty answer")

    questions = {
        str(row.id): row
        for row in db.execute(select(AssessmentQuestion).where(AssessmentQuestion.id.in_([UUID(value) for value in expected]))).scalars().all()
    }
    if set(questions) != expected:
        raise HTTPException(status.HTTP_409_CONFLICT, "One or more assessment questions are no longer available")
    profile = get_or_create_profile(db, student_id)
    answer_results, provider, model = _evaluate_attempt_answers(attempt.id, profile.role or profile.career_goal, questions, answers)
    existing_rows = db.execute(
        select(AssessmentAnswer).where(
            AssessmentAnswer.attempt_id == attempt.id,
            AssessmentAnswer.question_id.in_([UUID(value) for value in expected]),
        )
    ).scalars().all()
    existing_by_question: Dict[str, AssessmentAnswer] = {}
    for row in existing_rows:
        key = str(row.question_id)
        if key in existing_by_question:
            raise HTTPException(status.HTTP_409_CONFLICT, "This attempt has duplicate saved answers")
        existing_by_question[key] = row

    for item in answers:
        question = questions[item["question_id"]]
        score = answer_results[item["question_id"]]["score"]
        row = existing_by_question.get(item["question_id"])
        if row is None:
            row = AssessmentAnswer(attempt_id=attempt.id, question_id=question.id)
            db.add(row)
            existing_by_question[item["question_id"]] = row
        row.response = item["response"]
        row.score = score
        row.competency = question.competency
    db.flush()

    persisted_answers = db.execute(
        select(AssessmentAnswer).where(
            AssessmentAnswer.attempt_id == attempt.id,
            AssessmentAnswer.question_id.in_([UUID(value) for value in expected]),
        )
    ).scalars().all()
    if {str(row.question_id) for row in persisted_answers} != expected or len(persisted_answers) != len(expected):
        raise HTTPException(status.HTTP_409_CONFLICT, "Could not persist one answer for every question")
    question_results = [
        {"score": row.score, "competency": questions[str(row.question_id)].competency}
        for row in persisted_answers
    ]
    calculated = calculate_baseline_results(question_results)
    overall = calculated["overall_score"]
    scores = calculated["competencies"]
    strengths = calculated["strengths"]
    weaknesses = calculated["focus_areas"]
    difficulty = baseline_difficulty(overall)

    profile.baseline_competencies = scores
    profile.current_competencies = scores.copy()
    profile.strengths = strengths
    profile.weaknesses = weaknesses
    profile.assessment_summary = (
        f"Baseline assessment completed with an average question score of {overall:.2f}/100."
    )
    profile.recommended_difficulty = difficulty
    profile.last_assessed_at = datetime.now(timezone.utc)
    profile.recommended_parameters = build_simulation_recommendation(profile, overall_score=overall)
    db.add(profile)

    attempt.status = "completed"
    attempt.overall_score = overall
    attempt.competencies = scores
    attempt.completed_at = datetime.now(timezone.utc)
    metadata = dict(attempt.generation_metadata or {})
    metadata["evaluation"] = {
        "status": "completed",
        "provider": provider,
        "model": model,
        "overall_score": overall,
        "competencies": scores,
        "unassessed_competencies": calculated["unassessed_competencies"],
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "answers": [answer_results[item["question_id"]] for item in answers],
    }
    attempt.generation_metadata = metadata
    db.add(attempt)
    db.flush()
    return {
        "attempt_id": str(attempt.id),
        "overall_score": overall,
        "competencies": scores,
        "unassessed_competencies": calculated["unassessed_competencies"],
        "profile": serialize_profile(profile),
        "evaluation": metadata["evaluation"],
    }


def latest_result(db: Session, student_id: str) -> Optional[Dict[str, Any]]:
    attempt = db.execute(
        select(AssessmentAttempt)
        .where(AssessmentAttempt.student_id == student_id, AssessmentAttempt.status == "completed")
        .order_by(AssessmentAttempt.completed_at.desc())
    ).scalars().first()
    if attempt is None:
        return None
    return {
        "attempt_id": str(attempt.id),
        "overall_score": attempt.overall_score,
        "competencies": attempt.competencies or {},
        "unassessed_competencies": (attempt.generation_metadata or {}).get("evaluation", {}).get("unassessed_competencies", []),
        "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
        "evaluation": (attempt.generation_metadata or {}).get("evaluation"),
    }
