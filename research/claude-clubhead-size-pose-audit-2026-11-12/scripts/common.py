"""Shared read-only loaders for this audit. Nothing here writes outside the audit folder."""
from pathlib import Path

import numpy as np

ROOT = Path("F:/Coding/OpenFlight/research/fusion-timing-replay-2026-11-05/run-002/package/sessions/harjot-outdoor-test-2/arm6/paired/run-01")
AUDIT = Path(__file__).resolve().parents[1]
# shot number -> capture folder, from the session log's camera_capture events (results/inventory.out.txt)
CAPTURES = {
    1: "camera_20261027_170649_455_001",
    2: "camera_20261027_170845_065_003",
    3: "camera_20261027_171034_000_004",
    4: "camera_20261027_171231_622_005",
}
ROI_ORIGIN = (97, 107)  # prepared-image origin in the 640x400 frame for shot 1 (ball 282.63 -> 185.63, 202.72 -> 95.72)


def frames(shot):
    z = np.load(ROOT / "arm6/camera" / CAPTURES[shot] / "frames.npz")
    return z["frames"], z["sensor_timestamp_ns"]


PRIOR = Path("F:/Coding/OpenFlight/research/claude-clubhead-pose-ml-audit-2026-11-12/results")


def prepared(shot):
    """(replay state, arrays) of the unmodified application replay of one shot: this audit's results/shotN if present,
    else the earlier audit's (application 30f90bdd; prepared pixels do not depend on the fitter change since)."""
    import json
    for base in (AUDIT / "results", PRIOR):
        folder = base / ("shot%d" % shot)
        if (folder / "placement.json").exists():
            return json.loads((folder / "placement.json").read_text()), np.load(folder / "arrays.npz")
    raise FileNotFoundError(shot)
