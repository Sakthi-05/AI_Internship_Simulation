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


def build_simulation_recommendation(profile) -> Dict[str, Any]:
    from app.services.intelligence_service import COMPETENCIES, DEFAULT_COMPETENCIES

    scores = profile.baseline_competencies or profile.current_competencies or dict(DEFAULT_COMPETENCIES)
    numeric = [float(scores.get(key, 60)) for key in COMPETENCIES]
    average = statistics.mean(numeric) if numeric else 60.0
    role = profile.role or "Backend Developer"
    stack = list(ROLE_STACKS.get(role.strip().lower(), ["Python", "FastAPI", "PostgreSQL"]))
    declared = [skill for skill in (profile.skills or []) if skill.lower() not in NON_TECHNICAL]
    if declared:
        stack = declared[:4]
    ranked = sorted(((key, float(scores.get(key, 60))) for key in COMPETENCIES), key=lambda item: item[1])
    return {
        "role": role,
        "technology_stack": stack,
        "difficulty": profile.recommended_difficulty or _difficulty(average),
        "company_type": _company_type(profile.domain),
        "focus_competencies": [key for key, _score in ranked[:2]],
        "career_goal": profile.career_goal,
        "average_score": round(average, 2),
    }
