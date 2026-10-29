"""Optional current-pixel club observations inside the post-shot impact worker."""

from __future__ import annotations

import dataclasses
import hashlib

import numpy as np

from ..rig_geometry import geometry_fingerprint
from . import club_acquisition, impact_location
from .club_sequence import ClubAssociationGraph, ClubFrameIdentity, bind_observation

# pylint: disable=no-member


def _window(prepared, stamps):
    if prepared.window:
        return prepared.window, {
            "source": "legacy_prepared_window",
            "first_touch_interval_ns": None,
        }
    departure = impact_location.find_contact(prepared.frames, prepared.ball)
    selection = {
        "source": "image_departure_only",
        "departure": dataclasses.asdict(departure),
        "first_touch_interval_ns": None,
        "metric_preparation_refusal": prepared.contact.reason,
    }
    if departure.status != "ok" or departure.last_rest_frame is None:
        return (), selection
    last = departure.last_rest_frame
    first = int(np.searchsorted(stamps, stamps[last] - impact_location.WINDOW_BEFORE_S * 1e9))
    return tuple(range(first, min(len(stamps), last + 2))), selection


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
    if prepared is None:
        return {**result, "status": "withheld_no_prepared_contact_window"}
    stamps = clip.extra.get("capture_frame_timestamps_ns")
    exposures = clip.extra.get("capture_frame_exposure_us")
    count = len(prepared.frames)
    if stamps is None or exposures is None or len(stamps) != count or len(exposures) != count:
        return {**result, "status": "withheld_missing_per_frame_sensor_time_or_exposure"}
    window, selection = _window(prepared, stamps)
    result["window_selection"] = selection
    if not window:
        return {**result, "status": "withheld_no_image_departure_window"}
    frames = prepared.frames
    first = min(window)
    past = min(8, first)
    if past < 3:
        return {**result, "status": "withheld_no_past_background"}
    background = np.median(frames[:past].astype(np.float32), axis=0)
    variation = frames[:past].astype(np.float32).std(axis=0)
    noise = float(np.std(frames[past - 1].astype(float) - frames[past - 2]) / np.sqrt(2))
    ball = prepared.ball
    trackers = {}
    mapping = {
        "roi_origin_px": prepared.roi_origin,
        "full_analysis_size_px": prepared.full_size,
        "analysis_flipped": prepared.flipped,
        "optics_key": clip.extra.get("optics_template_key"),
    }
    view_id = geometry_fingerprint(mapping)
    for index in window:
        frame = frames[index]
        veto = None if prepared.masks is None else prepared.masks.ball_vetoes.get(index)
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
        branches = club_acquisition.current_hypotheses(
            frame,
            background,
            variation,
            ball_diameter_px=ball.diameter_px,
            noise_dn=noise,
            ball_veto=veto,
            ball_observation_ids=[f"own_prepared_ball_veto:{identity.capture_sha256}:{index}"]
            if veto is not None
            else (),
        )
        records = []
        for branch in branches:
            key = branch["ball_identity_branch"]
            if key not in trackers:
                trackers[key] = ClubAssociationGraph(
                    motion_scale_px_s=10000, base_margin_px=12, reacquire_after_ns=15_000_000
                )
            packet = bind_observation(
                frame,
                branch["parts"],
                identity,
                expected_identity=identity,
                background_frame_indices=range(past),
            )
            records.append(
                {
                    **branch,
                    "identity": dataclasses.asdict(identity),
                    "coordinate_mapping": mapping,
                    "association": trackers[key].update(packet),
                    "visibility_input": (
                        "own uncertain current flying-ball proposal; separate identity branches"
                    ),
                }
            )
        result["frames"].append({**records[0], "alternatives": records[1:]})
    return {**result, "status": "current_image_hypotheses_only"}
