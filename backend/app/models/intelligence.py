import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin


class StudentProfile(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "student_profiles"

    student_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    domain: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    technical_domain_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    role: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    college: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    degree: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    year_of_study: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    career_goal: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    skills: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    recommended_parameters: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    baseline_competencies: Mapped[Dict[str, float]] = mapped_column(JSONB, nullable=False, default=dict)
    current_competencies: Mapped[Dict[str, float]] = mapped_column(JSONB, nullable=False, default=dict)
    strengths: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    weaknesses: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    assessment_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recommended_difficulty: Mapped[str] = mapped_column(String(30), nullable=False, default="intermediate")
    last_assessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_student_profiles_student_id", "student_id", unique=True),)


class InteractionEvaluation(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "interaction_evaluations"

    interaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("simulation_interactions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    simulation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[str] = mapped_column(String(100), nullable=False)
    overall_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    competencies: Mapped[Dict[str, float]] = mapped_column(JSONB, nullable=False, default=dict)
    strengths: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    weaknesses: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    mistakes: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    feedback: Mapped[str] = mapped_column(Text, nullable=False, default="")
    recommendations: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    branch_selected: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    difficulty_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.75)

    interaction = relationship("SimulationInteraction", foreign_keys=[interaction_id])
    simulation = relationship("Simulation", foreign_keys=[simulation_id])

    __table_args__ = (
        Index("ix_interaction_evaluations_simulation_id", "simulation_id"),
        Index("ix_interaction_evaluations_student_id", "student_id"),
    )


class FeedbackReport(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "feedback_reports"

    student_id: Mapped[str] = mapped_column(String(100), nullable=False)
    simulation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("simulations.id", ondelete="SET NULL"), nullable=True
    )
    overall_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    strengths: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    weaknesses: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    recommendations: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    generated_by: Mapped[str] = mapped_column(String(50), nullable=False, default="rule_engine")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (Index("ix_feedback_reports_student_id", "student_id"),)


class ReadinessSnapshot(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "readiness_snapshots"

    student_id: Mapped[str] = mapped_column(String(100), nullable=False)
    simulation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("simulations.id", ondelete="SET NULL"), nullable=True
    )
    readiness_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    percentile: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    benchmark_score: Mapped[float] = mapped_column(Float, nullable=False, default=70)
    gap_to_benchmark: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    readiness_level: Mapped[str] = mapped_column(String(50), nullable=False, default="NOT_YET_PLACEMENT_READY")
    forecast_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    trend_direction: Mapped[str] = mapped_column(String(30), nullable=False, default="stable")
    metrics: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (Index("ix_readiness_snapshots_student_id", "student_id"),)


class EarlyWarningAlert(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "early_warning_alerts"

    student_id: Mapped[str] = mapped_column(String(100), nullable=False)
    simulation_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("simulations.id", ondelete="SET NULL"), nullable=True)
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(30), nullable=False, default="warning")
    previous_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trend_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="OPEN")

    __table_args__ = (Index("ix_early_warning_alerts_student_id", "student_id"),)


class Certificate(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "certificates"

    student_id: Mapped[str] = mapped_column(String(100), nullable=False)
    simulation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("simulations.id", ondelete="SET NULL"), nullable=True
    )
    certificate_number: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str] = mapped_column(String(150), nullable=False)
    readiness_percentile: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_certificates_student_id", "student_id"),)
