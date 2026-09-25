"""Adaptive AI + Module 4 intelligence service.

The service is deliberately provider-agnostic: with GEMINI_API_KEY empty it
uses deterministic local heuristics, so the full product can be tested offline.
When a key is supplied, the same structured operations are delegated to the
existing GeminiAIService.
"""
from __future__ import annotations

import hashlib
import math
import statistics
from typing import Any, Dict, List, Optional
from uuid import UUID

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

    if settings.GEMINI_API_KEY:
        ai = get_ai_service()
        prompt = f"""Assess this internship candidate.
Domain: {domain or profile.domain or 'technology'}
Role: {role or profile.role or 'intern'}
Skills: {skills}
Assessment responses: {responses}
Return JSON with competencies (keys: {COMPETENCIES}), strengths, weaknesses,
summary, and recommended_difficulty. Scores must be 0-100."""
        payload = await ai.generate_json(
            prompt,
            "You are an internship assessment engine. Score evidence, not personality. Return JSON only.",
        )
    else:
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

    scores = {k: _clamp(float(payload.get("competencies", {}).get(k, 60))) for k in COMPETENCIES}
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
) -> Dict[str, Any]:
    interactions = db.execute(
        select(SimulationInteraction)
        .where(SimulationInteraction.simulation_id == simulation.id)
        .order_by(desc(SimulationInteraction.sequence))
        .limit(6)
    ).scalars().all()
    history = [
        {"situation": x.situation, "response": x.student_response, "decision": x.decision}
        for x in reversed(interactions)
    ]
    weak = sorted((profile.current_competencies or DEFAULT_COMPETENCIES).items(), key=lambda x: x[1])[:2]
    focus = ", ".join(k.replace("_", " ") for k, _ in weak)

    if settings.GEMINI_API_KEY:
        ai = get_ai_service()
        prompt = f"""Generate the next workplace simulation situation.
Role: {profile.role or simulation.role}
Domain: {profile.domain or simulation.description}
Current competencies: {profile.current_competencies}
Recent interaction history: {history}
Target weaker competencies: {focus}
Return JSON: title, situation, objective, decision_options (3 short options),
branch_hint, difficulty, consequence_if_weak, consequence_if_strong.
The situation must depend on the supplied history and should not repeat it."""
        payload = await ai.generate_json(prompt, "You are an adaptive internship simulation designer. Return JSON only.")
    else:
        seq = len(interactions) + 1
        branch = (interactions[0].metadata_json or {}).get("branch_selected") if interactions else None
        templates = [
            ("Requirement ambiguity", "A stakeholder changes an important requirement one day before a milestone. Decide how you respond while protecting delivery quality.", "decision_making"),
            ("Production incident", "A monitoring alert shows elevated errors shortly after your change. The manager asks for an immediate response while customer impact is still unclear.", "problem_solving"),
            ("Team conflict", "A teammate challenges your implementation approach during review and proposes a faster alternative with higher technical risk.", "communication"),
            ("Priority collision", "Two tasks are now urgent and only one can be completed before the deadline. You must justify what gets prioritized.", "critical_thinking"),
        ]
        title, situation, competency = templates[_stable_number(str(simulation.id) + str(seq) + str(branch), 0, len(templates) - 1)]
        difficulty = profile.recommended_difficulty or simulation.difficulty.value
        payload = {
            "title": f"Adaptive Situation {seq}: {title}",
            "situation": situation,
            "objective": f"Demonstrate {competency.replace('_', ' ')} in a realistic workplace decision.",
            "decision_options": ["Investigate and clarify before acting", "Act immediately with a documented risk", "Escalate with a concise recommendation"],
            "branch_hint": competency,
            "difficulty": difficulty,
            "consequence_if_weak": "The issue escalates and creates additional stakeholder pressure.",
            "consequence_if_strong": "The issue is contained and the team gains confidence in your judgment.",
        }
    return payload


async def evaluate_interaction(
    db: Session,
    simulation: Simulation,
    interaction: SimulationInteraction,
    response: str,
) -> Dict[str, Any]:
    profile = get_or_create_profile(db, simulation.student_id)
    history = db.execute(
        select(SimulationInteraction)
        .where(SimulationInteraction.simulation_id == simulation.id)
        .order_by(desc(SimulationInteraction.sequence))
        .limit(5)
    ).scalars().all()
    if settings.GEMINI_API_KEY:
        ai = get_ai_service()
        prompt = f"""Evaluate this internship decision.
Situation: {interaction.situation}
Student response: {response}
Current competency profile: {profile.current_competencies}
Recent history: {[x.student_response for x in reversed(history)]}
Return JSON with overall_score, competencies ({COMPETENCIES}), strengths, weaknesses,
mistakes, feedback, recommendations, branch_selected (risk_escalation|stable|high_performance),
difficulty_delta (-1,0,1), confidence (0-1). Scores 0-100."""
        payload = await ai.generate_json(prompt, "You are a rigorous competency evaluator. Return JSON only.")
    else:
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
    return {
        "overall_score": _clamp(float(payload.get("overall_score", statistics.mean(scores.values())))),
        "competencies": scores,
        "strengths": payload.get("strengths", []),
        "weaknesses": payload.get("weaknesses", []),
        "mistakes": payload.get("mistakes", []),
        "feedback": payload.get("feedback", ""),
        "recommendations": payload.get("recommendations", []),
        "branch_selected": payload.get("branch_selected", "stable"),
        "difficulty_delta": int(payload.get("difficulty_delta", 0)),
        "confidence": float(payload.get("confidence", 0.75)),
    }


def update_competencies(db: Session, profile: StudentProfile, scores: Dict[str, float]) -> Dict[str, float]:
    current = dict(profile.current_competencies or DEFAULT_COMPETENCIES)
    # Exponential moving update: recent demonstrated performance matters more,
    # while the baseline still prevents a single response from swinging wildly.
    for key in COMPETENCIES:
        current[key] = _clamp(0.70 * current.get(key, 60) + 0.30 * scores.get(key, current.get(key, 60)))
    profile.current_competencies = current
    profile.strengths = [k.replace("_", " ").title() for k, _ in sorted(current.items(), key=lambda x: x[1], reverse=True)[:3]]
    profile.weaknesses = [k.replace("_", " ").title() for k, _ in sorted(current.items(), key=lambda x: x[1])[:3]]
    avg = statistics.mean(current.values())
    profile.recommended_difficulty = "advanced" if avg >= 78 else "intermediate" if avg >= 58 else "beginner"
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
        score = statistics.mean((profile.current_competencies or DEFAULT_COMPETENCIES).values())
        strengths = profile.strengths or []
        weaknesses = profile.weaknesses or []
        recs = ["Complete an adaptive simulation to generate evidence-based feedback."]
    else:
        score = statistics.mean(x.overall_score for x in evaluations)
        strengths = list(dict.fromkeys([s for x in evaluations for s in x.strengths]))[:5]
        weaknesses = list(dict.fromkeys([w for x in evaluations for w in x.weaknesses]))[:5]
        recs = list(dict.fromkeys([r for x in evaluations for r in x.recommendations]))[:6]
    if settings.GEMINI_API_KEY:
        ai = get_ai_service()
        payload = await ai.generate_json(
            f"Create a concise student performance summary. Score={score:.1f}; strengths={strengths}; weaknesses={weaknesses}; recommendations={recs}. Return JSON with summary, strengths, weaknesses, recommendations.",
            "You are a career feedback analyst. Be evidence-based and concise. Return JSON only.",
        )
        summary = payload.get("summary", "")
        strengths = payload.get("strengths", strengths)
        weaknesses = payload.get("weaknesses", weaknesses)
        recs = payload.get("recommendations", recs)
        generated_by = "gemini"
    else:
        summary = f"Across {len(evaluations)} evaluated interactions, the current demonstrated competency average is {score:.0f}/100. Focus on {', '.join(weaknesses[:2]) or 'continued practice'} while maintaining your strengths in {', '.join(strengths[:2]) or 'core competencies'}."
        generated_by = "offline_intelligence"
    report = FeedbackReport(student_id=student_id, simulation_id=simulation_id, overall_score=_clamp(score), summary=summary, strengths=strengths, weaknesses=weaknesses, recommendations=recs, generated_by=generated_by)
    db.add(report)
    db.flush()
    return serialize_feedback(report)


def calculate_readiness(db: Session, student_id: str, simulation_id: Optional[UUID] = None) -> Dict[str, Any]:
    profile = get_or_create_profile(db, student_id)
    scores = profile.current_competencies or DEFAULT_COMPETENCIES
    competency_avg = statistics.mean(scores.values())
    evaluations = db.execute(select(InteractionEvaluation).where(InteractionEvaluation.student_id == student_id).order_by(InteractionEvaluation.created_at)).scalars().all()
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
            alert = EarlyWarningAlert(
                student_id=student_id, simulation_id=simulation_id, alert_type="DECLINING_PERFORMANCE", severity="warning",
                previous_score=recent_three[0], current_score=recent_three[-1], trend_value=recent_three[-1] - recent_three[0],
                explanation="The last three evaluated sessions show a continuous decline in overall performance.",
                recommended_action="Target the weakest competency with a focused adaptive simulation and review the latest feedback.",
            )
            db.add(alert)
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


def get_analytics(db: Session, student_id: str) -> Dict[str, Any]:
    profile = get_or_create_profile(db, student_id)
    evaluations = db.execute(select(InteractionEvaluation).where(InteractionEvaluation.student_id == student_id).order_by(InteractionEvaluation.created_at)).scalars().all()
    simulations = db.execute(select(Simulation).where(Simulation.student_id == student_id).order_by(Simulation.created_at)).scalars().all()
    scores = [round(x.overall_score, 2) for x in evaluations]
    trend = []
    running = {}
    for i, evaluation in enumerate(evaluations, 1):
        trend.append({"session": i, "score": evaluation.overall_score, "simulation_id": str(evaluation.simulation_id)})
        for k, v in (evaluation.competencies or {}).items():
            running[k] = v
    latest_readiness = db.execute(select(ReadinessSnapshot).where(ReadinessSnapshot.student_id == student_id).order_by(desc(ReadinessSnapshot.calculated_at))).scalars().first()
    alerts = db.execute(select(EarlyWarningAlert).where(EarlyWarningAlert.student_id == student_id, EarlyWarningAlert.status == "OPEN").order_by(desc(EarlyWarningAlert.created_at)).limit(10)).scalars().all()
    return {"student_id": student_id, "profile": serialize_profile(profile), "summary": {"simulations": len(simulations), "evaluated_interactions": len(evaluations), "average_score": round(statistics.mean(scores), 2) if scores else None, "best_score": max(scores) if scores else None, "latest_score": scores[-1] if scores else None}, "competencies": running or profile.current_competencies, "score_history": trend, "readiness": serialize_readiness(latest_readiness) if latest_readiness else None, "alerts": [{"id": str(a.id), "type": a.alert_type, "severity": a.severity, "previous_score": a.previous_score, "current_score": a.current_score, "trend_value": a.trend_value, "explanation": a.explanation, "recommended_action": a.recommended_action, "created_at": a.created_at.isoformat()} for a in alerts]}


def list_admin_students(db: Session) -> List[Dict[str, Any]]:
    profiles = db.execute(select(StudentProfile).order_by(StudentProfile.student_id)).scalars().all()
    result = []
    for p in profiles:
        analytics = get_analytics(db, p.student_id)
        result.append({"student_id": p.student_id, "full_name": p.full_name, "role": p.role, "domain": p.domain, "average_score": analytics["summary"]["average_score"], "simulations": analytics["summary"]["simulations"], "readiness": analytics["readiness"]})
    return result


def issue_certificate(db: Session, student_id: str, simulation_id: Optional[UUID] = None) -> Dict[str, Any]:
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
