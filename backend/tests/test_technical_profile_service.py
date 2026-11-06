"""Database-independent validation and persistence-service tests."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Insert
from sqlalchemy.sql.elements import BooleanClauseList

from app.models.assessment import StudentSkill
from app.models.intelligence import StudentProfile
from app.models.technical_domain_rating import StudentTechnicalTechnologyRating
from app.services import technical_profile_service as service
from app.services.technical_domain_catalog import get_technical_domain


def _ratings(domain_id: str, value: int = 3):
    domain = get_technical_domain(domain_id)
    return [{"technology_id": tech.id, "self_rating": value} for tech in domain.technologies]


def test_valid_domain_and_all_technology_identifiers_validate():
    domain_id, values = service.validate_technical_domain_ratings(
        "software_development", _ratings("software_development", 4)
    )
    assert domain_id == "software_development"
    assert set(values) == {item["technology_id"] for item in _ratings("software_development")}
    assert set(values.values()) == {4}


def test_invalid_domain_identifier_is_rejected():
    with pytest.raises(service.TechnicalProfileValidationError, match="Unknown technical domain"):
        service.validate_technical_domain_ratings("healthcare", [])


def test_technology_from_another_domain_is_rejected():
    values = _ratings("software_development")
    values[0] = {"technology_id": "docker", "self_rating": 3}
    with pytest.raises(service.TechnicalProfileValidationError, match="does not belong"):
        service.validate_technical_domain_ratings("software_development", values)


def test_missing_and_duplicate_technology_ids_are_rejected():
    complete = _ratings("software_development")
    with pytest.raises(service.TechnicalProfileValidationError, match="Missing technology"):
        service.validate_technical_domain_ratings("software_development", complete[:-1])
    with pytest.raises(service.TechnicalProfileValidationError, match="Duplicate technology"):
        service.validate_technical_domain_ratings("software_development", [*complete, complete[0]])


@pytest.mark.parametrize("rating", [0, 6, -1, 1.0, True, False, "3", "5"])
def test_out_of_range_boolean_and_string_ratings_are_rejected(rating):
    values = _ratings("software_development")
    values[0]["self_rating"] = rating
    with pytest.raises(service.TechnicalProfileValidationError, match="integer from 1 to 5"):
        service.validate_technical_domain_ratings("software_development", values)


class _ScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


class _FakeSession:
    def __init__(self, ratings=(), student_skills=(), *, fail_flush=False, fail_upsert_after_first=False):
        self.ratings = list(ratings)
        self.student_skills = list(student_skills)
        self.added = []
        self.fail_flush = fail_flush
        self.fail_upsert_after_first = fail_upsert_after_first
        self.savepoint_rollbacks = 0
        self.profile_lock_count = 0
        self.upsert_count = 0

    def execute(self, statement):
        if isinstance(statement, Insert):
            sql = str(statement.compile(dialect=postgresql.dialect()))
            assert "ON CONFLICT ON CONSTRAINT uq_student_technical_rating_domain_technology DO UPDATE" in sql
            self.upsert_count += 1
            for index, column_values in enumerate(statement._multi_values[0]):
                values = {column.key: value for column, value in column_values.items()}
                existing = next((row for row in self.ratings if (
                    row.student_id == values["student_id"]
                    and row.technical_domain_id == values["technical_domain_id"]
                    and row.technology_id == values["technology_id"]
                )), None)
                if existing is None:
                    self.ratings.append(StudentTechnicalTechnologyRating(**values))
                else:
                    existing.self_rating = values["self_rating"]
                if self.fail_upsert_after_first and index == 0:
                    raise RuntimeError("simulated partial upsert failure")
            return _ScalarResult([])

        entity = statement.column_descriptions[0]["entity"]
        where = statement.whereclause
        clauses = list(where.clauses) if isinstance(where, BooleanClauseList) else [where]
        filters = {clause.left.key: clause.right.value for clause in clauses}
        if entity is StudentProfile:
            self.profile_lock_count += int(statement._for_update_arg is not None)
            return _ScalarResult([_profile] if _profile.student_id == filters["student_id"] else [])
        rows = [
            row for row in self.ratings
            if all(getattr(row, key) == value for key, value in filters.items())
        ]
        return _ScalarResult(rows)

    def add(self, row):
        self.added.append(row)
        if isinstance(row, StudentTechnicalTechnologyRating) and row not in self.ratings:
            self.ratings.append(row)

    def flush(self):
        if self.fail_flush:
            raise RuntimeError("simulated persistence failure")

    @contextmanager
    def begin_nested(self):
        previous_domain = _profile.technical_domain_id
        previous_ratings = [(row, row.self_rating) for row in self.ratings]
        previous_rows = list(self.ratings)
        previous_added = list(self.added)
        try:
            yield
        except Exception:
            _profile.technical_domain_id = previous_domain
            for row, rating in previous_ratings:
                row.self_rating = rating
            self.ratings[:] = previous_rows
            self.added[:] = previous_added
            self.savepoint_rollbacks += 1
            raise


_profile = SimpleNamespace(student_id="student-1", technical_domain_id=None, domain="startup", role="Backend Developer")


def _save(db, monkeypatch, domain_id, values):
    monkeypatch.setattr(service, "get_or_create_profile", lambda _db, _student_id: _profile)
    return service.save_technical_domain_ratings(db, "student-1", domain_id, values)


def test_configuration_only_technology_is_persisted_without_a_skill_row(monkeypatch):
    db = _FakeSession()
    _save(db, monkeypatch, "devops_cloud_operations", _ratings("devops_cloud_operations"))
    assert any(row.technology_id == "kubernetes" for row in db.ratings)
    assert not any(isinstance(row, StudentSkill) for row in db.added)


def test_updates_within_domain_preserve_other_domain_ratings_and_student_skills(monkeypatch):
    prior = StudentTechnicalTechnologyRating(
        student_id="student-1", technical_domain_id="software_development",
        technology_id="python", self_rating=1,
    )
    other_domain = StudentTechnicalTechnologyRating(
        student_id="student-1", technical_domain_id="devops_cloud_operations",
        technology_id="docker", self_rating=2,
    )
    skill_row = StudentSkill(student_id="student-1", skill_id="00000000-0000-0000-0000-000000000001", self_rating=5)
    db = _FakeSession([prior, other_domain], [skill_row])
    _save(db, monkeypatch, "software_development", _ratings("software_development", 4))
    assert prior.self_rating == 4
    assert other_domain.self_rating == 2
    assert db.ratings.count(prior) == 1
    assert db.student_skills == [skill_row]
    assert not any(isinstance(row, StudentSkill) for row in db.added)


def test_domain_switch_keeps_prior_domain_ratings(monkeypatch):
    previous = StudentTechnicalTechnologyRating(
        student_id="student-1", technical_domain_id="software_development",
        technology_id="python", self_rating=2,
    )
    db = _FakeSession([previous])
    _save(db, monkeypatch, "data_analysis_qa_testing", _ratings("data_analysis_qa_testing", 5))
    assert _profile.technical_domain_id == "data_analysis_qa_testing"
    assert previous.self_rating == 2
    assert any(
        row.technical_domain_id == "data_analysis_qa_testing" and row.technology_id == "pandas"
        for row in db.ratings
    )
    assert (_profile.domain, _profile.role) == ("startup", "Backend Developer")
    assert db.profile_lock_count == 1


def test_repeated_or_concurrent_style_rating_insert_uses_unique_constraint_upsert(monkeypatch):
    duplicate = StudentTechnicalTechnologyRating(
        student_id="student-1", technical_domain_id="software_development",
        technology_id="python", self_rating=1,
    )
    db = _FakeSession([duplicate])
    _save(db, monkeypatch, "software_development", _ratings("software_development", 5))
    assert db.upsert_count == 1
    assert duplicate.self_rating == 5
    assert db.ratings.count(duplicate) == 1


def test_failed_partial_upsert_rolls_back_all_domain_writes(monkeypatch):
    _profile.technical_domain_id = "software_development"
    db = _FakeSession(fail_upsert_after_first=True)
    with pytest.raises(RuntimeError, match="simulated partial upsert failure"):
        _save(db, monkeypatch, "devops_cloud_operations", _ratings("devops_cloud_operations"))
    assert db.savepoint_rollbacks == 1
    assert db.ratings == []
    assert _profile.technical_domain_id == "software_development"


def test_persistence_failure_rolls_back_selection_and_new_ratings(monkeypatch):
    _profile.technical_domain_id = "software_development"
    db = _FakeSession(fail_flush=True)
    with pytest.raises(RuntimeError, match="simulated persistence failure"):
        _save(db, monkeypatch, "devops_cloud_operations", _ratings("devops_cloud_operations"))
    assert db.savepoint_rollbacks == 1
    assert _profile.technical_domain_id == "software_development"
    assert db.ratings == []


def test_database_model_has_range_and_domain_technology_uniqueness_constraints():
    constraints = StudentTechnicalTechnologyRating.__table__.constraints
    assert any(
        constraint.name == "uq_student_technical_rating_domain_technology"
        and {column.name for column in constraint.columns}
        == {"student_id", "technical_domain_id", "technology_id"}
        for constraint in constraints
    )
    assert any(
        constraint.name == "ck_student_technical_rating_self_rating_range"
        for constraint in constraints
    )
    assert StudentProfile.__table__.c.technical_domain_id.nullable is True
