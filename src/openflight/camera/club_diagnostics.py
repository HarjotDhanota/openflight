"""Optional current-pixel club observations inside the post-shot impact worker."""

from __future__ import annotations

import dataclasses
import hashlib

import cv2
import numpy as np

from ..constants import BALL_DIAMETER_MM
from ..rig_geometry import geometry_fingerprint
from . import club_contours, club_observations, head_track
from .club_sequence import ClubAssociationGraph, ClubFrameIdentity, bind_observation

# pylint: disable=no-member


def analyse_clip(clip):
    """Analyse only the owning preparation's images, ball and current visibility.

    Contact-window scheduling is future-assisted post-shot work. Individual part
    observations use current pixels and past background, never readiness state.
    """
    result = {
        "schema": "openflight.club_postshot_diagnostics.v1",
        "frames": [],
        "analysis_mode": "postshot_contact_window",
        "future_assisted_frame_selection": True,
        "affects_live_readiness": False,
        "physical_pose": None,
        "strike": None,
    }
    prepared = clip.prepared
    if prepared is None or not prepared.window:
        return {**result, "status": "withheld_no_prepared_contact_window"}
    stamps = clip.extra.get("capture_frame_timestamps_ns")
    exposures = clip.extra.get("capture_frame_exposure_us")
    count = len(prepared.frames)
    if stamps is None or exposures is None or len(stamps) != count or len(exposures) != count:
        return {**result, "status": "withheld_missing_per_frame_sensor_time_or_exposure"}
    frames = prepared.frames
    first = min(prepared.window)
    past = min(8, first)
    if past < 3:
        return {**result, "status": "withheld_no_past_background"}
    background = np.median(frames[:past].astype(np.float32), axis=0)
    variation = frames[:past].astype(np.float32).std(axis=0)
    noise = float(np.std(frames[past - 1].astype(float) - frames[past - 2]) / np.sqrt(2))
    ball = prepared.ball
    scale = ball.diameter_px / BALL_DIAMETER_MM
    band = max(3, head_track.SHAFT_BAND_M * 1000 * scale)
    tracker = ClubAssociationGraph(
        motion_scale_px_s=10000, base_margin_px=12, reacquire_after_ns=15_000_000
    )
    mapping = {
        "roi_origin_px": prepared.roi_origin,
        "full_analysis_size_px": prepared.full_size,
        "analysis_flipped": prepared.flipped,
        "optics_key": clip.extra.get("optics_template_key"),
    }
    view_id = geometry_fingerprint(mapping)
    for index in prepared.window:
        frame = frames[index]
        delta = frame.astype(float) - background
        strong = abs(delta) > np.maximum(4 * variation, 18)
        walk = abs(delta) > max(3 * noise, 8)
        veto = None if prepared.masks is None else prepared.masks.ball_vetoes.get(index)
        if veto is not None:
            yy, xx = np.indices(frame.shape)
            disk = (xx - veto[0]) ** 2 + (yy - veto[1]) ** 2 <= veto[2] ** 2
            strong[disk] = walk[disk] = False
        axis = head_track.observe_shaft_axis(
            strong,
            walk,
            min_length_px=head_track.SHAFT_MIN_LENGTH_M * 1000 * scale,
            band_px=band,
            gap_px=max(3, head_track.FERRULE_GAP_M * 1000 * scale),
            golfer_on_left=True,
        )
        identity = ClubFrameIdentity(
            clip.extra["capture_npz_sha256"],
            int(index),
            hashlib.sha256(frame.tobytes()).hexdigest(),
            int(stamps[index]),
            float(exposures[index]),
            "recorded_start",
            view_id,
            clip.extra.get("template_capture_context"),
            clip.extra.get("template_capture_context"),
            clip.extra.get("optics_template_key"),
            clip.extra.get("equipment_id"),
        )
        parts, contours = None, None
        if axis is not None and axis.end_px is not None:
            geometry = {
                "anchor": axis.end_px,
                "axis": axis.direction_px,
                "shaft_band_px": band,
                "search_radius_px": 3 * ball.diameter_px,
                "ball_veto": veto,
            }
            parts = club_observations.observe_parts(frame, background, variation, geometry)
            residual = cv2.GaussianBlur(delta.astype(np.float32), (5, 5), 0.7)
            contours = club_contours.contour_hypotheses(
                frame.shape, parts, geometry, abs(residual) / np.maximum(variation, 1)
            )
        packet = bind_observation(
            frame, parts, identity, expected_identity=identity, background_frame_indices=range(past)
        )
        result["frames"].append(
            {
                "identity": dataclasses.asdict(identity),
                "coordinate_mapping": mapping,
                "axis": None if axis is None else dataclasses.asdict(axis),
                "parts": parts,
                "contours": contours,
                "association": tracker.update(packet),
                "visibility_input": (
                    "own preparation's uncertain current flying-ball veto; no stale resting veto"
                ),
            }
        )
    return {**result, "status": "current_image_hypotheses_only"}
