"""Optional live Gemini check. Skipped unless GEMINI_API_KEY is set."""
import os

import pytest

from app.services.ai.ai_service import get_ai_service


@pytest.mark.skipif(not os.getenv("GEMINI_API_KEY"), reason="GEMINI_API_KEY is not set")
@pytest.mark.asyncio
async def test_gemini_returns_json_object():
    service = get_ai_service()
    payload = await service.generate_json(
        '{"role":"Backend Developer","task":"Resolve a failing API test","student_response":"I reproduce the failure, check the request, and add a regression test.","target_skills":["problem_solving"]}',
        "Return JSON with overall_score and feedback. No reasoning.",
    )
    assert isinstance(payload, dict)
    assert payload
