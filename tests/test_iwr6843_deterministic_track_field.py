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


def test_shot_14_s_range_and_launch_on_the_ball_s_four_peak_walk(runs):
    """Before G4 C1 (4 Nov): 1.91 m estimated (the owner's ~1.91 m) and the OPS-guided
    angles conflicting (12.06-20.16 deg), withheld. The four-peak ball walk (21.1 m/s
    against OPS 20.2) holds frame after frame from 1.2 ms (pre-request frames 1-8 lie on its
    line), so read back to its start, with no camera prior, the launch point is 1.49 m;
    the LCMF on that tee accepts 40.3 deg. Both are today's values, not truth (g4 report)."""
    shot = runs["shot14"]

    assert shot["range_m"] == pytest.approx(1.493, abs=0.005)
    assert shot["range_label"] == "estimated"
    assert shot["launch_status"] == "accepted_ops_guided_single_channel"
    assert shot["launch_deg"] == pytest.approx(40.31, abs=0.1)
    assert shot["horizontal_deg"] is None


@pytest.mark.parametrize(
    ("key", "range_m", "launch_deg", "label"),
    [
        # before G4 C1 (4 Nov): 1.779 m estimated, 36.03 deg; the four-peak walk meets
        # its club walk and the anchor holds: measured
        ("ot1_arm6_002", 1.841, 37.94, "measured"),
        # before: 1.538 m measured; the four-peak walk competes with a different launch
        # point (ambiguous): estimated
        ("ot5_003", 1.629, None, "estimated"),
        # before: 1.837 m estimated; the four-peak walk meets a club walk, ambiguous
        ("ot7_001", 1.305, None, "estimated"),
    ],
)
def test_the_seed_stable_dumps_keep_their_answers(runs, key, range_m, launch_deg, label):
    shot = runs[key]

    assert shot["range_m"] == pytest.approx(range_m, abs=0.005)
    assert shot["range_label"] == label
    if launch_deg is not None:
        assert shot["launch_deg"] == pytest.approx(launch_deg, abs=0.1)
    # a competing walk, where there is one, has its verdict recorded
    if shot["competing"] is not None:
        assert shot["competing"]["verdict"] in ("agree", "runner_up_unusable", "ambiguous")


def test_ot7_003_s_four_peak_walk_is_read_back_to_its_start(runs):
    """Before C6a' (2 Nov): 1.849 m estimated; before G4 C1 (4 Nov): not measured (the
    34.7 m/s walk reached its 1.10 m start ~9 ms after the club walk). The four-peak walk
    (36.9 m/s against OPS 37.7) is no club-speed walk; read back to its start: 1.07 m,
    estimated (no camera prior, no club walk anchors it)."""
    shot = runs["ot7_003"]

    assert shot["range_status"] == "measured"
    assert shot["range_label"] == "estimated"
    assert shot["range_m"] == pytest.approx(1.065, abs=0.005)


@pytest.mark.parametrize(
    ("key", "range_m", "competing"),
    [
        # before G4 C1 (4 Nov): 1.775 m; the four-peak walk (31.0 m/s, OPS 29.9) starts at
        # the capture's first frame
        ("ot1_001", 1.583, None),
        # before: 1.510 m, runner_up_unusable; the four-peak walk (32.7 m/s, OPS 33.9) is
        # significant on its own
        ("ot5_001", 1.321, None),
        # before: 1.363 m; both walks start at ~1.4 m
        ("ot5_005", 1.370, "agree"),
    ],
)
def test_the_seed_dependent_dumps_have_one_answer(runs, key, range_m, competing):
    shot = runs[key]

    assert shot["range_m"] == pytest.approx(range_m, abs=0.005)
    # no club walk anchors their contact time (C6a', 2 Nov): estimated
    assert shot["range_label"] == "estimated"
    verdict = shot["competing"]["verdict"] if shot["competing"] else None
    assert verdict == competing
