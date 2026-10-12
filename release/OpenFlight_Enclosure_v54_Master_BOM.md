# OpenFlight Enclosure v54 — Master Build BOM

Audited against the active Fusion document:

`Updated OpenFlight Housing Back for X1209 + X12-A1 + LIS3DH + BME280 v54`

Audit date: 2026-09-11

This list covers one complete enclosure. It intentionally keeps the X12-A1,
BME280, LIS3DH, and X1209/Pi stack on **M2.5 hardware**. They are not M3.

## Fastener totals

| Thread | Screws | Heat-set inserts | Notes |
|---|---:|---:|---|
| M2 x 0.4 | 4 | 0 | Camera; direct-threaded printed pilots |
| M2.5 x 0.45 | 16 | 8 | BME280 uses direct-threaded pilots; inserts remain for LIS3DH and X12-A1 |
| M3 x 0.5 | 23 | 23 | Housing, screen, radars, sound detector, door, kickstand |
| **Total** | **43** | **31** | One enclosure |

## Exact screw list

Unless noted otherwise, lengths are measured under the head.

| Qty | Screw | McMaster-Carr | Used at |
|---:|---|---|---|
| 4 | M2 x 4 mm, standard socket head | [`90128A176`](https://www.mcmaster.com/90128A176) | Camera board; screws form threads in Ø1.60 mm printed pilots |
| 4 | M2.5 x 5 mm, standard socket head | [`91290A100`](https://www.mcmaster.com/91290A100) | LIS3DH |
| 4 | M2.5 x 6 mm, standard socket head | [`91290A101`](https://www.mcmaster.com/91290A101) | BME280; screws form threads in Ø2.00 mm printed pilots |
| 4 | M2.5 x 20 mm, standard socket head | [`91292A019`](https://www.mcmaster.com/91292A019) | X1209 and Raspberry Pi through-stack |
| 4 | M2.5 x 25 mm, standard socket head | [`91292A036`](https://www.mcmaster.com/91292A036) | X12-A1 reversible cradle/brace |
| 12 | M3 x 6 mm, standard socket head | [`91502A102`](https://www.mcmaster.com/91502A102) | Sound detector (2), OPS243 (4), radar brace anchors (2), display/screen (4) |
| 4 | M3 x 16 mm, standard socket head | [`91502A107`](https://www.mcmaster.com/91502A107) | IWR6843 direct-to-housing mounts |
| 4 | M3 x 55 mm, standard socket head | [`91290A573`](https://www.mcmaster.com/91290A573) | Housing front-to-back joint |
| 1 | M3 x 10 mm, 90-degree hex-drive flat head | [`93395A204`](https://www.mcmaster.com/93395A204) | Flush X12-A1 battery-door screw |
| 2 | M3 x 14 mm, low-profile socket head | [`93070A071`](https://www.mcmaster.com/93070A071) | Kickstand pivots |

Order one minimum package of each McMaster line. The quantities above are the
actual installed quantities, not the package quantities. Keeping two spare
screws of every short length and one spare M3 x 55 screw is recommended.

## Heat-set inserts

The current model uses two M3 insert depths: long inserts throughout the main
assembly and short inserts at the two kickstand pivots.

| Qty | Insert | McMaster-Carr | Used at |
|---:|---|---|---|
| 8 | M2.5 x 0.45, 4.0 mm installed length, 4.0 mm max hole | [`94459A768`](https://www.mcmaster.com/94459A768) | LIS3DH (4), X12-A1 brace (4) |
| 21 | M3 x 0.5, 5.7 mm installed length, 4.0 mm max hole | [`94459A140`](https://www.mcmaster.com/94459A140) | Sound detector (2), OPS (4), radar-brace anchors (2), IWR (4), housing joint (4), screen (4), battery door (1) |
| 2 | M3 x 0.5, 3.4 mm installed length, 4.0 mm max hole | [`94459A769`](https://www.mcmaster.com/94459A769) | Kickstand pivots |

### Required insert-fit validation

The Fusion reference geometry was built from a mixture of PEM/CNC Kitchen and
McMaster insert envelopes. The three McMaster numbers above are the proposed
single-supplier release standard, but the M2.5 short insert is not an exact
drop-in replacement for every earlier reference body. Before ordering a full
print, print a small boss coupon using the final slicer/profile and test:

- insertion temperature and depth;
- no boss splitting or wall bulge;
- screw engagement depth;
- pull-out and torque resistance.

Do not release the production STL files as “McMaster-compatible” until that
coupon passes. The nominal McMaster insert-pocket limit is 4.0 mm for M2.5/M3.
Camera and BME280 pilots are intentionally smaller because those screws form
threads directly in the printed plastic.

## Other mechanical hardware

| Qty | Item | McMaster / source | Notes |
|---:|---|---|---|
| 4 | M2.5 nylon washer, 2.7 mm ID x 5.0 mm OD x 0.3-0.5 mm thick | [`95630A310`](https://www.mcmaster.com/95630A310) | Install below the BME280 board |
| 2 | Press-fit spring-loaded ball plunger, 5 mm body, 4 mm ball, 1.4 mm travel | [`6052N13`](https://www.mcmaster.com/6052N13) | Kickstand; spring and ball are self-contained |
| 4 | M2.5 female/female brass spacer, 13.5 mm long | Included with Geekworm X1209 | Exact 13.5 mm stack height; do not substitute a 14 mm spacer without rechecking the stack |
| 1 | X1209 pogo pin | Included with Geekworm X1209 | Raspberry Pi 5 power-button interface |
| 1 | Waterproof DC 5.5 x 2.1 mm panel jack with cap | Amazon ASIN `B0DP6MNQQB` | Active port; housing hole is already modeled |
| 4 | Matched 18650 Li-ion cells | Battery supplier | Use one matched set specified for the X12-A1 |

Optional insert-installation tools:

- M2.5/M3 heat-set tip: McMaster [`92160A315`](https://www.mcmaster.com/92160A315)

The hidden Ethernet coupler and hidden X1209 barrel plug are retained only as
reference models. They are not required for the current build and are not part
of the purchasing total.

## Electronics and fitted assemblies

| Qty | Component | Exact design reference |
|---:|---|---|
| 1 | Single-board computer | Raspberry Pi 5 Model B |
| 1 | UPS HAT | Geekworm X1209 V1.0 |
| 1 | Battery carrier | Geekworm X12-A1 |
| 1 | Display | ROADOM 7-inch 1024 x 600 IPS touchscreen, Amazon `B07VNX4ZWY` |
| 1 | 24 GHz radar | OmniPreSense `OPS243-A-CW-RP` |
| 1 | 60 GHz radar | Texas Instruments `IWR6843LEVM` |
| 1 | Camera | InnoMaker `CAM-MIPIOV9281-V2`, 32 x 32 mm, 28 mm hole spacing |
| 1 | Environmental sensor | Adafruit BME280 STEMMA QT, product `2652` |
| 1 | Accelerometer | Adafruit LIS3DH STEMMA QT, product `2809` |
| 1 | Sound detector | 43.7 x 23.3 mm board, 3.5 mm mounting holes, 9.5 mm microphone envelope |

## Wiring not yet frozen

The enclosure geometry is ready for a mechanical release audit, but the exact
cable lengths and connector SKUs have not been frozen. A production-ready
electrical BOM still needs:

- Raspberry Pi camera FFC/CSI cable at the installed routing length;
- display video and power cables;
- OPS and IWR data/power cables;
- BME280 and LIS3DH STEMMA QT/I2C leads;
- sound-detector lead;
- DC panel-jack wire, terminals, heat-shrink, and fuse/protection choice.

Do not describe this document as the final electrical BOM until those are
measured in the assembled prototype.

## Printed parts: one each

1. Housing Back
2. Housing Front
3. Combined IWR/OPS Radar X-Brace
4. X12-A1 Reversible Perimeter Twin-X Cradle/Brace
5. X12-A1 Battery Door
6. Kickstand
7. Kickstand Peg — Left
8. Kickstand Peg — Right

The electronics, screws, inserts, washers, plungers, batteries, panel-port
models, and validation bodies are reference-only and must not be exported as
printable meshes.
