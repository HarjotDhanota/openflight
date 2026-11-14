"""Print the application's own per-frame edge diagnostics at this pin (toe, sole, shaft, topline) for the report."""
import json
from common import AUDIT
for shot in (1, 3, 4):
    s = json.loads((AUDIT / "results" / ("shot%d" % shot) / "placement.json").read_text())
    for f, fr in s["frames"].items():
        e = fr["edges"]
        print(shot, f, {k: {kk: vv for kk, vv in v.items() if kk in ("samples", "accepted", "median_residual_mm", "median_residual_px", "rows_kept", "weight", "fitted", "carried_from_frame")} for k, v in e.items()})
