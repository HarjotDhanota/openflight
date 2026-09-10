"""Does the registration replay still measure what it measured on 2026-09-03?

NO GROUND TRUTH EXISTS. These pin the camera-versus-radar datums and the
ball-clock agreement measured on session 20260825_181734 so they cannot move
without someone noticing. A pass says "the code still finds the same offsets
in the same data"; it says nothing about which sensor holds the error.

The whole file skips when the session export is absent, which is the normal
case: the capture is not in the repository. Point `OPENFLIGHT_SESSION` at an
export to run it.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from openflight.registration import MAX_SPREAD_DEG

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "replay_registration.py"
_SPEC = importlib.util.spec_from_file_location("replay_registration", _SCRIPT)
replay_registration = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = replay_registration
_SPEC.loader.exec_module(replay_registration)

# Measured 2026-09-03 on the CV integration branch, 19 of 21 shots (two 9-iron
# shots find no stable ball path). Tolerances are the standard errors of the
# medians, rounded up; the spreads are asserted to stay under the registration
# gate, not at a value.
EXPECTED = {
    "min_registered_shots": 17,
    # Camera vertical launch minus LCMF vertical: the datum gap the 2026-09-01
    # audit reported as +5.34 +- 0.83 deg on the pre-merge estimator, now
    # +6.18 deg (spread 1.15, se 0.33) through the corrected extrinsics.
    "elevation_offset_deg": (6.18, 0.5),
    # Camera horizontal launch minus IWR horizontal: FIRST measurement of the
    # horizontal datum between the two sensors. The IWR reads every shot ~5 deg
    # left of where the camera reads it, and does so consistently (spread
    # 0.92 deg), which is why face angle stays withheld until a common
    # horizontal reference exists.
    "azimuth_offset_deg": (5.19, 0.5),
    # The ball's own contact instant against the acoustic walk-back, in
    # frames at ~467 fps. RE-REGISTERED 2026-09-10: the earlier pin of 0.0
    # (MAD 0.11) came from intersecting the RADAR-anchored flight line with
    # the tee, which reaches the tee at -delay by construction -- circular.
    # The ball clock is now fitted from the ball's ray directions with the
    # OPS speed as the depth constraint, independent of both the trigger and
    # the radar, and it puts contact a median 1.89 frames (~4 ms) LATER than
    # the sound flight says, jittering 0.80 frames MAD shot to shot. Which
    # clock is right is undecided (a second ball witness disagrees with this
    # one by ~1 frame); this pin holds the MEASUREMENT still, not a claim.
    "ball_clock_median_frames": (1.89, 0.5),
    "ball_clock_mad_max_frames": 1.2,
}


def _session() -> Path | None:
    supplied = os.environ.get("OPENFLIGHT_SESSION")
    candidates = [Path(supplied)] if supplied else []
    candidates.append(
        Path.home()
        / "Downloads"
        / "openflight_session_20260825_181734_filtered"
        / "openflight_session_20260825_181734_filtered"
    )
    for candidate in candidates:
        if (candidate / "shots.csv").is_file() and (candidate / "shots").is_dir():
            return candidate
    return None


SESSION = _session()
pytestmark = pytest.mark.skipif(SESSION is None, reason="session export not available")


@pytest.fixture(name="summary", scope="module")
def _summary():
    """One pass over the whole session, shared by every assertion below."""
    records = replay_registration.replay_session(SESSION)
    return replay_registration.summarise(records)


def test_both_axes_register_on_enough_shots(summary):
    for axis in ("elevation", "azimuth"):
        assert summary[axis]["status"] == "registered", summary[axis]
        assert summary[axis]["n"] >= EXPECTED["min_registered_shots"]
        assert summary[axis]["spread_deg"] < MAX_SPREAD_DEG


def test_the_elevation_datum_has_not_moved(summary):
    target, tolerance = EXPECTED["elevation_offset_deg"]
    assert summary["elevation"]["offset_deg"] == pytest.approx(target, abs=tolerance)


def test_the_azimuth_datum_has_not_moved(summary):
    target, tolerance = EXPECTED["azimuth_offset_deg"]
    assert summary["azimuth"]["offset_deg"] == pytest.approx(target, abs=tolerance)


def test_the_ball_clock_still_agrees_with_the_acoustic_chain(summary):
    clock = summary["ball_clock_vs_acoustic_tee"]
    target, tolerance = EXPECTED["ball_clock_median_frames"]
    assert clock["n"] >= EXPECTED["min_registered_shots"]
    assert clock["median_frames"] == pytest.approx(target, abs=tolerance)
    assert clock["mad_frames"] < EXPECTED["ball_clock_mad_max_frames"]
