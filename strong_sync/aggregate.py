from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from . import config
from .parsing import ExerciseSession, SetRecord

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)

def working_sets(session: ExerciseSession) -> list[SetRecord]:
    if config.INCLUDE_WARMUP_SETS:
        return list(session.sets)
    real = [s for s in session.sets if not s.is_warmup]
    # If every set was flagged warmup, fall back to all of them rather than
    # pretending the exercise never happened.
    return real or list(session.sets)


def best_set_of(session: ExerciseSession) -> SetRecord | None:
    sets = working_sets(session)
    if not sets:
        return None
    return max(sets, key=lambda s: (s.weight, s.reps))


def rank_key(session: ExerciseSession) -> tuple[float, int, float]:
    """Sort key for comparing candidate best sessions."""
    best = best_set_of(session)
    if best is None:
        return (-1.0, -1, -1.0)
    return (best.weight, best.reps, session.date.timestamp())


@dataclass
class ExerciseRecord:
    metric_id: str
    latest: ExerciseSession | None = None
    best: ExerciseSession | None = None

    def add(self, session: ExerciseSession) -> None:
        if not working_sets(session):
            return
        if self.latest is None or session.date > self.latest.date:
            self.latest = session
        if self.best is None or rank_key(session) > rank_key(self.best):
            self.best = session

    def absorb(self, other: "ExerciseRecord") -> None:
        """Fold another record for the SAME exercise into this one.

        Strong can hold several measurement ids under one name (a legacy or
        custom copy alongside the current one). Left alone they render as
        duplicate entries -- and if only one carries a body-part tag, they
        land in different files.
        """
        for session in (other.latest, other.best):
            if session is not None:
                self.add(session)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "latest": self.latest.to_dict() if self.latest else None,
            "best": self.best.to_dict() if self.best else None,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ExerciseRecord":
        return ExerciseRecord(
            metric_id=d["metric_id"],
            latest=ExerciseSession.from_dict(d["latest"]) if d.get("latest") else None,
            best=ExerciseSession.from_dict(d["best"]) if d.get("best") else None,
        )


def merge_sessions(
    records: dict[str, ExerciseRecord], sessions: Iterable[ExerciseSession]
) -> dict[str, ExerciseRecord]:
    """Fold new sessions into the existing record cache (mutates and returns)."""
    for session in sessions:
        record = records.get(session.metric_id)
        if record is None:
            record = ExerciseRecord(metric_id=session.metric_id)
            records[session.metric_id] = record
        record.add(session)
    return records


def records_to_dict(records: dict[str, ExerciseRecord]) -> dict[str, Any]:
    return {mid: rec.to_dict() for mid, rec in records.items()}


def records_from_dict(data: dict[str, Any]) -> dict[str, ExerciseRecord]:
    return {mid: ExerciseRecord.from_dict(d) for mid, d in (data or {}).items()}
