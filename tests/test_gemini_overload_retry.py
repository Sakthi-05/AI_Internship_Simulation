"""Gemini 503 handling: one quick retry, then a propagatable overload error.

Uses a fake httpx client — no network, no API key needed.
"""
import httpx
import pytest

from app.services.ai import ai_service
from app.services.ai.ai_service import GeminiAIService, is_transient_ai_overload


def _resp(status_code, payload=None):
    request = httpx.Request("POST", "https://generativelanguage.googleapis.com/test")
    return httpx.Response(status_code, json=payload or {}, request=request)


def _ok_payload(text="hello"):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class _FakeClient:
    def __init__(self, responses, calls):
        self._responses = responses
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        idx = min(len(self._calls), len(self._responses) - 1)
        self._calls.append(args)
        return self._responses[idx]


@pytest.fixture
def _patch_sleep(monkeypatch):
    delays = []

    async def _fast_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(ai_service.asyncio, "sleep", _fast_sleep)
    return delays


def _patch_client(monkeypatch, responses):
    calls = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(responses, calls))
    return calls


def _service():
    return GeminiAIService(api_key="test-key", model="test-model")


@pytest.mark.asyncio
async def test_503_then_success_returns_text_with_one_retry(monkeypatch, _patch_sleep):
    calls = _patch_client(monkeypatch, [_resp(503), _resp(200, _ok_payload("recovered"))])
    text = await _service()._call("prompt", None)
    assert text == "recovered"
    assert len(calls) == 2
    assert _patch_sleep == [pytest.approx(1.0)]


@pytest.mark.asyncio
async def test_persistent_503_raises_after_retry(monkeypatch, _patch_sleep):
    calls = _patch_client(monkeypatch, [_resp(503), _resp(503)])
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await _service()._call("prompt", None)
    assert exc_info.value.response.status_code == 503
    assert len(calls) == 2
    assert len(_patch_sleep) == 1


@pytest.mark.asyncio
async def test_non_503_error_raises_immediately_without_retry(monkeypatch, _patch_sleep):
    calls = _patch_client(monkeypatch, [_resp(400)])
    with pytest.raises(httpx.HTTPStatusError):
        await _service()._call("prompt", None)
    assert len(calls) == 1
    assert _patch_sleep == []


def test_is_transient_ai_overload():
    assert is_transient_ai_overload(_wrap(_resp(503))) is True
    assert is_transient_ai_overload(_wrap(_resp(429))) is False
    assert is_transient_ai_overload(_wrap(_resp(500))) is False
    assert is_transient_ai_overload(ValueError("boom")) is False
    assert is_transient_ai_overload(None) is False


def _wrap(response):
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError("expected an HTTPStatusError")
