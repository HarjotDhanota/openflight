# Tester setup: the fixed box, the lens and the aim point

This is the backend of the tester's setup since 13 Oct 2026 (owner decisions 1a,
2a, 3a and the lens choice). The page itself is described in the
[tester pilot guide](tester-pilot.md).

## The fixed box (1a)

The unit stands **1.8–2.9 m behind the ball**, on the target line. A fixed,
TrackMan-style box is drawn over the live 1280×800 view and the tester puts the
ball into it; nothing is dragged.

- **Where the box is.** On the ground, 1.8–2.9 m ahead of the lens along its
  boresight and 0.3 m either side. The window lives in
  `config/placement_box.json` (`placement_window_m`), never on the page. On
  screen the box is one rectangle (`box_outline_px`) that holds every ball
  resting in that ground rectangle at any camera pitch within the tilt margin:
  ±3° until the unit's camera tilt is calibrated, 1° after it, plus the LIS3DH
  roll the camera does not apply (the unit's zero repeatability once it applies a
  verified, zeroed roll). It is
  drawn through the **selected lens**.
- **A ball outside the window is labelled, never blocked** (D15):
  `distance_note` says "inside the 1.8–2.9 m window", "closer than 1.8 m: numbers
  labelled, not blocked" or "farther than 2.9 m: …".
- **The box never yields a distance.** The camera finds the ball in it and gives
  its direction; the IWR gives the distance; the ball's size is only a sanity
  check. Once the tilt is calibrated the camera's ray (row) distance becomes its
  main distance, with the calibration's own uncertainty.

### The ball in the box

While the box is previewed, the live analyzer (`BoxBallLocator`) sets the picture's
brightness for viewing only and looks for the ball inside the box about once a
second; when the box holds none it looks in a wider region so it can say the ball
is outside. Its state, with a plain message:

| State | Message |
|---|---|
| `waiting` | Looking for the ball in the box. |
| `found` | Ball found in the box. |
| `no_ball` | No ball in the box. Put the ball in the box. |
| `ambiguous` | More than one ball-like thing is in the box. Keep only the ball in the box. |
| `outside_box` | A ball is outside the box. Move it into the box. |

A miss counts as `no_ball` only once the picture's brightness has settled (or
after 6 s). The size the search accepts is the ball's physical range: from the
box's padded near edge to 0.6 m past the window (`size_far_margin_m`), not to
the horizon a padded row reaches.

A different detector winner must be seen consistently before replacing the marker.
If the previous object still passes the current checks, the view stays ambiguous.
An ambiguous result is not retried just to obtain a single winner, and it disables
confirmation immediately. A genuinely moved ball can be reacquired; no coordinates
are smoothed between the old and new objects.

An ordinary missed look may retain the previous position briefly, shown as a dashed
amber ring with “Last seen here”. That retained position cannot be confirmed. The
green check appears only when the current observation is confirmable. Live analysis
includes the frame sequence, candidate evidence and `observation_state`, separately
from the displayed position, to help diagnose identity changes.

### The distance: measured on each swing (13 Oct, decision "a")

The setup no longer measures the resting ball's distance with the radar. The camera
locks the ball in the box and gives a **coarse** distance from its size and the
lens's focal length (`camera_prior`, labelled experimental, shown as "camera
estimate (coarse)", uncertainty never under 15 %). The kiosk gets it as a prior,
never a fixed range, in the setup record's `ball_range` section (`setup.json`,
with its uncertainty and source), together with the side offset from the camera's
ray at that distance. The prior sizes each swing's
IWR search, the IWR club gate and the club's contact check; each swing's own IWR
capture then measures its launch point (`ball_range_source` `iwr_post_impact`),
else the prior stands in, labelled estimated. Not yet validated against a tape.

#### The radar check (diagnostic)

The static captures below run only from the **Radar check (diagnostic)** button.
They never become the swings' distance: indoors the scene changes between the two
captures by more than a ball 2 m out echoes, so the method fails beyond about 1.5 m
(and outdoors beyond about 2.4 m). Their pairing with the camera's ball is kept as
`radar_check`.

The two static radar captures (empty, then ball present) take the **far
profile** until the ball's range is known. Their candidates are reported over
the box's padded range window; the pairing then keeps a radar ball only when:

1. **the two radar methods agree**: a magnitude change and a ground-level
   coherent change within 2σ of their combined uncertainty, never closer than
   2.5 range bins (`radar_pairs`). The range is the coherent peak's, with half
   the methods' disagreement added to its uncertainty. A capture without
   per-channel means (the coherent method could not run) lets the magnitude
   change stand with the camera alone, labelled;
2. **it is not a still reflector**: a bin holding 3× the strongest bin outside
   its own ±1 neighbours in the empty scene and changing at most 10 % with the
   ball (a door, a frame) is flagged on every candidate there and never paired.
   The coherent channel fit also uses such a dominant still bin even inside the
   excluded span, so its drift no longer dominates the residual;
3. **it lies in the camera's ray window**: the slant ranges the camera's ray to
   the ball spans across the box's depth, at the tilt margin, short of the net;
4. **it agrees with the camera's distance**: the ray distance once the tilt is
   calibrated, else the size (sigma about 21 %) as a sanity check.

Outcomes (`patch_ball.status`, and `ball.range_status` on the page):

| Status | Saved | `range_status` |
|---|---|---|
| `validated` | the radar's range, the camera's side offset | `validated` |
| `camera_only` | nothing: the radar's methods never agreed, or found nothing | `camera_only` |
| `disagree` | nothing: the radar's ball and the camera's disagree | `camera_only` |
| `no_ball` | nothing: the camera looked and found no ball. Never a radar peak. "No ball: put the ball in the box." | null |
| `ambiguous` | nothing: keep only the ball in the box | null |
| `camera_unavailable` | nothing, unless an agreed radar pair lies inside the box's range extent | null |
| `radar_only_unconfirmed` | that pair's range, labelled unconfirmed | `radar_only_unconfirmed` |

On indoor-test-2 (ball taped ~1.2 m, door at 1.575 m) the chain selects
the ball at 1.200 m (coherent 1.200 m and magnitude 1.293 m agree) with a side
offset of −0.14 m, and never the door (`tests/test_indoor_test_2_regression.py`).
On indoor-test-1 the radar's two methods never agree on the ball (its
magnitude change came from 17° above the array), so that setup now saves no
distance where it once took the magnitude's 1.18 m alone.

### The net line

The tester drags one horizontal line to where the floor meets the net or screen
(`set_net_line {row_px}`). At 95 mm a floor row is a poor ruler (a screen 4.5 m
out meets the floor about 20 rows below the horizon), so the row only bounds
where the empty capture looks for the net. Status:

- `not_set`: no line;
- `poorly_conditioned`: the row's window at the tilt margin is wider than 30 % of
  its distance (the usual case before calibration); no range from it;
- `set`: a well-conditioned row (calibrated tilt, a near net), its floor distance;
- `measured_by_radar`: the far profile's empty capture measured the net inside
  the row's window; it replaces the swing server's 4.6 m default.

The far profile covers about 3.6 m apparent, so a net farther than that keeps the
default, labelled.

### Per-shot re-location (the kiosk)

The ladder and swing kiosks get the box from the setup record, which the tester
writes afresh, in the pixels of the mode it starts each kiosk at; the kiosk converts
nothing.
About once a second, between clips, the kiosk finds the resting ball in it on its
latest frame. A shot takes the last look before its trigger (none older than
5 s): the setup's range while the ball sits where the setup measured it
(within half a diameter), else that range scaled by the setup ball's size over
this ball's. Since 13 Oct the setup's range is the camera prior, so a look is
that shot's own prior, with the prior's relative uncertainty
(`setup_camera_prior`, `camera_size_ratio_to_the_setup_camera_prior`). A ball
outside the box, or none, labels the shot and carries no range. The look is in
the IWR record's `per_shot_inputs`; `GET /api/ball-at-address` serves the
latest, and the tester's status relays it as `ball_at_address`.

A changed winner needs two consistent observations from different captured
frames before it can replace the address ball. While that check is pending
(`acquiring`), it supplies no center or range. When the winner changes, the
worker also fits the previous location in the current image: two supported
objects give `ambiguous`, without a range. This check does not enumerate every
object on initial acquisition or prove that a selected object is a golf ball.
Repeated reads of one frame add no support and do not refresh its age. Worker
failure is `unavailable`; a stale live look is `unknown`, including for the ready
light and swing exposure.

Before asynchronous shot enrichment starts, the server freezes the pre-trigger
look and its reference seed in `Shot.input_sources.ball_at_address`. Unresolved
identity explicitly rejects the camera prior, including the runtime's default
prior; it cannot restore the old setup ball. The shot's cached fitted ball
outline also checks the saved center and diameter through
`Shot.input_sources.ball_reference`. Independent accepted radar range and an
explicit bench range remain separate range sources. Capture arrival and sensor
timestamp metadata are retained; they do not establish exposure-to-impact
timing calibration.

The setup's side offset reaches the radar launch fit (`lateral_tee_offset_m`).

### What resets the setup

The operator's confirmation is kept on disk (`setup-confirmation.json` per
tester) with a `confirmation_id` that a server restart or a re-confirmation
without a real change keeps. A real change asks again: another approved
configuration, the unit turned by more than 1° in pitch or roll and staying turned
(three still LIS3DH readings over at least 3 s, against the median of the still
readings over the 2.5 s the confirmation took), another lens,
or another box (`placement-box.json`'s binding carries the confirmation id, the
lens and the box's identity, never a time). An older dragged-patch
`placement-box.json` is read and asks to be confirmed again.

## The camera tilt

Since 13 Oct the pairs come from **swings** (`openflight.camera.swing_tilt`): a
pair is one swing's measured IWR launch point and the resting ball's row in that
swing's own pre-impact frames, with its stable LIS3DH pitch; a swing ranged only
by the camera prior is never a pair. The tester hands the kiosk the lens's tilt
file (`--camera-tilt-calibration-path`) and the kiosk records each pair in it
(`median_of_swing_pairs`); a setup's static pair no longer calibrates it.

Each pair solves the camera's vertical offset (P8-5). The unit's tilt
is calibrated from the **median** of at least two pairs that agree (within 0.75°
of their median); one pair never calibrates it. Later pairs check it; two that
disagree on the same side in a row re-calibrate it, and say so. It is stored per
lens (`camera-vertical-offset.json` for the stock lens,
`camera-vertical-offset-<lens_id>.json` for another) and can be reset
(`POST /api/tester/lens {action: reset_tilt}`).

## The lens

`config/lenses.json` lists the stock 2.8 mm (f/2.2, 118° H, TV distortion about
−17 %), 6 mm (f/2.0) and 8 mm (aperture unknown) lenses. Each lens's nominal focal
length per mode is the rig file's scaled by `focal_mm / 2.8`; a checkerboard
calibration kept for its `lens_id` (`~/.config/openflight/optical-calibration/`)
replaces it and the lens reports `intrinsics: calibrated`. The selected lens drives
the box, the size-to-distance, the tilt solve, the aim yaw and the kiosk's camera
metrics (`--camera-lens-id`); it is recorded in session_start, the attempt
records and every clip's metadata, and a nominal lens says so in its label.
Each lens keeps its own enabled modes, default mode and ladder exposures
(`~/.config/openflight/camera-lens.json`). Changing the lens resets the box, the
aim point, the tilt in use and the range.

## The aim point (D34)

- The aim pixel's uncertainty is the touch's: 12.5 screen px times the page's
  `display_scale` (frame px per screen px), at least 2 px; without a scale a
  finger on the Pi's preview, 30 frame px.
- `total_uncertainty_deg`, the one ± the page shows, adds the direction's
  parallax at the target's distance beyond the ball.
- A perturbation that makes the floor-line method fall back to a direction
  leaves that part unbounded: no ±, and a warning. The floor line is never
  offered by default; it is still accepted.
- `{action: "skip"}`: swings measure from the unit's boresight, labelled.

The kiosk serves the line at `GET /api/target-line`. During free play a confirmed or
skipped aim goes to the running kiosk (`POST /api/free-play/target-line`), which
moves every horizontal angle to it between two shots; `GET /api/target-line` then
serves the new line.

## The kiosk window (2a, 3a)

The tester starts the ladder's and the swings' kiosks with
`start-kiosk.sh --no-browser` (`OPENFLIGHT_NO_BROWSER=1` does the same), so the
fullscreen kiosk window never covers the tester page; the page's "Kiosk view"
button opens the kiosk UI at `kiosk.url`. Free play opens the kiosk's fullscreen
window on the unit, unless the page itself is on the unit's screen
(`start-tester.sh --on-screen`): it then asks for `kiosk_window: "shared"`, the
kiosk starts with `--no-browser`, and the page's window switches to it. Every
kiosk the tester starts gets `--tester-control-port`, for its **Tester** button
(`GET /api/tester-page`). The 1280×800 ↔ 640×400 switch keeps its restart; `kiosk.state` is
`switching_mode` with a progress message meanwhile.

## Endpoints

| Endpoint | What it returns |
|---|---|
| `GET /api/tester/placement-box` (`?arm_id=`) | the box (`mode`, `box_outline_px`, `frame_size_px`, `placement_window_m`), `ball`, `net_line`, `distance_note`, the lens |
| `POST /api/tester/placement-box` | `show`, `confirm` (no coordinates), `set_net_line {row_px}` |
| `GET\|POST /api/tester/lens` | the lenses, the selection, each lens's settings, `allowed_ladder_us`; `select`, `settings`, `reset_tilt` |
| `GET /api/tester/status` | adds `lens_id`, `kiosk`, `ball_at_address`, `live_metrics` |
| `GET\|POST /api/tester/aim-point` | `display_scale`, `skip`, `total_uncertainty_deg`; during free play `preview_source: kiosk` and `free_play {applied, error}` |
| `GET\|POST /api/tester/handedness` | the tester's `handed` (`RH` or `LH`) and its `source` |
| `POST /api/tester/free-play/pause\|resume\|photo`, `GET .../view.jpg` | free play's pause (`cause` `photo` or `aim`), the face photo (`capture` or `skip`) and the kiosk camera while paused |
| `POST /api/tester/comparator` | a launch monitor export: `in_package`, or `beside` an existing package |
| kiosk `GET /api/target-line` | `status`, `yaw_deg`, `total_uncertainty_deg`, `source` |
| kiosk `GET /api/ball-at-address` | the latest resting-ball look |

`live_metrics` comes from the ladder's own light check on the current setting:
the resting ball's size, its peak and median DN, the clipped share, the applied
exposure and gain, the mode's frame rate, and the blur ceiling for this mode and
lens (1.5 px of smear at the toe's 13.6 m/s image speed at the ball's range,
never past 300 µs).
