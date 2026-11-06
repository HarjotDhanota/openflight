"""Final consumers wait for real camera/model enrichment after a speed deadline."""

import json
import threading
from dataclasses import replace

import pytest

from openflight import kiosk_extensions, server
from openflight.camera.impact_live import ImpactLiveExtension
from openflight.camera.native_fusion import NativeFusionExtension
from tests import test_saved_fusion_publication as saved


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    yield from saved.pipeline.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def consumers(pipeline, monkeypatch, tmp_path):
    jobs, notified = [], []

    class Runner:
        def submit(self, job):
            jobs.append(job)

        def stop(self):
            pass

    impact = ImpactLiveExtension(runner_factory=lambda _: Runner())
    impact.enabled = True
    impact.session_start_config({"golfer": {"handed": "RH"}})
    native = object.__new__(NativeFusionExtension)
    native.attempt_lock = threading.Lock()
    native.association = {"event_id": "known-answer-current-attempt"}
    native.finished = threading.Event()
    native.current_attempt = tmp_path / "native-attempt"
    native.current_attempt.mkdir()

    class Notifications(kiosk_extensions.KioskExtension):
        def after_shot_published(self, shot, *, update_kind):
            notified.append((shot.shot_number, update_kind))

    monkeypatch.setattr(
        kiosk_extensions,
        "_extensions",
        [*kiosk_extensions.registered(), native, impact, Notifications()],
    )
    yield jobs, notified, native, impact
    impact.on_shutdown()


@pytest.mark.parametrize("deadline", [False, True])
def test_impact_and_native_release_receive_only_complete_fusion_snapshot(
    pipeline, consumers, monkeypatch, deadline
):
    jobs, notified, native, impact = consumers
    waiting, release = threading.Event(), threading.Event()
    original = server._fuse_camera_measurements

    def held_camera(*args):
        waiting.set()
        assert release.wait(15), "camera fusion hold was not released"
        return original(*args)

    monkeypatch.setattr(server, "_fuse_camera_measurements", held_camera)
    try:
        shot = pipeline.capture(1)
        assert waiting.wait(10), "real enrichment did not reach camera fusion"
        if deadline:
            saved.expire_enrichment(1)
            packets = pipeline.received()
            assert packets and packets[-1]["shot"]["ball_speed_mph"] > 90
            assert packets[-1]["shot"]["camera_fusion_processing"] is None
            assert pipeline.forwarded == [1]
        assert not jobs, "provisional speed publication dispatched incomplete impact input"
        assert not native.finished.is_set(), "provisional speed publication released acquisition"
        assert notified == []
        assert len(impact._clips) == 1
        release.set()
        saved.wait_pipeline()
        assert len(jobs) == 1
        body = jobs[0]["shot"]
        assert body["camera_fusion_processing"]["ball_estimate"]["status"] == "accepted"
        assert body["launch_angle_vertical"] == pytest.approx(18.0, abs=1)
        assert len(body["metrics"]) == 37
        assert body["shot_number"] == 1
        assert notified == [(1, "late_update" if deadline else "first")]
        assert native.finished.is_set() and not impact._clips
        assert pipeline.forwarded == [1]
        impact.dispatcher.on_result(
            {
                "type": "result",
                "job_id": jobs[0]["job_id"],
                "reading": {
                    "status": "refused",
                    "reason_code": "known_answer_handoff",
                    "shot_number": 1,
                },
            }
        )
        final = server.shot_to_dict(shot)
        assert final["impact_location"]["reason_code"] == "known_answer_handoff"
        assert len(jobs) == 1 and len(notified) == 1
        stages = [
            json.loads(line)["stage"]
            for line in (native.current_attempt / "publication.jsonl").read_text().splitlines()
        ]
        assert stages.count("published") == 1
    finally:
        release.set()
        saved.wait_pipeline()


def test_terminal_ops_only_publication_still_notifies_final_consumers(pipeline, consumers):
    jobs, notified, native, _impact = consumers
    pipeline.cameras.pop(1)
    pipeline.radars[1] = replace(pipeline.radars[1], raw=None, error="known-answer missing IWR")
    shot = pipeline.capture(1)
    saved.wait_pipeline()
    assert notified == [(1, "first")] and native.finished.is_set()
    assert not jobs and pipeline.forwarded == [1]
    assert shot.ball_speed_mph > 90 and shot.launch_angle_vertical is None
    assert pipeline.received()[-1]["shot"]["ball_speed_mph"] > 90
