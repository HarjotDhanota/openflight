"""No enrichment stage that raises ever loses the shot or an unrelated metric.

Each stage of ``server._enrich_shot_from_optional_hardware`` is forced to raise in
turn: its error becomes the stage's own entry in ``Shot.stage_errors`` and every
other stage still runs (the IWR, the camera's logging and fusion, the range and
launch chains, the collision model's ``after_enrichment``).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from openflight import kiosk_extensions, server as server_module
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot


class Recorder(kiosk_extensions.KioskExtension):
    name = "recorder"

    def __init__(self, raises_on: str | None = None):
        self.calls: list[str] = []
        self._raises_on = raises_on

    def _call(self, hook: str) -> None:
        self.calls.append(hook)
        if hook == self._raises_on:
            raise RuntimeError(f"{hook} exploded")

    def on_camera_capture(self, shot, camera_capture) -> None:
        self._call("on_camera_capture")

    def after_enrichment(self, shot) -> None:
        self._call("after_enrichment")


class SessionLog:
    def __init__(self):
        self.camera_captures = []

    def log_camera_capture(self, **capture):
        self.camera_captures.append(capture)


CAPTURE = SimpleNamespace(
    trigger_timestamp=100.0,
    path=Path("camera-001"),
    metadata={},
    error=None,
    valid=True,
    sequence=1,
)
ARCHIVE = {"frames": "archive"}


@pytest.fixture
def enrichment(monkeypatch):
    """An enrichment with a camera clip, an IWR stage and one extension, all recorded."""
    calls = SimpleNamespace(fused=[], ranges=[], launches=[], session_log=SessionLog())

    class CameraRuntime:
        camera_analysis_eligible = True

        @staticmethod
        def capture_for_shot(_impact_timestamp, timeout_s):
            return CAPTURE

    def process_iwr(shot):
        shot.launch_angle_vertical = 15.0
        shot.launch_angle_vertical_source = "radar"
        return 12.0

    def resolve_range(shot, archive):
        calls.ranges.append(archive)
        shot.input_sources = {**(shot.input_sources or {}), "ball_range": {"route": None}}

    monkeypatch.setattr(server_module, "camera_capture_runtime", CameraRuntime())
    monkeypatch.setattr(server_module, "_load_camera_capture_archive", lambda _capture: ARCHIVE)
    monkeypatch.setattr(server_module, "_process_iwr6843_angle", process_iwr)
    monkeypatch.setattr(server_module, "_snapshot_inclinometer_for_shot", lambda _shot: None)
    monkeypatch.setattr(server_module, "_attach_camera_replay", lambda *_args: None)
    monkeypatch.setattr(
        server_module,
        "_fuse_camera_measurements",
        lambda shot, capture, archive: calls.fused.append((capture, archive)),
    )
    monkeypatch.setattr(server_module, "_resolve_shot_ball_range", resolve_range)
    monkeypatch.setattr(
        server_module,
        "_apply_camera_fallback_launch",
        lambda shot, fallback: calls.launches.append(fallback),
    )
    monkeypatch.setattr(server_module, "kld7_vertical", None)
    monkeypatch.setattr(server_module, "kld7_horizontal", None)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: calls.session_log)
    monkeypatch.setattr(server_module, "_shot_number_for_log", lambda shot, log: 1)
    monkeypatch.setattr(server_module, "log_session_error", lambda *_args, **_kwargs: None)
    return calls


def _install(monkeypatch, extension: Recorder) -> Recorder:
    monkeypatch.setattr(kiosk_extensions, "_extensions", [extension])
    return extension


def _shot() -> Shot:
    return Shot(
        ball_speed_mph=150.0,
        club_speed_mph=100.0,
        timestamp=datetime(2026, 10, 25, 12, 0, 0),
        impact_timestamp=100.0,
        club=ClubType.DRIVER,
    )


def _broken(name: str):
    def stage(*_args, **_kwargs):
        raise RuntimeError(f"{name} exploded")

    return stage


def test_a_raising_camera_fusion_is_its_own_reason_and_the_chains_still_close(
    monkeypatch, enrichment
):
    extension = _install(monkeypatch, Recorder())
    monkeypatch.setattr(server_module, "_fuse_camera_measurements", _broken("fusion"))
    shot = _shot()

    server_module._enrich_shot_from_optional_hardware(shot)  # pylint: disable=protected-access

    assert "fusion exploded" in shot.stage_errors["camera_fusion"]
    # the range chain ran on the clip, the launch chain closed with the stage's reason
    assert enrichment.ranges == [ARCHIVE]
    assert enrichment.launches[-1]["status"] == "not_run"
    assert "the camera stage failed" in enrichment.launches[-1]["reason"]
    # the IWR's launch and the collision model are untouched
    assert shot.launch_angle_vertical == 15.0
    assert extension.calls[-1] == "after_enrichment"


def test_a_fusion_that_resolved_the_range_before_raising_is_not_resolved_again(
    monkeypatch, enrichment
):
    _install(monkeypatch, Recorder())

    def half_done(shot, _capture, archive):
        server_module._resolve_shot_ball_range(shot, archive)  # pylint: disable=protected-access
        raise RuntimeError("late in the fusion")

    monkeypatch.setattr(server_module, "_fuse_camera_measurements", half_done)
    shot = _shot()

    server_module._enrich_shot_from_optional_hardware(shot)  # pylint: disable=protected-access

    assert enrichment.ranges == [ARCHIVE]
    assert "late in the fusion" in shot.stage_errors["camera_fusion"]


def test_a_raising_on_camera_capture_hook_still_logs_and_fuses_the_clip(monkeypatch, enrichment):
    extension = _install(monkeypatch, Recorder(raises_on="on_camera_capture"))
    shot = _shot()

    server_module._enrich_shot_from_optional_hardware(shot)  # pylint: disable=protected-access

    assert "on_camera_capture exploded" in shot.stage_errors["on_camera_capture"]
    assert len(enrichment.session_log.camera_captures) == 1
    assert enrichment.fused == [(CAPTURE, ARCHIVE)]
    assert extension.calls == ["on_camera_capture", "after_enrichment"]


def test_a_raising_camera_capture_log_still_fuses_the_clip(monkeypatch, enrichment):
    _install(monkeypatch, Recorder())
    enrichment.session_log.log_camera_capture = _broken("camera log")
    shot = _shot()

    server_module._enrich_shot_from_optional_hardware(shot)  # pylint: disable=protected-access

    assert "camera log exploded" in shot.stage_errors["camera_capture_log"]
    assert enrichment.fused == [(CAPTURE, ARCHIVE)]


def test_a_raising_iwr_stage_keeps_the_camera_and_the_collision_model(monkeypatch, enrichment):
    extension = _install(monkeypatch, Recorder())
    monkeypatch.setattr(server_module, "_process_iwr6843_angle", _broken("iwr"))
    shot = _shot()

    result = server_module._enrich_shot_from_optional_hardware(shot)  # pylint: disable=protected-access

    assert "iwr exploded" in shot.stage_errors["iwr6843"]
    assert result.iwr6843_ms is None
    assert enrichment.fused == [(CAPTURE, ARCHIVE)]
    assert extension.calls[-1] == "after_enrichment"


def test_a_raising_inclinometer_snapshot_keeps_every_sensor(monkeypatch, enrichment):
    _install(monkeypatch, Recorder())
    monkeypatch.setattr(server_module, "_snapshot_inclinometer_for_shot", _broken("lis3dh"))
    shot = _shot()

    server_module._enrich_shot_from_optional_hardware(shot)  # pylint: disable=protected-access

    assert "lis3dh exploded" in shot.stage_errors["inclinometer"]
    assert shot.launch_angle_vertical == 15.0
    assert enrichment.fused == [(CAPTURE, ARCHIVE)]


def test_a_raising_kld7_stage_is_recorded(monkeypatch, enrichment):
    _install(monkeypatch, Recorder())
    radar = SimpleNamespace(snapshot_buffer=_broken("kld7"))
    monkeypatch.setattr(server_module, "kld7_vertical", radar)
    monkeypatch.setattr(server_module, "_maybe_wait_for_kld7_post_shot_frames", lambda _ts: None)
    shot = _shot()

    server_module._enrich_shot_from_optional_hardware(shot)  # pylint: disable=protected-access

    assert "kld7 exploded" in shot.stage_errors["kld7"]
    assert enrichment.fused == [(CAPTURE, ARCHIVE)]


def test_an_enrichment_that_raises_outright_still_closes_the_chains(monkeypatch, enrichment):
    """The coarse guard: anything the stages miss still leaves range, launch and collision."""
    extension = _install(monkeypatch, Recorder())
    finalized = []

    def broken_enrichment(shot):
        shot.launch_angle_vertical = 15.0
        raise RuntimeError("enrichment exploded")

    monkeypatch.setattr(server_module, "_enrich_shot_from_optional_hardware", broken_enrichment)
    monkeypatch.setattr(server_module, "_has_slow_shot_enrichment", lambda _shot: False)
    monkeypatch.setattr(
        server_module,
        "_queue_shot_finalization",
        lambda shot, **kwargs: finalized.append((shot, kwargs)),
    )
    shot = _shot()

    server_module._finish_shot_detected(shot, emit_event="shot")  # pylint: disable=protected-access

    final, kwargs = finalized[0]
    assert "enrichment exploded" in final.stage_errors["enrichment"]
    assert kwargs["enrichment"].diagnostic_reason == "deferred_enrichment_error"
    assert "ball_range" in final.input_sources
    assert "the enrichment failed" in enrichment.launches[-1]["reason"]
    assert extension.calls == ["after_enrichment"]
    assert final.launch_angle_vertical == 15.0
