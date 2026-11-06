"""Persistence and validation for the student's current technical profile."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.intelligence import StudentProfile
from app.models.technical_domain_rating import StudentTechnicalTechnologyRating
from app.services.intelligence_service import get_or_create_profile
from app.services.technical_domain_catalog import get_technical_domain, list_technical_domains


class TechnicalProfileValidationError(ValueError):
    """The selected domain or its technology ratings do not match the catalog."""


_RATING_UNIQUE_CONSTRAINT = "uq_student_technical_rating_domain_technology"


def _rating_upsert_statement(
    student_id: str,
    technical_domain_id: str,
    ratings: Mapping[str, int],
):
    """Build a PostgreSQL upsert using the rating table's natural unique key."""
    statement = postgresql_insert(StudentTechnicalTechnologyRating).values([
        {
            "student_id": student_id,
            "technical_domain_id": technical_domain_id,
            "technology_id": technology_id,
            "self_rating": rating,
        }
        for technology_id, rating in ratings.items()
    ])
    return statement.on_conflict_do_update(
        constraint=_RATING_UNIQUE_CONSTRAINT,
        set_={
            "self_rating": statement.excluded.self_rating,
            "updated_at": func.now(),
        },
    )


def validate_technical_domain_ratings(
    technical_domain_id: str,
    technology_ratings: Sequence[Mapping[str, Any]],
) -> tuple[str, dict[str, int]]:
    """Validate a complete rating set and return its canonical domain and IDs."""
    domain = get_technical_domain(technical_domain_id)
    if domain is None:
        raise TechnicalProfileValidationError("Unknown technical domain identifier")
    if not isinstance(technology_ratings, Sequence) or isinstance(technology_ratings, (str, bytes)):
        raise TechnicalProfileValidationError("technology_ratings must be a list")

    expected = {technology.id for technology in domain.technologies}
    ratings: dict[str, int] = {}
    for item in technology_ratings:
        if not isinstance(item, Mapping):
            raise TechnicalProfileValidationError("Each technology rating must be an object")
        technology_id = item.get("technology_id")
        rating = item.get("self_rating")
        if not isinstance(technology_id, str) or not technology_id:
            raise TechnicalProfileValidationError("technology_id must be a non-empty string")
        if technology_id in ratings:
            raise TechnicalProfileValidationError(f"Duplicate technology identifier: {technology_id}")
        if technology_id not in expected:
            raise TechnicalProfileValidationError(
                f"Technology '{technology_id}' does not belong to domain '{technical_domain_id}'"
            )
        if type(rating) is not int or not 1 <= rating <= 5:
            raise TechnicalProfileValidationError("self_rating must be an integer from 1 to 5")
        ratings[technology_id] = rating

    missing = expected - ratings.keys()
    if missing:
        raise TechnicalProfileValidationError(
            "Missing technology ratings: " + ", ".join(sorted(missing))
        )
    return domain.id, ratings


def save_technical_domain_ratings(
    db: Session,
    student_id: str,
    technical_domain_id: str,
    technology_ratings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Atomically save the selected domain and all ratings for that domain.

    Existing ratings under other domains are left in place. The service never
    reads, deletes, or modifies StudentSkill rows. The caller retains ownership
    of the surrounding transaction; a nested transaction keeps this service's
    writes together if validation has already passed and persistence fails.
    """
    if not isinstance(student_id, str) or not student_id.strip():
        raise TechnicalProfileValidationError("student_id must be a non-empty string")
    canonical_domain_id, ratings = validate_technical_domain_ratings(
        technical_domain_id, technology_ratings
    )
    domain = get_technical_domain(canonical_domain_id)
    assert domain is not None  # validated above

    def _persist() -> None:
        with db.begin_nested():
            # Keep the established profile creation path. Once the row exists,
            # serialize selection updates for this student before writing the
            # domain-specific ratings.
            get_or_create_profile(db, student_id)
            profile = db.execute(
                select(StudentProfile)
                .where(StudentProfile.student_id == student_id)
                .with_for_update()
            ).scalar_one_or_none()
            if profile is None:
                raise RuntimeError("Student profile disappeared during technical profile update")
            profile.technical_domain_id = canonical_domain_id
            db.add(profile)

            db.execute(_rating_upsert_statement(student_id, canonical_domain_id, ratings))
            db.flush()

    try:
        _persist()
    except IntegrityError:
        # get_or_create_profile uses the existing select/add/flush convention.
        # If two first-time requests race on its student_id uniqueness, the
        # losing savepoint rolls back. Reuse the helper on one fresh savepoint
        # after confirming that the competing request created the profile.
        concurrent_profile = db.execute(
            select(StudentProfile)
            .where(StudentProfile.student_id == student_id)
            .with_for_update()
        ).scalar_one_or_none()
        if concurrent_profile is None:
            raise
        _persist()

    return {
        "student_id": student_id,
        "technical_domain_id": canonical_domain_id,
        "technology_ratings": [
            {"technology_id": technology.id, "self_rating": ratings[technology.id]}
            for technology in domain.technologies
        ],
    }


def get_student_technical_profile(db: Session, student_id: str) -> dict[str, Any]:
    """Return the selected domain and its catalog-enriched ratings, if selected."""
    profile = db.execute(
        select(StudentProfile).where(StudentProfile.student_id == student_id)
    ).scalar_one_or_none()
    domain_id = profile.technical_domain_id if profile is not None else None
    if domain_id is None:
        return {
            "student_id": student_id,
            "selected": False,
            "technical_domain_id": None,
            "domain": None,
            "technology_ratings": [],
        }

    domain = get_technical_domain(domain_id)
    # Profiles may contain stale/invalid values from outside the canonical
    # persistence service. Do not infer a replacement domain or expose data
    # under a different catalog entry.
    if domain is None:
        return {
            "student_id": student_id,
            "selected": True,
            "status": "unavailable",
            "technical_domain_id": domain_id,
            "domain": None,
            "technology_ratings": [],
        }

    stored_rows = db.execute(
        select(StudentTechnicalTechnologyRating).where(
            StudentTechnicalTechnologyRating.student_id == student_id,
            StudentTechnicalTechnologyRating.technical_domain_id == domain.id,
        )
    ).scalars().all()
    stored = {row.technology_id: row.self_rating for row in stored_rows}
    catalog = next(item for item in list_technical_domains()["domains"] if item["id"] == domain.id)
    technologies = []
    for technology in catalog["technologies"]:
        technologies.append({
            "technology_id": technology["id"],
            "name": technology["name"],
            "category": technology["category"],
            "description": technology["description"],
            "in_skill_catalog": technology["in_skill_catalog"],
            "self_rating": stored.get(technology["id"]),
        })
    return {
        "student_id": student_id,
        "selected": True,
        "status": "active",
        "technical_domain_id": domain.id,
        "domain": {"id": catalog["id"], "name": catalog["name"]},
        "technology_ratings": technologies,
    }
