# Tester setup eligibility

The mode study requires the measured v3 setup. A camera preview, a successful
software preflight or a file named `enclosure_v3_rig_geometry.json` is insufficient
to admit a capture. Setup eligibility is separate from optical calibration and
measurement accuracy.

## Required physical setup

The approved geometry describes the measured Pi 5 unit with the OV9281 camera,
nominal 2.8 mm lens, OPS243, IWR6843LEVM, sound trigger and LIS3DH. In the recorded
camera frame, +x is target-right, +y is down and +z is forward from the lens front:

| Quantity | Recorded requirement |
|---|---|
| OPS offset | (-85, 47, -20) mm |
| IWR receive-array center offset | (0, 44, -30) mm |
| Microphone offset | (-80, 0, 0) mm |
| Lens height above the supporting floor | 95 mm at the measured foot extension |
| Camera pitch / radar mount pitch | 0 degrees / +10 degrees |
| Reference housing pitch | 0 degrees; test placement deviations are measured and recorded |
| LIS3DH mounting pitch, roll, yaw | (0, 0, 180) degrees; +Y arrow points rearward |

The operator must confirm that the physical unit matches this arrangement and
that the feet, mounts, lens and focus have not changed. Merely choosing this
profile does not establish a physical match. Different enclosures, board
orientations or foot extensions require their own measured and reviewed setup
profile before joining this study.

Deliberately tilting the intact rig is allowed for correction tests. Keep its
sensor mounts and optical configuration fixed, and record any resulting change
to placement or ball setup. The nominal 95 mm reference height is not proof
that the lens remains at that height after repositioning.

Software can check configuration values and sensor observations. It cannot
inspect a mounting bracket, measure lens height, identify a substituted lens,
or prove target-line alignment. Operator confirmation is recorded as an
attestation, not as a sensor measurement or calibration certificate.

## Operator workflow

Enter the tester ID, inspect the setup checks and resolve each named blocker.
Check the physical confirmation box only after inspecting the actual unit,
then press **Confirm physical setup**. **Recheck setup** refreshes the current
server assessment. Gain collection, single-arm swings and ladder acquisition
require admission; hardware troubleshooting, saved-data review and export do
not require a confirmed rig.

**Check the hardware** must pass for the current tester before the normal
acquisition controls become eligible. Its IWR check opens the single-port
firmware CLI and sends `help`; it does not apply the static range profile or
capture a dump. The empty and ball-present steps still own those later actions
and preserve their own results. A passing CLI preflight establishes only that
the tester could reach the board at that time, not that its configuration,
static capture or calibration is usable. An unusable guided IWR capture
invalidates that preflight result, so **Check the hardware** must pass again
before Retry can advance the flow. Retry itself only changes guided state; it
does not open or command the radar.

Confirmation belongs to the tester ID and the exact geometry/inclinometer
configuration in this server instance. A server restart requires confirmation
again. Changing a configuration invalidates its earlier confirmation. Recheck
and reconfirm after moving the setup or changing its physical arrangement;
software cannot detect every mechanical change.

An in-progress automatic-range epoch remains bound to the confirmation that
started it. After a server restart, reconfirm the physical setup and then use
**Ball or rig moved: start over**. The stale epoch cannot be retried under the
new confirmation; the API persists `start_over_required` and the page disables
its Retry action rather than appearing to ignore the tap.

The tester checks its LIS3DH before handing hardware ownership to the kiosk.
The kiosk then checks its own sensor state. Do not start a second I2C reader
while it owns the sensor. The **ready** state describes observed acquisition
conditions, not radar calibration or validated fusion accuracy.

## Data policy

Missing or uncertain readiness must block eligible test acquisition. An
unplugged LIS3DH, stale orientation or moving unit must not silently fall back
to the rig's nominal pitch for this study. The sensor-owning capture process
must enforce runtime readiness after the tester hands over the hardware.
Normal camera buffering and radar dump transfer must not be mistaken for
hardware loss.

The camera records a frozen readiness snapshot at each accepted trigger. It
includes the configuration identity, logging session, run directory and sensor
observations. A later recovery cannot change that earlier snapshot. The kiosk
also rejects trigger evidence from a different session or configuration.

For paired study acceptance, the recorded camera capture must join to one
logging session and shot, a successful saved IWR dump and the terminal OPS shot
record. Waiting for the radar transfer is a pending result. Missing, failed or
ambiguous evidence cannot count as accepted paired data. The join does not
require an angle or club estimator to succeed: algorithm failures are valuable
test evidence and must not be filtered out by a hardware gate.

Preserve failed attempts and their reasons. A setup failure must not count as
an accepted swing or as evidence that an exposure setting is unusable. Keep
saved-data review, export and stopping available while acquisition is blocked.
Use the independent attempt tally for swings that produced no sensor event.

## Saved evidence and troubleshooting

`setup_eligibility.jsonl` under the tester directory records confirmation and
admission decisions. Each new run has `setup_admission.json`, binding its
operator confirmation and configuration. Camera metadata retains `tester_setup`
trigger evidence. The study package includes these files alongside the original
session logs and captures.

The paired-evidence check reads at most 32 session files, 16 MiB per file,
32 MiB in total and 20,000 lines per file. Exceeding those bounds withholds
eligibility with a reason; it does not delete evidence. A final record without
its newline remains unfinished. Export the original files for diagnosis rather
than editing the logs to bypass a failed check.

`--no-inclinometer` permits a tester server for saved-data work; it does not
qualify hardware acquisition. A missing sensor must be repaired or connected,
not replaced with a nominal pitch. An incompatible rig needs a separately
reviewed measured profile. Do not edit a profile just to make the checks pass.

The IWR6843 custom firmware uses the CP2105 **Enhanced/UARTA** interface for
both CLI and dump traffic. Prefer its stable
`/dev/serial/by-id/...-if00-port0` name and start the tester with
`--iwr-static-port` when USB numbering is not stable. Omitting the option keeps
CLI auto-detection. Do not encode a particular `/dev/ttyUSB` number in the rig
configuration.

Pitch or roll departures greater than two degrees produce a nonblocking
warning. The observed angles, departures and threshold travel with admission
and trigger evidence. Exactly two degrees does not trigger the warning.
Stable, finite observations and an upright gravity direction remain required;
missing, stale or moving sensor data cannot qualify a capture.

The two-degree threshold is a data flag, not a demonstrated accuracy limit.
Flagged captures remain eligible for collection and transport checks, but
must remain identifiable when comparing baseline placement with larger tilts.
No warning means only that this threshold was not exceeded. It does not mean
the system corrected placement accurately. The configured zero offset remains
part of the setup fingerprint.

Current IWR processing applies measured pitch departure to its configured
radar tilt. Camera projection infers pitch from the reference ball and uses a
configured image-roll correction. These paths are not yet a common verified
full placement transform. Compare repeatable targets and paired references at
baseline and deliberate positive/negative pitch and roll deviations separately,
preserving no-reads and original setup inputs. Do not label an estimate
"corrected successfully" solely because it produced a number.

These safeguards prevent identifiable setup errors; they do not establish
accurate fusion. Optical calibration, radar verification, independent timing,
reference matching and Pi workload measurements remain separate gates in the
[master plan](../development/fusion-master-plan.md).
