"""HLCMF-v1, the late-flight bearing: an offline comparator for the IWR's horizontal."""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.iwr6843 import estimate_lcmf_v1, lcmf, process_dump
from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ16, pack_dump, project_tx_pair
from openflight.iwr6843.hlcmf_v1 import tx2_horizontal_proxy
from openflight.iwr6843.lcmf import (
    HORIZONTAL_COHERENCE_MIN,
    TX2_LOOP_PERIOD_S,
    TX2_VERTICAL_TDM_TAU_S,
)
from openflight.iwr6843.shot import ShotMeasurement
from openflight.iwr6843.tracking import BallTrack
from tests.test_iwr6843_pipeline import DIRECTION_OF_MOTION_CASES, _direction_of_motion_shot


@pytest.mark.parametrize(("tee_m", "horizontal_deg", "launch_deg"), DIRECTION_OF_MOTION_CASES)
def test_the_late_flight_bearing_is_biased_where_the_direction_of_motion_is_not(
    tee_m, horizontal_deg, launch_deg
):
    raw, cal = _direction_of_motion_shot(tee_m, horizontal_deg, launch_deg)
    result = estimate_lcmf_v1(raw, cal, ball_speed_mph=45.0 * 2.23694, club="9i")
    shot = process_dump(
        project_tx_pair(raw, (0, 2)),
        cal,
        club="9i",
        loop_period_s=TX2_LOOP_PERIOD_S,
        tdm_tau_s=TX2_VERTICAL_TDM_TAU_S,
        tdm_sign_policy="positive",
    )
    old_deg, _coherence, old_status = tx2_horizontal_proxy(
        raw, shot, tdm_sign=1, phase_velocity_ms=45.0
    )
    assert old_status == "hlcmf_v1_accepted"
    if horizontal_deg:
        assert abs(old_deg) < 0.75 * abs(horizontal_deg)
        assert abs(result.horizontal_deg - horizontal_deg) < 0.25 * abs(old_deg - horizontal_deg)


def test_tx2_horizontal_uses_tail_of_ball_track_not_fixed_ring_tail():
    """Boundary-frozen blocks place impact anywhere, so physical slots 9-11 are not special."""
    n_frames, n_loops, n_tx, n_rx, n_bins = 12, 10, 3, 4, 80
    start_bin = 20
    cube = np.zeros((n_frames, n_loops * n_tx, n_rx, n_bins), dtype=complex)
    track = BallTrack(
        speed_ms=45.0,
        slope_bins=960.0,
        intercept_bins=31.0,
        rms_bins=0.2,
        n_inliers=40,
        t_first=0.012,
        t_last=0.031,
        low_confidence=False,
    )
    shot = ShotMeasurement(geometry=None, ball_found=True, track=track)

    for frame in range(n_frames):
        for loop in range(n_loops):
            time_s = frame * 0.006 + loop * TX2_LOOP_PERIOD_S
            if not track.t_first <= time_s <= track.t_last:
                continue
            local_bin = int(round(track.bin_at(time_s))) - start_bin
            common = np.exp(1j * 0.7 * loop)
            cube[frame, loop * n_tx, :, local_bin] = common
            cube[frame, loop * n_tx + 1, :, local_bin] = common * np.exp(0.25j)
            cube[frame, loop * n_tx + 2, :, local_bin] = common

    raw = pack_dump(
        cube,
        n_tx=3,
        version=3,
        frame_period_us=6000,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16,
        range_bin_start=start_bin,
    )

    angle_deg, coherence, status = tx2_horizontal_proxy(raw, shot, tdm_sign=1)

    assert angle_deg is not None
    assert coherence is not None and coherence > 0.25
    assert status == "hlcmf_v1_accepted"


def test_horizontal_proxy_withholds_incoherent_frames(monkeypatch):
    n_frames, n_loops, n_tx, n_rx, n_bins = 12, 10, 3, 4, 80
    cube = np.zeros((n_frames, n_loops * n_tx, n_rx, n_bins), dtype=complex)
    raw = pack_dump(
        cube,
        n_tx=3,
        version=3,
        frame_period_us=6000,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16,
        range_bin_start=20,
    )
    track = BallTrack(
        speed_ms=45.0,
        slope_bins=0.0,
        intercept_bins=31.0,
        rms_bins=0.2,
        n_inliers=80,
        t_first=0.0,
        t_last=0.067,
        low_confidence=False,
    )
    shot = ShotMeasurement(geometry=None, ball_found=True, track=track)

    def alternating_frame_phase(_mti, frame, *_args, **_kwargs):
        return (0.0 if frame % 2 == 0 else np.pi), 1.0

    monkeypatch.setattr(lcmf.doa, "tx2_phase_at", alternating_frame_phase)

    angle_deg, coherence, status = tx2_horizontal_proxy(raw, shot, tdm_sign=1)

    assert angle_deg is not None and math.isfinite(angle_deg)
    assert coherence is not None and coherence < HORIZONTAL_COHERENCE_MIN
    assert status == "hlcmf_v1_low_coherence"
