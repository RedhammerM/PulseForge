"""Settings from ~/.config/physio/config.toml; every key is optional."""

import os
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_PATH = Path("~/.config/physio/config.toml")
ENV_PATH = "PHYSIO_CONFIG"


@dataclass(frozen=True)
class Config:
    data_dir: Path = Path("~/Projects/physio-daq/data").expanduser()
    workout_csv: Path = Path("~/notes/Gym/Log/workout_logs.csv").expanduser()
    recordings_dir: Path = Path("~/Projects/physio-daq/recordings").expanduser()
    subject_id: str = "S01"
    timezone: str = "Europe/Bratislava"
    garmin_key_expires: date | None = None  # Garmin shows this only when the key is created
    repull_days: int = 3
    request_delay_s: float = 1.0

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "garmin" / "raw"

    @property
    def tables_dir(self) -> Path:
        return self.data_dir / "tables"

    @property
    def workout_raw_dir(self) -> Path:
        return self.data_dir / "training" / "raw"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "physio.duckdb"


def load_config(path: Path | None = None) -> Config:
    path = path or Path(os.environ.get(ENV_PATH, DEFAULT_PATH)).expanduser()
    if not path.exists():
        return Config()
    raw = tomllib.loads(path.read_text())
    known = Config.__dataclass_fields__
    unknown = set(raw) - set(known)
    if unknown:
        raise ValueError(f"unknown settings in {path}: {sorted(unknown)}")
    for key in ("data_dir", "workout_csv", "recordings_dir"):
        if key in raw:
            raw[key] = Path(raw[key]).expanduser()
    return Config(**raw)
