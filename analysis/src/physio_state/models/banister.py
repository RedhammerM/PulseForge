"""Two-state fitness-fatigue model: load drives a fast and a slow decaying state.

Time steps are half days. `grid_fit` treats states as deterministic, which makes
the loadings a linear regression and is fast enough for simulation studies.
`fit` estimates the full state-space model by maximum likelihood.
"""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

from .kalman import FilterResult, kalman_filter

STEP_DAYS = 0.5
TAU_FAST = np.array([2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 18])
TAU_SLOW = np.array([21, 25, 30, 35, 42, 50, 60, 75, 90])


def decay_state(u: np.ndarray, tau: float) -> np.ndarray:
    """State before each step's load is added."""
    a = np.exp(-STEP_DAYS / tau)
    x = np.zeros(len(u))
    for t in range(1, len(u)):
        x[t] = a * x[t - 1] + u[t - 1]
    return x


def _sse(y: np.ndarray, regressors: list[np.ndarray]) -> float:
    """Sum of squared residuals, fitted separately for each channel."""
    total = 0.0
    for k in range(y.shape[1]):
        seen = ~np.isnan(y[:, k])
        design = np.column_stack([np.ones(seen.sum()), *[r[seen] for r in regressors]])
        coef = np.linalg.lstsq(design, y[seen, k], rcond=None)[0]
        total += float(((y[seen, k] - design @ coef) ** 2).sum())
    return total


@dataclass
class GridFit:
    tau_fast: float
    tau_slow: float
    two_state_preferred: bool  # by BIC, against the best single-state model


def grid_fit(y: np.ndarray, u: np.ndarray, warmup: int = 0) -> GridFit:
    """`u` covers `warmup` steps before `y` starts, so states begin from known history."""
    states = {t: decay_state(u, t)[warmup:] for t in (*TAU_FAST, *TAU_SLOW)}
    best2 = min(
        ((_sse(y, [states[f], states[s]]), f, s) for f in TAU_FAST for s in TAU_SLOW),
        key=lambda v: v[0],
    )
    best1 = min(_sse(y, [x]) for x in states.values())
    k, n = y.shape[1], int((~np.isnan(y)).sum())
    bic2 = n * np.log(best2[0] / n) + (2 + 3 * k) * np.log(n)
    bic1 = n * np.log(best1 / n) + (1 + 2 * k) * np.log(n)
    return GridFit(float(best2[1]), float(best2[2]), bool(bic2 < bic1))


@dataclass
class Params:
    tau_fast: float
    tau_slow: float
    loadings: np.ndarray  # k x 2
    means: np.ndarray  # k
    obs_var: np.ndarray  # k
    state_var: float


def _system(p: Params) -> tuple:
    a = np.diag(np.exp(-STEP_DAYS / np.array([p.tau_fast, p.tau_slow])))
    return a, np.ones(2), p.loadings, p.means, np.eye(2) * p.state_var, p.obs_var


def run_filter(p: Params, y: np.ndarray, u: np.ndarray) -> FilterResult:
    a, b, c, d, q, r = _system(p)
    return kalman_filter(y, u, a, b, c, d, q, r, np.zeros(2), np.eye(2))


def _unpack(v: np.ndarray, k: int) -> Params:
    return Params(
        float(np.exp(v[0])),
        float(np.exp(v[1])),
        v[2 : 2 + 2 * k].reshape(k, 2),
        v[2 + 2 * k : 2 + 3 * k],
        np.exp(v[2 + 3 * k : 2 + 4 * k]),
        float(np.exp(v[2 + 4 * k])),
    )


def _start(y: np.ndarray, u: np.ndarray) -> np.ndarray:
    g = grid_fit(y, u)
    f, s = decay_state(u, g.tau_fast), decay_state(u, g.tau_slow)
    load, mean, var = [], [], []
    for k in range(y.shape[1]):
        seen = ~np.isnan(y[:, k])
        design = np.column_stack([f[seen], s[seen], np.ones(seen.sum())])
        coef = np.linalg.lstsq(design, y[seen, k], rcond=None)[0]
        load += list(coef[:2])
        mean.append(coef[2])
        var.append(max(np.var(y[seen, k] - design @ coef), 1e-6))
    return np.array(
        [np.log(g.tau_fast), np.log(g.tau_slow), *load, *mean, *np.log(var), np.log(1e-3)]
    )


def fit(y: np.ndarray, u: np.ndarray, maxiter: int = 60) -> Params:
    k = y.shape[1]

    def negative_loglik(v: np.ndarray) -> float:
        return -run_filter(_unpack(v, k), y, u).loglik

    bounds = [
        (np.log(TAU_FAST[0]), np.log(TAU_FAST[-1])),
        (np.log(TAU_SLOW[0]), np.log(TAU_SLOW[-1])),
        *[(None, None)] * (3 * k),
        *[(-12, 5)] * k,
        (-14, 2),
    ]
    start = _start(y, u)
    result = minimize(
        negative_loglik, start, method="L-BFGS-B", bounds=bounds, options={"maxiter": maxiter}
    )
    return _unpack(result.x, k)
