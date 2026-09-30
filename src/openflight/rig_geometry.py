"""Enclosure geometry in two layers: what the CAD fixes, what the teed ball solves.

`RigGeometry` is the static layer, measured once from the enclosure and shipped
as a JSON file. `solve_setup` is the dynamic layer: range, scale, lateral
offset and height read off the resting ball each session. Every solved number
carries its provenance so a measured value and an assumed one never look alike.

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

BALL_DIAMETER_MM = 42.67
SPEED_OF_SOUND_M_S = 343.0  # 20 C; the acoustic walk-back's default
# Below this the range solve is too soft to trust: at 8 px, one pixel of
# diameter error is 12 % of range. It still runs; the warning names it.
MIN_BALL_DIAMETER_PX = 8.0
EDGE_MARGIN_RADII = 1.0

# TI xWR6843LEVM (PROC116A) patch centres in the board's ECAD frame, mm, from
# TI's swrr178/swrr179 design files (assembly markers, STEP placements and
# the 3D PDF's copper agree to 0.1 mm). RX1-4 are one lambda/2 row; LCMF's
# vertical array uses TX1 and TX3 with all four RX.
LEVM_RX_PATCHES_MM = ((24.037, 45.851), (26.455, 45.851), (28.872, 45.851), (31.290, 45.851))
LEVM_LCMF_TX_PATCHES_MM = ((38.767, 42.136), (48.437, 42.136))
# TX2 sits midway along the TX1/TX3 line and lambda/2 off it toward the RX
# row: the baseline HLCMF's horizontal phase reads.
LEVM_TX2_PATCH_MM = (43.602, 44.553)
# Every LEVM antenna by name. Each is really a pair of small patches along its
# footprint's 3.7 mm length, and these are the footprint centres; an unequal
# series feed between the two patches could move the electrical centre along
# that length by tenths of a millimetre.
LEVM_ANTENNA_PATCHES_MM = {
    **{f"RX{index + 1}": patch for index, patch in enumerate(LEVM_RX_PATCHES_MM)},
    "TX1": LEVM_LCMF_TX_PATCHES_MM[0],
    "TX2": LEVM_TX2_PATCH_MM,
    "TX3": LEVM_LCMF_TX_PATCHES_MM[1],
}


def _board_plane_mm(x_mm: float, y_mm: float, board_rotation_deg: float) -> tuple[float, float]:
    """An ECAD-frame vector in the board's plane seen from the front: (toward
    the viewer's right, up) mm.

    The antennas face the front, so the ECAD frame is seen unmirrored, turned
    ``board_rotation_deg`` counter-clockwise.
    """
    angle = math.radians(board_rotation_deg)
    return (
        x_mm * math.cos(angle) - y_mm * math.sin(angle),
        x_mm * math.sin(angle) + y_mm * math.cos(angle),
    )


def _board_plane_to_camera_mm(
    right_mm: float, up_mm: float, boresight_pitch_deg: float
) -> tuple[float, float, float]:
    """A board-plane (right, up) vector in camera image axes, mm.

    The viewer facing the front has target-left on their right, so the board's
    rightward offset is camera -x. Aimed ``boresight_pitch_deg`` up, the board
    leans back: its own "up" gains a component away from the target (audit F11).
    """
    pitch = math.radians(boresight_pitch_deg)
    return (-right_mm, -up_mm * math.cos(pitch), -up_mm * math.sin(pitch))


def _levm_rx_row_centre_mm() -> tuple[float, float]:
    rx_x = sum(x for x, _y in LEVM_RX_PATCHES_MM) / len(LEVM_RX_PATCHES_MM)
    rx_y = sum(y for _x, y in LEVM_RX_PATCHES_MM) / len(LEVM_RX_PATCHES_MM)
    return rx_x, rx_y


def _levm_board_plane_offset_mm(board_rotation_deg: float) -> tuple[float, float]:
    """The phase centre from the RX-row centre in the board's plane seen from
    the front: (toward the viewer's right, up) mm.

    Each TX/RX pair's phase centre is midway between its two patches, so the
    array's is half the vector from the RX row to the TX pair.
    """
    rx_x, rx_y = _levm_rx_row_centre_mm()
    tx_x = sum(x for x, _y in LEVM_LCMF_TX_PATCHES_MM) / len(LEVM_LCMF_TX_PATCHES_MM)
    tx_y = sum(y for _x, y in LEVM_LCMF_TX_PATCHES_MM) / len(LEVM_LCMF_TX_PATCHES_MM)
    return _board_plane_mm(0.5 * (tx_x - rx_x), 0.5 * (tx_y - rx_y), board_rotation_deg)


def levm_antenna_offsets_mm(
    board_rotation_deg: float,
    boresight_pitch_deg: float,
    patches_mm: Mapping[str, tuple[float, float]] | None = None,
) -> dict[str, tuple[float, float, float]]:
    """Each LEVM antenna from the RX-row centre in camera image axes, mm.

    The same turn and lean as the phase centre's (`levm_phase_centre_offset_mm`),
    applied to each footprint centre instead of the pairs' midpoint.
    ``patches_mm`` replaces the ECAD table (a test's idealised array); the RX
    row it is measured from stays the named RX1-RX4 of that table.
    """
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
    """Height of the LCMF virtual array's phase centre above the RX-row centre,
    in the board's plane.

    The board is seen from the front, turned ``board_rotation_deg`` counter-
    clockwise from its ECAD frame (0: ECAD +Y up; +90: ECAD +X up, the v42 and
    v3 mount). The two-ray model's radar height is the RX-row height plus this,
    once the board's aim is applied (audit F11; `levm_phase_centre_offset_mm`).
    """
    return _levm_board_plane_offset_mm(board_rotation_deg)[1]


def levm_phase_centre_offset_mm(
    board_rotation_deg: float, boresight_pitch_deg: float
) -> tuple[float, float, float]:
    """The LCMF phase centre from the RX-row centre in camera image axes, mm.

    See `_board_plane_to_camera_mm` for the turn into camera axes (audit F11).
    """
    right, up = _levm_board_plane_offset_mm(board_rotation_deg)
    return _board_plane_to_camera_mm(right, up, boresight_pitch_deg)


def camera_rdf_offset_to_target_lfu(offset_mm) -> tuple[float, float, float]:
    """Convert a camera-relative right/down/forward offset to target lateral/forward/up.

    Lateral is positive toward target-right, the same as the camera's world rays
    (``unit_world_rays``) and ``enclosure_setup``.
    """
    right, down, forward = (float(value) / 1000.0 for value in offset_mm)
    return (right, forward, -down)


def geometry_fingerprint(parameters: Mapping) -> str:
    """SHA-256 of loaded parameters as sorted, compact, ASCII-escaped JSON."""
    payload = json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RigGeometry:
    """The static layer. Offsets locate each sensor from the camera's optical
    centre in image axes (mm); None means not measured, and every consumer must
    say so rather than guess. Heights are above the floor the unit stands on;
    pitches are positive up."""

    # Every field is required, with no defaults: a file that leaves one out is
    # refused rather than filled in (wiring audit C9). An explicit null still
    # means "not measured" where the type allows it.
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
    # The IWR board's turn in the enclosure seen from the front, counter-
    # clockwise from its ECAD frame: 0 is ECAD +Y up, +90 is ECAD +X up (USB
    # top right, RX row vertical on the left). It places the phase centre
    # off the RX row that iwr_offset_mm locates (audit F11).
    iwr_board_rotation_deg: float | None
    ops_boresight_pitch_deg: float | None
    housing_tilt_deg: float | None
    # LIS3DH board angles relative to the housing it is fixed to. None means
    # the file does not say, and the expectation falls back to "parallel".
    lis3dh_mount_pitch_deg: float | None
    lis3dh_mount_roll_deg: float | None
    # The board's turn about the vertical, counter-clockwise seen from above,
    # from the design's +Y arrow toward the enclosure's front. 180 means the
    # arrow points back and X and Y read reversed; None means as designed.
    lis3dh_mount_yaw_deg: float | None

    @property
    def principal_point(self) -> tuple[float, float]:
        """(cx, cy); the image centre stands in until a calibration exists."""
        return (self.image_width / 2.0, self.image_height / 2.0)

    @property
    def iwr_phase_centre_offset_mm(self) -> tuple[float, float, float] | None:
        """The IWR's virtual-array phase centre from the RX-row centre, camera axes mm.

        None when the board's rotation or its aim is not recorded; the RX row
        then stands in for the origin and ``enclosure_setup`` says so (audit F11).
        """
        if self.iwr_board_rotation_deg is None or self.iwr_boresight_pitch_deg is None:
            return None
        return levm_phase_centre_offset_mm(
            self.iwr_board_rotation_deg, self.iwr_boresight_pitch_deg
        )

    @property
    def iwr_origin_mm(self) -> tuple[float, float, float] | None:
        """Where the IWR's ranges start, from the camera in image axes (mm).

        The phase centre when the rig can place it, else the RX-row centre the
        file measures. Every range from the IWR starts here (audit F11); only a
        check against another measurement of the RX row reads iwr_offset_mm.
        """
        if self.iwr_offset_mm is None:
            return None
        offset = self.iwr_phase_centre_offset_mm
        if offset is None:
            return self.iwr_offset_mm
        return tuple(a + b for a, b in zip(self.iwr_offset_mm, offset))

    def enclosure_setup(self) -> "EnclosureSetup":
        """Derive the server's geometry flags from the enclosure's constants.

        The camera's lateral offset is camera-relative-to-radar, positive
        target-right, so it is the negative of the IWR origin's x offset.
        Anything the rig does not carry is None and named in ``missing``.
        """
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
        """The teed ball seen from the OPS: (forward, lateral, above) in metres.

        The IWR's tee range starts at its origin (``iwr_origin_mm``, the height
        ``radar_height_m`` names) and places the ball straight downrange of the
        RX row; the rig's OPS and IWR offsets then move the origin to the OPS
        itself, so the OPS cosine model uses its own line of sight (audit F12).
        Heights are above the hitting surface. None when an offset is missing.
        """
        origin_mm = self.iwr_origin_mm
        if self.ops_offset_mm is None or self.iwr_offset_mm is None or origin_mm is None:
            return None
        ops = camera_rdf_offset_to_target_lfu(self.ops_offset_mm)
        origin = camera_rdf_offset_to_target_lfu(origin_mm)
        ops_lateral, ops_forward, ops_up = (a - b for a, b in zip(ops, origin))
        # the phase centre sits off the RX row, the ball does not move with it (audit F11)
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
        """What the LIS3DH should read when this enclosure is placed as designed:
        housing tilt plus the board's own mount angle. Without a housing tilt
        there is no expectation and the pitch is None, named in ``missing``."""
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
        """Load a rig file, refusing missing, unknown or malformed fields (audit C9)."""
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
    """Every RigGeometry field, present and well-formed, with nothing extra.

    Nullable fields may be an explicit null, which says "not measured"; a
    field that is absent is refused rather than defaulted (wiring audit C9).
    """
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
    # The two-ray model wants the virtual array's vertical phase centre; the
    # rig file gives the RX-row centre and how the board is turned. With the
    # RX row vertical, as LCMF needs, the phase centre sits about 8 mm above
    # or below it, so a rig without the rotation stays on the RX row and says
    # so (audit F11; RigGeometry.iwr_origin_mm). The offset is the vertical
    # one, up positive; the RX-row height is kept for the record.
    radar_height_reference: str = "iwr_rx_row_centre"
    radar_phase_centre_offset_m: float | None = None
    radar_phase_centre_status: str = "unknown_iwr_board_orientation"
    radar_rx_row_height_m: float | None = None
    # How the board is turned, so LCMF can place each antenna off the phase
    # centre ``radar_height_m`` names; None when the rig does not record it.
    iwr_board_rotation_deg: float | None = None

    @property
    def tee_lateral_offset_m(self) -> float | None:
        """The tee's lateral offset from the IWR RX row, for the LCMF inversion.

        The ball is teed on the camera's axis, so it is the camera's own
        lateral offset: 0 for the v3 enclosure, whose lens sits centred above
        the RX row (audit F10).
        """
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
    ``pitch_deg`` is None when the file cannot say; ``roll_deg`` is always a
    number because "square" is a design statement the enclosure always makes."""

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


@dataclass(frozen=True)
class SetupSolution:
    """The dynamic layer, solved from the teed ball. ``solved_from`` says where
    each number came from; ``warnings`` names why a solve is soft."""

    range_to_ball_mm: float
    mm_per_px_at_ball: float
    lateral_offset_mm: float
    height_above_ball_mm: float
    camera_pitch_deg: float
    mic_to_ball_m: float | None
    solved_from: Mapping[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def range_disagreement_mm(self, reference_range_mm: float) -> float:
        """Camera-solved range minus an independent range (radar or tape)."""
        return float(self.range_to_ball_mm - reference_range_mm)

    def as_dict(self) -> dict:
        return {
            "range_to_ball_mm": self.range_to_ball_mm,
            "mm_per_px_at_ball": self.mm_per_px_at_ball,
            "lateral_offset_mm": self.lateral_offset_mm,
            "height_above_ball_mm": self.height_above_ball_mm,
            "camera_pitch_deg": self.camera_pitch_deg,
            "mic_to_ball_m": self.mic_to_ball_m,
            "solved_from": dict(self.solved_from),
            "warnings": list(self.warnings),
        }


def solve_setup(ball, rig: RigGeometry) -> SetupSolution:
    """Solve this setup's geometry from one detected teed ball.

    ``ball`` has ``x``, ``y`` (px) and ``diameter_px``. Range comes from the
    ball's angular size as a slant along its pixel ray; scale from the depth;
    lateral offset from its column; height from its row, ASSUMING the rig's
    designed pitch (height and pitch couple through a single ball). Half a
    pixel of diameter moves the range 60-65 mm, which is why
    `range_disagreement_mm` exists.
    """
    diameter_px = float(ball.diameter_px)
    if not math.isfinite(diameter_px) or diameter_px <= 0.0:
        raise ValueError(f"ball diameter must be positive pixels, got {ball.diameter_px}")
    cx, cy = rig.principal_point

    depth_mm = rig.focal_px * BALL_DIAMETER_MM / diameter_px
    direction = ((float(ball.x) - cx) / rig.focal_px, (float(ball.y) - cy) / rig.focal_px, 1.0)
    ball_cam = tuple(depth_mm * component for component in direction)
    range_mm = math.hypot(*ball_cam)
    mm_per_px = depth_mm / rig.focal_px
    lateral_mm = -(float(ball.x) - cx) * mm_per_px
    height_mm = (float(ball.y) - cy) * mm_per_px

    warnings: list[str] = []
    if diameter_px < MIN_BALL_DIAMETER_PX:
        warnings.append(f"ball_only_{diameter_px:.1f}_px_range_solve_is_soft")
    radius = diameter_px / 2.0
    margin = EDGE_MARGIN_RADII * radius
    if (
        float(ball.x) < radius + margin
        or float(ball.x) > rig.image_width - radius - margin
        or float(ball.y) < radius + margin
        or float(ball.y) > rig.image_height - radius - margin
    ):
        warnings.append("ball_near_frame_edge_diameter_at_risk")

    solved_from = {
        "range_to_ball_mm": "ball angular size (42.67 mm over its pixel diameter)",
        "mm_per_px_at_ball": "range over focal length",
        "lateral_offset_mm": "ball column off the principal point",
        "height_above_ball_mm": (
            f"ball row, ASSUMING the rig's designed boresight pitch "
            f"({rig.boresight_pitch_deg:+.2f} deg)"
        ),
        "camera_pitch_deg": "rig design value, ASSUMED not measured",
    }
    mic_to_ball_m: float | None = None
    if rig.mic_offset_mm is not None:
        mic_to_ball_m = math.dist(ball_cam, rig.mic_offset_mm) / 1000.0
        solved_from["mic_to_ball_m"] = "ball position in camera axes against the rig's mic offset"
    else:
        solved_from["mic_to_ball_m"] = "unavailable: rig has no mic offset"
        warnings.append("rig_has_no_mic_offset_acoustic_walkback_uses_its_default")

    return SetupSolution(
        range_to_ball_mm=float(range_mm),
        mm_per_px_at_ball=float(mm_per_px),
        lateral_offset_mm=float(lateral_mm),
        height_above_ball_mm=float(height_mm),
        camera_pitch_deg=float(rig.boresight_pitch_deg),
        mic_to_ball_m=mic_to_ball_m,
        solved_from=solved_from,
        warnings=tuple(warnings),
    )
