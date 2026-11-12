import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin
from app.models.enums import NotificationType


class Notification(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "notifications"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    simulation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("simulations.id", ondelete="SET NULL"), nullable=True
    )
    dedupe_key: Mapped[str | None] = mapped_column(String(250), nullable=True)
    context_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    notification_type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="notification_type_enum"), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_notifications_company_id", "company_id"),
        Index("ix_notifications_is_read", "is_read"),
        Index("ix_notifications_simulation_id", "simulation_id"),
        UniqueConstraint("company_id", "dedupe_key", name="uq_notifications_company_dedupe"),
    )
