from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from . import config, state as state_mod
from .aggregate import ExerciseRecord, merge_sessions, records_from_dict, records_to_dict
from .api import StrongAPIError, StrongClient
from .parsing import Metric, parse_log, parse_metric
from .render import build_files, write_files

log = logging.getLogger("strong_sync")


def collect_pages(pages: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    logs, metrics = [], []
    for page in pages:
        embedded = page.get("_embedded", {}) or {}
        logs.extend(embedded.get("log") or [])
        metrics.extend(embedded.get("measurement") or [])
    return logs, metrics


def fetch_pages(continuation: str | None) -> tuple[list[dict[str, Any]], str | None]:
    client = StrongClient()
    token, user_id = config.static_token()
    if token and user_id:
        client.use_static_token(token, user_id)
    else:
        username, password = config.credentials()
        client.login(username, password)
        log.info("Logged in as user %s", client.user_id)
    pages = list(client.sync_pages(["log", "measurement"], continuation=continuation))
    log.info("Fetched %d page(s)", len(pages))
    return pages, getattr(client, "last_continuation", None)


def load_fixture(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]


def run_sync(args: argparse.Namespace) -> int:
    st = state_mod.load_state()
    full = args.full_resync

    records: dict[str, ExerciseRecord] = {} if full else records_from_dict(state_mod.load_cache())
    metrics_raw: dict[str, Any] = {} if full else state_mod.load_metrics()
    seen_ids: set[str] = set() if full else set(st.get("seen_log_ids", []))
    continuation = None if full else st.get("continuation")

    fetch_error: str | None = None
    try:
        if args.fixture:
            pages = load_fixture(Path(args.fixture))
            new_continuation = continuation
            log.info("Loaded %d page(s) from fixture %s", len(pages), args.fixture)
        else:
            pages, new_continuation = fetch_pages(continuation)
    except (StrongAPIError, OSError, json.JSONDecodeError) as exc:
        fetch_error = str(exc)
        log.error("Fetch failed: %s", exc)
        pages, new_continuation = [], continuation

    raw_logs, raw_metrics = collect_pages(pages)
    log.info("Received %d log record(s), %d metric record(s)", len(raw_logs), len(raw_metrics))

    for raw in raw_metrics:
        metric = parse_metric(raw)
        if metric:  # parse_metric returns None for non-EXERCISE records
            metrics_raw[metric.metric_id] = {
                "name": metric.name,
                "body_part_raw": metric.body_part_raw,
            }

    new_logs, sessions = 0, []
    for raw in raw_logs:
        parsed = parse_log(raw)
        if not parsed:
            continue
        log_id = parsed[0].log_id or f"{parsed[0].date.isoformat()}"
        if log_id not in seen_ids:
            new_logs += 1
            seen_ids.add(log_id)
        sessions.extend(parsed)

    merge_sessions(records, sessions)
    log.info("New workouts this run: %d | exercises tracked: %d", new_logs, len(records))

    metrics = {
        mid: Metric(mid, m["name"], m.get("body_part_raw")) for mid, m in metrics_raw.items()
    }
    files, unresolved = build_files(records, metrics)
    if unresolved:
        log.info(
            "Skipped %d exercise(s) with no measurement record (deleted from your "
            "Strong account); they are not written to any file.", len(unresolved)
        )
        log.debug("Unresolved ids: %s", ", ".join(sorted(unresolved)))

    if args.dry_run:
        for part, content in files.items():
            print(f"\n===== data/{part}.txt =====\n{content}")
        print(f"\n[dry-run] new workouts: {new_logs} | nothing written")
        return 0

    if fetch_error:
        # The fetch failed. Writing now would risk overwriting good files with
        # an empty rebuild (e.g. if the cache was also missing). Advance the run
        # counter the script did run today but leave the data alone.
        log.warning("Fetch failed; leaving data/ untouched and only bumping the counter.")
        changed = []
    else:
        changed = write_files(files)
        state_mod.save_cache(records_to_dict(records))
        state_mod.save_metrics(metrics_raw)

    st["run_count"] = int(st.get("run_count", 0)) + 1
    st["first_run"] = st.get("first_run") or state_mod.now_iso()
    st["last_run"] = state_mod.now_iso()
    if not fetch_error:
        st["continuation"] = new_continuation
        st["seen_log_ids"] = sorted(seen_ids)
        st["total_workouts"] = len(seen_ids)
        st["last_successful_run"] = state_mod.now_iso()
        if new_logs:
            st["last_new_workout_at"] = state_mod.now_iso()
    state_mod.save_state(st)
    state_mod.write_run_count(st)

    log.info("Run #%d complete. Files changed: %s", st["run_count"], changed or "none")
    if fetch_error:
        log.error("Run counter advanced but the fetch failed.")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="strong_sync")
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="Fetch, update text files, bump the counter")
    sync.add_argument("--full-resync", action="store_true",
                      help="Ignore the saved continuation token and rebuild from scratch")
    sync.add_argument("--dry-run", action="store_true",
                      help="Print what would be written without touching any files")
    sync.add_argument("--fixture", metavar="PATH",
                      help="Read pages from a local JSON file instead of the network")
    sync.add_argument("-v", "--verbose", action="store_true")
    sync.set_defaults(func=run_sync)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
