"""The analysis job outlives the tester service that started it.

Real processes only: a tester service starts the analysis, is terminated while
the analysis worker keeps running, and a new service process picks the job up,
then serves its review and bundle.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from openflight import session_bundle
from openflight.camera import session_review_routes as review_routes
from tests.session_fixtures import TESTER, capture_tree

REPO = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _serve(root: Path, port: int, log: Path) -> subprocess.Popen:
    handle = log.open("ab")
    try:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "openflight.camera.tester_server",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--sessions-root",
                str(root),
                "--no-inclinometer",
            ],
            cwd=REPO,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    finally:
        handle.close()


def _request(port: int, path: str, body: dict | None = None) -> tuple[int, bytes]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _wait_until_serving(port: int, server: subprocess.Popen) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        assert server.poll() is None, "tester service exited while starting"
        try:
            if _request(port, "/api/tester/arms")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(0.2)
    raise AssertionError("tester service did not start")


def _stop(server: subprocess.Popen) -> None:
    if server.poll() is None:
        server.terminate()
        server.wait(timeout=15)


def test_an_analysis_survives_its_service_and_a_new_service_finishes_the_review(tmp_path):
    root = capture_tree(tmp_path / "sessions")
    first_port, second_port = _free_port(), _free_port()
    first = _serve(root, first_port, tmp_path / "first-service.log")
    second = None
    try:
        _wait_until_serving(first_port, first)
        status, body = _request(first_port, "/api/tester/analysis", {"tester_id": TESTER})
        assert status == 202, body
        _stop(first)
        stopped_at = datetime.now(timezone.utc)
        assert first.returncode is not None

        second = _serve(root, second_port, tmp_path / "second-service.log")
        _wait_until_serving(second_port, second)
        seen = []
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            status, body = _request(second_port, f"/api/tester/analysis?tester_id={TESTER}")
            assert status == 200
            analysis = json.loads(body)
            seen.append(analysis["state"])
            if analysis["state"] in ("complete", "failed", "stopped"):
                break
            time.sleep(0.5)
        assert analysis["state"] == "complete", (seen, analysis.get("job"))
        assert "interrupted" not in seen
        job = analysis["job"]
        assert datetime.fromisoformat(job["finished_at"]) > stopped_at
        assert job["shots_total"] == 2

        worker_log = review_routes.analysis_log(root, TESTER).read_text(encoding="utf-8")
        assert f"analysis complete for {TESTER}" in worker_log

        status, body = _request(second_port, f"/api/tester/session-review?tester_id={TESTER}")
        assert status == 200
        review = json.loads(body)
        assert [attempt["shot_number"] for attempt in review["attempts"]] == [1, 2]
        status, archive = _request(second_port, f"/api/tester/package?tester_id={TESTER}")
        assert status == 200
        downloaded = tmp_path / "downloaded.zip"
        downloaded.write_bytes(archive)
        assert session_bundle.validate_bundle(downloaded)["tester_id"] == TESTER
    finally:
        _stop(first)
        if second is not None:
            _stop(second)
