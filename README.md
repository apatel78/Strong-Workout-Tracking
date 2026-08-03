# Strong Workout Tracker

A daily automation to pull workout history from Strong App and publish the latest set, and best set
(determined by weight, then reps, then most recent date) for each individual exercise. 
These are stored under the data folder, seperated by body part.

---

## Data Representations
The body parts currently supported are as follows: Shoulders, Chest, Back, Arms, Legs, Core, and other.
There is also an additional file that tracks useful information about how often the automation is run.

Each excercise renders as follows:
```
BICEP CURL (CABLE)
  Latest Workout: 2025-03-23
    Set 1 ---- 8 reps ----- 19 lb
    Set 2 ---- 8 reps ----- 19 lb
  ---------
  Best Workout: 2025-02-18 (PR: 7 reps @ 30 lb)
    Set 1 ---- 8 reps ----- 25 lb
    Set 2 ---- 8 reps ----- 25 lb
    Set 3 ---- 7 reps ----- 30 lb
```

---

## Why the API host isn't in this repo
Strong App has no official or public API, so the API endpoints are hidden and need to be found
by the user. Same goes for device id, which should be configured to the users device in order
to avoid complications with multi factor authentication. 

---

## What it does

1. Logs in and pulls workout logs plus the exercise catalog.
2. Folds new sessions into a cache of per-exercise latest/best records.
3. Rewrites the body-part text files.
4. Increments a counter of how many days the script has run.
5. Commits and pushes anything that changed.

A few decisions worth knowing about:

- **The counter increments on every run**, workout or not — it measures days run, not
  days succeeded. `last_successful_run` in `state.json` is the health signal.
- **Deleted exercises are dropped**, not written as "Unknown Exercise".
- **Duplicate names are merged.** Strong can hold a legacy id and a current id for the
  same lift; without merging you'd see it twice, and in two different files if only one
  copy carries a body-part tag.
- **Weights are stored in kilograms** by the API and converted once, at parse time.

---

## 1. Get it running locally

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements-dev.txt
```

Then create your `.env`:

```bash
cp .env.example .env
```

Fill in all four values:

| Variable | What it is |
|---|---|
| `STRONG_API_BASE` | The API host. No default — you must supply it. |
| `STRONG_USERNAME` | Your Strong account email |
| `STRONG_PASSWORD` | Your Strong password |
| `STRONG_DEVICE_ID` | The device UUID your phone sends |

`.env` is gitignored. Never commit it — once a secret is pushed to a public repo,
deleting the commit does not undo it.

`STRONG_DEVICE_ID` is not optional. A login from a device Strong has never seen gets
answered with an MFA email challenge instead of a token, and no unattended job can
complete that. Use the UUID from your own proxy capture of the app's login request.

---

## 2. Test it locally

Work outward: logic first with no network, then live reads, then writes.

### Offline — no credentials needed

```bash
pytest -q                    # or: python tests/run_tests.py
```

29 tests covering the parts that are easy to get subtly wrong: rest-timer rows that
masquerade as sets, kilogram conversion, PR tiebreaks, duplicate merging, and making
sure a weak new session never erases an older PR.

Then run the whole pipeline against a fixture:

```bash
python -m strong_sync.cli sync --fixture tests/fixtures/sample_page.json --dry-run
```

### Live — read-only

```bash
python -m tools.explore login     # confirms auth; prints no secrets
python -m tools.explore schema    # shape of a log and a measurement
python -m tools.explore counts    # record totals per collection
```

Then a full read that writes nothing:

```bash
python -m strong_sync.cli sync --full-resync --dry-run -v
```

**Sanity-check the output against the Strong app.** Pick a few lifts you know well: does
`Latest Workout` match when you last did it? Does `Best Workout` match the PR the app
shows? Are the weights the right magnitude?

### First real write

```bash
python -m strong_sync.cli sync --full-resync -v
git status
```

Now read `data/other.txt` — **everything in there is an exercise whose tag isn't in the
mapping yet.** Add the tag slug to `GROUP_MAP` in `strong_sync/bodyparts.py` (or a name
pattern to `NAME_HINTS`) and re-run with `--full-resync`. Repeat until only genuine
oddballs remain.

### Confirm it's idempotent

```bash
python -m strong_sync.cli sync -v
git diff --stat
```

The body-part files should be unchanged. Only `state.json` and `data/run_count.txt`
should differ. If a text file changes on a no-op run, something is nondeterministic.

Full phase-by-phase walkthrough, including failure modes and schema notes, is in
**[TESTING.md](TESTING.md)**.

### All commands

```bash
python -m strong_sync.cli sync                 # normal daily run
python -m strong_sync.cli sync --dry-run       # print, write nothing
python -m strong_sync.cli sync --full-resync   # rebuild from scratch
python -m strong_sync.cli sync --fixture PATH  # read a local JSON file, not the network
python -m strong_sync.cli sync -v              # verbose
```

---

## 3. Set up the GitHub Action

### Step 1 — Push the code

Before the first push, confirm nothing secret is staged:

```bash
git add -A
git status --short
```

**`.env` must not appear.** Neither should `tests/fixtures/live_sample.json` or
`.strong-session.json`. If any do, fix `.gitignore` before committing.

> The `.gitignore` here is a **denylist** — everything is tracked except what's named.
> An allowlist (`*` plus `!` exceptions) silently excludes `strong_sync/` and
> `.github/workflows/`, and Actions cannot run a workflow that isn't in the repo.

```bash
git commit -m "Add sync package and workflow"
git push
```

### Step 2 — Add the four secrets

**Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `STRONG_API_BASE` | The API host |
| `STRONG_USERNAME` | Your Strong email |
| `STRONG_PASSWORD` | Your Strong password |
| `STRONG_DEVICE_ID` | Your device UUID |

All four are required; none has a default in the code. Secrets are write-only — you can
replace them but never read them back, so keep your local `.env` as the copy of record.

### Step 3 — Allow the workflow to push

**Settings → Actions → General → Workflow permissions → Read and write permissions →
Save**

The default is read-only. Without this the sync runs fine and then fails on `git push`
with a 403 — a confusing way to find out.

### Step 4 — Run it manually first

Don't wait for the schedule. **Actions → Daily Strong Sync → Run workflow.**

A healthy log looks like:

```
INFO Logged in as user <id>
INFO Access token valid for 1200s (~20 min)
INFO Received N log record(s), M measurement record(s)
INFO Run #N complete.
```

Then check for a `chore: strong sync <date>` commit from `strong-sync-bot`.

### Step 5 — Understand the schedule

```yaml
- cron: "0 22 * * *"      # 22:00 UTC = 5:00 PM EST
```

GitHub cron is **UTC only and ignores daylight saving**. From March to November (EDT)
this fires at 6:00 PM Eastern. Either accept the one-hour drift, or edit it twice a
year: `"0 21 * * *"` during EDT, `"0 22 * * *"` during EST.

Expect lag too — GitHub's scheduler is best-effort and often runs 5–30 minutes late. A
22:20 run is not broken.

### Step 6 — Check back the next day

- `data/run_count.txt` — `Days run` should have incremented.
- `state.json` — `last_run` and `last_successful_run` should be close together. Drift
  between them means the job runs but the API side is failing.
- If you worked out, that exercise's `Latest Workout` should have moved.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `STRONG_API_BASE is not set` | Missing secret or `.env` value | Add it; there is no default |
| `Missing credentials` | Secret names differ | Names are case-sensitive |
| `403` with a short non-JSON body | Edge block on an unknown client | Client headers in `api.py` were altered |
| `403 {"code":"MFA_REQUIRED"}` | Device not trusted | Set `STRONG_DEVICE_ID`; for CI see below |
| `401` mid-sync | 20-minute token expired | Handled automatically — it re-authenticates |
| `Permission denied` on push | Workflow permissions | Step 3 |
| Everything lands in `other.txt` | Tag slugs not in `GROUP_MAP` | Add them, then `--full-resync` |

**If CI gets MFA-challenged:** GitHub's runners use datacenter IPs far from your phone.
If Strong scores MFA on IP as well as device, the scheduled job can be challenged even
though your laptop never is. Two options — a self-hosted runner (`runs-on: self-hosted`
on a machine that stays on), or drop Actions entirely and run the script from local cron,
which also handles daylight saving properly.

---

## Layout

```
strong_sync/
  api.py         login, client headers, paginated sync, token re-auth
  parsing.py     raw API records -> ExerciseSession / SetRecord / Metric
  aggregate.py   latest + all-time-best per exercise, merging
  bodyparts.py   tag slugs and name patterns -> the seven output files
  render.py      text file contents; drops unknowns, merges duplicates
  state.py       run counter, continuation token, seen log ids, cache
  cli.py         orchestration
tools/explore.py schema discovery and auth debugging
tests/           29 tests plus an offline fixture
data/            output files and the durable cache
```

**Why there's a cache.** The sync endpoint appears to be delta-based, so all-time PRs
can't be recomputed from a single page. `data/_cache/exercises.json` accumulates them and
new sessions are folded in. It's committed because CI needs it to survive between runs.

Rebuild with `--full-resync` after any change to `bodyparts.py` or the ranking logic.

---

## Maintenance

- Rotate your Strong password → update the `STRONG_PASSWORD` secret too, or the job
  starts failing silently the next day.
- New exercise types land in `other.txt`. Add the slug, then `--full-resync`.
- The run log reports how many exercises were skipped as deleted. A sudden jump means a
  measurement sync came back short, not that you deleted 20 exercises.
- This is an undocumented API with no stability guarantees. Expect header requirements
  and response shapes to change without notice; `tools/explore.py` exists for that day.
