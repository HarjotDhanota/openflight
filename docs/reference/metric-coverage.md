---
icon: lucide/table
---

# Metric coverage

Every golf metric OpenFlight reports, and every place the owner looks for it.
Each metric is one record built by `src/openflight/shot_metrics.py` from the
shot's own fields and the Part E producers (E1 `club_arc`, E2
`collision_model`, E3 `full_speed`, E4 `flight`). Every place below reads that
same record, so a metric carries the same value and the same label everywhere.

## The record

Every metric has the same keys: `value`, `unit`, `status`, `source`, `range`,
`reason`, `frame`, `note` and `normalized` (flight metrics only).

`status` is one of five labels:

| Label | Means | Kiosk badge |
|---|---|---|
| `measured` | A sensor saw it | plain outline, "MEAS" |
| `calculated` | A named model on measured or calculated inputs (D38) | accent outline, "CALC" |
| `model_geometry` | Rests on an assumed swing arc (D39) | dashed accent outline, "MODEL" |
| `estimated` | An OPS-only install's labelled stand-in | filled accent with the ≈ mark, "EST" |
| `not_measured` | No value; `reason` says why (D17) | no badge: the reason is shown |

A simulated carry is `calculated` whether its spin was measured or calculated.
An older log's `measured` carry is read as `calculated`.

## Places

| Code | Place | Where in the code | Test |
|---|---|---|---|
| S | `Shot.to_dict()["metrics"]` | `launch_monitor.py`, `shot_metrics.py` | `test_shot_metrics.py`, `test_metric_coverage.py` |
| P | Kiosk payload: socket `shot` / `session_state` (via `shot_to_dict`) and `GET /api/shots/latest` | `server.py` | `test_metric_coverage.py` |
| K | Kiosk screen | Live tiles (`liveMetrics.ts`); Club and Flight tabs (`MetricGroupPanel.tsx`) | vitest `MetricGroupPanel.test.tsx`, `liveMetrics.test.ts`; Playwright `metric-panels.spec.ts` |
| T | Tester page, beside each swing's picture verdict | `ui/public/tester.html` (`#kiosk-shot`) | Playwright `tester-ladder.spec.ts` |
| L | Session log `shot_detected` / `shot_updated` lines | `session_logger.py` (writes `Shot.to_dict()`) | `test_metric_coverage.py` |
| R | Session review: `session_review.json` `kiosk_metrics`, `report.md`, `attempts.csv` (`kiosk.<id>`), `session-review.html` | `session_review.py`, `ui/public/session-review.html` | `test_metric_coverage.py`, Playwright `session-review.spec.ts` |
| B | Session bundle (session logs and the stored review, copied verbatim) | `session_bundle.py` | `test_metric_coverage.py` |
| C | `pipeline_check` stage table (`kiosk:<id>` rows) | `pipeline_check.py`, `scripts/analysis/check_pipeline.py` | `test_pipeline_check_kiosk_metrics.py`, `test_metric_coverage.py` |
| X | Simulators (OpenConnect) | `sim/resolver.py` | `test_sim_resolver.py`, `test_collision_model_geometry.py` |

The review replay metrics (`review_metrics.py`: accepted, experimental, ...)
are a separate layer: they re-run the estimators. The kiosk metrics above are
what the kiosk reported, under the kiosk's labels.

## Matrix

✓ means present with its label. "Live" and "Club" / "Flight" name the kiosk
screens. "key" on the tester page is the one-line summary under the swing
verdict; every metric is in its "All metrics" table. For simulators, "sent"
follows D31: a fusion shot goes only with measured or calculated speed,
launch, spin and spin axis, and model geometry only with
`SIMULATORS_ACCEPT_MODEL_GEOMETRY`; "n/a" means OpenConnect has no field for it.

| Metric | Id | S | P | K | T | L | R | B | C | X |
|---|---|---|---|---|---|---|---|---|---|---|
| Ball speed (radial) | `ball_speed` | ✓ | ✓ | Live, Flight | key, all | ✓ | ✓ | ✓ | ✓ | sent when no full speed |
| Ball speed (full) | `ball_speed_full` | ✓ | ✓ | Flight | key, all | ✓ | ✓ | ✓ | ✓ | sent when calculated |
| Vertical launch | `launch_vertical` | ✓ | ✓ | Live, Flight | key, all | ✓ | ✓ | ✓ | ✓ | sent |
| Horizontal launch | `launch_horizontal` | ✓ | ✓ | Live, Flight | key, all | ✓ | ✓ | ✓ | ✓ | sent |
| Spin rate | `spin_rate` | ✓ | ✓ | Live, Flight | key, all | ✓ | ✓ | ✓ | ✓ | sent (D31) |
| Spin axis | `spin_axis` | ✓ | ✓ | Live, Flight | key, all | ✓ | ✓ | ✓ | ✓ | sent (D31) |
| Gyro | `gyro` | ✓ | ✓ | Flight | all | ✓ | ✓ | ✓ | ✓ (PENDING) | n/a |
| Smash (radial) | `smash_factor` | ✓ | ✓ | Live, Flight | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Smash (full) | `smash_factor_full` | ✓ | ✓ | Flight | all | ✓ | ✓ | ✓ | ✓ (PENDING on club speed) | n/a |
| Carry | `carry` | ✓ | ✓ | Live, Flight | key, all | ✓ | ✓ | ✓ | ✓ | sent (D31) |
| Carry side | `carry_side` | ✓ | ✓ | Flight | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Total | `total` | ✓ | ✓ | Flight | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Total side | `total_side` | ✓ | ✓ | Flight | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Apex | `apex` | ✓ | ✓ | Flight | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Landing angle | `landing_angle` | ✓ | ✓ | Flight | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Hang time | `hang_time` | ✓ | ✓ | Flight | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Curve | `curve` | ✓ | ✓ | Flight | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Club speed (radial) | `club_speed` | ✓ | ✓ | Live, Club | key, all | ✓ | ✓ | ✓ | ✓ | sent |
| Club speed (full) | `club_speed_full` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ (PENDING W6, W9) | n/a |
| Club path | `club_path` | ✓ | ✓ | Live, Club | key, all | ✓ | ✓ | ✓ | ✓ | sent when measured; model geometry behind the switch |
| Attack angle | `attack_angle` | ✓ | ✓ | Live, Club | key, all | ✓ | ✓ | ✓ | ✓ | n/a |
| Face angle | `face_angle` | ✓ | ✓ | Club | key, all | ✓ | ✓ | ✓ | ✓ | n/a |
| Face to path | `face_to_path` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Dynamic loft | `dynamic_loft` | ✓ | ✓ | Club | key, all | ✓ | ✓ | ✓ | ✓ | n/a |
| Spin loft | `spin_loft` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| D-plane tilt | `d_plane_tilt` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Swing plane | `swing_plane` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Swing direction | `swing_direction` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Low point distance | `low_point_distance` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Low point height | `low_point_height` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Low point side | `low_point_side` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Impact offset | `impact_offset` | ✓ not measured (E5) | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ (PENDING E5) | n/a |
| Impact height | `impact_height` | ✓ not measured (E5) | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ (PENDING E5) | n/a |
| Dynamic lie | `dynamic_lie` | ✓ not measured (E6) | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ (PENDING E6) | n/a |

## pipeline_check causes for a kiosk metric

- **pass:** measured. **labelled:** calculated, model geometry or estimated (the value is shown, with its label).
- **PENDING:** not built yet (impact location E5, dynamic lie E6), or not measured for a named unbuilt dependency: the full club speed (W6, W9), the full smash on it, and gyro (the planar E2 model).
- **NOT REACHED:** every stage the metric stands on failed; it inherits that stage's cause.
- **DATA:** not measured with its stages passing: the shot's data did not support it, and the reason says why.
- **CODE:** the record breaks the label contract: an unknown label, a value under `not_measured`, no value under a label, not measured without a reason, or a metric missing from the shot.

## Not in scope

- The TV mirror (`/display`, `DisplayMode.tsx`) and the BLE phone protocol
  (`ble/protocol.py`) keep their own fixed field lists; neither is a place the
  owner reads during the Pi test.

## Regenerating the UI catalog

`ui/src/data/metricCatalog.json` is generated from the server's list:

```bash
uv run python -m openflight.shot_metrics > ui/src/data/metricCatalog.json
```

`tests/test_metric_coverage.py` fails when the two differ.
