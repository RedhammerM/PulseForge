import hashlib
import json
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pandas as pd
from physio_state.recordings.ingest import ingest

from physio_daq import checkin
from physio_daq.acquisition import Acquisition
from physio_daq.protocol import EcgSample

TZ = ZoneInfo("Europe/Bratislava")


def v1_recording(folder):
    """A file set in the old schema: naive local time, no events."""
    block = {"effective_fs": 500.0, "leads_off_fraction": 0.0}
    files = {}
    for k in ("ecg", "ppg"):
        files[k] = f"S01_20261024_104707_{k}.csv"
        (folder / files[k]).write_text("t_us\n")
    sha = hashlib.sha256(b"t_us\n").hexdigest()
    summary = {
        "schema_version": 1,
        "start": "2026-10-24T10:47:07",
        "duration_s": 36.9,
        "end_reason": "stopped",
        "firmware": "fw=3",
        "crc_errors": 0,
        "ecg": {**block, "file": files["ecg"], "gaps_gt_1p5_ticks": 0, "sha256": sha},
        "ppg": {
            "effective_fs": 400.0,
            "file": files["ppg"],
            "gaps_gt_1p5_periods": 0,
            "polled_rows": 0,
            "sha256": sha,
        },
        "metadata": {"subject": "S01", "test": "paced breathing 0.1 Hz"},
    }
    (folder / "S01_20261024_104707.json").write_text(json.dumps(summary))


def test_v1_recording_gets_utc_and_day(tmp_path):
    v1_recording(tmp_path)
    r = ingest(tmp_path, TZ)["recordings"].iloc[0]
    assert r["start_utc"] == pd.Timestamp("2026-10-24T08:47:07Z")  # summer time, +2 h
    assert str(r["day"]) == "2026-10-24"
    assert r["utc_offset_min"] == 120
    assert r["checksums_ok"]


def test_changed_csv_fails_the_checksum(tmp_path):
    v1_recording(tmp_path)
    (tmp_path / "S01_20261024_104707_ecg.csv").write_text("edited\n")
    assert not ingest(tmp_path, TZ)["recordings"].iloc[0]["checksums_ok"]


def test_session_with_recording_end_to_end(tmp_path):
    now = datetime(2026, 11, 7, 6, tzinfo=UTC)
    c = checkin.make_checkin("S01", "2026-11-07", "AM", {"readiness": 7}, 10, now)
    readings = {"before_seated": (120, 80, 55), "during_stand": (118, 78, None)}
    bp = checkin.make_bp(readings, "left", now)
    checkin.save_session(tmp_path, {**c, **bp})

    acq = Acquisition("/dev/null", outdir=tmp_path)
    acq.connected, acq.last_rx = True, time.time()
    acq.start_recording({"subject": "S01", "session_id": c["session_id"]})
    acq._on_ecg(EcgSample(t_raw=0, ecg=500, lo_plus=False, lo_minus=False))
    acq.mark("stand")
    acq.stop_recording()

    t = ingest(tmp_path, TZ)
    assert t["sessions"].iloc[0]["minutes_since_waking"] == 10
    assert t["anchors"].iloc[0]["readiness"] == 7
    assert t["bp_readings"]["sbp"].tolist() == [120, 118]
    assert t["bp_readings"]["posture"].tolist() == ["sitting", "standing"]
    rec = t["recordings"].iloc[0]
    assert rec["session_id"] == c["session_id"] and rec["checksums_ok"]
    assert str(rec["day"]) == "2026-11-07"
    assert t["events"].iloc[0]["label"] == "stand"


def test_empty_folder(tmp_path):
    assert all(df.empty for df in ingest(tmp_path, TZ).values())
