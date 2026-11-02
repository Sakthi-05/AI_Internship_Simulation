"""Student intelligence, adaptive simulation and Module 4 APIs."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser, get_current_user
from app.agents.contracts import ExecutionStatus
from app.agents.dependencies import get_agent_orchestrator
from app.agents.orchestrator import AgentOrchestrator
from app.agents.specialists.competency_agent import invoke_competency_agent
from app.models.enums import SimulationStatus
from app.models.intelligence import Certificate, FeedbackReport, InteractionEvaluation, ReadinessSnapshot, StudentProfile
from app.models.simulation import Simulation
from app.models.simulation_interaction import SimulationInteraction
from app.services.intelligence_service import (
    assess_student, calculate_readiness, create_feedback_report, evaluate_interaction,
    generate_adaptive_situation, get_analytics, get_or_create_profile, issue_certificate, latest_adaptive_branch,
    list_admin_students, serialize_certificate, serialize_feedback, serialize_profile,
    serialize_readiness, skill_gap_fields, update_competencies,
)
from app.services.simulation.interaction_history_service import record_interaction, serialize_interaction

router = APIRouter(prefix="/api/intelligence", tags=["AI Intelligence & Module 4"])


class ProfileRequest(BaseModel):
    full_name: Optional[str] = None
    domain: Optional[str] = None
    role: Optional[str] = None
    skills: List[str] = Field(default_factory=list)


class AssessmentRequest(ProfileRequest):
    responses: List[str] = Field(default_factory=list)


class AdaptiveResponse(BaseModel):
    response: str = Field(..., min_length=1, max_length=10000)
    decision: Optional[str] = None


def _owned_simulation(db: Session, user: CurrentUser, simulation_id: uuid.UUID) -> Simulation:
    simulation = db.get(Simulation, simulation_id)
    if not simulation:
        raise HTTPException(404, "Simulation not found")
    if simulation.student_id != user.student_id:
        raise HTTPException(403, "Not your simulation")
    return simulation


@router.get("/profile")
def profile(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return serialize_profile(get_or_create_profile(db, user.student_id))


@router.put("/profile")
def update_profile(payload: ProfileRequest, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    p = get_or_create_profile(db, user.student_id)
    for field in ["full_name", "domain", "role"]:
        value = getattr(payload, field)
        if value is not None:
            setattr(p, field, value)
    if payload.skills:
        p.skills = payload.skills
    db.add(p)
    db.commit()
    db.refresh(p)
    return serialize_profile(p)


@router.post("/assessment")
async def run_assessment(payload: AssessmentRequest, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    result = await assess_student(db, user.student_id, domain=payload.domain, role=payload.role, skills=payload.skills, responses=payload.responses)
    p = get_or_create_profile(db, user.student_id)
    p.full_name = payload.full_name or p.full_name
    db.add(p)
    db.commit()
    return result


@router.get("/simulations/{simulation_id}/adaptive/next")
async def next_situation(simulation_id: uuid.UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    simulation = _owned_simulation(db, user, simulation_id)
    profile = get_or_create_profile(db, user.student_id)
    unanswered = db.execute(select(SimulationInteraction).where(SimulationInteraction.simulation_id == simulation.id, SimulationInteraction.interaction_type == "adaptive_situation", SimulationInteraction.student_response.is_(None)).order_by(desc(SimulationInteraction.sequence))).scalars().first()
    if unanswered:
        meta = dict(unanswered.metadata_json or {})
        meta["situation"] = unanswered.situation
        return {"interaction": serialize_interaction(unanswered), "situation": meta}
    situation = await generate_adaptive_situation(
        db, simulation, profile, branch=latest_adaptive_branch(db, simulation),
    )
    last = db.execute(select(SimulationInteraction).where(SimulationInteraction.simulation_id == simulation.id).order_by(desc(SimulationInteraction.sequence))).scalars().first()
    interaction = record_interaction(db, simulation, situation=situation["situation"], interaction_type="adaptive_situation", parent_interaction_id=last.id if last else None, metadata={"title": situation.get("title"), "objective": situation.get("objective"), "decision_options": situation.get("decision_options", []), "branch_hint": situation.get("branch_hint"), "difficulty": situation.get("difficulty"), "started_at": datetime.now(timezone.utc).isoformat(), "expected_time": situation["expected_time"]})
    db.commit()
    return {"interaction": serialize_interaction(interaction), "situation": situation}


@router.post("/simulations/{simulation_id}/adaptive/respond")
async def respond_adaptively(simulation_id: uuid.UUID, payload: AdaptiveResponse, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    simulation = _owned_simulation(db, user, simulation_id)
    interaction = db.execute(select(SimulationInteraction).where(SimulationInteraction.simulation_id == simulation.id, SimulationInteraction.interaction_type == "adaptive_situation", SimulationInteraction.student_response.is_(None)).order_by(desc(SimulationInteraction.sequence))).scalars().first()
    if not interaction:
        raise HTTPException(409, "No unanswered adaptive situation. Request the next situation first.")
    interaction.student_response = payload.response
    interaction.decision = payload.decision or "free_response"
    completed_at = datetime.now(timezone.utc)
    meta = dict(interaction.metadata_json or {})
    try:
        started_at = datetime.fromisoformat(meta["started_at"])
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        response_time = max(0.0, (completed_at - started_at).total_seconds())
    except (KeyError, TypeError, ValueError):
        response_time = None
    meta.update({"completed_at": completed_at.isoformat(), "response_time": response_time})
    interaction.metadata_json = meta
    db.add(interaction)
    db.flush()
    evaluation = await evaluate_interaction(db, simulation, interaction, payload.response)
    expected_time = meta.get("expected_time")
    evaluation.update({"response_time": response_time, "expected_time": expected_time})
    if response_time is not None and isinstance(expected_time, (int, float)) and expected_time > 0:
        evaluation["speed_ratio"] = round(response_time / expected_time, 4)
    interaction.metadata_json = {**meta, "quality_score": evaluation["overall_score"], "expected_time": expected_time, "branch_selected": evaluation["branch_selected"], "evaluation": evaluation}
    db.add(interaction)
    db.flush()
    profile = get_or_create_profile(db, user.student_id)
    updated = update_competencies(db, profile, evaluation["competencies"])
    from app.services.intelligence_service import apply_adaptive_decision
    decided = apply_adaptive_decision(db, simulation, profile, evaluation)
    ev = InteractionEvaluation(
        interaction_id=interaction.id,
        simulation_id=simulation.id,
        student_id=user.student_id,
        overall_score=evaluation["overall_score"],
        competencies=evaluation["competencies"],
        strengths=evaluation["strengths"],
        weaknesses=evaluation["weaknesses"],
        mistakes=evaluation["mistakes"],
        feedback=evaluation["feedback"],
        recommendations=evaluation["recommendations"],
        branch_selected=evaluation["branch_selected"],
        difficulty_delta=evaluation["difficulty_delta"],
        confidence=evaluation["confidence"],
    )
    db.add(ev)
    interaction.metadata_json = {**(interaction.metadata_json or {}), "adaptive_decision": {key: evaluation.get(key) for key in ("quality_score", "overall_score", "response_time", "expected_time", "speed_ratio", "previous_difficulty", "next_difficulty", "branch_selected", "difficulty_delta")}}
    db.add(interaction)
    db.add(simulation)
    db.add(profile)
    db.flush()
    next_situation = await generate_adaptive_situation(
        db, simulation, profile, branch=evaluation["branch_selected"], decided_difficulty=decided,
    )
    next_interaction = record_interaction(db, simulation, situation=next_situation["situation"], interaction_type="adaptive_situation", parent_interaction_id=interaction.id, metadata={"title": next_situation.get("title"), "objective": next_situation.get("objective"), "decision_options": next_situation.get("decision_options", []), "branch_hint": next_situation.get("branch_hint"), "difficulty": next_situation.get("difficulty"), "triggered_by": evaluation["branch_selected"], "started_at": datetime.now(timezone.utc).isoformat(), "expected_time": next_situation["expected_time"]})
    feedback = await create_feedback_report(db, user.student_id, simulation.id)
    readiness = calculate_readiness(db, user.student_id, simulation.id)
    db.commit()
    db.refresh(interaction)
    db.refresh(next_interaction)
    return {"interaction": serialize_interaction(interaction), "evaluation": evaluation, "updated_competencies": updated, "feedback": feedback, "readiness": readiness, "next_step": {"branch": evaluation["branch_selected"], "difficulty_delta": evaluation["difficulty_delta"]}, "next_interaction": serialize_interaction(next_interaction), "next_situation": next_situation}


@router.get("/simulations/{simulation_id}/adaptive/history")
def adaptive_history(simulation_id: uuid.UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    simulation = _owned_simulation(db, user, simulation_id)
    rows = db.execute(select(SimulationInteraction).where(SimulationInteraction.simulation_id == simulation.id, SimulationInteraction.interaction_type == "adaptive_situation").order_by(SimulationInteraction.sequence)).scalars().all()
    evaluations = {x.interaction_id: x for x in db.execute(select(InteractionEvaluation).where(InteractionEvaluation.simulation_id == simulation.id)).scalars().all()}
    return {"interactions": [serialize_interaction(x) for x in rows], "evaluations": [{"interaction_id": str(i), "overall_score": e.overall_score, "competencies": e.competencies, "branch_selected": e.branch_selected} for i, e in evaluations.items()]}


@router.get("/analytics")
async def analytics(simulation_id: Optional[uuid.UUID] = None, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(get_current_user),
                    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator)):
    if simulation_id:
        _owned_simulation(db, user, simulation_id)
    result = get_analytics(db, user.student_id, simulation_id)
    if simulation_id:
        assessment = await invoke_competency_agent(
            orchestrator, db, simulation_id=simulation_id,
            student_id=user.student_id,
        )
        if assessment.status == ExecutionStatus.SUCCEEDED and assessment.result is not None:
            result["competency_assessment"] = assessment.result
        else:
            # Analytics remains available if a simulation's evidence context is
            # incomplete. Do not expose internal errors or block the dashboard.
            result["competency_assessment"] = {
                "status": "unavailable",
                "error_code": assessment.error.code if assessment.error else "agent_unavailable",
                "dimensions": [],
            }
    return result


@router.get("/feedback")
def feedback(simulation_id: Optional[uuid.UUID] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if simulation_id:
        _owned_simulation(db, user, simulation_id)
    query = select(FeedbackReport).where(FeedbackReport.student_id == user.student_id)
    if simulation_id:
        query = query.where(FeedbackReport.simulation_id == simulation_id)
    rows = db.execute(query.order_by(desc(FeedbackReport.generated_at)).limit(10)).scalars().all()
    gaps = skill_gap_fields(get_or_create_profile(db, user.student_id))
    reports = []
    for row in rows:
        item = serialize_feedback(row)
        item.update(gaps)
        reports.append(item)
    return {"reports": reports}


@router.post("/feedback/generate")
async def generate_feedback(simulation_id: Optional[uuid.UUID] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if simulation_id:
        _owned_simulation(db, user, simulation_id)
    result = await create_feedback_report(db, user.student_id, simulation_id)
    db.commit()
    return result


@router.get("/readiness")
def readiness(simulation_id: Optional[uuid.UUID] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if simulation_id:
        _owned_simulation(db, user, simulation_id)
    query = select(ReadinessSnapshot).where(ReadinessSnapshot.student_id == user.student_id)
    if simulation_id:
        query = query.where(ReadinessSnapshot.simulation_id == simulation_id)
    latest = db.execute(query.order_by(desc(ReadinessSnapshot.calculated_at))).scalars().first()
    return serialize_readiness(latest) if latest else calculate_readiness(db, user.student_id, simulation_id)


@router.post("/readiness/recalculate")
def recalculate_readiness(simulation_id: Optional[uuid.UUID] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if simulation_id:
        _owned_simulation(db, user, simulation_id)
    result = calculate_readiness(db, user.student_id, simulation_id)
    db.commit()
    return result


@router.get("/certificates")
def certificates(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    rows = db.execute(
        select(Certificate)
        .join(Simulation, Certificate.simulation_id == Simulation.id)
        .where(
            Certificate.student_id == user.student_id,
            Simulation.student_id == user.student_id,
            Simulation.status == SimulationStatus.COMPLETED,
            Simulation.progress >= 100,
        )
        .order_by(desc(Certificate.issued_at))
    ).scalars().all()
    return {"certificates": [serialize_certificate(x) for x in rows]}


@router.post("/certificates/issue")
def certificate_issue(simulation_id: Optional[uuid.UUID] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if simulation_id:
        _owned_simulation(db, user, simulation_id)
    try:
        result = issue_certificate(db, user.student_id, simulation_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return result


@router.get("/certificates/{certificate_id}/print", response_class=HTMLResponse)
def certificate_print(certificate_id: uuid.UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    cert = db.get(Certificate, certificate_id)
    if not cert or cert.student_id != user.student_id:
        raise HTTPException(404, "Certificate not found")
    simulation = db.get(Simulation, cert.simulation_id) if cert.simulation_id else None
    if not simulation or simulation.student_id != user.student_id or simulation.status != SimulationStatus.COMPLETED or simulation.progress < 100:
        raise HTTPException(404, "Certificate not found")
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>{cert.title}</title><style>body{{font-family:Arial,sans-serif;background:#f5f7fb;padding:60px}}.card{{max-width:850px;margin:auto;background:white;border:8px double #243052;padding:70px;text-align:center}}h1{{font-size:38px;margin:0 0 18px}}h2{{font-size:24px;font-weight:400}}.number{{font-family:monospace;color:#59688f;margin-top:30px}}button{{margin-top:35px;padding:12px 22px}}</style></head><body><div class='card'><p>AI INTERNSHIP WORKFLOW SIMULATION</p><h1>{cert.title}</h1><h2>This certifies demonstrated internship competencies in <strong>{cert.domain}</strong>.</h2><p>Readiness percentile: <strong>{cert.readiness_percentile:.0f}%</strong></p><p class='number'>{cert.certificate_number}</p><button onclick='window.print()'>Print / Save as PDF</button></div></body></html>"""


@router.get("/admin/students")
def admin_students(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if user.role not in {"admin", "administrator"}:
        raise HTTPException(403, "Admin role required")
    return {"students": list_admin_students(db)}
