# Fusion workbench: design

Date: 2026-09-25. Status: design approved in conversation; this spec awaits review.
Branch: `agent/fusion-workbench` (worktree `openflight-agent-fusion-workbench`), cut from
`agent/review-workflow` at `58fb2ae`. Fork only: this branch never goes upstream.

## 1. Purpose

The workbench is one desktop tool for reviewing and tuning sensor fusion from saved
sessions. It answers three questions:

1. **Foundations.** Where does each sensor sit relative to the others, and when did contact
   happen? These errors are the same on every shot, so we solve them once from many shots
   and save the result.
2. **Labels.** Where did a detector pick the wrong thing? These errors differ from shot to
   shot, so a person marks the truth and the marks accumulate into a shared label set.
3. **Scoreboard.** Did a change help everywhere? Every change is scored shot by shot
   against all labels, against sensor agreement, and against a reference device when a
   session has one.

The end goal is trustworthy **face angle** and **impact location**. Both depend on the
foundations. Face angle comes from launch direction and club path (the D-plane), which are
only comparable once camera and IWR agree on "straight". Impact location needs the right
contact frame.

### Success criteria

- Open a tester bundle or the August export with one command, and see IWR-predicted ball
  positions drawn over the camera frames.
- The solve reproduces the known August camera/IWR offsets: azimuth 5.19° and elevation
  6.18°, each ±0.5°.
- Label a wrong ball detection in under 10 seconds, including the reason.
- The Scoreboard compares two configurations shot by shot. It reports better or worse per
  check and per session, and it keeps reference-device rows apart from sensor-agreement
  rows.
- Nothing in the workbench changes recorded evidence, the Pi, or the live pipeline.

### Non-goals

- Any change to the Pi tester suite, the live pipeline, estimator gates or defaults.
- Applying a calibration candidate live. Section 9 describes what a later "apply" needs,
  but it is not built here.
- Training a learned detector. The label format supports it later (section 7.6).
- Community data sharing. That design is parked.
- New face-angle or impact-location estimators. The workbench scores the existing ones.

## 2. How it relates to the Pi

```
  ON THE PI (tester suite, unchanged)        ON THE DESKTOP (workbench)
  set up -> swings -> capture                 open bundle / export
  -> analyse & package  ----------------->    Foundations / Labels / Scoreboard
         session bundle (zip)                         |
                                              calibration candidate file
                                                      |  (later, deliberate)
                                                      v
                                          copied to a Pi and used live
```

The session bundle (`openflight.session_bundle.v1`) is the only handoff. The Session Review
page stays as it is; it may become a workbench tab later.

## 3. Stage 1: combine the two branches

The workbench needs code that lives on two branches, which diverged at `b547a5f`
(2026-08-28). The camera and IWR diff between them is 46 files.

| Needed | Lives on |
|---|---|
| Bundles, Track Review frames and annotations, `accuracy_benchmark`, detector-disagreement record, calibrated projection (pixel to ray), tee-range evidence | `agent/review-workflow` |
| `registration.py` (median offsets), `acoustics.py`, `clubpose/*` (forward pinhole, `impact_zone` contact rules, `impact_projection`), `ball_flight.fit_launch_from_rays` (ball clock), `replay_registration.py`, `first_look.py` | `integration/cv-2026-09` |

Merge `integration/cv-2026-09` into `agent/fusion-workbench`. The merge resolves conflicts
only; it changes no estimator behaviour, gate or default. Where both branches changed the
same function, keep review-workflow's live behaviour and add integration's functions beside
it.

**Exit gates (all must pass):**

1. Full test suite green, except the Windows-only failures already present at
   `agent/review-workflow`. List those failures by name before and after the merge.
2. The integration regression `tests/test_registration_regression.py` passes on the August
   export (`OPENFLIGHT_SESSION` or `~/Downloads/openflight_session_20260825_181734_filtered/...`):
   elevation 6.18 ±0.5, azimuth 5.19 ±0.5, ball clock 1.89 ±0.5 frames, MAD < 1.2.
3. The tester-bundle tests from review-workflow pass: `test_session_bundle`,
   `test_replay_portability`, `test_session_review`, `test_tester_restart`, and the
   session-review Playwright suite.
4. Known semantic-conflict checks from the September 2 integration: the `RadarRanges`
   contract, and corrected extrinsics shifting `head_track`. Replay the August club
   metrics before and after the merge; any change needs an explanation.

Run one regression replay at a time, never concurrently.

## 4. Core concepts

**Session source.** A session opens as a list of shot records from either:
- a tester bundle, verified against `bundle_manifest.json` and its `.sha256` sidecar and
  extracted read-only into the library (section 8); or
- an August-style export (`shots.csv`, `shots/<dir>/frames.npz`, `camera_metadata.json`,
  `*.l3dump`), identified by the SHA-256 of its `manifest.json`.

Every source has a **session identity**: the bundle SHA-256 or the export manifest SHA-256.
Labels, candidates and scores all refer to it.

**Shot record.** One per shot, holding:
- camera capture reference (npz SHA-256, frame timestamps, capture mode, exposure, gain)
- camera model: calibrated if the shot has an optical profile, otherwise legacy
- IWR evidence: range track (`BallTrack`: slope, intercept, resolution), `impact_t_s`,
  launch vertical and horizontal, confidence
- OPS: ball and club speed, `impact_timestamp_ms`, `impact_offset_from_trigger_ms`
- trigger host timestamp
- detector outputs: `reference_ball_diagnostics`, flight detections
- reference-device row, if paired

Missing items are null with a reason and are never guessed.

**World frame.** Target LFU (target-right, downrange, up), metres, as in
`live-calibrated-fusion.md`. The mesh loader's left-handed mirroring stays inside
`clubpose` and is not exposed here.

**Configuration.** What a score was produced with: the code identity (commit plus runtime
content-manifest hash) and a calibration, either the default or a candidate file.

## 5. Components

One package `src/openflight/workbench/`, one job per module:

| Module | Job |
|---|---|
| `sources.py` | Open bundles and exports as shot records (section 4). |
| `projection.py` | World point to pixel, with Brown-5 distortion. The exact inverse of `calibrated_projection.pixel_rays_lfu`. The legacy model uses the same interface. |
| `trajectory.py` | IWR-predicted ball position at a time (section 6.1). |
| `registration.py` | The four-parameter solve (section 6.2). |
| `witnesses.py` | Per-shot contact times on one clock (section 6.3). |
| `labels.py` | Label schema, store, queue and import (section 7). |
| `scoring.py` | Scorers, score files, paired comparison (section 8). |
| `reference.py` | Reference import and pairing (section 8.4). |
| `candidate.py` | Calibration candidate schema, read and write (section 9). |
| `jobs.py` | One background worker process for replays and solves, reusing the job pattern from `analyze_tester_session.py` (`job.json`, heartbeat, refresh-safe). |
| `server.py` | Desktop-only local server: `python -m openflight.workbench [--library DIR] <bundle.zip or export dir>...`. Binds `127.0.0.1`. |
| `ui/public/workbench.html` | The page, with Foundations, Labels and Scoreboard tabs. |

Reused as they are: `session_bundle` (verify, extract), `track_review.CaptureCache`
(single-frame reads, PNG), `replay_raw_fusion` / `raw_radar_replay` (per-shot replay),
`accuracy_benchmark.build_accuracy_report`, `compare_trackman.load_trackman`, integration's
`registration.register` (cross-check), `ball_flight.fit_launch_from_rays`, and
`clubpose.impact_zone` / `impact_projection`.

## 6. Foundations tab

### 6.1 Predicted ball positions

The IWR does not log a position per time step; it logs a range track and one launch
direction. For time t on the radar clock, the predicted ball position is

    B(t) = tee + s(t) · u(az, el) + ½ g (t − t_c)²

where:
- `u` is the IWR launch direction after the azimuth and elevation offsets;
- `s(t)` is chosen so that |B(t) − radar_origin| = range(t) from the range track;
- `t_c` is contact time.

The tee position comes from the shot's tee-range evidence when present, otherwise from the
rig default, labelled as such. Gravity is included; drag over the first 2 m is ignored,
since it is under 1 mm.

The page draws `project(B(t_k + dt))` for every camera frame k in flight, next to the
camera's own detections. Per-time IWR elevation from re-running the dump (`doa.AnglePoint`)
is an optional later overlay, not part of the solve.

### 6.2 The solve

**Parameters:**
- radar azimuth offset Δaz and radar elevation offset Δel, applied to `u`;
- camera roll offset Δroll, applied about the optical axis on top of the per-shot observed
  roll;
- camera-to-radar time offset dt.

The camera pitch model stays as it is. These match the quantities integration's
`registration.py` estimates (camera minus IWR), so the results can be compared.

**Residual.** The pixel distance between the projected B(t_k) and the camera ball detection
in frame k. Use only frames where the detection is accepted, or where a person labelled
the ball; labels override detections.

**Loss.** Robust (soft-L1, scale 2 px) least squares via `scipy.optimize.least_squares`,
over every usable frame of every usable shot.

**Uncertainty.** Bootstrap by resampling shots (default 500), reporting the 95% interval per
parameter and the parameter correlation matrix.

**Determined or not.** A parameter is marked **not determined** when:
- its 95% interval is wider than 1.0° (0.5 ms for dt); or
- its absolute correlation with another parameter exceeds 0.9 (for example roll when all
  shots go the same way).

Undetermined parameters keep their current value and cannot be saved.

**Cross-check.** Show integration's median/MAD estimate beside the least-squares result. A
gap above 0.5° is flagged on the page.

**Outliers.** Shots whose median residual exceeds 3× the session median are drawn red in the
scatter. Clicking one opens it in Labels. They are excluded from the solve only when the
user excludes them, and exclusions are recorded in the candidate.

The sliders move the same four parameters. Projection runs in the browser so every shot
updates instantly. The server re-scores the residual summary when a slider is released.

### 6.3 Contact timing strip

Per shot, contact time from each witness, converted to the camera host clock:

| Witness | Source | Clock conversion |
|---|---|---|
| Microphone rule | trigger host stamp − mic-to-ball distance / speed of sound (`acoustics`) | already on the host clock |
| Ball in camera | `fit_launch_from_rays` → `ball_clock_contact_s` | camera clock, end-of-exposure convention |
| IWR range track | `impact_t_s` (seconds from the oldest ring frame) | needs the dump's trigger frame, frame period and IWR trigger host stamp. **Verify this mapping first in stage 2.** If it cannot be established, show the IWR witness relative to its own trigger, labelled "own clock". |
| OPS onset | `impact_timestamp_ms` / `impact_offset_from_trigger_ms` | trigger-relative, then host |

**Session summary:** the spread of the ball-based witnesses, the microphone rule's offset
from their median, and its jitter (MAD). A "contact source" choice (microphone rule or
ball-based) is recorded in the candidate. It changes nothing live.

### 6.4 Saving a candidate

"Save candidate" writes a candidate file (section 9) with the determined parameters, their
intervals, the contact source, the sessions and shots used, and the code identity.

## 7. Labels tab

### 7.1 What can be labelled

| Kind | What the user does | Stored |
|---|---|---|
| `ball_circle` | Click the centre, drag to the edge | cx, cy, r (px, full-sensor coordinates) |
| `ball_absent` | Press "no ball here" | nothing more. Absent labels are needed to score false detections. |
| `club_point` | Click named points on the drawn outline, or drag its corners | name ∈ {heel, toe, leading_edge_heel, leading_edge_toe, hosel}, x, y |
| `impact_mark` | On the face photo, click the mark, then the heel and toe ends of the leading edge | photo SHA-256, mark px, leading-edge px. Face coordinates are computed (section 7.4). |

Every label records:
- the reason the detector was wrong: shadow, saturated, tee, blur, second ball, occluded,
  other, with an optional note;
- the detector's pick;
- conditions: exposure, gain, capture mode, and a lux band from exposure and gain;
- author, time, revision, and which revision it supersedes;
- `sampled`: `queue` or `random` (section 7.3).

Labels are append-only. A correction adds a revision and never deletes history.

### 7.2 Schema and store

One file per session: `labels/<session identity>.json`, schema `openflight.workbench.labels.v1`.
Each label is keyed by capture npz SHA-256 + frame index (or photo SHA-256), so it stays valid
when code changes and when the same capture appears in a re-packaged bundle.

Existing Track Review annotations (`openflight.track_annotation.v1`, points only) are
imported read-only as `ball_circle` / `club_point` labels without a radius, with
`source: track_review`.

"Export to bundle" can copy a session's labels into its `<tester>/annotations/` so they travel
with the session. The library copy remains the working copy.

### 7.3 The labelling queue

Frames are ordered by:
1. the scene and impact detectors disagreeing (`reference_ball_diagnostics.disagreement`);
2. Foundations outliers;
3. low detector confidence;
4. condition cells with the fewest labels.

**One frame in ten is drawn at random** and marked `sampled: random`. Queue-picked frames
overstate error, because they are chosen for being hard. The Scoreboard reports error on the
random sample separately, as the unbiased estimate.

**Keyboard:** Enter saves and goes to the next frame; 1–7 set the reason; A marks absent;
S skips.

### 7.4 Impact location in face coordinates

The two leading-edge ends set the blade axis and length in the photo. The scale comes from
the recorded club model's official blade length (Mizuno or Srixon per-club dimensions, see
the head-dimension notes). If there is none, the mark is stored as a fraction of blade length
and height, and no millimetre error is claimed.

The system's estimate is the existing impact projection, shown on the contact frame. Face
photos come from the study ladder's `photos` map. Its paths are relative to the tester
folder.

### 7.5 Face angle

Face angle is not labelled. From behind the ball, 1° of face angle moves an iron's face by
about 0.4 mm, which a person cannot see. Its truth is the reference device when present.
Otherwise the D-plane (launch direction and path) is reported as model-derived consistency,
not accuracy.

### 7.6 Later: training

Labels carry everything a learned detector needs. Training and COCO-style export are out of
scope, with one rule fixed now: split training and test sets by session identity, never by
frame. A learned detector replaces a hand-built one only after beating it on the Scoreboard.

## 8. Scoreboard tab

### 8.1 Score files

"Score this session" runs a replay under a configuration in the background worker, one job
at a time, cached by (session identity, code identity, calibration). It writes
`scores/<session>/<configuration>.json`, schema `openflight.workbench.score.v1`, holding one
value per check per shot, frame or label, plus the configuration identity.

### 8.2 Checks

| Check | Scored against | Unit |
|---|---|---|
| Ball found | ball labels. A hit is a centre within max(3 px, 0.25·r) and a radius within 15%. Absent labels count false detections. | hit rate, px |
| Club points | club_point labels | px |
| Impact location | impact_mark labels | mm, or a fraction of the face |
| Camera vs IWR direction | sensor agreement (Foundations residual) | deg, px |
| Contact-witness spread | sensor agreement | ms |
| Metrics vs reference | paired reference rows (section 8.4) | per metric |
| Availability | shots producing each metric | % |

These thresholds are the initial scoring contract, versioned in the score file. Changing a
threshold creates a new version and never rewrites old scores.

### 8.3 Comparing two configurations

Pick any two score files, for example the default calibration vs candidate #2, or commit A
vs commit B. Results are paired by shot, frame or label.

Per check: the mean paired difference in absolute error, with a 95% interval from
bootstrapping by session, plus how many sessions got better and how many got worse.

**Verdict:**
- **better:** the interval lies entirely below zero and the mean improvement is at least the
  check's practical threshold (0.2° for angles, 1 px, 0.5 mm, 0.1 ms);
- **worse:** the mirror image;
- otherwise **no clear change**.

Availability that drops by more than 1 shot per 100 is flagged **"fewer: explain"**, so a
change cannot improve by rejecting hard shots. Reference rows and sensor-agreement rows sit
in separate groups and are never averaged together.

### 8.4 Reference sessions

Import a TrackMan, Full Swing KIT or Mevo Gen 2 CSV. TrackMan uses
`compare_trackman.load_trackman`; KIT and Mevo get column maps in `reference.py`.

**Pairing is by time.** The clock offset comes from cross-correlating the two ball-speed
series, then nearest-in-time within 2 s. The existing order-based pairing is the fallback and
is labelled as such. The user confirms the pairing on the page before it is used. Reference
values flagged "estimated" are excluded per metric. Statistics come from
`accuracy_benchmark.build_accuracy_report`: bias, MAE, RMSE, P90, coverage with Wilson
intervals, and a session bootstrap.

The measurement point differs by device. TrackMan measures club metrics at the geometric
centre of the head, so the page states which point OpenFlight measures and never silently
offsets it.

## 9. Calibration candidate

`candidates/<id>.json`, schema `openflight.workbench.calibration_candidate.v1`:

- `candidate_id`, `created_at`, `author`
- `rig_geometry_sha256`: must match every source session's rig; a mismatch refuses to save
- `based_on`: the calibration it modifies (default, or a previous candidate id)
- `parameters`: for each of `radar_azimuth_offset_deg`, `radar_elevation_offset_deg`,
  `camera_roll_offset_deg`, `camera_radar_time_offset_ms`: value, 95% interval, determined
- `contact_source`: `acoustic_rule` or `ball_rays`
- `solved_from`: session identities, shots used, shots excluded with reasons
- `solver`: version, loss, bootstrap count; `code`: commit and content-manifest hash

Candidates are never applied automatically. A later "apply" would be a server flag reading
the candidate into the placement/registration transform, with the candidate id frozen into
each shot's provenance. It is not built here.

## 10. Storage and privacy

The library root defaults to `~/openflight-workbench` (`--library` overrides it) and holds:
`sessions/` (extracted sources, read-only), `labels/`, `candidates/`, `scores/`,
`references/`, `jobs/`. It sits outside every repository. Session data, labels and reference
exports are never committed. Opening a session never writes into the original bundle or
export.

## 11. Failure handling

| Situation | Behaviour |
|---|---|
| Bundle hash mismatch | Refuse to open; show the failing member. |
| Shot without IWR track, camera capture or accepted detection | Listed with the reason; excluded from the solve; still labellable. |
| Too few usable shots (< 8, matching `registration.MIN_SHOTS`) | Solve refuses and says how many are needed. |
| Parameter not determined | Shown greyed with its interval; cannot be saved. |
| IWR clock mapping unavailable | IWR witness shown on its own clock, labelled. |
| Rig mismatch across selected sessions | Solve and save refuse; sessions can be scored separately. |
| Worker dies or page reloads | Job state from `job.json` (heartbeat older than 30 s = interrupted); results are never partial. |
| Two label revisions for the same frame | Latest wins; history kept. |

## 12. Testing

- **Stage 1:** the merge gates in section 3.
- **Projection:** pixel to ray to pixel round trip within 0.01 px over the whole frame,
  including the corners where distortion is largest. Browser/Python parity within 0.01 px on
  1,000 random points (run under Node in the test suite).
- **Solve, by injection and recovery:** synthetic shots from `tests/session_fixtures.py`
  with known Δaz, Δel, Δroll and dt must be recovered within 0.05° and 0.05 ms. When all
  shots share one direction, roll must be reported as not determined. Real-capture
  injection: rotate a real session's IWR direction by a known angle and recover it.
- **August reproduction:** azimuth 5.19 ±0.5 and elevation 6.18 ±0.5, skipped when the
  export is absent, like the existing regression.
- **Witnesses:** clock conversion on constructed timestamps, plus the "own clock" fallback.
- **Labels:** schema round trip, rejection of malformed labels, revision history, Track
  Review import.
- **Scoring:** the verdict rule on constructed paired data (better, worse, no clear change),
  the availability flag, and a hit/miss table for ball labels.
- **Reference pairing:** with one shot dropped, time pairing stays correct where order
  pairing would shift every later pair.
- **End to end (Playwright):** open the fixture bundle, move a slider (dots move), solve,
  save a candidate, label a ball with a reason, score, and see a Scoreboard row.
- **Real smoke:** open one real tester bundle and the August export.

## 13. Stages

| Stage | Delivers | Done when |
|---|---|---|
| 1 | Merged branch | The section 3 gates pass. |
| 2 | Sources, projection, trajectory, witnesses (IWR clock mapping verified first), solve, candidate, server, Foundations tab | The August reproduction passes, injection and recovery pass, and the end-to-end Foundations path passes. |
| 3 | Labels: schema, store, queue, Track Review import, face-photo impact marks, club points | Label tests and end-to-end labelling pass. |
| 4 | Scoring, score files, paired comparison, reference import and pairing, Scoreboard tab | Scoring and pairing tests and the end-to-end Scoreboard path pass. |

Each stage ends working and tested before the next begins.

## 14. Risks and open points

- **The IWR ring-clock to host-clock mapping is unverified.** Stage 2 starts with it.
- **The August rig was never measured.** Its offsets are a reproduction check, not truth.
- **The merge can hide semantic conflicts,** as the September 2 integration did. The
  before/after August replay in gate 4 is the guard.
- **The camera model differs by shot:** calibrated for some, legacy for others. Each shot
  uses its own, and the solve reports how many of each it used.
- **Roll is often unobservable** unless launch directions vary. A "registration check"
  capture protocol of about 10 swings aimed left, centre and right belongs to the paused
  tester-UX design, not here.
