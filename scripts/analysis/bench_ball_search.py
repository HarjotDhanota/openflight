"""Time the resting-ball search on this machine, on real saved frames.

Run it on the Pi to see what a setup really costs there:

    uv run python scripts/analysis/bench_ball_search.py ~/frame.png
    uv run python scripts/analysis/bench_ball_search.py <epoch-dir>/camera-arm5-*.pgm

It times a full-frame search (first find and Save), a follow look (one fit at
the found size), one lit-ball fit, and the same full search in a worker process.
PGM needs nothing extra; PNG needs OpenCV or Pillow.
"""

from __future__ import annotations

import argparse
import os
import platform
import statistics
import sys
import time
from pathlib import Path

import numpy as np

from openflight.camera import tester_server as ts
from openflight.camera.ball_model import fit_lit_ball
from openflight.camera.reference_ball_range import estimate_reference_ball_range

BALL_CENTER_HEIGHT_M = 0.021335


def load_gray(path: Path) -> np.ndarray:
    """Read an 8-bit grey image: binary PGM directly, PNG through OpenCV or Pillow."""
    data = path.read_bytes()
    if data.startswith(b"P5"):
        fields = data.split(maxsplit=4)
        width, height, _maxval = (int(value) for value in fields[1:4])
        return np.frombuffer(fields[4][: width * height], dtype=np.uint8).reshape(height, width)
    try:
        import cv2  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is not None:
            return image
    except ImportError:
        pass
    from PIL import Image  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    return np.asarray(Image.open(path).convert("L"))


def _timed(function, repeat: int) -> tuple[float, object]:
    times, result = [], None
    for _ in range(repeat):
        started = time.perf_counter()
        result = function()
        times.append(time.perf_counter() - started)
    return statistics.median(times), result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="a saved camera frame (PGM or PNG)")
    parser.add_argument("--pitch-deg", type=float, default=0.0, help="camera pitch, + nose up")
    parser.add_argument("--roll-deg", type=float, default=0.0)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument(
        "--rig-geometry", type=Path, default=Path("config/enclosure_v3_rig_geometry.json")
    )
    args = parser.parse_args(argv)

    image = load_gray(args.image)
    arm = next(
        (arm for arm in ts.ARMS.values() if (arm.width, arm.height) == image.shape[::-1]), None
    )
    if arm is None:
        parser.error(f"no tester camera mode is {image.shape[1]}x{image.shape[0]}")
    tilt = {"camera_pitch_deg": args.pitch_deg, "roll_deg": args.roll_deg}
    camera = ts._reference_ball_camera(  # pylint: disable=protected-access
        arm, args.rig_geometry, tilt, None, None
    )
    frames = np.repeat(image[None], 3, axis=0)
    print(
        f"{platform.machine()} {platform.processor() or ''} | {os.cpu_count()} cores | "
        f"Python {sys.version.split()[0]} | numpy {np.__version__} | {arm.label}"
    )

    full_s, result = _timed(
        lambda: estimate_reference_ball_range(
            frames, camera, ball_center_height_m=BALL_CENTER_HEIGHT_M
        ),
        args.repeat,
    )
    print(f"full-frame search   {full_s * 1000:8.0f} ms  -> {result.status}")
    selected = result.selected
    if selected is None:
        print("no ball selected; follow and fit timings need one")
        return 1
    print(
        f"  ball at ({selected.x_px:.1f}, {selected.y_px:.1f}) d={selected.diameter_px:.1f} px, "
        f"size range {selected.size_camera_range_m:.3f} m, camera height "
        f"{selected.camera_height_m * 1000:.0f} mm"
    )
    follow = {"x_px": selected.x_px, "y_px": selected.y_px, "diameter_px": selected.diameter_px}
    follow_s, followed = _timed(
        lambda: ts._guided_camera_analysis(frames, camera, follow=follow),  # pylint: disable=protected-access
        args.repeat,
    )
    print(f"follow look         {follow_s * 1000:8.0f} ms  -> {followed[0].status}")
    fit_s, _fit = _timed(
        lambda: fit_lit_ball(
            image.astype(np.float32),
            selected.x_px,
            selected.y_px,
            selected.diameter_px / 2.0,
            noise_dn=1.0,
            expected_radius=selected.diameter_px / 2.0,
        ),
        args.repeat,
    )
    print(f"one held-size fit   {fit_s * 1000:8.0f} ms")
    ts.configure_ball_search_workers(1)
    try:
        kwargs = {"ball_center_height_m": BALL_CENTER_HEIGHT_M}
        ts._run_ball_search(frames, camera, kwargs)  # pylint: disable=protected-access
        worker_s, _ = _timed(
            lambda: ts._run_ball_search(frames, camera, kwargs),  # pylint: disable=protected-access
            args.repeat,
        )
    finally:
        ts.configure_ball_search_workers(0)
    print(f"full search, worker {worker_s * 1000:8.0f} ms  (includes moving the frames)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
