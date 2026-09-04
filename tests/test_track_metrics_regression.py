"""Does the promoted library still reproduce the research pass it came from?

NO GROUND TRUTH EXISTS. Every assertion here is a CONSISTENCY check against the
figures measured on session 20260825_181734 on 2026-08-29, not against a launch
monitor. A pass means "this code still produces what the research produced"; it
says nothing about whether either is the golfer's swing.

The whole file skips when the session export is absent, which is the normal
case: the capture is not in the repository. Point `OPENFLIGHT_SESSION` at an
export to run it.

Two expectations from the research pass are asserted as MISSES rather than
passes, because that is what this promotion measures. They are pinned so the
gap cannot quietly change size without someone noticing; see the module's
`EXPECTED_MISS` comment.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "replay_track_metrics.py"
_SPEC = importlib.util.spec_from_file_location("replay_track_metrics", _SCRIPT)
replay_track_metrics = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = replay_track_metrics
_SPEC.loader.exec_module(replay_track_metrics)

# The research pass used the taped 1.575 m ball-to-unit acoustic path. The
# export's own `tee_slant_range_m` is 1.524 m and shifts the contact frame by
# 0.07 frames, which changes no verdict; 1.575 is pinned here so the numbers
# below are the research's own, to the decimal.
BALL_TO_UNIT_M = 1.575

# Known gaps, measured and NOT tuned away. Re-registered 2026-09-02 after the
# audit's three camera corrections landed together (fixed intrinsics instead
# of a per-shot ball-derived focal; the LCMF range bias applied at the
# evidence boundary; the acoustic sound-flight delay added to the range
# query): the measured dynamic-loft medians moved from 35.7/39.4 to
# 32.20 (7-iron) and 36.56 (9-iron), i.e. the loft now sits 0.6-1.2 deg above
# the research band instead of ~5. The bounds below are the new measured
# values +-2 deg; they exist to catch the residual gap CHANGING, not to bless
# its size, and nothing here was adjusted to make a suite pass.
EXPECTED_MISS = {
    "7-iron": (30.2, 34.2),
    "9-iron": (34.6, 38.6),
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
pytestmark = pytest.mark.skipif(
    SESSION is None,
    reason="no session export: set OPENFLIGHT_SESSION to a capture directory",
)


@pytest.fixture(name="replay", scope="module")
def _replay():
    """One pass over the whole session, shared by every assertion below."""
    records = replay_track_metrics.replay_session(SESSION, ball_to_unit_m=BALL_TO_UNIT_M)
    return records, replay_track_metrics.summarise(records)


def test_the_session_still_reconstructs(replay) -> None:
    """18 of 21 shots produced a track in the research pass."""
    _records, summary = replay
    assert summary["n_reconstructed"] >= replay_track_metrics.EXPECTED["min_reconstructed"]


def test_the_swing_plane_median_is_unchanged(replay) -> None:
    _records, summary = replay
    target, tolerance = replay_track_metrics.EXPECTED["swing_plane_median_deg"]
    assert summary["swing_plane_median_deg"] == pytest.approx(target, abs=tolerance)


def test_the_swing_direction_median_is_unchanged(replay) -> None:
    _records, summary = replay
    target, tolerance = replay_track_metrics.EXPECTED["swing_direction_median_deg"]
    assert summary["swing_direction_median_deg"] == pytest.approx(target, abs=tolerance)


def test_the_track_club_path_is_tight_within_each_club(replay) -> None:
    """One golfer, one club, one session: the path should not scatter."""
    _records, summary = replay
    limit = replay_track_metrics.EXPECTED["track_path_sd_max_deg"]
    for club, stats in summary["per_club"].items():
        assert stats["track_club_path_sd_deg"] is not None, club
        assert stats["track_club_path_sd_deg"] <= limit, club


def test_the_track_attack_angle_corroborates_the_fused_estimator(replay) -> None:
    """Different arithmetic on the same two sensors should land in the same place.

    This is the strongest cross-check available without truth: the fused
    estimator and the 3-D track share the camera and the radar but not the
    method, so agreement is evidence and disagreement is a defect in one of
    them.
    """
    _records, summary = replay
    limit = replay_track_metrics.EXPECTED["track_attack_vs_fused_median_deg"]
    assert summary["track_attack_vs_fused_n"] >= 15
    assert summary["track_attack_vs_fused_median_deg"] == pytest.approx(0.0, abs=limit)


def test_the_boresight_anchor_puts_the_head_on_the_ball_at_contact(replay) -> None:
    """The radar fixes only depth; where the head lands across it is free.

    The research measured a median transverse miss of 20 mm -- half a ball --
    which is the acoustic contact frame, the taped range and the boresight all
    agreeing at once.
    """
    records, _summary = replay
    misses = [
        record["contact_transverse_miss_mm"]
        for record in records
        if record["track_status"] == "ok" and record["contact_transverse_miss_mm"] is not None
    ]
    assert len(misses) >= 15
    assert sorted(misses)[len(misses) // 2] < 60.0


def test_the_dynamic_loft_gap_against_the_research_expectation_has_not_moved(replay) -> None:
    """A pinned MISS: the camera launch angle still overshoots the loft band.

    The research brief expected 29-31 deg for the 7-iron and 34-36 for the
    9-iron. Before the 2026-09-01 audit corrections the reconstruction
    overshot by roughly 5 deg; with fixed intrinsics, the range bias and the
    acoustic delay applied, the medians are 32.20 and 36.56 -- 0.6-1.2 deg
    above the band. The residual is the unresolved camera-vs-LCMF elevation
    datum (physical boresight, uncalibrated). Nothing is tuned to close it;
    this test exists so the gap cannot change size unremarked.
    """
    _records, summary = replay
    for club, (low, high) in EXPECTED_MISS.items():
        loft = summary["per_club"][club]["dynamic_loft_median_deg"]
        assert loft is not None, club
        assert low <= loft <= high, f"{club} dynamic loft moved to {loft:.2f}"
        band = replay_track_metrics.EXPECTED["dynamic_loft_deg"][club]
        assert loft > band[1], f"{club} loft {loft:.2f} now meets the research band {band}"


def test_the_face_angle_is_computed_on_every_reconstructed_shot_and_never_offered(replay) -> None:
    """The uncalibrated offset is a display decision, not a computation failure."""
    records, _summary = replay
    reconstructed = [record for record in records if record["track_status"] == "ok"]
    assert reconstructed
    for record in reconstructed:
        assert record["face_angle_status"] == "withheld_launch_direction_offset"
        assert record["face_angle_computed_deg"] is not None


def test_the_arc_radius_still_reads_below_its_plausible_band(replay) -> None:
    """A pinned known bias: the omega = v/r validator expects 1.4-1.8 m.

    The research measured 1.14 m, one-sided across 17 of 18 shots, and did not
    remove the bias. Anything downstream that consumes this radius inherits it.
    """
    _records, summary = replay
    assert summary["arc_radius_median_m"] == pytest.approx(1.14, abs=0.10)
    assert summary["arc_radius_median_m"] < 1.4
