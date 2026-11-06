"""Canonical technical domains and their technology catalog.

This catalog is intentionally separate from company industries, student roles,
and the existing competency model. Technologies that are not currently in the
student skill catalog are configuration-only here; loading this module never
creates or changes database rows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.assessment_service import SKILL_CATALOG


@dataclass(frozen=True)
class Technology:
    id: str
    name: str
    description: str
    catalog_skill_name: str | None = None


@dataclass(frozen=True)
class TechnicalDomain:
    id: str
    name: str
    technologies: tuple[Technology, ...]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


_SKILL_DETAILS = {
    name.casefold(): {"category": category, "description": description}
    for name, category, description in SKILL_CATALOG
}


def _technology(name: str, description: str, *, skill_name: str | None = None) -> Technology:
    return Technology(id=_slug(name), name=name, description=description, catalog_skill_name=skill_name)


TECHNICAL_DOMAINS: tuple[TechnicalDomain, ...] = (
    TechnicalDomain(
        id="software_development",
        name="Software Development",
        technologies=(
            _technology("Java", "Java programming language."),
            _technology("Python", "General-purpose language used across backend and data work.", skill_name="Python"),
            _technology("JavaScript", "Language used by browser and full-stack applications.", skill_name="JavaScript"),
            _technology("React", "Component UI library.", skill_name="React"),
            _technology("Node.js", "JavaScript runtime for server-side applications."),
            _technology("FastAPI", "Python API framework.", skill_name="FastAPI"),
            _technology("SQL", "Querying and shaping relational data.", skill_name="SQL"),
            _technology("MongoDB", "Document-oriented NoSQL database."),
        ),
    ),
    TechnicalDomain(
        id="debugging_code_review",
        name="Debugging and Code Review",
        technologies=(
            _technology("Java", "Java programming language."),
            _technology("Python", "General-purpose language used across backend and data work.", skill_name="Python"),
            _technology("JavaScript", "Language used by browser and full-stack applications.", skill_name="JavaScript"),
            _technology("Git", "Version control and collaborative change history.", skill_name="Git"),
            _technology("GitHub", "Hosted source control and code review workflows."),
            _technology("IDE debugging tools", "Debugger features such as breakpoints, stepping, and variable inspection."),
            _technology("Testing", "Proving behavior with automated checks.", skill_name="Testing"),
            _technology("Debugging", "Finding and isolating defects from evidence.", skill_name="Debugging"),
        ),
    ),
    TechnicalDomain(
        id="devops_cloud_operations",
        name="DevOps / Cloud Operations",
        technologies=(
            _technology("Linux", "Operating system and command-line administration."),
            _technology("Docker", "Packaging and running services consistently.", skill_name="Docker"),
            _technology("Kubernetes", "Container orchestration platform."),
            _technology("AWS", "Amazon Web Services cloud platform."),
            _technology("CI/CD", "Continuous integration and delivery pipelines."),
            _technology("Git", "Version control and collaborative change history.", skill_name="Git"),
            _technology("Terraform", "Infrastructure as code tooling."),
        ),
    ),
    TechnicalDomain(
        id="cybersecurity_incident_response",
        name="Cybersecurity Incident Response",
        technologies=(
            _technology("Linux", "Operating system and command-line administration."),
            _technology("Networking", "Network protocols, traffic, and connectivity fundamentals."),
            _technology("Wireshark", "Network protocol analysis tool."),
            _technology("Nmap", "Network discovery and security auditing tool."),
            _technology("SIEM tools", "Security information and event management platforms."),
            _technology("Python", "General-purpose language used across backend and data work.", skill_name="Python"),
            _technology("Security fundamentals", "Core security concepts including vulnerabilities, controls, and incident handling."),
        ),
    ),
    TechnicalDomain(
        id="data_analysis_qa_testing",
        name="Data Analysis / QA Testing",
        technologies=(
            _technology("Python", "General-purpose language used across backend and data work.", skill_name="Python"),
            _technology("SQL", "Querying and shaping relational data.", skill_name="SQL"),
            _technology("Pandas", "Python library for data analysis and tabular data."),
            _technology("NumPy", "Python library for numerical computing."),
            _technology("Power BI", "Business intelligence and data visualization platform."),
            _technology("Excel", "Spreadsheet tool for analysis and reporting."),
            _technology("Selenium", "Browser automation framework for testing."),
            _technology("PyTest", "Python testing framework."),
            _technology("Testing", "Proving behavior with automated checks.", skill_name="Testing"),
        ),
    ),
)

_DOMAINS_BY_ID = {domain.id: domain for domain in TECHNICAL_DOMAINS}


def list_technical_domains() -> dict[str, list[dict[str, Any]]]:
    """Serialize the approved domain catalog without database access or writes."""
    domains = []
    for domain in TECHNICAL_DOMAINS:
        technologies = []
        for technology in domain.technologies:
            existing = _SKILL_DETAILS.get(technology.catalog_skill_name.casefold()) if technology.catalog_skill_name else None
            technologies.append({
                "id": technology.id,
                "name": technology.name,
                "category": existing["category"] if existing else "technical",
                "description": existing["description"] if existing else technology.description,
                "in_skill_catalog": existing is not None,
            })
        domains.append({"id": domain.id, "name": domain.name, "technologies": technologies})
    return {"domains": domains}


def get_technical_domain(domain_id: str) -> TechnicalDomain | None:
    """Look up an approved technical domain by its stable identifier."""
    return _DOMAINS_BY_ID.get(domain_id)
