from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strong_sync.aggregate import (  # noqa: E402
    ExerciseRecord,
    best_set_of,
    merge_sessions,
    records_from_dict,
    records_to_dict,
)
from strong_sync.bodyparts import classify  # noqa: E402
from strong_sync.parsing import Metric, parse_log, parse_metric  # noqa: E402
from strong_sync.render import build_files, render_exercise  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "sample_page.json"


def load():
    pages = json.loads(FIXTURE.read_text(encoding="utf-8"))
    embedded = pages[0]["_embedded"]
    sessions = []
    for raw in embedded["log"]:
        sessions.extend(parse_log(raw))
    metrics = {}
    for raw in embedded["measurement"]:
        m = parse_metric(raw)
        if m:
            metrics[m.metric_id] = m
    records = merge_sessions({}, sessions)
    return records, metrics


# --- parsing ---------------------------------------------------------------


BENCH = "aaaa1111-0000-4000-8000-000000000001"
SQUAT = "aaaa1111-0000-4000-8000-000000000002"
CURL = "aaaa1111-0000-4000-8000-000000000003"


def test_parse_log_produces_one_session_per_exercise():
    pages = json.loads(FIXTURE.read_text(encoding="utf-8"))
    log1 = pages[0]["_embedded"]["log"][0]
    sessions = parse_log(log1)
    assert len(sessions) == 3
    assert {s.metric_id for s in sessions} == {BENCH, SQUAT, CURL}
    assert sessions[0].date.strftime("%Y-%m-%d") == "2026-07-01"


def test_exercise_id_is_extracted_from_the_link_href():
    """The measurement id exists ONLY inside _links.measurement.href."""
    pages = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sessions = parse_log(pages[0]["_embedded"]["log"][0])
    assert sessions[0].metric_id == BENCH


def test_rest_timer_rows_are_not_counted_as_sets():
    """Each bench group has a REST_TIMER cellSet between the working sets."""
    pages = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sessions = parse_log(pages[0]["_embedded"]["log"][0])
    bench = next(s for s in sessions if s.metric_id == BENCH)
    assert len(bench.sets) == 2
    assert [s.index for s in bench.sets] == [1, 2]


def test_kilograms_are_converted_to_pounds():
    """API stores 61.23kg; the account displays 135 lb."""
    pages = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sessions = parse_log(pages[0]["_embedded"]["log"][0])
    bench = next(s for s in sessions if s.metric_id == BENCH)
    assert bench.sets[0].weight == 135.0
    assert bench.sets[1].weight == 155.0


def test_incomplete_sets_are_skipped():
    pages = json.loads(FIXTURE.read_text(encoding="utf-8"))
    log3 = pages[0]["_embedded"]["log"][2]
    bench = next(s for s in parse_log(log3) if s.metric_id == BENCH)
    assert all(s.weight != 999.0 for s in bench.sets)


def test_non_workout_logs_are_ignored():
    pages = json.loads(FIXTURE.read_text(encoding="utf-8"))
    measurement_log = pages[0]["_embedded"]["log"][3]
    assert parse_log(measurement_log) == []


def test_parse_metric_reads_name_and_body_part():
    m = parse_metric({"id": "x", "name": "Bench Press", "bodyPart": "Chest"})
    assert m == Metric("x", "Bench Press", "Chest")


def test_parse_metric_unwraps_nested_custom_name():
    m = parse_metric({"id": "Y", "name": {"custom": "Incline Press"}, "bodyPart": "Chest"})
    assert m.name == "Incline Press" and m.metric_id == "y"


def test_body_part_comes_from_tag_links():
    """There is no bodyPart field -- it is /api/users/{u}/tags/{slug}."""
    m = parse_metric({
        "_links": {
            "self": {"href": f"/api/users/u/measurements/{BENCH}"},
            "tag": [{"href": "/api/users/u/tags/back"}],
        },
        "name": {"custom": "Archer Pull"},
        "measurementType": "EXERCISE",
    })
    assert m.body_part_raw == "back"
    assert classify(m.name, m.body_part_raw) == "back"


def test_equipment_tags_do_not_shadow_the_muscle_tag():
    m = parse_metric({
        "_links": {
            "self": {"href": f"/api/users/u/measurements/{SQUAT}"},
            "tag": [{"href": "/api/users/u/tags/barbell"},
                    {"href": "/api/users/u/tags/upper_legs"}],
        },
        "name": {"custom": "Squat"},
        "measurementType": "EXERCISE",
    })
    assert classify(m.name, m.body_part_raw) == "legs"


def test_non_exercise_measurements_are_ignored():
    assert parse_metric({
        "id": "x", "name": {"custom": "Bodyweight"},
        "measurementType": "BODY_MEASUREMENT",
    }) is None


def test_workout_name_is_never_used_as_an_exercise_name():
    """`name.custom` on a LOG is the session title, not the exercise."""
    pages = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sessions = parse_log(pages[0]["_embedded"]["log"][0])
    assert all(s.name_hint is None for s in sessions)


def test_unresolved_exercises_are_dropped_entirely():
    records, _ = load()
    files, unresolved = build_files(records, {})  # nothing resolves
    everything = "\n".join(files.values())
    assert "MIDDAY WORKOUT" not in everything
    # Unresolved exercises are dropped entirely, not labelled "Unknown".
    assert "UNKNOWN EXERCISE" not in everything
    assert len(unresolved) == 6


def test_parse_metric_falls_back_to_self_href():
    m = parse_metric({
        "_links": {"self": {"href": f"/api/users/u/measurements/{BENCH}"}},
        "name": {"custom": "Bench Press"},
        "measurementType": "EXERCISE",
    })
    assert m.metric_id == BENCH


# --- best-set selection ----------------------------------------------------


def test_best_is_highest_weight_not_most_recent():
    records, _ = load()
    bench = records[BENCH]
    assert bench.latest.date.strftime("%Y-%m-%d") == "2026-08-01"
    assert bench.best.date.strftime("%Y-%m-%d") == "2026-07-15"


def test_reps_break_a_weight_tie():
    # log-2 has 185x5, log-3 has 185x3 -> log-2 wins on reps.
    records, _ = load()
    top = best_set_of(records[BENCH].best)
    assert (top.weight, top.reps) == (185.0, 5)


def test_recency_breaks_a_full_tie():
    # Curl is 30x12 on both 07-01 and 08-01 -> newer session wins.
    records, _ = load()
    assert records[CURL].best.date.strftime("%Y-%m-%d") == "2026-08-01"


def test_warmup_sets_are_excluded_from_best():
    # log-3 bench has a 225 warmup; it must not become the PR.
    records, _ = load()
    assert best_set_of(records[BENCH].best).weight == 185.0


# --- bucketing -------------------------------------------------------------


def test_classify_uses_body_part_when_present():
    assert classify("Squat (Barbell)", "Quadriceps") == "legs"
    assert classify("Bicep Curl", "Biceps") == "arms"
    assert classify("Plank", "Abdominals") == "core"
    assert classify("Overhead Press", "Shoulders") == "shoulders"


def test_classify_falls_back_to_exercise_name():
    assert classify("Lat Pulldown (Cable)", None) == "back"
    assert classify("Incline Bench Press", None) == "chest"


def test_unknown_exercises_go_to_other_not_dropped():
    assert classify("Sled Push", "Cardio") == "other"
    assert classify("Zercher Widget", None) == "other"


# --- rendering -------------------------------------------------------------


def test_render_matches_expected_format():
    records, _ = load()
    text = render_exercise("Bench Press (Dumbbell)", records[BENCH])
    lines = text.splitlines()
    assert lines[0] == "BENCH PRESS (DUMBBELL)"
    assert lines[1] == "  Latest Workout: 2026-08-01"
    assert lines[2] == "    Set 2 ---- 3 reps ----- 185 lb"
    assert "---------" in text
    assert "  Best Workout: 2026-07-15 (PR: 5 reps @ 185 lb)" in lines


def test_build_files_creates_every_body_part():
    records, metrics = load()
    files, _ = build_files(records, metrics)
    assert set(files) == {"shoulders", "chest", "legs", "arms", "back", "core", "other"}
    assert "BENCH PRESS (DUMBBELL)" in files["chest"]
    assert "SQUAT (BARBELL)" in files["legs"]
    assert "LAT PULLDOWN (CABLE)" in files["back"]
    assert "SLED PUSH" in files["other"]
    assert "no exercises logged yet" in files["shoulders"]


# --- incremental behaviour -------------------------------------------------


def test_cache_roundtrip_is_lossless():
    records, _ = load()
    restored = records_from_dict(records_to_dict(records))
    assert set(restored) == set(records)
    assert restored[BENCH].best.date == records[BENCH].best.date
    assert best_set_of(restored[BENCH].best).weight == 185.0


def make_log(log_id: str, date: str, metric_id: str, sets: list[tuple[int, float]]):
    """Build a log in the real cellSetGroup shape. `sets` are (reps, pounds)."""
    from strong_sync.config import KG_TO_LB

    cell_sets = []
    for reps, lb in sets:
        cell_sets.append({
            "cells": [
                {"cellType": "REPS", "value": str(reps)},
                {"cellType": "DUMBBELL_WEIGHT", "value": str(lb / KG_TO_LB)},
            ],
            "isCompleted": True,
        })
    return {
        "id": log_id,
        "startDate": date,
        "logType": "WORKOUT",
        "_embedded": {"cellSetGroup": [{
            "_links": {"measurement": {"href": f"/api/users/u/measurements/{metric_id}"}},
            "cellSets": cell_sets,
        }]},
    }


def test_delta_sync_preserves_older_pr():
    """Simulate: full history cached, then a delta page with only a weak session."""
    records, _ = load()
    cached = records_from_dict(records_to_dict(records))

    delta = parse_log(make_log("log-4", "2026-08-10T16:00:00Z", BENCH, [(10, 95)]))
    merge_sessions(cached, delta)

    assert cached[BENCH].latest.date.strftime("%Y-%m-%d") == "2026-08-10"
    assert cached[BENCH].best.date.strftime("%Y-%m-%d") == "2026-07-15"


def test_new_pr_replaces_old_one():
    records, _ = load()
    merge_sessions(
        records,
        parse_log(make_log("log-5", "2026-08-11T16:00:00Z", BENCH, [(2, 205)])),
    )
    assert records[BENCH].best.date.strftime("%Y-%m-%d") == "2026-08-11"
    assert best_set_of(records[BENCH].best).weight == 205.0


def test_empty_record_renders_without_crashing():
    text = render_exercise("Ghost Lift", ExerciseRecord(metric_id="none"))
    assert "(none)" in text


def test_duplicate_names_merge_into_one_entry():
    """A legacy untagged copy must not create a second entry in another file."""
    from strong_sync.aggregate import ExerciseRecord, merge_sessions
    from strong_sync.parsing import Metric

    legacy_id = "cccccccc-0000-0000-0000-000000000001"
    records, metrics = load()
    merge_sessions(records, parse_log(make_log("legacy", "2024-06-06T12:00:00Z", legacy_id, [(5, 25)])))
    metrics[legacy_id] = Metric(legacy_id, "Bicep Curl (Dumbbell)", None)

    files, _ = build_files(records, metrics)
    assert files["arms"].count("BICEP CURL (DUMBBELL)") == 1
    assert "BICEP CURL" not in files["other"]


def test_tagged_copy_wins_the_bucket_over_untagged_copy():
    from strong_sync.aggregate import merge_sessions
    from strong_sync.parsing import Metric

    untagged = "dddddddd-0000-0000-0000-000000000002"
    tagged = "dddddddd-0000-0000-0000-000000000003"
    records = merge_sessions({}, parse_log(make_log("l1", "2024-01-01T12:00:00Z", untagged, [(5, 20)])))
    merge_sessions(records, parse_log(make_log("l2", "2025-01-01T12:00:00Z", tagged, [(5, 25)])))
    metrics = {
        untagged: Metric(untagged, "JM Press", None),
        tagged: Metric(tagged, "JM Press", "upper_arms"),
    }
    files, _ = build_files(records, metrics)
    assert "JM PRESS" in files["arms"]
    assert "JM PRESS" not in files["other"]
