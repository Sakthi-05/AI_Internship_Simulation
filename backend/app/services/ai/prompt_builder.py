from __future__ import annotations
from typing import Any, Dict


def build_review_prompt(context: Dict[str, Any]) -> str:
    task_title = context.get('task_title', 'Unknown Task')
    task_description = (context.get('task_description') or '')[:500]
    acceptance_criteria = context.get('acceptance_criteria', [])[:8]
    submission_content = (context.get('submission_content') or 'No submission content available.')[:12000]
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
        f"TASK: {task_title}",
    ]
    if not has_evidence:
        prompt_parts.append("EVIDENCE STATUS: none provided — do not claim any code was analyzed.")
    if task_description:
        prompt_parts.append(f"DESCRIPTION: {task_description}")
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
    submission = str(context.get("submission_content") or "")[:800]
    return (
        "Return only the review JSON schema from the previous instruction. No reasoning. "
        f"Task: {title}. Submission: {submission}"
    )
