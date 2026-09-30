# Wiring fixes: plan

Date: 29 September 2026. Branch: `feat/tester-capture-pilot`. It implements `2026-09-29-wiring-fixes-spec.md`. All seven decisions (D1-D7) are approved as recommended.

## How the work runs

- **One item at a time, test first:**
  - Write the item's tests from the spec and see them fail.
  - Make the change, then run that module's tests and the tester flow tests.
  - Commit one spec ID per commit (or a tight group), with the ID in the subject line.
- **At the end of each phase:**
  - Run the full pytest suite against the Windows-only baseline, plus the Playwright tester specs.
  - Update the tester guide (`docs/camera/tester-pilot.md`) where behaviour changed.
  - Push only when Harjot says so.
- **Fixtures:**
  - Field data from 29 Sept (`pi-handoff/`) becomes a regression fixture wherever an item fixes a field failure.
  - Already saved: the door and outdoor-mat radar setups.
  - To be added: the outdoor gain screens and the Outdoors-test-3 ladder state.
- **No hardware is touched from the PC.** Pi steps are Harjot's, listed per phase.

## Before Phase 1: Harjot, on the Pi (five minutes)

**P1 (D1): check that libcamera reports the sensor black level.** Stop the tester server, then run:

```
cd ~/openflight
.venv/bin/python -c "from picamera2 import Picamera2; p=Picamera2(); c=p.create_video_configuration(raw={'size':(1280,800)}, controls={'FrameRate':120}); p.configure(c); p.start(); m=p.capture_metadata(); print({k: m.get(k) for k in ('SensorBlackLevels','ExposureTime','AnalogueGain')}); p.stop(); p.close()"
```

Paste the output. If `SensorBlackLevels` is missing, B5 falls back to the tuning file's value (`/usr/share/libcamera/ipa/rpi/pisp/ov9281_mono.json`, `rpi.black_level`), which is recorded with its source.

Answered 29 Sept: `SensorBlackLevels` is 4096 on a 16-bit scale, 16 DN in the raw 8-bit stream.

**P2 (D2) is withdrawn.** It asked for TrackMan-scored sessions to replay F3 against. None exist for the metrics F3 changes (see D2 in the spec), so F3 is scored against the camera's contact time and the setup's tee range on the first field session instead.

## Phase 1: blockers (then one field session)

Order and dependencies:

| Step | Items | Files | Notes |
|---|---|---|---|
| 1 | B8, B2, B7 | study_ladder.py, tester_server.py (`gain_facts`) | small, independent |
| 2 | B1 | tester_server.py (`_range_state`, `admitted_range`, job-finish paths) | FakeTilt gets a real `stop()` in the tests |
| 3 | B3 | new shared `reference_ball_pixel_bounds`; ball_flight.py, club_delivery.py, club_motion.py | callers pass focal length |
| 4 | T6 | study_ladder.py (`ball_light`) | uses B3's bounds and the admitted range |
| 5 | B4, B5 | tester_server.py (`choose_gain`, `light_index`), calibrate_camera_exposure.py (black level per setting), static_exposure.py (no ring-median floor) | needs P1's answer |
| 6 | B6, T3 | study_ladder.py (`judge_light`, failure cause, eligibility by trigger-time controls) | both change how swings are counted |
| 7 | T1, T2, T7 | tester_server.py (`study_overview`, `start_mode` try/finally, run numbering), tester.html, KioskClient.frames | |

**Done when:**
- Every Phase 1 spec test passes, the full suite matches the baseline, and the tester specs pass.
- The Outdoors-test-3 gain screens replayed through `choose_gain` give `too_bright` with an equivalent ≤ 1.
- The 29 Sept ladder file loads.

**Then Harjot runs a field session, outdoors, after pulling:**
1. A gain screen for each mode.
2. The setup, with the ball clear of the mat edge and visible on the preview.
3. The ladder through the sun rungs.
4. 20 swings on the shortest clean rung.

Copy the session over afterwards. It succeeds if:
- the setup survives the ladder (still resolved or raw-only, not "start over");
- rung gains follow the equivalent gain;
- swing verdicts find the ball at 1280×800;
- ball pixels aren't clipped.

## Phase 2: setup and what swings receive

| Step | Items | Notes |
|---|---|---|
| 1 | S1 | `candidate_window_m` in `compare_static_range_profiles`; the auditor's synthetic case becomes a test |
| 2 | S2 (D4), S5, S4, S6, S10 | all in tester_server's hand-off; small |
| 3 | S9 | the arm6 agreement check at finalize |
| 4 | S3 with C3, C4, C5 | the `handed_to_swings` record; the server's `--iwr6843-tee-range-source`; hash names; scene height and ball-height basis in `session_start` |

**Done when:** an unqualified flow test finds the same tee value, candidate, heights and bases in arm.json, `setup_admission.json`, the run's `tee_range.json` and `session_start`, and both hashes join the epoch to the session.

## Phase 3: face angle and fusion

| Step | Items | Notes |
|---|---|---|
| 1 | F1, F4, F5, F6 | small and independent: path selection, accepted evidence only, per-axis tile, precedence |
| 2 | F2, F9, F10, F11, F12, F13 | one club-path frame; offset handling; July lateral constant; phase centre; OPS offsets; mph constant |
| 3 | F7 | ball-flight timing from the departure frame |
| 4 | F3 | range spaces plus removing the −2 ms constant, after F7 (it needs the contact time). Replay the field session before and after; keep the change only if impact time moves no further from the camera's contact time and the club's range at impact lands on the tee range (D2). |
| 5 | F8 (D3) | status `azimuth_uncalibrated` now; the in-situ calibration after the alignment-stick session |

**Done when:**
- A replay of the field session shows face angle and path only from accepted, displayed paths, in one frame, with their sources recorded.
- F3's replay report is attached to its commit.

Step 1 touches server.py and the UI; step 2 touches the camera and IWR modules. They could run in parallel worktrees, but this phase has shared server.py edits, so sequential is safer.

## Phase 4: rig-file discipline

| Step | Items | Notes |
|---|---|---|
| 1 | C1 (D6), C9, C10 | `start-kiosk.sh` passes the v3 file; the server refuses `--iwr6843` without a rig file; strict rig-file validation; no bias or uncertainty fallbacks; `--iwr-calibration` forwarded |
| 2 | C2, C11 | focal length from the rig file; an approved-hash list for admission; shared placement checks |
| 3 | C7 (D7) | net range from the empty static capture, passed as `--net-range-m` |
| 4 | C12 | 320×200 strip offset (or refuse the mode for measurement) |

**Done when:** no code path outside the rig file supplies geometry to the swing server (a grep test for the old constants), and a rig file with a new focal length and an approved hash flows through to the models.

## Phase 5: the rest

T4, T5, T8-T14 (T14 with D5), S7, S8 (unless complex radar subtraction lands first), S11, C6, C8 (the existing roll item A3).

## Rough effort (agent time)

| Phase | Effort | Blocked on |
|---|---|---|
| 1 | done 29 Sept | — |
| 2 | half a day | nothing |
| 3 | about a day, plus the replay | the field session's swings, for step 4 |
| 4 | half a day | nothing |
| 5 | half a day | nothing |

## Risks

- **B1 changes when setup is re-validated.** It must still refuse a real rig move at capture start. That is covered by a test.
- **F3 can make things worse if done halfway.** The range fix and the −2 ms constant change together, behind the replay check.
- **C2 changes the admission gate.** The approved-hash list starts with today's hash, so nothing changes until a new rig file is approved.
- **B3 widens what counts as a ball at 1280×800.** The shadow and blob cases are tested, and T6 adds expected size and row in the ladder.
