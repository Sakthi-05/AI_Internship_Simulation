"""
Proactive workplace chat: the manager and teammates message first.

Covers: lazy seeding on thread open, persistence across visits (logout/login
is stateless JWT, so "returning" == a fresh GET), cooldown anti-spam,
contextual grounding in internship progress, task-lifecycle reactions, the
scheduler sweep, and multi-tenant isolation.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from app.services import proactive_chat_service as proactive


@pytest.fixture(autouse=True)
def _no_external_ai(monkeypatch):
    """Force deterministic template rendering (never hit Gemini in tests)."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")


def _generate(client, auth_headers, student_id="student_001"):
    resp = client.post(
        "/api/generate",
        headers=auth_headers,
        json={
            "student_id": student_id,
            "role": "Backend Developer",
            "technology_stack": ["Python", "FastAPI", "PostgreSQL"],
            "difficulty": "intermediate",
            "company_type": "startup",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _manager_history(client, auth_headers, company_id):
    resp = client.get(f"/api/companies/{company_id}/chat/manager", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _backdate_task_deadline(task_id, days_ago=1):
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tasks SET deadline = :deadline WHERE id = :id"),
            {"deadline": datetime.now(timezone.utc) - timedelta(days=days_ago), "id": task_id},
        )


def _age_proactive_messages(company_id, minutes=10):
    """Pretend existing proactive messages were sent minutes ago (both the
    messages and their per-category activity-log entries)."""
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE messages SET created_at = :ts, updated_at = :ts WHERE company_id = :id"),
            {"ts": datetime.now(timezone.utc) - timedelta(minutes=minutes), "id": company_id},
        )
        conn.execute(
            text("UPDATE activity_logs SET created_at = :ts, updated_at = :ts "
                 "WHERE company_id = :id AND action LIKE 'chat:proactive:%'"),
            {"ts": datetime.now(timezone.utc) - timedelta(minutes=minutes), "id": company_id},
        )


def _backdate_task_started(task_id, minutes=10):
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tasks SET started_at = :ts WHERE id = :id"),
            {"ts": datetime.now(timezone.utc) - timedelta(minutes=minutes), "id": task_id},
        )


def _proactive_count(company_id):
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        ctx = proactive.build_workplace_context(db, company_id)
        return ctx["proactive_count"] if ctx else 0
    finally:
        db.close()


def test_manager_thread_seeds_proactive_message_on_first_open(client, auth_headers):
    data = _generate(client, auth_headers)
    history = _manager_history(client, auth_headers, data["company_id"])

    assert len(history) >= 1
    first = history[0]
    assert first["sender_type"] == "manager"
    assert len(first["content"]) > 0
    # Grounded in this student's workplace: company, project, or first task.
    blob = first["content"]
    company = client.get(f"/api/companies/{data['company_id']}", headers=auth_headers).json()
    project = client.get(f"/api/projects/{data['project_id']}", headers=auth_headers).json()
    assert (company["name"] in blob) or (project["title"] in blob)
    assert "your current task" not in blob  # fallback text must never leak


def test_seeded_message_persists_and_cooldown_blocks_reseed(client, auth_headers):
    data = _generate(client, auth_headers)
    first = _manager_history(client, auth_headers, data["company_id"])
    second = _manager_history(client, auth_headers, data["company_id"])

    assert [m["id"] for m in first] == [m["id"] for m in second]
    assert len(second) == len(first)


def test_force_bypasses_cooldown(client, auth_headers):
    from app.core.database import SessionLocal

    data = _generate(client, auth_headers)
    _manager_history(client, auth_headers, data["company_id"])

    db = SessionLocal()
    try:
        msg = proactive.maybe_send_proactive_message(
            db, data["company_id"], channel="manager", force=True
        )
        assert msg is not None
        second = proactive.maybe_send_proactive_message(db, data["company_id"], channel="manager")
        assert second is None  # cooldown now applies
    finally:
        db.close()

    history = _manager_history(client, auth_headers, data["company_id"])
    assert len(history) >= 2


def test_teammate_thread_seeds_message_from_that_teammate(client, auth_headers):
    data = _generate(client, auth_headers)
    company_id = data["company_id"]
    teammate_id = client.get(f"/api/companies/{company_id}/team", headers=auth_headers).json()[0]["id"]

    resp = client.get(f"/api/companies/{company_id}/chat/team/{teammate_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    history = resp.json()

    mate_msgs = [m for m in history if m["sender_type"] == "team_member"]
    assert len(mate_msgs) >= 1
    assert mate_msgs[0]["sender_id"] == teammate_id
    assert len(mate_msgs[0]["content"]) > 0


def test_reminder_names_the_overdue_task(client, auth_headers):
    from app.core.database import SessionLocal

    data = _generate(client, auth_headers)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=auth_headers).status_code == 200
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=auth_headers)
    assert current.status_code == 200, current.text
    task = current.json()
    assert client.post(
        f"/api/simulations/{sim_id}/tasks/{task['task_id']}/start", headers=auth_headers
    ).status_code == 200
    _backdate_task_deadline(task["task_id"])

    db = SessionLocal()
    try:
        ctx = proactive.build_workplace_context(db, data["company_id"])
        assert ctx is not None
        assert any(t.title == task["title"] for t in ctx["overdue"])
        slot = proactive.choose_slot(ctx, channel="manager")
        assert slot is not None and slot[2] == "reminder"
        content = proactive.render_proactive_message(ctx, slot[0], ctx["manager"], slot[2])
        assert task["title"] in content
    finally:
        db.close()


def test_starting_a_task_draws_teammate_reaction(client, auth_headers):
    data = _generate(client, auth_headers)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=auth_headers).status_code == 200
    # Let the start-of-simulation greeting age so the kickoff reaction below
    # is a spaced-out follow-up, not a burst.
    _age_proactive_messages(data["company_id"], minutes=10)
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=auth_headers)
    assert current.status_code == 200, current.text
    task = current.json()
    assert client.post(
        f"/api/simulations/{sim_id}/tasks/{task['task_id']}/start", headers=auth_headers
    ).status_code == 200

    teammate_id = client.get(
        f"/api/companies/{data['company_id']}/team", headers=auth_headers
    ).json()[0]["id"]
    history = client.get(
        f"/api/companies/{data['company_id']}/chat/team/{teammate_id}", headers=auth_headers
    ).json()
    mate_msgs = [m for m in history if m["sender_type"] == "team_member"]
    assert len(mate_msgs) >= 1
    # The kickoff reaction names the work that was just started.
    assert task["title"] in " ".join(m["content"] for m in mate_msgs)
    # Still visible on return: a fresh read shows the same persisted message.
    again = client.get(
        f"/api/companies/{data['company_id']}/chat/team/{teammate_id}", headers=auth_headers
    ).json()
    assert [m["id"] for m in again] == [m["id"] for m in history]


def test_submitting_a_task_draws_manager_ack(client, auth_headers):
    data = _generate(client, auth_headers)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=auth_headers).status_code == 200
    _age_proactive_messages(data["company_id"], minutes=10)
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=auth_headers)
    assert current.status_code == 200, current.text
    task = current.json()
    assert client.post(
        f"/api/simulations/{sim_id}/tasks/{task['task_id']}/start", headers=auth_headers
    ).status_code == 200
    _age_proactive_messages(data["company_id"], minutes=10)
    resp = client.post(
        f"/api/simulations/{sim_id}/tasks/{task['task_id']}/submit",
        headers=auth_headers,
        data={"metadata": '{"response": "Here is my work"}'},
    )
    assert resp.status_code == 201, resp.text

    history = _manager_history(client, auth_headers, data["company_id"])
    mgr_msgs = [m for m in history if m["sender_type"] == "manager"]
    assert len(mgr_msgs) >= 1
    assert task["title"] in " ".join(m["content"] for m in mgr_msgs)


def test_rapid_actions_never_send_multiple_messages_at_once(client, auth_headers):
    """Start → submit → complete within seconds: at most one extra message."""
    data = _generate(client, auth_headers)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=auth_headers).status_code == 200
    before = _proactive_count(data["company_id"])
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=auth_headers)
    assert current.status_code == 200, current.text
    task_id = current.json()["task_id"]
    assert client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=auth_headers
    ).status_code == 200
    resp = client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/submit",
        headers=auth_headers,
        data={"metadata": '{"response": "Here is my work"}'},
    )
    assert resp.status_code == 201, resp.text
    assert _proactive_count(data["company_id"]) - before <= 1


def test_trigger_gap_blocks_rapid_reaction_but_force_sends(client, auth_headers):
    from app.core.database import SessionLocal

    data = _generate(client, auth_headers)
    db = SessionLocal()
    try:
        first = proactive.maybe_send_proactive_message(
            db, data["company_id"], channel="team_member", trigger="task_started"
        )
        assert first is not None
        # Seconds later, another trigger must NOT produce a rapid follow-up.
        assert proactive.maybe_send_proactive_message(
            db, data["company_id"], channel="manager", trigger="task_submitted"
        ) is None
        # Force still goes through (tests, admin resends).
        assert proactive.maybe_send_proactive_message(
            db, data["company_id"], channel="manager", trigger="task_submitted", force=True
        ) is not None
    finally:
        db.close()


def test_greeting_goes_out_once_then_instructions(client, auth_headers):
    from app.core.database import SessionLocal

    data = _generate(client, auth_headers)
    history = _manager_history(client, auth_headers, data["company_id"])
    assert history[0]["sender_type"] == "manager"

    db = SessionLocal()
    try:
        # A repeat greeting trigger degrades to task instructions instead.
        _age_proactive_messages(data["company_id"], minutes=10)
        msg = proactive.maybe_send_proactive_message(
            db, data["company_id"], channel="manager", trigger="simulation_started"
        )
        assert msg is not None
        entry = db.execute(
            text("SELECT action FROM activity_logs WHERE company_id = :id "
                 "ORDER BY created_at DESC LIMIT 1"),
            {"id": data["company_id"]},
        ).fetchone()
        assert entry is not None and entry[0] == "chat:proactive:task_update"
    finally:
        db.close()


def test_followup_gated_until_task_has_aged(client, auth_headers):
    from app.core.database import SessionLocal

    data = _generate(client, auth_headers)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=auth_headers).status_code == 200
    _age_proactive_messages(data["company_id"], minutes=10)
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=auth_headers)
    task_id = current.json()["task_id"]
    assert client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=auth_headers
    ).status_code == 200

    db = SessionLocal()
    try:
        ctx = proactive.build_workplace_context(db, data["company_id"])
        # Task started seconds ago: no follow-up yet.
        assert proactive._category_eligible(ctx, "checkin") is False
        db.close()
        _backdate_task_started(task_id, minutes=10)
        db = SessionLocal()
        ctx = proactive.build_workplace_context(db, data["company_id"])
        assert proactive._category_eligible(ctx, "checkin") is True
    finally:
        db.close()


def test_category_interval_blocks_repeat_followup(client, auth_headers):
    from app.core.database import SessionLocal

    data = _generate(client, auth_headers)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=auth_headers).status_code == 200
    current = client.get(f"/api/simulations/{sim_id}/tasks/current", headers=auth_headers)
    task_id = current.json()["task_id"]
    assert client.post(
        f"/api/simulations/{sim_id}/tasks/{task_id}/start", headers=auth_headers
    ).status_code == 200
    _backdate_task_started(task_id, minutes=10)
    _age_proactive_messages(data["company_id"], minutes=10)

    db = SessionLocal()
    try:
        first = proactive.maybe_send_proactive_message(
            db, data["company_id"], channel="manager", category="checkin", force=True
        )
        assert first is not None
        # Same follow-up kind must wait out its own interval.
        assert proactive.maybe_send_proactive_message(
            db, data["company_id"], channel="manager", category="checkin"
        ) is None
    finally:
        db.close()


def test_templates_cover_every_category():
    from app.core.database import SessionLocal

    # Reuse any generated company for a realistic context.
    from sqlalchemy import create_engine as _ce

    engine = _ce(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id FROM companies LIMIT 1")).fetchone()
    assert row is not None

    db = SessionLocal()
    try:
        ctx = proactive.build_workplace_context(db, row[0])
        assert ctx is not None
        for category in proactive._MANAGER_TEMPLATES:
            content = proactive.render_proactive_message(
                ctx, proactive.MessageSenderType.MANAGER, ctx["manager"], category
            )
            assert isinstance(content, str) and len(content) > 20, category
        mate = ctx["teammates"][0]
        for category in proactive._TEAMMATE_TEMPLATES:
            content = proactive.render_proactive_message(
                ctx, proactive.MessageSenderType.TEAM_MEMBER, mate, category
            )
            assert isinstance(content, str) and len(content) > 20, category
        assert "?" in proactive.render_proactive_message(
            ctx, proactive.MessageSenderType.TEAM_MEMBER, mate, "question"
        )
    finally:
        db.close()


def test_other_student_cannot_read_or_seed_chat(client, auth_headers, other_auth_headers):
    data = _generate(client, auth_headers)
    resp = client.get(f"/api/companies/{data['company_id']}/chat/manager", headers=other_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_scheduler_sweep_sends_to_active_company(client, auth_headers):
    data = _generate(client, auth_headers)
    sim_id = data["simulation_id"]
    assert client.post(f"/api/simulations/{sim_id}/start", headers=auth_headers).status_code == 200
    # Age well past every pacing window so the sweep deterministically picks
    # this company up (the stagger only ever *defers*, never skips, recent ones).
    _age_proactive_messages(data["company_id"], minutes=25)

    sent = await proactive.sweep_proactive_messages()
    assert sent >= 1

    history = _manager_history(client, auth_headers, data["company_id"])
    team_first = client.get(
        f"/api/companies/{data['company_id']}/chat/team/"
        + client.get(f"/api/companies/{data['company_id']}/team", headers=auth_headers).json()[0]["id"],
        headers=auth_headers,
    ).json()
    assert len(history) + len(team_first) >= 1
