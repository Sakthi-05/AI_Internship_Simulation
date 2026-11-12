import uuid

from sqlalchemy import Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin
from app.models.enums import MessageSenderType


class Message(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "messages"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    simulation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("simulations.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    in_reply_to_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    sender_type: Mapped[MessageSenderType] = mapped_column(
        Enum(MessageSenderType, name="message_sender_type_enum"), nullable=False
    )
    sender_id: Mapped[str] = mapped_column(Text, nullable=False)  # student_id, manager.id, or team_member.id
    content: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_messages_company_id", "company_id"),
        Index("ix_messages_created_at", "created_at"),
        Index("ix_messages_simulation_id", "simulation_id"),
        UniqueConstraint("company_id", "sender_id", "idempotency_key", name="uq_messages_sender_idempotency"),
    )
