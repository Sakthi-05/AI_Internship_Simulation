"""Module 1 generic skill request validation without a database."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes.students import SkillUpdate
from app.services import assessment_service


@pytest.mark.parametrize("rating", [True, False, "3", 3.0, 0, 6])
def test_skill_request_rejects_coerced_or_out_of_range_ratings(rating):
    with pytest.raises(ValidationError):
        SkillUpdate.model_validate({"skills": [{"name": "Python", "self_rating": rating}]})


def test_skill_request_rejects_duplicate_names_case_insensitively():
    with pytest.raises(ValidationError, match="included only once"):
        SkillUpdate.model_validate({"skills": [
            {"name": "Python", "self_rating": 3},
            {"name": " python ", "self_rating": 4},
        ]})


class _SkillRows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _NoDeleteSession:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _statement):
        return _SkillRows(self.rows)

    def query(self, *_):
        raise AssertionError("invalid skill input must be rejected before existing rows are deleted")


def test_service_rejects_case_insensitive_duplicate_before_replacing_existing_skills(monkeypatch):
    skill = SimpleNamespace(id=uuid4(), name="Python")
    monkeypatch.setattr(assessment_service, "ensure_skill_catalog", lambda _db: None)
    db = _NoDeleteSession([skill])
    with pytest.raises(HTTPException) as exc:
        assessment_service.replace_student_skills(db, "student-1", [
            {"name": "Python", "self_rating": 3},
            {"name": "python", "self_rating": 4},
        ])
    assert exc.value.status_code == 400
