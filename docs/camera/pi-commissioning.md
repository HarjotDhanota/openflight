# First Pi session with the fusion bench

This procedure commissions the software and gathers diagnostic evidence. It
does not qualify launch-monitor accuracy. Follow the
[master plan](../development/fusion-master-plan.md) for the remaining gates.

## Install the intended checkout

Use the tester-pilot checkout containing the new setup gate, attempt ledger,
saved-capture review and fusion diagnostics. Local desktop edits are not on
the Pi merely because both machines show the same branch or commit. Transfer
the reviewed working tree or publish and fetch its revision before testing;
preserve any Pi-only configuration and recordings. Do not reset a dirty Pi
checkout to update it.

For the local morning handoff ZIP, follow its `PI_START_HERE.md`: clone the
fork into a new directory, check out the recorded base commit, then overlay the
archive there. The ZIP contains the uncommitted source, built UI and a file-hash
manifest; pulling the branch alone does not install these changes. Keep the
clone's `.git` directory because tester preflight records its base revision.

From that checkout, stop the normal kiosk and run:

```bash
bash scripts/start-tester.sh
```

Open `http://127.0.0.1:8765`. The launcher installs the camera extra and uses
the system Picamera2 installation. See the [tester guide](tester-pilot.md) for
the one-time Pi packages, driver and wiring requirements. Keep the terminal
log when startup fails; a failure is evidence to fix, not a reason to disable
the setup checks.

## Establish a level baseline

1. Match the [measured v3 rig](tester-setup-gate.md), including fixed mounts,
   the default foot extension and LIS3DH orientation. Measure the level lens
   height and radar-window-to-ball distance. Keep focus and lighting fixed.
2. Choose a unique tester ID for these conditions. Run **Check the hardware**,
   inspect the setup checks, then confirm the physical setup. Software
   preflight alone does not establish live radar acquisition.
3. Open one arm's live view and check that the stationary ball is visible and
   sharp. Set the tape distance, find gain, and start **Capture swings**.
4. Make a short screening block, allowing each shot to finish. Record every
   physical swing in the operator tally, including no-reads. Keep warmups and
   false triggers separately identified. Screening counts are not an accuracy
   or reliability acceptance sample.
5. Inspect **View fusion diagnostics**: shot identity stays consistent from
   pending to final, missing/rejected outputs have reasons, and displayed
   sources distinguish measurements from model-derived estimates. A terminal
   result is not a validation certificate.
6. Stop, reload, and resume. Confirm that exposure/gain and the selected mode
   are restored. Check that the saved capture can be opened in track review.
   For a full-resolution ladder run, complete or explicitly skip the pending
   final face photograph before the mode transition.

## Test deliberate tilt separately

Use separate tester IDs or clearly identified runs for level, positive/negative
pitch and positive/negative roll conditions. Change one axis at a time first.
Keep sensor mounts, foot extension, lens and focus fixed; record the observed
angles and any changed lens height, ball position or supporting surface.

A stable upright rig above two degrees on either axis should remain eligible,
show an amber warning and retain that warning in its evidence. Exactly two
degrees does not exceed the threshold. The rig must settle before acquisition:
moving, missing or invalid inclinometer observations remain blockers.

Do not infer successful compensation from a plausible number. Compare each
condition with a repeatable known target or an independently matched reference
shot. Golfer variation prevents separate swings from being identical test
inputs. Preserve the level baseline and all unsuccessful tilted attempts.

## Exercise failure and recovery

Between collection blocks, verify that an unconfirmed tester cannot start a
run and that missing/stale required sensor evidence produces a visible block.
Use a controlled service/device fault where practical; avoid disturbing
powered wiring. Stop the run, restore the device, and confirm readiness before
resuming. A saved capture made during failure must remain diagnosable and must
not increase the accepted-pair count.

Verify the local inclinometer resumes after stopping capture. Review, export
and stop controls should remain available when new acquisition is blocked.
Report the precise action and terminal log if recovery fails.

## Preserve the evidence

Use **Package the data**, including a reference-device export when available.
Keep the original package and raw capture folders; analyze copies. Record:

- Tester/run IDs, actual rig and lens/focus identity, lighting, measured setup
  and all placement changes.
- Operator swing counts, missed shots, false triggers and any ambiguous
  reference-shot associations. Do not silently pair rows by position.
- Startup/runtime logs and the saved source identity, setup admission,
  trigger-time readiness, camera archives and radar capture files.
- Whether diagnostics were visible, and any observed delays or dropped
  captures under the complete workload.

Missing evidence should be reported explicitly. A count match does not prove
physical shot matching; disagreement between two estimates is not reference
error. Independent optical calibration and LED/GATE/contact timing measurements
remain separate experiments even if this commissioning session succeeds.

## Run the new analysis paths

Start with the normal baseline configuration. Enable
[live calibrated projection](live-calibrated-fusion.md) only after supplying
the actual optical artifact and measured placement inputs; do not substitute
the example's origins or pivot for measurements. Missing calibration does not
prevent collecting baseline evidence.

After collecting, run the [commissioning report](commissioning-report.md) and
[raw replay](raw-fusion-replay.md). Preserve their explicit missing-input and
rejection reasons alongside the original capture package. Use the
[accuracy benchmark](accuracy-benchmark.md) for reviewed reference matches and
held-out acceptance criteria. A successful software run does not supply the
physical calibration, reference qualification or numerical limits those gates
require.

For a complete session candidate, append
`--benchmark-candidate-output replay-session-candidate.json` to the raw replay
command. This replays every logged sensor shot, including failed attempts, so
the candidate can be passed directly to `benchmark_accuracy.py --candidate`.
The specified club and sample rate apply to that replay experiment; preserve
their documented override meaning. Physical swings with no sensor event still
come from the separately reviewed operator ledger, not inferred shot numbers.

Use [controlled sensitivity replay](fusion-replay.md#offline-sensitivity-variants)
to compare recorded inputs with declared timing, depth, camera-origin or focal
perturbations. An unavailable result is part of the comparison. These reports
measure sensitivity and model disagreement, not error against a reference.

The benchmark's `--write-criteria-template` command creates an incomplete draft
for the reviewed metric contracts. Fill and freeze the protocol limits before
collecting held-out acceptance sessions. Missing limits remain incomplete;
the software does not select limits from the results it is evaluating.
