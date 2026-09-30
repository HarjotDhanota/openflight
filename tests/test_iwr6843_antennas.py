"""Per-antenna LEVM geometry for the LCMF vertical estimator.

The positions come from TI's ECAD patch centres, turned by the rig's board
rotation and aim. These tests pin the v3 numbers by hand from the ECAD table
so the geometry the estimator and the simulator share has its own check.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.iwr6843 import antennas
from openflight.rig_geometry import (
    LEVM_ANTENNA_PATCHES_MM,
    LEVM_LCMF_TX_PATCHES_MM,
    LEVM_RX_PATCHES_MM,
    RigGeometry,
    levm_antenna_offsets_mm,
    levm_phase_centre_offset_mm,
)

V3_RIG = "config/enclosure_v3_rig_geometry.json"
RX_ROW_HEIGHT_M = 0.051
PITCH_DEG = 10.0


def _v3_layout(rotation_deg: float = 90.0) -> antennas.AntennaLayout:
    return antennas.AntennaLayout(
        board_rotation_deg=rotation_deg,
        rx_row_height_m=RX_ROW_HEIGHT_M,
        basis="test",
    )


def test_patch_table_is_the_lcmf_rows_plus_tx2():
    assert LEVM_ANTENNA_PATCHES_MM["RX1"] == LEVM_RX_PATCHES_MM[0]
    assert LEVM_ANTENNA_PATCHES_MM["RX4"] == LEVM_RX_PATCHES_MM[3]
    assert LEVM_ANTENNA_PATCHES_MM["TX1"] == LEVM_LCMF_TX_PATCHES_MM[0]
    assert LEVM_ANTENNA_PATCHES_MM["TX3"] == LEVM_LCMF_TX_PATCHES_MM[1]
    assert LEVM_ANTENNA_PATCHES_MM["TX2"] == (43.602, 44.553)


def test_v3_positions_match_the_ecad_table_by_hand():
    """+90 deg: ECAD +X is up, ECAD +Y is the viewer's left (target-right)."""
    positions = _v3_layout().positions_m(math.radians(PITCH_DEG))
    rx_x = sum(x for x, _y in LEVM_RX_PATCHES_MM) / 4.0
    rx_y = LEVM_RX_PATCHES_MM[0][1]
    tx_x = sum(x for x, _y in LEVM_LCMF_TX_PATCHES_MM) / 2.0
    tx_y = LEVM_LCMF_TX_PATCHES_MM[0][1]
    pitch = math.radians(PITCH_DEG)
    centre_up = 0.5 * (tx_x - rx_x)  # phase centre above the RX row, board plane
    centre_right_of_rx = 0.5 * (tx_y - rx_y)  # ECAD +Y offset, becomes lateral
    for name, (x_mm, y_mm) in LEVM_ANTENNA_PATCHES_MM.items():
        board_up = x_mm - rx_x
        expected = np.array(
            [
                (-board_up * math.sin(pitch) + centre_up * math.sin(pitch)) / 1000.0,
                ((y_mm - rx_y) - centre_right_of_rx) / 1000.0,
                RX_ROW_HEIGHT_M + board_up * math.cos(pitch) / 1000.0,
            ]
        )
        np.testing.assert_allclose(positions[name], expected, atol=1e-9, err_msg=name)


def test_v3_order_is_rx1_lowest_and_tx1_below_tx3():
    positions = _v3_layout().positions_m(math.radians(PITCH_DEG))
    heights = [positions[f"RX{index}"][2] for index in range(1, 5)]
    assert heights == sorted(heights)
    assert positions["TX1"][2] < positions["TX3"][2]
    # the TX pair's centre sits about 16 mm above the RX row's
    tx_centre = 0.5 * (positions["TX1"][2] + positions["TX3"][2])
    assert tx_centre - float(np.mean(heights)) == pytest.approx(0.0157, abs=0.0002)


def test_minus_ninety_reverses_the_order():
    positions = _v3_layout(-90.0).positions_m(math.radians(PITCH_DEG))
    heights = [positions[f"RX{index}"][2] for index in range(1, 5)]
    assert heights == sorted(heights, reverse=True)
    assert positions["TX1"][2] > positions["TX3"][2]
    # and the TX pair now hangs below the RX row
    assert positions["TX3"][2] < float(np.mean(heights))


def test_phase_centre_is_the_rigs():
    layout = _v3_layout()
    tilt = math.radians(PITCH_DEG)
    positions = layout.positions_m(tilt)
    centre = 0.5 * (
        np.mean([positions[name] for name in ("TX1", "TX3")], axis=0)
        + np.mean([positions[f"RX{index}"] for index in range(1, 5)], axis=0)
    )
    # the frame's horizontal origin is the phase centre
    np.testing.assert_allclose(centre[:2], [0.0, 0.0], atol=1e-12)
    offset_down_mm = levm_phase_centre_offset_mm(90.0, PITCH_DEG)[1]
    assert layout.phase_centre_height_m(tilt) == pytest.approx(
        RX_ROW_HEIGHT_M - offset_down_mm / 1000.0
    )
    assert centre[2] == pytest.approx(layout.phase_centre_height_m(tilt))


def test_offsets_use_the_rigs_phase_centre_convention():
    offsets = levm_antenna_offsets_mm(90.0, PITCH_DEG)
    centre = 0.5 * (
        np.mean([offsets[name] for name in ("TX1", "TX3")], axis=0)
        + np.mean([offsets[f"RX{index}"] for index in range(1, 5)], axis=0)
    )
    np.testing.assert_allclose(centre, levm_phase_centre_offset_mm(90.0, PITCH_DEG), atol=1e-9)


def test_layout_from_the_v3_rig_file_places_the_rx_row_at_51_mm():
    rig = RigGeometry.from_json(V3_RIG)
    setup = rig.enclosure_setup()
    layout = antennas.layout_from_phase_centre(
        phase_centre_height_m=setup.radar_height_m,
        board_rotation_deg=setup.iwr_board_rotation_deg,
        boresight_pitch_deg=setup.iwr_tilt_deg,
    )
    assert layout.rx_row_height_m == pytest.approx(0.051)
    assert layout.board_rotation_deg == 90.0
    assert layout.basis == antennas.RIG_BASIS
    assert layout.phase_centre_height_m(math.radians(PITCH_DEG)) == pytest.approx(
        setup.radar_height_m
    )


def test_legacy_layout_takes_the_height_as_the_rx_row_at_plus_ninety():
    layout = antennas.legacy_layout(0.152)
    assert layout.rx_row_height_m == 0.152
    assert layout.board_rotation_deg == antennas.LEGACY_BOARD_ROTATION_DEG == 90.0
    assert layout.basis == antennas.LEGACY_BASIS
    assert "rx_row" in layout.basis


def test_canonical_channels_follow_the_snapshot_builder():
    """Slot k of a canonical snapshot is (TX3, RX4) ... (TX1, RX1) in both TX orders.

    canonicalize_tx_blocks restores the physical TX order and reverses the
    eight channels, so the slot-to-antenna map is derived from it, not typed.
    """
    expected = (
        ("TX3", "RX4"),
        ("TX3", "RX3"),
        ("TX3", "RX2"),
        ("TX3", "RX1"),
        ("TX1", "RX4"),
        ("TX1", "RX3"),
        ("TX1", "RX2"),
        ("TX1", "RX1"),
    )
    assert antennas.canonical_channels("normal") == expected
    assert antennas.canonical_channels("reversed") == expected
    with pytest.raises(ValueError, match="tx_order"):
        antennas.canonical_channels("sideways")


def test_exact_paths_mirror_the_floor():
    tx = np.array([[0.0, 0.0, 0.10]])
    rx = np.array([[0.0, 0.0, 0.05]])
    ball = np.array([[3.0, 0.0, 0.50]])
    paths = antennas.channel_paths_m(tx, rx, ball)
    direct_tx = math.hypot(3.0, 0.40)
    image_tx = math.hypot(3.0, 0.60)
    direct_rx = math.hypot(3.0, 0.45)
    image_rx = math.hypot(3.0, 0.55)
    np.testing.assert_allclose(
        paths[0, 0],
        [
            direct_tx + direct_rx,
            direct_tx + image_rx,
            image_tx + direct_rx,
            image_tx + image_rx,
        ],
    )
