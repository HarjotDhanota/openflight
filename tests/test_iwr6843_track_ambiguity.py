"""What an ambiguous IWR ball track does to the shot's range and launch angle.

When two different walks compete (``tracking.search_range_walks``), the launch-point
range works out both walks' launch points. Two that agree within their uncertainty are
one answer. Two that do not leave the range labelled ``estimated`` ("two IWR tracks
compete"), with both candidates and the camera prior's cross-check on the record. The
launch angle's baseline track, when ambiguous, sends the shot through the OPS-guided
alternate-track pass, where the OPS speed decides.
"""

from __future__ import annotations

import math
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from openflight.iwr6843 import launch_range as lr
from openflight.iwr6843.lcmf import LCMFResult
from openflight.iwr6843.recovery import RecoveryCandidate
from openflight.iwr6843.runtime import IWR6843Runtime
from openflight.iwr6843.shot import PreparedShotDump
from openflight.iwr6843.tracking import MPH_PER_MS, BallTrack, Geometry, TrackAmbiguity

N_FRAMES = 24
N_LOOPS = 8
N_RX = 4
N_BINS = 128
FRAME_PERIOD_S = 0.003
LOOP_PERIOD_S = 135e-6
RES_M = 6.0 / N_BINS
BALL_MS = 45.0


def _geometry() -> Geometry:
    return Geometry(
        n_frames=N_FRAMES,
        chirps_per_frame=N_LOOPS * 2,
        n_tx=2,
        n_rx=N_RX,
        n_samples=N_BINS,
        frame_period_s=FRAME_PERIOD_S,
        trigger_frame=0,
        loop_period_s=LOOP_PERIOD_S,
        range_fft_size=N_BINS,
    )


def _add_target(cube, frame, loop, range_m, amplitude, rng):
    centre = range_m / RES_M
    profile = amplitude * np.exp(-0.5 * ((np.arange(N_BINS) - centre) / 0.45) ** 2)
    phase = np.exp(1j * rng.uniform(0.0, 2.0 * math.pi))
    for tx in range(2):
        cube[frame, loop * 2 + tx, :, :] += profile[None, :] * phase


def _two_walks(launches_m=(1.60, 2.60), impact_s=0.020, seed=4):
    """Two receding walks at the ball's speed from different launch points, taking turns
    as each loop's strongest echo: the IWR cannot tell which is the ball."""
    rng = np.random.default_rng(seed)
    geo = _geometry()
    cube = (rng.normal(0.0, 1.0, (N_FRAMES, N_LOOPS * 2, N_RX, N_BINS)) + 0j).astype(complex)
    cube += 1j * rng.normal(0.0, 1.0, cube.shape)
    for frame in range(N_FRAMES):
        for loop in range(N_LOOPS):
            t = geo.loop_time(frame, loop)
            if t <= impact_s:
                continue
            if t - impact_s > 0.036:  # both walks in view for the same time
                continue
            launch = launches_m[(frame + loop) % 2]
            _add_target(cube, frame, loop, launch + BALL_MS * (t - impact_s), 40.0, rng)
    return PreparedShotDump(metadata={}, cube=cube, geometry=geo, range_domain=True)


def _estimate(prepared, prior=None):
    return lr.estimate_launch_range(
        prepared,
        ball_speed_mph=BALL_MS * MPH_PER_MS,
        club_speed_mph=None,
        prior=prior,
        net_range_m=4.6,
    )


def test_two_walks_with_different_launch_points_leave_the_range_estimated():
    estimate = _estimate(_two_walks())

    assert estimate.status == "measured", estimate.reason
    assert estimate.label == "estimated"
    competing = estimate.competing
    assert competing["reason"] == "two IWR tracks compete"
    assert competing["verdict"] == "ambiguous"
    launch_points = sorted(
        (competing["selected"]["launch_range_m"], competing["runner_up"]["launch_range_m"])
    )
    assert launch_points == pytest.approx([1.60, 2.60], abs=0.12)
    assert estimate.range_m == pytest.approx(competing["selected"]["launch_range_m"])
    assert competing["camera_prior"] is None
    assert estimate.to_dict()["competing"] == competing

    chosen = lr.shot_ball_range(estimate, None)
    assert chosen.source == "iwr_post_impact"
    assert chosen.label == "estimated"
    assert "two IWR tracks compete" in chosen.reason


def test_the_camera_prior_picks_the_walk_it_agrees_with_but_the_label_stays_estimated():
    prior = lr.LaunchRangePrior(range_m=1.70, uncertainty_m=0.15, source="camera_size")

    estimate = _estimate(_two_walks(), prior)

    assert estimate.status == "measured", estimate.reason
    assert estimate.range_m == pytest.approx(1.60, abs=0.12)
    assert estimate.label == "estimated"
    competing = estimate.competing
    assert competing["verdict"] == "ambiguous"
    assert competing["resolved_by"] == "camera_prior"
    assert competing["camera_prior"]["agrees_with"] == ["selected"]
    chosen = lr.shot_ball_range(estimate, prior)
    assert chosen.label == "estimated"
    assert "camera prior" in chosen.reason


def test_a_tight_camera_prior_keeps_the_far_walk_out_of_the_search():
    prior = lr.LaunchRangePrior(range_m=2.60, uncertainty_m=0.10, source="camera_size")

    estimate = _estimate(_two_walks(), prior)

    assert estimate.status == "measured", estimate.reason
    assert estimate.range_m == pytest.approx(2.60, abs=0.12)
    assert estimate.competing is None
    assert estimate.label == "measured"


def test_competing_walks_that_launch_from_the_same_place_are_one_answer():
    estimate = _estimate(_two_walks(launches_m=(1.60, 1.60)))

    assert estimate.status == "measured", estimate.reason
    assert estimate.label == "measured"
    assert estimate.competing is None or estimate.competing["verdict"] == "agree"


def test_a_partial_dump_s_ambiguous_range_is_still_estimated():
    estimate = _estimate(_two_walks())
    partial = replace(estimate, partial={"missing": "frame 3"})

    assert partial.label == "estimated"


def _track(speed_ms=40.0, ambiguity=None):
    return BallTrack(
        speed_ms=speed_ms,
        slope_bins=speed_ms / RES_M,
        intercept_bins=30.0,
        rms_bins=0.3,
        n_inliers=30,
        t_first=0.01,
        t_last=0.04,
        low_confidence=False,
        ambiguity=ambiguity,
    )


def _ambiguity():
    return TrackAmbiguity(
        reason="two IWR tracks compete",
        support_z=0.4,
        separation_bins=12.0,
        selected={"speed_ms": 40.0},
        runner_up={"speed_ms": 44.0},
        runner_up_track=_track(44.0),
    )


def test_the_lcmf_record_carries_the_baseline_track_s_ambiguity():
    from openflight.iwr6843.calibration import Calibration  # noqa: PLC0415
    from openflight.iwr6843.lcmf import _result_from_track  # noqa: PLC0415

    cal = Calibration.identity()
    cal.tee_range_m = 1.5
    shot = SimpleNamespace(
        track=_track(ambiguity=_ambiguity()),
        geometry=_geometry(),
        quality="good",
        tdm_sign_used=1,
    )

    result = _result_from_track("accepted", shot, cal=cal)

    assert result.track_ambiguity["reason"] == "two IWR tracks compete"
    assert result.to_dict()["track_ambiguity"]["runner_up"] == {"speed_ms": 44.0}


@pytest.fixture(name="runtime")
def fixture_runtime():
    class Capture:
        valid = True
        raw = b"x" * 32
        path = None
        error = None
        trigger_timestamp = 1.0
        dump_duration_s = 5.3
        sequence = 1

    class Monitor:
        def capture_for_shot(self, _ts, timeout_s):
            del timeout_s
            return Capture()

        def stop(self):
            pass

    with patch(
        "openflight.iwr6843.runtime.prepare_lcmf_capture",
        return_value=SimpleNamespace(vertical=object()),
    ):
        yield IWR6843Runtime(capture_monitor=Monitor(), calibration=object(), net_range_m=4.0)


def _candidate():
    return RecoveryCandidate(
        track=_track(100.0 / MPH_PER_MS),
        scope="burst",
        impact_s=0.012,
        speed_ratio=1.0,
    )


def test_an_ambiguous_baseline_track_goes_through_the_ops_guided_pass(runtime):
    baseline = LCMFResult(
        status="accepted",
        angle_deg=15.6,
        track_speed_mph=101.0,
        track_ambiguity=_ambiguity().to_dict(),
    )
    recovered = LCMFResult(status="accepted", angle_deg=13.6, track_speed_mph=100.0, n_frames=6)

    with (
        patch("openflight.iwr6843.runtime.estimate_lcmf_v1", side_effect=[baseline, recovered]),
        patch(
            "openflight.iwr6843.runtime.find_recovery_candidates", return_value=[_candidate()]
        ) as find_candidates,
    ):
        result = runtime.process_shot(impact_timestamp=1.0, ball_speed_mph=100.0, club="7i")

    find_candidates.assert_called_once()
    assert result.measurement.angle_deg == pytest.approx(13.6)
    assert result.measurement.status == "accepted_ops_guided"


def test_an_ambiguous_baseline_without_a_replacement_says_so(runtime):
    baseline = LCMFResult(
        status="accepted",
        angle_deg=15.6,
        track_speed_mph=101.0,
        track_ambiguity=_ambiguity().to_dict(),
    )

    with (
        patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=baseline),
        patch("openflight.iwr6843.runtime.find_recovery_candidates", return_value=[]),
    ):
        result = runtime.process_shot(impact_timestamp=1.0, ball_speed_mph=100.0, club="7i")

    assert result.measurement.angle_deg == pytest.approx(15.6)
    assert result.measurement.status == "accepted_track_ambiguous"
    assert result.measurement.track_ambiguity["reason"] == "two IWR tracks compete"
