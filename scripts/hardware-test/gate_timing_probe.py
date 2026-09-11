#!/usr/bin/env python3
"""Decide what the SEN-14262 GATE actually fires on: air, or something faster.

THE QUESTION
------------
The club pipeline walks back from the trigger to contact by the sound's
flight time from the ball (`camera.clubpose.impact_zone.contact_frame_from_trigger`,
1.575 m / 343 m/s at 20 C = 4.59 ms). The August session's ball motion says
the gate
fires only 0.5-4 ms after contact, jittering +-1.5 ms. If the gate heard the
impact through the air it cannot beat 4.59 ms, so either the camera's stamps
lag (see `camera_latency_led.py`) or the gate is being driven by a path that
is not airborne -- shock through the mat, the kickstand or the enclosure --
or by an envelope crossing that happens earlier than the audible arrival.

THE MEASUREMENT
---------------
Record the microphone's own AUDIO pin while logging GATE edges on a Pi GPIO,
both on the monotonic clock. For each impact compare:

  * the envelope onset  -- the first sample above k x the noise floor: when
    the sound actually reached the microphone;
  * the GATE edge       -- when the comparator fired;
  * the airborne expectation -- distance / the speed of sound, the flight
    time the pipeline currently assumes. `--temperature-c` moves that speed
    off the 20 C default (`openflight.acoustics`); over 0-35 C it is a
    3 percent, +-0.13 ms effect at this distance.

A GATE that consistently LEADS the acoustic onset did not come through the
air. A GATE that lags it by a small, repeatable amount is an ordinary
comparator delay, and the timing error lives elsewhere.

The Pi has no analog input: the AUDIO pin needs a USB sound card or an ADC.
With neither, the script still logs GATE edges and pairs them with the camera
archives' `trigger_host_timestamp_ns`, which measures the host's own
edge-to-stamp latency instead.

USAGE
-----
    SCRIPT=scripts/hardware-test/gate_timing_probe.py

    # bench run on the Pi: log edges, and record audio if a device is present
    uv run python $SCRIPT --gpio 27 --seconds 60 --out ~/bench/gate
    uv run python $SCRIPT --gpio 27 --seconds 60 --audio 1 --out ~/bench/gate

    # offline: what did the gate fire on?
    uv run python $SCRIPT --edges edges.csv --wav mic.wav --distance-m 1.575

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
# Below this the median gap is scatter, not a direction.
DEFAULT_TOLERANCE_MS = 0.2
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
    """What the gate fired on, over a run of impacts."""

    impacts: tuple[ImpactTiming, ...]
    median_gate_minus_onset_ms: float
    mad_gate_minus_onset_ms: float
    airborne_flight_ms: float
    path: str
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


def _as_mono(samples) -> np.ndarray:
    """A 1-D float view of a recording, whatever it was stored as."""
    data = np.asarray(samples)
    if data.ndim > 1:
        data = data[:, 0]
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
    """Time every GATE edge against the acoustic onset of its own impact.

    Both clocks are the host's monotonic clock: ``audio_start_ns`` is the
    stamp taken as the recording started, so sample *i* happened at
    ``audio_start_ns + i / rate``. ``speed_of_sound_m_s`` defaults to the
    codebase's 20 C constant; `acoustics.speed_of_sound_m_s(temperature_c)`
    supplies a measured one.
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
    limit = max(tolerance_ms, mad)
    if median < -limit:
        path = "faster_than_air"
    elif median > limit:
        path = "airborne"
    else:
        path = "inconclusive"
    return GateAudioReport(
        impacts=tuple(impacts),
        median_gate_minus_onset_ms=median,
        mad_gate_minus_onset_ms=mad,
        airborne_flight_ms=flight_ms,
        path=path,
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
    """Read a mono or multi-channel recording as float samples."""
    rate, data = wavfile.read(Path(path).expanduser())
    return int(rate), _as_mono(data)


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


def start_audio_recording(device, *, seconds: float, sample_rate: int):
    """Begin a non-blocking recording of the microphone's AUDIO pin.

    Needs a USB sound card or an ADC: the Pi has no analog input. Returns the
    monotonic stamp the recording started at and a callable that waits for it
    to finish and hands back the samples.
    """
    import time  # pylint: disable=import-outside-toplevel

    import sounddevice  # pylint: disable=import-error,import-outside-toplevel

    frames = int(seconds * sample_rate)
    recording = sounddevice.rec(
        frames, samplerate=sample_rate, channels=1, dtype="float32", device=device
    )
    started_ns = time.monotonic_ns()

    def finish() -> np.ndarray:
        sounddevice.wait()
        return _as_mono(recording)

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
            args.audio, seconds=args.seconds, sample_rate=args.sample_rate
        )
    log_gate_edges(args.gpio, seconds=args.seconds, csv_path=csv_path, edges=args.max_edges)
    if finish is not None:
        wav_path = args.out / "mic.wav"
        wavfile.write(wav_path, args.sample_rate, finish().astype(np.float32))
        wav_path.with_suffix(wav_path.suffix + ".json").write_text(
            json.dumps({"audio_start_ns": started_ns, "sample_rate": args.sample_rate}) + "\n",
            encoding="utf-8",
        )
    return csv_path, wav_path, started_ns


def _report_audio(args: argparse.Namespace, edges: np.ndarray) -> int:
    """Print the gate-versus-onset table and verdict."""
    rate, samples = read_wav(args.wav)
    report = gate_vs_audio(
        edges,
        samples,
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
        f"GATE {direction} the acoustic onset by "
        f"{abs(report.median_gate_minus_onset_ms):.2f} ms "
        f"(MAD {report.mad_gate_minus_onset_ms:.2f}, n={len(report.impacts)}) -> {report.path}"
    )
    if report.path == "faster_than_air":
        print("The trigger is NOT arriving through the air: the acoustic walk-back is wrong.")
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


def main(argv=None) -> int:
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
