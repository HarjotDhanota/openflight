"""PROTOCOL E0. One run of the application's own raw replay route for one shot, with transparent in-process
wrappers that forward every argument untouched and only copy results out. No source file is edited, no setting
is overridden (the multi-frame comparison stays at its default, off). Usage: replay_capture.py <shot 1-4>"""
import contextlib
import dataclasses
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np

from openflight.camera import impact_placement as ip
from scripts.analysis import replay_raw_fusion as raw

from common import AUDIT, ROOT

APP = Path("F:/Coding/OpenFlight/openflight-integration")
shot_number = int(sys.argv[1])
out = AUDIT / "results" / ("shot%d" % shot_number)
out.mkdir(parents=True, exist_ok=True)
if (out / "RUN.json").exists():
    raise SystemExit("already run; PROTOCOL allows one run")


def plain(value):
    if isinstance(value, np.ndarray):
        return value.tolist() if value.size <= 64 else {"shape": list(value.shape), "dtype": str(value.dtype)}
    if isinstance(value, np.generic):
        return value.item()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


state = {"place_clip_calls": 0, "shafts": {}}
arrays = {}
original_place, original_shaft = ip.place_clip, ip._Placer.observed_shaft


def shaft_capture(self, index):
    found = original_shaft(self, index)
    state["shafts"][str(index)] = None if found is None else {k: plain(np.asarray(v)) for k, v in found.items()}
    return found


def placement(clip, shot, start, **kwargs):
    state["place_clip_calls"] += 1
    result = original_place(clip, shot, start, **kwargs)
    if state["place_clip_calls"] > 1:
        return result
    placed, orientation, scene = result
    state["kwargs"] = sorted(kwargs)
    state["clip"] = {k: plain(getattr(clip, k, None)) for k in (
        "shot_id", "shot_number", "fps", "mirrored", "trigger_index", "exposure_us", "frame_size", "club",
        "handedness", "club_speed_mps", "ball", "ball_source", "range_mm", "range_sigma_mm", "range_source", "error")}
    state["clip"]["face"] = plain(clip.face)
    state["clip"]["extra_dynamic_lie"] = plain(clip.extra.get("dynamic_lie"))
    state["clip"]["angle_input_decisions"] = plain(clip.extra.get("angle_input_decisions"))
    state["collision_model"] = plain(shot.get("collision_model"))
    state["placement"] = placed.as_dict()
    state["orientation"] = orientation.as_dict()
    state["scene"] = scene.as_dict()
    prepared = clip.prepared
    if prepared is not None:
        state["prepared"] = {
            "roi_origin": plain(prepared.roi_origin), "flipped": prepared.flipped, "full_size": plain(prepared.full_size),
            "camera": plain(prepared.camera), "ball": plain(prepared.ball), "contact": {
                k: plain(getattr(prepared.contact, k, None)) for k in ("contact_frame", "last_rest_frame", "source", "confirmed_by", "warning")},
            "contact_fraction": prepared.contact_fraction, "contact_fraction_source": prepared.contact_fraction_source,
            "ball_range_mm": prepared.ball_range_mm, "contact_ms": prepared.contact_ms(),
            "frame_time_ms": [prepared.frame_time_ms(i) for i in range(len(prepared.frames))],
            "window": plain(prepared.window),
        }
        arrays["prepared_frames"] = np.asarray(prepared.frames)
    state["frames"] = {}
    for index, fit in placed.frames.items():
        state["frames"][str(index)] = {
            "toe_mm": fit.toe_mm, "up_mm": fit.up_mm, "normal_mm": fit.normal_mm,
            "anchor_px": plain(fit.anchor_px), "edges": plain(fit.edges),
            "contact_point_px": plain(fit.contact_point_px), "face_centre_px": plain(fit.face_centre_px),
        }
        arrays["outline_%d" % index] = np.asarray(fit.outline_px)
        arrays["sole_%d" % index] = np.asarray(fit.sole_px)
        for part, (points, accepted) in fit.samples_px.items():
            arrays["samples_%s_%d" % (part, index)] = np.asarray(points)
            arrays["accepted_%s_%d" % (part, index)] = np.asarray(accepted)
    return result


args = [str(ROOT / "session_20261027_170621_arm6.jsonl"), str(shot_number), "--camera", "--iwr", "--full-shot",
        "--timing-coverage", "--output", str(out / "replay.json")]
head = subprocess.check_output(["git", "-C", str(APP), "rev-parse", "HEAD"], text=True).strip()
dirty = subprocess.check_output(["git", "-C", str(APP), "status", "--porcelain"], text=True)
started = time.perf_counter()
error, code = None, None
try:
    with (out / "stdout.txt").open("w") as so, (out / "stderr.txt").open("w") as se:
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se), \
                patch.object(ip, "place_clip", placement), patch.object(ip._Placer, "observed_shaft", shaft_capture):
            code = raw.main(args)
except Exception as exc:  # recorded, not hidden
    error = {"type": type(exc).__name__, "message": str(exc)}
(out / "placement.json").write_text(json.dumps(state, indent=1, default=str))
np.savez_compressed(out / "arrays.npz", **arrays)
run = {"application_head": head, "application_dirty": dirty, "args": args, "returncode": code, "exception": error,
       "elapsed_s": round(time.perf_counter() - started, 2), "place_clip_calls": state["place_clip_calls"]}
(out / "RUN.json").write_text(json.dumps(run, indent=1))
p = state.get("placement", {})
print("shot", shot_number, "return", code, "error", error, "calls", state["place_clip_calls"], "elapsed", run["elapsed_s"])
print("  status", p.get("status"), p.get("reason"), "| offset", p.get("impact_offset_mm"), "+-", p.get("impact_offset_sigma_mm"),
      "| height", p.get("height_status"), p.get("impact_height_mm"), "+-", p.get("impact_height_sigma_mm"))
print("  notes", p.get("notes"))
