import numpy as np
import pandas as pd
import pytest
from physio_state.models import banister
from physio_state.models.kalman import kalman_filter
from physio_state.models.simulate import WARMUP_WEEKS, Scenario, simulate, study_length
from physio_state.panel.baselines import past_mean, past_z
from physio_state.panel.day_panel import COLS, build_day_panel
from scipy.stats import multivariate_normal


def test_kalman_likelihood_matches_the_joint_normal_with_missing_values():
    a, b = np.array([[0.8]]), np.zeros(1)
    c, d, q, r = (
        np.array([[1.0], [0.5]]),
        np.array([1.0, 2.0]),
        np.array([[0.3]]),
        np.array([0.2, 0.4]),
    )
    y = np.array([[1.2, np.nan], [0.7, 2.5], [np.nan, 1.9]])
    got = kalman_filter(y, np.zeros(3), a, b, c, d, q, r, np.zeros(1), np.eye(1)).loglik

    var = [1.0]
    for _ in range(2):
        var.append(0.64 * var[-1] + 0.3)
    cov_x = np.array([[var[min(i, j)] * 0.8 ** abs(i - j) for j in range(3)] for i in range(3)])
    rows = [(0, 0), (1, 0), (1, 1), (2, 1)]  # (time, channel) seen
    h = np.array([[c[k, 0] if t == s else 0 for s in range(3)] for t, k in rows])
    mean = np.array([d[k] for _, k in rows])
    cov = h @ cov_x @ h.T + np.diag([r[k] for _, k in rows])
    want = multivariate_normal(mean, cov).logpdf([y[t, k] for t, k in rows])
    assert got == pytest.approx(want)


def test_grid_fit_finds_time_constants_without_noise():
    rng = np.random.default_rng(3)
    u = rng.random(600) * (rng.random(600) < 0.4)
    y = (banister.decay_state(u, 7) - 0.5 * banister.decay_state(u, 42))[:, None]
    g = banister.grid_fit(y + rng.normal(0, 1e-6, y.shape), u)
    assert (g.tau_fast, g.tau_slow) == (7, 42)
    assert g.two_state_preferred


def test_simulator_channels_follow_their_slots():
    y, u = simulate(8, Scenario(missing=0), np.random.default_rng(1))
    assert y.shape == (8 * 14, 4) and len(u) == (8 + WARMUP_WEEKS) * 14
    assert np.isnan(y[1::2, 0]).all() and np.isnan(y[0::2, 1]).all()
    assert np.isnan(y[1::2, 2:]).all()
    assert not np.isnan(y[0::2, [0, 2, 3]]).any()


def test_longer_studies_recover_more():
    t = study_length(weeks=(6, 52), snrs=(1.5,), reps=30)
    assert t["both_recovered"].iloc[1] > t["both_recovered"].iloc[0]


def test_filter_beats_the_seven_day_baseline_on_simulated_data():
    s = Scenario(snr_fast=2.0, snr_slow=1.0, missing=0.0)
    y, u = simulate(30, s, np.random.default_rng(5))
    u = u[WARMUP_WEEKS * 14 :]
    channels = y[:, [0, 2]]
    split = 20 * 14
    p = banister.fit(channels[:split], u[:split], maxiter=25)
    pred = banister.run_filter(p, channels, u).predicted_y[split:, 0]
    real = pd.Series(channels[:, 0])
    base = real.dropna().pipe(past_mean, 7, 3).reindex(real.index).ffill().to_numpy()[split:]
    seen = ~np.isnan(channels[split:, 0]) & ~np.isnan(base)
    truth = channels[split:, 0]
    assert np.mean((truth[seen] - pred[seen]) ** 2) < np.mean((truth[seen] - base[seen]) ** 2)


def test_baselines_ignore_the_present_and_future():
    x = pd.Series([1.0, 2, 3, 4, 100])
    assert past_mean(x, 3, 1).iloc[3] == 2
    assert past_z(x, 4, 3).iloc[3] == pytest.approx(2.0)


def tables():
    d = pd.to_datetime(["2026-11-01", "2026-11-02", "2026-11-03"]).date
    pm = pd.Timestamp("2026-11-02T18:00:00Z")
    return {
        "hrv_nightly": pd.DataFrame(
            {"day": d[:2], "hrv_ln_mean": [4.2, 4.3], "n_readings": [80, 80], "garmin_status": "x"}
        ),
        "sleep_nightly": pd.DataFrame(
            {"day": d[:1], "sleep_s": [25000], "avg_hr": [50], "avg_respiration": [14.0]}
        ),
        "hr_daily": pd.DataFrame({"day": d, "resting_hr": [50, 51, 49]}),
        "sessions": pd.DataFrame(
            {"day": [d[1]], "slot": ["PM"], "session_id": ["x"], "checkin_utc": [pm]}
        ),
        "load_events": pd.DataFrame(
            {
                "day": [d[1], d[1], d[2]],
                "start_utc": [pm - pd.Timedelta(hours=3), pm + pd.Timedelta(hours=1), pm],
                "srpe_load": [100.0, 40.0, None],
                "garmin_training_load": [10.0, 4.0, 2.0],
                "edwards_trimp": [1.0, 1.0, 1.0],
            }
        ),
        "load_daily": pd.DataFrame({"day": d}),
        "anchors": pd.DataFrame({"day": d, "readiness": [1, 2, 3]}),
    }


def test_panel_orders_load_around_the_evening_session():
    p = build_day_panel(tables()).set_index(["day", "slot"])
    d1, d2 = pd.to_datetime(["2026-11-01", "2026-11-02"]).date
    assert p.loc[(d2, "AM"), "load_srpe"] == 100
    assert p.loc[(d2, "PM"), "load_srpe"] == 40
    assert p.loc[(d1, "AM"), "load_srpe"] == 0  # a logged rest day
    assert p.loc[(d2, "PM"), "session_id"] == "x"
    assert pd.isna(p.loc[(d2, "PM"), "night_hrv_ln"])  # overnight values sit on AM rows


def test_day_with_unrated_event_has_no_srpe():
    p = build_day_panel(tables()).set_index(["day", "slot"])
    d3 = pd.to_datetime("2026-11-03").date()
    assert pd.isna(p.loc[(d3, "AM"), "load_srpe"])
    assert p.loc[(d3, "AM"), "load_garmin"] == 2


def test_unrated_event_is_not_a_rest_day():
    t = tables()
    t["load_events"]["garmin_training_load"] = None
    t["load_events"]["edwards_trimp"] = None
    t["load_events"]["srpe_load"] = None
    p = build_day_panel(t).set_index(["day", "slot"])
    d3 = pd.to_datetime("2026-11-03").date()
    assert p.loc[(d3, "AM"), ["load_srpe", "load_garmin", "load_trimp"]].isna().all()


def test_panel_holds_no_anchors_or_vendor_scores():
    assert not [
        c for c in COLS if c.startswith("garmin_") or c in ("readiness", "soreness", "stress")
    ]


def test_past_z_does_not_change_when_the_future_changes():
    x = pd.Series([1.0, 2, 3, 4, 5, 6])
    y = x.copy()
    y.iloc[5] = -50
    pd.testing.assert_series_equal(past_z(x, 4, 3).iloc[:5], past_z(y, 4, 3).iloc[:5])
