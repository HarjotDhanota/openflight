import math
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from club_pose.dplane import club_params
from club_pose.sim.budget_dplane import dplane_verdict, run_dplane_budget


def _med(result, key):
    vals = [r[key] for r in result["rows"] if r["ok"] and math.isfinite(r[key])]
    return float(np.median(vals))


def test_budget_machinery_exact_when_known_coefficients_and_perfect_gear():
    res = run_dplane_budget(
        club="driver",
        gear_mode="perfect",
        sigma_launch=0.0,
        sigma_path=0.0,
        sigma_axis=0.0,
        sigma_impact=0.0,
        b_frame=0.0,
        coeff_width=0.0,
        n=80,
        seed=1,
    )

    assert _med(res, "face_launch_err_deg") < 1e-10
    assert _med(res, "face_axis_err_deg") < 1e-10
    assert _med(res, "loft_launch_err_deg") < 1e-10


def test_analytic_launch_and_path_slopes_match_coefficient_flowdown():
    params = club_params("driver")
    c = params.c_mid
    launch = run_dplane_budget("driver", "perfect", 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 1600, 2)
    path = run_dplane_budget("driver", "perfect", 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1600, 3)

    launch_slope = _med(launch, "face_launch_err_deg") / (0.5 * 0.67449)
    path_slope = _med(path, "face_launch_err_deg") / (1.0 * 0.67449)

    assert abs(launch_slope - 1.0 / c) / (1.0 / c) < 0.15
    assert abs(path_slope - (1.0 - c) / c) / ((1.0 - c) / c) < 0.15


def test_axis_route_is_less_sensitive_than_raw_axis_error_for_driver():
    sigma_axis = 5.0
    res = run_dplane_budget("driver", "perfect", 0.0, 0.0, sigma_axis, 0.0, 0.0, 0.0, 1200, 4)

    assert _med(res, "face_axis_err_deg") < 0.35 * sigma_axis


def test_frame_bias_is_one_to_one_but_cancels_face_to_path():
    b_frame = 0.75
    res = run_dplane_budget("iron", "perfect", 0.0, 0.0, 0.0, 0.0, b_frame, 0.0, 300, 5)

    assert abs(_med(res, "face_launch_err_deg") - b_frame) < 0.03
    assert abs(_med(res, "face_axis_err_deg") - b_frame) < 0.03
    assert _med(res, "ftp_launch_err_deg") < 1e-10
    assert _med(res, "ftp_axis_err_deg") < 1e-10


def test_coefficient_honesty_nonzero_and_grows_with_face_to_path():
    lo = run_dplane_budget("driver", "perfect", 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 600, 6)
    rows = [r for r in lo["rows"] if r["ok"]]
    small = [r["face_launch_err_deg"] for r in rows if abs(r["truth_ftp_deg"]) < 2.0]
    large = [r["face_launch_err_deg"] for r in rows if abs(r["truth_ftp_deg"]) > 7.0]

    assert float(np.median([r["face_launch_err_deg"] for r in rows])) > 0.0
    assert float(np.median(large)) > float(np.median(small)) * 2.0


def test_camera_and_perfect_gear_reduce_uncorrected_axis_error():
    kwargs = dict(
        club="driver",
        sigma_launch=0.0,
        sigma_path=0.0,
        sigma_axis=0.0,
        sigma_impact=3.0,
        b_frame=0.0,
        coeff_width=0.0,
        n=600,
        seed=7,
    )
    none = run_dplane_budget(gear_mode="none", **kwargs)
    camera = run_dplane_budget(gear_mode="camera", **kwargs)
    perfect = run_dplane_budget(gear_mode="perfect", **kwargs)

    assert _med(none, "face_axis_err_deg") > _med(camera, "face_axis_err_deg") * 2.0
    assert _med(camera, "face_axis_err_deg") > _med(perfect, "face_axis_err_deg")
    assert _med(perfect, "face_axis_err_deg") < 1e-10


def test_dplane_verdict_counts_attempts_and_reports_boundaries_by_gear():
    grid = [
        run_dplane_budget("driver", gear, 0.5, 1.0, 5.0, 3.0, 0.0, 0.0, 20, seed)
        for seed, gear in enumerate(("none", "camera", "perfect"), start=10)
    ]
    verdict = dplane_verdict(grid)

    assert {c["gear_mode"] for c in verdict["cells"]} == {"none", "camera", "perfect"}
    assert verdict["requirement_boundaries"]["camera"]["face_1p5"]["sigma_launch"] is not None
    assert verdict["requirement_boundaries"]["none"]["face_2p5"]["sigma_axis"] is not None
    assert all(c["ok_rate"] == 1.0 for c in verdict["cells"])


def test_run_budget_0d_script_is_self_locating_and_emits_full_grid():
    root = Path(__file__).resolve().parents[3]
    script = root / "research" / "club_pose" / "sim" / "run_budget_0d.py"

    proc = subprocess.run(
        [sys.executable, str(script), "--n", "3", "--seed", "0", "--compact"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    cells = data["verdict"]["cells"]

    assert {(c["club"], c["gear_mode"]) for c in cells} >= {
        ("driver", "none"),
        ("driver", "camera"),
        ("driver", "perfect"),
        ("iron", "none"),
        ("iron", "camera"),
        ("iron", "perfect"),
    }
    assert {"sigma_launch", "sigma_path", "sigma_axis", "b_frame", "coeff_width"} <= {
        c["axis"] for c in cells
    }
    assert "sigma_rate" not in {c["axis"] for c in cells}
    assert "camera" in data["verdict"]["requirement_boundaries"]
