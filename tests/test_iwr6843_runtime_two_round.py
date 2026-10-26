"""The runtime's two-round path gives the full dump's numbers, behind a flag that is off.

Each capture is served once as a whole dump and once by the simulated radar through the
two-round protocol; every estimator output must match. The flag defaults to ``full``,
and a capture that offers no two-round link is measured from its dump as today.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pytest

from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED, pack_dump, parse_dump
from openflight.iwr6843.runtime import (
    TRANSFER_FULL,
    TRANSFER_TWO_ROUND,
    IWR6843Runtime,
)
from openflight.iwr6843.two_round import TwoRoundClient
from openflight.iwr6843.two_round_sim import SimulatedIWR6843TwoRound, SimulatedSerial
from tests.iwr6843_partial_scene import TEMPERATURE, pack, scene
from tests.test_iwr6843_pipeline import RADAR_HEIGHT_M, synth_shot

WINDOW_STARTS = (20,) * 9 + (32,) * 7 + (47,) * 8
BINS = 53


def _production_dump(raw_adc: bytes) -> bytes:
    """A raw-ADC synthetic shot (trigger at slot 0) as the production wide IQ16 timed dump."""
    meta, cube = parse_dump(raw_adc)
    assert meta["trigger_frame"] == 0
    rfft = np.fft.fft(cube, axis=-1)
    cropped = np.stack([frame[..., s : s + BINS] for frame, s in zip(rfft, WINDOW_STARTS)])
    cropped = np.round(cropped.real) + 1j * np.round(cropped.imag)
    assert np.abs(cropped.real).max() < 32767 and np.abs(cropped.imag).max() < 32767
    n_frames = meta["n_frames"]
    return pack_dump(
        cropped,
        n_tx=3,
        trigger_frame=0,
        version=7,
        frame_period_us=3000,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
        range_bin_starts=list(WINDOW_STARTS),
        range_bin_counts=[BINS] * n_frames,
        frame_time_offsets_us=[3000 * f for f in range(n_frames)],
        temperature_report=TEMPERATURE,
    )


@pytest.fixture(name="ball_dump", scope="module")
def _ball_dump() -> bytes:
    raw = synth_shot(
        n_tx=3,
        n_loops=12,
        n_frames=24,
        frame_period_us=3000,
        trigger_frame=0,
        amp=120.0,
        noise=4.0,
        speed_ms=45.0,
        launch_deg=16.0,
    )
    return _production_dump(raw)


def _calibration(tee_range_m: float | None) -> Calibration:
    cal = Calibration.identity()
    cal.tilt_rad = math.radians(10.4)
    cal.tee_range_m = tee_range_m
    cal.tee_ball_height_m = RADAR_HEIGHT_M
    cal.meta["radar_height_m"] = RADAR_HEIGHT_M
    return cal


@dataclass
class _Capture:
    raw: bytes | None
    sequence: int = 1
    valid: bool = True
    partial_raw: bytes | None = None
    two_round_link: TwoRoundClient | None = None


class _Monitor:
    def __init__(self, capture):
        self.capture = capture

    def capture_for_shot(self, _impact, timeout_s=0.0):  # noqa: ARG002
        return self.capture

    def stop(self):
        return None


def _runtime(capture, transfer: str, tee_range_m: float | None) -> IWR6843Runtime:
    return IWR6843Runtime(
        capture_monitor=_Monitor(capture),
        calibration=_calibration(tee_range_m),
        net_range_m=4.0,
        transfer=transfer,
    )


def _numbers(result) -> dict:
    m, c, lr = result.measurement, result.club_path, result.launch_range
    return {
        "withheld": result.withheld_reason,
        "launch_status": getattr(m, "status", None),
        "launch_deg": getattr(m, "angle_deg", None),
        "horizontal_deg": getattr(m, "horizontal_deg", None),
        "horizontal_status": getattr(m, "horizontal_status", None),
        "impact_t_s": getattr(m, "impact_t_s", None),
        "track_speed_mph": getattr(m, "track_speed_mph", None),
        "range_status": getattr(lr, "status", None),
        "range_m": getattr(lr, "range_m", None),
        "path_status": getattr(c, "status", None),
        "attack_status": getattr(c, "attack_angle_status", None),
        "attack_deg": getattr(c, "candidate_attack_angle_deg", None),
    }


def _assert_same(full: dict, two: dict) -> None:
    assert full.keys() == two.keys()
    for key, value in full.items():
        other = two[key]
        if isinstance(value, float) and isinstance(other, float):
            assert other == pytest.approx(value, rel=1e-6, abs=1e-6), key
        else:
            assert other == value, key


def _shot(runtime):
    return runtime.process_shot(
        impact_timestamp=None, ball_speed_mph=45.0 * 2.2369362920544, club=None, club_speed_mph=30.0
    )


def test_the_flag_is_off_by_default_and_checked():
    runtime = IWR6843Runtime(
        capture_monitor=_Monitor(None), calibration=_calibration(None), net_range_m=4.0
    )
    assert runtime.transfer == TRANSFER_FULL
    with pytest.raises(ValueError, match="transfer"):
        _runtime(None, "half", None)


@pytest.mark.parametrize("tee_range_m", [None, 1.5])
def test_two_round_gives_the_full_dumps_numbers(ball_dump, tee_range_m):
    full = _shot(_runtime(_Capture(ball_dump), TRANSFER_FULL, tee_range_m))
    radar = SimulatedIWR6843TwoRound()
    radar.freeze(ball_dump)
    link = TwoRoundClient(SimulatedSerial(radar), timeout_s=0.05)
    two = _shot(_runtime(_Capture(None, two_round_link=link), TRANSFER_TWO_ROUND, tee_range_m))
    assert full.transfer is None
    assert full.measurement is not None and full.measurement.track_speed_mph
    _assert_same(_numbers(full), _numbers(two))
    record = two.transfer
    assert record["released"] and record["error"] is None and not radar.frozen
    assert record["cell_rounds"] >= 1
    assert record["link_bytes"] < len(ball_dump) / 3


def test_clutter_only_capture_matches_too():
    raw = pack(scene(24, seed=11))
    full = _shot(_runtime(_Capture(raw), TRANSFER_FULL, 1.5))
    radar = SimulatedIWR6843TwoRound()
    radar.freeze(raw)
    link = TwoRoundClient(SimulatedSerial(radar), timeout_s=0.05)
    two = _shot(_runtime(_Capture(None, two_round_link=link), TRANSFER_TWO_ROUND, 1.5))
    _assert_same(_numbers(full), _numbers(two))


def test_a_capture_without_a_link_is_measured_from_its_dump(ball_dump):
    full = _shot(_runtime(_Capture(ball_dump), TRANSFER_FULL, None))
    flagged = _shot(_runtime(_Capture(ball_dump), TRANSFER_TWO_ROUND, None))
    assert flagged.transfer is None
    assert _numbers(flagged) == _numbers(full)


def test_a_failed_transfer_is_reported_not_measured(ball_dump):
    radar = SimulatedIWR6843TwoRound()
    radar.freeze(ball_dump)
    radar.corrupt_next = 2  # the overview and its one retry
    link = TwoRoundClient(SimulatedSerial(radar), timeout_s=0.05)
    result = _shot(_runtime(_Capture(None, two_round_link=link), TRANSFER_TWO_ROUND, None))
    assert result.measurement is None
    assert result.withheld_reason == "iwr_two_round_transfer_failed"
    assert "CRC" in result.transfer["error"]
    # no trusted session id: the host cannot release, the radar's hold timeout rearms it
    assert result.transfer["session_id"] is None and not result.transfer["released"]
    assert radar.frozen
