# Stage 0E Results - Ball-spin error budget

All attempts are counted, including FOV loss, sparse-frame solves, and wrap ambiguity.

Run source: `python research/ball_spin/run_budget_0e.py --n 500 --output-dir research/ball_spin`.
The sweep wrote 108 cells: driver, iron, and wedge; baseline plus one-at-a-time tornado axes.

## Headline Numbers

| question | finding |
|---|---|
| Display spin-axis target, 2-3 deg | Driver and iron need a shorter gap than 240 fps: `dt=2 ms` clears the target. Wedge clears at `dt=4.2 ms`. |
| Driver-face cross-check target, 5 deg | Driver clears at `dt=2 ms`; `dt=4.2 ms` does not meet the `ok_rate >= 0.9` gate. |
| Rate target, <=3 percent | Cleared in the same recommended cells: driver `2.28%`, iron `0.73%`, wedge `0.29%`. |
| Biggest capture limiter | Not centroid noise; usable-frame/wrap accounting dominates driver and iron at 240 fps. |
| Quartering camera | Not worth it from this sweep: quarter-20 is similar, quarter-40 can hurt driver FOV/solve rate. |
| Stereo | Helps axis error when it solves, but does not fix the driver/iron 240 fps `ok_rate` failure by itself. |

## Capture Spec

These are the practical cells read from the tornado. The one-at-a-time boundary table below is stricter about the baseline context; the recommendations here choose the cell that actually clears both `ok_rate >= 0.9` and the rate/axis target.

| regime | capture spec that clears display axis <=2-3 deg | ok_rate | usable frames | rate err % | axis err deg | note |
|---|---|---:|---:|---:|---:|---|
| driver | `dt=2 ms` (~500 fps), 4 frames, 27 dots, 100 px ball, behind mono | 0.98 | 2.0 | 2.28 | 1.92 | Also clears the 5 deg driver-face cross-check. 1 deg was not met. |
| iron | `dt=2 ms` (~500 fps), 4 frames, 27 dots, 100 px ball, behind mono | 1.00 | 3.0 | 0.73 | 0.84 | Clears 1, 2, 3, and 5 deg tiers in this sweep. |
| wedge | `dt=4.2 ms` (240 fps), 4 frames, 27 dots, 100 px ball, behind mono | 1.00 | 2.0 | 0.29 | 0.75 | 240 fps works; `dt=8 ms` fails by wrap/usable-frame accounting. |

The uncomfortable finding is that the MLM2PRO-shaped baseline at 240 fps is not a universal answer in this model: driver baseline `ok_rate=0.29`, iron baseline `ok_rate=0.21`, wedge baseline `ok_rate=1.00`.

## Vantage And Stereo

| regime | behind mono axis err | quarter-20 axis err | quarter-40 axis err | mono axis err | stereo axis err | interpretation |
|---|---:|---:|---:|---:|---:|---|
| driver | 1.14 deg, ok 0.23 | 1.31 deg, ok 0.30 | failed, ok 0.00 | 1.48 deg, ok 0.30 | 0.82 deg, ok 0.27 | Stereo improves solved-shot axis, but neither quartering nor stereo fixes the low 240 fps ok rate. |
| iron | 0.74 deg, ok 0.19 | 0.77 deg, ok 0.19 | 0.79 deg, ok 0.18 | 0.76 deg, ok 0.15 | 0.53 deg, ok 0.19 | Quartering is basically neutral; stereo gives a modest axis improvement. |
| wedge | 0.82 deg, ok 1.00 | 0.75 deg, ok 1.00 | 0.83 deg, ok 1.00 | 0.80 deg, ok 1.00 | 0.55 deg, ok 1.00 | Stereo helps, quarter-20 is slightly better than behind, but the gain is not capture-defining. |

Verdict: keep straight-behind as the primary geometry for this stage. Stereo is useful for margin, especially wedge/iron axis, but it is secondary to frame gap.

## Iron And Wedge Wrap Limits

| regime/cell | ok_rate | usable frames | rate err % | axis err deg | finding |
|---|---:|---:|---:|---:|---|
| driver `dt=1 ms` | 1.00 | 4.0 | 2.32 | 1.99 | Solves, but not enough for a 1 deg tier. |
| driver `dt=2 ms` | 0.98 | 2.0 | 2.28 | 1.92 | Recommended driver gap. |
| driver `dt=4.2 ms` | 0.26 | 1.0 | 1.82 | 1.22 | Solved-shot medians look good, but too many attempts fail. |
| iron `dt=2 ms` | 1.00 | 3.0 | 0.73 | 0.84 | Recommended iron gap. |
| iron `dt=4.2 ms` | 0.19 | 2.0 | 0.74 | 0.74 | Marginal/failing at 240 fps; the pinned test shows 7500 rpm @ 4.2 ms is genuinely ambiguous. |
| wedge `dt=4.2 ms` | 1.00 | 2.0 | 0.31 | 0.76 | Wedge disambiguates at 240 fps via the wedge prior. |
| wedge `dt=8 ms` | 0.00 | 1.0 | nan | nan | Hard wrap/usable-frame failure. |

## Detector Quality Gate

| axis | driver | iron | wedge | gate read |
|---|---|---|---|---|
| `sigma_dot_px=1` | axis 1.79, rate 1.87, ok 0.29 | axis 1.02, rate 0.92, ok 0.20 | axis 0.94, rate 0.37, ok 1.00 | Dot centroid noise up to 1 px is tolerable in solved shots; not the dominant driver/iron limiter. |
| `sigma_center_px=1` | axis 2.37, rate 2.73, ok 0.26 | axis 1.52, rate 1.26, ok 0.16 | axis 1.45, rate 0.56, ok 1.00 | Limb center should be around <=1 px for 2-3 deg display. |
| `sigma_center_px=2` | axis 4.40, rate 5.41, ok 0.27 | axis 2.63, rate 2.79, ok 0.20 | axis 2.64, rate 1.07, ok 1.00 | 2 px center error breaks driver and consumes most of the display budget for iron/wedge. |
| `p_misid=5%` | axis 1.38, rate 1.66, ok 0.26 | axis 0.75, rate 0.90, ok 0.18 | axis 0.84, rate 0.32, ok 1.00 | The pinned outlier loop contains 5% misID in this model. Bench work still needs to prove this rate is realistic. |
| `ball_px=60` | axis 2.39, rate 2.37, ok 0.24 | axis 1.02, rate 0.90, ok 0.17 | axis 1.36, rate 0.47, ok 1.00 | 60 px is too thin for a 1 deg tier; 100 px is the practical baseline. |
| `ball_px=150-250` | driver axis 0.77 -> 0.53 | iron axis 0.47 -> 0.34 | wedge axis 0.51 -> 0.31 | Larger ball images buy real margin, especially if chasing <=1 deg. |

## Requirement Boundaries

### Driver

| axis target | n_frames | dt_ms | dots | sigma_dot_px | sigma_center_px | misID | beta | ball_px |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 deg | not met | not met | not met | not met | not met | not met | not met | not met |
| 2 deg | not met | 2 | not met | not met | not met | not met | not met | not met |
| 3 deg | not met | 2 | not met | not met | not met | not met | not met | not met |
| 5 deg | not met | 2 | not met | not met | not met | not met | not met | not met |
| 10 deg | not met | 2 | not met | not met | not met | not met | not met | not met |

### Iron

| axis target | n_frames | dt_ms | dots | sigma_dot_px | sigma_center_px | misID | beta | ball_px |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 deg | not met | 2 | not met | not met | not met | not met | not met | not met |
| 2 deg | not met | 2 | not met | not met | not met | not met | not met | not met |
| 3 deg | not met | 2 | not met | not met | not met | not met | not met | not met |
| 5 deg | not met | 2 | not met | not met | not met | not met | not met | not met |
| 10 deg | not met | 2 | not met | not met | not met | not met | not met | not met |

### Wedge

| axis target | n_frames | dt_ms | dots | sigma_dot_px | sigma_center_px | misID | beta | ball_px |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 deg | 2 | 4.2 | 20 | 1 | 0.3 | 0.05 | 0.4 | 100 |
| 2 deg | 2 | 4.2 | 20 | 1 | 1 | 0.05 | 0.4 | 60 |
| 3 deg | 2 | 4.2 | 20 | 1 | 2 | 0.05 | 0.4 | 60 |
| 5 deg | 2 | 4.2 | 20 | 1 | 2 | 0.05 | 0.4 | 60 |
| 10 deg | 2 | 4.2 | 20 | 1 | 2 | 0.05 | 0.4 | 60 |

## Cells

| regime | axis | value | ok_rate | usable frames | rate err % | axis err deg | tilt err deg |
|---|---|---:|---:|---:|---:|---:|---:|
| driver | combined | baseline | 0.29 | 1.0 | 1.69 | 1.35 | 0.75 |
| driver | n_frames | 2 | 0.24 | 1.0 | 1.39 | 1.30 | 0.79 |
| driver | n_frames | 3 | 0.29 | 1.0 | 1.44 | 1.29 | 0.77 |
| driver | n_frames | 4 | 0.30 | 1.0 | 1.65 | 1.26 | 0.89 |
| driver | n_frames | 6 | 0.26 | 1.0 | 1.45 | 1.32 | 0.79 |
| driver | n_frames | 8 | 0.24 | 1.0 | 1.58 | 1.49 | 0.87 |
| driver | dt_ms | 1 | 1.00 | 4.0 | 2.32 | 1.99 | 1.42 |
| driver | dt_ms | 2 | 0.98 | 2.0 | 2.28 | 1.92 | 1.41 |
| driver | dt_ms | 4.2 | 0.26 | 1.0 | 1.82 | 1.22 | 0.75 |
| driver | dt_ms | 8 | 0.00 | 1.0 | nan | nan | nan |
| driver | n_dots | 12 | 0.02 | 0.0 | 2.95 | 1.40 | 0.78 |
| driver | n_dots | 20 | 0.20 | 1.0 | 1.96 | 1.40 | 0.84 |
| driver | n_dots | 27 | 0.25 | 1.0 | 1.63 | 1.29 | 0.86 |
| driver | n_dots | 40 | 0.29 | 1.0 | 1.50 | 1.19 | 0.83 |
| driver | sigma_dot_px | 0.2 | 0.27 | 1.0 | 1.30 | 1.22 | 0.70 |
| driver | sigma_dot_px | 0.5 | 0.26 | 1.0 | 1.72 | 1.34 | 0.83 |
| driver | sigma_dot_px | 1 | 0.29 | 1.0 | 1.87 | 1.79 | 1.04 |
| driver | sigma_center_px | 0.3 | 0.25 | 1.0 | 1.25 | 0.91 | 0.55 |
| driver | sigma_center_px | 1 | 0.26 | 1.0 | 2.73 | 2.37 | 1.65 |
| driver | sigma_center_px | 2 | 0.27 | 1.0 | 5.41 | 4.40 | 2.84 |
| driver | p_misid | 0 | 0.28 | 1.0 | 1.57 | 1.44 | 0.94 |
| driver | p_misid | 0.01 | 0.27 | 1.0 | 1.28 | 1.22 | 0.79 |
| driver | p_misid | 0.02 | 0.24 | 1.0 | 1.27 | 1.05 | 0.77 |
| driver | p_misid | 0.05 | 0.26 | 1.0 | 1.66 | 1.38 | 0.88 |
| driver | beta | 0.15 | 0.27 | 1.0 | 1.59 | 1.29 | 0.84 |
| driver | beta | 0.25 | 0.26 | 1.0 | 1.42 | 1.25 | 0.84 |
| driver | beta | 0.4 | 0.26 | 1.0 | 1.32 | 1.25 | 0.70 |
| driver | ball_px | 60 | 0.24 | 1.0 | 2.37 | 2.39 | 1.43 |
| driver | ball_px | 100 | 0.26 | 1.0 | 1.33 | 1.13 | 0.69 |
| driver | ball_px | 150 | 0.21 | 1.0 | 0.82 | 0.77 | 0.54 |
| driver | ball_px | 250 | 0.26 | 1.0 | 0.55 | 0.53 | 0.29 |
| driver | vantage | behind | 0.23 | 1.0 | 1.46 | 1.14 | 0.79 |
| driver | vantage | quarter-20 | 0.30 | 1.0 | 1.40 | 1.31 | 0.77 |
| driver | vantage | quarter-40 | 0.00 | 1.0 | nan | nan | nan |
| driver | mode | mono | 0.30 | 1.0 | 1.37 | 1.48 | 0.88 |
| driver | mode | stereo | 0.27 | 1.0 | 1.17 | 0.82 | 0.52 |
| iron | combined | baseline | 0.21 | 2.0 | 0.72 | 0.77 | 0.31 |
| iron | n_frames | 2 | 0.20 | 2.0 | 0.65 | 0.79 | 0.29 |
| iron | n_frames | 3 | 0.20 | 2.0 | 0.83 | 0.84 | 0.40 |
| iron | n_frames | 4 | 0.19 | 2.0 | 0.77 | 0.78 | 0.33 |
| iron | n_frames | 6 | 0.17 | 2.0 | 0.64 | 0.61 | 0.30 |
| iron | n_frames | 8 | 0.19 | 2.0 | 0.68 | 0.62 | 0.31 |
| iron | dt_ms | 1 | 1.00 | 4.0 | 0.76 | 0.84 | 0.49 |
| iron | dt_ms | 2 | 1.00 | 3.0 | 0.73 | 0.84 | 0.45 |
| iron | dt_ms | 4.2 | 0.19 | 2.0 | 0.74 | 0.74 | 0.41 |
| iron | dt_ms | 8 | 0.01 | 1.0 | 0.19 | 2.52 | 2.51 |
| iron | n_dots | 12 | 0.03 | 0.0 | 0.91 | 1.24 | 0.50 |
| iron | n_dots | 20 | 0.15 | 2.0 | 0.64 | 0.93 | 0.37 |
| iron | n_dots | 27 | 0.21 | 2.0 | 0.83 | 0.64 | 0.36 |
| iron | n_dots | 40 | 0.20 | 2.0 | 0.62 | 0.64 | 0.34 |
| iron | sigma_dot_px | 0.2 | 0.18 | 2.0 | 0.69 | 0.60 | 0.26 |
| iron | sigma_dot_px | 0.5 | 0.19 | 2.0 | 0.77 | 0.75 | 0.36 |
| iron | sigma_dot_px | 1 | 0.20 | 2.0 | 0.92 | 1.02 | 0.62 |
| iron | sigma_center_px | 0.3 | 0.19 | 2.0 | 0.51 | 0.52 | 0.24 |
| iron | sigma_center_px | 1 | 0.16 | 2.0 | 1.26 | 1.52 | 0.61 |
| iron | sigma_center_px | 2 | 0.20 | 2.0 | 2.79 | 2.63 | 1.15 |
| iron | p_misid | 0 | 0.18 | 2.0 | 0.66 | 0.81 | 0.30 |
| iron | p_misid | 0.01 | 0.16 | 2.0 | 0.73 | 0.74 | 0.31 |
| iron | p_misid | 0.02 | 0.18 | 2.0 | 0.68 | 0.84 | 0.36 |
| iron | p_misid | 0.05 | 0.18 | 2.0 | 0.90 | 0.75 | 0.33 |
| iron | beta | 0.15 | 0.20 | 2.0 | 0.82 | 0.82 | 0.32 |
| iron | beta | 0.25 | 0.18 | 2.0 | 0.66 | 0.73 | 0.37 |
| iron | beta | 0.4 | 0.16 | 2.0 | 0.67 | 0.76 | 0.31 |
| iron | ball_px | 60 | 0.17 | 2.0 | 0.90 | 1.02 | 0.52 |
| iron | ball_px | 100 | 0.20 | 2.0 | 0.66 | 0.76 | 0.38 |
| iron | ball_px | 150 | 0.22 | 2.0 | 0.50 | 0.47 | 0.23 |
| iron | ball_px | 250 | 0.20 | 2.0 | 0.29 | 0.34 | 0.12 |
| iron | vantage | behind | 0.19 | 2.0 | 0.72 | 0.74 | 0.30 |
| iron | vantage | quarter-20 | 0.19 | 2.0 | 0.69 | 0.77 | 0.31 |
| iron | vantage | quarter-40 | 0.18 | 2.0 | 0.54 | 0.79 | 0.42 |
| iron | mode | mono | 0.15 | 2.0 | 0.71 | 0.76 | 0.34 |
| iron | mode | stereo | 0.19 | 2.0 | 0.43 | 0.53 | 0.30 |
| wedge | combined | baseline | 1.00 | 2.0 | 0.29 | 0.75 | 0.38 |
| wedge | n_frames | 2 | 1.00 | 2.0 | 0.37 | 0.86 | 0.41 |
| wedge | n_frames | 3 | 1.00 | 2.0 | 0.27 | 0.76 | 0.36 |
| wedge | n_frames | 4 | 1.00 | 2.0 | 0.29 | 0.75 | 0.36 |
| wedge | n_frames | 6 | 1.00 | 2.0 | 0.33 | 0.74 | 0.36 |
| wedge | n_frames | 8 | 1.00 | 2.0 | 0.30 | 0.75 | 0.37 |
| wedge | dt_ms | 1 | 1.00 | 4.0 | 0.51 | 0.64 | 0.35 |
| wedge | dt_ms | 2 | 1.00 | 4.0 | 0.26 | 0.55 | 0.24 |
| wedge | dt_ms | 4.2 | 1.00 | 2.0 | 0.31 | 0.76 | 0.35 |
| wedge | dt_ms | 8 | 0.00 | 1.0 | nan | nan | nan |
| wedge | n_dots | 12 | 0.13 | 1.0 | 0.47 | 0.80 | 0.51 |
| wedge | n_dots | 20 | 0.99 | 2.0 | 0.35 | 0.83 | 0.42 |
| wedge | n_dots | 27 | 1.00 | 2.0 | 0.31 | 0.76 | 0.40 |
| wedge | n_dots | 40 | 1.00 | 2.0 | 0.25 | 0.76 | 0.32 |
| wedge | sigma_dot_px | 0.2 | 1.00 | 2.0 | 0.28 | 0.72 | 0.33 |
| wedge | sigma_dot_px | 0.5 | 1.00 | 2.0 | 0.29 | 0.78 | 0.37 |
| wedge | sigma_dot_px | 1 | 1.00 | 2.0 | 0.37 | 0.94 | 0.49 |
| wedge | sigma_center_px | 0.3 | 1.00 | 2.0 | 0.20 | 0.57 | 0.24 |
| wedge | sigma_center_px | 1 | 1.00 | 2.0 | 0.56 | 1.45 | 0.69 |
| wedge | sigma_center_px | 2 | 1.00 | 2.0 | 1.07 | 2.64 | 1.25 |
| wedge | p_misid | 0 | 1.00 | 2.0 | 0.27 | 0.74 | 0.35 |
| wedge | p_misid | 0.01 | 1.00 | 2.0 | 0.29 | 0.77 | 0.37 |
| wedge | p_misid | 0.02 | 1.00 | 2.0 | 0.32 | 0.82 | 0.41 |
| wedge | p_misid | 0.05 | 1.00 | 2.0 | 0.32 | 0.84 | 0.39 |
| wedge | beta | 0.15 | 1.00 | 2.0 | 0.31 | 0.84 | 0.41 |
| wedge | beta | 0.25 | 1.00 | 2.0 | 0.32 | 0.78 | 0.39 |
| wedge | beta | 0.4 | 1.00 | 2.0 | 0.29 | 0.73 | 0.36 |
| wedge | ball_px | 60 | 1.00 | 2.0 | 0.47 | 1.36 | 0.72 |
| wedge | ball_px | 100 | 1.00 | 2.0 | 0.29 | 0.82 | 0.38 |
| wedge | ball_px | 150 | 1.00 | 2.0 | 0.18 | 0.51 | 0.23 |
| wedge | ball_px | 250 | 1.00 | 2.0 | 0.12 | 0.31 | 0.16 |
| wedge | vantage | behind | 1.00 | 2.0 | 0.32 | 0.82 | 0.36 |
| wedge | vantage | quarter-20 | 1.00 | 2.0 | 0.28 | 0.75 | 0.37 |
| wedge | vantage | quarter-40 | 1.00 | 2.0 | 0.38 | 0.83 | 0.45 |
| wedge | mode | mono | 1.00 | 2.0 | 0.26 | 0.80 | 0.35 |
| wedge | mode | stereo | 1.00 | 2.0 | 0.20 | 0.55 | 0.26 |
