"""Record and read unified simulation interaction history.

Append-only chronological log. Parent links form a linear chain today and
can later attach branched children without rewriting earlier rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.simulation import Simulation
from app.models.simulation_interaction import SimulationInteraction


def record_interaction(
    db: Session,
    simulation: Simulation,
    *,
    situation: str,
    interaction_type: str,
    student_response: Optional[str] = None,
    decision: Optional[str] = None,
    scenario_id: Optional[uuid.UUID] = None,
    task_id: Optional[uuid.UUID] = None,
    parent_interaction_id: Optional[uuid.UUID] = None,
    metadata: Optional[Dict[str, Any]] = None,
    occurred_at: Optional[datetime] = None,
) -> SimulationInteraction:
    """Append one interaction for a simulation, sequenced after the latest row."""
    db.execute(
        select(Simulation).where(Simulation.id == simulation.id).with_for_update()
    )

    last = db.execute(
        select(SimulationInteraction)
        .where(SimulationInteraction.simulation_id == simulation.id)
        .order_by(SimulationInteraction.sequence.desc())
        .limit(1)
    ).scalar_one_or_none()

    next_sequence = (last.sequence if last else 0) + 1
    parent_id = parent_interaction_id
    if parent_id is None and last is not None:
        parent_id = last.id

    entry = SimulationInteraction(
        simulation_id=simulation.id,
        student_id=simulation.student_id,
        scenario_id=scenario_id if scenario_id is not None else simulation.current_scenario_id,
        task_id=task_id,
        sequence=next_sequence,
        situation=situation,
        student_response=student_response,
        decision=decision,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        interaction_type=interaction_type,
        parent_interaction_id=parent_id,
        metadata_json=metadata or {},
    )
    db.add(entry)
    db.flush()
    return entry


def list_interactions(db: Session, simulation_id: uuid.UUID) -> List[SimulationInteraction]:
    """Return interactions in chronological sequence order."""
    return (
        db.execute(
            select(SimulationInteraction)
            .where(SimulationInteraction.simulation_id == simulation_id)
            .order_by(SimulationInteraction.sequence, SimulationInteraction.occurred_at)
        )
        .scalars()
        .all()
    )


def serialize_interaction(entry: SimulationInteraction) -> Dict[str, Any]:
    return {
        "id": str(entry.id),
        "simulation_id": str(entry.simulation_id),
        "student_id": entry.student_id,
        "scenario_id": str(entry.scenario_id) if entry.scenario_id else None,
        "task_id": str(entry.task_id) if entry.task_id else None,
        "sequence": entry.sequence,
        "situation": entry.situation,
        "student_response": entry.student_response,
        "decision": entry.decision,
        "timestamp": entry.occurred_at.isoformat() if entry.occurred_at else None,
        "interaction_type": entry.interaction_type,
        "parent_interaction_id": str(entry.parent_interaction_id) if entry.parent_interaction_id else None,
        "metadata": entry.metadata_json or {},
    }
