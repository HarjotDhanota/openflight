"""The deterministic IWR ball tracker on every complete field dump (fork only: field data).

The seeded RANSAC it replaced moved these answers between seeds 1-6 (25 Oct 2026):
shot 14 launch 12.4-15.9 deg; Outdoors-test-1 ..._001 launch range 1.51 / 2.18 m;
Outdoors-test-5 ..._001 1.45-1.48 / 2.15 m and ..._005 1.36 / 2.30 m; Outdoors-test-7
..._003 1.84 / 1.10 m. The search now has one answer per dump. The values pinned here
are today's, not truth: only shot 14's range has an outside check (the owner's ~1.91 m;
the camera's resting-ball size read 2.07 m at the lens's listed focal length, +-8 %).

The dumps stay in the pi-handoff folder beside the checkout (or ``OPENFLIGHT_PI_HANDOFF``);
without them these tests skip.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.iwr6843 import deterministic_track_field as field, partial_dump_simulation as sim

REPO = Path(__file__).resolve().parents[1]
HANDOFF = Path(os.environ.get("OPENFLIGHT_PI_HANDOFF") or REPO.parent / "pi-handoff")
RUNS = {
    "shot14": ("Harjot-outdoor-test-1/arm5", "iwr6843_20261024_172814_013_013.l3dump"),
    "ot1_001": ("Outdoors-test-1/arm5", "iwr6843_20260929_112202_754_001.l3dump"),
    "ot1_arm6_002": ("Outdoors-test-1/arm6", "iwr6843_20260929_112533_338_002.l3dump"),
    "ot5_001": ("Outdoors-test-5/arm5", "iwr6843_20260929_185706_847_001.l3dump"),
    "ot5_003": ("Outdoors-test-5/arm5", "iwr6843_20260929_185829_456_003.l3dump"),
    "ot5_005": ("Outdoors-test-5/arm5", "iwr6843_20260929_185955_708_005.l3dump"),
    "ot7_001": ("Outdoors-test-7/arm5", "iwr6843_20260930_101810_278_001.l3dump"),
    "ot7_003": ("Outdoors-test-7/arm5", "iwr6843_20260930_101902_777_003.l3dump"),
}


def _paths(key):
    arm_dir, dump = RUNS[key]
    run = HANDOFF / arm_dir / "paired" / "run-01"
    return next(run.glob("session_*.jsonl"), None), run / "iwr6843" / dump


pytestmark = pytest.mark.skipif(
    not all(_paths(key)[0] is not None and _paths(key)[1].exists() for key in RUNS),
    reason="the field dumps are not here: set OPENFLIGHT_PI_HANDOFF",
)


def _run(key):
    session, dump = _paths(key)
    shot = sim.shot_contexts(session)[dump.name]
    return field.run_with_inputs(dump.read_bytes(), shot)


@pytest.fixture(scope="module", name="runs")
def fixture_runs():
    return {key: _run(key) for key in RUNS}


ANSWER = ("launch_status", "launch_deg", "range_status", "range_m", "range_label")


def test_every_dump_gives_the_same_answer_twice(runs):
    for key, first in runs.items():
        second = _run(key)
        assert {k: second[k] for k in ANSWER} == {k: first[k] for k in ANSWER}, key
        assert second["competing"] == first["competing"], key


# At 4e7a9077 (before G4) these prior-less dumps were read back to the club-ball
# intersection or to the walk's own start: shot14 1.91 m (the owner's ~1.91 m; OPS-guided
# angles conflicting), ot1_001 1.775, ot1_arm6_002 1.779 (36.03 deg), ot5_001 1.510,
# ot5_003 1.538 (measured, intersection), ot5_005 1.363, ot7_001 1.837, ot7_003 not
# measured. Neither is a contact time (G4): with no camera prior there is no tee bin to
# read the club walk at, so each walk is evidence and no launch point, and the angle stage
# has no tee range.
WALK_RATES_MS = {
    "shot14": 21.1,
    "ot1_001": 31.0,
    "ot1_arm6_002": 38.7,
    "ot5_001": 32.7,
    "ot5_003": 46.2,
    "ot5_005": 36.3,
    "ot7_001": 40.6,
    "ot7_003": 35.2,
}


@pytest.mark.parametrize("key", sorted(WALK_RATES_MS))
def test_a_prior_less_dump_s_walk_is_evidence_with_no_launch_point(runs, key):
    shot = runs[key]

    assert shot["range_status"] == "not_measured"
    assert shot["range_label"] == "not_measured"
    assert shot["range_m"] is None
    assert shot["range_reason"].startswith(f"ball walk found (rate {WALK_RATES_MS[key]:.1f} m/s")
    assert "but no contact time to read it back to" in shot["range_reason"]
    assert shot["launch_status"] == "tee_range_unresolved"
    assert shot["launch_deg"] is None
