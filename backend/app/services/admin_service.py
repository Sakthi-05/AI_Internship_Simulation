"""Database-backed admin monitoring read models built from existing entities."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.assessment import AssessmentAttempt, AssessmentAnswer
from app.models.activity_log import ActivityLog
from app.models.company import Company
from app.models.enums import SimulationStatus
from app.models.intelligence import (
    Certificate, FeedbackReport, InteractionEvaluation, ReadinessSnapshot, StudentProfile,
)
from app.models.project import Project
from app.models.simulation import Simulation
from app.models.simulation_interaction import SimulationInteraction
from app.models.sprint import Sprint
from app.models.submission import AIReview, Submission, SubmissionVersion
from app.models.task import Task
from app.models.user import User


def _iso(value):
    return value.isoformat() if value else None


def _enum(value):
    return value.value if hasattr(value, "value") else value


def _adaptive_reason(decision: dict[str, Any]) -> str:
    if decision.get("reason"):
        return str(decision["reason"])
    score = decision.get("overall_score")
    ratio = decision.get("speed_ratio")
    if score is None:
        return "Adaptive rule applied"
    if float(score) < 58:
        return "Low-quality response"
    if float(score) < 78 and ratio is not None and float(ratio) > 1.2:
        return "Slow response with lower performance"
    if float(score) >= 78 and ratio is not None and float(ratio) > 1.2:
        return "Strong response, slower than expected"
    if float(score) >= 78:
        return "High performance"
    return "Stable performance"


def _account(db: Session, student_id: str):
    account = db.execute(select(User.student_id, User.role, User.full_name, User.email, User.created_at)
        .where(User.student_id == student_id)).one_or_none()
    if not account or account.role not in {"student", "user"}:
        raise HTTPException(404, "Student not found")
    return account


def _latest_readiness_subquery():
    return (select(ReadinessSnapshot.readiness_score)
        .where(ReadinessSnapshot.student_id == User.student_id)
        .order_by(ReadinessSnapshot.calculated_at.desc()).limit(1).scalar_subquery())


def _latest_assessment_subquery(field):
    return (select(field).where(AssessmentAttempt.student_id == User.student_id,
        AssessmentAttempt.status == "completed")
        .order_by(AssessmentAttempt.completed_at.desc()).limit(1).scalar_subquery())


def _latest_activity_subquery():
    sources = [
        select(func.max(Simulation.updated_at)).where(Simulation.student_id == User.student_id).scalar_subquery(),
        select(func.max(AssessmentAttempt.completed_at)).where(AssessmentAttempt.student_id == User.student_id).scalar_subquery(),
        select(func.max(SimulationInteraction.occurred_at)).where(SimulationInteraction.student_id == User.student_id).scalar_subquery(),
        select(func.max(FeedbackReport.generated_at)).where(FeedbackReport.student_id == User.student_id).scalar_subquery(),
        select(func.max(ActivityLog.created_at)).join(Company, Company.id == ActivityLog.company_id).where(Company.student_id == User.student_id).scalar_subquery(),
    ]
    return func.greatest(*sources, User.updated_at, User.created_at)


def _student_base_query():
    readiness = _latest_readiness_subquery()
    baseline = _latest_assessment_subquery(AssessmentAttempt.overall_score)
    simulation_status = (select(Simulation.status).where(Simulation.student_id == User.student_id)
                         .order_by(Simulation.updated_at.desc()).limit(1).scalar_subquery())
    sims_count = (select(func.count(Simulation.id)).where(Simulation.student_id == User.student_id).scalar_subquery())
    return (select(User.student_id, User.full_name, User.email, User.created_at, StudentProfile,
                   readiness.label("readiness"), baseline.label("baseline_score"),
                   simulation_status.label("simulation_status"),
                   sims_count.label("simulation_count"), _latest_activity_subquery().label("last_activity"))
            .outerjoin(StudentProfile, StudentProfile.student_id == User.student_id)
            .where(User.role == "student"))


def list_students(db: Session, *, q=None, baseline_status=None, internship_status=None,
                  simulation_status=None, difficulty=None, activity_status=None,
                  readiness_min=None, readiness_max=None, sort="registration_date",
                  order="desc", page=1, page_size=25):
    query = _student_base_query()
    readiness = _latest_readiness_subquery()
    baseline_exists = select(AssessmentAttempt.id).where(
        AssessmentAttempt.student_id == User.student_id, AssessmentAttempt.status == "completed").exists()
    simulation = select(Simulation.status).where(Simulation.student_id == User.student_id).order_by(Simulation.updated_at.desc()).limit(1).scalar_subquery()
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.where(or_(User.full_name.ilike(term), User.email.ilike(term)))
    if baseline_status == "completed": query = query.where(baseline_exists)
    if baseline_status == "not_started": query = query.where(~baseline_exists)
    if internship_status == "active": query = query.where(simulation == SimulationStatus.IN_PROGRESS)
    elif internship_status == "completed": query = query.where(simulation == SimulationStatus.COMPLETED)
    elif internship_status == "not_started": query = query.where(~select(Simulation.id).where(Simulation.student_id == User.student_id).exists())
    if simulation_status: query = query.where(simulation == SimulationStatus(simulation_status))
    if difficulty: query = query.where(StudentProfile.recommended_difficulty == difficulty)
    if readiness_min is not None: query = query.where(readiness >= readiness_min)
    if readiness_max is not None: query = query.where(readiness <= readiness_max)
    now = datetime.now(timezone.utc)
    last_activity = _latest_activity_subquery()
    if activity_status == "active": query = query.where(last_activity >= now - timedelta(days=30))
    elif activity_status == "inactive": query = query.where(last_activity < now - timedelta(days=30))

    sort_expr = {
        "name": User.full_name,
        "readiness": readiness,
        "baseline": _latest_assessment_subquery(AssessmentAttempt.overall_score),
        "last_activity": last_activity,
        "registration_date": User.created_at,
    }[sort]
    query = query.order_by(sort_expr.desc().nullslast() if order == "desc" else sort_expr.asc().nullslast())
    count = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = db.execute(query.offset((page - 1) * page_size).limit(page_size)).all()
    ids = [row.student_id for row in rows]
    sims = db.execute(select(Simulation.student_id, Simulation.status, func.count(Simulation.id))
        .where(Simulation.student_id.in_(ids)).group_by(Simulation.student_id, Simulation.status)).all() if ids else []
    assessments = db.execute(select(AssessmentAttempt.student_id, func.count(AssessmentAttempt.id))
        .where(AssessmentAttempt.student_id.in_(ids), AssessmentAttempt.status == "completed")
        .group_by(AssessmentAttempt.student_id)).all() if ids else []
    sim_counts = {}
    for sid, status, n in sims: sim_counts.setdefault(sid, {})[_enum(status)] = n
    attempts = dict(assessments)
    attention_map = {}
    if ids:
        attention_rows, _ = students_needing_attention(db, limit=len(ids), student_ids=ids)
        attention_map = {item["student_id"]: item["reasons"] for item in attention_rows}
    items = []
    for student_id, name, email, registered_at, profile, readiness_score, baseline_score, sim_status, sim_count, last in rows:
        counts = sim_counts.get(student_id, {})
        current = profile.current_competencies if profile and profile.last_assessed_at else {}
        competency = round(mean(current.values()), 1) if current else None
        baseline_done = attempts.get(student_id, 0) > 0
        active_status = _enum(sim_status)
        overall = "Needs Attention" if attention_map.get(student_id) else (
            "Completed" if counts.get("completed", 0) and active_status == "completed" else
            "Simulation Active" if active_status == "in_progress" else
            "Baseline Completed" if baseline_done else "Not Started")
        items.append({
            "student_id": student_id, "name": name, "email": email,
            "registration_date": _iso(registered_at), "baseline_status": "completed" if baseline_done else "not_started",
            "baseline_score": baseline_score, "competency_score": competency,
            "readiness_score": readiness_score, "recommended_difficulty": profile.recommended_difficulty if profile and profile.last_assessed_at else None,
            "internship_status": "active" if active_status == "in_progress" else active_status or "not_started", "simulation_status": active_status or "not_started",
            "simulation_count": int(sim_count or 0), "completed_simulations": counts.get("completed", 0),
            "last_activity": _iso(last), "overall_status": overall,
        })
    return {"students": items, "pagination": {"page": page, "page_size": page_size, "total": count,
        "pages": (count + page_size - 1) // page_size}}


def dashboard(db: Session):
    now = datetime.now(timezone.utc)
    total = db.scalar(select(func.count(User.student_id)).where(User.role == "student")) or 0
    latest_activity = _latest_activity_subquery()
    active = db.scalar(select(func.count()).select_from(User).where(User.role == "student", latest_activity >= now - timedelta(days=30))) or 0
    baseline_done = db.scalar(select(func.count(func.distinct(AssessmentAttempt.student_id))).join(User, User.student_id == AssessmentAttempt.student_id).where(User.role == "student", AssessmentAttempt.status == "completed")) or 0
    sim_counts = dict(db.execute(select(Simulation.status, func.count(Simulation.id)).join(User, User.student_id == Simulation.student_id).where(User.role == "student").group_by(Simulation.status)).all())
    completed_sim_students = db.scalar(select(func.count(func.distinct(Simulation.student_id))).join(User, User.student_id == Simulation.student_id).where(User.role == "student", Simulation.status == SimulationStatus.COMPLETED)) or 0
    ranked_readiness = select(
        ReadinessSnapshot.student_id.label("student_id"),
        ReadinessSnapshot.readiness_score.label("score"),
        func.row_number().over(partition_by=ReadinessSnapshot.student_id, order_by=ReadinessSnapshot.calculated_at.desc()).label("rank"),
    ).join(User, User.student_id == ReadinessSnapshot.student_id).where(User.role == "student").subquery()
    latest_by_student = dict(db.execute(select(ranked_readiness.c.student_id, ranked_readiness.c.score).where(ranked_readiness.c.rank == 1)).all())
    profiles = db.execute(select(StudentProfile.current_competencies).join(User, User.student_id == StudentProfile.student_id).where(User.role == "student", StudentProfile.last_assessed_at.is_not(None))).scalars().all()
    comp_scores = [score for profile in profiles for score in (profile or {}).values()]
    assessment_count = db.scalar(select(func.count(AssessmentAttempt.id)).join(User, User.student_id == AssessmentAttempt.student_id).where(User.role == "student", AssessmentAttempt.status == "completed")) or 0
    feedback_count = db.scalar(select(func.count(FeedbackReport.id)).join(User, User.student_id == FeedbackReport.student_id).where(User.role == "student")) or 0
    attention_rows, attention_total = students_needing_attention(db)
    return {"total_students": total, "active_students": active, "baseline_completed": baseline_done,
        "simulations_in_progress": int(sim_counts.get(SimulationStatus.IN_PROGRESS, 0)),
        "simulations_completed": int(sim_counts.get(SimulationStatus.COMPLETED, 0)), "students_completed_simulations": completed_sim_students,
        "average_readiness": round(mean(latest_by_student.values()), 1) if latest_by_student else None,
        "average_competency": round(mean(comp_scores), 1) if comp_scores else None,
        "assessments_completed": assessment_count, "simulations_count": sum(int(v) for v in sim_counts.values()),
        "feedback_reports": feedback_count, "students_needing_attention": attention_total, "attention_students": attention_rows}


def students_needing_attention(db: Session, limit: int = 12, student_ids: list[str] | None = None):
    """System indicators based on recorded performance and workflow state."""
    now = datetime.now(timezone.utc)
    latest_ready = _latest_readiness_subquery()
    last_activity = _latest_activity_subquery()
    baseline = select(AssessmentAttempt.id).where(AssessmentAttempt.student_id == User.student_id, AssessmentAttempt.status == "completed").exists()
    has_simulation = select(Simulation.id).where(Simulation.student_id == User.student_id).exists()
    active_simulation = select(Simulation.id).where(Simulation.student_id == User.student_id, Simulation.status == SimulationStatus.IN_PROGRESS).exists()
    has_response = select(SimulationInteraction.id).join(Simulation, Simulation.id == SimulationInteraction.simulation_id).where(Simulation.student_id == User.student_id, Simulation.status == SimulationStatus.IN_PROGRESS, SimulationInteraction.student_response.is_not(None)).exists()
    poor = select(InteractionEvaluation.student_id).where(InteractionEvaluation.student_id == User.student_id).group_by(InteractionEvaluation.student_id).having(func.count(InteractionEvaluation.id) >= 3, func.avg(InteractionEvaluation.overall_score) < 45).exists()
    risks = select(InteractionEvaluation.student_id).where(InteractionEvaluation.student_id == User.student_id, InteractionEvaluation.branch_selected == "risk_escalation").group_by(InteractionEvaluation.student_id).having(func.count(InteractionEvaluation.id) >= 2).exists()
    low_readiness = latest_ready < 40
    baseline_waiting = and_(baseline, ~has_simulation)
    internship_waiting = and_(active_simulation, ~has_response)
    inactive = last_activity < now - timedelta(days=30)
    condition = or_(low_readiness, baseline_waiting, internship_waiting, inactive, poor, risks)
    filters = [User.role == "student", condition]
    if student_ids is not None:
        filters.append(User.student_id.in_(student_ids))
    total = db.scalar(select(func.count(User.student_id)).where(*filters)) or 0
    rows = db.execute(select(User.student_id, User.full_name, User.email, latest_ready.label("readiness"),
        low_readiness.label("low_readiness"), baseline_waiting.label("baseline_waiting"),
        internship_waiting.label("internship_waiting"), inactive.label("inactive"),
        poor.label("poor_performance"), risks.label("risk_escalation"))
        .where(*filters).order_by(User.created_at.desc()).limit(limit)).all()
    labels = (("low_readiness", "Very low readiness"), ("baseline_waiting", "Baseline complete; internship simulation not started"),
        ("internship_waiting", "Simulation active; no response activity"), ("inactive", "No activity in 30 days"),
        ("poor_performance", "Repeated low simulation scores"), ("risk_escalation", "Repeated risk escalation branch"))
    return ([{"student_id": row.student_id, "name": row.full_name, "email": row.email,
        "readiness_score": row.readiness, "reasons": [label for key, label in labels if getattr(row, key)]} for row in rows], total)


def student_detail(db: Session, student_id: str):
    user = _account(db, student_id)
    profile = db.execute(select(StudentProfile).where(StudentProfile.student_id == student_id)).scalar_one_or_none()
    attempts = db.execute(select(AssessmentAttempt).where(AssessmentAttempt.student_id == student_id).order_by(AssessmentAttempt.started_at.desc()).limit(100)).scalars().all()
    attempt = next((a for a in attempts if a.status == "completed"), attempts[0] if attempts else None)
    answer_count = db.scalar(select(func.count(AssessmentAnswer.id)).join(AssessmentAttempt).where(AssessmentAttempt.student_id == student_id, AssessmentAttempt.id == attempt.id)) if attempt else 0
    simulations = db.execute(select(Simulation, Company, Project).join(Company, Company.id == Simulation.company_id).join(Project, Project.id == Simulation.project_id).where(Simulation.student_id == student_id).order_by(Simulation.created_at.desc())).all()
    evaluation_stats = db.execute(select(func.avg(InteractionEvaluation.overall_score), func.count(InteractionEvaluation.id)).where(InteractionEvaluation.student_id == student_id)).one()
    simulation_averages = dict(db.execute(select(InteractionEvaluation.simulation_id, func.avg(InteractionEvaluation.overall_score)).where(InteractionEvaluation.student_id == student_id).group_by(InteractionEvaluation.simulation_id)).all())
    evaluations = db.execute(select(InteractionEvaluation).where(InteractionEvaluation.student_id == student_id).order_by(InteractionEvaluation.created_at.desc()).limit(50)).scalars().all()
    performance_trend = "improving" if len(evaluations) > 1 and evaluations[0].overall_score > evaluations[1].overall_score else "declining" if len(evaluations) > 1 and evaluations[0].overall_score < evaluations[1].overall_score else "stable" if len(evaluations) > 1 else None
    interactions = db.execute(select(SimulationInteraction).where(SimulationInteraction.student_id == student_id).order_by(SimulationInteraction.occurred_at.desc()).limit(50)).scalars().all()
    feedback = db.execute(select(FeedbackReport).where(FeedbackReport.student_id == student_id).order_by(FeedbackReport.generated_at.desc()).limit(1)).scalar_one_or_none()
    readiness = db.execute(select(ReadinessSnapshot).where(ReadinessSnapshot.student_id == student_id).order_by(ReadinessSnapshot.calculated_at.desc()).limit(1)).scalar_one_or_none()
    transitions = []
    recent_responses = []
    technical_evaluations = []
    behavioral_evaluations = []
    eval_by_interaction = {str(row.interaction_id): row for row in evaluations}
    for item in reversed(interactions):
        meta = item.metadata_json or {}
        decision = meta.get("adaptive_decision") or {}
        ev = eval_by_interaction.get(str(item.id))
        if ev is not None:
            record = {"at": _iso(item.occurred_at), "score": ev.overall_score, "competencies": ev.competencies, "feedback": ev.feedback, "task_id": str(item.task_id) if item.task_id else None, "submission_id": meta.get("submission_id"), "evaluation_id": str(ev.id), "source": meta.get("evaluation_source")}
            if meta.get("evidence_kind") == "technical":
                record.update({"dimensions": meta.get("dimensions", {}), "adaptive_decision": decision})
                technical_evaluations.append(record)
            else:
                record.update({"branch": ev.branch_selected, "adaptive_decision": decision})
                behavioral_evaluations.append(record)
        if decision.get("previous_difficulty") or decision.get("next_difficulty"):
            transitions.append({"at": _iso(item.occurred_at), "from": decision.get("previous_difficulty"), "to": decision.get("next_difficulty"), "branch": decision.get("branch_selected"), "score": decision.get("technical_score", decision.get("overall_score")), "evidence_kind": meta.get("evidence_kind", "behavioral"), "response_time": decision.get("response_time"), "reason": _adaptive_reason(decision)})
        if item.student_response:
            ev = eval_by_interaction.get(str(item.id))
            recent_responses.append({"at": _iso(item.occurred_at), "scenario": (meta.get("title") or item.situation)[:180], "response": item.student_response, "decision": item.decision, "score": ev.overall_score if ev else meta.get("quality_score"), "branch": ev.branch_selected if ev else decision.get("branch_selected")})
    sim_details = [{"id": str(sim.id), "title": sim.title, "status": _enum(sim.status), "difficulty": _enum(sim.difficulty), "progress": sim.progress, "started_at": _iso(sim.started_at), "completed_at": _iso(sim.completed_at), "company": company.name, "role": sim.role, "project": project.title, "technology_stack": project.technology_stack or [], "average_score": round(float(simulation_averages[sim.id]), 1) if simulation_averages.get(sim.id) is not None else None} for sim, company, project in simulations]
    internship_data = None
    if simulations:
        sim, company, project = simulations[0]
        current_task = db.get(Task, sim.current_task_id) if sim.current_task_id else None
        sprint_state = db.execute(select(Sprint.sprint_number).where(Sprint.id == current_task.sprint_id)).first() if current_task else None
        latest_submission = db.execute(select(Submission).where(Submission.task_id == current_task.id).order_by(Submission.created_at.desc()).limit(1)).scalars().first() if current_task else None
        latest_version = db.execute(select(SubmissionVersion).where(SubmissionVersion.submission_id == latest_submission.id).order_by(SubmissionVersion.version_number.desc()).limit(1)).scalars().first() if latest_submission else None
        latest_review = db.execute(select(AIReview).where(AIReview.version_id == latest_version.id).order_by(AIReview.created_at.desc()).limit(1)).scalars().first() if latest_version else None
        latest_technical = next((record for record in reversed(technical_evaluations) if record.get("task_id")), None)
        evaluated_task = db.get(Task, UUID(latest_technical["task_id"])) if latest_technical else None
        evaluated_submission = db.get(Submission, UUID(latest_technical["submission_id"])) if latest_technical and latest_technical.get("submission_id") else None
        evaluated_version = db.execute(select(SubmissionVersion).where(SubmissionVersion.submission_id == evaluated_submission.id).order_by(SubmissionVersion.version_number.desc()).limit(1)).scalars().first() if evaluated_submission else None
        evaluated_review = db.execute(select(AIReview).where(AIReview.version_id == evaluated_version.id).order_by(AIReview.created_at.desc()).limit(1)).scalars().first() if evaluated_version else None
        internship_data = {"status": "active" if _enum(sim.status) == "in_progress" else _enum(sim.status), "company": company.name if company else None, "role": project.role or sim.role, "project": project.title, "technology_stack": project.technology_stack or [], "difficulty": _enum(sim.difficulty), "current_sprint": sprint_state[0] if sprint_state else None, "current_task": current_task.title if current_task else None, "task_status": _enum(current_task.status) if current_task else None, "submission_status": _enum(latest_version.status) if latest_version else (_enum(latest_submission.status) if latest_submission else None), "evaluation_status": _enum(latest_review.status) if latest_review else None, "latest_technical_score": latest_technical.get("score") if latest_technical else None, "latest_task": evaluated_task.title if evaluated_task else None, "latest_task_status": _enum(evaluated_task.status) if evaluated_task else None, "latest_submission_status": _enum(evaluated_version.status) if evaluated_version else None, "latest_evaluation_status": _enum(evaluated_review.status) if evaluated_review else None, "latest_evaluation_id": str(evaluated_review.id) if evaluated_review else None, "start_date": _iso(project.start_date or sim.started_at), "end_date": _iso(project.end_date or sim.completed_at)}
    has_competency_evidence = bool((profile and profile.last_assessed_at) or evaluation_stats[1])
    baseline_scores = (profile.baseline_competencies or {}) if profile and profile.last_assessed_at else {}
    baseline_ranked = sorted(baseline_scores.items(), key=lambda item: item[1], reverse=True)
    baseline_strengths = [key.replace("_", " ").title() for key, _ in baseline_ranked[:3]]
    baseline_weaknesses = [key.replace("_", " ").title() for key, _ in baseline_ranked[-3:]] if baseline_ranked else []
    return {"profile": {"student_id": student_id, "name": user.full_name, "email": user.email, "registered_at": _iso(user.created_at), "role": profile.role if profile else None, "target_role": profile.career_goal if profile else None, "domain": profile.domain if profile else None},
        "baseline": {"status": attempt.status if attempt else "not_started", "score": attempt.overall_score if attempt else None, "question_count": len(attempt.question_ids or []) if attempt else 0, "answered_count": answer_count or 0, "competencies": attempt.competencies if attempt else {}, "strengths": baseline_strengths, "weaknesses": baseline_weaknesses, "recommended_level": profile.recommended_difficulty if profile and profile.last_assessed_at else None, "completed_at": _iso(attempt.completed_at) if attempt else None},
        "competencies": {"baseline": profile.baseline_competencies if profile and profile.last_assessed_at else {}, "current": profile.current_competencies if profile and has_competency_evidence else {}},
        "internship": internship_data, "simulations": sim_details,
        "simulation_summary": {"count": len(simulations), "completed": sum(1 for sim, _, _ in simulations if _enum(sim.status) == "completed"), "average_score": round(float(evaluation_stats[0]), 1) if evaluation_stats[0] is not None else None, "evaluation_count": evaluation_stats[1], "technical_score": technical_evaluations[-1]["score"] if technical_evaluations else None, "behavioral_score": behavioral_evaluations[-1]["score"] if behavioral_evaluations else None, "technical_evaluations": technical_evaluations[-20:], "behavioral_evaluations": behavioral_evaluations[-20:], "performance_trend": performance_trend, "average_response_time": round(mean([float((i.metadata_json or {}).get("response_time")) for i in interactions if (i.metadata_json or {}).get("response_time") is not None]), 1) if any((i.metadata_json or {}).get("response_time") is not None for i in interactions) else None, "evaluations": [{"at": _iso(e.created_at), "score": e.overall_score, "branch": e.branch_selected, "competencies": e.competencies, "feedback": e.feedback} for e in evaluations[:20]], "responses": recent_responses[-20:], "adaptive_transitions": transitions[-20:]},
        "feedback": {"latest": {"summary": feedback.summary, "score": feedback.overall_score, "strengths": feedback.strengths, "weaknesses": feedback.weaknesses, "recommendations": feedback.recommendations, "generated_at": _iso(feedback.generated_at)} if feedback else None},
        "readiness": {"score": readiness.readiness_score, "level": readiness.readiness_level, "trend": readiness.trend_direction, "calculated_at": _iso(readiness.calculated_at)} if readiness else None}


def _event(student_id: str, name: str, activity: str, at, score=None, status=None, detail=None):
    return {"student_id": student_id, "student_name": name, "activity": activity, "timestamp": _iso(at), "score": score, "status": status, "detail": detail}


def student_activity(db: Session, student_id: str, limit: int = 100):
    user = _account(db, student_id)
    events = []
    for row in db.execute(select(AssessmentAttempt).where(AssessmentAttempt.student_id == student_id).order_by(AssessmentAttempt.started_at.desc()).limit(limit)).scalars():
        if row.completed_at: events.append(_event(student_id, user.full_name, "Baseline assessment completed", row.completed_at, row.overall_score, row.status))
    for row in db.execute(select(Simulation).where(Simulation.student_id == student_id).order_by(Simulation.updated_at.desc()).limit(limit)).scalars():
        events.append(_event(student_id, user.full_name, "Simulation completed" if _enum(row.status) == "completed" else "Simulation activity", row.completed_at or row.started_at or row.updated_at, status=_enum(row.status), detail=row.title))
    for row in db.execute(select(SimulationInteraction, InteractionEvaluation).outerjoin(InteractionEvaluation, InteractionEvaluation.interaction_id == SimulationInteraction.id).where(SimulationInteraction.student_id == student_id).order_by(SimulationInteraction.occurred_at.desc()).limit(limit)).all():
        interaction, evaluation = row
        if interaction.student_response:
            events.append(_event(student_id, user.full_name, "Simulation response evaluated" if evaluation else "Simulation response submitted", interaction.occurred_at, evaluation.overall_score if evaluation else None, evaluation.branch_selected if evaluation else None, (interaction.metadata_json or {}).get("title")))
            decision = (interaction.metadata_json or {}).get("adaptive_decision") or {}
            if decision.get("previous_difficulty") and decision.get("next_difficulty"):
                verb = "changed" if decision["previous_difficulty"] != decision["next_difficulty"] else "retained"
                events.append(_event(student_id, user.full_name, f"Difficulty {verb}: {decision['previous_difficulty']} → {decision['next_difficulty']}", interaction.occurred_at, decision.get("overall_score"), decision.get("branch_selected")))
    for row in db.execute(select(FeedbackReport).where(FeedbackReport.student_id == student_id).order_by(FeedbackReport.generated_at.desc()).limit(limit)).scalars():
        events.append(_event(student_id, user.full_name, "Feedback report generated", row.generated_at, row.overall_score))
    for row in db.execute(select(ReadinessSnapshot).where(ReadinessSnapshot.student_id == student_id).order_by(ReadinessSnapshot.calculated_at.desc()).limit(limit)).scalars():
        events.append(_event(student_id, user.full_name, "Readiness recalculated", row.calculated_at, row.readiness_score, row.readiness_level))
    for row in db.execute(select(Certificate).where(Certificate.student_id == student_id).order_by(Certificate.issued_at.desc()).limit(limit)).scalars():
        events.append(_event(student_id, user.full_name, "Certificate issued", row.issued_at, row.readiness_percentile, detail=row.certificate_number))
    for row in db.execute(select(ActivityLog).join(Company, Company.id == ActivityLog.company_id).where(Company.student_id == student_id).order_by(ActivityLog.created_at.desc()).limit(limit)).scalars():
        events.append(_event(student_id, user.full_name, row.action.replace("_", " ").capitalize(), row.created_at, detail=row.detail))
    return sorted(events, key=lambda item: item["timestamp"] or "", reverse=True)[:limit]


def recent_activity(db: Session, limit: int = 30):
    events = []
    # Each source is individually bounded; merge only recent rows for registered students.
    for row, name in db.execute(select(AssessmentAttempt, User.full_name).join(User, User.student_id == AssessmentAttempt.student_id).where(User.role == "student", AssessmentAttempt.completed_at.is_not(None)).order_by(AssessmentAttempt.completed_at.desc()).limit(limit)).all():
        events.append(_event(row.student_id, name, "Baseline assessment completed", row.completed_at, row.overall_score))
    for row, name in db.execute(select(Simulation, User.full_name).join(User, User.student_id == Simulation.student_id).where(User.role == "student").order_by(Simulation.updated_at.desc()).limit(limit)).all():
        if row.started_at: events.append(_event(row.student_id, name, "Simulation completed" if _enum(row.status) == "completed" else "Internship simulation started", row.completed_at or row.started_at, status=_enum(row.status), detail=row.title))
    for row, evaluation, name in db.execute(select(SimulationInteraction, InteractionEvaluation, User.full_name).join(InteractionEvaluation, InteractionEvaluation.interaction_id == SimulationInteraction.id).join(User, User.student_id == SimulationInteraction.student_id).where(User.role == "student").order_by(SimulationInteraction.occurred_at.desc()).limit(limit)).all():
        events.append(_event(row.student_id, name, "Simulation evaluated", row.occurred_at, evaluation.overall_score, evaluation.branch_selected))
        decision = (row.metadata_json or {}).get("adaptive_decision") or {}
        if decision.get("previous_difficulty") and decision.get("next_difficulty"):
            verb = "changed" if decision["previous_difficulty"] != decision["next_difficulty"] else "retained"
            events.append(_event(row.student_id, name, f"Difficulty {verb}: {decision['previous_difficulty']} → {decision['next_difficulty']}", row.occurred_at, decision.get("overall_score"), decision.get("branch_selected")))
    for row, name in db.execute(select(FeedbackReport, User.full_name).join(User, User.student_id == FeedbackReport.student_id).where(User.role == "student").order_by(FeedbackReport.generated_at.desc()).limit(limit)).all():
        events.append(_event(row.student_id, name, "Feedback report generated", row.generated_at, row.overall_score))
    for row, name in db.execute(select(ReadinessSnapshot, User.full_name).join(User, User.student_id == ReadinessSnapshot.student_id).where(User.role == "student").order_by(ReadinessSnapshot.calculated_at.desc()).limit(limit)).all():
        events.append(_event(row.student_id, name, "Readiness recalculated", row.calculated_at, row.readiness_score, row.readiness_level))
    for row, name in db.execute(select(Certificate, User.full_name).join(User, User.student_id == Certificate.student_id).where(User.role == "student").order_by(Certificate.issued_at.desc()).limit(limit)).all():
        events.append(_event(row.student_id, name, "Certificate issued", row.issued_at, row.readiness_percentile))
    for row, name, student_id in db.execute(select(ActivityLog, User.full_name, Company.student_id).join(Company, Company.id == ActivityLog.company_id).join(User, User.student_id == Company.student_id).where(User.role == "student").order_by(ActivityLog.created_at.desc()).limit(limit)).all():
        events.append(_event(student_id, name, row.action.replace("_", " ").capitalize(), row.created_at, detail=row.detail))
    return sorted(events, key=lambda item: item["timestamp"] or "", reverse=True)[:limit]


def analytics(db: Session):
    ranked_readiness = select(
        ReadinessSnapshot.student_id.label("student_id"), ReadinessSnapshot.readiness_level.label("level"),
        ReadinessSnapshot.readiness_score.label("score"),
        func.row_number().over(partition_by=ReadinessSnapshot.student_id, order_by=ReadinessSnapshot.calculated_at.desc()).label("rank"),
    ).join(User, User.student_id == ReadinessSnapshot.student_id).where(User.role == "student").subquery()
    latest_rows = db.execute(select(ranked_readiness.c.student_id, ranked_readiness.c.level, ranked_readiness.c.score).where(ranked_readiness.c.rank == 1)).all()
    latest = {student_id: (level, score) for student_id, level, score in latest_rows}
    profiles = db.execute(select(StudentProfile.recommended_difficulty, StudentProfile.current_competencies)
        .join(User, User.student_id == StudentProfile.student_id).where(User.role == "student", StudentProfile.last_assessed_at.is_not(None))).all()
    difficulty = {}; competency = {}
    for level, scores in profiles:
        difficulty[level] = difficulty.get(level, 0) + 1
        for key, value in (scores or {}).items(): competency.setdefault(key, []).append(float(value))
    attempts = db.execute(select(AssessmentAttempt.overall_score).join(User, User.student_id == AssessmentAttempt.student_id).where(User.role == "student", AssessmentAttempt.status == "completed", AssessmentAttempt.overall_score.is_not(None))).scalars().all()
    evaluations = db.execute(select(InteractionEvaluation.overall_score, InteractionEvaluation.branch_selected).join(User, User.student_id == InteractionEvaluation.student_id).where(User.role == "student")).all()
    def distribution(values):
        bins = {"0–39": 0, "40–59": 0, "60–79": 0, "80–100": 0}
        for value in values:
            bins["0–39" if value < 40 else "40–59" if value < 60 else "60–79" if value < 80 else "80–100"] += 1
        return [{"range": key, "count": value} for key, value in bins.items()]
    branches = {}
    for _score, branch in evaluations:
        if branch: branches[branch] = branches.get(branch, 0) + 1
    _, attention_total = students_needing_attention(db)
    return {"average_competencies": [{"competency": k.replace("_", " ").title(), "score": round(mean(v), 1)} for k, v in sorted(competency.items())],
        "readiness_distribution": [{"level": str(level), "count": sum(1 for current_level, _score in latest.values() if current_level == level)} for level in sorted({value[0] for value in latest.values()}, key=str)],
        "difficulty_distribution": [{"difficulty": k, "count": v} for k, v in sorted(difficulty.items())],
        "baseline_performance": distribution(attempts), "simulation_performance": distribution([r[0] for r in evaluations]),
        "adaptive_branch_distribution": [{"branch": k, "count": v} for k, v in sorted(branches.items())],
        "students_requiring_attention": attention_total}
