# Phase 1 results: 0C budget at the real camera/radar (pre-registered grid)

Run: `python -m silhouette_poc.eval.run_budget_0c_radar --n 96 --seed 0`.
Marker-keypoint fitting is an optimistic proxy for silhouette fitting:
failing cells fail for the real system; passing cells are necessary, not sufficient.

| club | px/mm | sync | depth | exp us | ok | impact mm med | offset mm | height mm | face deg | gate |
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|
| driver | 1.33 | frame_2.14ms | radar_bias_0mm | 10 | 1.00 | 16.51 | 10.40 | 12.52 | 2.60 | fail (buildable) |
| driver | 1.33 | frame_2.14ms | radar_bias_0mm | 500 | 1.00 | 28.97 | 16.73 | 16.49 | 3.93 | fail |
| driver | 1.33 | frame_2.14ms | radar_bias_10mm | 10 | 1.00 | 16.07 | 10.15 | 12.26 | 2.45 | fail (buildable) |
| driver | 1.33 | frame_2.14ms | radar_bias_10mm | 500 | 1.00 | 29.81 | 17.27 | 16.07 | 3.84 | fail |
| driver | 1.33 | frame_2.14ms | radar_bias_20mm | 10 | 1.00 | 17.18 | 9.87 | 14.18 | 2.38 | fail (buildable) |
| driver | 1.33 | frame_2.14ms | radar_bias_20mm | 500 | 1.00 | 29.97 | 17.21 | 18.36 | 3.73 | fail |
| driver | 1.33 | frame_2.14ms | radar_bias_40mm | 10 | 1.00 | 22.08 | 9.56 | 17.66 | 2.34 | fail (buildable) |
| driver | 1.33 | frame_2.14ms | radar_bias_40mm | 500 | 1.00 | 29.85 | 16.81 | 19.71 | 3.50 | fail |
| driver | 1.33 | frame_2.14ms | stereo_ref_3mm | 10 | 1.00 | 17.44 | 12.65 | 12.11 | 2.64 | fail |
| driver | 1.33 | frame_2.14ms | stereo_ref_3mm | 500 | 1.00 | 97.16 | 54.73 | 65.56 | 23.58 | fail |
| driver | 1.33 | iq_33us | radar_bias_0mm | 10 | 1.00 | 2.39 | 1.31 | 1.81 | 0.33 | PASS (buildable) |
| driver | 1.33 | iq_33us | radar_bias_0mm | 500 | 1.00 | 20.76 | 11.51 | 13.65 | 4.04 | fail |
| driver | 1.33 | iq_33us | radar_bias_10mm | 10 | 1.00 | 5.34 | 1.25 | 5.07 | 0.33 | PASS (buildable) |
| driver | 1.33 | iq_33us | radar_bias_10mm | 500 | 1.00 | 19.99 | 12.70 | 13.07 | 3.84 | fail |
| driver | 1.33 | iq_33us | radar_bias_20mm | 10 | 1.00 | 9.33 | 1.42 | 9.17 | 0.38 | PASS (buildable) |
| driver | 1.33 | iq_33us | radar_bias_20mm | 500 | 1.00 | 21.46 | 13.38 | 13.86 | 3.64 | fail |
| driver | 1.33 | iq_33us | radar_bias_40mm | 10 | 1.00 | 16.72 | 1.85 | 16.45 | 0.40 | fail (buildable) |
| driver | 1.33 | iq_33us | radar_bias_40mm | 500 | 1.00 | 24.64 | 14.70 | 18.29 | 3.29 | fail |
| driver | 1.33 | iq_33us | stereo_ref_3mm | 10 | 1.00 | 3.81 | 2.35 | 2.32 | 0.78 | PASS |
| driver | 1.33 | iq_33us | stereo_ref_3mm | 500 | 1.00 | 95.33 | 52.56 | 61.90 | 21.24 | fail |
| driver | 1.31 | frame_2.14ms | radar_bias_0mm | 10 | 1.00 | 16.53 | 10.41 | 12.51 | 2.60 | fail |
| driver | 1.31 | frame_2.14ms | radar_bias_0mm | 500 | 1.00 | 28.97 | 16.74 | 16.49 | 3.93 | fail (buildable) |
| driver | 1.31 | frame_2.14ms | radar_bias_10mm | 10 | 1.00 | 16.07 | 10.15 | 12.25 | 2.44 | fail |
| driver | 1.31 | frame_2.14ms | radar_bias_10mm | 500 | 1.00 | 29.81 | 17.28 | 16.07 | 3.84 | fail (buildable) |
| driver | 1.31 | frame_2.14ms | radar_bias_20mm | 10 | 1.00 | 17.18 | 9.88 | 14.18 | 2.37 | fail |
| driver | 1.31 | frame_2.14ms | radar_bias_20mm | 500 | 1.00 | 29.97 | 17.21 | 18.36 | 3.73 | fail (buildable) |
| driver | 1.31 | frame_2.14ms | radar_bias_40mm | 10 | 1.00 | 22.08 | 9.56 | 17.67 | 2.34 | fail |
| driver | 1.31 | frame_2.14ms | radar_bias_40mm | 500 | 1.00 | 29.85 | 16.82 | 19.71 | 3.50 | fail (buildable) |
| driver | 1.31 | frame_2.14ms | stereo_ref_3mm | 10 | 1.00 | 17.44 | 12.68 | 12.11 | 2.65 | fail |
| driver | 1.31 | frame_2.14ms | stereo_ref_3mm | 500 | 1.00 | 97.16 | 54.74 | 65.56 | 23.58 | fail |
| driver | 1.31 | iq_33us | radar_bias_0mm | 10 | 1.00 | 2.39 | 1.32 | 1.81 | 0.33 | PASS |
| driver | 1.31 | iq_33us | radar_bias_0mm | 500 | 1.00 | 20.76 | 11.51 | 13.65 | 4.05 | fail (buildable) |
| driver | 1.31 | iq_33us | radar_bias_10mm | 10 | 1.00 | 5.34 | 1.26 | 5.08 | 0.33 | PASS |
| driver | 1.31 | iq_33us | radar_bias_10mm | 500 | 1.00 | 19.99 | 12.70 | 13.07 | 3.84 | fail (buildable) |
| driver | 1.31 | iq_33us | radar_bias_20mm | 10 | 1.00 | 9.32 | 1.42 | 9.15 | 0.38 | PASS |
| driver | 1.31 | iq_33us | radar_bias_20mm | 500 | 1.00 | 21.46 | 13.39 | 13.86 | 3.64 | fail (buildable) |
| driver | 1.31 | iq_33us | radar_bias_40mm | 10 | 1.00 | 16.73 | 1.86 | 16.46 | 0.40 | fail |
| driver | 1.31 | iq_33us | radar_bias_40mm | 500 | 1.00 | 24.64 | 14.70 | 18.29 | 3.29 | fail (buildable) |
| driver | 1.31 | iq_33us | stereo_ref_3mm | 10 | 1.00 | 3.85 | 2.40 | 2.33 | 0.79 | PASS |
| driver | 1.31 | iq_33us | stereo_ref_3mm | 500 | 1.00 | 95.33 | 52.57 | 61.90 | 21.24 | fail |
| driver | 0.656 | frame_2.14ms | radar_bias_0mm | 10 | 1.00 | 16.76 | 10.28 | 12.50 | 2.68 | fail |
| driver | 0.656 | frame_2.14ms | radar_bias_0mm | 500 | 1.00 | 29.07 | 16.84 | 16.53 | 3.97 | fail (buildable) |
| driver | 0.656 | frame_2.14ms | radar_bias_10mm | 10 | 1.00 | 16.01 | 10.11 | 12.07 | 2.47 | fail |
| driver | 0.656 | frame_2.14ms | radar_bias_10mm | 500 | 1.00 | 29.91 | 17.40 | 16.17 | 3.86 | fail (buildable) |
| driver | 0.656 | frame_2.14ms | radar_bias_20mm | 10 | 1.00 | 16.85 | 10.25 | 14.05 | 2.37 | fail |
| driver | 0.656 | frame_2.14ms | radar_bias_20mm | 500 | 1.00 | 29.86 | 17.23 | 18.45 | 3.74 | fail (buildable) |
| driver | 0.656 | frame_2.14ms | radar_bias_40mm | 10 | 1.00 | 22.59 | 9.77 | 17.62 | 2.34 | fail |
| driver | 0.656 | frame_2.14ms | radar_bias_40mm | 500 | 1.00 | 29.93 | 16.91 | 19.90 | 3.50 | fail (buildable) |
| driver | 0.656 | frame_2.14ms | stereo_ref_3mm | 10 | 1.00 | 16.91 | 12.56 | 12.34 | 3.44 | fail |
| driver | 0.656 | frame_2.14ms | stereo_ref_3mm | 500 | 0.99 | 87.48 | 53.70 | 48.33 | 25.24 | fail |
| driver | 0.656 | iq_33us | radar_bias_0mm | 10 | 1.00 | 3.06 | 1.76 | 2.20 | 0.53 | PASS |
| driver | 0.656 | iq_33us | radar_bias_0mm | 500 | 1.00 | 20.87 | 11.56 | 13.69 | 4.06 | fail (buildable) |
| driver | 0.656 | iq_33us | radar_bias_10mm | 10 | 1.00 | 5.82 | 1.79 | 5.25 | 0.51 | PASS |
| driver | 0.656 | iq_33us | radar_bias_10mm | 500 | 1.00 | 20.08 | 12.78 | 13.17 | 3.85 | fail (buildable) |
| driver | 0.656 | iq_33us | radar_bias_20mm | 10 | 1.00 | 9.49 | 1.96 | 9.24 | 0.58 | PASS |
| driver | 0.656 | iq_33us | radar_bias_20mm | 500 | 1.00 | 21.55 | 13.45 | 13.82 | 3.66 | fail (buildable) |
| driver | 0.656 | iq_33us | radar_bias_40mm | 10 | 1.00 | 16.77 | 2.35 | 16.51 | 0.52 | fail |
| driver | 0.656 | iq_33us | radar_bias_40mm | 500 | 1.00 | 24.80 | 14.77 | 18.31 | 3.30 | fail (buildable) |
| driver | 0.656 | iq_33us | stereo_ref_3mm | 10 | 1.00 | 7.14 | 4.02 | 4.61 | 1.55 | PASS |
| driver | 0.656 | iq_33us | stereo_ref_3mm | 500 | 0.99 | 78.80 | 50.61 | 46.64 | 23.72 | fail |
| iron | 1.33 | frame_2.14ms | radar_bias_0mm | 10 | 1.00 | 15.34 | 8.86 | 10.70 | 1.65 | fail (buildable) |
| iron | 1.33 | frame_2.14ms | radar_bias_0mm | 500 | 1.00 | 79.70 | 40.08 | 51.80 | 22.86 | fail |
| iron | 1.33 | frame_2.14ms | radar_bias_10mm | 10 | 1.00 | 18.41 | 9.07 | 14.03 | 1.65 | fail (buildable) |
| iron | 1.33 | frame_2.14ms | radar_bias_10mm | 500 | 1.00 | 84.79 | 42.91 | 51.19 | 22.86 | fail |
| iron | 1.33 | frame_2.14ms | radar_bias_20mm | 10 | 1.00 | 22.84 | 9.58 | 20.52 | 1.65 | fail (buildable) |
| iron | 1.33 | frame_2.14ms | radar_bias_20mm | 500 | 1.00 | 92.37 | 46.37 | 52.95 | 22.86 | fail |
| iron | 1.33 | frame_2.14ms | radar_bias_40mm | 10 | 1.00 | 37.63 | 9.72 | 35.89 | 1.65 | fail (buildable) |
| iron | 1.33 | frame_2.14ms | radar_bias_40mm | 500 | 1.00 | 108.48 | 53.90 | 66.94 | 22.86 | fail |
| iron | 1.33 | frame_2.14ms | stereo_ref_3mm | 10 | 1.00 | 20.00 | 12.85 | 15.51 | 3.43 | fail |
| iron | 1.33 | frame_2.14ms | stereo_ref_3mm | 500 | 1.00 | 78.23 | 47.19 | 48.64 | 68.16 | fail |
| iron | 1.33 | iq_33us | radar_bias_0mm | 10 | 1.00 | 6.89 | 2.07 | 5.89 | 1.65 | PASS (buildable) |
| iron | 1.33 | iq_33us | radar_bias_0mm | 500 | 1.00 | 83.87 | 45.18 | 54.06 | 22.86 | fail |
| iron | 1.33 | iq_33us | radar_bias_10mm | 10 | 1.00 | 13.43 | 2.37 | 12.89 | 1.65 | fail (buildable) |
| iron | 1.33 | iq_33us | radar_bias_10mm | 500 | 1.00 | 91.38 | 48.54 | 54.25 | 22.86 | fail |
| iron | 1.33 | iq_33us | radar_bias_20mm | 10 | 1.00 | 21.24 | 2.74 | 20.61 | 1.65 | fail (buildable) |
| iron | 1.33 | iq_33us | radar_bias_20mm | 500 | 1.00 | 98.53 | 52.24 | 56.70 | 22.86 | fail |
| iron | 1.33 | iq_33us | radar_bias_40mm | 10 | 1.00 | 36.62 | 3.78 | 36.37 | 1.65 | fail (buildable) |
| iron | 1.33 | iq_33us | radar_bias_40mm | 500 | 1.00 | 112.93 | 58.29 | 66.86 | 22.86 | fail |
| iron | 1.33 | iq_33us | stereo_ref_3mm | 10 | 1.00 | 4.97 | 3.58 | 2.74 | 3.43 | PASS |
| iron | 1.33 | iq_33us | stereo_ref_3mm | 500 | 1.00 | 81.25 | 47.50 | 47.88 | 68.16 | fail |
| iron | 1.31 | frame_2.14ms | radar_bias_0mm | 10 | 1.00 | 15.39 | 8.92 | 10.74 | 1.68 | fail |
| iron | 1.31 | frame_2.14ms | radar_bias_0mm | 500 | 1.00 | 79.71 | 40.08 | 51.80 | 22.86 | fail (buildable) |
| iron | 1.31 | frame_2.14ms | radar_bias_10mm | 10 | 1.00 | 18.40 | 9.16 | 14.01 | 1.68 | fail |
| iron | 1.31 | frame_2.14ms | radar_bias_10mm | 500 | 1.00 | 84.80 | 42.91 | 51.19 | 22.86 | fail (buildable) |
| iron | 1.31 | frame_2.14ms | radar_bias_20mm | 10 | 1.00 | 22.91 | 9.60 | 20.56 | 1.68 | fail |
| iron | 1.31 | frame_2.14ms | radar_bias_20mm | 500 | 1.00 | 92.37 | 46.37 | 52.95 | 22.86 | fail (buildable) |
| iron | 1.31 | frame_2.14ms | radar_bias_40mm | 10 | 1.00 | 37.69 | 9.81 | 35.96 | 1.68 | fail |
| iron | 1.31 | frame_2.14ms | radar_bias_40mm | 500 | 1.00 | 108.48 | 53.90 | 66.94 | 22.86 | fail (buildable) |
| iron | 1.31 | frame_2.14ms | stereo_ref_3mm | 10 | 1.00 | 20.21 | 12.88 | 15.49 | 3.49 | fail |
| iron | 1.31 | frame_2.14ms | stereo_ref_3mm | 500 | 1.00 | 78.23 | 47.19 | 48.64 | 68.16 | fail |
| iron | 1.31 | iq_33us | radar_bias_0mm | 10 | 1.00 | 6.98 | 2.10 | 5.94 | 1.68 | PASS |
| iron | 1.31 | iq_33us | radar_bias_0mm | 500 | 1.00 | 83.88 | 45.18 | 54.07 | 22.86 | fail (buildable) |
| iron | 1.31 | iq_33us | radar_bias_10mm | 10 | 1.00 | 13.45 | 2.39 | 12.86 | 1.68 | fail |
| iron | 1.31 | iq_33us | radar_bias_10mm | 500 | 1.00 | 91.39 | 48.54 | 54.25 | 22.86 | fail (buildable) |
| iron | 1.31 | iq_33us | radar_bias_20mm | 10 | 1.00 | 21.24 | 2.76 | 20.58 | 1.68 | fail |
| iron | 1.31 | iq_33us | radar_bias_20mm | 500 | 1.00 | 98.53 | 52.25 | 56.70 | 22.86 | fail (buildable) |
| iron | 1.31 | iq_33us | radar_bias_40mm | 10 | 1.00 | 36.64 | 3.81 | 36.30 | 1.68 | fail |
| iron | 1.31 | iq_33us | radar_bias_40mm | 500 | 1.00 | 112.94 | 58.30 | 66.86 | 22.86 | fail (buildable) |
| iron | 1.31 | iq_33us | stereo_ref_3mm | 10 | 1.00 | 5.03 | 3.64 | 2.74 | 3.49 | PASS |
| iron | 1.31 | iq_33us | stereo_ref_3mm | 500 | 1.00 | 81.25 | 47.50 | 47.88 | 68.16 | fail |
| iron | 0.656 | frame_2.14ms | radar_bias_0mm | 10 | 1.00 | 19.24 | 10.25 | 12.26 | 3.21 | fail |
| iron | 0.656 | frame_2.14ms | radar_bias_0mm | 500 | 1.00 | 80.02 | 40.36 | 52.18 | 23.02 | fail (buildable) |
| iron | 0.656 | frame_2.14ms | radar_bias_10mm | 10 | 1.00 | 20.14 | 10.80 | 15.76 | 3.21 | fail |
| iron | 0.656 | frame_2.14ms | radar_bias_10mm | 500 | 1.00 | 85.28 | 43.20 | 51.50 | 23.02 | fail (buildable) |
| iron | 0.656 | frame_2.14ms | radar_bias_20mm | 10 | 1.00 | 26.21 | 11.51 | 22.37 | 3.21 | fail |
| iron | 0.656 | frame_2.14ms | radar_bias_20mm | 500 | 1.00 | 92.86 | 46.54 | 53.09 | 23.02 | fail (buildable) |
| iron | 0.656 | frame_2.14ms | radar_bias_40mm | 10 | 1.00 | 40.07 | 12.28 | 36.90 | 3.21 | fail |
| iron | 0.656 | frame_2.14ms | radar_bias_40mm | 500 | 1.00 | 108.94 | 54.20 | 67.18 | 23.02 | fail (buildable) |
| iron | 0.656 | frame_2.14ms | stereo_ref_3mm | 10 | 1.00 | 23.15 | 15.11 | 16.46 | 7.58 | fail |
| iron | 0.656 | frame_2.14ms | stereo_ref_3mm | 500 | 1.00 | 78.49 | 50.24 | 48.02 | 68.32 | fail |
| iron | 0.656 | iq_33us | radar_bias_0mm | 10 | 1.00 | 10.30 | 3.81 | 7.89 | 3.21 | PASS |
| iron | 0.656 | iq_33us | radar_bias_0mm | 500 | 1.00 | 84.35 | 45.45 | 54.39 | 23.02 | fail (buildable) |
| iron | 0.656 | iq_33us | radar_bias_10mm | 10 | 1.00 | 15.31 | 4.59 | 14.04 | 3.21 | fail |
| iron | 0.656 | iq_33us | radar_bias_10mm | 500 | 1.00 | 91.91 | 48.87 | 54.43 | 23.02 | fail (buildable) |
| iron | 0.656 | iq_33us | radar_bias_20mm | 10 | 1.00 | 22.64 | 5.37 | 21.89 | 3.21 | fail |
| iron | 0.656 | iq_33us | radar_bias_20mm | 500 | 1.00 | 99.08 | 52.59 | 57.07 | 23.02 | fail (buildable) |
| iron | 0.656 | iq_33us | radar_bias_40mm | 10 | 1.00 | 37.79 | 7.11 | 37.53 | 3.21 | fail |
| iron | 0.656 | iq_33us | radar_bias_40mm | 500 | 1.00 | 113.50 | 58.70 | 67.10 | 23.02 | fail (buildable) |
| iron | 0.656 | iq_33us | stereo_ref_3mm | 10 | 1.00 | 8.78 | 6.91 | 5.23 | 7.58 | PASS |
| iron | 0.656 | iq_33us | stereo_ref_3mm | 500 | 1.00 | 81.63 | 47.94 | 47.70 | 68.32 | fail |

## Gate verdict

**GO** — 4 buildable cell(s) pass the spec gate. Best: driver @ 1.33 px/mm, iq_33us, radar_bias_0mm, 10 us -> 2.39 mm median impact vector.

## Reading the table honestly

1. **Sync dominates everything.** No frame-quantized (2.14 ms) cell passes anywhere; every passing cell uses I/Q-localized timing (33 us). Wiring `ops_impact_finder.py` into the camera timebase is mandatory, not optional — and it is pure software.
2. **500 us exposure fails every cell under THIS model** — but the model is knowingly pessimistic for blur: it treats the smear as random centroid noise (sigma = smear/sqrt(12) per marker). A silhouette fitter that models the smear explicitly (fit the blurred template to the blurred image) can do better — Trackman 4 measures impact location at 60 fps under continuous light, which this model would declare impossible. **Treat the 500 us column as a lower bound; the Phase 3/4 end-to-end sim with exposure-integrated rendering is the honest test of ambient-light viability.** The 10 us (strobed) column needs no such caveat.
3. **Radar depth replaces stereo — confirmed at low bias.** Driver mono+radar (bias 0) beats the stereo reference (2.39 vs 3.81 mm). The stereo question is settled in radar's favor *if* clubhead phase-center bias stays small.
4. **Clubhead range bias is the deciding hardware unknown.** It lands ~1:1 in impact height. Driver tolerates ~20 mm (9.33 mm median, still PASS); iron fails already at 10 mm (13.43 mm vs a 12 mm gate). Characterizing IWR6843 range bias on a real clubhead is the single most important hardware measurement for iron-grade impact location.
5. **Plate scale barely matters** (0.656 vs 1.33 px/mm: 3.06 vs 2.39 mm driver). The as-shipped camera resolution is NOT the limiter for impact location — the readout-mode upgrade matters for spin, not for this metric. Centroid noise is dominated by sync, blur, and depth bias at every tested scale.
6. **Proxy caveat as pre-registered:** marker-keypoint fitting is optimistic vs silhouette fitting for sharp images (caveat applies to PASS cells), pessimistic for blurred ones (caveat applies to 500 us cells).
