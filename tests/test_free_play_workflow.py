"""Free-play stop through the real analysis child and a verified immutable zip."""

import json
import threading
import time
import zipfile

from flask import Flask

from openflight import free_play, session_bundle
from openflight.camera.tester_server import TesterJobManager as JobManager
from tests.session_fixtures import TESTER, capture_tree
from tests.test_free_play import Jobs


def test_stop_packages_real_saved_evidence_and_preserves_free_play_decisions(tmp_path, monkeypatch):
    root = capture_tree(tmp_path / "sessions")
    log = next(root.rglob("session_*.jsonl"))
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    identity = {"tester_id": TESTER, "run_id": "free-one", "unit_id": "synthetic-unit"}
    for event in events:
        if event["type"] == "session_start":
            event["config"]["free_play"] = {**identity, "enabled": True}
        if event["type"] == "shot_detected":
            event["free_play"] = {"run_id": identity["run_id"], "geometry_unverified": False}
            event["exposure_decision"] = {
                "status": "recorded",
                "decision": {
                    "setting": {"exposure_us": 100, "gain": 2},
                    "reading": {"median_dn": 60, "peak_dn": 90},
                    "light_index": {"median": 0.25},
                    "reason": "resting_ball",
                    "status": "ball_driven",
                },
            }
    log.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    capture, packaging = Jobs(), JobManager()
    capture.job = {"state": "running", "action": "free_play"}
    controller = free_play.FreePlayController(root, capture, packaging, kiosk_port=8080)
    controller.begin(TESTER, identity["run_id"], log.parent)
    tester = Flask("free-play-tester-workflow")
    controller.register(tester)
    closed = threading.Event()

    def finish_capture():
        with log.open("a", encoding="utf-8") as handle:
            handle.write('{"type":"session_end"}\n')
        closed.set()

    def proxy(_config, path, payload=None):
        with tester.test_client() as client:
            if payload is None:
                response = client.get("/api/tester/free-play" + path, query_string=identity)
            else:
                if path == "/package":
                    assert closed.is_set()
                response = client.post("/api/tester/free-play" + path, json={**payload, **identity})
            assert response.status_code < 300, response.get_json()
            return response.get_json()

    monkeypatch.setattr(free_play, "tester_request", proxy)
    kiosk = Flask("free-play-kiosk-workflow")
    config = {**identity, "enabled": True, "phase": "recording"}
    free_play.register_kiosk_routes(kiosk, config, finish_capture)
    with kiosk.test_client() as client:
        assert client.post("/api/free-play", json={"action": "stop"}).status_code == 200
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = client.get("/api/free-play").get_json()
            if state["phase"] in {"ready", "error"}:
                break
            time.sleep(0.05)
        assert state["phase"] == "ready", (state, packaging.status())
    bundle = state["bundle"]["path"]
    assert session_bundle.validate_bundle(bundle)["tester_id"] == TESTER
    with zipfile.ZipFile(bundle) as archive:
        log_name = next(name for name in archive.namelist() if name.endswith(log.name))
        saved = [json.loads(line) for line in archive.read(log_name).decode().splitlines()]
    assert saved[-1]["type"] == "session_end"
    shots = [entry for entry in saved if entry["type"] == "shot_detected"]
    assert len(shots) == 2
    assert shots[0]["exposure_decision"]["decision"]["setting"]["exposure_us"] == 100
    assert saved[0]["config"]["free_play"]["unit_id"] == "synthetic-unit"
    packaging._thread.join(timeout=5)
    assert packaging.status()["state"] == "complete"


def test_repackaging_an_unchanged_session_reuses_its_bundle_and_is_ready(tmp_path):
    """The analysis reuses the latest bundle when nothing changed; that is a success."""
    root = capture_tree(tmp_path / "sessions")
    log = next(root.rglob("session_*.jsonl"))
    capture, packaging = Jobs(), JobManager()
    capture.job = {"state": "running", "action": "free_play"}
    controller = free_play.FreePlayController(root, capture, packaging, kiosk_port=8080)
    controller.begin(TESTER, "free-one", log.parent)

    def package_and_wait():
        controller.package(TESTER, "free-one")
        packaging._thread.join(timeout=120)
        return controller.status()

    first = package_and_wait()
    assert first["phase"] == "ready", (first, packaging.status())
    # a retry after a failure the tester saw: the same run packages again, unchanged
    controller.run.update(phase="error", bundle=None, error="Packaging failed")
    second = package_and_wait()
    assert second["phase"] == "ready", (second, packaging.status())
    assert second["bundle"]["name"] == first["bundle"]["name"]
    assert second["bundle"]["reused"] is True
    assert session_bundle.validate_bundle(second["bundle"]["path"])["tester_id"] == TESTER
