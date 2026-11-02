"""Authoritative persistence path for manager replies in the shared chat."""
from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy.orm import Session

from app.models.manager import Manager
from app.models.enums import MessageSenderType
from app.models.message import Message
from app.models.project import Project
from app.models.project_state import ProjectState
from app.services.ai.ai_manager import generate_manager_reply
from app.services.ai.ai_service import AIService


async def create_manager_response(
    db: Session,
    ai_service: AIService,
    manager: Manager,
    project: Project,
    state: ProjectState | None,
    history: List[Message],
    student_message: str,
    workplace_context: str,
) -> Message:
    """Generate one in-context manager reply and persist it in chat history."""
    content = await generate_manager_reply(
        ai_service, manager, project, state, history, student_message,
        workplace_context=workplace_context,
    )
    return persist_manager_response(db, manager, history, content)


def persist_manager_response(
    db: Session,
    manager: Manager,
    history: List[Message],
    content: str,
) -> Message:
    """Persist generated manager text through the established chat record path."""
    message = Message(
        company_id=manager.company_id,
        sender_type=MessageSenderType.MANAGER,
        sender_id=str(manager.id),
        content=content,
        created_at=max(
            datetime.now(timezone.utc),
            (history[-1].created_at + timedelta(microseconds=1))
            if history and history[-1].created_at
            else datetime.now(timezone.utc),
        ),
    )
    db.add(message)
    db.flush()
    return message
