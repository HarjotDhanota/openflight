# Ball-metered exposure bias

`config/swing_exposure.json` accepts `ball_exposure_bias_stops`, from `0.0` to
`0.5`. The default is `0.0`; `0.5` is an experimental option for later swing
comparisons. The capture runtime reads it when configuring its swing-exposure
policy. It does not change the tester's placement-preview brightness, guided
static-reference exposure or explicitly selected ladder exposures.

The existing ball-core p95 target is 55% of the usable signal above black.
The optional bias multiplies that target by `2 ** stops`: half a stop gives
about 77.8%. This is a derived target under the existing linear-response model,
not a measured improvement in club visibility. The hysteresis band moves with
the target, with its upper end capped at an assumed 90% to retain clipping
headroom. Requested bias, effective target, band and controls appear in exposure
decision evidence.

The existing control selection still spends gain before lengthening exposure,
within the sensor, frame-period, enforced-profile and blur limits. Bias never
raises those limits. Actual ball-core clipping or insufficient signal retains
the existing backoff and analysis-eligibility rules; predictions do not certify
the next exposure. A full-stop bias is rejected rather than silently clamped.

Use an explicit `0.5` setting only for a labelled experiment. Compare the same
scene and club at zero and half-stop bias, retain exposure-associated originals,
and grade ball clipping, centre/outline stability, club-edge visibility and blur.
Stationary recordings cannot establish moving-club quality. Latest stationary
development data also show size sensitivity to fitting/exposure, so a green
locator alone does not qualify metric geometry. No half-stop default or live
performance improvement is claimed by adding this option.
