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

**The setup saves as experimental.** No range qualification is required or used.
After both have looked, the camera's ball in the patch and the radar's candidates
in the patch check each other, and the setup always saves what they found,
labelled **experimental**: the radar's distance when the two agree within twice
their combined uncertainty; the camera's distance, with a warning, when the radar
found nothing or disagrees; the radar's strongest candidate, with a warning, when
the camera found no ball; and nothing, with "No ball found in the patch. The ball
may be outside it: move the ball or the patch.", when neither did. The page, the
run's records (`handed_to_swings` says `experimental: true`, which source it used,
the pair and the warning) and the session review all say experimental: nothing has
qualified this range, so nothing built on it is a measurement yet. The range
summary's **swings get** line says which it will be before you start.
`--use-unqualified-tee-range` only matters for a setup finished before this change.
The only things that stop the setup are a sensor that is not connected and a rig
moved in the middle of it. Face angle appears on the **Club path** tile
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

**The ready light.** A band pinned to the top of the study page, and the same
band across the top of the unit's own screen, says when to swing. **SWING**
(green) only when the OPS243 is armed and waiting, the IWR6843 is idle and
armed, and the camera is running in the current setting with no clip being
saved. **WAIT** (amber) names what is busy, with a rough time left: the OPS243
dumping, draining or re-arming, the IWR6843 dumping (about 7 s), or the camera
saving a clip. **NOT READY** (red) names the cause: no admitted setup, a
sensor missing or failed, the kiosk starting or restarting between settings,
the ladder's light check, a face photo owed, or the ladder stopped or
finished. The kiosk reads each state from the sensors themselves, not from
timers, and logs every change with its time (`[READY]` lines). A swing it
picks up flashes the band once, then the band shows the result: the ball
speed, **No radar shot** or **Not a shot**. Wait for SWING before each swing;
a swing on amber or red may not count.

Use the numbered test suite for a normal collection: **1. Set up the rig and
place the patch**, **2. Check hardware and light**, **3. Automatic ball range**,
**4. Capture the exposure ladder**, then **5. Review and package**. **Advanced:
manual single-arm tools** is for a maintainer-directed investigation of one mode;
it is not the normal pilot and does not replace the ladder.

**The patch is step 1.** Set the unit down where it will stay and confirm the
physical setup; the live 1280×800 picture then opens by itself under **Place the
patch**. The patch is a 2 ft (0.61 m) square on the ground, drawn in perspective:
drag it to where you will hit from, near or far, and press **Confirm the patch**.
Everything after it works from the patch, and stays locked until it is confirmed:
the hardware check (A), the light measurement (B), which is judged inside it, the
ball range and the ladder. The patch is also the camera's first check: if the
picture does not open, the step says the camera did not answer and offers **Show
the camera** (see [Automatic ball range](#automatic-ball-range)).

## Test suite

On the study page, fill in **Who and where**, review the setup checks and confirm
the physical setup. Then place the patch (step 1, above) and work down **Test
suite**:

1. **A. Check the hardware.** Ready means the software and camera answered.
2. **B. Measure the light.** Runs the camera's light screen at both modes, about
   a minute each. Keep the room as you will hit in; it also proves each camera
   mode streams. The light is judged inside the patch you confirmed (halved for
   640×400), not over a fixed part of the picture, so a bright sky or a dark
   fence elsewhere does not decide it. Each mode then reads "light sufficient",
   "more light needed" (swings become evidence only), "too bright: shorter
   exposures used" or "mixed light" (a sunlit patch clipped the brighter gains,
   so the gain stays below it). The last two are normal outdoors. Under the
   button the page shows how long ago the light was measured. Outdoors a
   measurement lasts 30 minutes; indoors it lasts the day. After that, if you
   move between indoors and outdoors, or if you move the patch, the page asks you
   to measure the light again, and **C** waits until you do.
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

   Below **C**, one button per exposure switches to it straight away, ticked or
   not, and also starts a stopped ladder on it. Swings already taken on the
   exposure you leave keep counting when you come back to it; an exposure with
   its 5 good swings reads "done" and can't be pressed; pressing a failed or
   skipped one reopens it with a fresh red count. A pressed exposure runs even
   in light its check calls too bright or too dark (the panel shows the warning),
   and its swings are judged as usual. When it finishes or fails, the ladder goes
   on with the ticked exposures in their usual order. Within a mode only the
   camera's settings change; a press into the other mode restarts the kiosk as
   usual, after any swing already taken has been judged (and, leaving 1280×800,
   after its face photo). Each press is recorded with its time for the review.
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

**First, the patch (step 1).** Set the unit down where it will stay and confirm
the physical setup. The page then opens the live 1280×800 picture under **Place
the patch**, with the patch where you last confirmed it, or 1.25 m straight ahead
of the unit the first time. The patch is a 2 ft (0.61 m) square lying on the
hitting surface, drawn in true perspective: near the unit it is wide and deep in
the picture, far away it is narrow and thin. Drag it (mouse or finger) over the
spot you will hit from; while you drag, the page says how far its centre is from
the radar and how far left or right ("Patch centre 1.25 m from the radar, straight
ahead."). Its centre can go from 0.8 m to 3.0 m. Then press **Confirm the patch**.
Nothing is checked or captured before that: the hardware check, the light
measurement, the radar captures, the camera's ball search and the ladder's ball
checks all work from the patch, and nothing looks anywhere else. The patch is
stored on the ground (its centre and corners in metres from the unit), with its
outline in the picture and the camera tilt it was drawn with, in
`placement-box.json`.

The dashed outline around the patch is where the camera looks for the ball. Its
columns are exact; its rows are padded because the camera's tilt is known only to
a few degrees until it is calibrated (see *Camera tilt* below): before then the
dashed area is taller, afterwards it hugs the patch. With no ball in it the page
says "No ball found in the patch. The ball may be outside it: move the ball or the
patch." The picture sets its own brightness while it is open, so the scene shows in
sun or at dusk before the light has been measured; "Adjusting brightness…" shows
under it until it settles, usually within two to four looks. That brightness is
for viewing only: it is logged as `box_preview` and never used as a light
measurement, a lock or a starting point for the camera checks. If the picture does
not open, the step says the camera did not answer (the first camera check of the session); fix the cable or close whatever holds the
camera, then press **Show the camera**. After a restart of the tester, or a new
physical setup confirmation, the page asks for the patch again, starting where you
last confirmed it; confirming it in the same spot changes nothing.

**The patch is the hitting zone.** Wherever the tester judges the hitting zone it
uses the confirmed patch's search area instead of the fixed centre-lower part of
the picture (rows 45–90 %, columns 20–80 %): the light screens (B), the ladder's
light checks ("N% of the box clipped") and the kiosk's capture-time exposure rating
when it has no setup ball. The fixed zone remains only when no patch was confirmed,
and every stored zone result says which was used (`zone_source`: `placement_box`
or `fixed`).

**Moving the patch later.** Press **Move the patch**, drag it and confirm it. A
patch moved more than 2 cm on the ground makes the light measurement stale (B asks
to be run again, and **C** waits for it) and starts the ball range over, the same
as **Ball or rig moved: start over**; the new setup records the move, the previous
setup and the time (`started_by`), and `placement-box.json` keeps the history of
every confirmation with its time. Stop the ladder first: a move is refused while a
capture holds the camera or radar. Confirming the patch in the same spot changes
nothing.

Step 3 then begins directly with the radar captures, with the live picture and the
patch over it while you clear the spot and place the ball (viewing only: nothing
is measured from it). The workflow asks for an empty and a ball radar capture and
one 1280×800 Save; there is no 640×400 check: the ladder's 640×400 settings use the
1280×800 ball halved. Each setup epoch is immutable; **Start over / ball moved**
preserves it and creates a new one, while refresh resumes the saved step. The
setup always finishes **experimental** (see *The setup saves as experimental*
above) or, when neither sensor found a ball in the patch, with no range: the
ladder remains available, as long as the camera found the ball, but range metrics
stay withheld. Advanced tape is validation only.

For the two radar captures, stand in one spot outside the radar's view (behind
the rig) for both, keep still, and keep others away. The ball is a weak radar
target: in the first tape-checked test at 1.25 m its return was about a quarter
of the room's typical background, so a person standing somewhere different for
the second capture changed the picture more than the ball did and the pair was
correctly rejected.

The camera step first sets its brightness on the ball, in a few steps. It
starts from the light measurement (B) or the last lock that passed, and uses the
camera's linear response (signal above black is proportional to exposure × gain)
to go straight to a target: when it sees a ball, the next step brings the ball's
brightest part to about 60 % of clipping, so it is at least 20 DN above black and
not clipped; when it sees no ball and the patch itself is dark, it brings the patch
up and looks again; when it sees no ball in a lit patch, it looks again at the same
setting, and after four such looks it stops with "No ball found in the patch".
Not finding the ball is never taken to mean the picture is too dark. Each step uses
the shortest exposure that reaches the brightness, then gain. At most eight changes
are made; the lock is the setting whose applied camera metadata match the request
and whose ball pixels pass the brightness and clipping checks. Save stays disabled
until that lock holds, and the lock is dropped if the light changes. If the ball
cannot be made bright enough, the step reports that more light is needed: add
light and retry, or keep the view as unqualified raw evidence, which saves what the
radar found; with no ball found at 1280×800 the ladder then refuses to start. This
lock is for the stationary ball only; it never sets swing-capture exposure.

The ball search looks only inside the patch's dashed outline, and only for balls
of the sizes the patch's near and far edges allow (25 % either way). It does not
reject a ball for sitting above or below the floor row it expects: that height is
recorded (`floor_height_residual_m`) but never used to refuse, because the camera's
tilt alone moves it by more than a ball. When several ball-like things are in the
patch, they are ranked by how well each fits a lit sphere; if two fit about as
well, the step says "Several ball-like things are in the patch: keep only the ball
in it" and looks again at the same setting, never brighter. The camera never uses
the radar while it searches. Once found, each live look re-fits the ball where it
was in a fraction of a second; Save still searches the whole patch as the
independent check. Contrast against the surroundings and edge sharpness are
recorded but do not gate the lock.

Heights are measured from the hitting surface, so the setup ball goes directly on
the mat or grass, never on a tee: its centre is then one radius up. The radar's
floor-bounce model (its vertical launch angle) needs the radar's height above the
surface it reflects from, which a tee-top reference would get wrong.

The lens height normally comes from the rig file, which is right whenever the unit
and the ball stand on the same surface. When the camera and radar agree on the
ball, the pair also solves the lens height from the radar's range along the ball's
pixel ray (once the camera tilt is calibrated). One ball solves it only to about ±25–65 mm, so the solve is a gross-error
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

The radar searches only the patch's distances: from its near edge to its far
edge as seen from the radar, widened for the camera's tilt uncertainty and 0.1 m
either way. The two captures are compared two ways, and every candidate either way
is reported with its distance, score and (for the coherent one) elevation:

* by magnitude: every place in the window where the ball capture's echo grew by a
  quarter or more over the empty one, strongest three kept;
* coherently, one virtual channel at a time: each channel of the empty capture is
  first scaled by one complex factor fitted on still reflectors outside the patch
  (a radar restart turns every channel's phase), then subtracted from the ball
  capture; each clear change is a candidate, flagged when its elevation is not at
  ground level.

Nothing is rejected for a fraction below a fixed gate, and a changed scene (a
person or a club elsewhere) is a warning, not a rejection. A patch whose far edge
is beyond the default capture's reach (about 2.75 m) uses the far profile,
`config/iwr6843_static_range_18f3ms_72bin_iq16.cfg` (18 frames, out to 3.7 m
apparent), chosen automatically.

After both have finished, the camera's ball and the radar's candidates check each
other: a candidate agrees when it is within twice their combined uncertainty of
the camera's distance (from the ball's size). Of the agreeing ones, the closest
wins, then the strongest; a coherent change off the ground is never taken for the
ball, so a person standing behind the ball stays a candidate the pair does not
pick. The summary's **ball** line says what happened (camera and radar agree,
camera only, radar only, disagree, no ball), with both distances and how far the
ball sits left or right of straight ahead (from the camera's ray at the radar's
distance); its **radar candidates** line lists everything the radar saw in the
patch. Something ball-sized at the same distance as the ball, such as a shoe beside
it, is not separated, so the prompt asks you to step at least 2 m away before each
capture.

**Camera tilt.** The enclosure's inclinometer (LIS3DH) gives the camera's pitch,
but the camera sits in the enclosure a degree or two differently from it, and at
the lens's 95 mm each degree moves a ball at 1.25 m by about a quarter of a metre.
So the first setup in which the camera and radar agree calibrates the unit: it
solves the camera's pitch that puts the ball's pixel at the radar's distance and
stores the difference from the LIS3DH's pitch in
`~/.config/openflight/camera-vertical-offset.json` on the Pi (per unit, not in the
rig file). Every later setup and swing uses the LIS3DH pitch plus that offset,
labelled `unit_calibration_from_a_validated_pair`, and its patch outline tightens.
A solve beyond ±8° is refused as a sign that the ball, the radar's distance or the
lens height is wrong. Later agreeing pairs only check it: a difference of more than
1.5° is shown as a warning on the summary's **camera tilt** line, and the stored
offset is not replaced. To calibrate again (after a rebuild, or a camera that
moved in the enclosure), delete that file. The calibrating pair cannot also check
the lens height (it assumes the rig's); later pairs do.

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
capture, and Save opens once the radar result is in. Each tester also remembers
the last lock whose Save passed in `static-exposure-memory.json` in the tester
directory, and the next setup tries it first. It must pass every check again; any
failure runs the brightness steps. A policy or camera-mode change discards it.

The setup admission is also frozen with the epoch: the approved configuration,
operator confirmation and starting LIS3DH orientation must still match before
each capture, evaluation, finalization and later ladder admission. A changed rig
or orientation requires **Start over / ball moved**; a temporarily ineligible
setup is refused by the server even when a stale browser still shows an enabled
button. A range qualification file, if one is configured, is still checked and
its result recorded on each candidate, but it no longer decides anything: the
setup saves as experimental either way (`qualification_outcome: not_required`).

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
| Range summary: **swings get tee range pending**, `No ball found in the patch. The ball may be outside it: move the ball or the patch.` | Neither the camera nor the radar found a ball in the patch | Swings still record; launch and club metrics that need the range are withheld. Check the ball is inside the patch on the picture; if it is not, move the ball, or press **Move the patch** in step 1 and drag it over the ball (the ball range then starts over) |
| Range summary: **experimental range**, ball line `camera and radar agree` | The normal result: the camera's ball and a radar candidate agree | Nothing to fix. Numbers built on it are labelled experimental |
| Ball line `camera and radar disagree` — `The camera puts the ball at … m and the radar at … m` | The radar's strongest candidate is not where the camera's ball is: something else in the patch changed (a person, a club, a second ball), or the ball moved | The camera's distance is saved, with the warning. Redo the setup if you can: stand in the same spot, at least 2 m away, for both captures, and keep clubs, spare balls and bags out of the patch |
| Ball line `camera only` — `The radar found no ball in the patch` | The ball's echo did not stand out in the patch's distances | The camera's distance is saved, with the warning. Keep everyone still and away during both radar captures; on a far patch (beyond about 2.5 m) the ball's echo is weak |
| Ball line `radar only` — `The camera did not find the ball in the patch` | The camera could not pick the ball out (too dark, clipped, or several ball-like things) | The radar's strongest candidate is saved, unconfirmed. Fix the light or clear the patch and start over for a camera-checked range |
| Summary **camera tilt** line in red: `… it disagrees by … deg` | This setup's ball puts the camera's tilt more than 1.5° from the unit's calibration: the unit stands raised or tilted differently, or the camera moved in the enclosure | If the unit is on the hitting surface and the camera was not touched, ignore one; if every setup says it, delete `~/.config/openflight/camera-vertical-offset.json` on the Pi and the next agreeing setup calibrates again |
| Summary **camera tilt** `not calibrated` — `… beyond ±8 deg: it was not stored` | The pair's solve is implausible: the ball, the radar's distance or the lens height is wrong | Check the unit stands on the hitting surface and nothing else is in the patch, then start over |
| `Finish this setup` on a setup from before this change | It reached the old 640×400 check, which no longer exists | Press **Finish this setup**: it saves what the 1280×800 camera and the radar found |
| `the LIS3DH reading has no camera pitch` | The inclinometer is off or still settling (for example just after the kiosk handed it back) | Wait a few seconds for a stable reading, then press the step again |
| `The camera hasn't found the ball, so the ladder can't judge your swings` when pressing **C** | The setup's camera never picked out the ball in the patch (a spare ball or a white cloth in it, or the ball outside it) | Run the setup again: drag the patch over the spot you hit from, put the ball inside it on the hitting surface, keep spare balls and white things out of it, then press **C** |
| `No ball found in the patch. The ball may be outside it: move the ball or the patch` during the camera step | The camera looks only inside the patch's dashed outline, and nothing ball-sized for that distance is there | Move the ball into the patch. If the patch is in the wrong place, press **Move the patch** in step 1 and drag it over the ball (the ball range then starts over) |
| `Several ball-like things are in the patch: keep only the ball in it` | Two things in the patch fit a lit ball about equally well (a spare ball, a tee, a white tuft or a logo) | Take everything but the ball out of the patch; the step looks again at the same brightness |
| **Confirm the patch** stays greyed out, or `The camera is not showing` | The live picture is not running, so there is no patch to place | Press **Show the camera**; if it still fails, run **A. Check the hardware** |
| Ladder verdict red: `ball: the setup has no ball position for this camera mode` | The setup's camera never found the ball, so a swing's pictures cannot be checked against it | Press **Stop**, run the setup again until the camera finds the ball, then press **C** |
| Ladder verdict amber: `resting ball not found` | The camera could not distinguish a plausible resting ball in that frame | The swing still counts; keep placing the ball in the same spot |
| `run the gain step for both modes first` (or for one mode) | Step **B** did not finish for a mode that has a ticked exposure | Run **B** again |
| `Choose at least one setting.` or `choose at least one setting` | Every exposure box above **C** is unticked | Tick at least one exposure, then press **C** again |

