# OpenFlight camera hardware spec v1 — from the 0A→0E sim requirements to a priced build

> **SUPERSEDED IN PART (2026-08-21) — read [v2 guide §1J](markerless-club-data-guide-v2-research-corrected.md) first.**
> Upstream PR #215 (`feat/OV9281-camera`) shipped a working behind-ball capture system: OV9281 mono GS, 320x200 @ ~468 fps
> (**~2.14 ms gap — §0's hardest requirement, met**), straight-behind vantage, shared BCM17 trigger, full offline artifacts.
> What it does **not** provide is the optics this spec was written for: ball is ~28 px (not ≥100), exposure 500 us (not 10–20),
> no IR strobe, no dot ball. **§1 (single-unit architecture), §2 (capture-architecture decision — Option A is now field-proven),
> and §5 (Pico/XTR genlock — not needed for the existing camera) are revised by §1J.** §3 (lens/sensor), §4 (illumination),
> §6 (consumables), §7 (BOM) and §8 (gates) still stand, but now describe a **second, `shape`-optimized camera** that
> complements the upstream one rather than replacing it.

> **Status (2026-07-03):** first hardware spec. Consolidates the *verified* requirements from the simulation chain (v2 guide §1F–§1I) and the fact-checked hardware research (§1F(b)) into a camera/illumination/trigger architecture, a phased BOM, and the bench tests that resolve the remaining open decisions. This is a **procurement + build + bench** document (human/maintainer-implemented), not Codex-implementable code.

## 0. What the sim chain requires (the consolidated target)

Every number below is a *verified* sim output (truth≠assumed splits, all failures counted, independently reproduced). The hardware exists to hit them:

| Requirement | Value | Source |
|---|---|---|
| Capture: inter-frame gap (spin) | **≈ 2 ms** (≈ 500 fps-equivalent) | 0E — 240 fps fails (FOV loss + iron wrap) |
| Ball image size | **≥ 100 px** at frame 0 (150–250 px buys 0.3–0.8° axis) | 0E |
| Dot count (ball) | **≥ 20** (27 = RPT-like, comfortable) | 0E |
| Limb-center fit quality | **≤ 1 px** (the dominant detector requirement) | 0E |
| Depth resolver | **Stereo** (mono fails impact height; also halves spin axis) | 0C, 0E |
| Camera↔impact sync | **≤ 100 µs**, via I/Q-buffer localization (NOT HOST_INT) | 0C, §1F(b) |
| Marker glare bias | **< 2 px** (retroreflective + IR + short exposure) | 0C |
| Per-club calibration | **~0.5 mm** (jig/routine) | 0C |
| Head image size (pose) | ~**450 px** (≈ 4 px/mm at 1.2 m, 16 mm lens) | 0B/0C |
| Motion freeze | exposure/strobe **≈ 10–20 µs** | §1F(b) arithmetic + PiTrac |
| Vantage | **straight behind** (quartering NOT needed) | 0E |
| Receiver (maintainer's domain) | σ_launch ≤ 1°, boresight leveling ≤ 1° | 0D |

## 1. Architecture overview

A **behind-the-ball unit** (~1.2 m behind the tee, ~0.3 m up, aimed down the target line / slightly into the flight corridor) containing:
- **Two synchronized global-shutter mono cameras** (stereo, ~150 mm baseline, straight-behind — no quartering).
- **Near-IR illumination** (strobe + flood) + **IR-pass filter** per camera.
- **A microcontroller trigger board** (Pico-class) genlocking the cameras and providing the staggered/ROI timing for spin.
- The existing **Pi 5** as compute, the **OPS243** as impact reference (via its I/Q rolling buffer) and radar kinematics.
- Consumables: **dot-pattern balls** (spin) and, for the optional "pro mode," **retroreflective club markers**.

The camera carries **spin (rate + axis)** and, in pro mode, **impact location + a face/loft cross-check**; the radar D-plane carries face/loft/path; they fuse (§1E "do both").

## 2. THE capture-architecture decision (the open question 0E surfaced)

Full-frame GS at 240 fps is insufficient — spin needs **~2 ms gaps** and the ball must stay in frame. Three ways to get there, with the honest trade:

| Option | How | Pros | Cons | Env |
|---|---|---|---|---|
| **A. ROI-cropped high-fps** (recommended to bench first) | crop the GS sensor to a vertical strip along the flight corridor → 500+ fps | one camera, **same viewpoint** (clean spin solve), works outdoors with short exposure | strip must be aimed/sized to the corridor; less of the head for pose | both |
| **B. Staggered dual-camera triggers** | fire the stereo pair ~2 ms apart | **no extra hardware** (you own 2 cameras); reuses the genlock line | the 2 spin images are 150 mm apart → parallax to correct in the solve; interleaving pose vs spin frames adds logic | both |
| **C. Strobe multi-pulse** (PiTrac-style) | one long exposure, several IR flashes → multiple ball images in one frame | any gap, cheapest, no high-fps needed | **fatal outdoors** (long exposure integrates sun — §1F(b)); overlapping ball images confuse detection | indoor only |

**Recommendation:** bench **A (ROI high-fps)** as the primary — it's single-viewpoint (simplest spin geometry), outdoor-capable, and needs no extra parts; keep **C** as the indoor high-margin fallback (cheap, and the IR strobe hardware is shared). **B** is a fallback if the IMX296 ROI fps proves insufficient. *A bench test (Gate 1) decides — this is the single most important thing to validate before committing the full build.*

**The framing tension (must be resolved at aim/lens time):** club-pose wants the *head* framed at the impact zone (~450 px, 16 mm, aimed at the tee); spin wants the *ball* tracked up the corridor (100–250 px, staying in FOV, aimed into the corridor). These pull the aim/lens in different directions. Starting resolution: aim to cover the **impact zone + ~30 cm of corridor** with a **~12 mm lens** (wider than the 16 mm pose-optimal), accept the head-px trade, and let Gate 1 tune it. Since the phased build tests **spin first**, the first camera is optimized for the spin framing only; the pose/club framing is re-tuned when stereo + pro mode are added.

## 3. Cameras

- **Sensor:** Sony **IMX296** mono global-shutter (1456×1088, 1.58 MP) — mature Pi support, hardware external trigger (XTR), good NIR. Mono for light + resolution. *Alternative:* **OV9281** (1 MP) — cheaper/faster-fps at ROI but fewer px (borderline for the 100 px ball at range); keep as a fallback.
- **Global shutter + external trigger are non-negotiable** (rolling shutter skews a 45 m/s target; XTR is how we genlock + time).
- **Lens:** C/CS-mount, start ~**12 mm** (spin/corridor) and hold a 16 mm for pose tuning. Longer lens → bigger ball/head (margin) but tighter FOV (FOV-loss risk) — the Gate-1 trade.
- **Known gotchas (PiTrac-proven, §1F(b)):** XTR is **1.8 V logic** (level-shift from a 3.3 V MCU: ~1.5 kΩ series + 1.8 kΩ to GND); some boards need **R11 removed**; the **official Pi GS board needs a "flush pulse"** (InnoMaker boards don't) — **prefer InnoMaker/Arducam IMX296 modules** to avoid it; set `imx296.trigger_mode=1` and pin a fixed shutter so AGC doesn't fight the trigger.

## 4. Illumination + filter

- **Strobe:** ~**10× Vishay VSMA1085400** (850 nm, 5 A pulsed) switched by an **IRLU024N MOSFET + MCP1407 gate driver** at 12 V, current set by a small DAC — the PiTrac-proven design, delivering **10–20 µs pulses**. A retroreflective marker returns far more light than PiTrac's white ball, so this suffices at 1.2 m (may need more current/longer lens than PiTrac's 0.5 m — Gate 2).
- **Short-exposure regime, not long-exposure pulse trains** — mandatory for outdoor (§1F(b)): total exposure 15–30 µs so ambient can't integrate.
- **Wavelength trade:** **850 nm** = more LED output + sensor QE (indoor/shade). **940 nm narrowband** = sits in the solar-absorption dip → better outdoor sun rejection, at ~2–3× less output. **Plan for a 940 nm narrowband filter + emitters for the outdoor marker path** (the sun-glint risk is real — §1F(b)); 850 nm longpass is fine indoors and for the ball (dark dots on white are contrast-based, sunlight-friendly).
- **Filter:** IR-pass per camera (850 nm longpass indoor; **940 nm narrowband for the outdoor marker mode** — the glint-rejection requirement).

## 5. Trigger + sync electronics

- **MCU:** Raspberry Pi **Pico** (~$4) drives both cameras' XTR (genlock), generates the ROI/staggered timing (Option A/B), and gates the strobe MOSFET.
- **Sync to impact:** **NOT** the OPS243 HOST_INT (it lags ~100 ms — §1F(b)). Use the **sound trigger** (µs electronics) to arm, then **localize the impact instant inside the 30 kHz I/Q rolling buffer post-hoc** (~33 µs resolution); calibrate the **acoustic propagation offset (~3 ms/m)**. This meets the ≤ 100 µs sync requirement as a *software* task on existing hardware. (Gate 3 validates it end-to-end.)
- **Level shifting** for the 1.8 V XTR (§3).

## 6. Consumables

- **Dot-pattern ball (spin):** a coded asymmetric ~20–27-dot pattern. Options: a **stamp/stencil on any ball** (cheap, DIY), printed balls, or **RCT/RPT-style balls** (~$3–5/ball). The pattern must break all symmetries (Stage 0E assumption). Outdoors these work in ambient light (no strobe needed for the ball).
- **Retroreflective club markers (pro mode only):** ~5–8 mm retroreflective dots on crown/back/hosel + a shaft band, in a coded asymmetric layout (§1E(d)). Per-club calibration jig/routine (~0.5 mm) — a one-time-per-club step.

## 7. BOM + phased purchase

**Phase 1 — spin bench (validates Gate 1 + 2 + the whole spin claim on ~$150):**

| Item | ~Price |
|---|---|
| 1× Arducam/InnoMaker IMX296 mono GS + trigger | $60–100 |
| 1× 12 mm C-mount lens | $20–35 |
| Raspberry Pi Pico + level-shift parts | $10 |
| 10× VSMA1085400 + MOSFET + gate driver + DAC + 12 V | $30–45 |
| 850 nm longpass filter | $10–20 |
| Dot-pattern balls (stamp or RCT) | $10–30 |
| **Phase 1 total** | **~$140–230** |

**Phase 2 — stereo + sync (impact height, pose, full spin axis):**

| Item | ~Price |
|---|---|
| 2nd IMX296 mono GS + lens | $80–135 |
| Stereo mount (150 mm baseline) + genlock wiring | $15–30 |
| **Phase 2 add** | **~$95–165** |

**Phase 3 — outdoor + pro mode (marker path):**

| Item | ~Price |
|---|---|
| 940 nm narrowband filter(s) + 940 nm emitters | $40–90 |
| Retroreflective marker kit + calibration jig | $20–40 |
| **Phase 3 add** | **~$60–130** |

**Full build ≈ $300–525** across three phases — consistent with the ~$120–300 capture-first target for Phase 1, and each phase gated by a bench test before the next spend.

## 8. Bench-test gates (resolve the open decisions before over-spending)

1. **Gate 1 — the 2 ms capture architecture (Phase 1).** Can the IMX296 ROI-strip mode deliver ~2 ms gaps with the ball at ≥ 100 px staying in frame over ≥ 3 frames? Measure real fps@ROI, tune lens/aim. *If yes → Option A confirmed; if no → try staggered triggers (B).* **The gating validation of the whole 0E result.**
2. **Gate 2 — illumination + detector quality (Phase 1).** Do 10 IR LEDs at 10–20 µs freeze the ball at 1.2 m with a limb-center fit ≤ 1 px and clean dot centroids? Indoor first, then **outdoor in ambient light**.
3. **Gate 3 — impact sync (Phase 1–2).** Localize the impact instant in the I/Q buffer + calibrate the acoustic offset → verify camera frame ↔ impact to ≤ 100 µs end-to-end.
4. **Gate 4 — outdoor marker sun test (Phase 3, the flagged risk).** Do retroreflective markers + 940 nm narrowband + short exposure survive **direct sun** without glint false-blobs? (§1F(b) says this is unproven for cheap rigs — test before relying on the pro mode outdoors.)
5. **Gate 5 — the 1-day markerless photo test** (parallel, no purchase): 20 real behind-ball photos, 2 annotators → real markerless σ → feed the 0C machinery for the honest markerless verdict (§1G(b)).

## 9. Open items / risks on the record

- **Framing tension (pose vs spin)** — resolved empirically at Gate 1; may ultimately want slightly different aim/lens for the two jobs (or a compromise mid-lens).
- **Receiver is the maintainer's parallel track** — the D-plane needs σ_launch ≤ 1° from the coherent receiver (§1A deep-dive); the camera side is independent but the *face/loft* metric depends on it landing.
- **Outdoor marker path is the biggest hardware unknown** (Gate 4) — the ball-spin path does not carry this risk (ambient-light friendly).
- **PiTrac is indoor-only by its own admission** — OpenFlight's outdoor ambition exceeds any demonstrated DIY build; treat outdoor as validate-before-claim.
