"""Recording flow with simulated frames: no board or Qt needed."""

import csv
import json
import time
from datetime import UTC, datetime

import pytest

from physio_daq import checkin
from physio_daq.acquisition import Acquisition
from physio_daq.config import STAND_PROTOCOL, STAND_PROTOCOL_DURATION_S
from physio_daq.protocol import EcgSample, PpgSample
from physio_daq.session import SCHEMA_VERSION


def acquisition(tmp_path, duration=300.0):
    acq = Acquisition("/dev/null", outdir=tmp_path, duration_s=duration)
    acq.connected, acq.last_rx = True, time.time()
    return acq


def ecg(acq, t_us):
    acq._on_ecg(EcgSample(t_raw=t_us, ecg=500, lo_plus=False, lo_minus=False))


def summary(tmp_path):
    return json.loads(next(p for p in tmp_path.glob("*.json")).read_text())


def test_stand_protocol_button_marks_stand_then_cuff(tmp_path):
    acq = acquisition(tmp_path)
    assert acq.start_recording({"subject": "S01", "test": STAND_PROTOCOL, "session_id": "x"})
    assert acq.session.duration_s == STAND_PROTOCOL_DURATION_S
    ecg(acq, 1000)
    ecg(acq, 3000)
    acq._on_message("# BTN")
    ecg(acq, 5000)
    acq._on_message("# BTN")
    assert acq.state == "recording"
    assert acq.snapshot()["events"] == {"stand": 0.002, "cuff": 0.004}
    acq.stop_recording()
    out = summary(tmp_path)
    assert out["schema_version"] == SCHEMA_VERSION == 2
    assert out["session_id"] == "x" and out["events"]["n_rows"] == 2
    with (tmp_path / out["events"]["file"]).open() as f:
        stand, cuff = csv.DictReader(f)
    assert stand["label"] == "stand" and int(stand["t_us"]) == 2000
    assert cuff["label"] == "cuff" and int(cuff["t_us"]) == 4000


def test_button_still_stops_other_recordings(tmp_path):
    acq = acquisition(tmp_path)
    acq.start_recording({"subject": "S01", "test": "supine rest"})
    acq._on_message("# BTN")
    assert acq.state == "preview"
    assert summary(tmp_path)["end_reason"] == "stopped (button)"


def test_auto_stop_uses_the_protocol_duration(tmp_path):
    acq = acquisition(tmp_path, duration=1.0)
    acq.start_recording({"subject": "S01", "test": STAND_PROTOCOL})
    ecg(acq, 0)
    ecg(acq, 2_000_000)
    assert acq.state == "recording"
    ecg(acq, int(STAND_PROTOCOL_DURATION_S * 1e6))
    assert acq.state == "preview"
    assert summary(tmp_path)["end_reason"] == "auto-stop (540 s)"


def test_times_are_utc_with_offset(tmp_path):
    acq = acquisition(tmp_path)
    acq.start_recording({"subject": "S01"})
    acq._on_ppg(PpgSample(t_raw=0, ir=1, red=1, polled=False, isr_lost=False))
    acq.stop_recording()
    out = summary(tmp_path)
    local = datetime.fromisoformat(out["start"])
    assert datetime.fromisoformat(out["start_utc"]) == local
    assert local.utcoffset().total_seconds() / 60 == out["utc_offset_min"]


def test_mark_without_recording_does_nothing(tmp_path):
    assert acquisition(tmp_path).mark("stand") is False


@pytest.mark.parametrize(
    "hour,day,slot",
    [(7, "2026-11-07", "AM"), (22, "2026-11-07", "PM"), (1, "2026-11-06", "PM")],
)
def test_session_day_and_slot(hour, day, slot):
    assert checkin.session_day_slot(datetime(2026, 11, 7, hour)) == (day, slot)


def test_bp_is_saved_without_losing_ratings(tmp_path):
    now = datetime(2026, 11, 7, 7, tzinfo=UTC)
    c = checkin.make_checkin("S01", "2026-11-07", "AM", {"readiness": 7}, 10, now)
    checkin.save_session(tmp_path, c)
    bp = checkin.make_bp({"before_seated": (120, 80, 55)}, "left", now)
    path = checkin.save_session(tmp_path, {"session_id": c["session_id"], **bp})
    saved = json.loads(path.read_text())
    assert path.name == "S01_2026-11-07_AM_session.json"
    assert saved["ratings"]["readiness"] == 7 and saved["bp"]["readings"][0]["sbp"] == 120


def test_swapped_bp_is_rejected():
    with pytest.raises(ValueError):
        checkin.make_bp({"before_seated": (80, 120, 55)}, "left", datetime.now())


@pytest.mark.parametrize(
    "elapsed,events,cue",
    [
        (10, {}, "stand at 07:00"),
        (420, {}, "STAND NOW, press Stand or D4"),
        (430, {"stand": 425}, "cuff at 08:05"),  # timed from the real stand, not the plan
        (485, {"stand": 425}, "CUFF NOW, press Cuff or D4"),
        (500, {"stand": 425, "cuff": 486}, ""),
    ],
)
def test_protocol_cue(elapsed, events, cue):
    assert checkin.protocol_cue(elapsed, events) == cue
