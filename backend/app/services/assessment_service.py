"""Question bank, baseline assessment scoring, and skill catalog."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import statistics
from types import SimpleNamespace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.assessment import (
    AssessmentAnswer,
    AssessmentAttempt,
    AssessmentQuestion,
    Skill,
    StudentSkill,
)
from app.services.ai.ai_service import get_ai_service
from app.services.intelligence_service import COMPETENCIES, get_or_create_profile, serialize_profile
from app.services.recommendation import (
    BASELINE_FOCUS_THRESHOLD,
    BASELINE_STRENGTH_THRESHOLD,
    build_simulation_recommendation,
)

logger = logging.getLogger(__name__)

BASELINE_COMPETENCY_MAP = {
    "technical_knowledge": ("technical_knowledge",),
    "problem_solving": ("problem_solving",),
    "critical_thinking": ("critical_thinking",),
    "decision_making": ("decision_making",),
}
BASELINE_MCQ_CORRECT_SCORE = 100.0
BASELINE_MCQ_INCORRECT_SCORE = 35.0
BASELINE_QUESTION_COUNT = 7
BASELINE_SNAPSHOT_SCHEMA_VERSION = 1
BASELINE_SCORING_POLICY_VERSION = "baseline-v1"
BASELINE_GENERATION_PROMPT_VERSION = "domain-baseline-v1"
BASELINE_ALLOWED_DIFFICULTIES = {"beginner", "intermediate", "advanced"}

# Each category is a meaningful domain concept, not merely a role or stack
# token. AI sets must cover at least three categories and every item must map
# to at least one category for the student's selected technical domain.
DOMAIN_COVERAGE_GUIDANCE = {
    "software_development": {
        "programming_fundamentals": ("programming fundamentals", "data structures", "algorithm", "language fundamentals"),
        "object_oriented_design": ("object oriented", "object-oriented", "encapsulation", "polymorphism", "inheritance"),
        "api_architecture": ("api architecture", "api endpoint", "rest api", "application architecture", "service architecture", "software design"),
        "databases": ("database", "sql", "query", "schema", "transaction", "mongodb", "relational"),
    },
    "debugging_code_review": {
        "debugging_method": ("debugging methodology", "debugging", "reproduce", "root cause", "breakpoint", "stack trace"),
        "error_analysis": ("error analysis", "exception", "log analysis", "failure diagnosis", "root cause"),
        "testing_coverage": ("test coverage", "regression test", "unit test", "boundary test", "automated test"),
        "review_quality": ("code review", "pull request", "diff", "code quality", "static analysis"),
    },
    "devops_cloud_operations": {
        "linux_cli": ("linux", "command line", "shell command", "file permissions", "process management"),
        "containers": ("container", "docker", "kubernetes", "image registry"),
        "delivery_pipelines": ("ci cd", "pipeline", "continuous integration", "continuous delivery", "build artifact"),
        "cloud_infrastructure": ("cloud infrastructure", "aws", "terraform", "infrastructure as code", "cloud resource"),
        "deployment_observability": ("deployment", "rollback", "monitoring", "health check", "service availability"),
    },
    "cybersecurity_incident_response": {
        "network_security": ("network security", "network traffic", "packet capture", "firewall", "wireshark", "nmap"),
        "threat_detection": ("threat detection", "vulnerability", "exploit", "security alert", "siem"),
        "security_logs": ("security log", "authentication log", "log correlation", "security event"),
        "incident_handling": ("incident response", "incident handling", "containment", "eradication", "recovery"),
    },
    "data_analysis_qa_testing": {
        "sql_data_handling": ("sql", "query", "join", "data query", "data handling", "missing values", "dataframe"),
        "data_processing": ("data processing", "data transformation", "data frame", "pandas", "numpy", "data pipeline"),
        "statistical_reasoning": ("statistical", "distribution", "median", "variance", "sampling", "outlier", "correlation"),
        "data_quality_testing": ("data quality", "test case", "boundary test", "quality testing", "selenium", "pytest"),
    },
}

# A topic label alone is not enough to establish relevance. Each category also
# requires a concrete domain concept and a reasoning/task cue in the same
# sentence. The alternatives intentionally cover common terminology variants.
DOMAIN_CONCEPT_ANCHORS = {
    "software_development": {
        "programming_fundamentals": ("data structure", "algorithm", "type system", "function", "variable", "complexity", "language semantics"),
        "object_oriented_design": ("encapsulation", "inheritance", "polymorphism", "class", "object", "interface", "responsibility"),
        "api_architecture": ("endpoint", "request", "response", "service boundary", "application layer", "client server", "error handling"),
        "databases": ("schema", "query", "table", "document", "transaction", "relational", "join", "consistency"),
    },
    "debugging_code_review": {
        "debugging_method": ("reproduce", "breakpoint", "step through", "hypothesis", "stack trace", "isolate", "root cause"),
        "error_analysis": ("exception", "stack trace", "log", "error", "failure", "root cause"),
        "testing_coverage": ("test case", "regression", "boundary", "unit test", "coverage", "assertion"),
        "review_quality": ("diff", "pull request", "review", "maintainability", "static analysis", "code change"),
    },
    "devops_cloud_operations": {
        "linux_cli": ("process", "permission", "shell", "command", "file system", "signal"),
        "containers": ("image", "container", "orchestration", "pod", "runtime", "registry"),
        "delivery_pipelines": ("pipeline", "build", "artifact", "integration", "delivery stage", "release"),
        "cloud_infrastructure": ("iam", "role", "permission", "least privilege", "resource", "infrastructure as code", "configuration drift"),
        "deployment_observability": ("health check", "metric", "log", "alert", "rollback", "deployment", "availability"),
    },
    "cybersecurity_incident_response": {
        "network_security": ("packet", "traffic", "protocol", "port", "connection", "packet capture"),
        "threat_detection": ("vulnerability", "exploit", "alert", "severity", "indicator", "scan finding", "service scan", "exposed port"),
        "security_logs": ("authentication log", "security event", "event correlation", "siem", "log source"),
        "incident_handling": ("containment", "evidence", "eradication", "recovery", "isolate", "incident"),
    },
    "data_analysis_qa_testing": {
        "sql_data_handling": ("null", "query", "join", "missing value", "duplicate", "schema"),
        "data_processing": ("dataframe", "data frame", "transform", "missing value", "pipeline", "normalize", "data type"),
        "statistical_reasoning": ("median", "mean", "distribution", "sample", "variance", "outlier", "correlation"),
        "data_quality_testing": ("test case", "assertion", "boundary", "expected result", "quality check", "selenium", "pytest"),
    },
}

_DOMAIN_REASONING_ACTION = re.compile(
    r"\b(how|why|what|which|when|where|explain\w*|describe\w*|compare\w*|"
    r"analy[sz]\w*|interpret\w*|predict\w*|determin\w*|identify\w*|define\w*|select\w*|"
    r"choos\w*|trace\w*|debug\w*|design\w*|implement\w*|configur\w*|validat\w*|"
    r"test\w*|review\w*|investigat\w*|evaluat\w*|assess\w*|distinguish\w*|"
    r"prioriti[sz]\w*|contain\w*|correlat\w*|quer\w*|model\w*|build\w*|deploy\w*)\b",
    re.I,
)

_DOMAIN_TASK_ACTIONS = {
    "software_development": ("design", "implement", "trace", "compare", "choose", "select", "calculate", "query", "model", "predict", "affect", "influence", "separate", "handle", "assign", "identify", "define"),
    "debugging_code_review": ("reproduce", "inspect", "isolate", "trace", "diagnose", "test", "review", "identify", "compare", "interpret", "step through", "define"),
    "devops_cloud_operations": ("configure", "inspect", "deploy", "rollback", "roll back", "monitor", "promote", "build", "verify", "manage", "restrict", "validate", "apply", "define", "affect"),
    "cybersecurity_incident_response": ("inspect", "capture", "correlate", "contain", "isolate", "preserve", "verify", "analyze", "distinguish", "assess", "prioritize", "investigate", "identify", "establish", "define"),
    "data_analysis_qa_testing": ("query", "transform", "calculate", "interpret", "validate", "test", "create", "compare", "filter", "aggregate", "join", "handle", "preserve", "define", "affect", "assess"),
}

_DOMAIN_FALLBACK_SPECS = {
    "software_development": [
        ("programming_fundamentals", "type systems and data structures", ("java", "python", "javascript"), "technical_knowledge"),
        ("object_oriented_design", "encapsulation and responsibility boundaries", ("java", "python", "javascript"), "critical_thinking"),
        ("api_architecture", "separation of API, application, and persistence layers", ("fastapi", "node_js"), "technical_knowledge"),
        ("databases", "relational schema and query semantics", ("sql",), "problem_solving"),
        ("databases", "document data modeling and consistency", ("mongodb",), "decision_making"),
        ("api_architecture", "client-server contracts and error handling", ("fastapi", "node_js"), "problem_solving"),
        ("programming_fundamentals", "software design trade-offs and testability", ("python", "java", "javascript"), "critical_thinking"),
    ],
    "debugging_code_review": [
        ("debugging_method", "reproducing an intermittent defect before changing code", ("debugging", "ide_debugging_tools"), "problem_solving"),
        ("error_analysis", "using a stack trace to narrow a failure to its root cause", ("python", "java", "javascript"), "critical_thinking"),
        ("testing_coverage", "regression tests that protect a reproduced bug fix", ("testing", "debugging"), "technical_knowledge"),
        ("review_quality", "reviewing a Git diff for behavior and edge cases", ("git", "github"), "decision_making"),
        ("debugging_method", "using breakpoints and variable inspection to test a hypothesis", ("ide_debugging_tools", "debugging"), "problem_solving"),
        ("testing_coverage", "test coverage gaps around boundary conditions", ("testing", "debugging"), "critical_thinking"),
        ("review_quality", "code review findings and maintainability trade-offs", ("github", "git"), "decision_making"),
    ],
    "devops_cloud_operations": [
        ("linux_cli", "Linux process and file-permission fundamentals", ("linux",), "technical_knowledge"),
        ("containers", "container images, runtime isolation, and reproducible environments", ("docker", "kubernetes"), "technical_knowledge"),
        ("delivery_pipelines", "CI/CD pipeline stages and artifact promotion", ("ci_cd", "git"), "problem_solving"),
        ("cloud_infrastructure", "least-privilege access for cloud infrastructure", ("aws",), "decision_making"),
        ("cloud_infrastructure", "declarative infrastructure and configuration drift", ("terraform", "aws"), "critical_thinking"),
        ("deployment_observability", "deployment health checks and safe rollback criteria", ("docker", "kubernetes", "aws"), "problem_solving"),
        ("deployment_observability", "monitoring signals for service availability after release", ("kubernetes", "aws", "linux"), "critical_thinking"),
    ],
    "cybersecurity_incident_response": [
        ("network_security", "network traffic indicators that distinguish normal activity from suspicious connections", ("networking", "wireshark"), "technical_knowledge"),
        ("network_security", "packet capture evidence and protocol analysis", ("wireshark", "networking"), "critical_thinking"),
        ("threat_detection", "distinguishing an exposed service from a verified exploitable vulnerability", ("nmap",), "decision_making"),
        ("threat_detection", "correlating SIEM alerts without treating one alert as proof of compromise", ("siem_tools",), "critical_thinking"),
        ("security_logs", "authentication log patterns that may indicate credential abuse", ("linux", "siem_tools"), "problem_solving"),
        ("incident_handling", "containment priorities while preserving incident evidence", ("security_fundamentals", "linux"), "decision_making"),
        ("incident_handling", "recovery validation using service checks and security event evidence", ("linux", "siem_tools"), "problem_solving"),
    ],
    "data_analysis_qa_testing": [
        ("sql_data_handling", "SQL NULL behavior and reliable filtering", ("sql",), "technical_knowledge"),
        ("data_processing", "missing-value handling and validation in a Pandas data frame", ("pandas", "python"), "problem_solving"),
        ("statistical_reasoning", "choosing a robust summary when a data distribution contains outliers", ("numpy", "excel"), "critical_thinking"),
        ("data_quality_testing", "boundary and equivalence-class test cases for a data transformation", ("pytest", "testing"), "technical_knowledge"),
        ("data_quality_testing", "reliable element waits and assertions in Selenium tests", ("selenium",), "problem_solving"),
        ("statistical_reasoning", "interpreting a Power BI measure without confusing correlation and causation", ("power_bi", "excel"), "decision_making"),
        ("sql_data_handling", "checking joins for duplicated or missing records", ("sql", "pandas"), "critical_thinking"),
    ],
}

_DOMAIN_FALLBACK_PROMPTS = {
    "software_development": [
        "In {technology}, how do type systems affect the choice of data structures and the algorithms that use them?",
        "In {technology}, how would encapsulation help assign responsibilities between objects in a maintainable design?",
        "In an application using {technology}, how should an API endpoint separate request handling from application and persistence layers?",
        "When using {technology} to model related data, how do schema choices and query semantics affect consistency and correct results?",
        "In {technology}, how would you choose between embedding and referencing related documents while preserving the required consistency?",
        "In a client-server API architecture using {technology}, how should request and response contracts handle validation errors and missing resources?",
        "When implementing a feature in {technology}, how can data structures and focused tests make its behavior predictable?",
    ],
    "debugging_code_review": [
        "Using {technology}, how would you reproduce an intermittent defect and test a root-cause hypothesis before changing code?",
        "When {technology} code raises an exception, how can a stack trace help isolate the failing operation and its root cause?",
        "How would regression tests in {technology} demonstrate that a reproduced defect is fixed without breaking adjacent behavior?",
        "In a {technology} diff, how would you identify a behavior change and assess its edge cases before approving the code review?",
        "Using {technology}, how would you set a breakpoint and inspect variables to determine which hypothesis explains a defect?",
        "How would boundary test cases and assertions in {technology} expose a test coverage gap around a defect fix?",
        "During a {technology} code review, how would you compare a proposed change with existing behavior and identify maintainability risks?",
    ],
    "devops_cloud_operations": [
        "On {technology}, which shell commands or system views would you use to inspect a process and verify its file permissions?",
        "For a service running with {technology}, how do image contents and runtime isolation affect reproducibility between environments?",
        "In a {technology} pipeline, how should a build artifact be validated and promoted between stages?",
        "When defining {technology} infrastructure, how would you apply least-privilege access and detect configuration drift?",
        "After a release involving {technology}, which health checks or monitoring signals would justify a rollback?",
        "If a {technology} deployment fails a readiness check, how would you use service health evidence to choose whether to roll back?",
        "After a {technology} deployment, how would you compare latency, error rate, and availability signals to verify service health?",
    ],
    "cybersecurity_incident_response": [
        "When investigating network traffic with {technology}, how would you inspect a packet capture to distinguish an unexpected connection from normal protocol traffic?",
        "What can an {technology} service scan establish about exposed ports, and what additional evidence is required to verify exploitability?",
        "How would {technology} correlate security alerts so that one alert is not treated as proof of compromise?",
        "How would you correlate authentication logs observed with {technology} by timestamp and source to investigate possible credential abuse?",
        "During incident response with {technology}, how would you contain a compromised host while preserving logs and other investigation evidence?",
        "After containment, how would you use {technology} service checks and security events to validate safe recovery?",
        "After incident response, how would you verify that recovered systems using {technology} no longer show the indicators that triggered the incident?",
    ],
    "data_analysis_qa_testing": [
        "In {technology}, how does NULL behavior affect filters and joins, and what query would avoid dropping valid records accidentally?",
        "In a {technology} data frame, how would you validate and transform missing values while preserving the meaning of the original data?",
        "When {technology} data contain extreme outliers, how would you compare the mean and median and explain which better represents the distribution?",
        "How would you use {technology} to create boundary test cases and assertions for a data transformation?",
        "In {technology} browser tests, how would you wait for an element state and assert the expected result without relying on a fixed delay?",
        "When a {technology} report shows correlation, how would you assess whether the relationship supports a causal claim?",
        "In {technology}, how would you test a join for duplicated keys and missing records before using its result in analysis?",
    ],
}


def calculate_baseline_results(
    question_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Calculate baseline totals and evidence-backed competencies from question scores."""
    if not question_results:
        raise ValueError("A baseline result requires at least one answered question")
    scores_by_competency: Dict[str, List[float]] = {}
    question_scores = []
    for result in question_results:
        score = result.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
            raise ValueError("Question scores must be numeric values from 0 to 100")
        question_scores.append(float(score))
        for competency in BASELINE_COMPETENCY_MAP.get(result.get("competency"), ()):
            scores_by_competency.setdefault(competency, []).append(float(score))

    overall = statistics.fmean(question_scores)
    competencies = {
        key: statistics.fmean(values)
        for key, values in scores_by_competency.items()
        if values
    }
    strengths = [
        key.replace("_", " ").title()
        for key, score in sorted(competencies.items(), key=lambda item: (-item[1], item[0]))
        if score >= BASELINE_STRENGTH_THRESHOLD
    ]
    focus_areas = [
        key.replace("_", " ").title()
        for key, score in sorted(competencies.items(), key=lambda item: (item[1], item[0]))
        if score < BASELINE_FOCUS_THRESHOLD
    ]
    return {
        "overall_score": overall,
        "competencies": competencies,
        "strengths": strengths,
        "focus_areas": focus_areas,
        "unassessed_competencies": [key for key in COMPETENCIES if key not in competencies],
    }


def baseline_difficulty(overall_score: float) -> str:
    """Apply the established baseline thresholds to its one overall score."""
    return "advanced" if overall_score >= 78 else "intermediate" if overall_score >= 58 else "beginner"

SKILL_CATALOG = [
    ("Python", "technical", "General-purpose language used across backend and data work."),
    ("FastAPI", "technical", "Python API framework."),
    ("PostgreSQL", "technical", "Relational database used by the internship simulation."),
    ("JavaScript", "technical", "Language of the browser and many full-stack services."),
    ("React", "technical", "Component UI library."),
    ("SQL", "technical", "Querying and shaping relational data."),
    ("Git", "technical", "Version control and collaborative change history."),
    ("Debugging", "technical", "Finding and isolating defects from evidence."),
    ("REST APIs", "technical", "Designing and consuming HTTP interfaces."),
    ("Docker", "technical", "Packaging and running services consistently."),
    ("Testing", "technical", "Proving behavior with automated checks."),
    ("Communication", "workplace", "Explaining decisions to teammates and managers."),
]

QUESTION_BANK = [
    {
        "competency": "technical_knowledge",
        "question_type": "mcq",
        "prompt": "A teammate asks you to store user passwords. Which approach is appropriate?",
        "options": [
            {"id": "A", "text": "Store the raw password so support can read it back."},
            {"id": "B", "text": "Store a salted password hash and never keep the original password."},
            {"id": "C", "text": "Email the password to the manager after each login."},
            {"id": "D", "text": "Encode the password with Base64 and store that string."},
        ],
        "correct_option": "B",
    },
    {
        "competency": "problem_solving",
        "question_type": "mcq",
        "prompt": "A feature works on your machine and fails in the shared environment. What do you do first?",
        "options": [
            {"id": "A", "text": "Rewrite the feature before reading the error."},
            {"id": "B", "text": "Compare the error, inputs, and environment differences, then form a hypothesis."},
            {"id": "C", "text": "Mark the task done and wait for the client to complain."},
            {"id": "D", "text": "Delete the failing test so the pipeline is green."},
        ],
        "correct_option": "B",
    },
    {
        "competency": "decision_making",
        "question_type": "mcq",
        "prompt": "The sprint deadline is tomorrow and a new requirement conflicts with a security fix. What is the stronger decision?",
        "options": [
            {"id": "A", "text": "Ship the requirement and leave the security fix undocumented."},
            {"id": "B", "text": "State the trade-off, protect the security fix, and ask the manager which scope can slip."},
            {"id": "C", "text": "Ignore both and start a new feature."},
            {"id": "D", "text": "Disable authentication so the requirement is easier to demo."},
        ],
        "correct_option": "B",
    },
    {
        "competency": "critical_thinking",
        "question_type": "mcq",
        "prompt": "A dashboard shows a sudden drop in sign-ups. Which conclusion is justified?",
        "options": [
            {"id": "A", "text": "The product is failing, so the team should pivot immediately."},
            {"id": "B", "text": "The chart proves a competitor launched today."},
            {"id": "C", "text": "The drop is a signal to check tracking, releases, and the data source before deciding."},
            {"id": "D", "text": "Charts are never useful, so ignore it."},
        ],
        "correct_option": "C",
    },
]


def ensure_skill_catalog(db: Session) -> List[Skill]:
    existing = {skill.name.lower(): skill for skill in db.execute(select(Skill)).scalars().all()}
    for name, category, description in SKILL_CATALOG:
        if name.lower() in existing:
            continue
        skill = Skill(name=name, category=category, description=description)
        db.add(skill)
        existing[name.lower()] = skill
    db.flush()
    return list(existing.values())


_SOFT_SKILL_COMPETENCIES = {
    "communication",
    "adaptability",
    "time_management",
    "professionalism",
    "collaboration",
    "ownership",
}


def ensure_question_bank(db: Session) -> None:
    standalone_soft_skill_mcqs = db.execute(
        select(AssessmentQuestion).where(
            AssessmentQuestion.question_type == "mcq",
            AssessmentQuestion.competency.in_(_SOFT_SKILL_COMPETENCIES),
            AssessmentQuestion.active.is_(True),
        )
    ).scalars().all()
    for question in standalone_soft_skill_mcqs:
        question.active = False
        db.add(question)
    if standalone_soft_skill_mcqs:
        db.flush()

    count = db.execute(select(AssessmentQuestion.id).limit(1)).first()
    if count:
        return
    for item in QUESTION_BANK:
        db.add(
            AssessmentQuestion(
                prompt=item["prompt"],
                competency=item["competency"],
                question_type=item["question_type"],
                options=item.get("options", []),
                correct_option=item.get("correct_option"),
                rubric_keywords=item.get("rubric_keywords", []),
                difficulty="intermediate",
                domain="software",
                active=True,
            )
        )
    db.flush()


def list_skills(db: Session) -> List[Dict[str, Any]]:
    ensure_skill_catalog(db)
    skills = db.execute(select(Skill).order_by(Skill.category, Skill.name)).scalars().all()
    return [_skill_public(skill) for skill in skills]


def _skill_public(skill: Skill) -> Dict[str, Any]:
    return {
        "id": str(skill.id),
        "name": skill.name,
        "category": skill.category,
        "description": skill.description,
    }


def list_student_skills(db: Session, student_id: str) -> List[Dict[str, Any]]:
    ensure_skill_catalog(db)
    rows = db.execute(select(StudentSkill).where(StudentSkill.student_id == student_id)).scalars().all()
    return [
        {
            "id": str(row.id),
            "skill_id": str(row.skill_id),
            "name": row.skill.name if row.skill else None,
            "category": row.skill.category if row.skill else None,
            "self_rating": row.self_rating,
        }
        for row in rows
    ]


def replace_student_skills(db: Session, student_id: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ensure_skill_catalog(db)
    catalog = {skill.name.lower(): skill for skill in db.execute(select(Skill)).scalars().all()}
    names: List[str] = []
    resolved: List[tuple[Skill, int]] = []
    seen_names = set()
    for item in items:
        skill_name = str(item["name"]).strip().lower()
        if skill_name in seen_names:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Duplicate skill: {item['name']}")
        seen_names.add(skill_name)
        skill = catalog.get(skill_name)
        if skill is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown skill: {item['name']}")
        rating = item.get("self_rating", 3)
        if type(rating) is not int or not 1 <= rating <= 5:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "self_rating must be an integer between 1 and 5")
        resolved.append((skill, rating))

    # Validate every requested row before deleting or replacing existing data.
    db.query(StudentSkill).filter(StudentSkill.student_id == student_id).delete()
    for skill, rating in resolved:
        db.add(StudentSkill(student_id=student_id, skill_id=skill.id, self_rating=rating))
        names.append(skill.name)
    profile = get_or_create_profile(db, student_id)
    profile.skills = names
    db.add(profile)
    db.flush()
    return list_student_skills(db, student_id)


def _public_question(question: AssessmentQuestion) -> Dict[str, Any]:
    return {
        "id": str(question.id),
        "prompt": question.prompt,
        "competency": question.competency,
        "question_type": question.question_type,
        "options": question.options or [],
        "difficulty": question.difficulty,
    }


def _admin_question(question: AssessmentQuestion) -> Dict[str, Any]:
    payload = _public_question(question)
    payload.update(
        {
            "correct_option": question.correct_option,
            "rubric_keywords": question.rubric_keywords or [],
            "domain": question.domain,
            "active": question.active,
        }
    )
    return payload


def list_questions_admin(db: Session) -> List[Dict[str, Any]]:
    ensure_question_bank(db)
    rows = db.execute(select(AssessmentQuestion).order_by(AssessmentQuestion.created_at)).scalars().all()
    return [_admin_question(row) for row in rows]


def create_question(db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
    competency = payload["competency"]
    if competency not in COMPETENCIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"competency must be one of {COMPETENCIES}")
    question_type = payload.get("question_type", "mcq")
    if question_type not in {"mcq", "short_answer"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "question_type must be mcq or short_answer")
    if question_type == "mcq" and not payload.get("correct_option"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MCQ questions require correct_option")
    question = AssessmentQuestion(
        prompt=payload["prompt"],
        competency=competency,
        question_type=question_type,
        options=payload.get("options") or [],
        correct_option=payload.get("correct_option"),
        rubric_keywords=payload.get("rubric_keywords") or [],
        difficulty=payload.get("difficulty") or "intermediate",
        domain=payload.get("domain") or "software",
        active=payload.get("active", True),
    )
    db.add(question)
    db.flush()
    return _admin_question(question)


def update_question(db: Session, question_id: UUID, payload: Dict[str, Any]) -> Dict[str, Any]:
    question = db.get(AssessmentQuestion, question_id)
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    for field in ("prompt", "competency", "question_type", "options", "correct_option", "rubric_keywords", "difficulty", "domain", "active"):
        if field in payload and payload[field] is not None:
            setattr(question, field, payload[field])
    if question.competency not in COMPETENCIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"competency must be one of {COMPETENCIES}")
    db.add(question)
    db.flush()
    return _admin_question(question)


def delete_question(db: Session, question_id: UUID) -> None:
    question = db.get(AssessmentQuestion, question_id)
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    question.active = False
    db.add(question)
    db.flush()


def _validated_technical_profile(db: Session, student_id: str) -> Dict[str, Any]:
    # Imports are local because the canonical catalog currently enriches itself
    # from this module's existing SKILL_CATALOG.
    from app.services.technical_domain_catalog import get_technical_domain
    from app.services.technical_profile_service import get_student_technical_profile

    profile = get_student_technical_profile(db, student_id)
    if not profile.get("selected"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Select and save a technical domain and familiarity ratings before starting the baseline assessment.")
    domain = get_technical_domain(profile.get("technical_domain_id"))
    if profile.get("status") != "active" or domain is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The selected technical domain is unavailable. Choose and save an approved domain before starting the baseline assessment.")
    by_id = {row.id: row for row in domain.technologies}
    ratings = []
    for item in profile.get("technology_ratings", []):
        technology_id = item.get("technology_id")
        value = item.get("self_rating")
        if technology_id not in by_id:
            continue
        if value is not None and (type(value) is not int or not 1 <= value <= 5):
            raise HTTPException(status.HTTP_409_CONFLICT, "Stored technical familiarity ratings are invalid. Update the technical profile before starting the baseline assessment.")
        ratings.append({"technology_id": technology_id, "name": by_id[technology_id].name, "self_rating": value})
    # The service's active response is catalog-complete. Reject malformed or
    # stale persisted rating state rather than silently inventing familiarity.
    if len(ratings) != len(domain.technologies) or {r["technology_id"] for r in ratings} != set(by_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "The technical profile is incomplete. Update all domain familiarity ratings before starting the baseline assessment.")
    return {"technical_domain_id": domain.id, "domain": {"id": domain.id, "name": domain.name}, "technology_ratings": ratings}


def _normalise_text(text: str) -> str:
    value = text.casefold().replace("ci/cd", "ci cd").replace("object-oriented", "object oriented")
    return re.sub(r"[^a-z0-9+#]+", " ", value).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_text = f" {_normalise_text(text)} "
    normalized_phrase = _normalise_text(phrase)
    if f" {normalized_phrase} " in normalized_text:
        return True
    # Accept common plural forms without broad substring matching.
    if normalized_phrase.endswith("y"):
        plural = normalized_phrase[:-1] + "ies"
    else:
        plural = normalized_phrase + "s"
    return f" {plural} " in normalized_text


def _meaningfully_tests_domain_concept(text: str, domain_id: str) -> set[str]:
    """Return topics with a concept and a reasoning cue linked in one clause."""
    relevant: set[str] = set()
    topics = DOMAIN_COVERAGE_GUIDANCE[domain_id]
    anchors = DOMAIN_CONCEPT_ANCHORS[domain_id]
    task_actions = _DOMAIN_TASK_ACTIONS[domain_id]
    # Split at question/statement boundaries, not periods (e.g. Node.js).
    for clause in re.split(r"[?!;\n]+", text):
        has_task_action = any(
            (f" {_normalise_text(action)} " in f" {_normalise_text(clause)} ")
            if " " in action
            else re.search(rf"\b{re.escape(action)}\w*\b", clause, re.I)
            for action in task_actions
        )
        if not _DOMAIN_REASONING_ACTION.search(clause) or not has_task_action:
            continue
        for topic_id, markers in topics.items():
            topic_present = any(_contains_phrase(clause, marker) for marker in markers)
            concept_present = any(_contains_phrase(clause, anchor) for anchor in anchors[topic_id])
            if topic_present and concept_present:
                relevant.add(topic_id)
    return relevant


def _validate_generated_baseline_questions(raw_questions: Any, technical_profile: Dict[str, Any]) -> List[AssessmentQuestion]:
    if not isinstance(raw_questions, list) or len(raw_questions) != BASELINE_QUESTION_COUNT:
        raise ValueError("AI response must contain exactly seven questions")
    domain_id = technical_profile["technical_domain_id"]
    tech_names = {x["name"] for x in technical_profile["technology_ratings"]}
    seen: set[str] = set()
    covered_topics: set[str] = set()
    covered_technologies: set[str] = set()
    questions: List[AssessmentQuestion] = []
    competencies = {"technical_knowledge", "problem_solving", "critical_thinking", "decision_making"}
    soft_terms = re.compile(r"\b(manager|teammate|stakeholder|communicat(?:e|ion)|deadline|professionalism|feedback)\b", re.I)
    for item in raw_questions:
        if not isinstance(item, dict):
            raise ValueError("Question must be an object")
        text = item.get("prompt")
        if not isinstance(text, str) or len(text.strip()) < 20:
            raise ValueError("Question prompt is missing or too short")
        text = text.strip()
        norm = _normalise_text(text)
        if norm in seen:
            raise ValueError("Duplicate question")
        seen.add(norm)
        if soft_terms.search(text):
            raise ValueError("Soft-skill question rejected")
        matched_topics = _meaningfully_tests_domain_concept(text, domain_id)
        if not matched_topics:
            raise ValueError("Question does not meaningfully test a selected-domain concept")
        mentioned = {name for name in tech_names if _contains_phrase(text, name)}
        if not mentioned:
            raise ValueError("Question does not mention a technology in the selected domain")
        covered_topics.update(matched_topics)
        covered_technologies.update(mentioned)
        kind, comp, difficulty = item.get("question_type"), item.get("competency"), item.get("difficulty", "intermediate")
        if comp not in competencies or kind not in {"mcq", "short_answer"} or difficulty not in BASELINE_ALLOWED_DIFFICULTIES:
            raise ValueError("Unsupported competency, question type, or difficulty")
        options = item.get("options") or []
        if isinstance(options, list) and any(
            not isinstance(option, dict)
            or not isinstance(option.get("text"), str)
            or soft_terms.search(option["text"])
            for option in options
        ):
            raise ValueError("Malformed or unrelated answer option")
        correct = item.get("correct_option")
        rubric = item.get("rubric_keywords") or []
        if kind == "mcq":
            if not isinstance(options, list) or len(options) != 4 or any(not isinstance(o, dict) for o in options):
                raise ValueError("MCQs require four option objects")
            ids = [o.get("id") for o in options]
            option_texts = [_normalise_text(option["text"]) for option in options]
            if (
                any(not isinstance(i, str) or not i for i in ids)
                or len(set(ids)) != 4
                or correct not in ids
                or any(len(value) < 12 or len(value.split()) < 2 for value in option_texts)
                or len(set(option_texts)) != 4
            ):
                raise ValueError("MCQ options or answer key are invalid")
        elif options or not isinstance(rubric, list) or not rubric or any(not isinstance(k, str) or not k.strip() for k in rubric):
            raise ValueError("Short answers require a nonempty rubric and no options")
        questions.append(AssessmentQuestion(prompt=text, competency=comp, question_type=kind, options=options, correct_option=correct, rubric_keywords=rubric, difficulty=difficulty, domain=domain_id, active=True))
    if len(covered_topics) < 3 or len(covered_technologies) < 4:
        raise ValueError("Question set lacks required domain topic or technology coverage")
    return questions


def _personalized_fallback(technical_profile: Dict[str, Any]) -> List[AssessmentQuestion]:
    """Build a deterministic theoretical assessment using only the selected domain."""
    from app.services.technical_domain_catalog import get_technical_domain

    domain_id = technical_profile["technical_domain_id"]
    domain = get_technical_domain(domain_id)
    assert domain is not None
    ratings = {x["technology_id"]: x["self_rating"] for x in technical_profile["technology_ratings"]}
    techs = {x.id: x.name for x in domain.technologies}
    result = []
    for index, (topic, _concept, candidates, competency) in enumerate(_DOMAIN_FALLBACK_SPECS[domain_id]):
        # A null rating is unknown and is never converted to a low score.
        available = [candidate for candidate in candidates if candidate in techs]
        if not available:
            raise ValueError(f"Fallback topic '{topic}' has no technology in domain '{domain_id}'")
        familiarity = lambda candidate: ratings[candidate] if ratings[candidate] is not None else 3
        ordered = sorted(available, key=lambda candidate: (familiarity(candidate), candidate))
        # Alternate less- and more-familiar topics where a row has choices, so
        # the deterministic fallback does not equate unfamiliarity with poor ability.
        technology_id = ordered[0] if index % 2 == 0 else ordered[-1]
        rating = ratings[technology_id]
        difficulty = "beginner" if rating is not None and rating <= 2 else "advanced" if rating is not None and rating >= 4 else "intermediate"
        prompt = _DOMAIN_FALLBACK_PROMPTS[domain_id][index].format(technology=techs[technology_id])
        result.append(AssessmentQuestion(prompt=prompt, competency=competency, question_type="short_answer", options=[], correct_option=None, rubric_keywords=["explain", "evidence", "validation", "trade-off"], difficulty=difficulty, domain=domain_id, active=True))
    return result


async def generate_dynamic_baseline_questions(
    db: Session, student_id: str, attempt_seed: Optional[str] = None,
    *, technical_profile: Optional[Dict[str, Any]] = None,
) -> tuple[List[AssessmentQuestion], str]:
    tech_profile = technical_profile or _validated_technical_profile(db, student_id)
    ensure_question_bank(db)
    profile = get_or_create_profile(db, student_id)
    role = profile.role or profile.career_goal or "Student"
    prompt = (
        f"Generate a theoretical and conceptual baseline for {tech_profile['domain']['name']} ({tech_profile['technical_domain_id']}).\n"
        f"Target role is context only: {role}. Variation seed: {attempt_seed or uuid4()}.\n"
        f"Technology familiarity ratings are self-reported, not verified competence: {tech_profile['technology_ratings']}\n"
        "Use lower familiarity to guide some foundational coverage and higher familiarity to guide some more advanced conceptual coverage; balance both across the selected technologies. "
        "Interpret ratings 1-2 as foundational familiarity, 3 as core familiarity, and 4-5 as advanced conceptual familiarity; they are not ability estimates. "
        "Return exactly 7 domain-specific questions, each naming one catalog technology and one relevant concept. "
        "Across the set cover at least 3 topic categories and 4 technologies. Use only technical_knowledge, problem_solving, critical_thinking, decision_making. "
        "Use MCQ and short_answer, with four distinct options and correct_option for each MCQ, and rubric_keywords for each short answer. "
        "Avoid generic or soft-skill questions. Return JSON with a questions array."
    )
    try:
        ai = get_ai_service()
        data = await ai.generate_json(prompt, "Write only theoretical questions for the specified technical domain.")
        questions = _validate_generated_baseline_questions(data.get("questions") if isinstance(data, dict) else None, tech_profile)
        generation_details = {
            "source": "ai",
            "provider": getattr(ai, "provider_name", ai.__class__.__name__),
            "model": getattr(ai, "model", None),
            "prompt_version": BASELINE_GENERATION_PROMPT_VERSION,
        }
        for question in questions:
            question._snapshot_generation_details = generation_details
        with db.begin_nested():
            for question in questions:
                db.add(question)
            db.flush()
        return questions, "ai"
    except Exception as exc:
        logger.warning("Domain-aware baseline generation failed for %s: %s; using deterministic fallback", student_id, exc)
        questions = _personalized_fallback(tech_profile)
        with db.begin_nested():
            for question in questions:
                db.add(question)
            db.flush()
        return questions, "fallback"

def start_attempt(db: Session, student_id: str) -> Dict[str, Any]:
    existing = db.execute(
        select(AssessmentAttempt).where(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.status == "in_progress",
        )
    ).scalars().first()
    if existing:
        return _attempt_payload(db, existing)

    # Validate the student's explicit technical profile before question-bank
    # setup or attempt creation. Missing/stale data must never become a generic
    # assessment or an inferred domain.
    technical_profile = _validated_technical_profile(db, student_id)
    ensure_question_bank(db)
    # Keep newly generated question rows, the attempt, and its required
    # snapshot inside one savepoint. A caller that handles an exception and
    # later commits its outer transaction cannot accidentally persist a
    # partial unsnapshotted attempt.
    with db.begin_nested():
        return _create_snapshot_attempt(db, student_id, technical_profile)


def _create_snapshot_attempt(db: Session, student_id: str, technical_profile: Dict[str, Any]) -> Dict[str, Any]:
    attempt_seed = str(uuid4())
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            raise RuntimeError("start_attempt must run outside an async event loop")
        questions, source = asyncio.run(
            generate_dynamic_baseline_questions(db, student_id, attempt_seed, technical_profile=technical_profile)
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Error running domain-aware baseline generation for %s: %s; using domain fallback", student_id, exc)
        questions = _personalized_fallback(technical_profile)
        with db.begin_nested():
            for question in questions:
                db.add(question)
            db.flush()
        source = "fallback"

    if not questions:
        raise HTTPException(status.HTTP_409_CONFLICT, "The question bank is empty")

    created_at = datetime.now(timezone.utc)
    snapshot = _build_assessment_snapshot(
        questions,
        technical_profile,
        source=source,
        created_at=created_at,
        attempt_seed=attempt_seed,
    )

    attempt = AssessmentAttempt(
        student_id=student_id,
        status="in_progress",
        question_ids=[str(q.id) for q in questions],
        generation_metadata={
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "question_count": len(questions),
            "dynamic": source == "ai",
            "source": source,
            "technical_domain_id": technical_profile["technical_domain_id"],
            "technical_profile_snapshot": {
                "technical_domain_id": technical_profile["technical_domain_id"],
                "technology_ratings": [
                    {"technology_id": item["technology_id"], "self_rating": item["self_rating"]}
                    for item in technical_profile["technology_ratings"]
                ],
            },
        },
        assessment_snapshot=snapshot,
        started_at=created_at,
    )
    db.add(attempt)
    db.flush()
    return _attempt_payload(db, attempt)


def _attempt_payload(db: Session, attempt: AssessmentAttempt) -> Dict[str, Any]:
    if attempt.assessment_snapshot is not None:
        entries = attempt.assessment_snapshot.get("questions", [])
        questions = [_snapshot_public_question(item) for item in entries]
        return {
            "id": str(attempt.id),
            "status": attempt.status,
            "started_at": attempt.started_at.isoformat(),
            "questions": questions,
            "generation": attempt.generation_metadata or {},
        }
    ids = [UUID(value) for value in (attempt.question_ids or [])]
    questions = []
    if ids:
        rows = db.execute(select(AssessmentQuestion).where(AssessmentQuestion.id.in_(ids))).scalars().all()
        order = {value: index for index, value in enumerate(ids)}
        questions = [_public_question(row) for row in sorted(rows, key=lambda row: order.get(row.id, 0))]
    return {
        "id": str(attempt.id),
        "status": attempt.status,
        "started_at": attempt.started_at.isoformat(),
        "questions": questions,
        "generation": attempt.generation_metadata or {},
    }


def _build_assessment_snapshot(
    questions: List[AssessmentQuestion], technical_profile: Dict[str, Any], *, source: str, created_at: datetime,
    attempt_seed: str,
) -> Dict[str, Any]:
    """Capture exact validated generation objects before mutable rows can change."""
    details = getattr(questions[0], "_snapshot_generation_details", {}) if questions else {}
    return {
        "schema_version": BASELINE_SNAPSHOT_SCHEMA_VERSION,
        "created_at": created_at.isoformat(),
        "technical_domain_id": technical_profile["technical_domain_id"],
        "technology_ratings": [
            {"technology_id": item["technology_id"], "self_rating": item["self_rating"]}
            for item in technical_profile["technology_ratings"]
        ],
        "generation": {
            "source": source,
            "provider": details.get("provider"),
            "model": details.get("model"),
            "prompt_version": details.get("prompt_version"),
            "attempt_seed": attempt_seed,
        },
        "questions": [
            {
                "question_id": str(question.id),
                "prompt": question.prompt,
                "question_type": question.question_type,
                "options": question.options or [],
                "correct_option": question.correct_option,
                "rubric_keywords": question.rubric_keywords or [],
                "competency": question.competency,
                "difficulty": question.difficulty,
                "domain": question.domain,
            }
            for question in questions
        ],
    }


def _snapshot_public_question(item: Dict[str, Any]) -> Dict[str, Any]:
    """Project the existing student question contract without private rubric/key."""
    return {
        "id": item["question_id"],
        "prompt": item["prompt"],
        "competency": item["competency"],
        "question_type": item["question_type"],
        "options": item.get("options") or [],
        "difficulty": item["difficulty"],
    }


def _snapshot_question_models(snapshot: Dict[str, Any]) -> Dict[str, SimpleNamespace]:
    return {
        item["question_id"]: SimpleNamespace(
            id=UUID(item["question_id"]),
            prompt=item["prompt"],
            question_type=item["question_type"],
            options=item.get("options") or [],
            correct_option=item.get("correct_option"),
            rubric_keywords=item.get("rubric_keywords") or [],
            competency=item["competency"],
            difficulty=item["difficulty"],
            domain=item["domain"],
        )
        for item in snapshot.get("questions", [])
    }


def _provider_failure_category(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if code in (401, 403):
        return "provider_authentication"
    if code == 429:
        return "provider_rate_limit"
    if code is not None and code >= 500:
        return "provider_server_error"
    if isinstance(exc, asyncio.TimeoutError) or exc.__class__.__name__.lower().endswith("timeoutexception"):
        return "provider_timeout"
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "malformed_provider_response"
    return "provider_request_error"


def _parse_baseline_evaluation(data: Any, short_answer_ids: set[str]) -> Dict[str, Dict[str, Any]]:
    """Validate the provider's per-short-answer evaluations; never accept a total score."""
    if not isinstance(data, dict) or set(data) != {"evaluations"} or not isinstance(data.get("evaluations"), list):
        raise ValueError("evaluation response must contain an evaluations array")
    parsed: Dict[str, Dict[str, Any]] = {}
    for item in data["evaluations"]:
        if not isinstance(item, dict):
            raise ValueError("each evaluation must be an object")
        if set(item) != {"question_id", "score", "explanation", "feedback"}:
            raise ValueError("evaluation contains missing or unexpected fields")
        question_id = item.get("question_id")
        score = item.get("score")
        explanation = item.get("explanation")
        feedback = item.get("feedback")
        if question_id not in short_answer_ids or question_id in parsed:
            raise ValueError("evaluation question IDs are unexpected or duplicated")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
            raise ValueError("evaluation score must be numeric and between 0 and 100")
        if not isinstance(explanation, str) or not explanation.strip() or len(explanation) > 1000:
            raise ValueError("evaluation explanation is missing or invalid")
        if not isinstance(feedback, str) or not feedback.strip() or len(feedback) > 1000:
            raise ValueError("evaluation feedback is missing or invalid")
        parsed[question_id] = {
            "score": float(score),
            "explanation": explanation.strip(),
            "feedback": feedback.strip(),
        }
    if set(parsed) != short_answer_ids:
        raise ValueError("evaluation response is incomplete")
    return parsed


def _evaluate_attempt_answers(attempt_id: UUID, role: Optional[str], questions: Dict[str, AssessmentQuestion], answers: List[Dict[str, str]]):
    """Run one strict provider evaluation; MCQ correctness remains objectively graded here."""
    ai_service = get_ai_service()
    from app.services.ai.ai_service import MockAIService
    provider = getattr(ai_service, "provider_name", ai_service.__class__.__name__)
    model = getattr(ai_service, "model", None)
    if isinstance(ai_service, MockAIService):
        logger.error(
            "Baseline AI evaluation unavailable provider=mock model=none category=provider_not_configured attempt_id=%s",
            attempt_id,
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AI evaluation is not configured. Your attempt remains active; retry after a provider is configured.",
        )

    answer_by_id = {item["question_id"]: item["response"] for item in answers}
    short_ids = {qid for qid, question in questions.items() if question.question_type == "short_answer"}
    evaluation_input = []
    answer_results: Dict[str, Dict[str, Any]] = {}
    for question_id, question in questions.items():
        response = answer_by_id[question_id].strip()
        if question.question_type == "mcq":
            correct = response.upper() == (question.correct_option or "").upper()
            option_text = {str(option.get("id", "")).upper(): option.get("text", "") for option in (question.options or [])}
            correct_answer = option_text.get((question.correct_option or "").upper(), question.correct_option or "")
            explanation = "The selected option matches the answer key." if correct else f"The answer key is {question.correct_option}: {correct_answer}."
            answer_results[question_id] = {
                "score": BASELINE_MCQ_CORRECT_SCORE if correct else BASELINE_MCQ_INCORRECT_SCORE,
                "explanation": explanation,
                "feedback": "Correct." if correct else f"Review the concept behind the correct option: {correct_answer}.",
                "question_id": question_id,
                "competency": question.competency,
                "question": question.prompt,
                "response": response,
                "question_type": "mcq",
            }
        evaluation_input.append({
            "question_id": question_id,
            "question": question.prompt,
            "question_type": question.question_type,
            "options": question.options or [],
            "correct_option": question.correct_option,
            "rubric_keywords": question.rubric_keywords or [],
            "student_answer": response,
            "evaluate_with_ai": question.question_type == "short_answer",
        })

    prompt = (
        "Evaluate this baseline assessment answer set. Input JSON follows:\n"
        f"{json.dumps({'target_role': role or 'Software Engineer', 'items': evaluation_input}, ensure_ascii=False)}\n"
        "For each item where evaluate_with_ai is true, return one evaluation with exactly these fields: "
        "question_id, score (0-100), explanation, feedback. Judge the answer against that item's actual question and rubric_keywords; "
        "score correctness, relevance, completeness, and demonstrated technical understanding. Be specific to what the student wrote. "
        "Do not use a single generic score or feedback across answers. For MCQ items, do not grade them and do not include them in evaluations; "
        "their answer key is supplied only as context and they are graded objectively by the application. "
        "Return JSON only: {\"evaluations\":[...]}. Return exactly one entry for every short_answer question and no others. "
        "Do not return an overall or competency score."
    )
    try:
        data = asyncio.run(ai_service.generate_json(
            prompt,
            "You are the rubric-based evaluator for a baseline assessment. Return only strict JSON. System rubric: assess each short response independently.",
        ))
        short_results = _parse_baseline_evaluation(data, short_ids)
    except HTTPException:
        raise
    except Exception as exc:
        category = _provider_failure_category(exc)
        logger.error(
            "Baseline AI evaluation failed provider=%s model=%s category=%s attempt_id=%s",
            provider, model or "unknown", category, attempt_id,
            exc_info=True,
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AI evaluation is temporarily unavailable or returned an invalid result. Your attempt is unchanged; retry submission.",
        ) from exc

    for question_id, item in short_results.items():
        question = questions[question_id]
        answer_results[question_id] = {
            **item,
            "question_id": question_id,
            "competency": question.competency,
            "question": question.prompt,
            "response": answer_by_id[question_id],
            "question_type": "short_answer",
        }
    return answer_results, provider, model


def submit_attempt(db: Session, student_id: str, attempt_id: UUID, answers: List[Dict[str, str]]) -> Dict[str, Any]:
    attempt = db.execute(
        select(AssessmentAttempt).where(AssessmentAttempt.id == attempt_id).with_for_update()
    ).scalar_one_or_none()
    if attempt is None or attempt.student_id != student_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment attempt not found")
    if attempt.status != "in_progress":
        raise HTTPException(status.HTTP_409_CONFLICT, "This assessment is already submitted")
    expected = set(attempt.question_ids or [])
    provided = {item["question_id"] for item in answers}
    if len(answers) != len(expected) or provided != expected:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Submit one answer for every question in this attempt")
    if any(not isinstance(item.get("response"), str) or not item["response"].strip() for item in answers):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Every question needs a non-empty answer")

    if attempt.assessment_snapshot is not None:
        questions = _snapshot_question_models(attempt.assessment_snapshot)
    else:
        questions = {
            str(row.id): row
            for row in db.execute(select(AssessmentQuestion).where(AssessmentQuestion.id.in_([UUID(value) for value in expected]))).scalars().all()
        }
    if set(questions) != expected:
        raise HTTPException(status.HTTP_409_CONFLICT, "One or more assessment questions are no longer available")
    profile = get_or_create_profile(db, student_id)
    answer_results, provider, model = _evaluate_attempt_answers(attempt.id, profile.role or profile.career_goal, questions, answers)
    existing_rows = db.execute(
        select(AssessmentAnswer).where(
            AssessmentAnswer.attempt_id == attempt.id,
            AssessmentAnswer.question_id.in_([UUID(value) for value in expected]),
        )
    ).scalars().all()
    existing_by_question: Dict[str, AssessmentAnswer] = {}
    for row in existing_rows:
        key = str(row.question_id)
        if key in existing_by_question:
            raise HTTPException(status.HTTP_409_CONFLICT, "This attempt has duplicate saved answers")
        existing_by_question[key] = row

    for item in answers:
        question = questions[item["question_id"]]
        score = answer_results[item["question_id"]]["score"]
        row = existing_by_question.get(item["question_id"])
        if row is None:
            row = AssessmentAnswer(attempt_id=attempt.id, question_id=question.id)
            db.add(row)
            existing_by_question[item["question_id"]] = row
        row.response = item["response"]
        row.score = score
        row.competency = question.competency
    db.flush()

    persisted_answers = db.execute(
        select(AssessmentAnswer).where(
            AssessmentAnswer.attempt_id == attempt.id,
            AssessmentAnswer.question_id.in_([UUID(value) for value in expected]),
        )
    ).scalars().all()
    if {str(row.question_id) for row in persisted_answers} != expected or len(persisted_answers) != len(expected):
        raise HTTPException(status.HTTP_409_CONFLICT, "Could not persist one answer for every question")
    question_results = [
        {"score": row.score, "competency": questions[str(row.question_id)].competency}
        for row in persisted_answers
    ]
    calculated = calculate_baseline_results(question_results)
    overall = calculated["overall_score"]
    scores = calculated["competencies"]
    strengths = calculated["strengths"]
    weaknesses = calculated["focus_areas"]
    difficulty = baseline_difficulty(overall)

    profile.baseline_competencies = scores
    profile.current_competencies = scores.copy()
    profile.strengths = strengths
    profile.weaknesses = weaknesses
    profile.assessment_summary = (
        f"Baseline assessment completed with an average question score of {overall:.2f}/100."
    )
    profile.recommended_difficulty = difficulty
    profile.last_assessed_at = datetime.now(timezone.utc)
    profile.recommended_parameters = build_simulation_recommendation(profile, overall_score=overall)
    db.add(profile)

    attempt.status = "completed"
    attempt.overall_score = overall
    attempt.competencies = scores
    attempt.completed_at = datetime.now(timezone.utc)
    metadata = dict(attempt.generation_metadata or {})
    metadata["evaluation"] = {
        "status": "completed",
        "provider": provider,
        "model": model,
        "overall_score": overall,
        "competencies": scores,
        "unassessed_competencies": calculated["unassessed_competencies"],
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "answers": [answer_results[item["question_id"]] for item in answers],
    }
    attempt.generation_metadata = metadata
    if attempt.assessment_snapshot is not None:
        snapshot = dict(attempt.assessment_snapshot)
        snapshot["completion"] = {
            "completed_at": attempt.completed_at.isoformat(),
            "scoring_policy_version": BASELINE_SCORING_POLICY_VERSION,
            "overall_score": overall,
            "competencies": scores,
            "strengths": strengths,
            "focus_areas": weaknesses,
            "recommendation": profile.recommended_parameters,
            "difficulty": profile.recommended_difficulty,
            "evaluation_provenance": {
                "provider": provider,
                "model": model,
                "evaluated_at": metadata["evaluation"]["evaluated_at"],
            },
        }
        attempt.assessment_snapshot = snapshot
    db.add(attempt)
    db.flush()
    return {
        "attempt_id": str(attempt.id),
        "overall_score": overall,
        "competencies": scores,
        "unassessed_competencies": calculated["unassessed_competencies"],
        "profile": serialize_profile(profile),
        "evaluation": metadata["evaluation"],
    }


_PUBLIC_EVALUATION_FIELDS = {
    "status", "provider", "model", "overall_score", "competencies",
    "unassessed_competencies", "evaluated_at", "answers",
}
_PUBLIC_ANSWER_FIELDS = {
    "question_id", "competency", "question", "response", "question_type",
    "score", "explanation", "feedback",
}


def _safe_result_evaluation(metadata: Any) -> Optional[Dict[str, Any]]:
    """Project persisted educational feedback without exposing private metadata."""
    evaluation = metadata.get("evaluation") if isinstance(metadata, dict) else None
    if not isinstance(evaluation, dict):
        return None
    result = {key: evaluation[key] for key in _PUBLIC_EVALUATION_FIELDS if key in evaluation and key != "answers"}
    answers = evaluation.get("answers")
    if isinstance(answers, list):
        result["answers"] = [
            {key: answer[key] for key in _PUBLIC_ANSWER_FIELDS if key in answer}
            for answer in answers if isinstance(answer, dict)
        ]
    return result


def _assessment_result_projection(attempt: AssessmentAttempt) -> Dict[str, Any]:
    """Student-safe result DTO; historical fields never come from current profile."""
    snapshot = attempt.assessment_snapshot if isinstance(attempt.assessment_snapshot, dict) else None
    metadata = attempt.generation_metadata if isinstance(attempt.generation_metadata, dict) else {}
    evaluation = _safe_result_evaluation(metadata)
    if snapshot is not None:
        completion = snapshot.get("completion") if isinstance(snapshot.get("completion"), dict) else {}
        domain_id = snapshot.get("technical_domain_id")
        ratings = snapshot.get("technology_ratings")
        strengths = completion.get("strengths")
        focus_areas = completion.get("focus_areas")
        difficulty = completion.get("difficulty")
        recommendation = completion.get("recommendation")
        historical_status = "snapshot"
    else:
        # Older Stage 3A attempts may have a creation-time profile copy in metadata.
        profile_copy = metadata.get("technical_profile_snapshot")
        if not isinstance(profile_copy, dict):
            profile_copy = {}
        domain_id = profile_copy.get("technical_domain_id") or metadata.get("technical_domain_id")
        ratings = profile_copy.get("technology_ratings")
        strengths = focus_areas = difficulty = recommendation = None
        historical_status = "legacy"
    if isinstance(ratings, list):
        ratings = [
            {"technology_id": item["technology_id"], "self_rating": item["self_rating"]}
            for item in ratings
            if isinstance(item, dict)
            and isinstance(item.get("technology_id"), str)
            and item["technology_id"]
            and "self_rating" in item
            and (
                item["self_rating"] is None
                or (
                    type(item["self_rating"]) is int
                    and 1 <= item["self_rating"] <= 5
                )
            )
        ]
    else:
        ratings = None
    return {
        "attempt_id": str(attempt.id),
        "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
        "overall_score": attempt.overall_score,
        "competencies": attempt.competencies or {},
        "unassessed_competencies": (
            evaluation.get("unassessed_competencies") if evaluation else None
        ),
        "technical_domain_id": domain_id,
        "technology_ratings": ratings,
        "strengths": strengths,
        "focus_areas": focus_areas,
        "recommended_difficulty": difficulty,
        "recommendation": recommendation,
        "evaluation": evaluation,
        "historical_data_status": historical_status,
    }


def assessment_history(db: Session, student_id: str, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
    base = select(AssessmentAttempt).where(
        AssessmentAttempt.student_id == student_id,
        AssessmentAttempt.status == "completed",
    )
    total = db.execute(select(func.count()).select_from(AssessmentAttempt).where(
        AssessmentAttempt.student_id == student_id,
        AssessmentAttempt.status == "completed",
    )).scalar_one()
    attempts = db.execute(
        base.order_by(
            AssessmentAttempt.completed_at.desc().nulls_last(),
            AssessmentAttempt.started_at.desc(),
            AssessmentAttempt.id.desc(),
        ).limit(limit).offset(offset)
    ).scalars().all()
    return {
        "items": [_assessment_result_projection(attempt) for attempt in attempts],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(attempts) < total,
    }


def historical_result(db: Session, student_id: str, attempt_id: UUID) -> Dict[str, Any]:
    attempt = db.execute(
        select(AssessmentAttempt).where(
            AssessmentAttempt.id == attempt_id,
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.status == "completed",
        )
    ).scalar_one_or_none()
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Completed assessment not found")
    return _assessment_result_projection(attempt)


def latest_result(db: Session, student_id: str) -> Optional[Dict[str, Any]]:
    attempt = db.execute(
        select(AssessmentAttempt)
        .where(AssessmentAttempt.student_id == student_id, AssessmentAttempt.status == "completed")
        .order_by(AssessmentAttempt.completed_at.desc().nulls_last(), AssessmentAttempt.id.desc())
    ).scalars().first()
    if attempt is None:
        return None
    result = _assessment_result_projection(attempt)
    # Keep the established latest contract's values and defaults intact.
    result["unassessed_competencies"] = result["unassessed_competencies"] or []
    return result
