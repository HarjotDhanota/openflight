"""spin-refit-v1 held out on TM rows: each row predicted by a fit that never saw its session.

The table is every 6th fitted shot of each session (``fit_spin_refit.py --pinned``):
TM's own outputs only (club, ball speed, club speed, vertical launch, total spin).
Each row is predicted by the leave-one-session-out fold that held its session out
(the folds' coefficients are recorded in ``spin_refit_v1.json``), so the check is
out of sample. A small table is noisy: on these 19 rows the first fit
(170 v sin(LA)^1.2) happens to do better than the club tier, while over all 125
shared held-out shots the club tier's MAE is lower (the recorded comparison).
"""

from __future__ import annotations

import math

import pytest

from openflight.clubs.physics import get_club_physics
from openflight.clubs.types import ClubType
from openflight.spin_estimate import (
    SPIN_REFIT,
    TIER_CLUB,
    TIER_CLUB_SPEED,
    TIER_LAUNCH,
    club_category,
)

# (session, club, TM ball speed mph, TM club speed mph, TM vertical launch deg,
#  TM total spin rpm, the club tier's held-out prediction rpm)
ROWS = [
    ("2026-05-06", "7-iron", 123.8, 85.4, 15.4, 5492, 6253),
    ("2026-05-06", "7-iron", 121.7, 85.5, 17.0, 5671, 6147),
    ("2026-05-06", "driver", 162.6, 109.4, 12.3, 2321, 3943),
    ("2026-05-06", "pw", 82.7, 80.5, 18.5, 10456, 4925),
    ("2026-10-04", "pw", 61.7, 53.0, 26.8, 3720, 4339),
    ("2026-10-04", "pw", 80.1, 62.4, 26.8, 4427, 5633),
    ("2026-10-04", "pw", 64.7, 50.8, 19.6, 3741, 4550),
    ("2026-10-04", "8-iron", 76.8, 62.7, 14.8, 2362, 4442),
    ("2026-10-04", "8-iron", 94.4, 70.3, 21.1, 5279, 5459),
    ("2026-10-04", "7-iron", 97.2, 71.8, 17.4, 4282, 4982),
    ("2026-10-04", "6-iron", 86.8, 68.5, 14.6, 1250, 3939),
    ("2026-10-04", "6-iron", 100.6, 70.4, 17.3, 3720, 4565),
    ("2026-10-04", "5-wood", 112.0, 79.2, 15.2, 4242, 2725),
    ("2026-10-12", "pw", 56.6, 53.2, 27.9, 6530, 3462),
    ("2026-10-12", "pw", 72.3, 56.8, 27.2, 3289, 4422),
    ("2026-10-12", "pw", 82.9, 66.5, 26.4, 6289, 5071),
    ("2026-10-12", "pw", 73.5, 61.9, 22.4, 1943, 4496),
    ("2026-10-12", "7-iron", 83.2, 66.8, 22.1, 4584, 4068),
    ("2026-10-12", "7-iron", 81.0, 66.7, 20.1, 4021, 3960),
]


def _held_out(tier_name: str, row) -> float:
    session, club, ball, club_speed, launch, _spin, _pinned = row
    tier = SPIN_REFIT.tiers[tier_name]
    fold = next(f for f in tier.cv["folds"] if f["held_out"] == session)
    speed = club_speed if tier.speed == "club_speed" else ball
    angle = launch if tier.angle == "launch" else get_club_physics(ClubType(club)).nominal_loft_deg
    return fold["coefficient"] * speed * math.sin(math.radians(angle)) ** fold["exponent"]


def test_every_session_has_a_fold_that_held_it_out():
    sessions = {row[0] for row in ROWS}
    for tier in SPIN_REFIT.tiers.values():
        assert {f["held_out"] for f in tier.cv["folds"]} == sessions


@pytest.mark.parametrize("row", ROWS, ids=lambda r: f"{r[0]}-{r[1]}-{r[5]}")
def test_the_club_tier_s_held_out_prediction_is_pinned(row):
    assert _held_out(TIER_CLUB, row) == pytest.approx(row[6], abs=1.0)


@pytest.mark.parametrize("tier_name", [TIER_CLUB_SPEED, TIER_CLUB, TIER_LAUNCH])
def test_the_stated_68_percent_range_covers_about_two_thirds_of_held_out_rows(tier_name):
    tier = SPIN_REFIT.tiers[tier_name]
    inside = 0
    for row in ROWS:
        family = club_category(ClubType(row[1])) if tier_name != TIER_LAUNCH else None
        low, high = tier.factors(family)
        prediction = _held_out(tier_name, row)
        inside += low * prediction <= row[5] <= high * prediction

    assert 0.5 <= inside / len(ROWS) <= 0.9


@pytest.mark.parametrize("tier_name", [TIER_CLUB_SPEED, TIER_CLUB])
def test_over_all_shared_held_out_shots_the_club_tiers_beat_the_first_fit(tier_name):
    shared = SPIN_REFIT.tiers[tier_name].cv["comparators_same_shots"]["all"]
    refit = shared[f"{tier_name} tier (held out by session)"]
    first = shared["first fit 170 v sin(LA)^1.2"]

    assert refit["n"] == first["n"] >= 100
    assert refit["mae_rpm"] < first["mae_rpm"]
    assert refit["median_abs_pct"] < first["median_abs_pct"]


def test_the_held_out_error_is_stated_per_club_family():
    cv = SPIN_REFIT.tiers[TIER_CLUB].cv
    for family in ("wedge", "iron", "wood"):
        assert cv[family]["n"] > 0
        assert cv[family]["mae_rpm"] > 0
