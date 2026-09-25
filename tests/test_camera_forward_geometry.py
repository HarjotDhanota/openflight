"""Independent world-to-image projections for a separated camera and radar."""

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server
from openflight.camera import ball_flight, club_delivery
from openflight.camera.club_motion import ReferenceBall
from openflight.launch_monitor import Shot
from openflight.rig_geometry import RigGeometry


@pytest.mark.parametrize("forward_offset", [0.0, 0.03, -0.03, 0.20])
@pytest.mark.parametrize("lateral_offset", [0.0, 0.08])
@pytest.mark.parametrize("kind", ["club", "ball", "ball_size"])
def test_recovers_known_world_points(forward_offset, lateral_offset, kind):
    camera = np.array([lateral_offset, forward_offset, 0.095])
    radar = np.array([0.0, 0.0, 0.051])
    tee = np.array([0.0, 1.5, 0.021335])
    focal_px = 466.6667

    def project(point):
        delta = point - camera
        return np.array(
            [160 + focal_px * delta[0] / delta[1], 100 - focal_px * delta[2] / delta[1]]
        )

    ball_px = project(tee)
    ball = ReferenceBall(
        x=ball_px[0],
        y=ball_px[1],
        diameter_px=focal_px * 0.04267 / np.linalg.norm(tee - camera),
        area_px=140,
    )
    geometry_class = (
        club_delivery.CameraDeliveryGeometry if kind == "club" else ball_flight.CameraBallGeometry
    )
    geometry = geometry_class(
        camera_height_m=0.095,
        radar_height_m=0.051,
        tee_range_m=float(np.linalg.norm(tee - radar)),
        ball_height_m=0.021335,
        camera_lateral_offset_m=lateral_offset,
        camera_forward_offset_m=forward_offset,
        image_width_px=320,
        image_height_px=200,
    )
    points = [np.array([-0.05, 1.1, 0.07]), tee, np.array([0.10, 2.0, 0.25])]
    for point in points:
        pixel = project(point)
        if kind == "club":
            result = club_delivery._pixels_to_world(
                pixel[None, :],
                float(np.linalg.norm(point - radar)),
                ball=ball,
                geometry=geometry,
            )[0, [0, 2, 1]]
        else:
            diameter = focal_px * 0.04267 / np.linalg.norm(point - camera)
            candidate = ball_flight.BallCandidate(
                x=pixel[0],
                y=pixel[1],
                area=np.pi * (diameter / 2) ** 2,
                width=diameter,
                height=diameter,
                fill=0.75,
                circularity=0.9,
                mean_intensity=220,
            )
            model = ball_flight._camera_model(ball, geometry)
            if kind == "ball":
                result = ball_flight._project(
                    candidate,
                    float(np.linalg.norm(point - radar)),
                    model=model,
                    geometry=geometry,
                )
            else:
                result = ball_flight._project_from_ball_size(
                    candidate, model=model, geometry=geometry
                )
        np.testing.assert_allclose(result, point, atol=1e-9)


@pytest.mark.parametrize("kind", ["club", "ball"])
@pytest.mark.parametrize("configured_offset", [None, 0.03])
def test_live_fusion_receives_forward_offset(monkeypatch, tmp_path, kind, configured_offset):
    (tmp_path / "frames.npz").touch()
    config = {"mount_height_m": 0.095, "width": 320, "height": 200}
    if configured_offset is not None:
        config["forward_offset_m"] = configured_offset
    monkeypatch.setattr(server, "camera_capture_config", config)
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(
            calibration=SimpleNamespace(
                tee_range_m=1.5,
                radar_height_m=0.051,
                tee_ball_height_m=0.021335,
            )
        ),
    )
    observed = []

    def estimate(*_args, **kwargs):
        observed.append(kwargs["geometry"])
        if kind == "club":
            return club_delivery.ChainedDelivery(status="rejected_test")
        return ball_flight.CameraBallEstimate(status="rejected_test")

    module, name, fuse = (
        (club_delivery, "estimate_chained_delivery", server._fuse_camera_club_delivery)
        if kind == "club"
        else (ball_flight, "estimate_camera_ball_flight", server._fuse_camera_ball_flight)
    )
    monkeypatch.setattr(module, name, estimate)
    shot = Shot(ball_speed_mph=100.0, club_speed_mph=80.0, timestamp=datetime.now())
    archive = {
        "frames": np.zeros((2, 200, 320)),
        "host_timestamp_ns": np.array([0, 1]),
        "trigger_host_timestamp_ns": 0,
    }
    fuse(shot, SimpleNamespace(valid=True, path=tmp_path), camera_archive=archive)

    assert len(observed) == 1
    assert observed[0].camera_forward_offset_m == (configured_offset or 0.0)


@pytest.mark.parametrize("kind", ["club", "ball"])
def test_live_fusion_rejects_archive_dimension_mismatch_before_estimator(
    monkeypatch, tmp_path, kind
):
    (tmp_path / "frames.npz").touch()
    monkeypatch.setattr(
        server,
        "camera_capture_config",
        {"mount_height_m": 0.095, "width": 320, "height": 200},
    )
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(
            calibration=SimpleNamespace(
                tee_range_m=1.5,
                radar_height_m=0.051,
                tee_ball_height_m=0.021335,
            )
        ),
    )

    def unexpected_estimator(*_args, **_kwargs):
        pytest.fail("estimator must not run with mismatched archive dimensions")

    module, name, fuse = (
        (club_delivery, "estimate_chained_delivery", server._fuse_camera_club_delivery)
        if kind == "club"
        else (ball_flight, "estimate_camera_ball_flight", server._fuse_camera_ball_flight)
    )
    monkeypatch.setattr(module, name, unexpected_estimator)
    shot = Shot(ball_speed_mph=100.0, club_speed_mph=80.0, timestamp=datetime.now())
    shot.iwr6843_horizontal_deg = 1.0
    archive = {
        "frames": np.zeros((2, 100, 160)),
        "host_timestamp_ns": np.array([0, 1]),
        "trigger_host_timestamp_ns": 0,
    }
    fuse(shot, SimpleNamespace(valid=True, path=tmp_path), camera_archive=archive)

    status = (
        shot.experimental_fused_status
        if kind == "club"
        else shot.experimental_camera_horizontal_status
    )
    assert "rejected_invalid_camera_geometry" in status


def test_v3_rig_preserves_camera_ahead_of_radar():
    rig = RigGeometry.from_json(
        Path(__file__).resolve().parents[1] / "config/enclosure_v3_rig_geometry.json"
    )
    setup = rig.enclosure_setup()
    assert setup.camera_forward_offset_m == pytest.approx(0.03)
    assert setup.as_dict()["camera_forward_offset_m"] == pytest.approx(0.03)


@pytest.mark.parametrize(
    "use_rig,flag,expected", [(True, -0.2, 0.03), (False, -0.02, -0.02), (False, None, 0.0)]
)
def test_startup_passes_measured_offset_or_explicit_fallback(
    monkeypatch, tmp_path, use_rig, flag, expected
):
    argv = [
        "openflight-server",
        "--no-logging",
        "--camera-capture",
        "--log-dir",
        str(tmp_path),
        "--startup-status-file",
        str(tmp_path / "status.json"),
    ]
    if use_rig:
        argv += [
            "--rig-geometry",
            str(Path(__file__).resolve().parents[1] / "config/enclosure_v3_rig_geometry.json"),
        ]
    if flag is not None:
        argv += ["--camera-capture-forward-offset-m", str(flag)]
    monkeypatch.setattr(sys, "argv", argv)
    for name in ("rig_geometry", "rig_geometry_config"):
        monkeypatch.setattr(server, name, getattr(server, name))
    received = {}

    def capture(**kwargs):
        received.update(kwargs)
        return True

    monkeypatch.setattr(server, "init_camera_capture", capture)
    monkeypatch.setattr(server, "init_session_logger", lambda **_kwargs: None)
    monkeypatch.setattr(server, "start_monitor", lambda **_kwargs: None)
    monkeypatch.setattr(server.socketio, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_cleanup_hardware_for_shutdown", lambda: None)

    server.main()

    assert received["forward_offset_m"] == pytest.approx(expected)


def test_capture_records_forward_offset_in_session_config(monkeypatch, tmp_path):
    from openflight.camera import capture_runtime

    for name in (
        "camera_capture_runtime",
        "camera_capture_config",
        "camera_replay_manager",
        "camera_reference_ball_tracker",
        "camera_ball_flight_reference_tracker",
    ):
        monkeypatch.setattr(server, name, getattr(server, name))
    monkeypatch.setattr(
        capture_runtime,
        "CameraCaptureRuntime",
        lambda **kwargs: SimpleNamespace(
            settings=kwargs["settings"],
            start=lambda: None,
        ),
    )

    initialized = server.init_camera_capture(
        output_dir=tmp_path,
        gpio_pin=17,
        width=320,
        height=200,
        fps=450,
        pre_ms=90,
        post_ms=60,
        exposure_us=300,
        gain=4,
        stream="raw",
        rotate_180=False,
        mirror_horizontal=False,
        roll_correction_deg=0,
        scaler_crop=None,
        mount_height_m=0.095,
        lateral_offset_m=0,
        forward_offset_m=0.03,
        horizontal_offset_deg=0,
        use_gpio_trigger=False,
        auto_exposure=False,
    )

    assert initialized
    assert server._session_start_config()["camera_capture"]["forward_offset_m"] == pytest.approx(
        0.03
    )
