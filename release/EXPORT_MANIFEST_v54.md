# OpenFlight Enclosure v54 — Export Manifest

Source Fusion document:

`Updated OpenFlight Housing Back for X1209 + X12-A1 + LIS3DH + BME280 v54`

## Printable release set

Export each part individually in both 3MF and STL. Also export one editable
STEP assembly containing the eight parts as separate components.

| Release filename stem | Fusion component/body |
|---|---|
| `OpenFlight_Housing_Back_v54` | Housing Back under `Housing-Back` |
| `OpenFlight_Housing_Front_v54` | `Housing Front - IUTB Insert Ready (1)` |
| `OpenFlight_Radar_X-Brace_v54` | `PRINTABLE - Combined IWR-OPS X-Brace - M3 Standardized` |
| `OpenFlight_X12_Cradle_Brace_v54` | `X12 Reversible Perimeter Twin-X Brace - M2.5 Clearances` |
| `OpenFlight_X12_Battery_Door_v54` | `X12 Battery Door - Single Solid` |
| `OpenFlight_Kickstand_v54` | `Kick-Stand` |
| `OpenFlight_Kickstand_Peg_Left_v54` | `Kick-Stand-Peg` |
| `OpenFlight_Kickstand_Peg_Right_v54` | `Kick-Stand-Peg-R` |

## Required release package

```text
OpenFlight_Enclosure_v54/
  source/
    OpenFlight_Enclosure_v54_Source.f3d
    OpenFlight_Enclosure_v54_Printables.step
  3mf/
    OpenFlight_Housing_Back_v54.3mf
    OpenFlight_Housing_Front_v54.3mf
    OpenFlight_Radar_X-Brace_v54.3mf
    OpenFlight_X12_Cradle_Brace_v54.3mf
    OpenFlight_X12_Battery_Door_v54.3mf
    OpenFlight_Kickstand_v54.3mf
    OpenFlight_Kickstand_Peg_Left_v54.3mf
    OpenFlight_Kickstand_Peg_Right_v54.3mf
  stl/
    matching eight STL files
  docs/
    OpenFlight_Enclosure_v54_Master_BOM.md
    OpenFlight_Enclosure_v54_McMaster_Order.csv
    Assembly_Guide.md
    Print_Settings.md
    LICENSE.txt
  images/
    cover.png
    exploded.png
    hardware-map.png
    print-orientations.png
```

## Never include in printable meshes

- Raspberry Pi, X1209, X12-A1, display, camera, BME280, LIS3DH, sound detector,
  OPS, or IWR reference models;
- screw, insert, washer, spacer, plunger, battery, DC-jack, Ethernet, or plug
  reference bodies;
- kickstand motion/validation bodies;
- construction sketches, collision envelopes, archived geometry, or hidden
  duplicate bodies.

The Raspberry Pi model must remain hidden during preparation and export.

## Fusion cleanup required before export

1. Save a new Fusion version only after the user approves the final geometry.
2. Correct any stale reference-component labels: the active assembly contains
   31 heat-set inserts and 43 screws. Camera and BME280 mounts now use printed
   direct-thread pilots instead of inserts.
3. Hide/exclude the visible kickstand validation bodies:
   - `KICKSTAND VALIDATION - Right Plunger Fixed`
   - `KICKSTAND VALIDATION - Right Ball Nose`
   - `KICKSTAND VALIDATION - Left Plunger Fixed`
   - `KICKSTAND VALIDATION - Left Ball Nose`
4. Select each printable component explicitly; never export the root assembly
   directly to STL/3MF.
5. Run a final section/interference audit with Housing Front, Housing Back,
   screen, camera, X12-A1, both radars, and both panel locations represented.
6. Confirm each printable component contains one watertight solid and no
   zero-thickness faces, floating fragments, or external-wall artifacts.
7. Confirm the final insert-pocket coupon before labeling the files as
   compatible with the proposed McMaster insert set.

## Pre-export dimensional checks

- Housing Front/Back mating clearance and four M3 joint counterbores.
- Screen opening clearance: 0.6 mm per edge.
- Camera opening follows the 11-degree camera axis and clears the lens/cap.
- Four camera holes are centered on the M2 standoffs at 28 mm spacing and use
  Ø1.60 mm printed direct-thread pilots.
- BME280 is aligned to the selected cable-management slot centerline and its
  four mounts use Ø2.00 mm printed direct-thread pilots.
- Screen is centered left-to-right and all four screen mounts/cable passages
  moved with it.
- X12-A1 cradle/door clears the neighboring screen standoff by at least 1 mm.
- X12-A1 brace holes remain M2.5 and are centered on the housing standoffs.
- IWR and OPS remain in their approved positions; brace holes align without
  moving either radar.
- Kickstand reaches the verified stowed and 10-degree-enclosure deployment
  positions, with ball plungers engaging both detents.
- DC panel jack and hidden optional references do not collide with installed
  electronics.

## Suggested mesh export settings

- Units: millimeters.
- Format priority: 3MF first, STL fallback.
- Refinement: high; use a custom chord height of 0.05-0.10 mm for the camera
  flare, enclosure fillets, and kickstand pivots.
- Normal deviation: 5 degrees or finer.
- Export one body/component per file in its intended print orientation.

No geometry was saved or exported while creating this manifest.
