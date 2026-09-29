#!/usr/bin/env python3
"""EXPERIMENTAL heel-toe impact location over a saved tester session. Unqualified.

Walks a tester session folder or an unzipped session bundle (a ``.zip`` bundle
is extracted next to the output first), finds every camera clip
(``camera_*/frames.npz`` + ``metadata.json``), and for each one:

  1. finds the resting ball -- from the ladder's own swing verdict when the
     session has one, otherwise with ``club_motion.detect_reference_ball``;
  2. reads the OPS club speed and the club from the session JSONL when the
     clip is linked to a shot there (the speed pins the head's range);
  3. prepares the clip with ``openflight.camera.impact_location``: the
     ball-defined contact frame and the head masks around it.

Then it builds one self-built head template per (club, frame size) from every
clip of that group, reads each clip against it, and writes

  impact_location.csv / .json   one row per clip: contact frame, heel-toe mm
                                (+ toward the toe), uncertainty, status
  overlays/<clip>.png           the analysed frames with mask, placed outline
                                and the reading -- LOOK AT THESE FIRST
  templates/<group>.png         each template's occupancy and outline

With ``--truth`` (CSV with ``shot_id`` and ``heel_toe_mm`` columns, + toward
the toe from the face centre, measured on the foot-spray photo) it prints and
stores agreement: bias, RMS, bias-removed SD and Pearson r, and the face-centre
offset that would remove the bias. ``shot_id`` is the clip folder name
(``camera_YYYYMMDD_HHMMSS_mmm_NNN``, the key the ladder uses for impact
photos) or its path relative to the session root.

    uv run python scripts/analysis/impact_location.py SESSION_DIR --out OUT_DIR \
        [--truth spray.csv] [--handedness right|left] [--club-speed-mps 35]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from openflight.camera import impact_location as il  # noqa: E402

MPH_TO_MPS = 0.44704
RESTING_FRAMES = 10  # study_ladder.RESTING_FRAMES: the first pre-impact frames
CSV_FIELDS = (
    "shot_id",
    "relative_path",
    "club",
    "frame_size",
    "fps",
    "status",
    "reason",
    "contact_frame",
    "last_rest_frame",
    "contact_time_frames",
    "trigger_index",
    "contact_minus_trigger_ms",
    "heel_toe_mm",
    "uncertainty_mm",
    "ball_from_toe_mm",
    "face_width_mm",
    "club_speed_mps",
    "club_speed_source",
    "ball_source",
    "template_samples",
    "truth_heel_toe_mm",
    "label",
)


@dataclass
class Clip:
    """One saved camera clip and everything the session says about it."""

    shot_id: str
    path: Path
    relative_path: str
    fps: float | None = None
    mirrored: bool = False
    trigger_index: int | None = None
    exposure_us: float | None = None
    gain: float | None = None
    frame_size: str = ""
    club: str = ""
    club_speed_mps: float | None = None
    ball: il.RestingBall | None = None
    ball_source: str = ""
    prepared: il.PreparedShot | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# the session on disk
# ---------------------------------------------------------------------------


def resolve_root(path: Path, out: Path) -> Path:
    """A folder as given; a bundle ``.zip`` extracted under the output folder."""
    if path.is_dir():
        return path
    if path.suffix.lower() == ".zip" and path.is_file():
        target = out / f"bundle_{path.stem}"
        if not target.exists():
            with zipfile.ZipFile(path) as archive:
                archive.extractall(target)
        return target
    raise SystemExit(f"not a session folder or bundle zip: {path}")


def find_clips(root: Path) -> list[Clip]:
    """Every ``frames.npz`` with a sibling ``metadata.json``, in path order."""
    clips = []
    for frames in sorted(root.rglob("frames.npz")):
        if ".bundles" in frames.parts:
            continue
        folder = frames.parent
        clips.append(Clip(folder.name, folder, folder.relative_to(root).as_posix()))
    return clips


def _ladder_balls(root: Path) -> dict[str, dict]:
    balls: dict[str, dict] = {}
    for ladder in root.rglob("ladder.json"):
        try:
            data = json.loads(ladder.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for rung in (data.get("rungs") or {}).values():
            for swing in rung.get("swings", []) if isinstance(rung, dict) else []:
                ball = swing.get("ball") if isinstance(swing, dict) else None
                if isinstance(ball, dict) and {"x", "y", "diameter_px"} <= set(ball):
                    balls[str(swing.get("capture"))] = ball
    return balls


def _session_shots(root: Path) -> dict[str, dict]:
    """Clip folder name -> the shot the session JSONL linked it to."""
    linked: dict[str, dict] = {}
    for log in root.rglob("*.jsonl"):
        captures: dict[int, str] = {}
        shots: dict[int, dict] = {}
        try:
            lines = log.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict) or not isinstance(record.get("shot_number"), int):
                continue
            if record.get("type") == "camera_capture" and record.get("capture_path"):
                captures[record["shot_number"]] = Path(
                    str(record["capture_path"]).replace("\\", "/")
                ).name
            elif record.get("type") == "shot_detected":
                shots[record["shot_number"]] = record
        for number, name in captures.items():
            if number in shots:
                linked[name] = shots[number]
    return linked


def load_clip(
    clip: Clip, ladder: dict[str, dict], shots: dict[str, dict], args
) -> np.ndarray | None:
    """Fill the clip's metadata and return its frames, or record why not."""
    try:
        metadata = json.loads((clip.path / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        metadata = {}
    try:
        with np.load(clip.path / "frames.npz") as data:
            frames = np.asarray(data["frames"], dtype=np.uint8)
            if "pre_trigger_count" in data.files:
                clip.trigger_index = int(data["pre_trigger_count"])
            if "exposure_us" in data.files:
                clip.exposure_us = float(np.median(data["exposure_us"]))
            if "analogue_gain" in data.files:
                clip.gain = float(np.median(data["analogue_gain"]))
            stamps = (
                np.asarray(data["sensor_timestamp_ns"])
                if "sensor_timestamp_ns" in data.files
                else None
            )
    except (OSError, ValueError, KeyError) as exc:
        clip.error = f"unreadable_frames_npz: {exc}"
        return None
    if frames.ndim != 3:
        clip.error = "frames_npz_is_not_a_clip"
        return None
    clip.frame_size = f"{frames.shape[2]}x{frames.shape[1]}"
    fps = float(metadata.get("delivered_fps") or 0.0)
    if fps <= 0.0 and stamps is not None and stamps.size > 2:
        interval = float(np.median(np.diff(stamps.astype(np.float64))))
        fps = 1e9 / interval if interval > 0 else 0.0
    clip.fps = fps if fps > 0 else None
    settings = metadata.get("settings") if isinstance(metadata.get("settings"), dict) else {}
    clip.mirrored = settings.get("mirror_horizontal") is True
    if clip.trigger_index is None and isinstance(metadata.get("pre_trigger_frames"), int):
        clip.trigger_index = int(metadata["pre_trigger_frames"])

    shot = shots.get(clip.shot_id, {})
    clip.club = args.club or str(shot.get("club") or "unknown")
    speed_mph = shot.get("club_speed_mph")
    if isinstance(speed_mph, (int, float)) and speed_mph > 0:
        clip.club_speed_mps = float(speed_mph) * MPH_TO_MPS
        clip.extra["club_speed_source"] = "session_ops_club_speed"
    elif args.club_speed_mps:
        clip.club_speed_mps = float(args.club_speed_mps)
        clip.extra["club_speed_source"] = "cli"

    known = None if args.redetect_ball else ladder.get(clip.shot_id)
    if args.ball is not None:
        clip.ball = il.RestingBall(*(float(v) for v in args.ball))
        clip.ball_source = "cli"
    elif known is not None:
        clip.ball = il.RestingBall(
            float(known["x"]), float(known["y"]), float(known["diameter_px"])
        )
        clip.ball_source = "ladder_verdict"
    else:
        from openflight.camera.club_motion import detect_reference_ball

        try:
            found = detect_reference_ball(frames[: max(3, min(RESTING_FRAMES, len(frames)))])
        except ValueError as exc:
            clip.error = f"resting_ball_not_found: {exc}"
            return None
        clip.ball = il.RestingBall(found.x, found.y, found.diameter_px)
        clip.ball_source = "detect_reference_ball"
    return frames


# ---------------------------------------------------------------------------
# truth
# ---------------------------------------------------------------------------


def read_truth(path: Path) -> dict[str, float]:
    """``shot_id`` -> measured heel-toe mm (+ toward the toe)."""
    truth: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            key = (row.get("shot_id") or row.get("shot") or row.get("capture") or "").strip()
            value = (row.get("heel_toe_mm") or row.get("truth_heel_toe_mm") or "").strip()
            if not key or not value:
                continue
            try:
                truth[key] = float(value)
            except ValueError:
                continue
    return truth


def match_truth(truth: dict[str, float], shot_id: str, relative_path: str) -> float | None:
    """By folder name, then by relative path, then by a unique path suffix."""
    if shot_id in truth:
        return truth[shot_id]
    if relative_path in truth:
        return truth[relative_path]
    hits = [v for k, v in truth.items() if relative_path.endswith(k.strip("/"))]
    return hits[0] if len(hits) == 1 else None


def agreement_stats(
    readings: list[float], truths: list[float], face_centre_mm: float
) -> dict[str, Any]:
    """Bias, RMS, bias-removed SD and Pearson r of reading minus truth."""
    reading = np.asarray(readings, dtype=float)
    truth = np.asarray(truths, dtype=float)
    count = int(reading.size)
    if count == 0:
        return {"n": 0}
    residual = reading - truth
    bias = float(np.mean(residual))
    stats: dict[str, Any] = {
        "n": count,
        "bias_mm": bias,
        "rms_mm": float(np.sqrt(np.mean(residual**2))),
        "sd_after_bias_mm": float(np.std(residual, ddof=1)) if count > 1 else math.nan,
        "pearson_r": (
            float(np.corrcoef(reading, truth)[0, 1])
            if count >= 3 and np.std(reading) > 0 and np.std(truth) > 0
            else math.nan
        ),
        "truth_spread_sd_mm": float(np.std(truth, ddof=1)) if count > 1 else math.nan,
        # Raising the face-centre offset by b moves every reading by -b.
        "face_centre_offset_to_remove_bias_mm": face_centre_mm + bias,
    }
    return stats


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _row(
    clip: Clip, location: il.ImpactLocation | None, template: il.HeadTemplate | None
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "shot_id": clip.shot_id,
        "relative_path": clip.relative_path,
        "club": clip.club,
        "frame_size": clip.frame_size,
        "fps": clip.fps,
        "trigger_index": clip.trigger_index,
        "ball_source": clip.ball_source,
        "label": il.EXPERIMENTAL_LABEL,
        "club_speed_mps": clip.club_speed_mps,
        "club_speed_source": clip.extra.get("club_speed_source", "assumed_default"),
    }
    if location is None:
        row.update({"status": "rejected", "reason": clip.error or "not_analysed"})
        return row
    row.update(
        {
            "status": location.status,
            "reason": location.reason,
            "contact_frame": location.contact_frame,
            "last_rest_frame": location.last_rest_frame,
            "contact_time_frames": location.contact_time_frames,
            "heel_toe_mm": location.heel_toe_mm,
            "uncertainty_mm": location.uncertainty_mm,
            "ball_from_toe_mm": location.ball_from_toe_mm,
            "face_width_mm": location.face_width_mm,
            "template_samples": template.n_samples if template is not None else None,
        }
    )
    if location.contact_time_frames is not None and clip.trigger_index is not None and clip.fps:
        # The trigger frame is the one after the last pre-trigger frame.
        row["contact_minus_trigger_ms"] = (
            (location.contact_time_frames - clip.trigger_index) * 1000.0 / clip.fps
        )
    return row


def _fmt(value: Any) -> Any:
    return round(value, 3) if isinstance(value, float) and math.isfinite(value) else value


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return str(value)


def _save_png(path: Path, image: np.ndarray) -> None:
    import cv2  # pylint: disable=import-outside-toplevel

    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)  # pylint: disable=no-member


def _template_picture(template: il.HeadTemplate) -> np.ndarray:
    import cv2  # pylint: disable=import-outside-toplevel,no-member

    image = cv2.cvtColor(
        (255 * np.clip(template.occupancy, 0, 1)).astype(np.uint8), cv2.COLOR_GRAY2BGR
    )  # pylint: disable=no-member
    contours, _ = cv2.findContours(template.outline_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)  # pylint: disable=no-member
    cv2.drawContours(image, contours, -1, (0, 140, 255), 1)  # pylint: disable=no-member
    for name, colour in (("heel", (255, 80, 0)), ("toe", (255, 80, 0)), ("midpoint", (0, 220, 0))):
        x, y = template.landmarks[name]
        cv2.circle(image, (int(round(x)), int(round(y))), 3, colour, -1)  # pylint: disable=no-member
    return image


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "session", type=Path, help="tester session folder, unzipped bundle, or bundle .zip"
    )
    parser.add_argument("--out", type=Path, default=Path("impact_location_out"))
    parser.add_argument(
        "--truth", type=Path, default=None, help="CSV: shot_id, heel_toe_mm (+ toe)"
    )
    parser.add_argument("--handedness", choices=il.HANDEDNESS, default="right")
    parser.add_argument("--club", default="", help="override the club for every clip")
    parser.add_argument(
        "--club-speed-mps",
        type=float,
        default=None,
        help="club speed when the session JSONL has none (else 35 m/s is ASSUMED)",
    )
    parser.add_argument(
        "--contact-fraction",
        type=float,
        default=None,
        help="where in the bracket contact fell (default 0.5)",
    )
    parser.add_argument("--face-centre-offset-mm", type=float, default=il.FACE_CENTRE_TOEWARD_MM)
    parser.add_argument(
        "--ball",
        type=float,
        nargs=3,
        metavar=("X", "Y", "DIAMETER"),
        default=None,
        help="resting ball in SAVED-frame pixels for every clip (when the detector fails)",
    )
    parser.add_argument(
        "--redetect-ball",
        action="store_true",
        help="ignore the ladder's recorded ball and run the detector on every clip",
    )
    parser.add_argument("--template-scope", choices=("club", "session"), default="club")
    parser.add_argument(
        "--shot", action="append", default=[], help="only clips whose id contains this"
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-overlays", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:  # pylint: disable=too-many-branches
    """Prepare every clip, build templates per group, read, compare to truth."""
    args = build_parser().parse_args(argv)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    root = resolve_root(args.session, out)
    clips = find_clips(root)
    if args.shot:
        clips = [c for c in clips if any(s in c.relative_path for s in args.shot)]
    if args.limit:
        clips = clips[: args.limit]
    if not clips:
        print(f"no camera clips (frames.npz) under {root}")
        return 1
    ladder = _ladder_balls(root)
    shots = _session_shots(root)
    print(f"{len(clips)} clips under {root}; {len(ladder)} ladder balls; {len(shots)} linked shots")

    for index, clip in enumerate(clips, start=1):
        frames = load_clip(clip, ladder, shots, args)
        if frames is None or clip.ball is None:
            print(f"[{index}/{len(clips)}] {clip.shot_id}: {clip.error}")
            continue
        if clip.fps is None:
            clip.error = "frame_rate_unknown"
            print(f"[{index}/{len(clips)}] {clip.shot_id}: {clip.error}")
            continue
        clip.prepared = il.prepare_shot(
            frames,
            clip.ball,
            fps=clip.fps,
            handedness=args.handedness,
            frames_mirrored=clip.mirrored,
            club_speed_mps=clip.club_speed_mps,
            contact_fraction=args.contact_fraction,
            name=clip.shot_id,
            club=clip.club,
        )
        if clip.club_speed_mps is None:
            clip.extra["club_speed_source"] = "assumed_default"
        contact = clip.prepared.contact
        masks = len(clip.prepared.masks.masks) if clip.prepared.masks else 0
        print(
            f"[{index}/{len(clips)}] {clip.shot_id}: contact {contact.status} "
            f"({contact.reason}) after f{contact.last_rest_frame}, {masks} head masks"
        )

    groups: dict[str, list[Clip]] = {}
    for clip in clips:
        if clip.prepared is not None:
            key = (
                clip.frame_size
                if args.template_scope == "session"
                else f"{clip.club}_{clip.frame_size}"
            )
            groups.setdefault(key, []).append(clip)
    templates: dict[str, il.HeadTemplate | None] = {}
    template_notes: dict[str, Any] = {}
    for key, members in groups.items():
        template, reason = il.build_self_template([c.prepared for c in members], club=key)
        templates[key] = template
        template_notes[key] = template.summary() if template else {"reason": reason}
        if template is not None:
            _save_png(out / "templates" / f"{key}.png", _template_picture(template))
        print(f"template {key}: {template_notes[key]}")

    truth = read_truth(args.truth) if args.truth else {}
    rows: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for clip in clips:
        if clip.prepared is None:
            rows.append(_row(clip, None, None))
            continue
        key = (
            clip.frame_size
            if args.template_scope == "session"
            else f"{clip.club}_{clip.frame_size}"
        )
        template = templates.get(key)
        location = il.locate_impact(
            clip.prepared, template, face_centre_toeward_mm=args.face_centre_offset_mm
        )
        row = _row(clip, location, template)
        row["truth_heel_toe_mm"] = (
            match_truth(truth, clip.shot_id, clip.relative_path) if truth else None
        )
        rows.append(row)
        details.append({"shot_id": clip.shot_id, **location.as_dict()})
        if not args.no_overlays:
            _save_png(
                out / "overlays" / f"{clip.shot_id}.png",
                il.render_overlay(clip.prepared, template, location),
            )
        clip.prepared = replace(clip.prepared, frames=clip.prepared.frames[:0])  # free the pixels

    with (out / "impact_location.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _fmt(row.get(k)) for k in CSV_FIELDS})

    ok = [r for r in rows if r.get("status") == "ok"]
    print(f"\n{len(ok)} of {len(rows)} clips read (EXPERIMENTAL, unqualified; + = toward the toe)")
    rejected: dict[str, int] = {}
    for row in rows:
        if row.get("status") != "ok":
            reason = str(row.get("reason")).split(":")[0]
            rejected[reason] = rejected.get(reason, 0) + 1
    for reason, count in sorted(rejected.items(), key=lambda item: -item[1]):
        print(f"  rejected {count:3d}  {reason}")

    report: dict[str, Any] = {
        "label": il.EXPERIMENTAL_LABEL,
        "caveat": (
            "EXPERIMENTAL, UNQUALIFIED. The face centre is a convention "
            f"(outline heel-toe midpoint + {args.face_centre_offset_mm:.1f} mm toe-ward) until "
            "calibrated against spray truth; only agreement against --truth is evidence."
        ),
        "session": str(args.session),
        "handedness": args.handedness,
        "face_centre_offset_mm": args.face_centre_offset_mm,
        "templates": template_notes,
        "rows": rows,
        "details": details,
    }
    if truth:
        paired = [r for r in ok if r.get("truth_heel_toe_mm") is not None]
        report["truth_unmatched"] = sorted(
            set(truth) - {r["shot_id"] for r in rows} - {r["relative_path"] for r in rows}
        )
        overall = agreement_stats(
            [r["heel_toe_mm"] for r in paired],
            [r["truth_heel_toe_mm"] for r in paired],
            args.face_centre_offset_mm,
        )
        by_club = {
            club: agreement_stats(
                [r["heel_toe_mm"] for r in paired if r["club"] == club],
                [r["truth_heel_toe_mm"] for r in paired if r["club"] == club],
                args.face_centre_offset_mm,
            )
            for club in sorted({r["club"] for r in paired})
        }
        toe = agreement_stats(
            [r["ball_from_toe_mm"] for r in paired],
            [r["truth_heel_toe_mm"] for r in paired],
            0.0,
        )
        report["agreement"] = {
            "heel_toe": overall,
            "by_club": by_club,
            "toe_anchored_reading_vs_truth": toe,
        }
        print(f"\nagreement against truth ({len(paired)} paired; {len(truth)} truth rows)")
        for name, stats in [("all", overall)] + list(by_club.items()):
            if stats.get("n"):
                print(
                    f"  {name:12s} n={stats['n']:3d} bias {stats['bias_mm']:+6.1f} mm  "
                    f"RMS {stats['rms_mm']:5.1f}  SD after bias {stats['sd_after_bias_mm']:5.1f}  "
                    f"r {stats['pearson_r']:+.2f}  (truth SD {stats['truth_spread_sd_mm']:.1f}); "
                    f"--face-centre-offset-mm {stats['face_centre_offset_to_remove_bias_mm']:.1f} removes the bias"
                )
        if report["truth_unmatched"]:
            print(f"  {len(report['truth_unmatched'])} truth rows matched no clip")
    (out / "impact_location.json").write_text(
        json.dumps(report, indent=2, default=_json_default) + "\n", encoding="utf-8"
    )
    print(
        f"\nwrote {out / 'impact_location.csv'}, impact_location.json"
        + ("" if args.no_overlays else ", overlays/")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
