"""Question bank, baseline assessment scoring, and skill catalog."""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

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
from app.services.intelligence_service import COMPETENCIES, get_or_create_profile, serialize_profile
from app.services.recommendation import build_simulation_recommendation

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
    questions = db.execute(
        select(AssessmentQuestion).where(AssessmentQuestion.active.is_(True)).order_by(AssessmentQuestion.created_at)
    ).scalars().all()
    if not questions:
        raise HTTPException(status.HTTP_409_CONFLICT, "The question bank is empty")
    attempt = AssessmentAttempt(
        student_id=student_id,
        status="in_progress",
        question_ids=[str(question.id) for question in questions],
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
    }


def _score_answer(question: AssessmentQuestion, response: str) -> float:
    text = (response or "").strip()
    if question.question_type == "mcq":
        return 100.0 if text.upper() == (question.correct_option or "").upper() else 35.0
    keywords = [word.lower() for word in (question.rubric_keywords or [])]
    if not keywords:
        return 70.0 if len(text.split()) >= 8 else 40.0
    hits = sum(1 for word in keywords if word in text.lower())
    return float(min(100, 30 + hits * 14))


def submit_attempt(db: Session, student_id: str, attempt_id: UUID, answers: List[Dict[str, str]]) -> Dict[str, Any]:
    attempt = db.get(AssessmentAttempt, attempt_id)
    if attempt is None or attempt.student_id != student_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment attempt not found")
    if attempt.status != "in_progress":
        raise HTTPException(status.HTTP_409_CONFLICT, "This assessment is already submitted")
    expected = set(attempt.question_ids or [])
    provided = {item["question_id"] for item in answers}
    if provided != expected:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Submit one answer for every question in this attempt")

    questions = {
        str(row.id): row
        for row in db.execute(select(AssessmentQuestion).where(AssessmentQuestion.id.in_([UUID(value) for value in expected]))).scalars().all()
    }
    by_competency: Dict[str, List[float]] = {key: [] for key in COMPETENCIES}
    stored_answers: List[AssessmentAnswer] = []
    for item in answers:
        question = questions[item["question_id"]]
        score = _score_answer(question, item["response"])
        by_competency.setdefault(question.competency, []).append(score)
        stored_answers.append(
            AssessmentAnswer(
                attempt_id=attempt.id,
                question_id=question.id,
                response=item["response"],
                score=score,
                competency=question.competency,
            )
        )
    db.add_all(stored_answers)

    scores = {}
    for key in COMPETENCIES:
        values = by_competency.get(key) or []
        scores[key] = round(statistics.mean(values), 2) if values else 60.0
    overall = round(statistics.mean(scores.values()), 2)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    strengths = [key.replace("_", " ").title() for key, _value in ranked[:3]]
    weaknesses = [key.replace("_", " ").title() for key, _value in sorted(scores.items(), key=lambda item: item[1])[:3]]
    difficulty = "advanced" if overall >= 78 else "intermediate" if overall >= 58 else "beginner"

    profile = get_or_create_profile(db, student_id)
    profile.baseline_competencies = scores
    profile.current_competencies = scores.copy()
    profile.strengths = strengths
    profile.weaknesses = weaknesses
    profile.assessment_summary = (
        f"Baseline assessment completed with an average competency score of {overall:.0f}/100."
    )
    profile.recommended_difficulty = difficulty
    profile.last_assessed_at = datetime.now(timezone.utc)
    profile.recommended_parameters = build_simulation_recommendation(profile)
    db.add(profile)

    attempt.status = "completed"
    attempt.overall_score = overall
    attempt.competencies = scores
    attempt.completed_at = datetime.now(timezone.utc)
    db.add(attempt)
    db.flush()
    return {
        "attempt_id": str(attempt.id),
        "overall_score": overall,
        "competencies": scores,
        "profile": serialize_profile(profile),
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
        "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
    }
