# Stage 0B-2 Results: Keypoint PnP Impact-Location Requirement

Command:

```powershell
& 'C:\Users\harjo\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe' run --group research python research/club_pose/sim/run_sweep_0b2.py > research/club_pose/sim/sweep_0b2.json
```

## Procedural Structured Driver

Gated requirement boundary from `kp_verdict`:

| mode | sigma_px | baseline_mm | px_per_mm | visible-set label | ok_rate | median impact_mm | meets 5 mm bar |
|---|---:|---:|---:|---:|---:|---:|---|
| mono | 3.0 | 150.0 | 3.749 | all keypoints, strict visibility | 1.00 | 3.113 | yes |

`kp_verdict` chooses the loosest passing `sigma_px`, which is the mono `sigma_px=3.0` cell. For the practical detector-feasibility band, the important cells are:

| mode | sigma_px | baseline_mm | px_per_mm | ok_rate | median impact_mm | meets 5 mm bar |
|---|---:|---:|---:|---:|---:|---|
| mono | 0.5 | 150.0 | 3.749 | 1.00 | 0.552 | yes |
| mono | 1.0 | 150.0 | 3.749 | 1.00 | 1.090 | yes |
| mono | 2.0 | 150.0 | 3.749 | 1.00 | 2.126 | yes |
| mono | 3.0 | 150.0 | 3.749 | 1.00 | 3.113 | yes |
| mono | 5.0 | 150.0 | 3.749 | 1.00 | 5.249 | no |
| stereo | 0.5 | 150.0 | 3.749 | 1.00 | 1.685 | yes |
| stereo | 1.0 | 150.0 | 3.749 | 1.00 | 3.364 | yes |
| stereo | 2.0 | 150.0 | 3.749 | 1.00 | 6.881 | no |
| stereo | 3.0 | 150.0 | 3.749 | 1.00 | 10.491 | no |
| stereo | 5.0 | 150.0 | 3.749 | 1.00 | 17.774 | no |

### Face angle / dynamic loft recovery (mono, committed via `experiment_kp.py` `face_err_deg`/`loft_err_deg`)

The same keypoint fit recovers face angle and dynamic loft — both well inside the ±2–3° bar — which is why 0B-2's conclusion is "detection-limited, not vantage-limited." Reproduce with `run_kp_experiment(mode="mono", sigma_px=s)` + `kp_verdict`:

| sigma_px | median impact_mm | median face_err_deg | median loft_err_deg | ok_rate |
|---:|---:|---:|---:|---:|
| 0.5 | 0.49 | 0.05 | 0.13 | 1.00 |
| 1.0 | 0.97 | 0.10 | 0.25 | 1.00 |
| 2.0 | 1.94 | 0.21 | 0.52 | 1.00 |
| 3.0 | 2.92 | 0.31 | 0.79 | 1.00 |

Baseline, resolution, and subset effects at `sigma_px=1.0`, stereo:

| factor | value | ok_rate | median impact_mm | meets 5 mm bar |
|---|---:|---:|---:|---|
| baseline_mm | 100.0 | 1.00 | 5.013 | no |
| baseline_mm | 150.0 | 1.00 | 3.364 | yes |
| baseline_mm | 200.0 | 1.00 | 2.565 | yes |
| px_per_mm | 1.875 | 1.00 | 6.881 | no |
| px_per_mm | 3.749 | 1.00 | 3.364 | yes |
| px_per_mm | 7.499 | 1.00 | 1.685 | yes |
| keypoints available | 12 | 1.00 | 3.364 | yes |
| keypoints available | 4 | 1.00 | 4.361 | yes |

## Silhouette Baseline

Same structured mesh, same pose/noise machinery, existing 0B-1 silhouette fitter:

| severity | n_ok / n | median impact_mm |
|---|---:|---:|
| light | 12 / 12 | 7.858 |
| realistic | 12 / 12 | 17.869 |

This is worse than the optimistic keypoint machinery at realistic `sigma_px` and supports the Stage 0B-1 conclusion that the silhouette cue is the wrong primitive for precise impact location.

## Real OBJ Check

Skipped:

```json
{"skipped": "no assets/driver.obj - procedural is the primary result"}
```

No fake OBJ was fabricated. The real-OBJ repeat remains the main residual validation gap.

## Decision

This run selects the **CUE problem** fork for the optimistic structured-keypoint sim: with strict normal visibility, failed solves counted, and `ok_rate=1.0`, plausible `sigma_px <= 1-2` keypoint localization reaches the 3-5 mm impact-location bar on the procedural structured driver.

The next gate is not more silhouette fitting. It is markerless detector feasibility: can a real behind-ball camera reliably detect enough named rear/crown/hosel keypoints on a smooth, specular, near-symmetric driver at roughly 1-2 px localization error? If that detector requirement is not achievable in real images, the project should re-scope to spin plus coarse impact zones or require markers and/or a side/overhead vantage for precise impact, face, and loft.
