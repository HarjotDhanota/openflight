# Offline head-outline experiment

This folder is research code based on `80355096`. No application module imports it.
It compares the existing impact outline path with hosel-constrained component
selection, using the existing shaft tracker, core/area thresholds and two
background windows. It excludes exposures extending past the earliest declared
possible contact, rejects ambiguous anchors, cropped heads and ball overlap, and
never emits a shot metric. Timestamp semantics and acoustic delay remain unqualified.

The original four clips are a development set, already inspected before this
experiment. They provide no independent launch or numerical strike truth. The
experiment recovers zero gated head masks; it must not be wired into live impact
analysis on the strength of these results. A thin, connected contour detector needs
separate evidence; changing the inscribed-core radius alone is not supported here.

From this checkout:

```powershell
uv sync --extra camera
uv run --extra camera pytest research/tracking-improvement-2026-10-27 -q
uv run --extra camera python research/tracking-improvement-2026-10-27/replay.py --package ../research/session-review-2026-10-27-01 --out ../research/tracking-improvement-2026-10-27/reproduced
```

The tests need no private field data. Replay requires the original local package
and validates its archive hash and all manifest members before estimating. Paths
come from the manifest and session associations. Photos are hashed for integrity;
their image pixels do not enter detection, selection or estimation.

Outputs include all 236 baseline frame records, per-frame prototype reasons,
normal-route overlays for four shots, constrained-contour overlays for the two
range-bearing shots and source hashes. Orange outlines are unaccepted moving
components; magenta circles are motion references; cyan circles are detected ball
references; green regions would be gated candidates. No green region is recovered.
Overlay resizing is for inspection only. Geometry uses native saved pixels.

Detailed local evidence, public-source research, integration risks and validation
protocol live in `../../research/tracking-improvement-2026-10-27/REPORT.txt` from
the checkout root. Recordings, photographs and generated overlays stay outside Git.
