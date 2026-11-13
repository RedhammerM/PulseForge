"""App helpers, plus every page rendered headless against an empty and a filled data dir."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from physio_app import data
from physio_state.config import Config
from physio_state.store import write_table

PAGES = ["measure", "measurements", "recording", "training", "lifestyle", "data"]
MAIN = Path(__file__).parents[1] / "app" / "src" / "physio_app" / "main.py"


def test_minmax_downsample_keeps_extremes():
    t = np.arange(10_000, dtype=float)
    y = np.zeros(10_000)
    y[5_123], y[7_000] = 9.0, -4.0
    td, yd = data.minmax_downsample(t, y, 100)
    assert len(yd) == 200 and yd.max() == 9.0 and yd.min() == -4.0
    assert np.all(np.diff(td) >= 0)


def test_session_grid_uses_session_slot_then_when():
    rec = pd.DataFrame(
        {
            "day": [date(2026, 11, 12), date(2026, 11, 12), date(2026, 11, 13)],
            "session_id": ["S01_2026-11-12_AM", None, None],
            "when": ["morning", "evening", "morning"],
        }
    )
    g = data.session_grid(rec, date(2026, 11, 12), date(2026, 11, 14))
    n = g.set_index(["day", "slot"])["n"]
    assert n[(date(2026, 11, 12), "AM")] == 1 and n[(date(2026, 11, 12), "PM")] == 1
    assert n[(date(2026, 11, 14), "AM")] == 0
    assert data.streak(g, date(2026, 11, 14)) == 2  # today still open


def test_energy_balance_only_where_both_exist():
    summary = pd.DataFrame(
        {"day": [date(2026, 11, 12), date(2026, 11, 13)], "calories_total": [2500, 2600],
         "calories_active": [500, 600]}
    )  # fmt: skip
    food = pd.DataFrame({"day": [date(2026, 11, 13)], "kcal": [2300]})
    out = data.energy_balance(summary, food).set_index("day")
    assert pd.isna(out.loc[date(2026, 11, 12), "balance"])
    assert out.loc[date(2026, 11, 13), "balance"] == -300


def test_prefill_from_garmin():
    tables = {
        "sleep_nightly": pd.DataFrame({"day": [date(2026, 11, 13)], "sleep_s": [27000]}),
        "activities": pd.DataFrame(
            {"start_utc": pd.to_datetime(["2026-11-11T16:00Z"]), "duration_s": [4620.0],
             "garmin_rpe": [7.0]}
        ),
    }  # fmt: skip
    assert data.prefill(tables, date(2026, 11, 13)) == {
        "sleep_h": 7.5, "last_workout_min": 77, "last_session_rpe": 7,
    }  # fmt: skip


def _filled(cfg: Config) -> None:
    days = pd.date_range("2026-11-01", "2026-11-13").date
    write_table(
        pd.DataFrame({"day": days, "calories_total": 2400, "calories_active": 400, "steps": 8000,
                      "steps_goal": 7000, "garmin_stress_avg": 30, "garmin_bb_max": 80}),
        cfg.tables_dir, "daily_summary",
    )  # fmt: skip
    write_table(
        pd.DataFrame({"day": days, "hrv_mean_ms": 60.0, "garmin_baseline_balanced_low": 50,
                      "garmin_baseline_balanced_upper": 70}),
        cfg.tables_dir, "hrv_nightly",
    )  # fmt: skip
    write_table(
        pd.DataFrame({"day": days[:3], "time_utc": pd.to_datetime(["2026-11-01T07:00Z"] * 3),
                      "weight_kg": [77.0, 77.2, 76.9], "body_fat_pct": None}),
        cfg.tables_dir, "weight",
    )  # fmt: skip


@pytest.mark.parametrize("filled", [False, True])
def test_every_page_renders(tmp_path, monkeypatch, filled):
    from streamlit.testing.v1 import AppTest

    conf = tmp_path / "config.toml"
    conf.write_text(
        f'data_dir = "{tmp_path / "data"}"\nrecordings_dir = "{tmp_path / "rec"}"\n'
        f'workout_csv = "{tmp_path / "none.csv"}"\n'
    )
    monkeypatch.setenv("PHYSIO_CONFIG", str(conf))
    if filled:
        _filled(Config(data_dir=tmp_path / "data"))
    at = AppTest.from_file(str(MAIN), default_timeout=30).run()
    assert not at.exception, at.exception
    for page in PAGES[1:]:
        at.switch_page(f"views/{page}.py").run()
        assert not at.exception, (page, at.exception)


def test_lifestyle_forms_save_entries(tmp_path, monkeypatch):
    from physio_state import manual
    from streamlit.testing.v1 import AppTest

    conf = tmp_path / "config.toml"
    conf.write_text(f'data_dir = "{tmp_path / "data"}"\nrecordings_dir = "{tmp_path / "rec"}"\n')
    monkeypatch.setenv("PHYSIO_CONFIG", str(conf))
    at = AppTest.from_file(str(MAIN), default_timeout=30).run()
    at.switch_page("views/lifestyle.py").run()
    [kcal] = [n for n in at.number_input if n.label == "kcal"]
    kcal.set_value(2450)
    [save] = [b for b in at.button if b.label == "Save food"]
    save.click().run()
    assert not at.exception, at.exception
    cfg = Config(data_dir=tmp_path / "data")
    assert manual.load(cfg, "nutrition_daily")["kcal"].tolist() == [2450]

    [tags] = [p for p in at.pills if p.label == "Tags"]
    tags.set_value(["alcohol"])
    [save] = [b for b in at.button if b.label == "Save journal"]
    save.click().run()
    assert not at.exception, at.exception
    assert manual.load(cfg, "journal")["tags"].tolist() == [["alcohol"]]
