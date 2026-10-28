# Shared sound GATE evidence

The Pi observes BCM17 once, before queueing an IWR dump or calling any observers.
The resulting immutable `GateEvent` follows the IWR capture, camera trigger and
optional Pi trace. This is the first software step toward a common fusion timeline.

| Evidence | Before | With this change |
| --- | --- | --- |
| GPIO event age and clock brackets | Read by the trace observer when tracing is enabled | Read once per armed callback, with tracing on or off |
| Camera trigger | Its own callback timestamp | Callback timestamps plus the original GATE observation |
| Camera callback delay | Requires joining separate records | `gate_to_camera_callback_ns` records a lower and upper bound relative to the observed kernel tick |
| Capture identity | Nearby wall timestamps | IWR and camera evidence share an `event_id`; the trace calls it `gate_event_id` |
| Physical timing accuracy | Unmeasured | Still unmeasured; `physical_edge_uncertainty_ns` is null |

`kernel_mono_interval_ns` is `[mono_before_ns - age_ns, mono_after_ns - age_ns]`.
It is available only for the existing LGPIO backend, valid nonnegative readings,
and an event newer than the preceding callback. Unknown backends, missing or stale
ages and failed readings leave it null. Raw readings remain available for diagnosis.
The interval describes the observed kernel tick; it does not bound electrical-edge
to IRQ latency or sound travel and detector delay. Compare monotonic values only
within the same boot; join the same event across records by its ID.

Camera `metadata.json` carries the evidence in `trigger_clocks.gate_event` and
`trigger_clocks.gate_to_camera_callback_ns`. The IWR capture object carries
`gate_event`, and its session JSONL entry preserves the serialized observation.
The camera-only GPIO path uses the same reader. Legacy float trigger observers
still receive the original wall timestamp, including the OPS recovery listener.

The original change retained callback-based camera ring counts, NPZ trigger
timestamps and exposure split. The 25 October follow-up adds a bracketed camera
clock conversion and counts the tail against the mapped gate when available;
see [the timestamp and tail contract](../camera/timing-validation.md#shared-gate-and-capture-duration).
The legacy callback fields remain. IWR serial commands, OPS clock sync and
re-arm order and coarse shot matching are unchanged. Neither change maps IWR
cycle counters or OPS samples to the Pi clock for live metric calculations.

On the Pi, collect several captures with tracing enabled and disabled. Check that
paired camera/IWR records have the same event ID and raw readings, inspect the
interval widths and callback-delay distribution, and retain unknown results.
Use the existing GATE LED for the optical timing experiment in each camera mode
and exposure. Then validate GPIO latency, camera timestamp semantics and the
IWR external-edge latch before enabling a calibrated fusion mapping or claiming
the proposed 1 ms alignment target. There is no hardware accuracy measurement
from this software change.

Original shared-event validation on Windows: 1,100 tests passed across the capture, camera,
trace, server, logging, OPS trigger and provenance paths; four skipped because
Linux CLOCK_BOOTTIME or historical capture fixtures were unavailable. Ruff passed
on changed Python files; backend pylint passed its 9.0 threshold (9.77/10).
No real sensor hardware was exercised.

## Trigger source and acoustic contact

New sound events serialize `source: sound_gate`; camera trigger metadata and NPZ
archives retain `trigger_source: sound_gate`. This tag identifies the capture
request, not physical impact or calibrated clock accuracy. The legacy camera
GATE entry point remains sound-only. A separate typed capture-request entry point
can retain sound or OPS event evidence; no live OPS input is subscribed here.

The live and frozen head-track paths read the archive's source before estimating
contact. Explicit OPS, unknown or malformed sources cannot use acoustic travel,
detector calibration or the legacy callback-based sound window. Unlabelled older
archives keep their historical sound interpretation, with their existing
unqualified status; their source is not retrospectively labelled as observed.
Explicit sound captures retain the same acoustic math and calibration rules.

This is preparation for supporting both trigger paths. Live sync-input wiring,
qualification and the internal-trigger camera/IWR route remain pending. The current
internal-trigger full-fusion startup restriction stays in place. No firmware,
IWR transaction flow or live trigger selection changed.

## Selected-source request routing

`CaptureRequest` carries source, event/boot/arm identities, the observed kernel
MONOTONIC interval, receipt time and coarse wall time for association. It never
claims that the event is impact or supplies physical edge uncertainty.
`CaptureTriggerRouter` accepts one selected sound or OPS rising event per arm.
Other sources, including OPS capture-end edges, do not request a capture. Stale
boot/arm, reused event and missing kernel timing are retained as refusal results.
Rejected/failed consumers do not suppress the other consumers or retry a partial
capture. The caller must save every result and explicitly disarm/rearm only after
the capture owners release their buffers. Callbacks run without the router lock;
in-flight notification prevents disarm. Device readiness is not inferred here.

Consumers execute in supplied order: enqueue the IWR request first, then notify
the camera. They must not wait on transfers. The typed camera entry point maps
the original kernel interval through its bracketed camera clock reads, counts its
tail after the mapped upper bound and retains `capture_request`, callback lag and
`capture_event_boottime_interval_ns`. OPS requests never create sound GATE fields.

New typed captures also save `pre_trigger_count_by_capture_event`; exposure-based
analysis prefers it to the callback's legacy count. The old callback timestamp
and count remain diagnostic evidence, and older clips keep their interpretation.
The frame boundary is the interval's upper bound, not a claimed exact electrical
edge, shutter time or impact. No sensor timing has been physically qualified.
