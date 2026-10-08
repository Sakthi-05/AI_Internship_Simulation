"""
FEATURE 1: AI Company Generator.

Generates a realistic Company + its Manager, persists both, and returns
the ORM objects (caller commits).
"""
import random
import logging
import time
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.enums import CompanyType, ManagerPersonality
from app.models.manager import Manager
from app.services.ai.ai_service import AIService

logger = logging.getLogger(__name__)

_NAME_PARTS = {
    CompanyType.STARTUP: (["Nimbus", "Sparkline", "Loopin", "Verve", "Orbitly", "Fable"], ["Labs", "AI", "Technologies", ""]),
    CompanyType.PRODUCT_COMPANY: (["Northbeam", "Corestack", "Vantage", "Trailhead", "Meridian"], ["Software", "Systems", "Products"]),
    CompanyType.MNC: (["Global", "Continental", "Unified", "Apex", "Meridian"], ["Industries", "Group", "Holdings", "Corporation"]),
    CompanyType.HEALTHCARE: (["Vitalis", "CarePoint", "MedNova", "Wellspring", "Pulse"], ["Health", "Healthcare", "Medical Systems"]),
    CompanyType.BANKING: (["Sterling", "Ledger", "Trustline", "Ironclad", "Meridian"], ["Bank", "Financial", "Capital"]),
    CompanyType.ECOMMERCE: (["Cartify", "ShopNest", "Marketh", "Bazario", "Storefront"], ["Commerce", "Marketplace", ""]),
    CompanyType.EDUCATION: (["Lernova", "Eduspark", "Brightpath", "Scholarly", "Classwise"], ["Education", "Learning", "Academy"]),
}

_INDUSTRIES = {
    CompanyType.STARTUP: "Technology / SaaS",
    CompanyType.PRODUCT_COMPANY: "Enterprise Software Products",
    CompanyType.MNC: "Diversified Multinational Conglomerate",
    CompanyType.HEALTHCARE: "Healthcare Technology",
    CompanyType.BANKING: "Financial Services & Banking",
    CompanyType.ECOMMERCE: "E-Commerce & Retail Technology",
    CompanyType.EDUCATION: "Education Technology",
}

_DEPARTMENTS_BY_ROLE_HINT = [
    "Engineering", "Product Engineering", "Platform Engineering",
    "Data & AI", "Cloud Infrastructure", "Digital Products",
]

_MANAGER_FIRST = ["Aditi", "Marcus", "Priya", "Daniel", "Elena", "Rohan", "Sophia", "James", "Naomi", "Victor"]
_MANAGER_LAST = ["Sharma", "Chen", "Okafor", "Rodriguez", "Novak", "Iyer", "Larsson", "Whitfield", "Kapoor", "Silva"]
_MANAGER_TITLES = ["Engineering Manager", "Senior Project Manager", "Head of Product Engineering", "Delivery Lead"]

_SIZE_BY_TYPE = {
    CompanyType.STARTUP: "small",
    CompanyType.PRODUCT_COMPANY: "medium",
    CompanyType.MNC: "enterprise",
    CompanyType.HEALTHCARE: "medium",
    CompanyType.BANKING: "enterprise",
    CompanyType.ECOMMERCE: "medium",
    CompanyType.EDUCATION: "small",
}


def _generate_name(company_type: CompanyType, rng: random.Random) -> str:
    prefixes, suffixes = _NAME_PARTS[company_type]
    prefix = rng.choice(prefixes)
    suffix = rng.choice(suffixes)
    return f"{prefix} {suffix}".strip()


async def generate_company(
    db: Session,
    student_id: str,
    company_type: CompanyType,
    role: str,
    ai_service: AIService,
    technology_stack: Optional[List[str]] = None,
) -> Tuple[Company, Manager]:
    rng = random.Random(f"{student_id}|{company_type.value}|{role}")
    technology_stack = list(technology_stack or [])

    name = _generate_name(company_type, rng)
    industry = _INDUSTRIES[company_type]
    department = rng.choice(_DEPARTMENTS_BY_ROLE_HINT)

    ai_started_at = time.monotonic()
    logger.info("Generation AI stage started stage=company_mission")
    mission = await ai_service.generate_text(
        prompt=(
            f"Write a one-sentence company mission statement for a {company_type.value} "
            f"company named {name} in the {industry} industry."
        ),
        system_prompt="You are a branding copywriter. Be concise, concrete, no fluff.",
    )
    logger.info("Generation AI stage complete stage=company_mission elapsed_ms=%d", int((time.monotonic() - ai_started_at) * 1000))

    ai_started_at = time.monotonic()
    logger.info("Generation AI stage started stage=company_description")
    description = await ai_service.generate_text(
        prompt=(
            f"Write a two-sentence company description for {name}, a {company_type.value} "
            f"company in {industry}, describing what it builds and who it serves."
        ),
        system_prompt="You are a branding copywriter. Be concise, concrete, no fluff.",
    )
    logger.info("Generation AI stage complete stage=company_description elapsed_ms=%d", int((time.monotonic() - ai_started_at) * 1000))

    company = Company(
        student_id=student_id,
        name=name,
        company_type=company_type,
        industry=industry,
        department=department,
        mission=mission,
        description=description,
        size=_SIZE_BY_TYPE[company_type],
        technology_stack=technology_stack,
    )
    db.add(company)
    db.flush()  # get company.id without full commit

    manager_name = f"{rng.choice(_MANAGER_FIRST)} {rng.choice(_MANAGER_LAST)}"
    personality = rng.choice(list(ManagerPersonality))
    manager = Manager(
        company_id=company.id,
        name=manager_name,
        title=rng.choice(_MANAGER_TITLES),
        personality=personality,
        satisfaction_score=70,
    )
    db.add(manager)
    db.flush()

    return company, manager
