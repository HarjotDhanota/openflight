"""Confirm on the Pi that no kiosk load makes the camera drop frames.

24 Oct (Harjot-outdoor-test-1, OV9281 at 1280x800 "120 fps", 8 buffers): 9 of 39 clips
lost 3-4 frames. In each, the frame callback waited 75-85 ms, the 7 queued frames then
arrived within a millisecond of each other, and the sensor's next frames found no free
buffer. 10 of the 11 waits began 145-178 ms into the kiosk's once-a-second look for the
resting ball, which then searched the box on a kiosk thread (344-1035 ms on the Pi). The
look's search now runs in a worker process (``ball_at_address.LookWorker``).

This runs the camera as the kiosk does (``CameraCaptureRuntime``, the R8 stream, no
trigger), records every frame's SensorTimestamp and callback time, and runs each load in
THIS process, the one whose interpreter lock the frame callback needs:

* ``idle``         nothing else running
* ``look_worker``  a resting-ball look once a second through ``LookWorker`` (the kiosk now)
* ``look_here``    the same look on a thread of this process (the kiosk on 24 Oct): the
                   control, which should drop frames
* ``gil``          a thread doing JSON work in 60 ms bursts with 200 ms rests
* ``cpu``          four separate processes spinning (CPU load without the lock)

One JSON row per load: frames, frames dropped and where, callback waits over 30 and 50 ms,
the longest wait and the largest delivery lag. The exit code is 1 unless ``idle`` and
``look_worker`` dropped nothing. Stop the kiosk and the tester first: the camera has one
owner. Point the camera at the mat with a ball in the box, as at address.

    uv run python scripts/hardware-test/camera_frame_starvation_check.py \\
        --seconds 60 --output camera-starvation-check.jsonl
"""

from __future__ import annotations

import argparse
import contextlib
import json
import multiprocessing
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Iterator, TextIO

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from openflight.camera.ball_at_address import BallAtAddress, LookWorker  # noqa: E402
from openflight.camera.capture_runtime import (  # noqa: E402
    CameraCaptureRuntime,
    CameraCaptureSettings,
)
from openflight.camera.triggered_buffer import CameraFrame, delivery_summary  # noqa: E402

LOADS = ("idle", "look_worker", "look_here", "gil", "cpu")
MUST_NOT_DROP = ("idle", "look_worker")
# Harjot-outdoor-test-1's box and setup ball (1280x800, 6 mm lens)
DEFAULT_BOX = (301, 296, 975, 676)
DEFAULT_BALL = (724.5, 421.6, 43.0)
LOOK_EVERY_S = 1.0
GIL_BURST_S = 0.06
GIL_REST_S = 0.2
CPU_PROCESSES = 4
LOAD_WARMUP_S = 1.0
WAIT_THRESHOLDS_MS = (30.0, 50.0)


def spin(stop) -> None:
    """One CPU-burning process."""
    while not stop.is_set():
        sum(i * i for i in range(20_000))


def gil_bursts(stop: threading.Event) -> None:
    """Hold the interpreter lock in bursts, as building shot payloads for a page poll does."""
    blob = [{"k": i, "v": [float(j) for j in range(200)], "s": "x" * 200} for i in range(400)]
    while not stop.is_set():
        end = time.perf_counter() + GIL_BURST_S
        while time.perf_counter() < end:
            json.loads(json.dumps(blob))
        time.sleep(GIL_REST_S)


def looks(stop: threading.Event, runtime, monitor: BallAtAddress, locate) -> None:
    """The kiosk's look loop: one look at the latest frame a second."""
    while not stop.wait(LOOK_EVERY_S):
        frames = runtime.recent_frames(1, timeout_s=0.5)
        if frames:
            monitor.observe(frames[-1].image, at=time.time(), locate=locate)


class FrameLog:
    """Every frame's SensorTimestamp and callback time, appended from the frame callback."""

    def __init__(self):
        self._lock = threading.Lock()
        self._frames: list[CameraFrame] = []

    def wrap(self, add_frame: Callable[[CameraFrame], None]) -> Callable[[CameraFrame], None]:
        """``add_frame`` that also logs the frame's two times (never its image)."""

        def logged(frame: CameraFrame) -> None:
            with self._lock:
                self._frames.append(
                    CameraFrame(
                        image=None,
                        sensor_timestamp_ns=frame.sensor_timestamp_ns,
                        host_timestamp_ns=frame.host_timestamp_ns,
                        exposure_us=frame.exposure_us,
                        analogue_gain=frame.analogue_gain,
                    )
                )
            add_frame(frame)

        return logged

    def take(self) -> list[CameraFrame]:
        """The frames logged since the last take."""
        with self._lock:
            frames, self._frames = self._frames, []
        return frames


def summarize(load: str, frames: list[CameraFrame], seconds: float) -> dict:
    """One load's row: what the camera delivered while it ran."""
    row = {"load": load, "seconds": round(seconds, 1), "frames": len(frames)}
    if len(frames) < 2:
        return {**row, "dropped_frames": None, "error": "no frames reached the callback"}
    delivery = delivery_summary(frames, pre_trigger_count=1)
    waits_ms = [
        (later.host_timestamp_ns - earlier.host_timestamp_ns) / 1e6
        for earlier, later in zip(frames, frames[1:])
    ]
    span_s = (frames[-1].sensor_timestamp_ns - frames[0].sensor_timestamp_ns) / 1e9
    return {
        **row,
        "delivered_fps": round((len(frames) - 1) / span_s, 2) if span_s > 0 else None,
        "dropped_frames": delivery["dropped_frames"],
        "drop_events": len(delivery["drops"]),
        "drop_sizes": sorted(drop["frames_missing"] for drop in delivery["drops"]),
        **{
            f"callback_waits_over_{threshold:.0f}ms": sum(wait > threshold for wait in waits_ms)
            for threshold in WAIT_THRESHOLDS_MS
        },
        "longest_callback_wait_ms": round(max(waits_ms), 1),
        "max_delivery_lag_ms": round(delivery["delivery_lag_ms"]["max"], 1),
        "queue_burst_frames": delivery["queue_burst_frames"],
    }


@contextlib.contextmanager
def load_running(load: str, runtime, monitor: BallAtAddress) -> Iterator[None]:
    """Run ``load`` in this process (and its children) for the block's duration."""
    if load == "idle":
        yield
        return
    worker = None
    if load in ("look_worker", "look_here"):
        stop = threading.Event()
        worker = LookWorker() if load == "look_worker" else None
        locate = worker.locate if worker is not None else None
        workers = [threading.Thread(target=looks, args=(stop, runtime, monitor, locate))]
    elif load == "gil":
        stop = threading.Event()
        workers = [threading.Thread(target=gil_bursts, args=(stop,), daemon=True)]
    elif load == "cpu":
        # spawn, as the kiosk's workers are: never fork a process with threads
        context = multiprocessing.get_context("spawn")
        stop = context.Event()
        workers = [
            context.Process(target=spin, args=(stop,), daemon=True) for _ in range(CPU_PROCESSES)
        ]
    else:
        raise ValueError(f"unknown load {load!r}; one of {', '.join(LOADS)}")
    if worker is not None:
        # start the worker (its imports take seconds on the Pi) before the window opens
        frames = runtime.recent_frames(1, timeout_s=2.0)
        if frames:
            monitor.observe(frames[-1].image, at=time.time(), locate=worker.locate)
    for thread in workers:
        thread.start()
    try:
        yield
    finally:
        stop.set()
        for thread in workers:
            thread.join(timeout=15.0)
        if worker is not None:
            worker.close()


def run_loads(
    runtime,
    log: FrameLog,
    monitor: BallAtAddress,
    loads: tuple[str, ...],
    seconds: float,
    emit: Callable[[dict], None],
) -> list[dict]:
    """``seconds`` of frames under each load; each row is emitted as soon as it is read."""
    rows = []
    for load in loads:
        with load_running(load, runtime, monitor):
            time.sleep(LOAD_WARMUP_S)
            log.take()
            started = time.monotonic()
            time.sleep(seconds)
            row = summarize(load, log.take(), time.monotonic() - started)
        rows.append(row)
        emit(row)
    return rows


def passed(rows: list[dict]) -> bool:
    """Whether every load that must not drop frames ran and dropped none."""
    checked = [row for row in rows if row["load"] in MUST_NOT_DROP]
    return bool(checked) and all(row.get("dropped_frames") == 0 for row in checked)


def _floats(text: str, count: int) -> tuple[float, ...]:
    values = tuple(float(part) for part in text.split(","))
    if len(values) != count:
        raise argparse.ArgumentTypeError(f"expected {count} comma-separated numbers")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--seconds", type=float, default=60.0, help="frames per load")
    parser.add_argument("--loads", nargs="+", choices=LOADS, default=list(LOADS))
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--fps", type=float, default=120.0)
    parser.add_argument("--exposure-us", type=int, default=150)
    parser.add_argument("--gain", type=float, default=8.0)
    parser.add_argument(
        "--box",
        type=lambda text: tuple(int(v) for v in _floats(text, 4)),
        default=DEFAULT_BOX,
        help="the look's box x0,y0,x1,y1 (default: Harjot-outdoor-test-1's)",
    )
    parser.add_argument(
        "--ball",
        type=lambda text: _floats(text, 3),
        default=DEFAULT_BALL,
        help="the setup ball x,y,diameter in pixels (sets the look's detector scale)",
    )
    parser.add_argument("--output", type=Path, default=None, help="also append rows here")
    args = parser.parse_args(argv)
    sink: TextIO | None = args.output.open("a", encoding="utf-8") if args.output else None

    def emit(row: dict) -> None:
        line = json.dumps(row)
        print(line, flush=True)
        if sink is not None:
            sink.write(line + "\n")
            sink.flush()

    settings = CameraCaptureSettings(
        width=args.width,
        height=args.height,
        fps=args.fps,
        exposure_us=args.exposure_us,
        gain=args.gain,
        auto_exposure=False,
    )
    x, y, diameter = args.ball
    monitor = BallAtAddress(
        box_px=args.box, setup_ball={"x": x, "y": y, "diameter_px": diameter}, setup_range_m=None
    )
    log = FrameLog()
    rows: list[dict] = []
    try:
        with tempfile.TemporaryDirectory(prefix="camera-starvation-") as clips:
            runtime = CameraCaptureRuntime(
                output_dir=clips, settings=settings, use_gpio_trigger=False
            )
            runtime._ring.add_frame = log.wrap(runtime._ring.add_frame)  # pylint: disable=protected-access
            runtime.start()
            try:
                rows = run_loads(runtime, log, monitor, tuple(args.loads), args.seconds, emit)
            finally:
                runtime.stop()
        emit({"passed": passed(rows), "must_not_drop": list(MUST_NOT_DROP)})
    finally:
        if sink is not None:
            sink.close()
    return 0 if passed(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
