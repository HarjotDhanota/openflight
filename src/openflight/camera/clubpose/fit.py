"""Fit the real 3D club mesh to a real camera frame.

Everything the POC has measured so far came from fitting the mesh to SYNTHETIC
silhouettes produced by the same mesh. This module points it at real pixels for
the first time, which is the actual thing under validation.

Three corrections were required before the existing machinery could be used at
all, because the preset that claimed to be the shipped camera -- the retired
`A0`, since removed from `camera_presets()` -- described a camera we do not have:

    A0 said          fx = 1033 px,  plate scale 0.656 px/mm,  range 1575 mm
    measured         fx = 466.7 px, plate scale 0.295 px/mm,  range ~1581 mm

`fx` follows from the NOMINAL datasheet lens (2.8 mm) over the effective pixel
pitch of the shipped 320x200 mode (3.0 um at 2x subsample = 6.0 um). It is not
a calibrated camera matrix: there is no distortion model, no separately
estimated principal point, and no independent fx/fy.

The RANGE was previously 1425 mm, from a 13.97 px ball. That is wrong. Across
the 21 correctly exposed shots of session 20260825_181734 the teed ball
measures 12.77 px, and the tape chain -- camera lens 203.2 mm above the floor
(kiosk log `mount_height_m`), ball centre 40 mm, radar slant tee range 1575 mm,
camera lateral offset -60.325 mm -- gives 1581 mm. The 13.97 px figure traces
to the capture that turned out 99.8 % clipped, where the ball bloomed.

That matters more than a 10 % scale error. Both `range_grid_mm` defaults below
spanned 1300-1550 and 1325-1525, so neither CONTAINED the true range. The local
refinement below is not bounded by the grid, so it could in principle climb out
-- but it hill-climbs greedily from the best COARSE pose, and that pose was
selected at the wrong depth. Reaching the truth was therefore left to chance
rather than to the search. See
the technical report's clubhead-range section.

The pose model here is the one the POC already uses: 3D centre plus roll about
the face normal. That is **4 degrees of freedom, not 6** - the face normal is
fixed by `FACE_NORMAL` rather than solved. Loft and lie are therefore baked into
the mesh's own frame and are not recovered. Anything reported here is a fit of
position and roll only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from openflight.camera.clubpose.head_split import clip_hosel as _clip_hosel
from openflight.camera.clubpose.mesh import rasterize_projected_triangles
from openflight.camera.clubpose.projection import (
    CAMERA_BALL_RANGE_MM,
    CAMERA_HEIGHT_ABOVE_BALL_MM,
    CAMERA_PITCH_DEG,
    CAMERA_ROLL_DEG,
    FACE_NORMAL,
    CameraPreset,
    _face_axes,
    _project,
    _ray_world,
    camera_center_world,
)

# Measured configuration of the shipped camera, from the real capture.
LENS_MM = 2.8
PITCH_UM = 3.0
SUBSAMPLE = 2
FOCAL_PX = LENS_MM / (PITCH_UM * SUBSAMPLE * 1e-3)  # 466.7
# The tape chain lives in `projection.py`, which also derives the camera centre
# from it, so the fitter and the projector cannot drift apart. Re-exported here
# because this module's docstring and its callers name it.

# Independent per-frame fits jumped >100 deg between frames: a 20-40 px
# silhouette under-determines six DOF, so frames share bounds and smoothness.

# The GROUNDED pose -- a real club, sole on the ground, face square -- frozen.
# `angles.square_pose()` computes it, but `angles` imports `triad` from this
# module, so it cannot be called from here; `test_clubpose_fit_pose_box`
# asserts the two agree.
#
# It matters because `triad(0, 0, 0)` is the mesh's own normalised frame and in
# the world frame that is a BACKWARDS club, face toward the golfer. Bounds and
# grids centred on (0, 0, 0) therefore scored the grounded pose -1.0 before a
# mask was rendered: the search was never offered the club it was looking for.
GROUNDED_POSE_DEG = (-179.95683175631507, 13.360819162637801, 3.4586763279020394)

# Loose sanity bounds only, as half-widths about the grounded pose: yaw, pitch
# and roll here are renderer angles, not face angle / dynamic loft / lie.
POSE_BOUND_DEG = (60.0, 65.0, 70.0)

# One coarse grid shape for all three axes, as offsets from the grounded pose.
# The zero offset is a node, so the coarse stage starts ON the grounded pose.
GRID_OFFSETS_DEG = (-40.0, -20.0, 0.0, 20.0, 40.0)


def _grid(axis: int) -> tuple[float, ...]:
    return tuple(GROUNDED_POSE_DEG[axis] + offset for offset in GRID_OFFSETS_DEG)


YAW_GRID_DEG = _grid(0)
PITCH_GRID_DEG = _grid(1)
ROLL_GRID_DEG = _grid(2)


def pose_in_bounds(yaw_deg: float, pitch_deg: float, roll_deg: float) -> bool:
    """Is this renderer pose within the sanity box around the grounded pose?

    The comparison WRAPS: the grounded yaw is -180.0, and +180 is the same
    club. Comparing on the line would put half the box on the far side of the
    branch cut and reject poses a degree away from the answer.
    """
    return all(
        abs(math.remainder(float(value) - centre, 360.0)) <= bound
        for value, centre, bound in zip(
            (yaw_deg, pitch_deg, roll_deg), GROUNDED_POSE_DEG, POSE_BOUND_DEG, strict=True
        )
    )


def measured_camera(
    width: int = 320,
    height: int = 200,
    *,
    pitch_deg: float = CAMERA_PITCH_DEG,
    roll_deg: float = CAMERA_ROLL_DEG,
) -> CameraPreset:
    """The camera we actually have, from datasheet optics and measured range.

    ``pitch_deg`` is the mount's boresight elevation. The default is the
    session median solved from the teed ball's row; a caller with its own
    session should solve its own with `projection.camera_pitch_from_ball_row`
    and pass it here, because the mount is re-taped between sessions and the
    per-shot scatter is 0.12 deg.
    """
    return CameraPreset(
        name="MEASURED",
        width=width,
        height=height,
        fx=FOCAL_PX,
        fy=FOCAL_PX,
        cx=width / 2.0,
        cy=height / 2.0,
        plate_scale_px_per_mm=FOCAL_PX / CAMERA_BALL_RANGE_MM,
        sensor_crop=(336, 150, 816, 516),
        sampling_increment=(SUBSAMPLE, SUBSAMPLE),
        isp_offset=(4, 4),
        orientation="rot180",
        gate_b1_passed=False,
        physical_status="measured_from_real_capture",
        center_world_mm=tuple(
            float(v) for v in camera_center_world(CAMERA_HEIGHT_ABOVE_BALL_MM, CAMERA_BALL_RANGE_MM)
        ),
        pitch_deg=float(pitch_deg),
        roll_deg=float(roll_deg),
    )


def render_mask(mesh, center_world, roll_rad, camera) -> tuple[np.ndarray, np.ndarray] | None:
    """Project and rasterise the mesh, against an explicitly supplied camera.

    The evaluation copy of this resolves the camera by preset NAME, which hard-
    codes the wrong intrinsics for real data. This one takes the camera object.
    """
    axis_u, axis_v = _face_axes(float(roll_rad))
    local = mesh.vertices_local_mm
    world = (
        np.asarray(center_world, dtype=float)[None, :]
        + local[:, 0, None] * FACE_NORMAL[None, :]
        + local[:, 1, None] * axis_u[None, :]
        + local[:, 2, None] * axis_v[None, :]
    )
    uv, front = _project(world, camera)
    center_uv, center_front = _project(np.asarray(center_world, dtype=float)[None, :], camera)
    if not bool(center_front[0]) or not front.any():
        return None
    faces = mesh.faces[np.all(front[mesh.faces], axis=1)]
    if faces.size == 0:
        return None
    mask = rasterize_projected_triangles(uv, faces, width=camera.width, height=camera.height)
    return mask, center_uv[0]


def iou(a: np.ndarray, b: np.ndarray) -> float:
    """Intersection over union of two boolean masks; 0.0 when both are empty."""
    a = a.astype(bool)
    b = b.astype(bool)
    union = np.count_nonzero(a | b)
    return float(np.count_nonzero(a & b) / union) if union else 0.0


@dataclass
class RealFit:
    """One frame's 4-DOF result -- centre, roll and range -- or why there is none."""

    ok: bool
    reason: str
    iou: float
    center_world: np.ndarray | None
    roll_deg: float | None
    range_mm: float | None
    observed_px: int
    rendered_px: int


def fit_frame(
    mesh,
    observed_mask: np.ndarray,
    camera: CameraPreset,
    *,
    range_grid_mm=np.arange(1250.0, 1651.0, 50.0),
    roll_grid_deg=np.arange(-90.0, 90.0, 7.5),
    refine: bool = True,
) -> RealFit:
    """Best 3D centre + roll for one observed club silhouette, by direct IoU.

    The centre is constrained to the pixel ray through the observed centroid, so
    the search is over range and roll rather than free 3D translation. That is
    the same constraint the radar range sphere would impose, standing in for a
    radar measurement this camera-only capture does not have.
    """
    observed = observed_mask.astype(bool)
    n_obs = int(observed.sum())
    if n_obs < 40:
        return RealFit(False, "observed_mask_too_small", 0.0, None, None, None, n_obs, 0)

    ys, xs = np.nonzero(observed)
    centroid = np.array([xs.mean(), ys.mean()], dtype=float)
    ray = _ray_world(centroid, camera)

    def point_at(range_mm: float) -> np.ndarray:
        # Range is from the CAMERA, not the world origin at the impact point.
        return camera.center_world + ray * float(range_mm)

    best = (0.0, None, None, None, 0)
    for range_mm in range_grid_mm:
        center = point_at(range_mm)
        for roll_deg in roll_grid_deg:
            out = render_mask(mesh, center, math.radians(float(roll_deg)), camera)
            if out is None:
                continue
            score = iou(out[0], observed)
            if score > best[0]:
                best = (score, center, float(roll_deg), float(range_mm), int(out[0].sum()))

    if best[1] is None:
        return RealFit(False, "no_pose_projected", 0.0, None, None, None, n_obs, 0)

    if refine:
        score, center, roll_deg, range_mm, n_ren = best
        for _ in range(3):
            improved = False
            for d_range in (-25.0, -10.0, 10.0, 25.0):
                for d_roll in (-4.0, -1.5, 1.5, 4.0):
                    cand_center = point_at(range_mm + d_range)
                    out = render_mask(mesh, cand_center, math.radians(roll_deg + d_roll), camera)
                    if out is None:
                        continue
                    cand = iou(out[0], observed)
                    if cand > score:
                        score, center, roll_deg, range_mm = (
                            cand,
                            cand_center,
                            roll_deg + d_roll,
                            range_mm + d_range,
                        )
                        n_ren, improved = int(out[0].sum()), True
            if not improved:
                break
        best = (score, center, roll_deg, range_mm, n_ren)

    score, center, roll_deg, range_mm, n_ren = best
    return RealFit(True, "ok", score, center, roll_deg, range_mm, n_obs, n_ren)


# 6-DOF fitting; the earlier 4-DOF model could not represent loft, lie or face.


def _rot(axis: np.ndarray, angle_rad: float) -> np.ndarray:
    """Rodrigues rotation about an arbitrary axis."""
    a = np.asarray(axis, dtype=float)
    a = a / np.linalg.norm(a)
    K = np.array([[0.0, -a[2], a[1]], [a[2], 0.0, -a[0]], [-a[1], a[0], 0.0]])
    return np.eye(3) + math.sin(angle_rad) * K + (1.0 - math.cos(angle_rad)) * (K @ K)


def triad(yaw_deg: float, pitch_deg: float, roll_deg: float) -> tuple[np.ndarray, ...]:
    """Full orientation as an orthonormal (normal, u, v) triad.

    yaw   - about world up, i.e. FACE ANGLE (open/closed)
    pitch - about world right, i.e. DYNAMIC LOFT
    roll  - about the face normal, i.e. LIE / toe-up rotation
    """
    from openflight.camera.clubpose.projection import WORLD_RIGHT, WORLD_UP

    R = _rot(WORLD_UP, math.radians(yaw_deg)) @ _rot(WORLD_RIGHT, math.radians(pitch_deg))
    n = R @ FACE_NORMAL
    u = R @ WORLD_RIGHT
    v = R @ WORLD_UP
    Rr = _rot(n, math.radians(roll_deg))
    return n, Rr @ u, Rr @ v


def render_mask_6dof(mesh, center_world, yaw_deg, pitch_deg, roll_deg, camera, *, clip_hosel=False):
    """Project and rasterise with a FULL orientation rather than roll alone.

    ``clip_hosel`` cuts the rendered mask at the hosel neck with the same
    `head_split.split_head` that produced every observed mask this is compared
    against. Off by default so a synthetic-against-synthetic comparison is
    unaffected; anything scored against a segmented frame wants it on.
    """
    n, u, v = triad(yaw_deg, pitch_deg, roll_deg)
    local = mesh.vertices_local_mm
    world = (
        np.asarray(center_world, dtype=float)[None, :]
        + local[:, 0, None] * n[None, :]
        + local[:, 1, None] * u[None, :]
        + local[:, 2, None] * v[None, :]
    )
    uv, front = _project(world, camera)
    _, center_front = _project(np.asarray(center_world, dtype=float)[None, :], camera)
    if not bool(center_front[0]) or not front.any():
        return None
    faces = mesh.faces[np.all(front[mesh.faces], axis=1)]
    if faces.size == 0:
        return None
    mask = rasterize_projected_triangles(uv, faces, width=camera.width, height=camera.height)
    return _clip_hosel(mask) if clip_hosel else mask


def fit_frame_6dof(
    mesh,
    observed_mask,
    camera,
    *,
    range_grid_mm=(1456.0, 1581.0, 1706.0),
    yaw_grid=YAW_GRID_DEG,
    pitch_grid=PITCH_GRID_DEG,
    roll_grid=ROLL_GRID_DEG,
):
    """Best 6-DOF pose by direct IoU. Coarse grid, then local refinement."""
    observed = observed_mask.astype(bool)
    if int(observed.sum()) < 40:
        return {"ok": False, "reason": "observed_mask_too_small", "iou": 0.0}
    ys, xs = np.nonzero(observed)
    ray = _ray_world(np.array([xs.mean(), ys.mean()], dtype=float), camera)

    def score(rng, yaw, pitch, roll):
        m = render_mask_6dof(mesh, camera.center_world + ray * rng, yaw, pitch, roll, camera)
        return (0.0, None) if m is None else (iou(m, observed), m)

    best = (0.0, None)
    for rng in range_grid_mm:
        for yaw in yaw_grid:
            for pitch in pitch_grid:
                for roll in roll_grid:
                    s, _ = score(rng, yaw, pitch, roll)
                    if s > best[0]:
                        best = (s, (rng, yaw, pitch, roll))
    if best[1] is None:
        return {"ok": False, "reason": "no_pose_projected", "iou": 0.0}

    rng, yaw, pitch, roll = best[1]
    step = [60.0, 10.0, 10.0, 15.0]
    for _ in range(4):
        improved = False
        for k, deltas in enumerate(step):
            for d in (-deltas, deltas):
                cand = [rng, yaw, pitch, roll]
                cand[k] += d
                s, _ = score(*cand)
                if s > best[0]:
                    best, (rng, yaw, pitch, roll), improved = (s, tuple(cand)), cand, True
        if not improved:
            step = [x / 2.0 for x in step]
    return {
        "ok": True,
        "reason": "ok",
        "iou": best[0],
        "range_mm": rng,
        "yaw_deg": yaw,
        "pitch_deg": pitch,
        "roll_deg": roll,
    }


def _smoothness_penalty(
    prev: dict | None,
    rng: float,
    yaw: float,
    pitch: float,
    roll: float,
    smooth_deg: float | None,
    smooth_mm: float,
    *,
    penalise_range: bool,
) -> float:
    """What one pose costs for differing from the previously accepted one.

    The penalty exists to stop a 20-40 px silhouette inventing motion between
    frames. Radar-measured range motion is not invented, so when the range is
    pinned to a measurement (`penalise_range=False`) the depth term is dropped
    entirely: charging for it would pull every frame back toward its
    neighbour's depth and undo the measurement it was given.

    ``smooth_deg=None`` drops the ANGULAR term for the same kind of reason --
    see `fit_sequence`, and `test_clubpose_fit_smoothness` for the arithmetic.
    """
    if prev is None:
        return 0.0
    penalty = 0.0
    if smooth_deg is not None:
        angular = (
            abs(yaw - prev["yaw_deg"])
            + abs(pitch - prev["pitch_deg"])
            + abs(roll - prev["roll_deg"])
        )
        penalty += angular / (3.0 * smooth_deg)
    if penalise_range:
        penalty += abs(rng - prev["range_mm"]) / smooth_mm
    return penalty


def _validated_frame_ranges(range_mm_by_frame: dict[int, float], frames) -> dict[int, float]:
    """Fail closed on a per-frame range table that cannot pin every frame."""
    ranges = {int(frame): float(value) for frame, value in range_mm_by_frame.items()}
    missing = sorted(frame for frame in frames if frame not in ranges)
    if missing:
        raise ValueError(f"range_mm_by_frame has no range for frames {missing}")
    bad = sorted(
        frame
        for frame, value in ranges.items()
        if not math.isfinite(value) or value <= 0.0  # a range is a positive distance
    )
    if bad:
        raise ValueError(f"range_mm_by_frame holds non-physical ranges for frames {bad}")
    return ranges


def fit_sequence(
    mesh,
    masks: dict[int, np.ndarray],
    camera: CameraPreset,
    *,
    smooth_deg: float | None = None,
    smooth_mm: float = 300.0,
    range_grid_mm=(1481.0, 1581.0, 1681.0),
    yaw_grid=YAW_GRID_DEG,
    pitch_grid=PITCH_GRID_DEG,
    roll_grid=ROLL_GRID_DEG,
    refine_range: bool = True,
    range_mm_by_frame: dict[int, float] | None = None,
) -> dict[int, dict]:
    """Fit an ordered run of frames, penalising jumps between consecutive poses.

    Score is IoU minus a smoothness penalty against the previous accepted pose.
    `smooth_deg` and `smooth_mm` set how much orientation and range change costs
    one unit of IoU, so a large IoU gain can still justify real motion while noise
    cannot. Set ``refine_range=False`` with a singleton ``range_grid_mm`` to keep
    an externally measured range hard-pinned during local refinement.

    ``smooth_deg`` DEFAULTS TO None -- no angular penalty. The previous default
    of 70.0 charges 1/210 of an IoU point per degree of orientation change, and
    a 20-40 px silhouette buys about 0.002 per degree of yaw: the penalty was
    more than twice the evidence, so frame two could not afford to move and
    every frame after it inherited frame one's pose. On the seven-frame runs
    this pipeline fits, that froze the whole sequence. Pass a number when the
    run is long enough and noisy enough for the penalty to be affordable.

    ``range_mm_by_frame`` supplies a MEASURED range per frame -- normally
    `fusion.ranges_from_radar(...).ranges_mm`, which anchors the radar's range
    rate at the taped ball range at impact. That result also carries the MODEL
    it used, linear or quadratic, and on a clubhead the difference is tens of
    millimetres over one capture; a caller that cares should read it. When it is given, depth stops being a fitted
    parameter: each frame is pinned to its own measurement, ``range_grid_mm``
    and ``refine_range`` are ignored, and the smoothness penalty drops its range
    term, because motion the radar measured is not motion the fit invented.
    Every frame in ``masks`` must appear in it; a missing or non-physical range
    is an error rather than a silent fall back to the grid.
    """
    frame_ranges = (
        None
        if range_mm_by_frame is None
        else _validated_frame_ranges(range_mm_by_frame, sorted(masks))
    )
    penalise_range = frame_ranges is None
    out: dict[int, dict] = {}
    prev = None
    for i in sorted(masks):
        observed = masks[i].astype(bool)
        if int(observed.sum()) < 40:
            continue
        ys, xs = np.nonzero(observed)
        ray = _ray_world(np.array([xs.mean(), ys.mean()], dtype=float), camera)
        frame_grid = range_grid_mm if frame_ranges is None else (frame_ranges[i],)
        refine_this_range = refine_range and frame_ranges is None

        # `ray` and `observed` are this frame's; the closure is built, used and
        # discarded inside one iteration, never deferred past it.
        def score(rng, yaw, pitch, roll):
            if not pose_in_bounds(yaw, pitch, roll):
                return -1.0
            m = render_mask_6dof(mesh, camera.center_world + ray * rng, yaw, pitch, roll, camera)
            if m is None:
                return -1.0
            return iou(m, observed) - _smoothness_penalty(
                prev, rng, yaw, pitch, roll, smooth_deg, smooth_mm, penalise_range=penalise_range
            )

        best = (-1.0, None)
        for rng in frame_grid:
            for yaw in yaw_grid:
                for pitch in pitch_grid:
                    for roll in roll_grid:
                        s = score(rng, yaw, pitch, roll)
                        if s > best[0]:
                            best = (s, (rng, yaw, pitch, roll))
        if best[1] is None:
            continue
        rng, yaw, pitch, roll = best[1]
        step = [50.0, 8.0, 8.0, 8.0]
        for _ in range(4):
            improved = False
            for k, delta in enumerate(step):
                if k == 0 and not refine_this_range:
                    continue
                for d in (-delta, delta):
                    cand = [rng, yaw, pitch, roll]
                    cand[k] += d
                    s = score(*cand)
                    if s > best[0]:
                        best = (s, tuple(cand))
                        rng, yaw, pitch, roll = cand
                        improved = True
            if not improved:
                step = [x / 2.0 for x in step]

        m = render_mask_6dof(mesh, camera.center_world + ray * rng, yaw, pitch, roll, camera)
        prev = {
            "range_mm": rng,
            "yaw_deg": yaw,
            "pitch_deg": pitch,
            "roll_deg": roll,
            "iou": iou(m, observed) if m is not None else 0.0,
        }
        out[i] = dict(prev, mask=m)
    return out
