"""Swing plane, swing direction and arc radius from the 3-D clubhead track.

NO GROUND TRUTH EXISTS. Nothing here is scored against a launch monitor. Every
figure is a CONSISTENCY result: the camera's clubhead centroid lifted into world
millimetres by the radar's own range ramp, then fitted. Read it as "do the
sensors agree with each other and with a golf swing", never as "this is the
golfer's swing plane".

Pipeline, per shot:

  1. Frames are un-mirrored (`frames[:, :, ::-1]`) and the teed ball is found by
     `club_motion.detect_reference_ball`.
  2. A moving mask is `|frame - median(frames[8:56])| > max(4 sigma, 18)`.
  3. The teed-ball disk is vetoed FROM THE CONTACT FRAME ON, and the flying
     ball's own corridor is vetoed post-impact.
  4. The largest moving component within 200 px of the ball is split by
     `clubpose.head_split.split_head`; the HEAD half's centroid is the
     observation.
  5. The centroid becomes a camera ray via `clubpose.projection._ray_world` on
     `clubpose.fit.measured_camera(320, 200)`, optionally re-aimed so the teed
     ball's own observed ray lands on the taped ball position (see BORESIGHT).
  6. The range comes from `clubpose.fusion.ranges_from_radar`, anchored at
     1581 mm at contact frame `pre_trigger_frames - 1.575/343 * fps`.
  7. `point = camera.center_world + ray * range`.
  8. A plane is fitted by SVD, a circle inside that plane by the algebraic
     (Kasa) fit, and the velocity by a quadratic in time.

DEVIATIONS from the brief's recipe, both forced by what the pixels do, both
measured rather than tuned:

  * MORPHOLOGY. The clubhead images as a RIM: its interior matches the mat, so
    the raw moving mask of the head is an annulus whose maximum inscribed
    radius collapses to ~3 px. `split_head` needs >= 4 px to seed a head core,
    so it returned None or a fragment on roughly half the frames. A 3x3 close
    plus binary hole fill is applied to the moving mask before components are
    taken. Measured on shot 002: usable head frames went from 11 to 24.
  * TEE VETO TIMING. The brief vetoes the tee disk on every frame. The teed
    ball is STATIONARY, so it is not in the moving mask at all before impact --
    but the clubhead IS at the ball there, and the veto punches an 8.7 px hole
    through the middle of it. Measured on shot 002 frames 67-71, that dropped
    the component's maximum inscribed radius from 5.0-6.4 px to 3.2-3.6 px and
    made `split_head` fail. The veto is therefore applied only from the contact
    frame onward, which is the only place the ball's absence shows.

BORESIGHT. `projection` builds its world-to-camera rotation by aiming the lens
at the world ORIGIN. The rig's camera is LEVEL (2026-08-26 calibration), so the
two disagree by ~6.7 deg of pitch, and the teed ball -- whose world position is
taped -- lands ~178 mm below the ground plane when back-projected through the
as-shipped model. `--boresight ball` (default) rotates every ray by the single
rotation that maps the observed ball ray onto the taped ball direction, using
the ball already in every frame as the calibration target. `--boresight raw`
uses `_ray_world` untouched, exactly as the brief specifies. Both are reported;
the difference is a direct measure of how much this metric depends on the
boresight.

POST-IMPACT RANGE IS AN ASSUMPTION. The radar's `iwr_club_path_range_rate_ms`
is fitted before impact. This script continues that same constant rate through
the post-impact frames. The clubhead is decelerating and turning there, so
post-impact depths are the weakest link in every number below; the per-shot CSV
carries a pre-impact-only plane fit for comparison.

Run from the repository root:

    uv run python research/session_metrics/swing_plane.py \
        --session /path/to/export --out /path/to/output
"""

# pylint: disable=wrong-import-position,no-member,unsubscriptable-object

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import matplotlib
import numpy as np
from scipy import ndimage

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402  # pylint: disable=ungrouped-imports

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from openflight.camera.club_motion import ReferenceBall, detect_reference_ball  # noqa: E402
from openflight.camera.clubpose.fit import measured_camera  # noqa: E402
from openflight.camera.clubpose.fusion import ranges_from_radar  # noqa: E402
from openflight.camera.clubpose.head_split import split_head  # noqa: E402
from openflight.camera.clubpose.projection import (  # noqa: E402
    BALL_RADIUS_MM,
    CAMERA_BALL_RANGE_MM,
    CameraPreset,
    _ray_world,
)

BALL_TO_UNIT_M = 1.575
SPEED_OF_SOUND_M_S = 343.0
BACKGROUND_SLICE = slice(8, 56)
MOVING_SIGMA = 4.0
MOVING_FLOOR = 18.0
BALL_VETO_DIAMETERS = 0.75
COMPONENT_RADIUS_PX = 200.0
MIN_COMPONENT_AREA_PX = 40
MIN_HEAD_AREA_PX = 60
# The head sits on top of the ball through contact; the brief drops those
# frames outright. Measured here instead: any frame whose head mask reaches
# into the tee disk plus this margin is dropped, and the dropped set is
# reported per shot.
TEE_OVERLAP_MARGIN_PX = 3.0
# The brief drops f71-f73, where the head sits on the ball. Expressed against
# the measured contact frame so it travels with the shot.
CONTACT_SKIP_FRAMES = (-1.0, 2.0)
# The post-impact range is the pre-impact rate extrapolated. Ten frames past
# contact it already claims the head is 2.3 m from the lens; past that the
# assumption is not worth carrying, and an isolated late frame is the golfer,
# not the head.
POST_IMPACT_LIMIT_FRAMES = 10.0
# A head mask that reaches the image border is a head only partly in frame, so
# its centroid is the visible part's, not the head's. A head far smaller than
# its neighbours on the same side of impact is a `split_head` fragment, not a
# head; the gate is per side because contrast, not range, sets the apparent
# size, and it collapses once the head crosses the bright mat edge.
FRAGMENT_AREA_FRACTION = 0.35
MM_PER_S_TO_MPH = 1.0 / 447.04
MIN_TRACK_POINTS = 6
# Delivery is a PRE-impact quantity, and the post-impact depths rest on an
# assumption, so the velocity at contact is extrapolated from the last few
# pre-impact frames alone. The swing is locally quadratic over a few
# milliseconds, not over the whole 40 ms the track spans.
VELOCITY_PRE_FRAMES = 8
# Robust plane fit: a point this far off the fitted plane is a bad centroid,
# not a swing that left the plane. At most a quarter of the track may go.
PLANE_OUTLIER_FLOOR_MM = 30.0
PLANE_OUTLIER_MAD_FACTOR = 4.0
PLANE_MAX_OUTLIER_FRACTION = 0.25
# A track that will not lie on ANY plane to better than this is a tracking
# failure, not a swing. Such shots are reported, never quietly averaged in.
PLANE_QUALITY_LIMIT_MM = 20.0
# ... and a track whose answer moves this far when any single frame is removed
# has not measured anything, however small its residual.
JACKKNIFE_QUALITY_LIMIT_DEG = 15.0

SWING_PLANE_BAND_DEG = (55.0, 65.0)
SWING_DIRECTION_BAND_DEG = 10.0
ARC_RADIUS_BAND_M = (1.4, 1.8)

SHEET_COLUMNS = 6
SHEET_SCALE = 2


@dataclass(frozen=True)
class TrackPoint:
    """One frame's clubhead observation, in pixels and in world millimetres."""

    frame: int
    elapsed_s: float
    u_px: float
    v_px: float
    head_area_px: int
    range_mm: float
    world_mm: np.ndarray
    post_impact: bool


def _float(value: str | None) -> float | None:
    try:
        parsed = float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and math.isfinite(parsed) else None


def _session_path(value: str | None, parser: argparse.ArgumentParser) -> Path:
    supplied = value or os.environ.get("OPENFLIGHT_SESSION")
    if not supplied:
        parser.error("provide --session or set OPENFLIGHT_SESSION")
    session = Path(supplied).expanduser().resolve()
    if not (session / "shots.csv").is_file() or not (session / "shots").is_dir():
        parser.error(f"session export is missing shots.csv or shots/: {session}")
    return session


def contact_frame(metadata: dict[str, Any]) -> float:
    """Frame index of ball contact, from the acoustic trigger's flight time."""
    return int(metadata["pre_trigger_frames"]) - BALL_TO_UNIT_M / SPEED_OF_SOUND_M_S * float(
        metadata["delivered_fps"]
    )


def moving_mask(frames: np.ndarray) -> np.ndarray:
    """Per-frame boolean mask of pixels that moved, with the head filled in.

    The close-and-fill is the documented deviation: the clubhead images as a
    rim, and a rim cannot seed `split_head`.
    """
    background = np.median(frames[BACKGROUND_SLICE], axis=0)
    sigma = frames[BACKGROUND_SLICE].astype(float).std(axis=0)
    threshold = np.maximum(MOVING_SIGMA * sigma, MOVING_FLOOR)
    raw = np.abs(frames.astype(float) - background) > threshold
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    filled = np.empty_like(raw)
    for index in range(raw.shape[0]):
        closed = cv2.morphologyEx(raw[index].astype(np.uint8), cv2.MORPH_CLOSE, kernel)
        filled[index] = ndimage.binary_fill_holes(closed > 0)
    return filled


def _disk(shape: tuple[int, int], x: float, y: float, radius: float) -> np.ndarray:
    rows, cols = np.mgrid[0 : shape[0], 0 : shape[1]]
    return (cols - x) ** 2 + (rows - y) ** 2 <= radius**2


def ball_flight_veto(
    frames: np.ndarray,
    moving: np.ndarray,
    background: np.ndarray,
    ball: ReferenceBall,
    first_post_frame: int,
    tee: np.ndarray,
) -> tuple[np.ndarray, list[tuple[int, float, float]]]:
    """Union of disks over the departing ball's own image track.

    The ball leaves BRIGHTER than the mat, so a positive-signed compact
    component of about the tee ball's size is the ball and nothing else.
    """
    veto = np.zeros(frames.shape[1:], dtype=bool)
    track: list[tuple[int, float, float]] = []
    signed = frames.astype(float) - background
    for index in range(first_post_frame, frames.shape[0]):
        mask = (moving[index] & (signed[index] > 0)).astype(np.uint8)
        mask[tee] = 0
        count, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        best: tuple[int, float, float] | None = None
        for label in range(1, count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            width = int(stats[label, cv2.CC_STAT_WIDTH])
            height = int(stats[label, cv2.CC_STAT_HEIGHT])
            if not 15 <= area <= 3 * ball.area_px:
                continue
            if max(width, height) > 2.2 * ball.diameter_px or area / (width * height) < 0.5:
                continue
            if best is None or area > best[0]:
                best = (area, float(centroids[label][0]), float(centroids[label][1]))
        if best is None:
            continue
        area, x, y = best
        track.append((index, x, y))
        radius = max(BALL_VETO_DIAMETERS * ball.diameter_px, math.sqrt(area / math.pi) + 2.0)
        veto |= _disk(frames.shape[1:], x, y, radius)
    return veto, track


def _touches_border(mask: np.ndarray) -> bool:
    return bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any())


def head_masks(
    frames: np.ndarray, ball: ReferenceBall, contact: float
) -> tuple[
    dict[int, np.ndarray],
    dict[int, np.ndarray],
    list[tuple[int, float, float]],
    dict[str, list[int]],
]:
    """(accepted head masks, every candidate head mask, ball track, rejections)."""
    moving = moving_mask(frames)
    background = np.median(frames[BACKGROUND_SLICE], axis=0)
    tee = _disk(frames.shape[1:], ball.x, ball.y, BALL_VETO_DIAMETERS * ball.diameter_px)
    tee_margin = _disk(
        frames.shape[1:],
        ball.x,
        ball.y,
        BALL_VETO_DIAMETERS * ball.diameter_px + TEE_OVERLAP_MARGIN_PX,
    )
    first_post = int(math.ceil(contact))
    flight, flight_track = ball_flight_veto(frames, moving, background, ball, first_post, tee)
    masks: dict[int, np.ndarray] = {}
    candidates: dict[int, np.ndarray] = {}
    rejected: dict[str, list[int]] = {
        "contact": [],
        "tee_overlap": [],
        "border": [],
        "fragment": [],
        "late": [],
        "no_head": [],
    }
    for index in range(frames.shape[0]):
        mask = moving[index].copy()
        if index >= first_post:
            mask &= ~(tee | flight)
        labels_input = mask.astype(np.uint8)
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(labels_input, 8)
        best: tuple[int, int] | None = None
        for label in range(1, count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            distance = math.hypot(centroids[label][0] - ball.x, centroids[label][1] - ball.y)
            if area < MIN_COMPONENT_AREA_PX or distance > COMPONENT_RADIUS_PX:
                continue
            if best is None or area > best[0]:
                best = (area, label)
        if best is None:
            continue
        split = split_head((labels == best[1]).astype(np.uint8))
        if split is None or int(split[0].sum()) < MIN_HEAD_AREA_PX:
            rejected["no_head"].append(index)
            continue
        head = split[0] > 0
        candidates[index] = head
        if contact + CONTACT_SKIP_FRAMES[0] <= index <= contact + CONTACT_SKIP_FRAMES[1]:
            rejected["contact"].append(index)
            continue
        if index > contact + POST_IMPACT_LIMIT_FRAMES:
            rejected["late"].append(index)
            continue
        if index >= first_post and (head & tee_margin).any():
            rejected["tee_overlap"].append(index)
            continue
        if _touches_border(head):
            rejected["border"].append(index)
            continue
        masks[index] = head
    for post in (False, True):
        side = {index: mask for index, mask in masks.items() if (index >= first_post) is post}
        if len(side) < 2:
            continue
        median_area = float(np.median([int(mask.sum()) for mask in side.values()]))
        for index in sorted(side):
            if int(side[index].sum()) < FRAGMENT_AREA_FRACTION * median_area:
                rejected["fragment"].append(index)
                del masks[index]
    return masks, candidates, flight_track, rejected


def ball_anchored_rotation(ball: ReferenceBall, camera: CameraPreset) -> np.ndarray:
    """Rotation taking the observed teed-ball ray onto the taped ball direction.

    The teed ball is a calibration target that appears in every frame of every
    shot: its world position is the tape chain's, and its image position is
    measured. One rotation reconciles them, and it absorbs the difference
    between the shipped aim-at-origin camera model and the level lens the rig
    actually has.
    """
    observed = _ray_world(np.array([ball.x, ball.y]), camera)
    target = np.array([0.0, 0.0, BALL_RADIUS_MM]) - camera.center_world
    target = target / np.linalg.norm(target)
    axis = np.cross(observed, target)
    sine = float(np.linalg.norm(axis))
    cosine = float(observed @ target)
    if sine < 1e-12:
        return np.eye(3)
    axis = axis / sine
    cross = np.array([[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]], [-axis[1], axis[0], 0.0]])
    angle = math.atan2(sine, cosine)
    return np.eye(3) + math.sin(angle) * cross + (1.0 - math.cos(angle)) * (cross @ cross)


def track_points(
    masks: dict[int, np.ndarray],
    camera: CameraPreset,
    rotation: np.ndarray,
    contact: float,
    fps: float,
    range_rate_ms: float,
) -> list[TrackPoint]:
    """Lift every head centroid into world millimetres."""
    frames = sorted(masks)
    if not frames:
        return []
    elapsed = np.asarray(frames, dtype=float) / fps
    ranges = ranges_from_radar(
        elapsed, contact / fps, range_rate_ms, ball_range_mm=CAMERA_BALL_RANGE_MM
    )
    points: list[TrackPoint] = []
    for frame, time_s, range_mm in zip(frames, elapsed, ranges, strict=True):
        rows, cols = np.nonzero(masks[frame])
        uv = np.array([float(cols.mean()), float(rows.mean())])
        ray = rotation @ _ray_world(uv, camera)
        points.append(
            TrackPoint(
                frame=frame,
                elapsed_s=float(time_s),
                u_px=uv[0],
                v_px=uv[1],
                head_area_px=int(masks[frame].sum()),
                range_mm=float(range_mm),
                world_mm=camera.center_world + ray * float(range_mm),
                post_impact=frame > contact,
            )
        )
    return points


def fit_plane(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Least-squares plane: returns (centroid, unit normal, rms residual mm)."""
    centroid = points.mean(axis=0)
    _, singular, right = np.linalg.svd(points - centroid)
    normal = right[-1] / np.linalg.norm(right[-1])
    residual = float(singular[-1] / math.sqrt(len(points)))
    return centroid, normal, residual


def fit_plane_robust(points: list[TrackPoint]) -> tuple[list[TrackPoint], list[int]]:
    """Drop the centroids that cannot be on the swing plane, and say which."""
    kept = list(points)
    dropped: list[int] = []
    floor = max(MIN_TRACK_POINTS, int(round((1.0 - PLANE_MAX_OUTLIER_FRACTION) * len(points))))
    while len(kept) > floor:
        world = np.stack([p.world_mm for p in kept])
        centroid, normal, _ = fit_plane(world)
        offsets = np.abs((world - centroid) @ normal)
        limit = max(
            PLANE_OUTLIER_FLOOR_MM, PLANE_OUTLIER_MAD_FACTOR * float(np.median(offsets)) or 0.0
        )
        worst = int(np.argmax(offsets))
        if offsets[worst] <= limit:
            break
        dropped.append(kept[worst].frame)
        kept.pop(worst)
    return kept, dropped


def plane_basis(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """In-plane axes: the first horizontal, the second the up-ish partner."""
    horizontal = np.cross(np.array([0.0, 0.0, 1.0]), normal)
    if np.linalg.norm(horizontal) < 1e-9:
        horizontal = np.cross(np.array([0.0, 1.0, 0.0]), normal)
    horizontal = horizontal / np.linalg.norm(horizontal)
    if horizontal[0] < 0.0:
        horizontal = -horizontal
    vertical = np.cross(normal, horizontal)
    if vertical[2] < 0.0:
        vertical = -vertical
        horizontal = -horizontal
    return horizontal, vertical


def swing_plane_angles(normal: np.ndarray) -> tuple[float, float]:
    """(plane-to-ground angle, swing direction), both in degrees.

    The plane-to-ground angle is TrackMan's swing plane: the vertical angle
    between the ground and the circle the head travels on, so it is the dihedral
    angle, i.e. the angle between the plane normal and vertical. The swing
    direction is the heading of the plane's intersection with the ground,
    positive to the RIGHT of the target line, which is world -y.
    """
    unit = normal / np.linalg.norm(normal)
    plane_deg = math.degrees(math.acos(min(1.0, abs(float(unit[2])))))
    line = np.cross(np.array([0.0, 0.0, 1.0]), unit)
    if np.linalg.norm(line) < 1e-9:
        return plane_deg, math.nan
    line = line / np.linalg.norm(line)
    if line[0] < 0.0:
        line = -line
    return plane_deg, math.degrees(math.atan2(-line[1], line[0]))


def fit_circle(planar: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Algebraic (Kasa) circle fit: returns (centre, radius, rms residual)."""
    design = np.column_stack([2.0 * planar[:, 0], 2.0 * planar[:, 1], np.ones(len(planar))])
    target = (planar**2).sum(axis=1)
    solution, *_ = np.linalg.lstsq(design, target, rcond=None)
    centre = solution[:2]
    radius_squared = solution[2] + float(centre @ centre)
    if radius_squared <= 0.0:
        return centre, math.nan, math.nan
    radius = math.sqrt(radius_squared)
    residual = float(np.sqrt(np.mean((np.linalg.norm(planar - centre, axis=1) - radius) ** 2)))
    return centre, radius, residual


def position_at(points: list[TrackPoint], target_s: float) -> np.ndarray:
    """Quadratic-in-time world position, evaluated at ``target_s``."""
    times = np.asarray([p.elapsed_s for p in points]) - target_s
    world = np.stack([p.world_mm for p in points])
    design = np.stack([times**power for power in (2, 1, 0)], axis=1)
    coefficients, *_ = np.linalg.lstsq(design, world, rcond=None)
    return coefficients[-1]


def velocity_at(
    points: list[TrackPoint], target_s: float, pre_frames: int | None = None
) -> tuple[np.ndarray, int]:
    """Quadratic-in-time world velocity, evaluated at ``target_s``.

    The swing is only locally quadratic: fitted across the whole track the
    steep descent 0.8 m before the ball dominates and the answer at contact is
    the average of the arc, not the tangent to it. ``pre_frames`` restricts the
    fit to the last N pre-impact observations, so the delivered velocity never
    depends on the assumed post-impact range rate.
    """
    used = points
    if pre_frames is not None:
        pre = [p for p in points if not p.post_impact][-pre_frames:]
        if len(pre) >= 4:
            used = pre
    times = np.asarray([p.elapsed_s for p in used]) - target_s
    world = np.stack([p.world_mm for p in used])
    order = 2 if len(used) >= 4 else 1
    design = np.stack([times**power for power in range(order, -1, -1)], axis=1)
    coefficients, *_ = np.linalg.lstsq(design, world, rcond=None)
    # d/dt at t = 0 is the coefficient on the linear term.
    return coefficients[-2], len(used)


def jackknife(points: list[TrackPoint]) -> dict[str, float]:
    """Leave-one-out spread of the plane metrics.

    With no ground truth the only honest error bar is how much one frame's
    centroid can move the answer, so every fit is redone N times with one point
    held out and the spread reported.
    """
    planes: list[float] = []
    directions: list[float] = []
    radii: list[float] = []
    for index in range(len(points)):
        kept = points[:index] + points[index + 1 :]
        if len(kept) < 4:
            continue
        world = np.stack([p.world_mm for p in kept])
        centroid, normal, _ = fit_plane(world)
        plane_deg, direction_deg = swing_plane_angles(normal)
        horizontal, vertical = plane_basis(normal)
        planar = np.column_stack([(world - centroid) @ horizontal, (world - centroid) @ vertical])
        _, radius_mm, _ = fit_circle(planar)
        planes.append(plane_deg)
        directions.append(direction_deg)
        radii.append(radius_mm / 1000.0)
    if not planes:
        return {}
    return {
        "jackknife_swing_plane_sd_deg": float(np.std(planes, ddof=1)) if len(planes) > 1 else 0.0,
        "jackknife_swing_plane_range_deg": float(np.ptp(planes)),
        "jackknife_swing_direction_range_deg": float(np.ptp(directions)),
        "jackknife_arc_radius_range_m": float(np.ptp(radii)),
    }


def _sheet(
    frames: np.ndarray,
    masks: dict[int, np.ndarray],
    rejected: dict[str, list[int]],
    ball: ReferenceBall,
    contact: float,
    used: set[int],
    path: Path,
) -> None:
    first = max(0, int(contact) - 14)
    last = min(frames.shape[0], int(contact) + 22)
    reason_of = {frame: name for name, frames_ in rejected.items() for frame in frames_}
    tiles: list[np.ndarray] = []
    for index in range(first, last):
        tile = cv2.cvtColor(frames[index], cv2.COLOR_GRAY2BGR)
        mask = masks.get(index)
        if mask is not None:
            colour = np.array([0, 255, 0]) if index in used else np.array([0, 165, 255])
            tile[mask] = (0.35 * tile[mask] + 0.65 * colour).astype(np.uint8)
            rows, cols = np.nonzero(mask)
            cv2.drawMarker(
                tile,
                (int(round(cols.mean())), int(round(rows.mean()))),
                (0, 0, 255),
                cv2.MARKER_CROSS,
                7,
                1,
            )
        cv2.circle(
            tile, (int(ball.x), int(ball.y)), max(1, int(ball.diameter_px / 2)), (255, 0, 0), 1
        )
        label = str(index)
        if index in reason_of:
            label += " " + reason_of[index]
        cv2.putText(tile, label, (2, 12), cv2.FONT_HERSHEY_PLAIN, 0.8, (0, 255, 255), 1)
        tiles.append(tile)
    while len(tiles) % SHEET_COLUMNS:
        tiles.append(np.zeros_like(tiles[0]))
    rows_out = [
        np.hstack(tiles[index : index + SHEET_COLUMNS])
        for index in range(0, len(tiles), SHEET_COLUMNS)
    ]
    sheet = np.vstack(rows_out)
    if SHEET_SCALE != 1:
        sheet = cv2.resize(
            sheet, None, fx=SHEET_SCALE, fy=SHEET_SCALE, interpolation=cv2.INTER_NEAREST
        )
    cv2.imwrite(str(path), sheet)


def analyse_shot(
    shot_dir: Path, row: dict[str, str], boresight: str, sheet_dir: Path | None
) -> dict[str, Any]:
    """Every swing-plane figure for one shot, or a reason it has none."""
    number = int(shot_dir.name.split("_", 2)[1])
    record: dict[str, Any] = {
        "shot_number": number,
        "shot": shot_dir.name,
        "club": row.get("club", ""),
        "boresight": boresight,
        "status": "failed",
        "reason": "",
    }
    range_rate = _float(row.get("iwr_club_path_range_rate_ms"))
    if range_rate is None:
        record["reason"] = "no radar range rate"
        return record
    with (shot_dir / "camera_metadata.json").open(encoding="utf-8") as handle:
        metadata = json.load(handle)
    with np.load(shot_dir / "frames.npz") as capture:
        frames = capture["frames"][:, :, ::-1].astype(np.uint8)
    fps = float(metadata["delivered_fps"])
    contact = contact_frame(metadata)
    ball = detect_reference_ball(frames)
    camera = measured_camera(frames.shape[2], frames.shape[1])
    rotation = np.eye(3) if boresight == "raw" else ball_anchored_rotation(ball, camera)

    masks, candidates, flight_track, rejected = head_masks(frames, ball, contact)
    points = track_points(masks, camera, rotation, contact, fps, range_rate)
    if sheet_dir is not None:
        _sheet(
            frames,
            candidates,
            rejected,
            ball,
            contact,
            {p.frame for p in points},
            sheet_dir / f"{shot_dir.name}_heads.png",
        )
    record.update(
        {
            "contact_frame": contact,
            "fps": fps,
            "range_rate_ms": range_rate,
            "ball_x_px": ball.x,
            "ball_y_px": ball.y,
            "ball_diameter_px": ball.diameter_px,
            "rejected_contact": " ".join(str(f) for f in rejected["contact"]),
            "rejected_tee_overlap": " ".join(str(f) for f in rejected["tee_overlap"]),
            "rejected_border": " ".join(str(f) for f in rejected["border"]),
            "rejected_fragment": " ".join(str(f) for f in rejected["fragment"]),
            "rejected_late": " ".join(str(f) for f in rejected["late"]),
            "rejected_no_head": " ".join(str(f) for f in rejected["no_head"]),
            "ball_flight_frames": len(flight_track),
            "n_candidates": len(points),
        }
    )
    if len(points) < MIN_TRACK_POINTS:
        record["reason"] = f"only {len(points)} segmentable head frames"
        return record
    points, dropped = fit_plane_robust(points)
    record.update(
        {
            "n_points": len(points),
            "n_pre": sum(1 for p in points if not p.post_impact),
            "n_post": sum(1 for p in points if p.post_impact),
            "frames_used": " ".join(str(p.frame) for p in points),
            "rejected_off_plane": " ".join(str(f) for f in dropped),
        }
    )

    world = np.stack([p.world_mm for p in points])
    centroid, normal, residual = fit_plane(world)
    plane_deg, direction_deg = swing_plane_angles(normal)
    horizontal, vertical = plane_basis(normal)
    planar = np.column_stack([(world - centroid) @ horizontal, (world - centroid) @ vertical])
    circle_centre, radius_mm, circle_residual = fit_circle(planar)

    velocity, n_velocity = velocity_at(points, contact / fps)
    speed_mms = float(np.linalg.norm(velocity))
    in_plane = velocity - normal * float(velocity @ normal)
    in_plane_speed_mms = float(np.linalg.norm(in_plane))
    path_deg = math.degrees(math.atan2(-in_plane[1], in_plane[0]))
    attack_deg = math.degrees(math.asin(max(-1.0, min(1.0, in_plane[2] / in_plane_speed_mms))))

    # The direction of travel at contact is better read off the fitted arc than
    # off a numerical derivative: the arc uses every frame, the derivative uses
    # the two or three nearest and inherits their centroid noise.
    contact_world = position_at(points, contact / fps)
    contact_planar = np.array(
        [(contact_world - centroid) @ horizontal, (contact_world - centroid) @ vertical]
    )
    radial = contact_planar - circle_centre
    tangent_planar = np.array([-radial[1], radial[0]])
    planar_velocity = np.array([velocity @ horizontal, velocity @ vertical])
    if float(tangent_planar @ planar_velocity) < 0.0:
        tangent_planar = -tangent_planar
    tangent = horizontal * tangent_planar[0] + vertical * tangent_planar[1]
    tangent = tangent / np.linalg.norm(tangent)

    pre = [p for p in points if not p.post_impact]
    if len(pre) >= 5:
        pre_world = np.stack([p.world_mm for p in pre])
        pre_centroid, pre_normal, pre_residual = fit_plane(pre_world)
        pre_plane_deg, pre_direction_deg = swing_plane_angles(pre_normal)
        pre_horizontal, pre_vertical = plane_basis(pre_normal)
        pre_planar = np.column_stack(
            [
                (pre_world - pre_centroid) @ pre_horizontal,
                (pre_world - pre_centroid) @ pre_vertical,
            ]
        )
        _, pre_radius_mm, _ = fit_circle(pre_planar)
        pre_velocity, _ = velocity_at(pre, contact / fps, VELOCITY_PRE_FRAMES)
        pre_speed_mph = float(np.linalg.norm(pre_velocity)) * MM_PER_S_TO_MPH
        pre_radius_m = pre_radius_mm / 1000.0
    else:
        pre_plane_deg = pre_direction_deg = pre_residual = pre_speed_mph = math.nan
        pre_radius_m = math.nan

    ops_speed = _float(row.get("club_speed_mph"))
    record.update(
        {
            "status": "ok",
            "reason": "ok",
            "plane_normal_x": float(normal[0]),
            "plane_normal_y": float(normal[1]),
            "plane_normal_z": float(normal[2]),
            "swing_plane_deg": plane_deg,
            "swing_direction_deg": direction_deg,
            "plane_rms_residual_mm": residual,
            "arc_radius_m": radius_mm / 1000.0,
            "circle_rms_residual_mm": circle_residual,
            "circle_centre_height_mm": float(
                (centroid + horizontal * circle_centre[0] + vertical * circle_centre[1])[2]
            ),
            "contact_speed_mph": speed_mms * MM_PER_S_TO_MPH,
            "in_plane_speed_mph": in_plane_speed_mms * MM_PER_S_TO_MPH,
            "ops_club_speed_mph": ops_speed,
            "speed_ratio_vs_ops": (
                (in_plane_speed_mms * MM_PER_S_TO_MPH) / ops_speed if ops_speed else math.nan
            ),
            "track_club_path_deg": math.degrees(math.atan2(-tangent[1], tangent[0])),
            "track_attack_angle_deg": math.degrees(math.asin(max(-1.0, min(1.0, tangent[2])))),
            "derivative_club_path_deg": path_deg,
            "derivative_attack_angle_deg": attack_deg,
            "contact_world_x_mm": float(contact_world[0]),
            "contact_world_y_mm": float(contact_world[1]),
            "contact_world_z_mm": float(contact_world[2]),
            "fused_club_path_deg": _float(row.get("experimental_fused_club_path_deg")),
            "fused_attack_angle_deg": _float(row.get("experimental_fused_attack_angle_deg")),
            "fused_status": row.get("experimental_fused_status", ""),
            "omega_deg_s": (
                math.degrees(in_plane_speed_mms / radius_mm)
                if radius_mm and math.isfinite(radius_mm)
                else math.nan
            ),
            "pre_only_swing_plane_deg": pre_plane_deg,
            "pre_only_swing_direction_deg": pre_direction_deg,
            "pre_only_plane_rms_residual_mm": pre_residual,
            "pre_only_arc_radius_m": pre_radius_m,
            "pre_only_speed_mph": pre_speed_mph,
            "n_velocity_points": n_velocity,
            **jackknife(points),
            "_points": points,
        }
    )
    swing = record.get("jackknife_swing_plane_range_deg", math.inf)
    if residual > PLANE_QUALITY_LIMIT_MM:
        record["status"] = "low_quality"
        record["reason"] = f"plane rms {residual:.0f} mm exceeds {PLANE_QUALITY_LIMIT_MM:.0f} mm"
    elif swing > JACKKNIFE_QUALITY_LIMIT_DEG:
        record["status"] = "low_quality"
        record["reason"] = f"jackknife swing plane spans {swing:.0f} deg"
    return record


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key.startswith("_") or key in keys:
                continue
            keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _summary(values: np.ndarray) -> dict[str, float | int]:
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {"n": 0}
    return {
        "n": int(values.size),
        "median": float(np.median(values)),
        "mean": float(np.mean(values)),
        "sd": float(np.std(values, ddof=1)) if values.size > 1 else 0.0,
        "min": float(values.min()),
        "max": float(values.max()),
    }


def _plots(good: list[dict[str, Any]], out: Path) -> list[Path]:
    colours = {"7-iron": "#1f77b4", "9-iron": "#d62728"}
    written: list[Path] = []

    figure, axes = plt.subplots(2, 2, figsize=(12.0, 8.5))
    for club, colour in colours.items():
        subset = [r for r in good if r["club"] == club]
        if not subset:
            continue
        axes[0][0].scatter(
            [r["shot_number"] for r in subset],
            [r["swing_plane_deg"] for r in subset],
            c=colour,
            label=club,
            s=40,
        )
        axes[0][1].scatter(
            [r["fused_club_path_deg"] for r in subset],
            [r["swing_direction_deg"] for r in subset],
            c=colour,
            label=club,
            s=40,
        )
        axes[1][0].scatter(
            [r["shot_number"] for r in subset],
            [r["arc_radius_m"] for r in subset],
            c=colour,
            label=club,
            s=40,
        )
        axes[1][1].scatter(
            [r["ops_club_speed_mph"] for r in subset],
            [r["in_plane_speed_mph"] for r in subset],
            c=colour,
            label=club,
            s=40,
        )
    axes[0][0].axhspan(*SWING_PLANE_BAND_DEG, color="0.88", zorder=0, label="iron band 55-65")
    axes[0][0].set_xlabel("shot")
    axes[0][0].set_ylabel("swing plane to ground (deg)")
    axes[0][0].set_title("Swing plane")
    axes[0][1].axhline(0.0, color="0.85", lw=1)
    axes[0][1].axvline(0.0, color="0.85", lw=1)
    limit = 20.0
    axes[0][1].plot([-limit, limit], [-limit, limit], color="0.6", ls="--", lw=1, label="y = x")
    axes[0][1].set_xlabel("fused club path (deg)")
    axes[0][1].set_ylabel("swing direction from plane (deg)")
    axes[0][1].set_title("Swing direction vs fused club path")
    axes[1][0].axhspan(*ARC_RADIUS_BAND_M, color="0.88", zorder=0, label="1.4-1.8 m")
    axes[1][0].set_xlabel("shot")
    axes[1][0].set_ylabel("fitted arc radius (m)")
    axes[1][0].set_title("Arc radius")
    speeds = [r["ops_club_speed_mph"] for r in good if r["ops_club_speed_mph"]]
    if speeds:
        low, high = min(speeds) - 8, max(speeds) + 8
        axes[1][1].plot([low, high], [low, high], color="0.6", ls="--", lw=1, label="y = x")
    axes[1][1].set_xlabel("OPS243 club speed (mph)")
    axes[1][1].set_ylabel("in-plane track speed at contact (mph)")
    axes[1][1].set_title("Track speed vs OPS243")
    for axis in axes.ravel():
        axis.grid(alpha=0.25)
        axis.legend(fontsize=8)
    figure.suptitle(
        "Swing plane from the 3-D head track - CONSISTENCY ONLY, no ground truth exists",
        fontsize=12,
    )
    figure.tight_layout()
    path = out / "swing_plane_overview.png"
    figure.savefig(path, dpi=140)
    plt.close(figure)
    written.append(path)

    figure, axes = plt.subplots(1, 2, figsize=(12.0, 5.0))
    for record in good:
        points = record["_points"]
        world = np.stack([p.world_mm for p in points]) / 1000.0
        colour = colours.get(record["club"], "0.5")
        axes[0].plot(world[:, 0], world[:, 2], color=colour, lw=0.9, alpha=0.65)
        axes[1].plot(world[:, 0], world[:, 1], color=colour, lw=0.9, alpha=0.65)
    axes[0].axhline(0.0, color="0.3", lw=1)
    axes[0].set_xlabel("downrange x (m)")
    axes[0].set_ylabel("height z (m)")
    axes[0].set_title("Head track, side view")
    axes[1].axhline(0.0, color="0.3", lw=1)
    axes[1].set_xlabel("downrange x (m)")
    axes[1].set_ylabel("left +y (m)")
    axes[1].set_title("Head track, plan view")
    for axis in axes:
        axis.grid(alpha=0.25)
        axis.set_aspect("equal", adjustable="datalim")
    figure.suptitle("Reconstructed clubhead tracks (world mm / 1000)", fontsize=12)
    figure.tight_layout()
    path = out / "swing_plane_tracks.png"
    figure.savefig(path, dpi=140)
    plt.close(figure)
    written.append(path)
    return written


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "result": "PASS" if passed else "FAIL", "detail": detail}


def _checks(good: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not good:
        return [_check("any shot reconstructed", False, "no shot produced a plane fit")]
    planes = np.asarray([r["swing_plane_deg"] for r in good])
    directions = np.asarray([r["swing_direction_deg"] for r in good])
    radii = np.asarray([r["arc_radius_m"] for r in good])
    ratios = np.asarray([r["speed_ratio_vs_ops"] for r in good])
    checks = [
        _check(
            "swing plane median in "
            f"{SWING_PLANE_BAND_DEG[0]:.0f}-{SWING_PLANE_BAND_DEG[1]:.0f} deg",
            SWING_PLANE_BAND_DEG[0] <= float(np.median(planes)) <= SWING_PLANE_BAND_DEG[1],
            f"median {np.median(planes):.1f} deg, sd {np.std(planes, ddof=1):.1f}",
        ),
        _check(
            f"every swing direction within +-{SWING_DIRECTION_BAND_DEG:.0f} deg",
            bool(np.all(np.abs(directions) <= SWING_DIRECTION_BAND_DEG)),
            f"median {np.median(directions):+.1f} deg, "
            f"range [{directions.min():+.1f}, {directions.max():+.1f}]",
        ),
        _check(
            f"arc radius median in {ARC_RADIUS_BAND_M[0]}-{ARC_RADIUS_BAND_M[1]} m",
            ARC_RADIUS_BAND_M[0] <= float(np.median(radii)) <= ARC_RADIUS_BAND_M[1],
            f"median {np.median(radii):.2f} m, "
            f"range [{np.nanmin(radii):.2f}, {np.nanmax(radii):.2f}]",
        ),
    ]
    finite = ratios[np.isfinite(ratios)]
    if finite.size:
        checks.append(
            _check(
                "in-plane contact speed within 15% of the OPS243 club speed",
                0.85 <= float(np.median(finite)) <= 1.15,
                f"median ratio {np.median(finite):.2f}, "
                f"range [{finite.min():.2f}, {finite.max():.2f}]",
            )
        )
    # The range anchor fixes only the DEPTH of the head at contact. Where it
    # lands across the boresight is free, so the head arriving at the ball is a
    # real check on the timing and the boresight together.
    offsets = np.asarray(
        [
            math.hypot(r["contact_world_y_mm"], r["contact_world_z_mm"] - BALL_RADIUS_MM)
            for r in good
        ]
    )
    checks.append(
        _check(
            "head arrives within 30 mm of the ball centre, across the boresight, at contact",
            float(np.median(offsets)) <= 30.0,
            f"median transverse miss {np.median(offsets):.0f} mm, "
            f"range [{offsets.min():.0f}, {offsets.max():.0f}] mm",
        )
    )
    attacks = np.asarray(
        [
            (r["track_attack_angle_deg"], r["fused_attack_angle_deg"])
            for r in good
            if r["fused_attack_angle_deg"] is not None and abs(r["fused_attack_angle_deg"]) <= 15.0
        ]
    )
    if len(attacks) >= 4:
        difference = attacks[:, 0] - attacks[:, 1]
        checks.append(
            _check(
                "track attack angle agrees with the fused attack angle within 3 deg (median)",
                abs(float(np.median(difference))) <= 3.0,
                f"median difference {np.median(difference):+.2f} deg, "
                f"sd {np.std(difference, ddof=1):.2f} over n = {len(attacks)}",
            )
        )
    paired = [
        (r["fused_club_path_deg"], r["swing_direction_deg"])
        for r in good
        if r["fused_club_path_deg"] is not None and math.isfinite(r["swing_direction_deg"])
    ]
    if len(paired) >= 4:
        fused = np.asarray([p[0] for p in paired])
        plane = np.asarray([p[1] for p in paired])
        correlation = float(np.corrcoef(fused, plane)[0, 1])
        inner = np.abs(fused) <= 12.0
        inner_correlation = (
            float(np.corrcoef(fused[inner], plane[inner])[0, 1]) if inner.sum() >= 4 else math.nan
        )
        checks.append(
            _check(
                "swing direction correlates positively with the fused club path",
                correlation > 0.0,
                f"r = {correlation:+.2f} over n = {len(paired)}; "
                f"r = {inner_correlation:+.2f} over the n = {int(inner.sum())} shots whose "
                "fused path is inside +-12 deg",
            )
        )
        track = np.asarray([r["track_club_path_deg"] for r in good])
        checks.append(
            _check(
                "track club path correlates positively with the fused club path",
                float(np.corrcoef(fused, track)[0, 1]) > 0.0,
                f"r = {np.corrcoef(fused, track)[0, 1]:+.2f}; track path sd "
                f"{np.std(track, ddof=1):.2f} deg vs fused path sd {np.std(fused, ddof=1):.2f} deg",
            )
        )
    return checks


def main() -> int:
    """Reconstruct every shot's head track and report the plane metrics."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--copy-plots-to", default=None)
    parser.add_argument("--boresight", choices=("ball", "raw"), default="ball")
    parser.add_argument("--shot", type=int, action="append", default=None)
    parser.add_argument("--exclude-shot", type=int, action="append", default=[1])
    parser.add_argument("--no-sheets", action="store_true")
    args = parser.parse_args()
    session = _session_path(args.session, parser)
    out = Path(args.out).expanduser() if args.out else Path(tempfile.mkdtemp(prefix="swingplane_"))
    out.mkdir(parents=True, exist_ok=True)
    sheet_dir = None if args.no_sheets else out / "masks"
    if sheet_dir is not None:
        sheet_dir.mkdir(parents=True, exist_ok=True)

    with (session / "shots.csv").open(newline="", encoding="utf-8") as handle:
        rows = {int(row["shot_number"]): row for row in csv.DictReader(handle)}

    records: list[dict[str, Any]] = []
    for shot_dir in sorted((session / "shots").iterdir()):
        if not shot_dir.is_dir():
            continue
        number = int(shot_dir.name.split("_", 2)[1])
        if number in set(args.exclude_shot) or (args.shot and number not in set(args.shot)):
            continue
        record = analyse_shot(shot_dir, rows[number], args.boresight, sheet_dir)
        records.append(record)
        print(
            f"shot {number:3d} {record['club']:7s} {record['status']:6s} "
            f"n={record.get('n_points', 0):2d} "
            f"plane={record.get('swing_plane_deg', float('nan')):6.2f} "
            f"dir={record.get('swing_direction_deg', float('nan')):+6.2f} "
            f"r={record.get('arc_radius_m', float('nan')):5.2f}m "
            f"v={record.get('in_plane_speed_mph', float('nan')):6.1f}mph "
            f"ops={record.get('ops_club_speed_mph') or float('nan'):6.1f}mph "
            f"{record['reason']}"
        )

    good = [r for r in records if r["status"] == "ok"]
    _write_csv(out / "swing_plane_per_shot.csv", records)
    point_rows = [
        {
            "shot_number": record["shot_number"],
            "club": record["club"],
            "frame": point.frame,
            "u_px": point.u_px,
            "v_px": point.v_px,
            "head_area_px": point.head_area_px,
            "range_mm": point.range_mm,
            "world_x_mm": float(point.world_mm[0]),
            "world_y_mm": float(point.world_mm[1]),
            "world_z_mm": float(point.world_mm[2]),
            "post_impact": point.post_impact,
        }
        for record in records
        for point in record.get("_points", [])
    ]
    _write_csv(out / "swing_plane_track_points.csv", point_rows)

    plots = _plots(good, out) if good else []
    if args.copy_plots_to:
        destination = Path(args.copy_plots_to).expanduser()
        destination.mkdir(parents=True, exist_ok=True)
        for plot in plots:
            (destination / plot.name).write_bytes(plot.read_bytes())

    per_club = {
        club: {
            name: _summary(np.asarray([r[name] for r in good if r["club"] == club], dtype=float))
            for name in (
                "swing_plane_deg",
                "swing_direction_deg",
                "arc_radius_m",
                "in_plane_speed_mph",
                "speed_ratio_vs_ops",
                "track_club_path_deg",
                "track_attack_angle_deg",
                "derivative_club_path_deg",
                "derivative_attack_angle_deg",
                "contact_world_x_mm",
                "contact_world_z_mm",
                "plane_rms_residual_mm",
                "pre_only_swing_plane_deg",
                "pre_only_arc_radius_m",
                "n_points",
            )
        }
        for club in sorted({r["club"] for r in good})
    }
    checks = _checks(good)
    report = {
        "caveat": (
            "CONSISTENCY ONLY. No ground truth exists; nothing here is scored against a "
            "launch monitor. Post-impact ranges assume the pre-impact radar range rate "
            "continues, which is an ASSUMPTION, not a measurement."
        ),
        "session": str(session),
        "boresight": args.boresight,
        "counts": {
            "attempted": len(records),
            "reconstructed": len(good),
            "low_quality": [
                {"shot": r["shot_number"], "reason": r["reason"]}
                for r in records
                if r["status"] == "low_quality"
            ],
            "failed": [
                {"shot": r["shot_number"], "reason": r["reason"]}
                for r in records
                if r["status"] == "failed"
            ],
        },
        "per_club": per_club,
        "checks": checks,
    }
    (out / "swing_plane_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\n{report['caveat']}")
    for club, block in per_club.items():
        print(f"\n{club}")
        for name, stats in block.items():
            if not stats.get("n"):
                continue
            print(
                f"  {name:24s} n={stats['n']:2d} median {stats['median']:8.2f}  "
                f"mean {stats['mean']:8.2f}  sd {stats['sd']:6.2f}  "
                f"[{stats['min']:8.2f}, {stats['max']:8.2f}]"
            )
    print("\nsanity checks")
    for check in checks:
        print(f"  [{check['result']}] {check['check']}: {check['detail']}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
