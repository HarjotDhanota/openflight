# Codex brief — the mesh is mirrored and `square_pose()` is rolled ~90°; fix both, then re-run the outline alignment

**Branch:** `feat/clubpose-fit` @ `0456554`. This supersedes the outline-alignment spike result
(`docs/clubface-impact-location.md`, "Outline alignment — spike result"): that 0/21 was produced
with a template that was **mirrored and rolled ~90° from the real club**, so it says nothing
about the pixels. Keep the recorded result; add a paragraph saying why it is void.

## What was found (2026-08-28 evening, rendered and verified on shot 014 f71/f72)

1. **`square_pose()` renders a club rolled ~90°.** `delivered_angles` defines lie as
   `asin(|shaft_z|)` — the shaft's elevation — with no constraint on the sole line. Given loft and
   face angle (face normal fixed), the roll about the face normal is left with a discrete set of
   solutions that all satisfy "shaft elevation = 61.19°"; the one `square_pose()` returns has the
   heel–toe line at **73° elevation** (toe up ~41°). Rendered from the measured camera it is a
   21 × 37 px tall shape; the real head is ~30 × 13 px wide. Every alignment in the spike used this
   template — hence the locks on shadow/shaft and the `search_boundary` hits.
2. **The mesh is a left-handed club in its normalized frame.** Measured on `poc_7iron.npz`:
   face normal −x (2941 mm² planar patch, 80.6 mm heel–toe), hosel/ferrule at +y extending along
   `SHAFT_LOCAL` (head → grip), and the heel–toe axis of the face patch is `e_ht ≈ (0, +1, 0.06)`
   toward the heel. With face normal → `(cos 33°, 0, sin 33°)` (downrange, lofted) and heel → −y
   (golfer on the left, as the un-mirrored frames show), the **only** rotation that satisfies both
   sends the shaft to `(0.90, −0.22, −0.38)` — downrange and **into the ground**. A rotation cannot
   fix this; mirroring local z can: the same construction on the z-mirrored mesh gives the shaft
   at `(0.02, −0.22, 0.98)` — up, toward the golfer, no forward lean — and its outline lands on
   the real head at f71 **with no fitting at all** (hosel up-left along the real shaft, sole on the
   real sole). `normalize_clubhead` is not the cause: its axes have det = +1 and the signed volume
   is positive. The STL itself is LH or a mirrored export, whatever its GrabCAD title says.
3. **The mesh's own lie is 76°, not 61°.** Angle between `SHAFT_LOCAL` and the face-patch
   heel–toe axis is 76.2°; when soled, the shaft rises at 77.5°. A real 690CB is ~62–63°; the
   observed shaft in f71 rises at ~51° in the image. The hosel/ferrule geometry of this mesh is
   suspect (also flagged in `docs/Personal Research/research-2026-08-28/C_club_model.md`). Do
   not treat the mesh's hosel as the shaft reference; the head outline is what fits.
4. Consequently the 2026-08-27 fused refit ("0 of 6 shots in the envelope", face angles +5…+44°)
   and any `in_envelope` verdict were computed in the same wrong frame and are void too.

## Deliverables

1. **`mesh.py` / asset metadata:** an explicit `handedness` field on the normalized mesh, and a
   `mirror_to_right_handed()` step (flip local z, reverse triangle winding, re-derive the face
   plane) applied at load for this asset. Record the decision in `meshes/SOURCES.md` and the
   manifest. Test: after loading, the soled-club construction in §2 must put the shaft **up and
   toward −y** with |forward lean| < 15°.
2. **`angles.py`:** add a measured `HEEL_TOE_LOCAL` axis (from the striking-face patch, as in §2;
   store how it was measured) and a `SOLE_TILT` (toe-up) angle in `delivered_angles`. Define lie
   the golf way — shaft elevation **when the sole line is horizontal** — and make `square_pose()`
   solve for face normal + horizontal sole + heel toward −y, so it is unique. `in_envelope` gains
   `sole_tilt_deg ∈ [−15, +15]`. Tests: `square_pose()` renders wider than tall from
   `measured_camera()`; its shaft points up-left; the old rolled solution is rejected by the
   envelope.
3. **Re-run the outline-alignment spike unchanged otherwise** (same checks, same thresholds,
   sheets first). Report the result as before. Expectation to be tested, not assumed: with the
   right template the localisation should at least stop locking onto the shaft and shadow.
4. Append to `docs/clubface-impact-location.md`: why the first result was void, what changed,
   the new result. No accuracy claim — still no truth.

## Do not

Re-tune thresholds; touch `club_delivery.py` or the server; use the mesh hosel as the shaft
reference (use the image shaft line for lie, the mesh only for the head outline).
