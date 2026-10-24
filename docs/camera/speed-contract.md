# OPS speed and experimental correction

Live shots keep the OPS radial ball speed in `ball_speed_mph`, with
`ball_speed_contract: "radial"`; `ball_speed_raw_mph` holds the same
measurement for provenance. It is never replaced by a cosine-corrected value.

`experimental_ball_speed_total` carries the peak-radial correction as a
candidate: status, value or withholding reason, model version, inputs and
assumptions. It requires measured launch direction and OPS-relative geometry;
live IWR/K-LD7 geometry proxies and estimated launch angles do not qualify, so
it is normally withheld. It never replaces canonical speed or feeds spin, carry
or simulator connectors, and its model is unvalidated even when it returns a
value. The diagnostic view shows it separately, with why it is unavailable.

## Full ball speed by measured projection

The full ball speed now has its own field, `ball_speed_full`. It never replaces
canonical `ball_speed_mph`, which stays the OPS radial reading.

- **Method:** one OPS FFT-window radial reading divided by LOS . dir:
    - dir comes from this shot's measured launch angles;
    - LOS runs from the rig file's OPS position to the ball. The ball starts at
      the IWR tee range and the setup's tee side offset, and moves along dir to
      the reading's time.
- **Referred to separation:** with published drag (Ferguson et al. 2022) plus
  gravity along the path.
- **Label:** "calculated", source "measured projection: OPS radial + measured
  launch + rig geometry", with a range from stated input tolerances.
- **Refusals:** the field is "not_measured", with the reason, when LOS . dir is
  below 0.866 (cos 30°), a launch angle is missing or not measured, or a
  geometry source is missing.
- **The legacy factor:** the peak-radial factor does not feed it. That factor
  stays only in `experimental_ball_speed_total`, as a comparator.
- **Consumers:**
    - simulators fly `ball_speed_full` when it is calculated (provenance
      "calculated"), and the radial reading otherwise;
    - spin and carry still take the canonical radial value.
- **Smash:** `smash_factor_full` is calculated only from two full speeds. The
  club speed is still radial, so it is not measured. The shown
  `smash_factor` keeps its `radial_ratio` label.

The method, its threshold and synthetic results are in
[How it works: full ball speed](../how-it-works/full-ball-speed.md).

Reference scoring must distinguish radial speed from total ball speed and keep
model-derived candidates identifiable. Never compare two differently defined
speeds as an accuracy error merely because both use mph. See the
[master plan](../development/fusion-master-plan.md) for the geometry, timing and
reference gates required before promotion.
