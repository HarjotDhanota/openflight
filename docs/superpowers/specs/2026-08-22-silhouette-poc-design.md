# Silhouette-fusion impact-location POC — design spec

*Status: DRAFT rev 1 — awaiting Codex audit (see Appendix A) before implementation.*
*Branch: `feat/silhouette-poc` (= `feat/camera-club-data` research + `pr-215-ov9281` camera merged; both are in-tree).*
*Companions: `docs/Personal Research/camera-feasibility-verdict-2026-08.md` (the evidence base), v2 guide §1J.*

## 0. Purpose and one-paragraph summary

Prove, **without owning the camera or the IWR6843**, that OpenFlight's shipped hardware (InnoMaker OV9281 per upstream PR #215 + IWR6843 + OPS243) can measure **impact location on the clubface** using a Trackman-4-style OERT fusion: radar supplies precise impact timing and depth, the camera supplies silhouette pose constraints, and a fusion model extrapolates the clubhead's face template to the radar-timed impact instant. The POC is a **standalone web app ("Sim Studio")** driving a synthetic swing generator that writes the *real* system's artifact formats, a fusion engine that consumes only those artifacts, and a validation program with pre-registered pass/fail criteria. Everything runs on a dev PC in a browser; the physical launch monitor is never required. When real hardware arrives, the identical fusion pipeline runs on real captures unchanged.

## 1. Success criteria (pre-registered — do not tune after seeing results)

| Metric (synthetic end-to-end, as-shipped camera preset) | Pass | Stretch |
|---|---|---|
| Impact vector error (offset+height combined), median, driver | ≤ 10 mm | ≤ 5 mm |
| Impact vector error, median, 7-iron | ≤ 12 mm | ≤ 6 mm |
| Fusion solve rate (shots producing a gated estimate) | ≥ 80 % | ≥ 95 % |
| Classical silhouette IoU vs ground-truth masks (synthetic, degraded) | ≥ 0.85 | ≥ 0.92 |
| ML silhouette IoU on phone proxy footage (hand-labeled sample) — *non-gating (§6)* | ≥ 0.85 | ≥ 0.92 |

Context anchors: Stage 0C's idealized budget landed 3.6 mm (stereo) / 6.2 mm (mono); Trackman-class is ~1–2 mm; Mevo Gen 2 sells impact location with no published accuracy. Failing "Pass" on impact vector at BOTH camera presets = the POC's answer is "not viable as architected," which is a valid, reportable outcome.

## 2. Hardware truth table (single source for every constant)

All values from this repo, not datasheet ideals. `sim/config.py` (new) encodes this table; nothing elsewhere hardcodes these numbers.

| Parameter | Value | Source |
|---|---|---|
| Camera sensor | OV9281 mono GS, 1280×800, 3.0 µm | PR #215 docs + OmniVision |
| **Preset A "as-shipped"** | 320×200 @ 450 fps requested / **468 fps delivered**, 500 µs exposure, gain 2 | `docs/camera/README.md` |
| Preset A plate scale | **0.656 px/mm at tee** (ball 28 px @ 1.524 m) — *carry alternate 1.31 px/mm until Gate-0 settles the `.crop` contradiction; both must be run in eval* | verdict doc §1; `tests/test_camera_club_delivery.py` fixture |
| **Preset B "proposed"** (same sensor, new readout) | 1280×200 native 1:1, portrait (long axis vertical), ~446 fps, 10–20 µs exposure assumed strobed | verdict doc §3.2 — *unverified row-time model; Codex audit item A2* |
| Preset B plate scale | 1.33 px/mm (focal_px 2000, f=6 mm) | verdict doc §3.2 |
| Camera mount | height 0.20955 m; lateral offset 0.0 m default; roll 0.0° | `server.py` CLI defaults |
| Tee slant range | 1.575 m | `server.py` `--iwr6843-tee-m` default |
| Motion at impact | driver head ~40 m/s, ball ~67 m/s; iron/wedge per `club_pose/groundtruth.py` regimes | research code |
| IWR6843 range resolution | 4.69 cm bins (B = 3.2 GHz: slope 100 MHz/µs × 32 µs window) | `config/iwr6843_l3dump_dense_36f2ms_53bin_iq8.cfg` |
| IWR range noise model | σ = 3 mm (SNR-derived) + **bias uniform 0–40 mm (clubhead phase-center wander; swept, never assumed zero)** | verdict doc §3.7 — *Codex audit item A4* |
| OPS impact timing | σ = 33 µs (I/Q localization); alternate 2.14 ms (frame-quantized, PR #215 as-shipped) — both must be eval cells | `scripts/analysis/ops_impact_finder.py` |
| Ball diameter | 42.67 mm | `club_motion.BALL_DIAMETER_MM` |

## 3. Repository layout

```
research/silhouette_poc/
  README.md                  # pitch, GIF, quickstart, criteria table w/ results
  config.py                  # §2 truth table as dataclasses; presets A/B
  gen/
    swing.py                 # delivery kinematics → head pose trajectory (reuses club_pose.groundtruth, dplane)
    render.py                # mesh → silhouette frames w/ exposure-integrated motion blur, noise, light level
    artifacts.py             # writer: frames.npz + metadata.json + session JSONL + radar evidence + truth sidecar
  fusion/
    silhouette.py            # classical extractor (bg-sub + moving-bright mask + morphology)
    posefit2d.py             # projected-template fit per frame → head pose observations
    track.py                 # multi-frame head track; lift to 3D via radar range sphere
    impact.py                # extrapolate to radar-timed impact; face-template intersection → offset/height mm
    diagnostics.py           # per-stage artifacts for Studio overlays
  ml/
    dataset.py               # composite synthetic silhouettes over real backgrounds + augmentation
    train.py                 # small seg net; ROCm; exports ONNX
    evaluate.py              # IoU tables classical vs ML
  eval/
    run_budget_0c_radar.py   # PHASE 1: 0C budget + radar-depth resolver + blur + sync cells
    run_e2e.py               # PHASE 4: N≥200/club end-to-end vs truth
    run_degradation.py       # sweeps: exposure, light, bias, plate scale, sync
    report.py                # RESULTS_*.md generation
  server/
    app.py                   # Flask-SocketIO: REST for Studio + kiosk-compatible socket contract
  studio/                    # NEW Vite+React+TS app (primary UI; independent of ui/)
  fixtures/
    demo_session/            # one canned generated session (committed)
  tests/                     # pytest; mirrors package layout
```

## 4. Artifact contracts (the load-bearing design decision)

The generator writes, and the fusion reads, **the real system's formats only**. No private interchange format between gen and fusion.

**4.1 `frames.npz`** — must byte-load through PR #215's own loader (`server._load_camera_capture_archive`). Exact keys as `capture_runtime._save_capture` writes them: `frames` (uint8 stack), `sensor_timestamp_ns` (int64/frame), `host_timestamp_ns` (int64/frame), `exposure_us` (int32/frame), `analogue_gain` (float32/frame), `pre_trigger_count` (int32 scalar), `trigger_host_timestamp_ns` (int64 scalar), `trigger_epoch_timestamp` (float64 scalar). Uncompressed. Directory naming `camera_<ts>_<seq>/` with `first/trigger/last.pgm` and `metadata.json` matching `_save_capture`'s summary dict (verify field-for-field at implementation; a round-trip test is REQUIRED: generate → load via the PR #215 code path → assert).

**4.2 Radar evidence** — a JSON sidecar per shot mimicking what the live pipeline attaches to `Shot` (`iwr6843_club_range_evidence`, `iwr6843_ball_range_evidence` shapes as `server.py` uses them — Codex audit item A3 pins the exact structure) plus OPS impact timestamp and club/ball speeds.

**4.3 Truth sidecar** — `truth.json` per shot: true head pose trajectory (sampled), true impact instant, true impact offset/height mm, delivery params. Never read by fusion; only by eval and Studio's truth toggle.

**4.4 Session JSONL** — one real-format session log referencing the camera dirs via `camera_capture` entries (`session_logger.log_camera_capture` format), so kiosk replay and future real sessions are interchangeable.

## 5. Fusion engine (OERT-style)

Per shot, consuming only §4 artifacts:

1. **Background model** from earliest pre-trigger frames (median).
2. **Silhouette per frame** (classical leg): bg-subtraction + adaptive moving-bright mask (reuse thresholds/approach from `camera.club_delivery._club_mask` where sensible), morphology, component selection by size + ball-adjacency. Output binary mask + confidence.
3. **2D template fit per frame**: project the club-type head template (from `club_pose.template` / `sim.driverhead`) into the image via current pose hypothesis; optimize pose (translation px, in-plane rotation, scale) to match the silhouette (chamfer/IoU objective). Pre-impact frames only (reuse #215's impact-index guard rationale).
4. **Track + lift to 3D**: head reference point tracked across frames; rays through the calibrated camera model (focal from reference ball, exactly as `_pixels_to_world`) intersected with the IWR range sphere per frame → 3D positions; robust velocity fit.
5. **Extrapolate to impact**: propagate the fitted pose+velocity to the radar impact timestamp (the OERT substitution: timing precision replaces frame rate). 
6. **Impact solve**: intersect the extrapolated face template with the known ball position → impact offset (heel/toe mm) + impact height (mm), plus gates (fit residuals, track MAD, speed-vs-OPS ratio) → `status` + confidence tier, mirroring #215's gating idiom.
7. **Diagnostics** at every stage for Studio.

Design rule: each stage is a pure function over explicit inputs, independently testable; no stage reads global config.

## 6. ML silhouette leg (parallel, non-blocking)

Dataset: analytic silhouette renders composited over real background photos (user-supplied garage/range/phone stills) with randomized blur, noise, brightness, clutter occluders; masks are free. Model: small U-Net-class net (< 5 M params) trained on the ROCm box; exported ONNX; CPU inference must run < 50 ms/frame at 320×200. `fusion/silhouette.py` exposes `extract(frames) -> masks` with interchangeable classical/ML backends behind one interface. Deliverable: IoU table (classical vs ML × clean/degraded synthetic × proxy footage). The fusion pipeline must hit its criteria with the classical leg alone; ML is an upgrade path, not a dependency.

## 7. Sim Studio (primary UI — standalone web app)

New Vite + React + TS app in `research/silhouette_poc/studio/`, talking REST + Socket.IO to `server/app.py`. Runs on any PC; **the launch monitor is never needed**. Panels:

1. **Generate**: club selector, N shots, delivery randomization ranges, preset A/B, degradation controls (exposure, light level, radar bias, sync jitter, plate-scale candidate) → generate session (async job w/ progress).
2. **Shot view**: frame stepper with overlay layers (raw / silhouette / fitted template / head track / extrapolation to impact tick); toggle ground truth.
3. **Impact view**: clubface graphic with estimated dot vs truth dot per shot; session heatmap; error readouts.
4. **Results**: error distributions per club/preset; degradation curves; criteria table pass/fail live.
5. **Session manager**: list/load/delete generated sessions; load the committed fixture instantly.

Styling: consistent with the kiosk UI's look where cheap, but Studio is its own app — no coupling to `ui/` internals.

## 8. Kiosk integration (thin, last)

`server/app.py` additionally speaks the kiosk UI's socket contract (`shot` events with the real payload schema — Codex audit item A5 pins it) so the **unmodified** `ui/` kiosk app pointed at the replay server shows the synthetic session exactly as the device would. Scope strictly: no new kiosk components in this POC beyond wiring `experimental_impact_offset_mm` / `experimental_impact_height_mm` into the existing debug panel. The full kiosk impact view ships with the future upstream PR, not the POC.

## 9. Validation program (phased; Phase 1 gates everything)

- **Phase 1 — budget re-run (BEFORE any app code):** `eval/run_budget_0c_radar.py` extends the existing 0C machinery with a radar-depth resolver (σ 3 mm + bias 0/10/20/40 mm cells), sync 33 µs and 2.14 ms cells, both plate-scale candidates, and a blur-inflated centroid-noise term. Output `eval/RESULTS_0C_RADAR.md`. **Gate: if no realistic cell beats 10 mm median impact vector, STOP and report** — the POC's conclusion becomes "not viable as architected" and Studio is not built.
- **Phase 4 — end-to-end:** `eval/run_e2e.py`, N ≥ 200 shots/club (driver, 7i, wedge), randomized deliveries, real fusion code, scored vs truth. Must reconcile with Phase 1 predictions (large disagreement = bug until proven otherwise).
- **Phase 5 — degradation study:** sweeps published as curves in Studio + `RESULTS_DEGRADATION.md`.

## 10. Testing

TDD throughout. Non-negotiable tests: frames.npz round-trip through the PR #215 loader; zero-noise end-to-end recovers truth ≤ 0.5 mm (machinery-exactness, the 0A–0E idiom); noise cells match budget predictions within CI bounds; regression pinned on `fixtures/demo_session`; silhouette IoU floor on degraded synthetic; Studio component tests (vitest) for overlay math and criteria table; server contract test that a recorded kiosk socket session validates against the real schema.

## 11. Packaging / sharing

README quickstart (target: cloner sees Studio running on the fixture in < 2 min): `uv sync --group research`, `uv run python -m research.silhouette_poc.server`, `cd research/silhouette_poc/studio && npm i && npm run dev`. Demo GIF committed. Success-criteria table filled with real numbers. Branch pushed to fork `harjot/openflight` as `feat/silhouette-poc`. Draft PR-#215 comment (in README appendix) requesting one real session bundle + surfacing the `.crop`/plate-scale contradiction.

## 12. Implementation phases (for the plan doc)

1. Phase 1 eval (budget re-run) → gate decision
2. `config.py` + generator + artifact round-trip tests
3. Fusion classical path + zero-noise exactness tests
4. Server + Studio (generate/shot/impact views)
5. E2E + degradation eval + Results panel
6. ML leg (parallel with 4–5)
7. Kiosk thin integration + packaging + fixture + GIF

---

## Appendix A — Codex audit brief (run BEFORE implementing)

Codex: audit this spec and the findings under it. For each item, confirm or refute with evidence from the repo/datasheets/web; file corrections as a rev-2 edit list. Do not start implementation until the audit lands.

- **A1 — Plate scale.** Verify the claim that `mode_320x200_regs` in `drivers/ov9281/ov9282-high-speed.patch` implements 2× subsampling of an 816×516 window (⇒ 0.656 px/mm, ball ≈ 28 px), contradicting the mode's `.crop` struct. Check OV9282 register semantics (0x3814/0x3815 odd/even increments, ISP windowing) against the OmniVision datasheet and the upstream Linux `ov9282.c` driver. State which plate-scale candidate is correct or that it's genuinely unresolvable without a capture.
- **A2 — Preset B feasibility.** Validate the ~446 fps @ 1280×200 native readout claim: row-time model (~10.1 µs @ 1280 cols from the 120 fps full-res figure), HTS/VTS limits, 2-lane MIPI at ≤1.6 Gbps, Pi 5 CSI ingest at ~468 fps sustained, and whether libcamera/the driver can express a 1280×200 mode. If infeasible, name the closest feasible mode and update §2.
- **A3 — Radar evidence shape.** Pin the exact structure of `Shot.iwr6843_club_range_evidence` / `iwr6843_ball_range_evidence` as produced by the IWR runtime and consumed in `server.py` / `camera/club_delivery.py` (`range_evidence=` param). §4.2's sidecar must match it; correct the spec with the real schema.
- **A4 — IWR depth sigma & bias.** Sanity-check σ≈3 mm from the chirp config (B=3.2 GHz, SNR assumption) and the 0–40 mm clubhead phase-center bias range. Search TI literature (SWRA553, mmWave range-accuracy app notes) for a better-grounded bias model; adjust eval cells if warranted.
- **A5 — Kiosk socket contract.** Extract the authoritative `shot` event payload schema and any camera endpoints the kiosk `ui/` consumes (from `server.py` emit sites + `ui/src/services/socketService.ts` + `ui/src/types/shot.ts`) so `server/app.py` can implement it exactly. Attach the schema to §8.
- **A6 — metadata.json fields.** Complete §4.1's metadata dict field list from `capture_runtime._save_capture` and `timing_summary`; the spec's list is knowingly partial.
- **A7 — Fusion algorithm risks.** Review §5 steps 3–6 against `camera/club_delivery.py`'s actual pre-impact tracking (it tracks features, not template fits). Identify failure modes the spec misses (template mismatch across club models, silhouette ambiguity of a toe-down driver from behind, blur-asymmetric bias on the leading edge) and propose gates or scope cuts.
- **A8 — Architecture cross-check.** Verify the POC touches nothing `server.py`/PR #228's refactor will break (we intentionally live in `research/` + new `server/app.py`); confirm `research/` is excluded from the production package build and pre-commit hooks will pass on the new tree.
- **A9 — Missed prior art.** Search for existing open-source golf club/head segmentation or pose models (the spec claims none exist at this vantage) and any published OERT/silhouette-fusion detail (patents US10393870, Trackman OERT materials) that changes §5.
- **A10 — 0E/0C model flaw.** Independently verify the finding that `scaled_intrinsics` varies pixel count at fixed FOV (verdict doc §3.1) and that the planned Phase-1 eval correctly introduces a real FOV/plate-scale trade this time.

## Appendix B — pre-registered eval cells (Phase 1)

Presets {A@0.656, A@1.31, B@1.33 px/mm} × sync {33 µs, 2.14 ms} × depth {stereo-3mm-reference, radar 3 mm + bias 0/10/20/40 mm} × blur {10 µs, 500 µs exposure} × clubs {driver, 7i} — with the existing 0C noise/calibration baselines. Report median + p90 impact vector per cell; highlight the best *buildable* cell (A@shipped exposure is buildable today; B assumes the strobe).
