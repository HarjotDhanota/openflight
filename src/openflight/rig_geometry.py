"""Two-layer setup geometry: what the enclosure fixes, what every setup solves.

Every user places the unit differently -- a different distance behind the
ball, off-centre left or right, at a different height -- and none of that can
be a constant. This module splits the geometry the way the whole launch-
monitor market does (guide section 1M):

  * `RigGeometry` is the STATIC layer: where each sensor sits inside the
    enclosure, measured once from the CAD and shipped as constants.
    `RigGeometry.test_rig()` carries the current hand-taped rig until the
    enclosure STEP file replaces it.
  * `SetupSolution` is the DYNAMIC layer: everything `solve_setup` can read
    off the teed ball each session -- range, scale, lateral offset, height,
    and the microphone's distance to the ball for the acoustic contact
    walk-back. Every field carries its provenance, because a solved number
    and an assumed one must never look alike.

What a single teed ball can NOT solve, and where that lives instead:

  * PITCH couples with height through the ball's row; the solution assumes
    the rig's designed boresight (level) and says so. A caller with a taped
    ball height solves the mount instead via
    `projection.camera_pitch_from_ball_row` -- the two calibrations separate.
  * ROLL is invisible to a point target. It comes from the LIS3DH
    (roadmap 1.3), never from here.
  * YAW (aim at the target) is invisible to any static scene. It is the one
    user gesture (roadmap 2.1), never solved here.

FRAME CONVENTION: all rig offsets are in CAMERA IMAGE AXES, millimetres from
the camera's optical centre -- +x to the IMAGE RIGHT, +y DOWN, +z FORWARD
along the boresight. That is deliberately the projection module's camera
frame, so pixel arithmetic needs no basis change.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping

BALL_DIAMETER_MM = 42.67  # a conforming ball; also head_outline.BALL_DIAMETER_MM

# The shipped OV9281 mode's optics, from the vendor-confirmed lens over the
# effective pixel pitch (2.8 mm / 6.0 um). Inno-Maker's CAM-MIPI9281RAW-V2
# page (checked 2026-09-02) confirms the 2.8 mm lens and 3.0 um pixels, and
# adds two facts the pinhole model must respect: the lens is a WIDE-ANGLE
# FISHEYE with TV distortion < -17 percent, and the quoted 72 deg horizontal
# FOV only reconciles with f = 933 px full-width WITH that barrel distortion
# (a pure pinhole at 933 px gives 68.9 deg). Distortion is sub-percent within
# ~60 px of the principal point -- where the teed ball lives -- but grows
# roughly quadratically to several px near the crop's corners, which is one
# more reason the checkerboard calibration (audit follow-up) is the durable
# answer. The same arithmetic lives in `clubpose.fit.FOCAL_PX`;
# `test_rig_geometry` pins the two to each other so they cannot drift apart.
_TEST_RIG_FOCAL_PX = 2.8 / (3.0 * 2 * 1e-3)

# A ball this small in the frame makes the range solve too soft to trust: at
# 8 px, one pixel of diameter error is 12 % of range. The solve still runs --
# the warning names the softness rather than hiding the number.
MIN_BALL_DIAMETER_PX = 8.0
# Within this many ball radii of the frame edge, the detected diameter is at
# risk of truncation and the scale solve inherits it.
EDGE_MARGIN_RADII = 1.0

SPEED_OF_SOUND_M_S = 343.0


@dataclass(frozen=True)
class RigGeometry:
    """The static layer: enclosure-internal geometry, in camera image axes (mm).

    ``ops_offset_mm`` / ``iwr_offset_mm`` / ``mic_offset_mm`` locate each
    sensor relative to the camera's optical centre; None means the offset has
    not been measured yet, and every consumer must degrade by name rather
    than guess. ``boresight_pitch_deg`` is the DESIGNED camera elevation once
    the enclosure sits on its kickstand (the enclosure cants the camera down
    by the kickstand's own angle, so the design value is level).
    """

    focal_px: float
    image_width: int = 320
    image_height: int = 200
    boresight_pitch_deg: float = 0.0
    ops_offset_mm: tuple[float, float, float] | None = None
    iwr_offset_mm: tuple[float, float, float] | None = None
    mic_offset_mm: tuple[float, float, float] | None = None
    provenance: str = ""
    # The enclosure facts the live pipeline needs beyond the offsets, each
    # None until the CAD supplies it. Heights are above the FLOOR the unit
    # stands on (deployed on its kickstand); pitches are positive UP.
    lens_height_above_floor_mm: float | None = None
    iwr_boresight_pitch_deg: float | None = None
    ops_boresight_pitch_deg: float | None = None
    housing_tilt_deg: float | None = None

    @property
    def principal_point(self) -> tuple[float, float]:
        """(cx, cy). No calibrated principal point exists; the centre stands in."""
        return (self.image_width / 2.0, self.image_height / 2.0)

    @classmethod
    def test_rig(cls) -> "RigGeometry":
        """The ASSUMED 2026-08 rig numbers, kept only so old sessions replay.

        These were never measured. Harjot (2026-09-09): the August session's
        enclosure had no measurements at all -- the 203.2 mm lens height,
        the 60.325 mm lateral offset and the 152.4 mm OPS height were guesses
        (the tee range was a software guess from the ball's size). Earlier
        docstrings called this a "tape chain"; they were wrong. The numbers
        stay so that session 20260825_181734 replays as it was captured; a
        real enclosure comes in through `RigGeometry.from_json` (see
        `config/enclosure_v42_rig_geometry.json`). The mic position is the
        OPS position as a stated proxy; the IWR offset is None.
        """
        ops = (60.325, 203.2 - 152.4, 0.0)
        return cls(
            focal_px=_TEST_RIG_FOCAL_PX,
            ops_offset_mm=ops,
            iwr_offset_mm=None,
            mic_offset_mm=ops,
            provenance=(
                "ASSUMED, never measured: 2026-08 rig numbers as configured for "
                "session 20260825_181734 (Harjot 2026-09-09: no measurements existed); "
                "mic position is the OPS antenna's as a stated proxy; IWR offset unmeasured"
            ),
        )

    def enclosure_setup(self) -> "EnclosureSetup":
        """The live pipeline's geometry inputs, derived from the enclosure.

        Every value the server used to take from a typed-in flag (camera
        mount height, camera lateral offset, radar height, radar tilt) is
        derived here from the rig's own constants, so a session in this
        enclosure is born with measured numbers rather than assumptions --
        the 2026-08 session was shot with assumed ones. Anything the rig does
        not carry is None and is NAMED in ``missing``, never defaulted.

        Sign conventions match the server's flags: the camera's lateral
        offset is the camera relative to the IWR antenna, positive target-
        right; the rig stores the IWR relative to the camera in image axes
        (+x image-right), so the two are negatives of each other.
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
        lateral = None
        if self.iwr_offset_mm is None:
            missing.append("iwr_offset_mm")
        else:
            lateral = -self.iwr_offset_mm[0] / 1000.0
            if camera_height is not None:
                radar_height = camera_height - self.iwr_offset_mm[1] / 1000.0
        if self.iwr_boresight_pitch_deg is None:
            missing.append("iwr_boresight_pitch_deg")
        return EnclosureSetup(
            camera_mount_height_m=camera_height,
            camera_lateral_offset_m=lateral,
            radar_height_m=radar_height,
            iwr_tilt_deg=self.iwr_boresight_pitch_deg,
            missing=tuple(missing),
            provenance=self.provenance,
        )

    def to_json(self, path: str | Path) -> None:
        """Write the geometry as JSON -- the file the STEP-derived config becomes."""
        Path(path).write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")

    @classmethod
    def from_json(cls, path: str | Path) -> "RigGeometry":
        """Read a geometry written by `to_json` (tuples come back from lists)."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for key in ("ops_offset_mm", "iwr_offset_mm", "mic_offset_mm"):
            if data.get(key) is not None:
                data[key] = tuple(float(value) for value in data[key])
        return cls(**data)


@dataclass(frozen=True)
class EnclosureSetup:
    """What `RigGeometry.enclosure_setup` could derive, and what it could not.

    ``missing`` names the rig fields that were absent; a consumer keeps its
    own default for those and says so, rather than reading None as zero.
    """

    camera_mount_height_m: float | None
    camera_lateral_offset_m: float | None
    radar_height_m: float | None
    iwr_tilt_deg: float | None
    missing: tuple[str, ...] = ()
    provenance: str = ""

    def as_dict(self) -> dict:
        """JSON-safe view for the session log."""
        return {
            "camera_mount_height_m": self.camera_mount_height_m,
            "camera_lateral_offset_m": self.camera_lateral_offset_m,
            "radar_height_m": self.radar_height_m,
            "iwr_tilt_deg": self.iwr_tilt_deg,
            "missing": list(self.missing),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class SetupSolution:
    """The dynamic layer: this setup's geometry, solved from the teed ball.

    Every field's origin is in ``solved_from``; a consumer that cares whether
    a number was measured or assumed reads it there. ``warnings`` carries the
    named reasons a solve is soft; an empty tuple is a clean solve.
    """

    range_to_ball_mm: float
    mm_per_px_at_ball: float
    lateral_offset_mm: float
    height_above_ball_mm: float
    camera_pitch_deg: float
    mic_to_ball_m: float | None
    solved_from: Mapping[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def range_disagreement_mm(self, reference_range_mm: float) -> float:
        """Camera-solved range minus an independent range (radar or tape).

        The setup-validation cross-check: on a sane setup the camera's
        ball-diameter range and the radar's tee range agree to a few
        centimetres, and a large disagreement means a mis-detected ball, a
        wrong tee distance, or a moved unit -- before a single shot is hit.
        """
        return float(self.range_to_ball_mm - reference_range_mm)

    def as_dict(self) -> dict:
        """A JSON-safe view for the session log and the setup UI."""
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

    ``ball`` is anything with ``x``, ``y`` (pixels) and ``diameter_px`` --
    `camera.club_motion.ReferenceBall` in production. The solves, each exact
    given its stated assumption:

      * RANGE from the ball's angular size: the pinhole DEPTH is
        ``focal * 42.67 / diameter_px``, and the reported range is the SLANT
        along the ball's pixel ray -- +8.6 mm over the depth at the session
        ball's off-axis position (2026-09-01 audit fix). Half a pixel of
        diameter moves the solve ~60-65 mm, which is why the radar
        cross-check (`range_disagreement_mm`) exists.
      * SCALE at the ball: ``depth / focal`` (transverse pixels convert at
        the depth, not the slant).
      * LATERAL OFFSET of the camera from the ball line, from the ball's
        column: the ball imaging right of centre means the camera sits left
        of the line, so the sign matches `projection.CAMERA_LATERAL_OFFSET_MM`
        (the taped -60.325 mm).
      * HEIGHT above the ball from the ball's row, ASSUMING the rig's
        designed boresight pitch -- height and pitch couple through a single
        ball, and the assumption is named in ``solved_from``.
      * MIC-TO-BALL distance for the acoustic contact walk-back, replacing
        the hardcoded 1.575 m: the ball's position in camera axes against the
        rig's mic offset. None, by name, when the rig has no mic position.
    """
    diameter_px = float(ball.diameter_px)
    if not math.isfinite(diameter_px) or diameter_px <= 0.0:
        raise ValueError(f"ball diameter must be positive pixels, got {ball.diameter_px}")
    cx, cy = rig.principal_point

    # f*D/d is the pinhole DEPTH along the optical axis; the slant range to
    # an off-axis ball exceeds it by the pixel ray's length -- +8.6 mm at the
    # session ball's position (2026-09-01 audit). The transverse scale
    # divides the DEPTH, not the slant, for the same reason.
    depth_mm = rig.focal_px * BALL_DIAMETER_MM / diameter_px
    direction = (
        (float(ball.x) - cx) / rig.focal_px,
        (float(ball.y) - cy) / rig.focal_px,
        1.0,
    )
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
            f"({rig.boresight_pitch_deg:+.2f} deg); solve the mount with "
            "projection.camera_pitch_from_ball_row instead when a taped ball "
            "height exists"
        ),
        "camera_pitch_deg": "rig design value, ASSUMED not measured",
    }

    mic_to_ball_m: float | None = None
    if rig.mic_offset_mm is not None:
        # ball_cam above is already the exact back-projection: the pixel ray
        # at the solved depth, unnormalized.
        mic_to_ball_m = math.dist(ball_cam, rig.mic_offset_mm) / 1000.0
        solved_from["mic_to_ball_m"] = (
            "ball position in camera axes against the rig's mic offset "
            f"({rig.provenance or 'unstated rig provenance'})"
        )
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
