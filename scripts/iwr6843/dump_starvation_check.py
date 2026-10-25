"""Confirm on the Pi that no kiosk load can make an IWR6843 dump lose bytes.

24 Oct: the kiosk's dump read, run on a thread of the busy process, lost 20-42 KB
of every dump while another thread held the interpreter lock in 60 ms bursts;
idle, and four CPU-burning processes, lost nothing (n_tty holds about 4 KiB, about
39 ms at 1,041,667 baud, and the port has no flow control). The kiosk now reads
through ``IWR6843SerialProcess``: the serial session runs in its own process.

This runs that reader under the same three loads, each placed in THIS process,
the one a kiosk thread would starve:

* ``idle``  nothing else running
* ``gil``   a thread doing JSON work in 60 ms bursts with 200 ms rests (holds the lock)
* ``cpu``   four separate processes spinning (CPU load without the lock)

One JSON row per dump. Expected for the serial process: every row ``"lost": 0``
and ``"complete": true``; the exit code is 1 otherwise. ``--baseline`` then repeats
the loads with the old in-process reader as the control: it should lose bytes
under ``gil``, which shows the load still bites.

Stop the kiosk and the tester first: the radar's port has one owner.

    uv run python scripts/iwr6843/dump_starvation_check.py \\
        --port /dev/serial/by-id/<the IWR's if00 port> --reps 3 \\
        --output iwr-starvation-check.jsonl --baseline
"""

from __future__ import annotations

import argparse
import contextlib
import json
import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterator, TextIO

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from openflight.iwr6843.driver import IWR6843DumpRecoveryError, IWR6843Radar  # noqa: E402
from openflight.iwr6843.dump import declared_dump_nbytes  # noqa: E402
from openflight.iwr6843.serial_process import IWR6843SerialProcess  # noqa: E402

DEFAULT_CONFIG = REPO / "config" / "iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"
LOADS = ("idle", "gil", "cpu")
GIL_BURST_S = 0.06
GIL_REST_S = 0.2
CPU_PROCESSES = 4
LOAD_WARMUP_S = 0.5
BETWEEN_DUMPS_S = 1.0


def spin(stop) -> None:
    """One CPU-burning process."""
    while not stop.is_set():
        sum(i * i for i in range(20_000))


def gil_bursts(stop: threading.Event, burst_s: float = GIL_BURST_S, rest_s: float = GIL_REST_S):
    """Hold the interpreter lock in bursts, as building shot payloads for a page poll does."""
    blob = [{"k": i, "v": [float(j) for j in range(200)], "s": "x" * 200} for i in range(400)]
    while not stop.is_set():
        end = time.perf_counter() + burst_s
        while time.perf_counter() < end:
            json.loads(json.dumps(blob))
        time.sleep(rest_s)


@contextlib.contextmanager
def load_running(load: str) -> Iterator[None]:
    """Run ``load`` in this process (and its children) for the block's duration."""
    if load == "idle":
        yield
        return
    if load == "gil":
        stop = threading.Event()
        workers = [threading.Thread(target=gil_bursts, args=(stop,), daemon=True)]
    elif load == "cpu":
        # spawn, as the tester's workers are: never fork a process with threads
        context = multiprocessing.get_context("spawn")
        stop = context.Event()
        workers = [
            context.Process(target=spin, args=(stop,), daemon=True) for _ in range(CPU_PROCESSES)
        ]
    else:
        raise ValueError(f"unknown load {load!r}; one of {', '.join(LOADS)}")
    for worker in workers:
        worker.start()
    time.sleep(LOAD_WARMUP_S)
    try:
        yield
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=5.0)


def one_dump(radar, *, timeout_s: float = 40.0, stall_tolerance_s: float = 4.0) -> dict:
    """Read one dump; how many bytes its own header declared, and how many arrived."""
    started = time.monotonic()
    timing = None
    error = None
    raw = b""
    try:
        raw, timing = radar.read_dump_timed(timeout_s, stall_tolerance_s)
    except IWR6843DumpRecoveryError as exc:
        raw, timing, error = bytes(exc.raw or b""), exc.timing, str(exc)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        error = f"{type(exc).__name__}: {exc}"
    expected = declared_dump_nbytes(raw) if raw else None
    received = min(len(raw), expected) if expected is not None else len(raw)
    transfer_s = None
    if timing is not None and None not in (timing.first_byte_ns, timing.last_byte_ns):
        transfer_s = round((timing.last_byte_ns - timing.first_byte_ns) / 1e9, 3)
    return {
        "expected": expected,
        "received": received,
        "lost": expected - received if expected is not None else None,
        "complete": error is None and expected is not None and received == expected,
        "error": error,
        "seconds": round(time.monotonic() - started, 2),
        "transfer_s": transfer_s,
        "cli_ready": timing is not None and timing.cli_ready_ns is not None,
    }


def run_loads(
    radar,
    reader: str,
    loads: tuple[str, ...],
    reps: int,
    emit: Callable[[dict], None],
    *,
    between_s: float = BETWEEN_DUMPS_S,
) -> list[dict]:
    """``reps`` dumps under each load; every row is emitted as soon as it is read."""
    rows = []
    for load in loads:
        with load_running(load):
            for rep in range(reps):
                row = {
                    "reader": reader,
                    "reader_pid": getattr(radar, "pid", None) or os.getpid(),
                    "script_pid": os.getpid(),
                    "load": load,
                    "rep": rep,
                    **one_dump(radar),
                }
                rows.append(row)
                emit(row)
                time.sleep(between_s)
    return rows


def _session(radar, config: Path, settle_s: float, run: Callable[[object], list[dict]]):
    try:
        radar.send_config(str(config))
        time.sleep(settle_s)
        return run(radar)
    finally:
        try:
            radar.stop_sensor()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"sensorStop failed: {exc}", file=sys.stderr)
        radar.close()


def summary(rows: list[dict]) -> dict:
    """Bytes lost and incomplete dumps per (reader, load)."""
    out: dict[str, dict] = {}
    for row in rows:
        entry = out.setdefault(
            f"{row['reader']}/{row['load']}", {"dumps": 0, "incomplete": 0, "lost": 0}
        )
        entry["dumps"] += 1
        entry["incomplete"] += 0 if row["complete"] else 1
        entry["lost"] += row["lost"] or 0
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--port", default=None, help="IWR6843 CLI port (auto-detect by default)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="radar cfg")
    parser.add_argument("--reps", type=int, default=3, help="dumps per load")
    parser.add_argument("--loads", nargs="+", choices=LOADS, default=list(LOADS))
    parser.add_argument("--settle-s", type=float, default=2.0, help="wait after the config")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="then repeat with the old in-process reader (the control; it should lose bytes)",
    )
    parser.add_argument("--output", type=Path, default=None, help="also append rows here")
    args = parser.parse_args(argv)
    loads = tuple(args.loads)
    sink: TextIO | None = args.output.open("a", encoding="utf-8") if args.output else None

    def emit(row: dict) -> None:
        line = json.dumps(row)
        print(line, flush=True)
        if sink is not None:
            sink.write(line + "\n")
            sink.flush()

    try:
        rows = _session(
            IWR6843SerialProcess(port=args.port),
            args.config,
            args.settle_s,
            lambda radar: run_loads(radar, "serial_process", loads, args.reps, emit),
        )
        if args.baseline:
            rows += _session(
                IWR6843Radar(port=args.port),
                args.config,
                args.settle_s,
                lambda radar: run_loads(radar, "in_process", loads, args.reps, emit),
            )
        result = {"summary": summary(rows)}
        emit(result)
    finally:
        if sink is not None:
            sink.close()
    isolated = [row for row in rows if row["reader"] == "serial_process"]
    return 0 if isolated and all(row["complete"] and row["lost"] == 0 for row in isolated) else 1


if __name__ == "__main__":
    sys.exit(main())
