"""Database-independent checks for the technical-domain reference catalog."""

from app.api.routes.students import router
from app.services.assessment_service import SKILL_CATALOG
from app.services.technical_domain_catalog import (
    TECHNICAL_DOMAINS,
    get_technical_domain,
    list_technical_domains,
)


def test_catalog_exposes_exactly_five_approved_domains_with_stable_labels():
    payload = list_technical_domains()
    assert [(item["id"], item["name"]) for item in payload["domains"]] == [
        ("software_development", "Software Development"),
        ("debugging_code_review", "Debugging and Code Review"),
        ("devops_cloud_operations", "DevOps / Cloud Operations"),
        ("cybersecurity_incident_response", "Cybersecurity Incident Response"),
        ("data_analysis_qa_testing", "Data Analysis / QA Testing"),
    ]
    assert len(TECHNICAL_DOMAINS) == 5


def test_each_domain_has_unique_stable_technology_ids_and_expected_mapping():
    payload = list_technical_domains()
    expected = {
        "software_development": {"Java", "Python", "JavaScript", "React", "Node.js", "FastAPI", "SQL", "MongoDB"},
        "debugging_code_review": {"Java", "Python", "JavaScript", "Git", "GitHub", "IDE debugging tools", "Testing", "Debugging"},
        "devops_cloud_operations": {"Linux", "Docker", "Kubernetes", "AWS", "CI/CD", "Git", "Terraform"},
        "cybersecurity_incident_response": {"Linux", "Networking", "Wireshark", "Nmap", "SIEM tools", "Python", "Security fundamentals"},
        "data_analysis_qa_testing": {"Python", "SQL", "Pandas", "NumPy", "Power BI", "Excel", "Selenium", "PyTest", "Testing"},
    }
    for domain in payload["domains"]:
        technologies = domain["technologies"]
        assert {item["name"] for item in technologies} == expected[domain["id"]]
        assert len({item["id"] for item in technologies}) == len(technologies)
        assert all(item["category"] == "technical" and item["description"] for item in technologies)


def test_existing_skill_catalog_entries_are_reused_without_creating_catalog_records():
    existing_names = {name for name, _category, _description in SKILL_CATALOG}
    payload = list_technical_domains()
    mapped = {
        item["name"]
        for domain in payload["domains"]
        for item in domain["technologies"]
        if item["in_skill_catalog"]
    }
    assert mapped == {"Python", "JavaScript", "React", "FastAPI", "SQL", "Git", "Testing", "Debugging", "Docker"}
    assert mapped <= existing_names
    assert all("in_skill_catalog" in item for domain in payload["domains"] for item in domain["technologies"])


def test_domain_lookup_returns_none_for_unapproved_identifiers():
    assert get_technical_domain("software_development") is not None
    assert get_technical_domain("healthcare") is None
    assert get_technical_domain("unknown") is None


def test_read_only_catalog_route_is_additive_and_existing_skills_route_remains():
    route_contracts = {(route.path, method) for route in router.routes for method in getattr(route, "methods", set())}
    assert ("/api/technical-domains", "GET") in route_contracts
    assert ("/api/skills", "GET") in route_contracts
    assert ("/api/students/me/skills", "PUT") in route_contracts
