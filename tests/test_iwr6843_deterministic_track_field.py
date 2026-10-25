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


def test_shot_14_keeps_its_range_and_its_ops_guided_launch(runs):
    shot = runs["shot14"]

    assert shot["range_m"] == pytest.approx(1.91, abs=0.02)
    assert shot["range_label"] == "measured"
    # the IWR-only baseline walk is ambiguous, so the OPS speed picks the launch's walk
    assert shot["launch_status"].startswith("accepted_ops_guided")
    assert shot["launch_deg"] == pytest.approx(13.6, abs=0.3)


@pytest.mark.parametrize(
    ("key", "range_m", "launch_deg"),
    [("ot1_arm6_002", 1.779, 36.03), ("ot5_003", 1.538, None), ("ot7_001", 1.837, None)],
)
def test_the_seed_stable_dumps_keep_their_answers(runs, key, range_m, launch_deg):
    shot = runs[key]

    assert shot["range_m"] == pytest.approx(range_m, abs=0.005)
    assert shot["range_label"] == "measured"
    if launch_deg is not None:
        assert shot["launch_deg"] == pytest.approx(launch_deg, abs=0.1)
    # a competing walk, where there is one, either launches from the same place or has
    # no launch point of its own
    if shot["competing"] is not None:
        assert shot["competing"]["verdict"] in ("agree", "runner_up_unusable")


def test_two_walks_with_different_launch_points_leave_ot7_003_s_range_estimated(runs):
    shot = runs["ot7_003"]
    competing = shot["competing"]

    assert shot["range_label"] == "estimated"
    assert competing["verdict"] == "ambiguous"
    assert competing["reason"] == "two IWR tracks compete"
    assert competing["selected"]["launch_range_m"] == pytest.approx(1.849, abs=0.005)
    assert competing["runner_up"]["launch_range_m"] == pytest.approx(0.93, abs=0.05)
    assert competing["camera_prior"] is None  # the session saved no per-shot camera prior


@pytest.mark.parametrize(
    ("key", "range_m", "competing"),
    [
        # the walk along the visible streak, after the line mixing it with the clutter
        # band (36 -> 16 m/s between its halves) is refused; no competitor within 2 sigma
        ("ot1_001", 1.775, None),
        # the competitor is a 2-3 frame late segment with no launch point of its own
        ("ot5_001", 1.510, "runner_up_unusable"),
        # the burst walk is too thin; in the window scope both walks start at ~1.4 m
        ("ot5_005", 1.363, "agree"),
    ],
)
def test_the_seed_dependent_dumps_have_one_answer(runs, key, range_m, competing):
    shot = runs[key]

    assert shot["range_m"] == pytest.approx(range_m, abs=0.005)
    assert shot["range_label"] == "measured"
    verdict = shot["competing"]["verdict"] if shot["competing"] else None
    assert verdict == competing
