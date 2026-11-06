"""Activity ZIP to per-second samples and RR intervals. GPS never leaves this file."""

import io
import zipfile
from dataclasses import dataclass

import fitdecode
import pandas as pd

RECORD_FIELDS = {
    "heart_rate": "hr",
    "enhanced_speed": "speed_mps",
    "power": "power_w",
    "cadence": "cadence",
    "distance": "distance_m",
}
SAMPLE_COLS = ["activity_id", "time_utc", *RECORD_FIELDS.values()]
RR_COLS = ["activity_id", "seq", "rr_ms"]


@dataclass
class FitData:
    samples: pd.DataFrame
    rr: pd.DataFrame


def record_row(frame) -> dict | None:
    """Keep only the allowed fields of a FIT `record` message."""
    if not frame.has_field("timestamp"):
        return None
    row = {"time_utc": pd.Timestamp(frame.get_value("timestamp"))}
    for src, col in RECORD_FIELDS.items():
        row[col] = frame.get_value(src, fallback=None) if frame.has_field(src) else None
    return row


def rr_values(frame) -> list[float]:
    """RR intervals in ms from a FIT `hrv` message; unset slots are None."""
    times = frame.get_value("time", fallback=None) if frame.has_field("time") else None
    if times is None:
        return []
    times = times if isinstance(times, (tuple, list)) else (times,)
    return [t * 1000 for t in times if t is not None]


def parse_activity_zip(content: bytes, activity_id: int) -> FitData:
    records, rr = [], []
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        for name in z.namelist():
            if not name.lower().endswith(".fit"):
                continue
            with fitdecode.FitReader(io.BytesIO(z.read(name))) as reader:
                for frame in reader:
                    if not isinstance(frame, fitdecode.FitDataMessage):
                        continue
                    if frame.name == "record":
                        row = record_row(frame)
                        if row is not None:
                            records.append(row)
                    elif frame.name == "hrv":
                        rr += rr_values(frame)
    samples = pd.DataFrame(records, columns=SAMPLE_COLS[1:])
    samples.insert(0, "activity_id", activity_id)
    rr_df = pd.DataFrame({"activity_id": activity_id, "seq": range(len(rr)), "rr_ms": rr})
    return FitData(samples, rr_df[RR_COLS])
