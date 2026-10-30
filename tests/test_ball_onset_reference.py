"""Selected speed-observation time cannot move the unchanged ball's support."""

from dataclasses import asdict, replace

import numpy as np
import pytest

from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.types import IQCapture


def _capture():
    times = np.arange(4096) / 30000
    factor = 2 / 2.23694 / 0.01243
    club = 500 * ((times >= 0.015) & (times < 0.065)) * np.exp(2j * np.pi * factor * 65 * times)
    ball = (
        400
        * (times >= 0.065)
        * (1 + 0.1 * np.cos(2 * np.pi * 100 * times))
        * np.exp(2j * np.pi * factor * 90 * times + 0.7j)
    )
    signal = club + ball
    noise = np.random.default_rng(301030906).normal(0, 4, (2, len(times)))
    return IQCapture(
        0,
        0.068,
        np.round(2048 + signal.real + noise[0]).astype(int).tolist(),
        np.round(2048 + signal.imag + noise[1]).astype(int).tolist(),
        trigger_type="sound",
    )


def _process_at_reference(monkeypatch, capture, motion, index):
    processor = RollingBufferProcessor()
    selected = replace(motion, ball=replace(motion.ball, reference_index=index))
    monkeypatch.setattr(processor, "analyze_motion", lambda _capture: selected)
    inputs = []
    detector = processor.detect_spin_multitaper

    def observe(*args, **kwargs):
        inputs.append(args[2])
        return detector(*args, **kwargs)

    monkeypatch.setattr(processor, "detect_spin_multitaper", observe)
    return processor.process_capture(capture), inputs[0]


@pytest.mark.parametrize("index", [3, 8, 12])
def test_same_path_has_the_same_spin_support_when_speed_reference_changes(monkeypatch, index):
    capture = _capture()
    motion = RollingBufferProcessor().analyze_motion(capture)
    assert motion.ball is not None and len(motion.ball.peaks) > index
    baseline, start = _process_at_reference(monkeypatch, capture, motion, 0)
    moved, moved_start = _process_at_reference(monkeypatch, capture, motion, index)

    assert moved.ball_timestamp_ms > baseline.ball_timestamp_ms
    assert moved.ball_speed_mph == baseline.ball_speed_mph
    assert moved_start == pytest.approx(start)
    assert asdict(moved.spin) == asdict(baseline.spin)


@pytest.mark.parametrize("index", [3, 8, 12])
def test_same_path_has_the_same_impact_evidence_when_speed_reference_changes(monkeypatch, index):
    capture = _capture()
    motion = RollingBufferProcessor().analyze_motion(capture)
    baseline, _ = _process_at_reference(monkeypatch, capture, motion, 0)
    moved, _ = _process_at_reference(monkeypatch, capture, motion, index)

    assert baseline.impact.source == "ops_transition", baseline.impact.reason
    assert moved.ball_timestamp_ms > baseline.ball_timestamp_ms
    assert asdict(moved.impact) == asdict(baseline.impact)
