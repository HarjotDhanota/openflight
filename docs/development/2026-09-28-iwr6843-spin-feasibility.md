# IWR6843 Channel-Aware Spin Feasibility Study

Status: deferred offline research; no production implementation authorized

Date: 2026-09-28

Related milestone: fusion master plan M4

## Research question

Can OpenFlight use IWR6843 complex micro-Doppler together with calibrated
phase relationships across RX channels and TDM TX/RX virtual elements to:

1. improve total-spin coverage and accuracy;
2. resolve the existing 1×/2× harmonic ambiguity in OPS spin candidates; and
3. determine whether any independently testable spin-axis information is
   present?

The first two questions are plausible. The third is substantially less
certain and must remain a separate feasibility result.

## Why the current capture is worth testing

The checked-in IWR6843 profiles retain complex range-bin IQ for 3 TX × 4 RX,
12 TDM loops per frame and 72 ms of flight. The dense profile supplies 36
frames at 2 ms spacing. Unlike a power-only detection stream, this preserves
both amplitude and phase at every retained physical channel.

At 2,000 RPM, a 72 ms window contains about 2.4 rotations. At 6,000 RPM it
contains about 7.2. High-spin shots therefore have a credible periodic
observation, while low driver spin will be the difficult boundary and cannot
be judged from nominal Nyquist rate alone.

The OPS dechirped-sideband replay already shows that complex translation-
corrected radar returns can contain accurate spin information when the comb
locks. Its principal observed problems are low coverage and 1×/2× harmonic
selection. The IWR array may provide independent channel-consistency evidence
for those decisions.

## Measurement model and limitations

A rotating ball's dimples, seam, logo and other scattering asymmetries can
periodically modulate the complex return. After removing the ball's bulk
translation, the residual may contain micro-Doppler sidebands at the spin
frequency or one of its harmonics.

RX and TX/RX phase differences are not direct spin measurements:

- simultaneous RX phase primarily encodes arrival direction, element
  calibration, scattering-centroid position and multipath;
- different TX blocks are sampled at different times, so radial motion adds a
  deterministic TDM phase that must be removed before comparison;
- range migration can move energy between retained bins and create apparent
  amplitude/phase modulation;
- the virtual elements share one small monostatic aperture and are not twelve
  widely separated views;
- a nearly spherical, unmarked ball can produce symmetric scattering that
  hides the fundamental and leaves an integer harmonic ambiguity.

After range alignment, translation removal, TDM correction and array
calibration, residual cross-channel periodicity may still be valuable for
rejecting noise and multipath or selecting between harmonic candidates. It
does not guarantee recovery of a full 3D spin vector.

## Required offline pipeline

1. Decode the retained cube without coherently summing TX/RX channels.
2. Preserve exact frame and chirp timestamps and the physical TX order.
3. Follow the fitted ball range through neighboring bins, using a declared
   interpolation or coherent range-alignment method.
4. Remove the fitted bulk range phase and radial Doppler independently for
   each physical channel.
5. Apply the measured element gain/phase calibration.
6. Correct each TX block for its actual TDM offset using the instantaneous
   radial-velocity model. Preserve correction residuals and rejected samples.
7. Form per-channel residual complex slow-time signals using the real,
   potentially bursty timestamps. Do not pretend they are uniformly sampled
   unless they are explicitly resampled with recorded assumptions.
8. Search a common harmonic comb while retaining both fundamental and harmonic
   candidates.

## Mandatory estimator ablation

Score these estimators on exactly the same truth-paired shots:

1. Coherently summed-channel micro-Doppler comb.
2. Independent-channel spectra with robust score fusion.
3. Simultaneous RX-pair cross-spectrum and coherence.
4. TDM-corrected TX-pair residual phase and coherence.
5. Combined multichannel estimator using only features that improved the
   development set for an explainable physical reason.

The RX/TX phase proposal succeeds only if the ablation shows incremental
frozen-holdout value beyond the simpler summed or independently scored channel
spectra. Do not retain a multichannel solver merely because it produces a
plausible plot.

## Relationship to OPS spin

Keep OPS and IWR estimates independent through initial scoring. Compare:

- OPS candidate set and confidence;
- IWR candidate set and channel-consistency evidence;
- agreement on the fundamental versus 2× interpretation;
- correlated failures by club, speed, ball, environment and capture quality.

The likely first useful fusion is candidate validation rather than averaging:
use IWR evidence to accept or reject an OPS harmonic interpretation only after
truth data proves that rule. Do not substitute club-typical spin as measurement
evidence.

## Truth and dataset requirements

- Contemporaneous reference-monitor total spin for every scored shot.
- Independent spin-axis truth before scoring any axis estimate.
- Both IQ16 wide/default and IQ8 dense captures where practical.
- Driver through wedge coverage, including low-spin driver shots.
- Multiple balls and orientations, including plain and marked balls if both are
  intended to be supported.
- Multiple backgrounds and placements that exercise multipath.
- Retained failures, clipping, dropped frames and incomplete tracks.
- Separate development and untouched holdout sessions, preferably on different
  days and with at least one changed environment.

Launch direction minus club path is not independent spin-axis truth.

## Failure matrix

The experiment must explicitly measure:

- 0.5× and 2× harmonic locks;
- low-score noise locks;
- insufficient observed rotations;
- range-bin migration and interpolation sensitivity;
- phase-calibration drift;
- TDM correction error;
- multipath and wall/floor image contamination;
- IQ8 quantization versus IQ16;
- channel dropout or disagreement;
- sensitivity to ball logo, seam and orientation;
- estimator latency and memory cost on the Pi after offline feasibility passes.

## Proposed feasibility gate

Freeze the exact gate before viewing the holdout. The initial proposal is:

- at least 80% total-spin coverage inside the declared operating envelope;
- median relative total-spin error no greater than 5%;
- P90 relative total-spin error no greater than 10%;
- zero confidently emitted 0.5×/2× harmonic mistakes on the holdout;
- every low-confidence or inconsistent observation produces a no-read rather
  than a model-based numeric replacement;
- the channel-aware estimator must outperform the simplest summed-channel
  baseline on frozen holdout coverage or error without increasing confident
  catastrophic errors.

These are proposed research gates, not current results. Revise them only before
the holdout is frozen and record the reason.

Do not define a spin-axis acceptance gate until development data demonstrates
that axis information is identifiable from this geometry. If it is not, record
that result and compare a second view, optical marking or controlled
illumination rather than tuning an underdetermined solver.

## Deliverables

- A versioned offline replay tool that never changes production output.
- Hash-bound per-shot input and estimator identities.
- Per-estimator candidates, confidence, rejection reasons and channel
  diagnostics.
- Development and holdout score tables with coverage and failure categories.
- Plots for representative successes, no-reads and harmonic failures.
- A written decision to promote, retain as experimental, change hardware, or
  stop the approach.

No production fusion or UI metric should be added until this study passes its
independent holdout and the master plan records the promotion decision.
