# Club delivery drawings

The three kiosk views are original schematic population shapes in millimetres.
They contain no product artwork, logos, or model-specific surface patterns.
Face-on geometry remains in `faceImpact.ts`; rendering changes do not alter its
centre, outline, even rim, score lines or equipment checks.

At 800×480 the views use swipe navigation, and the side view looks **from the
heel** (the golfer side). At 1024×600 the three views sit alongside one another.
Smaller screens also use swipe navigation, with tap alternatives.

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
research selection's modern-club assumption, pending an audit. Iron sole
width: 21 models, 20.60 ± 2.17 mm; topline: 12, 5.83 ± 1.29 mm; bounce: 24,
6.09 ± 2.42°; offset: 47, 2.97 ± 0.97 mm. Wedge sole width: only 3 models,
23.84 ± 3.89 mm; bounce: 24, 10.44 ± 1.15°; offset: 5, 1.45 ± 0.91 mm.
Hosel-axis progression is 6 mm (assumed hosel radius) minus offset for blades.

*No published wedge topline value is present. The iron aggregate is a temporary
visual proxy, not a claimed wedge population measurement. Back profiles and
small edge rounding are illustrative. These sparse inputs are unverified.
These aggregates use the existing local research.

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
the heel neck is drawn separately from the planar blade. The address surface
carries the face's projected grooves, clipped to the blade, over metal shading
with a light topline/edge band. Face-on geometry and wood head shapes are shared
with the face view. Short visible shaft sections and a viewport fitted to the rotated
head, ball and arrows keep the drawings readable without changing the head/ball
size ratio. In the heel-side view the hosel is nearest the golfer and is drawn
in front of the head for both hands. Drawing it behind the head would instead
give the overlap cue of a toe-side view. The blade retains its lofted face line,
rounded leading edge, sole/bounce and fuller back profile.

The top line runs into the hosel as on a real iron, with no notch in any view. Face-on, the
iron's straight top line carries on into one circular fillet tangent to it and to the tube's
toe-side edge at the tube's foot (about 11 mm radius). The wedge does the same from its node
72 mm heel-ward of the toe, where its curved top line still rims the face evenly: it carries
straight on along its own tangent into a fillet of about 20 mm, so the old steepening and
tight turn just before the neck are gone; that node keeps its slope, so the outline up to it
is unchanged to the bit. Only heel-end nodes moved: the face centre, toe extreme, lowest sole
point, blade length and face height are unchanged, and so is every part of
`club_outline_reference.json` that impact placement fits (it never fits the heel end). The
iron face's rounded top-heel corner now sits under that fillet, so the even-rim check stops
where the face's straight top line starts. In the top view each neck edge (top-line side
and leading-edge side) leaves the blade's edge band tangent to it, toe-ward of the face's
top-heel corner, and sweeps into the ferrule in one cubic with no corner (tightest turn
about 12 mm on the iron's back edge, 8 mm on the wedge's; the band is the face outline's
2.4 mm stroke carried through the top view's projection, so it is narrower across the
blade than along it). The tube's cylindrical shading stays on the tube, so that sweep takes
the head's metal. In the heel view the hosel stands in front of the lofted face and meets the head only
at the neck, low on the blade where its back edge crosses the face line: the gap between the
hosel and the top of the face is open, as on a real club.
The top view is titled "Top at impact" because it depicts impact, not address:
its head is turned by the shot's face angle at impact, its arrows are the face
angle and club path at impact, and its numbers are face to path, face angle and
club path. Only a shot with no face angle draws the head square.
Top heads rotate by signed face angle so their normal follows the
face arrow. Side heads rotate by the club's static loft minus dynamic loft around the
impact centre. The ball is 42.7 mm
in diameter and sits beyond the arrows on the target side; its position is
illustrative, not a measured ball location. Ball and head share one millimetre
scale; each panel fits its own viewport, so pixel diameters differ between panels
while proportions hold. Browser tests recover the ball diameter through the head's
screen transform for every club, hand and kiosk size. The cover is original
artwork: 388 dimples in tetrahedral close packing, eight illustrative diameter
classes from 2.8 to 4.5 mm, shaded recesses and a broad highlight, all inside the
ball outline. It is not a manufacturer's dimple layout; there are no logos,
alignment marks, image textures or filters.
The side face normal and loft arrow
share the same angle. Spin loft is the backend's 3D face-normal/path separation,
not generally dynamic loft minus attack angle. Face-on still looks from the
target: RH toe left, LH toe right. The face-on assembly rotates about its
unchanged centre by the difference between its static hosel projection and
`atan2(sin(dynamic lie) × cos(loft), cos(dynamic lie))`, using dynamic loft
when available and otherwise the category's static loft.

Each selectable club type has its own static loft and lie (`CLUB_STATIC` in
`ui/src/components/panel/clubDelivery.ts`). The side view is the category's profile built at
the club's own static loft (`clubProfilePaths(category, loft)`): the face line runs at that
loft, the sole stays on the ground and the shaft upright, and only what the profile derives
from loft (the face's projection, the blade's depth) changes; nothing is rotated to stand in
for a loft. A shot's dynamic loft turns the head from that static loft as before. The
face view never tilts for a static value: at address every club's face sits the same way,
and a shot's measured dynamic lie tilts it by its difference from the club's own static lie
(both seen along the face normal). A shot's dynamic loft or lie always wins. All values are population averages from the official spec tables
in research/club-models: the saved OEM set and wedge tables, and for the 2-iron, 5-wood,
7-wood and 5-, 7- and 9-hybrid official OEM spec pages and sell sheets of 2022+ models (each
row says how many). Only an unknown club name takes its category's values.

| Club | Loft | Lie | Source |
|---|---|---|---|
| 2-iron | 17.6 | 59.7 | mean of 5 utility/driving irons listed as #2: PING iDi, Srixon ZXiU and ZX Mk II Utility, Callaway Apex UT (2024), TaylorMade P·UDI (a modern 2-iron is a utility iron) |
| 3-iron | 18.5 | 60.1 | 7-iron category + mean offset from each model's 7-iron, 7 OEM set tables (Cobra, Mizuno, Srixon, Wilson pages in club-geometry/irons) |
| 4-iron | 21.7 | 60.7 | same method, 13 set tables |
| 5-iron | 24.6 | 61.3 | same method, 13 set tables |
| 6-iron | 27.8 | 61.8 | same method, 13 set tables |
| 7-iron | 31.4 | 62.3 | the iron category: 56 official 7-iron specs (club-geometry/irons-3view/official_specs.csv) |
| 8-iron | 35.5 | 62.9 | same method as 3-iron, 13 set tables |
| 9-iron | 39.8 | 63.4 | same method, 13 set tables |
| PW | 44.4 | 63.9 | same method, 13 set tables |
| GW | 49.5 | 64.2 | same method, 9 set tables with a GW |
| SW | 56.0 | 64.0 | official wedge tables, 56 deg rows (24 models; official_specs.csv) |
| LW | 59.0 | 64.0 | official wedge tables, 58 and 60 deg rows (24 models each) |
| Driver | 10.4 | 57.4 | the driver category: 13 OEM specs (club-geometry/woods/data/specs_oem.csv) |
| 3-wood | 14.9 | 56.6 | the fairway category: 8 OEM 3-wood specs (specs_oem.csv) |
| 5-wood | 18.1 | 57.5 | mean of 7 models: PING G440 Max, Callaway Elyte and Quantum Max, Srixon ZXi RKT, TaylorMade Qi35, Tour Edge Exotics Max, Wilson DynaPWR Max |
| 7-wood | 21.0 | 58.1 | the same 7 models |
| 3-hybrid | 18.8 | 57.7 | the hybrid category: 8 OEM 3H specs (specs_oem.csv) |
| 5-hybrid | 25.2 | 59.4 | mean of 14 models: PING G440; Callaway Elyte, Elyte X, Quantum Max, Big Bertha (2023); Cobra DS-ADAPT; TaylorMade Qi35, Qi35 Max, Qi10, Qi10 Max, Stealth 2, Stealth 2 HD, Stealth; Tour Edge Exotics Max |
| 7-hybrid | 32.2 | 60.6 | mean of 9 models: PING G440; Callaway Elyte X, Quantum Max, Big Bertha; TaylorMade Qi35 Max, Qi10, Qi10 Max, Stealth 2, Stealth |
| 9-hybrid | 39.1 | 61.6 | only one model lists a 9H (Callaway Big Bertha 2023: 36 / 62), so the 7H mean plus two per-number steps (3.4 deg, 0.5 deg per number, the mean 5H-to-7H gap of the 9 models with both) |
| unknown name | category | category | the category the club name maps to |

Adjustable heads list either one lie (PING's is the average over its hosel positions) or a
range; a TaylorMade range is read at its low end, the standard sleeve setting, as the
existing wood specs read it. Per-model rows, URLs and the aggregation are in
research/club-models/research/per-club-loft-lie/ (`fallback_rows.csv`,
`aggregate_fallbacks.py`, `per_club_fallbacks.json`).

The extraction and aggregation scripts and the per-row data are in
research/club-models/research/per-club-loft-lie/ (local only).

Missing, non-finite and `not_measured` records display a dash and no corresponding
arrow. Missing face angle gives a square top view; missing dynamic loft or lie
keeps the club's static address orientation. A missing companion metric does not
erase an available reading. Every available number retains the shot's provenance
badge. The original detailed metric cells remain below the drawings.

Validation is through unit tests and mock browser captures, not hardware or
human validation. Review screenshots live outside the repository in the shared
`research/club-models/codex-rounds/` folder.

## Sign and mathematics audit

The same physical reading keeps the same number for either handedness. Changing
the viewing orientation is not the same operation as reflecting a physical shot.
The Club header uses the displayed shot's club and resolved hand, matching
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
not alter any supplied metric. A backend regression independently checks
252 combinations against `atan2(length(n cross d), n dot d)` and their mirrors.

For an LH 3-wood example, the unrounded fixture has F = -3.17 degrees,
P = +4.23 degrees, L = 16.5 degrees and A = -4.2 degrees. It correctly displays
face angle -3.2, path +4.2, face-to-path -7.4 and spin loft 22.0 degrees.
The orange top-view arrow above the rightward target line represents target-left,
so its negative sign is correct. This is an open face for a left-handed golfer.

Face-on impact dots are tested in the rotated face's local axes, not against
screen horizontal/vertical. Browser checks recover the signed millimetres and
projected shaft angle from actual screen transforms for every club and hand.
The SVG viewport encloses the rotated assembly; 45/75-degree lie cases catch
clipping. The paths, centre,
rim, grooves and physical scale relative to the impact dot are unchanged.

Dynamic lie has a [camera-based estimate](dynamic-lie.md),
always labelled model geometry. A missing shaft reading stays a dash. The main
review screenshots use that unavailable state; separate synthetic tilt fixtures
carry the MODEL label. Its visual projection follows the schematic rule above,
not a full shaft-lean reconstruction.
The top blade uses static category loft, while the side uses dynamic loft: these
are metric illustrations rather than three camera projections of a fitted 3D
club pose. This audit verifies signs, arithmetic and rendering against supplied
readings; it does not validate sensor calibration or the provisional populations.
