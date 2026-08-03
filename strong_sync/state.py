from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from . import config

DEFAULT_STATE: dict[str, Any] = {
    "run_count": 0,
    "first_run": None,
    "last_run": None,
    "last_successful_run": None,
    "continuation": None,
    "seen_log_ids": [],
    "total_workouts": 0,
    "last_new_workout_at": None,
}

CACHE_FILE = config.CACHE_DIR / "exercises.json"
METRICS_FILE = config.CACHE_DIR / "metrics.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict[str, Any]:
    if not config.STATE_FILE.exists():
        return dict(DEFAULT_STATE)
    try:
        data = json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_STATE)
    merged = dict(DEFAULT_STATE)
    merged.update(data)
    return merged


def save_state(state: dict[str, Any]) -> None:
    config.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.STATE_FILE.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_run_count(state: dict[str, Any]) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        f"Days run: {state['run_count']}",
        f"First run: {state.get('first_run') or 'n/a'}",
        f"Last run: {state.get('last_run') or 'n/a'}",
        f"Workouts tracked: {state.get('total_workouts', 0)}",
        f"Last new workout seen: {state.get('last_new_workout_at') or 'n/a'}",
    ]
    config.RUN_COUNT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_cache() -> dict[str, Any]:
    return _read_json(CACHE_FILE, {})


def save_cache(payload: dict[str, Any]) -> None:
    _write_json(CACHE_FILE, payload)


def load_metrics() -> dict[str, Any]:
    return _read_json(METRICS_FILE, {})


def save_metrics(payload: dict[str, Any]) -> None:
    _write_json(METRICS_FILE, payload)
