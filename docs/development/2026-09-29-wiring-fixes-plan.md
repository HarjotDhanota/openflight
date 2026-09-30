# Wiring fixes: plan

Date: 29 September 2026. Branch: `feat/tester-capture-pilot`. It implements `2026-09-29-wiring-fixes-spec.md`. All seven decisions (D1-D7) are approved as recommended.

## Status (29 Sept, evening)

56 of the 59 items are done on `feat/tester-capture-pilot`: Phase 1 (pushed as e3308529), Phases 2-5 (merged locally in e59dcfac) and F11. Each phase was built test first on its own branch, reviewed, merged, and checked with the full suite against the Windows baseline, the UI unit tests and the tester page tests.

**Left, each waiting on something outside the code:**

| Item | Waits for |
|---|---|
| F3 (range spaces and the −2 ms constant) | The Outdoors-test-5 swings, scored as D2 says. The contact-time function it needs (F7) is built. |
| F8's horizontal reference | An alignment-stick session (D3). Until then radar horizontal and the IWR path are marked `azimuth_uncalibrated` and face angle uses camera paths only. |
| C8's roll correction | A phone-level check against the LIS3DH roll (setup spec B2). The sign is derived and pinned by a test, but roll stays unapplied in both camera paths until the check. |

**F11 closed (30 Sept, `agent/radar-phase-centre`, 5060e8a9).** Harjot reported the IWR board's turn on 29 Sept: USB top right and RX antennas on the left, seen from the front, which is +90° from the ECAD frame. The v3 rig file records it as `iwr_board_rotation_deg: 90.0`, with a new approved fingerprint (f2b05c97…; the 22 Sept entry is kept). The phase centre sits 7.97 mm above the RX row in the board's plane; with the 10° aim that is 7.85 mm up, 1.38 mm back and 1.86 mm target-left. The radar height the two-ray model uses on the v3 enclosure became 58.8 mm, not 51 mm, and every range that starts at the IWR starts there (`RigGeometry.iwr_origin_mm`). Checks against a placement's measured RX row, and the tape's start, keep the RX row. On 30 Sept Harjot re-measured the receive column straight up from the surface: the bottom edge of RX1 at 40.2 mm, its middle at 44.3 mm and the top edge of RX4 at 48.6 mm (the layout predicts an 8.8 mm span; measured 8.4). Fitting all three puts the middle at 44.4 mm, 50.6 mm below the lens he confirmed at 95 mm. The rig file's offset is now 50.6 mm, with a new approved fingerprint (ef96c8ee…), and the halfway point is 52.25 mm. The vertical launch model itself now places every antenna separately (lcmf_v2_per_antenna): receive antennas 40.8-48.0 mm, transmit antennas 55.3 and 64.9 mm.

**Where the build departs from this plan or the spec, and why:**
- C2: focal length scales by the camera's binning, not by the mode's width, because 320×200 is a crop of the 640×400 mode. The approved-rig list keys on the parameter fingerprint, since the file's bytes change with line endings.
- C6: the legacy DTL trace estimator keeps its frame-size scale; its search window cannot fit a 200-row frame otherwise. The live chained-delivery windows use the focal scale.
- C7: the static radar configuration sees only to about 2.9 m, so a net further out is not measured and the 4.6 m default is used and flagged.
- C12: 320×200 is refused for measurement rather than corrected, since nothing records the strip offset's sign.
- S8: the interim leak term uses the RMS cross term (√2·|c|·|ℓ|). The spec's 2·|c|·|ℓ| rejected the door fixture, whose tape-confirmed range must stay accepted; the door keeps about 17 % margin.
- T8: old failures are hidden by the transition that set them, not deleted, so the only copy of a camera error is kept.
- T11: the admission is written before the job starts, because the kiosk creates the run folder itself; a start refused late removes the folder.
- S7, S8 and S11 change the camera and IWR estimator hashes, so any stored qualification pinned to the old ones is invalid. None exists in the field yet.

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

## Phase 6: what Outdoors-test-5 exposed (added 29 Sept, evening)

Outdoors-test-5 ran at dusk (18:45-19:01) on e3308529. Capture worked end to end, but nothing fused: the camera never found the ball, which sat about 1.8 m out on a raised mat at 15-30 DN, so the setup stayed raw-only. The field analysis is the Claude Doc "Outdoors-test-5 field analysis — handoff". Harjot decided D8 and D9 on 29 Sept.

| ID | Problem seen in the field | Fix |
|---|---|---|
| P6-1 | A swing went green on a 5 px speck: with no ball position from the setup, the verdict searched the whole frame. | No green or amber without the setup's ball position: the swing is red and says why, and the pre-check fails, so that mode's settings are skipped before anyone swings. Nothing searches the whole frame any more. |
| P6-2 | The ladder started on a setup whose camera never found the ball, so its swings could only yield speeds. | **D8:** C refuses to start or resume, and says the camera can't see the ball and what to fix: distance, a flat surface, no spare balls or white objects in view. It checks the mode of the first setting still to run (640×400 falls back to the 1280×800 ball halved); only a camera association that selected a ball counts, and an unresolved radar range with a camera ball still runs. |
| P6-3 | A setting could not end after its first 3 swings: full-300 stuck at 2 of 5 and 640×400 never ran. | **D9:** a setting fails once it has 3 red swings in total, at any point (it skips the shorter settings only if a red was dark). The Phase 1 early exit (2 reds in the first 3) stays. Two dark reds in a row also fail it and skip the shorter settings in that mode. |
| P6-4 | The ball-departure detector's fixed 30 DN threshold (`BALL_PRESENT_DELTA`) exceeds the ball's whole contrast in dim light, so the contact time (F7) returns nothing. | Scale the threshold to the clip's measured noise, with a regression fixture cropped from Outdoors-test-5. |
| P6-5 | Frames are split before and after the trigger by arrival, not exposure (a ~40 ms host stall moved frames 18-20 across), and the trigger's timestamp is taken after a deepcopy. | Timestamp the trigger first. Record the split by sensor exposure time as a new field, without changing what the existing field means, and use it where the code compares against the trigger. Contact appeared 7-26 ms before the trigger here, against 0.5-4 ms earlier; F3 waits until that is settled. |
| P6-6 | Misleading messages. | "Reduce scene brightness" when the scene was dark; a false "frames missing" in the analysis report when the cause was no fusion context; three carries logged for one shot; a gain screen's solved range marked "clean" on the wrong object; the degree sign garbled if the log's encoding is at fault. |

P6-1 to P6-3 touch the same ladder code as the setting selection, so they follow it; P6-4 to P6-6 run in parallel.

**What P6-4 and P6-5 found (merged 29 Sept):**
- The "7-26 ms before the trigger" was mostly a late timestamp. The trigger's time was taken after the evidence gathering, which holds the frame lock and stalled frame delivery by 14-31 ms. Bounded by when frame 18 was blocked, contact sits roughly −5 to +3 ms around the trigger, in line with the earlier 0.5-4 ms. GPIO latency, rolling-shutter row offset and SensorTimestamp's exact meaning remain open for F3.
- On 4 of the 6 swings the club head already covers the ball in frame 18, so the departure detector says 17: the contact time comes out one frame (8.7 ms) early. That is a property of the behind-ball view in any light, and F3's scoring must allow for it.

| ID | Problem | Fix |
|---|---|---|
| P6-7 | `notify_trigger` holds the frame lock while it gathers evidence and copies it, so frame delivery stalls 14-31 ms at every trigger. | Gather and copy outside the lock; hold it only to freeze the ring and queue the trigger's records. |
| P6-8 | Chained club delivery still times frames by host arrival, which the stall distorts by up to 31 ms around impact. | Time frames by sensor timestamps where the clip has them, as ball flight and the trigger split now do. |

## Phase 7: what Outdoors-test-6 and -7 exposed (added 30 Sept)

Outdoors-test-6 and -7 ran in full sun on 30 Sept (10:10-10:26) on 63b69be9. Harjot made 5 real swings; 2 became complete shots, and no camera or IWR number reached any of them. Four read-only investigations traced why (data in `pi-handoff/Outdoors-test-6`, `-7` and `tester-server-7.log`). Harjot decided D10 and D11 on 30 Sept.

- **D10: a placement box the tester drags (revised 30 Sept).** The tester sets the unit down, drags the box on the live view to where the balls will be hit, and confirms it. The camera and radar checks then run from that box. The box starts at a default spot, projected through the shared rig file and the tilt sensor: straight ahead, about 1.35 m out. It has a fixed size of about 0.20 m wide at that distance, and it can be moved but not resized. A ball found in the box must still pass the physical hitting-area checks. The first version was a fixed box, and Harjot replaced it the same day: "drag the box to where they will hit the balls, and then we run the camera and radar checks based off of that".
- **D11: the setup saves as experimental.** The camera's ball in the box, plus the radar's range when the two roughly agree, is enough to save a range. No qualification file is needed. Every number built on that range is labelled experimental.

**Why nothing fused:**
- The setup could never resolve. It needs the radar and both camera modes, plus a qualification artifact that nothing produces. No IWR number has reached a shot in any session.
- The radar's static difference rejected a real ball. It failed the fractional gate at 0.34 against 0.50, because the mat edge interferes with the ball's echo. Coherent subtraction of the saved captures shows one ground-level reflector at 1.53-1.62 m on all 10 recorded setups.
- The 640×400 check treated "ambiguous" as dark, judged from the whole frame. It walked up to gain 12 until only the tray balls stayed visible. The 1280×800 step locked the one mid-mat ball every time.
- The kiosk's analysis eligibility rule is too strict for a sunny mat. It requires under 8 % clipping of the whole hitting-zone box, and a sunny mat is always about 20 % clipped. The ladder's pre-check matched fence clutter 175 px from the ball. The shortest setting (30 µs) is 3× longer than sun needs.
- Wind set off the microphone 13 times.
  - A false trigger starts a 7 s IWR dump, and the camera only hears edges through the IWR, so one real shot was dropped.
  - The OPS243 never re-armed after a trigger raced its reset, so the last two swings got no speed.

**Workstream T: triggers**

| ID | Problem | Fix |
|---|---|---|
| P7-1 | The OPS243 stops dumping for good when an edge races the re-arm: `reset_input_buffer` discards the dump's start marker, and nothing re-arms after a wait times out (`ops243.py:1564, 1749`, `trigger.py:595-597`, `monitor.py:574-577`). | Never flush a dump already in flight, and re-arm after every timeout or discarded dump. If BCM17 sees an edge and no dump starts within about 0.3 s, log it and re-arm. |
| P7-2 | The camera hears an edge only through the IWR monitor, which ignores edges while it dumps (`iwr6843/monitor.py:176-193`). | Tell the camera about every BCM17 edge, busy or not, keeping the 0.1 s de-duplication. |
| P7-3 | An OPS-accepted shot with no camera trigger evidence is dropped (`server.py:5023-5050`). | Log it as a shot, flagged with the missing evidence, and say so in the review. |

**Workstream S: setup and the placement box**

| ID | Problem | Fix |
|---|---|---|
| P7-4 | There is no placement box. The ball search takes anything in a 1.0-2.5 m × ±0.30 m area, and the kiosk "+" is a picture only. | **D10:** show a draggable box on the setup view. It starts at the projected default and is padded for pitch, roll and a raised mat. The camera and radar checks are offered only once it is confirmed. Store it with the setup, halving it for 640×400. Restrict every setup search to it: live, Save and both modes. The camera's ball in the box sets the radar's range window. With no ball in the box, say "put the ball in the box". |
| P7-5 | The 640×400 step starts from scratch and walks brighter on ambiguity (`static_exposure.py:559-628`). | Warm-start it from the 1280×800 lock, inside the box. Judge darkness on the box, not the frame. Retry "ambiguous" at the same step; never step brighter for it. |
| P7-6 | The radar's static difference compares magnitudes, so an echo interfering with the mat edge fails the fractional gate (`range_evidence.py:44-47, 723-787`). | Subtract the empty and ball captures coherently, per virtual channel, and search the box's range window with a margin. A clear single ground-level peak is `accepted_unqualified`. A rejected result no longer writes a slant range. |
| P7-7 | Finalising needs all three candidates and a qualification artifact (`tester_server.py:5264-5281`). | **D11:** save a range when the 1280×800 ball in the box is locked and the radar range agrees with the camera's size range within their combined uncertainty. Use the radar's value, with the camera's range as the fallback when the radar has none. 640×400 becomes advisory; its settings use the 1280×800 ball halved, as D8 already does. The kiosk starts with the unqualified range, and the setup says the range is experimental. |

**Workstream L: light, the ladder, labels**

| ID | Problem | Fix |
|---|---|---|
| P7-8 | Capture-time `analysis_eligible` fails when more than 8 % of the whole hitting-zone box is clipped (`capture_runtime.py:571-591`, `auto_exposure.py:78-81`). | Judge it on the setup's ball with the ladder's `judge_light` (core clipped 5 % or less, 20 DN or more above black). Keep the zone rule only when there is no setup ball. |
| P7-9 | The shortest settings are too long for sun, and the pre-check can match clutter up to 6 ball diameters away (`study_ladder.py:185-190`). | Add full-20, full-10, half-30 and half-15. Tighten the ball match to about one diameter, so the too-bright skip judges the real ball. |
| P7-10 | Clips with no OPS shot stay "pending" forever, and the review calls real swings "Not a shot" (`paired_eligibility.py:168-172`). | Time them out as "no radar shot" and say so in the review. |
| P7-11 | The review labels any `accepted*` IWR launch "accepted", whatever the tee's source (`review_metrics.py:230`). With a guessed tee, the launch moves 4-28° per ±0.25 m. | Label it experimental whenever the tee is unqualified or the status is single-channel, and carry the tee's range and source. |
| P7-12 | Each rung's starting gain comes from the zone gain screen (`rung_gain`, `study_ladder.py:119-125`). In sun it starts full-10 at gain 12 when the ball needs about 1, and the ladder takes three swings to correct. (Found while building P7-9.) | Start each rung at the setup's ball lock, keeping its exposure × gain. A mode without its own lock scales the 1280×800 lock by the gain screens' mode ratio. With no lock, keep the gain-screen rule. Record the source per rung. |

**Harjot, before the next session:** put a foam windscreen on the microphone, and check the GATE LED stays quiet in wind before swinging.

The three workstreams run in parallel on their own branches. S owns the tester page and runs the Playwright specs; T and L run pytest and vitest only.

## Rough effort (agent time)

| Phase | Effort | Blocked on |
|---|---|---|
| 1 | done 29 Sept | — |
| 2 | done 29 Sept | — |
| 3 | done 29 Sept except F3 | the Outdoors-test-5 swings, for F3 |
| 4 | done 29 Sept | — |
| 5 | done 29 Sept | — |

## Risks

- **B1 changes when setup is re-validated.** It must still refuse a real rig move at capture start. That is covered by a test.
- **F3 can make things worse if done halfway.** The range fix and the −2 ms constant change together, behind the replay check.
- **C2 changes the admission gate.** The approved-hash list starts with today's hash, so nothing changes until a new rig file is approved.
- **B3 widens what counts as a ball at 1280×800.** The shadow and blob cases are tested, and T6 adds expected size and row in the ladder.
