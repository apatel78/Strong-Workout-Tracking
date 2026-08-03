# Testing Plan

Work through these phases in order. Each one isolates a different failure mode, so when
something breaks you know which layer to look at. Don't enable the schedule until
Phase 7 passes.

---

## Phase 0 — Offline logic (no credentials, no network)

Proves the parsing, PR-selection, bucketing, and rendering logic is correct before any
network variable enters the picture.

```bash
pytest -q                    # or: python tests/run_tests.py
```

**Expect:** 16 passed.

The suite specifically pins down the rules that are easy to get subtly wrong:

| Test | What it proves |
|---|---|
| `test_best_is_highest_weight_not_most_recent` | Best ≠ latest |
| `test_reps_break_a_weight_tie` | 185×5 beats 185×3 |
| `test_recency_breaks_a_full_tie` | Identical weight *and* reps → newer wins |
| `test_warmup_sets_are_excluded_from_best` | A 225 warmup doesn't become your PR |
| `test_unknown_exercises_go_to_other_not_dropped` | Nothing vanishes silently |
| `test_delta_sync_preserves_older_pr` | A weak new session doesn't erase an old PR |
| `test_parser_tolerates_alternate_field_names` | Parser survives a schema guess being wrong |

Then run the whole pipeline offline:

```bash
python -m strong_sync.cli sync --fixture tests/fixtures/sample_page.json --dry-run
```

**Expect:** seven file bodies printed; bench under CHEST, squat under LEGS, lat pulldown
under BACK (classified by name, since that fixture metric has no `bodyPart`), sled push
under OTHER, `no exercises logged yet` under SHOULDERS.

---

## Phase 1 — Authentication

The single most likely blocker: `/auth/login` returns a userId, but you also need
whatever credential authorizes the sync call.

```bash
python -m tools.explore login
```

**Look for, in order of likelihood:**

1. A token field → the client sets `Authorization: Bearer …` automatically. Confirm the
   printed `AUTH HEADER SET?` is `True`.
2. No token but cookies listed → `requests.Session` carries them; that's fine.
3. Neither → the credential is under a key not in `TOKEN_KEYS` (in `api.py`), or it comes
   back in a response header. Add the key name to that tuple.

**If it's a JWT**, decode the payload at jwt.io (paste only the middle segment) and check
the `exp` claim. If it expires in under 24h you must log in on every run — which this
script already does, so no change needed. Just don't add token caching later.

**Red flag:** login returns 200 but the body has no id-like field → you're hitting a
different endpoint shape than expected; print `resp.text` before assuming.

---

## Phase 1b — Auth notes (confirmed behaviour)

Login is solved; this records what was learned so future-you isn't confused.

- **The edge blocks unknown clients.** `python-requests` and `curl` both get a bare
  `403 Forbidden` (9 bytes, `text/html`). The `User-Agent: Strong iOS` plus
  `X-Client-Version` / `X-Client-Build` / `X-Client-Platform` headers are required.
  These are already the defaults in `api.py`.
- **`deviceId` is required in the login body**, and it is what MFA keys off. An
  unrecognized device gets `403 {"code":"MFA_REQUIRED"}` with an emailed code. Set
  `STRONG_DEVICE_ID` to the UUID your phone sends and the challenge disappears —
  a trusted device isn't challenged.
- **Auth is a bearer token**, `accessToken` from the login response, with a
  `refreshToken` alongside. ASP.NET Identity cookies (`.AspNetCore.Identity.Application`,
  plus Azure `ARRAffinity` affinity cookies) are also set; `requests.Session` carries
  them, so don't build a second Session.
- **The access token lives ~20 minutes** (`expiresIn: 1200`). `sync_pages` detects a
  mid-run 401 and simply logs in again rather than using the refresh endpoint — fewer
  unknowns, and it costs at most one extra request per 20 minutes.

If MFA reappears later, `python -m tools.explore login` will print the challenge, the
verify URL, and the expiry.

**Watch for this in CI:** GitHub's runners come from datacenter IPs in a different
country than your phone. If MFA is scored on IP as well as device, the scheduled job may
be challenged even though your laptop isn't. Phase 9 tests this — do the manual dispatch
before trusting the schedule. If CI does get challenged, the fallbacks are a self-hosted
runner on your own machine, or dropping GitHub Actions and running the script from a
local cron / Task Scheduler job that pushes to the repo.

---

## Phase 2 — Schema verification

The log schema is now confirmed and `parsing.py` targets it directly:

```
log.startDate                                  workout date
log.logType == "WORKOUT"                       non-workout logs are skipped
log._embedded.cellSetGroup[]                   ONE PER EXERCISE
  ._links.measurement.href                     exercise id, only as a UUID in a URL
  .cellSets[]                                  one per set -- AND per rest-timer row
    .isCompleted
    .cells[] {cellType, value}                 value is a STRING
      REPS, *_WEIGHT, RPE, REST_TIMER
```

Three traps, all covered by tests:

- **The exercise id is only in a link href.** There is no `metricId` field.
- **REST_TIMER cellSets are not sets.** Counting them inflates every set count and
  injects phantom 0-rep sets.
- **Weights are kilograms**, always, even though your account displays pounds
  (`22.6796185` is a 50 lb dumbbell). Conversion happens once, in `parsing.py`, so
  everything downstream is already in `STRONG_WEIGHT_UNIT`.

**Exercises are the `measurement` collection, not `metric`.** `metric` returns nothing.
You can confirm the linkage yourself: the measurement UUIDs in your logs are the same
keys as in your account's `preferences.restTimer` map.

### measurement (the exercise catalog) — also confirmed

```
measurement.id
           .name.custom                        exercise name
           .measurementType == "EXERCISE"      body measurements are filtered out
           ._links.tag[].href                  BODY PART, as /tags/back
           .cellTypeConfigs[]                  which cell types this exercise uses
```

**There is no `bodyPart` field.** The body part is a tag link — `/api/users/{u}/tags/back`
— and the slug is the last path segment. An exercise can carry several tags, so
`first_recognized_tag()` scans for one that maps rather than taking `tags[0]`, which
would let an equipment tag like `barbell` shadow the muscle tag.

**Tag vocabulary check.** Slugs use underscores (`upper_legs`, `upper_arms`). After your
first `--full-resync`, read `data/other.txt`: anything there carries a tag not yet in
`GROUP_MAP` in `bodyparts.py`. Add it and re-run.

Then capture a real page for offline iteration:

```bash
python -m tools.explore fixture
python -m strong_sync.cli sync --fixture tests/fixtures/live_sample.json --dry-run
```

That file is gitignored. Review it for personal data before doing anything else with it.

**Red flag:** `Received N log record(s)` but `exercises tracked: 0` → the parser found
logs but couldn't read the exercise nesting. Go back to `LOG_EXERCISES` in `parsing.py`.

---

## Phase 3 — Full read against the live API, writing nothing

```bash
python -m strong_sync.cli sync --full-resync --dry-run -v
```

**Sanity-check against the Strong app itself.** Pick 3–4 exercises you know well:

- Does `Latest Workout` match the last date you actually did that lift?
- Does `Best Workout` match the PR the app shows you?
- Does the total exercise count roughly match your app's exercise list?
- **Are the weights the right magnitude?** If your 185 lb bench shows as ~84, the API is
  serving kilograms — set `STRONG_CONVERT_KG_TO_LB=1` in `.env`.

**Red flag:** every exercise lands in `other` → the body-part field name is wrong or uses
vocabulary not in `GROUP_MAP`. Fix `bodyparts.py` now; it's much easier before there are
committed files to diff.

---

## Phase 4 — First real write

```bash
python -m strong_sync.cli sync --full-resync -v
git status
```

**Expect:** modified files under `data/`, an updated `state.json`, and
`data/_cache/exercises.json`.

Now read `data/other.txt` carefully. **Every exercise in there is one your mapping
missed.** Add its muscle group to `GROUP_MAP` or its name pattern to `NAME_HINTS`, then
re-run with `--full-resync`. Repeat until `other.txt` contains only things that genuinely
belong there (cardio, full-body, etc.).

Check `state.json`: `run_count` should be `1` (it ships committed at `0`),
`total_workouts` should be close to your app's lifetime workout count, and `continuation`
should be a non-null token.

---

## Phase 5 — Is the continuation token actually a delta cursor?

This determines whether "check if any new workouts have been done" is cheap or expensive.
It's the one assumption in the design worth testing directly.

1. Note the current `continuation` value in `state.json`.
2. Run `python -m strong_sync.cli sync -v` again immediately.
3. Read the log line `Received N log record(s)`.

| Result | Meaning | Action |
|---|---|---|
| `Received 0` | True delta cursor. Nothing changed, so nothing came back. | Ideal — keep as is. |
| `Received <all your logs>` | The token is an offset/no-op; you get a full replay. | Still correct, just wasteful. Fine at your data size. |
| An error or 400 | The token expired or isn't reusable across sessions. | Set `continuation` to `null` in `state.json` and treat every run as full. |

Either way the output files stay correct — the cache and `seen_log_ids` make new-workout
detection independent of how the pagination behaves. This phase only tells you how much
data crosses the wire.

---

## Phase 6 — Idempotency

```bash
python -m strong_sync.cli sync -v
git diff --stat
```

**Expect:** `data/*.txt` unchanged. Only `state.json` and `data/run_count.txt` differ
(counter and timestamps).

**Red flag:** body-part files change on a no-op run → nondeterministic ordering somewhere.
Everything is sorted by design, so this would mean two exercises share a name or a metric
ID is unstable.

---

## Phase 7 — New workout detection (the real end-to-end test)

1. Log a workout in the Strong app — a real one, or a throwaway with one set of one
   exercise. Make sure it syncs from your phone.
2. Run `python -m strong_sync.cli sync -v`.
3. **Expect:** `New workouts this run: 1`, `total_workouts` incremented by 1 in
   `state.json`, and that exercise's `Latest Workout` block updated in the right file.
4. If you deliberately used a weight above your PR, confirm the `Best Workout` block moved
   too. If below, confirm it did **not**.

If you logged a throwaway, delete it in the app afterward. Note that `seen_log_ids` will
still contain it — harmless, but run `--full-resync` if you want the counts to be exact.

---

## Phase 8 — Failure modes

Test these deliberately; each should fail loudly and leave the repo in a sane state.

```bash
# Wrong password
STRONG_PASSWORD=definitelywrong python -m strong_sync.cli sync -v
```
**Expect:** clear `Login failed (4xx)` message, exit code 1, `data/*.txt` **unchanged**.

This is deliberate: on any fetch error the script skips writing entirely rather than
rebuilding from a possibly-empty cache, and leaves `continuation` / `seen_log_ids` at
their last-good values. Only the run counter and `last_run` advance.

```bash
# No credentials at all
env -u STRONG_USERNAME -u STRONG_PASSWORD python -m strong_sync.cli sync
```
**Expect:** the "Missing credentials" message, not a traceback.

```bash
# Corrupted state
cp state.json state.json.bak && echo 'not json' > state.json
python -m strong_sync.cli sync -v
cp state.json.bak state.json   # restore when done
```
**Expect:** falls back to defaults and runs rather than crashing. (Note the run counter
resets — that's the tradeoff for resilience. `state.json` is in git, so recover the real
number with `git show HEAD:state.json` if needed.)

Verify the counter still advances on a failed fetch: the run count is "days the script
ran", not "days it succeeded". `last_successful_run` is what tells you about health — if
`last_run` and `last_successful_run` drift apart, something is broken.

---

## Phase 9 — CI

1. Push the repo. The **Tests** workflow should run on push and pass.
2. Add `STRONG_USERNAME` / `STRONG_PASSWORD` as repository secrets.
3. Set *Settings → Actions → General → Workflow permissions* to **Read and write**.
4. Trigger **Daily Strong Sync** manually via *Actions → Run workflow*.
5. **Expect:** a green run and a `chore: strong sync <date>` commit from `strong-sync-bot`.

**Common CI-only failures:**

| Symptom | Cause |
|---|---|
| `Permission denied` on push | Workflow permissions still read-only |
| Login works locally, 403 in CI | Backend rejecting the datacenter IP or missing a User-Agent — add one in `api.py` |
| Push rejected | You committed locally between runs; the retry loop's `git pull --rebase` handles most of this |
| Workflow never fires on schedule | Repo inactive 60+ days, or you're expecting punctuality — GitHub's cron regularly lags 5–30 min |

6. Only after a successful manual dispatch, leave the schedule enabled and check back the
   next morning.

---

## Ongoing monitoring

- Watch `data/run_count.txt` — if `Days run` stops advancing, the schedule stopped firing.
- Watch `last_successful_run` vs `last_run` in `state.json` — divergence means the API
  side is failing while the job still runs.
- Skim `data/other.txt` occasionally — new exercise types land there.
- Re-run `--full-resync` after any change to `bodyparts.py` or the PR-ranking logic.
