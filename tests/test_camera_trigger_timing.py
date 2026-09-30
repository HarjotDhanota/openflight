"""The trigger is timed first, and frames are split by exposure time (P6-5).

Outdoors-test-5 showed a ~31 ms host stall at every trigger: the trigger held
the frame lock while it copied its evidence, so frames 18-20 arrived after the
trigger's timestamp even though they were exposed before or around it. The
arrival split (``pre_trigger_count``) keeps its meaning; the exposure split is
a new field that time comparisons use when a clip carries it.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server
from openflight.camera import ball_flight, capture_runtime, club_delivery, fusion_processing as fp
from openflight.camera.ball_flight import CameraBallEstimate
from openflight.camera.capture_runtime import CameraCaptureRuntime
from openflight.camera.club_delivery import ChainedDelivery
from openflight.camera.triggered_buffer import (
    CameraFrame,
    TriggeredCapture,
    exposure_trigger_index,
    pre_trigger_count_by_exposure,
)
from openflight.launch_monitor import Shot

FRAME_NS = 8_685_000


def _frames(count: int, *, start_ns: int = 1_000_000_000, latency_ns: int = 9_500_000):
    return tuple(
        CameraFrame(
            image=np.full((2, 3), index, dtype=np.uint8),
            sensor_timestamp_ns=start_ns + index * FRAME_NS,
            host_timestamp_ns=start_ns + index * FRAME_NS + latency_ns,
            exposure_us=800,
            analogue_gain=8.0,
        )
        for index in range(count)
    )


def test_the_trigger_is_stamped_before_its_evidence_is_gathered(tmp_path, monkeypatch):
    ticks = iter(range(1_000, 10_000, 10))
    monkeypatch.setattr(capture_runtime.time, "monotonic_ns", lambda: next(ticks))
    seen = {}

    def provider(_timestamp):
        seen["provider_ns"] = time.monotonic_ns()
        return {"ready": True}

    runtime = CameraCaptureRuntime(output_dir=tmp_path, trigger_evidence_provider=provider)
    runtime._running = True
    runtime._ring = SimpleNamespace(trigger=lambda stamp: seen.setdefault("trigger_ns", stamp))

    assert runtime.notify_trigger(10.0)

    assert seen["trigger_ns"] < seen["provider_ns"]
    assert runtime._trigger_clocks.get_nowait()["host_monotonic_ns"] == seen["trigger_ns"]


def test_the_trigger_records_the_sensor_clock_beside_the_monotonic_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_runtime.time, "monotonic_ns", lambda: 5_000)
    monkeypatch.setattr(capture_runtime, "_boottime_ns", lambda: 5_750)
    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    runtime._running = True
    runtime._ring = SimpleNamespace(trigger=lambda _stamp: True)

    assert runtime.notify_trigger(10.0)

    assert runtime._trigger_clocks.get_nowait() == {
        "host_monotonic_ns": 5_000,
        "boottime_ns": 5_750,
    }


def test_boottime_falls_back_cleanly_where_the_os_has_no_such_clock(monkeypatch):
    monkeypatch.delattr(capture_runtime.time, "CLOCK_BOOTTIME", raising=False)
    assert capture_runtime._boottime_ns() is None


@pytest.mark.skipif(not hasattr(time, "CLOCK_BOOTTIME"), reason="Linux clock")
def test_boottime_is_read_where_the_os_has_it():
    assert isinstance(capture_runtime._boottime_ns(), int)


def test_the_exposure_split_counts_frames_exposed_at_or_before_the_trigger():
    sensor = np.asarray([frame.sensor_timestamp_ns for frame in _frames(24)])

    assert pre_trigger_count_by_exposure(sensor, int(sensor[18]) + 1) == 19
    assert pre_trigger_count_by_exposure(sensor, int(sensor[18])) == 19
    assert pre_trigger_count_by_exposure(sensor, int(sensor[18]) - 1) == 18
    assert pre_trigger_count_by_exposure(sensor, int(sensor[0]) - 1) == 0


def test_a_saved_clip_carries_the_exposure_split_and_both_clocks(tmp_path):
    """The arrival split stays 18 while the exposure split names frame 18 as exposed first."""
    frames = _frames(24)
    trigger_boottime = frames[18].sensor_timestamp_ns + 3_000_000
    capture = TriggeredCapture(
        frames=frames, pre_trigger_count=18, trigger_host_timestamp_ns=trigger_boottime - 250
    )
    runtime = CameraCaptureRuntime(output_dir=tmp_path)

    saved = runtime._save_capture(
        1,
        123.0,
        capture,
        trigger_clocks={
            "host_monotonic_ns": trigger_boottime - 250,
            "boottime_ns": trigger_boottime,
        },
    )

    with np.load(saved.path / "frames.npz") as archive:
        assert int(archive["pre_trigger_count"]) == 18
        assert int(archive["pre_trigger_count_by_exposure"]) == 19
        assert int(archive["trigger_boottime_ns"]) == trigger_boottime
        assert int(archive["trigger_host_timestamp_ns"]) == trigger_boottime - 250
    written = json.loads((saved.path / "metadata.json").read_text(encoding="utf-8"))
    assert written["pre_trigger_frames"] == 18
    assert written["pre_trigger_frames_by_exposure"] == 19
    assert written["trigger_clocks"] == {
        "host_monotonic_ns": trigger_boottime - 250,
        "boottime_ns": trigger_boottime,
        "boottime_minus_monotonic_ns": 250,
        "exposure_split": capture_runtime.EXPOSURE_SPLIT_DEFINITION,
    }
    assert "start of the first row's exposure" in capture_runtime.EXPOSURE_SPLIT_DEFINITION


def test_a_clip_without_the_sensor_clock_records_no_exposure_split(tmp_path):
    capture = TriggeredCapture(frames=_frames(4), pre_trigger_count=2, trigger_host_timestamp_ns=7)
    runtime = CameraCaptureRuntime(output_dir=tmp_path)

    saved = runtime._save_capture(
        1, 123.0, capture, trigger_clocks={"host_monotonic_ns": 7, "boottime_ns": None}
    )

    with np.load(saved.path / "frames.npz") as archive:
        assert "pre_trigger_count_by_exposure" not in archive.files
        assert "trigger_boottime_ns" not in archive.files
    assert saved.metadata["pre_trigger_frames_by_exposure"] is None
    assert saved.metadata["trigger_clocks"]["boottime_minus_monotonic_ns"] is None


def test_the_save_loop_pairs_each_capture_with_its_trigger_clocks(tmp_path):
    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    runtime._running = True
    frames = _frames(4)
    boottime = frames[1].sensor_timestamp_ns + 10
    runtime._trigger_epochs.put(123.0)
    runtime._trigger_auto_exposure.put({})
    runtime._trigger_evidence.put(None)
    runtime._trigger_clocks.put({"host_monotonic_ns": boottime, "boottime_ns": boottime})
    runtime._ready.put(
        TriggeredCapture(frames=frames, pre_trigger_count=1, trigger_host_timestamp_ns=boottime)
    )
    runtime._ready.put(None)

    runtime._save_loop()

    assert runtime._captures[0].metadata["pre_trigger_frames_by_exposure"] == 2


@pytest.mark.parametrize(
    ("archive", "expected"),
    [
        ({"pre_trigger_count": np.int32(18)}, None),
        ({"pre_trigger_count": np.int32(18), "pre_trigger_count_by_exposure": np.int32(19)}, 18),
        ({"pre_trigger_count_by_exposure": np.int32(0)}, None),
        ({"pre_trigger_count_by_exposure": np.int32(25)}, None),
        ({"pre_trigger_count_by_exposure": np.int32(24)}, 23),
    ],
)
def test_the_exposure_trigger_index_is_used_only_when_recorded(archive, expected):
    assert exposure_trigger_index(archive, frame_count=24) == expected


def _fusion_context():
    from openflight.camera.club_delivery import ReferenceBallTracker  # noqa: PLC0415
    from openflight.camera.geometry_contract import (  # noqa: PLC0415
        EffectiveCameraGeometryInputs,
    )
    from openflight.clubs import ClubType  # noqa: PLC0415

    geometry = EffectiveCameraGeometryInputs(
        camera_height_m=0.095,
        radar_height_m=0.051,
        tee_slant_range_m=1.5,
        ball_height_m=0.021,
        camera_lateral_offset_m=0.0,
        camera_forward_offset_m=0.03,
        image_width_px=8,
        image_height_px=6,
        horizontal_pixel_sign=1.0,
        roll_correction_deg=0.0,
        ball_horizontal_output_offset_deg=0.0,
        ball_diameter_m=0.04267,
    )
    return fp.build_context(
        geometry=geometry,
        lighting_eligible=True,
        ball_tracker=ReferenceBallTracker(),
        club_tracker=ReferenceBallTracker(),
        ball_range_evidence=None,
        club_range_evidence=None,
        ops_ball_speed_mph=100.0,
        ops_club_speed_mph=75.0,
        iwr_vertical_deg=18.0,
        iwr_horizontal_deg=1.0,
        iwr_horizontal_confidence=0.8,
        club=ClubType.IRON_7,
        capture_npz_sha256="capture-hash",
        session_uuid="session-a",
        shot_number=3,
    )


def _fusion_archive(**extra):
    return {
        "frames": np.zeros((24, 6, 8), np.uint8),
        "host_timestamp_ns": np.arange(24, dtype=np.int64) * FRAME_NS,
        "trigger_host_timestamp_ns": np.asarray(20 * FRAME_NS, dtype=np.int64),
        "pre_trigger_count": np.asarray(18, dtype=np.int64),
        "_capture_npz_sha256": "capture-hash",
        **extra,
    }


@pytest.mark.parametrize(
    ("extra", "club_trigger", "ball_trigger"),
    [
        ({}, 17, None),
        ({"pre_trigger_count_by_exposure": np.asarray(19, dtype=np.int64)}, 18, 18),
    ],
)
def test_fusion_replay_times_against_the_exposure_split_when_recorded(
    monkeypatch, extra, club_trigger, ball_trigger
):
    seen = {}

    def ball_estimator(*_args, **kwargs):
        seen["ball"] = kwargs.get("trigger_frame_index")
        return CameraBallEstimate(status="rejected_test")

    def club_estimator(*_args, **kwargs):
        seen["club"] = kwargs["trigger_index"]
        return ChainedDelivery(status="rejected_test")

    monkeypatch.setattr(fp, "estimate_camera_ball_flight", ball_estimator)
    monkeypatch.setattr(fp, "estimate_chained_delivery", club_estimator)

    fp.process_camera_fusion(_fusion_context(), _fusion_archive(**extra))

    assert seen == {"club": club_trigger, "ball": ball_trigger}


def _live_server(monkeypatch, tmp_path):
    (tmp_path / "frames.npz").touch()
    monkeypatch.setattr(
        server, "camera_capture_config", {"mount_height_m": 0.095, "width": 8, "height": 6}
    )
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(
            calibration=SimpleNamespace(
                tee_range_m=1.5, radar_height_m=0.051, tee_ball_height_m=0.021335
            )
        ),
    )


@pytest.mark.parametrize(
    ("extra", "club_trigger", "ball_trigger"),
    [
        ({}, 17, None),
        ({"pre_trigger_count_by_exposure": np.asarray(19, dtype=np.int64)}, 18, 18),
    ],
)
def test_the_live_path_times_against_the_exposure_split_when_recorded(
    monkeypatch, tmp_path, extra, club_trigger, ball_trigger
):
    _live_server(monkeypatch, tmp_path)
    seen = {}

    def club_estimator(*_args, **kwargs):
        seen["club"] = kwargs["trigger_index"]
        return ChainedDelivery(status="rejected_test")

    def ball_estimator(*_args, **kwargs):
        seen["ball"] = kwargs.get("trigger_frame_index")
        return CameraBallEstimate(status="rejected_test")

    monkeypatch.setattr(club_delivery, "estimate_chained_delivery", club_estimator)
    monkeypatch.setattr(ball_flight, "estimate_camera_ball_flight", ball_estimator)
    archive = _fusion_archive(**extra)
    shot = Shot(ball_speed_mph=100.0, club_speed_mph=80.0, timestamp=datetime.now())
    capture = SimpleNamespace(valid=True, path=Path(tmp_path))

    server._fuse_camera_club_delivery(shot, capture, camera_archive=archive)
    server._fuse_camera_ball_flight(shot, capture, camera_archive=archive)

    assert seen == {"club": club_trigger, "ball": ball_trigger}


def test_ball_flight_takes_the_exposure_trigger_frame_over_the_nearest_arrival(monkeypatch):
    seen = {}

    def select(_frames, trigger_frame, _geometry, _tracker, **_kwargs):
        seen["trigger_frame"] = trigger_frame
        return None, {}

    monkeypatch.setattr(ball_flight, "_select_reference_ball", select)
    frames = np.zeros((24, 6, 8), np.uint8)
    host = np.arange(24, dtype=np.int64) * FRAME_NS
    geometry = SimpleNamespace(calibrated_model=None)

    kwargs = {
        "trigger_ns": int(host[20]),
        "range_evidence": None,
        "geometry": geometry,
        "ops_ball_speed_mph": 100.0,
    }
    ball_flight.estimate_camera_ball_flight(frames, host, **kwargs)
    assert seen["trigger_frame"] == 20
    ball_flight.estimate_camera_ball_flight(frames, host, trigger_frame_index=18, **kwargs)
    assert seen["trigger_frame"] == 18
