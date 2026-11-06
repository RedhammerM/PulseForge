"""Parquet tables plus a DuckDB file of views. Both can be rebuilt from raw."""

import os
from pathlib import Path

import duckdb
import pandas as pd


def write_table(df: pd.DataFrame, tables_dir: Path, name: str) -> None:
    tables_dir.mkdir(parents=True, exist_ok=True)
    tmp = tables_dir / f".{name}.parquet.tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, tables_dir / f"{name}.parquet")


def read_table(tables_dir: Path, name: str) -> pd.DataFrame:
    return pd.read_parquet(tables_dir / f"{name}.parquet")


def refresh_views(tables_dir: Path, db_path: Path) -> list[str]:
    """Recreate one view per Parquet file. Returns the view names."""
    names = sorted(p.stem for p in tables_dir.glob("*.parquet"))
    db_path.unlink(missing_ok=True)
    with duckdb.connect(str(db_path)) as con:
        for name in names:
            path = str((tables_dir / f"{name}.parquet").resolve()).replace("'", "''")
            con.execute(f"CREATE VIEW \"{name}\" AS SELECT * FROM read_parquet('{path}')")
    return names
