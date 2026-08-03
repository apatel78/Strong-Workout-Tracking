from __future__ import annotations

from datetime import datetime
from typing import Iterable

from . import config
from .aggregate import ExerciseRecord, best_set_of, working_sets
from .bodyparts import BODY_PARTS, classify
from .parsing import ExerciseSession, Metric

SEPARATOR = "---------"


def _weight(value: float) -> str:
    # Already normalized to config.WEIGHT_UNIT during parsing.
    if abs(value - round(value)) < 0.01:
        return f"{int(round(value))} {config.WEIGHT_UNIT}"
    return f"{value:.1f} {config.WEIGHT_UNIT}"


def _date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _set_lines(session: ExerciseSession) -> list[str]:
    lines = []
    for i, s in enumerate(working_sets(session), start=1):
        number = s.index if s.index else i
        lines.append(f"    Set {number} ---- {s.reps} reps ----- {_weight(s.weight)}")
    return lines


def render_exercise(name: str, record: ExerciseRecord) -> str:
    lines = [name.upper()]

    if record.latest:
        lines.append(f"  Latest Workout: {_date(record.latest.date)}")
        lines.extend(_set_lines(record.latest))
    else:
        lines.append("  Latest Workout: (none)")

    lines.append(f"  {SEPARATOR}")

    if record.best:
        top = best_set_of(record.best)
        pr = f" (PR: {top.reps} reps @ {_weight(top.weight)})" if top else ""
        lines.append(f"  Best Workout: {_date(record.best.date)}{pr}")
        lines.extend(_set_lines(record.best))
    else:
        lines.append("  Best Workout: (none)")

    return "\n".join(lines)


def render_body_part(part: str, entries: Iterable[tuple[str, ExerciseRecord]]) -> str:
    entries = sorted(entries, key=lambda pair: pair[0].lower())
    header = [part.upper(), "=" * len(part), ""]
    if not entries:
        return "\n".join(header + ["(no exercises logged yet)", ""])
    blocks = [render_exercise(name, rec) for name, rec in entries]
    return "\n".join(header) + "\n\n".join(blocks) + "\n"


def _name_key(name: str) -> str:
    """Collapse cosmetic differences so duplicates of one exercise merge."""
    return " ".join(name.lower().replace("_", " ").replace("-", " ").split())


def build_files(
    records: dict[str, ExerciseRecord], metrics: dict[str, Metric]
) -> tuple[dict[str, str], list[str]]:
    """Return ({body_part: file_contents}, [unresolved metric ids]).

    Two things happen here that the raw records don't do for you:

    1. Exercises with no measurement record are DROPPED, not written as
       "Unknown Exercise (abc12345)". These are exercises deleted from your
       Strong account; their ids are returned so the caller can report a count.
    2. Records sharing a name are merged. Strong may hold a legacy id and a
       current id for the same lift; without merging you get the same exercise
       twice, potentially in two different files if only one is tagged.
    """
    unresolved: list[str] = []
    groups: dict[str, dict] = {}

    for metric_id, record in records.items():
        metric = metrics.get(metric_id)
        if metric is None:
            unresolved.append(metric_id)
            continue

        key = _name_key(metric.name)
        part = classify(metric.name, metric.body_part_raw)
        group = groups.get(key)
        if group is None:
            merged = ExerciseRecord(metric_id=metric_id)
            merged.absorb(record)
            groups[key] = {"name": metric.name, "record": merged, "parts": [part]}
        else:
            group["record"].absorb(record)
            group["parts"].append(part)

    buckets: dict[str, list[tuple[str, ExerciseRecord]]] = {p: [] for p in BODY_PARTS}
    for group in groups.values():
        # If any copy of this exercise carried a real body-part tag, trust it
        # over the untagged copy that would otherwise fall through to "other".
        part = next((p for p in group["parts"] if p != "other"), "other")
        buckets[part].append((group["name"], group["record"]))

    files = {part: render_body_part(part, entries) for part, entries in buckets.items()}
    return files, unresolved


def write_files(files: dict[str, str]) -> list[str]:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    changed = []
    for part, content in files.items():
        path = config.DATA_DIR / f"{part}.txt"
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if old != content:
            path.write_text(content, encoding="utf-8")
            changed.append(path.name)
    return changed
