# Launch Monitor Enclosure — Design Spec v2
*Direction B (two-zone swept), R50 language. Envelope 285H × 183W × 129D mm. Companion: `params.py` (master parameters), `sketch_views.svg`.*

## 0. Decided architecture (2026-07-29)

| Decision | Choice | Rationale |
|---|---|---|
| Radar v1 | **OPS243-A + IWR6843LEVM**; custom 24GHz PCB later | OPS243 = golden speed/trigger ref; IWR6843 = multi-RX AoA experiment. K-LD7s out entirely. |
| Sensor zone | **Fixed minimal-but-solid mounts** (cassette dropped — user: waste of plastic/space) | Each sensor gets a simple keyed mount to the body; if hardware changes, the freed volume is reused and affected mounts reprinted. Camera + IWR mount plate still share one printed datum part. |
| Compute | **Pi 5 first; phone offload fallback. No Jetson bay.** | Test whether Pi 5 handles the CV load; MLM2PRO-pattern phone offload if not. Zero volume impact either way. |
| Windows | **Three cut PC panes + printed dark bezel overlay** | 3.6mm smoked (24GHz) / 1.4mm (60GHz) / 2.0mm optically clear (camera). Solid sheet only — FDM prints have air voids (unpredictable εr) and are never optically clear. Bezel keeps the one-dark-inset look. |
| Power | **Geekworm X1206 V2.0 UPS** under Pi 5 | 4×21700 1S4P, 5.1V/6A out (26W peak = 5.1A, ~15% headroom), pogo-pin feed, charge-through, MAX17040 @0x36. **Not** X1202 (18650, 25.5W < peak). |
| Power input | **12V DC5521 panel jack** (+ USB-C 5V alt) | USB-C 25W can't charge-through at full load. |
| Display | **Standardized body opening + per-panel mounting frame.** Body: one rectangular opening, fixed M3 insert ring, max-depth budget. Each panel (ROADOM first, HMTECH/TD2 later) gets its own frame handling its mounting style — tab panels screw tabs to frame rear; rear-boss panels get matching bosses. Frame carries the cosmetic bezel lip so active-area shifts between panels don't constrain the body aperture. | Tab vs rear-hole panels can't share one mount; body-side interface can still be common (IARC case precedent). |
| Deployment | **Outdoor, IP54 target**, PETG/ASA | Full gasket program, rain-shadowed vents, no PLA. |
| Peripherals v1 | SEN-14262 (R17=47kΩ), **LIS3DH** (deliberate: vs ADXL355/SCL3300, print tolerance dominates), **BME280** (needs RH for air density) | Leveling-session decisions |
| Radar mount | **Adjustable tilt plate** for IWR6843 (~5.5–10.4° up measured; design nominal 10°), LIS3DH rides ON the plate | Leveling design doc §7 |
| Display wiring | HDMI + lower/Touch micro-USB + 3-pin GPIO: 5V→pin 2, GND→pin 14; pin 4 reserved for OPS243 J3 p9 | Hardware-integration doc (settled) |
| 2nd camera | **Dropped for v1** (explicit user decision 2026-07-29, overrides earlier provision-as-insurance note). If face-axis error later lands >2°, revisit = lower-body reprint. | User |
| Custom radar board | No reserved volume; when it arrives it reuses the OPS243/IWR6843 space and gets new mounts | User decision |
| Power board | **X1206 V2.0 confirmed** after lineup survey (only X12xx with 9–18V input + 6A + 4×21700 + ALON). Verify silkscreen says V2.0 — V1.1 burns out on 12V. Pogo path: FAQ-supported at 5A+ (`PSU_MAX_CURRENT=5000`); real risk is contact quality, not copper | Geekworm lineup research |
| 60GHz note | IWR6843 conflicts with the 24GHz-only custom-board decision — accepted as *interim experiment*, not product direction | Cassette makes it disposable. |

## 1. Mandatory features

| # | Feature | Forcing constraint |
|---|---|---|
| F1 | Fixed minimal mounts, each keyed + solid: OPS243 pocket, IWR6843 adjustable plate, camera bracket. Camera bracket + IWR plate datum reference the same printed feature. Freed volume documented for reuse on hardware changes | User decision (cassette dropped); extrinsics still need a shared datum |
| F2 | 24GHz pane: cut PC **3.6±0.1mm**, smoked ok, ≥6.2mm standoff, flat & parallel to OPS243 antenna | RFbeam AN-03 |
| F3 | 60GHz pane: cut PC **1.4±0.1mm**, standoff n×2.42mm, over IWR6843 **top-face** etched antenna | TI SWRA705 (2mm ABS measurably degrades pattern) |
| F4 | Bezel cutout for IWR6843 clears **120°az/80°el** cone, chamfered ≥45° edges | Beam clipping/multipath |
| F5 | Camera pane 2.0mm optically clear (never smoked), recessed/hooded against sun | §3.3 optics |
| F6 | Camera mount: back-focus access, rigid to cassette datum | Extrinsics from printed geometry |
| F7 | ruthex M3×5.7 inserts everywhere serviceable: Ø4.0 pilot ×6.7 deep, Ø8 boss | 1.6mm min wall |
| F8 | Main seam: 3mm cord, 4.0W×2.3D groove (23% compression), screws ≤60mm pitch | IP54, Full Swing pattern |
| F9+F10 | Body split 120+165mm at the gasket seam, Ø4 alignment keys | 285 > 250mm Ender Z |
| F11 | Port island: USB-C (Ø22), HDMI snap-in (25.8×12, **local wall ≤5mm**), **DC5521 jack** | Adafruit 4054/4218 specs; X1206 charge-through |
| F12 | Rear recessed vent panel, 2.5mm rib gaps, rain-shadowed | 16.5W typ; IP54 = splash not sealed |
| F13 | BME280 flush behind shaded intake grille, away from Pi/screen heat | Self-heating corrupts air density |
| F14–F16 | **25° screen inset** (optimized: at-address viewing from 3.1m, eye 1.5–1.75m → 22–27°; also cuts sky glare vs 41°) / near-plumb radar face; green LED bar light-pipe; power pill; **R50-style integrated slot handle**: 110×32mm through upper rear shoulder, R12 grip edges, 4mm reinforced walls, sized for ~3kg | Viewing geometry + R50-inspired-not-copied exterior |
| F17 | No metallic paint/film near either radar aperture | Radar killer |
| F18 | Pico + 1.8V level shifter adjacent to camera (**XTR = 1.8V**, 3.3V destroys it) | Trigger architecture |
| F19 | X1206 bay: Pi5+UPS stack (≤112×91×64 bounding), 21700 cell access hatch, pogo-pin service access | Cell swaps without teardown |
| F20 | **4× M8 threaded leveling feet** into heat-set inserts: Ø40 rubber pads (grass), ~15mm adjustment, knurled locknuts, ribbed bosses clear of gasket. No tripod mount. | Ground placement; feet + LIS3DH bubble level = setup workflow |
| F21 | Acoustic port through wall at SEN-14262 mic capsule, rain-baffled | Sound trigger + IP54 |
| F22 | **Adjustable IWR6843 mount plate** (pitch, 0–15°, lockable) with LIS3DH on the plate via two locators | Leveling §7; tilt is measured, not assumed |
| F23 | **Reference pad ≥40mm** flat, parallel to antenna face, on the mount plate assembly; printed off the same feature/orientation as the radar datum | Angle-gauge zeroing; ±0.2° residual |
| F24 | No steel inserts/screws near radar apertures; pad gauge-accessible at assembly | Magnetic gauge + RF |
| F25 | Route per PARTS.md **Layout A** (validated): OPS243 on Pi GPIO UART (5V, GND, TX, RX jumpers); IWR6843 on USB (data-capable cable to CP2105); 3-way GATE splice (SEN→HOST_INT J3 P3 + BCM17 pin 11); I²C chain Pi pins 17/25/3/5 → 4397 cable → BME280 → 4210/4401 QT cable → LIS3DH on mount plate; 3-pin display cable | PARTS.md + hardware-integration doc |
| F26 | **IWR6843 boot-mode switch (S1.1) + RESET button accessible** without full teardown — firmware flashing is a documented recurring operation | PARTS.md: custom firmware required |
| F27 | QT cable reach: mount-plate LIS3DH needs 100 or 200mm QT run from BME280 — cable length picked by CAD routing distance | PARTS.md cable options |

## 2. Power tree (X1206 5.1V/6A rail; budget 14W typ / 19.4W peak per hardware-integration doc)

- Pogo pins → **Pi 5** (EEPROM `PSU_MAX_CURRENT=5000`, `usb_max_current_enable=1` → 1.6A USB budget)
- **Screen: settled scheme** — 3-pin GPIO cable, 5V→pin 2, GND→pin 14 (~550mA); rides the pogo-fed header rail. Pin 4 → OPS243 J3 pin 9.
- X1206 USB-A / XH2.54 → **IWR6843** (draw UNMEASURED — inline USB meter gates the battery math; TI says one USB port can't sustain 3-TX)
- Pi USB → radars' data connections, Pico (low draw)
- Never plug the Pi's own USB-C while the X1206 is installed.
- **Verify:** total pogo-path current (Pi + header 5V loads) within X1206 pogo-pin rating.
- Runtime ~4.7h @14W with 4×5000mAh 21700.

## 3. Component CAD status & blocking unknowns

| Component | CAD | Action |
|---|---|---|
| Pi 5 | **STEP ✓** [official Jun-2026](https://pip-assets.raspberrypi.com/categories/892-raspberry-pi-5/documents/RP-010083-CA-1-rpi-5%203D%20STEP%20-%20No%20Graphics%20small%20file.zip) | Pull connector Z-heights (not in 2D drawing) |
| X1206 V2.0 | wiki drawing image only | **Measure stack height + hole coords** ([wiki](https://wiki.geekworm.com/X1206)) |
| IWR6843LEVM | no STEP; outline in login-gated [TI Altium pkg](https://www.ti.com/tool/IWR6843LEVM) | **Download pkg (free TI login) or calipers**; note antenna face + connector heights |
| OPS243 | **STEP ✓ in repo**: `cad/OPS243.stp` + design drawing PDF | Use standard OPS243 (USB), never OPS243-A-W WiFi (baud-locked 19200). J3: right-to-left, pin 1 = GPIO, GND = pin 10 (PR #166) |
| InnoMaker CAM-IMX296 | none | **Calipers:** holes, stack height, optical-axis offset, J2/J3 positions |
| ROADOM 7" | none exists | **Calipers: 8 measurements** (outer, holes, active area/bezels, lip, rear protrusions, boss pattern, plug depth) |
| SEN-14262 | Eagle only | **Calipers** incl. mic capsule center |
| LIS3DH 2809 / BME280 2652 | [Adafruit_CAD_Parts](https://github.com/adafruit/Adafruit_CAD_Parts) partial | Verify holes |
| Pico | **STEP ✓** [RP-009061-CA](https://pip.raspberrypi.com/documents/RP-009061-CA) | Verify 47×11.4 pattern |
| ruthex insert | **STEP ✓** [ruthex CAD](https://www.ruthex.de/en/pages/cad-daten) | — |
| PC sheet 3.6 + 1.4mm | — | Source (McMaster nominal 1/8" = 3.175 ≠ 3.6! **Buy metric or verify actual thickness**) |

## 4. Verified math (`verify.py` + this session)

- 24GHz PC: 6.2/√2.9 = 3.64 → 3.6±0.1 ✓ · 60GHz PC: λm/2 @62GHz = 1.42 → 1.4±0.1 ✓
- Peak current 26/5.1 = 5.1A < 6A ✓ · X1202 ruled out (25.5W < 26W) ✓
- Split 120+165 = 285, both ≤250 ✓ · 183×129 fits bed ✓
- Cassette module row: OPS243 (90) + IWR6843 (~65 est, UNKNOWN) + cam (38) + gaps — **must re-verify once IWR6843 outline known; may force two-row cassette layout**
- Boss 8.0 ≥ 7.2 ✓ · Main gasket 23% / cassette gasket 22.5% compression ✓

## 5. Workflow

1. Fill UNKNOWNs: caliper session (display, InnoMaker cam, SEN-14262, X1206 stack, IWR6843 if no Altium) + Pi 5 STEP Z-pull. OPS243 STEP already in repo `cad/`.
2. build123d reads `params.py` → per-part STL + assembly STEP
3. STEP → Fusion 360 for visual review/tweaks
4. Programmatic DFM pass (interference, walls, overhangs) → print cassette first (highest-risk part), test-fit before printing body
