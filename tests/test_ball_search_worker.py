"""The ball search gives the same answer in a worker process as in the tester process."""

import subprocess
import sys
import threading
from multiprocessing.connection import Listener
from pathlib import Path

import pytest

from openflight.camera import tester_server as ts
from tests import test_patch_ball_search as patch_tests


def test_the_worker_process_finds_the_same_ball():
    # the search the tester runs since P8-2: inside a confirmed patch only, on the
    # indoor scene the patch tests use
    camera = patch_tests.v3(pitch_deg=1.72)
    frames = patch_tests.indoor_scene()
    ball = patch_tests.INDOOR_BALL_PX
    patch = patch_tests.patch_from_centre_pixel(camera, (ball[0], ball[1] + 10.0))
    kwargs = {
        "search": patch_tests.search_for(camera, patch),
        "ball_center_height_m": patch_tests.BALL_CENTER_HEIGHT_M,
    }
    local = ts._run_ball_search(frames, camera, kwargs)
    ts.configure_ball_search_workers(1)
    try:
        remote = ts._run_ball_search(frames, camera, kwargs)
    finally:
        ts.configure_ball_search_workers(0)

    assert remote.status == local.status
    assert remote.selected is not None
    assert remote.selected.x_px == pytest.approx(local.selected.x_px)
    assert remote.selected.y_px == pytest.approx(local.selected.y_px)


def test_a_worker_ends_when_the_tester_server_is_killed(tmp_path):
    """29 Sept: every full test run left two spawn workers alive. A tester server that
    is killed (Stop on Windows, SIGKILL on the Pi) never shuts its pool down."""
    listener = Listener(("127.0.0.1", 0))
    accepted = []
    waiter = threading.Thread(target=lambda: accepted.append(listener.accept()), daemon=True)
    waiter.start()
    script = (
        "import os\n"
        "from openflight.camera import tester_server as ts\n"
        "from tests.worker_probe import hold_connection\n"
        "ts.configure_ball_search_workers(1)\n"
        f"print(ts._BALL_SEARCH_POOL.submit(hold_connection, {listener.address!r}).result())\n"
        "os._exit(0)\n"  # dies without shutting the pool down, as a killed server does
    )
    log = tmp_path / "server.log"
    with log.open("w", encoding="utf-8") as handle:
        # a file, not a pipe: a surviving worker would hold a pipe open forever
        server = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            stdout=handle,
            stderr=subprocess.STDOUT,
            timeout=120,
            check=False,
        )
    waiter.join(30)
    listener.close()
    output = log.read_text(encoding="utf-8")
    assert server.returncode == 0, output
    assert accepted, "the worker never reported in"
    worker = accepted[0]

    # the worker's end closes its connection; a live worker keeps it silent
    try:
        assert worker.poll(15), f"worker {output.strip()} outlived its server"
        with pytest.raises((EOFError, ConnectionError)):  # a reset on Windows
            worker.recv()
    finally:
        if not worker.closed:
            worker.close()
