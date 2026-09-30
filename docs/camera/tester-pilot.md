# Camera mode study — tester guide

For the first run of the complete fusion bench, follow the
[Pi commissioning procedure](pi-commissioning.md) alongside this guide.

The test suite finds the shortest camera exposure that still works at full
resolution (1280×800), and compares it with 640×400, while recording everything
the camera and both radars see. You hit a 7-iron; the page sets each exposure
itself, gives a verdict after every swing, skips exposures your light cannot
support, and takes a photo of the club face after each full-resolution swing.
The capture walkthrough does not show estimated club or ball numbers. Its
separate [saved-capture review page](recorded-track-comparison.md) supports
manual annotations and conditional geometry comparisons after recording.
Use **View fusion diagnostics** for existing pipeline results as shots finish,
including source labels, partial outcomes and recorded camera/IWR disagreements.
The [diagnostics guide](live-fusion-diagnostics.md) explains hidden-metric blocks
and why a completed result does not establish measurement accuracy.

## Requirements

- The measured v3 enclosure (`config/enclosure_v3_rig_geometry.json`) at its
  recorded foot extension, with the lens 95 mm above the supporting floor.
  A matching file alone does not prove that your physical unit matches it.
- Raspberry Pi 5, OV9281 with the OpenFlight high-speed driver installed,
  OPS243, IWR6843LEVM, sound trigger, and the LIS3DH connected. The
  inclinometer runs on every session; it is how ball height is solved.
- A 7-iron, balls, foot powder spray, and a cloth. Confirm the physical setup
  before starting; the [setup requirements](tester-setup-gate.md) list the
  approved sensor offsets and LIS3DH orientation.

## Before your first run

Once per Pi:

```bash
sudo apt update && sudo apt install -y swig liblgpio-dev python3-dev
```

`lgpio`, the Pi 5 GPIO library OpenFlight uses for the sound trigger, is
compiled from source and needs SWIG (a build-time code generator from the
Raspberry Pi OS repositories; it runs only during that build) and the lgpio
and Python headers. `start-tester.sh` checks for them and prints this line if
they are missing.

Set up the camera as in the [camera README](README.md#raspberry-pi-packages),
including the high-speed driver. `rpicam-hello --list-cameras` must list
`320x200` before you start.

The reviewed tester is published on the fork's `feat/tester-capture-pilot`
branch. If your clone's `origin` is the upstream repository, add the fork once:

```bash
git remote add fork https://github.com/HarjotDhanota/openflight.git
git fetch fork
git checkout -B feat/tester-capture-pilot fork/feat/tester-capture-pilot
```

Confirm `git rev-parse --short HEAD` reports `ba082d8` or a later reviewed
revision before collecting evidence. Preserve existing Pi configuration,
source edits and recordings when updating; do not reset a dirty checkout.

## Running it

Stop the normal kiosk (it holds the camera and radars), then from the
repository root:

```bash
bash scripts/start-tester.sh
```

On the Pi itself, open `http://127.0.0.1:8765`. From another computer, use
`http://<pi-hostname-or-address>:8765`; `127.0.0.1` on that computer points
back to that computer, not to the Pi.

The tester also writes a rotating service log to
`~/openflight_sessions/tester_pilot/tester-server.log`. It remains available
after the terminal or browser disconnects, and the session bundle includes it
under `diagnostics/` with any rotated copies. Refreshing the page is safe:
the tester ID and active ladder state are restored from browser and server
state. If the ladder was deliberately stopped, use **Resume ladder**.

The runner holds each arm's exposure and gain fixed for the whole run;
auto-exposure is off by design. It reads the enclosure's inclinometer the
whole time, with the service the kiosk runs, shows the enclosure's tilt, and
applies it to the camera the kiosk's way; during **Capture swings** it hands
the sensor to the kiosk and takes it back afterwards. The OPS243 is expected on the GPIO UART
(`/dev/ttyAMA0`); pass `--radar-port <port>` to `start-tester.sh` if yours is
elsewhere. The first `start-tester.sh` builds the environment and takes a few
minutes; later starts are quick.

**Test runs without qualification.** Until a range qualification exists, the
automatic range always ends unqualified and swings get no tee range, so
fusion, club path and face angle stay blank. For a test session, start the
tester with `--use-unqualified-tee-range`:
swings then get the static IWR range if its checks accepted it, including one
the camera's window re-selected. The camera's own range is never handed over as
the tee range: without an accepted radar range, swings start with the tee range
pending even in a test session. The range summary's **swings get** line says
which it will be before you start. Face angle appears on the **Club path** tile
as `face ±x.x° (D-plane)`: an estimate from start direction and path, not seen
on the club. It uses only the path the tile shows, and only when it is
accepted. The IWR's horizontal zero is not calibrated yet, so a radar
horizontal launch or IWR club path reads *azimuth uncalibrated* and gives no
face angle; face angle comes from camera paths until that calibration exists.

What the kiosk was started with is written down with each run: `handed_to_swings`
in the arm's `arm.json`, the run's `setup_admission.json` and its
`tee_range.json` names the tee range (or pending), the setup candidate it came
from, whether the camera re-selected it, the lens height the kiosk used and the
one the radar solved, and the ball height, which is one radius (assumed on the
surface). The kiosk's `session_start` repeats the same values and both rig-file
hashes.

Use the numbered test suite for a normal collection: **1. Set up the rig**,
**2. Check hardware and light**, **3. Automatic ball range**, **4. Capture the
exposure ladder**, then **5. Review and package**. **Advanced: manual single-arm tools** is for a
maintainer-directed investigation of one mode; it is not the normal pilot and
does not replace the ladder.

## Test suite

On the study page, fill in **Who and where**, review the setup checks and confirm
the physical setup. Then work down **Test suite**:

1. **A. Check the hardware.** Ready means the software and camera answered.
2. **B. Measure the light.** Runs the camera's light screen at both modes, about
   a minute each. Keep the room as you will hit in; it also proves each camera
   mode streams. Each mode then reads "light sufficient", "more light needed"
   (swings become evidence only), "too bright: shorter exposures used" or "mixed
   light" (a sunlit patch clipped the brighter gains, so the gain stays below it).
   The last two are normal outdoors. Under the button the page shows how long
   ago the light was measured. Outdoors a measurement lasts 30 minutes; indoors
   it lasts the day. After that, or if you move between indoors and outdoors,
   the page asks you to measure the light again, and **C** waits until you do.
3. **C. Start the exposure ladder.** The page sets each exposure itself and shows
   which one you are on. Hit a normal shot, wait for the verdict, repeat. It moves
   on after 5 good swings, and skips exposures your light cannot support. An
   exposure fails after 3 red swings at any point, 2 red among its first 3, or 2
   too-dark reds in a row; when darkness failed it, the shorter exposures of
   that mode are skipped too, since they are darker still.
   **C** starts only when the setup's camera found the ball in the mode it
   starts in: without the ball's position no swing can be judged, so the page
   says to run the setup again instead.
   On the 1280×800 exposures: spray the face before each swing; after it, hold
   the face about 0.5 m from the lens inside the dashed box, press
   **Photograph face**, then wipe it. Before leaving full resolution, the page
   holds the final capture for its photo. Photograph it or choose **Skip photo**
   before continuing; do not take another swing while this choice is pending.
   The kiosk then restarts in the next mode (about 20 s). A failed photo can be
   retried without losing the pending capture. The photo sets its own exposure
   from the measured light, down to a few tens of microseconds in sun, so it
   works outdoors too.

   Above the button, tick boxes choose which exposures run: 300 to 10 µs at
   1280×800 and 300, 150, 75, 30 and 15 µs at 640×400. All are ticked unless you
   changed them; the page remembers your choice. Unticked exposures are
   skipped and recorded as "not selected by the tester", not as a light
   failure. If no 640×400 box is ticked, the ladder ends after 1280×800 with no
   restart; if no 1280×800 box is ticked, it starts at 640×400 and asks for no
   face photos. **B** is needed only for a mode with a ticked box. Exposures
   that have already run are greyed out. To change your choice partway, press
   **Stop**, change the ticks, then press **C** to carry on.
4. **D. Analyse, review & package.** Stop the ladder first. If you have a TM4,
   Full Swing KIT or Mevo Gen 2 export, choose it first. One press replays every
   shot on the Pi, builds the session review and writes one session bundle. It
   runs in the background: you can leave or refresh the page, and a stopped or
   interrupted analysis resumes where it left off. Capture is refused while it
   runs. When it finishes, **Open the session review** shows one card per
   attempt, including camera triggers that never became a shot: the first,
   trigger and last frames with the camera's accepted ball region and the
   candidates it found, the impact photo, and every metric with its status,
   source, confidence and reason. Experimental values such as radar spin are
   labelled as candidates. See [session review workflow](session-review-workflow.md)
   for the status vocabulary.
5. **Download the session bundle** and copy it off the Pi (USB stick or the
   browser download). It can be about 1 GB and is never overwritten; its SHA-256
   is shown beside the link. Pressing D again when nothing has changed keeps the
   existing bundle instead of filling the card with copies. To review on any
   computer: extract `review.html` from the bundle ZIP (or use **Download the
   offline viewer** on the tester page), open it in a browser, then choose the
   original, unextracted bundle `.zip`. The same review appears and every file is
   checked against its recorded hash. Use the [community contribution package
   workflow](community-contributions.md) to record consent and create a
   shareable archive.

**Review saved capture tracks** is for manual point annotation when a
maintainer asks for it. **View live fusion diagnostics** shows the live
pipeline's pending and final states as shots finish; "processing finished" there
means the pipeline stopped working on the shot, not that fusion succeeded.

Reloading the page restores the ladder display. If the ladder was stopped,
press **C** to resume. A pending final photo stays associated with its original
capture across Stop and Resume; photograph that same strike mark, or skip it
if the face has already been wiped or used for another swing. Skipped photos
are recorded with the capture in the session bundle.

### Measure capture rate (optional)

You do not need this for the ladder: the kiosk captures every swing it hears
and the ladder judges it by itself. The tally, folded away under **Measure
capture rate (optional)**, is only for when a maintainer asks you to count your
swings, to measure how often the system misses one. It currently compares
totals only; a later version will match each press to a capture by time.

To use it during either the ladder or a single-arm capture, check the selected
arm and run, then press **Record swing** once for each physical swing. If the system missed it, use **Record missed shot** instead; do not press
both for the same swing. Record warmups and false triggers with **Other event**.
**Undo last** removes the latest observation from the tally while preserving
its audit record in the package.

The selected run stays pinned when the ladder advances so the last swing can
still be recorded against its original run. Select the new run before recording
its swings. Saved runs remain available after Stop and page reload. If a save
is uncertain, confirm the pending entry with the retry control before recording
another; its original identity prevents a duplicate.

The tally compares recorded swings with logged sensor shots. A matching count
does not prove that the same shots were matched or that every swing was recorded.
Physical capture rate and accuracy remain unknown until coverage and independent
reference matching are established. Missing tallies show unknown values, not zero
swings. Use one tester service to write a capture directory at a time.

New sessions also preserve a source archive beside their logs, and exports verify
and retain it. This records the allowlisted source on disk at session startup,
including local edits, plus software versions. It does not certify loaded module
bytes or device firmware. Collection failures are recorded without stopping capture.

## The arms

The closed **Advanced: manual single-arm tools** section below the suite is for
investigating one mode by hand when a maintainer asks for it. A normal tester
should use the exposure ladder above.

| Arm | Mode | Exposure | Why it exists |
| --- | --- | --- | --- |
| 1 | 320×200 @ 450 | 300 µs | reference: 2× sampling, high frame rate |
| 2 | 320×200 @ 450 | 175 µs | arm 1 at a shorter exposure → blur against noise |
| 3 | 320×200 @ 450 | 87 µs | 1.5 px at the full 130 mph head speed → blur against noise |
| 4 | 640×400 @ 120 | 300 µs | arm 1 at 1:1's frame rate → frame rate alone |
| 5 | 1280×800 @ 120 | 300 µs | arm 4 at 1:1 sampling → pixels alone |

Exposure is not a setting you choose. 300 µs keeps a 7-iron's smear under
4 mm on the frames the estimators use; arms 2 and 3 are deliberately shorter
so the data shows what exposure costs. Gain is found per arm from a static
screen and stops at 12×, above which the sensor adds offset rather than
signal; an arm that needs more at your light is telling you its floor.

## What counts as accepted

The delivery estimator reports a named status for every swing. **Accepted**
means it produced a club delivery (`ok`, `fused`, `chained_high`,
`approach_high`). Everything else — `low_light`, `overexposed`,
`rejected_insufficient_features`, `no_impact`, … — is recorded with its reason
and counted against the arm. The page also names pairing problems: camera
frames without radar dumps, or counts that do not line up.

## What is deliberately absent

The swing study does not require each tester to collect checkerboard images.
Independent optical calibration is a separate maintainer bench step; the v3
nominal focal length is still uncalibrated, and transfer between units, focus
settings and readout modes is not established. No enclosure measurements:
the rig file carries them. No lux meter: the gain screen records a light index
that is comparable across every unit. No exposure or gain choices in the
normal ladder: both are derived. No driver or wedge yet; no second camera.
Fusion diagnostics are available on a separate screen, but remain diagnostic
evidence rather than a result from the capture walkthrough or proof of accuracy.

## What this pilot does not do

Apart from finding a room-lit resting ball, which goes upstream as its own
pull request, it changes no production camera default, driver table,
geometry, calibration constant, threshold, or fusion behaviour. Each of those
is a separate maintainer-approved pull request. Camera/radar agreement is a consistency
check, not proof of absolute accuracy.

## Troubleshooting

### Automatic ball range

The main workflow asks for empty/ball IWR captures, an Arm 5 reference frame and
an Arm 6 validation frame. Each setup epoch is immutable; **Start over / ball
moved** preserves it and creates a new one, while refresh resumes the saved step.
Missing qualification or disagreement ends in raw-only mode: the ladder remains
available, as long as the camera found the ball, but range metrics stay withheld. A qualified Arm 5/IWR pair freezes
one range for both modes; Arm 6 cannot change it. Advanced tape is validation only.

For the two radar captures, stand in one spot outside the radar's view (behind
the rig) for both, keep still, and keep others away. The ball is a weak radar
target: in the first tape-checked test at 1.25 m its return was about a quarter
of the room's typical background, so a person standing somewhere different for
the second capture changed the picture more than the ball did and the pair was
correctly rejected.

Each camera step first finds its own static exposure: it raises exposure at full
gain until the ball is visible, then locks the lowest exposure and gain whose
applied camera metadata match the request and whose ball pixels pass the
brightness and clipping checks. Save stays disabled until that
lock holds, and the lock is dropped if the light changes. If no setting passes,
the step reports that more light is needed: add light and retry, or keep the view
as unqualified raw evidence, which finishes the setup raw-only; with no ball
found at 1280×800 the ladder then refuses to start. This lock is for
the stationary ball only; it never sets swing-capture exposure. Its thresholds
are provisional until the camera lighting study.

The ball search does not assume the lens height: feet sink into carpet and a
unit may stand on something, so each candidate's implied camera height is
solved from its apparent size and position, and only places where a resting
ball could be (below the horizon, at a plausible height) are searched. Once
found, each live look re-fits the ball where it was in a fraction of a second;
Save still runs the full-frame search as the independent check. Contrast
against the surroundings and edge sharpness are recorded but no longer gate the
lock: in camera levels both grow with exposure exactly as the background does, so
a fixed floor only pushed the search into clipping. On 29 Sept a white ball on a
white door was found and fitted at 2 ms × 12 with 3 levels of contrast, and the
old 12-level gate drove the search to 8 ms × 10 with 5 % of the ball clipped.
Whether the ball stands out is the detector's call.

The search does not walk every setting. Brightness on this sensor is
proportional to exposure × gain, and so is the ball's signal. So the first
frame sets how far to jump while the ball is still invisible, and the first
measured ball predicts where its signal is just enough and where it would clip. Steps predicted to be clearly too dark or clearly clipped are
skipped; the rest are still verified lowest exposure first, so the lock is the
lowest passing setting. On synthetic scenes this takes 3–7 settings instead of 11–15.

Heights are measured from the hitting surface, so the setup ball goes directly on
the mat or grass, never on a tee: its centre is then one radius up. The radar's
floor-bounce model (its vertical launch angle) needs the radar's height above the
surface it reflects from, which a tee-top reference would get wrong.

The lens height normally comes from the rig file, which is right whenever the unit
and the ball stand on the same surface. When the static radar accepted the ball,
Save also solves the lens height from the radar's range along the ball's pixel
ray. One ball solves it only to about ±25–65 mm, so the solve is a gross-error
check: it replaces the rig file's height only when the two disagree by more than
60 mm (for example a unit standing on a box). Swings are then started with
`--solved-camera-height-m`, which moves the radar height with the lens; the kiosk
log states both. A solve that would put the radar below the hitting surface is
refused. A mat under the ball but not the unit (about 20–30 mm) is below what one
ball can see; in simulation it moves the radar's vertical launch by up to about
1° at low launch angles, which is within that model's present noise at this
radar height.

The setup's two static radar captures (empty scene, then ball) run through one
radar session (`scripts/iwr6843/static_range_session.py`), started at the empty
capture and closed after the ball capture. Closing the radar's CP2105 port after a
capture costs 5 s on the Pi (the kernel's purge-on-close times out, logged as
`cp210x ttyUSB0: failed set request 0x12 status: -110`; draining or clearing
HUPCL first does not avoid it), so the port is now closed once per setup, after
the ball capture has already reported. Before any other hardware job (hardware
check, swings, ladder) the tester closes an idle session; an unused one closes
itself after 10 minutes. Session messages go to `iwr/static-radar-session.log`.

Measured on the Pi on 29 Sept, before these changes: 18.9 s per capture
(configure 4.8, transfer 7.1, close 5.7, settle 1.0). Configure then waited out a
0.3 s read timeout per command, which is fixed. Every capture record
(`iwr/<capture>.json`) lists `stage_seconds` and `total_seconds`. A 14-frame
profile with the same windows, `config/iwr6843_static_range_14f3ms_53bin_iq16.cfg`,
moves 427 KB (about 4.1 s instead of 7.1 s). It is opt-in (`--iwr-static-config`
on the tester server) until an A/B on the Pi shows the same accepted range and
frame stability as the 24-frame default.

The empty capture also measures the net: the strongest still reflector 2-6 m out,
at least 10 dB above the rest of that window, is recorded as `net_range` in the
setup evidence and handed to swings as `--net-range-m`, so the swing server's ball
gates stop 0.25 m short of it. The static capture's window ends near 2.9 m
(apparent), so a net further away is not seen; swings then assume 4.6 m, and
`session_start`'s `net_range` says the value was assumed.

At Save the camera's own range to the ball (from its apparent size, found without
the radar's hint) sets a window of ±2σ, at least ±40 %, around it. If the radar
chose something outside that window, such as a person, a club or a net at another
distance, the radar selection is re-run inside the window from the saved profiles.
Both results are recorded in the setup evidence. The re-run only chooses which
change in the radar profile may be the ball; how much of the profile changed is
still judged over the whole search, so a ball at 1 m is not rejected as clutter
just because the window is narrow. If the camera puts the ball where the radar does
not search, or the re-run cannot be done, the radar's pick is kept on record but
marked rejected: it is not handed to swings and does not set the lens height.
Something ball-sized at the same distance as the ball, such as a shoe beside it, is
not separated, so the prompt asks you to step at least 2 m away before each capture.

The 640×400 step checks the 1280×800 one. Their ranges should agree within twice
their combined uncertainty; the summary's **640×400 check** line says whether they
do. A disagreement is flagged in red but does not stop the setup: check that the
ball did not move between the two Saves, and start over if it did. Only the
1280×800 step decides the lens height; 640×400's solve is recorded as a check.

Each camera step needs a pitch reading from the enclosure's inclinometer. If the
LIS3DH is off or still settling, the step is refused with "the LIS3DH reading has
no camera pitch" rather than assuming the camera is level; wait a few seconds and
press the step again.

Outdoors in sun the scene can be too bright rather than too dark. The gain screen
now starts at unity gain and judges the hitting zone (the sky clips at any usable
setting). If even unity gain is too bright at the screen's 300 µs, the arm is marked
too bright and stores a light-equivalent gain below 1; the ladder then gives each
shorter rung a real gain from it (for example 75 µs at about 2–3× in full sun) and
skips any rung whose hitting zone still clips. The setup exposure search reaches 30 µs
and unity gain, and says "too bright" when even that clips the ball. On 29 Sept the
old rule saved gain 12 as "lighting required" and every swing clip came out white.

The light is measured above the sensor's black level, which the camera reports
(16 DN on the OV9281), not above the darkest part of the picture: in sun nothing in
view is dark, and the old floor read the scene's own shadows as black. The light
index is the hitting zone's median per microsecond per unit gain, so a sunlit
strip of background no longer hides it.

Each ladder rung's check and every swing on it apply one light rule. The resting
ball must have at least 20 DN of signal and at most 5 % clipped, and the hitting
zone around it, which is the club's background, must have at least 10 DN: a dark
zone fails the rung even when the ball is fine, because the club cannot be seen
against it. A clipped background behind a well-exposed ball is only amber. The
ball counts only where the setup found it and at about its size there, so a
shadow or a second ball is not judged in its place. If the check fails, the ladder
corrects that rung's gain (up to three times, between unity and 12): down for a
clipped ball, up for a dark ball or zone, never so far that the ball clips. The
check uses only frames the camera took at the controls just set; new controls take
a few frames to arrive, and it waits up to a second for them.

A rung that fails for being too bright, or on its swings for anything but
darkness, skips only itself, so the shorter rungs still get their chance. A rung
too dark skips the shorter rungs in its mode, since they are darker still. A swing
taken while the ladder was correcting the gain or taking a photo is set aside: it
is kept, but it neither counts towards the five nor fails the rung. On 29 Sept
(Outdoors-test-3) the old zone rule failed every rung on a sunlit patio beyond the
mat, and each failure skipped the rest of its mode. The 1280×800 mode now has four sunlight rungs after 75 µs, at 50, 30, 20 and 10 µs, and 640×400 has two, at 30 and 15 µs (30 Sept, full sun: the setup locked the ball at 10 µs and 30 µs was marginal); the setup search reaches 10 µs, and the OV9281 accepts exposures down to 9 µs (one row). Indoors they are skipped with the rest once 75 µs is too dark. The pre-rung check looks for the ball only around where the setup saw it and takes nothing more than about one ball diameter away, so fence or foliage clutter is never judged in its place; a ball melted into a clipped, sunlit patch of mat is judged where the setup saw it, so the too-bright exposure is skipped. Each exposure now starts at the gain that keeps the setup's ball lock as bright (its applied exposure × gain; 640×400 without its own lock scales the 1280×800 lock by the two light steps' ratio), not from the hitting zone's light step, so in sun a short exposure starts near unity gain and the long ones are skipped as too bright before any swing. On 30 Sept (a 7 µs lock) that leaves only 10 µs at 1280×800, at about gain 1.3, and no 640×400 exposure, which would need about 3 µs. The kiosk now allows or withholds camera analysis of each swing by the same rule, on the setup's ball (its core clipped 5 % or less, 20 DN or more above black): a clipped background or a dark hitting zone no longer withholds it, as on 30 Sept, when every sunny clip was withheld for 20 % clipping of the hitting-zone box.

Keep the ball in the camera's view on the live preview: with the lens about 95 mm off
the ground, a raised mat edge or bumper between the unit and the ball hides it.

The ball search runs in worker processes (`--ball-search-workers`, default 2; 0
runs it in the tester process) so it never holds up camera capture. To see what
it costs on this Pi, run
`uv run python scripts/analysis/bench_ball_search.py <saved frame .pgm or .png> --pitch-deg <tilt>`.

Two things keep this search short. The 1280×800 search starts together with the
radar ball capture, since the ball is already at address, so it is usually
locked by the time the radar finishes; the page shows its progress during the
capture, and Save opens once the radar result is in. This early search does not
use the radar's range hint, which exists only after the radar finishes; the
640×400 step still uses it. Each tester also remembers the last lock whose Save
passed, per camera mode, in `static-exposure-memory.json` in the tester
directory, and the next setup tries it first. It must pass every check again;
any failure runs the full search. A policy or camera-mode change discards it.

The setup admission is also frozen with the epoch: the approved configuration,
operator confirmation and starting LIS3DH orientation must still match before
each capture, evaluation, finalization and later ladder admission. A changed rig
or orientation requires **Start over / ball moved**; a temporarily ineligible
setup is refused by the server even when a stale browser still shows an enabled
button. Camera qualification binds the exact rig, optical calibration, placement
and saved-image mode profile. IWR qualification additionally requires a finite,
positive measured range-bias uncertainty in the exact hashed calibration file.
Missing or legacy qualification fields preserve the captures but keep them
raw-only.

If the tester service restarts during a static IWR capture, it keeps waiting only
while the reserved capture process is alive within the bounded restart window,
then consumes its late immutable result. A dead or stale reservation becomes a
retryable step. Camera retries write a new immutable attempt frame and retain
every earlier frame and failure record for review.

| What you see | Cause | Fix |
| --- | --- | --- |
| `IWR6843 did not acknowledge '<config line>'`, at a different line each time, and RESET does not help | The radar board's CP2105 USB bridge stopped answering; `dmesg` shows `cp210x ttyUSB0: failed set request 0x12 status: -110` at each failure. RESET restarts the radar, not the bridge | Unplug the radar's USB cable for 5 s, plug it back in, run **Check the hardware**, then retry. If it keeps happening, try a short thick cable or a powered USB hub |
| `fatal: ambiguous argument 'origin/feat/tester-capture-pilot'` | `origin` is the upstream repository; the study branch is on the fork | Add the fork as shown in *Before your first run* |
| `Failed to build lgpio` … `swig: No such file or directory` | Build tools missing | `sudo apt install -y swig liblgpio-dev python3-dev`, then start again |
| Find gain fails with `IndexError: list index out of range` in `Picamera2()`, or `rpicam-hello --list-cameras` has no `320x200` | The camera or its high-speed driver is not set up on this Pi | Follow the [camera README](README.md#raspberry-pi-packages) setup, then reboot |
| The live view is soft or fuzzy even at 10000 µs | The lens is out of focus; fitting the camera into the enclosure can turn it | Turn the lens barrel until the ball's edge is crisp, then run **Find gain** again |
| The ball line says no plausible resting ball was found and the floor is clipped white | The carpet or mat around the ball is saturated, so a white ball cannot be told from it | Lower the exposure or the gain until the floor has texture again |
| The ball line says no plausible resting ball was found in a dim picture | The ball stands too little above the picture's noise at this exposure and gain | Raise the gain; the arm's own setting is still worth recording |
| `Removed virtual environment at: .venv` after running a repository script | Plain `uv run` uses the repository's Python 3.11 and rebuilds the environment without the Pi's camera library | Run repository scripts on the Pi with `.venv/bin/python`, not `uv run`; `start-tester.sh` restores the environment |
| `Creating virtual environment at: .venv` on a Pi that ran OpenFlight before | The runner rebuilds the environment when it cannot import `picamera2`; lgpio compiles again | Expected once; needs the build tools above |
| Every swing rejected with no ball speed | The OPS243 was not found | Pass `--radar-port` with your port (`/dev/ttyAMA0` for the GPIO UART, `/dev/ttyACM0` for USB) |
| An arm's counter stays at 0 while swings save | The estimator rejected them; the status histogram in the archive says why | Hit the five anyway if it reads `lighting required`; its acceptance rate is part of the result |
| Stopped mid-arm | Nothing is lost | Press **Capture swings** again; runs are kept separately and counted together |
| The page is slow, frozen, or disconnects | The browser, tester service, Wi-Fi, or Pi may have stalled independently | Preserve the session and run `tail -n 250 ~/openflight_sessions/tester_pilot/tester-server.log`; packaged data now carries this log automatically |
| Refreshing the page shows a held photo step | The ladder restored its durable boundary-photo checkpoint | Do not swing; use **Photograph face**, **Skip photo**, or **Resume ladder** if it says the ladder is stopped |
| Ladder verdict red: `frames: ... fps delivered` or `gap(s)` | The Pi could not keep up with the camera mode | Close other programs, check the power supply, run **A** again |
| Ladder verdict red: `controls: exposure ...` or `gain ...` | The camera did not take the exposure's setting | Press **C** again; the ladder carries on where it stopped |
| An exposure reads `failed … — 3 red swings`, or a shorter one `skipped … — full-300 failed: 3 red swings` | Three of its swings went red, whatever the order, so the ladder moved on rather than wait for 5 good ones | Read the red swings' reasons; the ladder continues on its own. A red for darkness also skips the shorter exposures of that mode |
| An exposure reads `failed … — 2 dark red swings in a row` | The light fell during the exposure (dusk, a cloud) | The shorter exposures of that mode are skipped; add light and measure it again (**B**) before another ladder |
| The review reads `No radar shot` for a swing | The OPS243 logged no shot within 30 s of the camera's trigger (all shot records normally arrive about 7 s after it, when the IWR dump ends), so the swing is set aside, not counted and not failed | Swing again; if it happens on every swing, check the OPS243's LED and its cable, and that the ball is in front of the unit |
| Ladder verdict red: `light: too dark ...` (the hitting zone or the ball) | This exposure is below what your light supports | Expected on the shortest exposures; the ladder skips the rest of the mode |
| Ladder verdict red: `light: too bright for the ball ... clipped` | The ball area is washed out | Expected on the longest exposures in sun; this exposure fails and the shorter ones are still tried |
| Ladder verdict red: `ladder: the camera did not apply ... within 1 s` | The kiosk has not taken the new exposure yet | Wait: the ladder retries every second. If it repeats for a minute, press **Stop**, then **C** |
| The impact photo is white or washed out | The light changed a lot since step **B** (the sun came out) | Run **B** again, then retake the photo; the pending capture is kept |
| `Measure the light again (B): this screen is … min old` or `… was measured indoors and you are outdoors now` | The light measurement is too old for the light you are in: 30 minutes outdoors, another day indoors | Press **B**, wait for both modes, then **C**. A rung you were partway through is checked again when the ladder resumes |
| After resuming the ladder, a rung's swing count is back to 0 | The light no longer passed that exposure's check, so the rung started again | Nothing to fix; keep swinging. The earlier swings are kept for review but no longer count |
| A swing is listed as set aside, `taken at ... not this rung's ...` or `taken during a still_photo` | It was taken while the ladder was changing the camera's settings | Nothing is lost; hit the next swing once the rung shows as set |
| `finish automatic tee range before capture (retryable_failure)` | The setup's range record no longer matches the one the ladder was admitted with | Press **Start over / ball moved** and run the setup again |
| Range summary: **swings get tee range pending** | No radar range was accepted (or none is qualified and the tester was not started with `--use-unqualified-tee-range`). The camera's own range is never used as the tee range | Swings still record; launch and club metrics that need the range are withheld. For a test session with a range, redo the setup with everyone clear of the radar |
| Range summary: IWR `rejected — camera_window_disjoint` or `not_rechecked` | The radar picked something outside the camera's range window and nothing inside it replaced it | Redo the setup: step well away from the rig during both radar captures and keep the ball in the camera's view |
| Range summary: **640×400 check disagrees** | The two camera modes put the ball at different ranges | Make sure the ball did not move between the two Saves; if it did, **Start over / ball moved**. The setup is not blocked |
| `the LIS3DH reading has no camera pitch` | The inclinometer is off or still settling (for example just after the kiosk handed it back) | Wait a few seconds for a stable reading, then press the step again |
| `The camera hasn't found the ball, so the ladder can't judge your swings` when pressing **C** | The setup's camera never picked out the ball (it saw spare balls or a white cloth, or the ball was too far, dark or raised) in the mode the ladder starts in. A setup whose radar range is unresolved still runs once the camera found the ball | Run the setup again with the ball 1.0 to 1.3 m from the lens, on the same surface as the unit (not a raised mat), and nothing ball-like or white in view, then press **C** |
| Ladder verdict red: `ball: the setup has no ball position for this camera mode` | The setup's camera never found the ball, so a swing's pictures cannot be checked against it | Press **Stop**, run the setup again until the camera finds the ball, then press **C** |
| Ladder verdict amber: `resting ball not found` | The camera could not distinguish a plausible resting ball in that frame | The swing still counts; keep placing the ball in the same spot |
| `run the gain step for both modes first` (or for one mode) | Step **B** did not finish for a mode that has a ticked exposure | Run **B** again |
| `Choose at least one setting.` or `choose at least one setting` | Every exposure box above **C** is unticked | Tick at least one exposure, then press **C** again |

