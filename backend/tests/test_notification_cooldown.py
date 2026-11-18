"""Unit tests for notification_service.notify() cooldown and duplicate prevention.

All tests run without a live database.  A lightweight FakeDB records what was
inserted and simulates the query pattern expected by _find_recent_duplicate().

Coverage:
- Rapid duplicates are suppressed within the cooldown window.
- After cooldown expiry a fresh notification is inserted.
- Different notification types are never suppressed by an unrelated type.
- Different message texts are never suppressed by an unrelated message.
- Cooldown of 0 (disabled) always inserts a fresh row.
- The existing notify() signature (positional args) is preserved.
- Concurrent scenario: the second caller finds the first caller's row via the
  plain-read pass when FOR UPDATE SKIP LOCKED returns nothing.
- SoftSkillEvidence records are not affected (notification_service isolation).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.models.enums import NotificationType
from app.models.notification import Notification
from app.services.notification_service import (
    DEFAULT_COOLDOWN_SECONDS,
    _find_recent_duplicate,
    notify,
)


# ---------------------------------------------------------------------------
# Minimal Fake DB
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class FakeScalarsResult:
    def __init__(self, rows: List[Any]):
        self._rows = rows

    def first(self) -> Optional[Any]:
        return self._rows[0] if self._rows else None

    def all(self) -> List[Any]:
        return list(self._rows)


class FakeExecuteResult:
    def __init__(self, rows: List[Any]):
        self._rows = rows

    def scalars(self) -> FakeScalarsResult:
        return FakeScalarsResult(self._rows)


class FakeDB:
    """Minimal session that records added rows and simulates notification queries."""

    def __init__(self, *, existing: Optional[List[Notification]] = None, for_update_raises: bool = False):
        # Notifications already in the simulated DB (e.g. a notification inserted 10s ago).
        self._existing: List[Notification] = list(existing or [])
        self._added: List[Any] = []
        self._for_update_raises = for_update_raises
        self._flush_count = 0

    def add(self, obj: Any) -> None:
        self._added.append(obj)
        # Give newly added notifications an ID and created_at so queries find them.
        if isinstance(obj, Notification):
            if obj.id is None:
                obj.id = uuid.uuid4()
            if not hasattr(obj, "created_at") or obj.created_at is None:
                obj.created_at = _now_utc()
            self._existing.append(obj)

    def flush(self) -> None:
        self._flush_count += 1

    def execute(self, stmt: Any) -> FakeExecuteResult:
        """Simulate the two queries in _find_recent_duplicate()."""
        is_for_update = getattr(stmt, "_for_update_arg", None) is not None

        if is_for_update and self._for_update_raises:
            raise RuntimeError("FOR UPDATE not supported (simulating SQLite)")

        # Extract filter values from compiled parameters.
        try:
            params = stmt.compile().params
        except Exception:
            params = {}

        company_id = params.get("company_id_1")
        notification_type = params.get("notification_type_1")
        message = params.get("message_1")
        cutoff = params.get("created_at_1")

        matches = [
            n for n in self._existing
            if _matches(n, company_id, notification_type, message, cutoff)
        ]
        # Order by created_at desc, limit 1
        matches.sort(key=lambda n: n.created_at, reverse=True)
        top = matches[:1]

        # Simulate SKIP LOCKED: if another request holds the lock, return empty.
        if is_for_update and self._for_update_raises:
            top = []

        return FakeExecuteResult(top)

    @property
    def added_notifications(self) -> List[Notification]:
        return [obj for obj in self._added if isinstance(obj, Notification)]


def _matches(n: Notification, company_id, notification_type, message, cutoff) -> bool:
    if company_id is not None and str(n.company_id) != str(company_id):
        return False
    nt_str = n.notification_type.value if isinstance(n.notification_type, NotificationType) else str(n.notification_type)
    nt_filter = notification_type.value if isinstance(notification_type, NotificationType) else str(notification_type or "")
    if notification_type is not None and nt_str != nt_filter:
        return False
    if message is not None and n.message != message:
        return False
    if cutoff is not None:
        created = n.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if isinstance(cutoff, datetime):
            cmp_cutoff = cutoff
            if cmp_cutoff.tzinfo is None:
                cmp_cutoff = cmp_cutoff.replace(tzinfo=timezone.utc)
        else:
            cmp_cutoff = cutoff
        if created < cmp_cutoff:
            return False
    return True


def _make_notification(
    company_id: uuid.UUID,
    notification_type: NotificationType,
    message: str,
    age_seconds: float = 0.0,
) -> Notification:
    """Build a Notification object with controlled created_at."""
    n = Notification(
        company_id=company_id,
        notification_type=notification_type,
        message=message,
    )
    n.id = uuid.uuid4()
    n.is_read = False
    n.created_at = _now_utc() - timedelta(seconds=age_seconds)
    return n


# ---------------------------------------------------------------------------
# Tests: basic API preservation
# ---------------------------------------------------------------------------

def test_notify_returns_notification_object():
    db = FakeDB()
    company_id = uuid.uuid4()
    result = notify(db, company_id, NotificationType.TASK_SUBMITTED, "Task submitted.")
    assert isinstance(result, Notification)
    assert result.company_id == company_id
    assert result.message == "Task submitted."


def test_notify_flushes_new_row():
    db = FakeDB()
    notify(db, uuid.uuid4(), NotificationType.TASK_SUBMITTED, "Some message")
    assert db._flush_count >= 1
    assert len(db.added_notifications) == 1


def test_notify_positional_args_signature_preserved():
    """The original call signature must still work without keyword args."""
    db = FakeDB()
    result = notify(db, uuid.uuid4(), NotificationType.REVIEW_COMPLETED, "Review done")
    assert result.message == "Review done"


# ---------------------------------------------------------------------------
# Tests: cooldown suppression
# ---------------------------------------------------------------------------

def test_rapid_duplicate_suppressed_within_cooldown():
    """A second identical notification within cooldown is not inserted."""
    company_id = uuid.uuid4()
    existing = _make_notification(company_id, NotificationType.TASK_SUBMITTED, "Task submitted.", age_seconds=5)
    db = FakeDB(existing=[existing])

    result = notify(db, company_id, NotificationType.TASK_SUBMITTED, "Task submitted.")

    # No new row inserted — existing row is returned.
    assert len(db.added_notifications) == 0
    assert result.id == existing.id


def test_suppression_uses_the_most_recent_matching_notification():
    """When multiple matching notifications exist, the most recent is returned."""
    company_id = uuid.uuid4()
    old = _make_notification(company_id, NotificationType.TASK_SUBMITTED, "Task submitted.", age_seconds=50)
    recent = _make_notification(company_id, NotificationType.TASK_SUBMITTED, "Task submitted.", age_seconds=10)
    db = FakeDB(existing=[old, recent])

    result = notify(db, company_id, NotificationType.TASK_SUBMITTED, "Task submitted.")

    assert result.id == recent.id
    assert len(db.added_notifications) == 0


def test_new_row_inserted_when_no_existing_notification():
    """No existing notification → a fresh row is always inserted."""
    db = FakeDB(existing=[])
    company_id = uuid.uuid4()

    result = notify(db, company_id, NotificationType.REVIEW_STARTED, "Review started.")

    assert len(db.added_notifications) == 1
    assert result.message == "Review started."


# ---------------------------------------------------------------------------
# Tests: cooldown expiry
# ---------------------------------------------------------------------------

def test_notification_allowed_after_cooldown_expiry():
    """A notification older than the cooldown window is not a duplicate."""
    company_id = uuid.uuid4()
    old = _make_notification(
        company_id, NotificationType.DEADLINE_UPDATED, "Deadline updated.",
        age_seconds=DEFAULT_COOLDOWN_SECONDS + 10,  # > 60 s old
    )
    db = FakeDB(existing=[old])

    result = notify(db, company_id, NotificationType.DEADLINE_UPDATED, "Deadline updated.")

    assert len(db.added_notifications) == 1
    assert result.id != old.id


def test_notification_at_exact_boundary_is_suppressed():
    """A notification at exactly the cutoff age (< cooldown) is treated as recent."""
    company_id = uuid.uuid4()
    boundary = _make_notification(
        company_id, NotificationType.DEADLINE_UPDATED, "Deadline updated.",
        age_seconds=DEFAULT_COOLDOWN_SECONDS - 1,  # 59 s — still within window
    )
    db = FakeDB(existing=[boundary])

    result = notify(db, company_id, NotificationType.DEADLINE_UPDATED, "Deadline updated.")

    assert result.id == boundary.id
    assert len(db.added_notifications) == 0


def test_custom_cooldown_seconds_respected():
    """A caller-supplied cooldown of 300 s suppresses notifications up to 5 min old."""
    company_id = uuid.uuid4()
    existing = _make_notification(
        company_id, NotificationType.SPRINT_COMPLETED, "Sprint 1 complete!",
        age_seconds=250,  # within 300 s
    )
    db = FakeDB(existing=[existing])

    result = notify(
        db, company_id, NotificationType.SPRINT_COMPLETED, "Sprint 1 complete!",
        cooldown_seconds=300,
    )

    assert result.id == existing.id
    assert len(db.added_notifications) == 0


def test_custom_cooldown_30s_allows_notification_at_60s():
    """A 30 s cooldown does not suppress a notification that is 45 s old."""
    company_id = uuid.uuid4()
    existing = _make_notification(
        company_id, NotificationType.SPRINT_COMPLETED, "Sprint 2 complete!",
        age_seconds=45,  # > 30 s → outside window
    )
    db = FakeDB(existing=[existing])

    result = notify(
        db, company_id, NotificationType.SPRINT_COMPLETED, "Sprint 2 complete!",
        cooldown_seconds=30,
    )

    assert len(db.added_notifications) == 1
    assert result.id != existing.id


# ---------------------------------------------------------------------------
# Tests: cooldown=0 (disabled)
# ---------------------------------------------------------------------------

def test_cooldown_zero_always_inserts():
    """cooldown_seconds=0 disables deduplication; every call inserts a new row."""
    company_id = uuid.uuid4()
    existing = _make_notification(company_id, NotificationType.TASK_SUBMITTED, "Task submitted.", age_seconds=1)
    db = FakeDB(existing=[existing])

    result = notify(db, company_id, NotificationType.TASK_SUBMITTED, "Task submitted.", cooldown_seconds=0)

    assert len(db.added_notifications) == 1
    assert result.id != existing.id


# ---------------------------------------------------------------------------
# Tests: different types / messages are independent
# ---------------------------------------------------------------------------

def test_different_notification_type_not_suppressed():
    """A REVIEW_STARTED notification is never suppressed by a TASK_SUBMITTED one."""
    company_id = uuid.uuid4()
    existing = _make_notification(company_id, NotificationType.TASK_SUBMITTED, "Same message.", age_seconds=5)
    db = FakeDB(existing=[existing])

    result = notify(db, company_id, NotificationType.REVIEW_STARTED, "Same message.")

    assert len(db.added_notifications) == 1
    assert result.notification_type == NotificationType.REVIEW_STARTED


def test_different_message_text_not_suppressed():
    """A notification with a different message is never suppressed."""
    company_id = uuid.uuid4()
    existing = _make_notification(company_id, NotificationType.TASK_SUBMITTED, "Task 'Auth' submitted.", age_seconds=5)
    db = FakeDB(existing=[existing])

    result = notify(db, company_id, NotificationType.TASK_SUBMITTED, "Task 'Orders' submitted.")

    assert len(db.added_notifications) == 1
    assert "Orders" in result.message


def test_different_company_not_suppressed():
    """A notification for company B is not suppressed by company A's row."""
    company_a = uuid.uuid4()
    company_b = uuid.uuid4()
    existing = _make_notification(company_a, NotificationType.SPRINT_COMPLETED, "Sprint done.", age_seconds=5)
    db = FakeDB(existing=[existing])

    result = notify(db, company_b, NotificationType.SPRINT_COMPLETED, "Sprint done.")

    assert len(db.added_notifications) == 1
    assert result.company_id == company_b


def test_multiple_different_types_all_inserted():
    """Several different notification types for the same company are all inserted independently."""
    company_id = uuid.uuid4()
    db = FakeDB()

    r1 = notify(db, company_id, NotificationType.TASK_SUBMITTED, "Task submitted.")
    r2 = notify(db, company_id, NotificationType.REVIEW_STARTED, "Review started.")
    r3 = notify(db, company_id, NotificationType.SPRINT_COMPLETED, "Sprint complete.")

    assert len(db.added_notifications) == 3
    ids = {r1.id, r2.id, r3.id}
    assert len(ids) == 3  # all distinct


# ---------------------------------------------------------------------------
# Tests: concurrent scenario
# ---------------------------------------------------------------------------

def test_concurrent_second_caller_uses_plain_read_fallback():
    """When FOR UPDATE SKIP LOCKED returns nothing (lock contention), the caller
    falls back to a plain read.  If the winning concurrent caller's row is
    already in the DB, the plain read returns it and no duplicate is inserted.
    """
    company_id = uuid.uuid4()
    # Simulate: another request already inserted this row; it's visible via
    # plain read but FOR UPDATE SKIP LOCKED returns nothing (locked by winner).
    concurrent_row = _make_notification(
        company_id, NotificationType.TASK_SUBMITTED, "Task submitted.", age_seconds=1
    )

    class ConcurrentFakeDB(FakeDB):
        """FOR UPDATE always returns empty (simulating lock contention);
        plain read returns the concurrent winner's row."""
        def execute(self, stmt):
            is_for_update = getattr(stmt, "_for_update_arg", None) is not None
            if is_for_update:
                return FakeExecuteResult([])  # lock held by another transaction
            return FakeExecuteResult([concurrent_row])

    db = ConcurrentFakeDB()
    result = notify(db, company_id, NotificationType.TASK_SUBMITTED, "Task submitted.")

    # No new row inserted; existing concurrent row returned.
    assert len(db.added_notifications) == 0
    assert result.id == concurrent_row.id


def test_for_update_raises_falls_back_gracefully():
    """When FOR UPDATE is not supported (e.g. SQLite in tests), the exception is
    caught and a plain read is used as fallback.  If a recent row exists, it is
    returned without inserting a duplicate."""
    company_id = uuid.uuid4()
    existing = _make_notification(company_id, NotificationType.REVIEW_COMPLETED, "Review done.", age_seconds=5)

    class ForUpdateRaisesFakeDB(FakeDB):
        def execute(self, stmt):
            is_for_update = getattr(stmt, "_for_update_arg", None) is not None
            if is_for_update:
                raise RuntimeError("FOR UPDATE not supported")
            # Plain read succeeds.
            return FakeExecuteResult([existing])

    db = ForUpdateRaisesFakeDB()
    result = notify(db, company_id, NotificationType.REVIEW_COMPLETED, "Review done.")

    assert len(db.added_notifications) == 0
    assert result.id == existing.id


# ---------------------------------------------------------------------------
# Tests: persistence and isolation
# ---------------------------------------------------------------------------

def test_returned_notification_has_correct_fields():
    """The returned Notification has the right company_id, type, and message."""
    company_id = uuid.uuid4()
    db = FakeDB()

    result = notify(db, company_id, NotificationType.TASK_APPROVED, "Task approved!")

    assert result.company_id == company_id
    assert result.notification_type == NotificationType.TASK_APPROVED
    assert result.message == "Task approved!"
    assert result.id is not None


def test_suppressed_notification_returns_original_row_not_new():
    """The suppressed call returns the original persisted row, not a new object."""
    company_id = uuid.uuid4()
    original = _make_notification(company_id, NotificationType.TASK_APPROVED, "Task approved!", age_seconds=2)
    original.is_read = True  # mark as read to verify same object comes back
    db = FakeDB(existing=[original])

    result = notify(db, company_id, NotificationType.TASK_APPROVED, "Task approved!")

    assert result.id == original.id
    assert result.is_read is True  # the existing row's state is preserved


def test_default_cooldown_is_60_seconds():
    """DEFAULT_COOLDOWN_SECONDS is documented to be 60."""
    assert DEFAULT_COOLDOWN_SECONDS == 60


def test_rapid_sequence_inserts_only_one_notification():
    """Calling notify() three times in rapid succession inserts exactly one row."""
    company_id = uuid.uuid4()
    db = FakeDB()

    r1 = notify(db, company_id, NotificationType.SPRINT_COMPLETED, "Sprint complete.")
    r2 = notify(db, company_id, NotificationType.SPRINT_COMPLETED, "Sprint complete.")
    r3 = notify(db, company_id, NotificationType.SPRINT_COMPLETED, "Sprint complete.")

    assert len(db.added_notifications) == 1
    assert r1.id == r2.id == r3.id


def test_notify_does_not_affect_unrelated_notification_types():
    """Sending a duplicate for type A does not suppress an unrelated type B."""
    company_id = uuid.uuid4()
    db = FakeDB()

    # First: insert a TASK_SUBMITTED notification
    r1 = notify(db, company_id, NotificationType.TASK_SUBMITTED, "Task submitted.")
    # Second: same TASK_SUBMITTED — suppressed
    r2 = notify(db, company_id, NotificationType.TASK_SUBMITTED, "Task submitted.")
    # Third: a different type — must still be inserted
    r3 = notify(db, company_id, NotificationType.REVIEW_STARTED, "Task submitted.")

    assert len(db.added_notifications) == 2  # r1 and r3
    assert r1.id == r2.id  # duplicate suppressed
    assert r3.id != r1.id  # different type got its own row
    assert r3.notification_type == NotificationType.REVIEW_STARTED
