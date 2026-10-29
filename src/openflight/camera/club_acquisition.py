"""Current club acquisition retains alternative identities for uncertain ball disks."""

from __future__ import annotations

import dataclasses

import cv2
import numpy as np

from ..constants import BALL_DIAMETER_MM
from . import club_contours, club_observations, head_track

# pylint: disable=no-member


def current_hypotheses(
    frame,
    background,
    variation,
    *,
    ball_diameter_px,
    noise_dn,
    ball_veto,
    ball_observation_ids=(),
    axis_roi=None,
):
    """Keep excluded and unexcluded current-pixel branches, without selecting either.

    An inherited flying-ball disk does not establish object identity. The second
    branch tests whether its pixels belong to the club; it never establishes ball
    absence or authorizes changing the ball owner's result. ROI endpoints remain
    search anchors, not anatomical hosel locations.
    """
    if (
        frame.ndim != 2
        or frame.dtype != np.uint8
        or background.shape != frame.shape
        or variation.shape != frame.shape
        or not np.isfinite(background).all()
        or not np.isfinite(variation).all()
        or (variation < 0).any()
        or not np.isfinite([ball_diameter_px, noise_dn]).all()
        or ball_diameter_px <= 0
        or noise_dn < 0
    ):
        raise ValueError(
            "current uint8 image, finite background/noise and positive ball scale required"
        )
    height, width = frame.shape
    x0, y0, x1, y1 = (0, 0, width, height) if axis_roi is None else axis_roi
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError("axis ROI must lie inside the current view")
    if ball_veto is not None:
        veto = np.asarray(ball_veto, float)
        if veto.shape != (3,) or not np.isfinite(veto).all() or veto[2] <= 0:
            raise ValueError("finite positive-radius ball proposal required")
        if not ball_observation_ids:
            raise ValueError("own ball proposal lineage required")
    delta = frame.astype(float) - background
    strong = abs(delta) > np.maximum(4 * variation, 18)
    walk = abs(delta) > max(3 * noise_dn, 8)
    residual = cv2.GaussianBlur(delta.astype(np.float32), (5, 5), 0.7)
    scale = ball_diameter_px / BALL_DIAMETER_MM
    band = max(3, head_track.SHAFT_BAND_M * 1000 * scale)
    assumptions = (
        [("no_ball_veto_available", None)]
        if ball_veto is None
        else [("ball_excluded", tuple(veto)), ("ball_identity_uncertain", None)]
    )
    branches = []
    for label, used_veto in assumptions:
        supported, connected = strong.copy(), walk.copy()
        if used_veto is not None:
            yy, xx = np.indices(frame.shape)
            disk = (xx - used_veto[0]) ** 2 + (yy - used_veto[1]) ** 2 <= used_veto[2] ** 2
            supported[disk] = connected[disk] = False
        axis = head_track.observe_shaft_axis(
            supported[y0:y1, x0:x1],
            connected[y0:y1, x0:x1],
            min_length_px=head_track.SHAFT_MIN_LENGTH_M * 1000 * scale,
            band_px=band,
            gap_px=max(3, head_track.FERRULE_GAP_M * 1000 * scale),
            golfer_on_left=True,
        )
        if axis is not None:
            axis = dataclasses.replace(
                axis,
                point_px=(axis.point_px[0] + x0, axis.point_px[1] + y0),
                end_px=None if axis.end_px is None else (axis.end_px[0] + x0, axis.end_px[1] + y0),
                support_pixels_px=tuple((x + x0, y + y0) for x, y in axis.support_pixels_px),
            )
        parts, contours, geometry = None, None, None
        if axis is not None and axis.end_px is not None:
            geometry = {
                "anchor": axis.end_px,
                "axis": axis.direction_px,
                "shaft_band_px": band,
                "search_radius_px": 3 * ball_diameter_px,
                "ball_veto": used_veto,
            }
            parts = club_observations.observe_parts(frame, background, variation, geometry)
            contours = club_contours.contour_hypotheses(
                frame.shape, parts, geometry, abs(residual) / np.maximum(variation, 1)
            )
        branches.append(
            {
                "ball_identity_branch": label,
                "ball_veto_proposal": None if ball_veto is None else veto.tolist(),
                "ball_veto_used": used_veto,
                "ball_observation_ids": list(ball_observation_ids),
                "axis": None if axis is None else dataclasses.asdict(axis),
                "geometry": geometry,
                "parts": parts,
                "contours": contours,
                "physical_pose": None,
                "strike": None,
            }
        )
    return branches
