"""
Admin-only service for managing reusable company templates.

Templates are stored in ``company_templates`` and are completely separate
from the student-scoped ``companies`` table so that existing student
simulation data is never touched.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.company_template import CompanyTemplate
from app.models.enums import CompanyType

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

VALID_COMPANY_TYPES = {ct.value for ct in CompanyType}


class CompanyTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    company_type: str
    industry: str = Field(..., min_length=1, max_length=150)
    department: str = Field(..., min_length=1, max_length=150)
    mission: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    size: str = Field(default="small", max_length=40)
    technology_stack: list[str] = Field(default_factory=list)

    @field_validator("company_type")
    @classmethod
    def _validate_company_type(cls, value: str) -> str:
        if value not in VALID_COMPANY_TYPES:
            raise ValueError(
                f"Invalid company_type '{value}'. "
                f"Must be one of: {sorted(VALID_COMPANY_TYPES)}"
            )
        return value

    @field_validator("technology_stack")
    @classmethod
    def _validate_tech_stack(cls, value: list) -> list:
        if not all(isinstance(item, str) for item in value):
            raise ValueError("technology_stack must be a list of strings")
        return value


class CompanyTemplateUpdate(BaseModel):
    """All fields are optional for partial updates."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    company_type: Optional[str] = None
    industry: Optional[str] = Field(default=None, min_length=1, max_length=150)
    department: Optional[str] = Field(default=None, min_length=1, max_length=150)
    mission: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = Field(default=None, min_length=1)
    size: Optional[str] = Field(default=None, max_length=40)
    technology_stack: Optional[list[str]] = None

    @field_validator("company_type")
    @classmethod
    def _validate_company_type(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in VALID_COMPANY_TYPES:
            raise ValueError(
                f"Invalid company_type '{value}'. "
                f"Must be one of: {sorted(VALID_COMPANY_TYPES)}"
            )
        return value

    @field_validator("technology_stack")
    @classmethod
    def _validate_tech_stack(cls, value: Optional[list]) -> Optional[list]:
        if value is not None and not all(isinstance(item, str) for item in value):
            raise ValueError("technology_stack must be a list of strings")
        return value


class CompanyTemplateResponse(BaseModel):
    id: str
    name: str
    company_type: str
    industry: str
    department: str
    mission: str
    description: str
    size: str
    technology_stack: list[str]
    is_active: bool
    created_by: str
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize(template: CompanyTemplate) -> dict:
    return {
        "id": str(template.id),
        "name": template.name,
        "company_type": template.company_type.value
        if hasattr(template.company_type, "value")
        else template.company_type,
        "industry": template.industry,
        "department": template.department,
        "mission": template.mission,
        "description": template.description,
        "size": template.size,
        "technology_stack": template.technology_stack or [],
        "is_active": template.is_active,
        "created_by": template.created_by,
        "created_at": template.created_at.isoformat() if template.created_at else None,
        "updated_at": template.updated_at.isoformat() if template.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------


def list_company_templates(
    db: Session,
    *,
    q: Optional[str] = None,
    company_type: Optional[str] = None,
    include_inactive: bool = False,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    """Return a paginated list of company templates."""
    if hasattr(page, "default"):
        page = page.default
    if hasattr(page_size, "default"):
        page_size = page_size.default
    page = int(page or 1)
    page_size = int(page_size or 25)

    query = select(CompanyTemplate)

    if not include_inactive:
        query = query.where(CompanyTemplate.is_active.is_(True))

    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.where(
            or_(
                CompanyTemplate.name.ilike(term),
                CompanyTemplate.industry.ilike(term),
                CompanyTemplate.department.ilike(term),
            )
        )

    if company_type:
        if company_type not in VALID_COMPANY_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid company_type filter '{company_type}'",
            )
        query = query.where(CompanyTemplate.company_type == company_type)

    count_q = select(func.count()).select_from(query.order_by(None).subquery())
    total = db.scalar(count_q) or 0

    rows = (
        db.execute(
            query.order_by(CompanyTemplate.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    return {
        "templates": [_serialize(row) for row in rows],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        },
    }


def get_company_template(db: Session, template_id: str) -> dict:
    """Return a single template or raise 404."""
    try:
        uid = uuid.UUID(template_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Company template not found")

    row = db.execute(
        select(CompanyTemplate).where(CompanyTemplate.id == uid)
    ).scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Company template not found")

    return _serialize(row)


def create_company_template(
    db: Session, *, admin_id: str, data: CompanyTemplateCreate
) -> dict:
    """Create and persist a new company template."""
    template = CompanyTemplate(
        id=uuid.uuid4(),
        name=data.name.strip(),
        company_type=CompanyType(data.company_type),
        industry=data.industry.strip(),
        department=data.department.strip(),
        mission=data.mission.strip(),
        description=data.description.strip(),
        size=data.size.strip() if data.size else "small",
        technology_stack=data.technology_stack or [],
        is_active=True,
        created_by=admin_id,
    )
    db.add(template)
    db.flush()
    db.refresh(template)
    return _serialize(template)


def update_company_template(
    db: Session, *, template_id: str, data: CompanyTemplateUpdate
) -> dict:
    """Apply a partial update to an existing template."""
    try:
        uid = uuid.UUID(template_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Company template not found")

    row = db.execute(
        select(CompanyTemplate).where(CompanyTemplate.id == uid)
    ).scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Company template not found")

    update_data = data.model_dump(exclude_none=True)
    if "company_type" in update_data:
        update_data["company_type"] = CompanyType(update_data["company_type"])
    if "name" in update_data:
        update_data["name"] = update_data["name"].strip()
    if "industry" in update_data:
        update_data["industry"] = update_data["industry"].strip()
    if "department" in update_data:
        update_data["department"] = update_data["department"].strip()
    if "mission" in update_data:
        update_data["mission"] = update_data["mission"].strip()
    if "description" in update_data:
        update_data["description"] = update_data["description"].strip()
    if "size" in update_data:
        update_data["size"] = update_data["size"].strip()

    for key, value in update_data.items():
        setattr(row, key, value)

    db.flush()
    db.refresh(row)
    return _serialize(row)


def deactivate_company_template(db: Session, *, template_id: str) -> dict:
    """Soft-delete: set is_active=False. Never hard-deletes rows."""
    try:
        uid = uuid.UUID(template_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Company template not found")

    row = db.execute(
        select(CompanyTemplate).where(CompanyTemplate.id == uid)
    ).scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Company template not found")

    if not row.is_active:
        raise HTTPException(
            status_code=409, detail="Company template is already deactivated"
        )

    row.is_active = False
    db.flush()
    db.refresh(row)
    return _serialize(row)
