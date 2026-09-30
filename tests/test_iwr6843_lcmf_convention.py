"""The per-antenna LCMF dictionary against the processed data's phase convention.

Written before the exact-path dictionary replaced LCMF-v1's single-height
one. The calibrated channels are known to follow LCMF-v1's dictionary,
exp(+j*pi*k*sin(angle)) on canonical slot k. Collapse the LEVM onto one line
at lambda/2 spacing, put the ball in the far field, and the exact paths must
reproduce that dictionary up to one complex constant per column; turned the
other way up, they must reproduce its mirror image instead.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.iwr6843 import Calibration, antennas, doa, lcmf
from openflight.iwr6843.multipath import mimo_four_path_dictionary
from openflight.iwr6843.music import LAM

TILT_DEG = 10.0
HALF_LAMBDA_MM = 0.5 * LAM * 1000.0


def _collapsed_patches() -> dict[str, tuple[float, float]]:
    """The LEVM's element order on one ECAD line at exact lambda/2 spacing.

    RX1-RX4 at indices 0..3 and TX1/TX3 at 0 and 4 along ECAD +X, as LCMF-v1
    assumed, both rows centred on one point so they share one height.
    """
    centre_x, row_y = 30.0, 45.0
    patches = {
        f"RX{index + 1}": (centre_x + (index - 1.5) * HALF_LAMBDA_MM, row_y) for index in range(4)
    }
    patches["TX1"] = (centre_x - 2.0 * HALF_LAMBDA_MM, row_y)
    patches["TX2"] = (centre_x, row_y)
    patches["TX3"] = (centre_x + 2.0 * HALF_LAMBDA_MM, row_y)
    return patches


def _collapsed_layout(rotation_deg: float, height_m: float) -> antennas.AntennaLayout:
    return antennas.AntennaLayout(
        board_rotation_deg=rotation_deg,
        rx_row_height_m=height_m,
        basis="test_collapsed",
        patches_mm=_collapsed_patches(),
    )


def _same_up_to_column_constants(exact: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Per-column phase residual after removing each column's own constant."""
    ratio = exact / reference
    return np.angle(ratio / ratio[:1, :])


def _far_field_steering(rotation_deg: float, tx_order: str = "normal"):
    height_m = 1.0e4  # far field AND a distinct floor-image angle
    ball = np.array([[1.0e5, 0.0, 1.5e4]])
    layout = _collapsed_layout(rotation_deg, height_m)
    tilt = math.radians(TILT_DEG)
    tx, rx = antennas.channel_positions_m(layout.positions_m(tilt), tx_order)
    exact = antennas.steering(antennas.channel_paths_m(tx, rx, ball))[0]
    centre = layout.phase_centre_height_m(tilt)
    direct = math.atan2(ball[0, 2] - centre, ball[0, 0]) - tilt
    image = math.atan2(-(ball[0, 2] + centre), ball[0, 0]) - tilt
    return exact, direct, image


@pytest.mark.parametrize("tx_order", ["normal", "reversed"])
def test_far_field_collapse_reproduces_the_lcmf_v1_dictionary(tx_order):
    exact, direct, image = _far_field_steering(90.0, tx_order)
    assert abs(math.degrees(direct - image)) > 10.0  # the image columns are distinct
    residual = _same_up_to_column_constants(exact, mimo_four_path_dictionary(direct, image))
    np.testing.assert_allclose(residual, 0.0, atol=1e-4)


def test_minus_ninety_reproduces_the_mirror_image():
    exact, direct, image = _far_field_steering(-90.0)
    mirrored = mimo_four_path_dictionary(-direct, -image)
    np.testing.assert_allclose(_same_up_to_column_constants(exact, mirrored), 0.0, atol=1e-4)
    unmirrored = mimo_four_path_dictionary(direct, image)
    assert np.max(np.abs(_same_up_to_column_constants(exact, unmirrored))) > 0.5


def test_the_opposite_phase_sign_would_mirror_a_plus_ninety_board():
    """exp(-j*2*pi*path/lambda) on the +90 deg board gives the MIRRORED dictionary.

    So with the snapshot builder's slot order (slot 0 = TX3/RX4) and the ECAD
    table, only PHASE_SIGN = +1 matches the channels' known convention.
    """
    assert antennas.PHASE_SIGN == 1.0
    exact, direct, image = _far_field_steering(90.0)
    conjugate = np.conj(exact)
    mirrored = mimo_four_path_dictionary(-direct, -image)
    np.testing.assert_allclose(_same_up_to_column_constants(conjugate, mirrored), 0.0, atol=1e-4)


def test_phase_sign_agrees_with_the_positive_tdm_registration():
    """The later chirp sees the receding ball 2*v*tau of path further on.

    Production locks the TDM sign positive (measured on the hardware), which
    removes exp(+j*4*pi*v*tau/lambda) from the later block. That cancels the
    exact steering's advance only if phase grows with path.
    """
    speed_ms, tau_s = 45.0, doa.TDM_TAU_S
    layout = antennas.AntennaLayout(90.0, 0.051, "test")
    tx, rx = antennas.channel_positions_m(layout.positions_m(math.radians(TILT_DEG)), "normal")
    ball = np.array([[2.0, 0.0, 0.3]])
    centre = np.array([0.0, 0.0, layout.phase_centre_height_m(math.radians(TILT_DEG))])
    direction = (ball[0] - centre) / np.linalg.norm(ball[0] - centre)
    later_ball = ball + direction * speed_ms * tau_s
    now = antennas.steering(antennas.channel_paths_m(tx, rx, ball))[0, :, 0]
    later = antennas.steering(antennas.channel_paths_m(tx, rx, later_ball))[0, :, 0]
    # chip order: TX1 (early) block then TX3 (late), RX1..RX4, as recorded
    chip_early = now[4:][::-1]
    chip_late = later[:4][::-1]
    canonical = doa.canonicalize_tx_blocks(
        chip_early,
        chip_late,
        tdm_phase=+1 * 4.0 * np.pi * speed_ms * tau_s / LAM,
        tx_order="normal",
    )
    # the radial path rate differs from 2*v by the aperture's parallax only
    np.testing.assert_allclose(np.angle(canonical / now), 0.0, atol=2e-3)


def _collapsed_cal(height_m: float = 0.152) -> Calibration:
    cal = Calibration.identity()
    cal.tilt_rad = math.radians(TILT_DEG)
    cal.tee_range_m = 1.5
    cal.tee_ball_height_m = 0.0213
    cal.meta["radar_height_m"] = height_m
    cal.antennas = _collapsed_layout(90.0, height_m)
    return cal


def test_lcmf_dictionary_is_exact_paths_along_the_candidate_trajectory():
    cal = Calibration.identity()
    cal.tilt_rad = math.radians(TILT_DEG)
    cal.tee_range_m = 1.5
    cal.tee_ball_height_m = 0.0213
    cal.lateral_tee_offset_m = 0.01
    cal.antennas = antennas.AntennaLayout(90.0, 0.051, "test")
    geometry = lcmf._model_geometry(cal, ball_speed_mph=100.0, tx_order="normal", tdm_tau_s=45e-6)
    ranges = np.linspace(1.6, 3.5, 12)
    launch = math.radians(14.0)
    dictionary = lcmf._spatial_dictionary("four4", launch, ranges, geometry, 45e-6)
    x_m, height_m, _direct_vr, _image_vr = lcmf._candidate_trajectory(launch, ranges, geometry)
    ball = np.stack([x_m, np.full_like(x_m, 0.01), height_m], axis=-1)
    positions = cal.antennas.positions_m(cal.tilt_rad)
    tx, rx = antennas.channel_positions_m(positions, "normal")
    expected = antennas.steering(antennas.channel_paths_m(tx, rx, ball))
    np.testing.assert_allclose(dictionary, expected, atol=1e-12)
    assert lcmf._spatial_dictionary("two8", launch, ranges, geometry, 45e-6).shape == (12, 8, 2)


def test_lcmf_dictionary_reduces_to_lcmf_v1_on_a_collapsed_array():
    """Near field at 1.5-4 m moves a collapsed array's phases by hundredths of a radian."""
    cal = _collapsed_cal()
    geometry = lcmf._model_geometry(cal, ball_speed_mph=100.0, tx_order="normal", tdm_tau_s=45e-6)
    ranges = np.linspace(1.6, 4.0, 20)
    launch = math.radians(18.0)
    exact = lcmf._spatial_dictionary("four4", launch, ranges, geometry, 45e-6)
    x_m, height_m, _dvr, _ivr = lcmf._candidate_trajectory(launch, ranges, geometry)
    centre = geometry["phase_centre_height_m"]
    for index in range(len(ranges)):
        direct = math.atan2(height_m[index] - centre, x_m[index]) - cal.tilt_rad
        image = math.atan2(-(height_m[index] + centre), x_m[index]) - cal.tilt_rad
        residual = _same_up_to_column_constants(
            exact[index], mimo_four_path_dictionary(direct, image)
        )
        assert np.max(np.abs(residual)) < 0.03


def test_four_path_tdm_keeps_the_cross_phase_on_the_later_block():
    cal = _collapsed_cal()
    for tx_order in ("normal", "reversed"):
        geometry = lcmf._model_geometry(
            cal, ball_speed_mph=100.0, tx_order=tx_order, tdm_tau_s=90e-6
        )
        ranges = np.linspace(1.6, 3.0, 5)
        launch = math.radians(20.0)
        plain = lcmf._spatial_dictionary("four4", launch, ranges, geometry, 90e-6)
        tdm = lcmf._spatial_dictionary("four4_path_tdm", launch, ranges, geometry, 90e-6)
        _x, _h, direct_vr, image_vr = lcmf._candidate_trajectory(launch, ranges, geometry)
        cross = 2.0 * np.pi * (image_vr - direct_vr) * 90e-6 / LAM
        later = slice(
            4 * doa.later_physical_tx_index(tx_order), 4 * doa.later_physical_tx_index(tx_order) + 4
        )
        earlier = slice(4 - later.start, 8 - later.start)
        np.testing.assert_allclose(tdm[:, earlier], plain[:, earlier])
        np.testing.assert_allclose(tdm[:, later, 0], plain[:, later, 0])
        for column, factor in ((1, 1), (2, 1), (3, 2)):
            np.testing.assert_allclose(
                tdm[:, later, column],
                plain[:, later, column] * np.exp(1j * factor * cross)[:, None],
            )


def test_without_a_layout_the_radar_height_is_the_rx_row_at_plus_ninety():
    cal = Calibration.identity()
    cal.tilt_rad = math.radians(TILT_DEG)
    cal.tee_range_m = 1.5
    cal.tee_ball_height_m = 0.0213
    cal.meta["radar_height_m"] = 0.152
    geometry = lcmf._model_geometry(cal, ball_speed_mph=100.0, tx_order="normal", tdm_tau_s=45e-6)
    legacy = antennas.legacy_layout(0.152)
    assert geometry["antenna_layout"] == legacy
    assert geometry["phase_centre_height_m"] == pytest.approx(
        legacy.phase_centre_height_m(cal.tilt_rad)
    )
    tx, rx = antennas.channel_positions_m(legacy.positions_m(cal.tilt_rad), "normal")
    np.testing.assert_allclose(geometry["tx_positions_m"], tx)
    np.testing.assert_allclose(geometry["rx_positions_m"], rx)


def test_estimator_is_versioned_off_the_frozen_v1():
    assert lcmf.NAME == "lcmf_v2_per_antenna"
    assert lcmf.LCMFResult(status="accepted").to_dict()["estimator"] == lcmf.NAME
