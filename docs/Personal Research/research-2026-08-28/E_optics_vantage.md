# E — Optics & vantage for the OV9281 behind-ball camera, no strobe

> Scope: single InnoMaker CAM-MIPI9281RAW-V2 (OV9281, 1280×800 GS, 3.0 µm, fixed 2.8 mm M12 f/2.2
> lens), no strobe (hard constraint), behind-ball placement as currently built. Tags:
> **[CONFIRMED]** primary source (datasheet/driver/repo), **[PRIOR]** already settled in a prior
> research doc and reused here without re-deriving, **[DERIVED]** arithmetic done in this doc from
> confirmed inputs, **[INFERRED]** reasoned but not measured, **[UNKNOWN]** open, needs a bench test.

## 0. Verdict, in six sentences

**Readout:** the shipped driver has *no* 1:1 high-speed mode — 640×400, 640×200, 320×200 all
2×2-subsample the same field, so they are optically identical (0.295 px/mm) and differ only in
crop width and frame rate; the only 1:1 option today is the **stock, unpatched 1280×800 mode**
(no kernel patch needed), which roughly doubles plate scale for a light-budget cost of **~15×**.
**Lens:** swapping the fixed 2.8 mm M12 lens (~$15, screw-mount) for 4/6/8 mm buys more plate
scale but costs light geometrically (18×/27×/52× vs today) and field of view (2.17 m → 1.5/1.0/0.76 m);
without a strobe, 6 mm is borderline and 8 mm is not realistic indoors. **Vantage:** behind-ball is
provably right for transverse position, lie/shaft direction and (with radar depth) impact
*translation*, and provably wrong for face angle and dynamic loft — both are rotations about axes
the camera stares straight down, a geometric degeneracy no amount of resolution fixes.
**Repositioning within one behind-ball enclosure** (height, lateral offset, look-down tilt) converts
that degeneracy from exactly-zero to weakly non-zero (**≤0.4 px per 5° of rotation** even at an
aggressive 300–500 mm reposition) — real, but not a rescue. **A second camera in the same
enclosure** (stereo baseline ≤250 mm) buys real depth precision on the clubhead (radar's weak
spot) but does *not* fix face/loft, because its two views are still only ~4–9° off-axis from each
other — the same weak-tilt regime as one camera moved sideways. **A second camera face-on
(side vantage), on a cable,** is the only option analyzed here that removes the degeneracy
outright, because it makes the face normal roughly perpendicular to the viewing ray instead of
parallel to it — that is the vantage every commercial system that measures face/loft optically
(GCQuad, Uneekor, Garmin R50) actually uses, and behind-ball systems (Trackman, Mevo) that
don't use it get face/loft from radar instead.

---

## 1. OV9281 readout modes actually available

### 1.1 The chip itself

**[CONFIRMED, datasheet]** 1280×800 active array, 3.0 µm square pixels, global shutter, up to
**120 fps at full 1280×800** over 2-lane MIPI.
Sources: [OmniVision OV9281 product page](https://www.ovt.com/products/ov9281/),
[OV9281 product brief v1.4 (PDF)](https://www.ovt.com/wp-content/uploads/2024/05/OV9281-PB-v1.4-WEB.pdf);
[Arducam OV9281 module datasheet (PDF)](https://blog.arducam.com/downloads/modules/RaspberryPi_camera/OV9281_MIPI_Camera_Module_DS_v2.pdf),
[Arducam OV9281 wiki](https://docs.arducam.com/Raspberry-Pi-Camera/Native-camera/Global-Shutter/1MP-OV9281-OV9282/). **[PRIOR]**

### 1.2 What the repo's driver actually exposes — read directly from the patch

`drivers/ov9281/ov9282-high-speed.patch` (a narrow patch on the Raspberry Pi upstream `ov9282.c`
driver) defines the mode table this camera runs under. Read directly, not inferred:

| Mode index | Resolution | Source | `X_INC`/`Y_INC` (0x3814/0x3815) | Subsample? |
|---|---|---|---|---|
| `MODE_1280_800` (0) | 1280×800 | **stock driver, unpatched** | not touched by the patch | **1:1 — full native resolution** [INFERRED: it is the array-sized baseline mode; not directly shown in the diff, which only touches the *new* modes] |
| `MODE_1280_720` (1) | 1280×720 | stock driver, unpatched | not touched | **1:1**, row-cropped only [INFERRED, same basis] |
| `MODE_640_400` (2) | 640×400 | stock driver, unpatched (patch only *appends* register writes to it) | not independently re-derived here | **2×2 subsample of the full field** — this is not this document's finding, it is `camera-feasibility-verdict-2026-08.md` §1's settled register analysis, reused as **[PRIOR]** rather than re-derived, because the raw diff hunk touching this array shows only appended lines, not the full existing table |
| `MODE_640_200` (3) — **added by this patch** | 640×200 | patch, `mode_640x200_regs` | **`{0x3814,0x22}`, `{0x3815,0x22}`** — confirmed in the patch text | **2×2 subsample**, vertically cropped to a centered 400-row window |
| `MODE_640_100` (4) — **added** | 640×100 | patch, shares the 640×200 register block below the diff's line 96 marker | same `0x22/0x22` region | **2×2 subsample**, 200-row window |
| `MODE_320_200` (5) — **added** | 320×200 (**the shipped default**) | patch, `mode_320x200_regs` | **`{0x3814,0x22}`, `{0x3815,0x22}`** — confirmed explicitly in the patch text | **2×2 subsample**, `HTS=728`/`VTS=288` = InnoMaker's proven ~480 fps timing |

**[CONFIRMED from the patch file itself]** All three modes this repo added (`640×200`, `640×100`,
`320×200`) use identical `X_INC`/`Y_INC = 0x22` register values — the same 2×2 subsample as the
stock `640×400` mode. **None of the four non-native modes preserve full angular resolution.**
Only `MODE_1280_800` and `MODE_1280_720` do, and neither has ever been requested by
`CameraCaptureSettings` in this codebase (`src/openflight/camera/capture_runtime.py` defaults to
640×400 and every documented `start-kiosk.sh` invocation in `docs/camera/README.md` uses
320×200). **Getting to 1:1 today needs no new driver code — it needs requesting the mode the
driver already ships** (`main`/`raw` size `1280×800` or `1280×720` in `create_video_configuration`,
per `capture_runtime.py:206-208`, which passes `width`/`height` straight through to Picamera2).
A genuine 1:1 *strip* mode (e.g. 1280×200, the "portrait ROI" idea in the prior verdict doc) does
**not exist yet** — it would need a new register table with `X_INC/Y_INC=0x11`, which is unbuilt.
**[CONFIRMED code path / INFERRED for the unbuilt mode]**

### 1.3 Measured fps per mode, and what that implies for the exposure ceiling

`docs/camera/README.md` "Capture Modes" table, measured on the test Pi 5 **[CONFIRMED, repo docs]**:

| Mode | Measured cadence | Frame period | Exposure ceiling (≈ period − blanking) |
|---|---:|---:|---:|
| 640×400 | ~288 fps | 3.47 ms | ~3.4 ms |
| 640×200 | ~536 fps | 1.87 ms | ~1.8 ms |
| 320×200 | ~576 fps (450 fps requested, ~468 fps delivered outdoors) | 1.74 ms (2.14 ms as run) | ~1.7–2.1 ms |
| 640×100 | not recommended — "vertically fragile" | — | — |
| 1280×800 | **not measured in this repo** — datasheet ceiling 120 fps | 8.3 ms | ~8.2 ms |
| 1280×720 | not measured | ≥8.3 ms (fewer rows, so somewhat faster) [INFERRED] | similarly generous |

**The exposure ceiling is never the binding constraint at any of these frame rates** — every
mode's ceiling is one to two orders of magnitude above the exposures this document's light budget
actually reaches (tens to a few hundred µs). This matches the prior finding that frame rate
buys nothing for blur **[PRIOR, camera-feasibility-verdict §0.6.3]**: the binding constraint is
always *how little light lets you use a short-enough exposure*, never how short the sensor's
timing table permits — so §2 below is the real readout-mode decision, not this table.

**[UNKNOWN — flag for the bench test]** The prompt's working figure of "~144 fps" for 1280×800
exceeds the datasheet's 120 fps spec for that mode; the Pi 5's specific MIPI/ISP throughput may or
may not sustain higher than the OmniVision reference number. Treat 144 fps as a target to be
measured, not a settled fact — Gate 1 in §4 below measures it directly.

---

## 2. Light-budget table

Anchor: today's shipped 320×200/640×400 configuration achieves **247–298 µs exposure at gain
4–5** (measured facts) with the stock 2.8 mm f/2.2 lens — call the midpoint **270 µs, f/2.2**
the reference point. Clubhead speed is derived directly from the measured facts rather than
assumed: **77 mm/frame ÷ 2.1385 ms/frame (467.6 fps) = 36.0 m/s**, and this reproduces the
measured ~3 px blur at ~270 µs exposure and 0.295 px/mm almost exactly (36.0 m/s × 270 µs =
9.72 mm = 2.87 px) — an internal consistency check that the reference numbers are self-coherent.
**[DERIVED from measured facts]**

### 2.1 Plate scale, ball/clubhead size, field of view

Effective pixel pitch: 3.0 µm native, 6.0 µm at 2×2 subsample. `fx_px = focal_mm × 1000 / pitch_µm`.
Range Z = 1580 mm (the tape-measured camera-to-ball distance carried in
`openflight-real-capture-metadata` / the falsification doc's `CAMERA_BALL_RANGE_MM = 1581.0`
**[PRIOR]**). Ball diameter 42.67 mm; clubhead reference length taken as 102 mm (back-solved from
the given "clubhead ~30 px at today's plate scale" fact: 30 px ÷ 0.2954 px/mm = 101.6 mm — used
consistently below, not re-measured per mode). Field of view width = (sensor output columns used) ÷
px/mm; 320×200 uses only 320 of the 640 output columns the sensor width maps to, so its field is
half that of the full-width modes.

| Candidate | Readout | Lens | `fx` (px) | px/mm @ 1580 mm | Ball (px) | Clubhead (px) | FOV width (mm) |
|---|---|---|---:|---:|---:|---:|---:|
| **320×200 (shipped)** | 2×2 sub | 2.8 mm | 466.7 | **0.2954** | 12.6 | 30.1 | 1083 |
| 640×400 | 2×2 sub | 2.8 mm | 466.7 | **0.2954** (identical — see §1.2) | 12.6 | 30.1 | 2166 |
| **1280×800 (stock 1:1)** | **1:1, no patch needed** | 2.8 mm | 933.3 | **0.5907** | 25.2 | 60.3 | 2166 |
| 1280×800 1:1 | 1:1 | +4 mm | 1333.3 | 0.8438 | 36.0 | 86.1 | 1517 |
| 1280×800 1:1 | 1:1 | +6 mm | 2000.0 | 1.2658 | 54.0 | 129.1 | 1011 |
| 1280×800 1:1 | 1:1 | +8 mm | 2666.7 | 1.6879 | 72.0 | 172.2 | 758 |

**Note the corrected entry: 640×400 is NOT a 1:1 crop.** It is the same 2×2-subsampled field as
320×200, just wider — a point already settled in the prior verdict doc (§0.6.2, "640×400 is a
trap") and reproduced here directly from the patch registers in §1.2. If the task brief's premise
of "640×400 1:1 crop" was assumed anywhere downstream, that assumption is wrong; flagging it here
rather than quietly using the corrected number. **[PRIOR, re-confirmed against the patch file]**

### 2.2 Motion blur and the exposure it forces

Blur (mm) = 36.0 m/s × exposure (µs) × 0.001; blur (px) = blur (mm) × px/mm. Target: ≤1.5 px
(half the shipped-mode blur, a reasonable shape-fidelity bar consistent with 0E's ≤1 px limb-fit
requirement plus margin). **[DERIVED]**

| Candidate | Blur @ 300 µs (px) | Exposure for ≤1.5 px blur (µs) |
|---|---:|---:|
| 320×200 / 640×400 (2.8 mm, 2×sub) | 3.19 | **141.0** |
| 1280×800 1:1, 2.8 mm | 6.38 | **70.5** |
| 1280×800 1:1, +4 mm | 9.11 | **49.4** |
| 1280×800 1:1, +6 mm | 13.67 | **32.9** |
| 1280×800 1:1, +8 mm | 18.23 | **24.7** |

(Cross-check: at today's real 270 µs exposure, 320×200 blur = 3.24 px ≈ the measured "~3 px" —
consistent with §0's anchor.)

### 2.3 Light multiplier vs today's bay

Photon count per pixel scales as `exposure × aperture_area × pixel_area`, i.e.
`∝ t / N² × pitch²` where `N` is f-number. Holding SNR constant when moving to a shorter,
blur-limited exposure on a smaller effective pixel:

```
required_lux_ratio = (t_ref / t_target) × (N_target / N_ref)² × (pitch_ref / pitch_target)²
```

Reference: `t_ref = 270 µs`, `N_ref = 2.2` (2.8 mm lens, **[CONFIRMED datasheet]** f/2.2),
`pitch_ref = 6.0 µm` (2×sub). **f-numbers for the 4/6/8 mm lenses are [INFERRED — typical M12
board-lens catalog values (Foctek/Sunex/LensationDS-class, ~f/1.8–2.5 across this focal range),
NOT the specific vendor's datasheet** — confirm the actual purchased lens's f-number before
trusting the multiplier below to better than ±30%.

| Candidate | `t_target` (µs) | `N` (assumed) | `pitch` (µm) | **Light multiplier vs today** |
|---|---:|---:|---:|---:|
| 320×200 / 640×400 (today) | 270 | 2.2 | 6.0 | **1×** (reference) |
| 1280×800 1:1, 2.8 mm | 70.5 | 2.2 | 3.0 | **≈15×** |
| 1280×800 1:1, +4 mm | 49.4 | ~2.0 (est.) | 3.0 | **≈18×** |
| 1280×800 1:1, +6 mm | 32.9 | ~2.0 (est.) | 3.0 | **≈27×** |
| 1280×800 1:1, +8 mm | 24.7 | ~2.4 (est.) | 3.0 | **≈52×** |

**Reading it honestly:** today's bay is already reported at gain 4–5 with (per the separately
recorded falsification-doc finding) *no analogue-gain headroom left* — so every one of these
candidates needs genuinely more incident light, not just a settings change. If today's ambient is
in the ~300–500 lux range typical of an indoor bay with one directional lamp (**[UNKNOWN]** — not
measured; the falsification doc found a bright near-field light of unexplained provenance was on),
then:
- **1280×800 1:1 stock lens (≈15×)** needs roughly **4,500–7,500 lux** — bright but achievable
  with one or two strong continuous LED work lights aimed into the corridor, well under Trackman
  4's 700–800 lux only because that comparator runs at a much slower shutter/coarser blur
  tolerance; ours is short-exposure so it needs more, not less, than Trackman's own number.
- **+6 mm (≈27×)** needs roughly **8,000–14,000 lux** — borderline; near or above what continuous
  LED panels can put on a corridor-sized area without a dedicated fixture.
- **+8 mm (≈52×)** needs roughly **16,000–26,000 lux** — approaching full outdoor daylight,
  **not realistic indoors without a strobe**, which is excluded by this task's hard constraint.

**Conclusion: without a strobe, `1280×800` 1:1 at the stock 2.8 mm lens is the practical ceiling.**
Longer lenses trade FOV and light budget for plate scale in a regime this constraint cannot
comfortably afford; 6 mm is a stretch worth bench-testing, 8 mm is not worth trying under "no
strobe." **[DERIVED, with the f-number caveat above]**

---

## 3. Vantage

### 3.1 What behind-ball fundamentally sees — the rotation-axis argument

**[PRIOR, established across `markerless-club-data-guide-v2` §1C/§1D and
`silhouette-cannot-see-orientation`]**: from directly behind the ball, the club's face normal
points almost exactly along the camera's optical axis. A rotation of a rigid body about an axis
the camera is staring down projects to **zero first-order image motion** — only a second-order
foreshortening (measured: 5° of face angle → ~0.4% silhouette change, i.e. ≈0 px). This is a
geometric fact about the vantage, not a resolution or algorithm limitation, and it is why:
- **Face angle** (yaw, rotation about a near-vertical axis roughly parallel to the viewing ray) and
- **dynamic loft** (pitch, rotation about a near-horizontal axis also roughly parallel to the
  viewing ray for a face pointed downrange)

are both **degenerate** from behind-ball, while:
- **transverse position** (ball departure line, horizontal launch direction, toe-heel/high-low
  translation of a contact point given a known face pose) and
- **lie/shaft direction** (the shaft is a ~1 m lever arm at a real angle to the viewing ray, so its
  orientation moves plainly in the image)

are **not** degenerate — they are ordinary translations or off-axis rotations, and this is exactly
the OV9281's job today (club path, attack angle, horizontal launch, ball self-calibration) and
what it should keep doing.

### 3.2 The projection-geometry derivation for camera repositioning

Set world axes: `x` = downrange (nominal camera boresight), `y` = lateral, `z` = up. A feature at
head-radius `r` from the rotation center, under a small rotation `δ` (radians) about `y` (loft) or
`z` (face angle), moves by `ΔP ≈ (−rδ, 0, 0)` — i.e. **along the boresight**, which a camera
looking straight down `x` cannot see (only depth/magnification changes, second order). Tilting the
camera boresight by an angle `β` (look-down, for loft) or `γ` (lateral yaw, for face angle)
rotates the local image axes so that a fraction `sin(β)` (or `sin(γ)`) of that boresight-aligned
motion becomes transverse, and therefore visible:

```
Δpx/° ≈ (fx_px × r_mm × sin(tilt)) / Z_mm × (π/180)
```

Using `fx = 466.7 px`, `Z = 1580 mm`, and `r ≈ 45–50 mm` (a driver's toe-heel or front-back
half-extent — the lever arm a real feature/marker would sit at):

| Reposition | Induced tilt angle | `sin(tilt)` | Δpx per 5° of rotation |
|---|---:|---:|---:|
| Raise camera 100 mm (look-down) | β = atan(100/1580) = 3.62° | 0.0631 | **0.08 px** |
| Raise camera 300 mm | β = 10.73° | 0.1863 | **0.24 px** |
| Raise camera 500 mm | β = 17.55° | 0.3015 | **0.39 px** |
| Lateral offset 100 mm | γ = 3.62° | 0.0631 | **0.07 px** (face angle) |
| Lateral offset 250 mm (enclosure-scale limit) | γ = 8.98° | 0.1561 | **0.18 px** (face angle) |

**[DERIVED]** — even an aggressive, enclosure-stretching 300–500 mm height gain or 250 mm lateral
offset buys well under half a pixel of signal per 5° of face angle or loft. That is why this
document's baseline case (β=0) reproduces the measured "0 px for the first 5°" finding exactly,
and why no realistic single-camera reposition within a "one unit behind the ball" constraint
rescues face angle or dynamic loft — it converts an exact zero into a barely-nonzero number, not
into a usable signal. A **look-down tilt primarily helps see more of the topline/crown** (useful
for segmentation and for the head/shaft split problem documented in the falsification doc's §6d.3
scale-mismatch finding) but does not, by this arithmetic, buy face/loft observability worth
building around.

### 3.3 Impact height and other translations are already fine

Impact height (vertical ball-to-face contact point given a known face pose) is a translation, not
a rotation, so it does **not** suffer the §3.1 degeneracy: 1 mm of vertical offset projects to
`fx/Z = 0.2954 px/mm` at today's plate scale regardless of camera tilt (only scaled by `cos(β)`,
≈1 for the small angles in §3.2 — a <2% effect at β=17°). This is already well-observed and is why
the radar-range-plus-camera-ray depth resolver (ball to ~3 mm, per `openflight-radar-can-carry-more`
**[PRIOR]**) is the right lever for impact location — not a repositioned camera.

### 3.4 Vantage comparison table

| Vantage | Face normal vs. viewing ray | Sees well | Sees poorly | Commercial precedent |
|---|---|---|---|---|
| **Behind-ball (current)** | ~parallel | transverse position, launch direction, shaft/lie, impact location *given* a face-pose prior + radar depth | **face angle, dynamic loft** (degenerate, §3.1) | Trackman 4 (radar-primary, camera does OERT impact location only), Mevo Gen 2, Full Swing KIT — none get face/loft optically from here **[PRIOR]** |
| Down-the-line-from-the-front (opposite end of the same line, looking back at the golfer) | ~parallel but **facing** the strike, so the face is briefly visible near-frontally at impact | face angle, dynamic loft, impact location — the face plane itself | timing (only a few ms of unobstructed view before/at impact), and the ball departs almost straight at the lens | Not used behind-ball by any comparator researched; closest analogue is GCQuad's/Uneekor's imaging of the face, from the side/above rather than dead ahead **[INFERRED — no direct product precedent found for this exact placement]** |
| **Face-on (side)** — perpendicular to the target line, level with the ball | **~perpendicular** | face angle (yaw axis now crosses the viewing ray, full `sin≈1` sensitivity, not `sin(tilt)≈0.1–0.3`), swing plane, path | dynamic loft somewhat (its rotation axis is closer to the viewing ray from this angle) | **GCQuad, SkyTrak, Garmin R50** — the standard vantage for optical face/loft **[CONFIRMED, `markerless-club-data-guide-v2` §1 comparator table]** |
| **Overhead** | perpendicular for yaw, parallel-ish for pitch | face angle (yaw), club path in the horizontal plane | dynamic loft (same degeneracy as behind-ball, rotated 90°); ball recedes across a large depth range fast | **Trackman iO, Uneekor EYE XO** — both overhead, both pay for it with very high frame rate (iO: 4,600 fps) because the ball crosses the frame almost perpendicular to the axis, a materially different — and easier — optical problem than behind-ball **[PRIOR, `camera-feasibility-verdict-2026-08.md` §2.4 correction]** |

### 3.5 Would a second camera in the same enclosure (stereo, ≤250 mm baseline) add anything radar range doesn't?

**On the ball: no.** Radar range to a smooth single-scatterer sphere is already ~3 mm, matching or
beating a 250 mm stereo baseline's noise floor at this range (**[PRIOR]** `camera-feasibility-verdict`
§3.7: "radar-range depth is a strong replacement for stereo on the ball").

**On the clubhead: plausibly yes, on depth — but not on face/loft.** Radar's phase center wanders
across a rotating, extended, multi-scatterer clubhead, an estimated **10–40 mm bias**
(**[INFERRED]**, same prior source). Stereo triangulation noise at baseline `B`, range `Z`, focal
`fx`, and sub-pixel disparity noise `σ_d`:

```
σ_Z ≈ Z² × σ_d / (fx × B)
```

At `Z=1580 mm`, `B=250 mm` (the largest baseline this task allows in one enclosure), `σ_d=0.3 px`
(a generous/optimistic centroid precision on a low-texture head): with today's `fx=466.7`,
`σ_Z ≈ 6.4 mm`; with the `1:1 + 6 mm` candidate's `fx=2000`, `σ_Z ≈ 1.5 mm`. **[DERIVED]** Either
would beat radar's clubhead bias — *if* correspondence between the two views can be established on
a specular, largely textureless head, which is the same detection problem that already caps
markerless keypoint pose (measured: 1 px of mask error ≈ 10° face angle, `openflight-segmentation-
is-the-wall`). So stereo's real, currently-unproven value is **clubhead depth**, not face/loft.

**Stereo does not rescue face/loft geometrically either.** A 250 mm baseline at 1580 mm gives each
camera only `atan(125/1580) ≈ 4.5°` of yaw off the shared centerline — the *same order of
magnitude* as the §3.2 lateral-offset numbers, i.e. still deep in the "barely nonzero" regime
(≈0.09 px per 5° of face angle from either single view). Combining the two views can improve *SNR*
on whatever weak signal exists, but it cannot manufacture the `sin(θ)≈1` sensitivity that only a
genuinely different viewing angle (§3.4's face-on row) provides.

### 3.6 A second camera face-on, on a cable — DIY acceptability and payoff

**Payoff:** per §3.4, a face-on placement is the only option analyzed here that converts face
angle and dynamic loft from a `sin(tilt)≈0.06–0.3` degenerate signal into a `sin(θ)≈1` direct one —
this is not an incremental gain, it is the mechanism every optical face/loft product actually uses
(GCQuad, Uneekor, Garmin R50, all side- or overhead-mounted, none behind-ball for this metric
**[PRIOR]**). It would also give an independent cross-check on club path/attack angle from a
different geometry than the behind-ball camera's, and (per the marker-placement reasoning in
`markerless-club-data-guide-v2` §1E-d) is the natural place to add face-visible or crown-visible
markers later without changing the primary unit.

**Cost to the product:** it breaks the "one unit" value proposition — a second small enclosure,
~1–2 m to the side of the hitting area, elevated to roughly ball height, on a cable back to the
same Pi. The Pi 5 has **two native CSI ports**, so this does *not* need a second compute unit —
only a second ribbon/cable run and, per the existing genlock discussion in
`camera-hardware-spec-v1.md` §5 **[PRIOR]**, either software-timestamp correlation (already
solved to ~33 µs via `ops_impact_finder.py`, reusable here) or a hardware trigger if tighter sync
is later required. **DIY acceptability is plausible but not free**: it needs its own leveling,
its own calibration to the shared world frame (the same camera→world calibration discipline as
the primary unit, `markerless-club-data-guide-v2` §4.2), and physical placement that many home
bays and mats will not have room for cleanly (a side camera intrudes into the practice-swing area
in a way a behind-ball unit does not). Recommended framing: **not a Phase-1 purchase**, but the
correct answer if face/loft optically (rather than via the radar D-plane, §1E/§1H of the guide)
is ever promoted from "stretch" to "requirement."

---

## 4. Recommended next capture session

**Primary — 1280×800, 1:1, stock 2.8 mm lens.** Zero purchase (no lens swap, no driver patch —
the mode already exists in the unpatched `ov9282` driver per §1.2), ~15× light multiplier (§2.3),
doubles plate scale to 0.59 px/mm (ball 25 px, clubhead 60 px), and *keeps* the full 2.17 m field
width — the same field 640×400 has today, just at twice the angular resolution. This is the
single highest-value, lowest-risk test in this document.

Exact settings to record:
- `width=1280, height=800, stream="raw"` (verify Picamera2 selects `MODE_1280_800`, not a
  software-scaled fallback — log the actual `ScalerCrop`/sensor mode libcamera reports)
- requested fps and **measured delivered fps + gap count** (this settles the "~144 fps" open
  question in §1.3)
- `exposure_us` swept per the lux ladder below, `analogue_gain` recorded at each step
- lens: confirm "2.8 mm, f/2.2" against the physical part in hand (a substitution during assembly
  would invalidate every number above)
- camera height above floor, camera-to-ball range, lateral offset, roll correction — all per the
  existing enclosure metrology fields already logged (`docs/camera/README.md` mounting section)
- ball reference diameter (42.67 mm) visible and unclipped in at least one calibration frame per
  session, for the existing self-calibration path
- ambient lux at the ball and at the clubhead corridor (~0.3–1 m up-range), measured with a lux
  meter, at each lighting step

**Alternate — same 1:1 mode + 6 mm lens swap ($15, M12 screw-mount, no tools beyond a lens
wrench).** Plate scale 1.27 px/mm (ball 54 px, clubhead 129 px), field narrows to ~1.0 m, light
multiplier ~27×. Run this only after the primary configuration is characterized — it isolates
whether the *extra* resolution (beyond the free 1:1 win) is worth its FOV and light cost, which is
exactly the open question `camera-feasibility-verdict-2026-08.md` §3.3 flagged as unresolved for
the "150–250 px" spin/pose tier.

### Lux ladder procedure

1. **Baseline.** With today's known-working lighting, measure lux at the ball and at the corridor
   midpoint. Record whatever exposure/gain the auto-exposure controller settles on for the
   *primary* config at this light level (it may be at/near max gain — record whether it is).
2. **Step up in roughly geometric increments** (×2, ×4, ×8, ×16, and ×30 if reachable) using one or
   more continuous LED work lights aimed into the corridor — not a strobe. At each step, capture:
   - a stationary reference frame (checkerboard or the teed ball alone) for focal/lux logging,
   - 3–5 live swings,
   - the auto-exposure controller's settled `exposure_us`/`gain` and whether it hit a rail.
3. **Stop increasing once** gain reaches its floor (≈1) **and** exposure reaches the ≤1.5 px-blur
   ceiling computed in §2.2 for the mode under test (70.5 µs for the primary config, 32.9 µs for
   the alternate). That lux level is the "sufficient" light level for that mode — record it, don't
   extrapolate it from the §2.3 arithmetic, per the standing lesson that measurement beats
   inference here (`feedback-render-before-measuring`, `openflight-meshfit-iou-anticorrelated`).
4. **At each rung, also record:** measured ball px and clubhead px (compare against §2.1's
   predictions — a mismatch bigger than ~10% means a lens/range assumption is wrong, not that the
   arithmetic is), and whether contrast still inverts sign between address and flight as it did in
   the 2026-08-24 capture (**[PRIOR]**, `camera-feasibility-verdict` §0.5) — that defect is
   orthogonal to exposure and will not be fixed by more light alone.
5. **Gate before spending on the alternate lens:** if the primary 1:1 configuration cannot reach
   its ≤1.5 px-blur exposure at any practically achievable lux in one session, the +6 mm lens
   (which needs ~1.8× more light again) is very unlikely to be worth bench-testing this round —
   fix the light budget on the free mode first.

This single session — one mode change (free), one lux ladder, one optional $15 lens swap — settles
§1.3's open fps question, §2.3's light-multiplier prediction against reality, and whether the
face/loft degeneracy in §3.1 is visible in real frames the same way it is in the sim (the "render
before measuring" check this project has twice needed and skipped).

---

## 5. Sources

**Datasheets/driver [CONFIRMED]:**
- [OmniVision OV9281 product page](https://www.ovt.com/products/ov9281/)
- [OV9281 product brief v1.4 (PDF)](https://www.ovt.com/wp-content/uploads/2024/05/OV9281-PB-v1.4-WEB.pdf) — 1280×800 @ 120 fps, 3 µm global shutter, ROI/windowing
- [Arducam OV9281 module datasheet (PDF)](https://blog.arducam.com/downloads/modules/RaspberryPi_camera/OV9281_MIPI_Camera_Module_DS_v2.pdf)
- [Arducam OV9281 wiki](https://docs.arducam.com/Raspberry-Pi-Camera/Native-camera/Global-Shutter/1MP-OV9281-OV9282/)

**Repo (read directly for this doc):**
- `drivers/ov9281/ov9282-high-speed.patch` — mode table, register values (`X_INC`/`Y_INC` for all three added modes confirmed `0x22`)
- `src/openflight/camera/capture.py`, `src/openflight/camera/capture_runtime.py` — confirms width/height pass straight to Picamera2's `create_video_configuration`, so `1280×800` needs no code change, only a settings change
- `docs/camera/README.md` — measured fps per mode, mounting geometry, current exposure/gain defaults

**Prior research docs (read first, tagged [PRIOR] throughout, not repeated in full):**
`camera-hardware-spec-v1.md`, `camera-feasibility-verdict-2026-08.md` (§0.5, §0.6, §2.4, §3.2–3.7),
`markerless-club-data-guide-v2-research-corrected.md` (§1, §1C–§1E, §3, §6A.1, §6B),
`2026-08-26-falsification-results.md` (§0.5 exposure/contrast findings, §6d scale-mismatch),
plus the session memory files `openflight-resolution-vs-light-budget`,
`openflight-silhouette-cannot-see-orientation`, `openflight-segmentation-is-the-wall`,
`openflight-radar-can-carry-more`, `openflight-real-capture-metadata`, `openflight-comparator-set`.
