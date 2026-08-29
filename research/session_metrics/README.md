# Session metrics — two things never computed on the 21-shot session

Research-only. Nothing here is production code, nothing here is destined
upstream, and **no ground truth exists for any number these scripts print**.
Every result is a CONSISTENCY result: do the sensors agree with each other and
with a golf swing? Nothing has been scored against a launch monitor.

Session: `openflight_session_20260825_181734_filtered` (22 rows; `shot_001` is
excluded everywhere, leaving 21 shots).

| Script | Question it answers |
| --- | --- |
| `dplane_metrics.py` | **A.** What dynamic loft, spin loft, face angle and face-to-path does the standard D-plane inversion give, from the session's own launch angles and its fused attack angle and club path? Are they physically plausible, and how much do they move with the Wood et al. coefficients? |
| `swing_plane.py` | **B.** What swing plane, swing direction and arc radius come out of the clubhead's 3-D track (camera centroid × radar range ramp)? Does the in-plane speed at contact match the OPS243's club speed, and does the plane's ground heading track the fused club path? |
| `RESULTS.md` | The tables, every sanity check as PASS/FAIL, and a one-line verdict per metric. |
| `results/` | The four plots, committed. Everything else (CSV, JSON, mask contact sheets) is written to `--out`. |

## Running them

```bash
S=~/Downloads/openflight_session_20260825_181734_filtered/openflight_session_20260825_181734_filtered
OUT=/tmp/session_metrics

uv run python research/session_metrics/swing_plane.py \
    --session "$S" --out "$OUT/swing" \
    --copy-plots-to research/session_metrics/results

uv run python research/session_metrics/dplane_metrics.py \
    --session "$S" --out "$OUT/dplane" \
    --copy-plots-to research/session_metrics/results \
    --track-csv "$OUT/swing/swing_plane_per_shot.csv"
```

`--out` defaults to a fresh temp directory. Run `swing_plane.py` first if you
want the `--track-csv` cross-check, which re-runs the D-plane inversion with the
attack angle and club path measured by the 3-D track instead of the shipped
fusion — that comparison is what identifies which input each failed sanity check
comes from.

Useful flags:

* `swing_plane.py --boresight raw` — use `projection._ray_world` exactly as it
  ships (lens aimed at the world origin) instead of re-aiming it onto the taped
  ball. The difference is reported in `RESULTS.md`.
* `swing_plane.py --shot N` — restrict to one shot; repeatable.
* `swing_plane.py --no-sheets` — skip the mask contact sheets.

## Outputs written to `--out`

`swing_plane.py`

* `swing_plane_per_shot.csv` — one row per shot: plane, direction, radius,
  speeds, delivery angles, jackknife spreads, and every rejected frame with its
  reason.
* `swing_plane_track_points.csv` — one row per usable frame: pixel centroid,
  radar range, world millimetres.
* `swing_plane_summary.json` — per-club medians and the sanity checks.
* `masks/<shot>_heads.png` — the contact sheet for every shot. **Look at these
  before trusting a centroid**; the sole shadow and the motion blur are visible
  in them and they are the dominant error source.

`dplane_metrics.py`

* `dplane_per_shot.csv` — every shot at all nine coefficient combinations.
* `dplane_coefficient_sensitivity.csv` — per-club medians per combination.
* `dplane_summary.json` — per-club medians, sanity checks, implied sensor
  offsets, the club-path-outlier supplement, and the track-angle variant.

## Read this before believing anything

* No ground truth. Every "PASS" means "inside a band a golf swing is expected to
  occupy", not "correct".
* Post-impact clubhead ranges assume the radar's pre-impact range rate keeps
  going. It is an assumption; it is capped at ten frames past contact and the
  per-shot CSV carries a pre-impact-only fit for comparison.
* The clubhead mask is contaminated by the sole shadow on the mat and by motion
  blur. That is a bias, not noise, and the jackknife columns bound its effect on
  orientation but do not remove it.
