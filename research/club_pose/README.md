# club_pose (Stage 0A geometry core)

Pure-Python sandbox: clubhead 6-DOF body pose + club template -> golf metrics
(impact location, face angle, dynamic loft, club path, attack angle), validated
against analytic ground truth. Spec: docs/superpowers/specs/2026-06-28-club-pose-geometry-core-design.md

Run tests: `uv run pytest research/club_pose/tests/ -v`

## Modules
- `types` -- Measurement, ClubheadPose (validated), ClubMetrics
- `frames` -- angle decompositions (right/up positive), nominal camera
- `template` -- parametric curved-face template, loft override, projection
- `metrics` -- impact location, face angle, dynamic loft, club path, attack angle, compute_metrics
- `groundtruth` -- analytic oracle builders
- `sensitivity` -- error-budget sweeps (single-camera vs stereo)
