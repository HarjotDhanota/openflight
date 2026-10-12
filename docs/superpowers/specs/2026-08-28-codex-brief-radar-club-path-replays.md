# Codex brief — radar club path / attack angle: two replayable fixes on the 22 existing dumps

**Branch:** `feat/clubpose-fit` (or a fresh branch off it — this touches `iwr6843/`, not the
camera). Session export: `~/Downloads/openflight_session_20260825_181734_filtered/…` — each
`shots/<shot>/iwr6843_*.l3dump` plus `shots.csv` (`club_speed_mph`, `iwr_measurement_impact_t_s`,
`iwr_club_path_*` as shipped). Exclude nothing; all 22 have radar.

**Question:** the radar-only club path is `rejected_phase_span` on 22/22 and the attack-angle
candidate is pinned at −25…−37° on every shot. Two concrete causes were found by reading the
code (`docs/Personal Research/research-2026-08-28/F_radar_club_path.md` on
`feat/silhouette-poc`, verified in source). Do they explain the data, and does fixing them
bring radar-only path/attack into the physical band?

## The two mechanisms (verified in source; do not re-derive)

1. **Per-burst MTI notch on the horizontal path.** `club.py:750-751` re-parses the 3-TX cube and
   subtracts the per-frame mean over loops (`rfft.mean(axis=1)`) — the `scope="burst"` mode that
   `tracking.mti_filter`'s docstring (`tracking.py:156-163`) says notches a mover whose radial
   speed is near `n × λ/(2·T_loop)`. With λ = 4.835 mm and `T_loop` = 135 µs (3 × 45 µs,
   `doa.py:31-32`, `shot.py:24`) the notches are 17.9, **35.8**, 53.7 m/s; this session's club
   radial speeds are 30.6–36.6 m/s. `mti_filter(scope="window")` exists for exactly this reason
   (the 2026-07-14 5-iron notch).
2. **Attack-angle fit anchored at the ball's tee height.** `estimate_attack_angle_candidate`
   (`club.py:453-519`) passes the club's height points into `trajectory.fit_tee` (`club.py:506`),
   which forces the line through `cal.tee_anchor_h_m` = the **ball's** resting height
   (`calibration.py:47-49`), over a 4-frame baseline. A near-constant offset between the
   clubhead's true height and the ball's, divided by a short baseline, is a large,
   swing-invariant slope — the observed signature.

   Also established: TDM radial motion IS compensated (`doa.py:314-319`) from the track's
   quadratic-refit speed; `dθ/dv` = 0.117 rad per m/s (45 µs hop), 0.234 (90 µs). A 1–2 m/s
   refit drift is a plausible secondary contributor, not the cause. The "2-TX hardcode" worry is
   a false lead (`project_tx_pair(raw,(0,2))` feeds `mti_filter` consistently).

## Replays (instrument, do not ship yet)

A replay script `scripts/analysis/iwr6843_club_path_replay.py` (`--session`, `--out`) that runs
`estimate_club_path` on all 22 dumps under four arms and writes one CSV row per shot per arm:

| arm | change |
|---|---|
| A0 | shipped code, unchanged (must reproduce `shots.csv` `iwr_club_path_*` to the printed precision — this is the control; if it does not, stop and say so) |
| A1 | horizontal-path static removal switched to window scope (mean over all loops of all pre-impact frames in the window), everything else unchanged |
| A2 | A1 + TDM de-rotation velocity from the linear `track.speed_ms` instead of the quadratic refit; and separately from an OPS-anchored radial speed (`club_speed_mph` × the shot's `track_speed_ratio`) — report both |
| A3 | attack-angle fit anchored at the club's own impact range/height from `ClubRangeEvidence` (or `fit_free` on the same points as a second reference), instead of `fit_tee` |

Per shot per arm record: `phase_span_rad`, `fit_residual_deg`, `candidate_path_deg`,
`candidate_attack_angle_deg`, status, and the camera-fused values from `shots.csv`
(`experimental_fused_club_path_deg`, `experimental_fused_attack_angle_deg`) for comparison.

## Pre-registered reads (decide before running)

- A1 vs A0: `phase_span_rad` should fall toward the ~1.3 rad physical ceiling on most shots if
  the notch is the cause; if it does not move, the notch is not dominant.
- A2 vs A1: if span shrinks materially with a steadier velocity source, refit drift matters.
- A3 vs A0: attack angle should leave the −25…−37° cluster; compare to the camera-fused
  −2.6…−6.6° band (not truth, but an independent estimator).
- Path: compare accepted radar-only path to the camera-fused path per shot; report the
  paired difference distribution. Neither is truth.
- Plots: `phase_span_rad` per shot per arm; attack angle per shot per arm with the fused value
  overlaid. Look at them before writing numbers.

## Deliverables

Replay script + CSV + plots (out dir, not committed); a results section in
`docs/iwr6843/` (or the club-path design doc) with the four-arm table and a one-line verdict
per mechanism; **no production change** in this pass — if A1/A3 clearly help, propose the
change as its own follow-up with tests, and note the `scope="window"` interaction with
`Geometry.range_bin_starts`. Lint clean; conventional commits; no AI attribution lines.
