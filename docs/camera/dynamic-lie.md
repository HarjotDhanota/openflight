# Dynamic lie: camera-based estimate

The owner approved a labelled estimate on 23 October 2026. This is **model
geometry**, not a measured 3D shaft pose. Its accuracy has not been graded on real
swings. The existing late camera worker publishes it in the same shot update as
impact location, under `impact_location.dynamic_lie`. The metric catalogue turns
that record into the existing `dynamic_lie` metric. No new sensor or dependency
is required. `--no-impact-live` disables both camera readings.

The [definition](https://support.trackmangolf.com/hc/en-us/articles/39724352849307-Parameters-Dynamic-Lie-Tee-to-Green)
is the lower shaft's angle to the horizon at impact. A single camera line does
not determine that 3D angle: different shaft depths can produce the same image.
For example, with a level pinhole camera, a shaft base at `(0, 2, 0)` metres and
tip at `(0.1, 2, 0.173205)` has a 60-degree lie. Moving the tip along its camera
ray to `(0.105, 2.1, 0.181865)` leaves its image unchanged but gives about
51.435 degrees. Coordinates here are lateral, forward, up.

## Model and evidence

- Use only this shot's saved clip, its detected ball departure/contact bracket
  and connected moving head/shaft. No pooled outline, static lie or another
  shot's angle supplies the result. It can arrive while the impact-location
  outline is still building.
- Assume the lower shaft lies in a plane of constant world-forward depth. This
  excludes forward/back shaft lean; changing that assumption changes the result.
- Use the recorded rig pitch, or the configured rig file when absent, assuming
  level housing and zero roll. The per-lens pinhole intrinsics and principal
  point are those of the impact pipeline; no new distortion calibration is
  implied. Actual unit tilt, distortion and unknown shaft depth remain biases.
- Fit the lower shaft in each of the last two adjacent pre-departure frames,
  then linearly extrapolate to the contact fraction already used by impact
  location. A midpoint contact fraction remains an assumption. Do not use
  post-impact shaft motion.

After pitch correction, image rays meet a constant-forward plane. Their common
depth cancels in the direction. The lie is `atan2(abs(delta up), abs(delta
lateral))`, in degrees, with no handedness multiplier. Saved-image mirroring and
LH normalization use the existing impact preparation. Source, note, frame
indices, fitted endpoints, residuals, contact source, intrinsics source and rig
source accompany the result. This estimate feeds no collision or flight metric.

## Provisional rejection gates

The moving component must overlap the detected head. Its thick core anchors a
search above the head, clear of the head rim, over up to 100 mm of vertical image
extent. Fit row centres of a single thin branch with orthogonal least squares.
Require at least 12 rows, a 12-pixel and 40-mm modelled span, no gaps above two
rows, no branch touching the left/right image edge, width at most 25 mm, and a
95th-percentile centreline residual at most 1.5 pixels. These are provisional
engineering gates, not validated accuracy limits. The scale for selecting the
segment inherits the existing impact pipeline's head-depth/speed model.

Both immediately preceding head frames must be present, with frame interval and
last-frame-to-contact gap at most 12 ms. More than 8 degrees of inter-frame angle
change, or an extrapolated angle outside `(0, 90]`, is refused. Every refusal
has a reason and no value. In particular, a missing shaft never gets the club
category's nominal lie. Low-rate recordings may be unavailable under these gates.

There is **no claimed confidence interval**: line-fit residuals do not bound the
unobserved shaft lean or calibration error. Successful values always retain
`model_geometry`; the catalogue rejects a mislabelled `measured` entry.

## Validation and review

Known-answer images cover 45, 60 and 75 degrees, RH/LH, mirrored/unmirrored saved
frames and two image resolutions. Independent world projection tests cover camera
pitch. Missing contact, head, shaft or geometry, short/blurred evidence and distant
frames must refuse. Live clip tests check that a model value reaches shot metrics
before the impact outline is ready. These tests establish software behaviour,
not accuracy on the Pi or real clubs.

Review captures with no shaft reading show a dash. Separate synthetic lie-tilt
fixtures show the MODEL badge. Before relying on the estimate, grade matched
real shots across both hands, lenses and modes, including shaft lean, lighting,
motion blur, camera tilt, coverage and refusals. No hardware qualification is
claimed by this implementation.
