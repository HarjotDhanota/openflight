# Why camera club path and attack angle are inaccurate — two quantified causes

> **Prompted by** a contributor report: *"i just tried to improve club path and AoA accuracy
> with my trackman session and they are still pretty off because the camera code today does
> not do this."* This document identifies two concrete, quantified defects in
> `src/openflight/camera/club_delivery.py`, both fixable, one of them large.
>
> Comparator set is **Trackman 4, Full Swing KIT, Mevo Gen 2**. Grading as in
> `camera-feasibility-verdict-2026-08.md`.

---

## 0. What the camera code does today

`club_delivery._pixels_to_world` is well built and the architecture is right:

- **focal scale from the reference ball** — `focal_px = ball.diameter_px * camera_ball_range_m / ball_diameter_m`, so no user calibration is needed
- **camera pitch derived** from the reference ball's image position
- **roll correction** applied via `deroll_normalized_offsets`
- each pixel ray **intersected with the IWR slant-range sphere**, correctly accounting for the camera and radar sitting at different heights

That is the pixel-ray ∩ range-sphere design, implemented properly. The two defects below sit
on top of it.

## 1. Lens distortion is not modelled at all — **~2.7° systematic**

`_pixels_to_world` converts pixels to rays with a **pure pinhole model**:

    image_x = horizontal_pixel_sign * (points_px[:, 0] - center_x) / focal_px
    image_z = -(points_px[:, 1] - center_y) / focal_px

There is **no distortion term anywhere in the repository** — searching `src/openflight/` for
`distort`, `k1`, `k2`, `undistort` or radial distortion returns nothing.

The fitted lens is an InnoMaker **2.8 mm wide-angle** with a datasheet **TV distortion of
< −17 %**. Modelling that as `r_d = r(1 + k1·r²)` with `k1 ≈ −0.17` at the field corner, on
the shipped `320×200` frame (half-diagonal 189 px):

| Clubhead position | r / r_max | Radial shift |
|---|---|---|
| optical axis | 0.00 | 0.00 px |
| near impact | 0.19 | 0.22 px |
| at the ball | 0.35 | **1.36 px** |
| low and left | 0.54 | **5.16 px** |

Club path and attack angle are computed from a **displacement between two positions**, so
what matters is the *differential* distortion across that baseline. For a representative
two-frame clubhead travel of 64 px, the differential is **2.99 px**:

> ### ⇒ **2.67° of systematic error on club path and attack angle**

Three things make this worse than the number alone suggests:

1. **It is systematic, not random.** It does not average out over a session, so a user
   comparing against Trackman sees a consistent bias rather than scatter.
2. **It varies with where the club sits in frame**, which varies with setup and swing. The
   same delivery at two different ball positions reads differently — which presents as
   erratic accuracy rather than a clean offset.
3. It grows as **r³**, so it is mild near the axis and severe toward the edges — exactly
   where a clubhead sweeps.

**Fix:** a one-time checkerboard calibration for `k1`/`k2` and an undistort step before
`_pixels_to_world`. Standard, cheap, no hardware.

## 2. Velocity is a two-point difference — **~0.65° avoidable random error**

`_delivery_from_feature_pair` requires exactly two frames (`times.shape != (2,)`) and takes
a raw finite difference:

    velocity = (positions[:, 1] - positions[:, 0]) / elapsed

Any centroid noise on either endpoint goes straight into the angle. Over a 62 px baseline:

| Centroid noise | Two-point | 4-frame fit | 8-frame fit | 12-frame fit |
|---|---|---|---|---|
| 0.5 px | **0.65°** | 0.62° | 0.50° | **0.43°** |

Roughly a 1.5× improvement, costing nothing but a longer window.

**The radar side already learned this.** `iwr6843/club.py::estimate_club_path` fits `x(t)`
and `y(t)` linearly over a window, and its docstring says why:

> *"An angle-rate fit's slope is therefore a window-averaged rate... and no choice of
> range-evaluation point can turn that average back into the instantaneous rate at impact —
> confirmed on this module's own synthetic fixture: a fitted azimuth rate of 106.4 deg/s
> against a true instantaneous rate of 64.1 deg/s at impact."*

The camera path does exactly what the radar path explicitly rejected.

## 3. Combined, and what to expect

| Source | Type | Magnitude |
|---|---|---|
| Lens distortion | **systematic** | **~2.7°** |
| Two-point differencing | random | ~0.65° |

Trackman-class club path is quoted around 1°. **Distortion alone is roughly 2.7× that
budget**, which is consistent with the report that the numbers are "pretty off" rather than
slightly noisy.

**Recommended order:**

1. **Checkerboard calibration + undistort.** Largest single win, one-time, no hardware.
2. **Fit the delivery over a window** instead of two frames, matching `club.py`.
3. Only then re-measure against a Trackman session — the current comparison is dominated by
   a term that has nothing to do with the estimator's quality.

## 4. Not yet investigated — do not assume these are fine

- Whether the reference-ball focal scale is itself distortion-biased. `focal_px` comes from
  the ball's *apparent diameter*, and a ball off-axis is distorted too, so the scale may
  carry its own error that **compounds** with §1 rather than being independent of it.
- Whether `horizontal_pixel_sign` and `roll_correction_deg` are correct for the shipped 180°
  camera mount. That would be a sign or offset error rather than a scale one, and would look
  quite different in the data.
- Whether the two frames chosen by the pair estimator are close enough to impact. The radar
  side found impact landing 2–4 frames from where it was assumed, which put the club-path
  estimator on the follow-through; the camera side has not been checked for the same fault.
