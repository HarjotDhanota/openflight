# Camera feasibility verdict (2026-08-22) — can the OV9281 do our job, or do we need Mevo/Trackman-class hardware?

> **Scope.** Decides whether the camera upstream shipped in [PR #215](https://github.com/jewbetcha/openflight/pull/215) can be made to satisfy our Stage 0C (club pose) and Stage 0E (ball spin) budgets, or whether different hardware is required. Companion to v2 guide **§1J**. Supersedes parts of `camera-hardware-spec-v1.md` §2–§5.
>
> **Method note.** Commercial-system claims are graded: **[FCC]** = teardown photo from an FCC equipment-authorization filing; **[MAN]** = manufacturer manual; **[SPEC]** = datasheet; **[PRESS]** = vendor/press claim; **[INF]** = my inference. Repo claims are read from code, not docs.

---

## 0. The verdict, in five sentences

**Keep the OV9281 — the sensor is not the problem.** The shipped configuration underperforms our budgets by ~3.6× in plate scale and ~25× in exposure, but both deficits are caused by the *readout mode* and the *absence of illumination*, not by the silicon: a native 1:1 readout of a **1280×200 strip with the camera rotated 90°** delivers a **~57 px ball at ~446 fps (2.24 ms)** on the same sensor and the same 6 mm lens, which lands on Stage 0E's `ball_px=60` cell and clears the 3° display tier and the 5° driver-face gate. **A pulsed IR strobe with a capacitor bank is mandatory and is the single real hardware purchase** — both commercial comparators I could open up are strobed (MLM2PRO carries a ~13,600 µF/35 V flash bank **[FCC]**; PiTrac uses a ~100 W pulsed LED array **[PRESS]**), and no ambient-light configuration can reach a 20 µs exposure. Reaching the tighter **2° tier (150–250 px ball) does require a bigger sensor** — that, and only that, is what would justify new hardware. **Our own Stage 0E model contains a flaw that overstated the case for new optics**, described in §3.1: its `ball_px` sweep varies sensor pixel count at *fixed* field of view, so it never represented the real lens trade, and §1I's stated reason for the 240 fps failure is mis-attributed.

---

## 0.5 Measured against a real capture (2026-08-24) — **[MEASURED]**

The first real capture archive with metadata (`frames.npz`: 99 frames, `320x200` mono, plus per-frame timestamps, exposure, gain and the radar trigger) tests several assumptions in this document directly. Where §0 estimated, this section measures.

| Quantity | This document assumed | Measured | Effect |
|---|---|---|---|
| Exposure | 500 µs ("as shipped" in `budget_radar.py`) | **997 µs** | Doubles the error — see below |
| Analogue gain | not modelled | **15.94 = 100 % of max** | No headroom left in either knob |
| Frame interval | 2.14 ms / 468 fps | **2.1380 ms, jitter 2.9 µs** | ✅ Confirms §0 |
| Ball at the tee | 28 px (0.656 px/mm) | **~14–16 px (~0.35 px/mm)** | See §1 note |
| Illumination | "absence of illumination" | **A near-field directional light was ON**, and drove 71 % of the mat to clip at 255 DN | Refines §0 |
| Ball detectability | not modelled | **Visible in every frame — but contrast inverts sign** between address (−62 DN) and flight (+141 DN) | Breaks the fixed-polarity detector |

**The exposure finding reorders the whole document.** Re-running our own pre-registered `budget_radar.py` with *only* the exposure constant changed moves median impact error from **94.9 mm to 193.5 mm**. More importantly, re-running at the higher plate scale gives **193.2 mm vs 193.5 mm** — a 0.3 mm difference. Motion smear is measured in pixels, so doubling plate scale doubles smear at the same moment it doubles detail, and the two very nearly cancel. **At 997 µs, better optics buy nothing.** §0's ranking of "~3.6× in plate scale and ~25× in exposure" as two comparable deficits is wrong: the exposure deficit dominates and the plate-scale deficit is second-order until it is fixed. This *strengthens* §0's conclusion that a pulsed strobe is the single mandatory purchase, and weakens the case for spending effort on readout modes first.

**Caveat on the 94.9 → 193.5 mm figures.** `budget_radar.py` treats blur as pure centroid noise, which is the conservative screening model, not the real solver. Blur-aware template fitting forward-models the smear and does far better (A-v3 ambient: 1.050 mm median). But that result was *also* computed at 500 µs, so it too is pending a re-run at 997 µs and must not be quoted about real hardware until then.

**Refinement to "absence of illumination".** §0 says no ambient configuration can reach a 20 µs exposure, which remains true. But the capture shows a bright, near-field, *directional* light that was on throughout the armed window and switched off 12.8 ms after the radar trigger — the near mat lost 58 % of its brightness while the backdrop behind it lost 14 %, which is inverse-square falloff from a source close to the tee. **Whose light this is, is unresolved.** `GPIO6` was reserved for a camera strobe and then reallocated to UPS power-loss detect (`docs/build-checklist.md`), so nothing in our software controls it. The timing relative to the trigger is too tight for coincidence. *This needs answering by whoever captured the shot, not inferring from pixels.*

**A defect this surfaced.** `silhouette_poc/fusion/pipeline.py::_ball_component` thresholds at `percentile(frame,10) + 210 DN`. On a real capture the 10th percentile is 66–77 DN, putting the threshold at 276–287 DN — **above the 8-bit ceiling, so the mask is always empty and it raises `visibility_ball` unconditionally**, on every frame, for reasons unrelated to the ball. Its companion in `_separate_head_from_shaft` fails the other way: `+160 DN` admits 30 % of the frame as "bright shaft" because the saturated mat clears the bar everywhere.

**The ball's contrast inverts, and that is the real defect.** The ball is visible in *every* frame of this shot — but not with a consistent sign:

| | Ball | Background | Contrast | Against the `+210 DN` rule |
|---|---|---|---|---|
| At address (F20) | 192 DN | 255 DN (clipped mat) | **−62 DN** | wrong sign — unreachable at any threshold |
| In flight (F76) | 224 DN | 102 DN (dark wall) | **+122 DN** | right sign, still short of the bar |

So the rule fails in *both* directions, the easy case included. No choice of constant fixes it, because the background swings from 102 DN to 255 DN inside a single shot. The replacement must find compact regions differing from their **local** background in **either** direction and confirm them by **shape** — the ball is the one object in frame with a known fixed physical diameter, which is a far stronger discriminator than any DN offset.

**Status of the fix (2026-08-24).** A replacement detector is in `silhouette_poc/fusion/ball_detect.py` (new file; `pipeline.py` not yet rewired), with `tests/test_ball_detect.py` pinning each defect below. Measured on the real capture:

| | Old rule | New detector |
|---|---|---|
| Ball among candidates, 16 address frames | **0 / 16** | 3 / 16 per frame |
| Teed-ball centre, vs hand-measured truth | never found | **3.64 px error** |
| Circle fit bias at 120° of visible arc | −44 % | **exact** (geometric fit replaces algebraic) |

Three things were needed, and only the first was expected: (1) threshold the *signed* residual against a *local* background, in both directions; (2) replace the algebraic circle fit, which was biased −44 % on the partial arcs that are the normal case here; (3) select by **departure** rather than shape — a bay contains signage and shoes that fit a circle better than a blended ball, and a shape-only score picked the golfer's leg in 10 real frames out of 10. Static clutter scores 0.00 on departure, the ball 1.00. Per-frame detection remains low (~20–45 %), so the temporal aggregation is load-bearing, not a refinement.

**What is still NOT recoverable from this capture: the ball's radius, and therefore the plate scale.** The same ball in the same shot measures 6.86 px at address (biased low — the lit top blends into the clipped mat, so the half-maximum crossing lands inside the ball) and 13.97 px in flight (biased high — the core saturates and blooms, plus ~4.7 px of vertical motion smear). **A factor of 2.0 apart.** No algorithm closes that gap; it needs a correctly exposed reference ball. This is the same 2× ambiguity as Gate 0, arriving by a different route, and it means impact location in *millimetres* is blocked on exposure rather than on software.

**Consequence for focal self-calibration: it survives, with a condition.** §0.5's earlier draft recorded the ball as unmeasurable at address; that was a measurement error on my part (a median taken over a region that was mostly mat, swamping the ball's ~100 px). The ball *is* measurable — but its visible extent at address is a **partial disk**, roughly full width and ~60 % of height, because the upper surface faces the light and blends into the mat. Self-calibration must therefore **fit a circle of known radius to the visible arc**, not measure a bounding box, or it will systematically under-read the diameter and inflate the derived scale.

Full write-up with figures: <https://claude.ai/code/artifact/c6b048fa-6950-4433-8891-42c875600d72>

---

## 0.6 Frame rate, readout mode, and "zoom" — **[MEASURED + DERIVED]**

Raised 2026-08-25: *do we even need 467 fps, would a higher-resolution mode be better, and can we zoom?* Working it through with the corrected 2.8 mm lens changes §0's central recommendation.

### 0.6.1 Frame rate is not the binding constraint — pixel throughput is

| Mode | Pixel rate | Effective pitch | `focal_px` | px/mm @1425 mm | Ball |
|---|---|---|---|---|---|
| 1280×800 native (1:1) | 147 Mpx/s | 3.0 µm | 933 | 0.655 | **27.9 px** |
| 640×400 (2× subsample) | 65 Mpx/s | 6.0 µm | 467 | 0.327 | 14.0 px |
| **320×200 — SHIPPED** | **30 Mpx/s** | 6.0 µm | 467 | 0.327 | **14.0 px** |
| 1280×200 strip (1:1, rotated) | 114 Mpx/s | 3.0 µm | 933 | 0.655 | **27.9 px** |

**The shipped mode uses about 20 % of the sensor's pixel budget.** There is roughly 5× headroom, which means resolution can be bought *without* giving up frame rate. Frame rate was never the thing to trade.

### 0.6.2 640×400 is a trap

It is the obvious "higher resolution" move and it does nothing for us. Per §1's register analysis, 640×400 is `1280×800` at 2× subsample and `320×200` is a *crop* of the same subsampled field — **identical angular scale per output pixel**. Switching modes widens the field and leaves the ball at 14 px. Only a **1:1 (unbinned) readout** raises plate scale.

### 0.6.3 Dropping frame rate buys nothing for blur

Blur depends on exposure time, not frame rate. And exposure is *capped by* the frame period, so a lower frame rate only permits a **longer** exposure — the wrong direction. The only thing a lower frame rate buys is freed pixel budget, and §0.6.1 shows we do not need to free any.

It also costs frames. Tonight's capture had the club in view for ~34 ms:

| | Frame interval | Club travel between frames | Frames with club in view |
|---|---|---|---|
| 467 fps | 2.14 ms | 96 mm | ~16 |
| 253 fps | 3.95 ms | 178 mm | ~9 |
| 144 fps | 6.94 ms | 312 mm | ~5 |

At 144 fps the club moves three head-lengths between frames and only ~5 frames carry it. **Keep the frame rate; spend the pixel headroom instead.**

**The metric that actually binds is not frame count — it is the worst-case gap between the nearest frame and the impact instant.** We do not need a frame *at* impact; the radar I/Q supplies the impact time and the club's pose is extrapolated to it. Half the inter-frame travel is therefore how far that extrapolation has to reach:

| fps | Frame period | Club travel | **Worst-case gap** | |
|---|---|---|---|---|
| 4,600 | 0.22 ms | 10 mm | **5 mm** | TrackMan iO measurement camera |
| **467** | 2.14 ms | 96 mm | **48 mm** | **OpenFlight, shipped** |
| 253 | 3.95 ms | 178 mm | 89 mm | OV9281 640×400 |
| 144 | 6.94 ms | 312 mm | 156 mm | OV9281 1280×800 |
| 60 | 16.67 ms | 750 mm | **375 mm** | TrackMan 4 **video** camera |

A driver face is ~100 mm heel-to-toe and impact location needs single-digit mm. Extrapolating a rotating head on an arc over 48 mm is demanding; over 375 mm it is not a measurement.

**⚠️ Do not confuse TrackMan 4's two cameras.** Its published *"HD 720p @ 60 fps / Full HD 1080p @ 45 fps"* is the **video recording** camera — TrackMan's own wording is "Radar Synchronized High **Dynamic Range**". The measurement path is separately named "Radar Synchronized High **Speed** Optics" and **TrackMan does not publish its frame rate**. The 4,600 fps figure belongs to the iO. This is the same trap §1J already recorded for MLM2PRO — one high-speed measurement camera plus a wide-angle 2K *video* camera — and Mevo+ repeats it. Spec sheets lead with the video camera because it is the consumer-facing feature. **[MAN]** <https://www.trackman.com/golf/trackman-4/tech-specs>

**Unbudgeted error term this exposes:** at 48 mm we are ~10× worse than the iO on extrapolation reach. The pose fit must therefore recover clubhead *velocity* as well as pose, and the extrapolation over that gap is an error source that no current budget cell models.

### 0.6.4 "Zoom" means a different lens, not a crop

Digital zoom adds no information — it is interpolation. A *native crop* is not zoom either; it is how the 1:1 strip is obtained, and its benefit comes from the unbinned pixels, not the cropping. The real lever is that **M12 is a screw mount and the lens is interchangeable** (~$15):

| Lens, 1:1 readout | `focal_px` | px/mm | Ball | Field width |
|---|---|---|---|---|
| **2.8 mm (fitted)** | 933 | 0.655 | 27.9 px | 1954 mm |
| 4.0 mm | 1333 | 0.936 | 39.9 px | 1368 mm |
| **6.0 mm** | 2000 | 1.404 | **59.9 px** | 912 mm |
| 8.0 mm | 2667 | 1.871 | 79.9 px | 684 mm |

⚠️ The enclosure params list `CAM_FLANGE_C/CS = 17.526/12.526` (C/CS mount) while the fitted camera is **M12**. Confirm the mechanical interface before ordering anything.

### 0.6.5 Knock-on: §0's headline is overstated by 2.1×

§0 recommends *"a native 1:1 readout of a 1280×200 strip with the camera rotated 90° delivers a ~57 px ball at ~446 fps on the same sensor and the same 6 mm lens."* That 57 px was computed on the **inferred 6 mm lens**. The lens is 2.8 mm, so the same strip delivers **~28 px**, not 57 — and §0's conclusion that this "lands on Stage 0E's `ball_px=60` cell and clears the 3° display tier" **does not hold as written**.

**The recommendation is rescuable, but it now needs two changes rather than one:** the 1:1 strip readout *and* a longer lens. 6 mm at 1:1 gives 59.9 px — which is what §0 believed it was getting — and still leaves a 912 mm field.

The cost is the framing tension §1J already named: a longer lens narrows the field and trades against the camera's *other* job, live swing preview and alignment, which is presumably why a 2.8 mm wide-angle is fitted in the first place. That trade is now a real decision rather than a hypothetical.

**Open:** whether a 912 mm field still contains the launch corridor for long enough, and whether preview/alignment can live with it or needs its own treatment.

---

## 1. The plate scale — settled

Three artefacts disagreed about what the `320x200` mode does. Resolved from OmniVision register semantics.

Registers in `drivers/ov9281/ov9282-high-speed.patch`, `mode_320x200_regs`:

| Register | Value | Meaning |
|---|---|---|
| `0x3800/01` | 336 | X_ADDR_START |
| `0x3804/05` | 1151 | X_ADDR_END → **816 columns** |
| `0x3802/03` | 150 | Y_ADDR_START |
| `0x3806/07` | 665 | Y_ADDR_END → **516 rows** |
| `0x3814`/`0x3815` | `0x22` | X_INC / Y_INC = (odd 2, even 2) → **step 2 = 2× subsample** |
| `0x3808/09` | 320 | X_OUTPUT_SIZE |
| `0x380a/0b` | 200 | Y_OUTPUT_SIZE |
| `0x3810/11`, `0x3812/13` | 4, 4 | ISP X/Y offset |

816 columns × 516 rows sampled at step 2 → 408 × 258 available; the ISP then emits a 320 × 200 window from that.

**Conclusion: the 320×200 mode subsamples 2×, which is the *same angular scale per output pixel* as the 640×400 mode (1280×800 at 2×). A ball is the same pixel size in both (~28 px). The field is 50 % of the 640×400 field in each axis.**

> **[MEASURED 2026-08-24] The real capture shows a ~14–16 px ball, not 28 px — and the register analysis above is *not* what is wrong.**
>
> Two independent references in `frames.npz` agree: the ball measures 13.8–14.7 px in flight (~16 px back-projected to the tee), and a golf shoe spans ~48 px where 0.656 px/mm predicts 190 px for a 290 mm shoe. Both put the field of view near **930 mm**, not the 488 mm assumed here. The scene corroborates it — a 19-inch field could not contain a shoe, a stretch of mat and the golfer's leg at once.
>
> The distinction that matters: this section derives the **angular** scale per output pixel from `X_INC`/`Y_INC`, which is a property of the readout mode and stands. What is measured in the capture is the **plate** scale — angular scale ÷ camera-to-ball distance. So the 2× gap lands on a term this section never touched.
>
> **RESOLVED 2026-08-24: the lens is 2.8 mm, not 6 mm.** The unit uses the InnoMaker `CAM-MIPI9281RAW-V2` (user-confirmed exact part). Its datasheet gives a **fixed 2.8 mm wide-angle M12 lens, FOV(D) 90°, FOV(H) 72°, f/2.2, TV distortion < −17 %**. §3's "6.0 mm" was inferred by assuming `focal_px = 1000` and back-solving — it was never read off a datasheet, and it is wrong by 2.1×.
>
> Everything reconciles once that is corrected:
>
> | | Value |
> |---|---|
> | Effective pixel pitch, `320×200` mode (3.0 µm at 2× subsample) | 6.0 µm |
> | **`focal_px` in the shipped mode** (2.8 mm ÷ 6.0 µm) | **466.7 px** |
> | Measured airborne ball, 13.97 px → plate scale | 0.327 px/mm |
> | ⇒ implied camera-to-ball distance | **1425 mm = 4.7 ft** |
> | README's documented placement | **3–5 ft** ✅ |
> | ⇒ implied FOV(H) | 68.9° |
> | Datasheet FOV(H) | **72°** ✅ (gap is the −17 % distortion) |
>
> Three independent sources — the datasheet lens, the measured ball, and the documented placement — agree. **Gate 0 is settled: plate scale ≈ 0.33 px/mm and the ball is ≈ 14 px at the tee.** Both prior candidates were wrong: 0.656 px/mm was 2.0× too optimistic and 1.31 px/mm was 4.0×. Use `focal_px = 466.7` and derive plate scale as `466.7 / Z_mm` for whatever placement distance is actually used.
>
> **New consequence — lens distortion is not optional.** A 2.8 mm wide-angle at **< −17 % TV distortion** is a barrel lens, and the POC projects its mesh through an undistorted pinhole model. That is fine near the optical axis and wrong toward the edges, exactly where a clubhead sweeps. A one-time checkerboard calibration for `k1/k2` is now a prerequisite for millimetre-accurate impact location, and no such calibration exists anywhere in the repo.

Therefore:

- The patch's `.crop = {left+480, top+150, 320, 200}` struct is **wrong** — it declares a 1:1 native window the registers do not implement. libcamera will report an incorrect FOV/`ScalerCrop` from it. *(Worth reporting upstream.)*
- `club_delivery._image_scale()`'s 0.5× assumption is **also wrong** — objects are the same pixel size in both modes, so its search radii come out ~2× too small (partly masked by the `max(24.0, …)` / `max(14, …)` floors).

Derived optics, anchored on the shipped test fixture (ball 28 px at 1.524 m):

| Quantity | Value |
|---|---|
| focal length in 2×-subsampled pixels | 28 × 1.524 / 0.04267 = **1000 px** |
| effective pixel pitch (2× subsample) | 6.0 µm |
| **lens focal length** | 1000 × 6.0 µm = **6.0 mm** (a stock M12 6 mm — plausible for InnoMaker) |
| FOV, 640×400 mode | 35.5° H × 22.6° V → **1.01 × 0.63 m at 1.575 m** |
| FOV, 320×200 mode | 18.2° H × 11.4° V → **0.505 × 0.315 m at 1.575 m** |
| **plate scale at the tee** | 28 px / 42.67 mm = **0.656 px/mm** |

**Confirming measurement (one capture, five minutes):** run `detect_reference_ball` on a real `320x200` frame. **~28 px confirms this reading; ~56 px would mean the `.crop` struct is right and I am wrong.** Everything downstream depends on this, so do it before spending money.

---

## 2. Commercial comparators

### 2.1 Rapsodo MLM2PRO — the closest analogue, and the most informative

| Finding | Grade |
|---|---|
| **"Impact Vision Camera — integrated high-speed camera captures 240 frames per second for a slow-motion view of your club path and contact point."** | **[MAN]** |
| **"Shot Vision Camera — this wide-angle 2K video camera captures swing videos with shot-tracer."** | **[MAN]** |
| **Placement: "approximately 6.5–8.5 feet directly behind the ball"** = **1.98–2.59 m** | **[MAN]** |
| Range mode needs ~30 yd of flight; net mode ≥ 8 ft. "Ensure you are hitting in a well-lit environment." | **[MAN]** |
| Ball must sit inside a constrained on-screen "Hitting Area" for the Impact Vision camera to see it | **[MAN]** |
| Ships Callaway **RPT Chrome Soft X**; app has a ball-type selector | **[MAN]** |
| **Two lens openings + an LED array of ~12 emitters in a ~30 × 32 mm grid** | **[FCC]** |
| **Two HEC 6800 µF / 35 V electrolytics (~13,600 µF) on one board; a second board with two more large caps and an FFC silkscreened "Flash Conn"** | **[FCC]** |
| Stored strobe energy ≈ ½CV² = 0.5 × 0.0136 × 35² ≈ **8.3 J** (≈6 J at a 30 V working point) | **[INF]** from the above |
| Spin comes from the **Impact Vision camera reading the RPT dot print at 240 fps**; claimed within 1 % of high-end monitors with RPT balls | **[PRESS]** |

**Two corrections to our own prior notes.** (a) We recorded MLM2PRO as "behind-ball 240 fps camer**as**", implying a stereo pair. It is **one** high-speed camera; the second is a wide-angle 2K *video* camera for tracer and alignment. (b) MLM2PRO is emphatically **not** an ambient-light system — the flash bank is unambiguous. That directly validates `camera-hardware-spec-v1.md` §4 and directly contradicts PR #215's ambient-only approach for anything needing frozen motion.

### 2.2 FlightScope Mevo Gen 2 — our stated competitive target

| Finding | Grade |
|---|---|
| Board silkscreened **"E27-MT426 MG2 Camera and LED Board"** | **[FCC]** |
| **One** camera module — a small square phone-class fixed-lens CMOS module, *not* a C/CS-mount machine-vision camera | **[FCC]** |
| LEDs on the **same** board (dark-epoxy packages in a row; dark epoxy is conventional for IR-pass) | **[FCC]** for presence, **[INF]** for IR |

The headline: **the competitive target's camera is a phone-class module, not a machine-vision camera.** Whatever Mevo Gen 2 does optically, it does with modest hardware plus illumination — consistent with the camera carrying impact-location and alignment while the radar carries kinematics.

### 2.3 PiTrac — the DIY precedent

| Finding | Grade |
|---|---|
| Uses the **Pi Global Shutter camera (IMX296, ~US$50)**, two Pi cameras in the build | **[PRESS]** |
| "High-speed, **infrared, strobe-based** image capture" | **[PRESS]** |
| **"a 100 watt, densely-packed LED array"** — described in a safety note about it staying on if Pi power fails | **[PRESS]** |
| Indoor-only by the project's own admission (carried over from our §1F(b)) | prior work |

**~100 W pulsed is the DIY scale marker.** Our `camera-hardware-spec-v1.md` §4 proposed ~10 × VSMA1085400 with **no energy-storage bank specified** — the emitter count is in the right area, but the spec omits the capacitor bank and pulsed driver that both real systems have. That is the actual gap in our design.

### 2.4 Trackman — resolved (2026-08-22)

| System | Camera(s) | Illumination | What the camera measures | Grade |
|---|---|---|---|---|
| **Trackman iO** (indoor) | **High-speed, up to 4,600 fps**, + a **separate patented alignment camera** | **Embedded IR, 810–850 nm**, "no external lighting required" | **3D spin (markerless — reads the ball surface, no stickers or special balls), impact location on the clubface, club orientation** | **[SPEC]** |
| **Trackman 4** (outdoor/tour) | **One** camera, HDR **720p @ 60 fps / 1080p @ 45 fps** | none stated (ambient) | **Alignment and swing video only — does not measure spin.** All ballistics from dual radar | **[SPEC]** |

> **CORRECTION (2026-08-22, prompted by a challenge from the user): Trackman iO is a CEILING-MOUNTED OVERHEAD unit, not a behind-ball one.** Vendor and reseller descriptions are consistent — "compact, ceiling-mounted design", "no minimum distance requirements in front of or behind the ball", and it is reviewed as an *overhead* launch monitor. I originally presented its 4,600 fps as a headline comparator for our geometry, which was wrong, and inconsistent with my own §2.4 reasoning that overhead/side systems (Uneekor, GCQuad) do not transfer.
>
> **From overhead at ~2.5 m the ball and clubhead pass nearly perpendicular to the optical axis:** the ball is large in frame, does not recede, and does not climb out of the field. That is a fundamentally easier optical problem than a down-the-line view of a ball flying away. **4,600 fps is the price of markerless spin FROM OVERHEAD; it is not a behind-ball number and does not sit on the same scale as our 468 fps.** The "spin ladder" framing below is retained only for the two genuinely behind-ball systems.
>
> The 4,600 fps figure itself is **verified** — verbatim from Trackman's tech-specs "Camera Sensors" section across two independent fetches, corroborated by several third-party resellers and reviewers.

> **SECOND CORRECTION (2026-08-22, again prompted by the user): Trackman 4's camera IS used for measurement — I had this wrong twice.** The claim "alignment and swing video only" below is withdrawn. Trackman's own support center (fetched via browser; the site blocks automated fetch) describes **OERT — Optically Enhanced Radar Tracking** — verbatim: *"Impact Location Without Markers: OERT can determine the exact point where the ball strikes the clubface without the need for any markers"*; *"Indoor Spin Adjustments: The OERT technology adjusts spin axis and spin rate measurements for indoor environments, accounting for the gear effect"*; plus higher club-data pickup (>90 %), chip/pitch/approach club data, and "4D silhouette clubhead tracking" (Trackman's own phrase, per their Twitter/X and blog). It is a TPS toggle: "Use internal camera for improved tracking (OERT)". **[SPEC/MAN]**

**How Trackman 4 actually does it — and why it matters enormously for us:**

- **The OERT camera is the built-in 720p @ 60 fps video camera.** Not a high-speed camera. At 60 fps a driver head travels ~0.7 m between frames — the camera cannot possibly track impact on its own. It works because the **dual radar provides impact timing at 40,000 samples/s** and the fusion runs "clubhead position in every frame — and in-between frames": radar carries kinematics and timing, the camera adds positional/angular constraints, and the silhouette model ties them together. **Impact location falls out of the fusion, not out of frame rate.**
- **The lighting is CONTINUOUS, not strobed.** Trackman's indoor lighting spec **[MAN]**: an ordinary non-flickering LED spotlight ~50 cm behind the tee giving **700–800 lux at the ball** (300 lux one metre behind it, 200 lux minimum ambient). That's bright-office-level light from a ~$100 track fixture. No flash bank, no IR array, for THIS job.
- **Face angle / spin axis:** the camera's impact location feeds a **gear-effect correction** to the radar's spin/face numbers — which is *exactly* the D-plane + camera-impact-location architecture our Stage 0D verdict arrived at independently. Trackman 4 is the commercial proof of our §1H conclusion.

**The read-across to OpenFlight is direct and favourable.** The OERT recipe needs: (1) precise radar impact timing — we have ~33 µs from the OPS 30 kHz I/Q buffer, better than needed; (2) a camera synchronized to it — we have one at **468 fps, ~8× Trackman's**, so where TM4 gets ~1 usable frame near impact we get ~8; (3) radar kinematics — OPS + IWR6843; (4) **700–800 lux continuous light indoors** — a cheap LED spot; outdoors, daylight exceeds this by an order of magnitude for free; (5) the fusion model — this is our `research/club_pose/` work, and it is the hard part Trackman spent its money on.

Note the wavelength: **810–850 nm**, matching §3.5's recommendation over 940 nm, and the band where OV9281 QE is 2× better.

### 2.5 FlightScope Mevo Gen 2 — the camera's actual job

| Finding | Grade |
|---|---|
| **Face Impact Location** is a paid add-on using "Fusion Tracking" (radar + camera) | **[PRESS]** |
| **Requires a minimum of 300 lux**, ideally consistent >300 lux within a 3-foot radius of the ball | **[PRESS]** |
| **The same built-in camera also records swing video** with data overlays | **[PRESS]** |
| Spin is **not** camera-derived — it comes from the radar (with a metallic ball sticker indoors) | prior work |

**This is the most important comparator finding for us.** Mevo Gen 2 delivers **face impact location from ONE phone-class camera module at 300 lux** — dim indoor lighting — while that same camera also shoots swing video. It does *not* attempt camera spin.

### 2.6 The pattern across all four systems

Every system separates **alignment/video** from **measurement**, and every camera that measures spin or impact carries **its own IR illumination**:

| System | Alignment/video | Measurement camera | Spin source |
|---|---|---|---|
| Trackman 4 | same camera, video mode | same 720p60 camera + **OERT fusion** (impact location, markerless, 700–800 lux continuous) | radar (gear-corrected by camera impact location) |
| Trackman iO *(overhead, not comparable)* | separate alignment camera | 4,600 fps IR | **camera, markerless** |
| MLM2PRO | Shot Vision 2K video | Impact Vision 240 fps + flash bank | **camera, RPT dot ball** |
| Mevo Gen 2 | same camera, video mode | same camera + board LEDs, ≥300 lux | radar + sticker |

**Restricted to the two genuinely behind-ball systems, the pattern is: nobody gets spin from ambient light, and nobody gets behind-ball spin without marking the ball.** Rapsodo pays an engineered dot ball plus a flash bank to get spin at 240 fps from 2.0–2.6 m behind. Mevo Gen 2 doesn't attempt camera spin at all and takes it from radar. Trackman's behind-ball product (Trackman 4) gets **impact location** from a mere 60 fps camera under continuous 700-800 lux light by fusing it with 40 kHz radar timing (OERT) — no strobe, no markers, no high frame rate. **Markerless behind-ball spin has no commercial precedent** — Trackman iO achieves markerless spin only by moving overhead and spending 4,600 fps (§2.4 correction).

### 2.5 The ball-pixel question

I could not obtain a directly measured ball-pixel figure for any commercial system; none publish it and the FCC photos don't show lenses in enough detail to derive focal length. What the manual *does* pin down is that MLM2PRO works at **2.0–2.6 m** — further than our 1.575 m — with a constrained hitting area. **[INF]** At that range, with a phone-class module, its ball image is very unlikely to exceed ~40–60 px, which puts it in the same regime as the §4 proposal below rather than in our 100–250 px aspiration.

---

## 3. Engineering analysis

### 3.1 A flaw in our own Stage 0E model (read this before trusting §1I)

`research/club_pose/sim/camera.py`:

```python
def scaled_intrinsics(factor: float) -> CameraIntrinsics:
    """Vary angular resolution at a fixed FOV (px/mm scales with `factor`) for the resolution sweep."""
    return CameraIntrinsics(
        fx=IMX296.fx * factor, fy=IMX296.fy * factor,
        cx=IMX296.cx * factor, cy=IMX296.cy * factor,
        width=int(round(IMX296.width * factor)), height=int(round(IMX296.height * factor)),
    )
```

`fx` and `width` scale **together**. The docstring says it outright: *"Vary angular resolution at a fixed FOV."* Stage 0E's `ball_px` axis therefore models **a sensor with more pixels behind the same lens** — never a longer lens.

Baseline: `IMX296` intrinsics `fx=4638, width=1456` → **FOV 17.8° H × 13.4° V**, held constant across the entire `ball_px` sweep (60 → 250). Camera at `(-1200, 0, 300)` mm aimed at impact → range 1237 mm → baseline ball 160 px; `_intrinsics_for_ball_px` then rescales to hit the requested `ball_px`.

Two consequences:

1. **§1I's failure attribution is wrong.** It states driver@240 fps fails "100 % by FOV loss (rising ball exits the tight **100 px lens** frame)". The frame is not tight *because of* the 100 px spec — FOV is identical at `ball_px=60` and `ball_px=250`. The ball exits because the model's FOV is a fixed 17.8° × 13.4° **aimed at the tee** while the ball climbs away. The mechanism is the **aim point**, not the plate scale.
2. **The real hardware trade is absent from the sim.** On a fixed sensor, buying plate scale costs field of view, which costs frames. The sim gets plate scale for free. So **0E's ≥100 px requirement is not a statement about lenses at all — it is a statement about pixel count.** Read correctly, it says: *put ~910 × 680 px across an 18° field*, which is a sensor spec.

This substantially weakens the case for "we need better optics" and strengthens the case for "we need a better readout mode", which is where §3.2 lands. It also removes the apparent contradiction with MLM2PRO: a 240 fps system with a *wider* field and a *smaller* ball image is a perfectly reachable operating point that 0E can represent (`ball_px=60`) but whose narrative dismissed for the wrong reason.

### 3.2 Can a mode change alone fix the plate scale? Yes — 2× for free

The shipped mode throws away half its resolution in each axis to subsampling (§1). Reading the **same optical field at 1:1** doubles the plate scale at no optical cost.

Sensor timing anchor **[SPEC]**: OV9281 does **1280×800 at 120 fps** on 2-lane MIPI. → frame time 8.33 ms over ≈822 rows → **row time ≈ 10.1 µs for a 1280-column row.** (Cross-check: the shipped 320×200 mode uses HTS=728 for an 816-column window at ~480 fps → 7.2 µs/row. Row time tracks the analog window width, as expected.)

A **native 1280 × 200** readout:

| | |
|---|---|
| rows per frame | 200 + 22 blanking = 222 |
| frame time | 222 × 10.1 µs = **2.24 ms** |
| **frame rate** | **≈ 446 fps** |
| MIPI load (10-bit) | 1280 × 200 × 10 × 446 = **1.14 Gbps** vs 1.6 Gbps available → **fits** (0.91 Gbps at 8-bit) |
| focal_px at 1:1 (f = 6.0 mm, 3.0 µm) | 2000 px |
| **ball at 1.5 m** | 2000 × 42.67 / 1500 = **≈ 57 px** |
| plate scale | **1.33 px/mm** (2× the shipped 0.656) |
| FOV | 1280 × 3 µm = 3.84 mm → **35.5°**; 200 × 3 µm = 0.6 mm → **5.7°** |

**2.24 ms is within a whisker of 0E's 2 ms requirement, and 57 px sits on 0E's `ball_px=60` cell:** driver axis **2.39°**, iron **1.02°**, wedge **1.36°** — clearing the **3° display tier** and the **5° driver-face gate**, missing the 2° tier.

**The catch, and the fix: orientation.** A 5.7° vertical field is only 0.157 m tall at 1.5 m; a ball climbing at 15° exits in ~2 frames. But the cheap ROI axis on this sensor is *rows*, and the ball's image motion from directly behind is dominated by **climb** (vertical), not lateral travel.

> **Rotate the camera 90°.** Mount it portrait so the sensor's 1280-pixel *column* axis runs vertically in the world and the 200-pixel *row* axis runs horizontally. You then get **35.5° of vertical coverage (~0.96 m at 1.5 m) along the flight corridor** and 5.7° across it — exactly the right ROI shape for a ball that climbs and recedes, and the frame-rate saving comes out of the axis we don't need.

This costs a mount rotation and a driver mode. It is the highest-leverage, lowest-cost move available.

### 3.3 What a 100–250 px ball would actually require

Read as a pixel-count spec (§3.1): ~**910 × 680 px across an 18° field** for 100 px, ~2.5× that for 250 px. Against the OV9281's 1280 × 800:

- 100 px is reachable *only* by giving up field in the other axis, which the portrait trick already exploits. Ball ≈ 57 px is what 1:1 readout yields at f = 6 mm; **100 px needs f ≈ 10.5 mm at 1:1**, narrowing the corridor axis to ~20° (0.55 m at 1.5 m) — still workable in portrait, but the frames-in-view margin shrinks and the light budget worsens by (100/57)² = 3.1×.
> **CORRECTION (2026-08-22): impact location is NOT gated by plate scale.** An earlier reading of this document treated 0C's "~450 px head / 4 px/mm" as a hard requirement. Re-reading `RESULTS_0C.md`, 0C's own stated gating requirements are **correlated centroid/glare bias in PIXELS (<2 px), sync ≤100 µs, and ball depth** — *"correlated bias control is the gating optical requirement."* Plate scale enters only as the px→mm multiplier: at 1.33 px/mm instead of 4 px/mm, a 2 px bias costs 1.5 mm instead of 0.5 mm. Meanwhile the two dominant terms both *improve* on our hardware — sync goes to ~33 µs via `ops_impact_finder.py` (§3.6, better than 0C's 100 µs baseline) and ball depth goes to ~3 mm via the radar range sphere (§3.7). **Mevo Gen 2 ships face impact location from one phone-class camera at 300 lux (§2.5), which is direct evidence the requirement is far softer than 4 px/mm.** The free experiment that settles it: re-run the 0C budget at 1.33 px/mm with radar depth and 33 µs sync. Until then, treat impact location as **plausible on the improved single camera**, not out of reach.

- **150–250 px (the 0.3–0.8° tier for SPIN) is out of reach on a 1 MP sensor.** That needs ~2000+ px along the corridor axis → **AR0234 (1920 × 1200 GS mono, 3 µm; Arducam sells Pi modules)** or a machine-vision GS camera. This is the only finding that justifies buying a different sensor, and it buys the 2° tier, not basic function.

### 3.4 Motion blur vs exposure

| Configuration | ball @ 67 m/s | clubhead @ 40 m/s |
|---|---|---|
| shipped: 500 µs, 0.656 px/mm | 33.5 mm → **22 px** | 20 mm → **13 px** |
| proposed: 500 µs, 1.33 px/mm | 33.5 mm → **45 px** | 20 mm → **27 px** |
| proposed: 20 µs, 1.33 px/mm | 1.34 mm → **1.8 px** | 0.8 mm → **1.1 px** |
| to hit 0E's ≤ 1 px limb fit at 1.33 px/mm | **≤ 11 µs** | ≤ 19 µs |

**0E's ≤1 px limb-center requirement forces ~10 µs, not 20 µs**, at the proposed plate scale. Note this cuts the other way too: the shipped 500 µs exposure is *fine* for PR #215's own job — a symmetric smear still has an unbiased centroid, which is all a velocity estimate needs. It is only fatal for shape work.

### 3.5 Light budget — a strobe is mandatory, but it is not exotic

Relative to the working shipped configuration (500 µs, 28 px ball, gain 2, outdoors):

- exposure 500 → 20 µs: **25× less light**
- magnification 28 → 57 px: light spread over (57/28)² = **4.1× more pixels**
- **net ≈ 102× less signal per pixel**

No aperture change recovers that (~6.7 stops). **Ambient light cannot reach a 20 µs exposure, indoors or outdoors.** Confirmed independently by both comparators being strobed.

Sizing, from first principles:

- **Outdoors** the strobe must out-compete solar irradiance *within the pulse*. Full sun ≈ 1000 W/m². For ~3× contrast over a ball-and-surround patch of ~0.05 m²: 3000 W/m² × 0.05 m² ≈ **150 W optical**, ≈ **400 W electrical** at ~35 % wall-plug — for 20 µs, i.e. **~8 mJ per pulse**. The hard requirement is **peak current**, not energy, which is precisely why both commercial designs carry electrolytic banks.
- **Indoors** ambient is negligible; the strobe only needs a usable SNR. PiTrac's ~100 W array works at ~0.5 m **[PRESS]**; inverse-square to 1.5 m is 9× → few-hundred watts pulsed. Same hardware.
- **Cross-check:** ~8 mJ/pulse against MLM2PRO's ~8 J stored **[FCC]** leaves a factor of ~1000 — ample margin for multi-pulse bursts, longer range (2.0–2.6 m), recharge headroom and conservative derating. The numbers are mutually consistent.

**Wavelength.** OV9281 response **[SPEC]**: **13000 mV/µW·cm⁻²·s at 850 nm vs 6500 at 940 nm — exactly 2× in favour of 850 nm.** Combined with lower 940 nm LED output, 940 nm costs ~2–3× total (matching our §4 estimate) and buys solar-dip rejection. **Recommendation: 850 nm for the ball path** (dark dots on white are contrast-based and sunlight-tolerant), **940 nm narrowband reserved for the retroreflective marker path** where sun glint is the flagged risk.

**Correction to our own spec:** `camera-hardware-spec-v1.md` §4's emitter count is about right; what it omits — **the capacitor bank and pulsed constant-current driver** — is the part that actually makes a strobe a strobe. Both comparators have one; our spec doesn't mention one.

### 3.6 Sync

PR #215 never uses the sensor's external trigger — it freezes a software ring on a GPIO edge and then finds impact *visually*, giving ±1 frame ≈ **2.14 ms**. Against 0C's 100 µs baseline that is 21× over, and at 40 m/s it is **86 mm of clubhead travel** — fatal for impact location, tolerable for velocity fits over multiple intervals.

| Route | Expected accuracy | Cost |
|---|---|---|
| current: nearest-frame visual impact | ~2.14 ms | — |
| **sub-frame fit of ball departure** (back-extrapolate the post-impact track to the tee position) | ~0.1–0.3 frame ≈ **200–600 µs** | software only |
| **`scripts/analysis/ops_impact_finder.py`** — localize impact in the 30 kHz OPS I/Q buffer, map to camera frame timestamps | **~33 µs** (already implemented for K-LD7); camera stores both `trigger_epoch` (`time.time()`) and per-frame monotonic ns, so the clock domains reconcile | software only |
| hardware XTR trigger on the sensor | sub-µs | 1.8 V level shift + MCU |

**The I/Q route meets 0C's 100 µs requirement in software, on hardware we already own.** The XTR/Pico genlock in `camera-hardware-spec-v1.md` §5 is **not needed** — a real simplification, and it retires enclosure requirement F18.

### 3.7 Depth — does radar range replace stereo?

From `config/iwr6843_l3dump_dense_36f2ms_53bin_iq8.cfg`, `profileCfg 0 60.0 7 3 38 0 0 100 1 128 4000 0 0 30`:

- slope **100 MHz/µs**, **128** ADC samples at **4000 ksps** → ADC window 32 µs
- valid sweep bandwidth **B = 3.2 GHz** → **range resolution = c/2B = 4.69 cm**
- max unambiguous range = c·Fs/2S = **6.0 m**
- λ at 60 GHz = 5 mm → phase-derived range change is sub-mm but **ambiguous every λ/2 = 2.5 mm**

Range *noise* for a well-resolved target at ~20 dB SNR ≈ ΔR/√(2·SNR) ≈ 46.9 mm / 14.1 ≈ **3.3 mm** — genuinely stereo-class (0C stereo baseline is 3 mm; mono is 15 mm).

**But noise is not the binding term.** Radar reports range to the **dominant scattering centre**, not to the geometric feature the camera tracks. For a **golf ball** — a smooth sphere, single well-behaved scatterer — that centre is stable and radar depth should be excellent. For a **clubhead** — an extended, rapidly rotating multi-scatterer — the phase centre wanders across the head as it rotates. That is a **bias**, not noise; it does not average down, and it is plausibly **10–40 mm [INF]**.

**Verdict on depth:** radar-range depth is a **strong replacement for stereo on the ball** (so the camera-only ball-depth fallback in `ball_flight.py` should be preferred over apparent-size whenever IWR range is available), and a **weak one on the clubhead**, which is exactly where 0C wanted stereo's 3 mm. So:

- **Stereo is NOT needed for ball work** → the whole spin path is mono. Real saving.
- **Stereo remains the open question for club pose**, and the deciding experiment is cheap: re-run the 0C budget with a `radar_depth_mm` resolver at σ = 3 mm noise **plus a 10–40 mm bias term** and see whether the pro-mode impact/face numbers survive. Do this before buying a second camera.

---

## 4. Architecture verdict

**Option (A) with a caveat — one camera, re-moded, re-oriented, and lit.** Not (B), (C) or (D) for basic function.

### 4.1 The recommendation

| Element | Decision |
|---|---|
| **Sensor** | **Keep the OV9281.** No change. |
| **Readout** | Add a **native 1:1 `1280×200` mode** (no 2× subsample) → **~446 fps / 2.24 ms**, ball ≈ 57 px, 1.33 px/mm |
| **Orientation** | **Rotate the camera 90° (portrait)** so the 1280-px axis runs vertically along the flight corridor |
| **Lens** | Keep the 6 mm M12. Hold a ~10.5 mm for the 100 px experiment |
| **Illumination** | **850 nm pulsed IR array + capacitor bank + pulsed constant-current driver**, ~10–20 emitters, ~400 W electrical for 10–20 µs |
| **Filter** | 850 nm longpass for the ball path; reserve a 940 nm narrowband for the retroreflective marker path |
| **Exposure** | **~10 µs** (§3.4 — tighter than our previous 20 µs figure) |
| **Sync** | Software: `ops_impact_finder.py` I/Q localization → ~33 µs. **No XTR, no Pico.** Retire enclosure F18 |
| **Depth** | Radar range sphere for the ball; stereo decision for club pose deferred to the 0C re-run |
| **Expected result** | 0E `ball_px=60` cell: driver axis **2.39°**, iron **1.02°**, wedge **1.36°** → clears the 3° display tier and the 5° driver-face gate |

### 4.2 BOM (Phase 1 — the only spend that is justified now)

| Item | Part | ~Price |
|---|---|---|
| IR emitters ×12 | Vishay **VSMA1085400** 850 nm, 5 A pulsed | $25–35 |
| Gate driver | **MCP1407** | $2 |
| Switch | **IRLU024N** MOSFET | $2 |
| **Energy storage** | 2 × 6800 µF / 35 V low-ESR electrolytic (mirrors the MLM2PRO bank) | $10–15 |
| Pulse timing | Existing Pi GPIO, or an RP2040 if jitter demands it | $0–4 |
| Filter | 850 nm longpass, M12 | $10–20 |
| Dot balls | stamp/stencil, or RCT/RPT | $10–30 |
| **Phase 1 total** | | **$60–110** |

Down from the old spec's $140–230, because the camera, lens and trigger electronics are no longer purchases.

**Deferred (do not buy yet):** AR0234 1920×1200 GS module (~$60–100) — *only* if the 2° tier is required after Phase 1. 940 nm narrowband filter + emitters — only for the marker path. Second camera for stereo — only if the 0C radar-depth re-run says clubhead depth needs it.

### 4.3 The falsifying bench test

Run in this order; each gate kills the next if it fails.

1. **Gate 0 — plate scale (free, 5 min).** `detect_reference_ball` on one real 320×200 capture. **~28 px → §1 confirmed. ~56 px → §1 wrong, redo this document.**
2. **Gate 1 — the native mode (free, code only).** Add the `1280×200` 1:1 mode to the kernel patch. **Measure delivered fps and frame gaps.** *Pass: ≥ 400 fps sustained, gaps = 0. Fail: row-time model wrong → the whole (A) verdict collapses to (C), buy the AR0234.* **This is the single highest-risk assumption in the document** — it rests on a row-time figure inferred from the 120 fps full-res spec, not on a datasheet HTS table.
3. **Gate 2 — portrait framing (free).** Rotate the camera 90°, aim up the corridor. **Count usable ball frames across driver / iron / wedge.** *Pass: ≥ 3 frames with the ball fully in view.*
4. **Gate 3 — strobe (the $60–110).** Build the array. **Measure limb-center fit residual at 10 µs, indoors then in direct sun.** *Pass: ≤ 1 px.* This is the gate that decides whether spin is real.
5. **Gate 4 — sync (free).** Wire `ops_impact_finder.py` to camera frame timestamps; verify camera-frame ↔ impact ≤ 100 µs end to end.
6. **Gate 5 — the 0C radar-depth re-run (free).** Add a `radar_depth_mm` resolver with 3 mm noise + a 10–40 mm bias sweep. **Decides the second camera.**

Gates 0, 1, 2, 4 and 5 cost nothing. **Do all five before spending anything.**

---

## 5. What would change the verdict

- **Gate 0 returns ~56 px.** Then the `.crop` struct is right, the shipped plate scale is already 1.3 px/mm, the 1:1 trick is already spent, and reaching 57 px needs a lens change instead. Most of §3.2 would need redoing.
- **Gate 1 fails to sustain ~400 fps at 1280 wide.** The row-time inference (§3.2) is the weakest link. If native readout lands at ~270 fps (3.7 ms) instead, the driver case fails 0E's wrap gate and the answer flips to **(C): buy the AR0234**.
- **The 0C radar-depth re-run shows clubhead phase-centre bias dominating.** Then stereo returns for the pro-mode pose path — but *not* for spin.
- **The 2° display tier becomes a requirement** rather than the 3° tier. That alone forces a bigger sensor; nothing about the OV9281 reaches 150–250 px.
- **Trackman / Uneekor / GCQuad evidence** (§2.4, unresolved) could reveal an architecture that changes the vantage or the division of labour. Lower probability — none of them share our behind-ball, radar-primary geometry.

---

## 6. Sources

**Primary — FCC equipment-authorization internal photos** (local copies in the session scratchpad): Rapsodo MLM2PRO internal views (report SHATBL2410021) — camera/LED assembly, flash capacitor banks, "Flash Conn"; FlightScope Mevo Gen 2 internal views — "E27-MT426 MG2 Camera and LED Board".

**Primary — manufacturer manual:** Rapsodo MLM2PRO user manual (device overview, set-up distances, alignment/hitting area, ball types).

**Datasheets / vendor:**
- [OmniVision OV9281 product page](https://www.ovt.com/products/ov9281/) and [OV9281 product brief v1.4](https://www.ovt.com/wp-content/uploads/2024/05/OV9281-PB-v1.4-WEB.pdf) — 1280×800 @ 120 fps, 3 µm global shutter, ROI/windowing, NIR response 13000 (850 nm) / 6500 (940 nm) mV/µW·cm⁻²·s
- [Arducam OV9281 module datasheet](https://blog.arducam.com/downloads/modules/RaspberryPi_camera/OV9281_MIPI_Camera_Module_DS_v2.pdf) · [Arducam OV9281 wiki](https://docs.arducam.com/Raspberry-Pi-Camera/Native-camera/Global-Shutter/1MP-OV9281-OV9282/)

**PiTrac:**
- [PiTrac on Hackaday.io](https://hackaday.io/project/195042-pitrac-the-diy-golf-launch-monitor) — Pi Global Shutter camera, IR strobe-based capture, "100 watt, densely-packed LED array"
- [PiTrac documentation](https://docs.pitrac.org/) · [PiTrac GitHub](https://github.com/PiTracLM/PiTrac)

**MLM2PRO spin method:**
- [MyGolfSpy — Rapsodo MLM2PRO takes spin measurement to pro level](https://mygolfspy.com/news-opinion/rapsodo-mlm2pro-takes-spin-measurement-to-pro-level/)
- [Rapsodo MLM2PRO product page](https://rapsodo.com/products/mlm2pro-mobile-launch-monitor-golf-simulator)
- [My Golf Simulator — what data does the MLM2PRO measure](https://mygolfsimulator.com/rapsodo-mlm2pro-data-what-data-does-the-mlm2pro-measure/)

**Repo (read directly):** `drivers/ov9281/ov9282-high-speed.patch`, `src/openflight/camera/{club_delivery,ball_flight,club_motion}.py`, `docs/camera/README.md`, `config/iwr6843_l3dump_dense_36f2ms_53bin_iq8.cfg`, `research/club_pose/sim/camera.py`, `research/ball_spin/{budget,detect}.py`, `research/ball_spin/RESULTS_0E.md`, `research/club_pose/sim/RESULTS_0C.md`, `scripts/analysis/ops_impact_finder.py`.
