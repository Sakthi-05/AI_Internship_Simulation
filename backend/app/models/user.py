"""Local accounts for self-contained Module 1 authentication."""
import uuid

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin


class User(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="student")
    # Same identifier carried in the JWT `sub` claim and on every simulation row.
    student_id: Mapped[str] = mapped_column(String(100), nullable=False)

    __table_args__ = (
        Index("ix_users_email", "email", unique=True),
        Index("ix_users_student_id", "student_id", unique=True),
    )


def new_student_id() -> str:
    return str(uuid.uuid4())
