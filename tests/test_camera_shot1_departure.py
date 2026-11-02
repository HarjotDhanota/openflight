"""Oct 27 shot 1's departure through the camera ball estimate (C9a, 3 Nov 2026).

The clip (tests/fixtures/camera_oct27_shot1: the frames cropped to the resting ball and
its whole departure, with the clip's clocks) went to ``rejected_no_stable_path``. Two
causes, each fixed where it lives:

1. depth: the estimate read the ball's range against time only from an accepted IWR
   walk; on this config the IWR has none (C7c). The located tee (the camera's resting
   ball range, C8a) plus the OPS ball speed from the camera's own contact frame gives it.
2. appearance: the path gate wanted a bright (mean >= 195) round ball; at a 298 us
   exposure the lit cap of the ball is a half disk of ~120 counts, at rest and in flight
   alike. The gate now compares a candidate with the resting ball's own lit cap at the
   same threshold (a saturated round ball keeps the old 195 / 0.55).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from openflight.camera import ball_flight
from openflight.camera.calibrated_projection import build_calibrated_camera_model
from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs
from openflight.rig_geometry import RigGeometry, camera_rdf_offset_to_target_lfu

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "camera_oct27_shot1"
SET_DIR = REPO / "config" / "camera-calibration" / "6mm"
RIG = RigGeometry.from_json(REPO / "config" / "enclosure_v3_rig_geometry.json")


def _clip():
    inputs = json.loads((FIXTURE / "clip_inputs.json").read_text(encoding="utf-8"))
    crop = np.load(FIXTURE / "frames_crop.npz")
    (r0, r1), (c0, c1) = inputs["crop"]["rows"], inputs["crop"]["cols"]
    frames = np.empty(inputs["crop"]["full_shape"], dtype=np.uint8)
    frames[:] = np.uint8(np.median(crop["frames"][:20]))
    frames[:, r0:r1, c0:c1] = crop["frames"]
    archive = {name: crop[name] for name in crop.files if name != "frames"}
    return frames, archive, inputs


def _geometry(inputs):
    from openflight.server import _calibrated_observed_roll_deg  # noqa: PLC0415

    optical = json.loads((SET_DIR / "640x400.optical.json").read_text(encoding="utf-8"))
    placement = json.loads((SET_DIR / "640x400.placement.json").read_text(encoding="utf-8"))
    pose = inputs["inclinometer"]
    lens = np.array([0.0, 0.0, RIG.lens_height_above_floor_mm / 1000.0])
    radar = lens + np.asarray(camera_rdf_offset_to_target_lfu(RIG.iwr_origin_mm))
    model = build_calibrated_camera_model(
        optical,
        placement,
        observed_pitch_deg=pose["calibrated_pitch_deg"],
        observed_roll_deg=_calibrated_observed_roll_deg(pose, placement),
        origins={
            "camera_origin_lfu": lens.tolist(),
            "radar_origin_lfu": radar.tolist(),
            "basis": "rig_lens_and_iwr_phase_centre",
        },
    )
    candidate = optical.get("candidate", optical)
    effective = EffectiveCameraGeometryInputs(
        camera_height_m=float(lens[2]),
        radar_height_m=float(radar[2]),
        tee_slant_range_m=inputs["located_tee_range_m"],
        ball_height_m=0.02135,
        camera_lateral_offset_m=float(-radar[0]),
        camera_forward_offset_m=float(-radar[1]),
        image_width_px=640,
        image_height_px=400,
        horizontal_pixel_sign=1.0,
        roll_correction_deg=0.0,
        ball_horizontal_output_offset_deg=None,
        ball_diameter_m=0.0427,
    ).with_calibrated_model(
        model.snapshot,
        {
            "status": "unverified",
            "reasons": ["calibration remains unqualified"],
            "accuracy_qualified": False,
            "mode_profile_sha256": candidate["mode_profile_sha256"],
        },
    )
    return effective.ball_geometry()


def _estimate(**overrides):
    frames, archive, inputs = _clip()
    kwargs = {
        "trigger_ns": int(archive["trigger_host_timestamp_ns"]),
        "range_evidence": None,
        "geometry": _geometry(inputs),
        "ops_ball_speed_mph": inputs["ops_ball_speed_mph"],
        "sensor_timestamps_ns": archive["sensor_timestamp_ns"],
        "trigger_frame_index": int(archive["pre_trigger_count_by_exposure"]) - 1,
        "box_resting_ball": inputs["box_resting_ball"],
        "located_tee": {
            "range_m": inputs["located_tee_range_m"],
            "uncertainty_m": inputs["located_tee_sigma_m"],
        },
        **overrides,
    }
    return ball_flight.estimate_camera_ball_flight(frames, archive["host_timestamp_ns"], **kwargs)


# --- 1. depth from the located tee and the OPS speed ----------------------------------------


def test_the_located_tee_and_the_ops_speed_give_the_ball_s_range_against_time():
    evidence = ball_flight.located_tee_range_evidence(1.814, 81.77)

    speed = 81.77 / ball_flight.MPH_PER_MS
    assert evidence.status == "accepted_located_tee"
    assert evidence.impact_t_s == 0.0
    res = evidence.geometry.range_res_m
    assert evidence.track.range_at(0.0, res) == pytest.approx(1.814)
    assert evidence.track.range_at(0.010, res) == pytest.approx(1.814 + 0.010 * speed)


def test_without_an_iwr_walk_the_calibrated_estimate_still_gets_depth():
    estimate = _estimate()

    assert estimate.status != "rejected_calibrated_requires_iwr_range"
    assert estimate.reference_ball_diagnostics["reference_source"] == "resting_ball_in_box"


def test_the_fusion_hands_the_located_tee_to_the_ball_estimate():
    from openflight.camera import fusion_processing  # noqa: PLC0415

    context = {
        "ball_range": {"source": "camera_resting_ball", "value": 1.814, "uncertainty": 0.029}
    }
    assert fusion_processing._located_tee(context) == {  # pylint: disable=protected-access
        "range_m": 1.814,
        "uncertainty_m": 0.029,
    }
    assert (
        fusion_processing._located_tee({"ball_range": {"source": "iwr_post_impact", "value": 2.0}})
        is None
    )  # pylint: disable=protected-access


# --- 2. the path's appearance gate against the resting ball's own lit cap -----------------


def test_a_saturated_round_rest_ball_keeps_the_old_appearance_gate():
    gate = ball_flight.appearance_gate(rest_intensity=255.0, rest_shape=0.0)

    assert gate == pytest.approx({"min_intensity": 195.0, "max_shape": 0.55})


def test_a_dim_half_lit_rest_ball_sets_a_gate_its_flight_passes():
    gate = ball_flight.appearance_gate(rest_intensity=119.6, rest_shape=0.85)

    assert gate["min_intensity"] == pytest.approx(119.6 * 195.0 / 255.0)
    assert gate["max_shape"] == pytest.approx(0.85 + 0.55)


def test_oct27_shot1_s_departure_is_found_with_its_launch_angles():
    estimate = _estimate()

    assert estimate.status in ("accepted", "low_consensus"), estimate.status
    assert estimate.depth_source == "located_tee_plus_ops_speed"
    assert estimate.n_points >= 5
    assert -5.0 <= estimate.vertical_deg <= 55.0
    assert -30.0 <= estimate.horizontal_deg <= 30.0
    assert estimate.parameter_mad_deg is not None
    assert math.isfinite(estimate.speed_mph)


def test_the_rest_cap_reads_a_half_lit_ball_and_a_saturated_one():
    from openflight.camera.club_motion import ReferenceBall  # noqa: PLC0415

    anchor = ReferenceBall(50.0, 50.0, 24.0, 452)
    yy, xx = np.mgrid[:100, :100]
    disk = (xx - 50) ** 2 + (yy - 50) ** 2 <= 144
    half = np.full((100, 100), 40, np.uint8)
    half[disk & (yy < 50)] = 120  # only the cap facing the light is above the threshold
    saturated = np.full((100, 100), 40, np.uint8)
    saturated[disk] = 255

    mean, shape = ball_flight._rest_cap(half, anchor, 100)  # pylint: disable=protected-access
    assert mean == pytest.approx(120.0)
    assert shape == pytest.approx(math.log(25 / 12), abs=0.1)
    assert ball_flight._rest_cap(saturated, anchor, 100) == pytest.approx((255.0, 0.0), abs=0.05)  # pylint: disable=protected-access
    # no cap above the threshold reads as the original gate
    assert ball_flight._rest_cap(np.full((100, 100), 40, np.uint8), anchor, 100) == (255.0, 0.0)  # pylint: disable=protected-access


# --- C9b: the club stage's depth --------------------------------------------------------


def test_without_an_iwr_club_walk_the_club_stage_takes_the_located_tee_and_the_ops_club_speed(
    monkeypatch,
):
    from openflight.camera import fusion_processing as fp  # noqa: PLC0415
    from openflight.rig_geometry import geometry_fingerprint  # noqa: PLC0415
    from tests.camera_fallback_scene import render_scene  # noqa: PLC0415
    from tests.test_fallback_chain import _scene_context  # noqa: PLC0415

    context, archive = _scene_context(
        render_scene(),
        range_record={
            "source": "camera_resting_ball",
            "value": 1.814,
            "uncertainty": 0.029,
            "label": "estimated",
        },
    )
    context = {key: value for key, value in context.items() if key != "sha256"}
    context["ops_club_speed_mph"] = 68.6
    context["sha256"] = geometry_fingerprint(context)
    seen = {}

    def spy(*_args, **kwargs):
        seen.update(kwargs)
        raise ValueError("stop after the inputs")

    monkeypatch.setattr(fp, "estimate_chained_delivery", spy)
    fp.process_camera_fusion(context, archive)

    evidence = seen["range_evidence"]
    assert evidence.status == "accepted_located_tee"
    res = evidence.geometry.range_res_m
    assert evidence.track.range_at(0.0, res) == pytest.approx(1.814)
    assert evidence.track.speed_ms == pytest.approx(68.6 / ball_flight.MPH_PER_MS)
