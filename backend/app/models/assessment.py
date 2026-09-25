"""Skill catalog, student skills, and the baseline assessment question bank."""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin


class Skill(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "skills"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False, default="technical")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_skills_name", "name", unique=True),)


class StudentSkill(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "student_skills"

    student_id: Mapped[str] = mapped_column(String(100), nullable=False)
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
    )
    self_rating: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    skill = relationship("Skill")

    __table_args__ = (
        UniqueConstraint("student_id", "skill_id", name="uq_student_skills_student_skill"),
        Index("ix_student_skills_student_id", "student_id"),
    )


class AssessmentQuestion(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "assessment_questions"

    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    competency: Mapped[str] = mapped_column(String(80), nullable=False)
    question_type: Mapped[str] = mapped_column(String(30), nullable=False, default="mcq")
    options: Mapped[List[Dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    correct_option: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    rubric_keywords: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    difficulty: Mapped[str] = mapped_column(String(30), nullable=False, default="intermediate")
    domain: Mapped[str] = mapped_column(String(80), nullable=False, default="software")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (Index("ix_assessment_questions_competency", "competency"),)


class AssessmentAttempt(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "assessment_attempts"

    student_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="in_progress")
    question_ids: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    overall_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    competencies: Mapped[Dict[str, float]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    answers = relationship("AssessmentAnswer", back_populates="attempt", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_assessment_attempts_student_id", "student_id"),)


class AssessmentAnswer(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "assessment_answers"

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment_attempts.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment_questions.id", ondelete="CASCADE"), nullable=False
    )
    response: Mapped[str] = mapped_column(Text, nullable=False, default="")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    competency: Mapped[str] = mapped_column(String(80), nullable=False)

    attempt = relationship("AssessmentAttempt", back_populates="answers")

    __table_args__ = (Index("ix_assessment_answers_attempt_id", "attempt_id"),)
