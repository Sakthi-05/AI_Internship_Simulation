import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from app.models.enums import TaskPriority, TaskStatus


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sprint_id: uuid.UUID
    sequence: int = 0
    title: str
    description: str
    acceptance_criteria: List[str]
    priority: TaskPriority
    status: TaskStatus
    estimated_hours: float
    deadline: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    blocked_reason: Optional[str] = None
    deliverable_url: Optional[str] = None
    depends_on_task_ids: List[uuid.UUID] = []
    task_metadata: Optional[dict] = None
    # Enhanced fields for dynamic workflow
    hints: List[str] = []
    recommended_team_role: Optional[str] = None
    generation_reason: Optional[str] = None
    performance_trend_at_generation: Optional[str] = None
    dependency_details: List[dict] = []
    criteria_version: Optional[dict] = None


class TaskStatusUpdate(BaseModel):
    status: TaskStatus
    blocked_reason: Optional[str] = None
    deliverable_url: Optional[str] = None
