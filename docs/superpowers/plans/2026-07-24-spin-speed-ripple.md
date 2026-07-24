# Spin from Speed-Track Ripple — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline spin estimator that extracts spin from the ripple in an overlapped-STFT speed track, and score it against TrackMan-paired sessions (accuracy + coverage vs the production envelope method).

**Architecture:** Three new files under `scripts/analysis/`: a shared loader lib extracted from `experiment_spin_windows.py`, a pure-function ripple estimator, and an experiment CLI that runs the production processor (baseline) plus 4 ripple variants (hop {32,16} × track {frequency,magnitude}) per shot and emits a wide CSV + summary. Nothing in `src/` changes.

**Tech Stack:** Python 3 (uv), numpy (already a dependency; the estimator needs no scipy), pytest.

**Spec:** `docs/superpowers/specs/2026-07-24-spin-speed-ripple-design.md`

## Global Constraints

- Always use `uv run` for Python commands (`uv run pytest …`), never bare `python`/`pytest`.
- No changes to `src/openflight/` or `pyproject.toml` (no new dependencies).
- Branch: `feat/spin-experiments`.
- Test data files used by tests exist in-repo: `session_logs/session_20260605_132943_trackman.jsonl`, `session_logs/comparison_20260605_132943_trackman.csv`.
- Radar constants (must match `RollingBufferProcessor`): SAMPLE_RATE=30000, WINDOW_SIZE=128, FFT_SIZE=4096, WAVELENGTH_M=0.01243, MPS_TO_MPH=2.23694.
- Seam/spin constants (must match production `detect_spin`): seam band 33.0–200.0 Hz, detrend poly order 3, SNR floor 2.5, min 2 seam cycles, min 20 ms of ball signal.

---

### Task 1: Extract shared experiment lib from `experiment_spin_windows.py`

**Files:**
- Create: `scripts/analysis/spin_experiment_lib.py`
- Modify: `scripts/analysis/experiment_spin_windows.py` (replace lines 55–122 helpers with imports; replace the inline `IQCapture(...)` construction at ~line 412 with `capture_from_entry`)
- Test: `tests/test_spin_experiment_lib.py`

**Interfaces:**
- Consumes: `openflight.launch_monitor.ClubType`, `openflight.rolling_buffer.types.IQCapture` (existing).
- Produces (used by Tasks 7–8 and by the refactored `experiment_spin_windows.py`):
  - `club_enum(normalized_club: str) -> ClubType`
  - `to_float(value: Any) -> Optional[float]`
  - `to_int(value: Any) -> Optional[int]`
  - `load_session_entries(path: Path) -> tuple[list[dict], list[dict]]` — (shot_detected entries, rolling_buffer_capture entries)
  - `load_trackman_by_shot(comparison_path: Path) -> dict[int, dict[str, Any]]` — keys: `match_quality`, `spin_tm`, `ball_speed_tm`
  - `capture_from_entry(capture_entry: dict) -> IQCapture`

- [ ] **Step 1: Record baseline output of the existing experiment script (for the refactor diff in Step 7)**

```bash
uv run python scripts/analysis/experiment_spin_windows.py \
  --openflight session_logs/session_20260605_132943_trackman.jsonl \
  --comparison session_logs/comparison_20260605_132943_trackman.csv \
  --output /tmp/spin_windows_before.csv
```

Expected: runs to completion, prints a summary table, writes the CSV. If this pairing has zero matched shots, use `session_logs/session_20260527_125208_trackman.jsonl` with the same comparison file and note which pairing worked (reuse it in Step 7 and Task 8).

- [ ] **Step 2: Write the failing test**

Create `tests/test_spin_experiment_lib.py`:

```python
"""Tests for the shared spin-experiment loaders."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "analysis"))

from spin_experiment_lib import (  # noqa: E402
    capture_from_entry,
    club_enum,
    load_session_entries,
    load_trackman_by_shot,
    to_float,
    to_int,
)
from openflight.launch_monitor import ClubType  # noqa: E402

SESSION = PROJECT_ROOT / "session_logs" / "session_20260605_132943_trackman.jsonl"
COMPARISON = PROJECT_ROOT / "session_logs" / "comparison_20260605_132943_trackman.csv"


def test_load_session_entries_finds_shots_and_captures():
    shots, captures = load_session_entries(SESSION)
    assert len(shots) > 0
    assert len(captures) > 0
    assert all(entry["type"] == "shot_detected" for entry in shots)
    assert all(entry["type"] == "rolling_buffer_capture" for entry in captures)


def test_capture_from_entry_builds_full_capture():
    _, captures = load_session_entries(SESSION)
    capture = capture_from_entry(captures[0])
    assert capture.num_samples == 4096
    assert len(capture.q_samples) == 4096


def test_load_trackman_by_shot_parses_types():
    by_shot = load_trackman_by_shot(COMPARISON)
    assert len(by_shot) > 0
    assert all(isinstance(shot_number, int) for shot_number in by_shot)
    spins = [row["spin_tm"] for row in by_shot.values() if row["spin_tm"] is not None]
    assert len(spins) > 0
    assert all(isinstance(spin, float) for spin in spins)


def test_club_enum_known_and_unknown():
    assert club_enum("driver") == ClubType.DRIVER
    assert club_enum("7-iron") == ClubType.IRON_7
    assert club_enum("frying-pan") == ClubType.UNKNOWN


def test_numeric_coercion():
    assert to_float("") is None
    assert to_float("3.5") == 3.5
    assert to_int("7") == 7
    assert to_int(None) is None
```

Note: `tests/` has no `__init__.py`, so pytest adds each test file's directory to `sys.path`; the explicit `scripts/analysis` insertion above mirrors how existing analysis scripts handle imports. `src/` is importable because the project installs `openflight` in the uv environment.

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_spin_experiment_lib.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spin_experiment_lib'`

- [ ] **Step 4: Create `scripts/analysis/spin_experiment_lib.py`**

Move the bodies **verbatim** from `experiment_spin_windows.py` (drop the leading underscores; these are the exact functions at lines 55–122), and add `capture_from_entry`:

```python
#!/usr/bin/env python3
"""Shared loaders for offline spin experiments.

Used by experiment_spin_windows.py and experiment_spin_ripple.py: session
JSONL parsing, TrackMan comparison-CSV matching, club normalization, and
IQCapture reconstruction from logged capture entries.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from openflight.launch_monitor import ClubType  # noqa: E402
from openflight.rolling_buffer.types import IQCapture  # noqa: E402


def club_enum(normalized_club: str) -> ClubType:
    aliases = {
        "driver": ClubType.DRIVER,
        "3-wood": ClubType.WOOD_3,
        "5-wood": ClubType.WOOD_5,
        "7-wood": ClubType.WOOD_7,
        "3-hybrid": ClubType.HYBRID_3,
        "5-hybrid": ClubType.HYBRID_5,
        "7-hybrid": ClubType.HYBRID_7,
        "9-hybrid": ClubType.HYBRID_9,
        "2-iron": ClubType.IRON_2,
        "3-iron": ClubType.IRON_3,
        "4-iron": ClubType.IRON_4,
        "5-iron": ClubType.IRON_5,
        "6-iron": ClubType.IRON_6,
        "7-iron": ClubType.IRON_7,
        "8-iron": ClubType.IRON_8,
        "9-iron": ClubType.IRON_9,
        "pw": ClubType.PW,
        "gw": ClubType.GW,
        "sw": ClubType.SW,
        "lw": ClubType.LW,
    }
    return aliases.get(normalized_club, ClubType.UNKNOWN)


def to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> Optional[int]:
    number = to_float(value)
    return int(number) if number is not None else None


def load_session_entries(path: Path) -> tuple[list[dict], list[dict]]:
    shots = []
    captures = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("type") == "shot_detected":
                shots.append(entry)
            elif entry.get("type") == "rolling_buffer_capture":
                captures.append(entry)
    return shots, captures


def load_trackman_by_shot(comparison_path: Path) -> dict[int, dict[str, Any]]:
    by_shot = {}
    with comparison_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            shot_number = to_int(row.get("shot_number_of"))
            if shot_number is None:
                continue
            by_shot[shot_number] = {
                "match_quality": row.get("match_quality"),
                "spin_tm": to_float(row.get("spin_tm")),
                "ball_speed_tm": to_float(row.get("ball_speed_tm")),
            }
    return by_shot


def capture_from_entry(capture_entry: dict) -> IQCapture:
    """Rebuild an IQCapture from a logged rolling_buffer_capture entry."""
    return IQCapture(
        sample_time=capture_entry.get("sample_time", 0),
        trigger_time=capture_entry.get("trigger_time", 0),
        i_samples=capture_entry["i_samples"],
        q_samples=capture_entry["q_samples"],
    )
```

- [ ] **Step 5: Refactor `experiment_spin_windows.py` to import from the lib**

Delete its `_club_enum`, `_to_float`, `_to_int`, `_load_session_entries`, `_load_trackman_by_shot` definitions (lines 55–122) and the `csv`/`json` imports **if now unused** (`csv` is still used by `_write_csv` — keep it; `json` becomes unused — remove it). Add after the existing sys.path setup:

```python
from spin_experiment_lib import (  # noqa: E402
    capture_from_entry,
    club_enum as _club_enum,
    load_session_entries as _load_session_entries,
    load_trackman_by_shot as _load_trackman_by_shot,
    to_int as _to_int,
)
```

(Aliasing to the old underscore names keeps the rest of the file untouched. `_to_float` has no remaining callers once the loaders move — do not re-import it.)

Replace the inline capture construction in `_rows` (~line 412):

```python
        capture = capture_from_entry(capture_entry)
```

- [ ] **Step 6: Run the new tests and the full suite**

Run: `uv run pytest tests/test_spin_experiment_lib.py -v && uv run pytest tests/ -q`
Expected: new tests PASS; no regressions elsewhere.

- [ ] **Step 7: Verify refactor changed nothing (CSV diff)**

```bash
uv run python scripts/analysis/experiment_spin_windows.py \
  --openflight session_logs/session_20260605_132943_trackman.jsonl \
  --comparison session_logs/comparison_20260605_132943_trackman.csv \
  --output /tmp/spin_windows_after.csv
diff /tmp/spin_windows_before.csv /tmp/spin_windows_after.csv && echo IDENTICAL
```

Expected: `IDENTICAL`.

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check scripts/analysis/spin_experiment_lib.py scripts/analysis/experiment_spin_windows.py tests/test_spin_experiment_lib.py
git add scripts/analysis/spin_experiment_lib.py scripts/analysis/experiment_spin_windows.py tests/test_spin_experiment_lib.py
git commit -m "refactor(analysis): extract shared spin-experiment loaders"
```

---

### Task 2: Synthetic I/Q generator for estimator tests

**Files:**
- Create: `tests/spin_synth.py`
- Test: `tests/test_spin_synth.py`

**Interfaces:**
- Produces (used by Tasks 3–6 tests):
  - `synth_capture(rpm, *, ball_speed_mph=145.0, am_depth=0.03, fm_dev_hz=25.0, decel_mph_per_s=60.0, onset_ms=8.0, visible_ms=None, amplitude=40.0, noise_rms=2.0, seed=1, n_samples=4096, sample_rate=30000) -> tuple[list[float], list[float]]` — (i_samples, q_samples) centered at 2048, ball tone active from `onset_ms` for `visible_ms` (None = to end of capture), seam AM at depth `am_depth`, seam FM at deviation `fm_dev_hz`, linear deceleration `decel_mph_per_s`.

- [ ] **Step 1: Write the failing sanity test**

Create `tests/test_spin_synth.py`:

```python
"""Sanity checks for the synthetic seam-modulated I/Q generator."""

import numpy as np

from spin_synth import synth_capture


def _dominant_freq_hz(i_samples, q_samples, start, sample_rate=30000):
    i = np.array(i_samples[start:start + 1024]) - np.mean(i_samples[start:start + 1024])
    q = np.array(q_samples[start:start + 1024]) - np.mean(q_samples[start:start + 1024])
    spectrum = np.abs(np.fft.fft(i + 1j * q, 8192))
    peak_bin = int(np.argmax(spectrum[1:4096])) + 1
    return peak_bin * sample_rate / 8192


def test_tone_lands_at_ball_doppler():
    i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=145.0, decel_mph_per_s=0.0)
    expected_hz = 2 * (145.0 / 2.23694) / 0.01243  # ~10430 Hz
    measured = _dominant_freq_hz(i_samples, q_samples, start=600)
    assert abs(measured - expected_hz) < 150


def test_silent_before_onset():
    i_samples, q_samples = synth_capture(rpm=3000, onset_ms=20.0, noise_rms=0.0)
    onset_sample = int(20.0 * 30000 / 1000)
    assert max(abs(v - 2048.0) for v in i_samples[: onset_sample - 1]) < 1e-9
    assert max(abs(v - 2048.0) for v in i_samples[onset_sample + 10 :]) > 1.0


def test_visible_ms_truncates_signal():
    i_samples, _ = synth_capture(rpm=3000, onset_ms=8.0, visible_ms=15.0, noise_rms=0.0)
    end_sample = int((8.0 + 15.0) * 30000 / 1000)
    assert max(abs(v - 2048.0) for v in i_samples[end_sample + 10 :]) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_spin_synth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spin_synth'`

- [ ] **Step 3: Implement `tests/spin_synth.py`**

```python
"""Synthetic seam-modulated Doppler I/Q generator for spin estimator tests.

Models the outbound golf-ball return: a Doppler tone at the ball speed with
linear deceleration, seam amplitude modulation (AM) at 1x spin rate, seam
frequency modulation (FM) at 1x spin rate, plus white ADC noise. Values are
centered at 2048 to mimic the OPS243's 12-bit ADC.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

WAVELENGTH_M = 0.01243
MPS_TO_MPH = 2.23694
ADC_CENTER = 2048.0


def _mph_to_hz(mph: float) -> float:
    return 2 * (mph / MPS_TO_MPH) / WAVELENGTH_M


def synth_capture(
    rpm: float,
    *,
    ball_speed_mph: float = 145.0,
    am_depth: float = 0.03,
    fm_dev_hz: float = 25.0,
    decel_mph_per_s: float = 60.0,
    onset_ms: float = 8.0,
    visible_ms: Optional[float] = None,
    amplitude: float = 40.0,
    noise_rms: float = 2.0,
    seed: int = 1,
    n_samples: int = 4096,
    sample_rate: int = 30000,
) -> tuple[list[float], list[float]]:
    rng = np.random.default_rng(seed)
    t = np.arange(n_samples) / sample_rate
    onset_s = onset_ms / 1000.0
    seam_hz = rpm / 60.0

    time_since_onset = np.maximum(t - onset_s, 0.0)
    inst_freq = (
        _mph_to_hz(ball_speed_mph)
        - _mph_to_hz(decel_mph_per_s) * time_since_onset
        + fm_dev_hz * np.sin(2 * np.pi * seam_hz * time_since_onset)
    )
    phase = 2 * np.pi * np.cumsum(inst_freq) / sample_rate

    envelope = amplitude * (
        1.0 + am_depth * np.sin(2 * np.pi * seam_hz * time_since_onset + 0.7)
    )
    active = t >= onset_s
    if visible_ms is not None:
        active &= t < onset_s + visible_ms / 1000.0

    signal = envelope * np.exp(1j * phase) * active
    i_samples = ADC_CENTER + signal.real + rng.normal(0.0, noise_rms, n_samples)
    q_samples = ADC_CENTER + signal.imag + rng.normal(0.0, noise_rms, n_samples)
    return i_samples.tolist(), q_samples.tolist()
```

Note: `noise_rms=0.0` with `rng.normal(0.0, 0.0, n)` returns exact zeros, so the silence assertions in Step 1 hold.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_spin_synth.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check tests/spin_synth.py tests/test_spin_synth.py
git add tests/spin_synth.py tests/test_spin_synth.py
git commit -m "test(spin): add synthetic seam-modulated I/Q generator"
```

---

### Task 3: Ripple track extraction (`extract_ripple_track`)

**Files:**
- Create: `scripts/analysis/spin_ripple_estimator.py`
- Test: `tests/test_spin_ripple_estimator.py` (created here, extended in Tasks 4–6)

**Interfaces:**
- Produces (used by Tasks 4–6):
  - `RippleTrack` dataclass: `times_ms: np.ndarray`, `freq_hz: np.ndarray`, `magnitude: np.ndarray` (one entry per STFT window; `n_windows` property)
  - `extract_ripple_track(i_samples, q_samples, ball_speed_mph, hop) -> RippleTrack`
  - Module constants listed in Global Constraints plus: `TOLERANCE_MPH = 8.0`, `MIN_SEAM_HZ = 33.0`, `MAX_SEAM_HZ = 200.0`, `TRACK_FFT_SIZE = 8192`, `DETREND_POLY_ORDER = 3`, `SNR_MIN = 2.5`, `MIN_CYCLES = 2.0`, `MIN_TRACK_DURATION_MS = 20.0`

- [ ] **Step 1: Write the failing test**

Create `tests/test_spin_ripple_estimator.py`:

```python
"""Tests for the offline speed-track ripple spin estimator."""

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "analysis"))

from spin_synth import synth_capture  # noqa: E402
import spin_ripple_estimator as ripple  # noqa: E402


BALL_MPH = 145.0
BALL_HZ = 2 * (BALL_MPH / 2.23694) / 0.01243


class TestExtractRippleTrack:
    def test_track_follows_doppler_tone(self):
        i_samples, q_samples = synth_capture(
            rpm=3000, ball_speed_mph=BALL_MPH, decel_mph_per_s=0.0, fm_dev_hz=0.0
        )
        track = ripple.extract_ripple_track(i_samples, q_samples, BALL_MPH, hop=32)
        assert track.n_windows == (4096 - 128) // 32 + 1
        active = track.times_ms > 12.0
        assert np.all(np.abs(track.freq_hz[active] - BALL_HZ) < 120.0)

    def test_track_follows_deceleration_chirp(self):
        i_samples, q_samples = synth_capture(
            rpm=3000, ball_speed_mph=BALL_MPH, decel_mph_per_s=60.0, fm_dev_hz=0.0
        )
        track = ripple.extract_ripple_track(i_samples, q_samples, BALL_MPH, hop=32)
        active = track.times_ms > 12.0
        first = track.freq_hz[active][0]
        last = track.freq_hz[active][-1]
        # 60 mph/s over ~120 ms of visible flight = ~7 mph = ~520 Hz drop
        assert first - last > 300.0

    def test_hop_16_doubles_track_density(self):
        i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=BALL_MPH)
        track32 = ripple.extract_ripple_track(i_samples, q_samples, BALL_MPH, hop=32)
        track16 = ripple.extract_ripple_track(i_samples, q_samples, BALL_MPH, hop=16)
        assert track16.n_windows > 1.9 * track32.n_windows

    def test_magnitude_track_is_positive_during_flight(self):
        i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=BALL_MPH)
        track = ripple.extract_ripple_track(i_samples, q_samples, BALL_MPH, hop=32)
        active = track.times_ms > 12.0
        assert np.all(track.magnitude[active] > 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_spin_ripple_estimator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spin_ripple_estimator'`

- [ ] **Step 3: Implement the module skeleton with `extract_ripple_track`**

Create `scripts/analysis/spin_ripple_estimator.py`:

```python
#!/usr/bin/env python3
"""Spin estimation from overlapped-STFT speed-track ripple (offline).

The golf ball seam modulates the radar return once per revolution: in
amplitude (AM — what production detect_spin sees via the bandpass envelope)
and in apparent Doppler frequency (FM — the "ripple in the speed reports"
described by OmniPreSense when the main speed FFT runs with overlapped
128-sample windows). This module extracts both tracks from an overlapped
STFT and recovers the seam tone from either, mirroring the production
detect_spin gates (seam band, SNR floor, split-half persistence, rails,
minimum cycles) so results are comparable.

Offline/experimental only — nothing in src/ imports this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

# Radar/STFT constants — must match RollingBufferProcessor.
SAMPLE_RATE = 30000
WINDOW_SIZE = 128
FFT_SIZE = 4096
WAVELENGTH_M = 0.01243
MPS_TO_MPH = 2.23694

# Ball-tone tracking band around the detected ball speed.
TOLERANCE_MPH = 8.0

# Seam-tone gates — values mirror production detect_spin.
MIN_SEAM_HZ = 33.0            # ~2000 RPM
MAX_SEAM_HZ = 200.0           # 12000 RPM
TRACK_FFT_SIZE = 8192         # zero-padded ripple FFT
DETREND_POLY_ORDER = 3
SNR_MIN = 2.5
MIN_CYCLES = 2.0
MIN_TRACK_DURATION_MS = 20.0  # mirrors SPIN_MIN_SAMPLES (600 samples @ 30 ksps)
RAIL_GUARD_NATURAL_BINS = 2   # rail margin in natural-resolution bins

# Ball-signal-loss trim — production constants, expressed in raw samples;
# converted to track windows by hop at use time.
SIGNAL_LOSS_SMOOTH_SAMPLES = 90
SIGNAL_LOSS_REF_SAMPLES = 450
SIGNAL_LOSS_THRESHOLD = 0.15
SIGNAL_LOSS_HOLD_SAMPLES = 150

# Expected-spin prior disambiguation — production thresholds.
PRIOR_MIN_RELATIVE_MAG = 0.40
PRIOR_MAX_RELATIVE_ERROR = 0.55
PRIOR_STRONGEST_FAR_ERROR = 0.45


def _mph_to_hz(mph: float) -> float:
    return 2 * (mph / MPS_TO_MPH) / WAVELENGTH_M


@dataclass
class RippleTrack:
    """Per-window ball-tone measurements from the overlapped STFT."""

    times_ms: np.ndarray   # window-center timestamps
    freq_hz: np.ndarray    # interpolated ball-peak frequency (FM ripple)
    magnitude: np.ndarray  # ball-peak magnitude (AM ripple)

    @property
    def n_windows(self) -> int:
        return len(self.times_ms)


def extract_ripple_track(
    i_samples,
    q_samples,
    ball_speed_mph: float,
    hop: int,
) -> RippleTrack:
    """Track the ball tone through every overlapped STFT window.

    Unlike process_overlapping, no CFAR/threshold gates are applied: the
    peak inside a ±TOLERANCE_MPH band around the expected ball frequency is
    taken in every window so the track is continuous. Peak frequency is
    refined by parabolic interpolation on the zero-padded spectrum.
    """
    i_data = np.asarray(i_samples, dtype=np.float64)
    q_data = np.asarray(q_samples, dtype=np.float64)
    hann = np.hanning(WINDOW_SIZE)

    bin_hz = SAMPLE_RATE / FFT_SIZE
    center_hz = _mph_to_hz(ball_speed_mph)
    tol_hz = _mph_to_hz(TOLERANCE_MPH)
    lo_bin = max(1, int(np.floor((center_hz - tol_hz) / bin_hz)))
    hi_bin = min(FFT_SIZE // 2 - 2, int(np.ceil((center_hz + tol_hz) / bin_hz)))

    times, freqs, mags = [], [], []
    for start in range(0, len(i_data) - WINDOW_SIZE + 1, hop):
        i_block = i_data[start : start + WINDOW_SIZE]
        q_block = q_data[start : start + WINDOW_SIZE]
        block = (i_block - i_block.mean()) + 1j * (q_block - q_block.mean())
        spectrum = np.abs(np.fft.fft(block * hann, FFT_SIZE))

        band = spectrum[lo_bin : hi_bin + 1]
        peak_bin = int(np.argmax(band)) + lo_bin

        # Parabolic interpolation on the peak and its neighbors.
        y0, y1, y2 = spectrum[peak_bin - 1], spectrum[peak_bin], spectrum[peak_bin + 1]
        denom = y0 - 2 * y1 + y2
        delta = 0.5 * (y0 - y2) / denom if denom != 0 else 0.0
        delta = float(np.clip(delta, -0.5, 0.5))

        times.append((start + WINDOW_SIZE / 2) / SAMPLE_RATE * 1000)
        freqs.append((peak_bin + delta) * bin_hz)
        mags.append(float(y1))

    return RippleTrack(
        times_ms=np.array(times),
        freq_hz=np.array(freqs),
        magnitude=np.array(mags),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_spin_ripple_estimator.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check scripts/analysis/spin_ripple_estimator.py tests/test_spin_ripple_estimator.py
git add scripts/analysis/spin_ripple_estimator.py tests/test_spin_ripple_estimator.py
git commit -m "feat(analysis): extract overlapped-STFT ball ripple track"
```

---

### Task 4: Ball-window trimming (`trim_to_ball_window`)

**Files:**
- Modify: `scripts/analysis/spin_ripple_estimator.py`
- Test: `tests/test_spin_ripple_estimator.py` (append)

**Interfaces:**
- Consumes: `RippleTrack` (Task 3).
- Produces: `trim_to_ball_window(track: RippleTrack, ball_timestamp_ms: float, hop: int) -> RippleTrack`

- [ ] **Step 1: Write the failing tests (append to the test file)**

```python
class TestTrimToBallWindow:
    def _track(self, rpm=3000, onset_ms=8.0, visible_ms=None):
        i_samples, q_samples = synth_capture(
            rpm=rpm, ball_speed_mph=BALL_MPH, onset_ms=onset_ms, visible_ms=visible_ms
        )
        return ripple.extract_ripple_track(i_samples, q_samples, BALL_MPH, hop=32)

    def test_trims_windows_before_onset(self):
        track = self._track(onset_ms=20.0)
        trimmed = ripple.trim_to_ball_window(track, ball_timestamp_ms=20.0, hop=32)
        assert trimmed.n_windows < track.n_windows
        assert trimmed.times_ms[0] >= 20.0

    def test_trims_after_signal_collapse(self):
        track = self._track(onset_ms=8.0, visible_ms=50.0)
        trimmed = ripple.trim_to_ball_window(track, ball_timestamp_ms=8.0, hop=32)
        # Signal dies at ~58 ms; the trimmed track must not extend far past it.
        assert trimmed.times_ms[-1] < 75.0

    def test_keeps_full_track_when_signal_persists(self):
        track = self._track(onset_ms=8.0, visible_ms=None)
        trimmed = ripple.trim_to_ball_window(track, ball_timestamp_ms=8.0, hop=32)
        kept = track.times_ms >= 8.0
        assert trimmed.n_windows == int(np.sum(kept))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_spin_ripple_estimator.py::TestTrimToBallWindow -v`
Expected: FAIL with `AttributeError: ... has no attribute 'trim_to_ball_window'`

- [ ] **Step 3: Implement `trim_to_ball_window` (append to the module)**

Port of `RollingBufferProcessor._ball_signal_end_sample`, operating on the magnitude track (units: windows instead of raw samples; the sample-domain constants divide by hop):

```python
def trim_to_ball_window(
    track: RippleTrack, ball_timestamp_ms: float, hop: int
) -> RippleTrack:
    """Trim the track to [ball onset, ball-signal collapse).

    Mirrors production _ball_signal_end_sample: signal is lost when the
    smoothed magnitude stays below SIGNAL_LOSS_THRESHOLD x the early-window
    reference level for a sustained hold period. Returns the full post-onset
    track when no loss is found (outdoor shots).
    """
    mask = track.times_ms >= ball_timestamp_ms
    times = track.times_ms[mask]
    freqs = track.freq_hz[mask]
    mags = track.magnitude[mask]
    if len(mags) == 0:
        return RippleTrack(times, freqs, mags)

    smooth_n = max(1, SIGNAL_LOSS_SMOOTH_SAMPLES // hop)
    ref_n = max(3, SIGNAL_LOSS_REF_SAMPLES // hop)
    hold_n = max(1, SIGNAL_LOSS_HOLD_SAMPLES // hop)

    if len(mags) < ref_n:
        return RippleTrack(times, freqs, mags)
    kernel = np.ones(smooth_n) / smooth_n
    smoothed = np.convolve(mags, kernel, mode="same")
    reference = float(np.median(smoothed[:ref_n]))
    if reference <= 0:
        return RippleTrack(times, freqs, mags)

    below = smoothed < reference * SIGNAL_LOSS_THRESHOLD
    if len(below) < hold_n:
        return RippleTrack(times, freqs, mags)
    sustained = np.convolve(below.astype(float), np.ones(hold_n), mode="valid") >= hold_n
    if not sustained.any():
        return RippleTrack(times, freqs, mags)
    end = int(np.argmax(sustained))
    return RippleTrack(times[:end], freqs[:end], mags[:end])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_spin_ripple_estimator.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check scripts/analysis/spin_ripple_estimator.py tests/test_spin_ripple_estimator.py
git add scripts/analysis/spin_ripple_estimator.py tests/test_spin_ripple_estimator.py
git commit -m "feat(analysis): trim ripple track to ball-visible window"
```

---

### Task 5: Seam-tone detection on a track (`detect_ripple_spin`)

**Files:**
- Modify: `scripts/analysis/spin_ripple_estimator.py`
- Test: `tests/test_spin_ripple_estimator.py` (append)

**Interfaces:**
- Produces (used by Task 6):
  - `RippleSpinResult` dataclass: `spin_rpm: float`, `snr: float`, `peak_freq_hz: Optional[float]`, `seam_cycles: Optional[float]`, `n_windows: int`, `persistent: bool`, `at_lower_rail: bool`, `at_upper_rail: bool`, `rejection_reason: Optional[str]`; property `detected -> bool` (True iff `rejection_reason is None`)
  - `detect_ripple_spin(values: np.ndarray, track_rate_hz: float, *, expected_spin_rpm: Optional[float] = None) -> RippleSpinResult`

These tests drive the function with **directly constructed track arrays** (not synthetic I/Q) so each gate is exercised in isolation. Track rate 937.5 Hz = hop 32.

- [ ] **Step 1: Write the failing tests (append to the test file)**

```python
TRACK_RATE = 30000 / 32  # 937.5 Hz


def _sine_track(freq_hz, n=110, rate=TRACK_RATE, amp=1.0, noise=0.02, seed=3):
    rng = np.random.default_rng(seed)
    t = np.arange(n) / rate
    return amp * np.sin(2 * np.pi * freq_hz * t) + rng.normal(0, noise, n)


class TestDetectRippleSpin:
    def test_recovers_clean_seam_tone(self):
        result = ripple.detect_ripple_spin(_sine_track(60.0), TRACK_RATE)
        assert result.detected
        assert abs(result.spin_rpm - 3600.0) < 150.0
        assert result.snr >= ripple.SNR_MIN
        assert result.persistent

    def test_rejects_track_too_short(self):
        result = ripple.detect_ripple_spin(_sine_track(60.0, n=12), TRACK_RATE)
        assert not result.detected
        assert "short" in result.rejection_reason

    def test_rejects_flat_track(self):
        result = ripple.detect_ripple_spin(np.ones(110), TRACK_RATE)
        assert not result.detected

    def test_rejects_pure_noise(self):
        rng = np.random.default_rng(7)
        result = ripple.detect_ripple_spin(rng.normal(0, 1, 110), TRACK_RATE)
        assert not result.detected

    def test_cubic_drift_alone_is_not_spin(self):
        # Deceleration-style smooth drift, no seam tone.
        t = np.linspace(0, 1, 110)
        drift = 500.0 * t - 180.0 * t**2 + 40.0 * t**3
        result = ripple.detect_ripple_spin(drift, TRACK_RATE)
        assert not result.detected

    def test_transient_burst_fails_persistence(self):
        # Tone present only in the first half of the track.
        tone = _sine_track(60.0, n=110, noise=0.0)
        tone[55:] = 0.0
        rng = np.random.default_rng(9)
        result = ripple.detect_ripple_spin(tone + rng.normal(0, 0.02, 110), TRACK_RATE)
        assert not result.detected

    def test_prior_recovers_fundamental_over_stronger_harmonic(self):
        fundamental = _sine_track(55.0, amp=0.6, noise=0.0)
        harmonic = _sine_track(110.0, amp=1.0, noise=0.0, seed=4)
        rng = np.random.default_rng(5)
        track = fundamental + harmonic + rng.normal(0, 0.02, 110)
        without_prior = ripple.detect_ripple_spin(track, TRACK_RATE)
        with_prior = ripple.detect_ripple_spin(
            track, TRACK_RATE, expected_spin_rpm=3300.0
        )
        assert abs(without_prior.spin_rpm - 6600.0) < 200.0
        assert abs(with_prior.spin_rpm - 3300.0) < 200.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_spin_ripple_estimator.py::TestDetectRippleSpin -v`
Expected: FAIL with `AttributeError: ... has no attribute 'detect_ripple_spin'`

- [ ] **Step 3: Implement `RippleSpinResult`, peak selection, persistence, and `detect_ripple_spin` (append to the module)**

```python
@dataclass
class RippleSpinResult:
    """Seam-tone detection result from one ripple track."""

    spin_rpm: float
    snr: float
    peak_freq_hz: Optional[float] = None
    seam_cycles: Optional[float] = None
    n_windows: int = 0
    persistent: bool = False
    at_lower_rail: bool = False
    at_upper_rail: bool = False
    rejection_reason: Optional[str] = None

    @property
    def detected(self) -> bool:
        return self.rejection_reason is None


def _reject(reason: str, **kwargs) -> RippleSpinResult:
    return RippleSpinResult(spin_rpm=0.0, snr=0.0, rejection_reason=reason, **kwargs)


def _select_peak(
    valid_mag: np.ndarray, valid_freqs: np.ndarray, expected_spin_rpm: Optional[float]
) -> int:
    """Argmax, unless it is far from a supplied prior and a strong local
    maximum sits near the prior — production _select_spin_peak, simplified."""
    strongest = int(np.argmax(valid_mag))
    if expected_spin_rpm is None or expected_spin_rpm <= 0:
        return strongest
    strongest_error = abs(valid_freqs[strongest] * 60 - expected_spin_rpm) / expected_spin_rpm
    if strongest_error <= PRIOR_STRONGEST_FAR_ERROR:
        return strongest

    interior = (valid_mag[1:-1] > valid_mag[:-2]) & (valid_mag[1:-1] > valid_mag[2:])
    candidates = np.where(interior)[0] + 1
    peak_mag = valid_mag[strongest]
    best = None
    for idx in candidates:
        relative = valid_mag[idx] / peak_mag if peak_mag > 0 else 0.0
        error = abs(valid_freqs[idx] * 60 - expected_spin_rpm) / expected_spin_rpm
        if relative >= PRIOR_MIN_RELATIVE_MAG and error <= PRIOR_MAX_RELATIVE_ERROR:
            if best is None or valid_mag[idx] > valid_mag[best]:
                best = int(idx)
    return best if best is not None else strongest


def _band_spectrum(values: np.ndarray, track_rate_hz: float):
    """Hann-windowed zero-padded magnitude spectrum inside the seam band."""
    windowed = values * np.hanning(len(values))
    magnitude = np.abs(np.fft.fft(windowed, TRACK_FFT_SIZE))
    freqs = np.fft.fftfreq(TRACK_FFT_SIZE, d=1 / track_rate_hz)
    half = TRACK_FFT_SIZE // 2
    magnitude, freqs = magnitude[1:half], freqs[1:half]
    band = (freqs >= MIN_SEAM_HZ) & (freqs <= MAX_SEAM_HZ)
    return magnitude[band], freqs[band]


def _peak_is_persistent(
    values: np.ndarray, peak_freq_hz: float, track_rate_hz: float
) -> bool:
    """Production _spin_peak_is_persistent, ported to the track domain: the
    picked tone must be present and (near-)dominant in both track halves."""
    half = len(values) // 2
    if half < 8:
        return True
    for segment in (values[:half], values[half:]):
        seg = segment - np.mean(segment)
        valid_mag, valid_freqs = _band_spectrum(seg, track_rate_hz)
        if valid_mag.size == 0 or not np.any(valid_mag > 0):
            return False
        floor = float(np.median(valid_mag[valid_mag > 0]))
        tol_hz = 2.0 * track_rate_hz / len(seg)
        near = np.abs(valid_freqs - peak_freq_hz) <= tol_hz
        if not near.any() or floor <= 0:
            return False
        near_max = float(valid_mag[near].max())
        if near_max < 2.5 * floor or near_max < 0.7 * float(valid_mag.max()):
            return False
    return True


def detect_ripple_spin(
    values: np.ndarray,
    track_rate_hz: float,
    *,
    expected_spin_rpm: Optional[float] = None,
) -> RippleSpinResult:
    """Recover the seam tone from one ripple track (frequency or magnitude).

    Detrend (poly order 3) removes the deceleration chirp / range falloff;
    the zero-padded FFT of the residual is searched inside the seam band with
    production-mirrored gates: SNR floor, minimum seam cycles, rail guards,
    and split-half persistence.
    """
    values = np.asarray(values, dtype=np.float64)
    n = len(values)
    duration_ms = n / track_rate_hz * 1000
    if n < 8 or duration_ms < MIN_TRACK_DURATION_MS:
        return _reject(
            f"Track too short ({duration_ms:.1f} ms, need {MIN_TRACK_DURATION_MS:.0f})",
            n_windows=n,
        )

    centered = values - np.mean(values)
    if np.std(centered) < 1e-12:
        return _reject("No ripple variation in track", n_windows=n)
    x = np.arange(n, dtype=np.float64)
    trend = np.polyval(np.polyfit(x, centered, DETREND_POLY_ORDER), x)
    residual = centered - trend
    if np.std(residual) < 1e-12:
        return _reject("No ripple variation after detrend", n_windows=n)

    valid_mag, valid_freqs = _band_spectrum(residual, track_rate_hz)
    if valid_mag.size < 3:
        return _reject("No seam band in track spectrum", n_windows=n)

    peak_idx = _select_peak(valid_mag, valid_freqs, expected_spin_rpm)
    peak_freq = float(valid_freqs[peak_idx])
    peak_mag = float(valid_mag[peak_idx])

    positive = valid_mag[valid_mag > 0]
    noise_floor = float(np.median(positive)) if positive.size else 0.0
    snr = peak_mag / noise_floor if noise_floor > 0 else 0.0

    # Rail guards: margin = RAIL_GUARD_NATURAL_BINS natural-resolution bins,
    # expressed in zero-padded bins (bin width = track_rate / TRACK_FFT_SIZE;
    # natural resolution = track_rate / n).
    rail_bins = int(np.ceil(RAIL_GUARD_NATURAL_BINS * TRACK_FFT_SIZE / n))
    at_lower_rail = peak_idx < rail_bins
    at_upper_rail = peak_idx >= len(valid_mag) - rail_bins

    seam_cycles = peak_freq * (n / track_rate_hz)
    persistent = _peak_is_persistent(residual, peak_freq, track_rate_hz)
    diagnostics = dict(
        peak_freq_hz=peak_freq,
        seam_cycles=seam_cycles,
        n_windows=n,
        persistent=persistent,
        at_lower_rail=at_lower_rail,
        at_upper_rail=at_upper_rail,
    )

    if seam_cycles < MIN_CYCLES:
        return _reject(
            f"Too few seam cycles ({seam_cycles:.1f}, need {MIN_CYCLES:.0f})",
            **diagnostics,
        )
    if snr < SNR_MIN:
        result = _reject(f"SNR {snr:.2f} below {SNR_MIN}", **diagnostics)
        result.snr = round(snr, 2)
        return result
    if at_lower_rail or at_upper_rail:
        rail = "lower" if at_lower_rail else "upper"
        result = _reject(f"Peak at {rail} rail of seam band", **diagnostics)
        result.snr = round(snr, 2)
        return result
    if not persistent:
        result = _reject("Seam tone not persistent across track halves", **diagnostics)
        result.snr = round(snr, 2)
        return result

    return RippleSpinResult(
        spin_rpm=peak_freq * 60.0,
        snr=round(snr, 2),
        **diagnostics,
    )
```

Note `_reject` passes `snr=0.0` by default; the SNR/rail/persistence rejections overwrite it so the experiment CSV can show how close a miss was.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_spin_ripple_estimator.py -v`
Expected: PASS (14 tests). If `test_prior_recovers_fundamental_over_stronger_harmonic` fails on the no-prior expectation, check that 110 Hz is inside the band mask (it is: 33–200 Hz) and that the harmonic amplitude reads stronger after Hann windowing — adjust amplitudes (0.6/1.0) rather than thresholds; the production prior constants are fixed.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check scripts/analysis/spin_ripple_estimator.py tests/test_spin_ripple_estimator.py
git add scripts/analysis/spin_ripple_estimator.py tests/test_spin_ripple_estimator.py
git commit -m "feat(analysis): seam-tone detection on ripple tracks"
```

---

### Task 6: End-to-end variants (`run_ripple_variants`) on synthetic captures

**Files:**
- Modify: `scripts/analysis/spin_ripple_estimator.py`
- Test: `tests/test_spin_ripple_estimator.py` (append)

**Interfaces:**
- Produces (used by Task 7):
  - `VARIANT_NAMES = ("freq_hop32", "mag_hop32", "freq_hop16", "mag_hop16")`
  - `run_ripple_variants(i_samples, q_samples, ball_speed_mph, ball_timestamp_ms, *, expected_spin_rpm=None, hops=(32, 16)) -> dict[str, RippleSpinResult]` — keys are `VARIANT_NAMES`

- [ ] **Step 1: Write the failing tests (append to the test file)**

```python
class TestRunRippleVariants:
    def _run(self, **synth_kwargs):
        i_samples, q_samples = synth_capture(ball_speed_mph=BALL_MPH, **synth_kwargs)
        return ripple.run_ripple_variants(
            i_samples, q_samples, BALL_MPH, ball_timestamp_ms=8.0
        )

    def test_returns_all_four_variants(self):
        results = self._run(rpm=3000)
        assert set(results) == set(ripple.VARIANT_NAMES)

    def test_combined_modulation_recovered_across_rpm_grid(self):
        for rpm in (2500, 3000, 5000, 7000, 9500):
            results = self._run(rpm=rpm, seed=rpm)
            detected = {
                name: result
                for name, result in results.items()
                if result.detected
            }
            assert detected, f"no variant detected spin at {rpm} RPM"
            for name, result in detected.items():
                assert abs(result.spin_rpm - rpm) < 200.0, (
                    f"{name} at {rpm} RPM read {result.spin_rpm:.0f}"
                )

    def test_fm_only_seen_by_frequency_track(self):
        results = self._run(rpm=3000, am_depth=0.0, fm_dev_hz=30.0)
        assert results["freq_hop32"].detected
        assert abs(results["freq_hop32"].spin_rpm - 3000.0) < 200.0

    def test_am_only_seen_by_magnitude_track(self):
        results = self._run(rpm=3000, am_depth=0.05, fm_dev_hz=0.0)
        assert results["mag_hop32"].detected
        assert abs(results["mag_hop32"].spin_rpm - 3000.0) < 200.0

    def test_no_modulation_yields_no_detection(self):
        results = self._run(rpm=3000, am_depth=0.0, fm_dev_hz=0.0)
        assert not any(result.detected for result in results.values())

    def test_decel_chirp_alone_yields_no_detection(self):
        results = self._run(
            rpm=3000, am_depth=0.0, fm_dev_hz=0.0, decel_mph_per_s=90.0
        )
        assert not any(result.detected for result in results.values())

    def test_short_visibility_rejected(self):
        results = self._run(rpm=3000, visible_ms=15.0)
        assert not any(result.detected for result in results.values())
```

(`rpm` doubles as the seed in the grid test so each point uses independent noise.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_spin_ripple_estimator.py::TestRunRippleVariants -v`
Expected: FAIL with `AttributeError: ... has no attribute 'run_ripple_variants'`

- [ ] **Step 3: Implement `run_ripple_variants` (append to the module)**

```python
VARIANT_NAMES = ("freq_hop32", "mag_hop32", "freq_hop16", "mag_hop16")


def run_ripple_variants(
    i_samples,
    q_samples,
    ball_speed_mph: float,
    ball_timestamp_ms: float,
    *,
    expected_spin_rpm: Optional[float] = None,
    hops: tuple[int, ...] = (32, 16),
) -> dict[str, RippleSpinResult]:
    """Run all hop x track ripple variants for one capture."""
    results: dict[str, RippleSpinResult] = {}
    for hop in hops:
        track = extract_ripple_track(i_samples, q_samples, ball_speed_mph, hop)
        trimmed = trim_to_ball_window(track, ball_timestamp_ms, hop)
        track_rate_hz = SAMPLE_RATE / hop
        results[f"freq_hop{hop}"] = detect_ripple_spin(
            trimmed.freq_hz, track_rate_hz, expected_spin_rpm=expected_spin_rpm
        )
        results[f"mag_hop{hop}"] = detect_ripple_spin(
            trimmed.magnitude, track_rate_hz, expected_spin_rpm=expected_spin_rpm
        )
    return results
```

- [ ] **Step 4: Run the full estimator test file**

Run: `uv run pytest tests/test_spin_ripple_estimator.py tests/test_spin_synth.py -v`
Expected: PASS (22 tests). Tuning guidance if the synthetic grid fails:
- A 4096-sample capture with onset 8 ms gives a ~128 ms track → ripple-FFT natural resolution ~8 Hz (≈470 RPM between independent bins), but the interpolated zero-padded peak of a clean tone should land well inside ±200 RPM. If a grid point misses by more, first suspect the detrend (order-3 fit absorbing a low seam frequency on a short track) — print `result.peak_freq_hz` and the residual spectrum before touching constants.
- Do not weaken the production-mirrored gate constants to make tests pass; adjust the synthetic signal (amplitude, noise_rms, am_depth/fm_dev_hz) toward realistic-but-clean instead, and note any change in the commit message.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check scripts/analysis/spin_ripple_estimator.py tests/test_spin_ripple_estimator.py
git add scripts/analysis/spin_ripple_estimator.py tests/test_spin_ripple_estimator.py
git commit -m "feat(analysis): run hop/track ripple variant grid per capture"
```

---

### Task 7: Experiment CLI (`experiment_spin_ripple.py`)

**Files:**
- Create: `scripts/analysis/experiment_spin_ripple.py`
- Test: `tests/test_experiment_spin_ripple.py`

**Interfaces:**
- Consumes: `spin_experiment_lib` loaders (Task 1), `spin_ripple_estimator.run_ripple_variants`/`VARIANT_NAMES` (Task 6), `RollingBufferProcessor.process_capture`, `get_optimal_spin_for_ball_speed`, `compare_trackman.normalize_club`.
- Produces:
  - `build_row(shot_number, normalized_club, spin_tm, ball_speed_tm, expected_spin_rpm, processed, ripple_results) -> dict` — one wide CSV row
  - `summarize(rows: list[dict]) -> list[dict]` — one summary dict per method (`envelope` + 4 variants) with keys `method`, `shots`, `detected`, `coverage_pct`, `mae_rpm`, `within_300_pct`, `rescues`, `regressions`
  - CLI: `uv run python scripts/analysis/experiment_spin_ripple.py --openflight <jsonl> --comparison <csv> --output <csv>`

**Metric definitions (fixed):**
- Envelope "detected" = production `SpinResult` with `spin_rpm > 0` and neither rail flag (same rule as `experiment_spin_windows._result_is_reportable`).
- Variant "detected" = `RippleSpinResult.detected`.
- Error = method RPM − `spin_tm`; MAE and within-±300 computed over that method's detected shots only.
- Rescue (variants only) = envelope NOT detected AND variant detected AND |variant error| ≤ 500.
- Regression (variants only) = envelope detected with |envelope error| ≤ 300 AND (variant not detected OR |variant error| > 300).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_experiment_spin_ripple.py`:

```python
"""Tests for the ripple experiment's row building and summary metrics."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "analysis"))

from experiment_spin_ripple import summarize  # noqa: E402


def _row(spin_tm, env_rpm, env_detected, variant_rpm, variant_detected):
    row = {
        "shot_number": 1,
        "spin_tm": spin_tm,
        "env_rpm": env_rpm,
        "env_detected": env_detected,
    }
    for name in ("freq_hop32", "mag_hop32", "freq_hop16", "mag_hop16"):
        row[f"{name}_rpm"] = variant_rpm
        row[f"{name}_detected"] = variant_detected
    return row


def test_summarize_counts_rescue():
    # Envelope missed; variant within 500 RPM of TrackMan -> rescue.
    rows = [_row(spin_tm=3000.0, env_rpm=None, env_detected=False,
                 variant_rpm=3200.0, variant_detected=True)]
    summary = {entry["method"]: entry for entry in summarize(rows)}
    assert summary["freq_hop32"]["rescues"] == 1
    assert summary["freq_hop32"]["regressions"] == 0
    assert summary["envelope"]["detected"] == 0


def test_summarize_counts_regression_on_miss():
    # Envelope accurate; variant detected but off by >300 -> regression.
    rows = [_row(spin_tm=3000.0, env_rpm=3100.0, env_detected=True,
                 variant_rpm=3900.0, variant_detected=True)]
    summary = {entry["method"]: entry for entry in summarize(rows)}
    assert summary["freq_hop32"]["regressions"] == 1
    assert summary["freq_hop32"]["rescues"] == 0


def test_summarize_counts_regression_on_no_detect():
    # Envelope accurate; variant silent -> regression.
    rows = [_row(spin_tm=3000.0, env_rpm=3100.0, env_detected=True,
                 variant_rpm=None, variant_detected=False)]
    summary = {entry["method"]: entry for entry in summarize(rows)}
    assert summary["freq_hop32"]["regressions"] == 1


def test_summarize_accuracy_metrics():
    rows = [
        _row(spin_tm=3000.0, env_rpm=3100.0, env_detected=True,
             variant_rpm=3100.0, variant_detected=True),
        _row(spin_tm=5000.0, env_rpm=5400.0, env_detected=True,
             variant_rpm=4900.0, variant_detected=True),
    ]
    summary = {entry["method"]: entry for entry in summarize(rows)}
    assert summary["envelope"]["mae_rpm"] == 250.0
    assert summary["envelope"]["within_300_pct"] == 50.0
    assert summary["freq_hop32"]["mae_rpm"] == 100.0
    assert summary["freq_hop32"]["within_300_pct"] == 100.0
    assert summary["freq_hop32"]["coverage_pct"] == 100.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_experiment_spin_ripple.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'experiment_spin_ripple'`

- [ ] **Step 3: Implement `scripts/analysis/experiment_spin_ripple.py`**

```python
#!/usr/bin/env python3
"""Score speed-track ripple spin variants against TrackMan sessions.

Runs the production processor per capture for the baseline (ball speed +
envelope spin), then the 4 ripple variants (hop {32,16} x track
{frequency,magnitude}) from spin_ripple_estimator, and writes one wide CSV
row per TrackMan-matched shot plus a printed per-method summary.

Usage:
    uv run python scripts/analysis/experiment_spin_ripple.py \
        --openflight session_logs/session_20260605_132943_trackman.jsonl \
        --comparison session_logs/comparison_20260605_132943_trackman.csv \
        --output session_logs/spin_ripple_experiment_20260605.csv
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import compare_trackman as ct  # noqa: E402
from spin_experiment_lib import (  # noqa: E402
    capture_from_entry,
    club_enum,
    load_session_entries,
    load_trackman_by_shot,
    to_int,
)
from spin_ripple_estimator import (  # noqa: E402
    VARIANT_NAMES,
    RippleSpinResult,
    run_ripple_variants,
)

from openflight.rolling_buffer.monitor import get_optimal_spin_for_ball_speed  # noqa: E402
from openflight.rolling_buffer.processor import RollingBufferProcessor  # noqa: E402
from openflight.rolling_buffer.types import SpinResult  # noqa: E402

RESCUE_TOLERANCE_RPM = 500.0
ACCURATE_TOLERANCE_RPM = 300.0
METHODS = ("envelope",) + VARIANT_NAMES


def _envelope_detected(spin: Optional[SpinResult]) -> bool:
    return bool(
        spin is not None
        and spin.spin_rpm > 0
        and not spin.at_lower_rail
        and not spin.at_upper_rail
    )


def build_row(
    shot_number: Optional[int],
    normalized_club: str,
    spin_tm: float,
    ball_speed_tm: Optional[float],
    expected_spin_rpm: float,
    processed,
    ripple_results: dict[str, RippleSpinResult],
) -> dict[str, Any]:
    spin = processed.spin
    env_detected = _envelope_detected(spin)
    row: dict[str, Any] = {
        "shot_number": shot_number,
        "club": normalized_club,
        "ball_speed_of": round(processed.ball_speed_mph, 3),
        "ball_speed_tm": ball_speed_tm,
        "spin_tm": spin_tm,
        "expected_spin_rpm": round(expected_spin_rpm),
        "env_rpm": round(spin.spin_rpm) if env_detected else None,
        "env_detected": env_detected,
        "env_confidence": spin.confidence if spin is not None else None,
        "env_rejection_reason": spin.rejection_reason if spin is not None else "no result",
        "env_error_rpm": round(spin.spin_rpm - spin_tm, 1) if env_detected else None,
    }
    for name in VARIANT_NAMES:
        result = ripple_results[name]
        row[f"{name}_rpm"] = round(result.spin_rpm) if result.detected else None
        row[f"{name}_detected"] = result.detected
        row[f"{name}_snr"] = result.snr
        row[f"{name}_persistent"] = result.persistent
        row[f"{name}_n_windows"] = result.n_windows
        row[f"{name}_rejection_reason"] = result.rejection_reason
        row[f"{name}_error_rpm"] = (
            round(result.spin_rpm - spin_tm, 1) if result.detected else None
        )
    return row


def _method_error(row: dict[str, Any], method: str) -> Optional[float]:
    prefix = "env" if method == "envelope" else method
    if not row[f"{prefix}_detected"] or row[f"{prefix}_rpm"] is None:
        return None
    return float(row[f"{prefix}_rpm"]) - float(row["spin_tm"])


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    total = len(rows)
    for method in METHODS:
        errors = []
        rescues = 0
        regressions = 0
        for row in rows:
            error = _method_error(row, method)
            env_error = _method_error(row, "envelope")
            if error is not None:
                errors.append(error)
            if method == "envelope":
                continue
            if env_error is None and error is not None and abs(error) <= RESCUE_TOLERANCE_RPM:
                rescues += 1
            if env_error is not None and abs(env_error) <= ACCURATE_TOLERANCE_RPM:
                if error is None or abs(error) > ACCURATE_TOLERANCE_RPM:
                    regressions += 1
        detected = len(errors)
        within = sum(1 for error in errors if abs(error) <= ACCURATE_TOLERANCE_RPM)
        summary.append({
            "method": method,
            "shots": total,
            "detected": detected,
            "coverage_pct": round(100 * detected / total, 1) if total else None,
            "mae_rpm": round(statistics.mean(abs(e) for e in errors), 1) if errors else None,
            "within_300_pct": round(100 * within / detected, 1) if detected else None,
            "rescues": rescues if method != "envelope" else None,
            "regressions": regressions if method != "envelope" else None,
        })
    return summary


def _print_summary(summary: list[dict[str, Any]]) -> None:
    header = (
        f"{'method':<14} {'n':>3} {'cov%':>6} {'MAE':>7} "
        f"{'<=300%':>7} {'rescue':>7} {'regress':>8}"
    )
    print(header)
    for entry in summary:
        def fmt(value):
            return "-" if value is None else value
        print(
            f"{entry['method']:<14} {entry['detected']:>3} {fmt(entry['coverage_pct']):>6} "
            f"{fmt(entry['mae_rpm']):>7} {fmt(entry['within_300_pct']):>7} "
            f"{fmt(entry['rescues']):>7} {fmt(entry['regressions']):>8}"
        )


def _rows(
    shots: list[dict],
    captures: list[dict],
    trackman_by_shot: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    processor = RollingBufferProcessor()
    rows = []
    for shot_entry, capture_entry in zip(shots, captures):
        shot_data = shot_entry.get("data", shot_entry)
        shot_number = to_int(shot_data.get("shot_number"))
        trackman = trackman_by_shot.get(shot_number or -1, {})
        if trackman.get("match_quality") != "good" or trackman.get("spin_tm") is None:
            continue

        normalized_club = ct.normalize_club(shot_data.get("club"))
        club = club_enum(normalized_club)
        capture = capture_from_entry(capture_entry)
        processed = processor.process_capture(
            capture,
            expected_spin_for_ball_speed=lambda ball_speed, club=club: (
                get_optimal_spin_for_ball_speed(ball_speed, club)
            ),
        )
        if not processed:
            continue

        expected_spin = get_optimal_spin_for_ball_speed(processed.ball_speed_mph, club)
        ripple_results = run_ripple_variants(
            capture.i_samples,
            capture.q_samples,
            processed.ball_speed_mph,
            processed.ball_timestamp_ms,
            expected_spin_rpm=expected_spin,
        )
        rows.append(build_row(
            shot_number,
            normalized_club,
            trackman["spin_tm"],
            trackman.get("ball_speed_tm"),
            expected_spin,
            processed,
            ripple_results,
        ))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openflight", required=True, type=Path)
    parser.add_argument("--comparison", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    shots, captures = load_session_entries(args.openflight)
    trackman_by_shot = load_trackman_by_shot(args.comparison)
    rows = _rows(shots, captures, trackman_by_shot)
    if not rows:
        print("No TrackMan-matched shots with captures found; nothing to score.")
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    _print_summary(summarize(rows))
    print(f"Wrote {args.output} ({len(rows)} shots)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_experiment_spin_ripple.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Full suite + lint, commit**

```bash
uv run pytest tests/ -q
uv run ruff check scripts/analysis/experiment_spin_ripple.py tests/test_experiment_spin_ripple.py
git add scripts/analysis/experiment_spin_ripple.py tests/test_experiment_spin_ripple.py
git commit -m "feat(analysis): ripple spin experiment CLI with rescue/regression scoring"
```

---

### Task 8: Run the experiment on TrackMan sessions and record findings

**Files:**
- Create: `session_logs/spin_ripple_experiment_20260605.csv` (script output)
- Create: `docs/spin-ripple-experiment-findings.md`

**Interfaces:**
- Consumes: the Task 7 CLI.
- Produces: the go/no-go evidence for production integration.

- [ ] **Step 1: Run on the primary TrackMan pairing**

```bash
uv run python scripts/analysis/experiment_spin_ripple.py \
  --openflight session_logs/session_20260605_132943_trackman.jsonl \
  --comparison session_logs/comparison_20260605_132943_trackman.csv \
  --output session_logs/spin_ripple_experiment_20260605.csv
```

Expected: summary table printed, CSV written with >0 shots. (If this pairing yielded no matches in Task 1 Step 1, use the pairing that worked there.)

- [ ] **Step 2: Run on additional TrackMan sessions**

The other `session_logs/session_2026*_trackman.jsonl` files may pair with `comparison_20260506.csv` or the test2 CSVs. For each remaining `*_trackman.jsonl`, try the comparison CSV whose date or name matches; keep any run that reports >0 shots and write its output to `session_logs/spin_ripple_experiment_<session-date>.csv`. Record which pairings produced no matches instead of silently skipping them.

- [ ] **Step 3: Write up findings**

Create `docs/spin-ripple-experiment-findings.md` with: the summary tables per session, the per-method totals across sessions, notable per-shot cases (biggest rescues, any regressions, harmonic errors), and a short recommendation: which variant (if any) merits production integration, per the spec's bar — adds rescues without adding regressions, or beats the envelope method outright. Include exact re-run commands.

- [ ] **Step 4: Commit results**

```bash
git add session_logs/spin_ripple_experiment_*.csv docs/spin-ripple-experiment-findings.md
git commit -m "docs(spin): speed-track ripple experiment results vs TrackMan"
```

---

## Self-Review Notes

- **Spec coverage:** estimator chain (Tasks 3–6), lib extraction + refactor diff (Task 1), CLI + metrics (Task 7), synthetic tests incl. AM/FM/chirp/short-visibility/prior (Tasks 2, 5, 6), loader tests on real session fixture (Task 1), TrackMan validation runs (Task 8). Rail guards and persistence mirror production; the spec's "SNR gate … mirror production" is implemented with the global-median floor (the local-floor refinement is envelope-FFT-specific red-noise armor; the track FFT operates on far fewer points — noted as a follow-up if real-data false positives appear).
- **Type consistency:** `RippleTrack`/`RippleSpinResult`/`run_ripple_variants`/`VARIANT_NAMES` names match across Tasks 3–7; lib function names match between Task 1 and Task 7.
- **Known judgment calls:** window-center timestamps (vs production's window-start) for track times; parabolic interpolation clipped to ±0.5 bin; `MIN_TRACK_DURATION_MS=20` mirrors `SPIN_MIN_SAMPLES`.
