# OPS speed and experimental correction

New live shots retain the OPS radial ball speed in `ball_speed_mph`, with
`ball_speed_contract: "radial"`. `ball_speed_raw_mph` retains that same
measurement for explicit provenance. This changes the previous behavior that
automatically replaced the field with a cosine-corrected value when an angle
radar was enabled.

The correction had used a historical peak-radial model, including a fixed
drag assumption and sampling window. It could consume a fallback launch angle
and geometry configured for IWR or K-LD7 instead of the OPS origin. Those inputs
do not establish an accurate total speed for the measured v3 rig. Historical
results documented in the module are not a new-rig validation certificate.

`experimental_ball_speed_total` now carries an explicit candidate status,
value or withholding reason, model version, inputs and assumptions. The shared
evaluator requires measured direction and explicitly identified OPS-relative
geometry. Existing live IWR/K-LD7 geometry proxies cannot satisfy that condition,
so the candidate is withheld until appropriate inputs are available. Estimated
launch angles also cannot qualify it.

The candidate never replaces canonical speed or feeds spin, carry or simulator
connectors. Spin and carry receive the retained canonical radial value; their
existing derived outputs remain estimates. Expect numerical differences from
older runs that automatically promoted the correction. The diagnostic view
shows the experimental candidate separately, including why it is unavailable.

The pure candidate evaluator can be called on recorded inputs for research.
Its scalar model remains unvalidated even when it returns a finite value:
correcting radial speed requires a defensible relationship between the measured
Doppler quantity, its time interval, the ball trajectory and the OPS line of
sight. A mathematical projection identity alone cannot validate the radar's
speed extraction or the historical peak-window assumption.

## Full ball speed by measured projection (E3)

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
    - simulators fly `ball_speed_full` when it is calculated (D31; provenance
      "calculated"), and the radial reading otherwise;
    - spin and carry still take the canonical radial value.
- **Smash:** `smash_factor_full` is calculated only from two full speeds. The
  club speed is still radial, so it is not measured (D21). The shown
  `smash_factor` keeps its `radial_ratio` label.

The method, its threshold and synthetic results are in
[How it works: full ball speed](../how-it-works/full-ball-speed.md).

Reference scoring must distinguish radial speed from total ball speed and keep
model-derived candidates identifiable. Never compare two differently defined
speeds as an accuracy error merely because both use mph. See the
[master plan](../development/fusion-master-plan.md) for the geometry, timing and
reference gates required before promotion.
