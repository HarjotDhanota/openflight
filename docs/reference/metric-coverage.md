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

`status` is one of six labels:

| Label | Means | Kiosk badge |
|---|---|---|
| `measured` | A sensor saw it | plain outline, "MEAS" |
| `calculated` | A named model on measured or calculated inputs (D38) | accent outline, "CALC" |
| `model_geometry` | Rests on an assumed swing arc (D39) | dashed accent outline, "MODEL" |
| `estimated` | An OPS-only install's labelled stand-in, or a value built on a weak input (a low-coherence horizontal launch under spin, spin axis or face; the radial speed under a flight when the full speed is refused) | filled accent with the ≈ mark, "EST" |
| `experimental` | Shown to testers before it is validated, and fed to no other metric: the camera's impact location, read live after the shot | dotted outline in its own colour, "EXP", titled "not yet validated" |
| `not_measured` | No value; `reason` says why (D17) | no badge: the reason is shown |

A simulated carry is `calculated` whether its spin was measured or calculated.

`ball_range` (13 Oct) is the range the shot's range-dependent metrics used: its own
IWR launch point (`measured`, noted "not validated against a tape yet"), else the
setup's camera prior (`estimated`), else `not_measured` with the reason. When the
camera prior stood in, every metric that rests on the range (all but the OPS243's
radial speeds, radial smash and spin rate) is `estimated` and its note says so.
An older log's `measured` carry is read as `calculated`.

## Face, loft, spin axis and curve

Details and sources are in `docs/how-it-works/collision-model.md`.

| Metric | Built from | Label |
|---|---|---|
| Face angle, dynamic loft | With an accepted measured spin and the full ball speed: the rigid-body relation, ball speed · sin θ = (I / m r) · spin with I / m r = 7.50 mm (Cross & Nathan 2007; Penner 2001). Otherwise the inversion of the fitted launch model (`collision_forward`), noted "rigid-body relation not used" with the reason; without a vertical launch or attack the face alone from the Wood et al. 2018 horizontal ratio. The model's own spin never feeds them | `calculated`, or the weakest input's label |
| Spin loft, face to path, D-plane tilt | The exact 3-D definitions (`metric_definitions`) on that face and loft | the same |
| Spin axis | A measured axis wins. Irons and wedges: tan(axis) = tan(tilt) · (0.81385 + 0.00342 · dynamic loft), a clean-room fit to the irons of a TM4 normalized export, 6 May 2026. Drivers, woods, hybrids: the fitted collision model's centre-strike axis. A gear term from the fitted model only with a measured impact location (none yet, noted; the experimental live one is never used) | `calculated (model: …)` for irons |
| Spin rate (calculated) | The impulse spin of `collision_forward`: the smaller of the rolling (grip) and sliding (slip) spin of a rigid ball, Penner 2001 and Cross 2002, from V sin(spin loft) and V cos(spin loft) only. Published ball, face friction (0.40) and restitution; typical head mass, centre-of-mass depth and inertia (assumed); one spin gain per category fitted by the maintainer. Centre strike unless an impact location is measured (the experimental live one never is), which adds the effective-mass loss and the linear Cross & Nathan 2007 gear effect (gains fitted by the maintainer, capped at 1.5 × rigid body). An iron or wedge with attack ≥ −2°, a strike above +8 mm or a camera club delivery with no low point carries a low-confidence note (possible flier or heavy contact); the value is unchanged | `calculated`, or the weakest input's label |
| Spin index, smash index | Measured spin over the expected spin `V · k · sin(spin loft)`, and ball speed (full) over the expected ball speed `V · (a · cos(spin loft) + c)`, in percent; k, a and c fitted by the maintainer to launch-monitor data for the driver, 7-iron and pitching wedge. Other clubs take their category's k and the published normal-impulse speed times a category gain, noted "no fitted index for this club". The spin index needs a measured spin | `calculated`, or the weakest input's label |
| Low point distance | The arc's geometry plus a per-club offset for the 7-iron, driver and PW, fitted by the maintainer to a TM4 normalized export, 6 May 2026; other clubs the geometry alone, noted | the arc's label |
| Curve | TrackMan's definition: the landing point's distance from the launch-direction line, with carry as the straight-line distance (`metric_definitions.curve`). Matches that export to 1e-9 ft | the flight's label |

## Impact location (experimental, live)

`impact_offset` (+ toe) and `impact_height` (+ up from the face centre) come from
`Shot.impact_location`. `camera/impact_live.py` leaves it there after the shot, and
the shot goes out again as an update. An accepted reading is `experimental` with its
1-sigma `range` and the note "not validated yet: compare it with the face spray
marks". Otherwise it is `not_measured` with the reason, for example "building the
head outline: 3 of 5 swings with this club", "no radar distance to the ball on this
shot", or "dropped: the next shot arrived before this one was read". A shot never
read says it needs its camera clip.

It feeds no other metric: no gear effect, spin, spin axis, face or flight
(`tests/test_impact_isolation.py`). How it is read: `docs/camera/impact-location.md`.

## Places

| Code | Place | Where in the code | Test |
|---|---|---|---|
| S | `Shot.to_dict()["metrics"]` | `launch_monitor.py`, `shot_metrics.py` | `test_shot_metrics.py`, `test_metric_coverage.py` |
| P | Kiosk payload: socket `shot` / `session_state` (via `shot_to_dict`) and `GET /api/shots/latest` | `server.py` | `test_metric_coverage.py` |
| K | Kiosk screen | Live tiles (`liveMetrics.ts`); Club and Flight tabs (`MetricGroupPanel.tsx`); the Club tab's face-impact view (`FaceImpactView.tsx`) | vitest `MetricGroupPanel.test.tsx`, `FaceImpactView.test.tsx`, `liveMetrics.test.ts`; Playwright `metric-panels.spec.ts`, `face-impact.spec.ts` |
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
| Ball distance | `ball_range` | ✓ | ✓ | Club, Flight | key, all | ✓ | ✓ | ✓ | ✓ | n/a |
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
| Spin index | `spin_index` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Smash index | `smash_index` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| D-plane tilt | `d_plane_tilt` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Swing plane | `swing_plane` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Swing direction | `swing_direction` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Low point distance | `low_point_distance` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Low point height | `low_point_height` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Low point side | `low_point_side` | ✓ | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Impact offset | `impact_offset` | ✓ experimental, after the shot | ✓ | Club, face view | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Impact height | `impact_height` | ✓ experimental, after the shot | ✓ | Club, face view | all | ✓ | ✓ | ✓ | ✓ | n/a |
| Dynamic lie | `dynamic_lie` | ✓ not measured (E6) | ✓ | Club | all | ✓ | ✓ | ✓ | ✓ (PENDING E6) | n/a |

## pipeline_check causes for a kiosk metric

- **pass:** measured. **labelled:** calculated, model geometry, estimated or experimental (the value is shown, with its label).
- **PENDING:** not built yet (dynamic lie E6), or not measured for a named unbuilt dependency: the full club speed (W6, W9), the full smash on it, and gyro (the planar E2 model).
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
