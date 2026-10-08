"""Adaptive AI + Module 4 intelligence service.

The service is deliberately provider-agnostic: with GEMINI_API_KEY empty it
uses deterministic local heuristics, so the full product can be tested offline.
When a key is supplied, the same structured operations are delegated to the
existing GeminiAIService.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.intelligence import (
    Certificate,
    EarlyWarningAlert,
    FeedbackReport,
    InteractionEvaluation,
    ReadinessSnapshot,
    StudentProfile,
)
from app.models.enums import SimulationStatus
from app.models.simulation import Simulation
from app.models.simulation_interaction import SimulationInteraction
from app.services.ai.ai_service import get_ai_service

COMPETENCIES = [
    "technical_knowledge",
    "problem_solving",
    "decision_making",
    "communication",
    "critical_thinking",
    "adaptability",
    "time_management",
    "professionalism",
]

DEFAULT_COMPETENCIES = {key: 60.0 for key in COMPETENCIES}

_SITUATIONS = {
    "decision_making": ("Requirement ambiguity", "A stakeholder changes an important requirement one day before a milestone. Decide how you respond while protecting delivery quality."),
    "problem_solving": ("Production incident", "A monitoring alert shows elevated errors shortly after your change. The manager asks for an immediate response while customer impact is still unclear."),
    "communication": ("Team conflict", "A teammate challenges your implementation approach during review and proposes a faster alternative with higher technical risk."),
    "critical_thinking": ("Priority collision", "Two tasks are now urgent and only one can be completed before the deadline. You must justify what gets prioritized."),
    "technical_knowledge": ("API failure", "A production API used by the current task is returning errors. Explain the checks you run before changing code."),
    "adaptability": ("Scope change", "The acceptance criteria change after you have started. Decide which completed work still applies."),
    "time_management": ("Deadline pressure", "The sprint deadline moves earlier. Decide what you finish, what you defer, and what you tell the manager."),
    "professionalism": ("Review feedback", "A reviewer rejects your submission with specific comments. Decide how you respond and what you change."),
}

_ADAPTIVE_EXPECTED_SECONDS = {"beginner": 180, "intermediate": 300, "advanced": 450}


def adaptive_expected_seconds(difficulty: Optional[str], task_expected_seconds: Optional[float] = None) -> float:
    """Use a task estimate when present, otherwise a centralized difficulty estimate."""
    if task_expected_seconds is not None and task_expected_seconds > 0:
        return float(task_expected_seconds)
    return float(_ADAPTIVE_EXPECTED_SECONDS.get(str(difficulty or "intermediate").lower(), 300))


def latest_adaptive_branch(db: Session, simulation: Simulation) -> Optional[str]:
    """Recover the last completed adaptive decision for next-task generation."""
    latest = db.execute(
        select(SimulationInteraction)
        .where(
            SimulationInteraction.simulation_id == simulation.id,
            SimulationInteraction.interaction_type == "adaptive_situation",
            SimulationInteraction.student_response.is_not(None),
        )
        .order_by(desc(SimulationInteraction.sequence))
        .limit(1)
    ).scalar_one_or_none()
    if latest is None:
        return None
    metadata = latest.metadata_json or {}
    decision = metadata.get("adaptive_decision") or metadata.get("evaluation") or {}
    branch = decision.get("branch_selected") or metadata.get("branch_selected")
    return branch if branch in {"high_performance", "stable", "risk_escalation"} else None


class EvaluationPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    overall_score: float = 60
    competencies: Dict[str, float] = Field(default_factory=dict)
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    mistakes: List[str] = Field(default_factory=list)
    feedback: str = ""
    recommendations: List[str] = Field(default_factory=list)
    next_focus: List[str] = Field(default_factory=list)
    branch_selected: str = "stable"
    confidence: float = 0.7


def _difficulty_delta(overall: float) -> int:
    if overall >= 80:
        return 1
    if overall < 55:
        return -1
    return 0


def skill_gap_fields(profile: StudentProfile) -> Dict[str, List[str]]:
    scores = profile.current_competencies or DEFAULT_COMPETENCIES
    gaps = [key for key, value in sorted(scores.items(), key=lambda item: float(item[1])) if float(value) < 70]
    return {"skill_gaps": gaps, "next_focus": gaps[:2]}


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return round(max(low, min(high, value)), 2)


def _stable_number(text: str, low: int, high: int) -> int:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return low + (int(digest[:8], 16) % (high - low + 1))


def get_or_create_profile(db: Session, student_id: str) -> StudentProfile:
    profile = db.execute(
        select(StudentProfile).where(StudentProfile.student_id == student_id)
    ).scalar_one_or_none()
    if profile:
        return profile
    profile = StudentProfile(
        student_id=student_id,
        baseline_competencies=dict(DEFAULT_COMPETENCIES),
        current_competencies=dict(DEFAULT_COMPETENCIES),
        recommended_difficulty="intermediate",
    )
    db.add(profile)
    db.flush()
    return profile


async def assess_student(
    db: Session,
    student_id: str,
    *,
    domain: Optional[str] = None,
    role: Optional[str] = None,
    skills: Optional[List[str]] = None,
    responses: Optional[List[str]] = None,
) -> Dict[str, Any]:
    profile = get_or_create_profile(db, student_id)
    skills = skills or profile.skills or []
    responses = responses or []

    payload = None
    if settings.GEMINI_API_KEY and responses:
        ai = get_ai_service()
        prompt = json.dumps({
            "role": role or profile.role or "intern",
            "domain": domain or profile.domain or "technology",
            "skills": skills[:8],
            "responses": [text[:500] for text in responses[:8]],
            "competency_keys": COMPETENCIES,
        })
        try:
            raw = await ai.generate_json(prompt, "Score the responses. Return JSON only with competencies, strengths, weaknesses, summary, recommended_difficulty. No reasoning.")
            competencies = raw.get("competencies") if isinstance(raw, dict) else None
            valid_scores = isinstance(competencies, dict) and all(
                key in competencies
                and isinstance(competencies[key], (int, float))
                and not isinstance(competencies[key], bool)
                and 0 <= competencies[key] <= 100
                for key in COMPETENCIES
            )
            payload = raw if valid_scores else None
        except Exception:
            payload = None
    if payload is None:
        combined = " ".join(responses).lower()
        scores = {}
        positive = sum(w in combined for w in ["because", "first", "verify", "test", "trade-off", "risk", "monitor"])
        technical = 60 + min(20, len(skills) * 2) + _stable_number(combined or student_id, 0, 10)
        scores.update({k: _clamp(technical) for k in COMPETENCIES})
        scores["problem_solving"] = _clamp(62 + positive * 4 + _stable_number(student_id, 0, 8))
        scores["decision_making"] = _clamp(58 + positive * 5 + _stable_number(student_id + "decision", 0, 9))
        scores["communication"] = _clamp(60 + min(20, len(combined.split()) // 12) + _stable_number(student_id + "comm", 0, 8))
        scores["critical_thinking"] = _clamp(60 + positive * 3 + _stable_number(student_id + "critical", 0, 9))
        scores["adaptability"] = _clamp(60 + _stable_number(student_id + "adapt", 0, 12))
        scores["time_management"] = _clamp(60 + _stable_number(student_id + "time", 0, 12))
        scores["professionalism"] = _clamp(65 + _stable_number(student_id + "prof", 0, 12))
        scores["technical_knowledge"] = _clamp(technical)
        strengths = [k.replace("_", " ").title() for k, v in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]]
        weaknesses = [k.replace("_", " ").title() for k, v in sorted(scores.items(), key=lambda x: x[1])[:3]]
        avg = statistics.mean(scores.values())
        payload = {
            "competencies": scores,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "summary": f"Baseline assessment completed with an average competency score of {avg:.0f}/100.",
            "recommended_difficulty": "advanced" if avg >= 78 else "intermediate" if avg >= 58 else "beginner",
        }

    scores = {k: _clamp(float(payload["competencies"][k])) for k in COMPETENCIES}
    profile.full_name = profile.full_name
    profile.domain = domain or profile.domain
    profile.role = role or profile.role
    profile.skills = skills
    profile.baseline_competencies = scores
    profile.current_competencies = scores.copy()
    profile.strengths = payload.get("strengths", [])
    profile.weaknesses = payload.get("weaknesses", [])
    profile.assessment_summary = payload.get("summary", "")
    profile.recommended_difficulty = payload.get("recommended_difficulty", "intermediate")
    from datetime import datetime, timezone
    profile.last_assessed_at = datetime.now(timezone.utc)
    from app.services.recommendation import build_simulation_recommendation

    profile.recommended_parameters = build_simulation_recommendation(profile)
    db.add(profile)
    db.flush()
    return serialize_profile(profile)


def serialize_profile(profile: StudentProfile) -> Dict[str, Any]:
    return {
        "student_id": profile.student_id,
        "full_name": profile.full_name,
        "domain": profile.domain,
        "role": profile.role,
        "college": profile.college,
        "degree": profile.degree,
        "year_of_study": profile.year_of_study,
        "career_goal": profile.career_goal,
        "skills": profile.skills or [],
        "baseline_competencies": profile.baseline_competencies or {},
        "current_competencies": profile.current_competencies or {},
        "strengths": profile.strengths or [],
        "weaknesses": profile.weaknesses or [],
        "assessment_summary": profile.assessment_summary,
        "recommended_difficulty": profile.recommended_difficulty,
        "recommended_parameters": profile.recommended_parameters or {},
        "last_assessed_at": profile.last_assessed_at.isoformat() if profile.last_assessed_at else None,
    }


async def generate_adaptive_situation(
    db: Session,
    simulation: Simulation,
    profile: StudentProfile,
    branch: Optional[str] = None,
    decided_difficulty: Optional[str] = None,
) -> Dict[str, Any]:
    interactions = db.execute(
        select(SimulationInteraction)
        .where(SimulationInteraction.simulation_id == simulation.id)
        .order_by(desc(SimulationInteraction.sequence))
        .limit(6)
    ).scalars().all()
    scores = profile.current_competencies or DEFAULT_COMPETENCIES
    weak = sorted(scores.items(), key=lambda item: float(item[1]))
    focus_key = weak[0][0] if weak else "decision_making"
    if branch == "high_performance" and scores:
        focus_key = max(scores, key=lambda key: float(scores[key]))
    elif branch == "stable":
        seen = {(item.metadata_json or {}).get("branch_hint") for item in interactions}
        if focus_key in seen and len(weak) > 1:
            focus_key = weak[1][0]
    if focus_key not in _SITUATIONS:
        focus_key = "decision_making"
    seq = len(interactions) + 1
    title, situation = _SITUATIONS[focus_key]
    if branch == "risk_escalation":
        title = f"Recovery: {title}"
        situation = "Work through this with a smaller first step. " + situation
    elif branch == "high_performance":
        title = f"Stretch: {title}"
    difficulty = (
        decided_difficulty
        or (simulation.difficulty.value if simulation.difficulty is not None else None)
        or profile.recommended_difficulty
        or "intermediate"
    )
    task_expected_seconds = None
    if simulation.current_task_id:
        from app.models.task import Task
        task = db.get(Task, simulation.current_task_id)
        if task is not None and task.estimated_hours:
            task_expected_seconds = float(task.estimated_hours) * 3600
    return {
        "title": f"Adaptive Situation {seq}: {title}",
        "situation": situation,
        "objective": f"Demonstrate {focus_key.replace('_', ' ')} in a realistic workplace decision.",
        "decision_options": ["Investigate and clarify before acting", "Act immediately with a documented risk", "Escalate with a concise recommendation"],
        "branch_hint": focus_key,
        "difficulty": difficulty,
        "expected_time": adaptive_expected_seconds(difficulty, task_expected_seconds),
        "consequence_if_weak": "The issue escalates and creates additional stakeholder pressure.",
        "consequence_if_strong": "The issue is contained and the team gains confidence in your judgment.",
    }


async def evaluate_interaction(
    db: Session,
    simulation: Simulation,
    interaction: SimulationInteraction,
    response: str,
) -> Dict[str, Any]:
    profile = get_or_create_profile(db, simulation.student_id)
    weak = sorted((profile.current_competencies or DEFAULT_COMPETENCIES).items(), key=lambda item: float(item[1]))[:2]
    target_skills = [key for key, _score in weak]
    payload = None
    if settings.GEMINI_API_KEY:
        ai = get_ai_service()
        prompt = json.dumps({
            "role": profile.role or simulation.role or "intern",
            "difficulty": profile.recommended_difficulty or "intermediate",
            "task": (interaction.situation or "")[:400],
            "student_response": response[:800],
            "target_skills": target_skills,
        })
        try:
            raw = await ai.generate_json(
                prompt,
                "Return JSON only with overall_score, competencies, strengths (max 3), weaknesses (max 3), mistakes (max 3), feedback (one short paragraph), recommendations (max 3), confidence. Scores 0-100. No reasoning.",
            )
            payload = EvaluationPayload.model_validate(raw).model_dump()
        except Exception:
            payload = None
    if payload is None:
        text = response.strip()
        words = text.split()
        lower = text.lower()
        reasoning = sum(x in lower for x in ["because", "first", "then", "verify", "risk", "impact", "test", "communicate", "escalate"])
        length_bonus = min(12, len(words) // 8)
        base = 52 + reasoning * 4 + length_bonus
        if any(x in lower for x in ["immediately", "ignore", "whatever", "don't know"]):
            base -= 12
        scores = {k: _clamp(base + _stable_number(str(interaction.id) + k, -5, 6)) for k in COMPETENCIES}
        scores["communication"] = _clamp(base + min(12, len(words) // 10))
        scores["decision_making"] = _clamp(base + reasoning * 2)
        overall = _clamp(statistics.mean(scores.values()))
        if overall >= 78:
            branch = "high_performance"
        elif overall < 58:
            branch = "risk_escalation"
        else:
            branch = "stable"
        payload = {
            "overall_score": overall,
            "competencies": scores,
            "strengths": [k.replace("_", " ").title() for k, v in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:2]],
            "weaknesses": [k.replace("_", " ").title() for k, v in sorted(scores.items(), key=lambda x: x[1])[:2]],
            "mistakes": (["Insufficient evidence or justification"] if reasoning < 2 else []),
            "feedback": "Your response was evaluated against reasoning, risk awareness, communication, and decision quality.",
            "recommendations": ["State your assumptions", "Explain the trade-off behind your decision", "Define a verification or follow-up step"],
            "branch_selected": branch,
            "difficulty_delta": 1 if overall >= 80 else -1 if overall < 55 else 0,
            "confidence": 0.78,
        }

    scores = {k: _clamp(float(payload.get("competencies", {}).get(k, 60))) for k in COMPETENCIES}
    overall = _clamp(float(payload.get("overall_score", statistics.mean(scores.values()))))
    branch = payload.get("branch_selected") or "stable"
    if branch not in {"risk_escalation", "stable", "high_performance"}:
        branch = "high_performance" if overall >= 78 else "risk_escalation" if overall < 58 else "stable"
    return {
        "overall_score": overall,
        "competencies": scores,
        "strengths": list(payload.get("strengths") or []),
        "weaknesses": list(payload.get("weaknesses") or []),
        "mistakes": list(payload.get("mistakes") or []),
        "feedback": str(payload.get("feedback") or ""),
        "recommendations": list(payload.get("recommendations") or []),
        "branch_selected": branch,
        "difficulty_delta": _difficulty_delta(overall),
        "confidence": max(0.0, min(1.0, float(payload.get("confidence", 0.75)))),
    }


def apply_adaptive_decision(db: Session, simulation: Simulation, profile: StudentProfile, evaluation: Dict[str, Any]) -> str:
    """Route workplace evidence to behavioral branches, never technical levels."""
    previous = simulation.difficulty.value if simulation.difficulty is not None else profile.recommended_difficulty
    response_time = evaluation.get("response_time")
    expected_time = evaluation.get("expected_time")
    speed_ratio = None
    if response_time is not None and expected_time is not None and float(expected_time) > 0:
        speed_ratio = float(response_time) / float(expected_time)

    # Workplace choices still select stretch/recovery situations and update
    # behavioral competencies. Technical task difficulty is only changed by
    # an evaluated technical submission.
    branch = evaluation.get("branch_selected") or "stable"
    evaluation["branch_selected"] = branch
    evaluation["difficulty_delta"] = 0
    evaluation["previous_difficulty"] = previous
    evaluation["next_difficulty"] = previous
    evaluation["speed_ratio"] = round(speed_ratio, 4) if speed_ratio is not None else None
    evaluation["evidence_kind"] = "behavioral"
    return previous


def update_competencies(db: Session, profile: StudentProfile, scores: Dict[str, float]) -> Dict[str, float]:
    current = dict(profile.current_competencies or DEFAULT_COMPETENCIES)
    # Exponential moving update: recent demonstrated performance matters more,
    # while the baseline still prevents a single response from swinging wildly.
    for key in COMPETENCIES:
        current[key] = _clamp(0.70 * current.get(key, 60) + 0.30 * scores.get(key, current.get(key, 60)))
    profile.current_competencies = current
    profile.strengths = [k.replace("_", " ").title() for k, _ in sorted(current.items(), key=lambda x: x[1], reverse=True)[:3]]
    profile.weaknesses = [k.replace("_", " ").title() for k, _ in sorted(current.items(), key=lambda x: x[1])[:3]]
    # Competency changes are retained as behavioral evidence, but they do not
    # overwrite the baseline or technical difficulty recommendation.
    db.add(profile)
    db.flush()
    return current


async def create_feedback_report(db: Session, student_id: str, simulation_id: Optional[UUID] = None) -> Dict[str, Any]:
    stmt = select(InteractionEvaluation).where(InteractionEvaluation.student_id == student_id).order_by(desc(InteractionEvaluation.created_at))
    if simulation_id:
        stmt = stmt.where(InteractionEvaluation.simulation_id == simulation_id)
    evaluations = db.execute(stmt).scalars().all()
    profile = get_or_create_profile(db, student_id)
    if not evaluations:
        score = 0.0 if simulation_id else statistics.mean((profile.current_competencies or DEFAULT_COMPETENCIES).values())
        strengths = [] if simulation_id else (profile.strengths or [])
        weaknesses = [] if simulation_id else (profile.weaknesses or [])
        recs = ["Complete evaluated work in this simulation to generate evidence-based feedback."] if simulation_id else ["Complete an adaptive simulation to generate evidence-based feedback."]
    else:
        score = statistics.mean(x.overall_score for x in evaluations)
        strengths = list(dict.fromkeys([s for x in evaluations for s in x.strengths]))[:5]
        weaknesses = list(dict.fromkeys([w for x in evaluations for w in x.weaknesses]))[:5]
        recs = list(dict.fromkeys([r for x in evaluations for r in x.recommendations]))[:6]
    gaps = skill_gap_fields(profile)
    focus = ", ".join(gaps["next_focus"]) or "continued practice"
    summary = (
        f"Across {len(evaluations)} evaluated interactions, the current demonstrated competency average is {score:.0f}/100. "
        f"Next focus: {focus}."
    )
    report = FeedbackReport(
        student_id=student_id,
        simulation_id=simulation_id,
        overall_score=_clamp(score),
        summary=summary,
        strengths=strengths,
        weaknesses=weaknesses,
        recommendations=recs,
        generated_by="offline_intelligence",
    )
    db.add(report)
    db.flush()
    payload = serialize_feedback(report)
    payload.update(gaps)
    return payload


def calculate_readiness(db: Session, student_id: str, simulation_id: Optional[UUID] = None) -> Dict[str, Any]:
    profile = get_or_create_profile(db, student_id)
    evaluation_query = select(InteractionEvaluation).where(InteractionEvaluation.student_id == student_id)
    if simulation_id:
        evaluation_query = evaluation_query.where(InteractionEvaluation.simulation_id == simulation_id)
    evaluations = db.execute(evaluation_query.order_by(InteractionEvaluation.created_at)).scalars().all()
    if simulation_id:
        scores = {}
        for evaluation in evaluations:
            scores.update(evaluation.competencies or {})
        scores = scores or {key: 0.0 for key in COMPETENCIES}
    else:
        scores = profile.current_competencies or DEFAULT_COMPETENCIES
    competency_avg = statistics.mean(scores.values()) if scores else 0.0
    session_scores = [float(x.overall_score) for x in evaluations]
    if len(session_scores) >= 2:
        recent = session_scores[-10:]
        weights = list(range(1, len(recent) + 1))
        readiness = sum(s * w for s, w in zip(recent, weights)) / sum(weights)
        trend = recent[-1] - recent[-2]
        # Simple least-squares projection for the next session.
        n = len(recent)
        xs = list(range(1, n + 1))
        xbar, ybar = statistics.mean(xs), statistics.mean(recent)
        denom = sum((x - xbar) ** 2 for x in xs) or 1
        slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, recent)) / denom
        forecast = _clamp(recent[-1] + slope)
    else:
        readiness = competency_avg
        trend = 0
        forecast = competency_avg
    benchmark = 70.0
    percentile = _clamp(50 + (readiness - benchmark) * 1.67)
    level = "PLACEMENT_READY" if readiness >= benchmark and forecast >= benchmark else "NOT_YET_PLACEMENT_READY"
    if len(session_scores) >= 3:
        recent_three = session_scores[-3:]
        if recent_three[0] > recent_three[1] > recent_three[2]:
            existing = db.execute(
                select(EarlyWarningAlert).where(
                    EarlyWarningAlert.student_id == student_id,
                    EarlyWarningAlert.alert_type == "DECLINING_PERFORMANCE",
                    EarlyWarningAlert.status == "OPEN",
                )
            ).scalars().first()
            if existing is None:
                existing = EarlyWarningAlert(
                    student_id=student_id,
                    simulation_id=simulation_id,
                    alert_type="DECLINING_PERFORMANCE",
                    severity="warning",
                    explanation="The last three evaluated sessions show a continuous decline in overall performance.",
                    recommended_action="Target the weakest competency with a focused adaptive simulation and review the latest feedback.",
                )
            existing.simulation_id = simulation_id or existing.simulation_id
            existing.previous_score = recent_three[0]
            existing.current_score = recent_three[-1]
            existing.trend_value = recent_three[-1] - recent_three[0]
            db.add(existing)
    snapshot = ReadinessSnapshot(
        student_id=student_id,
        simulation_id=simulation_id,
        readiness_score=_clamp(readiness),
        percentile=percentile,
        benchmark_score=benchmark,
        gap_to_benchmark=round(readiness - benchmark, 2),
        readiness_level=level,
        forecast_score=forecast,
        trend_direction="improving" if trend > 1 else "declining" if trend < -1 else "stable",
        metrics={"competencies": scores, "session_count": len(session_scores), "recent_scores": session_scores[-10:], "method": "10-session weighted average + linear forecast"},
    )
    db.add(snapshot)
    db.flush()
    return serialize_readiness(snapshot)


def serialize_feedback(report: FeedbackReport) -> Dict[str, Any]:
    return {"id": str(report.id), "student_id": report.student_id, "simulation_id": str(report.simulation_id) if report.simulation_id else None, "overall_score": report.overall_score, "summary": report.summary, "strengths": report.strengths, "weaknesses": report.weaknesses, "recommendations": report.recommendations, "generated_by": report.generated_by, "generated_at": report.generated_at.isoformat()}


def serialize_readiness(snapshot: ReadinessSnapshot) -> Dict[str, Any]:
    return {"id": str(snapshot.id), "student_id": snapshot.student_id, "simulation_id": str(snapshot.simulation_id) if snapshot.simulation_id else None, "readiness_score": snapshot.readiness_score, "percentile": snapshot.percentile, "benchmark_score": snapshot.benchmark_score, "gap_to_benchmark": snapshot.gap_to_benchmark, "readiness_level": snapshot.readiness_level, "forecast_score": snapshot.forecast_score, "trend_direction": snapshot.trend_direction, "metrics": snapshot.metrics, "calculated_at": snapshot.calculated_at.isoformat()}


def get_analytics(db: Session, student_id: str, simulation_id: Optional[UUID] = None) -> Dict[str, Any]:
    profile = get_or_create_profile(db, student_id)
    evaluation_query = select(InteractionEvaluation).where(InteractionEvaluation.student_id == student_id)
    if simulation_id:
        evaluation_query = evaluation_query.where(InteractionEvaluation.simulation_id == simulation_id)
    evaluations = db.execute(evaluation_query.order_by(InteractionEvaluation.created_at)).scalars().all()
    interaction_rows = db.execute(
        select(InteractionEvaluation, SimulationInteraction)
        .join(SimulationInteraction, SimulationInteraction.id == InteractionEvaluation.interaction_id)
        .where(InteractionEvaluation.student_id == student_id)
        .order_by(SimulationInteraction.occurred_at)
    ).all()
    if simulation_id:
        interaction_rows = [row for row in interaction_rows if row[0].simulation_id == simulation_id]
    simulation_query = select(Simulation).where(Simulation.student_id == student_id)
    if simulation_id:
        simulation_query = simulation_query.where(Simulation.id == simulation_id)
    simulations = db.execute(simulation_query.order_by(Simulation.created_at)).scalars().all()
    technical_scores = [round(float(e.overall_score), 2) for e, interaction in interaction_rows if (interaction.metadata_json or {}).get("evidence_kind") == "technical"]
    behavioral_scores = [round(float(e.overall_score), 2) for e, interaction in interaction_rows if (interaction.metadata_json or {}).get("evidence_kind") != "technical"]
    scores = [round(x.overall_score, 2) for x in evaluations]
    trend = []
    evidence_kind_by_evaluation = {
        evaluation.id: (interaction.metadata_json or {}).get("evidence_kind", "behavioral")
        for evaluation, interaction in interaction_rows
    }
    running = {}
    for i, evaluation in enumerate(evaluations, 1):
        kind = evidence_kind_by_evaluation.get(evaluation.id, "behavioral")
        trend.append({"session": i, "score": evaluation.overall_score, "simulation_id": str(evaluation.simulation_id), "evidence_kind": kind})
        for k, v in (evaluation.competencies or {}).items():
            running[k] = v
    readiness_query = select(ReadinessSnapshot).where(ReadinessSnapshot.student_id == student_id)
    alert_query = select(EarlyWarningAlert).where(EarlyWarningAlert.student_id == student_id, EarlyWarningAlert.status == "OPEN")
    if simulation_id:
        readiness_query = readiness_query.where(ReadinessSnapshot.simulation_id == simulation_id)
        alert_query = alert_query.where(EarlyWarningAlert.simulation_id == simulation_id)
    latest_readiness = db.execute(readiness_query.order_by(desc(ReadinessSnapshot.calculated_at))).scalars().first()
    alerts = db.execute(alert_query.order_by(desc(EarlyWarningAlert.created_at)).limit(10)).scalars().all()
    scoped_simulation = next((sim for sim in simulations if simulation_id is None or sim.id == simulation_id), None)
    latest_technical_interaction = next((interaction for _evaluation, interaction in reversed(interaction_rows) if (interaction.metadata_json or {}).get("evidence_kind") == "technical"), None)
    adaptation = (latest_technical_interaction.metadata_json or {}).get("adaptive_decision") if latest_technical_interaction else None
    return {"student_id": student_id, "profile": serialize_profile(profile), "summary": {"simulations": len(simulations), "evaluated_interactions": len(evaluations), "average_score": round(statistics.mean(scores), 2) if scores else None, "best_score": max(scores) if scores else None, "latest_score": scores[-1] if scores else None, "technical_score": technical_scores[-1] if technical_scores else None, "average_technical_score": round(statistics.mean(technical_scores), 2) if technical_scores else None, "behavioral_score": behavioral_scores[-1] if behavioral_scores else None, "average_behavioral_score": round(statistics.mean(behavioral_scores), 2) if behavioral_scores else None}, "current_difficulty": scoped_simulation.difficulty.value if scoped_simulation and scoped_simulation.difficulty else None, "latest_adaptation": adaptation, "competencies": running or ({} if simulation_id else profile.current_competencies), "score_history": trend, "readiness": serialize_readiness(latest_readiness) if latest_readiness else None, "alerts": [{"id": str(a.id), "type": a.alert_type, "severity": a.severity, "previous_score": a.previous_score, "current_score": a.current_score, "trend_value": a.trend_value, "explanation": a.explanation, "recommended_action": a.recommended_action, "created_at": a.created_at.isoformat()} for a in alerts]}


def list_admin_students(db: Session) -> List[Dict[str, Any]]:
    profiles = db.execute(select(StudentProfile).order_by(StudentProfile.student_id)).scalars().all()
    result = []
    for p in profiles:
        analytics = get_analytics(db, p.student_id)
        result.append({"student_id": p.student_id, "full_name": p.full_name, "role": p.role, "domain": p.domain, "average_score": analytics["summary"]["average_score"], "simulations": analytics["summary"]["simulations"], "readiness": analytics["readiness"]})
    return result


def issue_certificate(db: Session, student_id: str, simulation_id: Optional[UUID] = None) -> Dict[str, Any]:
    if simulation_id is None:
        raise ValueError("Certificate eligibility requires a completed simulation")
    simulation = db.get(Simulation, simulation_id)
    if not simulation or simulation.student_id != student_id:
        raise ValueError("Simulation not found for this student")
    if simulation.status != SimulationStatus.COMPLETED or simulation.progress < 100:
        raise ValueError("Complete the internship before requesting a certificate")
    evaluation_count = db.execute(select(InteractionEvaluation).where(InteractionEvaluation.student_id == student_id, InteractionEvaluation.simulation_id == simulation_id)).scalars().all()
    if not evaluation_count:
        raise ValueError("Complete evaluated internship work before requesting a certificate")
    readiness = calculate_readiness(db, student_id, simulation_id)
    profile = get_or_create_profile(db, student_id)
    if readiness["readiness_level"] != "PLACEMENT_READY":
        raise ValueError("Certificate eligibility requires placement-ready status")
    existing = db.execute(select(Certificate).where(Certificate.student_id == student_id, Certificate.simulation_id == simulation_id)).scalars().first()
    if existing:
        return serialize_certificate(existing)
    number = f"AIWSE-{student_id[:12].upper()}-{_stable_number(student_id + str(simulation_id), 100000, 999999)}"
    cert = Certificate(student_id=student_id, simulation_id=simulation_id, certificate_number=number, title=f"AI Internship Skill Certificate — {profile.role or 'Internship'}", domain=profile.domain or "Technology", readiness_percentile=readiness["percentile"], metadata_json={"competencies": profile.current_competencies, "readiness_score": readiness["readiness_score"]})
    db.add(cert)
    db.flush()
    return serialize_certificate(cert)


def serialize_certificate(cert: Certificate) -> Dict[str, Any]:
    return {"id": str(cert.id), "student_id": cert.student_id, "simulation_id": str(cert.simulation_id) if cert.simulation_id else None, "certificate_number": cert.certificate_number, "title": cert.title, "domain": cert.domain, "readiness_percentile": cert.readiness_percentile, "issued_at": cert.issued_at.isoformat(), "metadata": cert.metadata_json}
