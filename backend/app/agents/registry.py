"""Explicitly-owned registry for specialist agents."""
from typing import Dict, List

from app.agents.base_agent import BaseAgent


class AgentRegistryError(Exception):
    pass


class DuplicateAgentError(AgentRegistryError):
    pass


class UnknownAgentError(AgentRegistryError):
    pass


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: Dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        agent_id = getattr(agent, "agent_id", None)
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("Agent must define a non-empty agent_id")
        if agent_id in self._agents:
            raise DuplicateAgentError(f"Agent '{agent_id}' is already registered")
        self._agents[agent_id] = agent

    def get(self, agent_id: str) -> BaseAgent:
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise UnknownAgentError(f"Agent '{agent_id}' is not registered") from exc

    def registered_ids(self) -> List[str]:
        return sorted(self._agents)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._agents
