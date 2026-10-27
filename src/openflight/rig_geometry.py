"""Enclosure geometry: the static CAD layer.

Frame: camera image axes, millimetres from the lens front vertex -- +x to the
image right (target-right), +y down, +z forward along the boresight.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Mapping

SPEED_OF_SOUND_M_S = 343.0  # 20 C; the acoustic walk-back's default

# TI xWR6843LEVM (PROC116A) patch centres, ECAD frame, mm (TI swrr178/swrr179).
# RX1-4 are one lambda/2 row; LCMF's vertical array uses TX1 and TX3.
LEVM_RX_PATCHES_MM = ((24.037, 45.851), (26.455, 45.851), (28.872, 45.851), (31.290, 45.851))
LEVM_LCMF_TX_PATCHES_MM = ((38.767, 42.136), (48.437, 42.136))
# TX2 sits midway along the TX1/TX3 line and lambda/2 off it toward the RX
# row: the baseline HLCMF's horizontal phase reads.
LEVM_TX2_PATCH_MM = (43.602, 44.553)
LEVM_ANTENNA_PATCHES_MM = {
    **{f"RX{index + 1}": patch for index, patch in enumerate(LEVM_RX_PATCHES_MM)},
    "TX1": LEVM_LCMF_TX_PATCHES_MM[0],
    "TX2": LEVM_TX2_PATCH_MM,
    "TX3": LEVM_LCMF_TX_PATCHES_MM[1],
}


def _board_plane_mm(x_mm: float, y_mm: float, board_rotation_deg: float) -> tuple[float, float]:
    """An ECAD-frame vector as (viewer's right, up) mm in the board's plane, seen
    from the front and turned ``board_rotation_deg`` counter-clockwise."""
    angle = math.radians(board_rotation_deg)
    return (
        x_mm * math.cos(angle) - y_mm * math.sin(angle),
        x_mm * math.sin(angle) + y_mm * math.cos(angle),
    )


def _board_plane_to_camera_mm(
    right_mm: float, up_mm: float, boresight_pitch_deg: float
) -> tuple[float, float, float]:
    """A board-plane (right, up) vector in camera image axes, mm: the viewer's right
    is camera -x; pitched up, the board's "up" leans away from the target."""
    pitch = math.radians(boresight_pitch_deg)
    return (-right_mm, -up_mm * math.cos(pitch), -up_mm * math.sin(pitch))


def _levm_rx_row_centre_mm() -> tuple[float, float]:
    rx_x = sum(x for x, _y in LEVM_RX_PATCHES_MM) / len(LEVM_RX_PATCHES_MM)
    rx_y = sum(y for _x, y in LEVM_RX_PATCHES_MM) / len(LEVM_RX_PATCHES_MM)
    return rx_x, rx_y


def _levm_board_plane_offset_mm(board_rotation_deg: float) -> tuple[float, float]:
    """The phase centre from the RX-row centre, (viewer's right, up) mm in the
    board's plane: half the vector from the RX row to the TX pair."""
    rx_x, rx_y = _levm_rx_row_centre_mm()
    tx_x = sum(x for x, _y in LEVM_LCMF_TX_PATCHES_MM) / len(LEVM_LCMF_TX_PATCHES_MM)
    tx_y = sum(y for _x, y in LEVM_LCMF_TX_PATCHES_MM) / len(LEVM_LCMF_TX_PATCHES_MM)
    return _board_plane_mm(0.5 * (tx_x - rx_x), 0.5 * (tx_y - rx_y), board_rotation_deg)


def levm_antenna_offsets_mm(
    board_rotation_deg: float,
    boresight_pitch_deg: float,
    patches_mm: Mapping[str, tuple[float, float]] | None = None,
) -> dict[str, tuple[float, float, float]]:
    """Each LEVM antenna from the RX-row centre in camera image axes, mm."""
    patches = LEVM_ANTENNA_PATCHES_MM if patches_mm is None else patches_mm
    rows = [patches[f"RX{index}"] for index in range(1, 5)]
    rx_x = sum(x for x, _y in rows) / len(rows)
    rx_y = sum(y for _x, y in rows) / len(rows)
    offsets = {}
    for name, (x_mm, y_mm) in patches.items():
        right, up = _board_plane_mm(x_mm - rx_x, y_mm - rx_y, board_rotation_deg)
        offsets[name] = _board_plane_to_camera_mm(right, up, boresight_pitch_deg)
    return offsets


def levm_vertical_phase_centre_offset_mm(board_rotation_deg: float) -> float:
    """Height of the LCMF phase centre above the RX-row centre in the board's plane,
    ``board_rotation_deg`` counter-clockwise from ECAD seen from the front."""
    return _levm_board_plane_offset_mm(board_rotation_deg)[1]


def levm_phase_centre_offset_mm(
    board_rotation_deg: float, boresight_pitch_deg: float
) -> tuple[float, float, float]:
    """The LCMF phase centre from the RX-row centre in camera image axes, mm."""
    right, up = _levm_board_plane_offset_mm(board_rotation_deg)
    return _board_plane_to_camera_mm(right, up, boresight_pitch_deg)


def camera_rdf_offset_to_target_lfu(offset_mm) -> tuple[float, float, float]:
    """Camera right/down/forward offset to target lateral (+ target-right)/forward/up."""
    right, down, forward = (float(value) / 1000.0 for value in offset_mm)
    return (right, forward, -down)


def geometry_fingerprint(parameters: Mapping) -> str:
    """SHA-256 of loaded parameters as sorted, compact, ASCII-escaped JSON."""
    payload = json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RigGeometry:
    """The static layer: sensor offsets from the camera's optical centre in image
    axes (mm), None meaning not measured. Heights are above the floor; pitches
    are positive up."""

    focal_px: float
    image_width: int
    image_height: int
    boresight_pitch_deg: float
    ops_offset_mm: tuple[float, float, float] | None
    iwr_offset_mm: tuple[float, float, float] | None
    mic_offset_mm: tuple[float, float, float] | None
    provenance: str
    lens_height_above_floor_mm: float | None
    iwr_boresight_pitch_deg: float | None
    # IWR board turn seen from the front, counter-clockwise from its ECAD frame:
    # 0 is ECAD +Y up, +90 is ECAD +X up.
    iwr_board_rotation_deg: float | None
    ops_boresight_pitch_deg: float | None
    housing_tilt_deg: float | None
    # LIS3DH board angles relative to the housing; None means parallel.
    lis3dh_mount_pitch_deg: float | None
    lis3dh_mount_roll_deg: float | None
    # Board turn about the vertical, counter-clockwise from above; 180 reverses
    # X and Y; None means as designed.
    lis3dh_mount_yaw_deg: float | None

    @property
    def principal_point(self) -> tuple[float, float]:
        """(cx, cy); the image centre stands in until a calibration exists."""
        return (self.image_width / 2.0, self.image_height / 2.0)

    @property
    def iwr_phase_centre_offset_mm(self) -> tuple[float, float, float] | None:
        """The IWR's phase centre from the RX-row centre, camera axes mm; None if unplaceable."""
        if self.iwr_board_rotation_deg is None or self.iwr_boresight_pitch_deg is None:
            return None
        return levm_phase_centre_offset_mm(
            self.iwr_board_rotation_deg, self.iwr_boresight_pitch_deg
        )

    @property
    def iwr_origin_mm(self) -> tuple[float, float, float] | None:
        """Where the IWR's ranges start, camera image axes (mm): the phase centre
        when the rig can place it, else the RX-row centre."""
        if self.iwr_offset_mm is None:
            return None
        offset = self.iwr_phase_centre_offset_mm
        if offset is None:
            return self.iwr_offset_mm
        return tuple(a + b for a, b in zip(self.iwr_offset_mm, offset))

    def enclosure_setup(self) -> "EnclosureSetup":
        """Derive the server's geometry flags from the enclosure's constants; anything
        the rig does not carry is None and named in ``missing``."""
        missing: list[str] = []
        camera_height = (
            self.lens_height_above_floor_mm / 1000.0
            if self.lens_height_above_floor_mm is not None
            else None
        )
        if camera_height is None:
            missing.append("lens_height_above_floor_mm")
        radar_height = None
        rx_row_height = None
        lateral = None
        forward = None
        origin = self.iwr_origin_mm
        if self.iwr_offset_mm is None or origin is None:
            missing.append("iwr_offset_mm")
        else:
            lateral = -origin[0] / 1000.0
            forward = -origin[2] / 1000.0
            if camera_height is not None:
                radar_height = camera_height - origin[1] / 1000.0
                rx_row_height = camera_height - self.iwr_offset_mm[1] / 1000.0
        if self.iwr_boresight_pitch_deg is None:
            missing.append("iwr_boresight_pitch_deg")
        phase_centre = self.iwr_phase_centre_offset_mm
        derived = phase_centre is not None and origin is not None
        return EnclosureSetup(
            camera_mount_height_m=camera_height,
            camera_lateral_offset_m=lateral,
            camera_forward_offset_m=forward,
            radar_height_m=radar_height,
            iwr_tilt_deg=self.iwr_boresight_pitch_deg,
            missing=tuple(missing),
            provenance=self.provenance,
            radar_height_reference=(
                "iwr_virtual_array_phase_centre" if derived else "iwr_rx_row_centre"
            ),
            radar_phase_centre_offset_m=-phase_centre[1] / 1000.0 if derived else None,
            radar_phase_centre_status=(
                "derived_from_board_rotation" if derived else "unknown_iwr_board_orientation"
            ),
            radar_rx_row_height_m=rx_row_height,
            iwr_board_rotation_deg=self.iwr_board_rotation_deg,
        )

    def ops_ball_geometry_m(
        self, *, tee_slant_range_m: float, ball_height_m: float, radar_height_m: float
    ) -> tuple[float, float, float] | None:
        """The teed ball seen from the OPS: (forward, lateral, above) in metres, above
        the hitting surface, for the OPS cosine model; None when an offset is missing."""
        origin_mm = self.iwr_origin_mm
        if self.ops_offset_mm is None or self.iwr_offset_mm is None or origin_mm is None:
            return None
        ops = camera_rdf_offset_to_target_lfu(self.ops_offset_mm)
        origin = camera_rdf_offset_to_target_lfu(origin_mm)
        ops_lateral, ops_forward, ops_up = (a - b for a, b in zip(ops, origin))
        # the ball stays downrange of the RX row, not of the phase centre
        ball_lateral = camera_rdf_offset_to_target_lfu(self.iwr_offset_mm)[0] - origin[0]
        ball_forward = math.sqrt(
            tee_slant_range_m**2 - (ball_height_m - radar_height_m) ** 2 - ball_lateral**2
        )
        return (
            ball_forward - ops_forward,
            ball_lateral - ops_lateral,
            ball_height_m - (radar_height_m + ops_up),
        )

    def expected_inclinometer_orientation(self) -> "ExpectedOrientation":
        """What the LIS3DH should read when placed as designed: housing tilt plus
        board mount angle; pitch is None (named in ``missing``) without a housing tilt."""
        missing: list[str] = []
        provenance: dict[str, str] = {}
        mount_pitch = self.lis3dh_mount_pitch_deg
        mount_roll = self.lis3dh_mount_roll_deg
        if mount_pitch is None:
            missing.append("lis3dh_mount_pitch_deg")
        if mount_roll is None:
            missing.append("lis3dh_mount_roll_deg")

        pitch: float | None
        if self.housing_tilt_deg is None:
            missing.append("housing_tilt_deg")
            pitch = None
            provenance["pitch_deg"] = "unavailable: the rig file has no housing_tilt_deg"
        else:
            pitch = float(self.housing_tilt_deg) + float(mount_pitch or 0.0)
            mount_part = (
                f"lis3dh_mount_pitch_deg {float(mount_pitch):+.2f} from the rig file"
                if mount_pitch is not None
                else "an ASSUMED +0.00 board pitch (parallel to the housing)"
            )
            provenance["pitch_deg"] = (
                f"housing_tilt_deg {float(self.housing_tilt_deg):+.2f} plus {mount_part}"
            )
        roll = float(mount_roll or 0.0)
        provenance["roll_deg"] = (
            f"lis3dh_mount_roll_deg {float(mount_roll):+.2f} from the rig file"
            if mount_roll is not None
            else "ASSUMED +0.00: enclosure designed to stand square"
        )
        return ExpectedOrientation(
            pitch_deg=pitch,
            roll_deg=roll,
            missing=tuple(missing),
            provenance=provenance,
            rig_provenance=self.provenance,
        )

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")

    def snapshot(self) -> dict:
        """Preserve loaded values, including defaults, independently of the source file."""
        parameters = asdict(self)
        return {"parameters": parameters, "sha256": geometry_fingerprint(parameters)}

    @classmethod
    def from_json(cls, path: str | Path) -> "RigGeometry":
        """Load a rig file, refusing missing, unknown or malformed fields."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**validated_rig_fields(data))


_OFFSET_FIELDS = ("ops_offset_mm", "iwr_offset_mm", "mic_offset_mm")
_REQUIRED_NUMBER_FIELDS = ("focal_px", "boresight_pitch_deg")
_SIZE_FIELDS = ("image_width", "image_height")
_NULLABLE_NUMBER_FIELDS = (
    "lens_height_above_floor_mm",
    "iwr_boresight_pitch_deg",
    "iwr_board_rotation_deg",
    "ops_boresight_pitch_deg",
    "housing_tilt_deg",
    "lis3dh_mount_pitch_deg",
    "lis3dh_mount_roll_deg",
    "lis3dh_mount_yaw_deg",
)


def _finite_number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"rig geometry {name} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"rig geometry {name} must be finite, got {value!r}")
    return number


def validated_rig_fields(data) -> dict:
    """Every RigGeometry field, present and well-formed, with nothing extra."""
    if not isinstance(data, Mapping):
        raise ValueError("rig geometry must be a JSON object")
    expected = [item.name for item in fields(RigGeometry)]
    missing = [name for name in expected if name not in data]
    unknown = sorted(name for name in data if name not in expected)
    if missing or unknown:
        problems = []
        if missing:
            problems.append("missing " + ", ".join(missing))
        if unknown:
            problems.append("unknown " + ", ".join(unknown))
        raise ValueError("rig geometry fields: " + "; ".join(problems))
    loaded: dict = {}
    for name in _REQUIRED_NUMBER_FIELDS:
        loaded[name] = _finite_number(data[name], name)
    if loaded["focal_px"] <= 0.0:
        raise ValueError("rig geometry focal_px must be positive")
    for name in _SIZE_FIELDS:
        value = data[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"rig geometry {name} must be a positive integer, got {value!r}")
        loaded[name] = value
    for name in _NULLABLE_NUMBER_FIELDS:
        loaded[name] = None if data[name] is None else _finite_number(data[name], name)
    for name in _OFFSET_FIELDS:
        value = data[name]
        if value is None:
            loaded[name] = None
            continue
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ValueError(f"rig geometry {name} must be three numbers or null")
        loaded[name] = tuple(_finite_number(item, name) for item in value)
    if not isinstance(data["provenance"], str):
        raise ValueError("rig geometry provenance must be text")
    loaded["provenance"] = data["provenance"]
    return loaded


@dataclass(frozen=True)
class EnclosureSetup:
    """What `enclosure_setup` derived; ``missing`` names what it could not."""

    camera_mount_height_m: float | None
    camera_lateral_offset_m: float | None
    radar_height_m: float | None
    iwr_tilt_deg: float | None
    missing: tuple[str, ...] = ()
    provenance: str = ""
    camera_forward_offset_m: float | None = None
    # The two-ray model's vertical offset (up +) from the RX row to the phase centre.
    radar_height_reference: str = "iwr_rx_row_centre"
    radar_phase_centre_offset_m: float | None = None
    radar_phase_centre_status: str = "unknown_iwr_board_orientation"
    radar_rx_row_height_m: float | None = None
    iwr_board_rotation_deg: float | None = None

    @property
    def tee_lateral_offset_m(self) -> float | None:
        """The tee's lateral offset from the IWR RX row: the camera's own, since the
        ball is teed on the camera's axis."""
        return self.camera_lateral_offset_m

    def as_dict(self) -> dict:
        return {
            "camera_mount_height_m": self.camera_mount_height_m,
            "camera_lateral_offset_m": self.camera_lateral_offset_m,
            "tee_lateral_offset_m": self.tee_lateral_offset_m,
            "camera_forward_offset_m": self.camera_forward_offset_m,
            "radar_height_m": self.radar_height_m,
            "radar_height_reference": self.radar_height_reference,
            "radar_phase_centre_offset_m": self.radar_phase_centre_offset_m,
            "radar_phase_centre_status": self.radar_phase_centre_status,
            "radar_rx_row_height_m": self.radar_rx_row_height_m,
            "iwr_board_rotation_deg": self.iwr_board_rotation_deg,
            "iwr_tilt_deg": self.iwr_tilt_deg,
            "missing": list(self.missing),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class ExpectedOrientation:
    """The orientation a correctly placed enclosure should read, and why.
    ``pitch_deg`` is None when the file cannot say; ``roll_deg`` is always set."""

    pitch_deg: float | None
    roll_deg: float | None
    missing: tuple[str, ...] = ()
    provenance: Mapping[str, str] = field(default_factory=dict)
    rig_provenance: str = ""

    def as_dict(self) -> dict:
        return {
            "pitch_deg": self.pitch_deg,
            "roll_deg": self.roll_deg,
            "missing": list(self.missing),
            "provenance": dict(self.provenance),
            "rig_provenance": self.rig_provenance,
        }
