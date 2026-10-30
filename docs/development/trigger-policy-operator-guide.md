# Trigger comparison operator guide and handoff

Use the combined normal worktree `openflight-fusion-native-integration`, branch
`integration/fusion-native-triggers-2026-10-30`. The five policy commits from
`openflight-trigger-policies` at `854915f9` were integrated on the reviewed
metric/clock source `64193a85`. The original isolated policy checkout is preserved.
Combined validation and package details are in the parent workspace directory
`research/native-trigger-policy-integration-2026-10-30`; original policy evidence
remains in `research/trigger-policies-2026-10-30`. Nothing was pushed or deployed.

## Choose and run

1. Use the normal tester setup, resolution and club selection. The new **Capture
   policy** selector defaults to **Sound baseline**. Legacy `capture_mode: sound`
   maps to Sound; `capture_mode: native_internal` maps to Independent.
2. For native comparison, the tester still requires its reviewed
   `--internal-sync-clock-package`. Choose Independent, OPS-led (IWR association
   only), or Hybrid confirmation (OPS retention). IWR-led is the recommended
   experiment, but its notification is currently too late to retain OPS history.
   IWR-led and Common hardware remain visible and unavailable, with reasons.
   Without the reviewed package, only Sound is available.
3. Press **Start free play**. For native capture, follow the existing switch and
   RESET wizard only when an authorised operator is at the rig. The kiosk still
   uses its existing measured setup, calibration, processor and live metrics.
   The software does not automate switch changes, flashing or persistent writes.
4. Remove the ball and prepare an attempt; follow empty-lane learning and ball
   placement instructions. Swing only when Armed. Each radar retains independently;
   the OPS sampling edge retains the camera immediately. OPS-led cannot initiate
   IWR capture. Hybrid adds the existing exact ownership/GPIO/conditional IWR clock
   overlap check as a publication gate, after retention. Unconfirmed evidence stays.
5. **Stop & package** retains partials and original evidence. Follow normal IWR
   boot restoration and confirm reconnection before another hardware action.
   The selector stays locked through recording, stopping and packaging. Start a
   new run to change policy. A conflicting API/CLI selection fails visibly.

Normal shot publication continues through the existing ball-strike processor.
Dry-swing evidence may be retained without a shot. Native speed training is **not
supported**; no club-only record is promoted into a fabricated ball shot.

## Review and grading

The existing session review page has **Trigger policy comparison** under each
run. It includes every native attempt directory, including quiet, interrupted,
unconfirmed and unpublished attempts. Expand its timelines and coverage beside
the existing raw frame previews. The packaged JSON review and Markdown report
also retain the comparison. No separate bench app was added.

The run and each native attempt hold `trigger-policy.json`. Attempts retain
`iwr-observations.jsonl`, original radar bytes, GPIO/routing and serial journals,
`RESULT.json`, `fusion-association.json`, `policy-decision.json` and host
`publication.jsonl` milestones when they occur. An interrupted decision remains
explicitly incomplete. Existing calibration, measured setup and source/build
provenance remain in the normal run/package.

New free-play launches enable the existing `--pi-trace` instrumentation. A native
OPS wait gets its own existing trace identity, with first-byte/completion marks;
the existing shot/browser trace carries that identity to screen revisions. The
review includes trace integrity, clock-mapping uncertainty, stage timings and
post-render acknowledgements. An invalid trace supplies no derived timings.
This is a browser post-render **proxy**, not physical presentation or impact.
The GPIO-to-host notification latency and host rearm command duration have their
own clocks. Missing screen acknowledgements remain unmeasured.

Counterfactual policy decisions are labelled separately from acquisition. They
reuse already retained evidence; they cannot reconstruct expired buffers or
establish what another live hardware route would have captured.

Human grading is optional and never feeds acquisition or metrics. After stopping,
a maintainer can write `trigger-labels.json` in the run directory and rerun the
normal analysis/package step. It is a JSON list with these fields per activity:

```json
[
  {"id": "operator-activity-id", "kind": "real_shot", "attempts": []}
]
```

This is a format example, not a recorded swing. `kind` is `real_shot`, `dry_swing`,
`quiet` (operator-confirmed nuisance activity), or `unknown`. `attempts` contains
exact run-relative paths such as `native-sync/<run-id>/attempt-001`. Leave it empty
for a labelled activity that produced no retained candidate. Multiple paths mean
duplicate attempts from one activity. Never assign one attempt to multiple labels.
Foreign paths, duplicate IDs and invalid labels are reported and excluded.
This sidecar currently requires maintainer editing; the review does not provide
an annotation editor. The existing operator swing tally remains available too.

Counts distinguish labelled real-shot misses, real shots without publication,
retained valid dry swings, duplicates, full-sensor associations and labelled false
captures. No labels means no inferred negatives or measured false-capture rate.
Observed armed exposure ends at the first host detector observation (or disarm
request). IWR polling can overestimate it; this is an observation-based denominator,
not a physical detector-on-time qualification. Rates are labelled and withheld
when exposure is missing. Sound uses its existing trigger records/tally and trace;
native exposure is not fabricated for sound. Unlabelled capture counts remain
visible and prevent interpreting labelled rates as a complete false-trigger grade.

## Next labelled rig session

Use repeated labelled blocks of quiet time, nuisance motion, real ball strikes
and valid dry swings for Sound, Independent, OPS-led association and Hybrid.
Record every planned activity, including those with no candidate or published
shot. Record block order, club, tee/rig position, lighting, conditions, firmware,
calibration and wiring changes; keep settings fixed where routes can coexist.
Passive IWR/OPS observations are already retained on native runs. Sound and the
native RAM route need separate blocks and verified restoration between them.
No reference speed truth is required for capture reliability. Speed accuracy
requires an independent reference and is not graded by this work.

Before attempting IWR-led, qualify an early IWR detector output and a real OPS
retention path whose measured coverage precedes ring expiration. Before enabling
common hardware capture, verify the actual wiring, input handler/firmware and
each buffer's semantics. No dropdown or offline replay closes either gate.
All records keep `physical_timing_qualified: false`.

Native attempts now save `iwr-trigger-observation.json` when the host first sees
the IWR latch. The review's candidate timeline shows this observation separately
from the later held result. Its timestamp is host receipt of a polled status,
not a device event/contact time; expected shot/generation IDs are not validated
result IDs. It does not request OPS capture or enable the unavailable IWR-led
mode. The current 50 ms polling interval cannot guarantee retention with the
configured 25.6 ms OPS prehistory; the complete dump spans 136.5 ms.

The owner confirmed on 30 October that sound GATE is disconnected from OPS
J3 pin 3. Verify that connection before using the Sound comparison baseline;
this confirmation does not describe the detector's power or other GPIO wiring.
The OPS driver now accepts an optional per-attempt software-trigger event on its
existing dump reader. It sends S! once without clearing input, preserving an
already arriving dump and recording host write/flush bounds. This transport is
not connected to the normal IWR latch callback yet and does not enable IWR-led.

## Integration boundaries and validation

Review this work as separate capability, policy-selection, journal/trace and
comparison-report patches. Shared conflicts are expected in `native_fusion.py`,
`native_free_play.py`, `tester_server.py`, `tester_hooks.py`,
`tester_sync_session.py`, `joint_clock_rig.py`, the setup page and native kiosk
controls. Reporting also adds to `session_review.py` and its existing page.
Do not broadly merge the other agent's newer metric/onset/transition work.
Association mathematics and metric extraction thresholds are unchanged here.

Validation evidence is in the parent research directory: the initial unchanged
native/launch/rig baseline passed 43 tests; the affected backend batch passed 459
with one platform skip; all 891 UI unit tests and the production build passed.
The broader server batch passed 525 with two platform skips; the final focused
batch passed 122 with one platform skip; the final malformed-selection check
passed 26; the final reporting/trace batch passed 54 with one platform skip.
These batches overlap and must not be added together as unique coverage.
All 48 affected browser checks passed, including setup-to-Start propagation,
mode locking, restoration controls, native metrics, sound baseline, packaging,
normal review and all three kiosk sizes. The final report-only browser repeat
passed three. Ruff, formatting, ESLint, build and local commit hooks passed;
report-module Pylint scored 9.61, above the repository's 9.0 gate.
This is not a full-project green claim. The original handoff's
unrelated baseline failures remain outside this workstream. No Pi, sensor,
wiring, firmware load, reset, swing, physical latency or accuracy test occurred.

The combined normal source includes the newer metric/onset/transition/head/spin
fixes and the reviewed clock-package support. `tester_sync_session.py` retains
the selected package's firmware identity alongside the policy journals;
`run_joint_clock.py` retains both exact manifest/image identities. The rebuilt
RAM package includes the upstream raw-dump freeze fix, but has not been loaded
or tested on this rig. See [native free play](native-fusion-free-play.md) for its
location and restart requirements. Use the combined handoff's validation results
for this source; the preceding test counts describe the original policy branch.
