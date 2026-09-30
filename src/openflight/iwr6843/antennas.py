"""Where each IWR6843LEVM antenna sits, in the frame the LCMF model works in.

LCMF-v1 put every antenna at one radar height on idealised lambda/2 indices.
The LEVM's TX pair actually sits about 16 mm above its RX row (v3 enclosure,
board turned +90 deg), so the transmit and receive legs meet the floor bounce
at different angles; and which element is on top depends on how the board is
turned. This module places each antenna from TI's ECAD patch table, the
board's rotation and its aim, reusing the rig module's conventions
(`openflight.rig_geometry.levm_antenna_offsets_mm`).

Frame: metres; x forward (downrange), y lateral (target-right positive, as
`camera_rdf_offset_to_target_lfu`), z up above the hitting surface. The
horizontal origin is the LCMF virtual array's phase centre, where the IWR's
ranges start (audit F11).
"""

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

# The processed channels' carrier phase GROWS with path length:
# exp(+j*2*pi*path/lambda). Three things in the pipeline agree: the TDM
# correction is registered positive (a receding ball's later chirp is removed
# with exp(-j*4*pi*v*tau/lambda)), the four-path TDM cross term is
# exp(+j*...), and with the snapshot builder's channel order this sign is the
# only one under which today's dictionary exp(+j*pi*k*sin(angle)) describes a
# board turned +90 deg (RX1 lowest, TX1 below TX3): slot 0 is then the TOP
# element, (TX3, RX4). tests/test_iwr6843_lcmf_convention.py pins it.
PHASE_SIGN = +1.0

LEGACY_BOARD_ROTATION_DEG = 90.0
RIG_BASIS = "rig_geometry_phase_centre"
LEGACY_BASIS = "legacy_single_height_as_rx_row_assumed_plus_90_deg"


@dataclass(frozen=True)
class AntennaLayout:
    """The LEVM on its mount: enough to place every antenna at any aim.

    The aim is not stored: LCMF takes it from ``Calibration.tilt_rad``, which
    a shot's inclinometer reading can move. A change of aim turns the board
    about its RX-row centre, the point the rig file measures.
    """

    board_rotation_deg: float
    rx_row_height_m: float  # RX-row centre above the hitting surface
    basis: str
    # An idealised patch table for tests; None is TI's LEVM ECAD table.
    patches_mm: Mapping[str, tuple[float, float]] | None = None

    def _relative_m(self, tilt_rad: float) -> dict[str, np.ndarray]:
        offsets = levm_antenna_offsets_mm(
            self.board_rotation_deg, math.degrees(tilt_rad), self.patches_mm
        )
        relative = {}
        for name, offset in offsets.items():
            lateral, forward, up = camera_rdf_offset_to_target_lfu(offset)
            relative[name] = np.array([forward, lateral, up])
        return relative

    def _phase_centre_m(self, relative: Mapping[str, np.ndarray]) -> np.ndarray:
        tx = np.mean([relative[name] for name in LCMF_TX], axis=0)
        rx = np.mean([relative[name] for name in RX], axis=0)
        return 0.5 * (tx + rx)

    def positions_m(self, tilt_rad: float) -> dict[str, np.ndarray]:
        """Every antenna's (forward, lateral, up) in metres, origin below the
        phase centre, heights above the hitting surface."""
        relative = self._relative_m(tilt_rad)
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

    def phase_centre_height_m(self, tilt_rad: float) -> float:
        """Height of the LCMF virtual array's phase centre above the surface."""
        return self.rx_row_height_m + float(self._phase_centre_m(self._relative_m(tilt_rad))[2])

    def as_dict(self) -> dict:
        """JSON-safe description for session and replay records."""
        return {
            "board_rotation_deg": self.board_rotation_deg,
            "rx_row_height_m": self.rx_row_height_m,
            "basis": self.basis,
            "patches": "levm_ecad" if self.patches_mm is None else "custom",
        }


def layout_from_phase_centre(
    *,
    phase_centre_height_m: float,
    board_rotation_deg: float,
    boresight_pitch_deg: float,
) -> AntennaLayout:
    """The layout behind a rig-derived radar height.

    The server's radar height is the phase centre's (`EnclosureSetup`), at the
    rig's aim; the RX row it was derived from sits the rig's own offset below.
    A solved camera height moves both by the same amount, so this holds for it.
    """
    offset_down_mm = levm_phase_centre_offset_mm(board_rotation_deg, boresight_pitch_deg)[1]
    return AntennaLayout(
        board_rotation_deg=float(board_rotation_deg),
        rx_row_height_m=float(phase_centre_height_m) + offset_down_mm / 1000.0,
        basis=RIG_BASIS,
    )


def legacy_layout(radar_height_m: float) -> AntennaLayout:
    """A layout for a session that recorded one radar height and a tilt.

    July/August sessions have no rig file. Their single height is taken as the
    RX-row centre and the board as turned +90 deg, the orientation LCMF-v1's
    hard-coded element order implies. Replays built on it are not the numbers
    those sessions recorded: the per-antenna model differs by design.
    """
    return AntennaLayout(
        board_rotation_deg=LEGACY_BOARD_ROTATION_DEG,
        rx_row_height_m=float(radar_height_m),
        basis=LEGACY_BASIS,
    )


def canonical_channels(tx_order: str) -> tuple[tuple[str, str], ...]:
    """(TX, RX) of each slot of a canonical eight-channel snapshot.

    Derived by running channel labels through the snapshot builder
    (`doa.canonicalize_tx_blocks`), so this cannot drift from it. The chirp
    configs transmit TX1 first in the "normal" order and TX3 first in the
    "reversed" one (`monitor.tx_order_from_config`); the raw RX channels are
    RX1-RX4 in order.
    """
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
    """Exact DD, DG, GD, GG path lengths per ball position and channel.

    ``tx_m`` and ``rx_m`` are (channels, 3), ``ball_m`` is (n, 3); the result
    is (n, channels, 4). A floor-bounced leg is the straight line to the
    antenna mirrored in the hitting surface (z -> -z).
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
]
