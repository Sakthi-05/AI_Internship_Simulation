from __future__ import annotations
from typing import Any, Dict


def build_review_prompt(context: Dict[str, Any]) -> str:
    task_title = context.get('task_title', 'Unknown Task')
    task_description = (context.get('task_description') or '')[:500]
    acceptance_criteria = context.get('acceptance_criteria', [])[:8]
    submission_content = (context.get('submission_content') or 'No submission content available.')[:3500]
    stack = context.get('technology_stack') or []
    previous_review = (context.get('previous_review') or '')[:300]

    prompt_parts = [
        "Review this submission. Return JSON only. No reasoning.",
        f"TASK: {task_title}",
    ]
    if task_description:
        prompt_parts.append(f"DESCRIPTION: {task_description}")
    if stack:
        prompt_parts.append("TECHNOLOGY: " + ", ".join(str(item) for item in stack[:6]))
    if acceptance_criteria:
        prompt_parts.append("ACCEPTANCE CRITERIA:\n" + "\n".join("- " + str(c) for c in acceptance_criteria))
    if previous_review:
        prompt_parts.append(f"PREVIOUS REVIEW: {previous_review}")
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
        '  "required_changes": ["Specific changes needed"],',
        '  "acceptance_criteria": [',
        '    {',
        '      "criterion": "criterion text",',
        '      "met": true,',
        '      "evidence": "evidence from submission"',
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
        "- All fields are required; use empty arrays/strings if not applicable",
        "- strengths, issues, and required_changes: at most 3 short items each",
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