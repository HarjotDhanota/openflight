import math

import numpy as np

from club_pose.sim.budget import budget_verdict, run_budget


def _median(rows, key):
    vals = [r[key] for r in rows if r.get("ok") and math.isfinite(r[key])]
    return float(np.median(vals))


def _angle_median(result):
    rows = result["rows"]
    face = _median(rows, "face_err_deg")
    loft = _median(rows, "loft_err_deg")
    return max(face, loft)


def test_calibration_error_is_visible_with_zero_pixel_noise_and_scales():
    kwargs = dict(
        club="driver",
        mode="mono",
        sigma_c=0.0,
        delta_bias=0.0,
        ball_depth_sigma=0.0,
        sync_jitter_us=0.0,
        vel_err_frac=0.0,
        baseline_mm=150.0,
        n=24,
        seed=4,
    )
    clean = run_budget(sigma_cal=0.0, **kwargs)
    lo = run_budget(sigma_cal=0.2, **kwargs)
    hi = run_budget(sigma_cal=1.0, **kwargs)

    assert _angle_median(clean) < 1e-3
    assert _angle_median(lo) > 0.01
    assert _angle_median(hi) > _angle_median(lo) * 2.0


def test_timing_error_tracks_club_speed_times_sync_jitter():
    lo = run_budget(
        club="driver",
        mode="mono",
        sigma_c=0.0,
        delta_bias=0.0,
        sigma_cal=0.0,
        ball_depth_sigma=0.0,
        sync_jitter_us=50.0,
        vel_err_frac=0.0,
        baseline_mm=150.0,
        n=80,
        seed=8,
    )
    hi = run_budget(
        club="driver",
        mode="mono",
        sigma_c=0.0,
        delta_bias=0.0,
        sigma_cal=0.0,
        ball_depth_sigma=0.0,
        sync_jitter_us=200.0,
        vel_err_frac=0.0,
        baseline_mm=150.0,
        n=80,
        seed=8,
    )

    lo_med = _median(lo["rows"], "timing_translation_error_mm")
    hi_med = _median(hi["rows"], "timing_translation_error_mm")
    expected_abs_median = 45_000.0 * 200e-6 * 0.674
    assert hi_med > lo_med * 3.0
    assert expected_abs_median * 0.5 <= hi_med <= expected_abs_median * 1.5


def test_ball_depth_error_drives_height_and_stereo_depth_reduces_it():
    mono = run_budget(
        club="iron",
        mode="mono",
        sigma_c=0.0,
        delta_bias=0.0,
        sigma_cal=0.0,
        ball_depth_sigma=15.0,
        sync_jitter_us=0.0,
        vel_err_frac=0.0,
        baseline_mm=150.0,
        n=80,
        seed=11,
    )
    stereo = run_budget(
        club="iron",
        mode="stereo",
        sigma_c=0.0,
        delta_bias=0.0,
        sigma_cal=0.0,
        ball_depth_sigma=3.0,
        sync_jitter_us=0.0,
        vel_err_frac=0.0,
        baseline_mm=150.0,
        n=80,
        seed=11,
    )

    assert _median(mono["rows"], "height_err_mm") > _median(stereo["rows"], "height_err_mm") * 2.0


def test_correlated_bias_hurts_face_loft_more_than_same_independent_noise():
    kwargs = dict(
        club="driver",
        mode="mono",
        sigma_cal=0.0,
        ball_depth_sigma=0.0,
        sync_jitter_us=0.0,
        vel_err_frac=0.0,
        baseline_mm=150.0,
        n=60,
        seed=17,
    )
    independent = run_budget(sigma_c=2.0, delta_bias=0.0, **kwargs)
    correlated = run_budget(sigma_c=0.0, delta_bias=2.0, **kwargs)

    assert _angle_median(correlated) > _angle_median(independent) * 1.2


def test_budget_verdict_keeps_failed_solves_in_ok_rate_gate():
    grid = [
        {
            "club": "driver",
            "mode": "mono",
            "axis": "forced_failures",
            "params": {},
            "n_attempted": 10,
            "n_ok": 8,
            "rows": [{"ok": i < 8, "face_err_deg": 1.0, "loft_err_deg": 1.0,
                      "offset_err_mm": 1.0, "height_err_mm": 1.0, "impact_err_mm": 1.0}
                     for i in range(10)],
        }
    ]

    verdict = budget_verdict(grid)
    assert verdict["cells"][0]["ok_rate"] == 0.8
    assert verdict["cells"][0]["meets_ok_gate"] is False
