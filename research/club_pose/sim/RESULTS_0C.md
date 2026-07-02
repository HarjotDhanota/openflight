# Stage 0C Results: Marked-clubhead Honest Accuracy Budget

Command:

```powershell
& 'C:\Users\harjo\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe' run --group research python research/club_pose/sim/run_budget_0c.py --n 96 --seed 0 --compact
```

Baseline per cell: centroid sigma `0.5 px`, correlated bias `0 px`, calibration residual `0.5 mm`, mono ball depth sigma `15 mm`, stereo ball depth sigma `3 mm`, sync jitter `100 us`, velocity error fraction `0.03`, stereo baseline `150 mm`. Failed solves are counted in `ok_rate`; all cells below had `ok_rate = 1.00`.

## Driver Mono

| Metric | Median error | Dominant source | Worst swept median |
|---|---:|---|---:|
| Face angle | 0.52 deg | sync_jitter_us | 1.12 deg |
| Dynamic loft | 1.25 deg | ball_depth_mm | 2.33 deg |
| Impact offset | 2.18 mm | sync_jitter_us | 4.66 mm |
| Impact height | 5.46 mm | ball_depth_mm | 9.12 mm |
| Impact vector | 6.16 mm | ball_depth_mm | 9.47 mm |

## Driver Stereo

| Metric | Median error | Dominant source | Worst swept median |
|---|---:|---|---:|
| Face angle | 0.67 deg | correlated_bias_px | 1.67 deg |
| Dynamic loft | 0.67 deg | correlated_bias_px | 5.88 deg |
| Impact offset | 2.09 mm | sync_jitter_us | 4.43 mm |
| Impact height | 2.74 mm | correlated_bias_px | 8.43 mm |
| Impact vector | 3.60 mm | correlated_bias_px | 8.65 mm |

## Iron Mono

| Metric | Median error | Dominant source | Worst swept median |
|---|---:|---|---:|
| Face angle | 1.81 deg | sync_jitter_us | 5.51 deg |
| Dynamic loft | 4.71 deg | sync_jitter_us | 6.90 deg |
| Impact offset | 2.27 mm | centroid_sigma_px | 6.11 mm |
| Impact height | 15.47 mm | ball_depth_mm | 18.04 mm |
| Impact vector | 15.90 mm | ball_depth_mm | 18.45 mm |

## Iron Stereo

| Metric | Median error | Dominant source | Worst swept median |
|---|---:|---|---:|
| Face angle | 0.91 deg | correlated_bias_px | 3.87 deg |
| Dynamic loft | 1.04 deg | correlated_bias_px | 7.73 deg |
| Impact offset | 2.31 mm | sync_jitter_us | 6.02 mm |
| Impact height | 2.62 mm | sync_jitter_us | 6.21 mm |
| Impact vector | 3.72 mm | sync_jitter_us | 9.19 mm |

## Hardware requirements

- Stereo is required for honest impact height. Mono ball depth dominates height: driver mono lands at 5.46 mm median height error and iron mono at 15.47 mm. Stereo depth near 3 mm brings both into the 2.6-2.8 mm height band at baseline.
- Camera-radar timing must stay near or below 100 us. The 250 us sweep pushes impact vector errors into roughly 8-13 mm, so 100 us is a requirement, not margin.
- Correlated centroid/glare bias must be kept well below 2 px. At 2 px it dominates stereo face/loft and can drive loft to 5.88 deg on driver and 7.73 deg on iron.
- Per-club calibration must remain a separate fitted object, not the truth template. The Stage 0C tests prove nonzero face/loft error with `sigma_cal > 0` even at zero pixel noise. In this sweep, 0.5 mm calibration is not usually the dominant term, but it remains part of the budget and should be held at or below that level.
- Honest baseline verdict: driver stereo and iron stereo clear the 3-5 mm impact vector band at median (`3.60 mm` and `3.72 mm`). Mono does not for impact height, especially iron. Face/loft are useful cross-checks, but correlated bias control is the gating optical requirement.
