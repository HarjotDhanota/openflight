# Trigger policy feasibility, 30 October 2026

Source: `5584a432ec06de88371afb76ba9bb524c0dbc3bd`. Local implementation only,
in `openflight-trigger-policies`, branch `feat/trigger-policies-2026-10-30`.
Ownership: trigger selection, additive policy/report modules, narrow hooks in
native_free_play/native_fusion/tester_server, tester_hooks, shared collector and
setup/kiosk controls. The original integration checkout stays untouched. Clock,
association and metric fixes belong to the other workstream; no newer fixes are
merged here. Shared-file changes must be reviewed as patches before integration.

## Sensor capabilities

| Sensor | Actual detector/output and clock | Notification | Retention and duration | Rearm and wiring |
|---|---|---|---|---|
| IWR6843 reviewed RAM build | Independent ball-bin detector, held result/clock packet; device frame-start ISR and trigger/result counters, conditionally mapped to host MONOTONIC | `stats` polling every 50 ms plus UART exchange; held result fetched only after RF stops. No verified early detector GPIO output | `phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 1`: 9 pre frames, 7 impact frames, 47 ball frames at stride 8; 3 ms nominal frame period. Post capture is approximately 1.15 s. Raw dump uses existing driver on the same UART. No new force command is introduced | Fresh identified RAM generation; empty-lane background, stable ball lock, explicit arm; verified sensorStop before next attempt. Existing USB only; no shared trigger wire verified |
| OPS243-A 1.3.3 | Independent speed detector; J3 pin 1 sampling pulse to BCM27; raw sample_time/trigger_time are device times, pulse is not impact | Kernel edge timestamp and host receipt retained separately; actual notification delay measured per edge, physical sample correspondence unqualified | Existing internal-speed capture freezes 4096 I/Q samples at 30 ksps: 136.53 ms total; S#6 requests 25.6 ms prehistory. Vendor documents S! and HOST_INT (J3 pin 3) for rolling-buffer capture, but a delayed host command cannot recover expired history | Existing validated configure and explicit internal rearm; idle after dump; no rearm during readout/publication. J3 pin 1 is an output, not a common capture input |
| Camera | No independent swing detector; existing rolling ring and timestamped frames | OPS sampling GPIO through existing CaptureTriggerRouter; host callback is separate from sensor timestamp | Existing 200 ms pre / 150 ms post request, actual coverage from saved arrays; request acceptance alone is not capture | Existing capture runtime shared with normal kiosk; event/arm ownership; never open a second camera or sound listener |
| Sound baseline | SEN-14262 GATE, existing path | Existing GPIO receipt and sound timing semantics | Existing OPS HOST_INT and camera/IWR sound route, unchanged | Existing wiring and restoration procedure; status explicitly says sound baseline |

These are source/protocol findings, not measurements of this rig's live latency.
No hardware was accessed. The IWR profile's post history alone exceeds the entire
OPS ring, so **IWR-led retention is unavailable**. A stats poll is neither a
qualified early interrupt nor a contact timestamp. Lowering sample rate changes
the OPS measurable speed range and is outside this task.

The host collector now records the first polled latch observation immediately,
before waiting for the complete result. This is an observation hook, not an
IWR-led capture route or a measured detector timestamp. The current 50 ms polling
interval also exceeds the configured OPS prehistory: `S#6` is 768 samples, or
25.6 ms at 30 ksps. The full 136.5 ms dump is not a permissible notification
delay. An IWR-led experiment must qualify the early path against the selected
prehistory and required post-event coverage, including host/command delays and
the still-unqualified relationship between detection and physical contact.

## Policies

* `sound`: existing default comparison baseline.
* `independent`: backward mapping of saved `native_internal`. Each radar retains
  its own detection without the other firing; OPS retains camera. IWR-only and
  OPS-only attempts remain partials. Independent does not promise camera evidence
  for an IWR-only detection or zero false negatives.
* `ops_led`: **association only** for IWR. OPS retains OPS/camera immediately;
  IWR independently retains its event. It cannot initiate an IWR capture.
* `hybrid`: same prompt retention; publication additionally requires the existing
  exact-event/GPIO/conditional frame-clock vs actual camera-coverage association.
  This explicit timing rule is bounded by the actual saved clip, not UART receipt
  or an invented contact tolerance. Unconfirmed attempts and reasons are retained.
  The first eligible low-latency retention detector on this rig is OPS; IWR's
  later notification never delays OPS retention. This is a restricted hybrid route.
* `iwr_led`: recommended experiment, unavailable until an early detector output
  and OPS retention path have measured coverage. No automatic substitution.
* `common_hardware`: unavailable: no verified common wiring, RAM firmware input
  handler and capture semantics across all three sensors. SYNC_IN frame start is
  not synonymous with preserving a detector-centered rolling capture.

Native speed training remains unavailable. Raw dry swings are retained, but
normal publication still requires the existing ball-strike processing gates.
No thresholds, calibration, geometry or accuracy labels change.

## Primary sources checked

* [OmniPreSense AN-027](https://omnipresense.com/wp-content/uploads/2025/06/AN-027-A_Rolling-Buffer-1.pdf), pages 1–3: fixed ring, sample rates, segmented prehistory, trigger input/API and rearm. Local internal-speed command ordering remains from the reviewed driver, not inferred from this rolling-buffer note.
* [OmniPreSense AN-010-AD](https://omnipresense.com/wp-content/uploads/2025/10/AN-010-AD_API_Interface.pdf), pages 21 and 27: newer mode names GC (rolling) and GS (normal), with S!, S#n and HOST_INT. It supersedes the old G1/G0 names in AN-027. These documents do not qualify the 1.3.3 sampling-output edge as physical impact; that remains a separately gated local firmware/driver route.
* [TI IWR6843 datasheet Rev F](https://www.ti.com/lit/ds/symlink/iwr6843.pdf): hardware interfaces do not establish this custom firmware's trigger behavior.
* [Fusion architecture issue](https://github.com/open-flight/openflight/issues/289): detection, synchronization, association and fusion remain separate. The linked comment was not exposed by the fetched page; no unobserved comment claim is used.
* Local `joint_shot_clock.py`, `joint_clock_active.py`, `tester_sync_session.py`,
  `joint_clock_rig.py`, `firmware/iwr6843/l3_dump.c`, `ops243.py`,
  `capture_trigger.py`, `native_fusion.py` are the executable capability evidence.

All policies retain `physical_timing_qualified: false`. IWR reducing false OPS
triggers remains a hypothesis. Software mocks cannot qualify timing or coverage.
