"""The experimental impact location read live on the kiosk, after each shot.

A running outline per club is built from the session's own swings; until it has
enough swings a shot says how far along it is, every other refusal is named,
and a late reading remains attached to its own shot when subsequent shots arrive.
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
from impact_range_fixtures import bind_test_range  # noqa: E402

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
# The 2D outline route alone: the tests of its running outline (placement, which needs
# no outline, reads these shots first in the kiosk's resolution).
OUTLINE_ONLY = (live.OUTLINE_2D,)


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
            "shot": bind_test_range(
                bind_test_angles(shot_record(self.count, club=club, **kwargs), clip / "frames.npz"),
                clip / "frames.npz",
                self.uuid,
            ),
            "start": START,
            "handedness": handedness,
        }


def test_the_first_swings_build_the_outline_and_say_how_far_along(tmp_path):
    analyser, session = live.LiveAnalyser(routes=OUTLINE_ONLY), Session(tmp_path)
    for done in (1, 2):
        reading = analyser.analyse(session.job())
        assert reading["status"] == "rejected"
        assert reading["reason"] == f"building the head outline: {done} of 5 swings with this club"
        assert reading["height_reason"] == reading["reason"]
        assert reading["reason_code"] == "building_the_head_outline"
        assert reading["template"]["swings"] == done
        assert reading["label"] == il.EXPERIMENTAL_LABEL


def test_once_the_outline_has_its_swings_a_shot_is_read_on_both_axes(tmp_path):
    analyser, session = live.LiveAnalyser(swings_needed=2, routes=OUTLINE_ONLY), Session(tmp_path)
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
    analyser, session = live.LiveAnalyser(swings_needed=2, routes=OUTLINE_ONLY), Session(tmp_path)
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
    analyser, session = live.LiveAnalyser(swings_needed=1, routes=OUTLINE_ONLY), Session(tmp_path)
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


def test_a_reading_still_open_when_the_next_shot_arrives_is_retained(kiosk):
    first = _kiosk_shot(1)
    kiosk.extension.on_camera_capture(first, _capture())
    kiosk.extension.after_shot_published(first, update_kind="first")
    kiosk.extension.on_shot_accepted(_kiosk_shot(2))
    assert first.impact_location is None
    assert kiosk.runner.cancelled == []
    assert kiosk.log.events == []
    kiosk.extension.dispatcher.on_result(
        {"job_id": kiosk.runner.jobs[0]["job_id"], "reading": {"status": "ok"}}
    )
    assert first.impact_location["status"] == "ok"
    assert kiosk.log.events[0][1]["status"] == "ok"
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
    from openflight.session_logger import SessionLogger  # noqa: PLC0415

    emitted = []
    monkeypatch.setattr(
        server_module.socketio,
        "emit",
        lambda event, payload=None, **_: emitted.append((event, payload)),
    )
    log = SessionLogger(log_dir=tmp_path / "logs", enabled=True)
    log.start_session(mode="rolling-buffer", trigger_type="sound")
    monkeypatch.setattr(server_module, "get_session_logger", lambda: log)
    extension = live.ImpactLiveExtension(
        runner_factory=lambda on_result: live.ProcessRunner(
            on_result, settings={"swings_needed": 1, "routes": list(OUTLINE_ONLY)}
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
        clip = write_clip(tmp_path / "camera_001")
        shot.camera_fusion_session_uuid = log.active_session_uuid
        shot.impact_timestamp = 100.0
        shot.ball_range_label = "measured"
        shot.impact_range_evidence = bind_test_range(
            shot_record(1), clip / "frames.npz", log.active_session_uuid
        )["impact_range_evidence"]
        extension.on_camera_capture(shot, _capture(str(clip)))
        server_module._finalize_shot_detected(shot, emit_event="shot")  # pylint: disable=protected-access
        deadline = time.monotonic() + 120.0
        while (
            not any(event == "shot_update" for event, _ in emitted) and time.monotonic() < deadline
        ):
            time.sleep(0.1)
    finally:
        kiosk_extensions.unregister(extension)
        extension.on_shutdown()
        log.end_session()
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


# --- one resolution: placement first, the 2D outline as its fallback (G3) ----------------

D_PLANE_SOURCE = "D-plane inversion of the fitted launch model"
ANGLE_NAMES = ("face_angle", "dynamic_loft")


def without_capture_angles(job: dict) -> dict:
    """The shot as the kiosk records it today: no capture-bound face angle or loft."""
    for name in ANGLE_NAMES:
        job["shot"]["metrics"].pop(name)
    return job


def with_d_plane(job: dict, *, face_status: str = "calculated", loft: float = 0.0) -> dict:
    """The shot's collision-model block with a D-plane face angle and loft, as recorded."""
    without_capture_angles(job)
    job["shot"]["collision_model"] = {
        "metrics": {
            "face_angle": {
                "value": 0.0,
                "status": face_status,
                "source": D_PLANE_SOURCE,
                "unit": "deg",
                "frame": "unit_boresight",
                "reason": None,
                "note": None,
            },
            "dynamic_loft": {
                "value": loft,
                "status": "calculated",
                "source": D_PLANE_SOURCE,
                "unit": "deg",
                "frame": None,
                "reason": None,
                "note": None,
            },
        }
    }
    return job


def tried(reading: dict) -> list[tuple[str, str]]:
    return [(entry["route"], entry["reason"]) for entry in reading["resolution"]["tried"]]


@pytest.fixture
def placement_refuses(monkeypatch):
    """The placement model refuses every shot by a named edge (the 2D route then runs)."""
    from openflight.camera import impact_placement as ip  # noqa: PLC0415

    def refuse(shot, orientation, **kwargs):
        return ip.ImpactPlacement(
            "rejected",
            "toe_edge_not_found_on_frame_10_2_samples",
            diagnostics={"orientation": orientation.as_dict()},
        )

    monkeypatch.setattr(ip, "place_impact", refuse)


def test_placement_reads_a_shot_before_any_outline_is_built(tmp_path):
    analyser, session = live.LiveAnalyser(), Session(tmp_path)
    reading = analyser.analyse(session.job())
    assert reading["status"] == "ok", reading["reason"]
    assert reading["route"] == "placement"
    assert reading["resolution"]["route"] == "placement"
    assert tried(reading) == []
    assert abs(reading["impact_offset_mm"] - 8.0) <= reading["impact_offset_sigma_mm"]
    assert reading["resolution"]["value"] == reading["impact_offset_mm"]
    assert reading["template"] == {"swings": 1, "needed": 5, "built_from": None}
    assert set(reading["routes"]) == {"placement"}
    assert "orientation" in reading["routes"]["placement"]["diagnostics"]
    assert reading["label"] == il.EXPERIMENTAL_LABEL
    assert reading["stages_ms"]["placement_ms"] > 0
    json.dumps(reading)


def test_the_shots_own_d_plane_orientation_is_used_with_its_labels(tmp_path):
    analyser, session = live.LiveAnalyser(), Session(tmp_path)
    reading = analyser.analyse(with_d_plane(session.job(), face_status="model_geometry"))
    assert reading["status"] == "ok", reading["reason"]
    assert reading["route"] == "placement"
    assert reading["orientation_source"] == "face_angle=d_plane dynamic_loft=d_plane lie=prior"
    inputs = reading["inputs"]
    assert inputs["face_angle"]["label"] == "model_geometry"
    assert inputs["face_angle"]["source"].startswith("model_geometry")
    assert inputs["dynamic_loft"]["label"] == "calculated"
    assert inputs["lie"] == {"label": "estimated", "source": "prior_dynamic_lie_equals_static_lie"}
    assert inputs["ball_range"]["label"] == "measured"
    # the weakest input names the label, and an assumed lie is never "measured"
    assert reading["input_label"] == "estimated"
    assert reading["resolution"]["label"] == "estimated"
    assert "lie" in reading["weakest_inputs"]
    assert "face_angle" not in reading["weakest_inputs"]


def test_a_shot_with_no_angles_of_its_own_is_placed_on_priors_labelled_as_priors(tmp_path):
    analyser, session = live.LiveAnalyser(), Session(tmp_path)
    reading = analyser.analyse(without_capture_angles(session.job()))
    assert reading["status"] == "ok", reading["reason"]
    assert reading["orientation_source"] == "face_angle=prior dynamic_loft=prior lie=prior"
    for name in (*ANGLE_NAMES, "lie"):
        assert reading["inputs"][name]["label"] == "estimated"
        assert reading["inputs"][name]["source"].startswith("prior")
        assert reading["orientation"][name]["origin"] == "prior"
    assert {*ANGLE_NAMES, "lie"} <= set(reading["weakest_inputs"])
    assert reading["input_label"] == "estimated"
    assert "measured" not in json.dumps(reading["orientation"])


def test_when_placement_refuses_the_outline_runs_and_every_refusal_is_kept(
    tmp_path, placement_refuses
):
    analyser, session = live.LiveAnalyser(swings_needed=2), Session(tmp_path)
    first = analyser.analyse(session.job())
    assert first["status"] == "rejected"
    assert first["reason"] == "building the head outline: 1 of 2 swings with this club"
    assert first["route"] is None and first["input_label"] == "not_measured"
    assert tried(first) == [
        ("placement", "toe_edge_not_found_on_frame_10_2_samples"),
        ("outline_2d", "building_the_head_outline"),
    ]
    second = analyser.analyse(session.job())
    assert second["status"] == "ok", second["reason"]
    assert second["route"] == "outline_2d"
    assert tried(second) == [("placement", "toe_edge_not_found_on_frame_10_2_samples")]
    assert set(second["routes"]) == {"placement", "outline_2d"}
    assert second["routes"]["placement"]["reason"] == "the head's edge was not found around contact"
    assert second["routes"]["outline_2d"]["status"] == "ok"
    assert second["routes"]["outline_2d"]["diagnostics"]
    assert second["orientation_source"] == "face_angle=capture_angle dynamic_loft=capture_angle"
    assert second["inputs"]["face_angle"]["label"] == "calculated"
    assert second["impact_offset_mm"] == second["routes"]["outline_2d"]["impact_offset_mm"]


def test_a_wood_has_no_placement_model_and_the_outline_route_runs(tmp_path):
    analyser, session = live.LiveAnalyser(swings_needed=2), Session(tmp_path)
    reading = analyser.analyse(session.job(club="driver"))
    assert tried(reading) == [
        ("placement", "no_outline_model_for_category_driver"),
        ("outline_2d", "building_the_head_outline"),
    ]
    assert reading["reason"] == "building the head outline: 1 of 2 swings with this club"
    assert reading["routes"]["placement"]["reason"] == "no head model for this club category"


def test_a_shared_input_refusal_is_named_by_every_route(tmp_path):
    reading = live.LiveAnalyser().analyse(Session(tmp_path).job(range_source="camera_prior"))
    assert reading["reason_code"] == "no_ball_range_for_this_shot"
    assert tried(reading) == [
        ("placement", "no_ball_range_for_this_shot"),
        ("outline_2d", "no_ball_range_for_this_shot"),
    ]


def test_a_route_that_fails_is_refused_by_name_and_the_next_one_runs(tmp_path, monkeypatch):
    from openflight.camera import impact_placement as ip  # noqa: PLC0415

    def broken(*args, **kwargs):
        raise ValueError("bad edge")

    monkeypatch.setattr(ip, "place_impact", broken)
    analyser, session = live.LiveAnalyser(swings_needed=1), Session(tmp_path)
    reading = analyser.analyse(session.job())
    assert reading["status"] == "ok", reading["reason"]
    assert reading["route"] == "outline_2d"
    assert tried(reading) == [("placement", "error: ValueError: bad edge")]


def test_the_routes_are_the_placement_then_the_outline_and_nothing_else():
    assert live.ROUTES == ("placement", "outline_2d")
    assert live.LiveAnalyser().routes == live.ROUTES
    assert live.LiveAnalyser(routes=["outline_2d"]).routes == ("outline_2d",)
    with pytest.raises(ValueError):
        live.LiveAnalyser(routes=("outline_2d", "placement"))
    with pytest.raises(ValueError):
        live.LiveAnalyser(routes=("centre_default",))


def _shot_with_clip(session_job: dict, number: int) -> Shot:
    """A kiosk Shot carrying the synthetic clip's own bound range, as the kiosk records it."""
    from openflight.clubs import ClubType  # noqa: PLC0415

    record = session_job["shot"]
    shot = Shot(
        ball_speed_mph=110.0,
        club_speed_mph=record["club_speed_mph"],
        club=ClubType.IRON_7,
        timestamp=datetime(2026, 10, 22, 9, number),
        shot_number=number,
        ball_range_m=record["ball_range_m"],
        ball_range_source="iwr_post_impact",
        ball_range_uncertainty_m=0.03,
    )
    shot.camera_fusion_session_uuid = "session-a"
    shot.impact_timestamp = 100.0
    shot.ball_range_label = "measured"
    shot.impact_range_evidence = record["impact_range_evidence"]
    return shot


def test_each_live_reading_is_published_on_its_own_shot_with_its_route(kiosk, tmp_path):
    kiosk.extension.session_start_config(START)
    session = Session(tmp_path)
    jobs = [session.job(truth_mm=8.0), session.job(truth_mm=-6.0)]
    shots = [_shot_with_clip(job, number) for number, job in enumerate(jobs, start=1)]
    for shot, job in zip(shots, jobs):
        kiosk.extension.on_camera_capture(shot, _capture(job["clip_path"]))
        kiosk.extension.after_shot_published(shot, update_kind="first")
    analyser = live.LiveAnalyser()
    for index in range(2):
        job = kiosk.runner.jobs[index]
        assert job["shot_number"] == index + 1
        kiosk.extension.dispatcher.on_result(
            {"job_id": job["job_id"], "reading": analyser.analyse(job)}
        )
    for shot, truth in zip(shots, (8.0, -6.0)):
        reading = shot.impact_location
        assert reading["shot_number"] == shot.shot_number
        assert reading["status"] == "ok", reading["reason"]
        assert reading["route"] == "placement"
        assert reading["orientation_source"] == "face_angle=prior dynamic_loft=prior lie=prior"
        assert abs(reading["impact_offset_mm"] - truth) <= reading["impact_offset_sigma_mm"]
        assert reading["routes"]["placement"]["diagnostics"]["orientation"]
    assert [published.shot_number for published, _u, _s in kiosk.published] == [1, 2]
    assert [record["shot_number"] for _t, record in kiosk.log.events] == [1, 2]
    assert [record["route"] for _t, record in kiosk.log.events] == ["placement", "placement"]


def test_a_reading_for_another_shot_is_never_put_on_this_one(kiosk):
    shot = _kiosk_shot(1)
    kiosk.extension.on_camera_capture(shot, _capture())
    kiosk.extension.after_shot_published(shot, update_kind="first")
    kiosk.extension.dispatcher.on_result(
        {
            "job_id": kiosk.runner.jobs[0]["job_id"],
            "reading": {"status": "ok", "shot_number": 2, "impact_offset_mm": 5.0},
        }
    )
    assert shot.impact_location["status"] == "rejected"
    assert shot.impact_location["reason_code"] == "analysis_failed_reading_for_another_shot"
    assert "impact_offset_mm" not in shot.impact_location
