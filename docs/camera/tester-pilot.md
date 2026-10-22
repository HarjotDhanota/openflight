# Camera mode study — tester guide

## Free play for outside testers

Free play is implemented on the local `feat/tester-free-play` branch. It still
needs the Pi checks below before a tester release. **The test is only for units
with all four sensors**: the OPS243 speed radar, the IWR6843 ranging radar, the
OV9281 camera and the LIS3DH tilt sensor. Start `scripts/start-tester.sh`, open
`http://<pi-address>:8765/`, and work down the steps:

1. **Tester and lens.** Tap your tester ID (or **New tester**), **Indoors** or
   **Outdoors**, **Right-handed** or **Left-handed**, and the lens fitted to the
   unit. The hand is kept per tester (right-handed until you tap left) and given to
   the kiosk at start; a connected simulator's hand wins over it. The scoreboard
   header shows it beside the club ("7 Iron · Right-handed").
2. **Check the unit.** A. Tap **Check the unit**: the speed radar, the camera and
   the ranging radar each answer, and the tilt sensor is read all the time. A
   missing sensor is named ("Missing or not answering: Camera (OV9281)") and setup
   stops there. Tick **I pressed RESET on the ranging radar board** only when the
   ranging radar fails its check and you pressed its RESET. B. Tap **Start the
   tilt check**, then lift the FRONT of the unit about 2 cm and hold it until the
   line says **Tilt sensor checked: the front reads up**. If the sensor is fitted
   another way round the page offers **Turned around (180°)**, **Quarter turn left
   (90°)**, **Quarter turn right (90°)** or **Upside down**, with the one it read
   marked: tap it, and the check runs again. The fix is saved for this unit
   (`~/.config/openflight/lis3dh-mount.json`), never in the shared rig file, and
   the tester, the camera tilt and the kiosk all read the sensor with it; the
   check and every fix are recorded in `session_start`. C. Tick the setup box and
   tap **Confirm the setup**. **Details (for the maintainer)** holds the raw rows.
3. **Ball in the box**, 4. **Aim at your target** (or **Skip for now**).
5. **Check the light**: **Measure the light (both modes)**.
6. **Ball range**: the camera opens by itself; tap **Save the ball** once the
   ball in the box is found and steady.
7. **Free play** (below).
8. **Send the package**: the big **Download** button saves the zip to the device
   the page is open on; the unit also keeps a copy in `~/OpenFlight-to-send/`
   (its path and size are shown). Then upload the zip to your Google Drive
   (drive.google.com → New → File upload), share it as "Anyone with the link",
   and send the link to whoever asked you to test. Hit beside a TrackMan or
   another launch monitor? Put its export in the same Google Drive folder as the
   zip and share the folder.

The band at the top stays neutral until free play records: "SETTING UP · Step 4 ·
Aim at your target · free play starts at step 7" during setup, "NOT STARTED" on
step 7 before **Start free play**, "FINISHED" once packaged.

In step 7 tap the session's **resolution**, **1280×800** or
**640×400** (the maintainer tells each tester which one), answer **Which club
will you hit first?** from the kiosk's club grid, then tap **Start free play**.
The kiosk starts on that club; **CHANGE CLUB** on the scoreboard changes it as
usual. The lens's default resolution is marked "(lens default)" but is never
assumed: Start stays off until a resolution and a club are tapped. A resolution the lens settings
disable is not offered, and there is no switch mid-session. The manual exposure
ladder and its per-swing verdict card sit in the closed **Advanced: manual
exposure ladder (maintainer)** section under free play: leave it closed. While
setting up, the band at the top names the same step as the "You are here" bar,
e.g. "SETTING UP · Step 4 · Aim at your target · free play starts at step 7".

Hit normally while the full-screen scoreboard records camera clips, distance-radar
dumps, speed-radar data and exposure decisions. There is no swing limit, per-shot
prompt or annotation step. Face photos aren't required, but they help (below).
Testers collect and send data; the maintainer handles any reference marking and
accuracy grading.

### During free play: Setup, face photos and the aim

- **Setup** in the scoreboard's Live header brings the tester page back: the
  window that opened the scoreboard, or (on the unit's own screen, where the
  scoreboard covers everything) the tester page in the same window. **Kiosk view**
  goes back to the scoreboard.
- **Face photo** (scoreboard) or **Photograph the face** (step 7) pauses free
  play: the light shows amber **WAIT · taking a face photo**, swings are not
  counted ("Not counted: paused"), and the camera holds the ladder's still-photo
  exposure. The tester page shows the camera with the dashed box: hold the face
  about 0.5 m from the lens inside it and tap **Photograph face**, or **Skip**.
  The photo is saved against the latest shot number, and free play resumes.
  **Resume** on the scoreboard ends a pause without a photo.
- **Move the aim point** (step 4) works during free play without a restart: the
  kiosk pauses (**WAIT · moving the aim**) and its camera shows the target.
  Confirming (or skipping) hands the new target line to the running kiosk; the
  next shot is measured from it, and shots before it keep the line they used. If
  the kiosk is still processing a shot, the page says so: set it again in a moment.
  **Resume free play** in step 7 ends an aim pause left open.

### The exposure study runs by itself

Free play first walks the exposure ladder for the tapped resolution, so the
maintainer can lock in which exposure settings work for fusion:

- The settings are that mode's ladder rungs from the lens settings, longest
  exposure first. Before any swing, settings the setup's measured light cannot
  support (the same prediction as the ladder's light warning, 640×400 counted at
  its measured 2× signal) and settings below the sensor's recorded shortest
  exposure are skipped, with the reason recorded.
- Each setting's gain keeps the brightness of the setup's ball lock (the ladder's
  own rung gain). The camera starts at the first setting from its first frame,
  and moves to the next one live: no window or restart between settings.
- Five real shots per setting: only swings the speed radar records as a shot
  count; a trigger with no shot does not. Then the next setting follows.
- Every shot gets the ladder's automatic picture verdict (frames delivered,
  signal, clipping, whether the ball and the club are usable), recorded silently
  against its setting. The tester never acts on it.
- After the last setting, the automatic swing exposure runs for the rest of the
  session, seeded from the setup's light. When no setting can run (too little
  light, or no light measured), free play starts on the automatic exposure at
  once, never at a near-black startup.
- **Stop & package** works at any time; a partly run study is packaged and marked
  partial.

The scoreboard shows one line under "Free play: recording", for example
`Exposure study: 150 µs · swing 3 of 5 · setting 3 of 6`, then
`Automatic exposure`. The tester page shows the same line and each setting's
verdict counts (green, amber, red, without a clip) or why it was skipped.

- **Green SWING:** every configured sensor is armed. Too dark / too bright adds a
  lighting note without blocking shots. "Camera exposure unavailable" means the
  camera reported no exposure limits and holds its startup controls; shots still
  record, with that state in each shot's exposure record.
- **Amber WAIT:** a capture is saving, processing is busy, the camera is finding
  the ball, or it is **changing the camera setting** (the study's next setting).
  Wait for green.
- **Red NOT READY:** read the cause. A completed search with no ball says “no ball
  in the box”; a failed camera or distance radar remains a visible cause while OPS
  numbers continue to be recorded.

Any valid enclosure geometry file can collect free-play data. Unknown geometry is
labelled **geometry unverified**; geometry-dependent results carry that note.
The supplied file's values are used, so this does not validate their physical accuracy.
The advanced ladder retains its approved-enclosure requirement.

Tap **Stop & package** on the scoreboard or tester page. Accepted shots finish
writing before the session closes and analysis/packages start. Keep the scoreboard
open until **Package ready: <file>.zip · send it from the tester page (step 8)**
shows; once the run stops, that message and the **Done** button take the ready
light's band. Send the zip from step 8 on the tester page (see above), with a short
description of the lighting and enclosure. Sessions can be about 1 GB.
Tap **Done** on the scoreboard to close the window. This keeps progress visible
after acquisition stops. The bundle includes every shot's exposure decision,
brightness, light index, lens/mode and blur ceiling, or an explicit missing-capture
reason, plus the unit/run identity and setup/rig provenance.

### What the bundle records for the exposure study

In the run folder (`<tester>/<arm>/paired/run-NN/`):

- `free_play_exposure_plan.json` (`openflight.free_play_exposure_plan.v1`): the
  tester server's plan before any swing: `resolution`, `resolution_choice` (the
  tap, the lens and its default), each rung's `rung_id`, `exposure_us`, `gain`,
  `gain_source`, `status` (`pending` or `skipped`), `skip_cause` (`light`,
  `below_sensor_minimum`, `no_gain`), `skip_reason` and `light_support`, and the
  `startup` controls with their `source`.
- `free_play_exposure_study.json` (`openflight.free_play_exposure_study.v1`): the
  kiosk's study as it ran: `phase` (`ladder`, `automatic`, `camera_unavailable`),
  `ladder_status` (`running`, `complete`, `partial`, `not_run`), each rung's
  `status` (`active`, `done`, `incomplete`, `not_reached`, `skipped`), its swings
  (`shot_number`, `verdict`) and verdict `counts`, and the `automatic` hand-over
  with the policy's seed decision.
- `session_start.config.free_play` carries `resolution` and the whole
  `exposure_plan`; `session_start.config.golfer` the hand used and its source
  (`simulator`, `setup` or `default`); `session_start.config.target_line` the
  target line in force at the start (revision 0).
- Every shot carries `target_line`: the `revision`, `status`, `yaw_deg`,
  `horizontal_offset_deg`, `frame` and `since` of the target line it was measured
  from. Each move is a `target_line_changed` entry with its time, the previous line
  and the new one.
- Face photos are `face-photos/shot-NNNN.pgm` in the run folder (a second photo of
  the same shot gets `-2`), each with a `face_photo` entry (`shot_number`, `path`,
  `exposure_us`, `gain`, `taken_at`); skips are `face_photo_skipped`. Pauses are
  `free_play_paused` / `free_play_resumed` entries; a swing during one is
  `free_play_shot_not_counted`.
- A launch monitor's export is kept in `<tester>/comparator/`.
- Every shot entry carries `free_play.exposure_study`
  (`openflight.free_play_exposure_study_shot.v1`): `ladder_phase`, `resolution`,
  `rung_id`, `setting_index`, `settings_total`, `swing_of_rung`,
  `swings_per_rung`, `exposure_us`, `gain`, `controls_source` and `verdict`
  (`openflight.free_play_swing_verdict.v1`: `status` `judged`, `no_capture` or
  `error`, `color`, `reasons`, `delivered_fps`, `gaps`, the applied `exposure_us`
  and `gain`, `signal_dn`, `clipped_pct`, `light_cause`, `ball`, `ball_usable`,
  `club_usable` and `judged_against`). A shot first logged before its clip was
  matched gets the verdict in its `shot_updated` entry.

| Symptom | Action |
|---|---|
| Start free play stays off | Tap a resolution and the first club, then check the setup steps are complete. |
| WAIT · taking a face photo / moving the aim | Free play is paused: finish the photo or the aim on the tester page, or tap **Resume**. |
| Finding the ball | Keep a ball inside the confirmed box and wait for the search. |
| No ball in the box | Re-tee inside the box; add light if the ball is hard to see. |
| Changing the camera setting | Wait for green: the study is moving to its next setting. |
| Too dark / too bright | Add light or shade the ball. The note does not block a shot. |
| Camera exposure unavailable | The camera reported no exposure limits. Shots still record; restart the unit to retry, and mention it when sending the zip. |
| Packaging fails or shots are still finishing | Tap **Try again** on the scoreboard once processing settles. Captures remain on disk. |
| Kiosk or tester restarted | Do not assume the run resumed. Start the tester again; use step 8 to analyse and package the saved captures. |
| Sensor fails mid-run | Read the ready-light cause; OPS numbers continue. Package the session before restarting the unit. |

Before release, check on the Pi: full-screen opening and the touch Stop/Done
buttons; the light through a complete shot cycle; that the first frames are at
the first setting's exposure, and each setting change lands between swings
without a restart; the verdict's time per shot; and zip size/packaging time for
30 shots. Desktop mocks do not establish these hardware behaviors.

## Advanced exposure-ladder study

For the first run of the complete fusion bench, follow the
[Pi commissioning procedure](pi-commissioning.md) alongside this guide.

The test suite finds the shortest camera exposure that still works at full
resolution (1280×800), and compares it with 640×400, while recording everything
the camera and both radars see. You hit a 7-iron; the page sets each exposure
itself, gives a verdict after every swing, skips exposures your light cannot
support, and takes a photo of the club face after each full-resolution swing.
The swing step also shows the kiosk's numbers for its latest shot inside each
swing's verdict card, every value labelled (measured, calculated, model geometry
or estimated) and every left/right angle signed against its frame ("vs target
line" or "vs unit aim"): they are the kiosk's working numbers, not validated
measurements. The separate [saved-capture review page](recorded-track-comparison.md)
supports manual annotations and conditional geometry comparisons after recording.
Community contributors only need to record and package sessions; frame marking
is the maintainer's follow-up work. Automatic diagnostics do not require marks, and sessions without independent
references are not accuracy graded. See [community contributions](community-contributions.md).
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
After both have looked, the camera's ball in the box and the radar's candidates
in the box check each other, and the setup always saves what they found,
labelled **experimental**: the radar's distance when the two agree within twice
their combined uncertainty; the camera's distance, with a warning, when the radar
found nothing or disagrees; the radar's strongest candidate, with a warning, when
the camera found no ball; and nothing, with "No ball found in the box. The ball
may be outside it: move it into the box.", when neither did. The page, the
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

Work down the numbered steps for a normal collection: **1. Tester and lens**,
**2. Check the unit**, **3. Ball in the box**, **4. Aim at your target**,
**5. Check the light**, **6. Ball range**, **7. Free play** (the exposure ladder is
its maintainer section), then **8. Send the package**. A bar pinned under the
ready light says where you are ("You are here: Step 5 · Check the light"); tap it to scroll to
that step. The same bar holds **Kiosk view** and **Stop**. **Advanced: manual
single-arm tools** is for a maintainer-directed investigation of one mode; it is
not the normal pilot and does not replace the ladder.

**Step 1: tester ID and lens, by touch.** Nothing in setup is typed. Tap your
tester ID in the list, or **New tester** to start one with the suggested ID; the
page remembers it on this browser. Tap **Indoors** or **Outdoors**. Then tap the
lens fitted to the unit (stock 2.8 mm, 6 mm or 8 mm): the page shows its
aperture, field of view and whether its camera model is calibrated, and its
settings: the modes it runs, the default mode, and the exposure-ladder settings.
**Change this lens's settings** edits them by tapping: the modes, the default, and
each exposure from the allowed list. Changing the lens asks first, because the
ball box, the aim point and the camera tilt are set again for the new lens.

**Step 2: check the unit.** A: **Check the unit** reads all four sensors (the
box, light and range wait for it). B: the tilt check, by one lift of the front.
C: confirm the setup. The engineering rows and notes are folded under **Details
(for the maintainer)**.

**Step 3: the ball box.** Set the unit 1.8–2.9 m behind the ball, pointing down
the target line. The live picture opens by itself with a yellow box drawn on it:
the box does not move. Put the ball in the box; a green check appears when the
camera finds it, with its distance (the IWR radar's distance in the camera's
direction to the ball, the ball's size checking it). Then drag the blue line to
where the floor meets the net or screen: the far range window. Press **Confirm the
ball in the box**. Everything after it works from the box, and stays locked until
it is confirmed: the light measurement, which is judged inside it, the ball range
and the ladder. The box itself waits for step 2. The box step is also the camera's first check:
if the picture does not open, the step says the camera did not answer and offers
**Show the camera**.

**Step 4: the aim point.** Right after the box, the picture opens again with a
yellow vertical line, TrackMan's aim line, and an open circle on it that never
covers the target. Touch anywhere on the picture: the circle sits above your
fingertip so you can see what it is on; touch near the circle to drag it from
where it is. For a far flag press **Full screen with magnifier**: the picture
fills the screen, a magnifier shows the circle's surroundings at 2, 3 or 4 times
the camera's own pixels, and the arrow buttons move it one camera pixel at a time.
Press **Confirm the aim point**: the page then shows the angle and its ± (the
tester server's total, which includes the parallax and how finely you could
place the point). Every left and right angle the kiosk reports is then measured
from that line (see [Target line](#target-line)). **Skip for now** is allowed:
swings are then measured from where the unit points and say "vs unit aim"; set
the aim point any time with **Set the aim point now**. The aim step never takes
the camera from the ball range.

## Test suite

On the study page, pick your tester ID and lens (step 1), check the unit
(step 2: **Check the unit**, the tilt check, then confirm), put the ball in the box
(step 3) and set or skip the aim point (step 4), then:

1. (Moved to step 2 on 19 Oct: **Check the unit** reads the OPS243, the camera and
   the IWR6843 each. Tick the RESET row if you pressed RESET on the IWR6843 board
   since the last check.)
2. **Measure the light** (step 5). Runs the camera's light screen at both modes, about
   a minute each. Keep the room as you will hit in; it also proves each camera
   mode streams. The light is judged inside the ball box you confirmed (halved for
   640×400), not over a fixed part of the picture, so a bright sky or a dark
   fence elsewhere does not decide it. Each mode then reads "light sufficient",
   "more light needed" (swings become evidence only), "too bright: shorter
   exposures used" or "mixed light" (a sunlit spot clipped the brighter gains,
   so the gain stays below it). The last two are normal outdoors. Under the
   button the page shows how long ago the light was measured. Outdoors a
   measurement lasts 30 minutes; indoors it lasts the day. After that, if you
   move between indoors and outdoors, or if you redo the ball box, the page asks you
   to measure the light again, and the ladder waits until you do.
3. **Start the exposure ladder** (step 7: open **Advanced: manual exposure ladder
   (maintainer)** first; it opens by itself while a manual ladder runs). The page
   sets each exposure itself and shows which one you are on ("Now: 1280×800 ·
   150 µs"). Hit a normal shot, wait for the verdict, repeat. The verdict is the
   big card at the top of that section:
   green (good picture), amber (usable, with a warning) or red (not usable), with
   the setting, the frame rate delivered, the light on the ball zone and the
   clipped share, and the reasons in plain words (tap one for the full reason). The
   ready band repeats its colour. Above the card, "Ready for a swing: the ball is
   in the box" (or why not) re-checks the ball before each swing, and while the
   ladder runs a line of live numbers shows the ball's size and peak, the clipped
   share, the exposure against its blur limit, the frame rate and the gain. It moves
   on after 5 good swings, and skips exposures your light cannot support. An
   exposure fails after 3 red swings at any point, 2 red among its first 3, or 2
   too-dark reds in a row; when darkness failed it, the shorter exposures of
   that mode are skipped too, since they are darker still.
   **Start** works only when the setup's camera found the ball in the mode it
   starts in: without the ball's position no swing can be judged, so the page
   says to run the setup again instead.
   On the 1280×800 exposures: spray the face before each swing; after it, hold
   the face about 0.5 m from the lens inside the dashed box, press
   **Photograph face**, then wipe it. Before leaving full resolution, the page
   holds the final capture for its photo. Photograph it or choose **Skip photo**
   before continuing; do not take another swing while this choice is pending.
   The kiosk then restarts in the next mode (about 20 s); a line under the bar
   at the top says how far along it is, and the tester page stays where it is. A failed photo can be
   retried without losing the pending capture. The photo sets its own exposure
   from the measured light, down to a few tens of microseconds in sun, so it
   works outdoors too.

   Above the button, ticks choose which of the lens's exposures run this time
   (the lens settings in step 1 say which exist; with every setting allowed that
   is 300 to 5 µs at 1280×800 and 300, 150, 75, 30, 15, 10, 7, 5 and 3 µs at
   640×400). The settings under 10 µs (13 Oct) are for full sun. Each runs only
   if the camera reported it can go that short: a setting under a mode's recorded
   shortest exposure, or under 10 µs before that has been read, is greyed out and
   never run. A **kiosk** badge and the line under the light note show the setting
   the kiosk's own automatic exposure would use in the setup's light, so its choice
   can be judged against that setting's verdicts. All
   are ticked unless you changed them; the page remembers your choice. Unticked exposures are
   skipped and recorded as "not selected by the tester", not as a light
   failure. If no 640×400 box is ticked, the ladder ends after 1280×800 with no
   restart; if no 1280×800 box is ticked, it starts at 640×400 and asks for no
   face photos. The light measurement is needed only for a mode with a ticked
   box. Exposures that have already run are greyed out, and **Start** is greyed
   out while the ladder runs. To change your choice partway, press **Stop**,
   change the ticks, then press **Start** to carry on.

   Under **Switch to a setting now**, one button per exposure switches to it straight away, ticked or
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
4. **Analyse, review & package** (step 8). Stop the ladder first. If you have a TM4,
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
   is shown beside the link. Pressing it again when nothing has changed keeps the
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
press **Start the exposure ladder** to resume. A pending final photo stays associated with its original
capture across Stop and Resume; photograph that same strike mark, or skip it
if the face has already been wiped or used for another swing. Skipped photos
are recorded with the capture in the session bundle.

**The kiosk's own screen.** While the tester runs, the kiosk does not take over
the screen. **Kiosk view** in the bar at the top opens the kiosk's page in a
normal window or tab; nothing the ladder waits on (the verdict, the photo or skip,
switching exposure, Stop) needs it. Its Live tab says what left and right are
measured from: "Target line set +2.1° ±0.8°", or "Target line not set: left/right
is from the unit's aim".

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

**First, the ball box (step 3).** Set the unit down 1.8–2.9 m behind the ball,
pointing down the target line, and confirm the physical setup. The page then opens
the live picture under **Ball in the box**, with a yellow box drawn on it. The box
is fixed: it is where the camera and radar look for the ball, and it never sets a
distance. Put the ball in it on the hitting surface. The camera finds it by itself
and a green check appears on the box, with the distance: the IWR radar's distance
in the camera's direction to the ball, with the ball's size as a check (the page
says whether the camera and radar agree, or which one found it alone). A ball
outside the box, or more than one ball-like thing in it, keeps **Confirm the ball
in the box** greyed out and the page says why. A distance outside 1.8–2.9 m is
labelled, not blocked. Drag the blue line to where the floor meets the net or
screen (a drag moves it from where it is; a tap puts it where you tapped); it is
saved when your finger lifts and gives the far range window. Nothing is checked or
captured before **Confirm the ball in the box**: the hardware check, the light
measurement, the radar captures, the camera's ball search and the ladder's ball
checks all work from the box, and nothing looks anywhere else. The confirmation
is stored in `placement-box.json`.

The picture sets its own brightness while it is open, so the scene shows in sun or
at dusk before the light has been measured; "Adjusting brightness…" shows under it
until it settles, usually within two to four looks. That brightness is for viewing
only: it is logged as `box_preview` and never used as a light measurement, a lock
or a starting point for the camera checks. If the picture does not open, the step
says the camera did not answer (the first camera check of the session); fix the
cable or close whatever holds the camera, then press **Show the camera**. After a
restart of the tester, a new physical setup confirmation or a lens change, the
page asks for the box again. The box step never takes the camera from the ball
range.

**The box is the hitting zone.** Wherever the tester judges the hitting zone it
uses the confirmed box instead of the fixed centre-lower part of the picture
(rows 45–90 %, columns 20–80 %): the light screens, the ladder's light checks
("N% of the box clipped") and the kiosk's capture-time exposure rating when it has
no setup ball. The fixed zone remains only when no box was confirmed, and every
stored zone result says which was used (`zone_source`: `placement_box` or
`fixed`).

**Redoing the box later.** Press **Ball or unit moved: redo the box**, put the ball
in the box and confirm it. A moved ball or unit makes the light measurement stale
(the page asks for it again, and the ladder waits for it) and starts the ball range
over, the same as **Ball or rig moved: start over**. Stop the ladder first: a redo
is refused while a capture holds the camera or radar.

### Target line

TrackMan 4 sets its target line from a camera "Target Image" at every setup; the
tester does the same (decision D34). After the ball box is confirmed, the
1280×800 picture opens under **Aim at your target**, with its brightness set over
the whole picture (the target stands far beyond the box). The yellow vertical line
and its open circle start where you last confirmed them, or straight ahead on the
horizon the first time. Touch anywhere on the picture to put the circle there,
drawn above your fingertip, or touch near it to drag it (mouse, pen or finger),
onto your target. The page says the aim's angle while you move it: "Aim 2.4° right
of where the unit points". A target too small to see on the step's picture is
found with **Full screen with magnifier** (2, 3 or 4 times the camera's own
pixels, and one-pixel arrow buttons). With each confirmation the page sends how
many camera pixels one screen pixel covered where you set the point
(`display_scale`), so the tester server can scale the touch part of the
uncertainty; the only ± the page shows is the server's `total_uncertainty_deg`.

- **Pick a target low down and near the picture's middle.** Turn the unit to face
  the target roughly. The inclinometer's roll is recorded but not applied to the
  camera, so a target high on a screen moves sideways with any roll: 1.4 m up a
  screen 4.5 m from the unit, a 3° roll moves the angle about 0.9°. On the horizon (about
  the lens's 95 mm height) it moves it by almost nothing. Far from the picture's
  middle, the stock lens's barrel distortion adds up to about a degree.
- **The floor-line method is retired from the page.** The aim point is sent as a
  direction, and the tester server's total uncertainty includes the parallax
  between the lens and the ball. The server still accepts a floor line from an
  older page, but this page never sends one.
- **The angle goes to both sensors.** The rig file has no sideways angle between
  the camera and the IWR (they share a boresight by design), so the kiosk gets the
  same value in `--camera-capture-horizontal-offset-deg` and
  `--iwr6843-azimuth-offset-deg`. Those flags take the unit's boresight relative to
  the target line, so they get minus the target line's angle: a ball hit straight
  down the target line reads 0.
- **What is recorded.** `target-line.json`, beside `placement-box.json`, keeps the
  confirmed aim point (`aim_px`, 1280×800 pixels), the `display_scale` it was set
  at, the ball box, the method (`direction` or `floor_point`), the angle (`yaw_deg`, positive
  when the target line is right of where the unit points, seen from behind), the
  value the flags get (`kiosk_offset_deg`), its uncertainty and the history of
  every confirmation. Each swing run records the same in
  `handed_to_swings.target_line` (in `arm.json`, `setup_admission.json` and the
  run's `tee_range.json`), with `source: aim_point`, or `status: not_set` when no
  aim point was confirmed.
- **Uncertainty.** `uncertainty.parts_deg` holds what each source would move the
  angle by: ±2 px of aim (about 0.1°), the camera tilt (4° until the tilt is
  calibrated, 1° after), the unapplied roll (the inclinometer's reading, at least
  1°, or 3° without one), the focal length (8 %), the lens's barrel (not
  corrected), and for a floor line ±2 px of its rows. For a direction, the
  hand-off adds the parallax at the net's measured distance beyond the ball (the
  swing server's 4.6 m when no net was measured). `total_deg` adds them in
  quadrature.
- **Moving it.** Press **Move the aim point**, drag the line and confirm it.
  During free play the kiosk's camera shows the target and the running kiosk
  takes the new line for its next shot (see "During free play" above).
  Confirming within 4 px of the last aim point, with the same ball box,
  changes nothing. A moved aim point, or one set after swings were started without
  it, marks those swings stale: the step says which mode's swings measured from
  another line, and they should be taken again. Redoing the ball box, or a new physical
  setup confirmation, asks for the aim point again, starting where it was.
- **The alignment stick (Part B) checks it.** When the stick tool exists, its own
  angle for the same setup, with its uncertainty, is recorded beside the aim point
  (`checks` in `target-line.json`, through `record_alignment_stick_check`): the
  difference and whether it lies within two combined uncertainties. The stick never
  replaces the aim point's angle.

- **Skipping it.** **Skip for now** records that no aim point was set: swings are
  measured from where the unit points and say "vs unit aim". It is not an error,
  and the step shows no warning for it.

### Ball range, step 6

Since 13 Oct (owner decision "a") step 6 is **camera-only**. Put one ball in the
box on the hitting surface, press **Open the camera**, and **Save the ball** once
the camera has it steady. The camera locks the ball in the box and reads a coarse
distance from its size and the selected lens's focal length; the step then says,
for example, **Ball found · camera estimate 2.11 m (coarse) · the radar measures it
on each swing**. That is enough to finish the setup, and you never stand still for
radar captures.

The camera's distance is only a **prior**: the kiosk gets it with its uncertainty
(never less than 15 % of the distance) as `--iwr6843-tee-prior-m`, and uses it to
size the radar's search. Each swing's IWR capture then measures that swing's own
launch point: the ball's track after impact, worked back to where the club met it
(`openflight.iwr6843.launch_range`). A swing the radar cannot range uses the prior,
and every number built on it is labelled *estimated*; with neither, those numbers
are not measured, with the reason. Each shot records its `ball_range_m`,
`ball_range_source` (`iwr_post_impact`, `camera_prior` or none) and
`ball_range_uncertainty_m`; the swing's verdict card and the kiosk's Flight details
show the distance and where it came from. **The per-swing radar range is not
validated against a tape yet**: grade it with
`scripts/analysis/grade_launch_range.py SESSION --truth tape.json` at the next
taped session.

**Radar check (diagnostic)** is a secondary button before the camera opens. It runs
the old static empty and ball captures (stand in one spot outside the radar's view,
behind the rig, for both, keep still, and keep others away). What it finds is
recorded beside the camera's ball (`radar_check`, with the agreement) and never
becomes a swing's distance: indoors the scene changes between the two captures
more than a ball 2 m out echoes, so the method fails beyond about 1.5 m.

Each setup epoch is immutable; **Start over / ball moved** preserves it and creates
a new one, while refresh resumes the saved step. There is no 640×400 check: the
ladder's 640×400 settings use the 1280×800 ball halved. Advanced tape is
validation only. The camera tilt is no longer calibrated from a setup's static
pair: swings calibrate it (two swings whose radar launch point and resting-ball
row agree, per lens, `openflight.camera.swing_tilt`).

The camera step first sets its brightness on the ball, in a few steps. A still
ball is exposed for itself: the longest exposure the frame allows, then the lowest
gain (4x the exposure at a quarter of the gain is twice the signal-to-noise), never
the swing's 300 µs. It starts from this lens's last lock that passed, else this
lens's same-day light measurement (B), else 300 µs × 4's light at that split; a
screen or a lock from another lens is never used. When it sees a ball whose
brightest part is below 40 % of clipping, it raises it to about 60 %; a ball that
clips is brought down. A found, steady, unclipped ball is **never failed on
brightness**: if the sensor cannot make it brighter, it locks labelled **dim: add
light**, and Save goes on. When it sees no ball in a dim box (below 60 DN above
black), it brightens once before looking again; a box too dark to brighten further
reads **Too dark to see a ball in the box: add light**, never "No ball". In a lit
box it looks again at the same setting and after four looks says "No ball found in
the box". At most eight changes are made; the lock is the setting whose applied
camera metadata match the request. The exposure status is frozen when Save is
pressed, so a pose blip during the Save's search no longer fails it (every pose
change is logged). Save confirms the steady ball at its size and fits only the
close rivals the whole box's quick pass finds (about 0.5 s on a PC, about 2 s
expected on the Pi, which is not yet measured), and runs outside the request lock,
so the page never freezes while it works. This lock is for the stationary ball
only; it never sets swing-capture exposure.

After Save, the step shows which ladder settings your light supports: the measured
light (the ball's DN per µs of exposure and unit of gain) predicts each setting's
resting-ball level at gain 12, against the swings' 20 DN gate. 640×400 collects
twice the signal per pixel of 1280×800 at the same exposure and gain (measured from
the paired light screens). Settings below the gate are recorded but will read too
dark; the ladder step repeats the warning.

The ball search looks only inside the box's dashed outline, and only for balls
of the sizes the box's near and far edges allow (25 % either way). It does not
reject a ball for sitting above or below the floor row it expects: that height is
recorded (`floor_height_residual_m`) but never used to refuse, because the camera's
tilt alone moves it by more than a ball. When several ball-like things are in the
box, they are ranked by how well each fits a lit sphere; if two fit about as
well, the step says "Several ball-like things are in the box: keep only the ball
in it" and looks again at the same setting, never brighter. The camera never uses
the radar while it searches. Once found, each live look re-fits the ball where it
was in a fraction of a second; Save still searches the whole box as the
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

The radar searches only the box's distances: from its near edge to its far
edge as seen from the radar, widened for the camera's tilt uncertainty and 0.1 m
either way. The two captures are compared two ways, and every candidate either way
is reported with its distance, score and (for the coherent one) elevation:

* by magnitude: every place in the window where the ball capture's echo grew by a
  quarter or more over the empty one, strongest three kept;
* coherently, one virtual channel at a time: each channel of the empty capture is
  first scaled by one complex factor fitted on still reflectors outside the box
  (a radar restart turns every channel's phase), then subtracted from the ball
  capture; each clear change is a candidate, flagged when its elevation is not at
  ground level.

Nothing is rejected for a fraction below a fixed gate, and a changed scene (a
person or a club elsewhere) is a warning, not a rejection. A box whose far edge
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
distance; swings place the resting ball there, `--tee-side-offset-m`); its **radar candidates** line lists everything the radar saw in the
box. Something ball-sized at the same distance as the ball, such as a shoe beside
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
labelled `unit_calibration_from_a_validated_pair`, and its box outline tightens.
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
| `IWR6843 did not acknowledge '<config line>'`, at a different line each time, and RESET does not help | The radar board's CP2105 USB bridge stopped answering; `dmesg` shows `cp210x ttyUSB0: failed set request 0x12 status: -110` at each failure. RESET restarts the radar, not the bridge | Unplug the radar's USB cable for 5 s, plug it back in, run **Check the unit** (step 2), then retry. If it keeps happening, try a short thick cable or a powered USB hub |
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
| Ladder verdict red: `frames: ... fps delivered` or `gap(s)` | The Pi could not keep up with the camera mode | Close other programs, check the power supply, run **Check the unit** (step 2) again |
| Ladder verdict red: `controls: exposure ...` or `gain ...` | The camera did not take the exposure's setting | Press **Start the exposure ladder** again; the ladder carries on where it stopped |
| An exposure reads `failed … — 3 red swings`, or a shorter one `skipped … — full-300 failed: 3 red swings` | Three of its swings went red, whatever the order, so the ladder moved on rather than wait for 5 good ones | Read the red swings' reasons; the ladder continues on its own. A red for darkness also skips the shorter exposures of that mode |
| An exposure reads `failed … — 2 dark red swings in a row` | The light fell during the exposure (dusk, a cloud) | The shorter exposures of that mode are skipped; add light and measure it again (**Measure the light**) before another ladder |
| The review reads `No radar shot` for a swing | The OPS243 logged no shot within 30 s of the camera's trigger (all shot records normally arrive about 7 s after it, when the IWR dump ends), so the swing is set aside, not counted and not failed | Swing again; if it happens on every swing, check the OPS243's LED and its cable, and that the ball is in front of the unit |
| Ladder verdict red: `light: too dark ...` (the hitting zone or the ball) | This exposure is below what your light supports | Expected on the shortest exposures; the ladder skips the rest of the mode |
| Ladder verdict red: `light: too bright for the ball ... clipped` | The ball area is washed out | Expected on the longest exposures in sun; this exposure fails and the shorter ones are still tried |
| Ladder verdict red: `ladder: the camera did not apply ... within 1 s` | The kiosk has not taken the new exposure yet | Wait: the ladder retries every second. If it repeats for a minute, press **Stop**, then **Start the exposure ladder** |
| The impact photo is white or washed out | The light changed a lot since **Measure the light** (the sun came out) | Run **Measure the light** again, then retake the photo; the pending capture is kept |
| `Measure the light again (B): this screen is … min old` or `… was measured indoors and you are outdoors now` | The light measurement is too old for the light you are in: 30 minutes outdoors, another day indoors | Press **Measure the light**, wait for both modes, then **Start the exposure ladder**. A rung you were partway through is checked again when the ladder resumes |
| After resuming the ladder, a rung's swing count is back to 0 | The light no longer passed that exposure's check, so the rung started again | Nothing to fix; keep swinging. The earlier swings are kept for review but no longer count |
| A swing is listed as set aside, `taken at ... not this rung's ...` or `taken during a still_photo` | It was taken while the ladder was changing the camera's settings | Nothing is lost; hit the next swing once the rung shows as set |
| `finish automatic tee range before capture (retryable_failure)` | The setup's range record no longer matches the one the ladder was admitted with | Press **Start over / ball moved** and run the setup again |
| `Ball found · camera estimate … m (coarse) · the radar measures it on each swing` | The normal result (13 Oct): the camera found the ball; its size distance is a coarse prior | Nothing to fix: each swing's radar measures the distance. Start the ladder |
| `Ball range: No ball found in the box: put the ball in the box, then start over` | The camera looked through the whole box and found no ball | Swings still record; numbers that need the ball's distance use each swing's own radar range when it measures one, else they are not measured. Put the ball inside the box (or press **Move the box** in step 1) and start over |
| `Ball range: The camera could not look for the ball (too dark or too bright)` | The light kept the camera from looking | Add light (or shade the ball in sun) and start over; the reason is shown, never "no ball found" |
| Detail line `radar check (diagnostic, never the swings' distance) disagree` or `camera only` | The optional radar check did not agree with the camera's ball: indoors the room changes between its two captures more than a ball 2 m out echoes | Nothing to fix for swings: the radar check is a diagnostic. Each swing's radar measures the distance |
| `… — dim: add light` beside **Exposure locked** | The ball is found and steady but under the 20 DN brightness floor even at the brightest setting | Save still works and the label is recorded. Add light on the hitting area for better numbers |
| `Too dark to see a ball in the box: add light` | No ball could be seen and the box is too dark to brighten further: a ball under the detector's 8 DN contrast floor | Add light on the hitting area, then retry; this is never reported as "no ball" |
| `Light: Your light supports … Shorter settings need at least …x more light` | The setup's measured light predicts which ladder settings reach the 20 DN ball gate | Add light before swinging to run the shorter settings; otherwise they are recorded and will read too dark |
| `kiosk would use: 1280×800 · 294 µs × gain 15.94 (nearest setting: …)`, and a **kiosk** badge on a setting | The setting the kiosk's own swing exposure would pick in the setup's light: the shortest exposure that, with gain up to the sensor's cap, brings the ball to about half of clip, never past the 294 µs blur ceiling | Nothing to fix: compare that setting's verdicts with the ones around it. `needs_sensor_minimum` means the camera has not reported this mode's shortest exposure yet; open the live view in that mode once |
| Verdict card `Light: too bright: shade the ball area (or an ND filter)` | The ball clips. In sun this happens even at the sensor's shortest exposure and gain 1 | Shade the ball area (an umbrella or a board), or fit an ND filter over the lens. An ND filter is new hardware. The swing is kept and labelled |
| Verdict card `Light: too dark: add light` | The ball is under the 20 DN gate, even at the blur ceiling with the gain at the sensor's cap | Add light on the hitting area. The swing is kept and labelled |
| Kiosk Camera tab: **Searching for the ball**, then `no ball found in the box (searched the full range)` | Automatic exposure saw no ball for 3 s, tried its whole exposure × gain range, and found none | Put the ball in the box. It searches again after 30 s, or at once if the box moves |
| A setting such as `7 µs` or `3 µs` is greyed out and unticked | It is shorter than this camera mode can go, or this mode's shortest exposure has not been read yet (nothing under 10 µs is offered until it has) | Nothing to fix. Open the live view in that mode once to record its limits; the shortest settings the sensor allows then appear |
| Verdict card `Ball distance: … m · camera estimate (coarse)` | That swing's radar did not measure its launch point; the setup camera's prior stood in | The swing's range-dependent numbers are labelled estimated. If it happens on every swing, check the OPS243 ball speed is reading and the ball flies down the radar's view |
| `the setup changed while Save was searching; press Save again` | Something changed the setup (start over, a lens change) while Save was searching | Press **Save the ball** again |
| `camera arm5 Save is already running` | Save was pressed twice | Wait for the first Save to finish |
| Summary **camera tilt** line in red: `… it disagrees by … deg` | This setup's ball puts the camera's tilt more than 1.5° from the unit's calibration: the unit stands raised or tilted differently, or the camera moved in the enclosure | If the unit is on the hitting surface and the camera was not touched, ignore one; if every setup says it, delete `~/.config/openflight/camera-vertical-offset.json` on the Pi and the next agreeing setup calibrates again |
| Summary **camera tilt** `not calibrated` — `… beyond ±8 deg: it was not stored` | The pair's solve is implausible: the ball, the radar's distance or the lens height is wrong | Check the unit stands on the hitting surface and nothing else is in the box, then start over |
| `Finish this setup` on a setup from before this change | It reached the old 640×400 check, which no longer exists | Press **Finish this setup**: it saves what the 1280×800 camera and the radar found |
| `the LIS3DH reading has no camera pitch` | The inclinometer is off or still settling (for example just after the kiosk handed it back) | Wait a few seconds for a stable reading, then press the step again |
| `The camera hasn't found the ball, so the ladder can't judge your swings` when pressing **Start the exposure ladder** | The setup's camera never picked out the ball in the box (a spare ball or a white cloth in it, or the ball outside it) | Run the setup again: put the ball inside the box on the hitting surface, keep spare balls and white things out of it, then press **Start the exposure ladder** |
| `No ball found in the box. The ball may be outside it: move it into the box` during the camera step | The camera looks only inside the box's dashed outline, and nothing ball-sized for that distance is there, in a box lit well enough to see one | Move the ball into the box. If the box is in the wrong place, press **Move the box** in step 1 and drag it over the ball (the ball range then starts over) |
| `The OpenFlight kiosk service (openflight.service) is running or starting.` when starting the tester | The boot service owns the camera and radars and its starts sync the Python environment | Run `sudo systemctl stop openflight.service`, then start the tester again. On a unit used with the tester, `sudo systemctl disable --now openflight.service` also keeps it from starting at the next boot |
| `Several ball-like things are in the box: keep only the ball in it` | Two things in the box fit a lit ball about equally well (a spare ball, a tee, a white tuft or a logo) | Take everything but the ball out of the box; the step looks again at the same brightness |
| **Confirm the ball in the box** stays greyed out, or `The camera is not showing here` | The live picture is not running, or the camera has not found the ball in the box yet | Press **Show the camera**; if it still fails, run **Check the unit** (step 2). With the picture showing, see the two ball-in-the-box rows below |
| **Aim at your target** says `Confirm the ball in the box (step 3) first: the target line starts at the ball.` | The target line starts at the ball in the box | Confirm the ball in the box (step 3); the aim point's picture then opens by itself |
| The target can't be seen in the aim point's picture | At 95 mm the camera sees about 23° above the horizon: a mark more than about 1.8 m up a net 4 m from the unit is out of the picture, and a target far to one side is outside its width | Use a mark lower down, near where the screen meets the floor, and turn the unit to face the target |
| `The target is … deg right (or left) of where the unit points: turn the unit …` | The cross is far from the picture's middle, where the lens is least known | Turn the unit to face the target, confirm the physical setup again, then set the box and the aim point |
| `Swings in 1280×800 used an aim point you have since moved` or `… were started before the aim point was set` | Those swings measured left and right from another line | Take them again; the new runs carry the new target line |
| `The ball box moved, so confirm the aim point again` | The target line starts at the ball, which moved | Check the yellow line is still on the target and press **Confirm the aim point** |
| Ladder verdict red: `ball: the setup has no ball position for this camera mode` | The setup's camera never found the ball, so a swing's pictures cannot be checked against it | Press **Stop**, run the setup again until the camera finds the ball, then press **Start the exposure ladder** |
| **Kiosk view** is greyed out | The kiosk is not running, or is between camera modes | Start the ladder; during a mode switch wait for the progress line under the top bar to finish (about 20 s). Nothing the ladder waits on needs the kiosk's window |
| A fullscreen kiosk window covers the tester page | The kiosk was started by an older start script | Close that window; use **Kiosk view**, which opens the kiosk in a normal window or tab |
| The target is too small to see on the aim picture | The step's picture is smaller than the camera's own pixels | Press **Full screen with magnifier**, choose 3× or 4×, drag the circle near the target, then use the arrow buttons to put it on the target one camera pixel at a time |
| `No ball in the box yet: put one in the yellow box` | The camera sees no ball in the box | Put the ball inside the yellow box on the hitting surface. If the box is not where you hit from, move the unit (1.8–2.9 m behind the ball, pointing down the target line); the box itself never moves |
| `The ball is outside the box: move it into the yellow box` | The camera found a ball, but not inside the box | Move the ball into the box, or move the unit so the box sits over your hitting spot; **Confirm the ball in the box** opens when the green check appears |
| `Lens set to … Set again: the ball box, the aim point, the camera tilt.` | The box, the aim point and the camera tilt belong to the lens, so a lens change starts them again | Expected: put the ball in the box (step 3), set or skip the aim point (step 4), and run the ball range again (step 6) |
| Ladder verdict amber: `resting ball not found` | The camera could not distinguish a plausible resting ball in that frame | The swing still counts; keep placing the ball in the same spot |
| `run the gain step for both modes first` (or for one mode) | **Measure the light** did not finish for a mode that has a ticked exposure | Run **Measure the light** again |
| `Choose at least one setting.` or `choose at least one setting` | Every exposure above **Start the exposure ladder** is unticked | Tick at least one exposure, then press **Start the exposure ladder** again |

