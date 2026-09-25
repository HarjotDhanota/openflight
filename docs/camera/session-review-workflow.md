# Session review workflow: audit and architecture

Status: M1/M2 tooling. Written 2026-09-24 from a code trace of
`feat/tester-capture-pilot` at `f7c67ac` and the five replay reports of the
first Pi session (`harjot-pilot-test-1`, Arm 5, run-01). No accuracy is
claimed anywhere in this workflow.

## Data path as found

```
start-kiosk (per run) ─► session_*.jsonl ─┬─ rolling_buffer_capture (OPS I/Q + processor_config)
                                          ├─ iwr6843_capture  ──► iwr6843/*.l3dump   (absolute Pi path)
                                          ├─ camera_capture   ──► <arm>/camera/camera_*/{frames.npz,metadata.json,first|trigger|last.pgm}
                                          ├─ shot_detected (live metrics, camera_fusion_context/processing)
                                          └─ fusion_diagnostic (pending/terminal snapshots)
tester server ─► ladder.json (verdicts, impact photos as absolute paths), arm.json, setup_admission.json,
                 attempt_ledger.jsonl, impact/*.pgm, calibration/*
replay_raw_fusion.py (CLI, one shot, operator flags) ─► replay-shot-N.json   (not stored anywhere)
package_study() (HTTP request thread) ─► <tester>-openflight-mode-study.zip  (no manifest, overwritten)
Track Review / Fusion Diagnostics pages ─► read the Pi's run folders only
```

## Findings, ranked by severity and dependency

| # | Severity | Finding | Evidence |
|---|---|---|---|
| 1 | Critical | Replay is not portable. IWR replay opens the recorded absolute `capture_path`; camera replay rejects any capture outside the session folder. An extracted archive on another machine fails every IWR and camera stage. | `replay_raw_fusion.py` capture resolution; `replay_camera_fusion._capture_file` |
| 2 | Critical | There is no canonical artifact. The package is the raw tree only: no manifest, hashes, schema, derived results or review. It is rebuilt in place under one fixed name, so a downloaded copy cannot be verified against the Pi, and it runs inside the HTTP request under the ladder lock. | `tester_server.package_study`, `create_package` |
| 3 | Critical | Replay is terminal-only, one shot per run, and asks the operator for `--ops-sample-rate-hz` and `--club` although both are recorded in each capture's `processor_config`. | `replay_raw_fusion.parser` |
| 4 | High | `_source_identity()` hard-codes arm, rig hash, exposure, gain, setup hash and placement warning to `null`. All are recorded: rig snapshot hash in `session_start.config.rig_geometry.snapshot`, the session location in `config.camera_capture.output_dir`, applied exposure/gain per frame in `frames.npz`, and setup hash plus placement guard in the camera event's `tester_setup`. Exposure changes per ladder rung inside one session, so these values are per shot, and the benchmark aggregator (which demands identical identities across shots) must compare only session-level keys. | `replay_raw_fusion._source_identity`; `raw_radar_replay.benchmark_candidate_from_raw_replays` |
| 5 | High | The replay software hash includes absolute source-file paths, so identical code hashes differently in every checkout; reproduction cannot be checked. | `_replay_software_content_sha256` |
| 6 | High | Experimental OPS spin is invisible. The diagnostic snapshot and the benchmark adapter have no spin metric; values exist only in the full replay report (session 1: 3076/9009/8789/3735/2252 rpm, confidence 0.20-0.59, quality `experimental`). | `fusion_diagnostics.build_snapshot`; `_benchmark_attempt_from_raw_replay` |
| 7 | High | One word, three meanings. Diagnostic `outcome: complete` means processing ended; ladder "accepted" means green/amber picture quality; tester progress and export "accepted" mean a fused club status. None means a validated metric. | `fusion_diagnostics`; `study_ladder.LadderState.accepted`; `tester_server.arm_progress` |
| 8 | High | Rejections lose their evidence. Diagnostics keep only the status string; the camera's scene/impact candidates and reasons, IWR horizontal confidence and the dependency chain (club `rejected_no_ball` because the ball failed) are dropped. | `fusion_diagnostics._metric` |
| 9 | Medium | Impact photos are recorded with absolute Pi paths and are not shown by any review page. | `study_ladder.record_photo`/`finish_photo` |
| 10 | Medium | Track Review annotation drafts live in browser storage and never reach the package. | `track-review.html` |
| 11 | Medium | Each Track Review frame request reads and decodes the whole `frames.npz` (about 24 MB at 1280×800 × 24 frames, 59 frames at 288 fps) twice in memory; the browser loads several at once. The saved `first/trigger/last.pgm` previews already exist. | `track_review._read_capture` |
| 12 | Medium | The contribution packager holds every file payload in memory before writing. | `contribution_package.build_contribution_package` |
| 13 | Medium | The camera save queue is unbounded; the trigger ring refuses only while a capture is still collecting frames, so a false-trigger storm with slow SD writes can accumulate full captures in RAM. | `capture_runtime._ready` |
| 14 | Low | The OPS replay config records the class `SAMPLE_RATE`, not the instance rate. Harmless while every kiosk uses 30 kS/s; noted, not changed. | `RollingBufferProcessor.replay_config` |

Session 1 status, reproduced from the reports: OPS ball and club speed on all
five; IWR vertical launch accepted on all five (three single-channel, one with
a track-speed warning); IWR horizontal withheld as `hlcmf_v1_low_coherence` on
all five; IWR club path `rejected_no_club_track` on four and
`rejected_no_impact_time` on one; camera `rejected_reference_ball_not_found`
on all five with every scene candidate failing the size/hitting-zone gate and
the impact detector finding no persistent departure; camera club delivery
`rejected_no_ball` as a consequence. Camera replay matches the recorded live
result, so the rejection is deterministic.

## Status vocabulary

Every metric in the review carries exactly one of:

| Status | Meaning |
|---|---|
| `accepted` | The production estimator accepted the value under its own rule. Not an accuracy claim. |
| `experimental` | A value exists but is a candidate (for example spin quality `experimental`); shown, never promoted. |
| `rejected` | The estimator ran and refused; the recorded reason and evidence are shown. |
| `not_requested` | The stage was not asked for (for example the total-speed projection without a manifest). |
| `unavailable` | A required input is absent (no capture, no event, or an upstream stage produced nothing). |
| `processing_failed` | The stage raised; the error text is kept. |

Diagnostic `complete` is shown as "processing finished"; ladder "accepted" is
shown as "usable picture"; neither is a fused-metric verdict.

## Decision: one canonical session bundle

Current approach: raw tree ZIP + terminal replay + three review pages.
Alternative chosen: one immutable, hash-inventoried **session bundle** per
tester, produced by one background job, readable on the Pi and anywhere else.

```
<tester>-session-bundle-<UTC>.zip          (+ .zip.sha256; in <sessions>/.bundles, never overwritten)
  bundle_manifest.json   schema openflight.session_bundle.v1: every entry's path, size, sha256, role
  review.html            the review page; opened from disk it imports this ZIP and verifies hashes
  <tester>/...           raw evidence, byte-identical, same layout as the old package (role raw)
  <tester>/annotations/<arm>/<run>/<capture>.tracks.json   saved Track Review tracks (role annotation)
  <tester>/diagnostics/tester-server.log*                  service logs (role diagnostics)
  <tester>/analysis/session_review.json   per-attempt metrics, statuses, reasons, evidence, identity (derived)
  <tester>/analysis/attempts.csv          one row per attempt and metric
  <tester>/analysis/report.md             human-readable per-attempt summary
  <tester>/analysis/replay/<arm>/<run>/shot-NNN.json   full replay reports with relative paths
```

The bundle root mirrors the sessions folder, so every path in the review is
the same on the Pi and inside the bundle.

- The job (**Analyse, review & package**) runs as a separate low-priority
  process, one at a time, refused while a capture runs. Its state is written
  atomically to `<tester>/analysis/job.json`, so a refresh, reconnect or tester
  restart shows it; an interrupted job resumes and skips shots whose replay
  inputs are unchanged.
- Replay locates captures relative to the session folder, reads sample rate
  and club from the recorded processor config, records per-shot identity, and
  hashes software by repository-relative path.
- Archive work streams files in fixed-size chunks and hashes while writing;
  nothing holds a whole capture or the whole session in memory.
- Reproduction: `scripts/analysis/analyze_tester_session.py --verify <zip>`
  re-runs the analysis from the bundle's own raw files and compares every
  derived value (floats within 1e-9); mismatches are listed, never hidden.
  Bitwise equality across CPU architectures is not claimed.
- Contribution packages stay a separate consent-bound derivative.

Expected benefit: the operator loop becomes capture → one button → review →
one download, and the same review opens on any machine without a terminal.
Cost: one new module pair and page; the old package layout is kept inside the
bundle so existing extraction habits keep working. Acceptance gates are not
changed; no estimator threshold is touched.

## Implementation status

| # | Finding | Status |
|---|---|---|
| 1 | Non-portable replay | Fixed: captures located inside the session folder; bundle-relative report paths (`b5ad7fc`) |
| 2 | No canonical artifact | Fixed: immutable session bundle with manifest, roles, hashes and sidecar (`df43f74`, `cfa5a50`) |
| 3 | Terminal-only replay | Fixed: one background job replays every shot from recorded sample rate and club (`b5ad7fc`, `df43f74`) |
| 4 | Dropped identity | Fixed: arm, rig hash, applied exposure/gain, setup hash, placement, with per-field evidence; per-shot keys excluded from session aggregation (`b5ad7fc`) |
| 5 | Path-dependent software hash | Fixed: replay hashes the capture snapshot's allowlist by relative path (`b5ad7fc`) |
| 6 | Hidden experimental spin | Fixed in the review: `experimental` with confidence and quality; the processor's own reliability rule decides `accepted` (`15b2745`) |
| 7 | Overloaded "accepted"/"complete" | Fixed in labels: "processing finished", "usable pictures", "with a fused club result" (`c1c97e0`) |
| 8 | Flattened rejections | Fixed in the review: scene/impact candidates, reasons, confidence and dependency chain (`15b2745`) |
| 9 | Impact photos | Fixed: stored tester-relative, shown per attempt; legacy absolute paths still resolve (`cfa5a50`, `c1c97e0`) |
| 10 | Browser-only drafts | Fixed: hash-bound save into `<tester>/annotations/`, bundled as `annotation` (`dbd359e`) |
| 11-13 | Pi memory | Fixed: single-frame reads, streamed packaging, camera save backlog bounded at three clips (`ce2c5ba`) |
| 14 | Class sample rate in replay config | Open, noted only |

Fusion diagnostics still show only the live snapshot allowlist; the full
replayed evidence is in the session review. The live snapshot schema was left
unchanged.

## Estimator questions blocked on frames or hardware

1. Camera reference ball, session 1: all scene candidates sit at y 193-246 on
   800-row frames while the gate admits y 320-760 (0.40-0.95 of height). The v3
   geometry (lens 95 mm, ball centre 21 mm above the floor, camera pitch about
   +3.4° measured on the walk) predicts the resting ball below the image
   centre, near y 450-550. A 180° orientation mismatch would put it near
   y 250-350, which is close to the candidates, but the candidates also move
   from x 584 to 1058 and range from 8.7 to 31 px between shots, which a
   re-teed ball does not do. Decide from the frames in the review page, with
   the recorded `camera_capture.rotate_180`, before touching the gate.
2. The 9-30 px diameter gate is resolution-independent. At 1280×800 with the
   nominal 933 px focal a ball nearer than about 1.33 m exceeds 30 px and is
   rejected regardless of position. Needs a per-mode size gate, validated on
   saved 1:1 frames.
3. IWR horizontal low coherence on all five shots: needs dumps from a known
   straight shot line to separate coherence from phase-reference error.
4. IWR club track absent on four of five: needs the dumps inspected for club
   returns inside the club window before any window change.
5. OPS spin: confidence 0.20-0.59 with SNR near 1.7; stays experimental until a
   reference (TrackMan or marked-ball camera) is paired with the same shots.
