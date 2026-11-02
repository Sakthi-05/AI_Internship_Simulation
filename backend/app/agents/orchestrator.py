"""Bounded coordinator for registered agents; owns no business persistence."""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Iterable, List

from pydantic import ValidationError

from app.agents.base_agent import output_mapping
from app.agents.contracts import AgentErrorInfo, AgentRequest, AgentResponse, ExecutionMetadata, ExecutionStatus
from app.agents.registry import AgentRegistry, UnknownAgentError

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    def __init__(self, registry: AgentRegistry):
        self.registry = registry

    async def execute(self, request: AgentRequest) -> AgentResponse:
        started = datetime.now(timezone.utc)
        tick = time.perf_counter()
        metadata = ExecutionMetadata(agent_id=request.agent_id, started_at=started)
        try:
            agent = self.registry.get(request.agent_id)
            validated_input = agent.input_model.model_validate(request.payload)
            raw_output = await asyncio.wait_for(
                agent.execute(validated_input, request.context), timeout=request.timeout_seconds
            )
            output = agent.output_model.model_validate(output_mapping(raw_output))
            result = output.model_dump(mode="json")
            status = ExecutionStatus.SUCCEEDED
            error = None
        except asyncio.TimeoutError:
            status, result = ExecutionStatus.TIMED_OUT, None
            error = AgentErrorInfo(code="agent_timeout", message="Agent execution timed out")
            logger.warning("Agent handoff failed agent_id=%s correlation_id=%s code=%s",
                           request.agent_id, request.correlation_id, error.code)
        except UnknownAgentError:
            status, result = ExecutionStatus.FAILED, None
            error = AgentErrorInfo(code="unknown_agent", message="Requested agent is not registered")
            logger.warning("Agent handoff failed agent_id=%s correlation_id=%s code=%s",
                           request.agent_id, request.correlation_id, error.code)
        except ValidationError:
            status, result = ExecutionStatus.FAILED, None
            error = AgentErrorInfo(code="validation_error", message="Agent input or output validation failed")
            logger.warning("Agent handoff failed agent_id=%s correlation_id=%s code=%s",
                           request.agent_id, request.correlation_id, error.code)
        except Exception as exc:
            status, result = ExecutionStatus.FAILED, None
            # Exception text may contain prompts, credentials, or provider internals.
            error = AgentErrorInfo(code="agent_execution_failed", message="Agent execution failed")
            logger.exception("Agent execution failed agent_id=%s correlation_id=%s error_type=%s code=%s",
                             request.agent_id, request.correlation_id, type(exc).__name__, error.code)

        metadata.completed_at = datetime.now(timezone.utc)
        metadata.duration_ms = (time.perf_counter() - tick) * 1000
        logger.info("Agent execution finished agent_id=%s correlation_id=%s status=%s duration_ms=%.2f",
                    request.agent_id, request.correlation_id, status.value, metadata.duration_ms)
        return AgentResponse(correlation_id=request.correlation_id, status=status,
                             result=result, error=error, metadata=metadata)

    async def execute_sequence(self, requests: Iterable[AgentRequest]) -> List[AgentResponse]:
        """Run each requested agent in order, returning every bounded outcome."""
        responses = []
        for request in requests:
            responses.append(await self.execute(request))
        return responses
