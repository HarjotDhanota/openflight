# Phase 1b results: silhouette + calibrated club-range gate

**GATE: PASS** — 7 buildable driver cell(s) meet median, p90, and solve-rate thresholds; best is poc_driver/A0/10us/iq_gaussian_33us/radar/residual+0mm/6ee9b44f3585 at 1.689 mm median / 3.037 mm p90 / 0.993 solve rate.

Evaluation hash: `dbccdfde87bf78022222d50e31078a9e7f871d2467698b782d8985e22e40b4d2`

The solver uses exposure-integrated silhouette moments and a calibrated club-range sphere
inside the club-state solve. Ball range is independent. No marker correspondence or
ball-only club bias is used. Preset B is not buildable because Gate B1 has not run.
Iron results remain `HARDWARE-BLOCKED` pending Gate R.

## Frozen run

- Root seed: `20260823`
- Core: 192 cells x 1000 trials
- Stress: 44 cells x 256 trials
- Buildable hardware: existing 320x200 optical mode, 10 us exposure,
  exposure-synchronous external pulse <=10 us, mono radar depth.
- Preset A1 is sensitivity-only; Preset B has not passed Gate B1.

## Passing buildable cells

- `poc_driver/A0/10us/iq_gaussian_33us/radar/residual+0mm/6ee9b44f3585` — median 1.689 mm, p90 3.037 mm, solve 0.993
- `poc_driver/A0/10us/iq_gaussian_33us/radar/residual-10mm/87759eb290b0` — median 1.885 mm, p90 3.589 mm, solve 0.993
- `poc_driver/A0/10us/iq_gaussian_33us/radar/residual+10mm/3573bba675d1` — median 2.017 mm, p90 3.586 mm, solve 0.993
- `poc_driver/A0/10us/iq_gaussian_33us/radar/residual+20mm/49f528a8445b` — median 2.835 mm, p90 4.630 mm, solve 0.990
- `poc_driver/A0/10us/iq_gaussian_33us/radar/residual-20mm/3521071da615` — median 2.878 mm, p90 4.755 mm, solve 0.993
- `poc_driver/A0/10us/iq_gaussian_33us/radar/residual+40mm/e96cc208d729` — median 5.178 mm, p90 7.232 mm, solve 0.978
- `poc_driver/A0/10us/iq_gaussian_33us/radar/residual-40mm/41b1ca558f55` — median 5.220 mm, p90 7.117 mm, solve 0.984

## Zero-noise and calibration controls

| control | solve | impact med mm | impact p90 mm | club range med mm | ball range med mm | IoU med | failures |
|---|---:|---:|---:|---:|---:|---:|---|
| zero_noise_recovery | 1.000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 1.000000 | none |
| static_bias_not_removed | 1.000 | 8.201142 | 8.883040 | 66.006982 | 0.000000 | 0.919226 | none |

## Core grid — all 192 cells

| club | preset | exp us | timing | depth | residual mm | buildable | solve | impact med | impact p90 | offset med/p90 | height med/p90 | IoU med/p10 | fit med/p90 | vis | ambiguity | status | hash | failures |
|---|---|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| poc_driver | A0 | 10 | iq_gaussian_33us | oracle | 0 | false | 0.995 | 1.64 | 3.18 | -0.00/1.77 | -0.06/1.92 | 0.946/0.901 | 4.68/6.38 | 0 | 0 | REFERENCE | `ffbab420b57a` | silhouette_fit_residual:5 |
| poc_driver | A0 | 10 | iq_gaussian_33us | radar | -40 | true | 0.984 | 5.22 | 7.12 | -0.17/2.24 | -4.86/-2.76 | 0.935/0.897 | 5.17/6.86 | 0 | 0 | PASS | `41b1ca558f55` | silhouette_fit_residual:16 |
| poc_driver | A0 | 10 | iq_gaussian_33us | radar | -20 | true | 0.993 | 2.88 | 4.76 | -0.07/1.89 | -2.43/-0.49 | 0.943/0.893 | 4.86/6.47 | 0 | 0 | PASS | `3521071da615` | silhouette_fit_residual:7 |
| poc_driver | A0 | 10 | iq_gaussian_33us | radar | -10 | true | 0.993 | 1.89 | 3.59 | -0.02/1.65 | -1.09/0.76 | 0.944/0.896 | 4.69/6.29 | 0 | 0 | PASS | `87759eb290b0` | silhouette_fit_residual:7 |
| poc_driver | A0 | 10 | iq_gaussian_33us | radar | 0 | true | 0.993 | 1.69 | 3.04 | 0.03/1.68 | 0.13/1.91 | 0.945/0.895 | 4.68/6.32 | 0 | 0 | PASS | `6ee9b44f3585` | silhouette_fit_residual:7 |
| poc_driver | A0 | 10 | iq_gaussian_33us | radar | 10 | true | 0.993 | 2.02 | 3.59 | 0.11/1.80 | 1.29/3.16 | 0.944/0.897 | 4.66/6.33 | 0 | 0 | PASS | `3573bba675d1` | silhouette_fit_residual:7 |
| poc_driver | A0 | 10 | iq_gaussian_33us | radar | 20 | true | 0.990 | 2.83 | 4.63 | 0.22/1.98 | 2.45/4.31 | 0.942/0.895 | 4.78/6.45 | 0 | 0 | PASS | `49f528a8445b` | silhouette_fit_residual:10 |
| poc_driver | A0 | 10 | iq_gaussian_33us | radar | 40 | true | 0.978 | 5.18 | 7.23 | 0.26/2.55 | 4.84/6.87 | 0.934/0.896 | 5.23/6.90 | 0 | 0 | PASS | `e96cc208d729` | silhouette_fit_residual:22 |
| poc_driver | A0 | 10 | frame_uniform_2.137ms | oracle | 0 | false | 0.998 | 11.55 | 20.67 | 0.03/9.65 | -0.28/15.35 | 0.947/0.902 | 4.70/6.27 | 0 | 0 | REFERENCE | `ff64d61233d6` | silhouette_fit_residual:2 |
| poc_driver | A0 | 10 | frame_uniform_2.137ms | radar | -40 | true | 0.991 | 11.28 | 22.21 | -0.67/9.47 | -4.81/10.04 | 0.934/0.894 | 5.25/6.94 | 0 | 0 | FAIL | `0655747cdc68` | silhouette_fit_residual:9 |
| poc_driver | A0 | 10 | frame_uniform_2.137ms | radar | -20 | true | 0.994 | 11.61 | 21.04 | -0.20/9.67 | -2.33/13.00 | 0.941/0.896 | 4.81/6.54 | 0 | 0 | FAIL | `5206116d5096` | silhouette_fit_residual:6 |
| poc_driver | A0 | 10 | frame_uniform_2.137ms | radar | -10 | true | 0.995 | 11.58 | 20.76 | 0.10/9.44 | -1.53/13.51 | 0.943/0.897 | 4.68/6.40 | 0 | 0 | FAIL | `4016f52edf18` | silhouette_fit_residual:5 |
| poc_driver | A0 | 10 | frame_uniform_2.137ms | radar | 0 | true | 0.996 | 11.12 | 20.90 | -0.19/9.24 | -0.01/15.88 | 0.946/0.898 | 4.68/6.38 | 0 | 0 | FAIL | `84ccbf3a2bd7` | silhouette_fit_residual:4 |
| poc_driver | A0 | 10 | frame_uniform_2.137ms | radar | 10 | true | 0.996 | 11.12 | 20.93 | 0.08/9.61 | 1.29/16.82 | 0.946/0.898 | 4.77/6.37 | 0 | 0 | FAIL | `3941f4901c7b` | silhouette_fit_residual:4 |
| poc_driver | A0 | 10 | frame_uniform_2.137ms | radar | 20 | true | 0.995 | 11.09 | 20.49 | 0.49/9.63 | 2.06/17.31 | 0.942/0.897 | 4.92/6.49 | 0 | 0 | FAIL | `d80741960712` | silhouette_fit_residual:5 |
| poc_driver | A0 | 10 | frame_uniform_2.137ms | radar | 40 | true | 0.981 | 11.27 | 22.30 | 0.31/9.39 | 4.83/20.30 | 0.934/0.893 | 5.32/6.95 | 0 | 0 | FAIL | `afa2a641da7e` | silhouette_fit_residual:19 |
| poc_driver | A0 | 500 | iq_gaussian_33us | oracle | 0 | false | 0.998 | 1.65 | 3.10 | -0.01/1.66 | 0.00/1.92 | 0.948/0.905 | 4.76/6.30 | 0 | 0 | REFERENCE | `51680c8176cc` | silhouette_fit_residual:2 |
| poc_driver | A0 | 500 | iq_gaussian_33us | radar | -40 | false | 0.982 | 5.11 | 7.01 | -0.26/2.12 | -4.75/-2.80 | 0.936/0.900 | 5.26/6.97 | 0 | 0 | NON-BUILDABLE | `8d85ec756d5d` | silhouette_fit_residual:18 |
| poc_driver | A0 | 500 | iq_gaussian_33us | radar | -20 | false | 0.997 | 2.84 | 4.78 | -0.16/1.72 | -2.38/-0.47 | 0.948/0.903 | 4.80/6.52 | 0 | 0 | NON-BUILDABLE | `c866669f285b` | silhouette_fit_residual:3 |
| poc_driver | A0 | 500 | iq_gaussian_33us | radar | -10 | false | 0.997 | 1.97 | 3.66 | -0.05/1.64 | -1.20/0.73 | 0.950/0.905 | 4.70/6.39 | 0 | 0 | NON-BUILDABLE | `a0fb365bfee6` | silhouette_fit_residual:3 |
| poc_driver | A0 | 500 | iq_gaussian_33us | radar | 0 | false | 0.997 | 1.65 | 3.17 | 0.02/1.67 | 0.09/1.99 | 0.951/0.905 | 4.68/6.25 | 0 | 0 | NON-BUILDABLE | `691c2923401b` | silhouette_fit_residual:3 |
| poc_driver | A0 | 500 | iq_gaussian_33us | radar | 10 | false | 0.997 | 2.00 | 3.67 | 0.13/1.74 | 1.30/3.18 | 0.950/0.904 | 4.76/6.28 | 0 | 0 | NON-BUILDABLE | `4a8dcdabdd81` | silhouette_fit_residual:3 |
| poc_driver | A0 | 500 | iq_gaussian_33us | radar | 20 | false | 0.994 | 2.85 | 4.75 | 0.04/1.85 | 2.46/4.40 | 0.946/0.902 | 4.95/6.56 | 0 | 0 | NON-BUILDABLE | `83d2f77c00f7` | silhouette_fit_residual:6 |
| poc_driver | A0 | 500 | iq_gaussian_33us | radar | 40 | false | 0.978 | 5.16 | 7.10 | 0.19/2.47 | 4.82/6.83 | 0.937/0.899 | 5.26/6.92 | 0 | 0 | NON-BUILDABLE | `031416664bcf` | silhouette_fit_residual:22 |
| poc_driver | A0 | 500 | frame_uniform_2.137ms | oracle | 0 | false | 0.996 | 11.84 | 20.33 | -0.67/9.18 | 1.19/15.39 | 0.951/0.903 | 4.77/6.36 | 0 | 0 | REFERENCE | `a12414548e73` | silhouette_fit_residual:4 |
| poc_driver | A0 | 500 | frame_uniform_2.137ms | radar | -40 | false | 0.985 | 12.42 | 22.55 | -0.06/9.36 | -5.98/10.13 | 0.936/0.901 | 5.20/6.92 | 0 | 0 | NON-BUILDABLE | `1668c8ee487c` | silhouette_fit_residual:15 |
| poc_driver | A0 | 500 | frame_uniform_2.137ms | radar | -20 | false | 0.996 | 11.74 | 21.18 | -0.48/9.78 | -1.81/13.43 | 0.948/0.901 | 4.85/6.55 | 0 | 0 | NON-BUILDABLE | `c062ed0b75e1` | silhouette_fit_residual:4 |
| poc_driver | A0 | 500 | frame_uniform_2.137ms | radar | -10 | false | 0.993 | 11.16 | 20.44 | -0.30/8.86 | -0.69/14.70 | 0.949/0.905 | 4.76/6.40 | 0 | 0 | NON-BUILDABLE | `69e03f15f04f` | silhouette_fit_residual:7 |
| poc_driver | A0 | 500 | frame_uniform_2.137ms | radar | 0 | false | 0.993 | 11.09 | 20.38 | -0.28/8.83 | 0.63/15.81 | 0.950/0.905 | 4.76/6.38 | 0 | 0 | NON-BUILDABLE | `0542b5d1e424` | silhouette_fit_residual:7 |
| poc_driver | A0 | 500 | frame_uniform_2.137ms | radar | 10 | false | 0.992 | 11.02 | 20.34 | 0.02/9.41 | 1.79/16.35 | 0.948/0.904 | 4.83/6.49 | 0 | 0 | NON-BUILDABLE | `cb808f2cf50e` | silhouette_fit_residual:8 |
| poc_driver | A0 | 500 | frame_uniform_2.137ms | radar | 20 | false | 0.989 | 11.24 | 20.71 | 0.25/9.15 | 2.71/17.52 | 0.946/0.906 | 4.93/6.58 | 0 | 0 | NON-BUILDABLE | `ce98159d4f91` | silhouette_fit_residual:11 |
| poc_driver | A0 | 500 | frame_uniform_2.137ms | radar | 40 | false | 0.983 | 11.76 | 22.59 | 0.53/9.72 | 4.59/20.48 | 0.936/0.897 | 5.20/6.91 | 0 | 0 | NON-BUILDABLE | `bf4c64083c27` | silhouette_fit_residual:17 |
| poc_driver | A1 | 10 | iq_gaussian_33us | oracle | 0 | false | 0.652 | 1.01 | 1.92 | 0.03/0.96 | -0.01/1.32 | 0.973/0.952 | 6.09/7.63 | 135 | 0 | REFERENCE | `864b20b1e3d1` | silhouette_fit_residual:213, visibility_club:135 |
| poc_driver | A1 | 10 | iq_gaussian_33us | radar | -40 | false | 0.236 | 4.92 | 6.25 | 0.16/1.88 | -4.72/-3.38 | 0.947/0.935 | 6.78/7.76 | 135 | 0 | NON-BUILDABLE | `7b7d24ed991b` | silhouette_fit_residual:629, visibility_club:135 |
| poc_driver | A1 | 10 | iq_gaussian_33us | radar | -20 | false | 0.504 | 2.56 | 3.82 | -0.03/1.31 | -2.37/-1.07 | 0.965/0.943 | 6.37/7.73 | 135 | 0 | NON-BUILDABLE | `fbbc2b9f8aae` | silhouette_fit_residual:361, visibility_club:135 |
| poc_driver | A1 | 10 | iq_gaussian_33us | radar | -10 | false | 0.608 | 1.49 | 2.66 | -0.02/0.92 | -1.23/0.07 | 0.971/0.947 | 6.24/7.62 | 135 | 0 | NON-BUILDABLE | `3edf91862564` | silhouette_fit_residual:257, visibility_club:135 |
| poc_driver | A1 | 10 | iq_gaussian_33us | radar | 0 | false | 0.654 | 1.05 | 1.95 | 0.00/1.01 | -0.07/1.33 | 0.972/0.948 | 6.13/7.61 | 135 | 0 | NON-BUILDABLE | `7fee6dcda7c5` | silhouette_fit_residual:211, visibility_club:135 |
| poc_driver | A1 | 10 | iq_gaussian_33us | radar | 10 | false | 0.631 | 1.51 | 2.73 | 0.10/0.99 | 1.25/2.56 | 0.971/0.948 | 6.24/7.59 | 135 | 0 | NON-BUILDABLE | `ca45bd516256` | silhouette_fit_residual:234, visibility_club:135 |
| poc_driver | A1 | 10 | iq_gaussian_33us | radar | 20 | false | 0.501 | 2.59 | 3.92 | 0.09/1.19 | 2.41/3.77 | 0.966/0.946 | 6.39/7.66 | 135 | 0 | NON-BUILDABLE | `edb6bf738b87` | silhouette_fit_residual:364, visibility_club:135 |
| poc_driver | A1 | 10 | iq_gaussian_33us | radar | 40 | false | 0.241 | 4.89 | 6.26 | 0.09/1.75 | 4.66/6.00 | 0.948/0.935 | 6.74/7.81 | 135 | 0 | NON-BUILDABLE | `a7997bda54b0` | silhouette_fit_residual:624, visibility_club:135 |
| poc_driver | A1 | 10 | frame_uniform_2.137ms | oracle | 0 | false | 0.669 | 10.95 | 20.23 | -0.23/9.76 | 0.62/14.80 | 0.972/0.947 | 6.14/7.63 | 135 | 0 | REFERENCE | `c3ab21d3477c` | silhouette_fit_residual:196, visibility_club:135 |
| poc_driver | A1 | 10 | frame_uniform_2.137ms | radar | -40 | false | 0.209 | 12.94 | 20.98 | 0.94/8.89 | -6.99/11.30 | 0.947/0.936 | 6.95/7.85 | 135 | 0 | NON-BUILDABLE | `2552e7583614` | silhouette_fit_residual:656, visibility_club:135 |
| poc_driver | A1 | 10 | frame_uniform_2.137ms | radar | -20 | false | 0.496 | 10.86 | 20.90 | -0.47/9.70 | -1.14/13.69 | 0.966/0.945 | 6.36/7.68 | 135 | 0 | NON-BUILDABLE | `c851bacba9d9` | silhouette_fit_residual:369, visibility_club:135 |
| poc_driver | A1 | 10 | frame_uniform_2.137ms | radar | -10 | false | 0.627 | 12.45 | 20.77 | -0.15/10.05 | -1.42/14.35 | 0.971/0.949 | 6.16/7.64 | 135 | 0 | NON-BUILDABLE | `d9b0cd09b966` | silhouette_fit_residual:238, visibility_club:135 |
| poc_driver | A1 | 10 | frame_uniform_2.137ms | radar | 0 | false | 0.659 | 11.55 | 20.91 | -0.34/9.18 | 0.75/15.97 | 0.973/0.947 | 6.07/7.62 | 135 | 0 | NON-BUILDABLE | `bc68289c7a9c` | silhouette_fit_residual:206, visibility_club:135 |
| poc_driver | A1 | 10 | frame_uniform_2.137ms | radar | 10 | false | 0.626 | 11.89 | 20.18 | 0.07/9.28 | 1.25/15.59 | 0.970/0.949 | 6.18/7.61 | 135 | 0 | NON-BUILDABLE | `ce33f6193404` | silhouette_fit_residual:239, visibility_club:135 |
| poc_driver | A1 | 10 | frame_uniform_2.137ms | radar | 20 | false | 0.550 | 11.21 | 20.86 | -0.09/9.81 | 2.34/17.63 | 0.966/0.944 | 6.30/7.65 | 135 | 0 | NON-BUILDABLE | `6bfcdd0ec477` | silhouette_fit_residual:315, visibility_club:135 |
| poc_driver | A1 | 10 | frame_uniform_2.137ms | radar | 40 | false | 0.244 | 12.25 | 22.51 | 0.31/10.25 | 4.57/19.54 | 0.948/0.936 | 6.91/7.84 | 135 | 0 | NON-BUILDABLE | `d5d57ee4e415` | silhouette_fit_residual:621, visibility_club:135 |
| poc_driver | A1 | 500 | iq_gaussian_33us | oracle | 0 | false | 0.650 | 1.01 | 1.93 | 0.02/0.97 | -0.06/1.40 | 0.975/0.951 | 6.16/7.58 | 178 | 0 | REFERENCE | `446ff42a487e` | silhouette_fit_residual:172, visibility_club:178 |
| poc_driver | A1 | 500 | iq_gaussian_33us | radar | -40 | false | 0.222 | 5.00 | 6.29 | -0.05/1.85 | -4.71/-3.43 | 0.947/0.935 | 6.94/7.78 | 178 | 0 | NON-BUILDABLE | `3038431ecf4a` | silhouette_fit_residual:600, visibility_club:178 |
| poc_driver | A1 | 500 | iq_gaussian_33us | radar | -20 | false | 0.501 | 2.46 | 3.90 | 0.00/1.19 | -2.29/-0.93 | 0.968/0.948 | 6.22/7.65 | 178 | 0 | NON-BUILDABLE | `da6391ae7383` | silhouette_fit_residual:321, visibility_club:178 |
| poc_driver | A1 | 500 | iq_gaussian_33us | radar | -10 | false | 0.594 | 1.39 | 2.70 | -0.01/1.07 | -1.12/0.20 | 0.972/0.951 | 6.08/7.60 | 178 | 0 | NON-BUILDABLE | `d58b66ec614e` | silhouette_fit_residual:228, visibility_club:178 |
| poc_driver | A1 | 500 | iq_gaussian_33us | radar | 0 | false | 0.609 | 0.98 | 2.03 | 0.01/0.95 | 0.01/1.32 | 0.975/0.950 | 6.17/7.65 | 178 | 0 | NON-BUILDABLE | `417d56727ed2` | silhouette_fit_residual:213, visibility_club:178 |
| poc_driver | A1 | 500 | iq_gaussian_33us | radar | 10 | false | 0.596 | 1.59 | 2.82 | -0.06/0.97 | 1.31/2.58 | 0.972/0.950 | 6.18/7.59 | 178 | 0 | NON-BUILDABLE | `aeaf6e5f38a1` | silhouette_fit_residual:226, visibility_club:178 |
| poc_driver | A1 | 500 | iq_gaussian_33us | radar | 20 | false | 0.503 | 2.56 | 3.98 | 0.00/1.26 | 2.44/3.83 | 0.968/0.948 | 6.32/7.62 | 178 | 0 | NON-BUILDABLE | `896881cfb256` | silhouette_fit_residual:319, visibility_club:178 |
| poc_driver | A1 | 500 | iq_gaussian_33us | radar | 40 | false | 0.221 | 5.02 | 6.47 | 0.04/1.79 | 4.87/6.34 | 0.949/0.939 | 6.87/7.76 | 178 | 0 | NON-BUILDABLE | `4a2fb951b670` | silhouette_fit_residual:601, visibility_club:178 |
| poc_driver | A1 | 500 | frame_uniform_2.137ms | oracle | 0 | false | 0.635 | 11.50 | 20.85 | -0.28/9.76 | 0.19/15.36 | 0.974/0.950 | 6.07/7.56 | 178 | 0 | REFERENCE | `faa51f70affb` | silhouette_fit_residual:187, visibility_club:178 |
| poc_driver | A1 | 500 | frame_uniform_2.137ms | radar | -40 | false | 0.225 | 12.37 | 22.67 | 0.20/10.34 | -4.80/11.03 | 0.947/0.936 | 6.92/7.78 | 178 | 0 | NON-BUILDABLE | `627263f3ad76` | silhouette_fit_residual:597, visibility_club:178 |
| poc_driver | A1 | 500 | frame_uniform_2.137ms | radar | -20 | false | 0.504 | 10.79 | 20.64 | 0.46/9.54 | -3.24/12.52 | 0.967/0.947 | 6.29/7.62 | 178 | 0 | NON-BUILDABLE | `28af88ad2ae5` | silhouette_fit_residual:318, visibility_club:178 |
| poc_driver | A1 | 500 | frame_uniform_2.137ms | radar | -10 | false | 0.597 | 11.30 | 20.04 | -0.18/9.22 | -1.45/14.56 | 0.972/0.952 | 6.17/7.51 | 178 | 0 | NON-BUILDABLE | `b287a81378b6` | silhouette_fit_residual:225, visibility_club:178 |
| poc_driver | A1 | 500 | frame_uniform_2.137ms | radar | 0 | false | 0.623 | 11.86 | 20.82 | 0.90/9.81 | -1.45/15.78 | 0.974/0.950 | 6.00/7.48 | 178 | 0 | NON-BUILDABLE | `8096932bb30a` | silhouette_fit_residual:199, visibility_club:178 |
| poc_driver | A1 | 500 | frame_uniform_2.137ms | radar | 10 | false | 0.601 | 10.54 | 20.91 | -0.42/9.21 | 2.00/16.83 | 0.973/0.948 | 6.17/7.52 | 178 | 0 | NON-BUILDABLE | `75559bfc8674` | silhouette_fit_residual:221, visibility_club:178 |
| poc_driver | A1 | 500 | frame_uniform_2.137ms | radar | 20 | false | 0.496 | 11.09 | 21.84 | 0.10/9.45 | 2.55/18.63 | 0.968/0.950 | 6.39/7.73 | 178 | 0 | NON-BUILDABLE | `016be83e6081` | silhouette_fit_residual:326, visibility_club:178 |
| poc_driver | A1 | 500 | frame_uniform_2.137ms | radar | 40 | false | 0.279 | 11.63 | 23.02 | 0.05/8.92 | 4.80/20.04 | 0.949/0.938 | 6.92/7.81 | 178 | 0 | NON-BUILDABLE | `b1c713ecb56f` | silhouette_fit_residual:543, visibility_club:178 |
| poc_driver | B | 10 | iq_gaussian_33us | oracle | 0 | false | 0.748 | 0.95 | 1.96 | -0.02/0.83 | -0.01/1.32 | 0.973/0.950 | 6.09/7.56 | 0 | 0 | REFERENCE | `699eefa239ef` | silhouette_fit_residual:252 |
| poc_driver | B | 10 | iq_gaussian_33us | radar | -40 | false | 0.233 | 5.08 | 6.24 | -0.33/1.67 | -4.84/-3.45 | 0.947/0.937 | 6.88/7.81 | 0 | 0 | NON-BUILDABLE | `7a356b3ec31a` | silhouette_fit_residual:767 |
| poc_driver | B | 10 | iq_gaussian_33us | radar | -20 | false | 0.610 | 2.53 | 3.91 | -0.13/0.95 | -2.39/-0.98 | 0.966/0.942 | 6.34/7.73 | 0 | 0 | NON-BUILDABLE | `f926578a753c` | silhouette_fit_residual:390 |
| poc_driver | B | 10 | iq_gaussian_33us | radar | -10 | false | 0.713 | 1.44 | 2.66 | -0.06/0.93 | -1.13/0.22 | 0.972/0.946 | 6.19/7.56 | 0 | 0 | NON-BUILDABLE | `7c8434cad53d` | silhouette_fit_residual:287 |
| poc_driver | B | 10 | iq_gaussian_33us | radar | 0 | false | 0.781 | 0.97 | 2.12 | -0.02/0.98 | 0.02/1.34 | 0.973/0.947 | 6.01/7.55 | 0 | 0 | NON-BUILDABLE | `37a58c7d1689` | silhouette_fit_residual:219 |
| poc_driver | B | 10 | iq_gaussian_33us | radar | 10 | false | 0.719 | 1.44 | 2.76 | 0.13/1.08 | 1.20/2.53 | 0.971/0.945 | 6.15/7.62 | 0 | 0 | NON-BUILDABLE | `faa056329cf0` | silhouette_fit_residual:281 |
| poc_driver | B | 10 | iq_gaussian_33us | radar | 20 | false | 0.609 | 2.67 | 3.99 | 0.08/1.40 | 2.49/3.81 | 0.966/0.945 | 6.43/7.65 | 0 | 0 | NON-BUILDABLE | `b56f765a71f4` | silhouette_fit_residual:391 |
| poc_driver | B | 10 | iq_gaussian_33us | radar | 40 | false | 0.316 | 4.95 | 6.49 | 0.36/2.29 | 4.77/6.22 | 0.947/0.936 | 6.95/7.82 | 0 | 0 | NON-BUILDABLE | `953da9793277` | silhouette_fit_residual:684 |
| poc_driver | B | 10 | frame_uniform_2.137ms | oracle | 0 | false | 0.747 | 11.13 | 20.19 | -0.42/9.39 | 0.77/14.46 | 0.974/0.950 | 6.18/7.62 | 0 | 0 | REFERENCE | `9859a3726d8f` | silhouette_fit_residual:253 |
| poc_driver | B | 10 | frame_uniform_2.137ms | radar | -40 | false | 0.231 | 12.63 | 22.51 | -0.87/9.11 | -5.09/11.28 | 0.947/0.937 | 6.90/7.82 | 0 | 0 | NON-BUILDABLE | `e8ccb165d504` | silhouette_fit_residual:769 |
| poc_driver | B | 10 | frame_uniform_2.137ms | radar | -20 | false | 0.593 | 11.57 | 20.63 | -0.57/9.29 | -2.28/12.76 | 0.966/0.947 | 6.42/7.66 | 0 | 0 | NON-BUILDABLE | `434599311e64` | silhouette_fit_residual:407 |
| poc_driver | B | 10 | frame_uniform_2.137ms | radar | -10 | false | 0.705 | 12.05 | 20.66 | -0.73/9.48 | -0.58/14.05 | 0.972/0.951 | 6.10/7.66 | 0 | 0 | NON-BUILDABLE | `628145b330da` | silhouette_fit_residual:295 |
| poc_driver | B | 10 | frame_uniform_2.137ms | radar | 0 | false | 0.733 | 11.62 | 20.51 | -0.31/9.25 | 0.55/15.64 | 0.973/0.948 | 6.03/7.57 | 0 | 0 | NON-BUILDABLE | `b36ad9273dbb` | silhouette_fit_residual:267 |
| poc_driver | B | 10 | frame_uniform_2.137ms | radar | 10 | false | 0.711 | 11.95 | 20.28 | 0.39/10.10 | 0.56/15.82 | 0.972/0.950 | 6.25/7.65 | 0 | 0 | NON-BUILDABLE | `1f6f97061ede` | silhouette_fit_residual:289 |
| poc_driver | B | 10 | frame_uniform_2.137ms | radar | 20 | false | 0.580 | 11.43 | 20.66 | 1.11/9.74 | 0.62/16.88 | 0.966/0.946 | 6.38/7.66 | 0 | 0 | NON-BUILDABLE | `2ce061022eb2` | silhouette_fit_residual:420 |
| poc_driver | B | 10 | frame_uniform_2.137ms | radar | 40 | false | 0.304 | 11.67 | 20.99 | -0.24/9.68 | 4.78/19.00 | 0.948/0.938 | 7.02/7.82 | 0 | 0 | NON-BUILDABLE | `a80144b568eb` | silhouette_fit_residual:696 |
| poc_driver | B | 500 | iq_gaussian_33us | oracle | 0 | false | 0.740 | 1.06 | 1.95 | -0.00/0.91 | -0.02/1.35 | 0.974/0.948 | 6.10/7.64 | 2 | 0 | REFERENCE | `b0dfa8c94663` | silhouette_fit_residual:258, visibility_club:2 |
| poc_driver | B | 500 | iq_gaussian_33us | radar | -40 | false | 0.269 | 4.95 | 6.37 | -0.04/1.81 | -4.61/-3.46 | 0.947/0.937 | 6.91/7.82 | 2 | 0 | NON-BUILDABLE | `58b9833c0eba` | silhouette_fit_residual:729, visibility_club:2 |
| poc_driver | B | 500 | iq_gaussian_33us | radar | -20 | false | 0.584 | 2.58 | 3.89 | -0.17/1.09 | -2.40/-1.06 | 0.968/0.948 | 6.39/7.66 | 2 | 0 | NON-BUILDABLE | `40eb6335306a` | silhouette_fit_residual:414, visibility_club:2 |
| poc_driver | B | 500 | iq_gaussian_33us | radar | -10 | false | 0.710 | 1.43 | 2.72 | -0.07/0.97 | -1.17/0.17 | 0.974/0.950 | 6.31/7.61 | 2 | 0 | NON-BUILDABLE | `f4dcd944893b` | silhouette_fit_residual:288, visibility_club:2 |
| poc_driver | B | 500 | iq_gaussian_33us | radar | 0 | false | 0.735 | 1.00 | 1.99 | 0.02/0.91 | -0.05/1.28 | 0.976/0.949 | 6.14/7.53 | 2 | 0 | NON-BUILDABLE | `1b3e8d60d556` | silhouette_fit_residual:263, visibility_club:2 |
| poc_driver | B | 500 | iq_gaussian_33us | radar | 10 | false | 0.695 | 1.48 | 2.68 | 0.10/1.17 | 1.13/2.52 | 0.973/0.950 | 6.14/7.64 | 2 | 0 | NON-BUILDABLE | `d94ab716202e` | silhouette_fit_residual:303, visibility_club:2 |
| poc_driver | B | 500 | iq_gaussian_33us | radar | 20 | false | 0.591 | 2.60 | 3.97 | 0.15/1.40 | 2.38/3.81 | 0.968/0.947 | 6.35/7.63 | 2 | 0 | NON-BUILDABLE | `fc76ab2e97ae` | silhouette_fit_residual:407, visibility_club:2 |
| poc_driver | B | 500 | iq_gaussian_33us | radar | 40 | false | 0.283 | 5.02 | 6.51 | 0.22/2.13 | 4.86/6.24 | 0.948/0.936 | 6.86/7.75 | 2 | 0 | NON-BUILDABLE | `84c52ba0f8c0` | silhouette_fit_residual:715, visibility_club:2 |
| poc_driver | B | 500 | frame_uniform_2.137ms | oracle | 0 | false | 0.764 | 11.34 | 20.78 | 0.78/9.78 | -1.17/15.26 | 0.974/0.952 | 6.11/7.57 | 2 | 0 | REFERENCE | `d53baf2f246e` | silhouette_fit_residual:234, visibility_club:2 |
| poc_driver | B | 500 | frame_uniform_2.137ms | radar | -40 | false | 0.226 | 11.28 | 20.63 | -0.57/7.87 | -3.64/10.86 | 0.948/0.939 | 6.95/7.86 | 2 | 0 | NON-BUILDABLE | `515a57cc62fc` | silhouette_fit_residual:772, visibility_club:2 |
| poc_driver | B | 500 | frame_uniform_2.137ms | radar | -20 | false | 0.613 | 10.92 | 20.87 | -0.55/9.38 | -1.71/12.37 | 0.967/0.948 | 6.40/7.69 | 2 | 0 | NON-BUILDABLE | `b50076fab5e2` | silhouette_fit_residual:385, visibility_club:2 |
| poc_driver | B | 500 | frame_uniform_2.137ms | radar | -10 | false | 0.721 | 10.54 | 20.23 | -0.74/8.69 | -0.37/14.02 | 0.973/0.950 | 6.16/7.57 | 2 | 0 | NON-BUILDABLE | `fea51b52b7f6` | silhouette_fit_residual:277, visibility_club:2 |
| poc_driver | B | 500 | frame_uniform_2.137ms | radar | 0 | false | 0.740 | 11.35 | 20.45 | 0.25/9.10 | -0.52/15.13 | 0.975/0.953 | 5.99/7.58 | 2 | 0 | NON-BUILDABLE | `7ee7a731a0aa` | silhouette_fit_residual:258, visibility_club:2 |
| poc_driver | B | 500 | frame_uniform_2.137ms | radar | 10 | false | 0.717 | 11.65 | 20.42 | -0.25/9.35 | 1.47/16.39 | 0.973/0.951 | 6.04/7.60 | 2 | 0 | NON-BUILDABLE | `c8b13e0a0ac0` | silhouette_fit_residual:281, visibility_club:2 |
| poc_driver | B | 500 | frame_uniform_2.137ms | radar | 20 | false | 0.598 | 10.98 | 20.42 | 0.15/8.91 | 2.33/17.54 | 0.968/0.949 | 6.57/7.71 | 2 | 0 | NON-BUILDABLE | `2d88adff69de` | silhouette_fit_residual:400, visibility_club:2 |
| poc_driver | B | 500 | frame_uniform_2.137ms | radar | 40 | false | 0.280 | 12.78 | 22.99 | -0.58/9.11 | 6.04/20.39 | 0.948/0.939 | 6.80/7.77 | 2 | 0 | NON-BUILDABLE | `093903d327ee` | silhouette_fit_residual:718, visibility_club:2 |
| poc_7iron | A0 | 10 | iq_gaussian_33us | oracle | 0 | false | 1.000 | 1.89 | 3.76 | -0.11/1.99 | -0.03/2.21 | 0.936/0.882 | 3.97/5.40 | 0 | 0 | REFERENCE | `d9bd4f6976bc` | none |
| poc_7iron | A0 | 10 | iq_gaussian_33us | radar | -40 | true | 1.000 | 5.19 | 7.38 | -0.16/2.58 | -4.77/-2.66 | 0.924/0.873 | 4.22/5.63 | 0 | 0 | HARDWARE-BLOCKED | `5fb47e036806` | none |
| poc_7iron | A0 | 10 | iq_gaussian_33us | radar | -20 | true | 1.000 | 2.98 | 5.04 | -0.12/2.14 | -2.39/-0.32 | 0.931/0.877 | 4.06/5.39 | 0 | 0 | HARDWARE-BLOCKED | `28bdb75d5f11` | none |
| poc_7iron | A0 | 10 | iq_gaussian_33us | radar | -10 | true | 1.000 | 2.16 | 4.10 | -0.09/2.05 | -1.21/0.88 | 0.933/0.877 | 4.00/5.31 | 0 | 0 | HARDWARE-BLOCKED | `da56ffa0a602` | none |
| poc_7iron | A0 | 10 | iq_gaussian_33us | radar | 0 | true | 1.000 | 1.87 | 3.61 | -0.04/2.04 | -0.04/2.10 | 0.934/0.878 | 3.97/5.29 | 0 | 0 | HARDWARE-BLOCKED | `82d69a2ca9ab` | none |
| poc_7iron | A0 | 10 | iq_gaussian_33us | radar | 10 | true | 1.000 | 2.13 | 4.04 | -0.02/2.10 | 1.15/3.24 | 0.934/0.877 | 3.99/5.34 | 0 | 0 | HARDWARE-BLOCKED | `290021c62a1e` | none |
| poc_7iron | A0 | 10 | iq_gaussian_33us | radar | 20 | true | 1.000 | 2.92 | 4.99 | 0.06/2.20 | 2.33/4.43 | 0.932/0.877 | 4.07/5.43 | 0 | 0 | HARDWARE-BLOCKED | `1d37e743d1be` | none |
| poc_7iron | A0 | 10 | iq_gaussian_33us | radar | 40 | true | 1.000 | 5.19 | 7.33 | 0.16/2.73 | 4.70/6.87 | 0.925/0.873 | 4.29/5.67 | 0 | 0 | HARDWARE-BLOCKED | `586d1f2089a2` | none |
| poc_7iron | A0 | 10 | frame_uniform_2.137ms | oracle | 0 | false | 1.000 | 10.35 | 19.51 | 0.21/6.45 | -1.51/14.94 | 0.936/0.881 | 4.05/5.38 | 0 | 0 | REFERENCE | `42ba131c4605` | none |
| poc_7iron | A0 | 10 | frame_uniform_2.137ms | radar | -40 | true | 1.000 | 10.98 | 22.07 | -0.66/6.80 | -4.22/11.44 | 0.925/0.876 | 4.24/5.80 | 0 | 0 | HARDWARE-BLOCKED | `b49bcdd849b2` | none |
| poc_7iron | A0 | 10 | frame_uniform_2.137ms | radar | -20 | true | 1.000 | 10.56 | 20.59 | -0.43/6.60 | -1.82/13.66 | 0.932/0.880 | 4.05/5.57 | 0 | 0 | HARDWARE-BLOCKED | `419cb0ba53d9` | none |
| poc_7iron | A0 | 10 | frame_uniform_2.137ms | radar | -10 | true | 1.000 | 10.54 | 19.97 | -0.27/6.42 | -0.56/14.89 | 0.934/0.880 | 4.05/5.53 | 0 | 0 | HARDWARE-BLOCKED | `b239b759b2fb` | none |
| poc_7iron | A0 | 10 | frame_uniform_2.137ms | radar | 0 | true | 1.000 | 10.88 | 19.87 | -0.02/6.40 | 0.63/16.09 | 0.934/0.881 | 4.08/5.52 | 0 | 0 | HARDWARE-BLOCKED | `4a984244ccc2` | none |
| poc_7iron | A0 | 10 | frame_uniform_2.137ms | radar | 10 | true | 1.000 | 10.79 | 20.34 | 0.15/6.41 | 1.82/17.22 | 0.934/0.881 | 4.11/5.55 | 0 | 0 | HARDWARE-BLOCKED | `0d2c4e9081ed` | none |
| poc_7iron | A0 | 10 | frame_uniform_2.137ms | radar | 20 | true | 0.999 | 10.91 | 20.79 | 0.21/6.70 | 2.69/18.36 | 0.931/0.877 | 4.14/5.55 | 0 | 0 | HARDWARE-BLOCKED | `3cdcb441a109` | silhouette_fit_residual:1 |
| poc_7iron | A0 | 10 | frame_uniform_2.137ms | radar | 40 | true | 0.999 | 11.46 | 22.59 | 0.42/6.68 | 5.21/20.65 | 0.923/0.874 | 4.34/5.77 | 0 | 0 | HARDWARE-BLOCKED | `7571c513f105` | silhouette_fit_residual:1 |
| poc_7iron | A0 | 500 | iq_gaussian_33us | oracle | 0 | false | 1.000 | 1.86 | 3.80 | 0.01/2.14 | -0.08/2.17 | 0.940/0.892 | 4.07/5.46 | 0 | 0 | REFERENCE | `ae392b9a017c` | none |
| poc_7iron | A0 | 500 | iq_gaussian_33us | radar | -40 | false | 1.000 | 5.16 | 7.48 | -0.14/2.49 | -4.73/-2.68 | 0.931/0.883 | 4.19/5.82 | 0 | 0 | HARDWARE-BLOCKED | `a5323c907250` | none |
| poc_7iron | A0 | 500 | iq_gaussian_33us | radar | -20 | false | 1.000 | 3.03 | 5.16 | -0.14/2.07 | -2.32/-0.32 | 0.939/0.885 | 4.02/5.61 | 0 | 0 | HARDWARE-BLOCKED | `49de91a4c95f` | none |
| poc_7iron | A0 | 500 | iq_gaussian_33us | radar | -10 | false | 1.000 | 2.21 | 4.29 | -0.10/1.98 | -1.18/0.86 | 0.941/0.886 | 3.99/5.54 | 0 | 0 | HARDWARE-BLOCKED | `efea287629e8` | none |
| poc_7iron | A0 | 500 | iq_gaussian_33us | radar | 0 | false | 1.000 | 1.90 | 3.77 | -0.09/2.00 | 0.01/2.08 | 0.942/0.887 | 4.02/5.46 | 0 | 0 | HARDWARE-BLOCKED | `7c31a9c15280` | none |
| poc_7iron | A0 | 500 | iq_gaussian_33us | radar | 10 | false | 1.000 | 2.24 | 4.06 | -0.06/2.05 | 1.21/3.26 | 0.940/0.886 | 4.04/5.47 | 0 | 0 | HARDWARE-BLOCKED | `4c301f8185b0` | none |
| poc_7iron | A0 | 500 | iq_gaussian_33us | radar | 20 | false | 1.000 | 2.96 | 5.02 | -0.08/2.22 | 2.37/4.41 | 0.938/0.885 | 4.09/5.57 | 0 | 0 | HARDWARE-BLOCKED | `0368fb9e7fb6` | none |
| poc_7iron | A0 | 500 | iq_gaussian_33us | radar | 40 | false | 1.000 | 5.15 | 7.32 | 0.02/2.72 | 4.69/6.87 | 0.929/0.881 | 4.29/5.79 | 0 | 0 | HARDWARE-BLOCKED | `1f344b00bfed` | none |
| poc_7iron | A0 | 500 | frame_uniform_2.137ms | oracle | 0 | false | 1.000 | 11.21 | 20.05 | 0.08/6.77 | 0.25/15.63 | 0.943/0.893 | 4.02/5.56 | 0 | 0 | REFERENCE | `c1245f503efb` | none |
| poc_7iron | A0 | 500 | frame_uniform_2.137ms | radar | -40 | false | 1.000 | 11.36 | 22.03 | -0.71/6.82 | -3.59/12.07 | 0.930/0.888 | 4.29/5.73 | 0 | 0 | HARDWARE-BLOCKED | `c866ab1d6d29` | none |
| poc_7iron | A0 | 500 | frame_uniform_2.137ms | radar | -20 | false | 1.000 | 11.24 | 20.56 | -0.51/6.66 | -1.29/14.44 | 0.939/0.892 | 4.10/5.51 | 0 | 0 | HARDWARE-BLOCKED | `9fceaf7ecbd8` | none |
| poc_7iron | A0 | 500 | frame_uniform_2.137ms | radar | -10 | false | 1.000 | 11.16 | 19.92 | -0.49/6.52 | -0.06/15.56 | 0.941/0.892 | 4.06/5.49 | 0 | 0 | HARDWARE-BLOCKED | `3f525094cda3` | none |
| poc_7iron | A0 | 500 | frame_uniform_2.137ms | radar | 0 | false | 1.000 | 10.98 | 20.09 | -0.44/6.49 | 1.18/16.82 | 0.941/0.892 | 4.03/5.46 | 0 | 0 | HARDWARE-BLOCKED | `60a925115b63` | none |
| poc_7iron | A0 | 500 | frame_uniform_2.137ms | radar | 10 | false | 1.000 | 11.05 | 20.29 | -0.44/6.42 | 2.43/18.06 | 0.941/0.891 | 4.13/5.48 | 0 | 0 | HARDWARE-BLOCKED | `5a24c2588f19` | none |
| poc_7iron | A0 | 500 | frame_uniform_2.137ms | radar | 20 | false | 1.000 | 11.23 | 20.89 | -0.34/6.46 | 3.58/19.29 | 0.939/0.890 | 4.14/5.54 | 0 | 0 | HARDWARE-BLOCKED | `682428b43941` | none |
| poc_7iron | A0 | 500 | frame_uniform_2.137ms | radar | 40 | false | 0.999 | 11.32 | 22.62 | 0.39/6.52 | 5.12/21.09 | 0.931/0.885 | 4.33/5.86 | 0 | 0 | HARDWARE-BLOCKED | `fca9ab0c8c90` | silhouette_fit_residual:1 |
| poc_7iron | A1 | 10 | iq_gaussian_33us | oracle | 0 | false | 0.933 | 1.12 | 2.14 | 0.03/1.06 | -0.08/1.32 | 0.967/0.940 | 5.52/7.17 | 17 | 0 | REFERENCE | `28f12615e686` | silhouette_fit_residual:50, visibility_club:17 |
| poc_7iron | A1 | 10 | iq_gaussian_33us | radar | -40 | false | 0.720 | 5.24 | 6.69 | -0.14/1.95 | -4.98/-3.33 | 0.945/0.927 | 6.27/7.67 | 17 | 0 | NON-BUILDABLE | `dedbff9bbfc5` | silhouette_fit_residual:263, visibility_club:17 |
| poc_7iron | A1 | 10 | iq_gaussian_33us | radar | -20 | false | 0.891 | 2.58 | 4.08 | -0.09/1.27 | -2.33/-0.91 | 0.961/0.938 | 5.85/7.42 | 17 | 0 | HARDWARE-BLOCKED | `8804947081a1` | silhouette_fit_residual:92, visibility_club:17 |
| poc_7iron | A1 | 10 | iq_gaussian_33us | radar | -10 | false | 0.926 | 1.61 | 2.92 | -0.03/1.08 | -1.26/0.21 | 0.966/0.939 | 5.69/7.34 | 17 | 0 | HARDWARE-BLOCKED | `3ceae619fea5` | silhouette_fit_residual:57, visibility_club:17 |
| poc_7iron | A1 | 10 | iq_gaussian_33us | radar | 0 | false | 0.926 | 1.18 | 2.28 | -0.00/1.10 | -0.05/1.44 | 0.966/0.938 | 5.57/7.14 | 17 | 0 | HARDWARE-BLOCKED | `3f6dbad6a467` | silhouette_fit_residual:57, visibility_club:17 |
| poc_7iron | A1 | 10 | iq_gaussian_33us | radar | 10 | false | 0.917 | 1.51 | 2.88 | 0.03/1.28 | 1.15/2.61 | 0.965/0.939 | 5.62/7.33 | 17 | 0 | HARDWARE-BLOCKED | `a60ea92666b4` | silhouette_fit_residual:66, visibility_club:17 |
| poc_7iron | A1 | 10 | iq_gaussian_33us | radar | 20 | false | 0.864 | 2.62 | 4.15 | 0.05/1.41 | 2.36/4.00 | 0.961/0.936 | 5.87/7.40 | 17 | 0 | HARDWARE-BLOCKED | `752a999b5dc2` | silhouette_fit_residual:119, visibility_club:17 |
| poc_7iron | A1 | 10 | iq_gaussian_33us | radar | 40 | false | 0.708 | 5.02 | 6.49 | 0.10/2.16 | 4.76/6.27 | 0.946/0.928 | 6.53/7.63 | 17 | 0 | NON-BUILDABLE | `ac235c5ed54b` | silhouette_fit_residual:275, visibility_club:17 |
| poc_7iron | A1 | 10 | frame_uniform_2.137ms | oracle | 0 | false | 0.917 | 10.96 | 19.89 | 0.01/6.35 | 0.07/16.29 | 0.967/0.937 | 5.51/7.20 | 17 | 0 | REFERENCE | `2cff7c8bb994` | silhouette_fit_residual:66, visibility_club:17 |
| poc_7iron | A1 | 10 | frame_uniform_2.137ms | radar | -40 | false | 0.708 | 11.76 | 21.87 | -0.66/6.63 | -4.10/12.19 | 0.945/0.928 | 6.41/7.68 | 17 | 0 | NON-BUILDABLE | `cdc194a73adb` | silhouette_fit_residual:275, visibility_club:17 |
| poc_7iron | A1 | 10 | frame_uniform_2.137ms | radar | -20 | false | 0.858 | 11.45 | 20.09 | -0.40/6.18 | -2.12/14.15 | 0.962/0.938 | 5.77/7.40 | 17 | 0 | HARDWARE-BLOCKED | `323264c73853` | silhouette_fit_residual:125, visibility_club:17 |
| poc_7iron | A1 | 10 | frame_uniform_2.137ms | radar | -10 | false | 0.898 | 11.22 | 19.89 | -0.10/6.07 | -1.46/15.13 | 0.965/0.938 | 5.59/7.31 | 17 | 0 | HARDWARE-BLOCKED | `7f4f98eef9ab` | silhouette_fit_residual:85, visibility_club:17 |
| poc_7iron | A1 | 10 | frame_uniform_2.137ms | radar | 0 | false | 0.914 | 11.60 | 19.49 | -0.08/6.30 | 0.22/16.19 | 0.966/0.939 | 5.49/7.18 | 17 | 0 | HARDWARE-BLOCKED | `efe94b129aec` | silhouette_fit_residual:69, visibility_club:17 |
| poc_7iron | A1 | 10 | frame_uniform_2.137ms | radar | 10 | false | 0.906 | 10.58 | 19.52 | -0.00/5.87 | 1.39/16.96 | 0.966/0.940 | 5.61/7.29 | 17 | 0 | HARDWARE-BLOCKED | `ecc8426ef6de` | silhouette_fit_residual:77, visibility_club:17 |
| poc_7iron | A1 | 10 | frame_uniform_2.137ms | radar | 20 | false | 0.872 | 11.42 | 20.31 | 0.35/6.32 | 1.45/18.65 | 0.962/0.937 | 5.86/7.46 | 17 | 0 | HARDWARE-BLOCKED | `aceae5ded9f6` | silhouette_fit_residual:111, visibility_club:17 |
| poc_7iron | A1 | 10 | frame_uniform_2.137ms | radar | 40 | false | 0.677 | 11.22 | 21.99 | 0.22/6.36 | 5.07/20.95 | 0.946/0.928 | 6.35/7.67 | 17 | 0 | NON-BUILDABLE | `128ab080c288` | silhouette_fit_residual:306, visibility_club:17 |
| poc_7iron | A1 | 500 | iq_gaussian_33us | oracle | 0 | false | 0.916 | 1.08 | 2.16 | -0.02/1.13 | -0.07/1.34 | 0.970/0.945 | 5.56/7.23 | 34 | 0 | REFERENCE | `e0aede29bb63` | silhouette_fit_residual:50, visibility_club:34 |
| poc_7iron | A1 | 500 | iq_gaussian_33us | radar | -40 | false | 0.691 | 4.90 | 6.34 | -0.07/1.82 | -4.69/-3.19 | 0.946/0.933 | 6.40/7.59 | 34 | 0 | NON-BUILDABLE | `a06dab939667` | silhouette_fit_residual:275, visibility_club:34 |
| poc_7iron | A1 | 500 | iq_gaussian_33us | radar | -20 | false | 0.861 | 2.58 | 3.90 | -0.15/1.25 | -2.40/-0.91 | 0.964/0.944 | 5.91/7.47 | 34 | 0 | HARDWARE-BLOCKED | `3996f0c4d87d` | silhouette_fit_residual:105, visibility_club:34 |
| poc_7iron | A1 | 500 | iq_gaussian_33us | radar | -10 | false | 0.890 | 1.51 | 2.79 | -0.05/1.10 | -1.15/0.21 | 0.969/0.944 | 5.64/7.26 | 34 | 0 | HARDWARE-BLOCKED | `17495cfc947d` | silhouette_fit_residual:76, visibility_club:34 |
| poc_7iron | A1 | 500 | iq_gaussian_33us | radar | 0 | false | 0.918 | 1.10 | 2.13 | -0.00/1.05 | 0.05/1.43 | 0.971/0.946 | 5.54/7.20 | 34 | 0 | HARDWARE-BLOCKED | `d53675deed31` | silhouette_fit_residual:48, visibility_club:34 |
| poc_7iron | A1 | 500 | iq_gaussian_33us | radar | 10 | false | 0.901 | 1.54 | 2.99 | 0.03/1.17 | 1.22/2.71 | 0.968/0.945 | 5.60/7.28 | 34 | 0 | HARDWARE-BLOCKED | `2171265345eb` | silhouette_fit_residual:65, visibility_club:34 |
| poc_7iron | A1 | 500 | iq_gaussian_33us | radar | 20 | false | 0.878 | 2.55 | 3.95 | 0.06/1.38 | 2.36/3.80 | 0.964/0.941 | 5.83/7.42 | 34 | 0 | HARDWARE-BLOCKED | `b28ef8971d5b` | silhouette_fit_residual:88, visibility_club:34 |
| poc_7iron | A1 | 500 | iq_gaussian_33us | radar | 40 | false | 0.687 | 4.91 | 6.54 | 0.05/2.03 | 4.74/6.36 | 0.947/0.933 | 6.38/7.67 | 34 | 0 | NON-BUILDABLE | `c50f054849ac` | silhouette_fit_residual:279, visibility_club:34 |
| poc_7iron | A1 | 500 | frame_uniform_2.137ms | oracle | 0 | false | 0.895 | 10.70 | 19.74 | -0.26/6.09 | 1.01/15.84 | 0.970/0.943 | 5.53/7.18 | 34 | 0 | REFERENCE | `288c43d91d94` | silhouette_fit_residual:71, visibility_club:34 |
| poc_7iron | A1 | 500 | frame_uniform_2.137ms | radar | -40 | false | 0.707 | 11.89 | 22.27 | -0.11/6.68 | -5.54/11.16 | 0.947/0.934 | 6.36/7.67 | 34 | 0 | NON-BUILDABLE | `2b157899d607` | silhouette_fit_residual:259, visibility_club:34 |
| poc_7iron | A1 | 500 | frame_uniform_2.137ms | radar | -20 | false | 0.858 | 11.96 | 20.73 | -0.67/6.82 | -1.06/14.57 | 0.965/0.941 | 5.75/7.40 | 34 | 0 | HARDWARE-BLOCKED | `22c35bec3852` | silhouette_fit_residual:108, visibility_club:34 |
| poc_7iron | A1 | 500 | frame_uniform_2.137ms | radar | -10 | false | 0.900 | 11.13 | 19.95 | -0.72/6.46 | 0.66/15.45 | 0.969/0.946 | 5.65/7.24 | 34 | 0 | HARDWARE-BLOCKED | `893c0c0a8e06` | silhouette_fit_residual:66, visibility_club:34 |
| poc_7iron | A1 | 500 | frame_uniform_2.137ms | radar | 0 | false | 0.914 | 10.80 | 19.75 | -0.23/6.15 | 1.02/16.30 | 0.971/0.945 | 5.58/7.15 | 34 | 0 | HARDWARE-BLOCKED | `e9d92eb72511` | silhouette_fit_residual:52, visibility_club:34 |
| poc_7iron | A1 | 500 | frame_uniform_2.137ms | radar | 10 | false | 0.885 | 10.76 | 19.60 | 0.22/6.31 | 1.39/16.89 | 0.969/0.945 | 5.56/7.34 | 34 | 0 | HARDWARE-BLOCKED | `5c7a9e27a67a` | silhouette_fit_residual:81, visibility_club:34 |
| poc_7iron | A1 | 500 | frame_uniform_2.137ms | radar | 20 | false | 0.848 | 11.15 | 19.90 | 0.13/6.49 | 2.65/18.09 | 0.965/0.942 | 5.67/7.45 | 34 | 0 | HARDWARE-BLOCKED | `1b96be42516d` | silhouette_fit_residual:118, visibility_club:34 |
| poc_7iron | A1 | 500 | frame_uniform_2.137ms | radar | 40 | false | 0.697 | 11.09 | 22.39 | 0.08/6.11 | 5.48/21.24 | 0.948/0.933 | 6.43/7.66 | 34 | 0 | NON-BUILDABLE | `c063b9bf0101` | silhouette_fit_residual:269, visibility_club:34 |
| poc_7iron | B | 10 | iq_gaussian_33us | oracle | 0 | false | 0.930 | 1.07 | 2.21 | -0.01/0.98 | 0.01/1.37 | 0.967/0.939 | 5.54/7.29 | 0 | 0 | REFERENCE | `934529df6f1f` | silhouette_fit_residual:70 |
| poc_7iron | B | 10 | iq_gaussian_33us | radar | -40 | false | 0.727 | 4.98 | 6.45 | -0.05/1.84 | -4.71/-3.31 | 0.945/0.928 | 6.37/7.69 | 0 | 0 | NON-BUILDABLE | `f0752e6a3ede` | silhouette_fit_residual:273 |
| poc_7iron | B | 10 | iq_gaussian_33us | radar | -20 | false | 0.905 | 2.59 | 3.98 | -0.03/1.38 | -2.33/-0.87 | 0.962/0.938 | 5.69/7.44 | 0 | 0 | HARDWARE-BLOCKED | `286fa821dae2` | silhouette_fit_residual:95 |
| poc_7iron | B | 10 | iq_gaussian_33us | radar | -10 | false | 0.928 | 1.57 | 2.87 | -0.02/1.16 | -1.16/0.24 | 0.966/0.938 | 5.65/7.21 | 0 | 0 | HARDWARE-BLOCKED | `328995684517` | silhouette_fit_residual:72 |
| poc_7iron | B | 10 | iq_gaussian_33us | radar | 0 | false | 0.926 | 1.14 | 2.30 | -0.02/1.09 | 0.01/1.42 | 0.967/0.940 | 5.62/7.18 | 0 | 0 | HARDWARE-BLOCKED | `de98cca42df3` | silhouette_fit_residual:74 |
| poc_7iron | B | 10 | iq_gaussian_33us | radar | 10 | false | 0.918 | 1.56 | 2.94 | 0.07/1.19 | 1.18/2.64 | 0.964/0.937 | 5.65/7.35 | 0 | 0 | HARDWARE-BLOCKED | `2c85bfb6540d` | silhouette_fit_residual:82 |
| poc_7iron | B | 10 | iq_gaussian_33us | radar | 20 | false | 0.863 | 2.66 | 4.06 | 0.10/1.35 | 2.46/3.86 | 0.962/0.936 | 5.75/7.42 | 0 | 0 | HARDWARE-BLOCKED | `bcddc2ad2b70` | silhouette_fit_residual:137 |
| poc_7iron | B | 10 | iq_gaussian_33us | radar | 40 | false | 0.711 | 5.00 | 6.47 | 0.22/2.10 | 4.76/6.24 | 0.946/0.929 | 6.37/7.66 | 0 | 0 | NON-BUILDABLE | `737b1eea235b` | silhouette_fit_residual:289 |
| poc_7iron | B | 10 | frame_uniform_2.137ms | oracle | 0 | false | 0.931 | 11.37 | 19.96 | -0.00/6.41 | -0.50/16.29 | 0.966/0.938 | 5.56/7.26 | 0 | 0 | REFERENCE | `5a0f2051d225` | silhouette_fit_residual:69 |
| poc_7iron | B | 10 | frame_uniform_2.137ms | radar | -40 | false | 0.686 | 10.68 | 23.16 | -0.41/6.34 | -5.36/10.96 | 0.946/0.929 | 6.41/7.65 | 0 | 0 | NON-BUILDABLE | `4b9f027fbe95` | silhouette_fit_residual:314 |
| poc_7iron | B | 10 | frame_uniform_2.137ms | radar | -20 | false | 0.880 | 11.22 | 20.14 | 0.12/6.11 | -3.43/13.85 | 0.962/0.935 | 5.83/7.43 | 0 | 0 | HARDWARE-BLOCKED | `7bf8bd8195b5` | silhouette_fit_residual:120 |
| poc_7iron | B | 10 | frame_uniform_2.137ms | radar | -10 | false | 0.920 | 10.75 | 19.57 | -0.18/6.00 | -1.15/14.96 | 0.966/0.938 | 5.62/7.29 | 0 | 0 | HARDWARE-BLOCKED | `93c53e1c4d2c` | silhouette_fit_residual:80 |
| poc_7iron | B | 10 | frame_uniform_2.137ms | radar | 0 | false | 0.929 | 10.35 | 19.67 | 0.09/5.98 | 0.12/15.63 | 0.967/0.935 | 5.52/7.20 | 0 | 0 | HARDWARE-BLOCKED | `b081d3c39d95` | silhouette_fit_residual:71 |
| poc_7iron | B | 10 | frame_uniform_2.137ms | radar | 10 | false | 0.909 | 11.23 | 19.61 | -0.27/5.91 | 2.43/17.09 | 0.966/0.935 | 5.61/7.28 | 0 | 0 | HARDWARE-BLOCKED | `0027b7ebab51` | silhouette_fit_residual:91 |
| poc_7iron | B | 10 | frame_uniform_2.137ms | radar | 20 | false | 0.868 | 10.91 | 20.19 | 0.44/6.28 | 1.61/18.49 | 0.962/0.938 | 5.84/7.45 | 0 | 0 | HARDWARE-BLOCKED | `be48c708d640` | silhouette_fit_residual:132 |
| poc_7iron | B | 10 | frame_uniform_2.137ms | radar | 40 | false | 0.721 | 10.89 | 22.64 | 0.04/6.30 | 5.64/21.25 | 0.946/0.927 | 6.54/7.66 | 0 | 0 | NON-BUILDABLE | `7b7b4bd3b672` | silhouette_fit_residual:279 |
| poc_7iron | B | 500 | iq_gaussian_33us | oracle | 0 | false | 0.941 | 1.11 | 2.18 | -0.08/1.08 | 0.03/1.44 | 0.970/0.943 | 5.57/7.28 | 0 | 0 | REFERENCE | `a42c2fa2c97d` | silhouette_fit_residual:59 |
| poc_7iron | B | 500 | iq_gaussian_33us | radar | -40 | false | 0.705 | 5.01 | 6.60 | -0.16/1.76 | -4.80/-3.36 | 0.946/0.931 | 6.33/7.63 | 0 | 0 | NON-BUILDABLE | `e7e623260348` | silhouette_fit_residual:295 |
| poc_7iron | B | 500 | iq_gaussian_33us | radar | -20 | false | 0.878 | 2.55 | 3.96 | -0.16/1.20 | -2.29/-0.84 | 0.964/0.940 | 5.87/7.42 | 0 | 0 | HARDWARE-BLOCKED | `d6be5f073007` | silhouette_fit_residual:122 |
| poc_7iron | B | 500 | iq_gaussian_33us | radar | -10 | false | 0.924 | 1.51 | 2.84 | -0.01/1.10 | -1.17/0.30 | 0.969/0.944 | 5.69/7.27 | 0 | 0 | HARDWARE-BLOCKED | `70ffe9e6cad6` | silhouette_fit_residual:76 |
| poc_7iron | B | 500 | iq_gaussian_33us | radar | 0 | false | 0.929 | 1.11 | 2.24 | -0.00/1.08 | 0.04/1.53 | 0.969/0.943 | 5.64/7.23 | 0 | 0 | HARDWARE-BLOCKED | `28c9693fb458` | silhouette_fit_residual:71 |
| poc_7iron | B | 500 | iq_gaussian_33us | radar | 10 | false | 0.924 | 1.51 | 2.87 | 0.12/1.20 | 1.14/2.66 | 0.968/0.943 | 5.73/7.27 | 0 | 0 | HARDWARE-BLOCKED | `b1ff548878c3` | silhouette_fit_residual:76 |
| poc_7iron | B | 500 | iq_gaussian_33us | radar | 20 | false | 0.882 | 2.63 | 4.00 | 0.11/1.33 | 2.42/3.81 | 0.965/0.943 | 5.91/7.36 | 0 | 0 | HARDWARE-BLOCKED | `06b484f0b61c` | silhouette_fit_residual:118 |
| poc_7iron | B | 500 | iq_gaussian_33us | radar | 40 | false | 0.703 | 4.98 | 6.54 | 0.11/2.15 | 4.71/6.32 | 0.948/0.934 | 6.40/7.69 | 0 | 0 | NON-BUILDABLE | `4a81b7429ac9` | silhouette_fit_residual:297 |
| poc_7iron | B | 500 | frame_uniform_2.137ms | oracle | 0 | false | 0.931 | 10.71 | 19.39 | -0.27/6.04 | 0.80/16.06 | 0.970/0.943 | 5.50/7.24 | 0 | 0 | REFERENCE | `be94ccae111c` | silhouette_fit_residual:69 |
| poc_7iron | B | 500 | frame_uniform_2.137ms | radar | -40 | false | 0.709 | 11.03 | 22.35 | -0.05/6.82 | -4.87/10.31 | 0.947/0.934 | 6.48/7.69 | 0 | 0 | NON-BUILDABLE | `fc47cd173162` | silhouette_fit_residual:291 |
| poc_7iron | B | 500 | frame_uniform_2.137ms | radar | -20 | false | 0.882 | 10.97 | 20.38 | -0.41/6.17 | -1.89/13.36 | 0.965/0.942 | 5.78/7.34 | 0 | 0 | HARDWARE-BLOCKED | `1178da6bfc42` | silhouette_fit_residual:118 |
| poc_7iron | B | 500 | frame_uniform_2.137ms | radar | -10 | false | 0.928 | 11.04 | 19.92 | -0.02/6.27 | -1.15/15.13 | 0.968/0.943 | 5.72/7.29 | 0 | 0 | HARDWARE-BLOCKED | `88fcd8fdd7e5` | silhouette_fit_residual:72 |
| poc_7iron | B | 500 | frame_uniform_2.137ms | radar | 0 | false | 0.931 | 11.42 | 19.73 | 0.07/6.49 | -0.16/16.38 | 0.971/0.946 | 5.63/7.35 | 0 | 0 | HARDWARE-BLOCKED | `c67f73037bab` | silhouette_fit_residual:69 |
| poc_7iron | B | 500 | frame_uniform_2.137ms | radar | 10 | false | 0.910 | 10.13 | 19.55 | 0.27/6.22 | 0.88/16.44 | 0.969/0.944 | 5.67/7.42 | 0 | 0 | HARDWARE-BLOCKED | `26525d414e62` | silhouette_fit_residual:90 |
| poc_7iron | B | 500 | frame_uniform_2.137ms | radar | 20 | false | 0.876 | 10.59 | 19.65 | 0.21/6.00 | 2.38/17.92 | 0.966/0.943 | 5.95/7.58 | 0 | 0 | HARDWARE-BLOCKED | `361fc25c5bc5` | silhouette_fit_residual:124 |
| poc_7iron | B | 500 | frame_uniform_2.137ms | radar | 40 | false | 0.710 | 11.25 | 21.75 | 0.73/6.51 | 4.27/20.51 | 0.948/0.934 | 6.40/7.66 | 0 | 0 | NON-BUILDABLE | `0e0ae8a35be8` | silhouette_fit_residual:290 |

## Mandatory stress grid — all 44 cells

| club | stress case | n | solve | impact med | impact p90 | IoU med | fit p90 | vis | rejection categories | hash |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| poc_driver | zero_noise_recovery | 256 | 1.000 | 0.00 | 0.00 | 1.000 | 0.00 | 0 | none | `abbafe319e07` |
| poc_driver | fov_edge_partial_visibility | 256 | 0.211 | 1.51 | 2.75 | 0.945 | 6.24 | 202 | visibility_club:202 | `4fcb46622af7` |
| poc_driver | forward_motion | 256 | 1.000 | 1.61 | 3.14 | 0.946 | 6.28 | 0 | none | `632ac362fe5a` |
| poc_driver | reverse_motion | 256 | 1.000 | 1.84 | 3.24 | 0.943 | 6.03 | 0 | none | `0f55741ed6fa` |
| poc_driver | ball_overlap | 256 | 0.000 | — | — | — | — | 0 | component_ball_overlap:256 | `c847321153b9` |
| poc_driver | shaft_connected | 256 | 0.000 | — | — | — | — | 0 | component_shaft_connected:256 | `5f56096bf679` |
| poc_driver | false_component | 256 | 0.000 | — | — | — | — | 0 | component_false_positive:256 | `d32a86170c30` |
| poc_driver | dropped_frame | 256 | 0.000 | — | — | — | — | 0 | dropped_frame:256 | `103292b57fc5` |
| poc_driver | template_dimension_perturbation | 256 | 0.512 | 1.68 | 3.11 | 0.880 | 7.79 | 0 | silhouette_fit_residual:125 | `cf0910bc9c9b` |
| poc_driver | leave_one_template_out | 256 | 0.020 | 1.20 | 1.74 | 0.815 | 7.76 | 0 | silhouette_fit_residual:251 | `217416e92dea` |
| poc_driver | translation_acceleration | 256 | 0.996 | 4.95 | 6.66 | 0.946 | 6.27 | 0 | silhouette_fit_residual:1 | `9753f10ac7ad` |
| poc_driver | angular_acceleration | 256 | 0.992 | 1.71 | 3.54 | 0.949 | 6.37 | 0 | silhouette_fit_residual:2 | `2fab236bd9bf` |
| poc_driver | maximum_extrapolation_horizon | 256 | 0.000 | — | — | — | — | 0 | extrapolation_horizon:256 | `e426d69733e7` |
| poc_driver | radar_low_confidence | 256 | 0.000 | — | — | — | — | 0 | radar_low_confidence:256 | `cc81c5e0b16a` |
| poc_driver | radar_reduced_inliers | 256 | 0.000 | — | — | — | — | 0 | radar_insufficient_inliers:256 | `8c08d2337e2f` |
| poc_driver | radar_measured_rms | 256 | 0.996 | 1.73 | 3.46 | 0.946 | 6.62 | 0 | silhouette_fit_residual:1 | `22ce0dbfea8b` |
| poc_driver | radar_missing | 256 | 0.000 | — | — | — | — | 0 | radar_missing:256 | `9d60a08cc97e` |
| poc_driver | camera_radar_extrinsic_offset | 256 | 0.996 | 11.43 | 13.04 | 0.945 | 6.09 | 0 | silhouette_fit_residual:1 | `cb272f4700ab` |
| poc_driver | camera_radar_time_offset | 256 | 0.996 | 5.49 | 7.17 | 0.949 | 6.58 | 0 | silhouette_fit_residual:1 | `9888bfccb23b` |
| poc_driver | lens_distortion | 256 | 0.992 | 1.96 | 3.85 | 0.936 | 6.19 | 0 | silhouette_fit_residual:2 | `90ca5487e231` |
| poc_driver | principal_point_offset | 256 | 1.000 | 5.62 | 7.21 | 0.841 | 6.29 | 0 | none | `de80aa34bad0` |
| poc_driver | signed_range_residual_symmetry | 256 | 1.000 | 2.88 | 4.57 | 0.943 | 6.62 | 0 | none | `a67f3f006768` |
| poc_7iron | zero_noise_recovery | 256 | 1.000 | 0.00 | 0.00 | 1.000 | 0.00 | 0 | none | `9c2f52914b38` |
| poc_7iron | fov_edge_partial_visibility | 256 | 0.254 | 1.79 | 4.09 | 0.930 | 5.32 | 191 | visibility_club:191 | `1b59e522e598` |
| poc_7iron | forward_motion | 256 | 1.000 | 1.94 | 3.74 | 0.933 | 5.58 | 0 | none | `b5f743b4ae4e` |
| poc_7iron | reverse_motion | 256 | 1.000 | 1.98 | 3.68 | 0.930 | 5.13 | 0 | none | `6c79d925183d` |
| poc_7iron | ball_overlap | 256 | 0.000 | — | — | — | — | 0 | component_ball_overlap:256 | `e6b7fecee1ab` |
| poc_7iron | shaft_connected | 256 | 0.000 | — | — | — | — | 0 | component_shaft_connected:256 | `facc26d74437` |
| poc_7iron | false_component | 256 | 0.000 | — | — | — | — | 0 | component_false_positive:256 | `3320aa16d521` |
| poc_7iron | dropped_frame | 256 | 0.000 | — | — | — | — | 0 | dropped_frame:256 | `0ccca7e024e5` |
| poc_7iron | template_dimension_perturbation | 256 | 0.977 | 1.71 | 3.16 | 0.879 | 7.07 | 0 | silhouette_fit_residual:6 | `445ecd0a6d6b` |
| poc_7iron | leave_one_template_out | 256 | 0.477 | 1.70 | 3.17 | 0.818 | 7.87 | 0 | silhouette_fit_residual:134 | `4b114990076e` |
| poc_7iron | translation_acceleration | 256 | 1.000 | 5.06 | 7.22 | 0.936 | 5.39 | 0 | none | `f02d437b8a87` |
| poc_7iron | angular_acceleration | 256 | 1.000 | 2.16 | 4.26 | 0.933 | 5.37 | 0 | none | `a77ebf8ca842` |
| poc_7iron | maximum_extrapolation_horizon | 256 | 0.000 | — | — | — | — | 0 | extrapolation_horizon:256 | `78de7e46f554` |
| poc_7iron | radar_low_confidence | 256 | 0.000 | — | — | — | — | 0 | radar_low_confidence:256 | `1cb6394f38e2` |
| poc_7iron | radar_reduced_inliers | 256 | 0.000 | — | — | — | — | 0 | radar_insufficient_inliers:256 | `f285645f7a0a` |
| poc_7iron | radar_measured_rms | 256 | 1.000 | 1.91 | 3.74 | 0.934 | 5.25 | 0 | none | `c7a1531aaad0` |
| poc_7iron | radar_missing | 256 | 0.000 | — | — | — | — | 0 | radar_missing:256 | `492265d7923a` |
| poc_7iron | camera_radar_extrinsic_offset | 256 | 1.000 | 11.31 | 13.20 | 0.933 | 5.47 | 0 | none | `5e959e4d25dc` |
| poc_7iron | camera_radar_time_offset | 256 | 1.000 | 5.16 | 7.23 | 0.939 | 5.56 | 0 | none | `445a9721eec5` |
| poc_7iron | lens_distortion | 256 | 1.000 | 2.28 | 4.23 | 0.922 | 5.38 | 0 | none | `0d16462e1dca` |
| poc_7iron | principal_point_offset | 256 | 1.000 | 5.82 | 7.69 | 0.797 | 5.50 | 0 | none | `d84a26cb802c` |
| poc_7iron | signed_range_residual_symmetry | 256 | 1.000 | 2.91 | 5.39 | 0.935 | 5.56 | 0 | none | `cdbd929d3157` |

## Interpretation

- Oracle cells are references and can never win the buildable gate.
- A1 cells are plate-scale sensitivity only.
- Preset B cells are theoretical and can never win before Gate B1.
- Iron synthetic passes are not product passes; Gate R keeps them hardware-blocked.
- Visibility failures remain in the solve-rate denominator.
- The frame timing model samples the exact uniform interval, not a variance-matched Gaussian.
