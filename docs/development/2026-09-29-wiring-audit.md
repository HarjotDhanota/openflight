# Wiring audit: tester suite and fusion path

Date: 29 September 2026. Code: `feat/tester-capture-pilot` at 455b41a0. Status: findings only; nothing fixed yet.

## Scope and method

The audit covered the tester suite (`camera/tester_server.py`, `ui/public/tester.html`, the gain screen, the range setup, the exposure ladder) and the swing-time fusion path (`server.py`, `camera/*`, `iwr6843/*`, `rig_geometry.py`). It did not cover OPS243 internals, ballistics, simulators, the kiosk display beyond the tiles that show fused values, review tooling or `.agents/`.

Five read-only reviews ran in parallel, one per slice:

1. Tester flow and hardware ownership.
2. Setup measurement chain.
3. Camera controls and the ladder.
4. Swing-time fusion and timing.
5. Constants, geometry and provenance.

Each finding cites file:line. The column "Checked" shows who confirmed it:

| Value | Meaning |
|---|---|
| **me** | Re-read and confirmed in the code after the review. |
| **probe** | The reviewer ran the real function in a scratch script. |
| **read** | The reviewer confirmed it by reading only. |
| **likely** | Inferred and not fully traced. |

Severity scale:

| Severity | Meaning |
|---|---|
| **Blocker** | Stops the next field test from producing usable data. |
| **High** | Gives wrong numbers or blocks the flow in common conditions. |
| **Medium** | Gives wrong numbers in specific conditions, or loses evidence. |
| **Low** | Cosmetic, latent, or small. |

## 1. Blockers for the next field test

| # | Finding | Where | Checked |
|---|---|---|---|
| B1 | **The page's status poll wipes out a finished setup once the ladder starts.** See below. | tester_server.py:4272-4287, 5662 | me, probe, field (Outdoors-test-1 and -3 both ended in exactly this state) |
| B2 | **The ladder never gets the sunlight gain.** See below. | tester_server.py:5646-5650, 5843-5845 | me, probe |
| B3 | **Fixed ball-size gates reject the ball at 1280×800 inside about 1.33 m.** See below. | ball_flight.py:35, 413, 668; club_delivery.py:207; club_motion.py:608, 660 | me, probe |
| B4 | **The gain screen still saves gain 12 in mixed light.** See below. | tester_server.py:614-622 | probe |
| B5 | **In sunlight the black floor is unknown, and the setup search quietly brings back the contrast gate.** See below. | tester_server.py:650; static_exposure.py:295 | probe |
| B6 | **The ladder's pre-check and the swing verdict disagree, and a failed rung still takes the shorter rungs with it.** See below. | study_ladder.py:177 vs 219-226; 395-396 → `_skip_from` | probe |
| B7 | **The gain correction can raise the gain on a clipped ball.** See below. | study_ladder.py:151-156 | probe (a half-sunlit ball, median 120, 7.5 % clipped, at gain 2 → suggested 2.5) |
| B8 | **A `ladder.json` written before 455b41a0 crashes the ladder endpoints.** See below. | study_ladder.py:260-266, 308-311 | probe |

B1: the page's status poll wipes out a finished setup once the ladder starts.
- The finished state re-runs the setup check on every GET, and that check reads the LIS3DH.
- `start_mode` stops the LIS3DH so the kiosk can own it. The check then fails, and the failure is saved as `retryable_failure` "start over required".
- Resuming or restarting the ladder is refused after that, and a new setup makes a new epoch, which the ladder refuses too. The tester ID is stuck.

B2: the ladder never gets the sunlight gain.
- `gain_facts` returns `{gain, light_index, black_floor_dn}` only, so `gain_at_300_equivalent` is always missing and the ladder uses the saved gain (1.0 in sun).
- Shorter rungs then get proportionally more gain: 30 µs at gain 10 rather than about 4.5.

B3: fixed ball-size gates reject the ball at 1280×800 inside about 1.33 m.
- The gates were written for 640×400 (half focal length): diameter 9-30 px, detector component area ≤ 600 px² (about 28 px across).
- At 1280×800 a ball at 1.0-1.3 m is 31-40 px.
- This breaks:
  - the ladder's ball-based pre-check (`ball_light` uses `detect_reference_ball`, which falls back to the zone);
  - the swing-time club-delivery ball anchor (`ReferenceBallTracker.resolve` stays "unverified");
  - camera ball flight (`rejected_implausible_reference_ball`).
- The setup detector (`reference_ball_range`) is not affected; it found 36-43 px balls.

B4: the gain screen still saves gain 12 in mixed light.
- Only the lowest gain is tested for "too bright". If it isn't too bright but later gains clip (shade plus a sun patch), the brightest gain is saved as `lighting_required`: the 29 Sept failure again.
- Separately, `too_bright` with a median below the band gives an "equivalent" gain above the lowest gain (for example 1.62), so it asks for more light.

B5: in sunlight the black floor is unknown, and the setup search quietly brings back the contrast gate.
- `light_index` fits only whole-frame-unclipped screens, so with sky in view it returns `light_index=None`, `black_floor_dn=None`.
- The setup exposure search then uses the ring median as "floor", so its signal gate becomes ball minus background ≥ 20 DN. That is the contrast gate removed in ab0a188c.
- The ladder falls back to floor 0 and light index 0.05, which puts impact photos at 1000 µs × 2.

B6: the ladder's pre-check and the swing verdict disagree, and a failed rung still takes the shorter rungs with it.
- The pre-check judges on the ball (ok); the verdict reds on a dark or ≥ 5 %-clipped zone.
- Two reds in the first three swings mark the rung `failed`, and `failed` still calls `_skip_from`.

B7: the gain correction can raise the gain on a clipped ball.
- For a clipped ball with median below 150 the suggestion is `gain*150/median`, which is above the current gain.

B8: a `ladder.json` written before 455b41a0 crashes the ladder endpoints.
- Old ladder files lack `full-50`/`full-30`, and `current` and `to_dict()` raise `KeyError`.
- The 29 Sept tester IDs get HTTP 500 on ladder start and poll.

## 2. Face angle and fusion correctness

| # | Sev | Finding | Where | Checked |
|---|---|---|---|---|
| F1 | High | **Face angle uses a club path the kiosk does not show.** The D-plane estimate takes `club_path_deg`, then `experimental_fused_club_path_deg`, then `experimental_club_path_deg`, without checking status. The IWR path is filled from `candidate_path_deg` even when it is `candidate_out_of_bounds` (> 30°) or noisy. The kiosk hides that path in camera sessions, but shows the face angle made from it. | server.py:3252-3275, 3617-3641; liveMetrics.ts:98-102 | me, probe |
| F2 | High | **The camera club path has two zero directions.** The chained branch (IWR club track present) has no yaw, so zero is the camera boresight. The camera-OPS fallback rotates by the ball's image azimuth, so zero is the camera→ball line. Both are shown as "camera fused". With the ball 67 px off centre at 640×400 the difference is 8.4°. | club_delivery.py:704-748 vs 1028-1053 | me, probe |
| F3 | Medium | **Apparent and bias-corrected radar ranges are mixed.** `club.find_club` gates and scores apparent-range tracks against the corrected tee range (66 mm off). The camera also intersects apparent club and ball ranges with geometry built from corrected range. The −2 ms `club_impact_correction_s` (fitted 7 Aug) is about 66 mm ÷ 35 m/s, so it probably absorbs the bias as a speed-dependent time shift. Fix both sides together. | club.py:356-357, 391-396; club_delivery.py:1042; ball_flight.py:531-536; runtime.py:306-311 | me (club.py), likely (compensation) |
| F4 | Medium | **Rejected radar tracks become camera depth.** `iwr6843_ball_range_evidence` and the club evidence are set even for rejected tracks. The camera can reach tier "high" on them and overwrite the displayed horizontal launch. A rejected static IWR candidate also still carries a positive range that the camera ranking uses. | server.py:3139-3140; lcmf.py:177-204; club.py:679-683; tester_server.py:2798-2808, 2950 | read |
| F5 | Medium | **The horizontal-launch tile shows the vertical's confidence and "estimated" flag.** `launch_angle_horizontal_confidence` is never read, and camera-only or legacy horizontal sources get no label. | liveMetrics.ts:83-85, 145-151 | read |
| F6 | Medium | **A size-only camera horizontal (confidence 0.30) replaces an accepted IWR horizontal.** Its zero is the camera boresight, not the IWR's, so the displayed zero jumps from shot to shot. This is against "camera ball flight is diagnostic only". | ball_flight.py:811-820; server.py:3829-3832 | read |
| F7 | Medium | **Camera ball flight uses the trigger as the impact time.** It uses host frame-arrival time minus trigger time, with no speed-of-sound walk-back, while contact is 0.5-4 ms before the trigger. Club delivery gets this right from the ball-departure frame. | ball_flight.py:527-536 | read, probe (H error up to 0.2°, V up to 0.9°) |
| F8 | Medium | **No IWR horizontal phase reference exists in the tester flow.** Per club.py's own note, an electrical phase bias rotates recovered positions rather than offsetting them, so it does not cancel from club path. This is separate from the known azimuth offset. | server.py:5973-5981; club.py:23-27 | read |
| F9 | Low | **`horizontal_offset_deg` applies to the camera ball direction but not to club path.** Latent: it defaults to 0. | geometry_contract.py:239-258 | read |
| F10 | Low | **`LATERAL_TEE_OFFSET_M = 0.064` is a July constant** used in the LCMF inversion but not in tee_x (about 1.4 mm). | lcmf.py:47, 303, 877 | read |

## 3. Range setup and radar

| # | Sev | Finding | Where | Checked |
|---|---|---|---|---|
| S1 | High | **Camera-steered re-selection rejects the real ball as clutter.** `changed_fraction`, scale and MAD are computed over the narrowed window (18-23 bins at 1.0-1.3 m). Any 3-bin ball cluster then exceeds the 12 % clutter limit, so the rescue path fails at normal distances. | range_evidence.py:693, 791; tester_server.py:2743-2751 | me, probe (full window accepted; camera window `rejected_clutter`) |
| S2 | High | **Unqualified mode can pass a camera size estimate as the radar tee range.** When the IWR is not accepted, `unqualified_tee_range_choice` falls back to the camera's size-derived range (σ ≈ 21 %). The kiosk treats it as a configured tee range (impact timing, club gate, LCMF anchor). | tester_server.py:522-534, 550-556; server.py:1558 | read |
| S3 | Medium | **What swings actually used is not in the tester's records.** In unqualified mode, arm.json, `setup_admission.json` and the run's `tee_range.json` all say "unresolved", with `tee_range_m` None, while the kiosk ran on a range. Only the command echo in the log has the value, the candidate, and the camera steering. | tester_server.py:5286-5302, 5681-5685 | read (three reviewers) |
| S4 | Medium | **The 640×400 validation can override the lens height the 1280×800 search found consistent.** `_setup_camera_height_m` falls through to arm6 whenever arm5 is not `static_iwr_range`. | tester_server.py:495-507 | read (two reviewers) |
| S5 | Low | **A failed camera re-check keeps an out-of-window radar pick as accepted** (`not_rechecked`). This can't happen with the default interval. | tester_server.py:2511-2519 | read |
| S6 | Low | **A pending range drops the solved lens height** (unit on a box), so the kiosk records the rig's 95/51 mm. | tester_server.py:540-557 | read |
| S7 | Low | **The hitting-area test mixes radar slant range with distance along the lens ray.** The area's edges shift by about 3 cm. | reference_ball_range.py:666, 781-791 | read |
| S8 | Low | **The leak gate models only sidelobe power.** It ignores the power-difference cross term with the clutter already in the ball's bins. | range_evidence.py:636-641 | likely |
| S9 | Low | **The 640×400 validation is never compared with anything.** It only has to exist. | tester_server.py:4462-4466 | read |

## 4. Tester flow, ownership and records

| # | Sev | Finding | Where | Checked |
|---|---|---|---|---|
| T1 | Medium | **"Too bright" never reaches the page.** `study_overview` has no `too_bright`, so the light step says "light sufficient" in sun, and the light panel is hidden when `light_index` is None. | tester_server.py:3801-3813; tester.html:947, 961-965, 1344 | me |
| T2 | Medium | **An error in `start_mode` leaves the LIS3DH stopped.** It is stopped before the command build and `write_setup_admission` (which uses `mkdir(exist_ok=False)` and `len(existing)+1` numbering, so a gap in run numbers collides). Every later setup check blocks until the server restarts. | tester_server.py:5662-5702, 717-731 | read |
| T3 | Medium | **Swings are judged against whichever rung is current.** Captures taken during gain corrections, a photo, or before the first `set_controls` are judged against the new rung, and their "controls" reds count toward failing it. | study_ladder.py:677-702, 797-802 | likely |
| T4 | Medium | **Clip `metadata.settings` is the kiosk's startup snapshot**, so reviews and exports report 300 µs for a 30 µs rung. The live request is already in `metadata.auto_exposure`. | capture_runtime.py:1158, 1199; capture_facts.py:139-140 | read (two reviewers) |
| T5 | Medium | **Impact photos can't be exposed in sun.** They are always gain 2 and ≥ 100 µs, from a whole-frame light index or the 0.05 fallback. | study_ladder.py:40-42, 85-89 | probe |
| T6 | Low | **The ladder's ball detector gets no expected size or row**, so it can take a dark blob such as a shadow for the ball. | study_ladder.py:114; club_motion.py:700-748 | likely |
| T7 | Low | **Pre-rung frames and the photo are never checked against the applied controls**; the client drops the per-frame arrays. | study_ladder.py:502-504 | read |
| T8 | Low | **A stale failure message persists.** `camera_capture_failure`/`capture_failure` evidence is never cleared, so a later failure shows the old message. | tester_server.py:4331, 4406, 4857; tester.html:1417-1433 | read |
| T9 | Low | **A finished capture can be processed twice** (GET plus reader-thread callback) with no phase or capture-ID guard. | tester_server.py:4381-4445 | likely |
| T10 | Low | **Stop or a timeout on a radar capture clears the hardware check.** | tester_server.py:4398-4399 | read |
| T11 | Low | **Side effects happen before a refusal.** Run folders, admissions and arm-state writes happen before `release()` or `jobs.start` can refuse, leaving orphan `run-NN` folders. | tester_server.py:5286-5334, 5681 | read |
| T12 | Low | **Every terminal-state poll appends a line to `setup_eligibility.jsonl`**, about one per second. | setup_eligibility.py:303-348 | read |
| T13 | Low | **`HeldStaticRadar.start` can wait up to 10 s under `tee_range_lock`** for an old session to exit, stalling polls. | static_radar_holder.py; tester_server.py:5063 | read |
| T14 | Low | **Stale gain facts are reused silently.** A resumed rung reuses its gain without a new pre-check, and the live view's minimum exposure (20 µs) is above the setup search's (10 µs). | tester_server.py:708-714, 128; study_ladder.py:630-635 | read |

## 5. Constants, rig file and provenance

| # | Sev | Finding | Where | Checked |
|---|---|---|---|---|
| C1 | Medium-high | **Without `--rig-geometry` the swing server uses July/August geometry.** It uses camera 209.55 mm, radar 152.4 mm and tilt 10.405° from the calibration JSON, and only logs it. `start-kiosk.sh` never passes the rig file, so any kiosk launch outside the tester runs the v3 box with the wrong geometry. | server.py:5645-5649, 5910-5920; calibration.py:44; trajectory.py:163 | read (two reviewers) |
| C2 | Medium | **The tester's focal lengths and admission pin bypass the rig file.** 933.33/466.67 are code constants, and `setup_eligibility` requires the rig file to equal hard-coded values and hash. A calibrated focal or re-measured offset in the rig file blocks capture until the code changes. | tester_server.py:118-119; setup_eligibility.py:16-32 | read |
| C3 | Medium | **Two different hashes are both called `rig_geometry_sha256`.** Setup evidence uses file bytes; `session_start` uses the canonical-parameter fingerprint, so an offline join never matches. | tester_server.py:2592, 2887; rig_geometry.py:160-163 | probe |
| C4 | Medium | **The rig's lens height stands in for the scene whenever the solve is "consistent".** The solved value should still be recorded as the scene height. | tester_server.py:464-466 | read |
| C5 | Medium | **Tee height is always one radius**, and "assumed on the surface" is not recorded anywhere. | tester_server.py:541-547 | read |
| C6 | Medium | **`club_delivery._image_scale` treats 320×200 as half scale**, though it is a crop with the 640×400 focal. | club_delivery.py:100-106 | likely |
| C7 | Low | **Net range defaults to 4.6 m and is never measured.** A net at 2.5-3 m falls inside the ball gates. | server.py:5903-5908; shot.py:330 | read |
| C8 | Low | **Roll handling is inconsistent across the calibrated and nominal paths** (known; one sign to derive). | tester_server.py:1534-1537; server.py:3756-3764 | read |
| C9 | Low | **`RigGeometry.from_json` has no validation.** Missing fields fall back to dataclass defaults. | rig_geometry.py:53-55 | read |
| C10 | Low | **Silent fallbacks:** range bias → `range_offset_m` → 0.0; radar uncertainty `or 0.05`; a tester `--iwr-calibration` override is not forwarded to the kiosk. | tester_server.py:2735, 443, 821-889 | read |

## 6. Checked and correct

The reviews also confirmed, with evidence, much of the wiring:

**Radar ranges and bias**
- The radar bias is subtracted exactly once in setup and added back exactly where apparent range is needed (shot.py:118, recovery.py:104).
- Camera windows and radar intervals share one space.
- Absolute range bins are used throughout the static selector.

**Frames, signs and geometry**
- Frame conversions and signs are consistent: `camera_rdf_offset_to_target_lfu` and the radar at lens + (0, −0.030, −0.044).
- The setup frame (origin at the lens) and the kiosk frame (origin at the radar) agree.
- The pitch sign is right, and a round trip with the correct pitch returns exactly 95.0 mm at 1.0-2.0 m in both modes.
- Horizontal sign conventions (IWR launch and path, camera launch and path, D-plane) all use + = target-right / in-to-out, and the IWR azimuth offset is applied once.
- LCMF, two-ray and the tee anchor use heights above the surface consistently after 59bcb616.

**Camera controls**
- Auto-exposure is off everywhere.
- No requested gain goes outside 1-15.94, and no rung exceeds its frame period.
- The 10-30 µs requests match within the 9 µs row tolerance.
- Rung controls do reach the sensor for swings, and per-frame applied values are recorded.

**Radar session and tester flow**
- The held radar session: command split, result written before `done`, close only after clean captures, release before every hardware job, restart recovery through reservation files.
- Request-ID idempotency, the unique parallel-search IDs, and the lock order between `ladder_lock` and the runner.

**Provenance**
- The rig geometry does ride in `session_start` (content plus fingerprint).
- Runtime provenance records the git commit and file hashes.
- Clips carry per-frame applied controls.

## 7. Suggested fix order

1. **Blockers B1-B8.** They are small and local, and they decide whether the next outdoor session produces anything.
2. **Face-angle correctness: F1, F2, F3 (together with the −2 ms constant), F4, F5, F6.** Without these, face angle and horizontal launch mix frames and unaccepted inputs.
3. **Setup and records: S1-S4, S3 with C3, T1, T2.**
4. **Rig-file discipline: C1, C2, C4, C5.**
5. **The rest.**
