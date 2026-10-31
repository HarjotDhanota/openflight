"""The ball's best-available-source chains, shared by the kiosk and its replays."""

from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera import fallback_chain as chain, fusion_processing as fp
from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from tests.camera_fallback_scene import render_scene

RADAR_HEIGHT = 0.052


def _shot(**values):
    return Shot(ball_speed_mph=90.0, timestamp=datetime(2026, 10, 25, 9, 0, 0), **values)


def _size_record(range_m=2.0):
    return {
        "status": "measured_from_size",
        "slant_range_m": range_m,
        "uncertainty_m": 0.17,
        "note": "the resting ball's own size",
    }


def test_the_shot_s_own_iwr_range_wins_and_nothing_else_runs():
    resolution = chain.ball_range_resolution(
        shot_range={
            "ball_range_m": 1.91,
            "ball_range_source": "iwr_post_impact",
            "ball_range_uncertainty_m": 0.115,
        },
        prior=SimpleNamespace(range_m=2.3, uncertainty_m=0.2, source="setup"),
        size_route=lambda: pytest.fail("the size route must not run"),
    )

    assert (resolution.value, resolution.label, resolution.source) == (
        1.91,
        "measured",
        "iwr_post_impact",
    )


def test_without_an_iwr_range_the_setup_s_prior_stands_in_estimated():
    resolution = chain.ball_range_resolution(
        shot_range={"ball_range_reason": "the IWR capture was truncated"},
        prior=SimpleNamespace(range_m=2.3, uncertainty_m=0.2, source="setup"),
        size_route=lambda: pytest.fail("the size route must not run"),
    )

    assert (resolution.value, resolution.label, resolution.source) == (
        2.3,
        "estimated",
        "camera_prior",
    )
    assert resolution.tried == (("iwr_post_impact", "the IWR capture was truncated"),)


def test_with_neither_the_resting_ball_s_size_gives_the_range_experimental():
    resolution = chain.ball_range_resolution(
        shot_range={"ball_range_reason": "no IWR capture matched this shot"},
        prior=None,
        size_route=_size_record,
    )

    assert resolution.value == 2.0
    assert resolution.label == "experimental"
    assert resolution.source == "camera_ball_size"
    assert resolution.uncertainty == 0.17
    assert [name for name, _why in resolution.tried] == ["iwr_post_impact", "camera_prior"]


def test_a_failing_size_route_leaves_the_range_not_measured_with_every_reason():
    def broken():
        raise RuntimeError("no frames")

    resolution = chain.ball_range_resolution(
        shot_range={"ball_range_reason": "no IWR capture matched this shot"},
        prior=None,
        size_route=broken,
    )

    assert not resolution.present
    assert "no IWR capture matched this shot" in resolution.reason
    assert "RuntimeError: no frames" in resolution.reason


def test_the_resolved_range_goes_on_the_shot_with_its_trail():
    shot = _shot()
    resolution = chain.ball_range_resolution(
        shot_range={"ball_range_reason": "no IWR capture matched this shot"},
        prior=None,
        size_route=_size_record,
    )

    chain.apply_ball_range(shot, resolution)

    assert shot.ball_range_m == 2.0
    assert shot.ball_range_source == "camera_ball_size"
    assert shot.ball_range_label == "experimental"
    assert shot.ball_range_uncertainty_m == 0.17
    assert "no IWR capture matched this shot" in shot.ball_range_reason
    trail = shot.input_sources["ball_range"]
    assert trail["route"] == "camera_ball_size"
    assert trail["details"]["status"] == "measured_from_size"
    assert shot.to_dict()["metrics"]["ball_range"]["status"] == "experimental"


def test_radar_launch_wins_and_the_camera_fallback_stays_a_second_opinion():
    shot = _shot(
        launch_angle_vertical=14.0,
        launch_angle_vertical_source="radar",
        inclinometer={"applied_to": ["camera", "iwr6843"]},
    )
    fallback = {
        "status": "accepted",
        "vertical_deg": 12.0,
        "vertical_sd_deg": 1.7,
        "horizontal_deg": 2.0,
        "horizontal_sd_deg": 2.7,
        "n_points": 5,
    }

    resolved = chain.launch_resolutions(shot, fallback)

    assert resolved["launch_vertical"].source == "radar"
    assert resolved["launch_vertical"].label == "measured"
    # no sensor horizontal: the fallback's fills it
    assert resolved["launch_horizontal"].source == "camera_fallback"
    assert resolved["launch_horizontal"].label == "experimental"


def test_without_the_radar_the_camera_fallback_gives_the_launch():
    shot = _shot(launch_angle_vertical_reason="no IWR capture matched this shot")
    fallback = {
        "status": "accepted",
        "vertical_deg": 12.0,
        "vertical_sd_deg": 1.7,
        "horizontal_deg": 2.0,
        "horizontal_sd_deg": 2.7,
        "n_points": 5,
    }

    resolved = chain.launch_resolutions(shot, fallback)["launch_vertical"]

    assert (resolved.value, resolved.label, resolved.source) == (
        12.0,
        "experimental",
        "camera_fallback",
    )
    assert resolved.uncertainty == 1.7
    assert resolved.tried == (("radar", "no IWR capture matched this shot"),)


def test_a_withheld_fallback_leaves_the_launch_not_measured_with_both_reasons():
    shot = _shot()

    resolved = chain.launch_resolutions(
        shot, {"status": "withheld_insufficient_track", "reason": "1 ball point"}
    )["launch_vertical"]

    assert not resolved.present
    assert (
        resolved.reason
        == "radar: no radar vertical launch on this shot; camera_fallback: 1 ball point"
    )


def _scene_context(scene, *, range_record=None, ops=True):
    x, y, _diameter = scene.rest_px()
    geometry = EffectiveCameraGeometryInputs(
        camera_height_m=float(scene.camera.origin_lfu[2]),
        radar_height_m=RADAR_HEIGHT,
        tee_slant_range_m=float(np.linalg.norm(scene.ball_lfu - np.array([0, 0, RADAR_HEIGHT]))),
        ball_height_m=float(scene.ball_lfu[2]),
        camera_lateral_offset_m=0.0,
        camera_forward_offset_m=0.0,
        image_width_px=scene.camera.width,
        image_height_px=scene.camera.height,
        horizontal_pixel_sign=1.0,
        roll_correction_deg=0.0,
        ball_horizontal_output_offset_deg=None,
        ball_diameter_m=0.0427,
    )
    archive = {
        "frames": scene.frames,
        "host_timestamp_ns": scene.sensor_timestamp_ns,
        "sensor_timestamp_ns": scene.sensor_timestamp_ns,
        "exposure_us": np.full(len(scene.frames), scene.exposure_us),
        "trigger_host_timestamp_ns": np.asarray(
            scene.sensor_timestamp_ns[scene.first_post_index - 1]
        ),
        "pre_trigger_count": np.asarray(scene.first_post_index),
        "pre_trigger_count_by_exposure": np.asarray(scene.first_post_index),
        "_capture_npz_sha256": "scene-hash",
    }
    context = fp.build_context(
        geometry=geometry,
        lighting_eligible=True,
        ball_range_evidence=None,
        club_range_evidence=None,
        ops_ball_speed_mph=scene.ops_radial_mph(),
        ops_club_speed_mph=None,
        iwr_vertical_deg=None,
        iwr_horizontal_deg=None,
        iwr_horizontal_confidence=None,
        club=ClubType.IRON_7,
        capture_npz_sha256="scene-hash",
        session_uuid="session-s",
        shot_number=1,
        setup_ball={"x": x + 4.0, "y": y - 3.0, "diameter_px": 46.0},
        ball_range=range_record
        or {"source": "iwr_post_impact", "label": "measured", "uncertainty_m": 0.05},
        camera_fallback={
            "focal_px": scene.camera.focal_px,
            "focal_source": "unit_calibration",
            "focal_rel_sd": 0.02,
            "ops_lfu": [float(v) for v in scene.ops_lfu] if ops else None,
            "horizontal_offset_sd_deg": 0.0,
            "lis3dh_camera_pitch_deg": None,
        },
    )
    return context, archive


def test_the_shared_processing_gives_the_camera_fallback_launch_without_any_iwr():
    scene = render_scene(vertical_deg=17.0, horizontal_deg=-2.0, speed_mph=85.0, pitch_deg=-0.8)

    context, archive = _scene_context(scene)
    result = fp.process_camera_fusion(context, archive)

    fallback = result["ball_fallback"]
    assert fallback["status"] == "accepted", fallback["reason"]
    assert fallback["vertical_deg"] == pytest.approx(17.0, abs=0.6)
    assert fallback["horizontal_deg"] == pytest.approx(-2.0, abs=0.6)
    assert fallback["label"] == "experimental"
    assert fallback["inputs"]["pitch_source"] == "resting_ball"
    assert fallback["camera"]["rest_centre_kind"] == "whole_ball_resting_outline"
    assert {point["centre_kind"] for point in fallback["track"]} == {"highlight_centroid"}
    # the IWR-depth estimator has no range track and says so; the fallback stands alone
    assert result["ball_estimate"]["status"].startswith("rejected")


def test_an_old_context_replays_exactly_as_recorded_without_the_fallback():
    scene = render_scene()
    context, archive = _scene_context(scene)
    context = {k: v for k, v in context.items() if k not in ("camera_fallback", "sha256")}
    from openflight.rig_geometry import geometry_fingerprint  # noqa: PLC0415

    context["sha256"] = geometry_fingerprint(context)

    assert "ball_fallback" not in fp.process_camera_fusion(context, archive)


def test_a_failing_fallback_stage_is_its_own_error_and_the_rest_still_runs(monkeypatch):
    scene = render_scene()
    context, archive = _scene_context(scene)
    monkeypatch.setattr(fp, "run_ball_fallback", lambda *_a, **_k: 1 / 0)

    result = fp.process_camera_fusion(context, archive)

    assert result["ball_fallback"]["status"] == "error"
    assert "ZeroDivisionError" in result["errors"]["ball_fallback"]
    assert "club_delivery" in result and "ball_estimate" in result


def test_a_weak_radar_horizontal_stands_only_after_the_fallback_is_gone():
    shot = _shot(launch_angle_horizontal=3.2, launch_angle_horizontal_source="radar_low_coherence")
    fallback = {"status": "accepted", "vertical_deg": 12.0, "horizontal_deg": 1.0, "n_points": 4}

    # both experimental on a nominal lens; the radar's failed its own gate, the camera's
    # passed: the camera's wins
    assert chain.launch_resolutions(shot, fallback)["launch_horizontal"].source == (
        "camera_fallback"
    )
    withheld = chain.launch_resolutions(shot, {"status": "withheld_residual", "reason": "x"})
    weak = withheld["launch_horizontal"]
    assert (weak.source, weak.label, weak.route) == (
        "radar_low_coherence",
        "experimental",
        "weak_sensor",
    )


def test_an_implausible_sensor_horizontal_is_refused_with_why():
    shot = _shot(launch_angle_horizontal=70.6, launch_angle_horizontal_source="radar_low_coherence")

    resolved = chain.launch_resolutions(shot, None)["launch_horizontal"]

    assert not resolved.present
    assert "shank" in resolved.reason


PARTIAL = {
    "ball_range_m": 1.91,
    "ball_range_source": "iwr_post_impact",
    "ball_range_uncertainty_m": 0.115,
    "ball_range_label": "iwr_partial",
    "ball_range_reason": "from a partial IWR dump: frames 40-52",
}


def test_a_partial_dump_range_keeps_its_label_and_its_reason():
    resolution = chain.ball_range_resolution(
        shot_range=PARTIAL,
        prior=SimpleNamespace(range_m=2.3, uncertainty_m=0.2, source="setup"),
        size_route=lambda: pytest.fail("the size route must not run"),
    )

    assert (resolution.value, resolution.label, resolution.source) == (
        1.91,
        "iwr_partial",
        "iwr_post_impact",
    )
    assert resolution.note == "from a partial IWR dump: frames 40-52"


def test_a_partial_dump_range_goes_on_the_shot_with_its_reason():
    shot = _shot()

    chain.apply_ball_range(shot, chain.ball_range_resolution(shot_range=PARTIAL))

    assert shot.ball_range_label == "iwr_partial"
    assert shot.ball_range_reason == "from a partial IWR dump: frames 40-52"
    assert shot.input_sources["ball_range"]["label"] == "iwr_partial"


COMPETING = {
    "ball_range_m": 2.10,
    "ball_range_source": "iwr_post_impact",
    "ball_range_uncertainty_m": 0.05,
    "ball_range_label": "estimated",
    "ball_range_reason": "two IWR tracks compete: launch points 2.10 m and 2.31 m; camera 2.12 m",
}


def test_an_iwr_range_two_tracks_compete_for_keeps_estimated_and_its_reason():
    shot = _shot()
    resolution = chain.ball_range_resolution(
        shot_range=COMPETING, size_route=lambda: pytest.fail("never run")
    )

    chain.apply_ball_range(shot, resolution)

    assert (resolution.label, resolution.source) == ("estimated", "iwr_post_impact")
    assert shot.ball_range_label == "estimated"
    assert shot.ball_range_reason.startswith("two IWR tracks compete")


# ---------------------------------------------------------------- the vertical (30 Oct 2026)

ACCEPTED = {
    "status": "accepted",
    "vertical_deg": 18.58,
    "vertical_sd_deg": 2.5,
    "horizontal_deg": 2.0,
    "horizontal_sd_deg": 2.7,
    "n_points": 6,
}


def _radar_vertical(value, **values):
    return _shot(
        launch_angle_vertical=value,
        launch_angle_vertical_source="radar",
        inclinometer={"applied_to": ["camera", "iwr6843"]},
        **values,
    )


def test_a_radar_vertical_on_the_camera_prior_range_is_estimated_in_the_chain():
    # Oct 27 shot 2: 35.87 on the camera prior's 3.217 m was "measured" in the chain
    shot = _radar_vertical(35.87, ball_range_source="camera_prior", ball_range_label="estimated")

    resolved = chain.launch_resolutions(shot, None)["launch_vertical"]

    assert (resolved.source, resolved.label) == ("radar", "estimated")
    assert "camera prior" in resolved.note


def test_the_plausible_vertical_bounds_are_named_and_generous():
    assert (chain.MIN_PLAUSIBLE_VERTICAL_DEG, chain.MAX_PLAUSIBLE_VERTICAL_DEG) == (-10.0, 60.0)
    assert chain.implausible_vertical_reason(-10.0) is None
    assert chain.implausible_vertical_reason(60.0) is None
    assert chain.implausible_vertical_reason(None) is None
    assert "-10" in chain.implausible_vertical_reason(-10.5)
    assert "60" in chain.implausible_vertical_reason(61.0)


@pytest.mark.parametrize("value", [72.4, -14.0])
def test_an_implausible_radar_vertical_is_refused_and_the_camera_stands_in(value):
    resolved = chain.launch_resolutions(_radar_vertical(value), ACCEPTED)["launch_vertical"]

    assert (resolved.source, resolved.value) == ("camera_fallback", 18.58)
    ((route, why),) = resolved.tried
    assert route == "radar" and f"{value:+.1f}" in why


def test_an_implausible_camera_vertical_is_refused_too():
    shot = _shot(launch_angle_vertical_reason="no IWR capture matched this shot")

    resolved = chain.launch_resolutions(shot, {**ACCEPTED, "vertical_deg": 75.0})["launch_vertical"]

    assert not resolved.present
    assert "camera_fallback: camera_fallback +75.0" in resolved.reason


def test_a_radar_vertical_far_from_the_camera_stays_first_but_reads_experimental():
    # Oct 27 shot 3: radar 29.99 against an accepted 18.58 +- 2.5 (4.6 sigma); radar is
    # primary for the ball, so it stays, labelled experimental with both values named
    resolved = chain.launch_resolutions(_radar_vertical(29.99), ACCEPTED)["launch_vertical"]

    assert (resolved.source, resolved.value, resolved.label) == ("radar", 29.99, "experimental")
    assert "29.99" in resolved.note and "18.58" in resolved.note
    assert "4.6 sigma" in resolved.note


def test_a_radar_vertical_within_three_sigma_of_the_camera_stays_measured():
    # Oct 27 shot 4: 28.50 against 21.52 +- 3.13 (2.2 sigma)
    fallback = {**ACCEPTED, "vertical_deg": 21.52, "vertical_sd_deg": 3.13}

    resolved = chain.launch_resolutions(_radar_vertical(28.50), fallback)["launch_vertical"]

    assert (resolved.source, resolved.label, resolved.note) == ("radar", "measured", None)
