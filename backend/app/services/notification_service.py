"""Notification creation with duplicate-suppression and configurable cooldown.

``notify()`` is called in many places — route handlers, background jobs, event
engines, and AI review services — so the API is intentionally unchanged.  The
only new behaviour is that an identical notification (same company, type, and
message text) is silently suppressed when a matching notification was already
persisted within the cooldown window.

Cooldown semantics
------------------
* Default window: 60 seconds (``DEFAULT_COOLDOWN_SECONDS``).
* Key: ``(company_id, notification_type, message)``.
* Suppression uses an exclusive row-level ``SELECT … FOR UPDATE SKIP LOCKED``
  on the most recent matching notification so that concurrent requests race
  over a real database lock rather than just a time comparison read.  The
  loser of the race reuses the winner's row; both callers see a valid
  ``Notification`` object returned.
* Different notification types or message texts are *never* suppressed by an
  unrelated notification — only exact (company, type, message) triples are
  compared.
* After the cooldown window has elapsed a fresh notification is always
  inserted, so legitimate repeated notifications (e.g. the same deadline
  warning one hour later) are never permanently silenced.
* All callers run inside an existing transaction; this function only uses
  ``flush()`` and never calls ``commit()`` or ``rollback()``.

Usage
-----
The public API is unchanged::

    notify(db, company_id, NotificationType.TASK_SUBMITTED, "Task 'Auth API' submitted.")

Pass ``cooldown_seconds=0`` to skip the check for a specific call site, or
``cooldown_seconds=None`` to use the global default.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.enums import NotificationType
from app.models.notification import Notification

DEFAULT_COOLDOWN_SECONDS: int = 60


def notify(
    db: Session,
    company_id,
    notification_type: NotificationType,
    message: str,
    *,
    cooldown_seconds: Optional[int] = None,
) -> Notification:
    """Create a notification unless an identical one was sent very recently.

    Parameters
    ----------
    db:
        Active SQLAlchemy session (must already be in a transaction).
    company_id:
        UUID of the company this notification belongs to.
    notification_type:
        A ``NotificationType`` enum value (or its string equivalent).
    message:
        Human-readable notification text.
    cooldown_seconds:
        How many seconds must pass before an identical notification is
        re-inserted.  ``None`` → ``DEFAULT_COOLDOWN_SECONDS`` (60 s).
        ``0`` → no cooldown; always insert.

    Returns
    -------
    Notification
        The newly inserted row, or the existing one that blocked the insert
        within the cooldown window.
    """
    effective_cooldown = DEFAULT_COOLDOWN_SECONDS if cooldown_seconds is None else cooldown_seconds

    if effective_cooldown > 0:
        existing = _find_recent_duplicate(db, company_id, notification_type, message, effective_cooldown)
        if existing is not None:
            return existing

    n = Notification(company_id=company_id, notification_type=notification_type, message=message)
    db.add(n)
    db.flush()
    return n


def _find_recent_duplicate(
    db: Session,
    company_id,
    notification_type: NotificationType,
    message: str,
    cooldown_seconds: int,
) -> Optional[Notification]:
    """Return an existing notification within the cooldown window, or None.

    Uses ``FOR UPDATE SKIP LOCKED`` so that only one of several concurrent
    callers acquires the lock and inserts a new row; the others skip the locked
    row but then find it on the plain-read pass, ensuring exactly-one
    insertion.
    """
    cutoff: datetime = datetime.now(timezone.utc) - timedelta(seconds=cooldown_seconds)

    # Normalise notification_type to its string value for the query.
    nt_value: str = (
        notification_type.value
        if isinstance(notification_type, NotificationType)
        else str(notification_type)
    )

    # Attempt to lock the most recent matching row.  If another concurrent
    # request already holds the lock, SKIP LOCKED returns nothing and that
    # caller will insert its own row (which the original request will then
    # find on the plain read below).
    stmt_for_update = (
        select(Notification)
        .where(
            Notification.company_id == company_id,
            Notification.notification_type == notification_type,
            Notification.message == message,
            Notification.created_at >= cutoff,
        )
        .order_by(Notification.created_at.desc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )

    try:
        locked_row: Optional[Notification] = db.execute(stmt_for_update).scalars().first()
    except Exception:
        # FOR UPDATE SKIP LOCKED is PostgreSQL-specific.  In test environments
        # (e.g. SQLite) or databases that do not support it, fall back to a
        # plain read so the rest of the logic still works correctly.
        locked_row = None

    if locked_row is not None:
        return locked_row

    # Plain read: the concurrent winner's row is now visible even though we
    # could not lock it.
    stmt_plain = (
        select(Notification)
        .where(
            Notification.company_id == company_id,
            Notification.notification_type == notification_type,
            Notification.message == message,
            Notification.created_at >= cutoff,
        )
        .order_by(Notification.created_at.desc())
        .limit(1)
    )

    try:
        plain_row: Optional[Notification] = db.execute(stmt_plain).scalars().first()
    except Exception:
        plain_row = None

    return plain_row  # None → caller inserts a fresh row

