"""The LED onset fit, proven against captures whose onset time we chose.

A synthetic archive lights an LED part-way through one frame's exposure and
stamps every frame with a known host latency. The fit has to hand that
latency back to better than 0.1 ms, find the LED on its own, honour an ROI
override, and refuse -- by name -- every capture that cannot carry the
measurement: no step, a one-frame flash, an LED that was already lit, and
the malformed archives in between.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "hardware-test" / "camera_latency_led.py"
)
_SPEC = importlib.util.spec_from_file_location("camera_latency_led", _SCRIPT)
led = importlib.util.module_from_spec(_SPEC)
# Dataclasses resolve their (string) annotations through sys.modules, so a
# spec-loaded module must be registered before exec -- same pattern as
# test_camera_intrinsics_calibration.
sys.modules[_SPEC.name] = led
_SPEC.loader.exec_module(led)

WIDTH, HEIGHT = 64, 40
FPS = 467.0
EXPOSURE_US = 800
DARK = 18.0
STEP = 120.0
LED_XY = (44, 9)
LED_PX = 5


def _synth_frames(  # pylint: disable=too-many-arguments
    *,
    latency_ms: float = 0.0,
    onset_fraction: float = 0.5,
    lit_frame: int = 14,
    lit_frames: int = 12,
    frame_count: int = 32,
    exposure_us: int = EXPOSURE_US,
    led_xy: tuple[int, int] = LED_XY,
    noise: float = 0.0,
    seed: int = 7,
) -> dict:
    """One capture whose LED onset time and stamp latency are both chosen.

    ``onset_fraction`` is the fraction of frame ``lit_frame``'s exposure
    during which the LED was already on, so the truth the fit must recover
    is exactly ``latency_ms``.
    """
    period_ns = 1e9 / FPS
    exposure_ns = exposure_us * 1000.0
    # Exposure of frame k ends at k * period; the host stamps it `latency` late.
    ends_ns = np.arange(frame_count, dtype=np.float64) * period_ns
    stamps = np.round(ends_ns + latency_ms * 1e6).astype(np.int64)

    onset_ns = ends_ns[lit_frame] - onset_fraction * exposure_ns
    off_ns = onset_ns + lit_frames * period_ns

    starts_ns = ends_ns - exposure_ns
    lit_ns = np.clip(
        np.minimum(ends_ns, off_ns) - np.maximum(starts_ns, onset_ns), 0.0, exposure_ns
    )
    fraction = lit_ns / exposure_ns

    rng = np.random.default_rng(seed)
    frames = np.full((frame_count, HEIGHT, WIDTH), DARK, dtype=np.float64)
    x, y = led_xy
    frames[:, y : y + LED_PX, x : x + LED_PX] += (STEP * fraction)[:, None, None]
    if noise:
        frames += rng.normal(0.0, noise, frames.shape)
    return {
        "frames": np.clip(np.round(frames), 0, 255).astype(np.uint8),
        "host_timestamp_ns": stamps,
        "trigger_host_timestamp_ns": int(round(onset_ns)),
        "exposure_us": np.full(frame_count, exposure_us, dtype=np.int32),
        "pre_trigger_count": np.int32(lit_frame),
    }


def _write_archive(directory: Path, name: str, **overrides) -> Path:
    """Persist a synthetic capture in the shipped frames.npz layout."""
    archive = _synth_frames(**overrides)
    shot_dir = directory / name
    shot_dir.mkdir(parents=True, exist_ok=True)
    path = shot_dir / "frames.npz"
    np.savez(
        path,
        frames=archive["frames"],
        host_timestamp_ns=archive["host_timestamp_ns"],
        exposure_us=archive["exposure_us"],
        analogue_gain=np.full(len(archive["frames"]), 4.0, dtype=np.float32),
        pre_trigger_count=archive["pre_trigger_count"],
        trigger_host_timestamp_ns=np.int64(archive["trigger_host_timestamp_ns"]),
    )
    return path


def _fit(archive: dict, **kwargs) -> "led.LedOnset":
    """Run the fit over a synthetic capture dict."""
    return led.led_onset(
        archive["frames"],
        archive["host_timestamp_ns"],
        archive["trigger_host_timestamp_ns"],
        exposure_us=archive["exposure_us"],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# the measurement itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("latency_ms", [0.0, 1.5, 4.25])
@pytest.mark.parametrize("onset_fraction", [0.1, 0.45, 0.95])
def test_recovers_the_chosen_latency(latency_ms: float, onset_fraction: float) -> None:
    """The fit returns the stamp latency that built the capture."""
    archive = _synth_frames(latency_ms=latency_ms, onset_fraction=onset_fraction)
    onset = _fit(archive)
    assert onset.latency_ms == pytest.approx(latency_ms, abs=0.1)


def test_subframe_onset_beats_the_frame_period() -> None:
    """Two onsets inside one frame are separated to well under a frame."""
    period_ms = 1000.0 / FPS
    early = _fit(_synth_frames(onset_fraction=0.9)).onset_time_ns
    late = _fit(_synth_frames(onset_fraction=0.1)).onset_time_ns
    gap_ms = (late - early) / 1e6
    assert gap_ms == pytest.approx(0.8 * EXPOSURE_US / 1000.0, abs=0.1)
    assert gap_ms < period_ms


def test_reports_the_lit_frame_and_fraction() -> None:
    """The onset frame is the first partially lit frame, with its fraction."""
    onset = _fit(_synth_frames(lit_frame=11, onset_fraction=0.25))
    assert onset.onset_frame_index == 11
    assert onset.lit_fraction == pytest.approx(0.25, abs=0.05)


def test_survives_sensor_noise() -> None:
    """A noisy capture still recovers the latency and reports its margin."""
    onset = _fit(_synth_frames(latency_ms=2.0, onset_fraction=0.6, noise=1.5))
    assert onset.latency_ms == pytest.approx(2.0, abs=0.1)
    assert onset.confidence > 10.0


def test_handles_a_short_gate_pulse() -> None:
    """A three-frame flash is enough: the LED need not latch on."""
    onset = _fit(_synth_frames(latency_ms=1.0, onset_fraction=0.5, lit_frames=3))
    assert onset.latency_ms == pytest.approx(1.0, abs=0.1)


# ---------------------------------------------------------------------------
# finding the LED
# ---------------------------------------------------------------------------


def test_finds_the_led_without_an_roi() -> None:
    """The automatic ROI lands on the pixels that stepped."""
    onset = _fit(_synth_frames(led_xy=(7, 26)))
    x, y, width, height = onset.roi
    assert 7 - LED_PX < x < 7 + LED_PX
    assert 26 - LED_PX < y < 26 + LED_PX
    assert width > 0 and height > 0


def test_roi_override_selects_the_other_light() -> None:
    """With two lights, the ROI decides which onset is measured."""
    first = _synth_frames(lit_frame=12, onset_fraction=0.9, led_xy=(5, 5))
    second = _synth_frames(lit_frame=20, onset_fraction=0.4, led_xy=(44, 30))
    frames = np.maximum(first["frames"], second["frames"])
    merged = dict(first, frames=frames)

    auto = _fit(merged)
    forced = _fit(merged, roi=(44, 30, LED_PX, LED_PX))
    assert auto.onset_frame_index == 12
    assert forced.onset_frame_index == 20


def test_roi_outside_the_frame_is_refused() -> None:
    """An ROI off the sensor is named, not clipped."""
    with pytest.raises(ValueError, match="roi_.*outside"):
        _fit(_synth_frames(), roi=(WIDTH - 2, 0, 10, 10))


# ---------------------------------------------------------------------------
# the refusals
# ---------------------------------------------------------------------------


def test_refuses_a_capture_with_no_step() -> None:
    """A dark capture carries no onset and says so."""
    archive = _synth_frames(noise=1.0)
    archive["frames"] = np.full_like(archive["frames"], int(DARK))
    with pytest.raises(ValueError, match="no_clean_led_step"):
        _fit(archive)


def test_refuses_a_single_frame_flash() -> None:
    """One lit frame cannot separate the partial frame from the plateau."""
    with pytest.raises(ValueError, match="led_lit_for_only_1_frame"):
        _fit(_synth_frames(lit_frames=1, onset_fraction=1.0))


def test_refuses_an_led_already_lit_at_capture_start() -> None:
    """Without a dark baseline the onset is outside the capture."""
    with pytest.raises(ValueError, match="led_already_lit"):
        _fit(_synth_frames(lit_frame=1, onset_fraction=1.0))


def test_refuses_mismatched_timestamps() -> None:
    """Frames and stamps must describe the same capture."""
    archive = _synth_frames()
    archive["host_timestamp_ns"] = archive["host_timestamp_ns"][:-3]
    with pytest.raises(ValueError, match="timestamp_count"):
        _fit(archive)


def test_refuses_non_monotonic_timestamps() -> None:
    """Out-of-order stamps make the sub-frame fit meaningless."""
    archive = _synth_frames()
    archive["host_timestamp_ns"][5] = archive["host_timestamp_ns"][4]
    with pytest.raises(ValueError, match="not_strictly_increasing"):
        _fit(archive)


def test_refuses_a_capture_without_a_trigger() -> None:
    """No trigger stamp, no latency."""
    archive = _synth_frames()
    archive["trigger_host_timestamp_ns"] = 0
    with pytest.raises(ValueError, match="no_trigger_timestamp"):
        _fit(archive)


def test_refuses_too_few_frames() -> None:
    """Three frames is the floor for dark, partial and lit."""
    archive = _synth_frames(frame_count=32)
    archive["frames"] = archive["frames"][:2]
    archive["host_timestamp_ns"] = archive["host_timestamp_ns"][:2]
    archive["exposure_us"] = archive["exposure_us"][:2]
    with pytest.raises(ValueError, match="only_2_frames"):
        _fit(archive)


def test_refuses_an_unusable_exposure() -> None:
    """A zero exposure window cannot carry a sub-frame fraction."""
    archive = _synth_frames()
    archive["exposure_us"] = np.zeros(len(archive["frames"]), dtype=np.int32)
    with pytest.raises(ValueError, match="exposure"):
        _fit(archive)


# ---------------------------------------------------------------------------
# archives, summary and CLI
# ---------------------------------------------------------------------------


def test_analyse_archive_roundtrip(tmp_path: Path) -> None:
    """A saved frames.npz analyses to the latency that built it."""
    path = _write_archive(tmp_path, "capture_001", latency_ms=3.0, onset_fraction=0.3)
    onset = led.analyse_archive(led.load_archive(path))
    assert onset.latency_ms == pytest.approx(3.0, abs=0.1)
    assert onset.source.endswith("frames.npz")


def test_find_archives_walks_a_directory(tmp_path: Path) -> None:
    """A session directory expands to its captures, in order."""
    _write_archive(tmp_path, "capture_002")
    _write_archive(tmp_path, "capture_001")
    found = led.find_archives([tmp_path])
    assert [p.parent.name for p in found] == ["capture_001", "capture_002"]


def test_find_archives_refuses_an_empty_directory(tmp_path: Path) -> None:
    """An empty path is named rather than summarised as zero shots."""
    with pytest.raises(ValueError, match="no_frames_npz"):
        led.find_archives([tmp_path])


def test_summary_reports_median_and_mad() -> None:
    """The summary is the median latency and its MAD, not a mean."""
    onsets = [
        _fit(_synth_frames(latency_ms=value, onset_fraction=0.4)) for value in (1.0, 1.2, 1.4, 9.0)
    ]
    summary = led.summarise_latencies(onsets)
    assert summary.n == 4
    assert summary.median_ms == pytest.approx(1.3, abs=0.1)
    assert summary.mad_ms == pytest.approx(0.2, abs=0.1)
    assert summary.max_ms == pytest.approx(9.0, abs=0.1)


def test_summary_refuses_an_empty_run() -> None:
    """Nothing measured, nothing reported."""
    with pytest.raises(ValueError, match="no_onsets"):
        led.summarise_latencies([])


def test_verdict_line_names_the_direction() -> None:
    """The one-liner says lag or lead, and carries the spread and count."""
    lag = led.LatencySummary(n=5, median_ms=2.34, mad_ms=0.12, min_ms=2.2, max_ms=2.5)
    lead = led.LatencySummary(n=5, median_ms=-2.34, mad_ms=0.12, min_ms=-2.5, max_ms=-2.2)
    assert led.verdict_line(lag) == "camera stamps lag exposure by 2.34 ms (MAD 0.12, n=5)"
    assert led.verdict_line(lead) == "camera stamps lead exposure by 2.34 ms (MAD 0.12, n=5)"


def test_cli_summarises_several_archives(tmp_path: Path, capsys) -> None:
    """The CLI prints one row per capture and one verdict."""
    for index, latency in enumerate((2.0, 2.2, 2.1), start=1):
        _write_archive(tmp_path, f"capture_{index:03d}", latency_ms=latency)
    assert led.main(["--archive", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.count("capture_00") == 3
    assert "camera stamps lag exposure by 2.1" in out
    assert "n=3" in out


def test_cli_reports_a_refusal_without_crashing(tmp_path: Path, capsys) -> None:
    """A dead capture is named in the table; the run still fails loudly."""
    good = _write_archive(tmp_path, "capture_001", latency_ms=2.0)
    bad = _write_archive(tmp_path, "capture_002", latency_ms=2.0)
    with np.load(bad) as data:
        fields = dict(data)
    fields["frames"] = np.full_like(fields["frames"], int(DARK))
    np.savez(bad, **fields)

    assert led.main(["--archive", str(good.parent), "--archive", str(bad.parent)]) == 0
    out = capsys.readouterr().out
    assert "no_clean_led_step" in out
    assert "n=1" in out


def test_cli_refuses_when_nothing_measured(tmp_path: Path) -> None:
    """Every capture refused means a non-zero exit."""
    bad = _write_archive(tmp_path, "capture_001")
    with np.load(bad) as data:
        fields = dict(data)
    fields["frames"] = np.full_like(fields["frames"], int(DARK))
    np.savez(bad, **fields)
    assert led.main(["--archive", str(tmp_path)]) == 1
