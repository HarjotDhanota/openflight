# Spin from Speed-Track Ripple — Offline Experiment Design

**Date:** 2026-07-24
**Branch:** `feat/spin-experiments`
**Status:** Approved design, pre-implementation

## Background

Production spin detection (`RollingBufferProcessor.detect_spin`) works on the
amplitude envelope of the ball's bandpass-filtered Doppler signal: the seam
modulates the return amplitude once per revolution, and an FFT of the detrended
envelope recovers the seam tone.

The omnipresence team suggested a complementary method: run the main speed FFT
with overlapped 128-sample windows (hop 32 = 1 ms, or 16 = 0.5 ms instead of
the sequential 128 = 4 ms) and exploit the *ripple in the resulting speed
reports*, which becomes more pronounced at smaller hops. That ripple is the
frequency-side (FM) signature of the same seam rotation.

The processor already computes an overlapped speed timeline
(`process_overlapping`, hop 32) but uses it only for impact timestamps and
club speed — not spin.

## Goal and success criteria

Build the ripple-based estimator **offline only**, validate it against the
TrackMan-paired sessions in `session_logs/`, and decide on production
integration from the numbers. Production behavior is untouched by this work.

Success is measured on **accuracy and coverage** against TrackMan:

- **Coverage:** % of TrackMan-matched shots producing a gated estimate.
- **Accuracy:** median absolute RPM error and % of detections within ±300 RPM.
- **Rescues:** shots where the production envelope method returns no confident
  spin but the ripple variant lands within ±500 RPM of TrackMan.
- **Regressions:** shots where the envelope method is within ±300 RPM but the
  variant misses by more than that.

A variant justifies productionizing if it adds rescues without adding
regressions (or beats the envelope method outright on accuracy at equal
coverage).

## Estimator signal chain

Input: one `IQCapture` (4096 I/Q samples @ 30 kHz) plus the
production-detected (mode-based) ball speed.

1. **STFT.** Hanning-windowed 128-sample blocks, zero-padded to 4096-point
   FFTs, at hop `h ∈ {32, 16}` (sweep parameter). The estimator does **not**
   reuse `process_overlapping`: the production timeline applies CFAR/threshold
   gates and drops sub-threshold windows, punching holes in the track. The
   estimator instead takes the peak within a ±8 mph tolerance band around the
   expected ball frequency in *every* window (outbound side only, no detection
   gates), so the track is continuous.
2. **Two tracks per shot.** (a) Interpolated peak frequency — the FM ripple;
   (b) peak magnitude — the AM ripple, nearly free from the same STFT.
3. **Ball visibility window.** From impact until the ball signal collapses,
   using the production magnitude-collapse criterion
   (`SPIN_SIGNAL_LOSS_THRESHOLD` logic) and the production minimum
   duration / minimum seam-cycle gates.
4. **Detrend.** Polynomial detrend of each track at the production order
   (`SPIN_DETREND_ORDER`): removes the deceleration chirp from the frequency
   track and range falloff from the magnitude track, leaving the ripple.
5. **Ripple FFT.** Zero-padded FFT of each detrended track (track sample rate
   = 30000 / h). Seam band limits (`SPIN_MIN_SEAM_HZ` … physical max RPM),
   SNR gate, and split-half persistence check all mirror the production
   `detect_spin` gates so the comparison is apples-to-apples.
   RPM = peak Hz × 60. Optional expected-spin prior for harmonic
   disambiguation, same as production.

Sweep grid: hop {32, 16} × track {frequency, magnitude} = **4 variants per
shot**.

Physics note: hop size does not improve the ripple FFT's frequency resolution
(set by ball visibility duration); smaller hop gives more track samples, which
lowers noise and strengthens the persistence check. The sweep lets the data
say whether 0.5 ms buys anything.

## Code structure

- `scripts/analysis/spin_ripple_estimator.py` — the estimator as pure
  functions (numpy in → result dataclass out), importable by the experiment
  script and by tests. Nothing in `src/` changes.
- `scripts/analysis/spin_experiment_lib.py` — session-loading,
  TrackMan-matching, and club-normalization helpers extracted from
  `experiment_spin_windows.py`; that script is refactored to import them with
  behavior unchanged. (Second consumer justifies the extraction.)
- `scripts/analysis/experiment_spin_ripple.py` — CLI mirroring
  `experiment_spin_windows.py`:
  `--openflight <session.jsonl> --comparison <trackman.csv> --output <csv>`.
  Per capture it runs the production processor for the baseline (ball speed +
  envelope spin), then all four ripple variants.

## Outputs

One wide CSV row per shot: shot number, club, TrackMan RPM, ball speed;
envelope baseline (RPM, confidence, no-detect reason); per ripple variant
(hop × track): RPM, ripple-FFT SNR, persistence pass/fail, track length in
windows.

Printed summary table per variant plus the envelope baseline: coverage,
median absolute error, % within ±300 RPM, rescues, regressions.

## Testing

- `tests/test_spin_ripple_estimator.py` — synthetic I/Q generator producing an
  outbound Doppler tone with configurable ball speed, linear deceleration,
  seam AM depth, seam FM deviation, RPM, and noise floor. Tests:
  - RPM recovered within tolerance across ~2,000–10,000 RPM (driver through
    wedge) for both hops; AM-only, FM-only, and combined modulation.
  - Deceleration chirp alone (no seam modulation) → rejected, not misread as
    spin.
  - Pure unmodulated tone → no detection; too-short ball visibility → gated
    out.
  - Harmonic disambiguation with the expected-spin prior.
- `tests/test_spin_experiment_lib.py` — loader tests against a real session
  fixture from `session_logs/` (in-repo): session-entry parsing and TrackMan
  shot matching. Locks in the `experiment_spin_windows.py` refactor.
- Refactor check: run `experiment_spin_windows.py` on a TrackMan session
  before and after the lib extraction and diff the output CSVs — proves
  behavior unchanged.

## Validation data

TrackMan-paired sessions with raw `rolling_buffer_capture` entries in
`session_logs/`, e.g. `session_20260527_*_trackman.jsonl` and
`session_20260605_132943_trackman.jsonl`, with comparison CSVs such as
`comparison_20260605_132943_trackman.csv`.

## Out of scope

- Any change to `src/openflight/` or live spin behavior.
- Fusing envelope + ripple estimates (a follow-up if the numbers justify it).
- New data capture; the experiment runs on existing sessions.
