# Club delivery drawings

The three kiosk views are original schematic population shapes in millimetres.
They contain no product artwork, logos, or model-specific surface patterns.
Face-on geometry remains in `faceImpact.ts`; rendering changes do not alter its
centre, outline, even rim, score lines or equipment checks.

The owner chose swipe navigation at 800×480 and a side view **from the heel**
(the golfer side) on 23 October 2026. At 1024×600 the three views sit alongside
one another. Smaller screens also use swipe navigation, with tap alternatives.

## Dimensions and evidence

These are rounded category aggregates from the local club-models research.
Raw per-model tables, downloaded photographs and population outline files remain
local and are not dependencies of the UI. The curves are independently drawn
from dimensions, not copied or traced outlines.

| mm unless noted | Driver | 3-wood | Hybrid | 7-iron | Wedge |
|---|---:|---:|---:|---:|---:|
| Head/blade length | 117 | 105 | 101 | 79.4 | 80 |
| Centre heelward from toe | 66.39 | 56.10 | 52.29 | 44.24 | 45.00 |
| Front-to-back wood depth | 114.5 | 87.9 | 68.5 | — | — |
| Wood crown height | 62 | 40.6 | 37.5 | — | — |
| Face height along face normal view | 50.3 | 30 | 28.2 | 44.4 | 51.8 |
| Static loft, degrees | 10.4 | 14.9 | 18.8 | 31.4 | 56.3 |
| Static lie, degrees | 57.4 | 56.6 | 57.7 | 62.3 | 64 |
| Front edge to hosel axis | 16.2 | 15.4 | 15.3 | 3.03 | 4.55 |
| Topline thickness | — | — | — | 5.83 | 5.83* |
| Sole width | — | — | — | 20.60 | 23.84 |
| Bounce, degrees | — | — | — | 6.09 | 10.44 |

Face dimensions are the shared README §6 population values. Wood depth, crown
and progression use the §7 draft `woods-3view/avg_views.json` recommended
aggregates (side samples 16/13/10; plan samples 12/9/7). These remain provisional,
not independently verified measurements. The draft driver plan width includes
the neck and exceeds 127 mm; the head drawing instead uses the established
117 mm face-round head length. This is not an equipment certification.
Top-view toe/heel extents are asymmetric around the impact station, using the
shared reference's centre-from-toe values; tests cross-check these and face
heights against `face_centre_reference.json`.

Iron/wedge figures use `irons-3view/official_specs.csv`: parse ranges by midpoint,
average entries within a brand/model, then give models equal weight. Exclude any
explicit release year before 2021. Some rows have no release year and retain the
research selection's modern-club assumption, pending Claude's audit. Iron sole
width: 21 models, 20.60 ± 2.17 mm; topline: 12, 5.83 ± 1.29 mm; bounce: 24,
6.09 ± 2.42°; offset: 47, 2.97 ± 0.97 mm. Wedge sole width: only 3 models,
23.84 ± 3.89 mm; bounce: 24, 10.44 ± 1.15°; offset: 5, 1.45 ± 0.91 mm.
Hosel-axis progression is 6 mm (assumed hosel radius) minus offset for blades.

*No published wedge topline value is present. The iron aggregate is a temporary
visual proxy, not a claimed wedge population measurement. Back profiles and
small edge rounding are illustrative. Claude should verify these sparse inputs
before owner review. No additional web research was performed this round.

## Coordinates and shot values

Right-handed top/heel-side views put the target to the right. Top view puts the
toe above the hosel. A positive face angle/path points below the target line
(right of target); positive loft/attack points above it. Arrows use their own
unrounded metrics and a fixed 86 mm vector length. Face-to-path and spin loft
are displayed directly from the shot, never recomputed from rounded numbers.
Left-handed mode mirrors the entire illustration horizontally, including the
ball, target, head and arrows; labels and displayed signed values remain readable.

Top heads rotate by face angle around the face-centre station. Side heads rotate
by static loft minus dynamic loft around the impact centre. The ball is 42.67 mm
in diameter and sits beyond the arrows on the target side; its position is
illustrative, not a measured ball location. The side face normal and loft arrow
share the same angle. The face-on assembly rotates about its unchanged centre
by the difference between its static hosel projection and
`atan2(sin(dynamic lie) × cos(loft), cos(dynamic lie))`, using dynamic loft
when available and otherwise the category's static loft.

Missing, non-finite and `not_measured` records display a dash and no corresponding
arrow. Missing face angle gives a square top view; missing dynamic loft or lie
keeps the club's static address orientation. A missing companion metric does not
erase an available reading. Every available number retains the shot's provenance
badge. The original detailed metric cells remain below the drawings.

Validation is through unit tests and mock browser captures, not hardware or
human validation. Review screenshots live outside the repository in the shared
`research/club-models/codex-rounds/` folder.
