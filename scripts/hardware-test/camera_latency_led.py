#!/usr/bin/env python3
# pylint: disable=too-many-lines  # one self-contained bench script, by design
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

SHOT MODE (--shot)
------------------
Leave the LED wired, hit a real ball, and the SAME capture carries both
events: the ball leaving the tee is contact, the LED lighting is the GATE
edge. Both are read off the camera's own clock, so no stamp latency and no
clock transfer enters their difference -- it IS the walk-back the pipeline
assumes (4.59 ms of airborne flight at 1.575 m and 20 C). This is the
primary decider for the timing question; the clap test above only
characterises the camera's stamps.

USAGE
-----
    SCRIPT=scripts/hardware-test/camera_latency_led.py

    # analyse captures on disk (a frames.npz, a capture dir or a session dir)
    uv run python $SCRIPT --archive ~/openflight_sessions
    uv run python $SCRIPT --archive ~/openflight_sessions --roi 120,40,12,12

    # on the Pi: arm the camera, clap N times, then analyse what was saved
    uv run python $SCRIPT --capture --repeat 10

    # struck balls with the LED in frame: the walk-back and its jitter
    uv run python $SCRIPT --shot --archive ~/openflight_sessions --distance-m 1.575
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

from openflight import acoustics  # noqa: E402  # pylint: disable=wrong-import-position
from openflight.camera.club_motion import (  # noqa: E402  # pylint: disable=wrong-import-position
    detect_reference_ball,
)

# The shipped rig's ball-to-unit distance (impact_zone.BALL_TO_UNIT_M).
BALL_TO_UNIT_M = 1.575
# How far the measured walk-back may sit from the airborne expectation before
# the shot is called early or late rather than airborne.
WALK_BACK_TOLERANCE_MS = 0.3
# Pixels of margin around the resting ball that the LED search must avoid: a
# ball that blurs as it leaves changes pixels just outside its resting disc.
LED_BALL_MARGIN_PX = 3
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
# A first-changed frame this fully changed was already changed when its
# exposure OPENED: the event landed in the sensor's dead time between
# exposures and can only be bounded by that gap, not placed inside a window.
SATURATED_FRACTION = 0.99
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
    uncertainty_ms: float
    roi: tuple[int, int, int, int]
    confidence: float
    lit_fraction: float
    dark_level: float
    lit_level: float
    exposure_us: float
    frame_count: int
    source: str = ""


@dataclass(frozen=True)
class StepOnset:
    """A one-sided step in a per-frame scalar, placed inside its own frame."""

    index: int
    fraction: float
    low: float
    high: float
    confidence: float

    @property
    def saturated(self) -> bool:
        """Whether the first changed frame was ALREADY fully changed."""
        return self.fraction >= SATURATED_FRACTION


@dataclass(frozen=True)
class BallDeparture:
    """When the teed ball stopped being where it was resting."""

    frame_position: float
    uncertainty_frames: float
    onset_frame_index: int
    fraction: float
    confidence: float
    ball_x: float
    ball_y: float
    ball_diameter_px: float


@dataclass(frozen=True)
class ShotTiming:
    """The trigger-to-contact walk-back, measured on ONE clock.

    Both events are read off the same camera: the ball leaving the tee is
    contact, the LED lighting is the GATE edge. No timestamp latency and no
    clock transfer enters the difference between them, which is exactly the
    number `impact_zone.contact_frame_from_trigger` assumes.
    """

    gate_minus_contact_ms: float
    uncertainty_ms: float
    airborne_flight_ms: float
    classification: str
    ball: BallDeparture
    led_frame_position: float
    led_onset_frame_index: int
    led_confidence: float
    roi: tuple[int, int, int, int]
    frame_interval_ns: float
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
    frames: np.ndarray, roi_px: int = DEFAULT_ROI_PX, *, exclude=None
) -> tuple[int, int, int, int]:
    """The box whose brightness rises most between consecutive frames.

    This is the LED: nothing else in a 2 ms exposure changes a compact patch
    of pixels by tens of counts in one frame. ``exclude`` is an
    ``(x0, y0, x1, y1)`` box held out of the search -- in shot mode the
    departing ball is a bigger rise than the LED, so it is masked first.
    """
    _, height, width = frames.shape
    size = max(1, min(int(roi_px), height, width))
    rises = np.diff(frames.astype(np.float32), axis=0)
    if exclude is not None:
        x0, y0, x1, y1 = (int(value) for value in exclude)
        rises[:, max(0, y0) : max(0, y1), max(0, x0) : max(0, x1)] = 0.0
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


def step_onset(trace, *, label: str, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> StepOnset:
    """Locate a one-sided step in a per-frame scalar, to sub-frame precision.

    Both events this script times are the same shape: a scalar that sits at a
    LOW level, rises once, and stays high. The frame where it rises is only
    PARTLY exposed to the new state, so its value sits between the two levels
    and that ratio is the fraction of the exposure that followed the event.

    ``label`` names the quantity in every refusal, so a caller can tell an
    LED that never lit from a ball that never left.
    """
    values = np.asarray(trace, dtype=np.float64)
    frame_count = values.size
    noise = _trace_noise(values)
    peak = float(np.max(values))
    midpoint = 0.5 * (float(np.min(values)) + peak)
    below = values[values < midpoint]
    low = float(np.median(below)) if below.size else float(np.median(values))
    step = peak - low
    if step / noise < min_confidence:
        raise ValueError(
            f"no_clean_{label}_step_contrast_{step / noise:.1f}_below_{min_confidence:.1f}"
        )

    # The onset frame is the FIRST frame carrying any of the change, not the
    # biggest step: when the event lands late in an exposure the step into the
    # NEXT (fully changed) frame is the larger of the two.
    threshold = low + max(3.0 * noise, ONSET_FRACTION_FLOOR * step)
    index = int(np.flatnonzero(values >= threshold)[0])
    if index < 2:
        raise ValueError(f"{label}_step_begins_at_frame_{index}_with_no_baseline_before_it")

    # The plateau is the new state across a FULL exposure, so it can only be
    # read from frames AFTER the partially exposed one.
    plateau = [i for i in range(index + 1, frame_count) if values[i] >= low + 0.9 * step]
    if not plateau:
        raise ValueError(
            f"{label}_step_lasts_only_1_frame_cannot_separate_the_partial_frame_from_the_plateau"
        )
    high = float(np.median(values[plateau]))
    return StepOnset(
        index=index,
        fraction=float(np.clip((values[index] - low) / (high - low), 0.0, 1.0)),
        low=low,
        high=high,
        confidence=(high - low) / noise,
    )


def _checked_stack(frames, host_timestamp_ns) -> tuple[np.ndarray, np.ndarray]:
    """A capture's frames and stamps, or the reason they cannot be used."""
    frames = np.asarray(frames)
    if frames.ndim != 3:
        raise ValueError("frames_must_be_a_stack_of_2d_images")
    frame_count = frames.shape[0]
    if frame_count < 3:
        raise ValueError(f"only_{frame_count}_frames_need_at_least_3_for_dark_partial_and_lit")
    stamps = np.asarray(host_timestamp_ns, dtype=np.int64)
    if stamps.shape != (frame_count,):
        raise ValueError(f"timestamp_count_{stamps.size}_does_not_match_frame_count_{frame_count}")
    if np.any(np.diff(stamps) <= 0):
        raise ValueError("host_timestamps_not_strictly_increasing")
    return frames, stamps


def _led_trace(frames: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    """Mean brightness inside the LED box, per frame."""
    x, y, box_w, box_h = box
    return frames[:, y : y + box_h, x : x + box_w].reshape(frames.shape[0], -1).mean(axis=1)


def led_onset(  # pylint: disable=too-many-arguments
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
    consecutive frames, unless ``roi`` names one; `step_onset` then places the
    onset inside the first lit frame:

        onset = host_stamp[k] - lit_fraction * exposure

    Latency is that onset minus the trigger stamp. Every capture that cannot
    carry the fit is refused by name rather than fitted anyway.
    """
    frames, stamps = _checked_stack(frames, host_timestamp_ns)
    frame_count, height, width = frames.shape
    trigger = int(trigger_host_timestamp_ns)
    if trigger <= 0:
        raise ValueError("capture_has_no_trigger_timestamp")

    box = brightest_step_roi(frames, roi_px) if roi is None else _validated_roi(roi, height, width)
    onset = step_onset(_led_trace(frames, box), label="led", min_confidence=min_confidence)
    window_ns = _exposure_ns(exposure_us, onset.index, frame_count)
    interval_ns = float(np.median(np.diff(stamps)))
    position, uncertainty = _frame_position(onset, window_ns, interval_ns)
    onset_ns = int(round(float(stamps[onset.index]) + (position - onset.index) * interval_ns))
    return LedOnset(
        onset_frame_index=onset.index,
        onset_time_ns=onset_ns,
        latency_ms=(onset_ns - trigger) / 1e6,
        uncertainty_ms=uncertainty * interval_ns / 1e6,
        roi=box,
        confidence=onset.confidence,
        lit_fraction=onset.fraction,
        dark_level=onset.low,
        lit_level=onset.high,
        exposure_us=window_ns / 1000.0,
        frame_count=frame_count,
        source=source,
    )


# ---------------------------------------------------------------------------
# shot mode: the walk-back, on one clock
# ---------------------------------------------------------------------------


def _disc_mask(shape, ball, scale: float) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """A boolean disc over the resting ball, and its bounding box."""
    height, width = shape
    radius = max(1.5, 0.5 * float(ball.diameter_px) * scale)
    x0 = max(0, int(math.floor(ball.x - radius)))
    x1 = min(width, int(math.ceil(ball.x + radius)) + 1)
    y0 = max(0, int(math.floor(ball.y - radius)))
    y1 = min(height, int(math.ceil(ball.y + radius)) + 1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise ValueError("resting_ball_disc_is_too_small_to_watch")
    ys, xs = np.ogrid[y0:y1, x0:x1]
    mask = (xs - ball.x) ** 2 + (ys - ball.y) ** 2 <= radius**2
    return mask, (x0, y0, x1, y1)


def _frame_position(
    onset: StepOnset, exposure_ns: float, frame_interval_ns: float
) -> tuple[float, float]:
    """Where a step's onset sits, in fractional frame indices, and how surely.

    A global-shutter sensor only integrates for `exposure` of each `interval`,
    so between frames there is a DEAD GAP it saw nothing in. An onset inside an
    exposure is placed by its fraction and is certain to well under a frame. An
    onset that finds its first frame ALREADY fully changed happened somewhere in
    the preceding dead gap: it can only be placed at that gap's midpoint, with
    half the gap as its uncertainty. At the rig's 800 us and 467 fps the gap is
    1.34 ms -- bigger than the effect being measured -- which is why the bench
    procedure raises the exposure towards the frame interval.
    """
    duty = min(1.0, exposure_ns / frame_interval_ns)
    if onset.saturated:
        return float(onset.index) - 0.5 * (1.0 + duty), 0.5 * (1.0 - duty)
    return float(onset.index) - onset.fraction * duty, 0.0


def ball_departure(  # pylint: disable=too-many-arguments
    frames,
    *,
    reference_frames: int,
    exposure_us,
    frame_interval_ns: float,
    ball=None,
    disc_scale: float = 1.0,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    brightness_threshold: int = 210,
) -> tuple[BallDeparture, tuple[int, int, int, int]]:
    """When the resting ball's own pixels first changed, to sub-frame precision.

    The ball is found by the shipped detector on the PRE-TRIGGER frames, where
    it is still sitting on the tee. The watched quantity is then the mean
    absolute difference between each frame and that resting median, inside the
    ball's disc: it is flat while the ball sits there, rises once as the ball
    is struck away, and stays high because the disc now shows the mat. A frame
    exposed while the ball left carries a proportional part of that rise,
    which is what places the departure inside the frame.
    """
    frames = np.asarray(frames)
    # Match `detect_reference_ball`, which medians at most its first 20
    # frames: contact PRECEDES the trigger, so the tail of the pre-trigger
    # window can already show an empty tee.
    reference_count = max(3, min(20, int(reference_frames), frames.shape[0]))
    resting = frames[:reference_count]
    if ball is None:
        try:
            ball = detect_reference_ball(resting, brightness_threshold=brightness_threshold)
        except ValueError as exc:
            raise ValueError("no_resting_ball_in_the_pre_trigger_frames") from exc

    mask, box = _disc_mask(frames.shape[1:], ball, disc_scale)
    x0, y0, x1, y1 = box
    patch = frames[:, y0:y1, x0:x1].astype(np.float64)
    reference = np.median(patch[:reference_count], axis=0)
    trace = np.abs(patch - reference)[:, mask].mean(axis=1)

    onset = step_onset(trace, label="ball", min_confidence=min_confidence)
    exposure_ns = _exposure_ns(exposure_us, onset.index, frames.shape[0])
    position, uncertainty = _frame_position(onset, exposure_ns, frame_interval_ns)
    return (
        BallDeparture(
            frame_position=position,
            uncertainty_frames=uncertainty,
            onset_frame_index=onset.index,
            fraction=onset.fraction,
            confidence=onset.confidence,
            ball_x=float(ball.x),
            ball_y=float(ball.y),
            ball_diameter_px=float(ball.diameter_px),
        ),
        box,
    )


def classify_walk_back(
    measured_ms: float, expected_ms: float, *, tolerance_ms: float = WALK_BACK_TOLERANCE_MS
) -> str:
    """Name the measured walk-back against the airborne expectation.

    Kept identical to `gate_timing_probe.classify_walk_back`, which applies it
    to the piezo-referenced measurement of the same quantity.
    """
    if measured_ms < expected_ms - tolerance_ms:
        return "early"
    if measured_ms > expected_ms + tolerance_ms:
        return "late"
    return "airborne"


def shot_timing(  # pylint: disable=too-many-arguments,too-many-locals
    frames,
    host_timestamp_ns,
    *,
    exposure_us=None,
    pre_trigger_count: int | None = None,
    roi=None,
    roi_px: int = DEFAULT_ROI_PX,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ball_to_mic_m: float = BALL_TO_UNIT_M,
    speed_of_sound_m_s: float = acoustics.SPEED_OF_SOUND_20C_M_S,
    tolerance_ms: float = WALK_BACK_TOLERANCE_MS,
    disc_scale: float = 1.0,
    brightness_threshold: int = 210,
    source: str = "",
) -> ShotTiming:
    """Measure trigger-minus-contact from a real shot with the LED in frame.

    Contact is the ball leaving the tee; the GATE edge is the LED lighting.
    Both are read off the SAME camera, so a constant stamp latency cancels
    exactly and the result is the walk-back the pipeline assumes -- 4.59 ms of
    airborne flight at the shipped distance. Frames are converted to
    milliseconds with the capture's MEDIAN frame interval rather than the two
    stamps, so one jittery stamp cannot move the answer.
    """
    frames, stamps = _checked_stack(frames, host_timestamp_ns)
    frame_count = frames.shape[0]
    interval_ns = float(np.median(np.diff(stamps)))
    reference_frames = pre_trigger_count if pre_trigger_count and pre_trigger_count > 2 else 20
    departure, disc_box = ball_departure(
        frames,
        reference_frames=reference_frames,
        exposure_us=exposure_us,
        frame_interval_ns=interval_ns,
        disc_scale=disc_scale,
        min_confidence=min_confidence,
        brightness_threshold=brightness_threshold,
    )

    height, width = frames.shape[1:]
    margin = LED_BALL_MARGIN_PX
    x0, y0, x1, y1 = disc_box
    guard = (x0 - margin, y0 - margin, x1 + margin, y1 + margin)
    box = (
        brightest_step_roi(frames, roi_px, exclude=guard)
        if roi is None
        else _validated_roi(roi, height, width)
    )
    x, y, box_w, box_h = box
    if x < guard[2] and guard[0] < x + box_w and y < guard[3] and guard[1] < y + box_h:
        raise ValueError("led_roi_overlaps_the_resting_ball_pass_an_explicit_roi")

    led = step_onset(_led_trace(frames, box), label="led", min_confidence=min_confidence)
    led_position, led_uncertainty = _frame_position(
        led, _exposure_ns(exposure_us, led.index, frame_count), interval_ns
    )
    gate_minus_contact_ms = (led_position - departure.frame_position) * interval_ns / 1e6
    flight_ms = float(ball_to_mic_m) / float(speed_of_sound_m_s) * 1000.0
    return ShotTiming(
        gate_minus_contact_ms=gate_minus_contact_ms,
        uncertainty_ms=(led_uncertainty + departure.uncertainty_frames) * interval_ns / 1e6,
        airborne_flight_ms=flight_ms,
        classification=classify_walk_back(
            gate_minus_contact_ms, flight_ms, tolerance_ms=tolerance_ms
        ),
        ball=departure,
        led_frame_position=led_position,
        led_onset_frame_index=led.index,
        led_confidence=led.confidence,
        roi=box,
        frame_interval_ns=interval_ns,
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
    """The one line the LED-latency bench test exists to print."""
    direction = "lag" if summary.median_ms >= 0 else "lead"
    return (
        f"camera stamps {direction} exposure by {abs(summary.median_ms):.2f} ms "
        f"(MAD {summary.mad_ms:.2f}, n={summary.n})"
    )


def summarise_shots(shots) -> LatencySummary:
    """Median walk-back and its MAD over a run of shots."""
    values = [float(shot.gate_minus_contact_ms) for shot in shots]
    if not values:
        raise ValueError("no_shots_to_summarise")
    median = statistics.median(values)
    return LatencySummary(
        n=len(values),
        median_ms=median,
        mad_ms=statistics.median([abs(value - median) for value in values]),
        min_ms=min(values),
        max_ms=max(values),
    )


def shot_verdict_line(
    summary: LatencySummary,
    expected_ms: float,
    *,
    tolerance_ms: float = WALK_BACK_TOLERANCE_MS,
) -> str:
    """The one line the shot-mode bench test exists to print."""
    verdict = classify_walk_back(summary.median_ms, expected_ms, tolerance_ms=tolerance_ms)
    return (
        f"gate fires {summary.median_ms:.2f} ms after contact "
        f"(MAD {summary.mad_ms:.2f}, n={summary.n}); "
        f"airborne expectation {expected_ms:.2f} ms -> {verdict}"
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


def analyse_shot_archive(archive: CaptureArchive, **kwargs) -> ShotTiming:
    """Measure the walk-back from one struck-ball capture."""
    return shot_timing(
        archive.frames,
        archive.host_timestamp_ns,
        exposure_us=archive.exposure_us,
        pre_trigger_count=archive.pre_trigger_count,
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
    parser.add_argument(
        "--shot",
        action="store_true",
        help="Archives of a STRUCK ball with the LED in frame: measure the "
        "gate-to-contact walk-back on the camera's own clock instead of the "
        "stamp latency.",
    )
    parser.add_argument(
        "--distance-m",
        type=float,
        default=BALL_TO_UNIT_M,
        help="Ball-to-microphone distance for the --shot airborne expectation.",
    )
    parser.add_argument(
        "--temperature-c",
        type=float,
        help="Ambient air temperature; omitted uses the 20 C speed of sound (343.0 m/s).",
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


def _print_table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    """Print one fixed-width row per capture."""
    widths = [max(len(header[i]), max(len(row[i]) for row in rows)) for i in range(len(header))]
    print("  ".join(name.ljust(widths[i]) for i, name in enumerate(header)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))


def _run_shot_mode(paths: list[Path], args: argparse.Namespace, roi) -> int:
    """Measure the gate-to-contact walk-back over struck-ball archives."""
    header = (
        "capture",
        "ball_fr",
        "led_fr",
        "ball_conf",
        "led_conf",
        "gate-contact_ms",
        "+-ms",
        "verdict",
    )
    rows: list[tuple[str, ...]] = []
    shots: list[ShotTiming] = []
    speed = acoustics.speed_of_sound_m_s(args.temperature_c)
    for path in paths:
        label = path.parent.name or path.name
        try:
            shot = analyse_shot_archive(
                load_archive(path),
                roi=roi,
                roi_px=args.roi_px,
                min_confidence=args.min_confidence,
                ball_to_mic_m=args.distance_m,
                speed_of_sound_m_s=speed,
            )
        except (ValueError, OSError) as exc:
            rows.append((label, "-", "-", "-", "-", "-", "-", f"REFUSED {exc}"))
            continue
        shots.append(shot)
        rows.append(
            (
                label,
                f"{shot.ball.frame_position:.2f}",
                f"{shot.led_frame_position:.2f}",
                f"{shot.ball.confidence:.1f}",
                f"{shot.led_confidence:.1f}",
                f"{shot.gate_minus_contact_ms:+.3f}",
                f"{shot.uncertainty_ms:.3f}",
                shot.classification,
            )
        )

    _print_table(header, rows)
    print()
    if not shots:
        print("No capture carried both a ball departure and an LED step.", file=sys.stderr)
        return 1
    summary = summarise_shots(shots)
    expected_ms = shots[0].airborne_flight_ms
    print(
        f"contact clock: camera only (stamp latency cancels)  "
        f"range {summary.min_ms:+.3f} .. {summary.max_ms:+.3f} ms  "
        f"at {args.distance_m:.3f} m and {speed:.1f} m/s"
    )
    print(shot_verdict_line(summary, expected_ms))
    return 0


def main(argv=None) -> int:  # pylint: disable=too-many-return-statements
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

    if args.shot:
        return _run_shot_mode(paths, args, roi)

    header = ("capture", "frame", "lit_frac", "exp_us", "conf", "latency_ms", "+-ms")
    rows: list[tuple[str, ...]] = []
    onsets: list[LedOnset] = []
    for path in paths:
        label = path.parent.name or path.name
        try:
            onset = analyse_archive(
                load_archive(path), roi=roi, roi_px=args.roi_px, min_confidence=args.min_confidence
            )
        except (ValueError, OSError) as exc:
            rows.append((label, "-", "-", "-", "-", "-", f"REFUSED {exc}"))
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
                f"{onset.uncertainty_ms:.3f}",
            )
        )

    _print_table(header, rows)
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
