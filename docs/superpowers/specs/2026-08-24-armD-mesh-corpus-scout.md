# Arm D mesh-corpus scout (2026-08-24)

**Scope decision (maintainer, 2026-08-24): the product mechanism is one shipped
canonical template per club type — zero user setup, no per-user scanning or
calibration.** Maintainer-scanned or maintainer-downloaded club meshes serve as
CORPUS and HELD-OUT VALIDATION data only, never as a user-facing flow. If
leave-one-club-out shows a category where one template cannot clear the gates,
the approved fallback is a small per-category template library (2–3 shapes) with
solver-side AUTO-SELECTION by fit residual over the first frames — still zero
user setup. Per-club user calibration is explicitly out of product scope.

Goal: 5–10 meshes per category (driver, iron) to build a category-mean template
and run leave-one-club-out validation under the frozen F1 criteria. Meshes are
NEVER committed — same local-use boundary as SOURCES.md; provenance + hashes only.

## Sketchfab candidates (CC, downloadable — API-verified metadata)

| Category | uid | Name | Author | Faces | Screen |
|---|---|---|---|---:|---|
| driver (have) | `978d0740…f0e3` | Callaway Maverik golf driver | paulekins2007 | 41,855 | acquired, validated |
| driver | `805c5f9e…fddb` | Callaway Maverik driver bundle | paulekins2007 | 6,779 | same club — low-poly variant; use only if geometry differs |
| ~~driver ×2~~ | `050418e0…7cc` | TaylorMade RBZ vs Callaway GBB | elaughli | 354,590 | **REJECTED (maintainer quality screen, 2026-08-24)** — geometry quality insufficient; archive downloaded, inspected, deleted |
| unknown club | `6d202f25…22d` | Golf Club | B_R_Brody | 91,904 | inspect type |
| unknown club | `8b990517…41d` | Golf Club 3D | Adroneltd | 991,963 | inspect type; heavy |
| unknown club | `5f83b786…aa5` | Golf Club | satinedean | 2,694 | inspect type |
| iron (superseded src) | `dc748ddd…3912` | Golf club Iron | real_slimshady | 1,864 | author-name validation currently fails (fraktur displayName); usable if validator keys on username |
| wedge/iron | `567173a8…469f` | Golf wedge iron | jollygolf | 11,277 | wedge — decide whether wedge joins iron category or its own |
| wedge/iron | `b869e0e2…a875c` | Golf-wedge-iron | mikethornley | 11,277 | identical face count to jollygolf — likely a re-upload; dedupe by geometry hash |

Rejected in sweep: game assets (Silent Hill, L4D2, Fortnite), low-poly toys
(<1.5k faces), courses, vehicles, Iron Man.

Net usable estimate after inspection: **3–4 drivers, 2–3 irons/wedges** — below
target on its own.

## GrabCAD (maintainer-download, local-use-only — the quality path)

Real CAD, mm-accurate, many club models. Redistribution-unclear, so the same
rule as the Titleist 690CB applies: maintainer downloads locally, never
committed, hash + provenance recorded. Action (maintainer): search GrabCAD for
"golf driver head", "golf iron", download 3–5 per category (prefer models with
STL or STEP; STL preferred — loader exists).

Already local: Titleist 690CB 7-iron (STL, 26,238 tris, hash f3593679…cb85).

## Thingiverse / Printables (secondary)

Printable club heads exist under CC-BY; dimensional accuracy varies (many are
stylized or scaled for printing). Use only to pad the corpus if still short
after GrabCAD; verify each model's dimensions against nominal category sizes
before admission.

## Corpus admission rules (pre-register before Arm D runs)

1. Real-club geometry (no toys/stylized), closed head + hosel visible.
2. Dimensions within ±15 % of category nominals after normalization (else reject).
3. Geometry-hash dedupe (re-uploads count once).
4. Provenance row per mesh: source, author, license/terms class, hash, admit/reject + reason.
5. Freeze the admitted corpus BEFORE building the mean or running leave-one-out.
