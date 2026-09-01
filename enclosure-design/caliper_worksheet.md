# Enclosure Caliper Worksheet
*Tools: digital calipers, spirit level nearby but not needed today. Record in mm to 0.1. Fill the blanks, then transfer to `params.py`.*

## 1. ROADOM 7" display (gates the display frame)

| # | Measurement | Value |
|---|---|---|
| 1.0 | **Mounting style: protruding tabs / rear bosses / both?** (photo helps) | ______ |
| 1.1 | Outer width × height × thickness | ______ × ______ × ______ |
| 1.2 | Mount hole Ø and thread (M3? M2.5?) | Ø ______ , M ______ |
| 1.3 | Hole spacing: center-to-center X and Y | ______ × ______ |
| 1.4 | Hole inset from panel edges (X, Y) | ______ , ______ |
| 1.5 | Active area width × height | ______ × ______ |
| 1.6 | Bezel width: top / bottom / left / right (often asymmetric) | __ / __ / __ / __ |
| 1.7 | Front glass lip height above bezel | ______ |
| 1.8 | Max rear protrusion + what it is (speaker? connector?) | ______ , ______ |
| 1.9 | Connector positions from a corner: HDMI, touch micro-USB, 3-pin header; and exit direction (rear/edge) | ______ |
| 1.10 | Plug clearance depth (connector + cable bend) | ______ |
| 1.11 | Rear Pi-mount boss pattern if present (58×49?) | ______ |

## 2. InnoMaker CAM-IMX296 (gates camera bracket)

| # | Measurement | Value |
|---|---|---|
| 2.1 | Mount hole Ø | ______ |
| 2.2 | Hole pattern X × Y (verify vs 30×30) | ______ × ______ |
| 2.3 | PCB thickness | ______ |
| 2.4 | PCB back → lens-mount front face (stack height) | ______ |
| 2.5 | Lens barrel OD | ______ |
| 2.6 | Optical axis offset from hole-pattern center (X, Y) | ______ , ______ |
| 2.7 | FPC connector exit side | ______ |
| 2.8 | J2 (strobe) / J3 (trigger) positions from a corner | ______ |
| 2.9 | Back-focus travel (lens fully in vs out) | ______ |

## 3. SEN-14262 sound detector (gates acoustic port)

| # | Measurement | Value |
|---|---|---|
| 3.1 | Board L × W | ______ × ______ |
| 3.2 | Hole Ø and XY from board corner | Ø ______ , ______ |
| 3.3 | **Mic capsule center XY from same corner** | ______ , ______ |
| 3.4 | Capsule OD and height | ______ , ______ |
| 3.5 | Header position + total height with headers | ______ |

## 4. Pi 5 + X1206 stack (gates power bay)

| # | Measurement | Value |
|---|---|---|
| 4.1 | **Total stack height**: X1206 underside → tallest point on Pi (incl. cooler) | ______ |
| 4.2 | Cell-holder protrusion below X1206 PCB | ______ |
| 4.3 | Connector positions from a board corner: DC jack, USB-C in, USB-A ×2, XH2.54 | ______ |
| 4.4 | Clearance needed to swap a 21700 (finger/cell extraction path) | ______ |
| 4.5 | X1206 mount hole coords if not on 58×49 Pi pattern | ______ |

## 5. IWR6843LEVM (skip if TI Altium package obtained — free login at ti.com/tool/IWR6843LEVM → Design files)

| # | Measurement | Value |
|---|---|---|
| 5.1 | PCB W × H, thickness | ______ × ______ , ______ |
| 5.2 | Hole Ø + positions from a corner | ______ |
| 5.3 | Antenna patch region bounding box + which face | ______ |
| 5.4 | **S1.1 boot switch + RESET button positions** (F26 access cutout) | ______ |
| 5.5 | Connector heights: micro-USB, 60-pin Samtec ×2 | ______ |
| 5.6 | Max component height, both faces | ______ |
| 5.7 | Draw @5V with inline USB meter: idle / streaming | ______ / ______ |

## 6. Not calipers — desk tasks

- Pi 5 connector Z-heights: open the official STEP (link in `enclosure_spec.md` §3) and measure in CAD.
- OPS243: use `openflight/cad/OPS243.stp` — already in repo.
- QT cable reach: after mount plate position is set in CAD, pick 100mm (4210) or 200mm (4401).
