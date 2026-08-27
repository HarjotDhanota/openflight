# Falsification harness

Scripts that test specific claims about impact location against the 21-shot
capture session, rather than against simulation. Each one answers a question and
prints its evidence; none of them is a pytest test despite the `test_` prefix,
which is historical. Run them directly.

The narrative write-ups are the place to start, not the code:

| document | what it covers |
|---|---|
| `docs/superpowers/specs/2026-08-26-codex-handoff.md` | **start here** — current state, seven retracted claims, ranked open problems |
| `docs/superpowers/specs/2026-08-26-falsification-results.md` | falsification tests 1, 2, 5, 9 and the mesh-frame investigation |
| `docs/superpowers/specs/2026-08-26-codex-followup-results.md` | tests 3, 4, 6–8, 10, 11 and the resolution-scaling refutation |

## Setup

You need two things that are **not** in git.

**1. The capture session** (~200 MB). Ask the maintainer for
`openflight_session_20260825_181734_filtered`, then either set the environment
variable or let the scripts find it in `~/Downloads`:

```bash
export OPENFLIGHT_SESSION="/path/to/openflight_session_20260825_181734_filtered"
```
```powershell
$env:OPENFLIGHT_SESSION = "C:\path\to\openflight_session_20260825_181734_filtered"
```

Any export with the same layout works — `shots.csv` plus `shots/<shot>/frames.npz`.
Resolution order and the search paths are in `session_path.py`, which fails closed
with instructions rather than a stack trace.

**2. The 7-iron mesh.** The model is admitted for local research use only and
**cannot be redistributed**, so it is not in the repository — see
`research/silhouette_poc/meshes/SOURCES.md`. Download the right-handed 690CB STL
yourself from [GrabCAD](https://grabcad.com/library/titleist-7-iron-golf-club-1),
then:

```bash
uv run python research/silhouette_poc/meshes/download_meshes.py \
    --local-iron "/path/to/690CB 7-iron.STL"
```

The importer verifies SHA-256 `f35936799295e6ce…` before parsing, so everyone
provably gets the same geometry.

Then run anything here with `uv run python <script>.py`.

## Two things worth knowing before reading any result

**Frames are mirrored.** The capture writes `image[:, ::-1]`, so a right-handed
golfer appears left-handed. Un-mirror the **frames**, never the mesh.

**`shot_001` is excluded everywhere.** It ran the old 495 µs / gain 15 settings
and is 99.8 % clipped. Every script hard-codes `EXCLUDE = {1}`.

## What each script answers

### Falsification tests
| script | question |
|---|---|
| `test1_vertical_trajectory.py` | Is the 7i/9i launch gap real, or is the radar estimator compressing it? Reconstructs ball flight from camera rays + radar range, excluding LCMF. |
| `test1a_camera_pitch.py` | Where is the camera pointing? Measures boresight pitch from the teed ball on all 21 shots. |
| `test1b_reconstruct_and_sweep.py` | Does the launch gap survive every geometric assumption? |
| `test1c_range_only.py` | Can the radar range walk alone referee the camera/radar disagreement? (No — too ill-conditioned.) |
| `test1d_offset_character.py` | Is the camera−radar offset a bias or a gain error? |
| `test2_ops_speed_contract.py` | Does LCMF receive radial speed where its model wants total speed? |
| `test2_decompose.py` | Which of the two speed contracts actually carries the error? |
| `test2_sensitivity.py` | ∂(launch angle)/∂(assumed velocity). |
| `test3_4_club_stability.py` | Jackknife and impact-time perturbation of club angles. |
| `test5_9_aoa_and_clustering.py` | Attack-angle robustness, and whether failures share a cause. |
| `test6_7_11_dplane_envelopes.py` | D-plane coefficient envelope, component models, impulse feasibility. |
| `test8_geometry_perturbation.py` | How much of the absolute launch angle is calibration? |
| `test10_same_point_consistency.py` | Do the camera and radar track the same physical point? |

### Mesh and pose
| script | question |
|---|---|
| `render_mesh_views.py` | **Run this first if the mesh confuses you.** Renders the model coloured by candidate surface. It is how the face-plane bug was found. |
| `test5q2*_*.py` | The mesh's loft/lie, and why `detect_face_plane` anchors to the cavity rim on the back of the club. |
| `test_meshfit_depth_ab.py` | Three depth treatments over identical masks — the run that showed IoU is anti-correlated with pose correctness. |
| `render_fit_overlays.py` | Draws the fitted mesh over the real frames. Found the tracker following the ball. |
| `test_dof_sensitivity.py` | How far each rotation can move before the objective notices. |
| `test_shaft_leverage.py` | Would modelling the shaft fix orientation? |
| `test_stub_and_foreshortening.py` | What the 62 mm protrusion is, and whether heel–toe foreshortening gives yaw. |
| `test_rigid_rotation_prior.py` | Fitting the swing as one rigid rotation instead of free angles per frame. |
| `test_resolution_requirement.py` | What plate scale a useful face angle would need. |
| `test_resolution_scaling_check.py` | Whether that extrapolation survives real segmented edges. **It does not.** |
| `test_area_excess_sources.py` | Decomposes the observed-vs-model area mismatch. |

### Support
| file | role |
|---|---|
| `session_path.py` | Finds the capture export; fails closed with guidance. |
| `flight_track.py` | Ball detection and in-flight tracking. |
| `build_sec11*.py` | Build HTML fragments for the public write-up. Output goes to `page/`, or `OPENFLIGHT_PAGE_OUT`. |

## Outputs

`*.json` results are committed — they let you re-analyse a run without repeating
a fit, which costs about 25 minutes per arm. Renders, `.npy` and logs are
gitignored; every script regenerates them.

## A standing convention, learned the hard way

**Render it and look before trusting a number.** Two published claims about the
clubface were wrong, and a tracker silently followed the *ball* after impact —
where the contaminated frames scored the *highest* IoU and the *best* coherence,
flattering every metric in use. No number gave a hint. One image did each time.
