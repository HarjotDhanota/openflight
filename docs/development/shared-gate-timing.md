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

The existing camera ring freeze, NPZ trigger timestamps and exposure split retain
their callback-based definitions. The IWR serial command, OPS clock sync and
re-arm order, coarse shot matching and metric calculations are unchanged. This
change does not yet map IWR cycle counters or OPS samples to the Pi clock.

On the Pi, collect several captures with tracing enabled and disabled. Check that
paired camera/IWR records have the same event ID and raw readings, inspect the
interval widths and callback-delay distribution, and retain unknown results.
Use the existing GATE LED for the optical timing experiment in each camera mode
and exposure. Then validate GPIO latency, camera timestamp semantics and the
IWR external-edge latch before enabling a calibrated fusion mapping or claiming
the proposed 1 ms alignment target. There is no hardware accuracy measurement
from this software change.

Software validation on Windows: 1,100 tests passed across the capture, camera,
trace, server, logging, OPS trigger and provenance paths; four skipped because
Linux CLOCK_BOOTTIME or historical capture fixtures were unavailable. Ruff passed
on changed Python files; backend pylint passed its 9.0 threshold (9.77/10).
No real sensor hardware was exercised.
