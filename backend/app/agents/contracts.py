"""Provider-independent contracts for agent execution."""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class AgentErrorInfo(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ExecutionMetadata(BaseModel):
    agent_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    duration_ms: Optional[float] = Field(default=None, ge=0)


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=100)
    payload: Dict[str, Any] = Field(default_factory=dict)
    correlation_id: UUID = Field(default_factory=uuid4)
    simulation_id: Optional[UUID] = None
    context: Optional["AgentContext"] = None
    timeout_seconds: float = Field(default=30, gt=0, le=300)


class AgentResponse(BaseModel):
    correlation_id: UUID
    status: ExecutionStatus
    result: Optional[Dict[str, Any]] = None
    error: Optional[AgentErrorInfo] = None
    metadata: ExecutionMetadata


class AgentContext(BaseModel):
    """Execution-time references and bounded, role-filtered simulation context."""
    model_config = ConfigDict(extra="forbid")

    simulation_id: Optional[UUID] = None
    student_id: Optional[str] = None
    company_id: Optional[UUID] = None
    task_id: Optional[UUID] = None
    current_task_id: Optional[UUID] = None
    submission_id: Optional[UUID] = None
    criteria_version_id: Optional[UUID] = None
    workflow_event_id: Optional[UUID] = None
    profile: Optional[str] = None
    current_task: Optional[Dict[str, Any]] = None
    scenario_history: list[Dict[str, Any]] = Field(default_factory=list)
    current_company: Optional[Dict[str, Any]] = None
    current_project: Optional[Dict[str, Any]] = None
    current_sprint: Optional[Dict[str, Any]] = None
    manager_profile: Optional[Dict[str, Any]] = None
    project_state: Optional[Dict[str, Any]] = None
    active_criteria: Optional[Dict[str, Any]] = None
    previous_criteria: list[Dict[str, Any]] = Field(default_factory=list)
    submission_history: list[Dict[str, Any]] = Field(default_factory=list)
    submission_content: Optional[str] = None
    submission_evidence_level: Optional[str] = None
    technical_reviews: list[Dict[str, Any]] = Field(default_factory=list)
    workplace_events: list[Dict[str, Any]] = Field(default_factory=list)
    manager_communications: list[Dict[str, Any]] = Field(default_factory=list)
    competencies: Optional[Dict[str, Any]] = None
    assessment_history: list[Dict[str, Any]] = Field(default_factory=list)
    interaction_history: list[Dict[str, Any]] = Field(default_factory=list)
    progression_history: list[Dict[str, Any]] = Field(default_factory=list)
    simulation_outcomes: list[Dict[str, Any]] = Field(default_factory=list)
    evidence: list["EvidenceReference"] = Field(default_factory=list)


class EvidenceReference(BaseModel):
    """Pointer to persisted evidence; references never assert a score themselves."""
    evidence_type: str
    evidence_record_id: UUID
    student_id: str
    simulation_id: Optional[UUID] = None
    task_id: Optional[UUID] = None
    submission_id: Optional[UUID] = None
    submission_version_id: Optional[UUID] = None
    workplace_event_id: Optional[UUID] = None
    criteria_version_id: Optional[UUID] = None
    occurred_at: Optional[datetime] = None
    source: str


AgentRequest.model_rebuild()
AgentContext.model_rebuild()
