"""Constrained silhouette pose refit in the CORRECTED club frame and camera.

Research script. Not production, not upstream. It re-runs the 2026-08-27
"0 of 6 shots inside the physical envelope" experiment after the two frame
defects that voided it were fixed on this branch (sole-grounded
`angles.square_pose()`; the mesh reflected into the world frame at load), and
then after three FURTHER defects this script found and corrects locally.

The question is NARROW and about CONSISTENCY, not accuracy: with the corrected
frame, do the fitted delivered angles come out inside the physical envelope and
coherent frame to frame? There is no truth here. Nothing below is an accuracy
claim.

WHAT THIS CORRECTS, none of it in production (see RESULTS.md for file:line):

1. CAMERA ORIENTATION. `projection._rotation_world_to_camera` aims the camera
   at the world origin, so the model necessarily projects the ball to
   ``(cx, cy)``. The ball is really at ``(176.6, 146.8)``: 46.8 px low, 16.6 px
   wide. Rather than assume "level", this SOLVES the pitch per shot from the
   ball's own pixel row and the tape chain, and solves the lateral offset from
   its pixel column, then uses the session medians.

2. POSE BOUNDS. `fit.fit_sequence` rejects ``abs(yaw) > 60`` and its default
   grids centre on the triad origin -- which is a BACKWARDS club, since the
   mesh's normalised +x points out the back. The corrected `square_pose()` sits
   at yaw ~= 179.6, so the shipped bounds exclude every real delivery. This
   script fits with grid AND bounds centred on `square_pose()`.

3. TEMPLATE/MASK DOMAIN MISMATCH. The cached mesh carries a hosel ~65 mm long
   past the head body, but `head_split.split_head` deliberately CUTS the hosel
   neck off the observed mask. IoU therefore rewards any pose that hides the
   hosel -- pointing it at the camera -- which is exactly the impossible
   attitude the uncorrected fit converged on. This clips the template at the
   same neck.

WORLD FRAME CONVENTION USED HERE. The origin is the point ON THE FLOOR directly
under the teed ball; +z is up, so ``z = 0`` is the ground and the clubhead can
never go below it; +x is downrange, away from the camera; +y is the repo's
``WORLD_RIGHT``. The ball CENTRE is therefore at ``(0, 0, 40)`` and the lens at
``z = 203.2``, both floor-referenced. This differs from `projection.py`, which
puts the origin at the ball CENTRE and then uses the floor-referenced 203.2 mm
as if it were a height above that origin -- a ~40 mm inconsistency this script
avoids by naming the datum explicitly.

Usage::

    uv run python research/pose_refit/refit_corrected.py --out <dir>
    uv run python research/pose_refit/refit_corrected.py --out <dir> --stage calib
    uv run python research/pose_refit/refit_corrected.py --out <dir> --stage masks
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402

from openflight.camera import club_motion  # noqa: E402
from openflight.camera.clubpose import (  # noqa: E402
    angles as ang,  # noqa: E402
    fit as fitmod,  # noqa: E402
    fusion,
    head_split,
)
from openflight.camera.clubpose.mesh import (  # noqa: E402
    TriangleMesh,
    load_club_mesh,
)
from openflight.camera.clubpose.projection import (  # noqa: E402
    WORLD_RIGHT,
    CameraPreset,
    _project,
    _ray_world,
)

DEFAULT_SESSION = Path.home() / (
    "Downloads/openflight_session_20260825_181734_filtered/"
    "openflight_session_20260825_181734_filtered"
)

# --- rig geometry, all FLOOR-referenced (see the world-frame note above) ---
LENS_HEIGHT_MM = 203.2  # kiosk log `mount_height_m`
BALL_CENTRE_HEIGHT_MM = 40.0  # ball on the mat, from the tape chain
CAMERA_DOWNRANGE_MM = 1567.887  # keeps `measured_camera()`'s own x

SPEED_OF_SOUND_M_S = 343.0
MIC_PATH_M = 1.575
FIRST_FIT_FRAME = 62
NEAR_CONTACT_FRAMES = 3.0
MIN_HEAD_PX = 60
BALL_VETO_DIAMETERS = 0.75
COMPONENT_RADIUS_PX = 200.0
BACKGROUND_SLICE = slice(8, 56)
MOVING_SIGMA = 4.0
MOVING_FLOOR = 18.0
MPH_TO_MS = 0.44704
SWING_RADIUS_M = (1.4, 1.8)
NOMINAL_RADIUS_M = 1.6
POSE_HALF_WIDTH_DEG = 30.0
POSE_STEP_DEG = 10.0

# Static catalogue lofts for the two clubs in this session, for the
# 7i-vs-9i read only. Nothing is fitted against them.
STATIC_LOFT_BY_CLUB = {"7-iron": 33.1, "9-iron": 41.0}

ANGLE_KEYS = ("dynamic_loft_deg", "face_angle_deg", "lie_deg", "sole_tilt_deg")


# --------------------------------------------------------------------------
# camera: pitch and lateral offset SOLVED from the teed ball
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SolvedCamera(CameraPreset):
    """A camera whose boresight is an explicit pitch, not "aim at the origin".

    `CameraPreset.rotation_world_to_camera` derives the orientation by pointing
    the camera at the world origin. That is an assumption, and on this rig it is
    wrong by 46.8 px. Overriding the property is the whole reason this subclass
    exists; every other field is inherited unchanged.
    """

    pitch_deg: float = 0.0
    roll_deg: float = 0.0

    @property
    def rotation_world_to_camera(self) -> np.ndarray:
        """Rows are the camera's right, down and forward axes in world mm.

        Pitch rotates the boresight about world-right; negative looks DOWN.
        Roll spins the image about the boresight and DEFAULTS TO ZERO -- the
        fits use zero and the mat/wall boundary is measured separately rather
        than the assumption being made silently. It is a parameter here only so
        the sensitivity run can price the boundary tilt that was measured.
        """
        pitch = math.radians(float(self.pitch_deg))
        forward = np.array([math.cos(pitch), 0.0, math.sin(pitch)])
        right = np.asarray(WORLD_RIGHT, dtype=float)
        down = np.cross(right, forward)
        down /= np.linalg.norm(down)
        roll = math.radians(float(self.roll_deg))
        if roll:
            cos, sin = math.cos(roll), math.sin(roll)
            right, down = cos * right + sin * down, -sin * right + cos * down
        rotation = np.stack([right, down, forward])
        rotation.flags.writeable = False
        return rotation


def solve_pitch_deg(
    ball_v_px: float,
    camera: CameraPreset,
    *,
    downrange_mm: float = CAMERA_DOWNRANGE_MM,
    lens_height_mm: float = LENS_HEIGHT_MM,
    ball_height_mm: float = BALL_CENTRE_HEIGHT_MM,
) -> float:
    """Camera pitch that puts the teed ball on its observed pixel ROW.

    With the boresight pitched by ``p`` about world-right, the ball at
    downrange ``D`` and ``h`` below the lens lands at
    ``v = cy + fy (D sin p + h cos p) / (D cos p - h sin p)``. Writing
    ``k = (v - cy) / fy`` and solving gives ``tan p = (kD - h) / (D + kh)``.

    The ball's LATERAL position does not enter: ``down`` has no y component
    under a pure pitch, so the row is a clean measurement of pitch alone.
    """
    height_below = float(lens_height_mm) - float(ball_height_mm)
    scale = (float(ball_v_px) - camera.cy) / camera.fy
    return math.degrees(
        math.atan2(scale * downrange_mm - height_below, downrange_mm + scale * height_below)
    )


def solve_lateral_offset_mm(
    ball_u_px: float,
    pitch_deg: float,
    camera: CameraPreset,
    *,
    downrange_mm: float = CAMERA_DOWNRANGE_MM,
    lens_height_mm: float = LENS_HEIGHT_MM,
    ball_height_mm: float = BALL_CENTRE_HEIGHT_MM,
) -> float:
    """Camera y offset that puts the teed ball on its observed pixel COLUMN.

    The tape chain records a -60.325 mm lateral camera offset that
    `projection.camera_center_world` drops, hard-coding ``y = 0``. This solves
    it back out of the image. Per-shot scatter here is partly the tee being
    replaced between shots, which is why the session MEDIAN is what gets used.
    """
    pitch = math.radians(float(pitch_deg))
    height_below = float(lens_height_mm) - float(ball_height_mm)
    forward_mm = downrange_mm * math.cos(pitch) - height_below * math.sin(pitch)
    return -(float(ball_u_px) - camera.cx) * forward_mm / camera.fx


def build_camera(
    pitch_deg: float,
    lateral_mm: float,
    *,
    roll_deg: float = 0.0,
    lens_height_mm: float = LENS_HEIGHT_MM,
    downrange_mm: float = CAMERA_DOWNRANGE_MM,
) -> SolvedCamera:
    """The session camera: measured intrinsics, solved extrinsics, floor datum."""
    base = fitmod.measured_camera()
    return SolvedCamera(
        **{
            field_.name: getattr(base, field_.name)
            for field_ in dataclasses.fields(CameraPreset)
            if field_.name != "center_world_mm"
        }
        | {
            "name": "SOLVED_FROM_BALL",
            "center_world_mm": (-float(downrange_mm), float(lateral_mm), float(lens_height_mm)),
        },
        pitch_deg=float(pitch_deg),
        roll_deg=float(roll_deg),
    )


def ball_residual_px(camera: SolvedCamera, ball: club_motion.ReferenceBall) -> tuple[float, float]:
    """Signed (u, v) error of the projected ball centre against the detected one."""
    uv, _front = _project(np.array([[0.0, 0.0, BALL_CENTRE_HEIGHT_MM]]), camera)
    return float(uv[0][0] - ball.x), float(uv[0][1] - ball.y)


def horizon_roll_deg(frame: np.ndarray) -> float | None:
    """Tilt of the mat/wall boundary in one pre-swing frame, degrees.

    Roll is TREATED as zero by `SolvedCamera`; this checks that rather than
    assuming it. The mat is much brighter than the wall behind it, so the
    boundary is the strongest horizontal gradient row in each column.
    """
    blurred = cv2.GaussianBlur(frame.astype(np.float32), (5, 5), 0)
    gradient = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=5)
    height, width = gradient.shape
    band = slice(int(height * 0.45), int(height * 0.85))
    columns, rows = [], []
    for column in range(10, width - 10, 4):
        strip = gradient[band, column]
        index = int(np.argmax(strip))
        if strip[index] <= 0.0:
            continue
        columns.append(column)
        rows.append(band.start + index)
    if len(columns) < 20:
        return None
    # Median-of-slopes is immune to the columns where a post or the ball
    # outvotes the mat edge; a least-squares line here is not.
    columns_a = np.asarray(columns, dtype=float)
    rows_a = np.asarray(rows, dtype=float)
    rng = np.random.default_rng(0)
    pairs = rng.integers(0, len(columns_a), (2000, 2))
    first, second = pairs[:, 0], pairs[:, 1]
    usable = columns_a[first] != columns_a[second]
    if not usable.any():
        return None
    slopes = (rows_a[second][usable] - rows_a[first][usable]) / (
        columns_a[second][usable] - columns_a[first][usable]
    )
    return math.degrees(math.atan(float(np.median(slopes))))


# --------------------------------------------------------------------------
# template: clip the hosel the observed mask never contains
# --------------------------------------------------------------------------


def hosel_cut_mm(
    mesh: TriangleMesh, axes, *, bin_mm: float = 5.0, neck_extent_mm: float = 15.0
) -> float:
    """Where along the shaft axis the head body ends and the hosel begins.

    Walking up ``axes.shaft_local`` the perpendicular extent of the mesh falls
    from ~58 mm across the head to a flat ~12.5 mm plateau: the hosel bore.
    The neck is the first bin past the widest section whose extent drops below
    ``neck_extent_mm`` and never recovers.
    """
    along = mesh.vertices_local_mm @ axes.shaft_local
    perpendicular = mesh.vertices_local_mm - np.outer(along, axes.shaft_local)
    edges = np.arange(float(along.min()), float(along.max()) + bin_mm, bin_mm)
    centres, extents = [], []
    for low, high in zip(edges[:-1], edges[1:]):
        inside = (along >= low) & (along < high)
        if int(inside.sum()) < 5:
            continue
        centres.append(low)
        extents.append(float(np.ptp(perpendicular[inside], axis=0).max()))
    extents_a = np.asarray(extents)
    widest = int(np.argmax(extents_a))
    for index in range(widest, len(extents_a)):
        if extents_a[index] < neck_extent_mm and np.all(extents_a[index:] < neck_extent_mm):
            return float(centres[index])
    return float(centres[-1])


def clip_hosel(mesh: TriangleMesh, axes, cut_mm: float) -> TriangleMesh:
    """Drop every face lying entirely past the hosel neck.

    The observed head mask is what `head_split.split_head` returns, and that
    cuts the hosel neck off. Rendering a template that still HAS the hosel
    compares two different objects, and IoU then pays for hiding a limb the
    observation never had.
    """
    along = mesh.vertices_local_mm @ axes.shaft_local
    keep_face = np.any(along[mesh.faces] <= float(cut_mm), axis=1)
    faces = mesh.faces[keep_face]
    used = np.unique(faces)
    remap = np.full(len(mesh.vertices_local_mm), -1, dtype=np.int32)
    remap[used] = np.arange(len(used), dtype=np.int32)
    return TriangleMesh(
        vertices_local_mm=mesh.vertices_local_mm[used],
        faces=remap[faces],
        source_uid=f"{mesh.source_uid}+hosel_clipped_{cut_mm:.1f}mm",
        source_sha256=mesh.source_sha256,
        club_handedness=mesh.club_handedness,
        reflect_into_world_frame=mesh.reflect_into_world_frame,
    )


# --------------------------------------------------------------------------
# session IO
# --------------------------------------------------------------------------


@dataclass
class Shot:
    number: int
    club: str
    directory: Path
    frames: np.ndarray
    times_s: np.ndarray
    pre_trigger: int
    fps: float
    club_speed_mph: float
    range_rate_ms: float

    @property
    def name(self) -> str:
        return f"shot_{self.number:03d}_{self.club}"

    @property
    def contact_frame(self) -> float:
        """Impact frame index: the trigger minus the acoustic flight time."""
        return self.pre_trigger - (MIC_PATH_M / SPEED_OF_SOUND_M_S) * self.fps

    @property
    def contact_time_s(self) -> float:
        return float(np.interp(self.contact_frame, np.arange(len(self.times_s)), self.times_s))


def read_shot_rows(session: Path) -> dict[int, dict[str, str]]:
    with open(session / "shots.csv", newline="", encoding="utf-8") as handle:
        return {int(row["shot_number"]): row for row in csv.DictReader(handle)}


def load_shot(session: Path, row: dict[str, str]) -> Shot:
    number = int(row["shot_number"])
    directory = next((session / "shots").glob(f"shot_{number:03d}_*"))
    payload = np.load(directory / "frames.npz")
    # The capture ran with mirror_horizontal=True; undo it before anything
    # geometric touches these pixels.
    frames = np.ascontiguousarray(payload["frames"][:, :, ::-1])
    times = payload["sensor_timestamp_ns"].astype(float) * 1e-9
    times -= times[0]
    metadata = json.loads((directory / "camera_metadata.json").read_text())
    return Shot(
        number=number,
        club=row["club"],
        directory=directory,
        frames=frames,
        times_s=times,
        pre_trigger=int(metadata["pre_trigger_frames"]),
        fps=1.0 / float(np.median(np.diff(times))),
        club_speed_mph=float(row["club_speed_mph"]),
        range_rate_ms=float(row["iwr_club_path_range_rate_ms"]),
    )


# --------------------------------------------------------------------------
# masks
# --------------------------------------------------------------------------


@dataclass
class MaskSet:
    ball: club_motion.ReferenceBall
    heads: dict[int, np.ndarray] = field(default_factory=dict)
    shafts: dict[int, np.ndarray] = field(default_factory=dict)
    moving: dict[int, np.ndarray] = field(default_factory=dict)
    rejected: dict[int, str] = field(default_factory=dict)


def build_masks(shot: Shot, first: int = FIRST_FIT_FRAME) -> MaskSet:
    """Head silhouettes for the pre-impact frames of one shot."""
    frames = shot.frames.astype(np.float32)
    ball = club_motion.detect_reference_ball(shot.frames)
    background = np.median(frames[BACKGROUND_SLICE], axis=0)
    threshold = np.maximum(MOVING_SIGMA * frames[BACKGROUND_SLICE].std(axis=0), MOVING_FLOOR)

    height, width = background.shape
    yy, xx = np.mgrid[0:height, 0:width]
    veto = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= (BALL_VETO_DIAMETERS * ball.diameter_px) ** 2

    out = MaskSet(ball=ball)
    for index in range(first, int(math.floor(shot.contact_frame)) + 1):
        moving = (np.abs(frames[index] - background) > threshold) & ~veto
        out.moving[index] = moving
        labels, count = ndimage.label(moving)
        if count == 0:
            out.rejected[index] = "no_moving_component"
            continue
        best, best_area = None, 0
        for label in range(1, count + 1):
            component = labels == label
            area = int(component.sum())
            ys, xs = np.nonzero(component)
            if math.hypot(xs.mean() - ball.x, ys.mean() - ball.y) > COMPONENT_RADIUS_PX:
                continue
            if area > best_area:
                best, best_area = component, area
        if best is None:
            out.rejected[index] = "no_component_near_ball"
            continue
        split = head_split.split_head(best.astype(np.uint8))
        if split is None:
            out.rejected[index] = "no_head_body_in_component"
            continue
        head, shaft = split
        if int(head.sum()) < MIN_HEAD_PX:
            out.rejected[index] = f"head_only_{int(head.sum())}px"
            continue
        out.heads[index] = head.astype(bool)
        out.shafts[index] = shaft.astype(bool)
    return out


def mask_sheet(shot: Shot, masks: MaskSet, path: Path) -> None:
    """One panel per candidate frame: the frame, the head, the shaft.

    Rendered so a human can LOOK before any angle is measured. Shadow and
    motion blur contaminate the frames nearest contact and only the eye
    catches that.
    """
    indices = sorted(set(masks.moving))
    columns = 5
    rows = int(math.ceil(len(indices) / columns))
    figure, axes = plt.subplots(rows, columns, figsize=(3.1 * columns, 2.2 * rows))
    axes = np.atleast_1d(axes).ravel()
    for axis, index in zip(axes, indices):
        axis.imshow(shot.frames[index], cmap="gray", vmin=0, vmax=255)
        overlay = np.zeros(shot.frames[index].shape + (4,), dtype=float)
        if index in masks.heads:
            overlay[masks.heads[index]] = (1.0, 0.2, 0.1, 0.55)
        if index in masks.shafts:
            overlay[masks.shafts[index]] = (0.1, 0.6, 1.0, 0.45)
        axis.imshow(overlay)
        axis.add_patch(
            plt.Circle(
                (masks.ball.x, masks.ball.y),
                masks.ball.diameter_px / 2,
                fill=False,
                color="yellow",
                lw=0.8,
            )
        )
        note = (
            f"{int(masks.heads[index].sum())} px"
            if index in masks.heads
            else masks.rejected.get(index, "?")
        )
        axis.set_title(f"f{index}  {note}", fontsize=7)
        axis.axis("off")
    for axis in axes[len(indices) :]:
        axis.axis("off")
    figure.suptitle(
        f"{shot.name}  contact f_c={shot.contact_frame:.2f}  "
        f"ball d={masks.ball.diameter_px:.1f} px",
        fontsize=9,
    )
    figure.tight_layout()
    figure.savefig(path, dpi=100)
    plt.close(figure)


# --------------------------------------------------------------------------
# fitting
# --------------------------------------------------------------------------


def centroid_uv(mask: np.ndarray) -> tuple[float, float]:
    ys, xs = np.nonzero(mask)
    return float(xs.mean()), float(ys.mean())


def frame_ranges(shot: Shot, frames: list[int], camera: SolvedCamera) -> dict[int, float]:
    """Per-frame camera-to-clubhead range from the radar's range rate.

    `fusion.ranges_from_radar` anchored at the camera-to-BALL-CENTRE distance
    under this script's floor datum, which is a few mm from `projection`'s
    1581 mm because the datum moved. The radar's own club track was checked and
    supplies NO curvature -- its `BallTrack.quad_bins` is None for club tracks,
    so `track.range_at` is exactly this straight line. The ramp IS the track.
    """
    anchor = float(
        np.linalg.norm(camera.center_world - np.array([0.0, 0.0, BALL_CENTRE_HEIGHT_MM]))
    )
    values = fusion.ranges_from_radar(
        shot.times_s[frames], shot.contact_time_s, shot.range_rate_ms, ball_range_mm=anchor
    )
    return {int(frame): float(value) for frame, value in zip(frames, values)}


def render_pose(mesh, centre_world, pose, camera) -> np.ndarray | None:
    return fitmod.render_mask_6dof(mesh, centre_world, pose[0], pose[1], pose[2], camera)


def fit_sequence_centred(
    mesh,
    masks: dict[int, np.ndarray],
    camera,
    square,
    ranges: dict[int, float],
    times: np.ndarray,
    *,
    half_width_deg: float = POSE_HALF_WIDTH_DEG,
    step_deg: float = POSE_STEP_DEG,
    smooth_deg: float = 70.0,
    independent: bool = False,
) -> dict[int, dict]:
    """`fit.fit_sequence`, but with grid AND bounds centred on `square_pose()`.

    The shipped `fit_sequence` cannot be used directly: its bounds are absolute
    (``abs(yaw) > YAW_BOUND_DEG``) around the triad origin, which is a backwards
    club, so the corrected square pose is out of bounds before the search
    starts. Everything else -- the smoothness penalty, the refinement schedule,
    the pinned per-frame range -- is the shipped behaviour, and the penalty
    itself is imported rather than reimplemented.
    """
    offsets = np.arange(-half_width_deg, half_width_deg + 1e-9, step_deg)
    out: dict[int, dict] = {}
    previous = None
    for index in sorted(masks):
        observed = masks[index].astype(bool)
        if int(observed.sum()) < 40:
            continue
        ray = _ray_world(np.array(centroid_uv(observed), dtype=float), camera)
        centre = camera.center_world + ray * ranges[index]
        rng = ranges[index]

        def score(yaw, pitch, roll):
            if (
                max(abs(yaw - square[0]), abs(pitch - square[1]), abs(roll - square[2]))
                > half_width_deg
            ):
                return -1.0
            rendered = render_pose(mesh, centre, (yaw, pitch, roll), camera)
            if rendered is None:
                return -1.0
            return fitmod.iou(rendered, observed) - fitmod._smoothness_penalty(
                None if independent else previous,
                rng,
                yaw,
                pitch,
                roll,
                smooth_deg,
                300.0,
                penalise_range=False,
            )

        best = (-1.0, None)
        for d_yaw in offsets:
            for d_pitch in offsets:
                for d_roll in offsets:
                    pose = (square[0] + d_yaw, square[1] + d_pitch, square[2] + d_roll)
                    value = score(*pose)
                    if value > best[0]:
                        best = (value, pose)
        if best[1] is None:
            continue
        yaw, pitch, roll = best[1]
        step = [8.0, 8.0, 8.0]
        for _ in range(4):
            improved = False
            for axis_index, delta in enumerate(step):
                for direction in (-delta, delta):
                    candidate = [yaw, pitch, roll]
                    candidate[axis_index] += direction
                    value = score(*candidate)
                    if value > best[0]:
                        best = (value, tuple(candidate))
                        yaw, pitch, roll = candidate
                        improved = True
            if not improved:
                step = [value / 2.0 for value in step]
        rendered = render_pose(mesh, centre, (yaw, pitch, roll), camera)
        previous = {
            "range_mm": rng,
            "yaw_deg": yaw,
            "pitch_deg": pitch,
            "roll_deg": roll,
            "iou": fitmod.iou(rendered, observed) if rendered is not None else 0.0,
        }
        out[index] = dict(previous, mask=rendered, centre_world=centre.tolist())
    return out


# --------------------------------------------------------------------------
# overlays
# --------------------------------------------------------------------------


LANDMARK_COLOURS = {"heel": "#ffd400", "toe": "#00e5ff", "hosel": "#ff36c8"}


def landmark_indices(mesh, axes) -> dict[str, int]:
    """Extreme vertices along the club's own axes: heel, toe, hosel."""
    local = mesh.vertices_local_mm
    heel = local @ axes.heel_toe_local
    return {
        "heel": int(np.argmax(heel)),
        "toe": int(np.argmin(heel)),
        "hosel": int(np.argmax(local @ ang.MESH_HOSEL_AXIS_LOCAL)),
    }


def overlay_sheet(
    shot: Shot, masks: MaskSet, fits: dict[int, dict], mesh, camera, axes, path: Path
) -> None:
    indices = sorted(fits)
    if not indices:
        return
    columns = 5
    rows = int(math.ceil(len(indices) / columns))
    figure, plots = plt.subplots(rows, columns, figsize=(3.1 * columns, 2.4 * rows))
    plots = np.atleast_1d(plots).ravel()
    landmarks = landmark_indices(mesh, axes)
    for plot, index in zip(plots, indices):
        record = fits[index]
        plot.imshow(shot.frames[index], cmap="gray", vmin=0, vmax=255)
        for mask, colour, width in (
            (masks.heads[index], "#ff3b30", 0.9),
            (record.get("mask"), "#34c759", 1.1),
        ):
            if mask is None:
                continue
            for contour in cv2.findContours(
                np.asarray(mask).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
            )[0]:
                points = contour.reshape(-1, 2)
                plot.plot(points[:, 0], points[:, 1], color=colour, lw=width)
        centre = np.asarray(record["centre_world"], dtype=float)
        basis = ang.basis_from_angles(record["yaw_deg"], record["pitch_deg"], record["roll_deg"])
        uv, _ = _project(
            np.array([centre + basis @ mesh.vertices_local_mm[i] for i in landmarks.values()]),
            camera,
        )
        for (name, _), (u, v) in zip(landmarks.items(), uv):
            plot.plot([u], [v], marker="o", ms=4, color=LANDMARK_COLOURS[name])
        angles = record["angles"]
        plot.set_title(
            f"f{index} IoU {record['iou']:.2f} z{centre[2]:.0f}  "
            f"L{angles['dynamic_loft_deg']:.0f} F{angles['face_angle_deg']:.0f} "
            f"lie{angles['lie_deg']:.0f} s{angles['sole_tilt_deg']:.0f}"
            + ("" if record["in_envelope"] else "  OUT"),
            fontsize=7,
        )
        plot.axis("off")
    for plot in plots[len(indices) :]:
        plot.axis("off")
    figure.suptitle(f"{shot.name}  red=observed head  green=fitted clipped template", fontsize=9)
    figure.tight_layout()
    figure.savefig(path, dpi=100)
    plt.close(figure)


# --------------------------------------------------------------------------
# per-shot driver
# --------------------------------------------------------------------------


def rotation_rate_deg_s(fits: dict[int, dict], times: np.ndarray) -> list[float]:
    indices = sorted(fits)
    rates = []
    for first, second in zip(indices, indices[1:]):
        relative = (
            ang.basis_from_angles(
                fits[second]["yaw_deg"], fits[second]["pitch_deg"], fits[second]["roll_deg"]
            )
            @ ang.basis_from_angles(
                fits[first]["yaw_deg"], fits[first]["pitch_deg"], fits[first]["roll_deg"]
            ).T
        )
        angle = math.degrees(
            math.acos(float(np.clip((float(np.trace(relative)) - 1.0) / 2.0, -1.0, 1.0)))
        )
        span = float(times[second] - times[first])
        if span > 0:
            rates.append(angle / span)
    return rates


def annotate(
    fits: dict[int, dict],
    mesh,
    camera,
    masks: MaskSet,
    axes,
    square,
    shot: Shot,
    ranges: dict[int, float],
) -> None:
    """Attach delivered angles and the search/geometry diagnostics in place."""
    for index, record in fits.items():
        pose = (record["yaw_deg"], record["pitch_deg"], record["roll_deg"])
        angles = ang.delivered_angles(ang.basis_from_angles(*pose), axes=axes)
        record["angles"] = angles
        record["in_envelope"] = bool(ang.in_envelope(angles))
        record["offset_from_square_deg"] = [float(pose[k] - square[k]) for k in range(3)]
        record["rail_pinned"] = bool(
            max(abs(pose[k] - square[k]) for k in range(3)) >= POSE_HALF_WIDTH_DEG - 0.05
        )
        record["frames_before_contact"] = float(shot.contact_frame - index)
        record["near_contact"] = bool(record["frames_before_contact"] <= NEAR_CONTACT_FRAMES)
        record["centre_z_mm"] = float(record["centre_world"][2])
        rendered = render_pose(
            mesh, np.asarray(record["centre_world"], dtype=float), square, camera
        )
        record["iou_at_square_pose"] = (
            0.0 if rendered is None else fitmod.iou(rendered, masks.heads[index])
        )
        record["square_render_px"] = 0 if rendered is None else int(rendered.sum())
        record["observed_px"] = int(masks.heads[index].sum())
        record["render_over_observed"] = (
            record["square_render_px"] / record["observed_px"] if record["observed_px"] else None
        )


def process_shot(
    shot: Shot, mesh, camera: SolvedCamera, axes, out: Path, sensitivity: bool
) -> dict:
    masks = build_masks(shot)
    mask_sheet(shot, masks, out / "sheets" / f"{shot.name}_masks.png")
    residual = ball_residual_px(camera, masks.ball)
    record: dict = {
        "shot": shot.number,
        "club": shot.club,
        "contact_frame": shot.contact_frame,
        "fps": shot.fps,
        "club_speed_mph": shot.club_speed_mph,
        "range_rate_ms": shot.range_rate_ms,
        "ball_px": {"x": masks.ball.x, "y": masks.ball.y, "diameter_px": masks.ball.diameter_px},
        "ball_residual_px": {"u": residual[0], "v": residual[1]},
        "candidate_frames": sorted(masks.moving),
        "kept_frames": sorted(masks.heads),
        "rejected_frames": {str(k): v for k, v in sorted(masks.rejected.items())},
        "head_px": {str(k): int(v.sum()) for k, v in sorted(masks.heads.items())},
    }
    if len(masks.heads) < 2:
        record["ok"] = False
        record["reason"] = "fewer_than_two_head_masks"
        return record

    square = ang.square_pose(axes=axes)
    frames = sorted(masks.heads)
    ranges = frame_ranges(shot, frames, camera)
    fits = fit_sequence_centred(
        mesh, {i: masks.heads[i] for i in frames}, camera, square, ranges, shot.times_s
    )
    annotate(fits, mesh, camera, masks, axes, square, shot, ranges)
    overlay_sheet(shot, masks, fits, mesh, camera, axes, out / "sheets" / f"{shot.name}_fit.png")
    independent = fit_sequence_centred(
        mesh,
        {i: masks.heads[i] for i in frames},
        camera,
        square,
        ranges,
        shot.times_s,
        independent=True,
    )
    annotate(independent, mesh, camera, masks, axes, square, shot, ranges)
    overlay_sheet(
        shot,
        masks,
        independent,
        mesh,
        camera,
        axes,
        out / "sheets" / f"{shot.name}_fit_independent.png",
    )

    rates = rotation_rate_deg_s(fits, shot.times_s)
    speed_ms = shot.club_speed_mph * MPH_TO_MS
    record["ok"] = True
    record["range_mm_by_frame"] = {str(k): v for k, v in ranges.items()}
    record["fits"] = {
        str(index): {key: value for key, value in fits[index].items() if key != "mask"}
        for index in sorted(fits)
    }
    record["independent_fits"] = {
        str(index): {key: value for key, value in independent[index].items() if key != "mask"}
        for index in sorted(independent)
    }
    record["min_centre_z_mm"] = min((fits[i]["centre_z_mm"] for i in fits), default=None)
    record["rotation"] = {
        "fitted_deg_s": rates,
        "median_fitted_deg_s": float(np.median(rates)) if rates else None,
        "omega_v_over_r_deg_s": {
            f"r={radius}": math.degrees(speed_ms / radius) for radius in SWING_RADIUS_M
        },
        "omega_nominal_deg_s": math.degrees(speed_ms / NOMINAL_RADIUS_M),
        "ratio_nominal": (
            float(np.median(rates)) / math.degrees(speed_ms / NOMINAL_RADIUS_M) if rates else None
        ),
    }
    if sensitivity:
        record["sensitivity"] = sensitivity_record(
            shot, masks, mesh, camera, axes, square, frames, fits
        )
    return record


def sensitivity_record(shot, masks, mesh, camera, axes, square, frames, baseline) -> dict:
    """How far the delivered angles move for a wrong pitch or lens height.

    Both perturbations are of the CAMERA, not the club, so any angle that
    swings here is an angle the fit is reading off the rig rather than the
    silhouette.
    """
    reference = {
        key: float(np.median([baseline[i]["angles"][key] for i in baseline])) for key in ANGLE_KEYS
    }
    out = {"reference_median": reference, "cases": {}}
    cases = {
        "pitch_plus_1deg": build_camera(camera.pitch_deg + 1.0, camera.center_world_mm[1]),
        "pitch_minus_1deg": build_camera(camera.pitch_deg - 1.0, camera.center_world_mm[1]),
        "lens_plus_25mm": build_camera(
            camera.pitch_deg, camera.center_world_mm[1], lens_height_mm=LENS_HEIGHT_MM + 25.0
        ),
        "lens_minus_25mm": build_camera(
            camera.pitch_deg, camera.center_world_mm[1], lens_height_mm=LENS_HEIGHT_MM - 25.0
        ),
        # The measured mat/wall boundary tilt. Not applied to the primary fit,
        # priced here because 3.2 deg is far too big to leave unquantified.
        "roll_plus_measured_3.2deg": build_camera(
            camera.pitch_deg, camera.center_world_mm[1], roll_deg=3.2
        ),
    }
    for name, perturbed in cases.items():
        ranges = frame_ranges(shot, frames, perturbed)
        fits = fit_sequence_centred(
            mesh, {i: masks.heads[i] for i in frames}, perturbed, square, ranges, shot.times_s
        )
        if not fits:
            continue
        out["cases"][name] = {
            key: float(
                np.median(
                    [
                        ang.delivered_angles(
                            ang.basis_from_angles(
                                fits[i]["yaw_deg"], fits[i]["pitch_deg"], fits[i]["roll_deg"]
                            ),
                            axes=axes,
                        )[key]
                        for i in fits
                    ]
                )
                - reference[key]
            )
            for key in ANGLE_KEYS
        }
    return out


# --------------------------------------------------------------------------
# summaries
# --------------------------------------------------------------------------


def spread(values: list[float]) -> float:
    return float(max(values) - min(values)) if len(values) > 1 else 0.0


def summary_rows(records: list[dict], *, near_contact_only: bool, key: str = "fits") -> list[dict]:
    rows = []
    for record in records:
        if not record.get("ok"):
            continue
        fits = record[key]
        keys = [
            k for k in sorted(fits, key=int) if not near_contact_only or fits[k]["near_contact"]
        ]
        if not keys:
            continue
        per = {key: [fits[i]["angles"][key] for i in keys] for key in ANGLE_KEYS}
        rows.append(
            {
                "shot": record["shot"],
                "club": record["club"],
                "scope": ("near_contact" if near_contact_only else "all_frames")
                + ("" if key == "fits" else "_independent"),
                "n_frames": len(keys),
                "n_in_envelope": sum(fits[i]["in_envelope"] for i in keys),
                "n_rail_pinned": sum(fits[i]["rail_pinned"] for i in keys),
                "mean_iou": float(np.mean([fits[i]["iou"] for i in keys])),
                "mean_iou_at_square": float(np.mean([fits[i]["iou_at_square_pose"] for i in keys])),
                "min_centre_z_mm": float(min(fits[i]["centre_z_mm"] for i in keys)),
                "render_over_observed_earliest": fits[keys[0]]["render_over_observed"],
                "render_over_observed_latest": fits[keys[-1]]["render_over_observed"],
                **{f"median_{key}": float(np.median(per[key])) for key in per},
                **{f"spread_{key}": spread(per[key]) for key in per},
                "median_rot_deg_s": record["rotation"]["median_fitted_deg_s"],
                "omega_nominal_deg_s": record["rotation"]["omega_nominal_deg_s"],
                "ratio_nominal": record["rotation"]["ratio_nominal"],
                "ball_residual_u_px": record["ball_residual_px"]["u"],
                "ball_residual_v_px": record["ball_residual_px"]["v"],
            }
        )
    return rows


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {}
    total = sum(row["n_frames"] for row in rows)
    summary = {
        "n_shots": len(rows),
        "n_frames": total,
        "fraction_frames_in_envelope": sum(r["n_in_envelope"] for r in rows) / total,
        "shots_with_any_in_envelope": sum(1 for r in rows if r["n_in_envelope"] > 0),
        "shots_all_frames_in_envelope": sum(1 for r in rows if r["n_in_envelope"] == r["n_frames"]),
        "fraction_frames_rail_pinned": sum(r["n_rail_pinned"] for r in rows) / total,
        "mean_iou": float(np.mean([r["mean_iou"] for r in rows])),
        "mean_iou_at_square_pose": float(np.mean([r["mean_iou_at_square"] for r in rows])),
        "shots_where_square_pose_wins_on_iou": sum(
            1 for r in rows if r["mean_iou_at_square"] >= r["mean_iou"]
        ),
        "shots_with_underground_centre": sum(1 for r in rows if r["min_centre_z_mm"] < 0.0),
        "min_centre_z_mm_overall": float(min(r["min_centre_z_mm"] for r in rows)),
    }
    for key in ANGLE_KEYS:
        summary[f"median_within_shot_spread_{key}"] = float(
            np.median([r[f"spread_{key}"] for r in rows])
        )
        summary[f"between_shot_sd_{key}"] = float(np.std([r[f"median_{key}"] for r in rows]))
    ratios = [r["ratio_nominal"] for r in rows if r["ratio_nominal"] is not None]
    summary["median_rotation_ratio"] = float(np.median(ratios)) if ratios else None
    for club in sorted({r["club"] for r in rows}):
        club_rows = [r for r in rows if r["club"] == club]
        summary[f"median_loft_{club}"] = float(
            np.median([r["median_dynamic_loft_deg"] for r in club_rows])
        )
        summary[f"n_{club}"] = len(club_rows)
    if "median_loft_7-iron" in summary and "median_loft_9-iron" in summary:
        summary["loft_gap_9i_minus_7i_deg"] = (
            summary["median_loft_9-iron"] - summary["median_loft_7-iron"]
        )
        summary["static_loft_gap_deg"] = (
            STATIC_LOFT_BY_CLUB["9-iron"] - STATIC_LOFT_BY_CLUB["7-iron"]
        )
    for when in ("earliest", "latest"):
        values = [
            r[f"render_over_observed_{when}"]
            for r in rows
            if r[f"render_over_observed_{when}"] is not None
        ]
        summary[f"median_render_over_observed_{when}_frame"] = (
            float(np.median(values)) if values else None
        )
    return summary


def plot_summary(rows: list[dict], out: Path) -> None:
    if not rows:
        return
    figure, axes = plt.subplots(1, 4, figsize=(15, 4))
    for axis, key in zip(axes, ANGLE_KEYS):
        for club, colour in (("7-iron", "#0a84ff"), ("9-iron", "#ff9f0a")):
            club_rows = [row for row in rows if row["club"] == club]
            if not club_rows:
                continue
            axis.errorbar(
                [row["shot"] for row in club_rows],
                [row[f"median_{key}"] for row in club_rows],
                yerr=[row[f"spread_{key}"] / 2 for row in club_rows],
                fmt="o",
                ms=4,
                capsize=2,
                color=colour,
                label=club,
            )
        low, high = ang.ENVELOPE[key]
        axis.axhspan(low, high, color="green", alpha=0.08)
        axis.set_title(key, fontsize=9)
        axis.set_xlabel("shot")
        axis.legend(fontsize=7)
    figure.suptitle(
        "Per-shot median delivered angle, bar = within-shot spread; band = physical envelope",
        fontsize=10,
    )
    figure.tight_layout()
    figure.savefig(out / "angles_by_shot.png", dpi=130)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(6, 4))
    axis.scatter(
        [row["omega_nominal_deg_s"] for row in rows],
        [row["median_rot_deg_s"] or 0.0 for row in rows],
        c=["#0a84ff" if row["club"] == "7-iron" else "#ff9f0a" for row in rows],
    )
    limit = max(row["omega_nominal_deg_s"] for row in rows) * 1.2
    axis.plot([0, limit], [0, limit], "k--", lw=0.8, label="omega = v/r (r = 1.6 m)")
    axis.set_xlabel("omega = v/r  [deg/s]")
    axis.set_ylabel("fitted frame-to-frame rotation rate  [deg/s]")
    axis.legend(fontsize=8)
    figure.tight_layout()
    figure.savefig(out / "rotation_rate_vs_omega.png", dpi=130)
    plt.close(figure)


def plot_calibration(calib: list[dict], out: Path) -> None:
    figure, axes = plt.subplots(1, 3, figsize=(13, 3.8))
    axes[0].plot([c["shot"] for c in calib], [c["pitch_deg"] for c in calib], "o-")
    axes[0].axhline(
        float(np.median([c["pitch_deg"] for c in calib])),
        color="r",
        ls="--",
        label="session median",
    )
    axes[0].set_title("camera pitch solved from ball row", fontsize=9)
    axes[0].set_xlabel("shot")
    axes[0].set_ylabel("deg")
    axes[0].legend(fontsize=7)
    axes[1].plot([c["shot"] for c in calib], [c["lateral_mm"] for c in calib], "o-")
    axes[1].axhline(float(np.median([c["lateral_mm"] for c in calib])), color="r", ls="--")
    axes[1].axhline(-60.325, color="g", ls=":", label="taped -60.325 mm")
    axes[1].set_title("camera lateral offset solved from ball column", fontsize=9)
    axes[1].set_xlabel("shot")
    axes[1].set_ylabel("mm")
    axes[1].legend(fontsize=7)
    axes[2].scatter([c["residual_u_px"] for c in calib], [c["residual_v_px"] for c in calib])
    for radius in (1.0, 2.0):
        axes[2].add_patch(plt.Circle((0, 0), radius, fill=False, color="green", ls="--"))
    axes[2].axhline(0, color="k", lw=0.5)
    axes[2].axvline(0, color="k", lw=0.5)
    axes[2].set_title("ball residual, session-median camera", fontsize=9)
    axes[2].set_xlabel("u residual [px]")
    axes[2].set_ylabel("v residual [px]")
    axes[2].set_aspect("equal")
    figure.tight_layout()
    figure.savefig(out / "camera_calibration.png", dpi=130)
    plt.close(figure)


# --------------------------------------------------------------------------


def calibrate_session(session: Path, rows: dict[int, dict], wanted: list[int]) -> dict:
    """Solve camera pitch and lateral offset per shot, before fitting anything."""
    base = fitmod.measured_camera()
    per_shot, rolls = [], []
    for number in wanted:
        shot = load_shot(session, rows[number])
        ball = club_motion.detect_reference_ball(shot.frames)
        pitch = solve_pitch_deg(ball.y, base)
        lateral = solve_lateral_offset_mm(ball.x, pitch, base)
        roll = horizon_roll_deg(shot.frames[10])
        if roll is not None:
            rolls.append(roll)
        per_shot.append(
            {
                "shot": number,
                "club": shot.club,
                "ball_u_px": ball.x,
                "ball_v_px": ball.y,
                "ball_diameter_px": ball.diameter_px,
                "pitch_deg": pitch,
                "lateral_mm": lateral,
                "horizon_roll_deg": roll,
            }
        )
    median_pitch = float(np.median([c["pitch_deg"] for c in per_shot]))
    median_lateral = float(np.median([c["lateral_mm"] for c in per_shot]))
    camera = build_camera(median_pitch, median_lateral)
    for entry in per_shot:
        ball = club_motion.ReferenceBall(
            x=entry["ball_u_px"],
            y=entry["ball_v_px"],
            diameter_px=entry["ball_diameter_px"],
            area_px=0,
        )
        u_res, v_res = ball_residual_px(camera, ball)
        entry["residual_u_px"], entry["residual_v_px"] = u_res, v_res
    within = lambda key, limit: sum(  # noqa: E731
        1 for e in per_shot if abs(e[key]) <= limit
    )
    return {
        "per_shot": per_shot,
        "median_pitch_deg": median_pitch,
        "pitch_sd_deg": float(np.std([c["pitch_deg"] for c in per_shot])),
        "pitch_range_deg": [
            float(min(c["pitch_deg"] for c in per_shot)),
            float(max(c["pitch_deg"] for c in per_shot)),
        ],
        "median_lateral_mm": median_lateral,
        "lateral_sd_mm": float(np.std([c["lateral_mm"] for c in per_shot])),
        "taped_lateral_mm": -60.325,
        "median_horizon_roll_deg": float(np.median(rolls)) if rolls else None,
        "horizon_roll_range_deg": ([float(min(rolls)), float(max(rolls))] if rolls else None),
        "n_shots_within_2px_v": within("residual_v_px", 2.0),
        "n_shots_within_2px_u": within("residual_u_px", 2.0),
        "n_shots_within_2px_both": sum(
            1 for e in per_shot if abs(e["residual_u_px"]) <= 2.0 and abs(e["residual_v_px"]) <= 2.0
        ),
        "n_shots": len(per_shot),
        "rms_residual_u_px": float(np.sqrt(np.mean([e["residual_u_px"] ** 2 for e in per_shot]))),
        "rms_residual_v_px": float(np.sqrt(np.mean([e["residual_v_px"] ** 2 for e in per_shot]))),
        "camera_center_world_mm": list(camera.center_world_mm),
        "camera_ball_range_mm": float(
            np.linalg.norm(camera.center_world - np.array([0.0, 0.0, BALL_CENTRE_HEIGHT_MM]))
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, default=DEFAULT_SESSION)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--shots", type=int, nargs="*", default=None)
    parser.add_argument("--stage", choices=("all", "calib", "masks", "aggregate"), default="all")
    parser.add_argument(
        "--force", action="store_true", help="refit shots that already have a cached per-shot JSON"
    )
    parser.add_argument(
        "--recalibrate",
        action="store_true",
        help="re-solve the session camera instead of reusing the cached solve",
    )
    parser.add_argument(
        "--sensitivity",
        action="store_true",
        help="also refit with the camera pitch and lens height perturbed",
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "sheets").mkdir(exist_ok=True)
    # Per-shot JSON is written as each shot finishes so a long sweep can be run
    # in chunks and resumed; `--stage aggregate` then reads them back.
    shot_dir = args.out / "shots"
    shot_dir.mkdir(exist_ok=True)

    full_mesh, metadata, digest = load_club_mesh()
    axes = ang.club_axes(full_mesh)
    square = ang.square_pose(axes=axes)
    cut = hosel_cut_mm(full_mesh, axes)
    mesh = clip_hosel(full_mesh, axes, cut)
    print(f"mesh cache v{metadata['cache_version']} digest {digest[:16]} axes={axes.source}")
    print(f"square_pose = {np.round(square, 3).tolist()}")
    print(
        f"hosel cut at {cut:.1f} mm along shaft_local: "
        f"{len(full_mesh.faces)} -> {len(mesh.faces)} faces"
    )

    rows = read_shot_rows(args.session)
    # The camera is a property of the SESSION, not of whichever shots this
    # invocation happens to fit. Solving it from `--shots` would give a
    # different camera to every chunk of a chunked sweep, so it is always
    # solved over every shot and cached.
    session_shots = [n for n in sorted(rows) if n != 1]
    wanted = args.shots or session_shots

    calibration_path = args.out / "camera_calibration.json"
    if calibration_path.exists() and not args.recalibrate:
        calib = json.loads(calibration_path.read_text())
    else:
        calib = calibrate_session(args.session, rows, session_shots)
        calibration_path.write_text(json.dumps(calib, indent=2))
    plot_calibration(calib["per_shot"], args.out)
    print(
        f"camera pitch {calib['median_pitch_deg']:.3f} deg "
        f"(sd {calib['pitch_sd_deg']:.3f}, range {np.round(calib['pitch_range_deg'], 3).tolist()})"
    )
    print(f"camera lateral {calib['median_lateral_mm']:.1f} mm (taped -60.325)")
    print(f"horizon roll {calib['median_horizon_roll_deg']} deg")
    print(
        f"ball residual rms u={calib['rms_residual_u_px']:.2f} v={calib['rms_residual_v_px']:.2f} px; "
        f"within 2 px: v {calib['n_shots_within_2px_v']}/{calib['n_shots']}, "
        f"u {calib['n_shots_within_2px_u']}/{calib['n_shots']}"
    )
    if args.stage == "calib":
        return

    camera = build_camera(calib["median_pitch_deg"], calib["median_lateral_mm"])
    render_template_check(mesh, full_mesh, camera, square, axes, args.out)

    records = []
    for number in wanted:
        cached = shot_dir / f"shot_{number:03d}.json"
        if args.stage == "aggregate" or (cached.exists() and not args.force):
            if cached.exists():
                records.append(json.loads(cached.read_text()))
                continue
            if args.stage == "aggregate":
                continue
        start = time.time()
        shot = load_shot(args.session, rows[number])
        if args.stage == "masks":
            masks = build_masks(shot)
            mask_sheet(shot, masks, args.out / "sheets" / f"{shot.name}_masks.png")
            print(f"{shot.name}: kept {sorted(masks.heads)} rejected {masks.rejected}")
            continue
        record = process_shot(shot, mesh, camera, axes, args.out, args.sensitivity)
        record["seconds"] = round(time.time() - start, 1)
        cached.write_text(json.dumps(record, indent=2))
        records.append(record)
        fits = record.get("fits", {})
        print(
            f"{shot.name}: {len(record.get('kept_frames', []))} masks, "
            f"{sum(f['in_envelope'] for f in fits.values())}/{len(fits)} in envelope, "
            f"minz {record.get('min_centre_z_mm')}, {record['seconds']} s"
        )
    if args.stage == "masks":
        return
    if not records:
        print("no shot records; nothing to aggregate")
        return

    all_rows = summary_rows(records, near_contact_only=False)
    near_rows = summary_rows(records, near_contact_only=True)
    indep_rows = summary_rows(records, near_contact_only=False, key="independent_fits")
    with open(args.out / "summary.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list((all_rows or near_rows)[0]))
        writer.writeheader()
        writer.writerows(all_rows + near_rows + indep_rows)
    summary = {
        "camera": {k: v for k, v in calib.items() if k != "per_shot"},
        "hosel_cut_mm": cut,
        "square_pose": [float(v) for v in square],
        "all_frames": summarise(all_rows),
        "near_contact_only": summarise(near_rows),
        "all_frames_independent": summarise(indep_rows),
        "per_shot": all_rows + near_rows + indep_rows,
    }
    if args.sensitivity:
        summary["sensitivity"] = aggregate_sensitivity(records)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    (args.out / "results.json").write_text(
        json.dumps(
            {
                "mesh_digest": digest,
                "mesh_cache_version": metadata["cache_version"],
                "club_axes_source": axes.source,
                "square_pose": [float(v) for v in square],
                "hosel_cut_mm": cut,
                "camera": summary["camera"],
                "shots": records,
            },
            indent=2,
        )
    )
    plot_summary(all_rows, args.out)
    print(json.dumps({k: v for k, v in summary.items() if k != "per_shot"}, indent=2))


def aggregate_sensitivity(records: list[dict]) -> dict:
    cases: dict[str, dict[str, list[float]]] = {}
    for record in records:
        for name, deltas in record.get("sensitivity", {}).get("cases", {}).items():
            for key, value in deltas.items():
                cases.setdefault(name, {}).setdefault(key, []).append(value)
    return {
        name: {key: float(np.median(values)) for key, values in keys.items()}
        for name, keys in cases.items()
    }


def render_template_check(mesh, full_mesh, camera, square, axes, out: Path) -> None:
    """Render the clipped and unclipped templates once, side by side, and LOOK."""
    centre = np.array([0.0, 0.0, BALL_CENTRE_HEIGHT_MM])
    figure, plots = plt.subplots(1, 2, figsize=(9, 3.4))
    for plot, (name, candidate) in zip(
        plots, (("full mesh (hosel intact)", full_mesh), ("clipped template", mesh))
    ):
        rendered = render_pose(candidate, centre, square, camera)
        plot.imshow(rendered, cmap="gray")
        plot.set_title(f"{name}\n{int(rendered.sum())} px at square pose", fontsize=9)
        plot.axis("off")
    figure.suptitle("Template at square_pose, placed at the ball", fontsize=10)
    figure.tight_layout()
    figure.savefig(out / "template_check.png", dpi=130)
    plt.close(figure)


if __name__ == "__main__":
    main()
