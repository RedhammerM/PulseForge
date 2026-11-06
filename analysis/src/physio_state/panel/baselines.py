"""Rolling personal baselines. Each value is compared with earlier values only."""

import pandas as pd


def past_mean(x: pd.Series, window: int, min_periods: int) -> pd.Series:
    return x.shift(1).rolling(window, min_periods=min_periods).mean()


def past_z(x: pd.Series, window: int = 28, min_periods: int = 7) -> pd.Series:
    past = x.shift(1).rolling(window, min_periods=min_periods)
    return (x - past.mean()) / past.std()
