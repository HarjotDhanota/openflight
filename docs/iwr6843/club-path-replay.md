# Radar club-path replay

## 2026-08-28 four-arm result

Session `20260825_181734` was replayed from all 22 archived `.l3dump` files
using the geometry and phase configuration recorded in its `session_start`
event. The live runtime's −2 ms club-impact correction and each row's recorded
window policy and TDM sign were preserved. This was analysis-only: no
production path was changed.

The A0 control is declared **PASSED** with the documented residual: production status and main-pipeline fields reproduce on 21/22 shots (shot 28: one snapshot), and the remaining differences are confined to the debug-only experimental_path_candidate fields on shots 11/16/29.

Three causal hypotheses were tested and rejected: median refactor, gate-edge
arithmetic, and kept-count. The shot 28 one-snapshot residual remains a
documented control observation; none of those three hypotheses explains it.

The replay CSV has 110 rows: A0, A1, both A2 velocity variants, and A3 for each
of 22 shots. "Spread" below gives IQR followed by the full observed range.
A1 and A2 retain A0's tee-anchored attack result; A3 retains A0's horizontal
result. The horizontal residual column is the main `fit_residual_deg`, not the
debug-only candidate residual.

| Arm | `phase_span_rad`, median; IQR; range | Main fit residual (°), median; IQR; range | Candidate path (°), median; IQR; range | Candidate attack (°), median; IQR; range | Main status |
|---|---|---|---|---|---|
| A0 — current code | 3.335; 0.368; 2.181…3.909 | 24.954; 13.433; 8.261…41.050 | 21.742; 21.938; −8.587…37.080 | −30.590; 4.118; −37.317…−25.293 | 22/22 `rejected_phase_span` |
| A1 — window static removal | 3.429; 0.346; 2.643…3.901 | 20.513; 15.541; 9.176…42.336 | 19.272; 17.034; −8.197…44.855 | −30.590; 4.118; −37.317…−25.293 | 22/22 `rejected_phase_span` |
| A2 — linear `track.speed_ms` | 3.429; 0.346; 2.643…3.863 | 20.513; 15.541; 9.176…42.336 | 19.272; 17.179; −8.197…44.855 | −30.590; 4.118; −37.317…−25.293 | 22/22 `rejected_phase_span` |
| A2 — OPS-anchored radial | 3.429; 0.346; 2.643…3.863 | 20.513; 15.541; 9.176…42.336 | 19.272; 17.179; −8.197…44.855 | −30.590; 4.118; −37.317…−25.293 | 22/22 `rejected_phase_span` |
| A3 — free attack fit | 3.335; 0.368; 2.181…3.909 | 24.954; 13.433; 8.261…41.050 | 21.742; 21.938; −8.587…37.080 | −43.346; 3.477; −51.176…−36.210 | 22/22 `rejected_phase_span` |

### Pre-registered reads

**A1 versus A0 — phase span.** The rendered phase sheet shows every A1 point
well above the 1.571 rad gate; the A1 range is 2.643…3.901 rad. The paired A1
minus A0 change has median +0.117 rad, IQR −0.020…+0.289 rad, and range
−0.382…+1.013 rad. Window-scope subtraction therefore does not move the phase
span toward the expected ~1.3 rad ceiling on most shots. The per-burst MTI
notch is not the dominant cause in these replays. Main fit residual also
remains far above its 0.5° gate: median 20.513°.

**A2 versus A1 — de-rotation velocity.** The linear and OPS sources are
identical here because `track_speed_ratio` is defined from the same OPS speed
and `track.speed_ms`; both therefore recover the linear track speed. Their
paired phase-span change has median 0.000 rad and range −0.037…0.000 rad. Main
residual changes span −0.133…0.000°, and candidate-path changes span
0.000…+0.731° with median 0.000°. All angle changes are below the control's
1.7° noise floor. The distributions do not support quadratic-refit drift as a
material contributor.

**A3 versus A0 — attack angle.** The rendered attack sheet shows A3 below A0
on every shot. The paired change has median −11.372°, IQR −14.007…−8.723°,
and range −23.140…−2.852°, so all 22 changes exceed the 1.7° control noise
floor. A3's median is −43.346° and 0/22 candidates lie in the fused
−2.6…−6.6° band. Removing the ball-height tee anchor does leave the original
−25…−37° cluster, but in the wrong direction; the anchor alone does not
explain the physically implausible attack candidates.

**Accepted radar path versus camera fusion.** There are no accepted radar-only
paths in any arm, so the pre-registered paired-difference distribution is not
available. Reporting debug-only `candidate_path_deg` against fusion would not
answer this check. Neither radar nor camera fusion is treated as truth.

### Plot inspection and verdicts

The phase plot visibly keeps all arm traces above the gate. A1 and both A2
traces mostly overlap, with local changes but no session-wide collapse. The
attack plot keeps A0/A1/A2 in the original negative cluster while A3 forms a
separate, still more-negative −36…−51° trace. The fused overlay has two visible
outliers (shots 20 and 25); 18/21 recorded fused values occupy the stated
−2.6…−6.6° session band. Those outliers do not change the registered A3 read,
which is against the band and has 0/22 inside it.

**Per-burst MTI verdict:** window-scope removal does not rescue phase span;
the notch is not dominant. **Tee-height anchor verdict:** a free fit makes the
attack candidates more negative, so the ball-height anchor is not the sole
cause and this replay does not justify a production change.

Artifacts were written outside the repository to
`C:\Users\harjo\Downloads\club_path_replay_20260828`: `club_path_replay.csv`,
`phase_span_by_arm.png`, and `attack_angle_by_arm.png`.
