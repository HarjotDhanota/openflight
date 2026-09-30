# Setup geometry and next steps: spec

Date: 29 September 2026. Branch: `feat/tester-capture-pilot` (fork). Status: A1 and A2a implemented (4cac9411, 59bcb616); setup radar timings recorded (7fb8bbcb); A2b–A4 open.

This spec fixes the two known code problems from the camera-fusion research log (`docs/research/camera-fusion/README.md`, section 4) and orders the work that follows. Only measured rig geometry is a fixed input. Everything else is measured at setup or per shot, or it is a design constant listed here.

## 1. Problems being fixed

1. **Teed balls are excluded from the ball search.** The resting-ball search drops anything whose ray points more than 1° above the camera's level line (`reference_ball_range._seed_filter` and `_candidate`). The lens is 80–95 mm above the floor, and a ball on a tee on a mat can sit at or above that.
2. **Heights measured from a tee top break the radar floor-bounce model.** The setup now measures heights from whatever the ball rests on (f02c3ec). Most swing geometry only uses height differences. The two-ray multipath model does not: `trajectory._two_ray_solve` and the LCMF path mirror the radar in the floor, so they need the radar's height above the reflecting floor. On a teed ball, "height 0" becomes the tee top. When the radar would come out negative, the server also lifts every height, which moves the floor again.

## 2. Work now (code only; testable without hardware)

### A1. Hitting area replaces the level-line rule (implemented, 4cac9411)

**What:** the resting ball must lie inside a hitting area. It is defined in the world, not in pixels, and projected into the image using the rig geometry and the LIS3DH pitch. It replaces the "not above the level line" rule in both the seed filter and the candidate check.

**Design constants** (from commercial practice; see the hitting-area research):

| Constant | Value | Why |
|---|---|---|
| Range from the radar | 1.0–2.5 m | TrackMan 4 is 1.8–2.9 m, Garmin R10 1.8–2.4 m, MLM2PRO 2.0–2.4 m. This rig's camera and radar are tuned nearer, at 1.1–1.6 m. |
| Sideways from the radar axis | ±0.30 m hard limit; ±0.15 m is "centred" | Commercial zones are ±0.15–0.3 m. |
| Ball centre above the hitting surface | r − 10 mm to r + 90 mm | Covers a ball sitting 10 mm into grass up to a 90 mm tee. |
| Lens above the hitting surface | 0–1.0 m | Covers the unit on grass, on a mat, or on a box or table. |

**Search region before any range is known:** the loosest projection.
- Columns: within f · 0.30/1.0 of the image centre, i.e. ±280 px at 1280×800 and ±140 px at 640×400.
- Rows: from 0.1 m above the lens at 1.0 m (5.7° above the level line) down to the bottom of the frame.

This already removes the door knobs and clothes in the 28 Sept field frame (well above that band) and the baseboard spot (about 500 px off-centre).

**Tightening once the radar range R is known:**
- Columns: within f · 0.30/R. At R = 1.5 m that is ±187 px.
- Expected diameter: f · 42.67 mm / R, ±25 %.
- Rows: if the surface height is also known (from this session's setup, or from the last verified setup in the same place), the ball's row band follows from r − 10 … r + 90 mm at range R. This is about 100 × 430 px, roughly 4 % of the frame.

**Candidate check:** a fitted candidate is rejected if its size-implied range (or the radar range, when known) puts it outside the hitting area. The rejection reason names the violated limit (for example "0.42 m right of the radar axis"). The existing score ceiling (2.5) stays.

**Live preview overlay (tester page):**
- Two sideways rails at ±0.30 m and a centre line, drawn on the floor plane at the setup distance.
- A status line: "no ball in the hitting area" / "ball 0.2 m right of centre" / "ball found".
- **No** filled depth box: at this camera height a depth box on the floor is a thin sliver and misleads.

**Patent note for maintainers:** US7641565B2 (ball-placement detection with guidance cues) is live until 25 July 2027. The overlay is a passive placement guide; review before shipping upstream.

**Tests:**
- A synthetic teed ball on a mat, its centre at lens height and 2 m out, is found. It fails on the current code.
- A spot 0.5 m off the radar axis is refused, with the reason named.
- The 28 Sept field frame still selects the ball.
- A synthetic unit on a 0.5 m box still finds a ball on the floor.

### A2. Surface reference and per-shot tee height

**A2a: setup semantics (implemented, 59bcb616)**

- **Setup instruction:** "Place one ball directly on the hitting surface (on the mat or grass, not on a tee) where you will hit from". This replaces "exactly as you'll hit it".
- **Heights are above the hitting surface.** The radar's height above the surface is the lens height minus 38.15 mm: the IWR's phase centre, 7.85 mm above the RX row that sits 46 mm below the lens (F11; the RX row was re-measured at 49 mm above the surface on 30 Sept, from 44 mm below the lens before).
- **The rig file's lens height is the default.** It is exact whenever the unit and the ball stand on the same surface, which is also what TrackMan, Garmin and Rapsodo assume.
- **The one-ball radar solve is a gross-error check, not a measurement.**
  - It is only good to about ±25–65 mm, because the ball sits just 2–3° below level, so each degree of tilt error costs about 22 mm at 1.25 m.
  - It replaces the rig height only when the two disagree by more than max(60 mm, 2σ), for example a unit on a box. Only then is `--solved-camera-height-m` handed to swings.
  - The evidence records `nominal_m`, `radar_solved_m`, `check` (`consistent` / `unit_raised` / `unit_lowered` / `not_checked`), `source` and `reference: hitting_surface`.
- **The uniform-lift workaround is removed.** A lens height that would put the radar less than 10 mm above the surface is refused, both at setup (`radar_rejected`) and by the server (`ValueError`).
- **Heights the swing server receives:**
  - `--solved-camera-height-m`: lens above the surface; only sent on a gross mismatch.
  - Radar above the surface: derived, used by the floor-bounce model.
  - `--iwr6843-ball-height-m`: ball centre above the surface. It stays one radius until A2b.

**Why not more precision (the two-spot fit was dropped):**
- The height only feeds the radar's vertical launch angle (floor-bounce model). Camera club data, impact location, ball speed and horizontal launch don't use it.
- In a noise-free simulation with the radar truly 51 mm up:
  - A 10–25 mm height error moves ball heights by +5 to +28 mm.
  - Launch angle moves by 0 to 1.4°, erratically, at 8° launch, and under 0.2° at 20°.
  - With the correct height the model already reads 8.8° for a true 8° in one case. That is the marginal floor-bounce geometry at 51 mm.
- A mat under the ball but not the unit (20–30 mm) is therefore within the model's present noise.
- A second ball placement would cost another radar capture (about 11–13 s) every setup.
- If the floor-bounce model is ever tightened, the better route is a once-per-unit calibration: a ChArUco board (B8) plus one tilt-sensor offset measurement. It is not a per-session step.

**Tests (all pass):**
- The server derives the radar height from the lens height with no lift.
- A teed ball height never changes the radar height.
- A lens height that would bury the radar is refused.
- A level unit keeps the rig height.
- A unit on a box uses the radar solve.
- Only a radar-solved height is handed to swings.

**A2b: per-shot tee height (next, after A2a)**

- **Source:** each swing clip's pre-trigger frames (18 at 115 fps in current captures). The resting ball is found in the last still frames using the hitting area, tightened with the setup range, and the follow fit (held size from the setup).
- **Maths:** the ball's height above the surface is h_ball = h_lens − t · down(û). Here û is the ray to the teed ball, and t is its distance along that ray, from the setup range corrected by the ball's new image position. The tee height is h_ball − r.
- **Size of the signal:** a 40 mm tee at 1.5 m moves the ball about 25 px at 1280×800 (12 px at 640×400). The expected accuracy is about ±3 mm (inferred). A 5 cm error in distance costs about 2–3 mm at these shallow angles.
- **Where it goes:** the per-shot ball height replaces the default in the tee anchor and the launch geometry for that shot. It is recorded on the shot, with its uncertainty.
- **Fallback:** if the ball isn't found in the pre-trigger frames, the shot uses the surface default and is flagged.

**Tests:**
- Synthetic clips with tees of 0, 20, 40 and 80 mm recover the tee height within ±3 mm.
- A clip with no visible resting ball falls back and is flagged.

### A3. Roll sign from the mount

- **Derive** the correct LIS3DH roll sign from the mounting (flat, yaw 180°) and the two camera paths (nominal rays and calibrated projection). Make both paths use one convention.
- **Test** with a synthetic rolled camera: render a level scene through a camera rolled +3°, apply the LIS3DH-derived correction, and check that level world lines come out level.
- **Re-enable** roll on the nominal path only after that test passes, and only once a phone-level reading confirms the direction on the real unit (step B2).
- **Status (wiring fix C8, 29 Sept):** both paths now take the roll from `camera/camera_roll.py`. With the board face up (the unit reads z_g = +1.06) and +Y forward after the 180° yaw, a positive `atan2(x_g, hypot(y_g, z_g))` is the target-right side up *if the LIS3DH axes are right-handed*; the calibrated projection then takes the roll as read and the nominal rays take −roll. `tests/test_camera_roll.py` renders a level line through a camera rolled ±3° and pins both. The roll stays recorded, not applied, in both paths (`LIS3DH_ROLL_APPLIED = False`): handedness is the datasheet's, not measured here, and the 28 Sept −2.9° reading against a frame under 1° says the board has a mount roll the rig file lacks. B2 settles the sign; a level line in the frame at the same placement gives `lis3dh_mount_roll_deg`.

### A4. Research, offline

**Radar spin axis (research done 28 Sept; offline look still open).** The idea is to sort the echo by Doppler, then measure each Doppler slice's angle across the receivers.
- **Physics:** sound, and inferred rather than measured. At 60 GHz the dimples form a Bragg ring at about 40° on the ball, whose top and bottom arcs give two Doppler lobes at ±0.64·ωr. The 24 GHz OPS243 never sees this.
- **Prior art:**
  - TrackMan US10850179B2 claims essentially this method: at least three non-collinear receivers, and a spin-axis line perpendicular to the Doppler components' angular positions. **A freedom-to-operate check is needed before shipping.**
  - FlightScope US10151831B2 uses time delays between receiver pairs, which is the same physics.
  - TrackMan US8845442B2 takes rate from sidebands and axis from the trajectory.
- **As mounted, the board is poor for axis tilt.** The 8-element array is vertical, and the only horizontal baseline is TX2's λ/2 offset: about 2.2× below TrackMan's stated minimum, with milliseconds of dwell against TrackMan's seconds.
- **Expected results:**

  | | Spin rate | Spin axis |
  |---|---|---|
  | Unmarked ball | coarse (±5–20 %), useful to settle the OPS 1×/2× ambiguity | not feasible as mounted |
  | Foil dot | about 1 % | ~6–20° as mounted; ~1.5–5° with the board rotated |

- **Other limits:**
  - The current 3-TX loop aliases the lobes above about 6,200 rpm. A 2-TX research profile clears about 9,300 rpm.
  - Tilt error grows as R³.
- **The board stays as mounted.** Rotating it would weaken the vertical array that launch angle and the floor-bounce model depend on. The camera with a marked ball remains the axis route.
- **Next:**
  - Offline look at the July TrackMan-scored dumps. Stop rule: driver lobes below 6 dB, or r < 0.5 against truth spin, ends unmarked IWR spin.
  - Then bench B6.

**Other items:**
- **Range–Doppler coupling** (≈0.62 ms impact-time bias): prepare the correction behind a flag, but do not enable it until B4 data checks the sign.
- **Check `iwr6843/shot.py` `NOTCH_SPACING_MS = 26.93`.** That is the 2-TX value (λ/(2·90 µs)). The 3-TX capture profiles have a 135 µs loop, which gives ≈17.9 m/s. It was tuned against TrackMan data, so verify on the July dumps before changing anything.

### A5. Setup radar capture time (timings added, 7fb8bbcb)

- **Cost:**
  - Each static capture is roughly 11–13 s, and the setup takes two (empty, then ball).
  - 7.0 s of each is the 732,812-byte ring crossing the UART at 1,041,667 baud. The rest is estimated: Python start-up and port search ~2–3 s, configure ~1 s, the fixed 1 s settle, and cleanup.
- **Timings now on file:** every capture record carries `stage_seconds` and `total_seconds`, so the Pi's real split is recorded.
- **Opt-in 14-frame profile:** `config/iwr6843_static_range_14f3ms_53bin_iq16.cfg` keeps the same windows and moves 427 KB (about 4.1 s). The static gate needs at least 12 frames. It stays opt-in (`--iwr-static-config`) until B9 passes.
- **Further options, not started:**
  - Keep one radar connection open across the empty and ball captures (~2–3 s each).
  - IQ8 storage: halves the transfer, fidelity unvalidated.
  - A firmware option to dump only a few frames.

## 3. Hardware steps (need the Pi or the rig)

| Step | Who | What it decides |
|---|---|---|
| B1 | Harjot | Run `scripts/analysis/bench_ball_search.py` on the Pi: real timings. |
| B2 | Harjot | Phone level on the enclosure, noting which side is low, against the LIS3DH roll: confirms A3's sign. |
| B3 | Harjot | Foil-wrapped vs bare ball at taped radar ranges stepped 5–10 mm apart: where the radar sees a real ball. |
| B4 | Harjot | Spray-marked session with an alignment stick in view: first impact and direction truth, and a range–Doppler sign check. |
| B5 | Harjot | LED on the trigger line filmed by the camera: camera-timestamp latency. |
| B6 | Harjot | Drill-spun ball in front of the IWR, plain and with foil dots, at known axis tilts: radar spin rate and axis feasibility. |
| B7 | Harjot | Powered USB hub or short cable if the CP2105 stalls return: power or data. |
| B8 | Harjot | Print a ChArUco board: camera intrinsics (focal length, principal point, distortion). |
| B9 | Harjot | Five setups with the 24-frame and five with the 14-frame static profile, same ball and spot: does the 14-frame profile give the same accepted range (within 10 mm) and pass the frame-stability gate as often? Also reports the real per-stage timings. |

## 4. After the data

- **Radar scattering offset** (from B3): apply the measured offset, or the two-return fit, to the static range.
- **Impact location v2** (needs B4, B5 and A2b):
  - contact time from the ball clock;
  - arc and impulse propagation instead of straight-line interpolation;
  - head depth from tangency;
  - P = B − r·n̂;
  - spray calibration.
- **Radar-to-camera direction offset** on this box (from B4).
- **Exposure gate thresholds** from a lighting study, and camera intrinsics from B8.
- **Face-angle D-plane weight per club** (from B4 truth).
- **Push and PR:** push the fork branch only after A1–A3 pass on the Pi. Upstream gets only what's necessary; this research log stays on the fork, linked from the PR.

## 5. Decisions

**Made (29 Sept):**
1. **Hitting-area constants in A1:** 1.0–2.5 m, ±0.30 m, tees to 90 mm. Implemented.
2. **Setup ball:** it goes on the surface, never on a tee (A2a). Implemented.
3. **Lens height:** the rig file's height by default; the radar solve is a gross-error check only. The two-spot fit is dropped (see A2a).
4. **IWR board:** stays as mounted. Axis tilt is not worth losing the vertical array.
5. **Radar setup capture:** timings recorded; the 14-frame profile is opt-in until B9.

**Still open:**
- Whether the live-preview overlay ships on the fork now, with the patent note carried to any upstream PR.

## 6. What other makers do (research, 28 Sept)

- **Gravity:** every maker references gravity with an onboard tilt sensor. TrackMan 4 self-levels with motorised legs; Mevo, Rapsodo, Garmin and SkyTrak gate or compensate tilt.
- **Vertical datum:** almost all define the unit's base as level with the hitting surface and tell the user to make it so. TrackMan's tolerance is ±5 cm (secondary source).
  - FlightScope is the exception. Mevo stays on the floor with a typed hitting-surface offset of at most 76 mm, and must never be raised on a block, which suggests its radar model uses the floor in front of the unit.
  - Voice Caddie tells users to raise the unit for tees over 1.5 in.
- **Tee height:** no maker measures it per shot.
- **Setup object:** high-end installs put a calibration object on the hitting surface (Uneekor board and bubble level, TrackMan iO board). For a portable behind-ball unit, the resting ball is that object; TrackMan's US8085188 (expires 24 June 2027) assumes the radar is "at a given height above the launch position".
- **Consequence for OpenFlight:** the rig height by default plus a radar gross-error check is the TrackMan model, with no typed input. An envelope of −10 to +76 mm ball support relative to the feet matches Mevo's published limit.
