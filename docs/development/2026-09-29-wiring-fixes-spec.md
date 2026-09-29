# Wiring fixes: spec

Date: 29 September 2026. Branch: `feat/tester-capture-pilot` (fork). Status: approved 29 Sept (all seven decisions as recommended); nothing implemented. Plan: `2026-09-29-wiring-fixes-plan.md`.

This spec answers every finding in the wiring audit (`2026-09-29-wiring-audit.md`, same folder) and in the five reviews behind it. The IDs are the audit's. A few findings from the reviews were not in the audit's tables; they get new IDs here, marked *(new)*.

Every item has four parts:

- **Requirement**: what must be true afterwards.
- **Design**: how to get there.
- **Tests**: what proves it. Unit and flow tests are required. A field check is listed where only the rig can prove it.
- **Size**: S = under an hour; M = a few hours; L = a day or more, or it needs data.

Items marked **Decision** need Harjot's choice before they are built. They are also collected in section 8.

## 1. Blockers for the next field test

### B1. A finished setup must survive the ladder and swings

**Requirement:**
- Reading the setup state (the page's once-a-second GET) never changes it.
- A finished setup stays usable while the kiosk owns the LIS3DH.
- Physical re-validation happens only at the point of use.

**Design:**
- In `_range_state`, the terminal branch keeps its identity checks: the final reference equals the current reference, and the epoch loads. It stops calling `bound_range_setup`.
- A failed identity check on a GET is returned as a transient warning and is not persisted.
- `admitted_range` (capture start) re-validates physically, as before.
- The ladder/swing kiosk owns the LIS3DH from `start_mode` until its job ends. Every job-finish path calls `enclosure.start()`. `admitted_range` waits up to 3 s for a stable reading before judging.

**Tests (FakeTilt whose `stop()` makes `reading()` return "off"):**
- After ladder start, 5 GETs leave the terminal state unchanged.
- Resume after Pause works.
- A genuine rig move at capture start is still refused.

**Size:** M.

### B2. The ladder uses the gain screen's light-equivalent gain

**Requirement:** each rung's starting gain comes from the saved `gain_at_300_equivalent` when it exists.

**Design:**
- `gain_facts` reads `gain_at_300_equivalent` and `too_bright` from arm.json (`read_arm_state`).
- The lambda uses `is not None`, not `or`.

**Tests:** a flow test with a too-bright gain screen (equivalent 0.45) shows full-30 starting at 4.5, not 10.

**Size:** S.

### B3. Ball-size gates follow the camera mode

**Requirement:** no fixed pixel gate for the resting ball or its flight. The gates follow from the focal length in use and the hitting area (1.0-2.5 m).

**Design:**
- One shared function, `reference_ball_pixel_bounds(focal_px, image_size)`:
  - diameter from f·42.67 mm/2.5 m to f·42.67 mm/1.0 m, with ±30 % slack;
  - component area from the diameter bounds;
  - per-frame step cap scaled by the focal ratio.
- Replace every fixed gate:
  - `ball_flight.REFERENCE_BALL_DIAMETER_PX`, the `9 <= d <= 30` check, the 400 px² area cap and the 38 px step cap;
  - `club_delivery.ReferenceBallTracker.resolve`;
  - `club_motion.detect_reference_ball`'s 600 px² cap and its 9-30 plausibility filter.
- Callers pass the focal length they know (the camera geometry at swing time; the arm in the tester).

**Tests:**
- A synthetic 36 px ball at 1280×800 (1.1 m) is accepted by the detector, the club-delivery tracker and ball flight.
- A 16 px ball at 640×400 is still accepted.
- A 90 px blob is refused in both modes.

**Size:** M.

### B4. The gain screen's choice is correct in mixed light

**Requirement:** a clipped hitting zone never leads to a brighter saved gain. "Too bright" never asks for more gain. "Needs light" only appears when the brightest gain was dark and unclipped.

**Design:** judge the zone metrics. `usable` means zone clipped ≤ 5 %.
1. **In-band usable gains exist:** pick the lowest.
2. **Otherwise, some gain is usable:** pick the brightest usable gain. It is `lighting_required` only if the brightest tested gain overall is usable and below the band. It is `mixed_light` when a brighter gain was rejected for clipping.
3. **No gain is usable, not even the lowest:** it is `too_bright`. `gain_at_300_equivalent = lowest × min(1, target/level) × max(0.1, 1 − 2·clipped_fraction)`. That is never above the lowest gain.

**Tests (fixtures):**
- **Mixed sun and shade:** zone median 71, gain 1 at 3 % clipped, gain 2 at 12 % clipped → gain 1, `mixed_light`.
- **29 Sept outdoor screen:** `too_bright`, equivalent ≤ 1.
- **Indoor dim:** `lighting_required` at 12.

**Size:** S.

### B5. Black level and light index are known in sunlight

**Requirement:**
- The black level never falls back to the ring median.
- The light index comes from the hitting zone.

**Design:**
- **Black level:** read libcamera's `SensorBlackLevels` from frame metadata (scaled to 8 bits). Fall back to the tuning file's value, recorded with its source.
- **Gain screen:** records `black_level_dn` per setting, and `light_index` is fitted on `zone_median − black` against exposure × gain, over unclipped zone rows.
- **Setup exposure search:** uses the recorded black level. If none is known, it refuses to judge signal ("black level unknown") rather than subtracting the background.
- **Ladder:** no `or 0.0` / `or 0.05` fallbacks. A missing light index makes the photo use the rung's own exposure × gain.

**Tests:**
- An outdoor screen fixture gives a non-null light index.
- `assess_static_exposure` with a None floor is refused, not contrast-gated.
- The ladder with a missing light index uses the rung's own controls for the photo.

**Size:** M. **Decision D1:** confirm `SensorBlackLevels` from the Pi (one command).

### B6. One light rule for the pre-check and the swing verdict; only darkness skips shorter rungs

**Requirement:** the pre-rung check and the per-swing verdict judge light the same way. A rung that fails for any reason other than being too dark skips only itself.

**Design:**
- One function, `judge_light(frames, black_level, expected_ball)`, returns the verdict, the cause (`ball_clipped`, `ball_dark`, `zone_dark_no_ball`, `zone_clipped_no_ball`, `background_clipped`, `ok`) and colour, and both callers use it:
  - with a ball found, only ball clipping or ball darkness is red; a dark or clipped zone is amber;
  - with no ball, the zone rules apply;
  - the zone "too bright" threshold is the same in both (50 %).
- `LadderState` records a failure cause on the rung. `record_swing`'s "failed" skips the shorter rungs only when the cause is `too_dark`.

**Tests:**
- Dark mat with a bright ball: the pre-check passes and the verdicts are not red.
- Two "too bright" reds fail only that rung, and full-50 stays pending.
- Two "too dark" reds still skip the shorter rungs.

**Size:** M.

### B7. Gain corrections move the right way

**Requirement:** a too-bright ball always gets a lower gain; a too-dark ball always gets a higher one.

**Design:**
- Too bright: `gain × min(0.8, 150/median)`, and ×0.5 if the median has clipped.
- Too dark: `gain × max(1.25, 150/max(median − black, 1))`.

**Tests:** median 120 with 7.5 % clipped at gain 2 → suggested gain below 1.6.

**Size:** S.

### B8. Older ladder files load

**Requirement:** a `ladder.json` from before a rung was added loads, with the new rungs pending. Unknown rungs are kept but ignored.

**Design:** migrate on load. Add missing rungs from `LADDER` as pending, record `"migrated_from_rungs": [...]`, and save.

**Tests:** a 29 Sept-style ladder file without full-50/full-30 → `current` works, `to_dict()` works, and they are pending.

**Size:** S.

## 2. Face angle and fusion correctness

### F1. Face angle only uses an accepted, displayed club path

**Requirement:**
- The D-plane face angle uses the same club path the kiosk shows, and only if that path is accepted.
- It records which path it used.

**Design:**
- One server function, `displayed_club_path(shot)`, returns `(value, source)` with precedence: accepted IWR `club_path_deg`, then camera fused chained, then camera fused OPS. It returns nothing for candidate, out-of-bounds or noisy statuses.
- `_attach_experimental_face_angle` uses it and records `face_angle_path_source`.
- The kiosk shows the same value.

**Tests:**
- An out-of-bounds IWR candidate with no camera path → no face angle, status `missing_accepted_club_path`.
- An accepted camera path → face angle with its source recorded.

**Size:** S.

### F2. One zero direction for every club path

**Requirement:** every club path (IWR, camera chained, camera-OPS) is measured from the same zero: the unit's boresight. The target-line correction comes later, from the alignment stick.

**Design:**
- Remove the camera-OPS branch's rotation by the ball's observed azimuth, or apply the same rotation in the chained branch. The choice: boresight in both.
- Record `club_path_frame: "unit_boresight"` on every path.

**Tests:** the same synthetic club motion gives the same path (±0.2°) in both branches, with the ball placed 67 px off centre.

**Size:** M.

### F3. Apparent and corrected radar ranges never mix

**Requirement:** every range carries its space. Comparisons happen in one space.

**Design:**
- IWR evidence types carry `range_space`.
- `club.find_club` gates and scores against `tee + bias` (apparent), or corrects the track first.
- The camera's ray-sphere intersections use corrected ranges.
- Remove the −2 ms `club_impact_correction_s` at the same time, then re-fit it. It only stays if an offline replay shows a residual.

**Tests:**
- Unit: club gate edges in apparent space.
- Replay: the stored August and July sessions before and after, with path, AoA and impact time. It is accepted only if TrackMan-scored metrics do not get worse.

**Size:** L. **Decision D2:** which stored sessions count as the replay reference.

### F4. Rejected radar evidence is never used as depth

**Requirement:** only accepted IWR ball or club tracks feed the camera's depth. A rejected static candidate carries no range as its value.

**Design:**
- `server.py:3139` and the club evidence export only on acceptance. Otherwise they export with `status` set, and the camera path downgrades to camera-only.
- `_guided_iwr_candidate` sets `radar_slant_range_m=None` unless accepted, keeping the number in the evidence.
- `_camera_to_iwr_ranking` checks the status.

**Tests:** a rejected ball track leaves `camera iwr_range` unused, and the horizontal source becomes camera-only experimental.

**Size:** S.

### F5. The horizontal-launch tile shows its own source and confidence

**Requirement:** the H-launch tile uses `launch_angle_horizontal_source` and `_confidence`, and labels camera-only and legacy sources.

**Design:** change `liveMetrics.ts` to use per-axis fields, with labels for `camera_only_experimental` and `camera_legacy_fallback`.

**Tests:** liveMetrics unit tests for the three combinations the audit named.

**Size:** S.

### F6. An accepted radar horizontal beats a size-only camera horizontal

**Requirement:**
- The displayed horizontal launch prefers an accepted IWR value over a camera size-only estimate.
- A camera-only value goes into its experimental field.

**Design:**
- Reorder `ball_flight` selection (811-820) so accepted IWR comes first.
- `server.py:3829` only overwrites when the camera value outranks the current one.

**Tests:** accepted IWR 3.0° plus camera-only 5.0° → displayed 3.0°, with the camera value kept as experimental.

**Size:** S.

### F7. Camera ball flight times impact from the ball, not the trigger

**Requirement:** ball-flight radar time is anchored on the ball-departure frame, as club delivery already is.

**Design:** reuse club delivery's departure-frame logic in `ball_flight.py:527-536`. Use sensor timestamps where available.

**Tests:** a synthetic clip with contact 2.6 ms before the trigger recovers H and V within 0.05°.

**Size:** M.

### F8. The IWR horizontal phase reference

**Requirement:**
- Radar horizontal launch and IWR club path are marked uncalibrated until a horizontal phase reference for this board exists.
- Face angle does not use an uncalibrated IWR path.

**Design:**
- Status `azimuth_uncalibrated` on radar horizontal and on the IWR path. The kiosk labels them.
- The reference itself comes from the alignment-stick session (B4 in the setup spec), by in-situ registration against camera tracks, or from a corner reflector at a known azimuth.

**Tests:** without a reference, the statuses are set and face angle uses camera paths only.

**Size:** M (code) plus field. **Decision D3:** the method.

### F9. `horizontal_offset_deg` applies to path too, or goes away

**Requirement:** a horizontal offset rotates the ball direction and the club path together.

**Design:** apply it in the path branch of `geometry_contract.py:239-252`, or remove the parameter.

**Tests:** a +2° offset shifts launch and path by 2° each; face angle is unchanged.

**Size:** S.

### F10. No July lateral tee offset

**Requirement:** the LCMF inversion uses this session's lateral geometry.

**Design:** `LATERAL_TEE_OFFSET_M` becomes a Calibration field from the rig (0 for v3), used in both the inversion and tee_x.

**Tests:** LCMF tests pass with 0. A replay is unchanged within 2 mm.

**Size:** S.

### F11 (new). Two-ray radar height uses the array's phase centre

**Requirement:** the two-ray model's radar height is the virtual array's vertical phase centre, not the RX row centre.

**Design:** derive the phase centre from the antenna geometry (TX and RX patch positions) in the rig file, and record both.

**Tests:** a unit check against the LEVM layout.

**Size:** S (if the TX-RX vertical offset is ~0, record that and close).

### F12 (new). OPS cosine correction uses the OPS position

**Requirement:** the OPS speed correction uses the OPS's own offsets from the rig file.

**Design:** pass the OPS lateral, height and forward offsets.

**Tests:** a unit test with an 85 mm lateral offset.

**Size:** S.

### F13 (new). One m/s-to-mph constant

**Design:** `tracking.py:123` uses the shared 2.23694.

**Size:** S.

## 3. Range setup and radar

### S1. Camera-window re-selection keeps the full-window statistics

**Requirement:** the camera window only chooses which cluster may be the ball. Normalisation, MAD and the changed-fraction limit use the full qualification window.

**Design:** `compare_static_range_profiles(..., candidate_window_m=None)`. The search window stays full; clusters outside `candidate_window_m` are dropped before ranking.

**Tests:**
- The auditor's synthetic case (1.0 m ball, 3-bin cluster) is accepted inside the window.
- A person at 2.8 m is excluded.
- The field fixtures keep their outcomes.

**Size:** S.

### S2. Swings never get a camera size estimate as the radar tee range

**Requirement:** `--iwr6843-tee-m` is only ever an accepted IWR range.

**Design:**
- `unqualified_tee_range_choice` returns only an accepted IWR candidate, camera-steered or not.
- With no accepted IWR range, swings start with `--iwr6843-tee-range-pending`, and the page says so.

**Tests:** a rejected IWR plus a camera candidate → pending, not `--iwr6843-tee-m`.

**Size:** S. **Decision D4:** confirm. The alternative is to pass the camera value with a source flag and have the server withhold anchor-dependent metrics.

### S3. The records say what swings used

**Requirement:**
- The tester's records name the tee range, lens height and ball height the swings ran with, where each came from, and whether it was unqualified or camera-steered.
- The server logs the source in `session_start`.

**Design:**
- `_tee_range_cli_args` returns the arguments plus a `handed_to_swings` record: `tee_m`, `candidate_id`, `qualified`, `camera_window` outcome, `solved_camera_height_m` and source, and `ball_height_m` with basis `assumed_on_surface`.
- The record is written to arm.json, `setup_admission.json` and the run's `tee_range.json`.
- A new server arg, `--iwr6843-tee-range-source`, is logged in `session_start`.

**Tests:** an unqualified flow test finds the value and the candidate in all three files and in `session_start`.

**Size:** M.

### S4. The lens height comes from the 1280×800 search only

**Design:** `_setup_camera_height_m` considers only the arm5 candidate. Arm6's solve is recorded as a check.

**Tests:** arm5 consistent plus arm6 `unit_raised` → no override.

**Size:** S.

### S5. A camera window that can't be applied rejects

**Design:**
- `not_rechecked` makes the candidate unusable for swings and for the height solve.
- An empty intersection of the camera window and the interval is a rejection (`camera_window_disjoint`).

**Tests:** a narrow qualification interval disjoint from the window gives a rejection.

**Size:** S.

### S6. Pending mode still carries the solved lens height

**Design:** the pending branch also passes `--solved-camera-height-m` when one was used, and records it. This also closes the audit's raw-only gross-height case.

**Tests:** pending plus a unit on a box → the kiosk gets the solved height.

**Size:** S.

### S7. The hitting-area test uses lens distance

**Design:** the ray tests in `_hitting_area_reason` use `size_range` (lens to ball) rather than the radar slant range.

**Tests:** a ball at the edge of the area is judged the same as the analytic lens-frame answer.

**Size:** S.

### S8. The scene-change gate accounts for the power-difference cross term

**Requirement:** until complex subtraction replaces power differencing, the leak budget includes the cross term 2·|c|·|ℓ| with the clutter already in the ball's bins.

**Design:** add the term to `_blocking_loss`, or drop this item when complex subtraction lands (planned).

**Tests:** a synthetic strong reflector 8 bins away with a clutter-filled ball bin is rejected.

**Size:** S (interim).

### S9. The 640×400 validation must agree

**Requirement:** the arm6 range agrees with arm5's within their combined uncertainty, or the setup is flagged.

**Design:** in `_finalize_range_state`, compare and record `validation_agreement`. Disagreement → `validation_disagrees`, shown on the page. It does not block in test mode.

**Tests:** fixtures with agreeing and disagreeing arm6 results.

**Size:** S.

### S10 (new). A missing pitch reading refuses rather than assuming 0°

**Design:** `_reference_ball_camera` raises when `camera_pitch_deg` is missing, instead of using the boresight.

**Tests:** a reading without pitch leads to a refusal.

**Size:** S.

### S11 (new). Names say what the values are

**Design:**
- Rename `floor_radar_range_m`, `floor_point_lfu_m` and the `*_floor_plane*` sources to size-derived names.
- Bump the policy version, and accept the old names when reading stored evidence.

**Size:** S.

## 4. Tester flow, ownership and records

### T1. The page shows "too bright"

**Design:**
- `study_overview` sends `too_bright`, `mixed_light` and `gain_at_300_equivalent`.
- The page shows the light panel without `light_index`, the step-B label becomes "too bright: shorter exposures used", and the badge appears.

**Tests:** Playwright states for a too-bright arm.

**Size:** S.

### T2. `start_mode` can't strand the LIS3DH

**Design:**
- Stop the LIS3DH inside the `try` whose `except` restarts it, and use a `finally` to restart it on any failure before the job starts.
- `next_run_directory` numbers runs max index + 1.

**Tests:**
- A forced `FileExistsError` leaves the reading active.
- A run gap (run-01, run-03) gives run-04.

**Size:** S.

### T3. Swings are counted against the controls they were taken at

**Requirement:** a swing counts for a rung only if its trigger-time controls and purpose match the rung.

**Design:** the verdict reads `metadata.auto_exposure` (exposure, gain, `controls_purpose`) and compares it with the rung. Mismatches are recorded as `ineligible_captures`, with no colour and no count.

**Tests:**
- A capture taken during a gain correction is ineligible.
- A capture taken at the rung's final gain counts.

**Size:** M.

### T4. Clip records state the controls actually requested

**Design:**
- `capture_runtime` writes `controls_at_trigger` (requested and applied) into `metadata.json`.
- `capture_facts` and `export_session` read it, falling back to `auto_exposure`, never to the startup `settings`.

**Tests:** a ladder capture at 30 µs reports 30 µs in facts and export.

**Size:** S.

### T5. Impact photos expose correctly in sun

**Design:**
- The photo uses gain 1-2 and 10 µs to the frame limit.
- It is aimed with the zone light index from B5, or scaled from the rung's own exposure × gain when that is missing.

**Tests:** an outdoor light index → a photo exposure under 100 µs at gain 1.

**Size:** S.

### T6. The ladder's ball detector is told what size to expect

**Design:**
- `ball_light` passes `expected_diameter_px` and `expected_row_px` from the admitted tee range, the arm's focal length and the tilt.
- Without an admitted range, it passes the B3 bounds.
- A detector exception means "no ball".

**Tests:** a shadow blob of the wrong size is not taken as the ball.

**Size:** S.

### T7. Pre-rung frames are checked against the applied controls

**Design:**
- `KioskClient.frames` returns the per-frame exposure and gain.
- The pre-check waits (up to 1 s) until they match the request within tolerance, and records the applied values.

**Tests:** a fake kiosk that applies the controls late → the rung is judged on matching frames only.

**Size:** S.

### T8. Old failure messages don't resurface

**Design:** clear `capture_failure` and `camera_capture_failure` on the next successful transition, and show a failure only when it was set in the current transition.

**Tests:** fail, retry and succeed, then a different failure → the page shows the new one.

**Size:** S.

### T9. A capture result is finished once

**Design:** `_finish_static_capture_locked` requires `phase == f"{kind}_capturing"` and `evidence[f"{kind}_capture_id"] == capture_id`.

**Tests:** a late empty-capture callback during `ball_capturing` changes nothing.

**Size:** S.

### T10. Stop and timeout don't clear the hardware check

**Design:** a `cancelled` status skips the `iwr_preflight = False` invalidation.

**Tests:** Stop during a capture → no new hardware check required.

**Size:** S.

### T11. Ownership is checked before side effects

**Design:**
- `run_action`, `start_mode` and the manual live start check `_range_resources_busy()` and `release()` first.
- Run directories and admissions are created only after the job is accepted.

**Tests:** a refused start leaves no run folder.

**Size:** M.

### T12. Polls don't write eligibility records

**Design:**
- Polls evaluate without recording.
- Actions record after `with_iwr_preflight`, so a refusal is logged as refused.

**Tests:** 10 polls add no records, and a refused action is logged as refused.

**Size:** S.

### T13. The radar session never waits under the setup lock

**Design:** `HeldStaticRadar.start` waits for an old session outside `tee_range_lock`, by releasing it and retrying, or does the close before taking the lock.

**Tests:** a slow-closing fake session doesn't block a concurrent GET.

**Size:** S.

### T14. Stale facts are visible and re-checked

**Design:**
- The gain facts carry their own `screened_at` and environment, and the page shows their age.
- A resumed rung re-runs the pre-check.
- The live view accepts 10 µs.
- A failed solve clears `solved_ball_diameter_px`.

**Tests:** resume re-checks; the live view accepts 10 µs.

**Size:** S. **Decision D5:** how old a gain screen may be before the tester asks for a new one (suggestion: 30 minutes outdoors, same day indoors).

## 5. Constants, rig file and provenance

### C1. The swing server always runs with a rig file

**Requirement:** no swing server run uses July or August geometry.

**Design:**
- `--rig-geometry` defaults to `config/enclosure_v3_rig_geometry.json`, and `start-kiosk.sh` passes it.
- The server refuses `--iwr6843` without a rig file.
- Remove the 0.20955 and 0.152 defaults and stop reading tilt and height from the board calibration.
- The rig file must name the radar's height and offset.

**Tests:**
- Starting without a rig file is refused.
- `start-kiosk.sh` passes the file.
- The calibration's 0.1524 m never reaches `Calibration.radar_height_m`.

**Size:** S. **Decision D6:** refuse without a rig file, or default to the v3 file.

### C2. Focal length and the admission check come from the rig file

**Design:**
- The per-mode focal length is `rig.focal_px × (mode width / rig mode width)`, used everywhere the tester uses 933.33/466.67.
- `setup_eligibility` checks the rig file against an approved-hash list in `config/`, not against literals.
- The remedy text reads its heights from the rig.

**Tests:**
- A rig file with a new focal length and an approved hash is admitted, and the models use its focal length.
- An unapproved file is refused.

**Size:** M.

### C3. One meaning per hash name

**Design:**
- Rename to `rig_geometry_file_sha256` (bytes) and `rig_geometry_params_sha256` (canonical fingerprint), and record both in the setup evidence and `session_start`.
- Readers accept the old key.

**Tests:** a setup epoch and its swing session join on both.

**Size:** S.

### C4. The scene height is always recorded

**Design:**
- The setup and `session_start` record `scene.lens_height_solved_m` and its uncertainty even when the rig height is used, plus `scene.lens_height_used_m` and its source.
- `session_start`'s `derived` stays rig-only; the solved values live in `solved_camera_height` (closes C13).

**Tests:** a consistent-height setup records the solved value.

**Size:** S.

### C5. Tee height is labelled as assumed

**Design:**
- The records carry `ball_height_basis: "assumed_on_surface"` until per-shot tee height (setup spec A2b) exists.
- The server logs it.

**Size:** S.

### C6. Image scale follows focal length

**Design:** `club_delivery._image_scale` uses the focal ratio to the 640×400 reference, not the image size (320×200 keeps 1.0).

**Tests:** 320×200 gives scale 1.0; 1280×800 gives 2.0.

**Size:** S.

### C7. The net's range is measured, not assumed

**Design:**
- The empty static capture already sees the net. Record the strongest far reflector in the 2-6 m window as `net_range_m`, and pass `--net-range-m` to swings.
- The 4.6 m default stays only when none is found, and is flagged.

**Tests:** an empty-capture fixture with a strong return at 2.8 m → `--net-range-m 2.8`.

**Size:** M. **Decision D7:** confirm, since this adds one inferred scene value.

### C8. One roll convention

**Design:** this is the existing setup-spec item A3: derive the sign once, and apply it identically in the nominal and calibrated paths.

**Size:** M.

### C9. The rig file is validated

**Design:** `RigGeometry.from_json` requires every geometry field and refuses unknown or missing ones. It has no silent dataclass defaults.

**Tests:** a missing `iwr_boresight_pitch_deg` is refused.

**Size:** S.

### C10. No silent fallbacks in setup

**Design:**
- A missing `range_bias_const_m` is refused (no fallback to `range_offset_m`, then 0).
- The radar uncertainty comes from the evidence, never `or 0.05`.
- The tester forwards `--iwr-calibration` to the kiosk.

**Tests:** a calibration without a bias is refused; the kiosk command carries the calibration path.

**Size:** S.

### C11 (new). The tester's calibrated camera path runs the server's rig checks

**Design:** share `init_camera_calibrated_fusion`'s placement checks (rig hash, IWR offset) with `tester_server`'s calibrated models.

**Tests:** a placement whose rig hash doesn't match is refused at setup, not later at the kiosk.

**Size:** S.

### C12 (new). The 320×200 strip offset is applied or refused

**Design:** apply the sysfs `strip_y_offset` to the principal point in 320×200 models, or refuse 320×200 for measurement. The mode is not in current use.

**Size:** S.

### C13 (new). Rig-derived and solved values are labelled apart

This is covered by C4.

## 6. Order of work

The order reflects dependencies and field value, not severity alone.

1. **Blockers**, in one branch:
   - B8, B2, B7 (S each);
   - B1;
   - B3, then T6 (T6 uses B3's bounds);
   - B4 and B5 together (B5 needs D1);
   - B6 with T3 (both change how swings are counted);
   - T1, T2, T7.

   Then one field session.
2. **Setup:** S1, S2 (D4), S4, S5, S6, S9, S10, then S3 with C3, C4, C5.
3. **Face angle:** F1, F4, F5, F6 (S each), then F2, F7, F9, F10, F11-F13, then F3 with the replay (D2), then F8 (D3).
4. **Rig-file discipline:** C1 (D6), C2, C9, C10, C11; C7 (D7); C12.
5. **The rest:** T4, T5, T8-T14, S7, S8, S11, C6.

## 7. How we'll know it worked

**Every change:**
- The full pytest suite runs against the baseline Windows-only failures, plus the Playwright tester specs.
- New tests are written first and fail before the change.

**After the blockers, one outdoor session:**
- a gain screen per mode;
- a setup with the ball clear of the mat edge;
- the ladder through the sun rungs;
- 20 swings on the shortest clean rung.

It succeeds if:
- the setup survives the ladder;
- no rung runs above its equivalent gain;
- the swing verdicts find the ball at 1280×800;
- the clips are not clipped on the ball.

**After the face-angle group:** a replay of stored sessions shows that face angle and path come only from accepted, displayed paths, in one frame, with sources recorded.

## 8. Decisions (all approved as recommended, 29 Sept)

| ID | Question | Decision |
|---|---|---|
| D1 | Read the black level from libcamera metadata (`SensorBlackLevels`)? | Yes; confirm it is present with one command on the Pi. |
| D2 | Which stored sessions are the replay reference for the range-space fix and the −2 ms constant? | The July TrackMan-scored sessions, plus the 21-shot August set. |
| D3 | How to calibrate the IWR horizontal zero? | In-situ, from camera ball tracks during the alignment-stick session. |
| D4 | With no accepted radar range, pass swings nothing (pending) rather than the camera's size estimate? | Pending. |
| D5 | How old may a gain screen be before the tester asks for a new one? | 30 minutes outdoors, same day indoors. |
| D6 | Without a rig file, should the swing server refuse, or default to the v3 file? | Default to v3 in `start-kiosk.sh`, and refuse in the server when none is given. |
| D7 | Measure the net's range from the empty radar capture? | Yes. |
