"""Module 1 student profile, skills, and simulation recommendation."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser, get_current_user
from app.services.assessment_service import list_skills, list_student_skills, replace_student_skills
from app.services.intelligence_service import get_or_create_profile, serialize_profile
from app.services.recommendation import build_simulation_recommendation
from app.services.technical_domain_catalog import list_technical_domains
from app.services.technical_profile_service import (
    TechnicalProfileValidationError,
    get_student_technical_profile,
    save_technical_domain_ratings,
)

router = APIRouter(tags=["Module 1 — Students"])


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    domain: Optional[str] = None
    role: Optional[str] = None
    college: Optional[str] = None
    degree: Optional[str] = None
    year_of_study: Optional[int] = Field(default=None, ge=1, le=8)
    career_goal: Optional[str] = None


class SkillRating(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    self_rating: StrictInt = Field(default=3, ge=1, le=5)


class SkillUpdate(BaseModel):
    skills: List[SkillRating] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_skills(self):
        names = [item.name.strip().casefold() for item in self.skills]
        if len(names) != len(set(names)):
            raise ValueError("Each skill may be included only once")
        return self


class TechnicalTechnologyRating(BaseModel):
    model_config = ConfigDict(extra="forbid")

    technology_id: str = Field(..., min_length=1)
    self_rating: StrictInt = Field(..., ge=1, le=5)


class TechnicalProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    technical_domain_id: str = Field(..., min_length=1)
    technology_ratings: List[TechnicalTechnologyRating]


def _profile_view(db: Session, student_id: str) -> dict:
    profile = serialize_profile(get_or_create_profile(db, student_id))
    skills = list_student_skills(db, student_id)
    profile["skill_ratings"] = skills
    profile["profile_complete"] = bool(profile.get("full_name") and profile.get("role") and profile.get("career_goal"))
    profile["assessed"] = bool(profile.get("last_assessed_at"))
    return profile


@router.get("/api/skills")
def skills(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    del user
    rows = list_skills(db)
    db.commit()
    return {"skills": rows}


@router.get("/api/technical-domains")
def technical_domains(user: CurrentUser = Depends(get_current_user)):
    """Return the read-only canonical technical-domain/technology catalog."""
    del user
    return list_technical_domains()


@router.get("/api/students/me/technical-profile")
def my_technical_profile(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Return only the authenticated student's current technical profile."""
    return get_student_technical_profile(db, user.student_id)


@router.put("/api/students/me/technical-profile")
def update_my_technical_profile(
    payload: TechnicalProfileUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Save a complete self-reported rating set for one canonical domain."""
    try:
        save_technical_domain_ratings(
            db,
            user.student_id,
            payload.technical_domain_id,
            [item.model_dump() for item in payload.technology_ratings],
        )
        response = get_student_technical_profile(db, user.student_id)
        # Student routes own the outer transaction; the persistence service
        # deliberately uses only a savepoint and never commits it. Prepare the
        # response before commit so a read/serialization failure can roll back.
        db.commit()
    except TechnicalProfileValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return response


@router.get("/api/students/me")
def my_profile(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    profile = _profile_view(db, user.student_id)
    db.commit()
    return profile


@router.put("/api/students/me")
def update_my_profile(payload: ProfileUpdate, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    profile = get_or_create_profile(db, user.student_id)
    for field in ("full_name", "domain", "role", "college", "degree", "year_of_study", "career_goal"):
        value = getattr(payload, field)
        if value is not None:
            setattr(profile, field, value)
    if profile.last_assessed_at:
        profile.recommended_parameters = build_simulation_recommendation(profile)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    view = _profile_view(db, user.student_id)
    db.commit()
    return view


@router.put("/api/students/me/skills")
def update_my_skills(payload: SkillUpdate, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if not payload.skills:
        raise HTTPException(400, "Select at least one skill")
    replace_student_skills(db, user.student_id, [item.model_dump() for item in payload.skills])
    profile = get_or_create_profile(db, user.student_id)
    if profile.last_assessed_at:
        profile.recommended_parameters = build_simulation_recommendation(profile)
        db.add(profile)
    db.commit()
    return {"skills": list_student_skills(db, user.student_id)}


@router.get("/api/students/me/recommendation")
def my_recommendation(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    profile = get_or_create_profile(db, user.student_id)
    if not profile.last_assessed_at:
        raise HTTPException(409, "Complete the baseline assessment before requesting a simulation")
    recommendation = profile.recommended_parameters or build_simulation_recommendation(profile)
    return {"student_id": user.student_id, "recommendation": recommendation}
