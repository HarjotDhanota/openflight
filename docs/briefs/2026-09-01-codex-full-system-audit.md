# Codex brief — full-system accuracy audit, D-plane vertical launch first

**Date:** 2026-09-01 · **Repo:** the OpenFlight fork (this checkout and its worktrees)
**Mission:** get every metric as close to TrackMan as this hardware allows. To that end,
adversarially audit the entire measurement chain for correctness — the D-plane
vertical-launch conflict first — AND judge each metric against how TrackMan actually
achieves it (the research below is an input, not background). You are looking for wrong
math, wrong signs, wrong frames, wrong units, wrong timestamps, unstated assumptions, and
places where our method structurally cannot reach parity while a known better method fits
this hardware. Every claim must carry evidence (file:line, a reproduction command, or a
computed counter-example).

## North star: TrackMan parity, with the research baked in

Read these BEFORE auditing — they contain the comparator methods, the patents, and the
corrections already paid for; re-deriving them wastes the audit, contradicting them without
evidence is a finding against YOU:

- `../openflight-research/docs/Personal Research/markerless-club-data-guide-v2-research-corrected.md`
  — the living research guide. Priority sections: **§2.1** (TrackMan's OERT method, patent
  US 10,953,303 — the real blueprint for club data on radar+camera), **§4.4** (TrackMan's
  impact-location spec and the path to it), **§1K** (roadmap + what each metric needs on
  this hardware), **§1L** (what the 21-shot session established), **§1M** (how TrackMan and
  every competitor solve setup/alignment; inter-sensor calibration patent US 11,619,708 —
  in-situ radar↔camera registration from live shot tracks, directly implementable here).
- `../openflight-metrics/research/session_metrics/RESULTS.md` — the session's D-plane and
  swing-plane numbers and their caveats.
- Repo-root research reports (one directory ABOVE this checkout):
  `../openflight_claude_artifact_dplane_audit.md`, `../openflight_club_pose_research_survey.md`,
  `../openflight_dplane_hardware_followup_research.md`, `../openflight_shaft_axis_followup_research.md`.
- Comparator discipline: the comparison set is TM4 / Full Swing KIT / Mevo Gen 2 (never iO);
  official-manufacturer club data only.

**Per-metric parity verdicts are a required output — for the CV-owned metrics.** For each of:
vertical launch, club path, attack angle, face angle, dynamic loft, impact location (heel-toe
and high-low) — state: what TrackMan does for it (cite the research), where our chain stands,
the gap, and whether closing it is a code fix (name it), a calibration/session (name what it
measures), or a hardware limit (prove the limit). Ball/club speed, horizontal launch, spin and
carry are out of this pass's scope (see Scope below) — list them in the table as
`out_of_scope_this_pass` rather than auditing them. Rank the report's recommendations by
parity impact per unit effort, not by how interesting the bug is.

---

## Where the code is

All branches stack on `feat/clubpose-fit`. Audit the NEWEST state of each chain:

| branch | worktree | contains |
|---|---|---|
| `feat/rig-geometry` (tip, stacks on impact-zone) | `../openflight-geometry` | `rig_geometry.py` (RigGeometry/SetupSolution/solve_setup), `clubpose/impact_projection.py` (3-D contact = B − r·n̂), acoustic walk-back wiring in `club_delivery.py` |
| `feat/clubpose-impact-zone` | `../openflight-impactzone` | `clubpose/impact_zone.py` (gates, carry, physics gate), `clubpose/head_outline.py` (masks, self-built template, toe calibration `calibrate_toe_landmark`, `mask_speeds`) |
| `feat/club-metrics-from-track` | `../openflight-trackmetrics` | `head_track.py`, `club_metrics.py` (D-plane; face angle + dynamic loft WITHHELD by named status), camera vertical launch, server wiring |
| `feat/clubpose-drift-fixes` | `../openflight-drift` | camera position/`fusion.py`/`range_mm_by_frame`/`detect_striking_face`/RH-default mesh handling |
| `chore/speed-contract-check` | `../openflight-smash` | the two speed contracts |
| `research/session-metrics` | `../openflight-metrics` | `research/session_metrics/RESULTS.md` — read for context, do not audit as product code |

**Data:** the real session export at
`~/Downloads/openflight_session_20260825_181734_filtered/openflight_session_20260825_181734_filtered`
(`shots.csv`, per-shot `frames.npz` + `camera_metadata.json` + IWR `.l3dump`), and the hand
marks at the repo root `contact_marks.jsonl` (pass 1, annotator `harjot`, is the regression
reference; pass 2 uses a different convention — never mix them).

**Environment:** `uv` only. A fresh worktree venv may need `uv pip install opencv-python scipy`.
The pinned regression suite is `tests/test_clubpose_impact_zone_regression.py` (~3 min, needs
the export); its registered numbers must NOT move — if one of your fixes moves them, that is a
finding to report, not a number to re-tune.

---

## PART 1 (priority): why do three vertical-launch estimators disagree?

The same shots yield three vertical launch angles, and D-plane dynamic loft + face angle are
withheld until this resolves:

| estimator | value (session means) | where |
|---|---|---|
| LCMF (`launch_angle_vertical`, source=radar) | ~18.5° | `iwr6843/` LCMF chain |
| camera + IWR reconstruction | ~24.1° | camera transverse + radar depth |
| camera `ball_flight` | ~28.0° | `camera/ball_flight.py` |

Physics bound (attack −4.6°, dynamic loft ≤ static for any modern 7-iron): true V ≤ ~23.5°,
so at most one of the last two can be right, and the spread is 9.5°. Trace each estimator END
TO END and answer, with evidence:

1. **Boresight elevation accounting.** Each radar's vertical angle is measured relative to
   its own boresight. The OPS is recommended mounted ~10° up (AN-027, see `ops243.py`
   docstring); the IWR mount tilt on the test rig is whatever it was. Where, exactly, does
   each chain add or fail to add the mount elevation? Is the LIS3DH tilt-compensation service
   (`inclinometer.py`) wired into the LCMF vertical, and with the right sign? A constant
   ~5.6° error here matches the LCMF-vs-reconstruction gap suspiciously well. Check the
   calibration path (`iwr6843/calibration.py`, `calibration_session.py`) for where tilt
   enters, and what happens when no inclinometer reading exists.
2. **Camera `ball_flight` chain.** Which frames does it fit (blooming in the first frames?
   ball leaving the top of the frame biasing late samples?), does it use the solved per-shot
   camera pitch or a constant, does it correct for the ball's off-axis position (the vertical
   angle of a pixel is not (row−cy)/fy at large offsets), is gravity (~0.5° over 60 ms at
   these speeds — compute it, don't guess) handled or negligible-and-stated?
3. **The reconstruction.** Which range anchors (note `RADAR_STATIC_BIAS_MM = 66.0069821` in
   `clubpose/projection.py` — where did it come from and is it applied consistently?), which
   lever arms between camera and IWR, which timestamps?
4. **Timing.** Are all three referenced to the same instant (acoustic contact vs radar
   trigger vs first camera detection)? Vertical launch decays as the ball flies; a different
   effective measurement time is a real physical difference, not an error — quantify it.
5. **Deliverable for Part 1:** either (a) a named, evidenced mechanism for each pairwise gap,
   or (b) the narrowed list of what a TrackMan side-by-side must decide. "The three measure
   different things at different times, correctly" is an acceptable answer if proven.

Also audit the D-plane implementation itself (`club_metrics.py`): the Wood et al. coefficient
application (direction of the k division — applying it inverted flips the correction), sign
conventions for RH golfers, degrees/radians, spin-loft geometry, and that the withheld
statuses can never leak a partial number into a shot record.

## PART 2: the rest of the chain, module by module

For each: re-derive the math independently before reading the comments; the comments were
written by the same process that wrote the bugs.

- **`rig_geometry.py`** — solve_setup: range from angular size (is the diameter estimator
  biased by blooming? what does ±0.5 px of diameter do at 12.6 px?), lateral sign convention
  vs `projection.CAMERA_LATERAL_OFFSET_MM`, mic-to-ball distance math, the principal-point
  stand-in (the shipped mode is a sensor crop — is the crop optical-axis-centred? compute
  from `sensor_crop`/`isp_offset` in `fit.measured_camera()` and reconcile with the ~5 mm
  tape agreement).
- **`clubpose/impact_projection.py`** — re-derive contact = B − r·n̂ and the plane
  intersections from scratch; check the face-normal construction for a LEVEL camera vs the
  solved −0.22° pitch and the unresolved 3.2° roll (what do 1° of each do to the readings —
  compute the sensitivity table); the landmarks-on-the-face-plane assumption (head depth
  ~20 mm — bound the error); the up-face sign flip logic.
- **`clubpose/impact_zone.py` + `head_outline.py`** — the toe calibration (median overhang:
  is align_frame inside `calibrate_toe_landmark` consistent with the alignment used at read
  time?), the physics gate bounds, the carry-to-contact quadratic (extrapolation distance vs
  fit window), the USGA gates' exclusion zones, and the KNOWN topline-anchor failure (see
  "known findings" — quantify which shots and why the specular ridge wins there, if you can).
- **Fusion (`clubpose/fusion.py`, `club_delivery.py`)** — frame/timestamp alignment between
  camera, IWR and OPS; unit boundaries (mph vs m/s vs mm/frame); the mirroring convention
  (frames stored MIRRORED, un-mirrored with `[:, :, ::-1]`; the world frame is LEFT-handed
  with +y = image right — hunt for double-mirrors or a consumer that assumes right-handed).
- **Acoustic timing** — `contact_frame_from_trigger` callers; the solved mic distance wiring;
  speed of sound is a constant 343 (temperature not yet wired — known, listed below).
- **Server wiring** (`server.py` staged pipeline, the `--experimental-impact-zone` flag) —
  can any withheld/experimental value reach a sim connector or the UI without its status?

## Scope

This audit is the RADAR + CAMERA COMPUTER-VISION chain and the D-plane only — Parts 1 and 2
above. **Out of scope for now** (a later audit): the OPS rolling-buffer speed extraction,
horizontal-launch LCMF, the spin model, ballistics/carry, the trigger/clock chain, sim
connectors, club_data tables, UI, cloud, power, and the deprecated K-LD7 stack. Where a
Part-1/Part-2 finding depends on one of those (e.g. a timestamp produced upstream), note the
dependency and stop at the boundary rather than auditing across it.

## Known findings — context, do NOT re-report as discoveries

1. Vertical channel's topline anchor locks onto the wrong feature on ~half the shots
   (template topline lands at/below ball-centre height — impossible). Fix planned: support-
   plane datum + mat-geometry gate.
2. The ball sits ON THE MAT (not teed): support plane = ball centre − r; floor-referenced
   verticals outside RigGeometry are a smell. Tee-height auto-detection planned.
3. Toe absolute position awaits foot spray; heel-side landmark includes the hosel by design.
4. Camera roll 3.2° unresolved (net-line evidence only); LIS3DH readout planned.
5. Temperature → speed of sound not yet wired (BME280 exists).
6. Face angle / dynamic loft withheld pending Part 1; high-low is `experimental_unvalidated`.
7. NO GROUND TRUTH EXISTS anywhere: every validation figure is consistency, not accuracy.
   Keep that language in anything you write.

## Rules

- `uv run` for everything; never bare python/pip/pytest.
- Bug found ⇒ write the FAILING test first, on the branch where the bug lives; fix only if
  the fix is unambiguous, otherwise report.
- Do not tune any pre-registered number to make a suite pass. Do not touch
  `build_template_from_labels` (the pinned reference path).
- Leave-one-out discipline for anything scored on the session.
- Work on a branch `audit/2026-09-01` off the relevant worktree branch; no pushes.

## Output

A single markdown report `docs/briefs/2026-09-01-audit-REPORT.md`, in this order:

1. **Part 1's answer** — the vertical-launch mechanism(s), evidenced.
2. **The TrackMan parity table** — per metric: TrackMan's method (cited), our state, the
   gap, and the named closer (code / calibration / hardware), ranked by parity impact per
   unit effort.
3. **Findings** ranked by severity, each carrying: claim (one sentence) · file:line ·
   evidence or repro command · measured impact on the session's numbers where feasible ·
   suggested test.
4. **What I could not check and why.**
