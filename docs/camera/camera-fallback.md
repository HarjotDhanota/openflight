# Camera fallback: the ball's range and launch when the IWR has none

The IWR6843 is the ball's primary sensor. When it measures a shot nothing here changes.
When its launch, its launch-point range or the setup's tee range is missing or refused,
the kiosk resolves every metric it still can from the camera clip, automatically, and
labels each value from the observation's gates and geometry. Reference validation is
tracked separately from the source label.

## One chain per input (`source_chain`)

Every upstream input takes the first route, in quality order, that gives a finite value.
Each route that gave nothing is kept with its reason in `Shot.input_sources`, and a
route that raises is refused with its error, so one failing stage never takes the shot
down. A gap is closed by adding a route, never by a special case.

| Input | Routes, in order | Labels |
|---|---|---|
| Ball range | the shot's IWR launch point; the camera prior; the resting ball's own size in this clip | IWR measured, or estimated for partial/ambiguous evidence; prior estimated; ball size estimated with calibrated intrinsics, otherwise experimental |
| Vertical launch | the radar (IWR LCMF, K-LD7); the camera fallback | radar estimated when using configured tilt or a weaker IWR range; camera estimated with calibrated intrinsics, otherwise experimental |
| Horizontal launch | radar and camera candidates, ranked by their attached uncertainties when both have them; radar wins otherwise | experimental never replaces non-experimental; a failed coherence/consensus gate is experimental |
| Side offset | the setup's measured offset; the resting ball on its camera bearing | measured; experimental |
| Spin | an accepted OPS spin; the collision model's spin; the OPS reading below the confidence floor | measured; the model's label (experimental when it rests on the camera head track or an experimental launch); experimental, feeding nothing |

No formula spin is a route (owner decision, 25 Oct 2026): the kinematic `170 v sin(LA)^1.2`
and its TrackMan refit missed TrackMan by about 30 % and 26 % median (58 % on woods), so a
shot with none of the three has no spin, and no carry, with the reason "no trustworthy
spin: ..." naming each route.

Club path keeps its own chain (`shot_models.displayed_club_path`, the club arc, and
whatever club-delivery result the camera gives). The attack angle is a `source_chain`
resolution (`shot_models.attack_angle_resolution`, recorded in
`Shot.input_sources["attack_angle"]`): the radar club track; the camera + IWR (or OPS)
fused delivery; an accepted IWR attack; the camera head track (experimental); the IWR
attack-angle candidate (estimated, 25 Oct 2026); the E1 arc. See
`docs/reference/metric-coverage.md`.

## The ball's own size (`camera/resting_ball_range.py`)

The resting ball is measured as the setup measures it, a lit sphere sized on its outline,
in 3-frame windows ending 30 ms before the trigger (the club is near the ball after
that). The median of the windows is its size, their spread (at least 1 px) its
uncertainty. Its distance from the lens is `focal x D / d`, at the unit's calibrated focal
length or the lens's listed one (+-8 %). On Harjot-outdoor-test-1 the 28 shots with a
resting ball measured 41.2-45.3 px, 1.92-2.12 m at the 6 mm lens's listed 2000 px; shot
14's IWR launch point was 1.91 +- 0.12 m.

Before IWR pose fitting, the kiosk compares the saved ball reference with this clip's
cached outline fit. A centre displacement beyond `max(8 px, 0.75 reference diameters)`
or an observed/reference diameter ratio outside `0.75..1.33` rejects the camera range
prior for that shot. These are the existing scene/impact detector agreement bounds.
Both the per-shot and session-default prior are excluded from IWR ranging and the
later camera range chain; a qualifying IWR range still wins. The size fallback keeps
its existing lens-dependent label. A missing fit is unavailable, not agreement, and
does not itself reject a prior. Agreement is a consistency check, not validated ball
identity or distance. The comparison is recorded in `Shot.input_sources.ball_reference`
and the IWR replay snapshot, including the rejection reason. This waits for the
existing camera prefetch before IWR analysis; capture monitoring remains independent,
and the Pi latency impact needs measurement.

## The launch (`camera/ball_launch_fallback.py`)

After impact the ball is a compact round blob against the median of the frames 75-25 ms
before the trigger. The longest straight run of blobs that starts from the resting ball
is its track, at least three points (two fit any pair of highlights, the club's toe
included). The track is fitted as one line from the resting ball, at each frame's
exposure centre:

    P(t) = P0 + v u (t - t0)  (+ gravity),  u = (cos V sin H, cos V cos H, sin V)

P0 is the resting ball on its own ray at the range above. The camera sees bearings;
depth along the line comes from the OPS243's radial speed, `v (LOS . u)` at a reading a
few milliseconds after impact, which ties the speed to the direction. The ball's image
size changes 10-30 % frame to frame with blur and light, too noisy to carry depth. The
camera-only speed (no OPS term) is fitted too and only compared with the OPS's: it is
never the ball speed.

The pitch is the unit's calibrated one, else the one that puts the resting ball on the
floor at its range (an uncalibrated principal point makes this +-1 deg, and the gap to
the LIS3DH's pitch widens it). Roll is the swing's verified LIS3DH roll. The horizontal
is turned onto the target line by the setup's aim yaw, as every horizontal angle is.

Each angle's 1-sigma is the fit's plus every systematic refitted at +-1 sigma: focal
length, pitch, roll, ball distance, OPS speed and reading time, and for the horizontal
the aim's own uncertainty. Withheld, with the reason: fewer than three points, a
residual over 2.5 px, an OPS misfit over 3 mph, a vertical outside -5..+60 deg, a
horizontal beyond 45 deg, or a camera speed more than 3 sigma and 25 % off the OPS's.

## Labels downstream

The source policy lives in `source_labels`. A high-tier camera + IWR club reading or
camera-assisted horizontal is measured with calibrated lens intrinsics, estimated with
nominal intrinsics. Lower tiers and camera + OPS club readings stay experimental. The
camera ball-launch fallback is estimated with calibrated intrinsics and experimental
otherwise. Camera-only and legacy fallback horizontals remain experimental. Lens state
and the accepted IWR launch status are recorded on each shot.

Every model output takes the
weakest label of what it is built on (`shot_metrics.UPSTREAM`): the full ball speed,
spin, spin axis, face, loft, D-plane tilt, the indices and every flight metric. A range
from the camera prior makes every range-dependent value estimated, one from the ball's
own size follows the lens-dependent label above. A model's spin is range-dependent,
the OPS's is not. Partial IWR evidence keeps `iwr_partial` in the route record and reads
as estimated in metric records. Partial or ambiguous ranges never calibrate tilt.

Collision outputs state the centre-face-strike assumption; flight sources name the air
density used. Model assumptions alone do not lower a calculated value. Failed sensor
gates do: the IWR attack candidate is experimental and its dependent outputs inherit it.
Simulator admission and provenance use the published metric records. Experimental
required inputs are always refused, even with estimated/model-geometry opt-ins enabled.

## Kiosk and replay

The kiosk (`server._fuse_camera_measurements`) and the replays call the same
`camera/fallback_chain.py`. `scripts/analysis/replay_shot_chain.py` runs recorded shots
through the kiosk's own functions (`--ignore-iwr` forces the fallback on a session whose
IWR worked); `scripts/analysis/replay_camera_fusion.py` rebuilds a context with no
recorded tee range through the same range chain.

The shot-chain replay preserves recorded address evidence and measures/checks the
resting outline before fusion, as the live handoff does. Older sessions can supply
the ball seed and aim uncertainty from their frozen context, after session, shot,
capture hash and context fingerprint checks. It reports missing address look/freshness
without inventing readiness. A session seed is explicitly labelled when no per-shot
seed exists. Aim overrides take precedence and never carry into another shot.
Existing reference rejections remain effective if a new fit is unavailable.
The replay retains recorded IWR readings; it does not rerun radar extraction after
changing a camera range prior. Accepted replay fits do not establish physical accuracy.
