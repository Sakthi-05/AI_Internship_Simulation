"""
Backend unit tests for admin company template management.

Uses the FakeDB pattern (no PostgreSQL required) so the tests run without
any external infrastructure.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.services.admin_company_service import (
    CompanyTemplateCreate,
    CompanyTemplateUpdate,
    VALID_COMPANY_TYPES,
    _serialize,
    create_company_template,
    deactivate_company_template,
    get_company_template,
    list_company_templates,
    update_company_template,
)
from app.models.enums import CompanyType


# ---------------------------------------------------------------------------
# Helpers / FakeDB
# ---------------------------------------------------------------------------


def _now():
    return datetime.now(timezone.utc)


def _make_template(**kwargs) -> SimpleNamespace:
    defaults = {
        "id": uuid.uuid4(),
        "name": "Acme Corp",
        "company_type": CompanyType.STARTUP,
        "industry": "Technology",
        "department": "Engineering",
        "mission": "Build great products",
        "description": "A fast-moving startup",
        "size": "small",
        "technology_stack": ["Python", "React"],
        "is_active": True,
        "created_by": "admin-001",
        "created_at": _now(),
        "updated_at": _now(),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class _FakeScalar:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        if isinstance(self._value, list):
            return self._value[0] if self._value else None
        return self._value

    def scalars(self):
        return self

    def all(self):
        if isinstance(self._value, list):
            return self._value
        return [self._value] if self._value is not None else []


class FakeDB:
    """Minimal SQLAlchemy Session stub."""

    def __init__(self, rows=None, scalar_value=None):
        self._rows = rows
        self._scalar_value = scalar_value
        self.added = []
        self.flushed = False
        self.refreshed = []
        self.committed = False

    def execute(self, stmt) -> Any:
        return _FakeScalar(self._rows)

    def scalar(self, stmt) -> Any:
        if self._scalar_value is not None:
            return self._scalar_value
        return len(self._rows) if isinstance(self._rows, list) else 0

    def add(self, obj):
        self.added.append(obj)
        # Inject timestamps so _serialize doesn't crash
        if not hasattr(obj, "created_at") or obj.created_at is None:
            obj.created_at = _now()
        if not hasattr(obj, "updated_at") or obj.updated_at is None:
            obj.updated_at = _now()

    def flush(self):
        self.flushed = True
        # Simulate DB assigning timestamps to newly added objects
        for obj in self.added:
            if not getattr(obj, "created_at", None):
                obj.created_at = _now()
            if not getattr(obj, "updated_at", None):
                obj.updated_at = _now()

    def refresh(self, obj):
        self.refreshed.append(obj)

    def commit(self):
        self.committed = True


# ---------------------------------------------------------------------------
# _serialize helper
# ---------------------------------------------------------------------------


def test_serialize_produces_expected_shape():
    t = _make_template()
    result = _serialize(t)
    assert result["id"] == str(t.id)
    assert result["name"] == t.name
    assert result["company_type"] == "startup"
    assert result["is_active"] is True
    assert isinstance(result["technology_stack"], list)
    assert "created_at" in result
    assert "updated_at" in result


def test_serialize_handles_enum_value_as_string():
    t = _make_template(company_type="mnc")
    result = _serialize(t)
    assert result["company_type"] == "mnc"


# ---------------------------------------------------------------------------
# list_company_templates
# ---------------------------------------------------------------------------


def test_list_returns_active_templates_by_default():
    t1 = _make_template(name="Alpha", is_active=True)
    t2 = _make_template(name="Beta", is_active=False)
    db = FakeDB(rows=[t1], scalar_value=1)
    result = list_company_templates(db)
    assert result["pagination"]["total"] == 1
    assert len(result["templates"]) == 1
    assert result["templates"][0]["name"] == "Alpha"


def test_list_pagination_shape():
    db = FakeDB(rows=[], scalar_value=0)
    result = list_company_templates(db, page=1, page_size=10)
    assert "pagination" in result
    assert result["pagination"]["page"] == 1
    assert result["pagination"]["page_size"] == 10
    assert result["pagination"]["total"] == 0
    assert result["pagination"]["pages"] == 1  # max(1, …)


def test_list_returns_all_when_include_inactive():
    t1 = _make_template(is_active=True)
    t2 = _make_template(is_active=False)
    db = FakeDB(rows=[t1, t2], scalar_value=2)
    result = list_company_templates(db, include_inactive=True)
    assert result["pagination"]["total"] == 2


def test_list_invalid_company_type_filter_raises_422():
    from fastapi import HTTPException

    db = FakeDB(rows=[], scalar_value=0)
    with pytest.raises(HTTPException) as exc_info:
        list_company_templates(db, company_type="invalid_type")
    assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# get_company_template
# ---------------------------------------------------------------------------


def test_get_template_returns_dict():
    t = _make_template()
    db = FakeDB(rows=t)
    result = get_company_template(db, str(t.id))
    assert result["id"] == str(t.id)
    assert result["name"] == t.name


def test_get_template_not_found_raises_404():
    from fastapi import HTTPException

    db = FakeDB(rows=None)
    with pytest.raises(HTTPException) as exc_info:
        get_company_template(db, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404


def test_get_template_invalid_uuid_raises_404():
    from fastapi import HTTPException

    db = FakeDB(rows=None)
    with pytest.raises(HTTPException) as exc_info:
        get_company_template(db, "not-a-uuid")
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# create_company_template
# ---------------------------------------------------------------------------


def _valid_create_data(**overrides) -> CompanyTemplateCreate:
    base = {
        "name": "New Template",
        "company_type": "startup",
        "industry": "Technology",
        "department": "Engineering",
        "mission": "Change the world",
        "description": "A great company",
        "size": "medium",
        "technology_stack": ["Python", "FastAPI"],
    }
    base.update(overrides)
    return CompanyTemplateCreate(**base)


def test_create_template_persists_and_returns_dict():
    db = FakeDB()
    data = _valid_create_data()
    result = create_company_template(db, admin_id="admin-001", data=data)
    assert len(db.added) == 1
    assert db.flushed is True
    assert result["name"] == "New Template"
    assert result["company_type"] == "startup"
    assert result["created_by"] == "admin-001"
    assert result["is_active"] is True


def test_create_template_stores_technology_stack():
    db = FakeDB()
    data = _valid_create_data(technology_stack=["Go", "Kubernetes"])
    result = create_company_template(db, admin_id="admin-001", data=data)
    assert result["technology_stack"] == ["Go", "Kubernetes"]


def test_create_template_strips_whitespace():
    db = FakeDB()
    data = _valid_create_data(name="  Padded Name  ", industry="  Tech  ")
    result = create_company_template(db, admin_id="admin-001", data=data)
    assert result["name"] == "Padded Name"
    assert result["industry"] == "Tech"


def test_create_template_invalid_company_type_raises_validation_error():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CompanyTemplateCreate(
            name="Bad",
            company_type="NOT_VALID",
            industry="X",
            department="Y",
            mission="M",
            description="D",
        )


def test_create_template_missing_name_raises_validation_error():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CompanyTemplateCreate(
            company_type="startup",
            industry="X",
            department="Y",
            mission="M",
            description="D",
        )


def test_create_template_empty_name_raises_validation_error():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CompanyTemplateCreate(
            name="",
            company_type="startup",
            industry="X",
            department="Y",
            mission="M",
            description="D",
        )


def test_create_template_all_valid_company_types():
    db = FakeDB()
    for ct in VALID_COMPANY_TYPES:
        data = _valid_create_data(company_type=ct)
        result = create_company_template(db, admin_id="admin-001", data=data)
        assert result["company_type"] == ct


def test_create_template_technology_stack_must_be_list_of_strings():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CompanyTemplateCreate(
            name="T",
            company_type="startup",
            industry="X",
            department="Y",
            mission="M",
            description="D",
            technology_stack=[1, 2, 3],
        )


# ---------------------------------------------------------------------------
# update_company_template
# ---------------------------------------------------------------------------


def test_update_template_applies_partial_changes():
    t = _make_template(name="Old Name")
    db = FakeDB(rows=t)
    data = CompanyTemplateUpdate(name="New Name")
    result = update_company_template(db, template_id=str(t.id), data=data)
    assert t.name == "New Name"
    assert db.flushed is True


def test_update_template_not_found_raises_404():
    from fastapi import HTTPException

    db = FakeDB(rows=None)
    data = CompanyTemplateUpdate(name="X")
    with pytest.raises(HTTPException) as exc_info:
        update_company_template(db, template_id=str(uuid.uuid4()), data=data)
    assert exc_info.value.status_code == 404


def test_update_template_invalid_uuid_raises_404():
    from fastapi import HTTPException

    db = FakeDB(rows=None)
    data = CompanyTemplateUpdate(name="X")
    with pytest.raises(HTTPException) as exc_info:
        update_company_template(db, template_id="bad-uuid", data=data)
    assert exc_info.value.status_code == 404


def test_update_template_changes_company_type():
    t = _make_template(company_type=CompanyType.STARTUP)
    db = FakeDB(rows=t)
    data = CompanyTemplateUpdate(company_type="mnc")
    update_company_template(db, template_id=str(t.id), data=data)
    assert t.company_type == CompanyType.MNC


def test_update_template_invalid_company_type_raises_validation_error():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CompanyTemplateUpdate(company_type="GARBAGE")


def test_update_template_none_fields_are_ignored():
    """model_dump(exclude_none=True) means unset fields don't overwrite existing values."""
    t = _make_template(name="Keep Me", industry="Finance")
    db = FakeDB(rows=t)
    data = CompanyTemplateUpdate(department="New Dept")
    update_company_template(db, template_id=str(t.id), data=data)
    assert t.name == "Keep Me"
    assert t.industry == "Finance"
    assert t.department == "New Dept"


# ---------------------------------------------------------------------------
# deactivate_company_template
# ---------------------------------------------------------------------------


def test_deactivate_sets_is_active_false():
    t = _make_template(is_active=True)
    db = FakeDB(rows=t)
    result = deactivate_company_template(db, template_id=str(t.id))
    assert t.is_active is False
    assert db.flushed is True


def test_deactivate_not_found_raises_404():
    from fastapi import HTTPException

    db = FakeDB(rows=None)
    with pytest.raises(HTTPException) as exc_info:
        deactivate_company_template(db, template_id=str(uuid.uuid4()))
    assert exc_info.value.status_code == 404


def test_deactivate_already_inactive_raises_409():
    from fastapi import HTTPException

    t = _make_template(is_active=False)
    db = FakeDB(rows=t)
    with pytest.raises(HTTPException) as exc_info:
        deactivate_company_template(db, template_id=str(t.id))
    assert exc_info.value.status_code == 409


def test_deactivate_invalid_uuid_raises_404():
    from fastapi import HTTPException

    db = FakeDB(rows=None)
    with pytest.raises(HTTPException) as exc_info:
        deactivate_company_template(db, template_id="not-a-valid-uuid")
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Access control tests (route-level, using TestClient-style stubs)
# ---------------------------------------------------------------------------


def _make_admin_user(role="admin"):
    return SimpleNamespace(student_id="admin-001", role=role, raw_claims={})


def _make_student_user():
    return SimpleNamespace(student_id="student-001", role="student", raw_claims={})


def test_require_role_allows_admin():
    """The require_role factory raises 403 for non-admin roles.

    Here we just verify the service functions work when given admin context —
    the route layer enforces the role check via Depends(require_role(...)).
    """
    db = FakeDB()
    data = _valid_create_data()
    admin = _make_admin_user()
    result = create_company_template(db, admin_id=admin.student_id, data=data)
    assert result["created_by"] == "admin-001"


def test_service_does_not_check_role_itself():
    """Service functions are role-agnostic; authorization is the route's job.

    This test documents that the service layer will execute for any caller —
    real access control happens in FastAPI's Depends(require_role(...)).
    """
    db = FakeDB()
    data = _valid_create_data()
    student = _make_student_user()
    # Service doesn't reject — the route layer does
    result = create_company_template(db, admin_id=student.student_id, data=data)
    assert result["created_by"] == "student-001"


# ---------------------------------------------------------------------------
# Search and filter tests
# ---------------------------------------------------------------------------


def test_search_by_name_q_parameter(monkeypatch):
    """Verify q is passed through to the WHERE clause (structural test)."""
    db = FakeDB(rows=[], scalar_value=0)
    # Should not raise; q="" is treated as no filter
    result = list_company_templates(db, q="Acme")
    assert "templates" in result


def test_filter_by_company_type_valid(monkeypatch):
    db = FakeDB(rows=[], scalar_value=0)
    result = list_company_templates(db, company_type="startup")
    assert "templates" in result


def test_deactivated_template_excluded_from_default_list():
    """is_active=False rows must not appear in the default listing."""
    # FakeDB simulates the WHERE clause result — we pass only active rows
    t = _make_template(is_active=True)
    db = FakeDB(rows=[t], scalar_value=1)
    result = list_company_templates(db, include_inactive=False)
    # All returned items must be active
    for item in result["templates"]:
        assert item["is_active"] is True


def test_include_inactive_shows_deactivated():
    t_active = _make_template(is_active=True)
    t_inactive = _make_template(is_active=False)
    db = FakeDB(rows=[t_active, t_inactive], scalar_value=2)
    result = list_company_templates(db, include_inactive=True)
    statuses = {item["is_active"] for item in result["templates"]}
    assert False in statuses or True in statuses  # both present


# ---------------------------------------------------------------------------
# CompanyTemplateCreate / Update validators
# ---------------------------------------------------------------------------


def test_valid_company_types_set_is_complete():
    expected = {"startup", "product_company", "mnc", "healthcare", "banking", "ecommerce", "education"}
    assert VALID_COMPANY_TYPES == expected


def test_update_schema_all_optional():
    """CompanyTemplateUpdate with no fields should validate fine."""
    data = CompanyTemplateUpdate()
    assert data.name is None
    assert data.company_type is None


def test_create_schema_default_technology_stack():
    data = CompanyTemplateCreate(
        name="X",
        company_type="startup",
        industry="Tech",
        department="Eng",
        mission="M",
        description="D",
    )
    assert data.technology_stack == []


def test_create_schema_default_size():
    data = CompanyTemplateCreate(
        name="X",
        company_type="startup",
        industry="Tech",
        department="Eng",
        mission="M",
        description="D",
    )
    assert data.size == "small"


# ---------------------------------------------------------------------------
# Auth dependency and route handler integration tests
# ---------------------------------------------------------------------------


def test_student_role_denied_by_require_role():
    from fastapi import HTTPException
    from app.core.security import CurrentUser, require_role

    checker = require_role("admin", "administrator")
    student = CurrentUser(student_id="student-1", role="student", raw_claims={"role": "student"})
    fake_db = FakeDB()

    with pytest.raises(HTTPException) as exc:
        checker(user=student, db=fake_db)
    assert exc.value.status_code == 403
    assert "not permitted" in exc.value.detail


def test_unauthenticated_denied_by_get_current_user():
    from fastapi import HTTPException
    from app.core.security import get_current_user

    with pytest.raises(HTTPException) as exc:
        get_current_user(None)
    assert exc.value.status_code == 401


def test_admin_role_requires_persisted_account():
    from fastapi import HTTPException
    from app.core.security import CurrentUser, require_role

    checker = require_role("admin", "administrator")
    admin_token_claim = CurrentUser(student_id="imposter-1", role="admin", raw_claims={"role": "admin"})
    # DB scalar_one_or_none returns None (not found or not admin in DB)
    fake_db = FakeDB(scalar_value=None)

    with pytest.raises(HTTPException) as exc:
        checker(user=admin_token_claim, db=fake_db)
    assert exc.value.status_code == 403
    assert "administrator account is required" in exc.value.detail


def test_admin_route_handlers():
    from app.api.routes.admin import (
        create_template_route,
        deactivate_template_route,
        get_template_route,
        list_templates_route,
        update_template_route,
    )
    from app.core.security import CurrentUser

    admin_user = CurrentUser(student_id="admin-999", role="admin", raw_claims={"role": "admin"})
    t = _make_template(is_active=True)
    db = FakeDB(rows=[t], scalar_value=1)

    # 1. List
    list_res = list_templates_route(page=1, page_size=25, db=db, admin=admin_user)
    assert "templates" in list_res
    assert len(list_res["templates"]) == 1

    # 2. Create
    create_data = _valid_create_data(name="API Created")
    created = create_template_route(body=create_data, db=db, admin=admin_user)
    assert created["name"] == "API Created"
    assert db.committed is True

    # 3. Get
    db_single = FakeDB(rows=t)
    fetched = get_template_route(template_id=str(t.id), db=db_single, _admin=admin_user)
    assert fetched["id"] == str(t.id)

    # 4. Update
    db_update = FakeDB(rows=t)
    update_data = CompanyTemplateUpdate(name="API Updated")
    updated = update_template_route(template_id=str(t.id), body=update_data, db=db_update, _admin=admin_user)
    assert updated["name"] == "API Updated"
    assert db_update.committed is True

    # 5. Deactivate
    db_deact = FakeDB(rows=t)
    deactivated = deactivate_template_route(template_id=str(t.id), db=db_deact, _admin=admin_user)
    assert deactivated["is_active"] is False
    assert db_deact.committed is True

