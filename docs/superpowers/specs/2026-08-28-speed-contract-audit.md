# Speed Contract Audit: Is Smash Factor Derivable Today?

Audited on branch `chore/speed-contract-check` from `feat/clubpose-fit` @ 96dac75.

Claims are tagged **CONFIRMED** (traced to `file:line`, or measured on the
2026-08-25 session) or **INFERRED** (reasoned, not measured).

## Question

Smash factor is `ball_speed_mph / club_speed_mph`. The OPS243 is a Doppler
radar: it measures the RADIAL (line-of-sight) component only. If one of the two
speeds is deprojected to TOTAL and the other is not, the ratio carries an
angle-dependent systematic error. A fitter acts on smash-factor differences of
0.02-0.04, so any contract mismatch larger than that makes the metric
misleading rather than merely noisy.

## 1. Contract trace

### Smash factor is already shipped — CONFIRMED

It is not a new metric. It exists end to end:

- `src/openflight/launch_monitor.py:349-362` — `Shot.smash_factor` property,
  `self.ball_speed_mph / self.club_speed_mph`, no correction of either input.
- `src/openflight/server.py:1132` — emitted in `shot_to_dict` as
  `"smash_factor"`, rounded to 2 dp, so it reaches the UI and GSPro payloads.
- `src/openflight/session_logger.py:327,435` — persisted per shot.
- `src/openflight/rolling_buffer/types.py:370` — parallel property on the
  rolling-buffer shot type.
- It is column 7 of the 2026-08-25 session `shots.csv`.

`scripts/analysis/compare_trackman.py:517-520` already computes `smash_delta`
against TrackMan and applies no correction to either speed — it compares the
values exactly as logged.

### `ball_speed_mph` = TOTAL speed (deprojected) — CONFIRMED

- `src/openflight/rolling_buffer/processor.py:1827` — `_find_consistent_ball_speed`
  returns the raw OPS Doppler mode, i.e. RADIAL.
- `src/openflight/server.py:3747-3755` — `_finalize_shot_detected` overwrites
  `shot.ball_speed_mph` with `correct_ball_speed(...)`, preserving the radial
  value in `shot.ball_speed_raw_mph`.
- `src/openflight/speed_correction.py:66-77` — divides by
  `radial_speed_factor`, the maximum of `v(t)·cos(launch − LOS_elevation(t))`
  over a 70 ms window.

Note that `radial_speed_factor` (`speed_correction.py:52-64`) projects onto the
geometric **line of sight**, computed as `atan2(y, x)` from the radar to the
ball — it contains no antenna-tilt or boresight term. That is correct physics:
Doppler shift is `−(2/λ)·dR/dt`, which depends only on the LOS. Mount tilt
changes antenna gain, not the projection factor. **The premise that a 10.4°
radar tilt or a "25° off boresight" approach implies a 0.906 projection factor
is wrong** — boresight is not the projection axis.

The correction was live during the 2026-08-25 session — CONFIRMED from
`source_logs/kiosk_20260825_181725.log`, which logs 21 lines of the form
`Ball speed cosine correction: 100.3 -> 102.8 mph (LA 16.8)`.

Geometry (recovered and verified): `iwr6843_tee_m` default 1.575 m
(`server.py:5233`), `radar_height_m` default 0.152 m
(`iwr6843/calibration.py:44`), `tee_ball_height_m` default 0.040 m
(`calibration.py:38`), wired at `server.py:5695-5697`. Replaying
`correct_ball_speed` with `D = 5.167 ft`, `H = −0.3675 ft` reproduces all 21
logged pairs to within ±0.11 mph — the residual is the log's 0.1° rounding of
the launch angle. **CONFIRMED.**

The deprojection uses the **shipped user-facing vertical launch angle**
(`shot.launch_angle_vertical`), applied after `_ensure_user_facing_launch_angles`
(`server.py:3745`). On this session that angle is the IWR6843 LCMF-v1 estimate.
Applied factors ranged 0.9631-0.9802, median **0.9722**.

### `club_speed_mph` = RADIAL, uncorrected — CONFIRMED

- `src/openflight/rolling_buffer/processor.py:1860` — set from
  `find_club_speed(...)`, which picks a robust percentile of the pre-impact
  upper Doppler branch (`processor.py:1470-1560`).
- `processor.py:1923` — carried into `ProcessedCapture.club_speed_mph`.
- `src/openflight/rolling_buffer/monitor.py:877,906` — copied onto the `Shot`.
- No deprojection is applied anywhere: `grep -n "cos(\|deproject\|radial\|
  boresight\|tilt" src/openflight/rolling_buffer/processor.py` returns **no
  matches**, and `correct_ball_speed` is referenced only at `server.py:3750`.

So the club speed is a raw OPS Doppler reading — a pure LOS projection of the
clubhead's velocity.

### The IWR ball speed never overrides the shipped value — CONFIRMED

`iwr_ball_speed_source` is the literal string `"ops243"`, hardcoded at
`src/openflight/session_logger.py:673`. It is a provenance label recording that
the IWR pipeline was *fed* the OPS speed, not a selector. `ShotMeasurement.
ball_speed_mph` (`iwr6843/shot.py:169-172`, docstring "raw radial") is the
IWR's own range-walk speed and is used only inside the IWR estimator. There is
no `iwr_ball_speed_source` branch in `src/`. The shipped `ball_speed_mph` is
always OPS-derived.

### Why `track_speed_ratio` does NOT show that OPS is "more than radial"

`iwr_club_path_track_speed_ratio` is defined at
`src/openflight/iwr6843/club.py:390` as

```
speed_ratio = abs(track.speed_ms) / max(ops_speed_ms, 1e-6)
```

i.e. **IWR range-rate / OPS club speed**. On the 21 shots it runs 0.833-0.958,
median **0.910**. The code comment at `club.py:103-104` reads that gap as a
projection: *"IWR sees radial speed while OPS estimates total club speed."*

That explanation does not survive the geometry. The IWR6843 and the OPS243 are
treated as co-located — the shipped ball correction takes its geometry straight
from the IWR calibration (`server.py:5695-5697`) — so both sensors project onto
essentially the same LOS. Two co-located Doppler sensors cannot legitimately
differ by 9% because of projection. The gap is a sensor-to-sensor measurement
difference, not a contract difference.

The direction of the error is settled by plausibility (**CONFIRMED**, measured):

| club | n | ball (med) | OPS club | smash from OPS | IWR range-rate | smash from IWR |
|---|---|---|---|---|---|---|
| 7-iron | 8 | 113.1 | 86.3 | **1.305** | 76.4 | 1.478 |
| 9-iron | 13 | 99.8 | 79.8 | **1.263** | 72.7 | 1.355 |

A 7-iron smash factor of 1.478 is above the physical ceiling (~1.38 — the COR
limit is a driver number and irons sit well below it). The **IWR range-rate
under-reads the clubhead**, and it is not a valid reference for the OPS. The
OPS-derived smash values are plausible for an amateur.

Likely causes of the IWR deficit, **INFERRED**: the range-walk RANSAC fits a
slope over 6 pre-impact frames (`club.py:PRE_IMPACT_FRAMES`, 12-24 ms) against
4.7 cm range bins and tracks a centroid-like scattering centre, while the OPS
Doppler FFT picks the fastest scatterer on the club. Both bias the ratio below 1.

Recommendation: correct the misattribution in the `club.py:100-105` comment.
The gate value (0.82) is empirical and should not change.

## 2. Numbers on the session

21 shots (22 included in the filtered export, minus `shot_001`).

### Assumed club geometry

Radar at the origin, 0.152 m above the mat, on the target line. Ball at
horizontal distance 1.5710 m (from the 1.575 m slant range and the 0.112 m
height difference), 0.040 m up. The clubhead travels a straight line through
the ball at the shot's own fused attack angle (vertical) and fused club path
(horizontal). Its position is solved from the measured slant range
`iwr_club_path_club_range_m` (1.19-1.29 m on this session). The projection
factor is `cos` of the angle between that velocity vector and the radar-to-club
LOS.

Where `experimental_fused_*` is missing or physically impossible (shots 20 and
25, reporting attack angles of −30.98° and +25.23°), the session median
(attack −4.58°, path +4.80°) is substituted.

### Result

| quantity | median | range |
|---|---|---|
| ball projection factor (applied) | 0.9722 | 0.9631-0.9802 |
| club projection factor (NOT applied) | 0.9912 | 0.9332-0.9980 (0.7579 incl. bad-fusion shots) |
| smash as-is (shipped) | **1.2839** | 1.159-1.354 |
| smash both-total (consistent contract) | **1.2773** | — |
| correction (both-total − as-is) | **−0.0107** | −0.0025 to −0.0788 |

**The as-is smash factor is NOT biased by more than 0.02.** The shipped bias is
**+0.009**, under half the 0.02 resolution threshold.

### Why the club needs almost no correction

This is a geometric coincidence, and it is worth stating precisely because it
is the whole reason the mismatched contracts do not matter. At a club range of
1.25 m the radar-to-club LOS is depressed about **−3.95°** below horizontal
(the club is 0.086 m below the antenna at 1.25 m out). The clubhead descends at
its attack angle, about **−4.6°**. The two nearly cancel:

| attack angle | club path | cos | Δ smash |
|---|---|---|---|
| −4.5° | 0° | 1.0000 | −0.0001 |
| −2.0° | 0° | 0.9990 | −0.0013 |
| −7.0° | 0° | 0.9979 | −0.0027 |
| −4.5° | ±5° | 0.9939 | −0.0078 |
| −4.5° | ±10° | 0.9759 | −0.0310 |

The attack angle contributes essentially nothing over its whole plausible
range. What residual exists comes almost entirely from **club path versus LOS
azimuth**, and it is second order — ±5° of path costs 0.008, and it takes ±10°
to reach 0.031. This confirms the claim in the `speed_correction.py:29-31`
docstring ("Club speed does NOT need this correction — the club head's delivery
is nearly parallel to the LOS") and supplies the number behind it.

Sensitivity to the things we do not know precisely:

- Radar height ±0.05 m: Δ smash changes by less than 0.002. Irrelevant.
- OPS lateral offset from the target line: 0.05 m → −0.014, 0.10 m → −0.022,
  0.20 m → −0.046. This is the one geometric unknown that could push the
  correction past 0.02, and it is unmeasured. **INFERRED** that it is small,
  since the camera's own lateral offset is 0.060 m
  (`kiosk_20260825_181725.log`, `lateral_offset_m: -0.060325`).

## 3. The real inconsistency: the contract depends on which radar is attached

**CONFIRMED**, `src/openflight/server.py:5536`:

```python
ball_speed_correction_enabled = args.kld7 or args.iwr6843
```

The ball deprojection rides on the presence of a *second* radar. The comment at
`server.py:5534-5535` gives the reason — the correction needs a measured launch
angle, and only the vertical radar supplies one. That is defensible on its own
terms.

But it silently makes `smash_factor` mean two different things:

| configuration | ball contract | club contract | median smash | bias vs consistent |
|---|---|---|---|---|
| OPS + IWR6843 (this session) | TOTAL | radial (≈total) | 1.2839 | **+0.009** |
| OPS alone | RADIAL | radial | 1.2469 | **−0.024** |

Same shots, same swings: the two configurations disagree by a median of
**0.037** — nearly twice the resolution a fitter acts on. The OPS-only
configuration is the one that exceeds the 0.02 threshold, and it is the
configuration `docs/PARTS.md:163` advertises ("OpenFlight works without any
angle radar: you get ball speed, club speed, smash").

Nothing in the code or docs records that `smash_factor` changes contract with
the hardware present.

## 4. Verdict

**DERIVABLE NOW — in the OPS + IWR6843 configuration.** Both shipped speeds are
total speed to within 1%: the ball explicitly (deprojected, factor 0.972), the
club implicitly (radial, but its LOS is within ~1° of its velocity, factor
0.991). The residual smash bias is +0.009, comfortably inside the 0.02
resolution. No fix is required to ship or trust smash factor on this rig, and
smash factor is in fact already shipped.

**NEEDS A FIX — the OPS-only configuration**, where the ball reverts to radial
and smash is biased −0.024. See the proposal below; it is not small enough to
apply blind.

### Proposal (not implemented)

Do not simply flip `server.py:5536` to always-on. The correction consumes
`shot.launch_angle_vertical`, and in OPS-only mode that is a table *estimate*
from `estimate_launch_angle` (`server.py:559-644`), not a measurement.
Correcting a measured speed by an estimated angle trades a known bias for
unknown per-shot scatter — and, worse, that estimator itself consumes smash
factor (`server.py:581-584`), which would close a feedback loop between the
ball speed and the angle used to correct it.

Two defensible options, in preference order:

1. **Record the contract.** Emit the projection factor (or a
   `ball_speed_contract` field of `"total"` / `"radial"`) alongside the speeds
   so downstream consumers, `compare_trackman.py` included, can tell which
   configuration produced a row. Cheap, no accuracy risk, and it makes the
   OPS-only bias legible instead of silent.
2. **Apply a fixed club-class projection factor in OPS-only mode**, keyed off
   the selected club rather than a per-shot estimated angle. This removes the
   −0.024 bias without adding per-shot scatter, at the cost of a constant that
   has to be validated per club class.

Both need a TrackMan-referenced session to validate. This one has none — the
session is named `_trackman` for its bay location, but
`source_logs/session_20260825_181734_trackman.jsonl` contains no TrackMan
records (**CONFIRMED**: entry types are `shot_detected`, `rolling_buffer_capture`,
`camera_capture`, `trigger_event`, and telemetry only).

### UNDETERMINED, and what would settle it

The OPS club speed itself. Geometry says its projection error is under 1%, but
nothing here validates its *absolute* accuracy, and the 9% disagreement with
the IWR range-rate is unexplained by projection. If the OPS Doppler peak tracks
the clubhead toe rather than the centre of the face — the standard definition of
club speed — the smash factor is biased low by a few percent, several times the
0.02 threshold. That would be a larger error than everything audited above.

The measurement that settles it: **one session with TrackMan club speeds** on
the same shots. `scripts/analysis/compare_trackman.py` already emits
`club_speed_delta` and `smash_delta` and needs no changes to run it. Until then
the absolute level of smash factor is unvalidated, even though its *contract*
is now confirmed self-consistent.

## Scope

Audit and documentation. No production behaviour is changed. The LCMF code path
is untouched — the two-contract issue in `estimate_lcmf_v1` is a separate item,
and it does not reach `ball_speed_mph` or `club_speed_mph`, because
`server.py:3245` passes the raw radial speed into the estimator before the
correction at `server.py:3750` runs.
