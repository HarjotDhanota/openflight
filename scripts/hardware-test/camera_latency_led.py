#!/usr/bin/env python3
"""Measure how far the camera's host timestamps lag the exposure they name.

THE BENCH TEST
--------------
An LED hangs off the SEN-14262 GATE line (through a resistor) so it lights the
instant the gate fires -- the same edge that triggers the capture. Clap near
the microphone: the capture triggers, and somewhere in the saved ring is the
first frame whose LED is lit. Compare the sub-frame instant the LED came on,
read off that frame's own exposure window, with the trigger stamp the capture
recorded, and the difference is the camera's stamp latency. The LED's own
rise (tens of nanoseconds) is negligible at 2 ms frames.

WHY IT MATTERS
--------------
The club pipeline walks back from the trigger to contact by the sound's
flight time (`camera.clubpose.impact_zone.contact_frame_from_trigger`). The
August session's ball motion says contact is 0.5-4 ms before the trigger, not
the 4.4 ms that walk-back predicts, and that it jitters shot to shot. Two
suspects: the camera's host stamps lag the exposure by a constant, or the
gate fires on something faster than air. This script decides the first one.
`gate_timing_probe.py` decides the second. Neither changes a pipeline
default; they produce the numbers that would.

THE CONVENTION
--------------
`host_timestamp_ns` is taken to mark the END of the frame's exposure, so
frame k was exposed over ``[t_k - exposure, t_k]``. A positive latency means
the stamp arrives that much AFTER the light it describes.

USAGE
-----
    SCRIPT=scripts/hardware-test/camera_latency_led.py

    # analyse captures on disk (a frames.npz, a capture dir or a session dir)
    uv run python $SCRIPT --archive ~/openflight_sessions
    uv run python $SCRIPT --archive ~/openflight_sessions --roi 120,40,12,12

    # on the Pi: arm the camera, clap N times, then analyse what was saved
    uv run python $SCRIPT --capture --repeat 10
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

# The LED is a handful of pixels; this box is the search unit and the default
# ROI size. Bigger dilutes the step with dark pixels, smaller rides noise.
DEFAULT_ROI_PX = 5
# Step contrast, in units of the dark-frame noise, below which the capture is
# refused rather than fitted. A working LED clears this by an order of
# magnitude; a capture that does not has no onset to find.
DEFAULT_MIN_CONFIDENCE = 6.0
# 8-bit quantisation floor, so a synthetic (noiseless) capture has a finite
# confidence instead of dividing by zero.
NOISE_FLOOR_DN = 0.5
# A frame lit for less than this fraction of its exposure is treated as dark,
# which bounds the onset error at this fraction of one exposure (40 us at the
# 800 us the rig runs) instead of chasing a crossing into the noise.
ONSET_FRACTION_FLOOR = 0.05
# Frames either side of the trigger for --capture. The LED fires AT the
# trigger, so the post window is what actually has to contain it.
CAPTURE_PRE_MS = 60.0
CAPTURE_POST_MS = 60.0


@dataclass(frozen=True)
class LedOnset:
    """When the LED came on, and how late the camera said so."""

    onset_frame_index: int
    onset_time_ns: int
    latency_ms: float
    roi: tuple[int, int, int, int]
    confidence: float
    lit_fraction: float
    dark_level: float
    lit_level: float
    exposure_us: float
    frame_count: int
    source: str = ""


@dataclass(frozen=True)
class LatencySummary:
    """The run, as a median and a spread -- never a mean."""

    n: int
    median_ms: float
    mad_ms: float
    min_ms: float
    max_ms: float


@dataclass(frozen=True)
class CaptureArchive:
    """One saved frames.npz, in the fields this measurement needs."""

    path: Path
    frames: np.ndarray
    host_timestamp_ns: np.ndarray
    trigger_host_timestamp_ns: int
    pre_trigger_count: int
    exposure_us: np.ndarray


# ---------------------------------------------------------------------------
# finding the LED
# ---------------------------------------------------------------------------


def _box_sums(stack: np.ndarray, size: int) -> np.ndarray:
    """Sums over every ``size``x``size`` box of every plane in a stack."""
    padded = np.pad(stack, ((0, 0), (1, 0), (1, 0)))
    integral = np.cumsum(np.cumsum(padded, axis=1), axis=2)
    return (
        integral[:, size:, size:]
        - integral[:, :-size, size:]
        - integral[:, size:, :-size]
        + integral[:, :-size, :-size]
    )


def brightest_step_roi(
    frames: np.ndarray, roi_px: int = DEFAULT_ROI_PX
) -> tuple[int, int, int, int]:
    """The box whose brightness rises most between consecutive frames.

    This is the LED: nothing else in a 2 ms exposure changes a compact patch
    of pixels by tens of counts in one frame.
    """
    _, height, width = frames.shape
    size = max(1, min(int(roi_px), height, width))
    rises = np.diff(frames.astype(np.float32), axis=0)
    sums = _box_sums(rises, size)
    position = np.unravel_index(int(np.argmax(sums)), sums.shape)
    return int(position[2]), int(position[1]), size, size


def _validated_roi(roi, height: int, width: int) -> tuple[int, int, int, int]:
    """An explicit ROI, or the reason it cannot be used."""
    try:
        x, y, box_w, box_h = (int(value) for value in roi)
    except (TypeError, ValueError) as exc:
        raise ValueError("roi_must_be_x_y_width_height") from exc
    if box_w <= 0 or box_h <= 0:
        raise ValueError(f"roi_{box_w}x{box_h}_has_no_area")
    if x < 0 or y < 0 or x + box_w > width or y + box_h > height:
        raise ValueError(f"roi_{x},{y},{box_w},{box_h}_falls_outside_the_{width}x{height}_frame")
    return x, y, box_w, box_h


# ---------------------------------------------------------------------------
# the fit
# ---------------------------------------------------------------------------


def _trace_noise(trace: np.ndarray) -> float:
    """Frame-to-frame noise on the ROI trace, floored at the quantisation step.

    Measured from the CONSECUTIVE DIFFERENCES rather than the levels, because
    the trace has two levels by construction: a MAD over the differences
    shrugs off the one huge outlier that is the LED itself.
    """
    steps = np.diff(trace)
    if steps.size == 0:
        return NOISE_FLOOR_DN
    deviation = np.abs(steps - float(np.median(steps)))
    scale = 1.4826 * float(np.median(deviation)) / math.sqrt(2.0)
    return max(scale, NOISE_FLOOR_DN)


def _exposure_ns(exposure_us, index: int, frame_count: int) -> float:
    """The exposure window of one frame, in nanoseconds."""
    if exposure_us is None:
        raise ValueError("exposure_us_missing_pass_the_archive_value_or_an_override")
    values = np.asarray(exposure_us, dtype=np.float64)
    if values.ndim == 0:
        window_us = float(values)
    elif values.shape == (frame_count,):
        window_us = float(values[index])
    else:
        raise ValueError("exposure_us_must_be_a_scalar_or_one_value_per_frame")
    if not math.isfinite(window_us) or window_us <= 0.0:
        raise ValueError(f"exposure_{window_us:.0f}_us_is_not_a_usable_window")
    return window_us * 1000.0


def led_onset(  # pylint: disable=too-many-arguments,too-many-locals
    frames,
    host_timestamp_ns,
    trigger_host_timestamp_ns,
    *,
    roi=None,
    exposure_us=None,
    roi_px: int = DEFAULT_ROI_PX,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    source: str = "",
) -> LedOnset:
    """Fit the LED's onset to sub-frame precision and report the stamp latency.

    The LED is found as the pixel box with the largest brightness step between
    consecutive frames, unless ``roi`` names one. The first lit frame is only
    PARTLY exposed to the LED, so its brightness sits between the dark level
    and the lit plateau; that ratio is the fraction of the exposure during
    which the LED was on, and it places the onset inside the frame:

        onset = host_stamp[k] - lit_fraction * exposure

    Latency is that onset minus the trigger stamp. Every capture that cannot
    carry the fit is refused by name rather than fitted anyway.
    """
    frames = np.asarray(frames)
    if frames.ndim != 3:
        raise ValueError("frames_must_be_a_stack_of_2d_images")
    frame_count, height, width = frames.shape
    if frame_count < 3:
        raise ValueError(f"only_{frame_count}_frames_need_at_least_3_for_dark_partial_and_lit")

    stamps = np.asarray(host_timestamp_ns, dtype=np.int64)
    if stamps.shape != (frame_count,):
        raise ValueError(f"timestamp_count_{stamps.size}_does_not_match_frame_count_{frame_count}")
    if np.any(np.diff(stamps) <= 0):
        raise ValueError("host_timestamps_not_strictly_increasing")

    trigger = int(trigger_host_timestamp_ns)
    if trigger <= 0:
        raise ValueError("capture_has_no_trigger_timestamp")

    box = brightest_step_roi(frames, roi_px) if roi is None else _validated_roi(roi, height, width)
    x, y, box_w, box_h = box
    trace = frames[:, y : y + box_h, x : x + box_w].reshape(frame_count, -1).mean(axis=1)

    noise = _trace_noise(trace)
    peak = float(np.max(trace))
    midpoint = 0.5 * (float(np.min(trace)) + peak)
    below = trace[trace < midpoint]
    dark = float(np.median(below)) if below.size else float(np.median(trace))
    step = peak - dark
    if step / noise < min_confidence:
        raise ValueError(
            f"no_clean_led_step_contrast_{step / noise:.1f}_below_{min_confidence:.1f}"
        )

    # The onset frame is the FIRST frame carrying any LED light, not the
    # biggest step: when the LED comes on late in an exposure the step into
    # the NEXT (fully lit) frame is the larger of the two.
    threshold = dark + max(3.0 * noise, ONSET_FRACTION_FLOOR * step)
    lit_frames = np.flatnonzero(trace >= threshold)
    index = int(lit_frames[0])
    if index < 2:
        raise ValueError(f"led_already_lit_at_frame_{index}_no_dark_baseline_to_step_from")

    # The plateau is the LED across a FULL exposure, so it can only be read
    # from frames AFTER the partially exposed one.
    plateau = [i for i in range(index + 1, frame_count) if trace[i] >= dark + 0.9 * step]
    if not plateau:
        raise ValueError(
            "led_lit_for_only_1_frame_cannot_separate_the_partial_frame_from_the_plateau"
        )
    lit = float(np.median(trace[plateau]))
    confidence = (lit - dark) / noise

    window_ns = _exposure_ns(exposure_us, index, frame_count)
    fraction = float(np.clip((trace[index] - dark) / (lit - dark), 0.0, 1.0))
    onset_ns = int(round(float(stamps[index]) - fraction * window_ns))
    return LedOnset(
        onset_frame_index=index,
        onset_time_ns=onset_ns,
        latency_ms=(onset_ns - trigger) / 1e6,
        roi=box,
        confidence=confidence,
        lit_fraction=fraction,
        dark_level=dark,
        lit_level=lit,
        exposure_us=window_ns / 1000.0,
        frame_count=frame_count,
        source=source,
    )


def summarise_latencies(onsets) -> LatencySummary:
    """Median latency and its MAD over a run of captures."""
    values = [float(onset.latency_ms) for onset in onsets]
    if not values:
        raise ValueError("no_onsets_to_summarise")
    median = statistics.median(values)
    return LatencySummary(
        n=len(values),
        median_ms=median,
        mad_ms=statistics.median([abs(value - median) for value in values]),
        min_ms=min(values),
        max_ms=max(values),
    )


def verdict_line(summary: LatencySummary) -> str:
    """The one line this whole bench test exists to print."""
    direction = "lag" if summary.median_ms >= 0 else "lead"
    return (
        f"camera stamps {direction} exposure by {abs(summary.median_ms):.2f} ms "
        f"(MAD {summary.mad_ms:.2f}, n={summary.n})"
    )


# ---------------------------------------------------------------------------
# archives
# ---------------------------------------------------------------------------


def find_archives(paths) -> list[Path]:
    """Every frames.npz under the given files or directories, in name order."""
    found: list[Path] = []
    for entry in paths:
        path = Path(entry).expanduser()
        if path.is_file():
            found.append(path)
        elif path.is_dir():
            found.extend(sorted(path.rglob("frames.npz")))
        else:
            raise ValueError(f"archive_path_does_not_exist_{path}")
    unique = sorted({item.resolve() for item in found})
    if not unique:
        raise ValueError(f"no_frames_npz_found_under_{', '.join(str(p) for p in paths)}")
    return unique


def load_archive(path) -> CaptureArchive:
    """Read one saved capture in the shipped frames.npz layout."""
    path = Path(path).expanduser()
    with np.load(path) as data:
        missing = [
            key
            for key in ("frames", "host_timestamp_ns", "trigger_host_timestamp_ns")
            if key not in data
        ]
        if missing:
            raise ValueError(f"archive_missing_{'_and_'.join(missing)}")
        frames = np.asarray(data["frames"])
        return CaptureArchive(
            path=path,
            frames=frames,
            host_timestamp_ns=np.asarray(data["host_timestamp_ns"], dtype=np.int64),
            trigger_host_timestamp_ns=int(data["trigger_host_timestamp_ns"]),
            pre_trigger_count=int(data["pre_trigger_count"]) if "pre_trigger_count" in data else -1,
            exposure_us=(
                np.asarray(data["exposure_us"], dtype=np.float64)
                if "exposure_us" in data
                else np.zeros(len(frames))
            ),
        )


def analyse_archive(archive: CaptureArchive, **kwargs) -> LedOnset:
    """Fit one loaded capture, labelled with the file it came from."""
    return led_onset(
        archive.frames,
        archive.host_timestamp_ns,
        archive.trigger_host_timestamp_ns,
        exposure_us=archive.exposure_us,
        source=str(archive.path),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# hardware: capture on the Pi (nothing below runs on a dev box)
# ---------------------------------------------------------------------------


def capture_archives(outdir: Path, repeat: int, args: argparse.Namespace) -> list[Path]:
    """Arm the shipped capture runtime and wait for `repeat` gate edges.

    This reuses `CameraCaptureRuntime` -- the same ring buffer, GPIO edge
    listener and frames.npz writer the kiosk uses -- so the archives this
    produces are byte-identical in layout to a real session's. Auto-exposure
    is held OFF: a moving exposure would change the very window the fit reads.
    """
    from openflight.camera.capture_runtime import (  # pylint: disable=import-outside-toplevel
        CameraCaptureRuntime,
        CameraCaptureSettings,
        parse_scaler_crop,
    )

    settings = CameraCaptureSettings(
        width=args.width,
        height=args.height,
        fps=args.fps,
        pre_ms=CAPTURE_PRE_MS,
        post_ms=CAPTURE_POST_MS,
        exposure_us=args.exposure_us,
        gain=args.gain,
        gpio_pin=args.gpio,
        auto_exposure=False,
        scaler_crop=parse_scaler_crop(args.scaler_crop),
    )
    runtime = CameraCaptureRuntime(output_dir=outdir, settings=settings)
    saved: list[Path] = []
    runtime.start()
    try:
        print(
            f"Armed at {args.width}x{args.height} {args.fps:.0f} fps, "
            f"{args.exposure_us} us exposure, GATE on BCM{args.gpio}."
        )
        print(f"Clap near the microphone {repeat} time(s); Ctrl-C to stop early.")
        while len(saved) < repeat:
            capture = runtime.capture_for_shot(None, timeout_s=args.timeout_s)
            if capture is None:
                print("  ...waiting for a gate edge")
                continue
            if not capture.valid:
                print(f"  capture #{capture.sequence} failed: {capture.error}")
                continue
            saved.append(Path(capture.path) / "frames.npz")
            print(f"  [{len(saved)}/{repeat}] saved {capture.path}")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        runtime.stop()
    return saved


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_roi(value: str | None) -> tuple[int, int, int, int] | None:
    """Parse an ``X,Y,W,H`` ROI."""
    if value is None:
        return None
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("--roi must be X,Y,W,H")
    try:
        x, y, width, height = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("--roi must contain integers") from exc
    return x, y, width, height


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--archive",
        action="append",
        default=[],
        metavar="PATH",
        help="A frames.npz, a capture directory or a session directory. Repeatable.",
    )
    parser.add_argument(
        "--capture",
        action="store_true",
        help="Raspberry Pi only: arm the camera and record --repeat captures first.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Captures to record with --capture (default: 1). The summary is "
        "over every archive analysed either way.",
    )
    parser.add_argument("--roi", metavar="X,Y,W,H", help="Force the LED ROI instead of finding it.")
    parser.add_argument("--roi-px", type=int, default=DEFAULT_ROI_PX, help="Automatic ROI size.")
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=DEFAULT_MIN_CONFIDENCE,
        help="Step contrast, in dark-frame noise units, required to accept a capture.",
    )
    parser.add_argument("--gpio", type=int, default=17, help="BCM pin carrying GATE (default: 17).")
    parser.add_argument("--fps", type=float, default=467.0)
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=200)
    parser.add_argument("--exposure-us", type=int, default=800)
    parser.add_argument("--gain", type=float, default=4.0)
    parser.add_argument("--scaler-crop", metavar="X,Y,W,H", help="Optional Picamera2 ScalerCrop.")
    parser.add_argument(
        "--timeout-s", type=float, default=10.0, help="Seconds to wait per capture."
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path.home() / "openflight_sessions" / "camera_latency_led",
        help="Where --capture writes its archives.",
    )
    return parser.parse_args(argv)


def _print_table(rows: list[tuple[str, str, str, str, str, str]]) -> None:
    """Print one fixed-width row per capture."""
    header = ("capture", "frame", "lit_frac", "exp_us", "conf", "latency_ms")
    widths = [max(len(header[i]), max(len(row[i]) for row in rows)) for i in range(len(header))]
    line = "  ".join(name.ljust(widths[i]) for i, name in enumerate(header))
    print(line)
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))


def main(argv=None) -> int:
    """Analyse LED captures and print the stamp-latency verdict."""
    args = parse_args(argv)
    try:
        roi = parse_roi(args.roi)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    paths: list[Path] = []
    if args.capture:
        outdir = args.outdir / datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            paths.extend(capture_archives(outdir, max(1, args.repeat), args))
        except (RuntimeError, ImportError) as exc:
            print(f"Capture unavailable: {exc}", file=sys.stderr)
            print(
                "Capture with the kiosk instead (scripts/start-kiosk.sh writes the same "
                "frames.npz), then re-run with --archive.",
                file=sys.stderr,
            )
            return 2
    if args.archive:
        try:
            paths.extend(find_archives(args.archive))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if not paths:
        print("Nothing to analyse: pass --archive PATH or --capture.", file=sys.stderr)
        return 2

    rows: list[tuple[str, str, str, str, str, str]] = []
    onsets: list[LedOnset] = []
    for path in paths:
        label = path.parent.name or path.name
        try:
            onset = analyse_archive(
                load_archive(path), roi=roi, roi_px=args.roi_px, min_confidence=args.min_confidence
            )
        except (ValueError, OSError) as exc:
            rows.append((label, "-", "-", "-", "-", f"REFUSED {exc}"))
            continue
        onsets.append(onset)
        rows.append(
            (
                label,
                str(onset.onset_frame_index),
                f"{onset.lit_fraction:.3f}",
                f"{onset.exposure_us:.0f}",
                f"{onset.confidence:.1f}",
                f"{onset.latency_ms:+.3f}",
            )
        )

    _print_table(rows)
    print()
    if not onsets:
        print("No capture carried a clean LED step; nothing measured.", file=sys.stderr)
        return 1
    summary = summarise_latencies(onsets)
    print(f"ROI {onsets[0].roi}  range {summary.min_ms:+.3f} .. {summary.max_ms:+.3f} ms")
    print(verdict_line(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
