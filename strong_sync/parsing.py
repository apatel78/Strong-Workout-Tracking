from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from . import config

_MISSING = object()
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)

# --- cell vocabulary -------------------------------------------------------

REPS_CELL_TYPES = {"REPS"}
# Anything ending in _WEIGHT (DUMBBELL_WEIGHT, OTHER_WEIGHT, BARBELL_WEIGHT...)
# plus a bare WEIGHT, counts as the load for the set.
def _is_weight_cell(cell_type: str) -> bool:
    return cell_type == "WEIGHT" or cell_type.endswith("_WEIGHT")

# Cell types that mark a cellSet as something other than a working set.
NON_SET_CELL_TYPES = {"REST_TIMER"}
# Where a warmup marker would live if the app emits one.
SET_TYPE_CELL_TYPES = {"SET_TYPE", "TYPE", "CELL_SET_TYPE"}

# --- candidate field names (still tolerant where the shape is unconfirmed) --

LOG_ID = ("id", "logId", "uuid")
LOG_DATE = ("startDate", "start", "date", "performedAt", "created")
LOG_TYPE = ("logType", "type")

MEASUREMENT_ID = ("id", "measurementId", "metricId", "uuid")
MEASUREMENT_NAME = ("name", "title", "displayName", "custom")
MEASUREMENT_BODYPART = (
    "bodyPart", "muscleGroup", "primaryMuscleGroup", "muscle", "category", "group",
)
# Only these are exercises; the collection also holds body measurements
# (bodyweight, body fat, etc.) which must not become "exercises".
EXERCISE_MEASUREMENT_TYPE = "EXERCISE"


def pick(obj: Any, keys: Iterable[str], default: Any = None) -> Any:
    if not isinstance(obj, dict):
        return default
    for key in keys:
        value = obj.get(key, _MISSING)
        if value is not _MISSING and value is not None:
            return value
    return default


def uuid_from(text: Any) -> str | None:
    """Pull a UUID out of a link href like /api/users/{u}/measurements/{id}."""
    if not isinstance(text, str):
        return None
    found = _UUID_RE.findall(text)
    return found[-1].lower() if found else None


def parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(value.strip(), fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_weight(raw_kg: float) -> float:
    """Convert stored weight into the display unit, once, at parse time."""
    if config.SOURCE_WEIGHT_UNIT == "kg" and config.WEIGHT_UNIT == "lb":
        return round(raw_kg * config.KG_TO_LB, 2)
    if config.SOURCE_WEIGHT_UNIT == "lb" and config.WEIGHT_UNIT == "kg":
        return round(raw_kg / config.KG_TO_LB, 2)
    return round(raw_kg, 2)


# --- normalized shapes -----------------------------------------------------


@dataclass(frozen=True)
class SetRecord:
    index: int
    reps: int
    weight: float
    is_warmup: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index, "reps": self.reps,
            "weight": self.weight, "is_warmup": self.is_warmup,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "SetRecord":
        return SetRecord(
            index=int(d["index"]), reps=int(d["reps"]),
            weight=float(d["weight"]), is_warmup=bool(d.get("is_warmup", False)),
        )


@dataclass
class ExerciseSession:
    metric_id: str
    date: datetime
    sets: list[SetRecord] = field(default_factory=list)
    log_id: str | None = None
    name_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id, "date": self.date.isoformat(),
            "sets": [s.to_dict() for s in self.sets],
            "log_id": self.log_id, "name_hint": self.name_hint,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ExerciseSession":
        return ExerciseSession(
            metric_id=d["metric_id"],
            date=parse_date(d["date"]) or datetime.min.replace(tzinfo=timezone.utc),
            sets=[SetRecord.from_dict(s) for s in d.get("sets", [])],
            log_id=d.get("log_id"), name_hint=d.get("name_hint"),
        )


@dataclass(frozen=True)
class Metric:
    """An exercise definition. Comes from the `measurement` collection."""
    metric_id: str
    name: str
    body_part_raw: str | None


# --- parsers ---------------------------------------------------------------


def _name_of(raw: dict[str, Any]) -> str | None:
    """Names arrive either as a string or as {"custom": "..."}."""
    value = pick(raw, MEASUREMENT_NAME)
    if isinstance(value, dict):
        for key in ("custom", "default", "name", "value", "en"):
            if value.get(key):
                return str(value[key]).strip()
        return None
    return str(value).strip() if value else None


def tag_slugs(raw: dict[str, Any]) -> list[str]:
    """Body parts arrive as tag links: /api/users/{u}/tags/back -> "back"."""
    tags = (raw.get("_links") or {}).get("tag")
    if isinstance(tags, dict):
        tags = [tags]
    if not isinstance(tags, list):
        return []
    slugs = []
    for entry in tags:
        href = entry.get("href") if isinstance(entry, dict) else entry
        if isinstance(href, str) and href.strip("/"):
            slugs.append(href.rstrip("/").rsplit("/", 1)[-1].lower())
    return slugs


def parse_metric(raw: dict[str, Any]) -> Metric | None:
    """Parse one `measurement` record into an exercise definition."""
    if str(raw.get("measurementType") or EXERCISE_MEASUREMENT_TYPE).upper() != EXERCISE_MEASUREMENT_TYPE:
        return None

    measurement_id = pick(raw, MEASUREMENT_ID) or uuid_from(
        ((raw.get("_links") or {}).get("self") or {}).get("href")
    )
    if not measurement_id:
        return None

    name = _name_of(raw) or f"Unnamed ({str(measurement_id)[:8]})"

    # Prefer an explicit field if one ever appears, else fall back to tags.
    body_part = pick(raw, MEASUREMENT_BODYPART)
    if isinstance(body_part, dict):
        body_part = _name_of(body_part)
    if isinstance(body_part, list):
        body_part = body_part[0] if body_part else None
    if not body_part:
        from .bodyparts import first_recognized_tag

        slugs = tag_slugs(raw)
        body_part = first_recognized_tag(slugs) or (slugs[0] if slugs else None)

    return Metric(str(measurement_id).lower(), name, str(body_part) if body_part else None)


def parse_cell_set(raw: dict[str, Any], fallback_index: int) -> SetRecord | None:
    """Turn one cellSet into a SetRecord, or None if it isn't a working set."""
    if not isinstance(raw, dict):
        return None
    cells = raw.get("cells")
    if not isinstance(cells, list):
        return None

    reps: int | None = None
    weight: float | None = None
    is_warmup = False
    has_only_non_set = True

    for cell in cells:
        if not isinstance(cell, dict):
            continue
        cell_type = str(cell.get("cellType") or "").upper()
        value = cell.get("value")

        if cell_type in NON_SET_CELL_TYPES:
            continue
        has_only_non_set = False

        if cell_type in REPS_CELL_TYPES and value is not None:
            number = _to_float(value)
            if number is not None:
                reps = int(number)
        elif _is_weight_cell(cell_type) and value is not None:
            number = _to_float(value)
            if number is not None:
                weight = normalize_weight(number)
        elif cell_type in SET_TYPE_CELL_TYPES and value:
            if "WARM" in str(value).upper():
                is_warmup = True

    # A rest-timer row has no reps and no weight; it is not a set.
    if has_only_non_set or (reps is None and weight is None):
        return None

    return SetRecord(
        index=fallback_index,
        reps=reps if reps is not None else 0,
        weight=weight if weight is not None else 0.0,
        is_warmup=is_warmup,
    )


def parse_log(raw: dict[str, Any]) -> list[ExerciseSession]:
    """Turn one workout log into one ExerciseSession per exercise performed."""
    if not isinstance(raw, dict):
        return []

    log_type = pick(raw, LOG_TYPE)
    if log_type and str(log_type).upper() != "WORKOUT":
        return []

    date = parse_date(pick(raw, LOG_DATE))
    if date is None:
        return []
    log_id = pick(raw, LOG_ID)

    groups = ((raw.get("_embedded") or {}).get("cellSetGroup")) or []
    if not isinstance(groups, list):
        return []

    sessions: list[ExerciseSession] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        # The exercise id is only present as a UUID inside a link href.
        href = ((group.get("_links") or {}).get("measurement") or {}).get("href")
        measurement_id = uuid_from(href)
        if not measurement_id:
            continue

        cell_sets = group.get("cellSets")
        if not isinstance(cell_sets, list):
            continue

        sets: list[SetRecord] = []
        for cell_set in cell_sets:
            if isinstance(cell_set, dict) and cell_set.get("isCompleted") is False:
                continue
            parsed = parse_cell_set(cell_set, len(sets) + 1)
            if parsed:
                sets.append(parsed)

        if not sets:
            continue
        sessions.append(
            ExerciseSession(
                metric_id=measurement_id,
                date=date,
                sets=sets,
                log_id=str(log_id) if log_id else None,
                # Deliberately NOT the workout name ("Midday Workout") -- that
                # is the session title, not the exercise. Names come from the
                # measurement record; unresolved ids render as Unknown.
                name_hint=None,
            )
        )
    return sessions
