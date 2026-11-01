import uuid
from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin


class AcceptanceCriteriaVersion(Base, UUIDPKMixin, TimestampMixin):
    """One snapshot of a task's acceptance criteria.

    ``status`` is ``active`` or ``superseded``. ``items`` is a list of
    ``{"text", "state"}`` objects. Item state is ``active``, ``modified``,
    or ``superseded``. ``workflow_event_id`` points at the requirement
    change that created the snapshot. The baseline snapshot has no event.
    """

    __tablename__ = "acceptance_criteria_versions"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    workflow_event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_events.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("task_id", "version_number", name="uq_acceptance_criteria_versions_task_number"),
        Index("ix_acceptance_criteria_versions_task_id", "task_id"),
        Index("ix_acceptance_criteria_versions_status", "task_id", "status"),
        Index("ix_acceptance_criteria_versions_event_id", "workflow_event_id"),
    )
