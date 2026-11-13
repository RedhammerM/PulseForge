from datetime import date

import pytest
from physio_state import manual
from physio_state.config import Config
from physio_state.store import read_table


@pytest.fixture
def cfg(tmp_path):
    return Config(data_dir=tmp_path)


def test_empty_files_give_typed_empty_tables(cfg):
    for name, df in manual.manual_tables(cfg).items():
        assert df.empty and list(df.columns) == manual.FILES[name][2]


def test_nutrition_same_day_replaces(cfg):
    manual.save_nutrition(cfg, date(2026, 11, 13), 2500, protein_g=150)
    manual.save_nutrition(cfg, date(2026, 11, 12), 2100)
    manual.save_nutrition(cfg, date(2026, 11, 13), 2700)
    df = manual.load(cfg, "nutrition_daily")
    assert df["kcal"].tolist() == [2100, 2700]  # sorted by day, one row each
    assert df["day"].tolist() == [date(2026, 11, 12), date(2026, 11, 13)]


def test_implausible_intake_is_rejected(cfg):
    with pytest.raises(ValueError):
        manual.save_nutrition(cfg, date(2026, 11, 13), 50000)


def test_journal_empty_entry_removes_day(cfg):
    manual.save_journal(cfg, date(2026, 11, 13), ["alcohol", "travel"], " late flight ")
    df = manual.load(cfg, "journal")
    assert df.iloc[0]["tags"] == ["alcohol", "travel"] and df.iloc[0]["note"] == "late flight"
    manual.save_journal(cfg, date(2026, 11, 13), [], "")
    assert manual.load(cfg, "journal").empty


def test_recording_note_flag_is_checked_and_rebuild_writes_tables(cfg):
    with pytest.raises(ValueError):
        manual.save_recording_note(cfg, "S01_x", "great", "")
    manual.save_recording_note(cfg, "S01_x", "bad", "electrode slipped")
    manual.rebuild_manual(cfg)
    notes = read_table(cfg.tables_dir, "recording_notes")
    assert notes.iloc[0]["quality"] == "bad"
    assert cfg.db_path.exists()
