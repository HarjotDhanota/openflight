"""Regression guard: ballistic model accuracy against committed TrackMan data.

The repo ships paired TrackMan reference captures in ``session_logs/`` and a
manual validator (``scripts/analysis/validate_ballistics.py``), but nothing in
the test suite reads them. That gap lets an accuracy regression ship green:
``test_ballistics.py`` asserts driver carry only within 250-300 yd, a 50-yard
window, so a 20-40 yard model bias passes unnoticed.

This module closes it by replaying the committed capture through the production
``resolve_launch``/``simulate`` path and asserting per-club error against a
budget. The budget is seeded at the CURRENT measured error, not at zero, so it
blocks *new* error immediately while leaving the existing bias to be paid down
deliberately. Ratchet the numbers down when the model improves; never up.

Reuses the validator's own loading/statistics helpers so the test and the
manual tool can never disagree about what the reference data means.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ANALYSIS = _REPO_ROOT / "scripts" / "analysis"
for _path in (_ANALYSIS, _REPO_ROOT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from validate_ballistics import (  # noqa: E402  (path set up above)
    _stats,
    load_trackman,
    validate_tm_inputs,
)

TRACKMAN_CSV = _REPO_ROOT / "session_logs" / "OpenFlight-Test.Normalized.csv"

# Per-club RMSE ceilings in yards. Ratcheted three times: after the aero
# coefficients were re-fit against this capture (#229), when the Cd/Cl
# functional form moved to the Ferguson quadratics (#230), and when E4's W7
# frame fixes made carry the straight-line distance to the landing point and
# turned the spin axis with the launch direction (no coefficient changed).
#
#                    pre-#229          #229 (Hill)       quadratic        + W7 frames
#   club            rmse     bias      rmse    bias     rmse    bias     rmse    bias
#   7-iron         11.64   +11.00      2.96   +1.52     1.33   +1.30     1.82   +1.76
#   driver         38.60   -37.18      1.75   -0.45     2.38   -1.33     1.10   -0.35
#   pitching wedge 13.56   +11.02      6.26   -2.96     4.48   +1.83     3.51   +3.26
#   OVERALL        24.52    -5.05      3.97   -0.45     2.90   +0.58     2.29   +1.50
#
# Driver moved up within its ceiling under the quadratic: the coefficients are
# an external published fit, not tuned to this capture, and the ceiling was
# left where it was rather than raised. Under W7 the 7-iron moved up within its
# ceiling: the straight-line carry is longer than the old x component on
# offline shots, which adds to the quadratic's existing +1.3 yd 7-iron bias.
#
# V-formulas C4 + C7 (both deliberate, not regressions, so the budgets were
# RAISED, and say so): spin decay by Ferguson 2022's own C_M = 0.01 S, and the
# model now flies the kiosk's own normalized reference (25 C, 1013.25 hPa, RH 50 %:
# 1.1769 kg/m3) instead of a hand-typed dry 1.184 kg/m3, so the comparison is
# normalized against normalized at the reference the kiosk reports. Thinner air
# carries farther, which adds to the 7-iron's existing positive bias.
#
#                    + C4 (dry 1.184)    + C7 (RH 50 %)
#   club            rmse     bias       rmse    bias
#   7-iron           1.81   +1.75       2.25   +2.20
#   driver           1.44   -0.60       1.54   -0.34
#   pitching wedge   3.43   +3.18       3.73   +3.47
#   OVERALL          2.31   +1.39       2.60   +1.72
#
# E8a (flight-fit-v1): Ferguson's form with its seven constants refitted to TM4's
# normalized exports on carry, apex, landing angle and hang time jointly (this
# capture plus Robinante's public 43-shot session; see
# ballistics.FLIGHT_FIT_V1.calibration). Ratcheted DOWN. NOTE: this capture is
# part of the fit, so these numbers are in-sample; the held-out numbers (pooled
# 10-fold, leave-one-out, leave-one-source-out) are in the calibration record
# and docs/how-it-works/ballistics.md.
#
#                    + C7 (Ferguson)     flight-fit-v1
#   club            rmse     bias       rmse    bias
#   7-iron           2.25   +2.20       0.39   -0.24
#   driver           1.54   -0.34       1.19   -0.04
#   pitching wedge   3.73   +3.47       1.64   -1.62
#   OVERALL          2.60   +1.72       1.14   -0.57
#
# Ceilings sit slightly above the measured values to absorb float/platform
# drift. Lower them again if the model improves; never raise them without a
# stated, deliberate change of reference (as above).
RMSE_BUDGET_YARDS = {
    "driver": 1.25,
    "7-iron": 0.45,
    "pitching wedge": 1.7,
}
OVERALL_RMSE_BUDGET_YARDS = 1.2

# Apex ceilings in FEET (TrackMan's "Max Height - Height" column is feet).
# Until E8a nothing was fitted against apex, so the Hill -> quadratic improvement
# below was out-of-sample. Since flight-fit-v1 apex is a fit target and this
# capture is in the fit: the E8a row is in-sample.
#
#   club            Hill (#229)   quadratic
#   7-iron            18.15         1.53
#   driver             5.08         3.17
#   pitching wedge    18.00         2.81
#   OVERALL           15.05         2.56
#
#   + C4 and C7 (RH 50 %): driver 4.08, 7-iron 1.74, PW 2.77, overall 2.99
#   (driver and overall budgets RAISED for that stated change of reference).
#   E8a flight-fit-v1 (apex now a fit target; in-sample here):
#                    driver 0.73, 7-iron 1.05, PW 0.76, overall 0.87
#
# Seeded above measured to absorb float/platform drift, matching the carry
# budgets above. Lower them when the model improves; never raise them.
#   Spin axis turned about the launch direction (no rifle spin): driver 0.80,
#   7-iron 1.05, PW 0.82; driver and PW budgets RAISED for that stated change.
APEX_RMSE_BUDGET_FEET = {
    "driver": 0.85,
    "7-iron": 1.1,
    "pitching wedge": 0.85,
}
OVERALL_APEX_RMSE_BUDGET_FEET = 0.9

# Landing angle ceilings in DEGREES ("Carry Flat - Land. Angle"), added with E8a.
#
#   club            Ferguson (C7)   flight-fit-v1
#   7-iron              2.26           0.76
#   driver              1.96           0.37
#   pitching wedge      4.00           1.24
#   OVERALL             2.80           0.84
#   Spin axis turned about the launch direction: driver 0.41 (budget RAISED).
LANDING_ANGLE_RMSE_BUDGET_DEG = {
    "driver": 0.45,
    "7-iron": 0.8,
    "pitching wedge": 1.3,
}
OVERALL_LANDING_ANGLE_RMSE_BUDGET_DEG = 0.9

# Hang time ceilings in SECONDS ("Carry Flat - Time"), added with E8a.
#
#   club            Ferguson (C7)   flight-fit-v1
#   7-iron              0.04           0.10
#   driver              0.23           0.08
#   pitching wedge      0.22           0.04
#   OVERALL             0.18           0.08
#
# The 7-iron's hang time got worse (+0.10 s bias, from -0.04): the joint fit traded
# it for the other outputs. Its ceiling is set at the new value, not the old one.
HANG_TIME_RMSE_BUDGET_S = {
    "driver": 0.09,
    "7-iron": 0.11,
    "pitching wedge": 0.05,
}
OVERALL_HANG_TIME_RMSE_BUDGET_S = 0.09

# The reference capture is a fixed, committed file: if the shot count changes,
# the fixture changed and every budget above needs re-deriving.
EXPECTED_SHOT_COUNT = 24


@pytest.fixture(scope="module")
def validation_rows():
    if not TRACKMAN_CSV.exists():
        pytest.skip(f"TrackMan reference capture not present: {TRACKMAN_CSV}")
    rows = validate_tm_inputs(load_trackman(TRACKMAN_CSV))
    if not rows:
        pytest.skip("TrackMan reference capture produced no usable shots")
    return rows


def test_reference_capture_shot_count(validation_rows):
    """Pin the fixture size so budget drift cannot hide behind a changed file."""
    assert len(validation_rows) == EXPECTED_SHOT_COUNT, (
        f"Reference capture has {len(validation_rows)} usable shots, expected "
        f"{EXPECTED_SHOT_COUNT}. If the fixture changed intentionally, re-derive "
        f"the RMSE budgets in this module from a fresh validator run."
    )


def test_overall_carry_rmse_within_budget(validation_rows):
    """Model carry vs TrackMan carry, fed TrackMan's own launch conditions."""
    stats = _stats([r.delta_yards for r in validation_rows])
    assert stats["rmse"] <= OVERALL_RMSE_BUDGET_YARDS, (
        f"Overall carry RMSE {stats['rmse']:.2f} yd exceeds budget "
        f"{OVERALL_RMSE_BUDGET_YARDS} yd (bias {stats['mean']:+.2f}, "
        f"max |delta| {stats['max_abs']:.2f}, n={stats['n']}). "
        f"The ballistic model got less accurate against the reference capture."
    )


@pytest.mark.parametrize("club", sorted(RMSE_BUDGET_YARDS))
def test_per_club_carry_rmse_within_budget(validation_rows, club):
    """Per-club budgets: a club-specific regression cannot hide in the average."""
    deltas = [r.delta_yards for r in validation_rows if r.club == club]
    assert deltas, f"No reference shots for club {club!r}"
    stats = _stats(deltas)
    budget = RMSE_BUDGET_YARDS[club]
    assert stats["rmse"] <= budget, (
        f"{club}: carry RMSE {stats['rmse']:.2f} yd exceeds budget {budget} yd "
        f"(bias {stats['mean']:+.2f}, max |delta| {stats['max_abs']:.2f}, "
        f"n={stats['n']})."
    )


def _apex_deltas(rows, club=None):
    """Apex errors in feet, skipping rows whose source carried no apex."""
    return [
        r.delta_apex_feet
        for r in rows
        if r.delta_apex_feet is not None and (club is None or r.club == club)
    ]


def test_reference_capture_has_apex_measurements(validation_rows):
    """Pin apex coverage: a fixture silently losing the column would turn
    every apex budget below into a vacuous pass over an empty list."""
    deltas = _apex_deltas(validation_rows)
    assert len(deltas) == EXPECTED_SHOT_COUNT, (
        f"{len(deltas)} of {EXPECTED_SHOT_COUNT} reference shots carry an apex "
        f"measurement. The fixture's 'Max Height - Height' column changed."
    )


def test_overall_apex_rmse_within_budget(validation_rows):
    """Model apex vs TrackMan apex, fed TrackMan's own launch conditions."""
    stats = _stats(_apex_deltas(validation_rows))
    assert stats["rmse"] <= OVERALL_APEX_RMSE_BUDGET_FEET, (
        f"Overall apex RMSE {stats['rmse']:.2f} ft exceeds budget "
        f"{OVERALL_APEX_RMSE_BUDGET_FEET} ft (bias {stats['mean']:+.2f}, "
        f"max |delta| {stats['max_abs']:.2f}, n={stats['n']}). "
        f"The ballistic model's flight height regressed."
    )


@pytest.mark.parametrize("club", sorted(APEX_RMSE_BUDGET_FEET))
def test_per_club_apex_rmse_within_budget(validation_rows, club):
    """Per-club apex budgets: trajectory shape is club-dependent, and the
    quadratic's biggest win (irons and wedges) must not silently erode."""
    deltas = _apex_deltas(validation_rows, club)
    assert deltas, f"No reference shots with apex for club {club!r}"
    stats = _stats(deltas)
    budget = APEX_RMSE_BUDGET_FEET[club]
    assert stats["rmse"] <= budget, (
        f"{club}: apex RMSE {stats['rmse']:.2f} ft exceeds budget {budget} ft "
        f"(bias {stats['mean']:+.2f}, max |delta| {stats['max_abs']:.2f}, "
        f"n={stats['n']})."
    )


def _deltas(rows, attr, club=None):
    """Errors for one output, skipping rows whose source did not measure it."""
    return [
        getattr(r, attr)
        for r in rows
        if getattr(r, attr) is not None and (club is None or r.club == club)
    ]


SHAPE_BUDGETS = {
    # output: (row attribute, unit, per-club budgets, overall budget)
    "landing angle": (
        "delta_landing_angle_deg",
        "deg",
        LANDING_ANGLE_RMSE_BUDGET_DEG,
        OVERALL_LANDING_ANGLE_RMSE_BUDGET_DEG,
    ),
    "hang time": (
        "delta_hang_time_s",
        "s",
        HANG_TIME_RMSE_BUDGET_S,
        OVERALL_HANG_TIME_RMSE_BUDGET_S,
    ),
}


@pytest.mark.parametrize("output", sorted(SHAPE_BUDGETS))
def test_reference_capture_has_every_shape_measurement(validation_rows, output):
    attr = SHAPE_BUDGETS[output][0]
    assert len(_deltas(validation_rows, attr)) == EXPECTED_SHOT_COUNT, (
        f"the fixture lost its {output} column: every {output} budget would pass vacuously"
    )


@pytest.mark.parametrize("output", sorted(SHAPE_BUDGETS))
def test_overall_shape_rmse_within_budget(validation_rows, output):
    attr, unit, _, budget = SHAPE_BUDGETS[output]
    stats = _stats(_deltas(validation_rows, attr))
    assert stats["rmse"] <= budget, (
        f"Overall {output} RMSE {stats['rmse']:.2f} {unit} exceeds budget {budget} {unit} "
        f"(bias {stats['mean']:+.2f}, max |delta| {stats['max_abs']:.2f}, n={stats['n']})."
    )


@pytest.mark.parametrize(
    "output, club",
    [(o, c) for o in sorted(SHAPE_BUDGETS) for c in sorted(SHAPE_BUDGETS[o][2])],
)
def test_per_club_shape_rmse_within_budget(validation_rows, output, club):
    attr, unit, budgets, _ = SHAPE_BUDGETS[output]
    deltas = _deltas(validation_rows, attr, club)
    assert deltas, f"No reference shots with {output} for club {club!r}"
    stats = _stats(deltas)
    assert stats["rmse"] <= budgets[club], (
        f"{club}: {output} RMSE {stats['rmse']:.2f} {unit} exceeds budget {budgets[club]} "
        f"{unit} (bias {stats['mean']:+.2f}, max |delta| {stats['max_abs']:.2f}, "
        f"n={stats['n']})."
    )


# Total ("Est. Total Flat - Length", yards): the normalized flight's carry plus the
# active fairway bounce and roll, against the export's estimated total. Added with
# turf-roll-v2, whose hops fly through the air (the roll is not fitted to this
# capture: out of sample).
#
#   club            turf-roll-v1     turf-roll-v2
#                   rmse   bias      rmse   bias
#   7-iron          0.49  +0.28      0.54  +0.31
#   driver          3.48  +1.94      2.08  +0.76
#   pitching wedge  0.94  -0.83      1.23  -1.19
#   OVERALL         2.09             1.41
#
# The wedge's total moved down with the shorter roll; flight-fit-v1 carries this
# capture's wedges short, which turf-roll-v1's longer roll had partly hidden.
TOTAL_RMSE_BUDGET_YARDS = {
    "driver": 2.15,
    "7-iron": 0.6,
    "pitching wedge": 1.3,
}
OVERALL_TOTAL_RMSE_BUDGET_YARDS = 1.45


@pytest.fixture(scope="module")
def total_deltas():
    """(club, model total - export total) per shot, flown at the normalized density."""
    import math

    from tm_owner_export import number, owner_shots
    from validate_ballistics import TM_FLAT_AIR_DENSITY, _normalize_club

    from openflight.ballistics import LaunchConditions, simulate

    if not TRACKMAN_CSV.exists():
        pytest.skip(f"TrackMan reference capture not present: {TRACKMAN_CSV}")
    out = []
    for shot in owner_shots():
        launch = LaunchConditions(
            number(shot, "Ball Speed"),
            number(shot, "Launch Angle"),
            number(shot, "Launch Direction") or 0.0,
            number(shot, "Spin Rate"),
            number(shot, "Spin Axis") or 0.0,
            "measured",
        )
        rest = simulate(launch, air_density=TM_FLAT_AIR_DENSITY).rest_point()
        total = number(shot, "Est. Total Flat - Length")
        if rest is not None and total is not None:
            out.append((_normalize_club(shot["Club"]), math.hypot(*rest) - total))
    return out


def test_every_reference_shot_has_a_total(total_deltas):
    assert len(total_deltas) == EXPECTED_SHOT_COUNT


def test_overall_total_rmse_within_budget(total_deltas):
    stats = _stats([d for _, d in total_deltas])
    assert stats["rmse"] <= OVERALL_TOTAL_RMSE_BUDGET_YARDS, (
        f"Overall total RMSE {stats['rmse']:.2f} yd exceeds budget "
        f"{OVERALL_TOTAL_RMSE_BUDGET_YARDS} yd (bias {stats['mean']:+.2f}, n={stats['n']})."
    )


@pytest.mark.parametrize("club", sorted(TOTAL_RMSE_BUDGET_YARDS))
def test_per_club_total_rmse_within_budget(total_deltas, club):
    stats = _stats([d for c, d in total_deltas if c == club])
    assert stats["rmse"] <= TOTAL_RMSE_BUDGET_YARDS[club], (
        f"{club}: total RMSE {stats['rmse']:.2f} yd exceeds budget "
        f"{TOTAL_RMSE_BUDGET_YARDS[club]} yd (bias {stats['mean']:+.2f}, n={stats['n']})."
    )
