"""The kiosk resolves every metric it can, whichever sensor failed.

A synthetic clip through the kiosk's own path (``server._fuse_camera_measurements``
then ``_finalize_shot_detected``): with no IWR the camera fallback's range and launch
are used and labelled experimental; with the IWR's they win and the camera's launch is
the second opinion; a stage that raises is that stage's own reason.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import kiosk_extensions, server as server_module, shot_models
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from openflight.rig_geometry import RigGeometry
from tests.camera_fallback_scene import render_scene

RADAR_HEIGHT = 0.052


@pytest.fixture(scope="module")
def scene():
    return render_scene(vertical_deg=17.0, horizontal_deg=-2.0, speed_mph=85.0, pitch_deg=-0.6)


@pytest.fixture
def kiosk(monkeypatch, scene):
    """A fusion kiosk with a camera and the rig file; the IWR is set per test."""
    monkeypatch.setattr(kiosk_extensions, "_extensions", [shot_models.extension])
    x, y, _d = scene.rest_px()
    monkeypatch.setattr(
        server_module,
        "camera_capture_config",
        {
            "enabled": True,
            "width": scene.camera.width,
            "height": scene.camera.height,
            "mount_height_m": float(scene.camera.origin_lfu[2]),
            "lateral_offset_m": 0.0,
            "forward_offset_m": 0.0,
            "mirror_horizontal": False,
            "roll_correction_deg": 0.0,
            "horizontal_offset_deg": None,
            "setup_ball": {"x": x + 5.0, "y": y - 2.0, "diameter_px": 47.0},
            "lens": {"focal_px_by_arm": {"arm5": scene.camera.focal_px}},
        },
    )
    monkeypatch.setattr(
        server_module,
        "rig_geometry_config",
        {"enabled": True, "lens": {"focal_px_by_arm": {"arm5": scene.camera.focal_px}}},
    )
    monkeypatch.setattr(
        server_module,
        "rig_geometry",
        RigGeometry.from_json("config/enclosure_v3_rig_geometry.json"),
    )
    monkeypatch.setattr(
        server_module,
        "setup_handoff_config",
        {
            "scene": {"radar_height_m": RADAR_HEIGHT, "ball_height_m": float(scene.ball_lfu[2])},
            "target_line": {"status": "not_set"},
        },
    )
    monkeypatch.setattr(server_module, "ball_at_address_monitor", None)
    monkeypatch.setattr(server_module, "camera_optical_calibration", None)
    monkeypatch.setattr(
        server_module, "camera_capture_runtime", SimpleNamespace(camera_analysis_eligible=True)
    )
    monkeypatch.setattr(server_module, "camera_tilt_calibration_path", None)
    monkeypatch.setattr(server_module, "horizontal_offset_deg", None)
    monkeypatch.setattr(server_module, "ballistics_enabled", True)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(server_module, "_forward_shot_to_simulators", lambda shot: None)
    monkeypatch.setattr(server_module, "iwr6843_runtime", None)
    return monkeypatch


def _archive(scene) -> dict:
    return {
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


def _shot(scene, **values) -> Shot:
    shot = Shot(
        ball_speed_mph=scene.ops_radial_mph(),
        club_speed_mph=70.0,
        timestamp=datetime(2026, 10, 25, 9, 0, 0),
        shot_number=4,
        impact_timestamp=1000.0,
        club=ClubType.IRON_7,
        spin_rpm=2300.0,
        spin_confidence=0.44,
        spin_method="multitaper_ungated",
        **values,
    )
    shot.camera_fusion_session_uuid = "session-o"
    # the OPS readings after impact (full_speed): the radial speed rising as the ball's
    # line of sight from the OPS swings onto its path
    radial = shot.ball_speed_mph
    shot.ops_speed_track = {
        "clock": "ops_capture",
        "impact_ms": 40.0,
        "impact_source": "ops_transition",
        "impact_uncertainty_ms": 0.6,
        "window_ms": 128 / 30.0,
        "radial_resolution_mph": 0.05,
        "canonical_radial_mph": radial,
        "outbound": [[40.5 + 1.0667 * i, radial - 1.5 + 0.25 * i] for i in range(8)],
    }
    return shot


def _run(scene, shot, archive="default"):
    capture = SimpleNamespace(valid=True, path=None, metadata={})
    server_module._fuse_camera_measurements(  # pylint: disable=protected-access
        shot, capture, _archive(scene) if archive == "default" else archive
    )
    server_module._finalize_shot_detected(shot, emit_event="shot")  # pylint: disable=protected-access
    return shot.to_dict()["metrics"]


def test_without_the_iwr_the_camera_fallback_resolves_the_range_and_the_launch(kiosk, scene):
    shot = _shot(scene)
    shot.ball_range_reason = "no IWR capture matched this shot"

    metrics = _run(scene, shot)

    assert shot.ball_range_source == "camera_ball_size"
    assert metrics["ball_range"]["status"] == "experimental"
    assert metrics["ball_range"]["value"] == pytest.approx(
        float(np.linalg.norm(scene.ball_lfu - np.array([0.0, 0.0, RADAR_HEIGHT]))), rel=0.05
    )
    assert shot.launch_angle_vertical_source == "camera_fallback"
    assert shot.launch_angle_vertical == pytest.approx(17.0, abs=1.0)
    assert shot.launch_angle_horizontal_source == "camera_fallback"
    assert shot.launch_angle_horizontal == pytest.approx(-2.0, abs=1.0)
    for name in ("launch_vertical", "launch_horizontal"):
        assert metrics[name]["status"] == "experimental", name
    trail = shot.input_sources
    assert trail["launch_vertical"]["route"] == "camera_fallback"
    assert trail["ball_range"]["tried"][0]["reason"] == "no IWR capture matched this shot"


def test_every_downstream_metric_the_fallback_reaches_resolves_labelled_by_it(kiosk, scene):
    shot = _shot(scene)

    metrics = _run(scene, shot)

    # the full speed on the fallback's launch and the resting ball's bearing
    full = metrics["ball_speed_full"]
    assert full["value"] is not None
    assert full["status"] == "experimental"
    assert shot.input_sources["tee_side_offset"]["source"] == "camera_resting_ball"
    # the OPS spin is below the floor and there is no club: no formula spin stands in
    # (25 Oct 2026), the OPS's own reading stays, experimental, feeding nothing
    assert shot.input_sources["spin"]["route"] == "ops_below_floor"
    assert metrics["spin_rate"]["value"] is not None
    assert metrics["spin_rate"]["status"] == "experimental"
    # radial OPS speeds are sensor readings: untouched
    assert metrics["ball_speed"]["status"] == "measured"
    assert metrics["club_speed"]["status"] == "measured"
    # no club path anywhere: no spin axis, so no flight; said, not filled in
    assert metrics["spin_axis"]["value"] is None
    assert metrics["carry"]["value"] is None
    assert metrics["carry"]["reason"]


def test_with_the_iwr_s_launch_the_radar_wins_and_the_camera_is_a_second_opinion(kiosk, scene):
    shot = _shot(
        scene,
        launch_angle_vertical=16.4,
        launch_angle_vertical_source="radar",
        launch_angle_vertical_confidence=0.8,
    )
    shot.ball_range_m = 1.95
    shot.ball_range_source = "iwr_post_impact"
    shot.ball_range_uncertainty_m = 0.05
    shot.ball_range_label = "measured"

    metrics = _run(scene, shot)

    assert shot.launch_angle_vertical == 16.4
    assert shot.launch_angle_vertical_source == "radar"
    assert metrics["launch_vertical"]["status"] == "measured"
    assert shot.ball_range_source == "iwr_post_impact"
    assert metrics["ball_range"]["status"] == "measured"
    second = shot.camera_fallback_launch
    assert second["status"] == "accepted"
    assert second["vertical_deg"] == pytest.approx(17.0, abs=1.0)
    assert shot.input_sources["launch_vertical"]["route"] == "radar"


def test_without_a_camera_clip_the_radar_and_ops_still_resolve_everything_they_can(kiosk, scene):
    kiosk.setattr(
        server_module,
        "iwr6843_runtime",
        SimpleNamespace(
            calibration=SimpleNamespace(
                tee_range_m=None, radar_height_m=RADAR_HEIGHT, tee_ball_height_m=0.02135
            ),
            range_prior=None,
        ),
    )
    shot = _shot(
        scene,
        launch_angle_vertical=16.4,
        launch_angle_vertical_source="radar",
        launch_angle_vertical_confidence=0.8,
    )

    metrics = _run(scene, shot, archive=None)

    assert metrics["launch_vertical"]["status"] == "measured"
    assert shot.camera_fallback_launch["reason"] == "no camera clip for this shot"
    # no formula spin on the radar's launch (25 Oct 2026): the OPS's own weak reading
    assert shot.input_sources["spin"]["route"] == "ops_below_floor"
    assert metrics["spin_rate"]["value"] is not None
    assert metrics["spin_rate"]["status"] == "experimental"
    assert metrics["ball_range"]["status"] == "not_measured"
    assert "no camera frames" in metrics["ball_range"]["reason"]


def test_with_neither_the_iwr_nor_the_camera_the_ops_readings_still_publish(kiosk, scene):
    shot = _shot(scene)

    metrics = _run(scene, shot, archive=None)

    assert metrics["ball_speed"]["status"] == "measured"
    assert metrics["club_speed"]["status"] == "measured"
    assert metrics["smash_factor"]["value"] is not None
    assert metrics["launch_vertical"]["status"] == "not_measured"
    assert "camera_fallback" in metrics["launch_vertical"]["reason"]
    # the OPS's own spin, under the label it always had
    assert metrics["spin_rate"]["value"] == pytest.approx(2300.0)
    assert metrics["spin_rate"]["source"] == "multitaper_ungated"


def test_a_stage_that_raises_is_its_own_reason_and_the_shot_still_logs(kiosk, scene):
    logged = []
    kiosk.setattr(
        server_module,
        "get_session_logger",
        lambda: SimpleNamespace(log_shot=lambda shot, pipeline_ms: logged.append(shot.to_dict())),
    )

    def broken(_shot):
        raise RuntimeError("full speed exploded")

    kiosk.setattr(shot_models, "_attach_full_speeds", broken)
    shot = _shot(scene)

    _run(scene, shot)

    assert logged, "the shot was not logged"
    assert logged[0]["ball_speed_mph"] == pytest.approx(scene.ops_radial_mph())
    assert "full speed exploded" in shot.stage_errors["speed_contract"]
    assert "full-speed stage failed" in shot.ball_speed_full["reason"]
    # the rest still resolved
    assert shot.launch_angle_vertical_source == "camera_fallback"


def test_the_camera_head_track_takes_the_range_chain_s_range_and_its_sigma(kiosk, scene):
    from openflight.camera import fusion_processing  # noqa: PLC0415

    seen = {}
    real_delivery = fusion_processing.estimate_chained_delivery

    def spy(*args, **kwargs):
        seen["tee_range_m"] = kwargs["geometry"].tee_range_m
        seen["sigma_m"] = kwargs["head_track"].ball_range_sigma_m
        return real_delivery(*args, **kwargs)

    kiosk.setattr(fusion_processing, "estimate_chained_delivery", spy)
    shot = _shot(scene)

    _run(scene, shot)

    assert shot.ball_range_source == "camera_ball_size"
    assert seen["tee_range_m"] == pytest.approx(shot.ball_range_m)
    assert seen["sigma_m"] == pytest.approx(shot.ball_range_uncertainty_m)


def test_a_raising_fusion_diagnostic_still_logs_the_shot(kiosk, scene):
    logged = []
    kiosk.setattr(
        server_module,
        "get_session_logger",
        lambda: SimpleNamespace(log_shot=lambda shot, pipeline_ms: logged.append(shot.to_dict())),
    )

    class Diagnostics(kiosk_extensions.KioskExtension):
        def on_fusion_diagnostic(self, *_args, **_kwargs):
            raise RuntimeError("diagnostic exploded")

    kiosk.setattr(kiosk_extensions, "_extensions", [shot_models.extension, Diagnostics()])
    shot = _shot(scene)

    _run(scene, shot)

    assert logged, "the shot was not logged"
    assert "diagnostic exploded" in shot.stage_errors["fusion_diagnostic"]
    assert logged[0]["stage_errors"]["fusion_diagnostic"] == shot.stage_errors["fusion_diagnostic"]


def test_a_shot_without_a_range_records_the_shared_fusion_s_refusal_as_a_stage_error(
    kiosk, scene, tmp_path
):
    kiosk.setattr(server_module, "_resolve_shot_ball_range", lambda shot, archive: None)
    (tmp_path / "frames.npz").write_bytes(b"")  # a saved clip, as on the kiosk
    capture = SimpleNamespace(valid=True, path=tmp_path, metadata={})
    shot = _shot(scene)

    server_module._fuse_camera_measurements(shot, capture, _archive(scene))  # pylint: disable=protected-access
    kiosk_extensions.call_all("after_enrichment", shot)
    server_module._finalize_shot_detected(shot, emit_event="shot")  # pylint: disable=protected-access

    assert "missing effective camera geometry" in shot.stage_errors["camera_fusion_shared"]
    # the shot itself still resolves what needs no camera geometry
    metrics = shot.to_dict()["metrics"]
    assert metrics["ball_speed"]["status"] == "measured"
    # every camera route that needs the ball's depth is refused for that reason, by name
    assert shot.experimental_fused_status == "rejected_no_ball_range"
    for name in ("club_path", "attack_angle"):
        assert metrics[name]["value"] is None, name
        assert "rejected_no_ball_range" in metrics[name]["reason"], name
    assert (
        "the camera head track did not run (rejected_no_ball_range)"
        in (metrics["attack_angle"]["reason"])
    )
    fallback = shot.camera_fallback_launch
    assert fallback["status"] == "not_run"
    assert fallback["reason"].startswith("no ball range")
    assert "depth" in fallback["reason"]
    assert "no ball range" in metrics["launch_vertical"]["reason"]
