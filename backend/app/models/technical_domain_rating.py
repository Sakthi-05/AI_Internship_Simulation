from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin


class StudentTechnicalTechnologyRating(Base, UUIDPKMixin, TimestampMixin):
    """A student's self-reported familiarity with a technology in one domain."""

    __tablename__ = "student_technical_technology_ratings"

    student_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("student_profiles.student_id", ondelete="CASCADE"),
        nullable=False,
    )
    technical_domain_id: Mapped[str] = mapped_column(String(80), nullable=False)
    technology_id: Mapped[str] = mapped_column(String(120), nullable=False)
    self_rating: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "technical_domain_id",
            "technology_id",
            name="uq_student_technical_rating_domain_technology",
        ),
        CheckConstraint(
            "self_rating >= 1 AND self_rating <= 5",
            name="ck_student_technical_rating_self_rating_range",
        ),
        Index("ix_student_technical_ratings_student_id", "student_id"),
        Index("ix_student_technical_ratings_domain_id", "technical_domain_id"),
    )
