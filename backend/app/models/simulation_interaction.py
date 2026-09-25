"""Append-only simulation interaction history.

Stores each meaningful student/system interaction in chronological sequence
without replacing Task, Scenario, Submission, or SimulationTaskPath records.
`parent_interaction_id` is reserved for future dynamic branching.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin


class SimulationInteraction(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "simulation_interactions"

    simulation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id: Mapped[str] = mapped_column(String(100), nullable=False)
    scenario_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scenarios.id", ondelete="SET NULL"),
        nullable=True,
    )
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    situation: Mapped[str] = mapped_column(Text, nullable=False)
    student_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decision: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    interaction_type: Mapped[str] = mapped_column(String(80), nullable=False)
    parent_interaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_interactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    simulation = relationship("Simulation", back_populates="interactions")
    scenario = relationship("Scenario", foreign_keys=[scenario_id])
    task = relationship("Task", foreign_keys=[task_id])
    parent = relationship(
        "SimulationInteraction",
        remote_side="SimulationInteraction.id",
        foreign_keys=[parent_interaction_id],
    )

    __table_args__ = (
        Index("ix_simulation_interactions_simulation_id", "simulation_id"),
        Index("ix_simulation_interactions_student_id", "student_id"),
        Index("ix_simulation_interactions_scenario_id", "scenario_id"),
        Index("ix_simulation_interactions_task_id", "task_id"),
        Index("ix_simulation_interactions_occurred_at", "occurred_at"),
        Index("ix_simulation_interactions_parent_id", "parent_interaction_id"),
        Index(
            "ix_simulation_interactions_simulation_sequence",
            "simulation_id",
            "sequence",
            unique=True,
        ),
    )
