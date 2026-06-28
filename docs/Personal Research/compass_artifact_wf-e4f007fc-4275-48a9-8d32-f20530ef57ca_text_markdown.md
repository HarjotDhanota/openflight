# Markerless Camera-Based Club Data for OpenFlight: A Technical Implementation Guide

## TL;DR
- **Build it software-first.** The achievable, high-value first target is **impact location on the face** (where the ball struck), derived from a markerless 6-DOF clubhead pose anchored to OpenFlight's existing radar impact timestamp — exactly the Trackman OERT / FlightScope Fusion "radar-first, camera-assists" architecture. Face angle and dynamic loft are achievable but materially harder from a single behind-ball view and should be staged later, with honest ±2° expectations.
- **The synchronization/capture subsystem is the genuinely new hardware learning curve.** The recommended starting point is a **global-shutter Sony IMX296 camera triggered by a Raspberry Pi Pico microcontroller**, with the Pico generating microsecond-deterministic exposure pulses on the camera's XTR pin and timestamping them against the OPS243-A impact event. Do not rely on the Pi's Linux/libcamera software path for timing.
- **Validate everything in simulation before touching hardware.** Use Blender/BlenderProc to render a generic clubhead model at known 6-DOF poses from a virtual behind-ball camera, recover the pose, and check it against ground truth. This de-risks the multi-view-geometry learning curve and lets the user's segmentation/ML strengths (BiRefNet, OpenCV, ROCm) carry the early stages.

## Key Findings

1. **Trackman's behind-ball markerless method is the proven blueprint.** Per Trackman's official "Two Radars, One Camera" description, the radars "operate at 40,000 samples per second to give a precise picture of the time of impact," while "the camera works together with the radar system to deliver 4D silhouette clubhead tracking with a pickup rate for club data of over 90% across all shot types," capturing "the exact impact location on the clubface without the use of markers" — and the unit "can be positioned safely behind the player because it 'sees' through the club." This validates the entire concept: you do not see the face from behind; you track the clubhead's silhouette/orientation and infer the face geometrically.

2. **Markerless 6-DOF pose of a fast, self-occluded, motion-blurred clubhead is the hard core.** The modern toolkit — instance segmentation → keypoints → PnP (PVNet), or render-and-compare (MegaPose/FoundationPose) — works and is trainable purely on synthetic data, but a single behind-ball viewpoint is depth/pose-ambiguous and must be regularized with a generic model prior and the radar's club-trajectory data.

3. **Global shutter is mandatory and the math is unforgiving.** A driver clubhead at ~100 mph moves ~45 m/s; freezing it to sub-pixel blur needs exposures on the order of tens of microseconds. Rolling shutter would skew the clubhead geometrically and corrupt pose. Outdoors, short global-shutter exposures in daylight are an advantage, not a fight with the sun.

4. **Compute is bursty, so a modest edge box suffices.** You process a handful of frames once every 30–60 seconds. Heavy inference offloads to the user's RX 9070 (ROCm) for development and a Jetson Orin (or phone NPU) for portable deployment; the Pi 5 just captures and ships frames.

5. **Patent caution is real but manageable for non-commercial open source.** Trackman holds an active, broad patent family on radar+camera fusion for ball/club tracking and explicitly marks OERT as patented. A non-commercial open-source contribution carries low practical risk, but a commercial product replicating OERT is the most patent-sensitive path. (General information, not legal advice.)

---

## Details

### 1. Markerless Clubhead Pose Estimation From Behind the Ball — The Core Problem

#### 1.1 The conceptual pipeline

```
Radar arms shot  ──►  pre-impact rolling buffer of GS frames
        │
   OPS243-A impact timestamp  ──► selects frame(s) bracketing impact
        │
   Detect/segment clubhead (markerless)
        │
   Estimate 6-DOF pose (R, t) of clubhead in camera frame
        │
   Transform into radar/target-line world frame (extrinsic calibration)
        │
   + ball position at impact (radar/camera)
        │
   ──► impact location on face, face angle, dynamic loft, club path, attack angle
```

The single most important conceptual point: **you never see the clubface from behind the ball.** You observe the back/crown/topline silhouette of the head. So you (a) estimate the *orientation of the clubhead as a rigid body* in 3D, then (b) apply a **known, generic club-type template** (a driver template, an iron template, a wedge template — not a scan of every specific club model) that says "given this head orientation, the face plane sits *here* with *this* loft and *this* normal." Trackman describes exactly this: it captures impact location "without the use of markers" because the camera + dual radar produce "4D silhouette clubhead tracking" and the radar fixes the precise impact time. The face data is *computed from the tracked head pose plus a club model*, not directly imaged.

#### 1.2 Why the behind/down-the-line view is hard

- **Self-occlusion:** from behind, the toe occludes part of the face/heel; the crown hides the sole. Only a partial silhouette is visible.
- **Motion blur:** at 45 m/s the head smears across many pixels unless exposure is tens of microseconds (Section 2.4).
- **Few milliseconds of visibility:** the head is in the useful zone for only a handful of frames.
- **Single-view depth/pose ambiguity:** one camera cannot resolve depth or certain rotation/translation trade-offs by itself. A given silhouette is consistent with a family of poses. You break this ambiguity three ways: (1) a **strong model prior** (the head is a known rigid shape of known size — scale fixes depth via apparent size), (2) the **radar's club trajectory** (the OPS243-A gives club speed and the K-LD7 pair give path/launch geometry, constraining the head's velocity vector and approximate position), and (3) **multi-frame temporal consistency** (the head follows a smooth swing arc, so poses across the bracket frames must be kinematically consistent).

#### 1.3 Markerless methods, compared

Because markers are ruled out, these are the candidate families. The user's segmentation strength (BiRefNet-class models) is a direct asset for the first two.

**(a) Deep instance segmentation → silhouette.** Segment the clubhead crisply (this is where BiRefNet-class high-resolution matting/segmentation shines — clubheads against sky/turf are a favorable figure-ground problem). The clean silhouette is then the input to either keypoint localization or analysis-by-synthesis. *Highest leverage for the user's existing skills; necessary precursor to everything else.*

**(b) Keypoint detection → PnP (PVNet, Keypoint R-CNN, YOLO-pose).** Train a network to find semantic clubhead landmarks (toe tip, heel, hosel/neck junction, crown apex, sole corners) as 2D points, then solve a Perspective-n-Point problem against the known 3D model coordinates of those landmarks to recover pose. **PVNet** (Peng et al., CVPR 2019, arXiv:1812.11788) is the canonical robust version: it regresses pixel-wise voting vectors toward keypoints and uses RANSAC voting, which makes it robust to the **occlusion and truncation** that dominate the behind-ball view — directly relevant here. Cost: you must define stable, visible-from-behind keypoints and have a 3D model to PnP against. This is the **most classical, debuggable, and synthetic-data-friendly** path and pairs naturally with OpenCV's `solvePnP`/`solvePnPRansac`.

**(c) Direct 6-DOF pose networks / render-and-compare (DOPE, GDR-Net, CosyPose, MegaPose, FoundationPose).** 
- **DOPE (Deep Object Pose)** and **GDR-Net** regress pose (or dense 2D-3D correspondences) end-to-end; DOPE is famous for being trained *entirely on synthetic/domain-randomized data*. 
- **MegaPose** (Labbé et al., CoRL 2022, arXiv:2212.06870) and **FoundationPose** (Wen et al., CVPR 2024, arXiv:2312.08344) are **render-and-compare** methods that work on *novel objects given a CAD model*, no per-object retraining: they render hypotheses of the model and iteratively refine the pose to match the observation. FoundationPose unifies model-based and model-free, is trained on large-scale synthetic data, and **explicitly tolerates blur** — its NVIDIA NGC model card states "our model is robust to blurry images" (with degradation only under "accutely [sic] blurry objects"), which is relevant for our regime.
- These are the **most accurate and most general** but the heaviest, and FoundationPose/MegaPose in their standard form expect **RGB-D**; we only have RGB, so we'd use the RGB pathways or supply depth via the model-scale prior. *Best ceiling on accuracy; highest compute; depth assumption is a caveat.*

**(d) Silhouette-based analysis-by-synthesis (classical render-and-compare).** Render the generic clubhead model's silhouette at a hypothesized pose, compare to the observed segmentation mask (IoU / chamfer distance), and optimize pose to maximize overlap. This is essentially a hand-rolled, lightweight version of (c) that runs on a CPU/modest GPU and is **fully transparent and tunable** — ideal for the simulation-first validation stage and a strong fallback when learned methods are data-starved. Pair it with the segmentation mask from (a).

**Recommended trajectory:** (a) segmentation first → (d) silhouette analysis-by-synthesis to validate the geometry in sim → (b) PVNet-style keypoints+PnP as the production markerless estimator → optionally (c) FoundationPose/MegaPose as an accuracy-ceiling experiment once the rig and synthetic pipeline exist.

#### 1.4 Realistic accuracy expectations and difficulty ranking

Honest ranking, easiest → hardest:
1. **Impact location (most achievable).** This is fundamentally about locating the ball relative to the segmented face region at the impact frame. It tolerates moderate pose error because it's a 2D-on-face position. Trackman/FlightScope present this as a heatmap (toe/heel/high/low); a realistic DIY target is **resolving quadrant + a few-mm-class estimate** rather than sub-mm.
2. **Club path / attack angle.** Already partly measured by OpenFlight's K-LD7 radars; the camera corroborates via head displacement across frames. Moderate difficulty.
3. **Face angle (harder).** A face-normal direction error maps directly to degrees; since the face is *inferred* from head orientation + template, small rotation errors and template mismatch both bite. Target **~±2°** versus a reference monitor, and expect that to be hard-won.
4. **Dynamic loft (hardest).** It compounds the vertical face angle with shaft lean and is most sensitive to the template's loft assumption and to depth ambiguity. Treat ±2–3° as a stretch goal and validate heavily.

> Source-quality flag: no public DIY system has demonstrated markerless behind-ball face angle to ±2°. These targets are extrapolated from commercial spec sheets and pose-estimation literature; treat them as goals to validate, not guarantees.

### 2. The Synchronization & Capture Subsystem (researched from scratch)

#### 2.1 Global shutter vs rolling shutter — and why GS is mandatory

A **rolling shutter** exposes the sensor row-by-row with each row starting at a slightly different time; a fast-moving object is therefore captured at different instants in different rows, producing geometric **skew/"jello"** distortion. A **global shutter** exposes every pixel simultaneously, freezing the scene at one instant. For a clubhead crossing the frame at 45 m/s, rolling-shutter skew would *systematically deform the very silhouette* you are trying to fit a pose to — corrupting pose, face angle, everything. **Global shutter is non-negotiable.**

#### 2.2 The Raspberry Pi Global Shutter Camera (Sony IMX296)

Key specs and trigger facts (from Raspberry Pi documentation and forums):
- **Sensor:** Sony IMX296, 1.6 MP, 1456×1088, **3.45 µm × 3.45 µm pixels**, C/CS-mount (same lens ecosystem as the HQ camera). Cost roughly **$50–80** for the camera; lenses extra.
- **Short exposures:** per Raspberry Pi docs the GS camera "can also operate with shorter exposure times – down to **30 µs**, given enough light – than a rolling shutter camera" — exactly our regime.
- **External trigger (XTR pin):** the GS camera can be triggered by pulsing the **XTR** connection. Critically, per the official docs, "the exposure time is equal to the low pulse-width time plus an additional **14.26 µs**," and "a PWM frequency of 30 Hz leads to a framerate of 30 frames per second." So the trigger pulse *both* fires the frame *and* sets exposure.
- **Wiring caveats:** XTR/XVS/XHS/XMASTER are **1.8 V logic — applying 3.3 V can destroy the sensor**; the official docs drive XTR from "a Raspberry Pi Pico" via "a 1.5 kΩ resistor" in series plus "a 1.8 kΩ resistor between XTR and GND to reduce the high logic level to 1.8 V." On boards with transistor Q2 fitted you must **remove resistor R11** to route the trigger; external-trigger support arrived in the kernel/driver in **August 2023** (set `imx296.trigger_mode=1`).
- **Native libcamera/rpicam support.**

**Alternatives:**
- **Arducam / InnoMaker IMX296 variants** — pin-compatible "drop-in" replacements that add **isolated hardware trigger and strobe** headers and ship their own driver/utility. InnoMaker's `i2c.py` utility (GitHub: INNO-MAKER/cam-imx296raw-trigger) offers "External trigger — enable/disable hardware trigger mode" and "Strobe — enable/disable the sensor's strobe output," and Arducam exposes Trigger/XVS/FSIN pins. These are easier to wire for triggering than soldering the bare Pi board. Similar ~$50–90 cost. **Recommended over the bare Pi GS board specifically because the trigger/strobe header is broken out.**
- **OV9281 / OV7251** — smaller mono global-shutter sensors (often 1 MP / VGA) with external trigger; OV7251 has a mainline Pi driver. Lower resolution, fewer pixels on the clubhead.
- **Industrial USB3 cameras (FLIR Blackfly S, Basler ace)** — robust opto-isolated trigger I/O, excellent drivers, precise timing, global shutter, higher frame rates and resolutions. **$300–900+.** This is the "do it properly / no fighting the toolchain" option if budget allows; the tradeoff is cost and that it pulls you off the cheap-Pi ethos of OpenFlight.

#### 2.3 The pre-impact rolling buffer

You must analyze the head *approaching and at* impact, but you don't know impact will happen until it does. Solution: the camera **free-runs into a circular (ring) buffer** continuously; when the radar fires its impact event, you **retain** the N frames already captured before/around that timestamp and discard the rest. This is conceptually identical to OpenFlight's existing OPS243-A **rolling buffer** for I/Q ball-speed data and PiTrac's pre-trigger approach — reuse that mental model. The buffer depth need only cover the few hundred milliseconds of downswing you care about.

#### 2.4 Motion-blur math: what exposure freezes a 100 mph clubhead

Blur in pixels: `B = (v · t_exp) / GSD`, where `v` = object speed, `t_exp` = exposure, `GSD` = ground-sample-distance (meters per pixel) at the clubhead's distance.

- 100 mph ≈ **44.7 m/s** (club-head speeds for mid-handicap golfers run ~90–110 mph; long drivers exceed 120 mph).
- Suppose the clubhead is framed so the field of view across the head region is ~0.3 m wide over ~1456 px → **GSD ≈ 0.2 mm/px**.
- For **≤1 px** blur: `t_exp ≤ GSD/v = 0.0002 / 44.7 ≈ 4.5 µs`.
- For a more forgiving **≤2 px**: `t_exp ≈ 9 µs`. For a wider FOV (GSD ≈ 0.5 mm/px), `t_exp ≈ 11–22 µs`.

So the operating point is **single-digit to low-tens of microseconds** exposure. The IMX296's ~30 µs floor (with the +14.26 µs offset) is right at the edge; in bright daylight you can push shorter. **This is why daylight is your friend:** very short exposures need lots of light, and the sun provides it for free. Indoor systems must add IR/LED strobes to deliver equivalent light in microseconds; outdoors you mostly don't (Trackman's OERT camera likewise works in ambient light rather than fighting the sun with strobes). **Frames needed around impact:** even at 60–120 fps you may catch only 1–3 frames in the impact zone, so the *trigger timing* matters more than raw frame rate — you want the trigger phased to land an exposure within ~1 ms of impact. A handful of frames bracketing impact (e.g., a short high-rate burst once triggered) is the goal. (Note: the ball-clubface contact itself lasts only ~0.4–0.5 ms, so you are imaging the head *approaching and leaving* the ball, not dwelling on contact.)

#### 2.5 Why sub-ms sync is hard on a Pi, and the microcontroller fix

The Pi runs **non-real-time Linux**: process scheduling jitter is milliseconds, and **libcamera's capture path has latencies of 100s of ms** and is not deterministic. You cannot ask Python-on-Linux to "take a frame exactly 0.7 ms after the radar trigger" reliably. The robust fix is to move the timing-critical job onto a **microcontroller (Raspberry Pi Pico, ~$4, or an Arduino)** or a small hardware circuit:

- The Pico runs the camera in **external-trigger mode**, generating the XTR pulse train (PWM sets frame rate; pulse width sets exposure) with **microsecond determinism**.
- The radar's impact event (the OPS243-A HOST_INT pin / sound trigger that OpenFlight already uses) is wired to a Pico GPIO. The Pico **timestamps** the impact against its own free-running clock and against the camera frames it is clocking, so you know *which frame* corresponds to *which time relative to impact* — to microseconds, independent of Linux jitter.
- The Pi then just pulls the buffered frames over CSI/USB and does CV. Timing lives in silicon, not in the OS.

This mirrors PiTrac's architecture, which uses strobe/trigger synchronization in hardware rather than trusting the Linux scheduler.

#### 2.6 Outdoor/daylight operation, lens, DOF, FOV

- **Daylight passive operation:** with µs exposures, full sun is an asset; you generally avoid IR strobes outdoors. Stop the lens down for depth of field and still have light to spare.
- **Sun-into-lens glare:** orient the camera so the sun is *behind or beside* it, not in frame; use a **lens hood**; avoid pointing down-target into a low sun. A slight downward tilt looking at the hitting area helps keep sky/sun out of frame.
- **Lens / FOV / DOF tradeoff:** a longer focal length (telephoto/zoom) puts **more pixels on the clubhead** (smaller GSD → less blur per pixel, finer pose) but **shrinks depth of field** and the capture volume, so the head can blur out-of-focus or leave frame. A wider lens is forgiving on DOF/framing but coarser. Start **wide enough to reliably catch the head through the impact zone**, stopped down (small aperture) for DOF, leaning on daylight for exposure; only zoom in once capture reliability is proven.

#### 2.7 Camera + trigger recommendation

**Start with:** an **Arducam/InnoMaker IMX296 global-shutter module (with broken-out hardware trigger + strobe header) + a Raspberry Pi Pico generating the XTR trigger and timestamping the OPS243-A impact**, feeding a Raspberry Pi 5. Rationale: cheapest path that gives *deterministic* triggering, reuses the Pi ecosystem and libcamera, matches OpenFlight's budget ethos, and the broken-out trigger header avoids the fragile R11-desolder on the bare Pi board. **Graduate to a FLIR Blackfly S / Basler ace** only if you hit a wall on frame rate, resolution, or trigger robustness and have the budget.

### 3. Synthetic Data Generation Pipeline (software-first, end-to-end)

#### 3.1 The renderer

Render a generic 3D clubhead (driver and iron templates) at **known 6-DOF poses** from a **virtual behind-ball camera with known intrinsics**, with realistic outdoor lighting, motion blur, and turf/sky backgrounds, outputting **ground-truth pose + mask + depth** labels.

**Tool comparison:**
- **Blender + Python (`bpy`):** maximum control, free, scriptable; you own the camera model and label export. Good default given the user's CV strength.
- **BlenderProc (DLR):** **purpose-built for synthetic pose-estimation training data.** Outputs RGB, depth, masks, and **6-DOF poses in BOP format**, supports physically-based rendering, distractors, and random indoor/outdoor backgrounds. In the **BOP Challenge 2020** (Hodaň et al., arXiv:2009.07378), participants were given "350K photorealistic training images generated by BlenderProc4BOP," and "methods achieved noticeably higher accuracy scores when trained on PBR training images than when trained on 'render & paste' images" — strong evidence this is the right tool. **Top recommendation for the training-data pipeline.**
- **NVIDIA Omniverse Replicator / Isaac Sim:** what NVIDIA used to train FoundationPose (678k images → 5.4M augmented); most powerful for large-scale domain randomization, but heavier setup and NVIDIA-centric (awkward on the user's AMD/ROCm box).
- **Unity Perception:** strong randomizers and label export; good if the user prefers Unity.
- **PyRender / PyBullet:** lightweight; PyBullet adds physics for pose sampling. Good for the **fast silhouette analysis-by-synthesis loop** and for quick validation, less photorealistic.

**Recommendation:** **BlenderProc for training data; a thin PyRender/Blender harness for the validation/analysis-by-synthesis loop.**

#### 3.2 Software-first validation (the core idea)

Because synthetic frames come with **exact ground-truth pose**, you can validate the entire estimation+derivation pipeline with zero hardware: feed rendered frames in, recover pose, and measure **recovered-vs-input pose error** (rotation in degrees, translation in mm), then propagate to the golf metrics and check those against the known synthetic ground truth too. This is how you de-risk the multi-view-geometry and metric-derivation code *before* the camera exists, and it directly leverages the user's ML/CV strengths.

#### 3.3 Domain randomization (sim-to-real)

The foundational result is **Tobin et al., "Domain Randomization for Transferring Deep Neural Networks from Simulation to the Real World," IROS 2017 (arXiv:1703.06907)**: by randomizing textures, lighting, camera pose, and distractors in the simulator — "with enough variability in the simulator, the real world may appear to the model as just another variation" — they "train a detector that is accurate to around 1.5 cm in the real world using only simulated data rendered with simple, algorithmically generated textures." Apply the same recipe: randomize clubhead materials/finish (chrome, matte, painted crown), lighting direction/intensity/color, sky and turf backgrounds, camera intrinsics/extrinsics within tolerances, and **motion-blur magnitude**. NVIDIA's DOPE and FoundationPose both rely on exactly this to transfer synthetic-only training to real objects.

#### 3.4 Where to get generic clubhead models, and how much fidelity

- **GrabCAD** has free golf club / clubhead / driver / 7-iron CAD models; **CGTrader / Sketchfab / 3DCADBrowser** have OBJ/STL/BLEND clubheads (e.g., a 7-iron with realistic loft/lie/length specs). 
- **Fidelity needed:** because the method uses a **generic per-type template**, you do **not** need a scan of every commercial club. You need the **gross external silhouette and face-plane geometry** correct (overall head shape, loft, face size/position). A clean, correctly-proportioned driver mesh and iron mesh — even simplified/parametric — are sufficient to start. Reserve high fidelity for the loft/face-plane region since that drives face-angle/dynamic-loft accuracy.

### 4. Deriving the Golf Metrics From Pose

Given the clubhead pose `(R, t)` (rotation + translation, camera frame) at the **impact instant selected by the radar timestamp**, and the known template:

- **Impact location:** Express the **ball center** (from radar and/or camera) and the **face plane** (a plane in the clubhead's local frame from the template) in a common frame. Intersect the ball-to-face contact geometry with the face plane, then read the contact point's coordinates **in the face's local (heel-toe, low-high) axes** → toe/heel and high/low offsets in mm. Present as a heatmap.
- **Face angle:** take the **face-normal vector** from the template, rotate it by `R` into world, project onto the horizontal plane, and measure its angle relative to the **target line** → open/closed in degrees.
- **Dynamic loft:** the **vertical** angle of the face normal (its elevation) at impact, relative to vertical/horizontal reference → degrees.
- **Club path & attack angle:** differentiate the head's translation `t` across the bracket frames to get the **clubhead velocity vector**; its horizontal deviation from target line = club path, its vertical angle = attack angle. Cross-check/fuse with the **K-LD7 radars**, which already give path/launch geometry.

**Coordinate frames & calibration (the new geometry work):**
- **Camera intrinsics:** calibrate with OpenCV `calibrateCamera` (checkerboard) → camera matrix + distortion. 
- **Pose solve:** `solvePnP` / `solvePnPRansac` for the keypoint→PnP path; these minimize reprojection error via Levenberg–Marquardt.
- **Camera↔radar extrinsics:** establish the rigid transform `(R, t)` between the camera frame and the radar/world frame so head pose and ball position live in one coordinate system aligned to the **target line**. Practically: place a known calibration target at known positions visible to the camera while the radar observes a known geometry (analogous to camera-LiDAR extrinsic calibration, which collects ≥6 corresponding points and solves with `solvePnP`). Because the radar isn't an imager, you calibrate against **known physical geometry** (fixed target locations, the tee position, a plumb/level target line) rather than a shared image.
- **Ball position at impact & frame selection:** the ball-at-rest position is known (teed/placed) and detectable in-frame (HoughCircles as PiTrac does, or segmentation); the **radar impact timestamp** (the same one OpenFlight already uses to align K-LD7 to OPS243-A) selects which buffered frame(s) to analyze. **Reuse OpenFlight's existing impact-correlation logic.**

### 5. Integration With OpenFlight + Compute Offload

#### 5.1 Slotting into the codebase (without disrupting radar)

OpenFlight's structure (`src/openflight/`): `ops243.py` (radar driver), `launch_monitor.py` (shot detection & club/ball separation), `rolling_buffer/` (I/Q spin processing), `server.py` (Flask/WebSocket + K-LD7 correlation), `kld7/` (angle radar FFT/phase). There is already a `scripts/vision/` directory ("Camera, YOLO, ML training") — the intended home.

Proposed additions, leaving the radar pipeline untouched:
- **`camera/` module:** Pico/trigger interface, rolling-buffer frame capture, frame retrieval keyed by timestamp.
- **`vision/` (pose) module:** segmentation → pose → metric derivation, designed to run **off-Pi** (see offload).
- **Hook point:** subscribe the camera capture to the **same `on_shot()` / impact-timestamp event** that already correlates K-LD7 to the OPS243-A impact. When a shot fires, the camera subsystem retains its buffered frames, tags them with the impact timestamp, and (a)nalyzes locally or (b) ships them to the offload box. Results merge into the existing `shot` object/WebSocket payload as new fields (impact location, face angle, etc.), so the **React UI and GSPro/E6 output** extend rather than change.
- Use OpenFlight's **mock mode** to develop the whole camera/pose path with synthetic frames and no hardware.

#### 5.2 Compute offload and what runs where

The workload is **bursty**: a handful of frames once every ~30–60 s. Sustained compute is therefore low even if per-shot inference is heavy. Strategy: **Pi 5 captures + buffers + ships frames; a stronger machine does inference.**

Concrete inference costs from the literature for the candidate models:
- **FoundationPose (render-and-compare):** measured by Wen et al. (arXiv:2312.08344) on an Intel i9-10980XE + **RTX 3090** — "pose estimation takes about **1.3 s for one object, where pose initialization takes 4 ms, refinement takes 0.88 s, pose selection takes 0.42 s. Tracking runs much faster at ∼32 Hz**" (~31 ms/frame). Reported elsewhere at **>120 FPS tracking on Jetson Thor**; Isaac ROS docs put peak VRAM at **~7 GB (FP32), ≥8 GB recommended**. So FoundationPose is viable per-shot (you can afford ~1 s) on a desktop GPU, and its tracking mode is real-time on strong edge hardware.
- **MegaPose:** **~50 ms per refiner iteration on an RTX 2080** (≈66 ms on RTX 3090 per outer update per the GenFlow paper); a few iterations per shot is easily within a per-shot budget.
- **PVNet (keypoints+PnP):** per the original paper, on an Intel i7 3.7 GHz + **GTX 1080 Ti** for a 480×640 image it "runs at **25 fps**... 10.9 ms for data loading, **3.3 ms for network forward propagation**, 22.8 ms for the RANSAC-based voting scheme, and 3.1 ms for the uncertainty-driven PnP." Light, real-time, modest VRAM. **Best fit for a portable box.**
- **YOLO-pose / YOLOv8-pose backbone:** ~**1–2 ms/img on an RTX 4090 (TensorRT FP16)**; ~**4.5–21 ms/frame on Jetson Orin Nano/Thor**. Trivial cost.
- **Instance segmentation (YOLOv8/11-seg):** **~4–8 FPS unoptimized PyTorch on Jetson Orin**, rising to **~52–65 FPS for YOLOv8n detection with TensorRT FP16/INT8 on Orin NX**. Heavier matting (BiRefNet-class) is more costly and is best run on the RX 9070 in development.

**What runs where:**
- **RX 9070 (ROCm) dev box:** train everything; run FoundationPose/MegaPose experiments and BiRefNet-class segmentation; the accuracy ceiling lives here.
- **Jetson Orin Nano/NX:** the realistic **portable deployment** target — PVNet-style keypoints+PnP or YOLO-pose + lightweight segmentation, TensorRT FP16/INT8, comfortably real-time for a per-shot burst; 8 GB is enough for these (but **not** for FP32 FoundationPose without care).
- **Phone NPU:** feasible for YOLO-pose/segmentation-class models; tighter for render-and-compare.
- **Pi 5 4GB:** capture, buffer, ball detection (HoughCircles), and orchestration only; not heavy pose inference.

### 6. Staged, Milestone-Driven Roadmap

**Stage 0 — Simulation only (no hardware). *Biggest early win; pure ML/CV/geometry.***
- Build BlenderProc renderer (driver + iron templates, behind-ball virtual camera, known intrinsics, domain randomization, motion blur).
- Implement metric-derivation math (Section 4) and validate against synthetic ground truth.
- **Benchmark:** recovered pose within, e.g., **≤2–3° rotation and ≤5 mm translation** of synthetic ground truth on clean renders; impact-location error **≤3 mm**; face-angle derivation **≤1°** *in sim*. Establishes the math is correct before reality intrudes.

**Stage 1 — Camera + radar-triggered capture (hardware). *Genuinely new learning: sync/trigger.***
- Build the IMX296 + Pico trigger + rolling buffer; wire the OPS243-A impact to the Pico; confirm frames are timestamped to impact.
- **Benchmark:** reliably capture **sharp (≤1–2 px blur) clubhead frames bracketing impact** on a high fraction of real swings (target a pickup rate you can measure, climbing toward Trackman's >90% as a north star); verify exposure ~tens of µs and correct frame-to-impact timing.

**Stage 2 — Markerless detection/segmentation on real frames. *Plays to the user's strengths.***
- Train/deploy clubhead segmentation (BiRefNet-class) on real captures; confirm sim-trained models transfer (domain randomization payoff).
- **Benchmark:** clean clubhead masks on real outdoor frames across lighting/clubs; mask IoU target (e.g., **≥0.9**) vs hand-labels.

**Stage 3 — 6-DOF pose on real shots.**
- Run keypoints+PnP (and/or render-and-compare) on real frames; compare to the synthetic-trained model's expectations and to multi-frame kinematic consistency.
- **Benchmark:** stable, physically-plausible pose across the bracket; reprojection error low; pose repeatability across similar shots.

**Stage 4 — Impact location.**
- Derive and display the heatmap.
- **Benchmark:** correctly classify deliberate **toe/heel/high/low** misses and center strikes; agreement with a reference monitor's impact map; few-mm-class consistency.

**Stage 5 — Face angle / dynamic loft.**
- **Benchmark:** **within ~±2° of a reference launch monitor** (Trackman/FlightScope/GCQuad) across a shot set — the hardest milestone; expect significant iteration on template fidelity and depth-ambiguity handling.

**Stage 6 — Integration.**
- Merge new fields into `server.py`'s shot payload, the React UI, and the GSPro/E6 output. Ship via OpenFlight's existing simulator interfaces.

**Where the new learning concentrates:** **Stage 1 (sync/trigger hardware)** and the **extrinsic calibration / multi-view geometry** in Stage 3–4 are the genuinely new areas; Stages 0, 2, and much of 4 ride the user's existing segmentation/ML/OpenCV skills.

### Patent caution (general information, not legal advice)
Trackman publicly marks **OERT as patented** and maintains an active, broad patent family on **coordinating radar data with camera/image data to track a sports ball** (e.g., US 10,471,328 and US 10,473,778, plus a stated portfolio around combined radar+camera tracking and impact-location-from-behind-the-ball). Replicating OERT — radar+camera fusion for **markerless impact location from behind the ball** — is therefore the **most patent-sensitive** part of this project. For a **non-commercial, open-source research contribution**, practical enforcement risk is low, and prior-art/independent-research framing helps, but anyone considering **commercializing** such a system should get **professional legal/patent counsel** first and review the specific claims. This is general information only.

---

## Recommendations

1. **Do Stage 0 now, entirely in software.** Stand up BlenderProc with a driver and an iron template, render behind-ball frames with ground-truth pose, and validate the full pose→metrics math against ground truth. Gate progress on **≤2–3° / ≤5 mm** pose recovery in sim. This is the highest-ROI use of the user's existing skills and requires zero hardware spend.
2. **Order the capture hardware in parallel:** an **Arducam/InnoMaker IMX296 GS module with broken-out trigger/strobe + a Raspberry Pi Pico**, plus a wide-ish C-mount lens and a lens hood. Budget < ~$120. Wire the Pico to the OPS243-A impact line.
3. **Choose the production estimator by deployment target:** **PVNet-style keypoints + `solvePnP`** for a portable Jetson Orin build (real-time, modest VRAM, transparent); reserve **FoundationPose/MegaPose** for the RX 9070 dev box as an accuracy-ceiling experiment (mind the RGB-D assumption and ~7–8 GB VRAM).
4. **Sequence the metrics by difficulty:** ship **impact location** first, **club path/attack angle** next (corroborating K-LD7), then attempt **face angle** and **dynamic loft** with ±2° as the bar.
5. **Reuse, don't rebuild, OpenFlight's impact correlation.** Hook the camera into the existing `on_shot()`/impact-timestamp path; extend the shot payload and UI rather than forking the radar pipeline. Develop against mock mode.

**Thresholds that change the plan:**
- If **Stage 1 pickup rate** stays low (frequently missing sharp impact frames), escalate the camera to an **industrial USB3 global-shutter camera (FLIR/Basler)** with hardware trigger before investing more in algorithms.
- If **single-view pose is too ambiguous** for face angle (Stage 5 stuck >±3°), add a **second camera** (stereo disambiguates depth) or lean harder on the radar club-trajectory prior — both are larger architectural changes to budget for.
- If **edge inference can't hit per-shot latency**, fall back from render-and-compare to **keypoints+PnP** (10–100× cheaper) or offload to a phone/laptop over the network.

## Caveats
- **No public DIY precedent for markerless behind-ball *face* data.** PiTrac (the closest open-source reference) does **ball** speed/spin/launch, not markerless club face angle/dynamic loft from behind. The face-data accuracy targets here are extrapolated from commercial specs and pose-estimation literature, not demonstrated on a DIY rig — treat ±2° as an aspirational, to-be-validated bar.
- **Render-and-compare methods (FoundationPose/MegaPose) assume depth (RGB-D) in their standard pipelines.** We have monocular RGB; you must use their RGB pathways or inject scale/depth via the model prior, which can reduce accuracy. Validate before committing.
- **Single behind-ball viewpoint is inherently depth/pose-ambiguous.** The model prior, radar trajectory, and multi-frame consistency mitigate but do not eliminate this; a second camera is the clean fix if needed.
- **The IMX296 ~30 µs exposure floor is near the motion-blur limit at driver speeds.** Bright daylight is essentially required to push exposures short enough; heavily overcast conditions or shade may force a wider FOV (coarser pose) or supplemental light.
- **Commercial use is patent-sensitive** (see above); this guide is general information, not legal advice.
- Some sourced figures (edge-inference FPS/VRAM) come from differing hardware, precisions (FP16 vs FP32), and image sizes; treat them as order-of-magnitude planning numbers, not guarantees for your exact pipeline.