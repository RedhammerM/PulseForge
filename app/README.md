# physio-app

Streamlit dashboards for the physiology project. Reads the tables built by
`physio-state` and writes only to `data/manual/` (food, journal, recording
notes) and to the session files in `recordings/`.

```bash
uv sync --all-packages
uv run physio-app            # opens http://localhost:8501, bound to this machine only
```

| Page | What it is for |
|---|---|
| Measure | Check-in ratings, recording defaults (sleep and last workout prefilled from Garmin), **Open live window** (the Qt viewer as a separate window), then the result: quality, cuff BP, your note |
| Measurements · Overview | AM/PM calendar, streak, tests, check-in ratings, BP seated vs standing, signal quality, all recordings |
| Measurements · Recording browser | ECG and PPG of any recording (overview and zoomed detail), events, leads-off stretches, metadata, session |
| Training | Daily load (Garmin, TRIMP, sRPE), weekly HR zones, night HRV with the Garmin range, resting HR, activities. Everything else: intervals.icu |
| Lifestyle | Log food (kcal, macros) and journal tags; weight, calories burned vs eaten, sleep stages, steps, stress |
| Data & sync | Garmin sync button, last runs, key expiry, coverage per day, backup paths |

The period selector in the sidebar applies to every page. Journal days appear as
◆ markers on the HRV, resting HR, sleep and stress charts.

The app re-reads a table only when its Parquet file changes. **Rebuild tables**
re-reads the recordings, logs and raw Garmin files without network calls (about a
second); the app also rebuilds when the live window closes.
