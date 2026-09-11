"""GATE timing, proven on waveforms whose instants we chose.

Two different measurements live here and the tests keep them apart. Against
the microphone's own AUDIO pin the probe can only measure the board
comparator's threshold delay -- GATE is derived from that signal, so the
comparison says nothing about which path the impact took. Against a piezo on
the mat it measures the real walk-back from contact and classifies it against
the airborne expectation. Both refuse, by name, the inputs that carry no
answer; the camera fallback (no ADC on the bench) is held to the same
standard.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hardware-test" / "gate_timing_probe.py"
_SPEC = importlib.util.spec_from_file_location("gate_timing_probe", _SCRIPT)
probe = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = probe
_SPEC.loader.exec_module(probe)

RATE = 48_000
DURATION_S = 1.0
NOISE = 0.002
BURST_S = 0.004
AUDIO_START_NS = 1_000_000_000
BALL_TO_MIC_M = 1.575
AIRBORNE_MS = BALL_TO_MIC_M / 343.0 * 1000.0


def _waveform(onsets_s, *, amplitude: float = 0.4, seed: int = 3) -> np.ndarray:
    """Room noise with an abrupt, decaying burst at each onset."""
    rng = np.random.default_rng(seed)
    samples = rng.normal(0.0, NOISE, int(RATE * DURATION_S))
    burst_len = int(BURST_S * RATE)
    envelope = np.exp(-np.arange(burst_len) / (0.25 * burst_len))
    tone = np.sin(2 * np.pi * 2500.0 * np.arange(burst_len) / RATE)
    for onset in onsets_s:
        start = int(round(onset * RATE))
        samples[start : start + burst_len] += amplitude * envelope * tone
    return samples


def _edges_ns(times_s) -> np.ndarray:
    """GATE edge stamps on the same monotonic clock as the recording."""
    return np.asarray([AUDIO_START_NS + int(round(time * 1e9)) for time in times_s], dtype=np.int64)


# ---------------------------------------------------------------------------
# the envelope onset
# ---------------------------------------------------------------------------


def test_envelope_onset_recovers_a_known_instant() -> None:
    """The first crossing lands within a fraction of a millisecond."""
    samples = _waveform([0.4])
    onset = probe.envelope_onset(samples, RATE)
    assert onset.time_s == pytest.approx(0.4, abs=0.0005)
    assert onset.threshold > onset.noise_floor
    assert onset.peak > onset.threshold


def test_envelope_onset_honours_the_search_window() -> None:
    """A window selects which impact is measured."""
    samples = _waveform([0.2, 0.6])
    second = probe.envelope_onset(samples, RATE, search_from_s=0.5, search_to_s=0.7)
    assert second.time_s == pytest.approx(0.6, abs=0.0005)


def test_envelope_onset_refuses_empty_audio() -> None:
    """No samples, no onset."""
    with pytest.raises(ValueError, match="empty_audio"):
        probe.envelope_onset(np.zeros(0), RATE)


def test_envelope_onset_refuses_a_bad_sample_rate() -> None:
    """A rate of zero makes every time meaningless."""
    with pytest.raises(ValueError, match="sample_rate"):
        probe.envelope_onset(_waveform([0.4]), 0)


def test_envelope_onset_refuses_silence() -> None:
    """Room noise alone never crosses the threshold."""
    with pytest.raises(ValueError, match="no_sample_above_threshold"):
        probe.envelope_onset(_waveform([]), RATE)


def test_noise_floor_uses_the_quietest_block() -> None:
    """A recording that is loud for most of its length still floors low."""
    samples = _waveform([0.05, 0.1, 0.15, 0.2, 0.25], amplitude=1.0)
    floor = probe.noise_floor(samples, RATE)
    assert floor == pytest.approx(NOISE, rel=0.6)


# ---------------------------------------------------------------------------
# gate versus the acoustic onset
# ---------------------------------------------------------------------------


def test_gate_after_our_onset_is_an_ordinary_comparator_delay() -> None:
    """The board's comparator needs some envelope before it fires."""
    onsets = [0.2, 0.45, 0.7]
    report = probe.gate_vs_audio(
        _edges_ns([value + 0.0003 for value in onsets]),
        _waveform(onsets),
        RATE,
        audio_start_ns=AUDIO_START_NS,
        ball_to_mic_m=BALL_TO_MIC_M,
    )
    assert report.median_gate_minus_onset_ms == pytest.approx(0.3, abs=0.1)
    assert report.verdict == "comparator_delay"
    assert len(report.impacts) == 3


def test_gate_before_our_onset_means_our_detector_is_the_conservative_one() -> None:
    """GATE cannot precede the signal it is derived from, so this is us.

    A negative gap says our 8 x noise crossing is later than the board's, NOT
    that the impact arrived by some faster path: that question needs a
    reference which is not the microphone.
    """
    onsets = [0.2, 0.45, 0.7]
    report = probe.gate_vs_audio(
        _edges_ns([value - 0.0012 for value in onsets]),
        _waveform(onsets),
        RATE,
        audio_start_ns=AUDIO_START_NS,
        ball_to_mic_m=BALL_TO_MIC_M,
    )
    assert report.median_gate_minus_onset_ms == pytest.approx(-1.2, abs=0.1)
    assert report.verdict == "detector_more_conservative_than_the_board"


def test_a_slow_comparator_is_named() -> None:
    """Beyond half a millisecond it is not a threshold difference any more."""
    onsets = [0.2, 0.45, 0.7]
    report = probe.gate_vs_audio(
        _edges_ns([value + 0.004 for value in onsets]),
        _waveform(onsets),
        RATE,
        audio_start_ns=AUDIO_START_NS,
        ball_to_mic_m=BALL_TO_MIC_M,
    )
    assert report.verdict == "comparator_slower_than_expected"


def test_the_audio_report_never_classifies_the_path() -> None:
    """The airborne figure is context; this report does not test against it."""
    onsets = [0.2, 0.45]
    report = probe.gate_vs_audio(
        _edges_ns([value + 0.0003 for value in onsets]),
        _waveform(onsets),
        RATE,
        audio_start_ns=AUDIO_START_NS,
        ball_to_mic_m=BALL_TO_MIC_M,
    )
    assert report.airborne_flight_ms == pytest.approx(4.592, abs=0.01)
    assert not hasattr(report, "path")
    assert report.verdict not in {"airborne", "early", "late"}


def test_airborne_expectation_is_carried_as_context() -> None:
    """1.575 m of air is 4.59 ms, and the implied contact is that much earlier."""
    onsets = [0.3]
    edges = _edges_ns([0.3005])
    report = probe.gate_vs_audio(
        edges,
        _waveform(onsets),
        RATE,
        audio_start_ns=AUDIO_START_NS,
        ball_to_mic_m=BALL_TO_MIC_M,
    )
    assert report.airborne_flight_ms == pytest.approx(4.592, abs=0.01)
    impact = report.impacts[0]
    assert impact.gate_time_ns == int(edges[0])
    assert impact.implied_contact_from_gate_ns == pytest.approx(edges[0] - 4.592e6, abs=1e4)
    assert impact.gate_minus_onset_ms == pytest.approx(0.5, abs=0.1)


def test_edges_outside_the_recording_are_reported_unpaired() -> None:
    """An edge with no audio around it is listed, not silently dropped."""
    onsets = [0.4]
    report = probe.gate_vs_audio(
        _edges_ns([0.4003, 5.0]),
        _waveform(onsets),
        RATE,
        audio_start_ns=AUDIO_START_NS,
        ball_to_mic_m=BALL_TO_MIC_M,
    )
    assert len(report.impacts) == 1
    assert report.unpaired_edges == (1,)


def test_gate_vs_audio_refuses_no_edges() -> None:
    """No edges, nothing to time."""
    with pytest.raises(ValueError, match="no_gate_edges"):
        probe.gate_vs_audio(
            np.zeros(0, dtype=np.int64),
            _waveform([0.4]),
            RATE,
            audio_start_ns=AUDIO_START_NS,
            ball_to_mic_m=BALL_TO_MIC_M,
        )


def test_gate_vs_audio_refuses_when_nothing_pairs() -> None:
    """Edges that never meet an onset are a refusal, not a zero-length median."""
    with pytest.raises(ValueError, match="no_gate_edge_paired"):
        probe.gate_vs_audio(
            _edges_ns([0.4]),
            _waveform([]),
            RATE,
            audio_start_ns=AUDIO_START_NS,
            ball_to_mic_m=BALL_TO_MIC_M,
        )


# ---------------------------------------------------------------------------
# the no-audio fallback: gate edge versus the camera's trigger stamp
# ---------------------------------------------------------------------------


def test_gate_to_host_stamp_latency() -> None:
    """Pairing edges with capture triggers measures the host's own delay."""
    edges = _edges_ns([0.2, 0.5, 0.9])
    triggers = edges + np.asarray([300_000, 420_000, 350_000], dtype=np.int64)
    report = probe.gate_vs_host_stamps(edges, triggers)
    assert report.n == 3
    assert report.median_ms == pytest.approx(0.35, abs=0.01)
    assert report.mad_ms == pytest.approx(0.05, abs=0.01)
    assert report.unpaired_triggers == ()


def test_gate_to_host_stamp_ignores_far_triggers() -> None:
    """A trigger with no edge near it is listed unpaired."""
    edges = _edges_ns([0.2])
    triggers = np.concatenate([edges + 300_000, _edges_ns([0.9])])
    report = probe.gate_vs_host_stamps(edges, triggers, max_pair_ms=5.0)
    assert report.n == 1
    assert report.unpaired_triggers == (1,)


def test_gate_to_host_stamp_refuses_empty_input() -> None:
    """Both sides are required."""
    with pytest.raises(ValueError, match="no_gate_edges"):
        probe.gate_vs_host_stamps(np.zeros(0, dtype=np.int64), _edges_ns([0.2]))
    with pytest.raises(ValueError, match="no_trigger_timestamps"):
        probe.gate_vs_host_stamps(_edges_ns([0.2]), np.zeros(0, dtype=np.int64))


def test_gate_to_host_stamp_refuses_when_nothing_pairs() -> None:
    """Edges and triggers from different runs do not average into an answer."""
    with pytest.raises(ValueError, match="no_trigger_paired"):
        probe.gate_vs_host_stamps(_edges_ns([0.2]), _edges_ns([9.0]))


# ---------------------------------------------------------------------------
# files and CLI
# ---------------------------------------------------------------------------


def test_edge_csv_roundtrip(tmp_path: Path) -> None:
    """Edges written by the GPIO logger read back unchanged."""
    path = tmp_path / "edges.csv"
    edges = _edges_ns([0.2, 0.5])
    probe.write_edges_csv(path, edges)
    assert np.array_equal(probe.read_edges_csv(path), edges)


def test_edge_csv_refuses_an_empty_file(tmp_path: Path) -> None:
    """A logger that caught nothing says so."""
    path = tmp_path / "edges.csv"
    path.write_text("edge,monotonic_ns\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no_gate_edges"):
        probe.read_edges_csv(path)


def test_cli_reports_the_comparator_delay(tmp_path: Path, capsys) -> None:
    """The mic-only CLI prints a row per impact and says what it measured."""
    from scipy.io import wavfile  # pylint: disable=import-outside-toplevel

    onsets = [0.2, 0.45, 0.7]
    wav_path = tmp_path / "mic.wav"
    wavfile.write(wav_path, RATE, _waveform(onsets).astype(np.float32))
    edges_path = tmp_path / "edges.csv"
    probe.write_edges_csv(edges_path, _edges_ns([value + 0.0003 for value in onsets]))

    code = probe.main(
        [
            "--edges",
            str(edges_path),
            "--wav",
            str(wav_path),
            "--audio-start-ns",
            str(AUDIO_START_NS),
            "--distance-m",
            str(BALL_TO_MIC_M),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "comparator_delay" in out
    assert "NOT what the gate fired on" in out
    assert "n=3" in out


def test_cli_reports_the_walk_back_from_a_piezo(tmp_path: Path, capsys) -> None:
    """With --contact-channel the CLI measures the real walk-back."""
    from scipy.io import wavfile  # pylint: disable=import-outside-toplevel

    contacts = [0.2, 0.45, 0.7]
    stereo = _stereo(contacts, heard_delay_ms=AIRBORNE_MS, gate_delay_ms=AIRBORNE_MS + 0.3)
    wav_path = tmp_path / "bench.wav"
    wavfile.write(wav_path, RATE, stereo["samples"].astype(np.float32))
    edges_path = tmp_path / "edges.csv"
    probe.write_edges_csv(edges_path, stereo["edges"])

    code = probe.main(
        [
            "--edges",
            str(edges_path),
            "--wav",
            str(wav_path),
            "--contact-channel",
            "1",
            "--audio-start-ns",
            str(AUDIO_START_NS),
            "--distance-m",
            str(BALL_TO_MIC_M),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "airborne" in out
    assert "n=3" in out


# ---------------------------------------------------------------------------
# gate versus a contact microphone on the mat
# ---------------------------------------------------------------------------


def _stereo(contacts, *, heard_delay_ms: float, gate_delay_ms: float, seed: int = 5) -> dict:
    """Channel 0 = the mic (delayed), channel 1 = a piezo AT contact."""
    piezo = _waveform(contacts, seed=seed)
    heard = _waveform([value + heard_delay_ms / 1000.0 for value in contacts], seed=seed + 1)
    return {
        "samples": np.stack([heard, piezo], axis=1),
        "edges": _edges_ns([value + gate_delay_ms / 1000.0 for value in contacts]),
    }


def test_piezo_referenced_walk_back_is_airborne_when_it_matches_the_flight_time() -> None:
    """A gate that trails contact by the flight time IS the airborne path."""
    contacts = [0.2, 0.45, 0.7]
    stereo = _stereo(contacts, heard_delay_ms=AIRBORNE_MS, gate_delay_ms=AIRBORNE_MS + 0.2)
    report = probe.gate_vs_contact(
        stereo["edges"],
        stereo["samples"],
        RATE,
        audio_start_ns=AUDIO_START_NS,
        ball_to_mic_m=BALL_TO_MIC_M,
    )
    assert report.median_audio_minus_contact_ms == pytest.approx(AIRBORNE_MS, abs=0.15)
    assert report.median_gate_minus_contact_ms == pytest.approx(AIRBORNE_MS + 0.2, abs=0.15)
    assert report.classification == "airborne"


def test_piezo_referenced_walk_back_names_an_early_gate() -> None:
    """A gate 2 ms after contact beats airborne sound: not through the air."""
    contacts = [0.2, 0.45, 0.7]
    stereo = _stereo(contacts, heard_delay_ms=AIRBORNE_MS, gate_delay_ms=2.0)
    report = probe.gate_vs_contact(
        stereo["edges"],
        stereo["samples"],
        RATE,
        audio_start_ns=AUDIO_START_NS,
        ball_to_mic_m=BALL_TO_MIC_M,
    )
    assert report.median_gate_minus_contact_ms == pytest.approx(2.0, abs=0.15)
    assert report.classification == "early"


def test_piezo_referenced_walk_back_names_a_late_gate() -> None:
    """Well beyond the flight time is late, not airborne."""
    contacts = [0.3, 0.6]
    stereo = _stereo(contacts, heard_delay_ms=AIRBORNE_MS, gate_delay_ms=7.0)
    report = probe.gate_vs_contact(
        stereo["edges"],
        stereo["samples"],
        RATE,
        audio_start_ns=AUDIO_START_NS,
        ball_to_mic_m=BALL_TO_MIC_M,
    )
    assert report.classification == "late"


def test_piezo_report_carries_every_impact() -> None:
    """Each impact keeps its own three instants, not just the median."""
    contacts = [0.25, 0.55]
    stereo = _stereo(contacts, heard_delay_ms=AIRBORNE_MS, gate_delay_ms=AIRBORNE_MS)
    report = probe.gate_vs_contact(
        stereo["edges"],
        stereo["samples"],
        RATE,
        audio_start_ns=AUDIO_START_NS,
        ball_to_mic_m=BALL_TO_MIC_M,
    )
    assert len(report.impacts) == 2
    first = report.impacts[0]
    assert first.contact_time_ns < first.audio_onset_time_ns
    assert first.airborne_flight_ms == pytest.approx(AIRBORNE_MS, abs=0.01)


def test_piezo_channel_must_exist() -> None:
    """A mono recording has no contact channel, and says so."""
    with pytest.raises(ValueError, match="channel_1_requested_from_a_mono_recording"):
        probe.gate_vs_contact(
            _edges_ns([0.2]),
            _waveform([0.2]),
            RATE,
            audio_start_ns=AUDIO_START_NS,
            ball_to_mic_m=BALL_TO_MIC_M,
        )


def test_piezo_report_refuses_no_edges() -> None:
    """No edges, nothing to time."""
    contacts = [0.2]
    stereo = _stereo(contacts, heard_delay_ms=AIRBORNE_MS, gate_delay_ms=AIRBORNE_MS)
    with pytest.raises(ValueError, match="no_gate_edges"):
        probe.gate_vs_contact(
            np.zeros(0, dtype=np.int64),
            stereo["samples"],
            RATE,
            audio_start_ns=AUDIO_START_NS,
            ball_to_mic_m=BALL_TO_MIC_M,
        )


def test_piezo_report_refuses_when_nothing_pairs() -> None:
    """Edges with no impact near them are a refusal, not a median of nothing."""
    contacts = [0.2]
    stereo = _stereo(contacts, heard_delay_ms=AIRBORNE_MS, gate_delay_ms=AIRBORNE_MS)
    with pytest.raises(ValueError, match="no_gate_edge_paired_with_a_contact_onset"):
        probe.gate_vs_contact(
            _edges_ns([0.9]),
            stereo["samples"],
            RATE,
            audio_start_ns=AUDIO_START_NS,
            ball_to_mic_m=BALL_TO_MIC_M,
        )


def test_cli_falls_back_to_camera_archives(tmp_path: Path, capsys) -> None:
    """With no ADC, the CLI pairs edges with camera trigger stamps instead."""
    edges = _edges_ns([0.2, 0.5])
    edges_path = tmp_path / "edges.csv"
    probe.write_edges_csv(edges_path, edges)
    for index, edge in enumerate(edges, start=1):
        shot = tmp_path / f"capture_{index:03d}"
        shot.mkdir()
        np.savez(
            shot / "frames.npz",
            frames=np.zeros((3, 4, 4), dtype=np.uint8),
            host_timestamp_ns=np.arange(3, dtype=np.int64),
            trigger_host_timestamp_ns=np.int64(edge + 400_000),
        )

    code = probe.main(["--edges", str(edges_path), "--archive", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "0.40" in out
    assert "n=2" in out


def test_cli_needs_something_to_pair(tmp_path: Path) -> None:
    """Edges alone are not a measurement."""
    edges_path = tmp_path / "edges.csv"
    probe.write_edges_csv(edges_path, _edges_ns([0.2]))
    assert probe.main(["--edges", str(edges_path)]) == 2
