"""Read schemas for Notification, Event, ActivityLog, MeetingSchedule — the Phase 3 read surfaces."""
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import EventType, MeetingType, NotificationType


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    company_id: uuid.UUID
    simulation_id: Optional[uuid.UUID] = None
    context_json: dict = Field(default_factory=dict)
    notification_type: NotificationType
    message: str
    is_read: bool
    created_at: datetime


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    event_type: EventType
    description: str
    payload: dict
    created_at: datetime


class ActivityLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    company_id: uuid.UUID
    actor: str
    action: str
    detail: Optional[str] = None
    created_at: datetime


class MeetingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    simulation_id: Optional[uuid.UUID] = None
    meeting_type: MeetingType
    scheduled_at: Optional[datetime] = None
    agenda: str
    participants: List[str]
    notes: Optional[str] = None
    attendance: List[str]
    action_items: List[str]
    completed: bool
    student_response: Optional[str] = None


class MeetingComplete(BaseModel):
    notes: Optional[str] = None
    attendance: List[str] = Field(default_factory=list)
    action_items: List[str] = Field(default_factory=list)


class MeetingCreate(BaseModel):
    meeting_type: MeetingType
    scheduled_at: datetime
    agenda: str
    participants: List[str] = Field(default_factory=list)
    idempotency_key: Optional[str] = None


class MeetingUpdate(BaseModel):
    scheduled_at: Optional[datetime] = None
    agenda: Optional[str] = None
    participants: Optional[List[str]] = None


class MeetingResponse(BaseModel):
    response: str = Field(min_length=1, max_length=4000)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=120)


class TimelineEntry(BaseModel):
    kind: str  # "task_completed" | "event" | "meeting" | "message"
    title: str
    detail: Optional[str] = None
    timestamp: datetime
