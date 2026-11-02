"""Dynamic task generation applies agent focus without owning progression."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.enums import DifficultyLevel, TaskType
from app.services.generators import dynamic_task_generator as generator


async def _immediate_wait_for(awaitable, timeout):
    return await awaitable


@pytest.fixture(autouse=True)
def no_platform_event_loop(monkeypatch):
    monkeypatch.setattr(asyncio, "wait_for", _immediate_wait_for)


class Result:
    def __init__(self, *, all_rows=None, first=None, scalar=None):
        self.all_rows = all_rows or []
        self.first_value = first
        self.scalar_value = scalar

    def scalars(self):
        return self

    def all(self):
        return self.all_rows

    def first(self):
        return self.first_value

    def scalar_one_or_none(self):
        return self.scalar_value


class FakeDB:
    def __init__(self):
        self.calls = 0
        self.added = []
        self.flushed = 0

    def execute(self, _statement):
        self.calls += 1
        # Completed-task rows, prior submission, and next task sequence.
        if self.calls == 1:
            return Result(all_rows=[])
        if self.calls == 2:
            return Result(scalar=None)
        return Result(first=3)

    def add(self, item):
        self.added.append(item)

    def flush(self):
        self.flushed += 1


class FakeAI:
    def __init__(self, *, failure=False):
        self.failure = failure
        self.prompts = []

    async def generate_json(self, prompt, system_prompt):
        self.prompts.append(prompt)
        if self.failure:
            raise RuntimeError("provider secret detail")
        return {"title": "Implement request retry handling", "description": "Add a bounded retry path.",
                "acceptance_criteria": ["Retries stop at the configured limit."],
                "task_type": "coding", "hints": ["Inspect existing request handling."],
                "generation_reason": "Next sprint deliverable."}


def run(coroutine):
    try:
        return coroutine.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coroutine.close()


def setup(monkeypatch, *, provider_failure=False):
    db = FakeDB()
    simulation = SimpleNamespace(id=uuid4(), role="Backend Developer",
                                 difficulty=DifficultyLevel.INTERMEDIATE)
    sprint = SimpleNamespace(id=uuid4(), goal="Stabilize payment requests", sprint_number=2)
    profile = SimpleNamespace(role="Backend Developer", current_competencies={}, skills=[])
    monkeypatch.setattr(generator, "get_or_create_profile", lambda *_: profile)
    monkeypatch.setattr(generator, "calculate_performance_trend", lambda *_: {"trend": "stable", "recent_avg": 65})
    provider = FakeAI(failure=provider_failure)
    monkeypatch.setattr(generator, "get_ai_service", lambda: provider)
    return db, simulation, sprint, provider


def test_recommended_competency_focus_guides_task_type_and_persisted_task_metadata(monkeypatch):
    db, simulation, sprint, provider = setup(monkeypatch)
    focus = {"status": "recommended", "focus_competencies": ["problem_solving"],
             "proposed_task_type": "debugging", "reason": "Persisted evidence supports a problem-solving focus.",
             "evidence_references": [{"evidence_type": "assessment_attempt", "evidence_record_id": str(uuid4())}]}
    task = run(generator.generate_dynamic_task(db, simulation, sprint, "student-22",
                                               competency_focus=focus))
    assert task.task_type == TaskType.DEBUGGING
    assert task.difficulty == DifficultyLevel.INTERMEDIATE
    assert task.task_metadata["competency_focus"]["dimensions"] == ["problem_solving"]
    assert task.task_metadata["competency_focus"]["evidence_references"] == focus["evidence_references"]
    assert any("Practice problem_solving" in hint for hint in task.task_metadata["hints"])
    assert "problem_solving" in provider.prompts[0]
    assert task in db.added
    assert db.flushed >= 1


def test_provider_failure_uses_existing_fallback_task_with_recommended_focus(monkeypatch):
    db, simulation, sprint, _provider = setup(monkeypatch, provider_failure=True)
    task = run(generator.generate_dynamic_task(
        db, simulation, sprint, "student-22",
        competency_focus={"status": "recommended", "focus_competencies": ["communication"],
                          "proposed_task_type": "documentation", "reason": "Practice communication.",
                          "evidence_references": []},
    ))
    assert task.task_type == TaskType.DOCUMENTATION
    assert task.difficulty == DifficultyLevel.INTERMEDIATE
    assert task.task_metadata["competency_focus"]["dimensions"] == ["communication"]


def test_absent_or_untrusted_focus_preserves_existing_dynamic_task_choice(monkeypatch):
    db, simulation, sprint, _provider = setup(monkeypatch)
    task = run(generator.generate_dynamic_task(
        db, simulation, sprint, "student-22",
        competency_focus={"status": "fallback", "focus_competencies": ["debugging"],
                          "proposed_task_type": "not_a_task_type"},
    ))
    assert task.task_type == TaskType.CODING
    assert task.task_metadata["competency_focus"] is None
