"""Linear Gaussian Kalman filter with missing observations (NaN entries)."""

from dataclasses import dataclass

import numpy as np


@dataclass
class FilterResult:
    loglik: float
    means: np.ndarray  # filtered state, T x n
    covs: np.ndarray  # T x n x n
    predicted_y: np.ndarray  # one-step predictions made before seeing y, T x k


def kalman_filter(y, u, a, b, c, d, q, r, x0, p0) -> FilterResult:
    """x[t+1] = a x[t] + b u[t] + w, y[t] = c x[t] + d + v; cov(w) = q, cov(v) = r (diagonal)."""
    steps, k = y.shape
    n = len(x0)
    x, p = np.array(x0, float), np.array(p0, float)
    means, covs = np.empty((steps, n)), np.empty((steps, n, n))
    predicted = np.empty((steps, k))
    loglik = 0.0
    for t in range(steps):
        predicted[t] = c @ x + d
        seen = ~np.isnan(y[t])
        if seen.any():
            ct = c[seen]
            err = y[t, seen] - predicted[t, seen]
            s = ct @ p @ ct.T + np.diag(r[seen])
            solved = np.linalg.solve(s, np.column_stack([err, ct @ p]))
            loglik -= 0.5 * (
                seen.sum() * np.log(2 * np.pi) + np.linalg.slogdet(s)[1] + err @ solved[:, 0]
            )
            x = x + (ct @ p).T @ solved[:, 0]
            p = p - (ct @ p).T @ solved[:, 1:]
        means[t], covs[t] = x, p
        x = a @ x + b * u[t]
        p = a @ p @ a.T + q
    return FilterResult(loglik, means, covs, predicted)
