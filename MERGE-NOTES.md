# Upstream merge, 12 Oct 2026: resolution log

- **Merge:** upstream open-flight/openflight `main` @ `8d681d0d` into fork
  `feat/tester-capture-pilot` @ `24aa2750`, on branch `merge/upstream-2026-10-12`.
  One merge, no rebase (D30).
- **Decisions applied:** D30 (a)-(e), D31, D17 and the upstream-impact analysis
  of 11 Oct (`openflight-test-bench/archive/analysis-archive/upstream-impact-2026-10-11/report.md`,
  "the analysis" below).
- **This log lists only the hunks where one side won** or where the result is
  neither side. Every other conflict hunk kept both sides.

## Conflicting files

| File | Side that won | Decided by |
|---|---|---|
| `src/openflight/ops243.py` | Upstream: `PA` re-arm, no input clear once live, the orphan-marker check. Fork: no flush on any wait, `missed_edge`, trigger phase, stored firmware, `S#` ValueError. The lost-dump detectors are merged into one | D30 (d), (e); analysis `ops243.py` row |
| `src/openflight/rolling_buffer/trigger.py` | Fork, plus upstream's `_rearm` helper and the lost-dump re-arm on the hardware path | D30 (d), (e); analysis `trigger.py` row |
| `src/openflight/rolling_buffer/monitor.py` | Fork (log wording only) | Analysis `monitor.py` row ("either") |
| `src/openflight/launch_monitor.py` | Both: the shared `SPIN_CONFIDENCE_RELIABLE` (already in the fork) and the fork's carry statuses | Analysis #276; D31 |
| `src/openflight/server.py` | Fork for fusion, lighting, D17 carry, enrichment diagnostics, OPS-only refusal and the gate-edge listener. Both for imports, BLE, cloud raw and `save_dumps`. BLE mapped onto the fork's enrichment result. `--camera-shot-analysis` refused | D30 (a), (b), (c); D17; D31; analysis `server.py` row |
| `src/openflight/camera/ball_flight.py` | Fork; upstream's `estimate_horizontal_launch` and its helpers kept, unwired | D30 (c); analysis `ball_flight.py` row |
| `src/openflight/camera/club_delivery.py` | Fork (no cross-shot tracker, no `reference_ball` from the refused path) | D17; analysis `club_delivery.py` row |
| `src/openflight/camera/club_motion.py` | Fork (its `detect_impact_reference_ball` and `detect_impact_frame` already carry the pixel-scale and setup-region forms) | Analysis `club_motion.py` row |
| `src/openflight/cloud/filtering.py` | Both: KL's `shot_updated` merge applied in both modes, raw mode also keeps the raw `shot_updated` line | Brief (#287); analysis `filtering.py` row |
| `src/openflight/sim/resolver.py` | Fork (D31 carry resolution) | D31 |
| `tests/test_server.py` | Fork for the camera-fusion tests and the hardware-trigger plumbing; upstream's `TestHardwareTriggerPlumbing` appended | D30 (c); D17 |
| `tests/test_ops243.py` | Both; upstream's orphan tests rewritten for the single detector | D30 (d), (e) |
| `tests/test_ballistics.py`, `tests/test_camera_club_motion.py` | Both | Analysis (both) |
| `tests/test_sim_resolver.py` | Fork (upstream's two carry tests were already in the fork via #276) | D31 |
| `docs/hardware-trigger.md` (add/add) | Upstream, plus the fork's OPS-only scope, option ranges and diagnostics/provenance sections | Brief (docs); analysis row |
| `docs/how-it-works/rolling-buffer.md` | Upstream, plus the OPS-only sentence | Brief (docs) |
| `docs/how-it-works/ballistics.md` | Fork (the fusion-unit carry paragraph) | D31 |
| `docs/reference/cli.md` | Fork for fork flags | Brief (docs) |
| `docs/changelog.md` | Both | Brief (docs) |
| `ui/package-lock.json` | Regenerated: fork lockfile, then `npm install` from the merged `package.json` (adds Electron 44) | Brief |

## Hunks where one side won, with the reason

### `ops243.py`
- **Wait signature and the flush hunk: fork.** Upstream's `discard_pending_input=True`
  default would bring back the sound-path flush that lost 154 s of OPS data (P7-1).
  The parameter is dropped entirely, so no caller can ask for a flush. Rule:
  "never take upstream's side of the flush hunk" (D30 (e)).
- **`detect_orphaned_dump` and `last_hardware_trigger_orphaned_dump`: neither, merged.**
  Upstream's orphan marker (a `{"Q"` before any capture marker) now feeds the fork's
  `discarded_dump` outcome, with no opt-in, on both trigger paths (D30 (d)). A short
  orphaned tail below `DISCARDED_DUMP_MIN_BYTES` is now caught too. The outcome
  (`last_hardware_trigger_outcome`) is the one source of truth; the upstream
  attribute is gone.
- **Trigger phase, `ops_wait_armed` trace mark, pending-at-start marker: fork.**
  Diagnostics the fork relies on (stage-1b trace); upstream had nothing there.
- **`_internal_trigger_firmware_version`: fork.** Recorded in trigger diagnostics.
- **Silent duplicate, not a conflict hunk.** Git auto-merged both sides' copies of
  `_drain_rearm_serial`, `configure_for_internal_speed_trigger`,
  `_restore_internal_speed_trigger_settings`, `rearm_internal_speed_trigger` and
  two helpers, so the file defined each twice (the later, upstream copy silently
  won). Kept one copy each: the fork's, with upstream's `PA` re-arm body and its
  "no `reset_input_buffer` after the live threshold" change in configure (D30 (e)).

### `rolling_buffer/trigger.py`
- **Hardware wait call: fork** (cancel event, first-byte timer); upstream's
  `discard_pending_input=False, detect_orphaned_dump=True` arguments no longer exist.
- **Lost dump on the hardware path:** re-armed with `PA` as upstream does, but logged
  with the fork's reason `dump_discarded` (the reason the sound path and the ready
  light already use), not upstream's `orphaned_dump`. One detector, one reason
  (D30 (d)). `docs/hardware-trigger.md` step 8 now says so.
- **Diagnostics decoration and `ops_timing`: fork.**

### `server.py`
- **`_ShotEnrichmentResult`: fork shape** (`diagnostic_outcome`/`diagnostic_reason`);
  no `skipped_reason` (D30 (a)). BLE is mapped onto it by `_phone_shot_state`.
- **KL's auto-merged `skipped_reason="queue_full"` line** in `_handle_shot_detected`
  (it passed `enrichment=` to `_abandon_shot_enrichment`, which takes none): removed.
  It would have raised a TypeError whenever the deferred backlog filled (D30 (a)).
- **Phone publishes (D30 (b)), neither side as written.** An OPS-only first result
  whose late IWR or camera result can still arrive goes to phones as provisional
  (`final: false`, `pending`); the late update is the same shot's final version
  (same `event_id`). A shot whose late result is abandoned gets a final `skipped`
  (`late_result_abandoned`). Partial results that nothing will follow are final
  `skipped` with the fork's `diagnostic_reason`.
- **Camera hunks (#290): fork.** Upstream's poor-lighting early return would withhold
  every camera metric (the fork labels instead, D15), and its `ball_tracker=` /
  `ReferenceBallTracker` arguments would bring back cross-shot ball borrowing (D17).
  Upstream's auto-merged `reference_ball=` parameters, the `estimate_horizontal_launch`
  import, the `shot_analysis_enabled` config key and the `analysis_started` timer were
  removed with it.
- **`--camera-shot-analysis`: refused** with a `parser.error` that explains why (D30 (c)).
  The flag turns the camera on, so every unit it runs on is a fusion unit: it is
  refused in every configuration. Upstream's `--mock` refusal and startup print were
  unreachable and are removed.
- **Carry (#276): fork** (D31: fusion units never fill in; the fork already had #276).
- **Trigger-threshold validation: fork** (also rejects non-finite values).
- **`save_dumps`: both** — the fork's IWR arguments with upstream's
  `args.debug or _cloud_raw_uploads_enabled()` (#287).

### `camera/*`
- **`ball_flight.py`:** `CameraBallEstimate` keeps both sides' fields. `_clean_launch_path`
  keeps the fork's list form; upstream's `_track_launch_ball` converts it to the
  frame-keyed dict it needs, so the two copies of the same algorithm became one.
- **`club_delivery.py`:** fork's `_detect_impact_index` (its own presence model), no
  `ball_tracker` parameter.

### Tests
- **Fork's `GC` re-arm test dropped** (`test_internal_trigger_rearm_restores_cached_settings`);
  upstream's `test_rearm_sends_only_pa_and_keeps_detector_settings` covers re-arm
  (D30 (e)).
- **Upstream camera-analysis tests dropped** (the opt-in wiring and tracker they test are
  refused, D30 (c), D17).
- **Upstream's `test_final_enrichment_describes_what_happened`** rewritten for
  `_phone_shot_state` (D30 (a)).
- **Upstream orphan tests** rewritten for the single detector (D30 (d)).

## Outside the conflicts

- **`pyproject.toml`:** upstream's `[tool.uv] environments` listed only Linux and macOS,
  so `uv sync` refused to run on Windows, where the fork and the bench are developed.
  Added `sys_platform == 'win32'`; Bless stays Linux-only by its own marker.
- **#266 (Ferguson 2022 quadratic Cd/Cl) is live** after the merge: it auto-merged,
  because the fork never changed `ballistics.py`. It replaces the Hill-form constants the
  fork fitted to its own 24-shot TrackMan capture. Both are fitted physics (class (c),
  on hold); the owner decides whether to keep it. See the merge report for its effect
  on fork and bench results.
- **BLE carry:** phones get `estimated_carry_yards` only, never the committed carry. On a
  fusion unit that is `null`; on an OPS-only unit it is the table estimate, which can
  differ from the kiosk's labelled simulator estimate. Upstream's protocol; not changed.
