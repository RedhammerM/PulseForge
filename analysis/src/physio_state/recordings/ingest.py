"""Recording and session JSON files to tables. Reads schema v1 and v2 recordings."""

import csv
import hashlib
import json
import logging
from pathlib import Path

import pandas as pd

from ..keys import physio_day, utc_offset_min

log = logging.getLogger(__name__)
RATINGS = ["readiness", "sleep_quality", "soreness", "stress"]
SESSION_COLS = [
    "session_id", "subject_id", "day", "slot", "checkin_utc", "utc_offset_min",
    "minutes_since_waking",
]  # fmt: skip
ANCHOR_COLS = ["session_id", "day", "slot", *RATINGS]
BP_COLS = ["session_id", "reading", "label", "entered_utc", "arm", "posture", "sbp", "dbp", "hr"]
RECORDING_COLS = [
    "recording_id", "session_id", "subject_id", "day", "schema_version", "start_utc",
    "utc_offset_min", "duration_s", "end_reason", "firmware", "test", "posture", "when",
    "ecg_fs", "ppg_fs", "ecg_gaps", "ppg_gaps", "leads_off_fraction", "ppg_polled_rows",
    "crc_errors", "checksums_ok",
]  # fmt: skip
EVENT_COLS = ["recording_id", "t_us", "label", "wall_utc"]


def _sha_ok(folder: Path, block: dict) -> bool:
    path = folder / block["file"]
    return path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == block["sha256"]


def _utc(text: str) -> pd.Timestamp:
    return pd.Timestamp(text).tz_convert("UTC")


def _offset(text: str) -> int:
    return int(pd.Timestamp(text).utcoffset().total_seconds() // 60)


def _recording_row(path: Path, summary: dict, tz) -> dict:
    meta = summary.get("metadata") or {}
    e, p = summary["ecg"], summary["ppg"]
    blocks = [e, p] + ([summary["events"]] if "events" in summary else [])
    if "start_utc" in summary:
        start = pd.Timestamp(summary["start_utc"])
    else:  # v1 stored naive local time
        start = pd.Timestamp(summary["start"]).tz_localize(tz).tz_convert("UTC")
    return {
        "recording_id": path.stem,
        "session_id": summary.get("session_id"),
        "subject_id": meta.get("subject"),
        "schema_version": summary["schema_version"],
        "start_utc": start,
        "duration_s": summary["duration_s"],
        "end_reason": summary["end_reason"],
        "firmware": summary["firmware"],
        "test": meta.get("test"),
        "posture": meta.get("posture"),
        "when": meta.get("when"),
        "ecg_fs": e["effective_fs"],
        "ppg_fs": p["effective_fs"],
        "ecg_gaps": e["gaps_gt_1p5_ticks"],
        "ppg_gaps": p["gaps_gt_1p5_periods"],
        "leads_off_fraction": e["leads_off_fraction"],
        "ppg_polled_rows": p["polled_rows"],
        "crc_errors": summary["crc_errors"],
        "checksums_ok": all(_sha_ok(path.parent, b) for b in blocks),
    }


def _events(path: Path, summary: dict) -> list[dict]:
    block = summary.get("events")
    file = path.parent / block["file"] if block else None
    if file is None or not file.exists():
        return []
    with file.open(newline="") as f:
        return [
            {
                "recording_id": path.stem,
                "t_us": int(r["t_us"]),
                "label": r["label"],
                "wall_utc": pd.Timestamp(r["wall_utc"]),
            }
            for r in csv.DictReader(f)
        ]


def ingest(folder: Path, tz, wakes: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    sessions, anchors, bp = [], [], []
    for path in sorted(folder.glob("*_session.json")):
        s = json.loads(path.read_text())
        base = {"session_id": s["session_id"], "day": s["day"], "slot": s["slot"]}
        sessions.append(
            {
                **base,
                "subject_id": s["subject"],
                "checkin_utc": _utc(s["checkin_time"]),
                "utc_offset_min": _offset(s["checkin_time"]),
                "minutes_since_waking": s.get("minutes_since_waking"),
            }
        )
        anchors.append({**base, **{k: s["ratings"].get(k) for k in RATINGS}})
        cuff = s.get("bp")
        for i, r in enumerate(cuff["readings"] if cuff else [], start=1):
            bp.append(
                {
                    "session_id": s["session_id"],
                    "reading": i,
                    "entered_utc": _utc(cuff["entered"]),
                    "arm": cuff["arm"],
                    **r,
                }
            )

    recordings, events = [], []
    for path in sorted(folder.glob("*.json")):
        if path.name.endswith("_session.json"):
            continue
        summary = json.loads(path.read_text())
        row = _recording_row(path, summary, tz)
        if not row["checksums_ok"]:
            log.warning("checksum mismatch in recording %s", path.stem)
        recordings.append(row)
        events += _events(path, summary)

    rec = pd.DataFrame(recordings, columns=RECORDING_COLS)
    if not rec.empty:
        rec["start_utc"] = pd.to_datetime(rec["start_utc"], utc=True)
        rec["utc_offset_min"] = utc_offset_min(rec["start_utc"], tz)
        rec["day"] = physio_day(rec["start_utc"], tz, wakes)
        days = {s["session_id"]: s["day"] for s in sessions}
        rec["day"] = [
            pd.Timestamp(days.get(sid, day)).date()
            for sid, day in zip(rec["session_id"], rec["day"], strict=True)
        ]
    tables = {
        "sessions": pd.DataFrame(sessions, columns=SESSION_COLS),
        "anchors": pd.DataFrame(anchors, columns=ANCHOR_COLS),
        "bp_readings": pd.DataFrame(bp, columns=BP_COLS),
        "recordings": rec,
        "events": pd.DataFrame(events, columns=EVENT_COLS),
    }
    for name in ("sessions", "anchors"):
        tables[name]["day"] = pd.to_datetime(tables[name]["day"]).dt.date
    return tables
