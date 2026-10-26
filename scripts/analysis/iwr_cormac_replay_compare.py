#!/usr/bin/env python3
"""RESEARCH: the IWR6843 on-radar pipeline (an external firmware tree) against our host estimators.

Offline only. Every dump of the IQ16 transfer study (``rows.jsonl``, which also holds our host
estimators' results on each original dump) is replayed through the external firmware's compiled
C modules with that tree's own host replay (``openflight.iwr6843.firmware_replay`` of the
external tree, ``replay_dump``), then the board's result packet is built with its own
``l3_result_build``. Nothing from that tree is copied here: ``replay`` imports it by path, so it
must run under an interpreter that can build it (numpy, scipy, pyserial and the ``ziglang`` wheel
when there is no C compiler). ``compare`` needs only the standard library.

Inputs the firmware needs that our host path does not, and what is supplied:

* a trigger tee bin (``triggerCfg``). Mode ``kiosk`` aims it as the external tree's own labelled
  replay tests do: the first labelled ball point, less ``SELF_TRIGGER_TEE_LEAD_BINS`` (read from
  its ``monitor.py`` source), default snr and tee band, no locked ball, self-trigger decides the
  freeze. Mode ``forced`` is the same with the recorded freeze as impact (``post_from_frame``),
  as an external OPS trigger would. Mode ``ours`` aims it at the tee our run used: our measured
  launch range, else the configured tee.
* ``ball.fit.teeBallHeightM`` (the board default is 0.04 m): set to our ball height.

Inputs ours needs that the firmware does not: the OPS243 ball and club speeds and the club.

Usage::

    <venv>/python scripts/analysis/iwr_cormac_replay_compare.py replay \\
        --firmware-repo <tree> --rows <study>/rows.jsonl --cal config/iwr6843_calibration_reference.json \\
        --out <dir> --mode kiosk
    python scripts/analysis/iwr_cormac_replay_compare.py compare --rows <study>/rows.jsonl --out <dir>
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
from pathlib import Path

BIN_M = 6.0 / 128.0
SEPTEMBER_TAPE_M = 1.575  # the transfer study's configured tee for the 2026-09 folder
MODES = ("kiosk", "forced", "ours", "kiosk_identity")
METRICS = ("ball", "vla", "hla", "launch_range", "path", "attack")
RESULT_METRICS = (
    "ball_speed",
    "vertical_launch",
    "horizontal_launch",
    "club_speed",
    "club_path",
    "angle_of_attack",
    "spin_rate",
    "spin_axis",
    "impact_range",
)
MEAS_VALID, MEAS_IMPLAUSIBLE = 1, 8
# Board-measured MSS scoring cost per range bin (the tree's architecture doc, 2026-09-30).
MSS_US_PER_BIN = 73.0
DSS_US_PER_BIN = 48.0


# --------------------------------------------------------------------------- pure helpers


def availability(ours: float | None, his: float | None) -> str:
    """Which side produced a value: both, ours, his or neither."""
    if ours is not None and his is not None:
        return "both"
    if ours is not None:
        return "ours"
    if his is not None:
        return "his"
    return "neither"


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile (q in 0..100); None for no values."""
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def abs_diff_stats(pairs: list[tuple[float, float]]) -> dict:
    """Median, p90 and max of |ours - his| over pairs, with the median signed difference."""
    diffs = [abs(a - b) for a, b in pairs]
    signed = [a - b for a, b in pairs]
    return {
        "n": len(diffs),
        "median_abs": statistics.median(diffs) if diffs else None,
        "p90_abs": percentile(diffs, 90),
        "max_abs": max(diffs) if diffs else None,
        "median_signed_ours_minus_his": statistics.median(signed) if signed else None,
    }


def range_to_bin(range_m: float, bias_m: float) -> float:
    """A bias-corrected range back on the global range-FFT bin axis."""
    return (range_m + bias_m) / BIN_M


def ours_values(original: dict, bias_m: float) -> dict:
    """Our host results as the metrics compared here; None where ours gave nothing usable."""
    accepted = bool(original.get("launch_accepted"))
    hstat = original.get("horizontal_status") or ""
    rng = original.get("range_m") if original.get("range_status") == "measured" else None
    return {
        "ball": 1.0 if accepted else None,
        "vla": original.get("launch_deg") if accepted else None,
        "hla": (
            original.get("horizontal_deg")
            if accepted
            and hstat.endswith("accepted")
            and original.get("horizontal_deg") is not None
            else None
        ),
        "launch_range": rng,
        "launch_range_bin": range_to_bin(rng, bias_m) if rng is not None else None,
        "path": original.get("path_deg") if original.get("path_accepted") else None,
        "attack": (
            original.get("attack_deg")
            if original.get("attack_status") == "candidate_available"
            else None
        ),
    }


def his_values(his: dict) -> dict:
    """The firmware's published result packet as the same metrics (valid and plausible only)."""
    metrics = his.get("result", {}).get("metrics", {})

    def ok(name: str, scale: float = 1.0) -> float | None:
        m = metrics.get(name)
        if not m or not m["flags"] & MEAS_VALID or m["flags"] & MEAS_IMPLAUSIBLE:
            return None
        return m["value"] * scale

    deg = 180.0 / math.pi
    first = his.get("ball_first_point")
    return {
        "ball": 1.0 if ok("ball_speed") is not None else None,
        "vla": ok("vertical_launch", deg),
        "hla": ok("horizontal_launch", deg),
        # Measured proxy: the ball track's first point (the published impact_range is the tee
        # input echoed back when no ball is locked, flagged inferred).
        "launch_range": first["range_m"] if first else None,
        "launch_range_bin": first["range_bin"] if first else None,
        "path": ok("club_path", deg),
        "attack": ok("angle_of_attack", deg),
    }


def label_first_ball(labels: dict | None) -> dict | None:
    """The first labelled ball point (frame, range_bin) of a reviewed labels sidecar."""
    if not labels or not labels.get("reviewed"):
        return None
    pts = sorted(labels.get("ball", {}).get("points", []), key=lambda p: p["frame"])
    return pts[0] if pts else None


def label_at_frame(labels_path: Path, frame: int) -> float | None:
    """The labelled ball bin on one frame, None when that frame is not labelled."""
    if not labels_path.exists():
        return None
    labels = json.loads(labels_path.read_text())
    if not labels.get("reviewed"):
        return None
    for p in labels.get("ball", {}).get("points", []):
        if p["frame"] == frame:
            return p["range_bin"]
    return None


def launch_range_label_error(range_bin: float | None, label_bin: float | None) -> float | None:
    """Signed launch-range error in bins against the first labelled (in-flight) ball point."""
    if range_bin is None or label_bin is None:
        return None
    return range_bin - label_bin


# --------------------------------------------------------------------------- replay (his venv)


def _shot_inputs(row: dict, root_manifests: dict) -> dict:
    """The physical inputs our run used for one dump."""
    geometry = {"tilt_deg": 10.0, "radar_height_m": 0.1524, "ball_height_m": 0.021335}
    tee_bin = None
    if row["dataset"] == "outdoors-test-7":
        folder = Path(row["path"]).parent.parent
        session = next(folder.glob("session_*.jsonl"))
        for line in session.read_text().splitlines():
            rec = json.loads(line)
            if rec.get("type") == "iwr6843_capture" and os.path.basename(
                rec["capture_path"]
            ) == os.path.basename(row["path"]):
                eff = rec["runtime_config"]["calibration"]["effective"]
                geometry = {
                    "tilt_deg": eff["tilt_deg"],
                    "radar_height_m": eff["radar_height_m"],
                    "ball_height_m": eff["ball_height_m"],
                    "board_rotation_deg": eff.get("iwr_board_rotation_deg"),
                }
                break
    elif row["dataset"] == "september":
        tee_bin = None  # tape given in metres below
    else:
        manifest = root_manifests.setdefault(
            str(Path(row["path"]).parent),
            json.loads((Path(row["path"]).parent / "manifest.json").read_text()),
        )
        entry = {**manifest.get("default", {}), **manifest.get(os.path.basename(row["path"]), {})}
        tee_bin = entry.get("dest_bin")
    return {
        "geometry": geometry,
        "configured_tee_bin": tee_bin,
        "configured_tee_m": SEPTEMBER_TAPE_M if row["dataset"] == "september" else None,
    }


def _lead_bins(firmware_repo: Path) -> int:
    text = (firmware_repo / "src/openflight/iwr6843/monitor.py").read_text(encoding="utf-8")
    return int(re.search(r"^SELF_TRIGGER_TEE_LEAD_BINS\s*=\s*(\d+)", text, re.M).group(1))


def replay_main(args: argparse.Namespace) -> int:  # pylint: disable=too-many-locals,too-many-statements
    sys.dont_write_bytecode = True  # never leave caches in the external tree
    sys.path.insert(0, str(args.firmware_repo / "src"))
    import ctypes  # pylint: disable=import-outside-toplevel
    from dataclasses import replace  # pylint: disable=import-outside-toplevel

    from openflight.iwr6843 import (  # pylint: disable=import-outside-toplevel
        firmware_host as fw,
        firmware_replay as fr,
        label_scoring as ls,
    )
    from openflight.iwr6843.board_calibration import (  # pylint: disable=import-outside-toplevel
        BoardCalibration,
    )
    from openflight.iwr6843.calibration import (  # pylint: disable=import-outside-toplevel
        Calibration,
    )
    from openflight.iwr6843.dump import parse_dump  # pylint: disable=import-outside-toplevel
    from openflight.iwr6843.labels import load_labels  # pylint: disable=import-outside-toplevel
    from openflight.iwr6843.self_trigger import (  # pylint: disable=import-outside-toplevel
        FIRMWARE_TRIGGER_DEFAULT_SNR,
        TEE_BAND_DEFAULT_BINS,
    )

    lead = _lead_bins(args.firmware_repo)
    lib = fr._default_library()  # pylint: disable=protected-access
    cal = Calibration.load(str(args.cal))
    rows = [json.loads(line) for line in args.rows.read_text().splitlines() if line.strip()]
    manifests: dict = {}
    args.out.mkdir(parents=True, exist_ok=True)
    out_path = args.out / f"cormac_{args.mode}.jsonl"
    with out_path.open("w", encoding="utf-8") as sink:
        for row in rows:
            path = Path(row["path"])
            raw = path.read_bytes()
            meta, _ = parse_dump(raw)
            inputs = _shot_inputs(row, manifests)
            g = inputs["geometry"]
            board = replace(
                (
                    BoardCalibration.identity()
                    if args.mode == "kiosk_identity"
                    else BoardCalibration.from_calibration(cal)
                ),
                pitch_deg=0.0 if args.mode == "kiosk_identity" else float(g["tilt_deg"]),
                radar_height_m=float(g["radar_height_m"]),
            )
            labels_path = Path(str(path) + ".labels.json")
            label_json = json.loads(labels_path.read_text()) if labels_path.exists() else None
            first_label = label_first_ball(label_json)
            our_range = row["original"].get("range_m")
            if args.mode != "ours" and first_label is not None:
                ball_bin, ball_bin_source = int(round(first_label["range_bin"])), "label_first_ball"
            elif our_range is not None:
                ball_bin = int(round(range_to_bin(our_range, board.range_bias_m)))
                ball_bin_source = "our_launch_range"
            elif inputs["configured_tee_bin"] is not None:
                ball_bin, ball_bin_source = int(inputs["configured_tee_bin"]), "manifest_dest_bin"
            else:
                ball_bin = int(round(range_to_bin(inputs["configured_tee_m"], board.range_bias_m)))
                ball_bin_source = "configured_tape"
            post_from = None
            if args.mode == "forced":
                known_post = fr.post_frame_count(meta)
                post_from = int(meta["n_frames"]) - known_post if known_post else 9
            base = fr.ReplayConfig(tee_bin=ball_bin - lead)
            config = replace(
                base,
                dest_bin=None,
                snr=FIRMWARE_TRIGGER_DEFAULT_SNR,
                band_bins=TEE_BAND_DEFAULT_BINS,
                post_from_frame=post_from,
                overrides={
                    "ball.fit.radarHeightM": float(g["radar_height_m"]),
                    "ball.fit.teeBallHeightM": float(g["ball_height_m"]),
                },
                **board.replay_overrides(),
            )
            record = {
                "path": str(path),
                "mode": args.mode,
                "dataset": row["dataset"],
                "inputs": {
                    "ball_bin": ball_bin,
                    "ball_bin_source": ball_bin_source,
                    "tee_bin": ball_bin - lead,
                    "post_from_frame": post_from,
                    "pitch_deg": board.pitch_deg,
                    "radar_height_m": g["radar_height_m"],
                    "tee_ball_height_m": g["ball_height_m"],
                    "range_bias_m": board.range_bias_m,
                    "board_rotation_deg_not_applied": g.get("board_rotation_deg"),
                },
            }
            try:
                result = fr.replay_dump(raw, config, lib=lib)
            except Exception as exc:  # noqa: BLE001  (record where it stops; never hack around it)
                record["error"] = f"{type(exc).__name__}: {exc}"
                sink.write(json.dumps(record) + "\n")
                continue
            launch = fw.Launch()
            summary = result.launch
            if summary is not None:
                launch.points = summary.points
                launch.speedMps = summary.speed_mps
                launch.radialSpeedMps = summary.radial_speed_mps
                launch.speedValid = 1
                launch.hlaValid = 1 if summary.hla_deg is not None else 0
                launch.vlaValid = 1 if summary.vla_deg is not None else 0
                launch.hlaRad = math.radians(summary.hla_deg or 0.0)
                launch.vlaRad = math.radians(summary.vla_deg or 0.0)
                launch.residualM = summary.residual_m
                launch.confidence = summary.confidence
            packet = fw.ShotResult()
            lib.l3_result_build(
                ctypes.byref(result.shot),
                ctypes.byref(result.ball_track),
                ctypes.byref(launch),
                None,  # the replay keeps only the fit's summary; it only adds a warning flag
                1,
                0,  # no locked ball: the ball detector needs idle frames a dump does not hold
                ctypes.byref(packet),
            )
            metrics = {
                name: {
                    "value": float(packet.metric[i].value),
                    "confidence": float(packet.metric[i].confidence),
                    "flags": int(packet.metric[i].flags),
                }
                for i, name in enumerate(RESULT_METRICS)
            }
            fired = result.fired_frame
            pre = [f.scored_bins for f in result.frames if fired is None or f.frame <= fired]
            post = [f.scored_bins for f in result.frames if fired is not None and f.frame > fired]
            first = result.ball_points[0] if result.ball_points else None
            scores = None
            if label_json is not None and label_json.get("reviewed"):
                labels = load_labels(path)
                scores = {
                    obj: {
                        "labelled": s.labelled,
                        "tracked": s.tracked,
                        "matched": s.matched,
                        "false_points": s.false_points,
                        "mean_abs_error_bins": s.mean_abs_error_bins,
                        "coverage": s.coverage,
                    }
                    for obj, s in ls.score_labels(labels, result).items()
                }
            record.update(
                {
                    "fired_frame": fired,
                    "fired_by": (
                        None
                        if fired is None
                        else "leave"
                        if result.leave_frame == fired
                        else "range"
                    ),
                    "label_launch_frame": first_label["frame"] if first_label else None,
                    "shot_status": result.shot_status,
                    "ball_status": result.ball_status,
                    "impact_status": result.impact_status,
                    "launch": None
                    if summary is None
                    else {
                        "speed_mps": summary.speed_mps,
                        "radial_speed_mps": summary.radial_speed_mps,
                        "vla_deg": summary.vla_deg,
                        "hla_deg": summary.hla_deg,
                        "points": summary.points,
                        "residual_m": summary.residual_m,
                        "confidence": summary.confidence,
                        "angles_accepted": summary.angles_accepted,
                        "angle_rms_deg": summary.angle_rms_deg,
                        "angle_why": summary.angle_why,
                    },
                    "ball_first_point": None
                    if first is None
                    else {
                        "frame": first.frame,
                        "range_bin": first.range_bin,
                        "range_m": first.range_m,
                    },
                    "ball_points": len(result.ball_points),
                    "club_points": len(result.points),
                    "result": {
                        "text": fw.c_text(lib.l3_result_format, ctypes.byref(packet), cap=300),
                        "verdict": ("invalid", "partial", "valid")[packet.verdict],
                        "quality_flags": int(packet.qualityFlags),
                        "metrics": metrics,
                    },
                    "work": {
                        "pre_scored_bins_max": max(pre, default=0),
                        "post_scored_bins_max": max(post, default=0),
                        "pre_ms_mss_est": max(pre, default=0) * MSS_US_PER_BIN / 1000.0,
                        "pre_ms_dss_est": max(pre, default=0) * DSS_US_PER_BIN / 1000.0,
                        "angle_snapshots_max": max(
                            (f.angle_snapshots for f in result.frames), default=0
                        ),
                    },
                    "label_scores": scores,
                }
            )
            sink.write(json.dumps(record) + "\n")
    print(f"wrote {out_path}")
    return 0


# --------------------------------------------------------------------------- compare


def compare_mode(rows: list[dict], his_rows: dict, bias_m: float) -> dict:  # pylint: disable=too-many-locals
    """Availability, agreement, disagreements and label scores for one replay mode."""
    per_dump, avail, pairs = [], {m: {} for m in METRICS}, {m: [] for m in METRICS}
    label_err = {"ours": [], "his": []}
    speed_pairs = []  # (labelled radial ball speed our run was given, his radial launch speed)
    for row in rows:
        his = his_rows.get(row["path"])
        if his is None:
            continue
        o = ours_values(row["original"], bias_m)
        h = his_values(his) if "error" not in his else dict.fromkeys(METRICS)
        labels_path = Path(row["path"] + ".labels.json")
        label = label_first_ball(
            json.loads(labels_path.read_text()) if labels_path.exists() else None
        )
        entry = {"dump": os.path.basename(row["path"]), "dataset": row["dataset"]}
        for m in METRICS:
            a = availability(o[m], h[m])
            avail[m][a] = avail[m].get(a, 0) + 1
            entry[m] = {"ours": o[m], "his": h[m], "availability": a}
            if a == "both" and m != "ball":
                pairs[m].append((o[m], h[m]))
        launch = his.get("launch")
        if h["ball"] is not None and launch and row.get("input_source") == "labels_radial_speed":
            speed_pairs.append(
                (row["ball_speed_mph"] / 2.2369362920544, launch["radial_speed_mps"])
            )
        lb = label["range_bin"] if label else None
        entry["label_first_ball_bin"] = lb
        err = launch_range_label_error(o["launch_range_bin"], lb)
        entry["launch_range_label_err_bins_ours"] = err
        if err is not None:
            label_err["ours"].append(err)
        first = his.get("ball_first_point")
        same = label_at_frame(labels_path, first["frame"]) if first else None
        err = launch_range_label_error(first["range_bin"] if first else None, same)
        entry["his_first_point_label_err_bins_same_frame"] = err
        entry["his_first_point_frame_minus_label_first"] = (
            first["frame"] - label["frame"] if first and label else None
        )
        if err is not None:
            label_err["his"].append(err)
        entry["his_why"] = {
            "error": his.get("error"),
            "fired_frame": his.get("fired_frame"),
            "fired_by": his.get("fired_by"),
            "label_launch_frame": his.get("label_launch_frame"),
            "verdict": his.get("result", {}).get("verdict"),
            "angle_why": (his.get("launch") or {}).get("angle_why"),
            "ball_status": his.get("ball_status"),
        }
        entry["ours_why"] = {
            "launch_status": row["original"].get("launch_status"),
            "horizontal_status": row["original"].get("horizontal_status"),
            "range_status": row["original"].get("range_status"),
            "path_status": row["original"].get("path_status"),
            "attack_status": row["original"].get("attack_status"),
        }
        per_dump.append(entry)
    agreement = {m: abs_diff_stats(pairs[m]) for m in METRICS if m != "ball"}
    # Ball speed: ours takes it as an input (the labels' radial speed on the recordings), so
    # this is his measured radial launch speed against the labels, in m/s.
    agreement["ball_radial_speed_mps_labels_minus_his"] = abs_diff_stats(speed_pairs)
    worst = {}
    for m in ("vla", "hla", "launch_range"):
        both = [e for e in per_dump if e[m]["availability"] == "both"]
        both.sort(key=lambda e, m=m: -abs(e[m]["ours"] - e[m]["his"]))
        worst[m] = [
            {
                "dump": e["dump"],
                "ours": e[m]["ours"],
                "his": e[m]["his"],
                "his_why": e["his_why"],
                "ours_why": e["ours_why"],
            }
            for e in both[:5]
        ]

    def label_stats(errs: list[float], window: tuple[float, float]) -> dict:
        within = [e for e in errs if window[0] <= e <= window[1]]
        return {
            "n": len(errs),
            "median_signed_bins": statistics.median(errs) if errs else None,
            "median_abs_bins": statistics.median([abs(e) for e in errs]) if errs else None,
            "p90_abs_bins": percentile([abs(e) for e in errs], 90),
            "within_window": len(within),
        }

    return {
        "dumps": len(per_dump),
        "availability": avail,
        "agreement": agreement,
        "worst": worst,
        # ours: launch range against the first labelled (in-flight) ball point, so a launch
        # up to ~3.5 bins (one frame of flight) short is right; his: the first ball point
        # against the label on the same frame (within one bin, the labels' tolerance).
        "launch_range_vs_labels": {
            "ours_vs_first_label_bins": label_stats(label_err["ours"], (-3.5, 0.5)),
            "his_first_point_vs_same_frame_label_bins": label_stats(label_err["his"], (-1.0, 1.0)),
            "his_first_point_frame_minus_label_first": statistics.median(
                [
                    e["his_first_point_frame_minus_label_first"]
                    for e in per_dump
                    if e["his_first_point_frame_minus_label_first"] is not None
                ]
            ),
        },
        "per_dump": per_dump,
    }


def his_breakdown(his_rows: dict) -> dict:
    """Why the firmware gave no ball result, and its trigger timing against the labels."""
    reasons: dict = {}
    offsets = []
    verdicts: dict = {}
    for his in his_rows.values():
        if "error" in his:
            reasons["replay_error"] = reasons.get("replay_error", 0) + 1
            continue
        verdicts[his["result"]["verdict"]] = verdicts.get(his["result"]["verdict"], 0) + 1
        if his["fired_frame"] is not None and his["label_launch_frame"] is not None:
            offsets.append(his["fired_frame"] - his["label_launch_frame"])
        if his_values(his)["vla"] is not None:
            continue
        if his["fired_frame"] is None and his["inputs"]["post_from_frame"] is None:
            key = "no_trigger"
        elif his["launch"] is None:
            key = "no_confirmed_ball_flight"
        elif his["launch"]["vla_deg"] is None:
            key = f"angles_{his['launch']['angle_why']}"
        else:
            key = "vla_implausible"
        reasons[key] = reasons.get(key, 0) + 1
    ball_scores = [h["label_scores"]["ball"] for h in his_rows.values() if h.get("label_scores")]
    work = [h["work"] for h in his_rows.values() if "work" in h]
    return {
        "no_vla_reasons": reasons,
        "verdicts": verdicts,
        "fire_minus_label_launch_frames": {
            "n": len(offsets),
            "median": statistics.median(offsets) if offsets else None,
            "within_-4_+2": sum(1 for o in offsets if -4 <= o <= 2),
        },
        "ball_track_vs_labels": {
            "dumps": len(ball_scores),
            "median_coverage": statistics.median([s["coverage"] for s in ball_scores])
            if ball_scores
            else None,
            "median_abs_err_bins": statistics.median(
                [
                    s["mean_abs_error_bins"]
                    for s in ball_scores
                    if s["mean_abs_error_bins"] is not None
                ]
            )
            if ball_scores
            else None,
            "false_points_total": sum(s["false_points"] for s in ball_scores),
        },
        "work": {
            "pre_scored_bins_max": max((w["pre_scored_bins_max"] for w in work), default=0),
            "post_scored_bins_max": max((w["post_scored_bins_max"] for w in work), default=0),
            "pre_ms_mss_est_max": max((w["pre_ms_mss_est"] for w in work), default=0),
            "pre_ms_dss_est_max": max((w["pre_ms_dss_est"] for w in work), default=0),
        },
    }


def compare_main(args: argparse.Namespace) -> int:
    rows = [json.loads(line) for line in args.rows.read_text().splitlines() if line.strip()]
    # Our range_m is bias-corrected; put it back on the bin axis with our calibration's bias.
    our_bias = float(json.loads(args.cal.read_text())["range_bias_const_m"])
    summary = {}
    for mode in MODES:
        path = args.out / f"cormac_{mode}.jsonl"
        if not path.exists():
            continue
        his_rows = {
            r["path"]: r
            for r in (json.loads(x) for x in path.read_text().splitlines() if x.strip())
        }
        result = compare_mode(rows, his_rows, our_bias)
        result["his"] = his_breakdown(his_rows)
        summary[mode] = result
        brief = {k: v for k, v in result.items() if k != "per_dump"}
        print(f"== {mode}\n{json.dumps(brief, indent=1, default=str)}")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    rep = sub.add_parser("replay")
    rep.add_argument("--firmware-repo", required=True, type=Path)
    rep.add_argument("--rows", required=True, type=Path)
    rep.add_argument("--cal", required=True, type=Path)
    rep.add_argument("--out", required=True, type=Path)
    rep.add_argument("--mode", choices=MODES, default="kiosk")
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("--rows", required=True, type=Path)
    cmp_.add_argument("--out", required=True, type=Path)
    cmp_.add_argument("--cal", required=True, type=Path)
    args = parser.parse_args(argv)
    return replay_main(args) if args.cmd == "replay" else compare_main(args)


if __name__ == "__main__":
    sys.exit(main())
