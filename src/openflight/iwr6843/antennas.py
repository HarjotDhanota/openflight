"""Where each IWR6843LEVM antenna sits, in the frame the LCMF model works in.

Positions come from TI's ECAD patch table, the board's rotation and its aim. Frame: metres;
x forward, y lateral (target-right positive), z up above the hitting surface; the horizontal
origin is the LCMF virtual array's phase centre, where the IWR's ranges start."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

import numpy as np

from openflight.iwr6843 import doa
from openflight.iwr6843.music import LAM
from openflight.rig_geometry import (
    camera_rdf_offset_to_target_lfu,
    levm_antenna_offsets_mm,
    levm_phase_centre_offset_mm,
)

LCMF_TX = ("TX1", "TX3")
RX = ("RX1", "RX2", "RX3", "RX4")

# Carrier phase GROWS with path length, exp(+j*2*pi*path/lambda); with the board turned
# +90 deg slot 0 is the TOP element (TX3, RX4), as a test pins.
PHASE_SIGN = +1.0

LEGACY_BOARD_ROTATION_DEG = 90.0
RIG_BASIS = "rig_geometry_phase_centre"
LEGACY_BASIS = "legacy_single_height_as_rx_row_assumed_plus_90_deg"


@dataclass(frozen=True)
class AntennaLayout:
    """The LEVM on its mount; a change of aim turns it about its RX-row centre."""

    board_rotation_deg: float
    rx_row_height_m: float  # RX-row centre above the hitting surface
    basis: str
    # An idealised patch table for tests; None is TI's LEVM ECAD table.
    patches_mm: Mapping[str, tuple[float, float]] | None = None

    def _relative_m(self, tilt_rad: float, roll_rad: float) -> dict[str, np.ndarray]:
        offsets = levm_antenna_offsets_mm(
            self.board_rotation_deg, math.degrees(tilt_rad), self.patches_mm
        )
        # the unit's roll turns the board about the forward axis: + lifts its right side
        cos_roll, sin_roll = math.cos(roll_rad), math.sin(roll_rad)
        relative = {}
        for name, offset in offsets.items():
            lateral, forward, up = camera_rdf_offset_to_target_lfu(offset)
            relative[name] = np.array(
                [
                    forward,
                    lateral * cos_roll - up * sin_roll,
                    lateral * sin_roll + up * cos_roll,
                ]
            )
        return relative

    def _phase_centre_m(self, relative: Mapping[str, np.ndarray]) -> np.ndarray:
        tx = np.mean([relative[name] for name in LCMF_TX], axis=0)
        rx = np.mean([relative[name] for name in RX], axis=0)
        return 0.5 * (tx + rx)

    def positions_m(self, tilt_rad: float, roll_rad: float = 0.0) -> dict[str, np.ndarray]:
        """Every antenna's (forward, lateral, up) in metres, origin below the
        phase centre, heights above the hitting surface; ``roll_rad`` turns the
        board about its RX-row centre."""
        relative = self._relative_m(tilt_rad, roll_rad)
        centre = self._phase_centre_m(relative)
        return {
            name: np.array(
                [
                    position[0] - centre[0],
                    position[1] - centre[1],
                    self.rx_row_height_m + position[2],
                ]
            )
            for name, position in relative.items()
        }

    def phase_centre_height_m(self, tilt_rad: float, roll_rad: float = 0.0) -> float:
        """Height of the LCMF virtual array's phase centre above the surface."""
        relative = self._relative_m(tilt_rad, roll_rad)
        return self.rx_row_height_m + float(self._phase_centre_m(relative)[2])

    def as_dict(self) -> dict:
        """JSON-safe description for session and replay records."""
        return {
            "board_rotation_deg": self.board_rotation_deg,
            "rx_row_height_m": self.rx_row_height_m,
            "basis": self.basis,
            "patches": "levm_ecad" if self.patches_mm is None else "custom",
        }


def world_lateral_m(board_lateral_m, height_m, roll_rad: float):
    """The world lateral a rolled board's horizontal baseline (TX2) measured.

    The baseline lies along (0, cos r, sin r) in (forward, lateral, up): it reads
    lateral * cos r + height * sin r, so the height (from the phase centre) comes out.
    """
    return (np.asarray(board_lateral_m) - np.asarray(height_m) * math.sin(roll_rad)) / math.cos(
        roll_rad
    )


def layout_from_phase_centre(
    *,
    phase_centre_height_m: float,
    board_rotation_deg: float,
    boresight_pitch_deg: float,
) -> AntennaLayout:
    """The layout behind a rig-derived (phase-centre) radar height, at the rig's aim."""
    offset_down_mm = levm_phase_centre_offset_mm(board_rotation_deg, boresight_pitch_deg)[1]
    return AntennaLayout(
        board_rotation_deg=float(board_rotation_deg),
        rx_row_height_m=float(phase_centre_height_m) + offset_down_mm / 1000.0,
        basis=RIG_BASIS,
    )


def legacy_layout(radar_height_m: float) -> AntennaLayout:
    """A rig-less session's layout: its height is the RX-row centre, board turned +90 deg."""
    return AntennaLayout(
        board_rotation_deg=LEGACY_BOARD_ROTATION_DEG,
        rx_row_height_m=float(radar_height_m),
        basis=LEGACY_BASIS,
    )


def canonical_channels(tx_order: str) -> tuple[tuple[str, str], ...]:
    """(TX, RX) of each slot of a canonical eight-channel snapshot, derived from its builder."""
    tx_order = doa.validate_tx_order(tx_order)
    early, late = ("TX1", "TX3") if tx_order == "normal" else ("TX3", "TX1")
    codes = np.arange(4, dtype=float)
    slots = doa.canonicalize_tx_blocks(codes, codes + 10.0, tdm_phase=0.0, tx_order=tx_order)
    channels = []
    for code in np.rint(slots.real).astype(int):
        tx = late if code >= 10 else early
        channels.append((tx, RX[code % 10]))
    return tuple(channels)


def channel_positions_m(
    positions: Mapping[str, np.ndarray], tx_order: str
) -> tuple[np.ndarray, np.ndarray]:
    """TX and RX positions per canonical slot, each shaped (8, 3)."""
    channels = canonical_channels(tx_order)
    tx = np.array([positions[tx_name] for tx_name, _rx_name in channels], dtype=float)
    rx = np.array([positions[rx_name] for _tx_name, rx_name in channels], dtype=float)
    return tx, rx


def channel_paths_m(tx_m: np.ndarray, rx_m: np.ndarray, ball_m: np.ndarray) -> np.ndarray:
    """Exact DD, DG, GD, GG path lengths, shaped (n, channels, 4).

    ``tx_m``/``rx_m`` are (channels, 3), ``ball_m`` (n, 3); a floor bounce mirrors z.
    """
    mirror = np.array([1.0, 1.0, -1.0])
    ball = np.asarray(ball_m, dtype=float)[:, None, :]
    tx_direct = np.linalg.norm(ball - tx_m[None, :, :], axis=-1)
    tx_image = np.linalg.norm(ball - (tx_m * mirror)[None, :, :], axis=-1)
    rx_direct = np.linalg.norm(ball - rx_m[None, :, :], axis=-1)
    rx_image = np.linalg.norm(ball - (rx_m * mirror)[None, :, :], axis=-1)
    return np.stack(
        [
            tx_direct + rx_direct,
            tx_direct + rx_image,
            tx_image + rx_direct,
            tx_image + rx_image,
        ],
        axis=-1,
    )


def steering(paths_m: np.ndarray) -> np.ndarray:
    """Channel response to a path of this length, in the data's convention."""
    return np.exp(1j * PHASE_SIGN * 2.0 * np.pi * np.asarray(paths_m) / LAM)


__all__ = [
    "LEGACY_BASIS",
    "LEGACY_BOARD_ROTATION_DEG",
    "PHASE_SIGN",
    "RIG_BASIS",
    "AntennaLayout",
    "canonical_channels",
    "channel_paths_m",
    "channel_positions_m",
    "layout_from_phase_centre",
    "legacy_layout",
    "steering",
    "world_lateral_m",
]
