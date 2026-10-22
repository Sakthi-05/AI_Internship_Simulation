import uuid
from typing import Any, Dict, Optional

from sqlalchemy import Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin


class SoftSkillEvidence(Base, UUIDPKMixin, TimestampMixin):
    """Observed soft-skill evidence tied to a student, simulation, task, and event.

    A row is stored even when the observation cannot be scored. ``status`` is
    ``insufficient_evidence`` and ``score`` stays null until the student's own
    messages and later actions meet the scenario rubric.
    """

    __tablename__ = "soft_skill_evidence"

    student_id: Mapped[str] = mapped_column(String(100), nullable=False)
    simulation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_events.id", ondelete="CASCADE"), nullable=False
    )
    scenario_key: Mapped[str] = mapped_column(String(80), nullable=False)
    competency: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="insufficient_evidence")
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("event_id", "competency", name="uq_soft_skill_evidence_event_competency"),
        Index("ix_soft_skill_evidence_student_id", "student_id"),
        Index("ix_soft_skill_evidence_simulation_id", "simulation_id"),
        Index("ix_soft_skill_evidence_task_id", "task_id"),
        Index("ix_soft_skill_evidence_event_id", "event_id"),
    )
