import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import ARRAY, Boolean, DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin
from app.models.enums import MeetingType


class MeetingSchedule(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "meeting_schedules"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    simulation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("simulations.id", ondelete="SET NULL"), nullable=True
    )
    meeting_type: Mapped[MeetingType] = mapped_column(
        Enum(MeetingType, name="meeting_type_enum"), nullable=False
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    agenda: Mapped[str] = mapped_column(Text, nullable=False)
    participants: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attendance: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    action_items: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    student_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    __table_args__ = (
        Index("ix_meeting_schedules_project_id", "project_id"),
        Index("ix_meeting_schedules_scheduled_at", "scheduled_at"),
        Index("ix_meeting_schedules_simulation_id", "simulation_id"),
        UniqueConstraint("project_id", "idempotency_key", name="uq_meetings_project_idempotency"),
    )


class SprintReview(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "sprint_reviews"

    sprint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sprints.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    velocity_completed_points: Mapped[int] = mapped_column(nullable=False, default=0)
    manager_feedback: Mapped[str] = mapped_column(Text, nullable=True)

    sprint = relationship("Sprint", back_populates="sprint_review")

    __table_args__ = (
        Index("ix_sprint_reviews_sprint_id", "sprint_id"),
    )
