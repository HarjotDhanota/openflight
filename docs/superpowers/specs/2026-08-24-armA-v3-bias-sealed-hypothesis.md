# Sealed hypothesis — Arm A-v3 systematic vertical bias

**Sealed 2026-08-24 by Claude BEFORE Codex's independent investigation.**
Purpose: allow an honest convergence/divergence comparison between two
independent diagnoses. Codex must NOT be shown this file until it has filed
its own root-cause findings.

## Observations being explained (facts, not interpretation)

- Arm A-v3 (exact mesh projection, corrected 690CB): ambient solve 0.840,
  impact median 9.152 mm, p90 11.577 mm.
- Signed: height median 8.594 / p90 11.274 mm; offset median -2.354 / p90 0.282 mm.
  The total error is dominated by a same-sign, tightly clustered VERTICAL term.
- IoU median 0.722 — the worst of every arm run to date, below the analytic
  ellipse on mesh truth (~0.94) and far below analytic-on-analytic (0.975).
- Fit residual median 5.903 px for an EXACT model.
- Truth geometry == fit geometry in this arm (both the corrected 690CB), i.e.
  this is the matched case, which should approach the 1.090 mm matched
  reference.

## Sealed primary hypothesis

**A vertical origin-convention mismatch between the truth renderer and the
A-v3 observation model.** The corrected iron mesh spans 96.9 mm in height
*including the hosel* while its face is only 55.2 mm tall. If the truth side
places/reports the club about one reference (face center or the impact point)
while the A-v3 projection is built about another (mesh centroid or bounding-box
center), the constant offset between those origins is of exactly the observed
~8–10 mm magnitude. The hosel is what makes the two origins diverge, and it
entered only with the corrected mesh — matching when this bias appeared.

Predicted signature if correct: a zero-noise, zero-jitter, truth-mesh ==
fit-mesh diagnostic cell still returns ~8–9 mm height error with IoU well
below 0.9, and the residual error is near-constant across poses.

## Sealed secondary hypotheses (ranked)

2. Face-plane/face-center derivation differs between the mesh normalizer's
   recorded face frame and what `truth.json` reports as `face_vector_mm`.
3. The 1% calibrated dimension residual is applied to one side only, scaling
   the tall (hosel-bearing) axis and displacing the face center.
4. A hosel-inclusion mismatch: truth renders the full head+hosel silhouette
   while the observation model's projection (or its contour/moment extraction)
   includes or excludes the hosel differently, biasing the centroid upward.

## Falsification

If the zero-noise diagnostic returns ~0 mm with IoU ~0.99, every hypothesis
above is wrong and the defect is pose/noise-dependent rather than a constant
frame offset.
