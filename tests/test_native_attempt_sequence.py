"""Known-answer consecutive attempts through real admission, consumption and packaging."""

import base64
import hashlib
import importlib
import json
import shutil
import sys
import threading
import zipfile
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import session_bundle
from openflight.camera.native_fusion import NativeCaptureMonitor, NativeFusionExtension
from openflight.camera.trigger_policy import snapshot
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.session_review import build_session_review, report_markdown
from scripts.analysis.analyze_tester_session import write_review
from tests.test_native_fusion import TIMING, bundle


@pytest.fixture
def sequence(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts/hardware-test"))
    tools = importlib.import_module("tester_sync_session")
    root = tmp_path / "sessions"
    run = root / "tester/arm5/paired/run-01"
    files = tools.SessionFiles(run / "native-sync" / ("a" * 32))
    files.state["trigger_policy"] = snapshot("iwr_led")
    monkeypatch.setattr(files, "command", lambda: "next")
    camera = SimpleNamespace(output_dir=run / "arm5/camera")
    pending = []
    calls = []

    class Collector:
        host_boot = "boot"
        port = SimpleNamespace(port="test-boundary")
        capture_plan = {"name": "default"}
        failed = False
        index = 0
        generation = 0
        shot_id = 0
        outcomes = {}

        def __init__(self):
            self.rows, self.state = [], {}

        def begin(self):
            if self.failed:
                raise RuntimeError("uncertain transfer; runtime recovery required")
            self.index += 1
            self.generation += 1
            calls.append((self.index, "begin"))

        def wait_ball(self, observe, _cancelled):
            observe("place_ball", {"ball": {"state": "locked"}})
            return 32, {"ball": {"state": "locked"}}

        def arm(self, index):
            assert index == 32
            calls.append((self.index, "arm"))

        def wait_result(self, observe, _cancelled, *, on_trigger):
            observe("armed", {"trigger": {"latched": "0"}})
            if self.outcomes.get(self.index) == "timeout":
                raise TimeoutError("no IWR result")
            self.shot_id += 1
            on_trigger({"expected_shot_id": self.shot_id, "expected_generation": self.generation})
            held = json.loads(self.source_held.read_text())
            held.update(
                result_b64=base64.b64encode(b"held result").decode(),
                packet_b64=base64.b64encode(b"clock packet").decode(),
            )
            held["record"].update(shot_id=self.shot_id, generation=self.generation)
            return held

        def read_raw_dump(self, _radar):
            if self.outcomes.get(self.index) == "cancel":
                self.failed = True
                error = InterruptedError("cancel during dump")
                error.raw = b"partial IWR transfer"
                raise error
            raw = f"IWR shot {self.index}".encode()
            self.generation += 1
            return raw, {
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "transfer_timing": asdict(TIMING),
            }

        def finish(self):
            calls.append((self.index, "finish"))

    collector = Collector()

    class Rig:
        def __init__(self, **kwargs):
            self.folder = kwargs["folder"]
            self.boot_id = "boot"
            self.arm_id = f"arm-{collector.index}"
            self.trace_attempt_id = f"trace-{collector.index}"
            self.native_capture = False
            self.stop = threading.Event()
            assert kwargs["mode"] == "iwr_request"
            assert kwargs["keep_camera"] and kwargs["keep_radar"]

        def prepare(self):
            staged = tmp_path / f"input-{collector.index}"
            staged.mkdir()
            source, _result, clip = bundle(staged)
            collector.source_held = source / "iwr-held.json"
            self.source, self.clip = source, clip
            self.folder.mkdir()

        def start_wait(self):
            calls.append((collector.index, "wait"))

        def request_ops_capture(self, notice):
            assert notice["expected_shot_id"] == collector.shot_id
            self.native_capture = True
            shutil.copyfile(self.source / "ops-camera/gpio.jsonl", self.folder / "gpio.jsonl")
            target = camera.output_dir / f"capture-{collector.index:03d}"
            target.mkdir(parents=True)
            metadata = json.loads((self.clip / "metadata.json").read_text())
            request = metadata["trigger_clocks"]["capture_request"]
            request.update(arm_id=self.arm_id, event_id=f"event-{collector.index}")
            metadata.update(pre_trigger_frames=2, post_trigger_frames=3, gap_count=0)
            metadata["tester_setup"] = {"attempt": collector.index}
            (target / "metadata.json").write_text(json.dumps(metadata))
            np.savez(
                target / "frames.npz",
                frames=np.full((5, 2, 2), collector.index, dtype=np.uint8),
                sensor_timestamp_ns=np.array([80, 90, 110, 130, 150]),
                host_timestamp_ns=np.array([80, 90, 110, 130, 150]),
            )
            pending.append(SimpleNamespace(path=target))
            (self.folder / "routing.jsonl").write_text(
                json.dumps({"status": "dispatched", "request": request}) + "\n"
            )
            (self.folder / "ops-response.raw").write_text(
                "\n".join(
                    json.dumps({key: value})
                    for key, value in {
                        "sample_time": 1.0,
                        "trigger_time": 1.0256,
                        "I": [2048 + collector.index] * 4096,
                        "Q": [2048] * 4096,
                    }.items()
                )
            )

            return True

        def close(self):
            calls.append((collector.index, "close"))

    monkeypatch.setitem(sys.modules, "joint_clock_rig", SimpleNamespace(RigControl=Rig))
    owner = object.__new__(NativeFusionExtension)
    owner.tools, owner.files = tools, files
    owner.collector, owner.camera = collector, camera
    owner.policy = snapshot("iwr_led")
    owner.publishing, owner.sequence = False, 0
    owner.finished = threading.Event()
    owner.finished.set()
    owner.attempt_lock = threading.Lock()
    owner.current_attempt = None
    owner.original_camera_match = lambda *_a, **_kw: pending.pop(0) if pending else None
    return SimpleNamespace(
        owner=owner,
        root=root,
        run=run,
        collector=collector,
        pending=pending,
        calls=calls,
        processor=RollingBufferProcessor(),
        adapter=NativeCaptureMonitor(owner),
        stop=threading.Event(),
    )


def acquire(sequence):
    return sequence.owner.acquire(object(), sequence.processor, sequence.stop, None)


def shot(owner, number):
    result = SimpleNamespace(extras={}, shot_number=number)
    owner.on_shot_detected(result)
    owner.on_shot_accepted(result)
    return result


def test_consecutive_attempts_keep_owned_partials_in_normal_review_and_package(sequence):
    owner = sequence.owner
    sequence.collector.outcomes = {2: "timeout", 4: "cancel"}
    first = acquire(sequence)
    assert first.capture_id == "event-1" and first.num_samples == 4096
    first_shot = shot(owner, 1)
    owner.after_shot_published(first_shot, update_kind="first")
    sequence.stop.set()
    with pytest.raises(InterruptedError):
        acquire(sequence)
    assert sequence.collector.index == 1
    assert sequence.adapter.capture_for_shot(None).raw == b"IWR shot 1"
    with pytest.raises(InterruptedError):
        acquire(sequence)
    assert sequence.collector.index == 1
    sequence.stop.clear()
    first_clip = owner.camera_for_shot(None)
    assert first_clip.path.name == "capture-001"
    assert owner.evidence_for_shot(None) == {"attempt": 1}
    assert acquire(sequence) is None
    assert owner.association is None
    assert sequence.adapter.capture_for_shot(None) is None
    assert owner.camera_for_shot(None) is None
    assert owner.evidence_for_shot(None) is None
    sequence.pending.insert(0, first_clip)
    assert acquire(sequence).capture_id == "event-3"
    third_shot = shot(owner, 2)
    assert sequence.adapter.capture_for_shot(None).raw == b"IWR shot 3"
    assert owner.camera_for_shot(None).path.name == "capture-003"
    assert owner.evidence_for_shot(None) == {"attempt": 3}
    owner.after_shot_published(first_shot, update_kind="late_enrichment")
    assert not owner.finished.is_set()
    owner.after_shot_published(third_shot, update_kind="first")
    assert owner.finished.is_set()
    with pytest.raises(InterruptedError, match="cancel during dump"):
        acquire(sequence)
    assert sequence.collector.failed
    assert sequence.adapter.capture_for_shot(None) is None
    assert owner.camera_for_shot(None) is None
    assert [event for _, event in sequence.calls] == ["begin", "arm", "wait", "finish", "close"] * 4

    review = write_review(sequence.root, "tester", {})
    assert review == build_session_review(sequence.root, "tester", analysis={})
    attempts = review["runs"][0]["trigger_comparison"]["attempts"]
    assert [item["result"]["status"] for item in attempts] == [
        "captured_unqualified",
        "partial",
        "captured_unqualified",
        "partial",
    ]
    assert [item["published"] for item in attempts] == [True, False, True, False]
    assert attempts[2]["association"]["iwr_shot_id"] == 2
    assert attempts[2]["association"]["iwr_generation"] == 4
    assert [
        row["shot_number"] for row in attempts[2]["publication"] if row["stage"] == "published"
    ] == [2]
    assert not attempts[1]["ops_retained"] and not attempts[1]["camera_clips"]
    assert "attempt-004" in report_markdown(review)
    before = {
        path.relative_to(sequence.root): path.read_bytes()
        for path in sequence.run.rglob("*")
        if path.is_file()
    }
    packaged = session_bundle.build_bundle(sequence.root, "tester", viewer=None, provenance={})
    session_bundle.validate_bundle(Path(packaged["path"]))
    with zipfile.ZipFile(packaged["path"]) as archive:
        for path, content in before.items():
            assert archive.read(path.as_posix()) == content
        assert json.loads(archive.read("tester/analysis/session_review.json")) == review
    cancelled = owner.files.folder / "attempt-004"
    assert (cancelled / "iwr-dump.partial.bin").read_bytes() == b"partial IWR transfer"
    assert json.loads((cancelled / "iwr-held.json").read_text())["record"] == {
        "shot_id": 3,
        "generation": 6,
    }


def test_delayed_duplicate_publication_cannot_write_into_or_release_the_next_attempt(
    sequence, monkeypatch
):
    owner = sequence.owner
    acquire(sequence)
    old = shot(owner, 1)
    sequence.adapter.capture_for_shot(None)
    owner.camera_for_shot(None)
    owner.after_shot_published(old, update_kind="first")
    entered, resume, progress, done = (threading.Event() for _ in range(4))
    errors = []
    lock = threading.Lock()

    class ObservedLock:
        def __enter__(self):
            if threading.current_thread() is next_acquisition:
                progress.set()
            lock.acquire()

        def __exit__(self, *_args):
            lock.release()

    owner.attempt_lock = ObservedLock()
    original = owner.record_stage

    def pause_old_publication(stage, published_shot=None):
        if stage == "published" and published_shot is old:
            entered.set()
            assert resume.wait(5)
        original(stage, published_shot)

    def publish_old():
        try:
            owner.after_shot_published(old, update_kind="late_enrichment")
        except BaseException as error:
            errors.append(error)

    def acquire_next():
        try:
            acquire(sequence)
            shot(owner, 2)
        except BaseException as error:
            errors.append(error)
        finally:
            done.set()
            progress.set()

    monkeypatch.setattr(owner, "record_stage", pause_old_publication)
    late = threading.Thread(target=publish_old)
    next_acquisition = threading.Thread(target=acquire_next)
    late.start()
    assert entered.wait(5)
    next_acquisition.start()
    try:
        assert progress.wait(5)
        assert not done.is_set()
    finally:
        resume.set()
        late.join(5)
        next_acquisition.join(5)
    assert not late.is_alive() and not next_acquisition.is_alive()
    assert errors == []
    assert not owner.finished.is_set()
    rows = [
        json.loads(line)
        for line in (owner.current_attempt / "publication.jsonl").read_text().splitlines()
    ]
    assert not any(row["stage"] == "published" for row in rows)


def test_unowned_publication_after_cancel_cannot_mark_the_partial_attempt_published(sequence):
    sequence.collector.outcomes = {1: "cancel"}
    with pytest.raises(InterruptedError):
        acquire(sequence)
    sequence.owner.after_shot_published(
        SimpleNamespace(extras={}, shot_number=999), update_kind="late_enrichment"
    )
    assert not (sequence.owner.current_attempt / "publication.jsonl").exists()
