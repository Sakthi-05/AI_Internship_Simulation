"""Demo loop: review evidence, difficulty, branch, and a single decline warning."""
import uuid

import pytest
from jose import jwt
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.core.config import settings
from app.models.company import Company
from app.models.enums import CompanyType, DifficultyLevel, ProjectStatus, SimulationStatus
from app.models.intelligence import EarlyWarningAlert, InteractionEvaluation, StudentProfile
from app.models.project import Project
from app.models.simulation import Simulation
from app.models.simulation_interaction import SimulationInteraction
from app.services.ai.review_service import _apply_review_to_competencies
from app.services.assessment_service import start_attempt, submit_attempt
from app.services.intelligence_service import (
    adaptive_expected_seconds,
    apply_adaptive_decision,
    calculate_readiness,
    create_feedback_report,
    evaluate_interaction,
    generate_adaptive_situation,
    get_or_create_profile,
    update_competencies,
)


def _world(student_id: str):
    db = SessionLocal()
    company = Company(
        name="Demo Co",
        company_type=CompanyType.STARTUP,
        industry="Technology",
        department="Engineering",
        mission="Ship useful software.",
        description="Demo company",
        student_id=student_id,
        size="small",
        technology_stack=["Python"],
    )
    db.add(company)
    db.flush()
    project = Project(
        company_id=company.id,
        title="Demo Project",
        role="Backend Developer",
        technology_stack=["Python"],
        difficulty=DifficultyLevel.INTERMEDIATE,
        status=ProjectStatus.ACTIVE,
        objectives=["Deliver the API"],
        modules=["Core"],
        deliverables=["Service"],
    )
    db.add(project)
    db.flush()
    simulation = Simulation(
        student_id=student_id,
        company_id=company.id,
        project_id=project.id,
        title="Demo simulation",
        description="Demo",
        role="Backend Developer",
        difficulty=DifficultyLevel.INTERMEDIATE,
        status=SimulationStatus.IN_PROGRESS,
    )
    db.add(simulation)
    db.flush()
    profile = get_or_create_profile(db, student_id)
    profile.role = profile.role or "Backend Developer"
    if not profile.last_assessed_at:
        profile.recommended_difficulty = "beginner"
        profile.current_competencies = {
            "technical_knowledge": 90,
            "problem_solving": 40,
            "decision_making": 70,
            "communication": 55,
            "critical_thinking": 70,
            "adaptability": 70,
            "time_management": 70,
            "professionalism": 80,
        }
    db.add(profile)
    db.commit()
    return db, simulation.id, profile.id


def test_assessment_returns_every_active_question_and_scores_profile():
    db = SessionLocal()
    student_id = f"assess-{uuid.uuid4().hex[:8]}"
    try:
        started = start_attempt(db, student_id)
        db.commit()
        assert started["status"] == "in_progress"
        assert started["questions"]
        answers = []
        for question in started["questions"]:
            if question["question_type"] == "mcq":
                answers.append({"question_id": question["id"], "response": "B"})
            else:
                answers.append({
                    "question_id": question["id"],
                    "response": "I am blocked by the API. The impact is the deadline. I need a next step and will test the fix.",
                })
        result = submit_attempt(db, student_id, uuid.UUID(started["id"]), answers)
        db.commit()
        assert result["overall_score"] > 0
        profile = get_or_create_profile(db, student_id)
        assert profile.current_competencies
        again = start_attempt(db, student_id)
        db.commit()
        assert again["id"] != started["id"]
        assert again["status"] == "in_progress"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_code_review_updates_existing_competencies():
    student_id = f"review-{uuid.uuid4().hex[:8]}"
    db, _simulation_id, profile_id = _world(student_id)
    try:
        before = get_or_create_profile(db, student_id).current_competencies["technical_knowledge"]
        await _apply_review_to_competencies(
            db,
            student_id,
            {
                "score": 90,
                "code_quality": {"score": 96},
                "testing": {"score": 94},
                "security": {"score": 88},
                "performance": {"score": 91},
            },
            uuid.uuid4(),
        )
        db.commit()
        profile = db.get(StudentProfile, profile_id)
        assert profile.current_competencies["technical_knowledge"] > before
    finally:
        db.close()


@pytest.mark.asyncio
async def test_interaction_evaluation_updates_competency_and_feedback():
    student_id = f"eval-{uuid.uuid4().hex[:8]}"
    db, simulation_id, profile_id = _world(student_id)
    try:
        simulation = db.get(Simulation, simulation_id)
        interaction = SimulationInteraction(
            simulation_id=simulation.id,
            student_id=student_id,
            sequence=1,
            situation="A production API is failing.",
            interaction_type="adaptive_situation",
            metadata_json={},
        )
        db.add(interaction)
        db.commit()
        before = db.get(StudentProfile, profile_id).current_competencies["problem_solving"]
        evaluation = await evaluate_interaction(
            db,
            simulation,
            interaction,
            "First I reproduce the error, then I verify the request, because the impact is customer traffic. I will test the fix.",
        )
        update_competencies(db, db.get(StudentProfile, profile_id), evaluation["competencies"])
        report = await create_feedback_report(db, student_id, simulation.id)
        db.commit()
        assert db.get(StudentProfile, profile_id).current_competencies["problem_solving"] != before
        assert report["summary"]
        assert "skill_gaps" in report
    finally:
        db.close()


@pytest.mark.asyncio
async def test_behavioral_stretch_branch_preserves_technical_difficulty():
    student_id = f"diff-{uuid.uuid4().hex[:8]}"
    db, simulation_id, profile_id = _world(student_id)
    try:
        simulation = db.get(Simulation, simulation_id)
        profile = db.get(StudentProfile, profile_id)
        baseline_recommendation = profile.recommended_difficulty
        decided = apply_adaptive_decision(db, simulation, profile, {"branch_selected": "high_performance", "difficulty_delta": 0})
        situation = await generate_adaptive_situation(db, simulation, profile, branch="high_performance", decided_difficulty=decided)
        db.commit()
        assert decided == "intermediate"
        assert situation["difficulty"] == "intermediate"
        assert "Stretch" in situation["title"]
        assert profile.recommended_difficulty == baseline_recommendation
    finally:
        db.close()


@pytest.mark.asyncio
async def test_risk_branch_uses_recovery_without_lowering_technical_difficulty():
    student_id = f"risk-{uuid.uuid4().hex[:8]}"
    db, simulation_id, profile_id = _world(student_id)
    try:
        simulation = db.get(Simulation, simulation_id)
        profile = db.get(StudentProfile, profile_id)
        decided = apply_adaptive_decision(db, simulation, profile, {"branch_selected": "risk_escalation"})
        situation = await generate_adaptive_situation(db, simulation, profile, branch="risk_escalation", decided_difficulty=decided)
        assert decided == "intermediate"
        assert situation["difficulty"] == "intermediate"
        assert "Recovery" in situation["title"]
    finally:
        db.close()


@pytest.mark.parametrize(("quality", "response_time", "expected_time", "wanted"), [
    (92, 100, 300, "advanced"),
    (35, 500, 300, "beginner"),
    (70, 300, 300, "intermediate"),
    (35, 30, 300, "beginner"),
    (92, None, None, "advanced"),
])
def test_behavioral_quality_and_speed_do_not_control_technical_difficulty(quality, response_time, expected_time, wanted):
    student_id = f"speed-{uuid.uuid4().hex[:8]}"
    db, simulation_id, profile_id = _world(student_id)
    try:
        simulation = db.get(Simulation, simulation_id)
        profile = db.get(StudentProfile, profile_id)
        evaluation = {"overall_score": quality, "response_time": response_time, "expected_time": expected_time}
        decided = apply_adaptive_decision(db, simulation, profile, evaluation)
        assert decided == "intermediate"
        assert evaluation["previous_difficulty"] == "intermediate"
        assert evaluation["next_difficulty"] == "intermediate"
        assert evaluation["difficulty_delta"] == 0
        assert evaluation["evidence_kind"] == "behavioral"
        db.commit()
        db.expire_all()
        assert db.get(Simulation, simulation_id).difficulty.value == "intermediate"
    finally:
        db.close()


def test_expected_duration_scales_by_difficulty_and_speed_ratio_uses_it():
    beginner = adaptive_expected_seconds("beginner")
    intermediate = adaptive_expected_seconds("intermediate")
    advanced = adaptive_expected_seconds("advanced")
    assert beginner < intermediate < advanced
    assert adaptive_expected_seconds("advanced", 7200) == 7200
    student_id = f"expected-time-{uuid.uuid4().hex[:8]}"
    db, simulation_id, profile_id = _world(student_id)
    try:
        simulation = db.get(Simulation, simulation_id)
        profile = db.get(StudentProfile, profile_id)
        beginner_evaluation = {"overall_score": 90, "response_time": 225, "expected_time": beginner}
        advanced_evaluation = {"overall_score": 90, "response_time": 225, "expected_time": advanced}
        apply_adaptive_decision(db, simulation, profile, beginner_evaluation)
        simulation.difficulty = DifficultyLevel.INTERMEDIATE
        apply_adaptive_decision(db, simulation, profile, advanced_evaluation)
        assert beginner_evaluation["speed_ratio"] == 1.25
        assert advanced_evaluation["speed_ratio"] == 0.5
    finally:
        db.close()


def test_completed_branch_survives_fetching_next_situation_again(client):
    student_id = f"branch-refresh-{uuid.uuid4().hex[:8]}"
    db, simulation_id, _profile_id = _world(student_id)
    try:
        simulation = db.get(Simulation, simulation_id)
        simulation.difficulty = DifficultyLevel.ADVANCED
        db.add(SimulationInteraction(
            simulation_id=simulation_id,
            student_id=student_id,
            sequence=1,
            situation="Previous adaptive task",
            student_response="Completed",
            interaction_type="adaptive_situation",
            metadata_json={
                "branch_selected": "high_performance",
                "adaptive_decision": {
                    "branch_selected": "high_performance",
                    "previous_difficulty": "intermediate",
                    "next_difficulty": "advanced",
                },
            },
        ))
        db.commit()
    finally:
        db.close()

    token = jwt.encode(
        {"sub": student_id, "student_id": student_id, "role": "student"},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    response = client.get(
        f"/api/intelligence/simulations/{simulation_id}/adaptive/next",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["situation"]["title"].startswith("Adaptive Situation 2: Stretch:")
    assert response.json()["situation"]["difficulty"] == "advanced"


@pytest.mark.asyncio
async def test_full_flow_reaches_next_adaptive_situation():
    student_id = f"loop-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        started = start_attempt(db, student_id)
        answers = [
            {"question_id": question["id"], "response": "B" if question["question_type"] == "mcq" else "I reproduced the failure, checked the impact, and tested the next step."}
            for question in started["questions"]
        ]
        submit_attempt(db, student_id, uuid.UUID(started["id"]), answers)
        db.commit()
        db.close()
        db, simulation_id, profile_id = _world(student_id)
        profile = db.get(StudentProfile, profile_id)
        assert profile.current_competencies
        simulation = db.get(Simulation, simulation_id)
        interaction = SimulationInteraction(
            simulation_id=simulation.id,
            student_id=student_id,
            sequence=1,
            situation="Choose how to handle a failing release.",
            interaction_type="adaptive_situation",
            metadata_json={},
        )
        db.add(interaction)
        db.commit()
        evaluation = await evaluate_interaction(db, simulation, interaction, "I will reproduce the failure, check the impact, and test the fix before release.")
        update_competencies(db, profile, evaluation["competencies"])
        decided = apply_adaptive_decision(db, simulation, profile, evaluation)
        situation = await generate_adaptive_situation(db, simulation, profile, branch=evaluation["branch_selected"], decided_difficulty=decided)
        report = await create_feedback_report(db, student_id, simulation.id)
        readiness = calculate_readiness(db, student_id, simulation.id)
        db.commit()
        assert situation["difficulty"] == decided
        assert report["overall_score"] >= 0
        assert "readiness_score" in readiness
    finally:
        db.close()


def test_decline_creates_one_open_warning():
    student_id = f"warn-{uuid.uuid4().hex[:8]}"
    db, simulation_id, _profile_id = _world(student_id)
    try:
        simulation = db.get(Simulation, simulation_id)
        for index, score in enumerate((80, 70, 60), start=1):
            interaction = SimulationInteraction(
                simulation_id=simulation.id,
                student_id=student_id,
                sequence=index,
                situation="Decision",
                interaction_type="adaptive_situation",
                metadata_json={},
            )
            db.add(interaction)
            db.flush()
            db.add(InteractionEvaluation(
                interaction_id=interaction.id,
                simulation_id=simulation.id,
                student_id=student_id,
                overall_score=score,
                competencies={},
                feedback="Noted",
            ))
        db.commit()
        calculate_readiness(db, student_id, simulation.id)
        calculate_readiness(db, student_id, simulation.id)
        db.commit()
        count = db.execute(
            select(func.count()).select_from(EarlyWarningAlert).where(
                EarlyWarningAlert.student_id == student_id,
                EarlyWarningAlert.status == "OPEN",
            )
        ).scalar_one()
        assert count == 1
    finally:
        db.close()
