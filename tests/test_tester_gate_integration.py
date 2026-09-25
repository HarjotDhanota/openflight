"""Integration coverage for ladder admission using saved paired evidence."""

import json
import uuid

import numpy as np

from openflight.camera import study_ladder as sl


class GateKiosk:
    def __init__(self, run_dir, session_uuid, config_hash="config-hash"):
        self.run_dir = run_dir
        self.session_uuid = session_uuid
        self.config_hash = config_hash
        self.ready = True

    def setup_readiness(self):
        return {
            "ready": self.ready,
            "config_hash": self.config_hash,
            "checks": [],
            "blockers": [] if self.ready else [{"id": "lis3dh", "reason": "lost"}],
            "observations": {
                "runtime": {"run_dir": str(self.run_dir), "session_uuid": self.session_uuid}
            },
        }

    @staticmethod
    def set_controls(exposure_us, gain):
        return {"exposure_us": exposure_us, "gain": gain}

    @staticmethod
    def frames(count):
        return np.full((count, 800, 1280), 60, dtype=np.uint8)


def make_runner(tmp_path, run, session_uuid, monkeypatch):
    monkeypatch.setattr(sl, "SETTLE_S", 0)
    state = sl.LadderState(tmp_path / "ladder.json")
    kiosk = GateKiosk(run, session_uuid)
    runner = sl.LadderRunner(
        state,
        kiosk,
        run_dir=lambda: run,
        black_floor=lambda _arm: 18.0,
        gain_at_300=lambda _arm: 3.0,
        light_index=lambda _arm: 0.05,
        photo_dir=tmp_path / "impact",
        on_mode_done=lambda _arm: None,
        ready_timeout_s=0.1,
    )
    runner.start_rung()
    return runner, kiosk


def make_capture(run, session_uuid, *, trigger_ready=True, name="camera_1"):
    folder = run / "camera" / name
    folder.mkdir(parents=True)
    rng = np.random.default_rng(4)
    frames = np.clip(60 + rng.normal(0, 1.5, (12, 800, 1280)), 0, 255)
    yy, xx = np.indices((800, 1280))
    frames[:, np.hypot(xx - 640, yy - 520) <= 10] = 200
    np.savez(
        folder / "frames.npz",
        frames=frames.astype(np.uint8),
        exposure_us=np.full(12, 300, np.int32),
        analogue_gain=np.full(12, 3.0, np.float32),
        pre_trigger_count=np.int32(9),
    )
    trigger_setup = {
        "required": True,
        "ready": trigger_ready,
        "config_hash": "config-hash",
        "blockers": [] if trigger_ready else [{"id": "lis3dh", "reason": "moving"}],
        "observations": {"runtime": {"run_dir": str(run), "session_uuid": session_uuid}},
    }
    (folder / "metadata.json").write_text(
        json.dumps({"delivered_fps": 120.0, "gap_count": 0, "tester_setup": trigger_setup}),
        encoding="utf-8",
    )
    return folder


def write_entries(run, entries):
    (run / "session_gate.jsonl").write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8"
    )


def paired_entries(run, capture, session_uuid, *, complete):
    entries = [
        {"type": "session_start", "session_uuid": session_uuid},
        {
            "type": "camera_capture",
            "shot_number": 1,
            "capture_path": str(capture),
            "capture_error": None,
        },
    ]
    if complete:
        dump = run / "iwr" / "shot_1.bin"
        dump.parent.mkdir()
        dump.write_bytes(b"paired-iwr")
        entries.extend(
            [
                {
                    "type": "iwr6843_capture",
                    "shot_number": 1,
                    "capture_path": str(dump),
                    "capture_bytes": dump.stat().st_size,
                    "capture_error": None,
                },
                {"type": "shot_detected", "shot_number": 1},
            ]
        )
    return entries


def test_capture_waits_for_iwr_and_terminal_then_is_accepted(tmp_path, monkeypatch):
    run = tmp_path / "run-01"
    run.mkdir()
    session_uuid = str(uuid.uuid4())
    runner, _kiosk = make_runner(tmp_path, run, session_uuid, monkeypatch)
    capture = make_capture(run, session_uuid)
    write_entries(run, paired_entries(run, capture, session_uuid, complete=False))

    assert runner.poll_once() == []
    assert runner.state.accepted("full-300") == 0
    assert runner.state.seen_captures() == set()

    write_entries(run, paired_entries(run, capture, session_uuid, complete=True))
    verdicts = runner.poll_once()
    assert [item["capture"] for item in verdicts] == [capture.name]
    assert verdicts[0]["color"] == "green"
    assert runner.state.accepted("full-300") == 1


def test_invalid_trigger_stays_ineligible_after_runtime_health_recovers(tmp_path, monkeypatch):
    run = tmp_path / "run-01"
    run.mkdir()
    session_uuid = str(uuid.uuid4())
    runner, kiosk = make_runner(tmp_path, run, session_uuid, monkeypatch)
    assert runner.poll_once() == []  # pin the required config before a later health loss
    capture = make_capture(run, session_uuid, trigger_ready=False)
    write_entries(run, paired_entries(run, capture, session_uuid, complete=True))

    kiosk.ready = False
    assert runner.poll_once() == []
    rung = runner.state.to_dict()["rungs"]["full-300"]
    assert rung["status"] == "active"
    assert rung["swings"] == []
    assert runner.state.accepted("full-300") == 0
    assert runner.state.seen_captures() == {capture.name}

    kiosk.ready = True
    assert runner.poll_once() == []
    rung = runner.state.to_dict()["rungs"]["full-300"]
    assert rung["status"] == "active"
    assert rung["swings"] == []
    assert runner.state.accepted("full-300") == 0
