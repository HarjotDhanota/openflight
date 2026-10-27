# Fast mode: impact through displayed result

Prepared 27 October 2026. Software instrumentation only; no new Pi run or physical
impact-to-screen result is claimed. The two-second goal never drops a metric.

Use the existing `--pi-trace` recorder, browser clock exchange and visible-result
acknowledgement. Fast mode requests `two_round`; each capture must actually report
`used: two_round`. A capability fallback to Full is a Full observation, even if the
UI requested Fast. Run in an isolated test checkout with the reviewed temporary
RAM firmware, using the existing supervised upload/identity/recovery procedure.
Do not start a second serial owner alongside the kiosk.

## Reconciled software

The Fast host includes the overview/cells/release bridge, exclusive serial lease,
capture matching and frozen transfer choice. Browser receipt, clock mapping and
post-render reports were already implemented; no second browser timer is added.
The OPS change `fd02979e` saves capture evidence before rearm. It does not hand the
capture to analysis early. `SoundTrigger.wait_for_trigger` still performs clock
selection and rearm before returning to the processor. Those waits remain in the
benchmark; changing that wire ordering needs a separate tested implementation.
The existing order avoids a documented net-impact/serial deadlock.

This branch adds research-only observations:

- Actual per-shot requested/used mode, capture/session identity, overview, cell and
  release command/response brackets, round-analysis durations and release failures.
- A separate processing-complete state. `final=True` alone is insufficient: a
  finalized OPS result can still await enrichment. Late completed revisions remain
  separate from the initial speed-only display.
- Sensor-readiness samples associated with an unchanged GATE sequence. A poll
  spanning a new edge cannot associate its previous state with that edge.

No estimator, trigger default, serial ordering or firmware changes are made.
The established Pi trace stage contract and core-eligibility verdict stay intact.

## Capture procedure

1. Retain the exact host commit, firmware image/source hashes, Pi boot identity,
   camera mode/exposure/lens settings, OPS baud and buffer split, rig/calibration
   identities, UI build/renderer, kiosk dimensions, workload and power state.
   Preserve original captures, sparse payloads, logs and every failed attempt.
2. Verify the reviewed RAM image advertises overview/cells/release, startup is
   idle/no-fault, and both UART ownership and recovery are understood. Passing a
   full-dump diagnostic is not a Fast-transfer qualification. Do not use the
   instrumented full-dump duration as the Fast throughput result.
3. Append `--pi-trace --iwr6843-transfer two_round --log-dir <run-directory>` to the
   established `scripts/start-kiosk.sh` configuration. Keep the actual rig,
   camera and radar arguments. Build the matching UI first and display Live in
   the local kiosk browser. Hidden/covered results and remote pages cannot count
   as local result presentation. Keep a dated attempt sheet, including misses,
   empty swings and non-shot triggers.
4. Begin with one controlled shot and inspect source identity, actual Fast mode,
   payload integrity, release/rearm, metric provenance, browser acknowledgement
   and kernel errors before repetition. Compare reconstructed data and estimator
   decisions against Full processing of suitable retained reference captures.
   Never compare two different swings as though their metrics should be identical.
5. For a descriptive pilot, declare five warmup shots then twenty recorded shots
   per condition before starting. These counts are protocol choices, not a
   statistical qualification. Retain warmups separately. Repeat at the intended
   camera modes and kiosk sizes under normal load. Do not pool unlike conditions.
6. Stop the application normally so the trace footer is written, close the serial
   owners and follow the supervised normal-firmware restoration check.

Analyze the saved trace without touching hardware:

```bash
uv run python scripts/analysis/summarize_pi_trace.py <trace.jsonl> \
  --sheet <attempts.csv> --out <summary-stem> --board-delay-unmeasured
```

The sheet columns are `sheet_id,kind,wall_time`, with timezone-aware ISO timestamps
or epoch seconds. `kind` is `swing`, `empty_swing` or `clap`. Supply an allowance
only when its geometry/calibration and uncertainty are documented. With no valid
impact anchor/allowance, impact-to-display remains not measurable; host stages
and GATE-relative durations can still be reported. Do not enter zero to force a
goal result. The current sound-detector delay remains uncalibrated.

## Read the report correctly

Keep these quantities separate for each shot:

| Quantity | Evidence and limits |
|---|---|
| First displayed result | Existing first-revision receive/post-render stages; may contain speeds only |
| Qualified core displayed | Existing eligibility and two-second goal ledger; unavailable metrics do not qualify |
| Processing-complete revision displayed | Newest observed completed revision, with a qualifying visible-core acknowledgement; status and pending revisions excluded |
| Fast transfer | Per-exchange host brackets, including radar work and scheduling; not pure UART serialization |
| Sensor readiness observed | First same-edge green sensor-state poll after the OPS first byte; a sampled report, not an exact transition or a browser-ready-light paint |

The browser post-render task is a presentation proxy, not independent proof of
physical pixel illumination. It verifies the emitted live core, not every metric
on every tab. Failed/missing browser mapping or a rendered-core mismatch leaves
the completed-display stage unobserved. The report does not assume the newest
observed completed revision is the last possible future update.

Overview transmission can overlap OPS collection, camera loading overlaps radar
processing, and cell requests alternate with analysis. Never sum overlapping
stages or add independently computed percentiles. Report per-shot elapsed time,
then p50/p95/p99/max, sample counts and all unobserved/failed attempts. Keep USB
close time separate from metric-display latency while retaining it as a reliability
failure. Narrow clock intervals do not establish physical acquisition alignment.
