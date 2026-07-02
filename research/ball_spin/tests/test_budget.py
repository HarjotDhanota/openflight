import math


def test_run_budget_counts_ambiguity_failures_as_attempts():
    from ball_spin.budget import run_budget

    result = run_budget(
        regime="iron",
        n=8,
        seed=4,
        n_frames=4,
        n_dots=80,
        dt_ms=4.2,
        rate_range_rpm=(7500.0, 7500.0),
        axis_tilt_range_deg=(4.0, 4.0),
        vla_range_deg=(12.0, 12.0),
        hla_range_deg=(0.0, 0.0),
        sigma_dot_px=0.0,
        sigma_center_px=0.0,
        sigma_radius_px=0.0,
        dropout=0.0,
        p_misid=0.0,
        beta=0.15,
        ball_px=250.0,
    )

    assert result["n_attempted"] == 8
    assert result["n_ok"] == 0
    assert result["ok_rate"] == 0.0
    assert all(row["ambiguous"] for row in result["rows"])


def test_run_budget_counts_fov_losses_and_reports_usable_frames():
    from ball_spin.budget import run_budget

    result = run_budget(
        regime="driver",
        n=8,
        seed=5,
        n_frames=4,
        dt_ms=8.0,
        hla_range_deg=(75.0, 75.0),
        sigma_dot_px=0.0,
        sigma_center_px=0.0,
        sigma_radius_px=0.0,
        dropout=0.0,
        p_misid=0.0,
    )

    assert result["n_attempted"] == 8
    assert result["n_ok"] < result["n_attempted"]
    assert result["median_usable_frames"] < 4


def test_budget_verdict_builds_per_regime_requirement_boundaries():
    from ball_spin.budget import budget_verdict

    grid = [
        {
            "regime": "driver",
            "axis": "ball_px",
            "value": 100,
            "params": {"ball_px": 100},
            "n_attempted": 10,
            "n_ok": 10,
            "rows": [{"ok": True, "rate_error_pct": 2.0, "axis_error_deg": 4.0, "tilt_error_deg": 3.0}],
        },
        {
            "regime": "driver",
            "axis": "ball_px",
            "value": 150,
            "params": {"ball_px": 150},
            "n_attempted": 10,
            "n_ok": 10,
            "rows": [{"ok": True, "rate_error_pct": 2.0, "axis_error_deg": 2.5, "tilt_error_deg": 2.0}],
        },
        {
            "regime": "iron",
            "axis": "dt_ms",
            "value": 2.0,
            "params": {"dt_ms": 2.0},
            "n_attempted": 10,
            "n_ok": 10,
            "rows": [{"ok": True, "rate_error_pct": 2.5, "axis_error_deg": 2.0, "tilt_error_deg": 1.0}],
        },
        {
            "regime": "iron",
            "axis": "dt_ms",
            "value": 4.2,
            "params": {"dt_ms": 4.2},
            "n_attempted": 10,
            "n_ok": 8,
            "rows": [{"ok": True, "rate_error_pct": 1.0, "axis_error_deg": 1.0, "tilt_error_deg": 1.0}],
        },
    ]

    verdict = budget_verdict(grid)

    assert verdict["requirement_boundaries"]["driver"]["axis_3deg"]["ball_px"] == 150
    assert verdict["requirement_boundaries"]["driver"]["axis_5deg"]["ball_px"] == 100
    assert verdict["requirement_boundaries"]["iron"]["axis_3deg"]["dt_ms"] == 2.0
    assert verdict["ok_rate_min"] == 0.8
    assert math.isfinite(verdict["cells"][0]["axis_error_deg_median"])
