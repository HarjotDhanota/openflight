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

Reference scoring must distinguish radial speed from total ball speed and keep
model-derived candidates identifiable. Never compare two differently defined
speeds as an accuracy error merely because both use mph. See the
[master plan](../development/fusion-master-plan.md) for the geometry, timing and
reference gates required before promotion.
