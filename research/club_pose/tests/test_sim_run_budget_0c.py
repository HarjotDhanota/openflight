import json
import subprocess
import sys
from pathlib import Path


def test_run_budget_0c_script_is_self_locating_and_emits_tornado_grid():
    root = Path(__file__).resolve().parents[3]
    script = root / "research" / "club_pose" / "sim" / "run_budget_0c.py"

    proc = subprocess.run(
        [sys.executable, str(script), "--n", "2", "--seed", "0"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    cells = data["verdict"]["cells"]
    combos = {(c["club"], c["mode"]) for c in cells}
    axes = {c["axis"] for c in cells}

    assert combos == {
        ("driver", "mono"),
        ("driver", "stereo"),
        ("iron", "mono"),
        ("iron", "stereo"),
    }
    assert {
        "combined",
        "centroid_sigma_px",
        "correlated_bias_px",
        "calibration_mm",
        "ball_depth_mm",
        "sync_jitter_us",
        "velocity_error_frac",
    } <= axes
    assert data["baselines"]["mono_ball_depth_sigma_mm"] > data["baselines"]["stereo_ball_depth_sigma_mm"]
