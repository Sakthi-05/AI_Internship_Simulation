"""Base contract for deterministic and provider-backed specialist agents."""
from abc import ABC, abstractmethod
from typing import Any, Dict, Type

from pydantic import BaseModel

from app.agents.contracts import AgentContext


class BaseAgent(ABC):
    agent_id: str
    description: str
    responsibility: str
    input_model: Type[BaseModel]
    output_model: Type[BaseModel]

    @abstractmethod
    async def execute(self, payload: BaseModel, context: AgentContext | None = None) -> Any:
        """Execute the agent's bounded responsibility and return its output model."""
        raise NotImplementedError


def output_mapping(value: Any) -> Dict[str, Any]:
    """Convert a validated Pydantic output (or mapping) into contract data."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    raise TypeError("Agent output must be a Pydantic model or mapping")
