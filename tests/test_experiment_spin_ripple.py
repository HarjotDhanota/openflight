"""Tests for the ripple experiment's row building and summary metrics."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "analysis"))

from experiment_spin_ripple import _pair_shots_with_captures, summarize  # noqa: E402


def _row(spin_tm, env_rpm, env_detected, variant_rpm, variant_detected):
    row = {
        "shot_number": 1,
        "spin_tm": spin_tm,
        "env_rpm": env_rpm,
        "env_detected": env_detected,
    }
    for name in ("freq_hop32", "mag_hop32", "freq_hop16", "mag_hop16"):
        row[f"{name}_rpm"] = variant_rpm
        row[f"{name}_detected"] = variant_detected
    return row


def test_summarize_counts_rescue():
    # Envelope missed; variant within 500 RPM of TrackMan -> rescue.
    rows = [_row(spin_tm=3000.0, env_rpm=None, env_detected=False,
                 variant_rpm=3200.0, variant_detected=True)]
    summary = {entry["method"]: entry for entry in summarize(rows)}
    assert summary["freq_hop32"]["rescues"] == 1
    assert summary["freq_hop32"]["regressions"] == 0
    assert summary["envelope"]["detected"] == 0


def test_summarize_counts_regression_on_miss():
    # Envelope accurate; variant detected but off by >300 -> regression.
    rows = [_row(spin_tm=3000.0, env_rpm=3100.0, env_detected=True,
                 variant_rpm=3900.0, variant_detected=True)]
    summary = {entry["method"]: entry for entry in summarize(rows)}
    assert summary["freq_hop32"]["regressions"] == 1
    assert summary["freq_hop32"]["rescues"] == 0


def test_summarize_counts_regression_on_no_detect():
    # Envelope accurate; variant silent -> regression.
    rows = [_row(spin_tm=3000.0, env_rpm=3100.0, env_detected=True,
                 variant_rpm=None, variant_detected=False)]
    summary = {entry["method"]: entry for entry in summarize(rows)}
    assert summary["freq_hop32"]["regressions"] == 1


def test_pairing_joins_by_shot_number():
    # Shots 1, 2, 3 but only captures for 1 and 3 -> shot 2 is skipped, not
    # shifted onto capture 3's data via positional zip.
    shots = [
        {"shot_number": 1},
        {"shot_number": 2},
        {"shot_number": 3},
    ]
    captures = [
        {"shot_number": 1, "marker": "cap1"},
        {"shot_number": 3, "marker": "cap3"},
    ]
    pairs = _pair_shots_with_captures(shots, captures)
    assert [(shot["shot_number"], capture["marker"]) for shot, capture in pairs] == [
        (1, "cap1"),
        (3, "cap3"),
    ]


def test_pairing_falls_back_to_positional():
    # No capture carries shot_number (older log format) -> positional zip.
    shots = [{"shot_number": 1}, {"shot_number": 2}]
    captures = [{"marker": "capA"}, {"marker": "capB"}]
    pairs = _pair_shots_with_captures(shots, captures)
    assert [(shot["shot_number"], capture["marker"]) for shot, capture in pairs] == [
        (1, "capA"),
        (2, "capB"),
    ]


def test_summarize_accuracy_metrics():
    rows = [
        _row(spin_tm=3000.0, env_rpm=3100.0, env_detected=True,
             variant_rpm=3100.0, variant_detected=True),
        _row(spin_tm=5000.0, env_rpm=5400.0, env_detected=True,
             variant_rpm=4900.0, variant_detected=True),
    ]
    summary = {entry["method"]: entry for entry in summarize(rows)}
    assert summary["envelope"]["mae_rpm"] == 250.0
    assert summary["envelope"]["within_300_pct"] == 50.0
    assert summary["freq_hop32"]["mae_rpm"] == 100.0
    assert summary["freq_hop32"]["within_300_pct"] == 100.0
    assert summary["freq_hop32"]["coverage_pct"] == 100.0
