"""Focused route-handler and request-schema tests without a database."""
import pytest
from fastapi import HTTPException

from app.api.routes import students
from app.core.security import CurrentUser, get_current_user
from app.services import technical_profile_service
from app.services.technical_domain_catalog import get_technical_domain


class _RouteSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _user(student_id="student-a"):
    return CurrentUser(student_id=student_id, role="student", raw_claims={})


def _ratings(domain_id="software_development", rating=3):
    domain = get_technical_domain(domain_id)
    return [{"technology_id": item.id, "self_rating": rating} for item in domain.technologies]


def _payload(domain_id="software_development", ratings=None):
    return students.TechnicalProfileUpdate.model_validate({
        "technical_domain_id": domain_id,
        "technology_ratings": _ratings(domain_id) if ratings is None else ratings,
    })


def test_auth_dependency_rejects_unauthenticated_access():
    with pytest.raises(HTTPException) as exc:
        get_current_user(None)
    assert exc.value.status_code == 401


def test_get_returns_clear_unselected_profile(monkeypatch):
    monkeypatch.setattr(students, "get_student_technical_profile", lambda _db, sid: {
        "student_id": sid, "selected": False, "technical_domain_id": None,
        "domain": None, "technology_ratings": [],
    })
    result = students.my_technical_profile(_RouteSession(), _user())
    assert result["selected"] is False
    assert result["technical_domain_id"] is None
    assert result["technology_ratings"] == []


class _ReadResult:
    def __init__(self, row=None, rows=()):
        self.row = row
        self.rows = list(rows)

    def scalar_one_or_none(self):
        return self.row

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _TechnicalProfileReadSession:
    def __init__(self, profile, ratings=()):
        self.results = [_ReadResult(row=profile)]
        if profile is not None and profile.technical_domain_id in {
            "software_development", "debugging_code_review", "devops_cloud_operations",
            "cybersecurity_incident_response", "data_analysis_qa_testing",
        }:
            self.results.append(_ReadResult(rows=ratings))

    def execute(self, _statement):
        return self.results.pop(0)


def test_read_service_returns_unselected_when_domain_is_null():
    response = technical_profile_service.get_student_technical_profile(
        _TechnicalProfileReadSession(None), "student-a"
    )
    assert response["selected"] is False
    assert response["technical_domain_id"] is None
    assert response["technology_ratings"] == []


def test_read_service_returns_active_catalog_data_and_null_for_missing_rating():
    profile = type("Profile", (), {"technical_domain_id": "software_development"})()
    stored = type("Rating", (), {"technology_id": "python", "self_rating": 4})()
    response = technical_profile_service.get_student_technical_profile(
        _TechnicalProfileReadSession(profile, [stored]), "student-a"
    )
    assert response["selected"] is True
    assert response["status"] == "active"
    assert response["technical_domain_id"] == "software_development"
    by_id = {item["technology_id"]: item for item in response["technology_ratings"]}
    assert by_id["python"]["self_rating"] == 4
    assert by_id["java"]["self_rating"] is None


def test_read_service_preserves_stale_domain_as_unavailable():
    profile = type("Profile", (), {"technical_domain_id": "retired_domain"})()
    response = technical_profile_service.get_student_technical_profile(
        _TechnicalProfileReadSession(profile), "student-a"
    )
    assert response["selected"] is True
    assert response["status"] == "unavailable"
    assert response["technical_domain_id"] == "retired_domain"
    assert response["domain"] is None
    assert response["technology_ratings"] == []


def test_get_returns_profile_catalog_data_for_current_student(monkeypatch):
    requested = []
    monkeypatch.setattr(students, "get_student_technical_profile", lambda _db, sid: requested.append(sid) or {
        "student_id": sid, "selected": True,
        "technical_domain_id": "software_development",
        "domain": {"id": "software_development", "name": "Software Development"},
        "technology_ratings": [{"technology_id": "python", "name": "Python", "self_rating": 4}],
    })
    result = students.my_technical_profile(_RouteSession(), _user("authenticated-student"))
    assert requested == ["authenticated-student"]
    assert result["technology_ratings"][0]["self_rating"] == 4


def test_put_uses_authenticated_identity_commits_and_returns_profile(monkeypatch):
    received = {}

    def save(_db, student_id, domain_id, ratings):
        received.update(student_id=student_id, domain_id=domain_id, ratings=ratings)
        return {"student_id": student_id, "technical_domain_id": domain_id}

    monkeypatch.setattr(students, "save_technical_domain_ratings", save)
    db = _RouteSession()
    prepared = []

    def get_profile(read_db, sid):
        assert read_db.commits == 0
        prepared.append(sid)
        return {
            "student_id": sid, "selected": True, "status": "active",
            "technical_domain_id": "software_development",
            "domain": {"id": "software_development", "name": "Software Development"},
            "technology_ratings": [{"technology_id": "python", "name": "Python", "self_rating": 3}],
        }

    monkeypatch.setattr(students, "get_student_technical_profile", get_profile)
    result = students.update_my_technical_profile(
        _payload(), db, _user("student-from-auth")
    )
    assert received["student_id"] == "student-from-auth"
    assert received["domain_id"] == "software_development"
    assert len(received["ratings"]) == len(_ratings())
    assert db.commits == 1
    assert prepared == ["student-from-auth"]
    assert result["selected"] is True


def test_response_preparation_failure_rolls_back_before_commit(monkeypatch):
    monkeypatch.setattr(students, "save_technical_domain_ratings", lambda *_: {})

    def fail_read(*_args):
        raise RuntimeError("response construction failed")

    monkeypatch.setattr(students, "get_student_technical_profile", fail_read)
    db = _RouteSession()
    with pytest.raises(RuntimeError, match="response construction failed"):
        students.update_my_technical_profile(_payload(), db, _user())
    assert db.commits == 0
    assert db.rollbacks == 1


def test_commit_failure_rolls_back_after_response_prepared(monkeypatch):
    monkeypatch.setattr(students, "save_technical_domain_ratings", lambda *_: {})
    prepared = []
    monkeypatch.setattr(students, "get_student_technical_profile", lambda _db, sid: prepared.append(sid) or {
        "student_id": sid, "selected": True, "status": "active",
        "technical_domain_id": "software_development", "domain": None, "technology_ratings": [],
    })

    class _FailingCommitSession(_RouteSession):
        def commit(self):
            self.commits += 1
            raise RuntimeError("commit failed")

    db = _FailingCommitSession()
    with pytest.raises(RuntimeError, match="commit failed"):
        students.update_my_technical_profile(_payload(), db, _user())
    assert prepared == ["student-a"]
    assert db.commits == 1
    assert db.rollbacks == 1


@pytest.mark.parametrize("bad_rating", [True, False, "3", 0, 6, 2.5])
def test_request_schema_rejects_invalid_rating_types_and_ranges(bad_rating):
    ratings = _ratings()
    ratings[0]["self_rating"] = bad_rating
    with pytest.raises(ValueError):
        _payload(ratings=ratings)


def test_request_schema_rejects_client_student_id_and_unknown_extra_fields():
    request = {"technical_domain_id": "software_development", "technology_ratings": _ratings(), "student_id": "victim"}
    with pytest.raises(ValueError):
        students.TechnicalProfileUpdate.model_validate(request)


@pytest.mark.parametrize("domain_id,ratings", [
    ("not-a-domain", []),
    ("software_development", _ratings()[:-1]),
    ("software_development", _ratings() + _ratings()[:1]),
    ("software_development", [{**item, "technology_id": "docker"} if index == 0 else item
                               for index, item in enumerate(_ratings())]),
])
def test_put_maps_catalog_validation_errors_to_422_and_rolls_back(monkeypatch, domain_id, ratings):
    # Keep the real service validator; only replace its database write boundary.
    monkeypatch.setattr(technical_profile_service, "get_or_create_profile", lambda *_: None)
    db = _RouteSession()
    with pytest.raises(HTTPException) as exc:
        students.update_my_technical_profile(_payload(domain_id, ratings), db, _user())
    assert exc.value.status_code == 422
    assert db.commits == 0
    assert db.rollbacks == 1


def test_update_failure_rolls_back_outer_transaction(monkeypatch):
    def fail(*_args):
        raise RuntimeError("write failed")

    monkeypatch.setattr(students, "save_technical_domain_ratings", fail)
    db = _RouteSession()
    with pytest.raises(RuntimeError, match="write failed"):
        students.update_my_technical_profile(_payload(), db, _user())
    assert db.commits == 0
    assert db.rollbacks == 1
