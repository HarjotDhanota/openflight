"""The tester's per-session solve from the teed ball's pixel size (range, scale, offsets)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

from openflight.constants import BALL_DIAMETER_MM
from openflight.rig_geometry import RigGeometry

# Below this the range solve is too soft to trust: at 8 px, one pixel of
# diameter error is 12 % of range. It still runs; the warning names it.
MIN_BALL_DIAMETER_PX = 8.0
EDGE_MARGIN_RADII = 1.0


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
    """Solve this setup's geometry from one detected teed ball (``x``, ``y``, ``diameter_px``).

    Height assumes the rig's designed pitch. Half a pixel of diameter moves the range 60-65 mm.
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
        "range_to_ball_mm": "ball angular size (42.7 mm over its pixel diameter)",
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
