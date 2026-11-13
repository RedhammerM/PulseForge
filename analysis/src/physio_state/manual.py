"""Values typed in by hand: food intake, a daily journal, notes on recordings.

These files are the only copy (unlike `tables/`), so they belong in the backup.
Each file holds one entry per key (day or recording); saving the same key again
replaces the entry. Writes go through a temp file, so a crash never leaves half a file.
"""

import json
import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .config import Config
from .store import refresh_views, write_table

JOURNAL_TAGS = [
    "alcohol",
    "illness",
    "travel",
    "late meal",
    "late caffeine",
    "poor sleep environment",
    "stress event",
    "sauna / heat",
    "medication",
    "other",
]
QUALITY_FLAGS = ["good", "usable", "bad"]
NUTRITION_COLS = ["day", "kcal", "protein_g", "carbs_g", "fat_g", "note"]
JOURNAL_COLS = ["day", "tags", "note", "edited"]
RECORDING_NOTE_COLS = ["recording_id", "quality", "note", "edited"]
FILES = {
    "nutrition_daily": ("nutrition.jsonl", "day", NUTRITION_COLS),
    "journal": ("journal.jsonl", "day", JOURNAL_COLS),
    "recording_notes": ("recording_notes.jsonl", "recording_id", RECORDING_NOTE_COLS),
}


def _path(cfg: Config, table: str) -> Path:
    return cfg.manual_dir / FILES[table][0]


def _read(cfg: Config, table: str) -> list[dict]:
    path = _path(cfg, table)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _upsert(cfg: Config, table: str, entry: dict) -> None:
    _, key, _ = FILES[table]
    rows = [r for r in _read(cfg, table) if r[key] != entry[key]] + [entry]
    rows.sort(key=lambda r: r[key])
    path = _path(cfg, table)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    os.replace(tmp, path)


def _delete(cfg: Config, table: str, key_value: str) -> None:
    _, key, _ = FILES[table]
    rows = _read(cfg, table)
    kept = [r for r in rows if r[key] != key_value]
    if len(kept) != len(rows):
        path = _path(cfg, table)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r) + "\n" for r in kept))
        os.replace(tmp, path)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def save_nutrition(
    cfg: Config,
    day: date,
    kcal: int | None,
    protein_g: float | None = None,
    carbs_g: float | None = None,
    fat_g: float | None = None,
    note: str = "",
) -> None:
    if kcal is not None and not 0 <= kcal <= 15000:
        raise ValueError(f"implausible intake {kcal} kcal")
    _upsert(
        cfg,
        "nutrition_daily",
        {
            "day": day.isoformat(),
            "kcal": kcal,
            "protein_g": protein_g,
            "carbs_g": carbs_g,
            "fat_g": fat_g,
            "note": note,
        },
    )


def save_journal(cfg: Config, day: date, tags: list[str], note: str = "") -> None:
    """An entry with no tags and no note removes the day."""
    if not tags and not note.strip():
        _delete(cfg, "journal", day.isoformat())
        return
    _upsert(
        cfg,
        "journal",
        {"day": day.isoformat(), "tags": list(tags), "note": note.strip(), "edited": _now()},
    )


def save_recording_note(cfg: Config, recording_id: str, quality: str | None, note: str) -> None:
    if quality is not None and quality not in QUALITY_FLAGS:
        raise ValueError(f"unknown quality flag {quality!r}")
    _upsert(
        cfg,
        "recording_notes",
        {"recording_id": recording_id, "quality": quality, "note": note.strip(), "edited": _now()},
    )


def load(cfg: Config, table: str) -> pd.DataFrame:
    _, key, cols = FILES[table]
    df = pd.DataFrame(_read(cfg, table), columns=cols)
    if key == "day":
        df["day"] = pd.to_datetime(df["day"]).dt.date
    return df


def manual_tables(cfg: Config) -> dict[str, pd.DataFrame]:
    return {name: load(cfg, name) for name in FILES}


def rebuild_manual(cfg: Config) -> None:
    """Rewrite only the hand-entered tables; fast enough to run after every save."""
    for name, df in manual_tables(cfg).items():
        write_table(df, cfg.tables_dir, name)
    refresh_views(cfg.tables_dir, cfg.db_path)
