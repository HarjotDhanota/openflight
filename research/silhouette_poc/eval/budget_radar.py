"""Phase 1 eval: the 0C accuracy budget re-run at the real OpenFlight camera/radar.

Extends `club_pose.sim.budget.run_budget` over the pre-registered Appendix-B grid
(spec `docs/superpowers/specs/2026-08-22-silhouette-poc-design.md`):

  presets {A@0.656, A@1.31, B@1.33 px/mm}
  x sync {I/Q 33 us, frame-quantized 2.14 ms}
  x depth {stereo-3mm reference (stereo mode), radar 3 mm + bias 0/10/20/40 mm (mono)}
  x exposure {10 us (strobed), 500 us (as shipped)}
  x clubs {driver, iron}

Marker-keypoint pose fitting is an OPTIMISTIC proxy for silhouette fitting, so a
FAILING cell here fails for the real system too; a passing cell is necessary,
not sufficient.
"""

from __future__ import annotations

import math

import numpy as np
from club_pose.sim.budget import _CLUB_SPEED_MM_S, _cell_summary, run_budget
from club_pose.sim.camera import IMPACT_TARGET, IMX296, mono_rig

# px/mm at the impact target for the unscaled 0C rig (fx / range-to-target).
BASELINE_PX_PER_MM = IMX296.fx / float(np.linalg.norm(mono_rig().center_world - IMPACT_TARGET))

# Camera presets: candidate plate scales at the tee (spec section 2).
PRESETS = (0.656, 1.31, 1.33)

# Sync cells. Frame quantization is uniform over one 468 fps frame period;
# the gaussian-jitter model uses sigma = T / sqrt(12) = 2137 us / 3.464.
SYNC_CELLS = (
    ("iq_33us", 33.0),
    ("frame_2.14ms", 2137.0 / math.sqrt(12.0)),
)

# Depth resolver cells: stereo reference vs mono + radar range sphere with a
# systematic clubhead phase-center bias (never assumed zero).
DEPTH_CELLS = (
    ("stereo_ref_3mm", "stereo", 3.0, 0.0),
    ("radar_bias_0mm", "mono", 3.0, 0.0),
    ("radar_bias_10mm", "mono", 3.0, 10.0),
    ("radar_bias_20mm", "mono", 3.0, 20.0),
    ("radar_bias_40mm", "mono", 3.0, 40.0),
)

EXPOSURES_US = (10.0, 500.0)
CLUBS = ("driver", "iron")

# 0C noise/calibration baselines carried unchanged (RESULTS_0C.md).
BASELINE = dict(
    sigma_c=0.5,
    delta_bias=0.0,
    sigma_cal=0.5,
    vel_err_frac=0.03,
    baseline_mm=150.0,
)


def camera_scale_for(px_per_mm: float) -> float:
    """Intrinsics scale factor that puts the given plate scale at the target."""
    return float(px_per_mm) / BASELINE_PX_PER_MM


def blur_sigma_px(speed_mm_s: float, exposure_us: float, px_per_mm: float) -> float:
    """Centroid noise equivalent of a uniform motion smear over the exposure."""
    smear_px = float(speed_mm_s) * float(exposure_us) * 1e-6 * float(px_per_mm)
    return smear_px / math.sqrt(12.0)


def build_grid(n: int, seed: int) -> list[dict]:
    """The pre-registered Appendix-B cell specs (no execution)."""
    cells = []
    for px_per_mm in PRESETS:
        for sync_label, sync_us in SYNC_CELLS:
            for depth_label, mode, depth_sigma, depth_bias in DEPTH_CELLS:
                for exposure_us in EXPOSURES_US:
                    for club in CLUBS:
                        cells.append(
                            {
                                "club": club,
                                "mode": mode,
                                "preset_px_per_mm": px_per_mm,
                                "sync_label": sync_label,
                                "sync_jitter_us": sync_us,
                                "depth_label": depth_label,
                                "ball_depth_sigma": depth_sigma,
                                "ball_depth_bias_mm": depth_bias,
                                "exposure_us": exposure_us,
                                "n": int(n),
                                "seed": int(seed),
                            }
                        )
    return cells


def run_cell(spec: dict) -> dict:
    """Execute one grid cell and return its summary merged with the labels."""
    px_per_mm = float(spec["preset_px_per_mm"])
    result = run_budget(
        spec["club"],
        spec["mode"],
        BASELINE["sigma_c"],
        BASELINE["delta_bias"],
        BASELINE["sigma_cal"],
        spec["ball_depth_sigma"],
        spec["sync_jitter_us"],
        BASELINE["vel_err_frac"],
        BASELINE["baseline_mm"],
        spec["n"],
        spec["seed"],
        camera_scale=camera_scale_for(px_per_mm),
        ball_depth_bias_mm=spec["ball_depth_bias_mm"],
        blur_sigma_px=blur_sigma_px(_CLUB_SPEED_MM_S, spec["exposure_us"], px_per_mm),
    )
    summary = _cell_summary(result)
    summary.update(
        {
            "preset_px_per_mm": px_per_mm,
            "sync_label": spec["sync_label"],
            "depth_label": spec["depth_label"],
            "exposure_us": float(spec["exposure_us"]),
        }
    )
    return summary
