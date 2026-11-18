from __future__ import annotations
from typing import Any, Dict


def build_review_prompt(context: Dict[str, Any]) -> str:
    task_title = context.get('task_title', 'Unknown Task')
    task_description = context.get('task_description') or ''
    acceptance_criteria = context.get('acceptance_criteria', [])
    submission_content = context.get('submission_content') or 'No submission content available.'
    stack = context.get('technology_stack') or []
    filenames = context.get("attached_filenames") or []
    test_results = (context.get("test_results") or "")[:2000]

    has_evidence = bool(context.get("has_work_evidence", True))
    prompt_parts = [
        "Review this submission. Return JSON only. No reasoning.",
        "Analyze ONLY the submission content actually provided below. "
        "Never invent file names, code, test results, or evidence. "
        "If the submission states that no work evidence was submitted, return changes_required "
        "explaining that implementation evidence is missing and listing exactly what must be provided "
        "(files, code, or a written response covering each acceptance criterion).",
        "For architecture, design, and planning tasks, evaluate the submitted technical specification against the acceptance criteria; do not require executable SQL or source code unless a criterion explicitly requires implementation. Markdown and intentionally commented specification text (including SQL block comments) are valid design-document formats. Do not reject a design solely because it is not executable.",
        "Inspect the complete submitted content from its first line through its final line. Do not infer that later content is missing unless it is absent from the provided submission. For code, inspect complete model definitions, service methods, API routes, tests, and the final lines; when relevant, verify receive_stock, allocate_stock, deduct_stock, release_stock, ship_stock, and adjust_stock.",
        "Judge this submission against the specific task, sprint objective, project objectives, and deliverables below. Generic terminology, length, formatting, or a well-written document do not satisfy a requirement unless the submission contains task-specific evidence. Assess every requirement separately and do not fabricate implementation or test evidence.",
        f"TASK ID: {context.get('task_id', 'unknown')}",
        f"TASK: {task_title}",
    ]
    if not has_evidence:
        prompt_parts.append("EVIDENCE STATUS: none provided — do not claim any code was analyzed.")
    if task_description:
        prompt_parts.append(f"TASK INSTRUCTIONS (complete): {task_description}")
    if context.get("sprint_id"):
        prompt_parts.append(f"SPRINT ID: {context['sprint_id']}")
    if context.get("sprint_goal"):
        prompt_parts.append(f"SPRINT OBJECTIVE: {context['sprint_goal']}")
    if context.get("project_title"):
        prompt_parts.append(f"PROJECT: {context['project_title']}")
    if context.get("project_objectives"):
        prompt_parts.append("PROJECT OBJECTIVES:\n" + "\n".join(f"- {item}" for item in context["project_objectives"]))
    if context.get("expected_deliverables"):
        prompt_parts.append("EXPECTED DELIVERABLES:\n" + "\n".join(f"- {item}" for item in context["expected_deliverables"]))
    if context.get("task_instructions"):
        prompt_parts.append("TASK-SPECIFIC INSTRUCTIONS:\n" + "\n".join(f"- {item}" for item in context["task_instructions"]))
    similarity = context.get("cross_task_similarity") or {}
    if similarity.get("matches"):
        prompt_parts.append(
            "SIMILARITY ALERT: This source is substantially identical to prior work for another task: "
            + ", ".join(similarity["matches"])
            + ". This is a request for extra task-specific scrutiny, not automatic rejection. Verify that this submission independently satisfies this task's requirements."
        )
    if stack:
        prompt_parts.append("TECHNOLOGY: " + ", ".join(str(item) for item in stack[:6]))
    if acceptance_criteria:
        prompt_parts.append("ACCEPTANCE CRITERIA:\n" + "\n".join("- " + str(c) for c in acceptance_criteria))
    if filenames:
        prompt_parts.append("SUBMITTED FILES: " + ", ".join(str(name) for name in filenames[:20]))
    if test_results:
        prompt_parts.append("STUDENT-PROVIDED TEST / VERIFICATION RESULTS (unverified claims; inspect against submission):\n" + test_results)
    prompt_parts.extend([
        "SUBMISSION:",
        submission_content,
        "",
    ])
    
    prompt_parts.extend([
        "REQUIRED OUTPUT FORMAT (JSON - must match exactly):",
        "{",
        '  "result": "approved|changes_required|rejected",',
        '  "score": 85,',
        '  "summary": "Brief overall assessment (2-3 sentences)",',
        '  "strengths": ["List of things done well"],',
        '  "issues": ["List of concerns or issues found"],',
        '  "required_changes": ["Concrete change: name the exact file/function/behavior to add or modify, which acceptance criterion it unblocks, and how to verify it before resubmitting"],',
        '  "acceptance_criteria": [',
        '    {',
        '      "criterion": "criterion text",',
        '      "met": true,',
        '      "evidence": "quote or cite the exact submission content examined for this criterion"',
        '      "score": 100,',
        '      "missing_elements": []',
        '    }',
        '  ],',
        '  "code_quality": {',
        '    "score": 85,',
        '    "feedback": "Code quality assessment"',
        '  },',
        '  "testing": {',
        '    "score": 80,',
        '    "feedback": "Testing assessment"',
        '  },',
        '  "security": {',
        '    "score": 90,',
        '    "feedback": "Security assessment"',
        '  },',
        '  "performance": {',
        '    "score": 85,',
        '    "feedback": "Performance assessment"',
        '  }',
        "}",
        "",
        "RULES:",
        "- result must be one of: approved, changes_required, rejected",
        "- score 0-100: 90-100=excellent, 75-89=good, 60-74=needs work, <60=poor",
        "- approved = meets all acceptance criteria",
        "- changes_required = minor issues needing fixes",
        "- rejected = major issues requiring significant rework",
        "- Each acceptance criterion must have met=true/false and evidence",
        "- For each acceptance criterion include a separate 0-100 requirement score and a missing_elements list; score 100 only when the criterion is demonstrably satisfied, otherwise score below 100 and name the missing or incorrect elements.",
        "- An unrelated submission must not be approved even when technically polished. If none of the task-specific requirements are addressed, reject or request changes and cite the mismatch.",
        "- When a similarity alert is present, require specific evidence tying this submission to this task's own named behaviors, deliverables, or system; do not count shared boilerplate as coverage. Reusable components are acceptable only when the task-specific work is also demonstrated.",
        "- A claimed test run is not proof of execution; only report execution results when they are present in verifiable submitted evidence.",
        "- Evaluate every acceptance criterion exactly once, preserving its exact text. All mandatory criteria must be met for approved.",
        "- When result is changes_required: every required_changes item must state (1) what is missing or incorrect, (2) which requirement was not satisfied, (3) the exact code/functionality to add or modify, and (4) what to test before resubmitting. Never return vague advice.",
        "- testing.feedback must name the concrete tests or checks the resubmission needs (unit tests, edge cases, manual verification steps).",
        "- All fields are required; use empty arrays/strings if not applicable",
        "- Findings and evidence must reference content that appears in the complete submission provided above; note any unavailable/truncated evidence explicitly.",
        "- strengths and issues: at most 3 short items each; required_changes: at most 5 concrete items",
        "- feedback fields: one short sentence",
        "- Output ONLY valid JSON, no markdown, no reasoning",
    ])
    
    return "\n".join(prompt_parts)


def build_compact_review_prompt(context: Dict[str, Any]) -> str:
    title = str(context.get("task_title") or "Task")[:120]
    submission = str(context.get("submission_content") or "")
    return (
        "Return only the review JSON schema from the previous instruction. No reasoning. "
        f"Task: {title}. Submission: {submission}"
    )
