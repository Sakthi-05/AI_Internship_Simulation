"""FEATURE 12: Notifications — persisted, contextual, retry-safe notices."""
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import NotificationType
from app.models.notification import Notification
from app.models.simulation import Simulation


def notify(
    db: Session,
    company_id,
    notification_type: NotificationType,
    message: str,
    *,
    simulation_id=None,
    context: Optional[dict[str, Any]] = None,
    dedupe_key: Optional[str] = None,
) -> Notification:
    """Create one notification or return the existing row on a retried action."""
    if simulation_id is None:
        simulation_id = db.execute(select(Simulation.id).where(
            Simulation.company_id == company_id,
        ).order_by(Simulation.created_at.desc()).limit(1)).scalar_one_or_none()
    if dedupe_key:
        existing = db.execute(select(Notification).where(
            Notification.company_id == company_id,
            Notification.dedupe_key == dedupe_key,
        )).scalar_one_or_none()
        if existing is not None:
            return existing

    notification = Notification(
        company_id=company_id,
        simulation_id=simulation_id,
        notification_type=notification_type,
        message=message,
        context_json=context or {},
        dedupe_key=dedupe_key,
    )
    if not dedupe_key:
        db.add(notification)
        db.flush()
        return notification

    try:
        with db.begin_nested():
            db.add(notification)
            db.flush()
        return notification
    except IntegrityError:
        existing = db.execute(select(Notification).where(
            Notification.company_id == company_id,
            Notification.dedupe_key == dedupe_key,
        )).scalar_one_or_none()
        if existing is None:
            raise
        return existing
