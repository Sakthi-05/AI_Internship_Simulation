"""FastAPI dependency for the application-owned agent orchestrator."""
from fastapi import Request

from app.agents.orchestrator import AgentOrchestrator


def get_agent_orchestrator(request: Request) -> AgentOrchestrator:
    orchestrator = getattr(request.app.state, "agent_orchestrator", None)
    if orchestrator is None:
        raise RuntimeError("Application agent orchestrator is not configured")
    return orchestrator
