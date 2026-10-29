"""The experimental impact location read live on the kiosk, after each shot.

A running outline per club is built from the session's own swings; until it has
enough swings a shot says how far along it is, every other refusal is named,
and a reading still being worked on when the next shot arrives is dropped.
"""

from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from openflight import server as server_module  # noqa: E402
from openflight.camera import (  # noqa: E402
    impact_live as live,
    impact_location as il,
)
from openflight.launch_monitor import Shot  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_camera_impact_location as synthetic  # noqa: E402
from impact_angle_fixtures import bind_test_angles  # noqa: E402

RIG = json.loads(
    (Path(__file__).resolve().parents[1] / "config" / "enclosure_v3_rig_geometry.json").read_text(
        encoding="utf-8"
    )
)
START = {
    "golfer": {"handed": "RH", "source": "setup"},
    "rig_geometry": {
        "snapshot": {
            "parameters": {
                **RIG,
                "iwr_offset_mm": [0.0, 0.0, 0.0],
                "lens_height_above_floor_mm": 95.0,
            }
        },
        "lens": {"focal_px_by_arm": {"arm5": 2.0 * synthetic.FOCAL, "arm6": synthetic.FOCAL}},
    },
}
WIDE = {"width": 1280, "height": 800, "focal": 2.0 * synthetic.FOCAL}


@pytest.mark.parametrize(
    "detail", ["no_body_thick_enough_to_be_a_clubhead", "no_moving_component_near_the_ball"]
)
def test_failed_head_extraction_does_not_claim_the_head_was_absent(detail):
    code = f"no_template_for_this_club_no_head_mask_{detail}"
    assert live.plain_reason(code) == "the club head could not be isolated in the clip"


def _slant_m() -> float:
    drop = synthetic.CAMERA_HEIGHT_MM - il.BALL_RADIUS_MM
    return math.sqrt(20.0**2 + drop**2 + synthetic.BALL_RANGE_MM**2) / 1000.0


def write_clip(folder: Path, *, truth_mm: float = 8.0, mirror: bool = False, **render) -> Path:
    frames, _ball = synthetic.render_swing(truth_mm=truth_mm, **render)
    if mirror:
        frames = np.ascontiguousarray(frames[:, :, ::-1])
    folder.mkdir(parents=True)
    np.savez(
        folder / "frames.npz",
        frames=frames,
        pre_trigger_count=np.int32(11),
        exposure_us=np.full(len(frames), 300.0),
        sensor_timestamp_ns=1_000_000_000
        + np.arange(len(frames), dtype=np.int64) * round(1e9 / synthetic.FPS),
    )
    (folder / "metadata.json").write_text(
        json.dumps({"delivered_fps": synthetic.FPS, "settings": {"mirror_horizontal": False}}),
        encoding="utf-8",
    )
    return folder


def shot_record(number: int, *, club: str = "7-iron", range_source: str = "iwr_post_impact"):
    return {
        "shot_number": number,
        "club": club,
        "club_speed_mph": synthetic.CLUB_SPEED / 0.44704,
        "ball_range_m": _slant_m(),
        "ball_range_source": range_source,
        "ball_range_uncertainty_m": 0.03,
        "metrics": {
            "dynamic_loft": {"value": 0.0, "status": "calculated"},
            "face_angle": {"value": 0.0, "status": "calculated", "frame": "unit_boresight"},
            "attack_angle": {"value": -2.0, "status": "measured"},
        },
    }


class Session:
    """Writes one synthetic clip per call and the job the kiosk would send for it."""

    def __init__(self, root: Path, uuid: str = "session-a"):
        self.root, self.uuid, self.count = root, uuid, 0

    def job(self, *, handedness: str = "right", club: str = "7-iron", **kwargs) -> dict:
        self.count += 1
        render = {k: kwargs.pop(k) for k in list(kwargs) if k in ("width", "height", "focal")}
        clip = write_clip(
            self.root / f"{self.uuid}-camera_{self.count:03d}",
            truth_mm=kwargs.pop("truth_mm", 8.0),
            mirror=kwargs.pop("mirror", False),
            **render,
        )
        return {
            "job_id": self.count,
            "session_uuid": self.uuid,
            "shot_number": self.count,
            "clip_path": str(clip),
            "shot": bind_test_angles(
                shot_record(self.count, club=club, **kwargs), clip / "frames.npz"
            ),
            "start": START,
            "handedness": handedness,
        }


def test_the_first_swings_build_the_outline_and_say_how_far_along(tmp_path):
    analyser, session = live.LiveAnalyser(), Session(tmp_path)
    for done in (1, 2):
        reading = analyser.analyse(session.job())
        assert reading["status"] == "rejected"
        assert reading["reason"] == f"building the head outline: {done} of 5 swings with this club"
        assert reading["height_reason"] == reading["reason"]
        assert reading["reason_code"] == "building_the_head_outline"
        assert reading["template"]["swings"] == done
        assert reading["label"] == il.EXPERIMENTAL_LABEL


def test_once_the_outline_has_its_swings_a_shot_is_read_on_both_axes(tmp_path):
    analyser, session = live.LiveAnalyser(swings_needed=2), Session(tmp_path)
    analyser.analyse(session.job())
    reading = analyser.analyse(session.job())
    assert reading["status"] == "ok", reading["reason"]
    assert reading["impact_offset_mm"] == pytest.approx(8.0, abs=2.5)
    assert reading["impact_offset_sigma_mm"] > 0
    assert reading["height_status"] in ("ok", "rejected")
    assert reading["handedness"] == "right"
    assert reading["club_category"] == "iron"
    assert reading["template"] == {"swings": 2, "needed": 2, "built_from": 2}
    assert reading["analysis_ms"] > 0
    json.dumps(reading)


def test_a_wide_clip_is_read_at_the_live_analysis_width(tmp_path):
    analyser, session = live.LiveAnalyser(swings_needed=1), Session(tmp_path)
    reading = analyser.analyse(session.job(**WIDE))
    assert reading["analysis_frame_size"] == "640x400"
    assert reading["status"] == "ok", reading["reason"]
    assert reading["impact_offset_mm"] == pytest.approx(8.0, abs=2.5)


def test_a_later_shot_reads_the_running_outline_and_a_refresh_adds_it(tmp_path):
    analyser, session = live.LiveAnalyser(swings_needed=2), Session(tmp_path)
    analyser.analyse(session.job())
    analyser.analyse(session.job())
    third = analyser.analyse(session.job(truth_mm=4.0))
    assert third["status"] == "ok", third["reason"]
    assert third["template"]["built_from"] == 2
    assert not analyser.refresh(should_stop=lambda: True)
    assert analyser.refresh()
    assert not analyser.refresh()
    fourth = analyser.analyse(session.job())
    assert fourth["template"]["built_from"] == 3


def test_a_refresh_that_cannot_build_does_not_try_the_same_swings_again(tmp_path, monkeypatch):
    analyser, session = live.LiveAnalyser(swings_needed=1), Session(tmp_path)
    analyser.analyse(session.job())
    analyser.analyse(session.job())
    calls = []

    def fails(samples, **kwargs):
        calls.append(len(samples))
        return None, "template_did_not_build"

    monkeypatch.setattr(il, "build_self_template", fails)
    assert not analyser.refresh()
    assert not analyser.refresh()
    assert calls == [2]


def test_a_refusal_is_named_and_the_swing_does_not_count_toward_the_outline(tmp_path):
    analyser, session = live.LiveAnalyser(), Session(tmp_path)
    refused = analyser.analyse(session.job(range_source="camera_prior"))
    assert refused["status"] == "rejected"
    assert refused["reason_code"] == "no_ball_range_for_this_shot"
    assert refused["reason"] == "no radar distance to the ball on this shot"
    assert refused["height_reason"] == refused["reason"]
    assert analyser.analyse(session.job())["template"]["swings"] == 1


def test_each_club_builds_its_own_outline(tmp_path):
    analyser, session = live.LiveAnalyser(swings_needed=2), Session(tmp_path)
    analyser.analyse(session.job())
    analyser.analyse(session.job())
    driver = analyser.analyse(session.job(club="driver"))
    assert driver["reason"] == "building the head outline: 1 of 2 swings with this club"
    assert driver["club_category"] == "driver"


def test_a_new_session_starts_its_outlines_again(tmp_path):
    analyser = live.LiveAnalyser()
    analyser.analyse(Session(tmp_path / "a", "session-a").job())
    reading = analyser.analyse(Session(tmp_path / "b", "session-b").job())
    assert reading["template"]["swings"] == 1


def test_a_shot_asked_to_stop_is_dropped_but_its_swing_still_builds_the_outline(tmp_path):
    analyser, session = live.LiveAnalyser(), Session(tmp_path)
    dropped = analyser.analyse(session.job(), should_stop=lambda: True)
    assert dropped["status"] == "dropped"
    assert analyser.analyse(session.job())["template"]["swings"] == 2


def test_a_left_handed_golfer_is_read_through_the_mirror(tmp_path):
    analyser, session = live.LiveAnalyser(swings_needed=1), Session(tmp_path)
    reading = analyser.analyse(session.job(handedness="left", mirror=True))
    assert reading["handedness"] == "left"
    assert reading["status"] == "ok", reading["reason"]
    assert reading["impact_offset_mm"] == pytest.approx(8.0, abs=2.5)


def test_an_unreadable_clip_is_refused_by_name(tmp_path):
    job = Session(tmp_path).job()
    (Path(job["clip_path"]) / "frames.npz").write_bytes(b"not a clip")
    reading = live.LiveAnalyser().analyse(job)
    assert reading["status"] == "rejected"
    assert reading["reason"] == "the camera clip could not be read"


@pytest.mark.parametrize(
    "code, words",
    [
        ("frames_saturated_around_the_resting_ball", "glare"),
        ("frames_saturate_around_the_ball_from_frame_9", "glare"),
        ("ball_never_moved_ball_contrast_76_dn_view_15pct_saturated", "never saw the ball leave"),
        ("resting_ball_contrast_3_dn_too_low_to_see_it_leave", "does not stand out"),
        ("no_template_for_this_club_no_head_mask_no_moving_head", "club head"),
        ("something_new_happened", "something new happened"),
    ],
)
def test_refusal_codes_read_as_plain_words(code, words):
    assert words in live.plain_reason(code)


class FakeRunner:
    def __init__(self):
        self.jobs, self.cancelled, self.stopped = [], [], False

    def submit(self, job):
        self.jobs.append(job)

    def cancel_through(self, job_id):
        self.cancelled.append(job_id)

    def stop(self):
        self.stopped = True


class FakeLog:
    active_session_uuid = "session-a"

    def __init__(self):
        self.events = []

    def log_event(self, entry_type, data):
        self.events.append((entry_type, data))


@pytest.fixture
def kiosk(monkeypatch):
    published, log = [], FakeLog()
    monkeypatch.setattr(
        server_module,
        "publish_shot_update",
        lambda shot, *, update, session_uuid: (
            published.append((shot, update, session_uuid)) or True
        ),
    )
    monkeypatch.setattr(server_module, "get_session_logger", lambda: log)
    runner = FakeRunner()
    extension = live.ImpactLiveExtension(runner_factory=lambda on_result: runner)
    extension.configure(SimpleNamespace(no_impact_live=False), parser=None)
    extension.session_start_config({"golfer": {"handed": "RH", "source": "setup"}})
    return SimpleNamespace(extension=extension, runner=runner, published=published, log=log)


def _kiosk_shot(number: int) -> Shot:
    shot = Shot(
        ball_speed_mph=110.0, timestamp=datetime(2026, 10, 22, 9, number), shot_number=number
    )
    shot.camera_fusion_session_uuid = "session-a"
    return shot


def _capture(path: str = "/clips/camera_001"):
    return SimpleNamespace(valid=True, path=Path(path), error=None)


def test_a_shot_with_a_clip_is_sent_for_reading_once_after_it_was_shown(kiosk):
    shot = _kiosk_shot(1)
    kiosk.extension.on_camera_capture(shot, _capture())
    kiosk.extension.after_shot_published(shot, update_kind="first")
    kiosk.extension.after_shot_published(shot, update_kind="late_update")
    assert len(kiosk.runner.jobs) == 1
    job = kiosk.runner.jobs[0]
    assert job["clip_path"] == str(Path("/clips/camera_001"))
    assert (job["shot_number"], job["session_uuid"], job["handedness"]) == (1, "session-a", "right")
    assert job["shot"]["ball_speed_mph"] == 110.0
    assert job["start"]["golfer"]["handed"] == "RH"


def test_a_shot_without_a_clip_is_never_sent(kiosk):
    shot = _kiosk_shot(1)
    kiosk.extension.on_camera_capture(shot, SimpleNamespace(valid=False, path=None, error="x"))
    kiosk.extension.after_shot_published(shot, update_kind="first")
    assert not kiosk.runner.jobs


def test_a_reading_is_put_on_the_shot_published_and_logged(kiosk):
    shot = _kiosk_shot(1)
    kiosk.extension.on_camera_capture(shot, _capture())
    kiosk.extension.after_shot_published(shot, update_kind="first")
    reading = {"status": "ok", "reason": None, "impact_offset_mm": 5.0, "analysis_ms": 812.0}
    kiosk.extension.dispatcher.on_result(
        {"job_id": kiosk.runner.jobs[0]["job_id"], "reading": reading}
    )
    assert shot.impact_location["impact_offset_mm"] == 5.0
    assert shot.impact_location["latency_ms"] >= 0
    ((published, update, uuid),) = kiosk.published
    assert published is shot and uuid == "session-a"
    assert update["reason"] == "impact_location"
    ((entry_type, record),) = kiosk.log.events
    assert entry_type == "impact_location"
    assert record["shot_number"] == 1 and record["status"] == "ok"
    assert record["analysis_ms"] == 812.0


def test_a_reading_still_open_when_the_next_shot_arrives_is_dropped(kiosk):
    first = _kiosk_shot(1)
    kiosk.extension.on_camera_capture(first, _capture())
    kiosk.extension.after_shot_published(first, update_kind="first")
    kiosk.extension.on_shot_accepted(_kiosk_shot(2))
    assert first.impact_location["status"] == "dropped"
    assert first.impact_location["reason"] == live.DROPPED_REASON
    assert kiosk.runner.cancelled == [kiosk.runner.jobs[0]["job_id"]]
    assert kiosk.log.events[0][1]["status"] == "dropped"
    kiosk.extension.dispatcher.on_result(
        {"job_id": kiosk.runner.jobs[0]["job_id"], "reading": {"status": "ok"}}
    )
    assert first.impact_location["status"] == "dropped"
    assert len(kiosk.published) == 1


def test_the_kiosk_can_turn_the_live_reading_off(kiosk):
    kiosk.extension.configure(SimpleNamespace(no_impact_live=True), parser=None)
    shot = _kiosk_shot(1)
    kiosk.extension.on_camera_capture(shot, _capture())
    kiosk.extension.after_shot_published(shot, update_kind="first")
    assert not kiosk.runner.jobs


def test_a_left_handed_golfer_is_sent_as_left(kiosk, monkeypatch):
    monkeypatch.setattr(server_module, "golfer_handed", "LH")
    shot = _kiosk_shot(1)
    kiosk.extension.on_camera_capture(shot, _capture())
    kiosk.extension.after_shot_published(shot, update_kind="first")
    assert kiosk.runner.jobs[0]["handedness"] == "left"


def test_the_hand_the_shot_was_hit_with_is_sent_not_the_hand_now(kiosk, monkeypatch):
    shot = _kiosk_shot(1)
    shot.handed = "LH"
    monkeypatch.setattr(server_module, "golfer_handed", "RH")
    kiosk.extension.on_camera_capture(shot, _capture())
    kiosk.extension.after_shot_published(shot, update_kind="first")
    assert kiosk.runner.jobs[0]["handedness"] == "left"
    assert kiosk.runner.jobs[0]["shot"]["handed"] == "LH"


def test_the_worker_process_reads_a_clip_and_stops(tmp_path):
    results = []
    runner = live.ProcessRunner(results.append, settings={"swings_needed": 1})
    try:
        runner.submit(Session(tmp_path).job())
        deadline = time.monotonic() + 120.0
        while not results and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        runner.stop()
    assert results, "the worker sent no result"
    assert results[0]["job_id"] == 1
    assert results[0]["reading"]["status"] == "ok", results[0]["reading"]["reason"]
    assert not runner.alive()


def test_a_worker_that_exits_fails_only_its_own_readings_and_a_new_one_starts(monkeypatch):
    published = []
    monkeypatch.setattr(
        server_module,
        "publish_shot_update",
        lambda shot, *, update, session_uuid: published.append(shot.shot_number) or True,
    )
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    runners = []

    def factory(on_result):
        runners.append(FakeRunner())
        return runners[-1]

    dispatcher = live.ImpactLiveExtension(runner_factory=factory).dispatcher
    first, second = _kiosk_shot(1), _kiosk_shot(2)
    dispatcher.submit(first, {"session_uuid": "session-a", "shot_number": 1})
    dispatcher.on_result({"type": "worker_exited", "runner": runners[0]})
    assert first.impact_location["reason"] == live.STOPPED_REASON
    dispatcher.submit(second, {"session_uuid": "session-a", "shot_number": 2})
    assert len(runners) == 2
    dispatcher.on_result({"type": "worker_exited", "runner": runners[0]})
    assert second.impact_location is None
    assert published == [1]


def test_a_worker_that_cannot_start_refuses_the_shot_by_name(monkeypatch):
    monkeypatch.setattr(
        server_module, "publish_shot_update", lambda shot, *, update, session_uuid: True
    )
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)

    def broken(on_result):
        raise OSError("no python")

    extension = live.ImpactLiveExtension(runner_factory=broken)
    extension.dispatcher.warm()
    shot = _kiosk_shot(1)
    extension.dispatcher.submit(shot, {"session_uuid": "session-a", "shot_number": 1})
    assert shot.impact_location["reason_code"] == "analysis_failed_worker_unavailable"


def test_a_kiosk_shot_with_a_clip_is_read_and_shown_as_a_shot_update(tmp_path, monkeypatch):
    from openflight import kiosk_extensions  # noqa: PLC0415
    from openflight.clubs import ClubType  # noqa: PLC0415

    emitted = []
    monkeypatch.setattr(
        server_module.socketio,
        "emit",
        lambda event, payload=None, **_: emitted.append((event, payload)),
    )
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    extension = live.ImpactLiveExtension(
        runner_factory=lambda on_result: live.ProcessRunner(
            on_result, settings={"swings_needed": 1}
        )
    )
    extension.configure(SimpleNamespace(no_impact_live=False), parser=None)
    extension.session_start_config(START)
    kiosk_extensions.register(extension)
    try:
        shot = Shot(
            ball_speed_mph=110.0,
            club_speed_mph=synthetic.CLUB_SPEED / 0.44704,
            club=ClubType.IRON_7,
            timestamp=datetime(2026, 10, 22, 9, 30),
            shot_number=1,
            ball_range_m=_slant_m(),
            ball_range_source="iwr_post_impact",
            ball_range_uncertainty_m=0.03,
        )
        extension.on_camera_capture(shot, _capture(str(write_clip(tmp_path / "camera_001"))))
        server_module._finalize_shot_detected(shot, emit_event="shot")  # pylint: disable=protected-access
        deadline = time.monotonic() + 120.0
        while (
            not any(event == "shot_update" for event, _ in emitted) and time.monotonic() < deadline
        ):
            time.sleep(0.1)
    finally:
        kiosk_extensions.unregister(extension)
        extension.on_shutdown()
    assert [event for event, _ in emitted] == ["shot", "shot_update"]
    update = emitted[-1][1]["shot"]
    assert update["impact_location"]["status"] == "ok", update["impact_location"]["reason"]
    assert update["metrics"]["impact_offset"]["status"] == "experimental"
    assert update["metrics"]["impact_offset"]["value"] == pytest.approx(8.0, abs=2.5)


def test_the_extension_is_off_until_the_kiosk_configures_it(kiosk):
    unconfigured = live.ImpactLiveExtension(runner_factory=lambda on_result: kiosk.runner)
    shot = _kiosk_shot(1)
    unconfigured.on_camera_capture(shot, _capture())
    unconfigured.after_shot_published(shot, update_kind="first")
    assert not kiosk.runner.jobs
    assert not live.extension.enabled
