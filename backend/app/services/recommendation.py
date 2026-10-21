"""Turn a competency profile into the parameters Module 2 consumes."""
from __future__ import annotations

import statistics
from typing import Any, Dict, List

from app.models.enums import CompanyType

ROLE_STACKS = {
    "backend developer": ["Python", "FastAPI", "PostgreSQL"],
    "frontend developer": ["JavaScript", "React", "Git"],
    "full stack developer": ["JavaScript", "React", "Python", "PostgreSQL"],
    "ai engineer": ["Python", "SQL", "Git"],
    "data engineer": ["Python", "SQL", "PostgreSQL"],
    "cybersecurity": ["Python", "Git", "Testing"],
    "devops engineer": ["Docker", "Git", "Python"],
    "mobile developer": ["JavaScript", "Git", "Testing"],
}

BASELINE_STRENGTH_THRESHOLD = 80.0
BASELINE_FOCUS_THRESHOLD = 70.0

NON_TECHNICAL = {"communication", "professionalism"}


def _difficulty(average: float) -> str:
    if average >= 88:
        return "expert"
    if average >= 78:
        return "advanced"
    if average >= 58:
        return "intermediate"
    return "beginner"


def _company_type(domain: str | None) -> str:
    value = (domain or "").strip().lower()
    allowed = {item.value for item in CompanyType}
    if value in allowed:
        return value
    hints = {
        "health": "healthcare",
        "bank": "banking",
        "finance": "banking",
        "commerce": "ecommerce",
        "retail": "ecommerce",
        "edu": "education",
        "college": "education",
        "enterprise": "mnc",
        "product": "product_company",
    }
    for needle, company in hints.items():
        if needle in value:
            return company
    return "startup"


def build_simulation_recommendation(profile, *, overall_score: float | None = None) -> Dict[str, Any]:
    from app.services.intelligence_service import COMPETENCIES, DEFAULT_COMPETENCIES

    current = profile.current_competencies or {}
    baseline = profile.baseline_competencies or {}
    # Later simulations follow the latest demonstrated scores. The baseline
    # remains the source until those scores exist.
    scores = current if any(key in current for key in COMPETENCIES) else baseline or {}
    numeric = [float(scores[key]) for key in COMPETENCIES if key in scores and scores[key] is not None]
    existing_assessment_score = (profile.recommended_parameters or {}).get("assessment_overall_score")
    average = (
        float(overall_score) if overall_score is not None
        else float(existing_assessment_score) if existing_assessment_score is not None
        else statistics.fmean(numeric) if numeric
        else statistics.fmean(DEFAULT_COMPETENCIES.values())
    )
    role = profile.role or "Backend Developer"
    stack = list(ROLE_STACKS.get(role.strip().lower(), ["Python", "FastAPI", "PostgreSQL"]))
    declared = [skill for skill in (profile.skills or []) if skill.lower() not in NON_TECHNICAL]
    if declared:
        stack = declared[:4]
    ranked = sorted(
        ((key, float(scores[key])) for key in COMPETENCIES if key in scores and scores[key] is not None),
        key=lambda item: (item[1], item[0]),
    )
    return {
        "role": role,
        "technology_stack": stack,
        "difficulty": profile.recommended_difficulty or _difficulty(average),
        "company_type": _company_type(profile.domain),
        # Focus labels require an observed score below the same baseline gap threshold.
        "focus_competencies": [key for key, score in ranked if score < BASELINE_FOCUS_THRESHOLD][:2],
        "career_goal": profile.career_goal,
        "average_score": average,
        "assessment_overall_score": average,
    }
