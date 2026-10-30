"""Known I/Q velocities grade trajectory identity and retained club-only motion."""

import json
from pathlib import Path

import numpy as np
import pytest

from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.types import IQCapture


def swing_capture(ball_speed=None, club_speed=78, seed=47):
    processor = RollingBufferProcessor()
    times = np.arange(4096) / processor.SAMPLE_RATE
    speeds = np.interp(times, [0, 0.020, 0.050, 0.060, 0.090], [0, 20, club_speed, club_speed, 20])
    frequencies = 2 * speeds / processor.MPS_TO_MPH / processor.WAVELENGTH_M
    phase = 2 * np.pi * np.cumsum(frequencies) / processor.SAMPLE_RATE
    gate = (times >= 0.020) & (times < (0.060 if ball_speed is not None else 0.100))
    signal = 500 * gate * np.exp(1j * phase)
    if ball_speed is not None:
        frequency = 2 * ball_speed / processor.MPS_TO_MPH / processor.WAVELENGTH_M
        signal += 400 * (times >= 0.066) * np.exp(2j * np.pi * frequency * times)
    noise = np.random.default_rng(seed).normal(0, 8, (2, len(times)))
    return IQCapture(
        sample_time=0,
        trigger_time=0.068,
        i_samples=np.round(2048 + signal.real + noise[0]).astype(int).tolist(),
        q_samples=np.round(2048 + signal.imag + noise[1]).astype(int).tolist(),
    )


@pytest.mark.parametrize("ball,club", [(67, 78), (110, 75), (50, 80)])
def test_club_and_ball_are_resolved_without_speed_ratio_assumptions(ball, club):
    processor = RollingBufferProcessor()
    capture = swing_capture(ball, club)
    motion = processor.analyze_motion(capture)
    assert motion.ball is not None
    assert motion.club is not None
    assert motion.ball.speed_mph == pytest.approx(ball, abs=1)
    assert motion.club.speed_mph == pytest.approx(club, abs=2)
    result = processor.process_capture(capture)
    assert result is not None
    assert result.ball_speed_mph == pytest.approx(ball, abs=1)
    assert result.club_speed_mph == pytest.approx(club, abs=2)
    assert result.ball_timestamp_ms >= 60


def test_dry_swing_retains_club_speed_for_training_but_is_not_a_normal_shot():
    processor = RollingBufferProcessor()
    capture = swing_capture()
    motion = processor.analyze_motion(capture)
    assert motion.ball is None
    assert motion.club is not None
    assert motion.club.speed_mph == pytest.approx(78, abs=2)
    assert processor.process_capture(capture) is None


def test_known_club_peak_is_not_raised_to_a_hann_sidelobe():
    motion = RollingBufferProcessor().analyze_motion(swing_capture(club_speed=78))
    assert motion.club is not None
    assert 77 <= motion.club.speed_mph <= 79


def test_processing_keeps_raw_iq_and_explicit_track_support():
    capture = swing_capture(67)
    before = capture.i_samples.copy(), capture.q_samples.copy()
    result = RollingBufferProcessor().process_capture(capture)
    assert result is not None
    assert result.motion is not None
    assert result.motion.ball.peaks
    assert result.motion.club.peaks
    assert (capture.i_samples, capture.q_samples) == before


def test_club_projection_keeps_actual_window_width_and_observed_peak():
    from openflight.ops_speed_track import ops_speed_track

    capture = swing_capture(67)
    capture.trigger_type = "sound"
    result = RollingBufferProcessor().process_capture(capture)
    assert result is not None
    track = ops_speed_track(result)
    assert track is not None
    reading = track["club_reading"]
    assert reading["window_ms"] == pytest.approx(256 / 30)
    observation = result.motion.club.peaks[result.motion.club.reference_index]
    assert reading["window_start_ms"] + reading["window_ms"] / 2 == pytest.approx(
        observation.time_ms
    )
    assert reading["radial_mph"] == observation.speed_mph


def test_brief_fast_transient_does_not_replace_the_club_or_ball():
    capture = swing_capture(67)
    processor = RollingBufferProcessor()
    times = np.arange(4096) / processor.SAMPLE_RATE
    frequency = 2 * 140 / processor.MPS_TO_MPH / processor.WAVELENGTH_M
    burst = 400 * ((times >= 0.016) & (times < 0.022)) * np.exp(2j * np.pi * frequency * times)
    capture.i_samples = np.round(np.array(capture.i_samples) + burst.real).astype(int).tolist()
    capture.q_samples = np.round(np.array(capture.q_samples) + burst.imag).astype(int).tolist()
    motion = processor.analyze_motion(capture)
    assert motion.club is not None and motion.ball is not None
    assert motion.club.speed_mph == pytest.approx(78, abs=2)
    assert motion.ball.speed_mph == pytest.approx(67, abs=1)


def long_plateau_capture(ball_speed=None):
    processor = RollingBufferProcessor()
    times = np.arange(4096) / processor.SAMPLE_RATE
    speed = np.interp(times, [0, 0.015, 0.030, 0.070, 0.095], [0, 15, 78, 78, 15])
    phase = 2 * np.pi * np.cumsum(2 * speed / processor.MPS_TO_MPH / processor.WAVELENGTH_M)
    phase /= processor.SAMPLE_RATE
    signal = 500 * ((times >= 0.015) & (times < 0.100)) * np.exp(1j * phase)
    if ball_speed is not None:
        frequency = 2 * ball_speed / processor.MPS_TO_MPH / processor.WAVELENGTH_M
        signal += 350 * (times >= 0.080) * np.exp(2j * np.pi * frequency * times)
    noise = np.random.default_rng(61).normal(0, 8, (2, len(times)))
    return IQCapture(
        sample_time=0,
        trigger_time=0.068,
        i_samples=np.round(2048 + signal.real + noise[0]).astype(int).tolist(),
        q_samples=np.round(2048 + signal.imag + noise[1]).astype(int).tolist(),
    )


def test_long_club_plateau_followed_by_deceleration_is_not_ball_flight():
    processor = RollingBufferProcessor()
    motion = processor.analyze_motion(long_plateau_capture())
    assert motion.ball is None
    assert motion.club is not None
    assert motion.club.speed_mph == pytest.approx(78, abs=2)
    assert motion.flight_rejections
    assert all(rejection.continuation for rejection in motion.flight_rejections)


def test_later_slower_ball_is_not_replaced_by_long_earlier_club_plateau():
    motion = RollingBufferProcessor().analyze_motion(long_plateau_capture(ball_speed=50))
    assert motion.ball is not None and motion.club is not None
    assert motion.ball.speed_mph == pytest.approx(50, abs=1)
    assert motion.club.speed_mph == pytest.approx(78, abs=2)


@pytest.mark.parametrize("ball_speed", [22, 67])
def test_genuine_flight_can_disappear_before_buffer_end(ball_speed):
    capture = swing_capture(ball_speed)
    times = np.arange(4096) / 30000
    frequency = 2 * ball_speed / 2.23694 / 0.01243
    removed = 400 * (times >= 0.112) * np.exp(2j * np.pi * frequency * times)
    capture.i_samples = np.round(np.array(capture.i_samples) - removed.real).astype(int).tolist()
    capture.q_samples = np.round(np.array(capture.q_samples) - removed.imag).astype(int).tolist()
    motion = RollingBufferProcessor().analyze_motion(capture)
    assert motion.ball is not None
    assert motion.ball.speed_mph == pytest.approx(ball_speed, abs=1)


def test_ball_flight_is_retained_when_its_late_return_changes_speed():
    processor = RollingBufferProcessor()
    capture = swing_capture(67)
    times = np.arange(4096) / processor.SAMPLE_RATE
    factor = 2 / processor.MPS_TO_MPH / processor.WAVELENGTH_M
    phase = 2 * np.pi * factor * (67 * times - 0.5 * 1800 * np.maximum(times - 0.112, 0) ** 2)
    old_ball = 400 * np.exp(2j * np.pi * factor * 67 * times)
    changed_ball = 400 * np.exp(1j * phase)
    difference = (changed_ball - old_ball) * (times >= 0.112)
    capture.i_samples = np.round(np.array(capture.i_samples) + difference.real).astype(int).tolist()
    capture.q_samples = np.round(np.array(capture.q_samples) + difference.imag).astype(int).tolist()
    motion = processor.analyze_motion(capture)
    assert motion.ball is not None
    assert motion.ball.speed_mph == pytest.approx(67, abs=1)
    assert motion.ball.peaks[0].time_ms <= 75


@pytest.mark.parametrize("shot", [4, 28, 33])
def test_late_tail_fit_keeps_the_earlier_supported_flight_interval(shot):
    fixture = Path(__file__).parent / "fixtures/ops_club_pick/outdoor-test-1-arm5.npz"
    with np.load(fixture, allow_pickle=False) as archive:
        index = int(np.flatnonzero(archive["shot"] == shot)[0])
        capture = IQCapture(
            sample_time=float(archive["sample_time"][index]),
            trigger_time=float(archive["trigger_time"][index]),
            i_samples=archive["i_samples"][index].tolist(),
            q_samples=archive["q_samples"][index].tolist(),
        )
    motion = RollingBufferProcessor().analyze_motion(capture)
    assert motion.ball is not None
    candidate_start = min(
        candidate.peaks[0].time_ms
        for candidate in motion.alternatives
        if abs(candidate.speed_mph - motion.ball.speed_mph) <= motion.resolution_mph
    )
    assert motion.ball.peaks[0].time_ms <= candidate_start + motion.window_samples / 30


def test_a_line_born_after_ball_onset_is_not_the_approaching_club():
    """May 2026 range session shot 3: TrackMan club 86.2 mph; a rising return near
    122 mph that begins after the 114.8 mph ball line was read as the club."""
    session = Path(__file__).parents[1] / "session_logs/session_20260506_152406_range.jsonl"
    event = next(
        event
        for event in map(json.loads, session.read_text().splitlines())
        if event["type"] == "rolling_buffer_capture" and event["shot_number"] == 3
    )
    capture = IQCapture(
        sample_time=event["sample_time"],
        trigger_time=event["trigger_time"],
        i_samples=event["i_samples"],
        q_samples=event["q_samples"],
    )
    motion = RollingBufferProcessor().analyze_motion(capture)
    assert motion.ball is not None and motion.club is not None
    assert motion.ball.speed_mph == pytest.approx(114.8, abs=0.5)
    assert motion.club.speed_mph == pytest.approx(86.2, abs=2)
    assert motion.club.peaks[0].time_ms < motion.ball.peaks[0].time_ms
