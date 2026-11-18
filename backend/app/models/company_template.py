"""
CompanyTemplate — admin-managed reusable company templates.

Intentionally separate from the ``Company`` model (which is student-scoped
and has a mandatory ``student_id`` FK).  Templates have no student owner and
are never modified by student flows.
"""
from __future__ import annotations

from typing import List

from sqlalchemy import Boolean, Enum, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin
from app.models.enums import CompanyType


class CompanyTemplate(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "company_templates"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    company_type: Mapped[CompanyType] = mapped_column(
        Enum(CompanyType, name="company_type_enum", create_constraint=False),
        nullable=False,
    )
    industry: Mapped[str] = mapped_column(String(150), nullable=False)
    department: Mapped[str] = mapped_column(String(150), nullable=False)
    mission: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[str] = mapped_column(
        String(40), nullable=False, default="small", server_default="small"
    )
    technology_stack: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Soft-delete flag — deactivated templates are excluded from default lists
    # but their rows are never hard-deleted.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # student_id of the admin who created the template (audit trail only).
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)

    __table_args__ = (
        Index("ix_company_templates_company_type", "company_type"),
        Index("ix_company_templates_is_active", "is_active"),
        Index("ix_company_templates_created_by", "created_by"),
    )
