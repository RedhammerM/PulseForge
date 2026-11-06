"""Synthetic fitness-fatigue data, and a study of how long the study must run.

Everything here is an assumption, not a measurement. Signal strength is the main
unknown: `snr` is the SD of each state's contribution in units of a channel's
measurement noise. The pilot gives a first real estimate of the noise.
"""

from dataclasses import dataclass
from functools import cache

import numpy as np
import pandas as pd

from .banister import decay_state, grid_fit

WARMUP_WEEKS = 12
# Channels: morning ln RMSSD, evening ln RMSSD, overnight ln HRV, resting HR.
FAST_LOADINGS = np.array([-1.0, -0.8, -1.0, 0.8])
SLOW_LOADINGS = np.array([1.0, 0.8, 1.0, -0.8])
AM_ONLY = np.array([False, False, True, True])
SHARED_NOISE_SD = 0.5  # unmodelled day-to-day drift common to all channels
SHARED_NOISE_PHI = 0.7  # per half-day step


def training_load(weeks: int, rng: np.random.Generator) -> np.ndarray:
    """Half-day loads: 5 sessions a week, 3 build weeks then 1 lighter week."""
    loads = np.zeros(weeks * 14)
    for week in range(weeks):
        level = (0.8, 1.0, 1.3, 0.5)[week % 4]
        for day in rng.choice(7, size=5, replace=False):
            loads[week * 14 + day * 2] = level * rng.lognormal(0, 0.35)
    return loads


@cache
def _state_sd(tau: float) -> float:
    reference = training_load(104, np.random.default_rng(0))
    return float(decay_state(reference, tau)[28:].std())


@dataclass
class Scenario:
    tau_fast: float = 7.0
    tau_slow: float = 42.0
    snr_fast: float = 1.0
    snr_slow: float = 1.0
    missing: float = 0.1


def simulate(weeks: int, s: Scenario, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Return observations (steps x 4, NaN if missing) and all load, warmup first."""
    u = training_load(weeks + WARMUP_WEEKS, rng)
    warmup = WARMUP_WEEKS * 14
    fast = decay_state(u, s.tau_fast)[warmup:] * s.snr_fast / _state_sd(s.tau_fast)
    slow = decay_state(u, s.tau_slow)[warmup:] * s.snr_slow / _state_sd(s.tau_slow)
    steps = len(fast)
    shared = np.zeros(steps)
    innovation = SHARED_NOISE_SD * np.sqrt(1 - SHARED_NOISE_PHI**2)
    for t in range(1, steps):
        shared[t] = SHARED_NOISE_PHI * shared[t - 1] + rng.normal(0, innovation)
    y = (
        np.outer(fast, FAST_LOADINGS)
        + np.outer(slow, SLOW_LOADINGS)
        + shared[:, None]
        + rng.normal(0, 1, (steps, 4))
    )
    y[rng.random(y.shape) < s.missing] = np.nan
    y[1::2, AM_ONLY] = np.nan  # odd steps are PM
    y[0::2, 1] = np.nan  # the evening channel exists only at PM
    y[1::2, 0] = np.nan  # the morning channel exists only at AM
    return y, u


def study_length(
    weeks: tuple[int, ...] = (6, 8, 10, 12, 16, 20, 26, 36, 52),
    snrs: tuple[float, ...] = (0.5, 1.0, 1.5),
    reps: int = 200,
    tolerance: float = 0.3,
    seed: int = 1,
) -> pd.DataFrame:
    """Share of simulated studies that recover both time constants, by length and signal."""
    rows = []
    for snr in snrs:
        scenario = Scenario(snr_fast=snr, snr_slow=snr)
        for w in weeks:
            rng = np.random.default_rng([seed, w, int(snr * 100)])
            fits = [
                grid_fit(*simulate(w, scenario, rng), warmup=WARMUP_WEEKS * 14) for _ in range(reps)
            ]
            fast = np.array([abs(f.tau_fast / scenario.tau_fast - 1) <= tolerance for f in fits])
            slow = np.array([abs(f.tau_slow / scenario.tau_slow - 1) <= tolerance for f in fits])
            rows.append(
                {
                    "snr": snr,
                    "weeks": w,
                    "fast_recovered": fast.mean(),
                    "slow_recovered": slow.mean(),
                    "both_recovered": (fast & slow).mean(),
                    "two_state_preferred": np.mean([f.two_state_preferred for f in fits]),
                }
            )
    return pd.DataFrame(rows)
