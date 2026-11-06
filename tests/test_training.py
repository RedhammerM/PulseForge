from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from physio_state.build import rebuild
from physio_state.config import Config
from physio_state.keys import physio_day
from physio_state.store import read_table
from physio_state.training import load, obsidian

TZ = ZoneInfo("Europe/Bratislava")
HEADER = "date,exercise,reps,weight,volume,origine,workout,timestamp,notes,protocol,weightUnit\n"
T0 = 1793960683237  # 2026-11-06T10:24:43Z


def row(t=T0, reps=5, weight=77, notes="", unit="kg"):
    return f"x,Pull-ups,{reps},{weight},0,[[Pull-ups]],Pull-ups,{t},{notes},standard,{unit}\n"


def parsed(*rows):
    return obsidian.parse_sets((HEADER + "".join(rows)).encode())


def activity(**kw):
    base = {
        "activity_id": 1,
        "activity_type": "strength_training",
        "start_utc": pd.Timestamp(T0 - 600000, unit="ms", tz="UTC"),
        "elapsed_s": 3600.0,
        "garmin_rpe": None,
        "garmin_training_load": 30.0,
        **{f"hr_zone{i}_s": None for i in range(1, 6)},
    }
    return pd.DataFrame([base | kw])


def test_parse_real_plugin_format():
    sets = parsed(row()).sets
    assert sets.iloc[0]["set_time_utc"] == pd.Timestamp("2026-11-06T10:24:43.237Z")
    assert sets.iloc[0]["weight_kg"] == 77


def test_bad_rows_are_reported_not_hidden():
    result = parsed(row(), row(reps="five"), row(unit="stone"))
    assert len(result.sets) == 1
    assert [e.split(":")[0] for e in result.errors] == ["line 3", "line 4"]


def test_missing_column_fails():
    with pytest.raises(ValueError, match="timestamp"):
        obsidian.parse_sets(b"exercise,reps\nx,1\n")


@pytest.mark.parametrize(
    "text,value",
    [("rpe=7", 7), ("RPE 8.5", 8.5), ("felt ok, rpe: 6,5", 6.5), ("rpe=11", None), ("", None)],
)
def test_note_rpe(text, value):
    assert obsidian.note_rpe(text) == value


def test_workouts_split_on_long_pause():
    sets = parsed(row(), row(t=T0 + 600000, notes="rpe=7"), row(t=T0 + 4 * 3600000)).sets
    w = load.build_workouts(sets)
    assert w["n_sets"].tolist() == [2, 1]
    assert w.iloc[0]["note_rpe"] == 7 and w.iloc[0]["volume_kg"] == 770


def test_workout_merges_with_its_garmin_activity():
    w = load.build_workouts(parsed(row(notes="rpe=7")).sets)
    e = load.build_load_events(w, activity(), TZ)
    assert len(e) == 1
    assert e.iloc[0]["duration_min"] == 60
    assert e.iloc[0]["rpe_source"] == "obsidian_note"
    assert e.iloc[0]["srpe_load"] == 420


def test_distant_activity_does_not_match():
    w = load.build_workouts(parsed(row()).sets)
    a = activity(start_utc=pd.Timestamp(T0 + 6 * 3600000, unit="ms", tz="UTC"))
    assert len(load.build_load_events(w, a, TZ)) == 2


def test_edwards_trimp_weights_zones_and_keeps_missing_hr():
    a = pd.concat([activity(hr_zone1_s=600.0, hr_zone3_s=1200.0), activity()], ignore_index=True)
    trimp = load.edwards_trimp(a)
    assert trimp[0] == 10 + 60
    assert pd.isna(trimp[1])


def test_daily_load_is_null_when_any_event_lacks_rpe_and_zero_on_rest_days():
    events = pd.DataFrame(
        {
            "day": pd.to_datetime(["2026-11-01", "2026-11-01", "2026-11-03"]).date,
            "srpe_load": [100.0, None, 50.0],
            "garmin_training_load": [10.0, 20.0, 5.0],
            "edwards_trimp": [None, None, 3.0],
        }
    )
    d = load.build_load_daily(events)
    assert d["n_events"].tolist() == [2, 0, 1]
    assert pd.isna(d.iloc[0]["srpe_load"])
    assert d.iloc[1]["srpe_load"] == 0
    assert d.iloc[0]["garmin_training_load"] == 30


def test_physio_day_keeps_late_evening_on_previous_day():
    t = pd.Series(pd.to_datetime(["2026-11-06T23:30:00Z", "2026-11-06T06:30:00Z"], utc=True))
    assert [str(d) for d in physio_day(t, TZ)] == ["2026-11-06", "2026-11-06"]
    wakes = pd.DataFrame(
        {
            "day": pd.to_datetime(["2026-11-06"]).date,
            "sleep_end_utc": pd.to_datetime(["2026-11-06T06:00:00Z"], utc=True),
        }
    )
    assert [str(d) for d in physio_day(t, TZ, wakes)] == ["2026-11-06", "2026-11-06"]


def test_rebuild_from_empty_raw_and_workout_snapshot(tmp_path):
    csv = tmp_path / "log.csv"
    csv.write_text(HEADER + row())
    cfg = Config(data_dir=tmp_path / "data", workout_csv=csv)
    assert obsidian.snapshot(cfg)
    assert obsidian.snapshot(cfg)
    assert len(list((cfg.workout_raw_dir / "obsidian").glob("*.csv"))) == 1
    counts = rebuild(cfg)
    assert counts["workout_sets"] == 1 and counts["load_events"] == 1
    assert read_table(cfg.tables_dir, "load_events").iloc[0]["activity_type"] == "strength_training"
    assert cfg.db_path.exists()
