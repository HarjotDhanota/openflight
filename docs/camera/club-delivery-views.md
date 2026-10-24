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
before owner review. These aggregates use the existing local research.

## Coordinates and shot values

Right-handed top/heel-side views put the target to the left; left-handed views
put it to the right. In the top view the golfer and shaft are below the toe.
Positive face angle/path means right of target for either hand: above the line
on the RH drawing, below it on the LH drawing. Positive loft/attack is upward
for either hand. Arrows use their own unrounded metrics and a fixed 86 mm vector
length. Face-to-path and spin loft
are displayed directly from the shot, never recomputed from rounded numbers.
The head, ball and target mirror horizontally between hands. Top-view signed
rotations also reverse before mirroring, so the same physical azimuth retains
its sign. Labels and displayed signed values never flip. Shot camera metadata
supplies handedness when present; otherwise all three drawings use the current
kiosk hand, defaulting to RH only when neither is known. Historical shots without
hand metadata therefore follow the current kiosk setting.

Blade address silhouettes project the existing fair face contour by
`(x, y) -> (y sin(static loft), x)` about its centre. The metal shell adds the
2.4 mm edge band and a topline that recedes by its thickness times cosine of loft;
the heel neck is drawn separately from the planar blade. At the owner's request,
the address surface retains the face's projected grooves, clipped to the blade,
over metal shading with a light topline/edge band.
This replaces the first
round's rectangular blade outlines without changing face-on geometry or wood
head shapes. Short visible shaft sections and a viewport fitted to the rotated
head, ball and arrows keep the drawings readable without changing the head/ball
size ratio. In the heel-side view the hosel is nearest the golfer and is drawn
in front of the head for both hands. Drawing it behind the head would instead
give the overlap cue of a toe-side view. The blade retains its lofted face line,
rounded leading edge, sole/bounce and fuller back profile.
Top heads rotate by signed face angle so their normal follows the
face arrow. Side heads rotate by static loft minus dynamic loft around the
impact centre. The ball is 42.67 mm
in diameter and sits beyond the arrows on the target side; its position is
illustrative, not a measured ball location. The diameter uses the
[equipment-rule minimum](https://www.usga.org/equipment-standards/equipment-rules-2019/equipment-rules/part-4-rule-4.html),
not a measurement of the shot's ball. Ball and head share the same millimetre
scale. Each panel fits independently, so pixel diameters can differ between
panels and clubs while preserving physical proportions. The owner retained
this fitting on 24 October 2026. Browser tests recover ball diameter using the
head's screen transform for both projections, all clubs/hands and both kiosk
sizes, guarding against independent ball scaling or stretching.
The ball finish takes its white urethane appearance and dimple density from
the owner's [Pro V1 reference](https://www.titleist.com.sg/golf-balls/25-prov1).
Its 388 centres use original close packing with tetrahedral rotational symmetry,
expanded from 33 seed positions. They replace the first ball's spiral layout.
Radii vary with neighbour spacing, giving broader, closely packed groups.
A finite-distance cover projection (viewpoint five sphere radii from the centre)
magnifies front dimples and hides centres beyond the tangent horizon. The
silhouette remains 42.67 mm; this is an illustrative cover treatment within the
schematic, not the club's camera model. Shallow shaded ellipses compress toward
the silhouette; a broad highlight softens the cover. Dimple radii, coordinates,
viewpoint and shading are illustrative, not measured product geometry or a copy
of the manufacturer's layout. All detail stays inside the
42.67 mm outline, with screen-light direction preserved for both hands. No
logos, alignment markings, image textures or filter effects are used.
The side face normal and loft arrow
share the same angle. Spin loft is the backend's 3D face-normal/path separation,
not generally dynamic loft minus attack angle. Face-on still looks from the
target: RH toe left, LH toe right. The face-on assembly rotates about its
unchanged centre by the difference between its static hosel projection and
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

## Sign and mathematics audit (23 October 2026)

The same physical reading keeps the same number for either handedness. Changing
the viewing orientation is not the same operation as reflecting a physical shot.
The Club header now uses the displayed shot's club and resolved hand, matching
the drawings even if the kiosk selection has since changed.

| Reading | Positive direction or definition | Handedness effect on the number |
|---|---|---|
| Impact height | Above the face centre, along the face | None |
| Impact offset | Toward the toe, along the face | None |
| Dynamic lie | Shaft angle above the horizontal | None |
| Face angle, F | Right of the target | None |
| Club path, P | Right of the target | None |
| Face to path | F minus P, using unrounded inputs | None |
| Dynamic loft, L | Face normal tilted upward | None |
| Attack angle, A | Club velocity tilted upward | None |
| Spin loft, S | Unsigned 3D separation of face normal and velocity | None |

The official definitions confirm target-right-positive
[face angle](https://www.trackman.com/blog/what-is-face-angle),
[club path](https://www.trackman.com/blog/club-path), and
[face to path](https://www.trackman.com/blog/face-to-path) for both hands.
An open face therefore has positive face angle for RH and negative for LH.
An in-to-out path similarly has positive club path for RH and negative for LH.
These interpretation words do not introduce an extra sign flip in the data.

With degrees converted to radians for trigonometry, the backend uses:

```text
face normal n = (cos L cos F, cos L sin F, sin L)
club direction d = (cos A cos P, cos A sin P, sin A)
cos S = n dot d = cos L cos A cos(F - P) + sin L sin A
```

The implementation computes S with the stable equivalent
`2 asin(min(1, length(n - d) / 2))`, yielding 0 through 180 degrees.
[Spin loft is a 3D angle](https://www.trackman.com/blog/spin-loft):
`abs(L - A)` is exact only when the horizontal directions agree in the usual
golf angle range. Reflecting a physical shot negates F, P and face-to-path,
while preserving L, A and spin loft. Merely selecting the other handedness does
not alter any supplied metric. A new backend regression independently checks
252 combinations against `atan2(length(n cross d), n dot d)` and their mirrors.

For the owner's LH 3-wood example, the unrounded fixture has F = -3.17 degrees,
P = +4.23 degrees, L = 16.5 degrees and A = -4.2 degrees. It correctly displays
face angle -3.2, path +4.2, face-to-path -7.4 and spin loft 22.0 degrees.
The orange top-view arrow above the rightward target line represents target-left,
so its negative sign is correct. This is an open face for a left-handed golfer.

Face-on impact dots are tested in the rotated face's local axes, not against
screen horizontal/vertical. Browser checks recover the signed millimetres and
projected shaft angle from actual screen transforms for every club and hand.
The SVG viewport now encloses the rotated assembly; additional 45/75-degree
lie cases catch clipping that the old fixed viewport allowed. The paths, centre,
rim, grooves and physical scale relative to the impact dot are unchanged.

Dynamic lie now has an owner-approved [camera-based estimate](dynamic-lie.md),
always labelled model geometry. A missing shaft reading stays a dash. The main
review screenshots use that unavailable state; separate synthetic tilt fixtures
carry the MODEL label. Its visual projection follows the schematic rule above,
not a full shaft-lean reconstruction.
The top blade uses static category loft, while the side uses dynamic loft: these
are metric illustrations rather than three camera projections of a fitted 3D
club pose. This audit verifies signs, arithmetic and rendering against supplied
readings; it does not validate sensor calibration or the provisional populations.
