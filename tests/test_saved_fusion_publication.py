"""Known-answer saved signals through production processing, publication and packaging.

Acquisition/matching and outbound transports are software boundaries. Signal
estimators, models, SocketIO publication and session artifacts run real code.
"""

import json
import math
import queue
import threading
import zipfile
from collections import deque
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import (
    kiosk_extensions,
    research_hooks,
    server,
    session_bundle,
    shot_metrics,
    shot_models,
)
from openflight.camera.capture_runtime import SavedCameraCapture
from openflight.clubs import ClubType
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.monitor import IWR6843Capture
from openflight.iwr6843.runtime import IWR6843Runtime
from openflight.raw_radar_replay import load_session_events
from openflight.rolling_buffer import monitor as monitor_module
from openflight.rolling_buffer.types import IQCapture
from openflight.session_logger import SessionLogger
from openflight.session_review import build_session_review
from scripts.analysis.analyze_tester_session import write_review
from tests import session_fixtures as signals
from tests.test_server import _wait_for_shot_finalization_idle


def wait_pipeline():
    task = server.shot_enrichment_task
    if task is not None:
        task.join(timeout=20)
        assert not task.is_alive(), "saved-shot enrichment did not finish"
    assert server.shot_enrichment_queue.unfinished_tasks == 0
    _wait_for_shot_finalization_idle(timeout_s=10)


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    root = signals.capture_tree(tmp_path / "sessions", tester_setup=True)
    run = root / signals.TESTER / "arm5/paired/run-01"
    old_log = next(run.glob("session_*.jsonl"))
    config = json.loads(old_log.read_text().splitlines()[0])["config"]
    old_log.unlink()
    logger = SessionLogger(run, provenance_collector=lambda *_: {"known_answer_fixture": True})
    logger.start_session(mode="rolling-buffer", trigger_type="sound", config=config)
    session_uuid = logger.active_session_uuid
    epoch = 1_790_301_037.0
    cameras, radars = {}, {}
    for number in (1, 2):
        camera_path = run / "arm5/camera" / f"camera_00{number}"
        metadata = json.loads((camera_path / "metadata.json").read_text())
        metadata["capture_path"] = str(camera_path)
        metadata["tester_setup"]["observations"]["runtime"].update(
            session_uuid=session_uuid, run_dir=str(run)
        )
        (camera_path / "metadata.json").write_text(json.dumps(metadata))
        cameras[number] = SavedCameraCapture(
            number, epoch + number, epoch + number + 0.1, camera_path, metadata
        )
        radar_path = run / "iwr6843" / f"iwr_00{number}.l3dump"
        radars[number] = IWR6843Capture(
            number, epoch + number, epoch + number + 0.1, 0.1, radar_path.read_bytes(), radar_path
        )

    waiting, release = threading.Event(), threading.Event()
    hold = set()
    match_calls = []

    def matched(table, timestamp, sensor):
        number = round(timestamp - epoch)
        match_calls.append((sensor, number))
        if sensor == "iwr" and number in hold:
            waiting.set()
            assert release.wait(10), "test did not release the saved IWR capture"
        return table.get(number)

    cal = Calibration.identity()
    cal.tilt_rad = np.radians(10.4)
    cal.tee_range_m = 1.5
    cal.tee_ball_height_m = 0.152
    cal.meta["radar_height_m"] = 0.152
    runtime = IWR6843Runtime(
        SimpleNamespace(
            capture_for_shot=lambda timestamp, **_: matched(radars, timestamp, "iwr"),
            ready_snapshot=lambda: {"running": False},
        ),
        cal,
        net_range_m=4.0,
        azimuth_offset_deg=0.0,
    )
    camera_runtime = SimpleNamespace(
        camera_analysis_eligible=True,
        capture_for_shot=lambda timestamp, **_: matched(cameras, timestamp, "camera"),
        trigger_rejection_for_shot=lambda _timestamp: None,
        ready_snapshot=lambda: {"running": False},
    )
    pending = deque()

    class Trigger:
        def wait_for_trigger(self, **_kwargs):
            if pending:
                return pending.popleft()
            monitor._running = False
            return None

        def drain_diagnostics(self):
            return []

        def reset(self):
            pass

    monkeypatch.setattr(monitor_module, "OPS243Radar", lambda **_: SimpleNamespace())
    monkeypatch.setattr(monitor_module, "create_trigger", lambda *_a, **_k: Trigger())
    monitor = monitor_module.RollingBufferMonitor(trigger_type="sound")
    monitor._current_club = ClubType.IRON_7
    monitor._shot_callback = server.on_shot_detected
    monkeypatch.setattr(monitor_module, "get_session_logger", lambda: logger)
    monkeypatch.setattr(server, "get_session_logger", lambda: logger)
    monkeypatch.setattr(server, "monitor", monitor)
    monkeypatch.setattr(server, "iwr6843_runtime", runtime)
    monkeypatch.setattr(server, "camera_capture_runtime", camera_runtime)
    monkeypatch.setattr(server, "camera_capture_config", config["camera_capture"])
    monkeypatch.setattr(server, "rig_geometry_config", {})
    monkeypatch.setattr(server, "surface_height_contributions", [])
    monkeypatch.setattr(server, "iwr6843_runtime_config", {})
    monkeypatch.setattr(
        server, "setup_handoff_config", {"scene": {}, "target_line": {"status": "not_set"}}
    )
    for name in (
        "kld7_vertical",
        "kld7_horizontal",
        "inclinometer_service",
        "ball_at_address_monitor",
        "camera_calibration_set",
        "camera_tilt_calibration_path",
        "rig_geometry",
        "camera_replay_manager",
    ):
        monkeypatch.setattr(server, name, None)
    monkeypatch.setattr(server, "horizontal_offset_deg", 0.0)
    monkeypatch.setattr(server, "ballistics_enabled", True)
    monkeypatch.setattr(server, "debug_mode", False)
    monkeypatch.setattr(server, "_SHOT_ENRICHMENT_DEADLINE_S", 60.0)
    monkeypatch.setattr(server, "shot_enrichment_queue", queue.Queue())
    monkeypatch.setattr(server, "_deferred_shot_enrichment", deque())
    monkeypatch.setattr(server, "shot_enrichment_task", None)
    forwarded = []
    monkeypatch.setattr(
        server, "_forward_shot_to_simulators", lambda shot: forwarded.append(shot.shot_number)
    )
    monkeypatch.setattr(server, "_publish_phone_shot", lambda *_a, **_k: None)
    monkeypatch.setattr(
        kiosk_extensions, "_extensions", [shot_models.extension, research_hooks.extension]
    )
    server._reset_shot_sequence()
    client = server.socketio.test_client(server.app)
    client.get_received()

    def capture(number):
        entry = signals.ops_capture(number)
        if number == 2:
            phase = 2 * np.pi * (2 * 30.0 / 0.01243) * np.arange(4096) / 30000
            entry["i_samples"] = (2048 + 500 * np.cos(phase)).astype(int).tolist()
            entry["q_samples"] = (2048 + 500 * np.sin(phase)).astype(int).tolist()
        pending.append(
            IQCapture(
                sample_time=entry["sample_time"],
                trigger_time=entry["trigger_time"],
                i_samples=entry["i_samples"],
                q_samples=entry["q_samples"],
                timestamp=epoch + number + 0.01,
                trigger_timestamp=epoch + number,
                capture_id=f"known-answer-ops-{number}",
                trigger_type="sound",
            )
        )
        monitor._running = True
        monitor._capture_loop()
        return monitor.get_shots()[-1]

    def received():
        return [
            event["args"][0]
            for event in client.get_received()
            if event["name"] in ("shot", "shot_update")
        ]

    state = SimpleNamespace(
        root=root,
        run=run,
        logger=logger,
        session_uuid=session_uuid,
        monitor=monitor,
        radars=radars,
        cameras=cameras,
        capture=capture,
        received=received,
        match_calls=match_calls,
        hold=hold,
        waiting=waiting,
        release=release,
        forwarded=forwarded,
    )
    yield state
    release.set()
    wait_pipeline()
    client.disconnect()
    logger.end_session()
    server._reset_shot_sequence()


def assert_metrics(shot):
    metrics = shot["metrics"]
    assert len(metrics) == 37 and set(metrics) == set(shot_metrics.METRIC_IDS)
    for metric in metrics.values():
        assert metric["status"] in shot_metrics.LABELS
        if metric["value"] is None:
            assert metric["status"] == "not_measured" and metric["reason"]
        else:
            assert math.isfinite(metric["value"]) and isinstance(metric["unit"], str)
    return metrics


def reviewed_metrics(attempt):
    return {
        row["key"]: {
            key: value for key, value in row.items() if key not in ("key", "label", "group")
        }
        for row in attempt["kiosk_metrics"]
    }


def test_real_saved_producers_publish_metrics_and_roundtrip_review_package(pipeline, tmp_path):
    shot = pipeline.capture(1)
    wait_pipeline()
    packets = pipeline.received()
    assert len(packets) == 2
    first, final = [packet["shot"] for packet in packets]
    assert first["shot_number"] == final["shot_number"] == 1
    metrics = assert_metrics(final)
    assert final["ball_speed_mph"] == pytest.approx(signals.BALL_SPEED_MS * 2.23694, abs=0.3)
    assert final["launch_angle_vertical"] == pytest.approx(signals.LAUNCH_DEG, abs=1.0)
    camera = final["camera_fusion_processing"]["ball_estimate"]
    assert camera["status"] == "accepted" and camera["n_points"] == 6
    assert camera["horizontal_deg"] == pytest.approx(signals.FLIGHT_HORIZONTAL_DEG, abs=0.2)
    assert camera["vertical_deg"] == pytest.approx(signals.LAUNCH_DEG, abs=0.3)
    assert camera["camera_model"]["status"] == "uncalibrated"
    assert final["collision_model"]["inputs"]["club_path"]["value"] is None
    assert metrics["carry"]["value"] is None
    assert "spin axis not measured" in final["flight"]["as_measured"]["metrics"]["carry"]["reason"]
    assert shot.ops_speed_track["impact_source"] == "sound_trigger"
    _, _, events = load_session_events(next(pipeline.run.glob("session_*.jsonl")))
    raw = next(event for event in events if event["type"] == "rolling_buffer_capture")
    assert raw["capture_id"] == "known-answer-ops-1" and raw["shot_number"] == 1
    pipeline.logger.end_session()
    review = write_review(pipeline.root, signals.TESTER, {})
    attempts = [row for row in review["attempts"] if row["shot_number"] == 1]
    assert len(attempts) == 1
    assert reviewed_metrics(attempts[0]) == metrics
    bundle = session_bundle.build_bundle(pipeline.root, signals.TESTER, viewer=None, provenance={})
    session_bundle.validate_bundle(Path(bundle["path"]))
    copied = tmp_path / "copied"
    with zipfile.ZipFile(bundle["path"]) as archive:
        archive.extractall(copied)
    assert build_session_review(copied, signals.TESTER, analysis={}) == review


@pytest.mark.parametrize("missing", ["iwr", "camera", "both"])
def test_missing_or_failed_sensor_keeps_owned_ops_and_explicit_metric_states(pipeline, missing):
    if missing in ("iwr", "both"):
        pipeline.radars[1] = replace(
            pipeline.radars[1], raw=None, error="known-answer failed transfer"
        )
    if missing in ("camera", "both"):
        pipeline.cameras.pop(1)
    shot = pipeline.capture(1)
    wait_pipeline()
    final = pipeline.received()[-1]["shot"]
    metrics = assert_metrics(final)
    assert final["ball_speed_mph"] > 100
    assert shot.ops_speed_track["canonical_radial_mph"] == pytest.approx(
        final["ball_speed_mph"], abs=0.05
    )
    if missing == "both":
        assert metrics["launch_vertical"]["value"] is None
        assert metrics["carry"]["value"] is None
    _, _, events = load_session_events(next(pipeline.run.glob("session_*.jsonl")))
    assert len([event for event in events if event["type"] == "shot_detected"]) == 1
    if missing in ("iwr", "both"):
        iwr = next(event for event in events if event["type"] == "iwr6843_capture")
        assert iwr["capture_error"] == "known-answer failed transfer" and iwr["measurement"] is None
    if missing in ("camera", "both"):
        camera = next(event for event in events if event["type"] == "camera_capture")
        assert camera["capture_path"] is None and camera["capture_error"]


def expire_enrichment(shot_number):
    with server._shot_finalization_condition:
        registered = server._shot_finalization_registered[shot_number]
        server._shot_finalization_registered[shot_number] = replace(
            registered, deadline_monotonic=0.0
        )
        server._shot_finalization_condition.notify_all()
    _wait_for_shot_finalization_idle(timeout_s=10)


def test_late_real_enrichment_updates_first_shot_without_borrowing_into_second(pipeline, tmp_path):
    pipeline.hold.add(1)
    first = pipeline.capture(1)
    assert pipeline.waiting.wait(5)
    expire_enrichment(1)
    packets = pipeline.received()
    assert len(packets) == 2 and packets[-1]["shot"]["launch_angle_vertical"] is None
    first_identity = (first.timestamp.isoformat(), first.shot_number)
    pipeline.radars[2] = replace(pipeline.radars[2], raw=None, error="second transfer failed")
    pipeline.cameras.pop(2)
    second = pipeline.capture(2)
    expire_enrichment(2)
    packets.extend(pipeline.received())
    pipeline.release.set()
    wait_pipeline()
    packets.extend(pipeline.received())
    assert [packet["shot"]["shot_number"] for packet in packets] == [1, 1, 2, 2, 1, 2]
    late_first, late_second = [packet["shot"] for packet in packets[-2:]]
    assert (late_first["timestamp"], late_first["shot_number"]) == first_identity
    assert late_first["launch_angle_vertical"] == pytest.approx(signals.LAUNCH_DEG, abs=1)
    assert late_first["camera_fusion_context"]["shot_number"] == 1
    assert late_first["camera_fusion_context"]["session_uuid"] == pipeline.session_uuid
    assert late_second["timestamp"] == second.timestamp.isoformat()
    assert late_second["ball_speed_mph"] == pytest.approx(30 * 2.23694, abs=0.3)
    assert late_second["launch_angle_vertical"] is None
    assert late_second["camera_fusion_processing"] is None
    for packet in packets:
        assert_metrics(packet["shot"])
    assert pipeline.forwarded == [1, 2]
    pipeline.logger.end_session()
    path = next(pipeline.run.glob("session_*.jsonl"))
    original = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["shot_number"] for row in original if row["type"] == "shot_detected"] == [1, 2]
    assert [row["shot_number"] for row in original if row["type"] == "shot_updated"] == [1, 2]
    captures = [row for row in original if row["type"] == "rolling_buffer_capture"]
    assert [(row["shot_number"], row["capture_id"]) for row in captures] == [
        (1, "known-answer-ops-1"),
        (2, "known-answer-ops-2"),
    ]
    review = write_review(pipeline.root, signals.TESTER, {})
    attempts = {row["shot_number"]: row for row in review["attempts"] if row["shot_number"]}
    assert set(attempts) == {1, 2}
    assert attempts[1]["live"]["launch_angle_vertical"] == late_first["launch_angle_vertical"]
    assert attempts[2]["live"]["launch_angle_vertical"] is None
    for number, payload in ((1, late_first), (2, late_second)):
        assert reviewed_metrics(attempts[number]) == payload["metrics"]
    built = session_bundle.build_bundle(pipeline.root, signals.TESTER, viewer=None, provenance={})
    copied = tmp_path / "copy"
    with zipfile.ZipFile(built["path"]) as archive:
        archive.extractall(copied)
    assert build_session_review(copied, signals.TESTER, analysis={}) == review
