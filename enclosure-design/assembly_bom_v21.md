# OpenFlight Enclosure v22 — Mechanical Assembly BOM (Draft)

This BOM was audited against the active Fusion document
`Updated OpenFlight Housing Back for X1209 + X12-A1 + LIS3DH + BME280 v22`
on 2026-09-05.

The quantities below cover one enclosure. The X1209 and Raspberry Pi use four
M2.5 x 20 through-screws into the screen mounts; they do not use eight short
screws at the two ends of the X1209 spacers.

## Heat-set inserts

| Exact insert | Required | Recommended order qty | Used at |
|---|---:|---:|---|
| PEM `IUTB-M2` | 4 | 10 | Camera board |
| PEM `IUTB-M2.5` (Mouser/SI order code `SI/IUTB-M25`) | 18 | 25 minimum; 50 if that is the supplier pack/MOQ | Radar brace to housing (2), display (4), BME280 (4), IWR6843 direct mounts (4), housing front-to-back joint (4) |
| CNC Kitchen M2.5 x 4 mm, manufacturer no. `M2,5x4,0` | 4 | 10 or one retail pack | LIS3DH |
| PEM `IUTB-M3` | 7 | 10 | X12 battery door (1), sound detector (2), OPS243 (4) |
| CNC Kitchen M3 x 3 mm short, manufacturer no. `TC-M3x3.0` | 4 | 10 or one retail pack | X12 reversible cradle/brace |

Confirmed insert total: **37**.

The two short CNC Kitchen sizes are intentional. The current LIS3DH and X12
bosses provide only about 4.56 mm of usable pocket depth, so the 5.74 mm-long
PEM IUTB-M2.5/IUTB-M3 parts do not fit without revising those bosses.

PEM pocket dimensions used by the CAD:

| Insert | Insert length | Insert OD | CAD hole | Minimum pocket depth |
|---|---:|---:|---:|---:|
| IUTB-M2 | 4.00 mm | 3.73 mm | 3.23 mm +0.08 | 4.76 mm |
| IUTB-M2.5 | 5.74 mm | 4.55 mm | 4.01 mm +0.08 | 6.50 mm |
| IUTB-M3 | 5.74 mm | 4.55 mm | 4.01 mm +0.08 | 6.50 mm |

Sources: [PEM IUTB-M2](https://www.pemnet.com/eu/products/product-finder/iutb-m2/),
[PEM IUTB-M2.5](https://www.pemnet.com/products/product-finder/iutb-m2-5/),
[PEM IUTB-M3](https://www.pemnet.com/products/product-finder/iutb-m3/),
[CNC Kitchen M2.5 x 4](https://cnckitchen.store/products/gewindeeinsatz-threaded-insert-m2-5-standard-100-stk-pcs),
[CNC Kitchen M3 x 3 short](https://cnckitchen.store/products/heat-set-insert-m3-x-3-short-version-100-pieces).

## Screws

Unless noted otherwise, use ISO 4762 / DIN 912 socket-head cap screws in
A2-70 (304/18-8) stainless steel. Length is measured under the head. Standard
coarse pitches are M2 x 0.4, M2.5 x 0.45, and M3 x 0.5.

| Exact screw | Required | Recommended order qty | Used at |
|---|---:|---:|---|
| M2 x 4, ISO 4762, A2-70 | 4 | 6 | Camera board |
| M2.5 x 5, ISO 4762, A2-70 | 8 | 10 | Display (4), LIS3DH (4) |
| M2.5 x 6, ISO 4762, A2-70 | 6 | 10 | Radar brace to housing (2), BME280 (4) |
| M2.5 x 16, ISO 4762, A2-70 | 4 | 6 | IWR6843 direct-to-housing mounts |
| M2.5 x 20, ISO 4762, A2-70 | 4 | 6 | X1209 and Raspberry Pi through-stack into the screen mounts |
| M2.5 x 55, ISO 4762, A2-70 | 4 | 6 | Housing back-to-front joint |
| M3 x 6, ISO 4762, A2-70 | 6 | 10 | Sound detector (2), OPS243 (4) |
| M3 x 10, ISO 10642 90-degree countersunk socket, A2-70 | 1 | 3 | X12 battery door |
| M3 x 25, ISO 4762, A2-70 | 4 | 6 | X12 reversible cradle/brace |

Finished-assembly screw total: **41**.

The Fusion screw-reference component contains 41 bodies. The four X1209
reference bodies are modeled and named as M2.5 x 20 through-screws. Their
centers match the four screen standoff centers within 0.003 mm, and the modeled
tips engage the screen standoffs by 3.59 mm. Geekworm's stock
eight-M2.5-x-5 arrangement is not used in this build.

The four back-to-front recesses are cylindrical counterbores, not conical
countersinks: 3.3 mm clearance bore, 5.5 mm counterbore diameter, 3.0 mm
counterbore depth. They are sized for the 4.5 mm head of an M2.5 ISO 4762
screw.

### McMaster-Carr screw cross-reference

McMaster-Carr can supply every current screw size. The most consistent
single-family choice is black-oxide Class 12.9 alloy-steel socket-head screws;
use the separate 90-degree flat-head screw for the battery door.

| Design screw | McMaster-Carr part | McMaster package | Notes |
|---|---|---:|---|
| M2 x 4 socket head | `90128A176` | 100 | Class 12.9 alloy steel |
| M2.5 x 5 socket head | `91290A100` | 50 | Class 12.9 alloy steel |
| M2.5 x 6 socket head | `91290A101` | 50 | Class 12.9 alloy steel |
| M2.5 x 16 socket head | `91290A106` | 50 | Class 12.9 alloy steel |
| M2.5 x 20 socket head | `91290A108` | 25 | X1209/Pi through-screw |
| M2.5 x 55 socket head | `91290A573` | 1 | Partially threaded; verify the unthreaded grip does not enter the insert before release |
| M3 x 6 socket head | `91290A111` | 100 | Class 12.9 alloy steel |
| M3 x 10, 90-degree flat head | `92125A130` | 100 | ISO 10642 / DIN 7991, 18-8 stainless |
| M3 x 25 socket head | `91290A125` | 100 | Class 12.9 alloy steel |

McMaster's any-orientation brass heat-set inserts are `93502A108` (M2,
3.2 mm long), `93502A109` (M2.5, 3.6 mm long), `93502A111` (M3, 3.6 mm
long), and `93502A137` (M3, 5.7 mm long). They are not all dimensionally
identical to the PEM and CNC Kitchen inserts used to size the current CAD.
In particular, McMaster has no long 5.7 mm M2.5 version in this family.
Treat them as an alternate insert standard only after a pocket-fit coupon and
pull-out test; do not silently substitute them in the release BOM.

McMaster nylon washer `95610A011` is the closest catalog match to the BME280
washer, but it is 2.7 mm ID x 5.6 mm OD x 0.6-1.0 mm thick instead of the
modeled 2.7 x 5.0 x 0.5 mm. It also needs a fit check before substitution.

Sources: [McMaster socket-head screw family](https://www.mcmaster.com/products/91251A541/),
[McMaster ISO 10642 flat-head screws](https://www.mcmaster.com/products/flat-head-screws/specifications-met~iso-10642/),
[McMaster any-orientation heat-set inserts](https://www.mcmaster.com/products/threaded-inserts/performance~any-orientation/),
[McMaster nylon flat washers](https://www.mcmaster.com/products/flat-washers/material~plastic-1/material~nylon/).

## Other mechanical hardware

| Item | Required | Notes |
|---|---:|---|
| M2.5 female/female brass spacer, 13.5 mm long | 4 | Included with Geekworm X1209 |
| M2.5 nylon flat washer, 2.7 mm ID x 5.0 mm OD x 0.5 mm thick | 4 | Under the BME280 board; order by dimensions rather than DIN number |
| X1209 pogo pin | 1 | Included with X1209; for Raspberry Pi 5 power-button function |
| Waterproof CAT6 RJ45 female/female panel coupler with cap, Amazon ASIN `B0FP2MP7T1` | 1 | Current housing cutout is 25.0 mm diameter |
| Waterproof DC 5.5 x 2.1 mm female panel jack with cap, Amazon ASIN `B0DP6MNQQB` | 1 | Current housing cutout is 13.4 mm diameter |

The panel-port nuts, washers, and caps supplied with the connector kits are
part of those connector assemblies and are not counted separately above.

## Electronics and fitted components

| Component | Exact model / current design reference | Qty |
|---|---|---:|
| Single-board computer | Raspberry Pi 5 Model B (RAM capacity is not mechanically relevant) | 1 |
| UPS HAT | Geekworm X1209 V1.0, 65 x 57 mm | 1 |
| Battery carrier | Geekworm X12-A1 | 1 |
| Batteries | Matching 3.7 V 18650 Li-ion cells, 4.2 V full-charge chemistry | 4 |
| Display | ROADOM 7-inch 1024 x 600 IPS touchscreen, Amazon ASIN `B07VNX4ZWY` | 1 |
| 24 GHz radar | OmniPreSense `OPS243-A-CW-RP` | 1 |
| 60 GHz radar | Texas Instruments `IWR6843LEVM` | 1 |
| Camera | InnoMaker `CAM-MIPIOV9281-V2`, 32 x 32 mm | 1 |
| Environmental sensor | Adafruit BME280 STEMMA QT, product ID `2652` | 1 |
| Accelerometer | Adafruit LIS3DH STEMMA QT, product ID `2809` | 1 |
| Sound detector | 43.7 x 23.3 mm board with 9.5 mm microphone envelope | 1 |

The sound detector vendor/model is not yet locked in the CAD. Cable lengths,
FFC orientation, STEMMA QT leads, radar data cables, and internal power leads
also need a separate wiring BOM after routing is finalized.

Sources: [Geekworm X1209](https://geekworm.com/en-au/products/x1209),
[ROADOM display](https://www.amazon.com/dp/B07VNX4ZWY),
[OPS243](https://omnipresense.com/wp-content/uploads/2021/09/OPS243-Product-Brief_004-E.pdf),
[TI IWR6843LEVM](https://www.ti.com/tool/IWR6843LEVM),
[InnoMaker camera](https://www.inno-maker.com/wp-content/uploads/2022/05/CAM-MIPIOV9281-V2-User-Manual-V1.2.pdf),
[Adafruit BME280](https://www.adafruit.com/product/2652),
[Adafruit LIS3DH](https://www.adafruit.com/product/2809).

## Printed parts

1. Housing Back
2. Housing Front
3. Combined IWR/OPS radar X-brace
4. X12 reversible battery cradle adapter
5. X12 battery door (single countersunk screw)

The kickstand and its custom hex-retainer pieces are maintained in a separate
Fusion document and are excluded from the confirmed totals in this draft.
They must be audited in that document before this BOM can be called the full
product BOM.

## Verified model corrections

- The Housing Back `Body117` now resolves as one connected lump. Three IWR
  standoff bases that were floating 1.00 mm above the floor were joined with
  exact 8.00 mm cylindrical floor bridges; the original centers, bores, and
  top heights were not changed.
- The four X1209/Pi screw references are M2.5 x 20 and terminate 3.59 mm into
  the aligned screen standoff envelopes.

## Release blockers before export

1. Rename stale browser entries: `Heat Set Inserts (41)` currently contains
   37 bodies; `Assembly Screws (45)` currently contains 41 bodies;
   `Lebula` should read `ROADOM`; panel-port reference names still contain old
   hole/barrel dimensions.
2. Re-run interference and section checks with Housing Front, Housing Back,
   display/camera, panel ports, X1209/Pi, X12, and both radars visible.
3. Save a versioned Fusion archive before producing neutral export files.

## Export set for Printables

Publish separate files so builders can replace only the part they need:

- `OpenFlight_Housing_Back_v1.3mf` and `.stl`
- `OpenFlight_Housing_Front_v1.3mf` and `.stl`
- `OpenFlight_Radar_Brace_v1.3mf` and `.stl`
- `OpenFlight_X12_Cradle_v1.3mf` and `.stl`
- `OpenFlight_X12_Battery_Door_v1.3mf` and `.stl`
- `OpenFlight_Enclosure_v1_Printables.step` with the five printable parts as
  separate editable components
- `OpenFlight_Enclosure_v1_Source.f3d` with the full editable component tree
- this BOM, an assembly guide, print orientation screenshots, and a labeled
  hardware diagram

Do not export the non-printing electronics, insert, screw, washer, or port
reference components as printable meshes.
