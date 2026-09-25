"""The exposure ladder's rungs, gains, pre-rung check and per-swing verdict."""

import json
import threading

import numpy as np
import pytest

from openflight.camera import study_ladder as sl


def _capture(
    tmp_path, *, level=60.0, exposure=150, gain=8.0, fps=120.0, gaps=0, name="camera_1", ball=True
):
    folder = tmp_path / name
    folder.mkdir()
    rng = np.random.default_rng(0)
    frames = np.clip(level + rng.normal(0, 1.5, (12, 800, 1280)), 0, 255)
    if ball:
        yy, xx = np.indices((800, 1280))
        frames[:, (np.hypot(xx - 640, yy - 520) <= 10)] = 200
    np.savez(
        folder / "frames.npz",
        frames=frames.astype(np.uint8),
        exposure_us=np.full(12, exposure, np.int32),
        analogue_gain=np.full(12, gain, np.float32),
        pre_trigger_count=np.int32(9),
    )
    (folder / "metadata.json").write_text(json.dumps({"delivered_fps": fps, "gap_count": gaps}))
    return folder


def test_the_ladder_is_the_agreed_rungs():
    assert [(r.arm_id, r.exposure_us) for r in sl.LADDER] == [
        ("arm5", 300),
        ("arm5", 200),
        ("arm5", 150),
        ("arm5", 100),
        ("arm5", 75),
        ("arm6", 300),
        ("arm6", 150),
        ("arm6", 75),
    ]
    assert all(r.photos for r in sl.LADDER if r.arm_id == "arm5")
    assert not any(r.photos for r in sl.LADDER if r.arm_id == "arm6")


def test_gain_keeps_the_brightness_until_the_ceiling():
    assert sl.rung_gain(3.0, 150) == pytest.approx(6.0)
    assert sl.rung_gain(5.0, 75) == pytest.approx(12.0)  # 20 capped


def test_photo_exposure_stays_under_the_frame_period():
    assert sl.photo_exposure_us(0.01, 20.0, 120.0) <= 8000
    assert sl.photo_exposure_us(10.0, 20.0, 120.0) == 100
    assert sl.photo_exposure_us(0.05, 20.0, 120.0) == 800  # (100-20)/(0.05*2)


def test_a_dark_rung_is_skipped_before_any_swing():
    frames = np.full((5, 800, 1280), 25.0) + np.random.default_rng(1).normal(0, 1, (5, 800, 1280))
    check = sl.pre_rung_check(frames, black_floor=18.0)
    assert check["ok"] is False and "too dark" in check["reason"]


def test_a_lit_rung_passes_the_pre_rung_check():
    frames = np.full((5, 800, 1280), 60.0) + np.random.default_rng(1).normal(0, 1, (5, 800, 1280))
    check = sl.pre_rung_check(frames, black_floor=18.0)
    assert check["ok"] is True and check["signal_dn"] == pytest.approx(42.0, abs=1.0)


def test_a_good_swing_is_green(tmp_path):
    rung = sl.Rung("full-150", "arm5", 150, True)
    verdict = sl.swing_verdict(_capture(tmp_path), rung, 8.0, 18.0, [])
    assert verdict["color"] == "green", verdict["reasons"]


@pytest.mark.parametrize(
    "kwargs, word",
    [
        ({"fps": 100.0}, "frames"),
        ({"gaps": 2}, "gap"),
        ({"exposure": 300}, "exposure"),
        ({"gain": 4.0}, "gain"),
        ({"level": 22.0}, "dark"),
        ({"level": 254.0}, "clipped"),
    ],
)
def test_each_picture_failure_is_red_and_named(tmp_path, kwargs, word):
    rung = sl.Rung("full-150", "arm5", 150, True)
    verdict = sl.swing_verdict(_capture(tmp_path, **kwargs), rung, 8.0, 18.0, [])
    assert verdict["color"] == "red"
    assert any(word in reason for reason in verdict["reasons"])


def test_no_resting_ball_is_only_amber(tmp_path):
    rung = sl.Rung("full-150", "arm5", 150, True)
    verdict = sl.swing_verdict(_capture(tmp_path, ball=False), rung, 8.0, 18.0, [])
    assert verdict["color"] == "amber"
    assert any("resting ball" in reason for reason in verdict["reasons"])


def _verdict(color, name):
    return {"capture": name, "color": color, "reasons": [], "ball": None}


def _make_pending_photo(state, capture="camera_final"):
    state.begin("full-300", 3.0, {"ok": True})
    state.record_swing(_verdict("red", "red-first"))
    state.record_swing(_verdict("green", "accepted-middle"))
    state.record_swing(_verdict("red", capture))


def test_five_accepted_swings_finish_a_rung_and_the_next_begins(tmp_path):
    state = sl.LadderState(tmp_path / "ladder.json")
    assert state.current.rung_id == "full-300"
    state.begin("full-300", 3.0, {"ok": True})
    for i in range(4):
        assert state.record_swing(_verdict("green" if i % 2 else "amber", f"c{i}")) == "active"
    assert state.record_swing(_verdict("green", "c4")) == "done"
    assert state.current.rung_id == "full-200"


def test_a_dark_rung_skips_itself_and_the_shorter_ones_in_its_mode(tmp_path):
    state = sl.LadderState(tmp_path / "ladder.json")
    state.begin("full-300", 3.0, {"ok": True})
    for i in range(5):
        state.record_swing(_verdict("green", f"a{i}"))
    state.begin("full-200", 4.5, {"ok": False, "reason": "too dark"})
    rungs = state.to_dict()["rungs"]
    statuses = [rungs[r]["status"] for r in ("full-200", "full-150", "full-100", "full-75")]
    assert statuses == ["skipped"] * 4
    assert state.current.rung_id == "half-300"


def test_two_reds_in_the_first_three_fail_the_rung(tmp_path):
    state = sl.LadderState(tmp_path / "ladder.json")
    state.begin("full-300", 3.0, {"ok": True})
    state.record_swing(_verdict("red", "r0"))
    state.record_swing(_verdict("green", "r1"))
    assert state.record_swing(_verdict("red", "r2")) == "failed"
    rungs = state.to_dict()["rungs"]
    assert rungs["full-300"]["status"] == "failed"
    assert rungs["full-75"]["status"] == "skipped"
    assert state.current.rung_id == "half-300"


def test_the_ladder_survives_a_reload_and_never_counts_a_capture_twice(tmp_path):
    path = tmp_path / "ladder.json"
    state = sl.LadderState(path)
    state.begin("full-300", 3.0, {"ok": True})
    state.record_swing(_verdict("green", "c0"))
    again = sl.LadderState(path)
    assert again.current.rung_id == "full-300"
    assert again.seen_captures() == {"c0"}
    assert again.record_swing(_verdict("green", "c0")) == "active"
    assert again.accepted("full-300") == 1


def test_the_end_of_the_ladder_has_no_current_rung(tmp_path):
    state = sl.LadderState(tmp_path / "ladder.json")
    for rung in sl.LADDER:
        state.begin(rung.rung_id, 2.0, {"ok": False, "reason": "too dark"})
        if state.current is None:
            break
    assert state.current is None


class FakeKiosk:
    def __init__(self, level=60.0, ready_after=0):
        self.level = level
        self.calls = []
        self._ready_after = ready_after

    def ready(self):
        self._ready_after -= 1
        return self._ready_after < 0

    def set_controls(self, exposure_us, gain):
        self.calls.append((exposure_us, gain))
        return {"exposure_us": exposure_us, "gain": gain}

    def frames(self, count):
        rng = np.random.default_rng(len(self.calls))
        noisy = self.level + rng.normal(0, 1.0, (count, 800, 1280))
        return np.clip(noisy, 0, 255).astype(np.uint8)


def _runner(tmp_path, kiosk, run_dir=None, done=None):
    state = sl.LadderState(tmp_path / "ladder.json")
    return sl.LadderRunner(
        state,
        kiosk,
        run_dir=lambda: run_dir,
        black_floor=lambda arm: 18.0,
        gain_at_300=lambda arm: 3.0,
        light_index=lambda arm: 0.05,
        photo_dir=tmp_path / "impact",
        on_mode_done=(done.append if done is not None else (lambda arm: None)),
        ready_timeout_s=1.0,
    )


def test_the_runner_waits_for_the_kiosk_then_sets_the_rung(tmp_path):
    kiosk = FakeKiosk(ready_after=3)
    runner = _runner(tmp_path, kiosk)
    runner.start_rung()
    assert kiosk.calls == [(300, 3.0)]
    assert runner.state.to_dict()["rungs"]["full-300"]["status"] == "active"


def test_new_captures_get_a_verdict_and_half_written_ones_wait(tmp_path):
    run = tmp_path / "run-01" / "arm5" / "camera"
    run.mkdir(parents=True)
    kiosk = FakeKiosk()
    runner = _runner(tmp_path, kiosk, run_dir=tmp_path / "run-01")
    runner.start_rung()
    _capture(run, exposure=300, gain=3.0, name="camera_a")
    (run / "camera_b").mkdir()  # still being written: no metadata yet
    verdicts = runner.poll_once()
    assert [v["capture"] for v in verdicts] == ["camera_a"]
    assert runner.poll_once() == []  # camera_a is not counted twice


def test_a_photo_restores_the_rung_even_when_it_fails(tmp_path):
    kiosk = FakeKiosk()
    runner = _runner(tmp_path, kiosk)
    runner.start_rung()
    runner.state._data["photo_target"] = {  # pylint: disable=protected-access
        "capture": "camera_a",
        "rung_id": "full-300",
    }

    def broken(count):
        raise OSError(f"kiosk went away asking for {count}")

    kiosk.frames = broken
    with pytest.raises(OSError):
        runner.photograph("camera_a", "full-300")
    assert kiosk.calls[-1] == (300, 3.0)


def test_a_photo_is_saved_against_the_last_swing(tmp_path):
    run = tmp_path / "run-01" / "arm5" / "camera"
    run.mkdir(parents=True)
    kiosk = FakeKiosk()
    runner = _runner(tmp_path, kiosk, run_dir=tmp_path / "run-01")
    runner.start_rung()
    _capture(run, exposure=300, gain=3.0, name="camera_a")
    runner.poll_once()
    path = runner.photograph("camera_a", "full-300")
    assert path.name == "camera_a.pgm" and path.is_file()
    assert kiosk.calls[-2] == (820, 2.0)  # the still: (100 - 18) / (0.05 x 2), then back
    assert kiosk.calls[-1] == (300, 3.0)
    assert runner.state.to_dict()["photos"]["camera_a"].endswith("camera_a.pgm")


def test_photo_after_same_mode_advance_restores_the_new_rung(tmp_path):
    run = tmp_path / "run-01" / "arm5" / "camera"
    run.mkdir(parents=True)
    kiosk = FakeKiosk()
    runner = _runner(tmp_path, kiosk, run_dir=tmp_path / "run-01")
    runner.start_rung()
    for index in range(4):
        runner.state.record_swing(_verdict("green", f"old-{index}"))
    _capture(run, exposure=300, gain=3.0, name="camera_fifth")

    runner.poll_once()
    path = runner.photograph("camera_fifth", "full-300")

    assert path.is_file()
    assert runner.state.current.rung_id == "full-200"
    assert kiosk.calls[-2] == (820, 2.0)
    assert kiosk.calls[-1] == (200, 4.5)


def test_final_full_mode_swing_waits_for_its_photo_before_handoff(tmp_path):
    run = tmp_path / "run-01" / "arm5" / "camera"
    run.mkdir(parents=True)
    done = []
    runner = _runner(tmp_path, FakeKiosk(), run_dir=tmp_path / "run-01", done=done)
    runner.start_rung()
    runner.state.record_swing(_verdict("red", "old-0"))
    runner.state.record_swing(_verdict("green", "old-1"))
    _capture(run, exposure=200, gain=3.0, name="camera_final")

    runner.poll_once()

    pending = runner.state.to_dict()["pending_photo"]
    assert pending == {"capture": "camera_final", "rung_id": "full-300"}
    assert runner.state.current.rung_id == "half-300"
    assert done == []
    runner.photograph("camera_final", "full-300")
    assert done == ["arm5"]
    assert runner.state.to_dict()["pending_photo"] is None
    assert runner.state.to_dict()["photo_target"] is None
    with pytest.raises(RuntimeError, match="no longer the current photo target"):
        runner.photograph("camera_final", "full-300")
    assert done == ["arm5"]


def test_fifth_accepted_full_75_swing_waits_for_its_exact_photo(tmp_path):
    run = tmp_path / "run-01" / "arm5" / "camera"
    run.mkdir(parents=True)
    state = sl.LadderState(tmp_path / "ladder.json")
    capture_number = 0
    for rung_id in ("full-300", "full-200", "full-150", "full-100"):
        state.begin(rung_id, 3.0, {"ok": True})
        for _ in range(5):
            state.record_swing(_verdict("green", f"old-{capture_number}"))
            capture_number += 1
    state.begin("full-75", 12.0, {"ok": True})
    for index in range(4):
        state.record_swing(_verdict("green", f"full75-{index}"))
    done = []
    runner = _runner(tmp_path, FakeKiosk(), run_dir=tmp_path / "run-01", done=done)
    runner.start_rung()
    _capture(run, exposure=75, gain=12.0, name="camera_final_75")

    runner.poll_once()

    assert runner.state.to_dict()["pending_photo"] == {
        "capture": "camera_final_75",
        "rung_id": "full-75",
    }
    assert done == []


def test_failed_shorter_prechecks_preserve_the_last_full_capture_for_photo(tmp_path):
    state = sl.LadderState(tmp_path / "ladder.json")
    state.begin("full-300", 3.0, {"ok": True})
    for index in range(5):
        state.record_swing(_verdict("green", f"camera_{index}"))
    done = []
    runner = _runner(tmp_path, FakeKiosk(level=24.0), done=done)

    runner.start_rung()

    assert runner.state.to_dict()["pending_photo"] == {
        "capture": "camera_4",
        "rung_id": "full-300",
    }
    assert done == []


def test_photo_failure_keeps_the_exact_pending_target_for_retry(tmp_path):
    kiosk = FakeKiosk()
    runner = _runner(tmp_path, kiosk)
    _make_pending_photo(runner.state, "c4")
    runner._configured_rung = "full-300"  # pylint: disable=protected-access
    kiosk.frames = lambda _count: (_ for _ in ()).throw(OSError("camera failed"))

    with pytest.raises(OSError, match="camera failed"):
        runner.photograph("c4", "full-300")

    assert runner.state.to_dict()["pending_photo"] == {
        "capture": "c4",
        "rung_id": "full-300",
    }


def test_stop_during_photo_write_keeps_pending_and_publishes_no_photo(tmp_path, monkeypatch):
    done = []
    runner = _runner(tmp_path, FakeKiosk(), done=done)
    runner.state.begin("full-300", 3.0, {"ok": True})
    runner.state.record_swing(_verdict("red", "c0"))
    runner.state.record_swing(_verdict("green", "c1"))
    runner.state.record_swing(_verdict("red", "camera_final"))
    runner.mode = "arm5"
    runner.tick()
    writing = threading.Event()
    release = threading.Event()
    original = sl.tempfile.NamedTemporaryFile

    class BlockingTemporary:
        def __init__(self, handle):
            self.handle = handle
            self.name = handle.name
            self.writes = 0

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, data):
            self.writes += 1
            if self.writes == 2:
                writing.set()
                assert release.wait(3)
            return self.handle.write(data)

    def temporary(*args, **kwargs):
        handle = original(*args, **kwargs)
        return (
            BlockingTemporary(handle)
            if kwargs.get("mode", args[0] if args else None) is None
            else handle
        )

    monkeypatch.setattr(sl.tempfile, "NamedTemporaryFile", temporary)
    errors = []

    def take_photo():
        try:
            runner.photograph("camera_final", "full-300")
        except RuntimeError as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=take_photo)
    worker.start()
    assert writing.wait(2)
    runner.stop()
    release.set()
    worker.join(3)
    assert errors == ["the ladder is stopped"]
    assert runner.state.to_dict()["pending_photo"]["capture"] == "camera_final"
    assert not (tmp_path / "impact" / "camera_final.pgm").exists()
    assert done == []


def test_pending_photo_and_skip_survive_a_reload(tmp_path):
    path = tmp_path / "ladder.json"
    state = sl.LadderState(path)
    _make_pending_photo(state)
    again = sl.LadderState(path)
    assert again.to_dict()["pending_photo"]["capture"] == "camera_final"
    again.skip_photo("camera_final", "full-300")
    reloaded = sl.LadderState(path).to_dict()
    assert reloaded["pending_photo"] is None
    assert reloaded["photo_target"] is None
    assert reloaded["photo_skips"]["camera_final"]["rung_id"] == "full-300"


def test_failed_photo_state_save_keeps_pending_in_memory(tmp_path, monkeypatch):
    state = sl.LadderState(tmp_path / "ladder.json")
    _make_pending_photo(state)
    monkeypatch.setattr(state, "_save", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        state.finish_photo("camera_final", "full-300", "impact/camera_final.pgm")
    assert state.to_dict()["pending_photo"] == {
        "capture": "camera_final",
        "rung_id": "full-300",
    }


def test_failed_photo_state_replace_keeps_pending_on_disk(tmp_path, monkeypatch):
    path = tmp_path / "ladder.json"
    state = sl.LadderState(path)
    _make_pending_photo(state)
    monkeypatch.setattr(sl.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError, match="disk"):
        state.finish_photo("camera_final", "full-300", "impact/camera_final.pgm")
    assert sl.LadderState(path).to_dict()["pending_photo"] == {
        "capture": "camera_final",
        "rung_id": "full-300",
    }


def test_stale_photo_identity_is_rejected(tmp_path):
    runner = _runner(tmp_path, FakeKiosk())
    runner.start_rung()
    runner._last_capture = "camera_new"  # pylint: disable=protected-access
    runner.last_verdict = {"capture": "camera_new", "rung_id": "full-300"}
    with pytest.raises(RuntimeError, match="no longer the current photo target"):
        runner.photograph("camera_old", "full-300")


def test_finishing_a_mode_hands_over_to_the_next(tmp_path):
    done = []
    kiosk = FakeKiosk(level=24.0)  # 6 DN above black: even the first rung is too dark
    runner = _runner(tmp_path, kiosk, done=done)
    runner.start_rung()
    assert done == ["arm5"]
    assert runner.state.current.rung_id == "half-300"
    assert runner.state.to_dict()["pending_photo"] is None


def test_a_capture_before_the_rung_is_set_waits_instead_of_killing_the_runner(tmp_path):
    run = tmp_path / "run-01" / "arm5" / "camera"
    run.mkdir(parents=True)
    kiosk = FakeKiosk()
    runner = _runner(tmp_path, kiosk, run_dir=tmp_path / "run-01")
    _capture(run, exposure=300, gain=3.0, name="camera_a")
    assert runner.poll_once() == []  # the rung is not set yet: nothing is verdicted
    runner.start_rung()
    assert [v["capture"] for v in runner.poll_once()] == ["camera_a"]


def test_the_runner_retries_a_kiosk_that_was_slow_to_come_up(tmp_path):
    kiosk = FakeKiosk(ready_after=100)
    runner = _runner(tmp_path, kiosk)
    runner.tick()
    assert "did not come up" in runner.last_verdict["reasons"][0]
    kiosk._ready_after = 0  # pylint: disable=protected-access
    runner.tick()
    assert runner.state.to_dict()["rungs"]["full-300"]["status"] == "active"


def test_an_unexpected_error_is_shown_not_fatal(tmp_path):
    kiosk = FakeKiosk()
    runner = _runner(tmp_path, kiosk)

    def broken():
        raise KeyError("something unforeseen")

    runner._run_dir = broken  # pylint: disable=protected-access
    runner.start_rung()
    runner.tick()
    assert "something unforeseen" in runner.last_verdict["reasons"][0]


def test_nothing_is_set_on_a_kiosk_that_is_between_modes(tmp_path):
    kiosk = FakeKiosk()
    runner = _runner(tmp_path, kiosk)
    runner.mode = "between modes"
    assert runner.start_rung() is None
    assert kiosk.calls == []
    runner.mode = "arm5"
    runner.start_rung()
    assert kiosk.calls == [(300, 3.0)]


def test_resuming_an_active_rung_reapplies_its_saved_controls_before_polling(tmp_path):
    kiosk = FakeKiosk()
    runner = _runner(tmp_path, kiosk)
    runner.state.begin("full-300", 3.0, {"ok": True})
    for i in range(5):
        runner.state.record_swing(_verdict("green", f"c{i}"))
    runner.state.begin("full-200", 4.5, {"ok": True})
    resumed = _runner(tmp_path, kiosk)
    resumed.tick()
    assert kiosk.calls == [(200, 4.5)]
    assert resumed.state.accepted("full-300") == 5
    resumed.tick()
    assert kiosk.calls == [(200, 4.5)]


def test_stopped_runner_does_not_apply_controls_or_take_photos(tmp_path):
    kiosk = FakeKiosk()
    runner = _runner(tmp_path, kiosk)
    runner.stop()
    runner.tick()
    runner.start_rung()
    with pytest.raises(RuntimeError, match="stopped"):
        runner.photograph("camera_a", "full-300")
    assert kiosk.calls == []
    assert runner.state.to_dict()["rungs"]["full-300"]["status"] == "pending"


def test_stop_interrupts_waiting_for_the_kiosk(tmp_path):
    entered = threading.Event()
    kiosk = FakeKiosk()

    def waiting():
        entered.set()
        return False

    kiosk.ready = waiting
    runner = _runner(tmp_path, kiosk)
    runner.ready_timeout_s = 90
    runner.start()
    try:
        assert entered.wait(2)
        runner.stop()
        assert not runner._thread.is_alive()  # pylint: disable=protected-access
        assert kiosk.calls == []
        assert runner.last_verdict is None
    finally:
        runner.stop()
