# Camera fallback: the ball's range and launch when the IWR has none

The IWR6843 is the ball's primary sensor. When it measures a shot nothing here changes.
When its launch, its launch-point range or the setup's tee range is missing or refused,
the kiosk resolves every metric it still can from the camera clip, automatically, and
labels each value `experimental` until it is validated against TrackMan.

## One chain per input (`source_chain`)

Every upstream input takes the first route, in quality order, that gives a finite value.
Each route that gave nothing is kept with its reason in `Shot.input_sources`, and a
route that raises is refused with its error, so one failing stage never takes the shot
down. A gap is closed by adding a route, never by a special case.

| Input | Routes, in order | Labels |
|---|---|---|
| Ball range | the shot's IWR launch point; the camera prior (the setup's ball range, or the shot's resting-ball look); the resting ball's own size in this clip | measured; estimated; experimental |
| Vertical launch | the radar (IWR LCMF, K-LD7); the camera fallback | measured; experimental |
| Horizontal launch | the radar or a camera horizontal on the IWR's depth; the camera fallback; a low-coherence or low-consensus horizontal | as `source_labels.launch_label`; experimental; estimated |
| Side offset | the setup's measured offset; the resting ball on its camera bearing | measured; experimental |
| Spin | an accepted OPS spin; the collision model's spin; the kinematic spin (`spin_estimate`, D38) | measured; the model's label; calculated on a calculated full speed, estimated on the radial one |

Club path and attack keep their own chain (`shot_models.displayed_club_path`, the club
arc, and whatever club-delivery result the camera gives).

## The ball's own size (`camera/resting_ball_range.py`)

The resting ball is measured as the setup measures it, a lit sphere sized on its outline,
in 3-frame windows ending 30 ms before the trigger (the club is near the ball after
that). The median of the windows is its size, their spread (at least 1 px) its
uncertainty. Its distance from the lens is `focal x D / d`, at the unit's calibrated focal
length or the lens's listed one (+-8 %). On Harjot-outdoor-test-1 the 28 shots with a
resting ball measured 41.2-45.3 px, 1.92-2.12 m at the 6 mm lens's listed 2000 px; shot
14's IWR launch point was 1.91 +- 0.12 m.

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

A camera launch with no validated geometry is `experimental`, never measured: the
fallback's and the older camera horizontals (`camera_assisted_experimental`,
`camera_only_experimental`, `camera_legacy_fallback`) alike. Every model output takes the
weakest label of what it is built on (`shot_metrics.UPSTREAM`): the full ball speed,
spin, spin axis, face, loft, D-plane tilt, the indices and every flight metric. A range
from the camera prior makes every range-dependent value estimated, one from the ball's
own size experimental; a model's spin is range-dependent, the OPS's is not. Simulators
never fly an experimental launch (D31).

## Kiosk and replay

The kiosk (`server._fuse_camera_measurements`) and the replays call the same
`camera/fallback_chain.py`. `scripts/analysis/replay_shot_chain.py` runs recorded shots
through the kiosk's own functions (`--ignore-iwr` forces the fallback on a session whose
IWR worked); `scripts/analysis/replay_camera_fusion.py` rebuilds a context with no
recorded tee range through the same range chain.
