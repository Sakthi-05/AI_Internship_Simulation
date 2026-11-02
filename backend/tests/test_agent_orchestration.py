"""Focused tests for provider-independent agent orchestration."""
import asyncio
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from app.agents import AgentOrchestrator, AgentRegistry, AgentRequest, ExecutionStatus
from app.agents.base_agent import BaseAgent
from app.agents.registry import DuplicateAgentError, UnknownAgentError
from app.services.ai.ai_service import get_ai_service


async def _immediate_wait_for(awaitable, timeout):
    """Run immediate fake-agent coroutines without creating a platform event loop."""
    if timeout < 0.1:
        awaitable.close()
        raise asyncio.TimeoutError
    return await awaitable


def _run_immediate(coroutine):
    """Drive a coroutine whose fake collaborators do not suspend on I/O."""
    try:
        return coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    finally:
        coroutine.close()


@pytest.fixture(autouse=True)
def _avoid_platform_socketpair(monkeypatch):
    # Python 3.12 on this restricted Windows runner falls back to a loopback TCP
    # socketpair; its accept() blocks. These unit tests use only immediate fakes.
    monkeypatch.setattr(asyncio, "wait_for", _immediate_wait_for)


class Input(BaseModel):
    value: int


class Output(BaseModel):
    value: int


class FakeAgent(BaseAgent):
    agent_id = "fake"
    description = "Test agent"
    responsibility = "Return input"
    input_model = Input
    output_model = Output

    def __init__(self, outcome=None):
        self.outcome = outcome
        self.context_seen = None

    async def execute(self, payload, context=None):
        self.context_seen = context
        if isinstance(self.outcome, Exception):
            raise self.outcome
        if self.outcome == "wait":
            await asyncio.sleep(1)
        if self.outcome == "bad_output":
            return {"value": "invalid"}
        return self.outcome if self.outcome is not None else {"value": payload.value + 1}


def test_registry_registration_retrieval_isolation_and_duplicates():
    registry = AgentRegistry()
    agent = FakeAgent()
    registry.register(agent)
    assert registry.get("fake") is agent
    assert registry.registered_ids() == ["fake"]
    with pytest.raises(DuplicateAgentError):
        registry.register(agent)
    assert AgentRegistry().registered_ids() == []


def test_registry_unknown_agent():
    with pytest.raises(UnknownAgentError, match="missing"):
        AgentRegistry().get("missing")


def test_orchestrator_invokes_agent_and_propagates_correlation_id():
    registry = AgentRegistry()
    agent = FakeAgent()
    registry.register(agent)
    correlation_id = uuid4()
    response = _run_immediate(AgentOrchestrator(registry).execute(
        AgentRequest(agent_id="fake", payload={"value": 4}, correlation_id=correlation_id)
    ))
    assert response.status is ExecutionStatus.SUCCEEDED
    assert response.result == {"value": 5}
    assert response.correlation_id == correlation_id


def test_request_contract_rejects_invalid_request():
    with pytest.raises(ValidationError):
        AgentRequest(agent_id="", timeout_seconds=0)


def test_orchestrator_rejects_invalid_input_and_output():
    registry = AgentRegistry()
    registry.register(FakeAgent())
    orchestrator = AgentOrchestrator(registry)
    invalid_input = _run_immediate(orchestrator.execute(AgentRequest(agent_id="fake", payload={"value": "x"})))
    assert invalid_input.status is ExecutionStatus.FAILED
    assert invalid_input.error.code == "validation_error"
    registry = AgentRegistry()
    registry.register(FakeAgent("bad_output"))
    invalid_output = _run_immediate(AgentOrchestrator(registry).execute(
        AgentRequest(agent_id="fake", payload={"value": 1})
    ))
    assert invalid_output.error.code == "validation_error"


def test_agent_failure_and_timeout_are_safe():
    registry = AgentRegistry()
    registry.register(FakeAgent(RuntimeError("secret provider detail")))
    failed = _run_immediate(AgentOrchestrator(registry).execute(AgentRequest(agent_id="fake", payload={"value": 1})))
    assert failed.status is ExecutionStatus.FAILED
    assert "secret" not in failed.error.message
    registry = AgentRegistry()
    registry.register(FakeAgent("wait"))
    timed_out = _run_immediate(AgentOrchestrator(registry).execute(
        AgentRequest(agent_id="fake", payload={"value": 1}, timeout_seconds=0.01)
    ))
    assert timed_out.status is ExecutionStatus.TIMED_OUT
    assert timed_out.error.code == "agent_timeout"


def test_unknown_agent_is_safe_response():
    response = _run_immediate(AgentOrchestrator(AgentRegistry()).execute(
        AgentRequest(agent_id="missing")
    ))
    assert response.error.code == "unknown_agent"


def test_sequential_execution_preserves_order_and_failure_handling():
    class NamedAgent(FakeAgent):
        def __init__(self, name):
            super().__init__()
            self.agent_id = name

        async def execute(self, payload, context=None):
            return {"value": payload.value}

    registry = AgentRegistry()
    registry.register(NamedAgent("first"))
    registry.register(NamedAgent("second"))
    responses = _run_immediate(AgentOrchestrator(registry).execute_sequence([
        AgentRequest(agent_id="second", payload={"value": 2}),
        AgentRequest(agent_id="first", payload={"value": 1}),
    ]))
    assert [response.result["value"] for response in responses] == [2, 1]
    registry.register(NamedAgent("third"))
    failed_then_succeeded = _run_immediate(AgentOrchestrator(registry).execute_sequence([
        AgentRequest(agent_id="missing"),
        AgentRequest(agent_id="third", payload={"value": 3}),
    ]))
    assert failed_then_succeeded[0].status is ExecutionStatus.FAILED
    assert failed_then_succeeded[1].result == {"value": 3}


def test_existing_mock_provider_remains_available_to_agents(monkeypatch):
    import app.services.ai.ai_service as ai_service

    monkeypatch.setattr(ai_service.settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(ai_service, "_MOCK_AI_SERVICE", None)
    provider = get_ai_service()
    assert provider.__class__.__name__ == "MockAIService"
    assert _run_immediate(provider.generate_text("test prompt"))
