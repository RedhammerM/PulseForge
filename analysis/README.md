# physio-state

Garmin sync, workout import and analysis tables for the physiology project.
Acquisition stays in `physio_daq`; this package has no Qt dependency.

## Setup

```bash
uv sync --all-packages
cp analysis/config.example.toml ~/.config/physio/config.toml   # optional
```

The Garmin key is read by Garmin's client from
`~/.config/garmin/connect-personal-api-key` (mode 600), never from this repo.

## Commands

```bash
uv run physio-state garmin sync       # last 3 days, new activities, workout CSV, rebuild
uv run physio-state garmin status     # what is stored, last run, key expiry
uv run physio-state garmin backfill --start 2026-05-01 --end 2026-10-31   # optional
uv run physio-state rebuild           # all tables from raw, no network
```

Daily timer (10:00, catches up at next login if the PC was off):

```bash
cp deploy/systemd/physio-garmin-sync.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now physio-garmin-sync.timer
journalctl --user -u physio-garmin-sync     # logs
```

## Data layout (`data/`, git-ignored)

```
garmin/raw/<endpoint>/<date or id>_<fetch time>.json|zip   untouched responses
garmin/raw/manifest.jsonl                                  SHA-256 and fetch time
garmin/sync_runs.jsonl                                     one line per run
training/raw/obsidian/                                     snapshots of the workout CSV
tables/*.parquet                                           parsed tables
physio.duckdb                                              views over tables
```

A new raw version is written only when the content changed. `tables/` and
`physio.duckdb` can be deleted and rebuilt. Query with DuckDB:

```python
import duckdb
con = duckdb.connect("data/physio.duckdb", read_only=True)
con.sql("select day, hrv_ln_mean, garmin_status from hrv_nightly order by day").df()
```

**Backup.** `recordings/` and `data/` are git-ignored and nothing backs them up
automatically. Recordings cannot be re-fetched, so copy both to an encrypted
disk regularly.

## Tables

| Table | One row per | Notes |
|---|---|---|
| `hrv_readings` | 5-minute reading | |
| `hrv_nightly` | night | own aggregates from readings; `garmin_*` are benchmarks |
| `sleep_nightly` | night | |
| `hr_2min`, `hr_daily` | sample, day | |
| `daily_summary` | calendar day | |
| `activities` | activity | no title, location, owner or GPS fields |
| `activity_samples`, `activity_rr` | second, RR interval | from FIT; `activity_rr` needs a strap that logs HRV |
| `hr_zones` | sport and stored version | |
| `workout_sets`, `workouts` | set, workout | from the Obsidian CSV |
| `load_events` | training event | a gym workout merges with its Garmin strength activity |
| `load_daily` | day | `srpe_load` is null if any event that day lacks RPE or duration |
| `sync_runs` | run | |

`day` is the wake-up date. Times are UTC with `utc_offset_min`. Columns starting
`garmin_` are vendor outputs: benchmarks, never anchors or ground truth.

## Logging RPE

Add `rpe=7` to the notes of any set in a workout; the last one in the workout
counts. Session RPE load is RPE × duration, with duration taken from the matching
Garmin activity, or from first to last set if there is none. A single logged set
with no Garmin activity has no duration, so its load stays null.
