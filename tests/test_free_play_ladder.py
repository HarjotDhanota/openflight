"""Free play's automatic exposure ladder: one resolution, its rungs, 5 counted shots each.

The tester taps one resolution at setup; free play then walks that mode's ladder
rungs (longest exposure first), skipping before any swing the rungs the setup's
light cannot support and any below the sensor's recorded minimum, counts only
real (OPS) shots, and hands over to the automatic swing exposure after the last.
"""

import json

import numpy as np
import pytest

from openflight.camera import free_play_ladder as fpl, study_ladder, swing_exposure as sx

LIMITS = sx.SensorLimits(exposure_min_us=4, exposure_max_us=8_000, gain_min=1.0, gain_max=15.9375)
LOCK_ARM5 = {"source": "setup_lock", "signal_us": 600.0}  # e.g. 150 us x gain 4


def plan(arm_id="arm5", ladder_us=(300, 150, 75, 30, 10, 5), **overrides):
    options = {
        "light_index": 0.25,
        "sensor_limits": LIMITS,
        "gain_basis": LOCK_ARM5,
        **overrides,
    }
    return fpl.build_plan(arm_id, list(ladder_us), **options)


def rung_ids(built):
    return [rung["rung_id"] for rung in built["rungs"]]


def statuses(built):
    return {rung["rung_id"]: rung["status"] for rung in built["rungs"]}


# --- the plan: one resolution, its rungs, the skips -----------------------------------------


def test_rungs_run_longest_exposure_first_whatever_order_the_lens_lists():
    built = plan(ladder_us=(30, 300, 75, 150))

    assert rung_ids(built) == ["full-300", "full-150", "full-75", "full-30"]
    assert built["resolution"] == {
        "arm_id": "arm5",
        "label": "1280×800",
        "width": 1280,
        "height": 800,
        "fps": 120.0,
    }
    assert built["swings_per_rung"] == 5
    assert built["status"] == "ready"


def test_640x400_uses_its_own_rung_ids():
    built = plan(
        "arm6", (300, 75, 15), gain_basis={"source": "setup_lock_scaled", "signal_us": 300.0}
    )

    assert rung_ids(built) == ["half-300", "half-75", "half-15"]
    assert built["resolution"]["label"] == "640×400"


def test_rungs_the_light_cannot_support_are_skipped_before_any_swing():
    # at the gain ceiling of 12: 300 us -> 36 DN, 150 us -> 18 DN, under the 20 DN gate
    dim = plan(light_index=0.01)

    assert statuses(dim)["full-300"] == "pending"
    for rung_id in ("full-150", "full-75", "full-30", "full-10", "full-5"):
        assert statuses(dim)[rung_id] == "skipped"
    skipped = next(r for r in dim["rungs"] if r["rung_id"] == "full-150")
    assert skipped["skip_cause"] == "light"
    assert "more light" in skipped["skip_reason"]
    assert skipped["light_support"]["supported"] is False


def test_640x400_gets_twice_the_light_when_judging_support():
    # 150 us x 12 x 0.01 = 18 DN at 1280x800 (skipped) but 36 DN at 640x400
    full = plan("arm5", (150,), light_index=0.01)
    half = plan("arm6", (150,), light_index=0.01, gain_basis={"source": "s", "signal_us": 300.0})

    assert statuses(full)["full-150"] == "skipped"
    assert statuses(half)["half-150"] == "pending"


def test_rungs_below_the_recorded_sensor_minimum_are_skipped():
    built = plan(sensor_limits=sx.SensorLimits(7, 8_000, 1.0, 15.9375))

    assert statuses(built)["full-5"] == "skipped"
    skipped = next(r for r in built["rungs"] if r["rung_id"] == "full-5")
    assert skipped["skip_cause"] == "below_sensor_minimum"
    assert statuses(built)["full-10"] == "pending"


def test_without_a_recorded_minimum_nothing_under_the_proven_10_us_runs():
    built = plan(sensor_limits=None)

    assert statuses(built)["full-5"] == "skipped"
    assert statuses(built)["full-10"] == "pending"


def test_each_rung_gain_keeps_the_setup_lock_brightness():
    built = plan()
    gains = {rung["rung_id"]: rung["gain"] for rung in built["rungs"]}

    assert gains["full-300"] == study_ladder.starting_gain(300, None, LOCK_ARM5)[0] == 2.0
    assert gains["full-150"] == 4.0
    assert gains["full-30"] == 12.0  # capped at the ladder's gain ceiling
    rung = built["rungs"][0]
    assert rung["gain_source"] == "setup_lock"


def test_without_a_lock_or_gain_screen_no_rung_runs_and_it_says_why():
    built = plan(gain_basis=None, light_index=None)

    assert built["status"] == "not_run"
    assert all(r["status"] == "skipped" for r in built["rungs"])
    assert all(r["skip_cause"] == "no_gain" for r in built["rungs"])
    assert "light" in built["reason"]


def test_a_gain_screen_sets_the_gain_when_there_is_no_lock():
    built = plan(gain_basis=None, gain_at_300=3.0)

    assert built["rungs"][0]["gain"] == study_ladder.rung_gain(3.0, 300)
    assert built["rungs"][0]["gain_source"] == "gain_screen"


# --- the startup controls: never the near-black 8 us x 1 ------------------------------------


def test_startup_is_the_first_runnable_rung():
    built = plan(light_index=0.01, ladder_us=(300, 150))

    assert built["startup"] == {"exposure_us": 300, "gain": 2.0, "source": "first_rung"}


def test_startup_without_a_ladder_is_the_policy_seed_from_the_light():
    built = plan(light_index=0.0001)  # nothing supported

    assert built["status"] == "not_run"
    startup = built["startup"]
    assert startup["source"] == "automatic_seed_estimate"
    assert startup["exposure_us"] >= 100
    assert startup["exposure_us"] * startup["gain"] > 8.0


def test_startup_with_nothing_measured_is_the_blur_ceiling_not_near_black():
    built = plan(gain_basis=None, light_index=None, sensor_limits=None)

    startup = built["startup"]
    assert startup["source"] == "no_light_measured_blur_ceiling"
    assert startup["exposure_us"] == 294
    assert startup["gain"] == 1.0


# --- the study: counting, advancing, automatic -------------------------------------------


class Camera:
    """The held-controls seam the kiosk's capture runtime provides."""

    def __init__(self):
        self.applied = []
        self.automatic = 0

    def apply(self, exposure_us, gain):
        self.applied.append((exposure_us, gain))

    def begin_automatic(self):
        self.automatic += 1
        return {"status": "seeded", "target": {"exposure_us": 120, "gain": 3.0}}

    def controls(self):
        return (120, 3.0)


def study(tmp_path, built=None, camera=None, **kwargs):
    camera = camera or Camera()
    return (
        fpl.FreePlayExposureStudy(
            built or plan(ladder_us=(300, 150, 75)),
            tmp_path / fpl.STATE_NAME,
            apply_controls=camera.apply,
            begin_automatic=camera.begin_automatic,
            current_controls=camera.controls,
            run_async=lambda work: work(),
            settle_s=0.0,
            **kwargs,
        ),
        camera,
    )


def test_the_study_starts_on_the_first_rung_without_reapplying_its_startup(tmp_path):
    running, camera = study(tmp_path)

    progress = running.progress()
    assert progress["phase"] == "ladder"
    assert progress["rung"]["rung_id"] == "full-300"
    assert progress["text"] == "Exposure study: 300 µs · swing 1 of 5 · setting 1 of 3"
    assert camera.applied == []  # the kiosk was started at the first rung's controls
    saved = json.loads((tmp_path / fpl.STATE_NAME).read_text())
    assert saved["schema"] == fpl.STATE_SCHEMA
    assert saved["rungs"][0]["status"] == "active"


def test_five_counted_shots_per_rung_then_the_next_rung_is_applied_live(tmp_path):
    running, camera = study(tmp_path)

    records = [running.on_shot(number) for number in range(1, 6)]

    assert [r["swing_of_rung"] for r in records] == [1, 2, 3, 4, 5]
    assert {r["rung_id"] for r in records} == {"full-300"}
    assert all(r["ladder_phase"] == "ladder" for r in records)
    assert records[0]["exposure_us"] == 300 and records[0]["gain"] == 2.0
    assert camera.applied == [(150, 4.0)]  # applied live: no restart, no window
    progress = running.progress()
    assert progress["rung"]["rung_id"] == "full-150"
    assert progress["text"] == "Exposure study: 150 µs · swing 1 of 5 · setting 2 of 3"
    sixth = running.on_shot(6)
    assert (sixth["rung_id"], sixth["swing_of_rung"], sixth["setting_index"]) == ("full-150", 1, 2)


def test_false_triggers_never_count(tmp_path):
    running, camera = study(tmp_path)

    # a clip with no OPS shot never reaches on_shot; a verdict for an unknown shot is ignored
    running.record_verdict(99, {"status": "judged", "color": "red"})
    for number in range(1, 5):
        running.on_shot(number)

    assert running.progress()["rung"]["swings_done"] == 4
    assert camera.applied == []


def test_manual_next_retains_shots_and_late_verdicts_and_marks_study_partial(tmp_path):
    running, camera = study(tmp_path, plan(ladder_us=(300, 150)))
    first = running.on_shot(1)
    running.next_exposure("full-300")
    running.record_verdict(1, {"status": "judged", "color": "green"})
    second = running.on_shot(2)
    for number in range(3, 7):
        running.on_shot(number)

    assert first["exposure_us"] == 300
    assert (second["exposure_us"], second["swing_of_rung"]) == (150, 1)
    saved = json.loads((tmp_path / fpl.STATE_NAME).read_text())
    assert saved["ladder_status"] == "partial"
    assert saved["rungs"][0]["status"] == "incomplete"
    assert saved["rungs"][0]["finish_reason"] == "manual_next"
    assert len(saved["rungs"][0]["swings"]) == 1
    assert saved["rungs"][0]["counts"]["green"] == 1
    assert saved["rungs"][1]["status"] == "done"
    assert camera.applied == [(150, 4.0)]
    assert camera.automatic == 1
    running.finish()
    assert running.progress()["ladder_status"] == "partial"


def test_manual_next_without_shots_finishes_with_automatic_not_a_complete_ladder(tmp_path):
    running, camera = study(tmp_path, plan(ladder_us=(300,)))
    progress = running.next_exposure("full-300")
    assert progress["phase"] == "automatic"
    assert progress["ladder_status"] == "partial"
    assert progress["rungs"][0]["swings"] == 0
    assert camera.automatic == 1
    with pytest.raises(RuntimeError, match="not running"):
        running.next_exposure("full-300")


def test_manual_next_refuses_stale_rung_stopped_study_and_changes_in_progress(tmp_path):
    pending = []
    camera = Camera()
    running = fpl.FreePlayExposureStudy(
        plan(ladder_us=(300, 150, 75)),
        tmp_path / fpl.STATE_NAME,
        apply_controls=camera.apply,
        begin_automatic=camera.begin_automatic,
        run_async=pending.append,
        settle_s=0,
    )
    with pytest.raises(RuntimeError, match="already changed"):
        running.next_exposure("full-150")
    running.next_exposure("full-300")
    assert running.progress()["changing"] is True
    with pytest.raises(RuntimeError, match="wait"):
        running.next_exposure("full-150")
    pending.pop()()
    with pytest.raises(RuntimeError, match="already changed"):
        running.next_exposure("full-300")
    assert running.progress()["rung"]["rung_id"] == "full-150"
    running.finish()
    with pytest.raises(RuntimeError, match="not running"):
        running.next_exposure("full-150")


def test_manual_next_preserves_sensor_limit_skips(tmp_path):
    running, camera = study(
        tmp_path, plan(ladder_us=(300, 5, 150), light_index=1.0), live_sensor_min_us=8
    )
    progress = running.next_exposure("full-300")
    assert progress["rung"]["rung_id"] == "full-150"
    assert camera.applied[0][0] == 150
    skipped = next(rung for rung in progress["rungs"] if rung["exposure_us"] == 5)
    assert skipped["status"] == "skipped"
    assert "shortest exposure" in skipped["skip_reason"]


def test_after_the_last_rung_free_play_switches_to_the_automatic_exposure(tmp_path):
    running, camera = study(tmp_path)

    for number in range(1, 16):
        running.on_shot(number)

    assert camera.applied == [(150, 4.0), (75, 8.0)]
    assert camera.automatic == 1
    progress = running.progress()
    assert progress["phase"] == "automatic"
    assert progress["text"] == "Automatic exposure"
    after = running.on_shot(16)
    assert after["ladder_phase"] == "automatic"
    assert after["rung_id"] is None
    assert (after["exposure_us"], after["gain"]) == (120, 3.0)
    assert after["controls_source"] == "automatic_policy"
    saved = json.loads((tmp_path / fpl.STATE_NAME).read_text())
    assert saved["ladder_status"] == "complete"
    assert saved["automatic"]["seed"]["status"] == "seeded"


def test_a_plan_with_nothing_to_run_starts_automatic_at_once(tmp_path):
    running, camera = study(tmp_path, plan(light_index=0.0001))

    assert camera.automatic == 1
    assert running.progress()["phase"] == "automatic"
    saved = json.loads((tmp_path / fpl.STATE_NAME).read_text())
    assert saved["ladder_status"] == "not_run"


def test_the_live_sensor_minimum_skips_rungs_the_plan_could_not_know(tmp_path):
    built = plan(ladder_us=(300, 10, 5), light_index=1.0)  # sunlight: 5 us is supported
    running, _camera = study(tmp_path, built, live_sensor_min_us=8)

    rungs = {r["rung_id"]: r for r in running.to_dict()["rungs"]}
    assert rungs["full-5"]["status"] == "skipped"
    assert rungs["full-5"]["skip_cause"] == "below_sensor_minimum"
    assert running.progress()["settings_total"] == 2


def test_the_ready_light_is_amber_while_the_setting_changes(tmp_path):
    pending = []
    camera = Camera()
    running = fpl.FreePlayExposureStudy(
        plan(ladder_us=(300, 150)),
        tmp_path / fpl.STATE_NAME,
        apply_controls=camera.apply,
        begin_automatic=camera.begin_automatic,
        current_controls=camera.controls,
        run_async=pending.append,
        settle_s=0.0,
    )
    for number in range(1, 6):
        running.on_shot(number)

    assert running.progress()["changing"] is True
    assert running.ready_state() == {"phase": "ladder", "changing": True}
    pending.pop()()
    assert running.progress()["changing"] is False


def test_a_failed_control_change_is_recorded_not_hidden(tmp_path):
    camera = Camera()

    def refuse(_exposure, _gain):
        raise RuntimeError("camera capture is not running")

    running, _ = study(tmp_path, camera=camera)
    running._apply_controls = refuse
    for number in range(1, 6):
        running.on_shot(number)

    rung = running.to_dict()["rungs"][1]
    assert rung["status"] == "active"
    assert "not running" in rung["apply_error"]


def test_each_verdict_is_filed_against_its_rung_with_counts(tmp_path):
    running, _ = study(tmp_path)
    for number in range(1, 4):
        running.on_shot(number)

    running.record_verdict(1, {"status": "judged", "color": "green"})
    running.record_verdict(2, {"status": "judged", "color": "red"})
    running.record_verdict(3, {"status": "no_capture", "color": None, "reason": "no clip"})

    rung = running.to_dict()["rungs"][0]
    assert [s["shot_number"] for s in rung["swings"]] == [1, 2, 3]
    assert rung["swings"][1]["verdict"]["color"] == "red"
    assert rung["counts"] == {"green": 1, "amber": 0, "red": 1, "no_capture": 1, "error": 0}
    assert running.progress()["rungs"][0]["counts"]["green"] == 1


def test_stop_part_way_records_a_partial_ladder(tmp_path):
    running, _ = study(tmp_path)
    for number in range(1, 8):
        running.on_shot(number)

    running.finish()

    saved = json.loads((tmp_path / fpl.STATE_NAME).read_text())
    assert saved["ladder_status"] == "partial"
    by_id = {r["rung_id"]: r for r in saved["rungs"]}
    assert by_id["full-300"]["status"] == "done"
    assert by_id["full-150"]["status"] == "incomplete"
    assert len(by_id["full-150"]["swings"]) == 2
    assert by_id["full-75"]["status"] == "not_reached"
    assert saved["stopped_at"] is not None


def test_stop_after_the_ladder_keeps_it_complete(tmp_path):
    running, _ = study(tmp_path, plan(ladder_us=(300,)))
    for number in range(1, 6):
        running.on_shot(number)

    running.finish()

    assert json.loads((tmp_path / fpl.STATE_NAME).read_text())["ladder_status"] == "complete"


def test_without_a_camera_shots_are_labelled_and_nothing_is_applied(tmp_path):
    running = fpl.FreePlayExposureStudy(
        plan(), tmp_path / fpl.STATE_NAME, apply_controls=None, begin_automatic=None
    )

    record = running.on_shot(1)

    assert record["ladder_phase"] == "camera_unavailable"
    assert record["exposure_us"] is None and record["gain"] is None
    assert running.progress()["text"] == "Exposure study: camera unavailable"


# --- the per-swing picture verdict ------------------------------------------------------


def _clip(folder, *, signal_dn=184, exposure_us=150, gain=4.0, fps=120.0, gaps=0):
    """A clip like the ladder's own fixtures: a lit ball on a mid-grey mat."""
    folder.mkdir(parents=True)
    rng = np.random.default_rng(0)
    frames = np.clip(60.0 + rng.normal(0, 1.5, (12, 800, 1280)), 0, 255)
    yy, xx = np.indices((800, 1280))
    frames[:, np.hypot(xx - 640, yy - 520) <= 10] = 16 + signal_dn
    np.savez(
        folder / "frames.npz",
        frames=frames.astype(np.uint8),
        exposure_us=np.full(12, exposure_us, np.int32),
        analogue_gain=np.full(12, gain, np.float32),
        pre_trigger_count=np.int32(9),
    )
    (folder / "metadata.json").write_text(
        json.dumps(
            {
                "delivered_fps": fps,
                "gap_count": gaps,
                "auto_exposure": {
                    "exposure_us": exposure_us,
                    "gain": gain,
                    "controls_purpose": "capture",
                },
            }
        )
    )
    return folder


BALL = {"x": 640.0, "y": 520.0, "diameter_px": 20.0}


def test_a_good_clip_is_green_with_ball_and_club_usable(tmp_path):
    clip = _clip(tmp_path / "camera_001")

    verdict = fpl.judge_swing(
        clip, arm_id="arm5", rung_id="full-150", exposure_us=150, gain=4.0, expected_ball=BALL
    )

    assert verdict["status"] == "judged"
    assert verdict["color"] == "green"
    assert verdict["delivered_fps"] == 120.0
    assert verdict["ball_usable"] is True
    assert verdict["club_usable"] is True
    assert verdict["judged_against"] == {"rung_id": "full-150", "exposure_us": 150, "gain": 4.0}
    assert verdict["capture"] == "camera_001"


def test_dropped_frames_make_the_club_unusable_but_not_the_ball(tmp_path):
    clip = _clip(tmp_path / "camera_002", fps=80.0)

    verdict = fpl.judge_swing(
        clip, arm_id="arm5", rung_id="full-150", exposure_us=150, gain=4.0, expected_ball=BALL
    )

    assert verdict["color"] == "red"
    assert verdict["club_usable"] is False
    assert verdict["ball_usable"] is True


def test_a_dark_ball_is_not_usable(tmp_path):
    clip = _clip(tmp_path / "camera_003", signal_dn=5)

    verdict = fpl.judge_swing(
        clip, arm_id="arm5", rung_id="full-150", exposure_us=150, gain=4.0, expected_ball=BALL
    )

    # too dark to find, or found too dark: either way no ball to measure
    assert verdict["ball_usable"] is False


def test_an_automatic_clip_is_judged_at_its_own_controls(tmp_path):
    clip = _clip(tmp_path / "camera_004", exposure_us=120, gain=3.0)

    verdict = fpl.judge_swing(
        clip, arm_id="arm5", rung_id=None, exposure_us=None, gain=None, expected_ball=BALL
    )

    assert verdict["judged_against"] == {"rung_id": None, "exposure_us": 120, "gain": 3.0}
    assert verdict["color"] == "green"


def test_an_unreadable_clip_is_an_error_verdict_never_a_crash(tmp_path):
    folder = tmp_path / "camera_005"
    folder.mkdir()

    verdict = fpl.judge_swing(
        folder, arm_id="arm5", rung_id="full-150", exposure_us=150, gain=4.0, expected_ball=BALL
    )

    assert verdict["status"] == "error"
    assert verdict["color"] is None
    assert verdict["reason"]


def test_a_shot_without_a_clip_says_so():
    verdict = fpl.no_capture_verdict("no matched camera capture")

    assert verdict == {
        **fpl.no_capture_verdict("no matched camera capture"),
        "status": "no_capture",
        "color": None,
        "reason": "no matched camera capture",
        "ball_usable": None,
        "club_usable": None,
    }


def test_the_setting_change_runs_off_the_shot_callback_thread(tmp_path):
    import threading
    import time

    applied_on = []
    done = threading.Event()

    def apply(exposure_us, gain):
        applied_on.append((threading.current_thread().name, exposure_us, gain))
        done.set()

    running = fpl.FreePlayExposureStudy(
        plan(ladder_us=(300, 150)),
        tmp_path / fpl.STATE_NAME,
        apply_controls=apply,
        begin_automatic=lambda: None,
        settle_s=0.0,
    )
    for number in range(1, 6):
        running.on_shot(number)

    assert done.wait(2.0)
    assert applied_on == [("free-play-exposure", 150, 4.0)]
    deadline = time.monotonic() + 2.0
    while running.progress()["changing"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert running.progress()["changing"] is False


def test_a_ball_teed_elsewhere_in_the_box_is_found(tmp_path):
    # Harjot-outdoor-test-1: 18 of 33 swings were amber "resting ball not found" with the
    # ball in the box, more than one setup diameter from where the setup saw it
    clip = tmp_path / "camera_009"
    clip.mkdir()
    yy, xx = np.indices((800, 1280), dtype=np.float32)
    image = np.full((800, 1280), 60.0, np.float32)
    dx, dy = (xx - 700.0) / 10.0, (yy - 520.0) / 10.0
    inside = dx * dx + dy * dy <= 1.0
    nz = np.sqrt(np.clip(1.0 - dx * dx - dy * dy, 0.0, None))
    image[inside] = (80.0 + 140.0 * np.clip(-0.4 * dx - 0.5 * dy + 0.77 * nz, 0.0, None))[inside]
    rng = np.random.default_rng(2)
    frames = np.clip(np.round(image[None] + rng.normal(0, 0.8, (12, 800, 1280))), 0, 255)
    np.savez(
        clip / "frames.npz",
        frames=frames.astype(np.uint8),
        exposure_us=np.full(12, 150, np.int32),
        analogue_gain=np.full(12, 4.0, np.float32),
    )
    (clip / "metadata.json").write_text(json.dumps({"delivered_fps": 120.0, "gap_count": 0}))

    verdict = fpl.judge_swing(
        clip,
        arm_id="arm5",
        rung_id="full-150",
        exposure_us=150,
        gain=4.0,
        expected_ball=BALL,  # 60 px (3 diameters) left of this ball
        zone_box=(480, 400, 900, 640),
    )

    assert verdict["color"] == "green", verdict["reasons"]
    assert verdict["ball_usable"] is True
    assert verdict["ball"]["x"] == pytest.approx(700.0, abs=1.5)
