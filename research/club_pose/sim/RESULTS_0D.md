# Stage 0D Results: D-plane Inversion Error Budget

Command:

```powershell
& 'C:\Users\harjo\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe' run --group research python research/club_pose/sim/run_budget_0d.py --n 2000 --seed 0 --compact
```

Baseline per cell: launch direction `sigma_launch = 0.5 deg`, club path/attack `sigma_path = 1.0 deg`, spin axis `sigma_axis = 5.0 deg`, frame bias `b_frame = 0 deg`, coefficient envelope width `1.0`, and camera impact `sigma_impact = 3 mm` for `gear=camera`. Spin rate is forward-model output only and is not budgeted. All public angles are degrees. All cells had `ok_rate = 1.00`.

## Combined Baseline Budget

### Driver

| Gear mode | Face launch-route | Face axis-route | Face fused | Dynamic loft | Axis-route face-to-path |
|---|---:|---:|---:|---:|---:|
| none | 1.16 deg | 2.33 deg | 1.01 deg | 0.80 deg | 2.16 deg |
| camera | 0.63 deg | 1.41 deg | 0.48 deg | 0.63 deg | 1.17 deg |
| perfect | 0.44 deg | 1.10 deg | 0.36 deg | 0.55 deg | 0.82 deg |

### Iron

| Gear mode | Face launch-route | Face axis-route | Face fused | Dynamic loft | Axis-route face-to-path |
|---|---:|---:|---:|---:|---:|
| none | 0.66 deg | 3.20 deg | 0.61 deg | 2.16 deg | 3.09 deg |
| camera | 0.60 deg | 3.15 deg | 0.57 deg | 2.12 deg | 3.12 deg |
| perfect | 0.63 deg | 2.89 deg | 0.59 deg | 2.20 deg | 2.79 deg |

Route-only tables are the source of truth. Fusion is secondary and uses fixed baseline-sigma inverse-variance weights.

## Receiver launch-direction requirement

Primary requirement is under `gear=camera` because that is OpenFlight's intended architecture. Values are the loosest swept input sigma that still held the launch-route face median under the target with other inputs at baseline.

| Club | Gear | Face <= 1.5 deg | Face <= 2.5 deg |
|---|---|---:|---:|
| driver | camera | 1.0 deg | 2.0 deg |
| iron | camera | 1.0 deg | 2.0 deg |
| driver | none | 1.0 deg | 2.0 deg |
| iron | none | 1.0 deg | 2.0 deg |

Headline: receiver horizontal launch direction should be `sigma_launch <= 1.0 deg` for a 1.5 deg face target, and `<= 2.0 deg` for a 2.5 deg face target. Path is less constraining in this model: all swept `sigma_path` values through `3.0 deg` held the launch-route face target in both gear modes.

## Camera spin-axis requirement

For face recovery, values below are the loosest swept `sigma_axis` that kept the axis-route face median under the target. For shot-shape display, spin-axis error passes through 1:1, so the display requirement is simply the desired displayed-axis sigma.

| Club | Gear | Face <= 1.5 deg | Face <= 2.5 deg |
|---|---|---:|---:|
| driver | camera | 5.0 deg | 10.0 deg |
| iron | camera | 1.0 deg | 2.5 deg |
| driver | none | not met | 5.0 deg |
| iron | none | not met | 2.5 deg |

Headline: for D-plane face, driver spin-axis can be loose, but the iron axis route is stricter. To support both clubs at face <= 1.5 deg, use `sigma_axis <= 1.0 deg` or prefer the launch route for face and treat axis as a consistency/shot-shape input. For shot-shape display, `sigma_axis = 2.5 deg` means about `2.5 deg` displayed axis error.

## Gear-correction benefit

Median error reduction from `gear=none` to `gear=camera`:

| Club | Launch-route face benefit | Axis-route face benefit | Fused face benefit |
|---|---:|---:|---:|
| driver | 0.53 deg | 0.93 deg | 0.54 deg |
| iron | 0.06 deg | 0.05 deg | 0.04 deg |

Residual camera-vs-perfect gap:

| Club | Launch route | Axis route | Fused |
|---|---:|---:|---:|
| driver | 0.19 deg | 0.31 deg | 0.11 deg |
| iron | -0.03 deg | 0.26 deg | -0.02 deg |

Headline: impact-location correction buys roughly `0.5-0.9 deg` on driver face estimates at the baseline strike distribution and `sigma_impact = 3 mm`; it buys little for irons under these constants because iron gear effect is much smaller. Perfect impact correction still improves driver axis-route face by another `0.31 deg`, so 3 mm impact is useful but not the absolute bound.

## Frame-bias / leveling requirement

The analytic frame-bias test is 1:1: a horizontal receiver boresight bias `b_frame` shifts absolute face-to-target by about `b_frame`, while face-to-path cancels because launch and path share the same biased frame. In the sweep, `b_frame = 1.0 deg` was the loosest tested value that still held the launch-route face target for both clubs at `gear=camera`.

Headline: receiver frame leveling/boresight calibration should be held to `<= 1.0 deg` for these targets, and tighter if the product wants absolute face/aim better than that. This is a calibration requirement, separate from random launch/path sensor noise.

## Notes

- Coefficients are sampled per shot from a conservative envelope: driver `U(0.76, 0.87)`, iron `U(0.61, 0.76)`. The inversion uses the fixed midpoint. The coefficient-honesty tests prove nonzero face error with all sigmas zero and envelope width on.
- Gear effect is always present in synthetic ball data. `gear=none` ignores it, `gear=camera` uses `(u,w)` measured to `sigma_impact = 3 mm`, and `gear=perfect` is the bound.
- Dynamic loft comes from the launch route only. The axis route estimates face only.
