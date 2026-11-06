"""Session check-in: ratings before the cuff, BP after the recording. One JSON per session.

A session is one morning or evening slot and can hold several recordings.
Ratings are 0-10, higher means more of the named quantity. These values are
anchors for validating the model, never model inputs.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from .config import CUFF_AFTER_STAND_S, STAND_AT_S

SCHEMA_VERSION = 1
RATINGS = {
    "readiness": "Perceived readiness (0 none, 10 fully ready)",
    "sleep_quality": "Sleep quality (0 worst, 10 best)",
    "soreness": "Muscle soreness (0 none, 10 extreme)",
    "stress": "Mood / psychological stress (0 none, 10 extreme)",
}
BP_READINGS = {  # label: posture
    "before_seated": "sitting",  # just before the recording
    "during_stand": "standing",  # 1 min into the stand, cuffed arm at heart level
}
ROLLOVER_HOUR = 4  # an evening session after midnight stays on the previous day
PM_FROM_HOUR = 14


def _clock(sec: float) -> str:
    return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"


def session_day_slot(now: datetime) -> tuple[str, str]:
    """Default wake-up date and slot for a local time."""
    day = (now - timedelta(hours=ROLLOVER_HOUR)).date()
    slot = "AM" if ROLLOVER_HOUR <= now.hour < PM_FROM_HOUR else "PM"
    return day.isoformat(), slot


def session_id(subject: str, day: str, slot: str) -> str:
    return f"{subject}_{day}_{slot}"


def make_checkin(
    subject: str,
    day: str,
    slot: str,
    ratings: dict[str, int | None],
    minutes_since_waking: int | None,
    now: datetime,
) -> dict:
    now = now.astimezone()
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id(subject, day, slot),
        "subject": subject,
        "day": day,
        "slot": slot,
        "checkin_time": now.isoformat(timespec="seconds"),
        "minutes_since_waking": minutes_since_waking,
        "ratings": {k: ratings.get(k) for k in RATINGS},
    }


def make_bp(
    readings: dict[str, tuple[int, int, int | None]], arm: str, entered: datetime
) -> dict:
    """Validate cuff readings given as {label: (SBP, DBP, HR)}."""
    for sbp, dbp, _ in readings.values():
        if not 40 <= dbp < sbp <= 300:
            raise ValueError(f"implausible BP reading {sbp}/{dbp}")
    return {
        "bp": {
            "arm": arm,
            "entered": entered.astimezone().isoformat(timespec="seconds"),
            "readings": [
                {"label": k, "posture": BP_READINGS[k], "sbp": s, "dbp": d, "hr": h}
                for k, (s, d, h) in readings.items()
            ],
        }
    }


def protocol_cue(elapsed_s: float, events: dict[str, float]) -> str:
    """Next step of the stand protocol, timed from the actual stand event."""
    stand = events.get("stand")
    if stand is None:
        if elapsed_s >= STAND_AT_S:
            return "STAND NOW, press Stand or D4"
        return f"stand at {_clock(STAND_AT_S)}"
    if "cuff" in events:
        return ""
    due = stand + CUFF_AFTER_STAND_S
    if elapsed_s >= due:
        return "CUFF NOW, press Cuff or D4"
    return f"cuff at {_clock(due)}"


def session_path(outdir: Path, sid: str) -> Path:
    return outdir / f"{sid}_session.json"


def save_session(outdir: Path, values: dict) -> Path:
    """Write the session file, keeping fields of an earlier save (e.g. a repeated check-in)."""
    outdir.mkdir(parents=True, exist_ok=True)
    path = session_path(outdir, values["session_id"])
    existing = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps({**existing, **values}, indent=2))
    return path
