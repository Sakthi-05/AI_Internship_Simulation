import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin
from app.models.enums import NotificationType


class Notification(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "notifications"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    notification_type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="notification_type_enum"), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_notifications_company_id", "company_id"),
        Index("ix_notifications_is_read", "is_read"),
        # Partial index on (company_id, notification_type, created_at DESC)
        # for efficient cooldown duplicate lookups.  Covers the WHERE clause
        # used by _find_recent_duplicate() so the query hits only recent rows.
        Index(
            "ix_notifications_cooldown_lookup",
            "company_id",
            "notification_type",
            "created_at",
        ),
    )

