# Setup geometry and next steps: spec

Date: 29 September 2026. Branch: `feat/tester-capture-pilot` (fork). Status: proposed.

This spec fixes the two known code problems from the camera-fusion research log (`docs/research/camera-fusion/README.md`, section 4) and orders the work that follows. Only measured rig geometry is a fixed input. Everything else is measured at setup or per shot, or it is a design constant listed here.

## 1. Problems being fixed

1. **Teed balls are excluded from the ball search.** The resting-ball search drops anything whose ray points more than 1° above the camera's level line (`reference_ball_range._seed_filter` and `_candidate`). The lens is 80–95 mm above the floor, and a ball on a tee on a mat can sit at or above that.
2. **Heights measured from a tee top break the radar floor-bounce model.** The setup now measures heights from whatever the ball rests on (f02c3ec). Most swing geometry only uses height differences. The two-ray multipath model does not: `trajectory._two_ray_solve` and the LCMF path mirror the radar in the floor, so they need the radar's height above the reflecting floor. On a teed ball, "height 0" becomes the tee top. When the radar would come out negative, the server also lifts every height, which moves the floor again.

## 2. Work now (code only; testable without hardware)

### A1. Hitting area replaces the level-line rule

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

**A2a: setup semantics (now)**

- **Setup instruction:** "Place one ball directly on the hitting surface (mat or grass, not on a tee), in the hitting area." This replaces "exactly as you'll hit it".
- **What the solved height means:** "lens height above the hitting surface". The radar's height above the surface is that minus 44 mm.
- **Remove the uniform-lift workaround** in `server._apply_solved_camera_height`. With a surface reference the radar can no longer go below zero; a solved lens height under 49 mm is refused as implausible.
- **Separate the heights the swing server receives:**
  - `--solved-camera-height-m`: lens above the surface.
  - radar above the surface (derived as above): used by the floor-bounce model.
  - `--iwr6843-ball-height-m`: the ball centre above the surface at address. It defaults to one radius until a per-shot tee height exists.
- **Record** the surface reference, and how it was solved, in the setup evidence and the session geometry.

**Tests:**
- The server derives the radar height from the lens height with no lift.
- A teed ball height never changes the radar height given to the two-ray model.
- The setup refuses a lens height below the radar depth.

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

### A4. Research, offline

- **Radar spin axis:** research how TrackMan and FlightScope patents get the spin axis from radar. Then check the IWR's feasibility: sort the echo by Doppler, then measure each Doppler slice's angle by comparing phase across the receivers. Do a first offline look at existing IWR shot dumps for a spin spread.
- **Range–Doppler coupling** (≈0.62 ms impact-time bias): prepare the correction behind a flag, but do not enable it until B4 data checks the sign.

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

## 5. Decisions needed from Harjot

1. The hitting-area constants in A1: 1.0–2.5 m, ±0.30 m, tees to 90 mm.
2. The setup ball goes on the surface, never on a tee (A2a).
3. Whether the live-preview overlay ships on the fork now, with the patent note carried to any upstream PR.
