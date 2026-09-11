#!/usr/bin/env python3
"""Time the SEN-14262 GATE against an INDEPENDENT reference for contact.

THE QUESTION
------------
The club pipeline walks back from the trigger to contact by the sound's
flight time from the ball (`camera.clubpose.impact_zone.contact_frame_from_trigger`,
1.575 m / 343 m/s at 20 C = 4.59 ms). The August session's ball motion says
the gate fires only 0.5-4 ms after contact, jittering +-1.5 ms. Either the
camera's stamps lag, or the gate is driven by something faster than airborne
sound -- shock through the mat, the kickstand or the enclosure.

WHAT THIS CAN AND CANNOT SETTLE
-------------------------------
The GATE is the board's own envelope comparator on the AUDIO pin. Comparing
GATE with the envelope onset of THAT SAME signal therefore cannot test the
structure-borne hypothesis at all: the two share a signal path, and the gate
cannot physically precede the waveform it is derived from. `gate_vs_audio`
measures only the COMPARATOR/THRESHOLD DELAY between our 8 x noise onset
detector and the board's own crossing -- useful for characterising the
detector (a negative median means our detector is the conservative one), and
nothing more.

Deciding the real question needs a reference for contact that is not the
microphone:

  * `camera_latency_led.py --shot` -- the ball leaving the tee, on the
    camera's own clock. One LED, no ADC. This is the primary route.
  * `gate_vs_contact` here -- a piezo/contact microphone taped under the mat
    beside the ball, recorded as the SECOND channel of the same stereo
    recording as the AUDIO pin (`--contact-channel`). Its delay to contact is
    the speed of sound in rubber over a few centimetres: negligible. Then
    `audio_onset_minus_contact_ms` should equal the airborne flight time if
    the microphone really is hearing the ball through the air, and
    `gate_minus_contact_ms` is the TRUE walk-back the pipeline needs, with
    its shot-to-shot jitter.

The Pi has no analog input: both AUDIO and the piezo need a USB sound card or
an ADC. With neither, the script still logs GATE edges and pairs them with the
camera archives' `trigger_host_timestamp_ns`, which measures the host's own
edge-to-stamp latency instead.

USAGE
-----
    SCRIPT=scripts/hardware-test/gate_timing_probe.py

    # bench run on the Pi: log edges, and record audio if a device is present
    uv run python $SCRIPT --gpio 27 --seconds 60 --out ~/bench/gate
    uv run python $SCRIPT --gpio 27 --seconds 60 --audio 1 --channels 2 --out ~/bench/gate

    # offline, stereo (mic + piezo): the true walk-back and its jitter
    uv run python $SCRIPT --edges edges.csv --wav bench.wav --contact-channel 1

    # offline, mic only: how late is the board's comparator vs our detector?
    uv run python $SCRIPT --edges edges.csv --wav mic.wav

    # offline, no ADC: how late is the host's stamp of the same edge?
    uv run python $SCRIPT --edges edges.csv --archive ~/openflight_sessions
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.io import wavfile

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from openflight import acoustics  # noqa: E402  # pylint: disable=wrong-import-position

# The shipped rig's ball-to-unit distance (impact_zone.BALL_TO_UNIT_M).
BALL_TO_UNIT_M = 1.575
# Multiples of the noise floor that count as the sound arriving.
DEFAULT_NOISE_K = 8.0
# Block length used to find the recording's quietest stretch.
DEFAULT_NOISE_WINDOW_S = 0.02
# How far either side of a GATE edge to look for its impact.
DEFAULT_WINDOW_MS = 30.0
# Below this the median comparator gap is scatter, not a direction.
DEFAULT_TOLERANCE_MS = 0.2
# The board's comparator needs some envelope to cross; more than this and it
# is not merely a threshold difference from our own detector.
COMPARATOR_EXPECTED_MAX_MS = 0.5
# How far the measured walk-back may sit from the airborne expectation before
# the shot is called early or late rather than airborne. Kept equal to
# `camera_latency_led.WALK_BACK_TOLERANCE_MS`, which classifies the same
# quantity measured through the camera.
WALK_BACK_TOLERANCE_MS = 0.3
# Largest gap that still pairs a GATE edge with a camera trigger stamp.
DEFAULT_MAX_PAIR_MS = 50.0


@dataclass(frozen=True)
class EnvelopeOnset:
    """When the microphone signal first rose out of the room noise."""

    index: int
    time_s: float
    noise_floor: float
    threshold: float
    peak: float


@dataclass(frozen=True)
class ImpactTiming:
    """One impact, seen by the comparator and by the waveform."""

    edge_index: int
    gate_time_ns: int
    onset_time_ns: int
    onset_sample: int
    gate_minus_onset_ms: float
    airborne_flight_ms: float
    implied_contact_from_gate_ns: int
    peak: float


@dataclass(frozen=True)
class GateAudioReport:
    """The board comparator's delay behind our own onset detector.

    NOT a test of what the gate fired on: GATE is derived from AUDIO, so the
    two cannot disagree about the path. `airborne_flight_ms` is carried for
    context only -- nothing here is compared against it.
    """

    impacts: tuple[ImpactTiming, ...]
    median_gate_minus_onset_ms: float
    mad_gate_minus_onset_ms: float
    airborne_flight_ms: float
    verdict: str
    unpaired_edges: tuple[int, ...]


@dataclass(frozen=True)
class ContactTiming:
    """One impact timed against a contact microphone on the mat."""

    edge_index: int
    gate_time_ns: int
    contact_time_ns: int
    audio_onset_time_ns: int
    audio_onset_minus_contact_ms: float
    gate_minus_contact_ms: float
    airborne_flight_ms: float


@dataclass(frozen=True)
class GateContactReport:
    """The TRUE trigger-to-contact walk-back, from an independent reference."""

    impacts: tuple[ContactTiming, ...]
    median_gate_minus_contact_ms: float
    mad_gate_minus_contact_ms: float
    median_audio_minus_contact_ms: float
    airborne_flight_ms: float
    classification: str
    unpaired_edges: tuple[int, ...]


@dataclass(frozen=True)
class GateHostPair:
    """One GATE edge and the host stamp of the capture it triggered."""

    edge_index: int
    gate_time_ns: int
    host_trigger_ns: int
    host_minus_gate_ms: float


@dataclass(frozen=True)
class GateHostReport:
    """How late the host stamps the edge it was interrupted by."""

    pairs: tuple[GateHostPair, ...]
    n: int
    median_ms: float
    mad_ms: float
    unpaired_triggers: tuple[int, ...]


# ---------------------------------------------------------------------------
# the waveform
# ---------------------------------------------------------------------------


def _as_mono(samples, channel: int = 0) -> np.ndarray:
    """A 1-D float view of one channel, whatever the recording was stored as."""
    data = np.asarray(samples)
    if data.ndim > 1:
        if not 0 <= channel < data.shape[1]:
            raise ValueError(f"channel_{channel}_not_in_a_{data.shape[1]}_channel_recording")
        data = data[:, channel]
    elif channel != 0:
        raise ValueError(f"channel_{channel}_requested_from_a_mono_recording")
    if np.issubdtype(data.dtype, np.integer):
        scale = float(max(abs(np.iinfo(data.dtype).min), np.iinfo(data.dtype).max))
        return data.astype(np.float64) / scale
    return data.astype(np.float64)


def noise_floor(
    samples, sample_rate_hz: float, *, window_s: float = DEFAULT_NOISE_WINDOW_S
) -> float:
    """The robust amplitude of the recording's QUIETEST block.

    Taking the quietest block rather than a fixed pre-roll keeps a recording
    that opens on an impact -- or one that is busy for most of its length --
    from inflating its own threshold.
    """
    data = _as_mono(samples)
    if data.size == 0:
        raise ValueError("empty_audio")
    if not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError(f"sample_rate_{sample_rate_hz}_must_be_positive")
    centred = data - float(np.median(data))
    block = max(16, int(window_s * sample_rate_hz))
    blocks = centred[: (centred.size // block) * block]
    if blocks.size == 0:
        return 1.4826 * float(np.median(np.abs(centred)))
    scales = 1.4826 * np.median(np.abs(blocks.reshape(-1, block)), axis=1)
    return float(np.min(scales))


def envelope_onset(  # pylint: disable=too-many-arguments
    samples,
    sample_rate_hz: float,
    *,
    noise_k: float = DEFAULT_NOISE_K,
    floor: float | None = None,
    noise_window_s: float = DEFAULT_NOISE_WINDOW_S,
    search_from_s: float = 0.0,
    search_to_s: float | None = None,
) -> EnvelopeOnset:
    """The first sample whose envelope clears ``noise_k`` x the noise floor.

    ``floor`` overrides the measured noise floor, which is what the per-impact
    search does: the floor is measured ONCE over the whole recording so every
    impact is judged against the same threshold.
    """
    data = _as_mono(samples)
    if data.size == 0:
        raise ValueError("empty_audio")
    if not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError(f"sample_rate_{sample_rate_hz}_must_be_positive")
    envelope = np.abs(data - float(np.median(data)))
    peak = float(np.max(envelope))
    level = noise_floor(data, sample_rate_hz, window_s=noise_window_s) if floor is None else floor
    # A digitally silent lead-in would otherwise make every sample a crossing.
    level = max(float(level), 1e-6 * peak, 1e-12)
    threshold = noise_k * level

    start = max(0, int(math.floor(search_from_s * sample_rate_hz)))
    stop = (
        data.size
        if search_to_s is None
        else min(data.size, int(math.ceil(search_to_s * sample_rate_hz)))
    )
    if start >= stop:
        raise ValueError(f"search_window_{search_from_s:.3f}_to_{search_to_s}_s_is_empty")

    window = envelope[start:stop]
    above = np.flatnonzero(window > threshold)
    if above.size == 0:
        raise ValueError(
            f"no_sample_above_threshold_{noise_k:.1f}x_noise_floor_in_the_search_window"
        )
    index = start + int(above[0])
    return EnvelopeOnset(
        index=index,
        time_s=index / float(sample_rate_hz),
        noise_floor=level,
        threshold=threshold,
        peak=float(np.max(window)),
    )


# ---------------------------------------------------------------------------
# gate versus the acoustic onset
# ---------------------------------------------------------------------------


def _median_and_mad(values) -> tuple[float, float]:
    """Median and median absolute deviation of a sequence."""
    median = statistics.median(values)
    return median, statistics.median([abs(value - median) for value in values])


def gate_vs_audio(  # pylint: disable=too-many-arguments,too-many-locals
    gate_edges_ns,
    samples,
    sample_rate_hz: float,
    *,
    audio_start_ns: int,
    ball_to_mic_m: float = BALL_TO_UNIT_M,
    speed_of_sound_m_s: float = acoustics.SPEED_OF_SOUND_20C_M_S,
    window_ms: float = DEFAULT_WINDOW_MS,
    noise_k: float = DEFAULT_NOISE_K,
    noise_window_s: float = DEFAULT_NOISE_WINDOW_S,
    tolerance_ms: float = DEFAULT_TOLERANCE_MS,
) -> GateAudioReport:
    """Measure the board comparator's delay behind our own onset detector.

    GATE is the board's envelope comparator on the AUDIO pin, so this compares
    two detectors reading ONE signal: it characterises the threshold, and
    cannot say what the gate fired on. Expect 0 to +0.5 ms; a negative median
    means our 8 x noise crossing is the later of the two. Use `gate_vs_contact`
    (piezo) or `camera_latency_led.py --shot` for the walk-back itself.

    Both clocks are the host's monotonic clock: ``audio_start_ns`` is the
    stamp taken as the recording started, so sample *i* happened at
    ``audio_start_ns + i / rate``. ``speed_of_sound_m_s`` defaults to the
    codebase's 20 C constant; `acoustics.speed_of_sound_m_s(temperature_c)`
    supplies a measured one, and only feeds the context figure.
    """
    edges = np.asarray(gate_edges_ns, dtype=np.int64).reshape(-1)
    if edges.size == 0:
        raise ValueError("no_gate_edges_to_time")
    data = _as_mono(samples)
    if data.size == 0:
        raise ValueError("empty_audio")
    if ball_to_mic_m <= 0 or speed_of_sound_m_s <= 0:
        raise ValueError("distance_and_speed_of_sound_must_be_positive")

    flight_ms = ball_to_mic_m / speed_of_sound_m_s * 1000.0
    level = noise_floor(data, sample_rate_hz, window_s=noise_window_s)
    half_window_s = window_ms / 1000.0

    impacts: list[ImpactTiming] = []
    unpaired: list[int] = []
    for edge_index, edge in enumerate(edges):
        edge_s = (int(edge) - int(audio_start_ns)) / 1e9
        try:
            onset = envelope_onset(
                data,
                sample_rate_hz,
                noise_k=noise_k,
                floor=level,
                search_from_s=edge_s - half_window_s,
                search_to_s=edge_s + half_window_s,
            )
        except ValueError:
            unpaired.append(edge_index)
            continue
        onset_ns = int(audio_start_ns) + int(round(onset.time_s * 1e9))
        impacts.append(
            ImpactTiming(
                edge_index=edge_index,
                gate_time_ns=int(edge),
                onset_time_ns=onset_ns,
                onset_sample=onset.index,
                gate_minus_onset_ms=(int(edge) - onset_ns) / 1e6,
                airborne_flight_ms=flight_ms,
                implied_contact_from_gate_ns=int(edge) - int(round(flight_ms * 1e6)),
                peak=onset.peak,
            )
        )

    if not impacts:
        raise ValueError("no_gate_edge_paired_with_an_acoustic_onset")

    median, mad = _median_and_mad([impact.gate_minus_onset_ms for impact in impacts])
    return GateAudioReport(
        impacts=tuple(impacts),
        median_gate_minus_onset_ms=median,
        mad_gate_minus_onset_ms=mad,
        airborne_flight_ms=flight_ms,
        verdict=classify_comparator(median, tolerance_ms=max(tolerance_ms, mad)),
        unpaired_edges=tuple(unpaired),
    )


def classify_comparator(
    median_gate_minus_onset_ms: float, *, tolerance_ms: float = DEFAULT_TOLERANCE_MS
) -> str:
    """Name the gap between the board's comparator and our onset detector."""
    if median_gate_minus_onset_ms < -tolerance_ms:
        return "detector_more_conservative_than_the_board"
    if median_gate_minus_onset_ms > COMPARATOR_EXPECTED_MAX_MS:
        return "comparator_slower_than_expected"
    return "comparator_delay"


def classify_walk_back(
    measured_ms: float, expected_ms: float, *, tolerance_ms: float = WALK_BACK_TOLERANCE_MS
) -> str:
    """Name the measured walk-back against the airborne expectation.

    Kept identical to `camera_latency_led.classify_walk_back`, which applies
    it to the camera-referenced measurement of the same quantity.
    """
    if measured_ms < expected_ms - tolerance_ms:
        return "early"
    if measured_ms > expected_ms + tolerance_ms:
        return "late"
    return "airborne"


def gate_vs_contact(  # pylint: disable=too-many-arguments,too-many-locals
    gate_edges_ns,
    samples,
    sample_rate_hz: float,
    *,
    audio_start_ns: int,
    contact_channel: int = 1,
    audio_channel: int = 0,
    ball_to_mic_m: float = BALL_TO_UNIT_M,
    speed_of_sound_m_s: float = acoustics.SPEED_OF_SOUND_20C_M_S,
    window_ms: float = DEFAULT_WINDOW_MS,
    noise_k: float = DEFAULT_NOISE_K,
    noise_window_s: float = DEFAULT_NOISE_WINDOW_S,
    tolerance_ms: float = WALK_BACK_TOLERANCE_MS,
) -> GateContactReport:
    """Time GATE and the microphone against a contact mic on the mat.

    The piezo is centimetres from the ball through rubber, so its onset IS
    contact for this purpose. Two numbers follow per impact: how long the
    airborne sound took to reach the microphone
    (``audio_onset_minus_contact_ms``, which should equal the airborne
    expectation if the microphone is hearing the ball through the air at all),
    and the walk-back the pipeline actually needs
    (``gate_minus_contact_ms``), classified against that expectation.
    """
    edges = np.asarray(gate_edges_ns, dtype=np.int64).reshape(-1)
    if edges.size == 0:
        raise ValueError("no_gate_edges_to_time")
    contact_data = _as_mono(samples, contact_channel)
    audio_data = _as_mono(samples, audio_channel)
    if contact_data.size == 0:
        raise ValueError("empty_audio")
    if ball_to_mic_m <= 0 or speed_of_sound_m_s <= 0:
        raise ValueError("distance_and_speed_of_sound_must_be_positive")

    flight_ms = ball_to_mic_m / speed_of_sound_m_s * 1000.0
    contact_floor = noise_floor(contact_data, sample_rate_hz, window_s=noise_window_s)
    audio_floor = noise_floor(audio_data, sample_rate_hz, window_s=noise_window_s)
    half_window_s = window_ms / 1000.0

    impacts: list[ContactTiming] = []
    unpaired: list[int] = []
    for edge_index, edge in enumerate(edges):
        edge_s = (int(edge) - int(audio_start_ns)) / 1e9
        try:
            contact = envelope_onset(
                contact_data,
                sample_rate_hz,
                noise_k=noise_k,
                floor=contact_floor,
                search_from_s=edge_s - half_window_s,
                search_to_s=edge_s + half_window_s,
            )
            heard = envelope_onset(
                audio_data,
                sample_rate_hz,
                noise_k=noise_k,
                floor=audio_floor,
                search_from_s=edge_s - half_window_s,
                search_to_s=edge_s + half_window_s,
            )
        except ValueError:
            unpaired.append(edge_index)
            continue
        contact_ns = int(audio_start_ns) + int(round(contact.time_s * 1e9))
        heard_ns = int(audio_start_ns) + int(round(heard.time_s * 1e9))
        impacts.append(
            ContactTiming(
                edge_index=edge_index,
                gate_time_ns=int(edge),
                contact_time_ns=contact_ns,
                audio_onset_time_ns=heard_ns,
                audio_onset_minus_contact_ms=(heard_ns - contact_ns) / 1e6,
                gate_minus_contact_ms=(int(edge) - contact_ns) / 1e6,
                airborne_flight_ms=flight_ms,
            )
        )

    if not impacts:
        raise ValueError("no_gate_edge_paired_with_a_contact_onset")

    median, mad = _median_and_mad([impact.gate_minus_contact_ms for impact in impacts])
    heard_median, _ = _median_and_mad([impact.audio_onset_minus_contact_ms for impact in impacts])
    return GateContactReport(
        impacts=tuple(impacts),
        median_gate_minus_contact_ms=median,
        mad_gate_minus_contact_ms=mad,
        median_audio_minus_contact_ms=heard_median,
        airborne_flight_ms=flight_ms,
        classification=classify_walk_back(median, flight_ms, tolerance_ms=tolerance_ms),
        unpaired_edges=tuple(unpaired),
    )


def gate_vs_host_stamps(
    gate_edges_ns,
    trigger_host_timestamps_ns,
    *,
    max_pair_ms: float = DEFAULT_MAX_PAIR_MS,
) -> GateHostReport:
    """Pair GATE edges with the capture stamps they caused.

    The fallback when there is no ADC: it cannot say what the gate fired on,
    only how much of the observed offset is the host's own edge-to-stamp
    latency rather than acoustics.
    """
    edges = np.asarray(gate_edges_ns, dtype=np.int64).reshape(-1)
    triggers = np.asarray(trigger_host_timestamps_ns, dtype=np.int64).reshape(-1)
    if edges.size == 0:
        raise ValueError("no_gate_edges_to_pair")
    if triggers.size == 0:
        raise ValueError("no_trigger_timestamps_to_pair")

    limit_ns = max_pair_ms * 1e6
    pairs: list[GateHostPair] = []
    unpaired: list[int] = []
    for trigger_index, trigger in enumerate(triggers):
        gaps = np.abs(edges - int(trigger))
        nearest = int(np.argmin(gaps))
        if gaps[nearest] > limit_ns:
            unpaired.append(trigger_index)
            continue
        pairs.append(
            GateHostPair(
                edge_index=nearest,
                gate_time_ns=int(edges[nearest]),
                host_trigger_ns=int(trigger),
                host_minus_gate_ms=(int(trigger) - int(edges[nearest])) / 1e6,
            )
        )
    if not pairs:
        raise ValueError("no_trigger_paired_with_a_gate_edge")

    median, mad = _median_and_mad([pair.host_minus_gate_ms for pair in pairs])
    return GateHostReport(
        pairs=tuple(pairs),
        n=len(pairs),
        median_ms=median,
        mad_ms=mad,
        unpaired_triggers=tuple(unpaired),
    )


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------


def write_edges_csv(path, edges_ns) -> Path:
    """Persist GATE edge stamps for later analysis."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["edge", "monotonic_ns"])
        for index, edge in enumerate(np.asarray(edges_ns, dtype=np.int64).reshape(-1)):
            writer.writerow([index, int(edge)])
    return path


def read_edges_csv(path) -> np.ndarray:
    """Read GATE edge stamps written by ``--gpio``."""
    path = Path(path).expanduser()
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    edges = [int(row["monotonic_ns"]) for row in rows if row.get("monotonic_ns")]
    if not edges:
        raise ValueError(f"no_gate_edges_in_{path.name}")
    return np.asarray(sorted(edges), dtype=np.int64)


def read_wav(path) -> tuple[int, np.ndarray]:
    """Read a recording, keeping its channels: the piezo lives in channel 1."""
    rate, data = wavfile.read(Path(path).expanduser())
    return int(rate), np.asarray(data)


def read_trigger_stamps(paths) -> np.ndarray:
    """Every capture's `trigger_host_timestamp_ns`, under files or directories."""
    found: list[Path] = []
    for entry in paths:
        path = Path(entry).expanduser()
        if path.is_file():
            found.append(path)
        elif path.is_dir():
            found.extend(sorted(path.rglob("frames.npz")))
    stamps: list[int] = []
    for archive in found:
        with np.load(archive) as data:
            if "trigger_host_timestamp_ns" in data:
                stamps.append(int(data["trigger_host_timestamp_ns"]))
    if not stamps:
        raise ValueError("no_trigger_timestamps_in_the_given_archives")
    return np.asarray(sorted(stamps), dtype=np.int64)


# ---------------------------------------------------------------------------
# hardware (nothing below runs on a dev box)
# ---------------------------------------------------------------------------


def log_gate_edges(pin: int, *, seconds: float, csv_path: Path, edges: int = 0) -> np.ndarray:
    """Record GATE rising edges on a Pi GPIO with monotonic stamps.

    gpiozero is imported here, not at module scope, so the analysis above
    stays importable on a machine with no GPIO.
    """
    import time  # pylint: disable=import-outside-toplevel

    from gpiozero import Button  # pylint: disable=import-error,import-outside-toplevel

    from openflight.gpio_factory import (  # pylint: disable=import-outside-toplevel
        ensure_lgpio_pin_factory,
    )

    ensure_lgpio_pin_factory()
    stamps: list[int] = []
    button = Button(pin, pull_up=False, bounce_time=None)
    button.when_pressed = lambda: stamps.append(time.monotonic_ns())
    print(f"Logging GATE edges on BCM{pin} for {seconds:.0f}s. Hit the ball or clap.")
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and (edges <= 0 or len(stamps) < edges):
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        button.close()
    print(f"{len(stamps)} edge(s) -> {csv_path}")
    write_edges_csv(csv_path, stamps)
    return np.asarray(stamps, dtype=np.int64)


def start_audio_recording(device, *, seconds: float, sample_rate: int, channels: int = 1):
    """Begin a non-blocking recording of the mic's AUDIO pin (and the piezo).

    Needs a USB sound card or an ADC: the Pi has no analog input. Record two
    channels to capture the mat's contact microphone alongside AUDIO; they
    then share one clock and one file. Returns the monotonic stamp the
    recording started at and a callable that waits for it and hands back the
    samples with their channels intact.
    """
    import time  # pylint: disable=import-outside-toplevel

    import sounddevice  # pylint: disable=import-error,import-outside-toplevel

    frames = int(seconds * sample_rate)
    recording = sounddevice.rec(
        frames, samplerate=sample_rate, channels=channels, dtype="float32", device=device
    )
    started_ns = time.monotonic_ns()

    def finish() -> np.ndarray:
        sounddevice.wait()
        return np.asarray(recording)

    return started_ns, finish


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--gpio", type=int, help="Raspberry Pi only: log GATE edges on this BCM pin."
    )
    parser.add_argument("--seconds", type=float, default=60.0, help="How long to log/record.")
    parser.add_argument(
        "--max-edges", type=int, default=0, help="Stop after N edges (0: no limit)."
    )
    parser.add_argument("--audio", help="sounddevice input device for the mic's AUDIO pin.")
    parser.add_argument("--sample-rate", type=int, default=48_000)
    parser.add_argument(
        "--out", type=Path, default=Path("."), help="Where --gpio writes its files."
    )
    parser.add_argument("--edges", type=Path, help="Edge CSV to analyse.")
    parser.add_argument("--wav", type=Path, help="Recording of the mic's AUDIO pin.")
    parser.add_argument(
        "--contact-channel",
        type=int,
        help="Channel of --wav carrying a piezo/contact mic on the mat. With it, "
        "the probe measures the TRUE walk-back instead of the comparator delay.",
    )
    parser.add_argument(
        "--audio-channel", type=int, default=0, help="Channel of --wav carrying the AUDIO pin."
    )
    parser.add_argument(
        "--channels", type=int, default=1, help="Channels to record with --audio (2 with a piezo)."
    )
    parser.add_argument(
        "--audio-start-ns",
        type=int,
        help="Monotonic stamp the recording started at; read from <wav>.json if omitted.",
    )
    parser.add_argument(
        "--archive",
        action="append",
        default=[],
        metavar="PATH",
        help="Camera capture(s) to pair edges with when there is no audio. Repeatable.",
    )
    parser.add_argument(
        "--distance-m",
        type=float,
        default=BALL_TO_UNIT_M,
        help="Ball-to-microphone distance used for the airborne expectation.",
    )
    parser.add_argument(
        "--temperature-c",
        type=float,
        help="Ambient air temperature; omitted uses the 20 C speed of sound (343.0 m/s).",
    )
    parser.add_argument("--noise-k", type=float, default=DEFAULT_NOISE_K)
    parser.add_argument("--window-ms", type=float, default=DEFAULT_WINDOW_MS)
    parser.add_argument("--max-pair-ms", type=float, default=DEFAULT_MAX_PAIR_MS)
    return parser.parse_args(argv)


def _audio_start_ns(args: argparse.Namespace) -> int:
    """The recording's start stamp, from the flag or the wav's sidecar."""
    if args.audio_start_ns is not None:
        return int(args.audio_start_ns)
    sidecar = args.wav.with_suffix(args.wav.suffix + ".json")
    if sidecar.exists():
        return int(json.loads(sidecar.read_text(encoding="utf-8"))["audio_start_ns"])
    raise ValueError(f"audio_start_ns_unknown_pass_the_flag_or_write_{sidecar.name}")


def _run_hardware(args: argparse.Namespace) -> tuple[Path, Path | None, int | None]:
    """Log edges (and optionally audio) into --out; return what was written."""
    args.out.mkdir(parents=True, exist_ok=True)
    csv_path = args.out / "edges.csv"
    wav_path: Path | None = None
    started_ns: int | None = None
    finish = None
    if args.audio is not None:
        started_ns, finish = start_audio_recording(
            args.audio,
            seconds=args.seconds,
            sample_rate=args.sample_rate,
            channels=args.channels,
        )
    log_gate_edges(args.gpio, seconds=args.seconds, csv_path=csv_path, edges=args.max_edges)
    if finish is not None:
        wav_path = args.out / ("bench.wav" if args.channels > 1 else "mic.wav")
        wavfile.write(wav_path, args.sample_rate, finish().astype(np.float32))
        wav_path.with_suffix(wav_path.suffix + ".json").write_text(
            json.dumps({"audio_start_ns": started_ns, "sample_rate": args.sample_rate}) + "\n",
            encoding="utf-8",
        )
    return csv_path, wav_path, started_ns


def _report_contact(args: argparse.Namespace, edges: np.ndarray) -> int:
    """Print the piezo-referenced walk-back table and verdict."""
    rate, samples = read_wav(args.wav)
    speed = acoustics.speed_of_sound_m_s(args.temperature_c)
    report = gate_vs_contact(
        edges,
        samples,
        rate,
        audio_start_ns=_audio_start_ns(args),
        contact_channel=args.contact_channel,
        audio_channel=args.audio_channel,
        ball_to_mic_m=args.distance_m,
        speed_of_sound_m_s=speed,
        window_ms=args.window_ms,
        noise_k=args.noise_k,
    )
    print(f"{'edge':>4}  {'heard-contact_ms':>17}  {'gate-contact_ms':>16}")
    print(f"{'-' * 4}  {'-' * 17}  {'-' * 16}")
    for impact in report.impacts:
        print(
            f"{impact.edge_index:>4}  "
            f"{impact.audio_onset_minus_contact_ms:>+17.2f}  "
            f"{impact.gate_minus_contact_ms:>+16.2f}"
        )
    if report.unpaired_edges:
        print(f"\nunpaired edges (no impact in the search window): {report.unpaired_edges}")
    print(
        f"\nairborne expectation for {args.distance_m:.3f} m at {speed:.1f} m/s: "
        f"{report.airborne_flight_ms:.2f} ms of flight; the microphone heard the "
        f"impact {report.median_audio_minus_contact_ms:.2f} ms after contact"
    )
    print(
        f"GATE fires {report.median_gate_minus_contact_ms:.2f} ms after contact "
        f"(MAD {report.mad_gate_minus_contact_ms:.2f}, n={len(report.impacts)}) "
        f"-> {report.classification}"
    )
    if report.classification == "early":
        print("The trigger beats airborne sound: it is not arriving through the air.")
    return 0


def _report_audio(args: argparse.Namespace, edges: np.ndarray) -> int:
    """Print the comparator-delay table and verdict."""
    rate, samples = read_wav(args.wav)
    report = gate_vs_audio(
        edges,
        _as_mono(samples, args.audio_channel),
        rate,
        audio_start_ns=_audio_start_ns(args),
        ball_to_mic_m=args.distance_m,
        speed_of_sound_m_s=acoustics.speed_of_sound_m_s(args.temperature_c),
        window_ms=args.window_ms,
        noise_k=args.noise_k,
    )
    print(f"{'edge':>4}  {'onset_s':>10}  {'gate_s':>10}  {'gate-onset_ms':>14}")
    print(f"{'-' * 4}  {'-' * 10}  {'-' * 10}  {'-' * 14}")
    base = _audio_start_ns(args)
    for impact in report.impacts:
        print(
            f"{impact.edge_index:>4}  "
            f"{(impact.onset_time_ns - base) / 1e9:>10.4f}  "
            f"{(impact.gate_time_ns - base) / 1e9:>10.4f}  "
            f"{impact.gate_minus_onset_ms:>+14.2f}"
        )
    if report.unpaired_edges:
        print(f"\nunpaired edges (no impact in the search window): {report.unpaired_edges}")
    print(
        f"\nairborne expectation for {args.distance_m:.3f} m at "
        f"{acoustics.speed_of_sound_m_s(args.temperature_c):.1f} m/s: "
        f"{report.airborne_flight_ms:.2f} ms of flight"
    )
    direction = "leads" if report.median_gate_minus_onset_ms < 0 else "lags"
    print(
        f"GATE {direction} our envelope onset by "
        f"{abs(report.median_gate_minus_onset_ms):.2f} ms "
        f"(MAD {report.mad_gate_minus_onset_ms:.2f}, n={len(report.impacts)}) -> {report.verdict}"
    )
    print(
        "This is the comparator threshold, NOT what the gate fired on: GATE is "
        "derived from AUDIO. Use --contact-channel, or camera_latency_led.py --shot."
    )
    return 0


def _report_archives(args: argparse.Namespace, edges: np.ndarray) -> int:
    """Print the gate-to-host-stamp table and verdict."""
    report = gate_vs_host_stamps(
        edges, read_trigger_stamps(args.archive), max_pair_ms=args.max_pair_ms
    )
    print(f"{'edge':>4}  {'host-gate_ms':>13}")
    print(f"{'-' * 4}  {'-' * 13}")
    for pair in report.pairs:
        print(f"{pair.edge_index:>4}  {pair.host_minus_gate_ms:>+13.2f}")
    if report.unpaired_triggers:
        print(f"\nunpaired triggers: {report.unpaired_triggers}")
    print(
        f"\nhost trigger stamp lags the GATE edge by {report.median_ms:.2f} ms "
        f"(MAD {report.mad_ms:.2f}, n={report.n})"
    )
    print("No audio: this is the host's edge-to-stamp latency, not what the gate fired on.")
    return 0


def main(argv=None) -> int:  # pylint: disable=too-many-return-statements
    """Log or analyse GATE timing and print the verdict."""
    args = parse_args(argv)
    if args.gpio is not None:
        try:
            csv_path, wav_path, _ = _run_hardware(args)
        except (ImportError, OSError, RuntimeError) as exc:
            print(f"Hardware logging unavailable: {exc}", file=sys.stderr)
            return 2
        args.edges = csv_path
        args.wav = args.wav or wav_path

    if args.edges is None:
        print("Nothing to analyse: pass --edges CSV (or --gpio PIN to record).", file=sys.stderr)
        return 2
    try:
        edges = read_edges_csv(args.edges)
        if args.wav is not None and args.contact_channel is not None:
            return _report_contact(args, edges)
        if args.wav is not None:
            return _report_audio(args, edges)
        if args.archive:
            return _report_archives(args, edges)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        "Edges alone measure nothing: add --wav (mic AUDIO via USB sound card or ADC) "
        "or --archive (camera captures).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
