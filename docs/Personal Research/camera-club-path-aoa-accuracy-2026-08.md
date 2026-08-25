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

---

## 5. CORRECTION and three larger findings (2026-08-25, multi-agent investigation + verification)

### 5.1 §1's ~2.7° is MODE-DEPENDENT, and wrong for the shipped mode

I computed the distortion error assuming a clubhead well off-axis in a full frame. The
shipped `320×200` mode reads only the **central 640×400 of the 1280×800 array** (registers
X 336–1151, Y 150–665, 2× subsample), so the in-frame field radius never exceeds ~0.55 of
the lens's full field.

Correct mechanism: radial distortion rotates a short image displacement by at most
`atan((rho-1)/(2*sqrt(rho)))`, where `rho` is radial-over-tangential scale. At real geometry:

| Clubhead radius from the true axis | Angular error |
|---|---|
| 25–35 px (impact zone) | **0.06°** |
| 70 px | 0.25° |
| 95 px (follow-through) | 0.47° |
| 209 px (extreme corner) | 1.0–2.6° |

**At `320×200`, distortion is fourth on the error list, not first.** The ~2.7° figure is
right only for a `640×400`-style full field, where a clubhead 0.8 m off-axis rotates by
2.4–4.3°.

**Do not ship a datasheet `k1` at 320×200.** If the lens is k₂-dominated, correcting with
`k1 = -0.191` turns 0.026° into 0.225° — worse than leaving it alone.

⚠️ **Which mode actually runs is unresolved.** The code defaults to `640×400`
(`server.py:4231`, `capture_runtime.py:49`, `club_delivery.py:113`, `REFERENCE_IMAGE_SIZE`),
while `docs/camera/README.md:170` says the production experiment "should use the fixed
320×200 mode". **Pin this before acting on any distortion number.**

### 5.2 The optical axis is not where the code assumes — ~1.0° of pure yaw

`_pixels_to_world` uses `center_x = image_width/2`. For the `320×200` crop the optical axis
is at output **(151.75, 124.75)**, not (160, 100), derived from
`OV9282_PIXEL_ARRAY_LEFT/TOP = 8` (active-array centre is native 647.5/407.5, not 640/400)
and the ISP offset being in the decimated domain.

The **−8.25 px X error is a pure 1.01° yaw** and lands directly on club path. The +24.75 px
Y error cancels, because pitch is already derived from the ball's `ball_z`.

**Fix, ~5 lines, no checkerboard:** port the yaw term that already exists at
`club_delivery.py:650-652` (`yaw_rad = expected_azimuth - observed_azimuth`) into
`_pixels_to_world`, where line 248 currently **discards the ball's azimuth as `_ball_x`**.
Deriving yaw from the ball also absorbs unmeasured M12 lens centration (±50 µm = ±8 output px).

### 5.3 BIGGER THAN BOTH — `detect_reference_ball` locks onto a false object

**Verified directly.** On the real capture:

```
detect_reference_ball(F[10:70]) -> x=182.8  y=128.8  diameter=17.0 px
hand-measured teed ball          -> x=125.8  y=157.4  diameter ~8.6 visible / ~14 true
```

**It is 63.8 px away, on the dark backdrop (DN 94–168), not the ball.** And it passes every
gate — the workflow's verifiers found `speed_ratio` stays 0.999–1.007 throughout.

This is the worst of the three because `focal_px = diameter_px * range / ball_diameter`, so
the scale error multiplies **every** world coordinate, and the camera **pitch** is derived
from the same false object's `y`. Reported effect: ~1.2–1.8× focal error, −1.3° path,
−1.6° AoA.

**Fix this before touching optics.** No distortion or lens work matters while the
self-calibration is anchored to the wrong object.

### 5.4 Do NOT change the lens

The premise that a narrower lens would cut background is already satisfied in silicon: the
`320×200` mode delivers **37.8° × 24.2°**, not the lens's 90° diagonal — ~46 % of the
horizontal field is already discarded.

**Ball-in-frame is the binding constraint** (measured from `frames.npz`: the ball is
trackable for 13 frames, climbing 10.8° of the 12.2° available above the axis):

| Focal | Trackable ball frames | Frame rate | Depth of field |
|---|---|---|---|
| **2.8 mm** | **16.6** | 468 fps | hyperfocal from 420 mm |
| 4.0 mm | 9.8 | ~360 | hyperfocal from 657 mm |
| 6.0 mm | 6.0 | ~261 | 938–2964 mm |
| 12.0 mm | 3.0 | ~142 | **375 mm total** |

Buying vertical field back costs rows, and frame time scales with rows. **Light is not a
reason either way** — at fixed f-number, focal length does not change per-pixel exposure.

---

**Revised priority: (1) fix `detect_reference_ball`; (2) derive the optical axis / yaw from
the ball; (3) pin which capture mode runs; (4) distortion only if running 640×400; (5) keep
the 2.8 mm lens.**
