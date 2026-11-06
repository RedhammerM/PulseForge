"""Workout Planner CSV (Obsidian vault) to a table of sets. The vault is never written."""

import csv
import io
import re
from dataclasses import dataclass

import pandas as pd

from ..garmin.rawstore import RawStore

REQUIRED = {"exercise", "reps", "weight", "workout", "timestamp", "notes", "weightUnit"}
LB_TO_KG = 0.45359237
RPE_NOTE = re.compile(r"\brpe\s*[=:]?\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
SET_COLS = ["set_time_utc", "exercise", "workout", "reps", "weight_kg", "note_rpe"]


@dataclass
class ImportResult:
    sets: pd.DataFrame
    errors: list[str]


def note_rpe(text: str) -> float | None:
    """Read an RPE such as `rpe=7` from a set's notes; out-of-range values give None."""
    m = RPE_NOTE.search(text or "")
    if m is None:
        return None
    value = float(m.group(1).replace(",", "."))
    return value if 0 <= value <= 10 else None


def parse_sets(content: bytes) -> ImportResult:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    missing = REQUIRED - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"workout CSV is missing columns: {sorted(missing)}")
    rows, errors = [], []
    for line, r in enumerate(reader, start=2):
        try:
            weight = float(r["weight"] or 0)
            unit = r["weightUnit"].strip().lower()
            if unit not in ("kg", "lb", "lbs"):
                raise ValueError(f"unknown weight unit {unit!r}")
            rows.append(
                {
                    "set_time_utc": pd.Timestamp(int(r["timestamp"]), unit="ms", tz="UTC"),
                    "exercise": r["exercise"].strip(),
                    "workout": r["workout"].strip(),
                    "reps": int(r["reps"]),
                    "weight_kg": weight if unit == "kg" else weight * LB_TO_KG,
                    "note_rpe": note_rpe(r["notes"]),
                }
            )
        except (TypeError, ValueError, AttributeError) as e:
            errors.append(f"line {line}: {e}")
    sets = pd.DataFrame(rows, columns=SET_COLS).sort_values("set_time_utc", ignore_index=True)
    return ImportResult(sets, errors)


def snapshot(cfg) -> bool:
    """Copy the CSV into the raw store if it changed. False if the file is absent."""
    if not cfg.workout_csv.exists():
        return False
    RawStore(cfg.workout_raw_dir).save(
        "obsidian", "workout_logs", cfg.workout_csv.read_bytes(), ".csv"
    )
    return True


def load_latest(cfg) -> ImportResult:
    path = RawStore(cfg.workout_raw_dir).latest("obsidian", "workout_logs", ".csv")
    if path is None:
        return ImportResult(pd.DataFrame(columns=SET_COLS), [])
    return parse_sets(path.read_bytes())
