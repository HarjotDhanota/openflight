"""The real shared collector preserves policy and partials across stop and timeout."""

import base64
import importlib
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from openflight.camera.trigger_policy import snapshot


@pytest.mark.parametrize("outcome", ["complete", "timeout", "cancel"])
def test_shared_collector_journals_partial_attempts_without_new_acquisition(
    tmp_path, monkeypatch, outcome
):
    scripts = Path(__file__).parents[1] / "scripts/hardware-test"
    monkeypatch.syspath_prepend(str(scripts))
    module = importlib.import_module("tester_sync_session")
    calls = []

    class Rig:
        def __init__(self, **kwargs):
            self.folder = kwargs["folder"]
            self.arm_id, self.boot_id = "owned-arm", "boot"
            self.native_capture = True
            self.trace_attempt_id = "trace-owned"
            self.stop = threading.Event()
            assert kwargs["keep_camera"] and kwargs["keep_radar"]

        def prepare(self):
            self.folder.mkdir()
            (self.folder / "ops-response.raw").write_bytes(b"owned raw OPS")
            calls.append("rig_prepare")

        def start_wait(self):
            calls.append("rig_wait")

        def close(self):
            calls.append("rig_closed")

    monkeypatch.setitem(sys.modules, "joint_clock_rig", SimpleNamespace(RigControl=Rig))
    monkeypatch.setattr(
        module, "camera_evidence", lambda *_a, **_k: {"status": "missing_or_ambiguous", "clips": []}
    )

    class Collector:
        rows = []
        failed = False
        state = {}
        port = object()
        host_boot = "boot"

        def begin(self):
            calls.append("iwr_begin")

        def wait_ball(self, publish, _cancelled):
            publish("learning_background", {"ball": {"state": "building"}})
            publish("place_ball", {"ball": {"state": "locked"}})
            return 32, {"ball": {"state": "locked"}}

        def arm(self, index):
            assert index == 32
            calls.append("iwr_arm")

        def wait_result(self, publish, _cancelled):
            publish("armed", {"trigger": {"latched": "0"}})
            if outcome == "timeout":
                raise TimeoutError("no IWR detection")
            if outcome == "cancel":
                raise InterruptedError("session stopped")
            return {
                "result_b64": base64.b64encode(b"result").decode(),
                "packet_b64": base64.b64encode(b"clock").decode(),
                "record": {"shot_id": 1, "generation": 2},
            }

        def read_raw_dump(self, _radar):
            return b"owned raw IWR", {"bytes": 13}

        def finish(self):
            calls.append("iwr_finish")

    files = module.SessionFiles(tmp_path / "control")
    files.state["trigger_policy"] = snapshot("hybrid")
    folder = files.folder / "attempt-001"
    camera = SimpleNamespace(output_dir=tmp_path / "camera")
    if outcome == "cancel":
        with pytest.raises(InterruptedError):
            module.attempt(Collector(), files, folder, camera=camera, radar=object())
    else:
        module.attempt(Collector(), files, folder, camera=camera, radar=object())
    result = json.loads((folder / "RESULT.json").read_text())
    assert result["status"] == "partial" and result["ops_capture_observed"]
    assert result["sound_gpio_used"] is False and result["iwr_forced_capture"] is False
    assert (folder / "ops-camera/ops-response.raw").read_bytes() == b"owned raw OPS"
    assert json.loads((folder / "trigger-policy.json").read_text()) == snapshot("hybrid")
    observations = [
        json.loads(line) for line in (folder / "iwr-observations.jsonl").read_text().splitlines()
    ]
    assert len(observations) == 3
    assert all(row["device_timestamp"] is None for row in observations)
    assert calls == ["iwr_begin", "rig_prepare", "iwr_arm", "rig_wait", "iwr_finish", "rig_closed"]
    assert (folder / "iwr-dump.bin").exists() == (outcome == "complete")
    assert files.state["attempts"][0]["name"] == "attempt-001"
