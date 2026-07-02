def test_run_sweep_smoke_includes_each_regime_and_verdict():
    from ball_spin.run_budget_0e import run_sweep

    grid, verdict = run_sweep(n=1, seed=9, axes=("dt_ms",), regimes=("driver", "iron", "wedge"))

    assert {cell["regime"] for cell in grid} == {"driver", "iron", "wedge"}
    assert {"combined", "dt_ms"} <= {cell["axis"] for cell in grid}
    assert set(verdict["requirement_boundaries"]) == {"driver", "iron", "wedge"}
