"""EXPERIMENTAL impact location over a recorded session or bundle, after the session.

Walks a tester session folder, an unzipped bundle or a bundle ``.zip`` (in the
fork's layout, ``<tester>/<arm>/paired/run-NN/``), links every saved camera clip to
the shot the run's session log recorded for it, gathers that shot's own inputs,
builds one self-built outline per (club, frame size) and reads both axes with
``openflight.camera.impact_location``. Nothing here runs on the kiosk; every
result is labelled ``experimental_unqualified`` and every refusal names its reason.

Per-shot inputs, each with its source:

- the ball's range: the shot's own IWR range (``ball_range_m``, never a camera
  prior), else the run's selected tee range, else the session's tee-range
  handoff, else ``range_m`` from the caller; the IWR slant range becomes the
  ball's depth through the rig's IWR offset and the resting ball's ray;
- the resting ball: the caller's, else the detector at the size that range
  implies, else the ladder's recorded ball;
- club speed (OPS), dynamic loft, face angle and attack from the shot record's
  labelled metrics, else the category priors of ``FaceInputs.priors``;
- the face centre: its height above the leading edge from an anchor per club
  (marked face photos), else the category's; heel to toe, the category's distance
  from the toe (``face_centre_reference.json``) unless ``--face-centre-from-toe-mm``
  overrides it for research;
- the golfer's hand from the shot's own record, else the session's ``--handed``
  record (right-handed by default), the lens's focal length from the session's lens record or the rig
  file's lens.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from openflight.ballistics import MPH_TO_MPS
from openflight.camera import impact_location as il
from openflight.camera.impact_angle_evidence import ANGLE_SIGMA_BY_STATUS, AngleEvidence

SCHEMA = "openflight.impact_location.v2"
ANCHOR_SCHEMA = "openflight.face_centre_anchors.v1"
RESTING_FRAMES = 10
CSV_FIELDS = (
    "shot_id",
    "run",
    "shot_number",
    "club",
    "handedness",
    "frame_size",
    "fps",
    "label",
    "status",
    "reason",
    "impact_offset_mm",
    "impact_offset_sigma_mm",
    "height_status",
    "height_reason",
    "impact_height_mm",
    "impact_height_sigma_mm",
    "contact_frame",
    "last_rest_frame",
    "contact_minus_trigger_ms",
    "ball_range_mm",
    "ball_range_source",
    "ball_source",
    "club_speed_source",
    "dynamic_loft_source",
    "face_centre_height_source",
    "face_centre_from_toe_source",
    "toe_blur_10_90_mm",
    "sole_blur_10_90_mm",
    "topline_blur_10_90_mm",
    "face_photo",
    "template_samples",
    "relative_path",
)


@dataclass
class Clip:  # pylint: disable=too-many-instance-attributes
    """One saved camera clip and everything its session says about it."""

    shot_id: str
    path: Path
    relative_path: str
    run_dir: Path | None = None
    shot_number: int | None = None
    fps: float | None = None
    mirrored: bool = False
    trigger_index: int | None = None
    exposure_us: float | None = None
    frame_size: str = ""
    club: str = "unknown"
    handedness: str = "right"
    club_speed_mps: float | None = None
    ball: il.RestingBall | None = None
    ball_source: str = ""
    range_mm: float | None = None
    range_sigma_mm: float | None = None
    range_source: str = "none"
    face: il.FaceInputs | None = None
    face_photo: str | None = None
    prepared: il.PreparedShot | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunRecord:
    """One run's session log, read once: its start record, shots, clips and photos."""

    start: dict[str, Any]
    shots: dict[int, dict[str, Any]]
    captures: dict[str, int]
    photos: dict[int, str]
    tee_range: dict[str, Any] | None


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
    raise FileNotFoundError(f"not a session folder or bundle zip: {path}")


def _events(log: Path) -> list[dict[str, Any]]:
    events = []
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            events.append(record)
    return events


def _name(recorded: Any) -> str | None:
    if not isinstance(recorded, str) or not recorded:
        return None
    return recorded.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def read_run(run_dir: Path) -> RunRecord:
    """The run's session log(s): shots (with their late updates), clips and face photos."""
    start: dict[str, Any] = {}
    shots: dict[int, dict[str, Any]] = {}
    captures: dict[str, int] = {}
    photos: dict[int, str] = {}
    for log in sorted(run_dir.glob("session_*.jsonl")):
        for record in _events(log):
            kind, number = record.get("type"), record.get("shot_number")
            if kind == "session_start" and not start:
                start = record
            elif kind == "shot_detected" and isinstance(number, int):
                shots[number] = dict(record)
            elif kind == "shot_updated" and isinstance(number, int) and number in shots:
                update = record.get("update")
                shots[number].update(update if isinstance(update, dict) else record)
            elif kind == "camera_capture" and isinstance(number, int):
                name = _name(record.get("capture_path"))
                if name and not record.get("capture_error"):
                    captures[name] = number
            elif kind == "face_photo" and isinstance(number, int) and record.get("path"):
                photos[number] = str(record["path"])
    for photo in sorted((run_dir / "face-photos").glob("shot-*.pgm")):
        stem = photo.stem.split("-")
        if len(stem) >= 2 and stem[1].isdigit():
            photos.setdefault(int(stem[1]), photo.relative_to(run_dir).as_posix())
    tee_range = None
    try:
        tee_range = json.loads((run_dir / "tee_range.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tee_range = None
    return RunRecord(start, shots, captures, photos, tee_range)


def find_clips(root: Path) -> list[Clip]:
    """Every ``frames.npz`` with a sibling ``metadata.json``, in path order."""
    clips = []
    for frames in sorted(root.rglob("frames.npz")):
        if ".bundles" in frames.parts or not (frames.parent / "metadata.json").is_file():
            continue
        folder = frames.parent
        run_dir = next((p for p in folder.parents if p.name.startswith("run-")), None)
        clips.append(
            Clip(folder.name, folder, folder.relative_to(root).as_posix(), run_dir=run_dir)
        )
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


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def session_handedness(start: Mapping[str, Any]) -> tuple[str, str]:
    """(right|left, source) from the session's ``--handed`` record; right by default."""
    golfer = (start.get("config") or {}).get("golfer") or {}
    handed = golfer.get("handed") if isinstance(golfer, dict) else None
    if handed == "LH":
        return "left", f"session_{golfer.get('source', 'setup')}"
    if handed == "RH":
        return "right", f"session_{golfer.get('source', 'setup')}"
    return "right", "default_right_handed"


def shot_handedness(shot: Mapping[str, Any], start: Mapping[str, Any]) -> tuple[str, str]:
    """(right|left, source): the hand the shot recorded, else the session's.

    The kiosk's hand can change mid-session, so a shot's own record comes first.
    """
    handed = shot.get("handed")
    if handed == "LH":
        return "left", "shot_record"
    if handed == "RH":
        return "right", "shot_record"
    return session_handedness(start)


def session_rig(start: Mapping[str, Any]):
    """The session's recorded rig geometry, else the repo's rig file, and its source."""
    from openflight.rig_geometry import RigGeometry, validated_rig_fields  # noqa: PLC0415

    rig = (start.get("config") or {}).get("rig_geometry") or {}
    parameters = (rig.get("snapshot") or {}).get("parameters") if isinstance(rig, dict) else None
    if isinstance(parameters, dict):
        try:
            return RigGeometry(**validated_rig_fields(parameters)), "session_rig_snapshot"
        except (TypeError, ValueError):
            pass
    default = Path(__file__).resolve().parents[3] / "config" / "enclosure_v3_rig_geometry.json"
    return RigGeometry.from_json(default), "repo_rig_file_enclosure_v3"


def focal_px(
    start: Mapping[str, Any], rig, width: int, *, lens_id: str | None = None
) -> tuple[float, str]:
    """The lens's focal length for this saved width and its source.

    The session's lens record first; else the named lens from ``config/lenses.json``
    (nominal, or its checkerboard calibration when one is kept); else the rig file's.
    """
    from openflight.camera import lens_profiles  # noqa: PLC0415
    from openflight.camera.ball_pixels import mode_focal_px  # noqa: PLC0415

    lens = ((start.get("config") or {}).get("rig_geometry") or {}).get("lens") or {}
    by_arm = lens.get("focal_px_by_arm") if isinstance(lens, dict) else None
    if isinstance(by_arm, dict):
        for arm, (arm_width, _height) in lens_profiles.ARM_SIZES.items():
            value = _number(by_arm.get(arm))
            if arm_width == width and value:
                return value, f"session_lens_{lens.get('intrinsics', 'nominal')}"
    if lens_id is not None:
        active = lens_profiles.active_lens(
            rig,
            lens_profiles.load_catalogue(),
            lens_id,
            [lens_profiles.default_calibration_dir()],
        )
        return active.focal_px(width), f"lens_profile_{lens_id}_{active.intrinsics}"
    return mode_focal_px(width, rig), "rig_file_lens_nominal"


def ball_depth_mm(
    rig, ball: il.RestingBall, camera: il.ImpactCamera, slant_range_mm: float
) -> float | None:
    """The resting ball's depth ahead of the lens: its ray meets the IWR's range sphere."""
    ray = np.array(
        [
            (ball.x - camera.principal_point_px[0]) / camera.focal_px,
            (ball.y - camera.principal_point_px[1]) / camera.focal_px,
            1.0,
        ]
    )
    ray /= np.linalg.norm(ray)
    offset = np.asarray(rig.iwr_offset_mm or (0.0, 0.0, 0.0), dtype=float)
    b = float(ray @ offset)
    discriminant = b * b - float(offset @ offset) + slant_range_mm**2
    if discriminant <= 0.0:
        return None
    return float((b + math.sqrt(discriminant)) * ray[2])


def shot_range_m(
    shot: Mapping[str, Any], run: RunRecord, given_m: float | None
) -> tuple[float | None, float | None, str]:
    """(IWR slant range m, its sigma m, source) for one shot, in the documented order."""
    value = _number(shot.get("ball_range_m"))
    source = str(shot.get("ball_range_source") or "")
    if value and not source.startswith("camera"):
        return value, _number(shot.get("ball_range_uncertainty_m")), f"shot_{source or 'iwr'}"
    selected = _number((run.tee_range or {}).get("selected_range_m"))
    if selected:
        sigma = _number((run.tee_range or {}).get("selected_uncertainty_m"))
        return selected, sigma, "run_tee_range_selected"
    config = run.start.get("config") or {}
    handoff = config.get("tee_range_handoff") or {}
    for key in ("tee_slant_range_m", "range_m"):
        value = _number(handoff.get(key)) if isinstance(handoff, dict) else None
        if value and "camera" not in str(handoff.get("source", "")):
            return value, None, f"session_tee_range_handoff_{handoff.get('source', 'unknown')}"
    if given_m:
        return float(given_m), None, "command_line"
    return None, None, "none"


def shot_face_inputs(
    shot: Mapping[str, Any],
    club: str,
    handedness: str,
    start: Mapping[str, Any],
    *,
    lens_height_mm: float | None,
    anchors: Mapping[str, il.Labelled] | None = None,
    capture_npz_sha256: str | None = None,
    capture_sensor_interval_ns: tuple[int, int] | None = None,
    decisions: dict[str, Any] | None = None,
) -> il.FaceInputs:
    """Use explicitly unconditioned, capture-bound angles; retain priors otherwise."""
    face = il.FaceInputs.priors(club, lens_height_mm=lens_height_mm)
    changes: dict[str, il.Labelled] = {}
    metrics = shot.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    for name in ("dynamic_loft", "attack_angle", "face_angle"):
        entry = metrics.get(name)
        reason = "angle_not_recorded"
        evidence = None
        if isinstance(entry, Mapping):
            reason = "angle_status_not_eligible"
            if isinstance(entry.get("status"), str) and entry["status"] in ANGLE_SIGMA_BY_STATUS:
                try:
                    evidence = AngleEvidence.from_metric(
                        name,
                        entry,
                        shot_number=shot.get("shot_number"),
                        capture_npz_sha256=capture_npz_sha256,
                        capture_sensor_interval_ns=capture_sensor_interval_ns,
                    )
                except ValueError as exc:
                    reason = str(exc)
        if evidence is not None:
            value = evidence.value_deg
            if name == "face_angle":
                offset = _number(
                    ((start.get("config") or {}).get("target_line") or {}).get(
                        "horizontal_offset_deg"
                    )
                )
                if evidence.frame == "target_line" and offset is None:
                    evidence, reason = None, "target_line_transform_unknown"
                else:
                    value -= offset if evidence.frame == "target_line" else 0.0
                    value = value if handedness == "right" else -value
            if evidence is not None:
                changes[name] = il.Labelled(
                    value,
                    max(ANGLE_SIGMA_BY_STATUS[evidence.status], evidence.sigma_deg),
                    evidence.source,
                    evidence,
                )
        if decisions is not None:
            decisions[name] = {
                "accepted": name in changes,
                "reason": None if name in changes else reason,
                "producer": None
                if not isinstance(entry, Mapping)
                else {
                    "value": _number(entry.get("value")),
                    **{
                        key: entry.get(key) if isinstance(entry.get(key), str) else None
                        for key in ("source", "status", "unit", "frame", "note")
                    },
                },
                "evidence": evidence.as_dict() if evidence is not None else None,
            }
    anchor = (anchors or {}).get(club)
    if anchor is not None:
        changes["face_centre_height"] = anchor
    return face.with_changes(**changes)


def read_anchors(path: Path | None) -> dict[str, il.Labelled]:
    """Per-club face-centre heights (mm along the face above the physical leading edge).

    The centre is the one ``impact_location.FACE_CENTRE_CONVENTION`` names; the
    leading edge is the head's lowest point seen along the face normal, not the
    face's own bottom edge.
    """
    if path is None:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != ANCHOR_SCHEMA:
        raise ValueError(f"{path} is not a {ANCHOR_SCHEMA} file")
    anchors = {}
    for club, entry in (data.get("clubs") or {}).items():
        value, sigma = _number(entry.get("value_mm")), _number(entry.get("sigma_mm"))
        if value is not None and sigma is not None:
            anchors[club] = il.Labelled(value, sigma, str(entry.get("source", "face_photo_marks")))
    return anchors


@dataclass
class Options:  # pylint: disable=too-many-instance-attributes
    """What the caller overrides; everything else comes from the session."""

    handedness: str | None = None
    club: str | None = None
    club_speed_mps: float | None = None
    range_m: float | None = None
    contact_fraction: float | None = None
    ball: tuple[float, float, float] | None = None
    anchors: dict[str, il.Labelled] = field(default_factory=dict)
    face_centre_from_toe_mm: float | None = None
    shots: tuple[str, ...] = ()
    template_scope: str = "club"
    lens_id: str | None = None
    analysis_max_width: int | None = None


def bin_frames(frames: np.ndarray, factor: int) -> np.ndarray:
    """Each ``factor`` x ``factor`` block of pixels averaged into one (the clip as is for 1)."""
    if factor <= 1:
        return frames
    count, height, width = frames.shape
    rows, cols = height // factor, width // factor
    blocks = frames[:, : rows * factor, : cols * factor].reshape(count, rows, factor, cols, factor)
    total = blocks.sum(axis=(2, 4), dtype=np.uint32)
    return ((total + factor * factor // 2) // (factor * factor)).astype(np.uint8)


def _load_frames(clip: Clip) -> tuple[np.ndarray | None, dict[str, Any]]:
    clip.extra.pop("capture_npz_sha256", None)
    clip.extra.pop("capture_sensor_interval_ns", None)
    try:
        metadata = json.loads((clip.path / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        metadata = {}
    try:
        raw_path = clip.path / "frames.npz"
        hasher = hashlib.sha256()
        with raw_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
            clip.extra["capture_npz_sha256"] = hasher.hexdigest()
            handle.seek(0)
            with np.load(handle, allow_pickle=False) as data:
                frames = np.asarray(data["frames"], dtype=np.uint8)
                if "pre_trigger_count" in data.files:
                    clip.trigger_index = int(data["pre_trigger_count"])
                if "exposure_us" in data.files:
                    clip.exposure_us = float(np.median(data["exposure_us"]))
                stamps = (
                    np.asarray(data["sensor_timestamp_ns"])
                    if "sensor_timestamp_ns" in data.files
                    else None
                )
    except (OSError, ValueError, KeyError) as exc:
        clip.error = f"unreadable_frames_npz: {exc}"
        return None, metadata
    if frames.ndim != 3:
        clip.error = "frames_npz_is_not_a_clip"
        return None, metadata
    if (
        stamps is not None
        and stamps.ndim == 1
        and len(stamps) == len(frames)
        and stamps.dtype.kind in "iu"
        and len(stamps) > 0
        and int(stamps[0]) > 0
        and np.all(np.diff(stamps.astype(object)) > 0)
    ):
        clip.extra["capture_sensor_interval_ns"] = (int(stamps[0]), int(stamps[-1]))
    fps = _number(metadata.get("delivered_fps")) or 0.0
    if fps <= 0.0 and stamps is not None and stamps.size > 2:
        interval = float(np.median(np.diff(stamps.astype(np.float64))))
        fps = 1e9 / interval if interval > 0 else 0.0
    clip.fps = fps if fps > 0 else None
    settings = metadata.get("settings") if isinstance(metadata.get("settings"), dict) else {}
    clip.mirrored = settings.get("mirror_horizontal") is True
    if clip.exposure_us is None:
        clip.exposure_us = _number(settings.get("exposure_us"))
    if clip.trigger_index is None and isinstance(metadata.get("pre_trigger_frames"), int):
        clip.trigger_index = int(metadata["pre_trigger_frames"])
    clip.frame_size = f"{frames.shape[2]}x{frames.shape[1]}"
    return frames, metadata


def _find_ball(clip: Clip, frames, camera, rig, range_m, ladder) -> il.RestingBall | None:
    from openflight.camera.club_motion import detect_reference_ball  # noqa: PLC0415

    first = frames[: max(3, min(RESTING_FRAMES, len(frames)))]
    if range_m:
        expected = camera.focal_px * il.BALL_DIAMETER_MM / (range_m * 1000.0)
        try:
            found = detect_reference_ball(first, expected_diameter_px=expected)
            clip.ball_source = "detector_at_the_range_size"
            return il.RestingBall(found.x, found.y, found.diameter_px)
        except ValueError as exc:
            clip.extra["detector_at_range"] = str(exc)
    known = ladder.get(clip.shot_id)
    if known is not None:
        clip.ball_source = "ladder_verdict"
        return il.RestingBall(float(known["x"]), float(known["y"]), float(known["diameter_px"]))
    try:
        found = detect_reference_ball(first)
    except ValueError as exc:
        clip.error = f"resting_ball_not_found: {exc}"
        return None
    clip.ball_source = "detector"
    return il.RestingBall(found.x, found.y, found.diameter_px)


def prepare_clip(  # pylint: disable=too-many-locals
    clip: Clip, runs: dict[Path, RunRecord], ladder: dict[str, dict], options: Options
) -> None:
    """Fill the clip's inputs from its session and prepare it, or record why not."""
    frames, _metadata = _load_frames(clip)
    if frames is None:
        return
    if options.analysis_max_width and frames.shape[2] > options.analysis_max_width:
        clip.extra["recorded_frame_size"] = clip.frame_size
        frames = bin_frames(frames, math.ceil(frames.shape[2] / options.analysis_max_width))
        clip.frame_size = f"{frames.shape[2]}x{frames.shape[1]}"
    run = runs.get(clip.run_dir) if clip.run_dir is not None else None
    run = run or RunRecord({}, {}, {}, {}, None)
    clip.shot_number = run.captures.get(clip.shot_id)
    shot = run.shots.get(clip.shot_number, {}) if clip.shot_number is not None else {}
    handed, handed_source = shot_handedness(shot, run.start)
    clip.handedness = options.handedness or handed
    clip.extra["handedness_source"] = "command_line" if options.handedness else handed_source
    clip.club = options.club or str(shot.get("club") or "unknown")
    speed_mph = _number(shot.get("club_speed_mph"))
    if speed_mph:
        clip.club_speed_mps = speed_mph * MPH_TO_MPS
        clip.extra["club_speed_source"] = "shot_ops_club_speed"
    elif options.club_speed_mps:
        clip.club_speed_mps = float(options.club_speed_mps)
        clip.extra["club_speed_source"] = "command_line"
    else:
        clip.extra["club_speed_source"] = "assumed_default"
    if clip.shot_number is not None:
        clip.face_photo = run.photos.get(clip.shot_number)
    if clip.fps is None:
        clip.error = "frame_rate_unknown"
        return
    rig, rig_source = session_rig(run.start)
    height, width = frames.shape[1:]
    focal, focal_source = focal_px(run.start, rig, int(width), lens_id=options.lens_id)
    camera = il.ImpactCamera(focal, ((width - 1) / 2.0, (height - 1) / 2.0), focal_source)
    clip.extra["rig_source"] = rig_source
    range_m, sigma_m, clip.range_source = shot_range_m(shot, run, options.range_m)
    if options.ball is not None:
        clip.ball, clip.ball_source = il.RestingBall(*options.ball), "command_line"
    else:
        clip.ball = _find_ball(clip, frames, camera, rig, range_m, ladder)
    if clip.ball is None:
        return
    if range_m:
        clip.range_mm = ball_depth_mm(rig, clip.ball, camera, range_m * 1000.0)
        clip.range_sigma_mm = (sigma_m or il.DEFAULT_RANGE_SIGMA_MM / 1000.0) * 1000.0
    lens_height = _number(getattr(rig, "lens_height_above_floor_mm", None))
    clip.face = shot_face_inputs(
        shot,
        clip.club,
        clip.handedness,
        run.start,
        lens_height_mm=lens_height,
        anchors=options.anchors,
        capture_npz_sha256=clip.extra.get("capture_npz_sha256"),
        capture_sensor_interval_ns=clip.extra.get("capture_sensor_interval_ns"),
        decisions=clip.extra.setdefault("angle_input_decisions", {}),
    )
    clip.prepared = il.prepare_shot(
        frames,
        clip.ball,
        fps=clip.fps,
        camera=camera,
        handedness=clip.handedness,
        frames_mirrored=clip.mirrored,
        club_speed_mps=clip.club_speed_mps,
        ball_range_mm=clip.range_mm,
        ball_range_sigma_mm=clip.range_sigma_mm,
        ball_range_source=clip.range_source,
        contact_fraction=options.contact_fraction,
        exposure_us=clip.exposure_us,
        name=clip.shot_id,
        club=clip.club,
    )
    from openflight.camera.dynamic_lie import estimate_dynamic_lie  # noqa: PLC0415

    clip.extra["dynamic_lie"] = estimate_dynamic_lie(
        clip.prepared, pitch_deg=rig.boresight_pitch_deg, geometry_source=rig_source
    )


def _blur(location: il.ImpactLocation, edge: str) -> float | None:
    return ((location.diagnostics.get("edge_blur") or {}).get("measured_10_90_mm") or {}).get(edge)


def result_row(
    clip: Clip, location: il.ImpactLocation | None, template: il.HeadTemplate | None
) -> dict[str, Any]:
    """One CSV/JSON row: both axes, both sigmas, the label and why when refused."""
    face = clip.face
    row: dict[str, Any] = {
        "shot_id": clip.shot_id,
        "run": clip.run_dir.name if clip.run_dir is not None else None,
        "shot_number": clip.shot_number,
        "club": clip.club,
        "handedness": clip.handedness,
        "frame_size": clip.frame_size,
        "fps": clip.fps,
        "label": il.EXPERIMENTAL_LABEL,
        "ball_range_mm": clip.range_mm,
        "ball_range_source": clip.range_source,
        "ball_source": clip.ball_source,
        "club_speed_source": clip.extra.get("club_speed_source"),
        "dynamic_loft_source": face.dynamic_loft.source if face else None,
        "face_centre_height_source": face.face_centre_height.source if face else None,
        "face_centre_from_toe_source": face.face_centre_from_toe.source if face else None,
        "face_photo": clip.face_photo,
        "relative_path": clip.relative_path,
        "dynamic_lie": clip.extra.get("dynamic_lie"),
        "angle_input_decisions": clip.extra.get("angle_input_decisions", {}),
    }
    if location is None:
        reason = clip.error or "not_analysed"
        row.update(status="rejected", reason=reason, height_status="rejected", height_reason=reason)
        return row
    from_toe = (location.diagnostics.get("inputs") or {}).get("face_centre_from_toe")
    if from_toe:
        row["face_centre_from_toe_source"] = from_toe["source"]
    row.update(
        status=location.status,
        reason=location.reason,
        impact_offset_mm=location.impact_offset_mm,
        impact_offset_sigma_mm=location.impact_offset_sigma_mm,
        height_status=location.height_status,
        height_reason=location.height_reason if location.status == "ok" else location.reason,
        impact_height_mm=location.impact_height_mm,
        impact_height_sigma_mm=location.impact_height_sigma_mm,
        contact_frame=location.contact_frame,
        last_rest_frame=location.last_rest_frame,
        toe_blur_10_90_mm=_blur(location, "toe"),
        sole_blur_10_90_mm=_blur(location, "sole"),
        topline_blur_10_90_mm=_blur(location, "topline"),
        template_samples=template.n_samples if template is not None else None,
    )
    if location.contact_time_frames is not None and clip.trigger_index is not None and clip.fps:
        row["contact_minus_trigger_ms"] = (
            (location.contact_time_frames - clip.trigger_index) * 1000.0 / clip.fps
        )
    return row


def _group(clip: Clip, scope: str) -> str:
    return clip.frame_size if scope == "session" else f"{clip.club}_{clip.frame_size}"


def analyse_session(
    root: Path, options: Options | None = None, *, overlays: Path | None = None
) -> dict[str, Any]:
    """Every clip of the session read on both axes; the report as a JSON-ready dict."""
    options = options or Options()
    centre = "the category's distance from the toe"
    if options.face_centre_from_toe_mm is not None:
        centre = (
            f"{options.face_centre_from_toe_mm:.1f} mm heel-ward of the toe (research override)"
        )
    clips = find_clips(root)
    if options.shots:
        clips = [c for c in clips if any(s in c.relative_path for s in options.shots)]
    runs = {c.run_dir: read_run(c.run_dir) for c in clips if c.run_dir is not None}
    ladder = _ladder_balls(root)
    for clip in clips:
        prepare_clip(clip, runs, ladder, options)
    groups: dict[str, list[Clip]] = {}
    for clip in clips:
        if clip.prepared is not None:
            groups.setdefault(_group(clip, options.template_scope), []).append(clip)
    templates: dict[str, il.HeadTemplate | None] = {}
    template_notes: dict[str, Any] = {}
    for key, members in groups.items():
        template, reason = il.build_self_template([c.prepared for c in members], club=key)
        templates[key] = template
        template_notes[key] = template.summary() if template else {"reason": reason}
    rows, details = [], []
    for clip in clips:
        if clip.prepared is None:
            rows.append(result_row(clip, None, None))
            continue
        template = templates.get(_group(clip, options.template_scope))
        location = il.locate_impact(
            clip.prepared,
            template,
            face=clip.face,
            face_centre_from_toe_mm=options.face_centre_from_toe_mm,
        )
        rows.append(result_row(clip, location, template))
        details.append({"shot_id": clip.shot_id, **location.as_dict()})
        if overlays is not None:
            _save_png(
                overlays / f"{clip.shot_id}.png",
                il.render_overlay(clip.prepared, template, location),
            )
        clip.prepared = replace(clip.prepared, frames=clip.prepared.frames[:0])
    return {
        "schema": SCHEMA,
        "label": il.EXPERIMENTAL_LABEL,
        "caveat": (
            "EXPERIMENTAL, UNQUALIFIED post-session analysis; not shown on the kiosk. "
            f"Face centre: {il.FACE_CENTRE_CONVENTION}. Heel-toe: {centre}. Height: per-club "
            "anchor from marked face photos, else the category's."
        ),
        "session": str(root),
        "templates": template_notes,
        "rows": rows,
        "details": details,
    }


def _save_png(path: Path, image: np.ndarray) -> None:
    import cv2  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)  # pylint: disable=no-member


def _fmt(value: Any) -> Any:
    return round(value, 3) if isinstance(value, float) and math.isfinite(value) else value


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return str(value)


def write_report(report: Mapping[str, Any], out: Path) -> tuple[Path, Path]:
    """``impact_location.csv`` (one row per clip) and ``impact_location.json``."""
    out.mkdir(parents=True, exist_ok=True)
    csv_path, json_path = out / "impact_location.csv", out / "impact_location.json"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in report["rows"]:
            writer.writerow({k: _fmt(row.get(k)) for k in CSV_FIELDS})
    json_path.write_text(
        json.dumps(report, indent=2, default=_json_default) + "\n", encoding="utf-8"
    )
    return csv_path, json_path


def summary_lines(report: Mapping[str, Any]) -> list[str]:
    """What the console prints: counts, then every refusal reason with its count."""
    rows = report["rows"]
    offsets = [r for r in rows if r.get("status") == "ok"]
    heights = [r for r in rows if r.get("height_status") == "ok"]
    lines = [
        f"{len(offsets)} of {len(rows)} clips read heel-toe, {len(heights)} read height "
        "(EXPERIMENTAL, unqualified; + toe, + up)"
    ]
    reasons: dict[str, int] = {}
    for row in rows:
        if row.get("status") != "ok":
            reasons[str(row.get("reason"))] = reasons.get(str(row.get("reason")), 0) + 1
        elif row.get("height_status") != "ok":
            key = f"height: {row.get('height_reason')}"
            reasons[key] = reasons.get(key, 0) + 1
    for reason, count in sorted(reasons.items(), key=lambda item: -item[1]):
        lines.append(f"  refused {count:3d}  {reason}")
    return lines
