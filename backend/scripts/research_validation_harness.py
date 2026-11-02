"""Repeatable, synthetic policy comparison; not a student-outcome experiment.

Runs fixed, labeled fixtures for the four proposed mechanisms. It intentionally
reports rule/contract conformance only; it does not model learner behavior or
claim empirical effectiveness. Uses only the Python standard library.
"""
from __future__ import annotations

import json


CASES = [
    {"old": "validate request", "new": "validate request and retry safely", "gap": "problem_solving", "scores": {"problem_solving": 58}},
    {"old": "show status", "new": "show status and audit changes", "gap": "communication", "scores": {"communication": 62}},
    {"old": "save profile", "new": "save profile with ownership checks", "gap": "technical_knowledge", "scores": {"technical_knowledge": 66}},
    {"old": "send notification", "new": "send notification with failure recovery", "gap": "adaptability", "scores": {"adaptability": 60}},
    {"old": "render report", "new": "render report accessibly", "gap": "critical_thinking", "scores": {}},
]


def experiment_a() -> dict:
    static_success = sum(case["old"] == case["new"] for case in CASES)
    evolving_success = sum(bool(case["new"] and case["new"] != case["old"]) for case in CASES)
    return {"runs": len(CASES), "outcome": "new_requirement_present_in_active_work_spec",
            "baseline_static_successes": static_success,
            "proposed_dynamic_successes": evolving_success}


def experiment_b() -> dict:
    # One synthetic persisted record and stable pointer per supported score.
    supported = [case for case in CASES if case["scores"]]
    baseline_refs = 0
    proposed_refs = sum(len(case["scores"]) for case in supported)
    score_count = proposed_refs
    return {"runs": len(CASES), "scored_dimensions": score_count,
            "baseline_traceability_rate": baseline_refs / score_count,
            "proposed_traceability_rate": proposed_refs / score_count,
            "baseline_unsupported_score_count": score_count - baseline_refs,
            "proposed_unsupported_score_count": score_count - proposed_refs}


def experiment_c() -> dict:
    evaluated = [case for case in CASES if case["scores"]]
    # Baseline policy is technical-performance-only and emits no competency focus.
    baseline_correct = 0
    proposed_correct = sum(min(case["scores"], key=case["scores"].get) == case["gap"]
                           for case in evaluated)
    evidence_unavailable_cases = sum(not case["scores"] for case in CASES)
    return {"runs": len(CASES), "eligible_evidence_cases": len(evaluated),
            "baseline_task_focus_matches": baseline_correct,
            "proposed_task_focus_matches": proposed_correct,
            "proposed_cases_withheld_for_missing_score": evidence_unavailable_cases}


def experiment_d() -> dict:
    # Five request fixtures: one failed review, two assessment results already
    # available, and two requests that need a competency handoff.
    cases = [
        {"review_ok": False, "assessment_available": False},
        {"review_ok": True, "assessment_available": True},
        {"review_ok": True, "assessment_available": True},
        {"review_ok": True, "assessment_available": False},
        {"review_ok": True, "assessment_available": False},
    ]
    baseline_calls = 3 * len(cases)  # independent invocation ignores dependencies/results
    proposed_calls = 0
    skipped_downstream = 0
    for case in cases:
        proposed_calls += 1  # review
        if not case["review_ok"]:
            skipped_downstream += 2
            continue
        if not case["assessment_available"]:
            proposed_calls += 1  # competency, only when absent
        proposed_calls += 1  # career explanation consumes/reuses assessment
    return {"runs": len(cases), "baseline_agent_calls": baseline_calls,
            "proposed_agent_calls": proposed_calls,
            "duplicate_competency_calls_avoided": sum(c["assessment_available"] and c["review_ok"] for c in cases),
            "downstream_calls_skipped_after_review_failure": skipped_downstream}


def main() -> None:
    print(json.dumps({"evaluation_type": "synthetic_policy_conformance",
                      "empirical_student_data": False,
                      "experiments": {"A_dynamic_requirements": experiment_a(),
                                      "B_evidence_provenance": experiment_b(),
                                      "C_adaptive_progression": experiment_c(),
                                      "D_coordination": experiment_d()}}, indent=2))


if __name__ == "__main__":
    main()
