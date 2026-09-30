#!/usr/bin/env python3
"""Replay a whole tester session through the production chain and report every stage (P8-6).

    uv run --extra camera python scripts/analysis/check_pipeline.py <tester folder> \\
        [--inject-tee-range M] [--inject-setup-ball X,Y,D] [--inject-box X0,Y0,X1,Y1] \\
        [--output check.json]

For every shot of every run it replays:

- the kiosk's own code (``server._process_iwr6843_angle``,
  ``server._fuse_camera_measurements``, ``server._attach_experimental_face_angle``)
  with the module's hardware objects replaced by the replayed IWR result and the
  saved clip, so the hand-offs are the ones the Pi makes;
- the review path (``replay_raw_fusion.replay`` and ``review_metrics``), which the
  one-button analysis job runs.

It prints one table per shot (``openflight.pipeline_check``) and writes it as JSON.
Sessions captured before the setup could save a range or a box can be replayed
with injected setup values; every injected value is printed and marked.
Nothing in the session folder is written.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import math
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Mapping

from openflight import pipeline_check as check
from openflight.camera.capture_runtime import (
    CameraCaptureRuntime,
    parse_hitting_zone,
    parse_setup_ball,
)
from openflight.camera.club_delivery import ReferenceBallTracker
from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs
from openflight.clubs import ClubType
from openflight.raw_radar_replay import load_session_events, locate_recorded_capture
from openflight.review_metrics import mapping, review_replay
from openflight.session_review import camera_outcome

try:
    from scripts.analysis import replay_raw_fusion as raw_replay
    from scripts.analysis.analyze_tester_session import _replay_args
except ModuleNotFoundError:  # Direct execution places scripts/analysis first on sys.path.
    import replay_raw_fusion as raw_replay  # type: ignore[no-redef]
    from analyze_tester_session import _replay_args  # type: ignore[no-redef]

INJECTED_SOURCE = "injected_by_pipeline_check"
# Gates the check can step past to show what the next gate does (read-only; labelled).
BYPASSABLE_GATES = ("lighting", "optical_quality")


class Injection(SimpleNamespace):
    """Setup values the operator supplies for a session that never recorded them."""

    tee_range_m: float | None = None
    setup_ball: dict | None = None
    box: tuple[int, int, int, int] | None = None
    bypass: frozenset = frozenset()

    @property
    def active(self) -> bool:
        """Whether anything is injected."""
        return any(item is not None for item in (self.tee_range_m, self.setup_ball, self.box))

    def as_dict(self) -> dict[str, Any]:
        """What was injected, for the report."""
        return {
            "tee_range_m": self.tee_range_m,
            "setup_ball": self.setup_ball,
            "box": list(self.box) if self.box else None,
        }

    def bypasses(self, gate: str) -> bool:
        """Whether the operator asked the check to step past this gate."""
        return gate in self.bypass


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def virtual_session(frozen: tuple, injection: Injection) -> tuple:
    """The recorded session with the injected setup applied where the setup hands it over.

    The tee range goes where ``tester_server`` hands it to the kiosk
    (``tee_range_handoff``, ``iwr6843.tee_slant_range_m``) and where the kiosk
    records it per shot (the IWR runtime snapshot, rehashed); the setup ball and
    box go where ``--camera-setup-ball`` and ``--camera-hitting-zone`` put them.
    """
    session_hash, _start, events = frozen
    events = copy.deepcopy(events)
    start = next(event for event in events if event.get("type") == "session_start")
    if not injection.active:
        return session_hash, start, events
    config = start.setdefault("config", {})
    camera = config.setdefault("camera_capture", {})
    if injection.setup_ball is not None:
        camera["setup_ball"] = dict(injection.setup_ball)
    if injection.box is not None:
        camera["hitting_zone"] = list(injection.box)
    if injection.tee_range_m is not None:
        config["tee_range_handoff"] = {
            "tee_slant_range_m": injection.tee_range_m,
            "status": "configured",
            "source": INJECTED_SOURCE,
            "candidate_id": None,
        }
        config.setdefault("iwr6843", {})["tee_slant_range_m"] = injection.tee_range_m
        for event in events:
            runtime = event.get("runtime_config")
            if event.get("type") != "iwr6843_capture" or not isinstance(runtime, dict):
                continue
            effective = mapping(mapping(runtime.get("calibration")).get("effective"))
            if not effective:
                continue
            runtime["calibration"]["effective"]["tee_slant_range_m"] = injection.tee_range_m
            runtime.pop("sha256", None)
            runtime["sha256"] = _canonical_sha256(runtime)
            event["runtime_config_sha256"] = runtime["sha256"]
    config["pipeline_check_injection"] = injection.as_dict()
    return session_hash, start, events


# ---------------------------------------------------------------- IWR tap


class _IwrTap:
    """Keep the replay's IWR objects, and run the estimator once per dump and calibration."""

    def __init__(self):
        self.results: dict[str, tuple] = {}
        self.last: tuple | None = None
        self.recomputed_context: dict | None = None

    @contextmanager
    def installed(self) -> Iterator[None]:
        """Wrap the replay's IWR estimator and its rebuilt-context camera call."""
        original_iwr = raw_replay.replay_iwr_capture_bytes
        original_camera = raw_replay.process_camera_fusion

        def tapped_iwr(raw, calibration, **kwargs):
            key = hashlib.sha256(
                raw + repr((vars(calibration), sorted(kwargs.items()))).encode("utf-8")
            ).hexdigest()
            if key not in self.results:
                self.results[key] = (*original_iwr(raw, calibration, **kwargs), calibration)
            self.last = self.results[key]
            return self.last[:2]

        def tapped_camera(context, archive):
            # only the recomputed_radar_context call runs in this module's namespace
            self.recomputed_context = dict(context)
            return original_camera(context, archive)

        raw_replay.replay_iwr_capture_bytes = tapped_iwr
        raw_replay.process_camera_fusion = tapped_camera
        try:
            yield
        finally:
            raw_replay.replay_iwr_capture_bytes = original_iwr
            raw_replay.process_camera_fusion = original_camera


# ---------------------------------------------------------------- kiosk chain


class _ReplayedIwrRuntime:
    """What ``server.iwr6843_runtime`` returns, from the replayed dump."""

    def __init__(self, calibration, result, runtime_config: Mapping[str, Any], matched: bool):
        self.calibration = calibration
        self.horizontal_phase_reference_rad = runtime_config.get("horizontal_phase_reference_rad")
        self._result = result
        self._matched = matched

    def process_shot(self, **_kwargs):
        """The replayed capture, measurement and club path (or the tee withholding)."""
        capture = (
            SimpleNamespace(
                valid=True,
                error=None,
                sequence=0,
                trigger_timestamp=None,
                path=None,
                raw=b"",
                dump_duration_s=None,
                temperature_report=None,
            )
            if self._matched
            else None
        )
        measurement, club_path = (self._result or (None, None))[:2]
        withheld = (
            "tee_range_unresolved"
            if self._matched and self.calibration.tee_range_m is None
            else None
        )
        return SimpleNamespace(
            capture=capture, measurement=measurement, club_path=club_path, withheld_reason=withheld
        )


@contextmanager
def _kiosk(server, **module_state) -> Iterator[Any]:
    """Give the kiosk module the replayed hardware for one shot, then restore it."""
    saved = {name: getattr(server, name) for name in module_state}
    try:
        for name, value in module_state.items():
            setattr(server, name, value)
        yield server
    finally:
        for name, value in saved.items():
            setattr(server, name, value)


def _effective_calibration(runtime_config: Mapping[str, Any]):
    """The runtime calibration's geometry scalars when no dump was replayed."""
    effective = mapping(mapping(runtime_config.get("calibration")).get("effective"))
    return SimpleNamespace(
        tee_range_m=effective.get("tee_slant_range_m"),
        radar_height_m=effective.get("radar_height_m"),
        tee_ball_height_m=effective.get("ball_height_m"),
    )


def _lighting(
    metadata: Mapping[str, Any],
    camera_config: Mapping[str, Any],
    frames,
    injection: Injection,
) -> tuple[dict, dict]:
    """Capture-time eligibility as recorded, or re-judged on an injected setup ball (P7-8)."""
    recorded = metadata.get("auto_exposure")
    auto_exposure = dict(recorded) if isinstance(recorded, Mapping) else None
    source = "recorded"
    manual = not camera_config.get("auto_exposure_enabled", False)
    if (
        auto_exposure is not None
        and frames is not None
        and (injection.setup_ball is not None or injection.box is not None)
    ):
        settings = SimpleNamespace(
            setup_ball=camera_config.get("setup_ball"),
            auto_exposure=not manual,
            hitting_zone=tuple(camera_config["hitting_zone"])
            if camera_config.get("hitting_zone")
            else None,
        )
        # the same method the capture runtime runs when it saves a clip
        auto_exposure = CameraCaptureRuntime._judged_on_setup_ball(  # pylint: disable=protected-access
            SimpleNamespace(settings=settings), auto_exposure, frames
        )
        source = "rejudged_with_injected_setup_ball"
    if auto_exposure is None:
        return {"eligible": True, "rule": "no_recorded_judgement", "source": source}, metadata
    eligibility = mapping(auto_exposure.get("analysis_eligibility"))
    observation = mapping(auto_exposure.get("observation"))
    ball = mapping(eligibility.get("ball"))
    if eligibility:
        reason = eligibility.get("reason") or (
            f"ball {ball.get('clipped_pct')}% clipped, {ball.get('signal_dn')} DN above black"
        )
    else:
        reason = (
            f"{observation.get('message')}; {observation.get('clipped_pct')}% of the zone clipped"
            if observation
            else None
        )
    judgement = {
        "eligible": bool(auto_exposure.get("analysis_eligible")),
        "rule": eligibility.get("rule") or "zone",
        "reason": reason,
        "source": source,
        "manual_exposure": manual,
        "ball": eligibility.get("ball"),
    }
    if injection.bypasses("optical_quality"):
        # without a recorded judgement the kiosk runs neither this gate nor optical quality
        judgement["bypassed"] = not judgement["eligible"]
        return judgement, {k: v for k, v in metadata.items() if k != "auto_exposure"}
    if injection.bypasses("lighting") and not judgement["eligible"]:
        judgement["bypassed"] = True
        auto_exposure = {**auto_exposure, "analysis_eligible": True}
    return judgement, {**metadata, "auto_exposure": auto_exposure}


def kiosk_chain(  # pylint: disable=too-many-arguments,too-many-locals
    *,
    server,
    config: Mapping[str, Any],
    session_uuid: str,
    shot_event: Mapping[str, Any],
    camera_event: Mapping[str, Any],
    clip_dir: Path | None,
    iwr_event: Mapping[str, Any],
    iwr_result: tuple | None,
    ops_result: Mapping[str, Any],
    club: str,
    trackers: tuple[ReferenceBallTracker, ReferenceBallTracker],
    injection: Injection,
) -> dict[str, Any]:
    """Run the kiosk's IWR hand-off, camera fusion and face angle for one shot."""
    from openflight.launch_monitor import Shot  # noqa: PLC0415

    runtime_config = mapping(iwr_event.get("runtime_config"))
    calibration = (
        iwr_result[2] if iwr_result is not None else _effective_calibration(runtime_config)
    )
    camera_config = dict(mapping(config.get("camera_capture")))
    metadata = dict(mapping(camera_event.get("metadata")))
    capture = None
    archive = None
    if clip_dir is not None:
        capture = SimpleNamespace(valid=True, path=clip_dir, metadata=metadata, error=None)
        archive = server._load_camera_capture_archive(capture)  # pylint: disable=protected-access
    judgement, metadata = _lighting(
        metadata, camera_config, archive["frames"] if archive else None, injection
    )
    if capture is not None:
        capture.metadata = metadata
    shot = Shot(
        ball_speed_mph=float(ops_result.get("ball_speed_mph") or shot_event.get("ball_speed_mph")),
        timestamp=datetime.now(),
        shot_number=shot_event.get("shot_number"),
        impact_timestamp=shot_event.get("impact_timestamp"),
        club_speed_mph=ops_result.get("club_speed_mph"),
        club=ClubType(club),
    )
    shot.inclinometer = shot_event.get("inclinometer")
    shot.camera_fusion_session_uuid = session_uuid
    geometry_snapshot = geometry_error = None
    try:
        geometry_snapshot = EffectiveCameraGeometryInputs.from_live(
            camera_config, calibration
        ).snapshot()
    except (AttributeError, TypeError, ValueError) as error:
        geometry_error = str(error)
    runtime = _ReplayedIwrRuntime(
        calibration,
        iwr_result,
        runtime_config,
        matched=bool(iwr_event) and not iwr_event.get("capture_error"),
    )
    tester_required = bool(mapping(metadata.get("tester_setup")).get("required"))
    with _kiosk(
        server,
        iwr6843_runtime=runtime,
        camera_capture_runtime=None,
        camera_capture_config=camera_config,
        camera_reference_ball_tracker=trackers[1],
        camera_ball_flight_reference_tracker=trackers[0],
        camera_optical_calibration=None,
        camera_placement=None,
        tester_setup_required=tester_required,
    ):
        server._process_iwr6843_angle(shot)  # pylint: disable=protected-access
        server._fuse_camera_measurements(shot, capture, archive)  # pylint: disable=protected-access
        server._attach_experimental_face_angle(shot)  # pylint: disable=protected-access
    return {
        "context": shot.camera_fusion_context,
        "processing": getattr(shot, "camera_fusion_processing", None),
        "lighting": judgement,
        "geometry_snapshot": geometry_snapshot,
        "geometry_error": geometry_error,
        "face_angle_status": shot.experimental_face_angle_status,
        "face_angle_deg": shot.experimental_face_angle_deg,
        "face_angle_path_source": shot.experimental_face_angle_path_source,
        "face_angle_launch_source": shot.experimental_face_angle_launch_source,
        "launch_angle_horizontal_status": shot.launch_angle_horizontal_status,
        "fused_status": shot.experimental_fused_status,
    }


# ---------------------------------------------------------------- one run


def _runs(tester_dir: Path) -> list[tuple[str, Path]]:
    return [
        (arm.name, run)
        for arm in sorted(p for p in tester_dir.iterdir() if p.is_dir() and (p / "paired").is_dir())
        for run in sorted(
            p for p in (arm / "paired").glob("run-*") if p.is_dir() and not p.is_symlink()
        )
    ]


def _clip_dir(camera_event: Mapping[str, Any], session_dir: Path) -> Path | None:
    recorded = camera_event.get("capture_path")
    if not isinstance(recorded, str) or camera_event.get("capture_error"):
        return None
    try:
        found, _resolution = locate_recorded_capture(recorded, session_dir)
    except (FileNotFoundError, ValueError):
        return None
    found = found if found.is_dir() else found.parent
    return found if (found / "frames.npz").is_file() else None


def _replay_report(session: Path, frozen: tuple, shot: int, root: Path, *, camera: bool) -> dict:
    args = _replay_args(session, shot, root)
    args.camera = camera
    return raw_replay.replay(args, frozen_session=frozen)


def _shot_rows(  # pylint: disable=too-many-arguments,too-many-locals
    *,
    server,
    run_dir: Path,
    session: Path,
    frozen: tuple,
    number: int,
    setup_rows: list[dict],
    trackers,
    injection: Injection,
    root: Path,
    tap: _IwrTap,
) -> dict[str, Any]:
    _hash, start, events = frozen
    config = mapping(start.get("config"))
    shot_events = [e for e in events if e.get("shot_number") == number]
    shot_event = next((e for e in shot_events if e.get("type") == "shot_detected"), None)
    captures = [e for e in shot_events if e.get("type") == "camera_capture"]
    camera_event = next((e for e in captures if not e.get("capture_error")), {})
    iwr_event = next((e for e in shot_events if e.get("type") == "iwr6843_capture"), {})
    clip_dir = _clip_dir(camera_event, run_dir)
    trigger_setup = mapping(mapping(camera_event.get("metadata")).get("tester_setup"))
    rows = {row["id"]: row for row in setup_rows}

    tap.last = None
    with tap.installed():
        first = _replay_report(session, frozen, number, root, camera=False)
    iwr_result = tap.last
    ops_stage = mapping(first["stages"].get("ops"))
    ops_result = mapping(ops_stage.get("result"))
    live: dict[str, Any] = {}
    ran_kiosk = shot_event is not None and ops_result.get("ball_speed_mph") is not None
    if ran_kiosk:
        live = kiosk_chain(
            server=server,
            config=config,
            session_uuid=start["session_uuid"],
            shot_event=shot_event,
            camera_event=camera_event,
            clip_dir=clip_dir,
            iwr_event=iwr_event,
            iwr_result=iwr_result,
            ops_result=ops_result,
            club=first["replay_inputs"]["club"],
            trackers=trackers,
            injection=injection,
        )
    recorded_context = mapping(mapping(shot_event).get("camera_fusion_context"))
    rebuilt = ran_kiosk and (
        injection.active or bool(injection.bypass) or recorded_context.get("available") is not True
    )
    if rebuilt and mapping(live.get("context")).get("available") is True:
        # the review path replays the context the kiosk would have frozen
        shot_event["camera_fusion_context"] = live["context"]
        shot_event["camera_fusion_processing"] = live["processing"]
    live["context_source"] = "rebuilt by the kiosk path" if rebuilt else "recorded"
    if not rebuilt and recorded_context:
        live["kiosk_context"] = live.get("context")
        live["context"] = recorded_context
    tap.recomputed_context = None
    with tap.installed():
        report = _replay_report(session, frozen, number, root, camera=True)
    reviewed = review_replay(
        report, mapping(shot_event), tee_range=mapping(config.get("tee_range_handoff")) or None
    )

    rows["ops_shot"] = check.ops_shot(mapping(report["stages"].get("ops")))
    rows["trigger_evidence"] = check.trigger_evidence(
        shot_event,
        trigger_setup,
        dropped=[
            e
            for e in events
            if e.get("type") == "error"
            and mapping(e.get("context"))
            and e.get("error", "").startswith("Tester shot rejected")
        ],
    )
    rows["clip_matched"] = check.clip_matched(camera_outcome(captures), clip_dir is not None)
    rows["iwr_capture"] = check.iwr_capture(mapping(report["stages"].get("iwr6843")), iwr_event)
    rows["lcmf"] = check.lcmf(mapping(report["stages"].get("iwr6843")), rows)
    if not ran_kiosk or not check.passed(rows["clip_matched"]):
        upstream = (
            rows["clip_matched"] if not check.passed(rows["clip_matched"]) else rows["ops_shot"]
        )
        for stage_id in (
            "lighting",
            "geometry",
            "context",
            "replay_agrees",
            "camera_ball",
            "camera_club",
        ):
            rows[stage_id] = check.stage(
                stage_id,
                check.NOT_REACHED,
                cause=upstream["cause"] or "DATA",
                blocked_by=upstream["id"],
                evidence=f"stopped upstream at {upstream['label']}",
            )
    else:
        rows["lighting"] = check.lighting(
            live["lighting"],
            config_has_setup_ball=bool(mapping(config.get("camera_capture")).get("setup_ball")),
        )
        rows["geometry"] = check.geometry(
            live["geometry_snapshot"],
            live["geometry_error"],
            rows["tee_range"],
            handed_tee_m=mapping(config.get("tee_range_handoff")).get("tee_slant_range_m"),
        )
        rows["context"] = check.context(live, rows)
        camera_stage = mapping(report["stages"].get("camera"))
        recomputed = mapping(camera_stage.get("recomputed_radar_context"))
        # the kiosk's own context for this shot, whether or not the review replays it
        kiosk_context = live.get("kiosk_context") or live.get("context")
        rows["replay_agrees"] = check.replay_agrees(
            kiosk_context,
            tap.recomputed_context if recomputed.get("status") == "replayed" else None,
            rows["context"],
        )
        result = (
            mapping(live.get("processing"))
            if rebuilt
            else mapping(mapping(camera_stage.get("recorded_context")).get("replay"))
        )
        if recomputed.get("status") == "replayed" and not rebuilt:
            result = mapping(recomputed.get("result"))
        camera_config = mapping(config.get("camera_capture"))
        size = (camera_config.get("width"), camera_config.get("height"))
        rows["camera_ball"] = check.camera_ball(
            result,
            rows,
            setup_ball=camera_config.get("setup_ball"),
            image_size=size if all(size) else None,
        )
        rows["camera_club"] = check.camera_club(
            result, rows, ops_club_speed=ops_result.get("club_speed_mph")
        )
        live["estimator"] = result
    order = [
        "setup_admitted",
        "box_and_setup_ball",
        "tee_range",
        "camera_vertical_offset",
        "ops_shot",
        "trigger_evidence",
        "clip_matched",
        "lighting",
        "geometry",
        "context",
        "replay_agrees",
        "camera_ball",
        "camera_club",
        "iwr_capture",
        "lcmf",
    ]
    table = [rows[stage_id] for stage_id in order]
    azimuth = (
        mapping(iwr_event.get("runtime_config")).get("horizontal_phase_reference_rad") is not None
    )
    estimator = mapping(live.get("estimator"))
    for metric in reviewed["metrics"]:
        table.append(
            check.metric_stage(metric, rows, estimator=estimator, azimuth_calibrated=azimuth)
        )
    if ran_kiosk:
        table.append(check.face_angle_stage(live, rows))
    return {
        "shot_number": number,
        "stages": table,
        "summary": check.summarize(table),
        "context_source": live.get("context_source"),
    }


def _setup_record(run_dir: Path) -> dict | None:
    record = _read_json(run_dir / "tee_range.json")
    return record if isinstance(record, dict) else None


def check_run(  # pylint: disable=too-many-arguments
    server,
    tester_dir: Path,
    arm_id: str,
    run_dir: Path,
    injection: Injection,
    root: Path,
    tap: _IwrTap,
) -> dict[str, Any]:
    """Every shot of one run, each with its setup stages and its own stages."""
    sessions = sorted(run_dir.glob("session_*.jsonl"))
    if len(sessions) != 1:
        return {
            "arm_id": arm_id,
            "run": run_dir.name,
            "error": f"expected one session log, found {len(sessions)}",
            "shots": [],
        }
    frozen = virtual_session(load_session_events(sessions[0]), injection)
    _hash, start, events = frozen
    config = mapping(start.get("config"))
    first_camera = next(
        (e for e in events if e.get("type") == "camera_capture" and not e.get("capture_error")),
        {},
    )
    runtime_tees = [
        mapping(mapping(mapping(e.get("runtime_config")).get("calibration")).get("effective")).get(
            "tee_slant_range_m"
        )
        for e in events
        if e.get("type") == "iwr6843_capture" and isinstance(e.get("runtime_config"), dict)
    ]
    setup_rows = check.setup_stages(
        admission=_read_json(run_dir / "setup_admission.json"),
        trigger_setup=mapping(mapping(first_camera.get("metadata")).get("tester_setup")),
        config=config,
        injected=injection.as_dict(),
        tester_box=_read_json(tester_dir / "placement-box.json"),
        runtime_tees=runtime_tees,
        setup_record=_setup_record(run_dir),
    )
    trackers = (ReferenceBallTracker(), ReferenceBallTracker())
    numbers = sorted({e["shot_number"] for e in events if type(e.get("shot_number")) is int})
    shots = [
        _shot_rows(
            server=server,
            run_dir=run_dir,
            session=sessions[0],
            frozen=frozen,
            number=number,
            setup_rows=setup_rows,
            trackers=trackers,
            injection=injection,
            root=root,
            tap=tap,
        )
        for number in numbers
    ]
    return {
        "arm_id": arm_id,
        "run": run_dir.name,
        "session_uuid": start.get("session_uuid"),
        "session_file": sessions[0].name,
        "shots": shots,
    }


# ---------------------------------------------------------------- setup-only testers


def _latest_setup(tester_dir: Path) -> dict[str, Any]:
    """What the tester's latest guided setup reached, from its saved state files."""
    epochs = tester_dir / "calibration" / "tee-range" / "guided" / "epochs"
    states = sorted(epochs.glob("*/state-*.json")) if epochs.is_dir() else []
    if not states:
        return {}
    latest = max(states, key=lambda path: (path.stat().st_mtime, path.name))
    state = mapping(_read_json(latest))
    evidence = mapping(state.get("evidence"))
    candidate = mapping(evidence.get("iwr_candidate"))
    difference = mapping(mapping(candidate.get("evidence")).get("difference"))
    searches = sorted(latest.parent.glob("camera-*-exposure-search.json"))
    search = mapping(_read_json(searches[-1])) if searches else {}
    return {
        "epoch_id": state.get("epoch_id"),
        "phase": state.get("phase"),
        "reason": state.get("reason"),
        "radar_range_m": candidate.get("radar_slant_range_m"),
        "radar_status": difference.get("status"),
        "camera_search_status": search.get("status"),
        "camera_search_reason": search.get("reason"),
    }


def setup_only(tester_dir: Path) -> list[dict]:
    """Stages for a tester that ran the setup and no swings."""
    rows = []
    box = _read_json(tester_dir / "placement-box.json")
    rows.append(
        check.stage("box_and_setup_ball", check.PASS, value=f"box {box.get('box_px')}")
        if isinstance(box, dict)
        else check.stage(
            "box_and_setup_ball",
            check.FAIL,
            cause="DATA",
            gate="setup_box_ball",
            evidence="no placement-box.json",
        )
    )
    latest = _latest_setup(tester_dir)
    radar = latest.get("radar_range_m")
    rows.append(
        # the saved files do not say whether the ball, the radar or a setup gate is at fault
        check.stage(
            "tee_range",
            check.FAIL,
            cause="UNKNOWN",
            gate="tee_range",
            value=f"radar {radar:.3f} m ({latest.get('radar_status')})" if radar else None,
            evidence=(
                f"setup stopped at {latest.get('phase')!r} ({latest.get('reason')}); camera "
                f"{latest.get('camera_search_status')}: {latest.get('camera_search_reason')}"
            ),
        )
    )
    rows.append(check.camera_vertical_offset({}))
    rows.append(
        check.stage(
            "swings",
            check.FAIL,
            label="Swings",
            cause="DATA",
            evidence="no paired swing runs were recorded",
        )
    )
    return rows


def check_tester(tester_dir: Path, injection: Injection | None = None) -> dict[str, Any]:
    """Run the check over every run of one tester folder."""
    from openflight import server  # noqa: PLC0415  (heavy: Flask, Socket.IO)

    # the kiosk logs every withheld stage with a traceback; the table carries the reason
    logging.getLogger(server.__name__).setLevel(logging.ERROR)
    injection = injection or Injection()
    tester_dir = Path(tester_dir).resolve()
    root = tester_dir.parent
    tap = _IwrTap()
    runs = [
        check_run(server, tester_dir, arm_id, run_dir, injection, root, tap)
        for arm_id, run_dir in _runs(tester_dir)
    ]
    report = {
        "schema": check.SCHEMA,
        "tester_id": tester_dir.name,
        "injected": injection.as_dict() if injection.active else None,
        "bypassed_gates": sorted(injection.bypass),
        "runs": runs,
        "setup_only": setup_only(tester_dir) if not runs else None,
    }
    rows = [row for run in runs for shot in run["shots"] for row in shot["stages"]]
    report["summary"] = check.summarize(rows + (report["setup_only"] or []))
    report["code_breaks"] = [
        {"run": f"{run['arm_id']}/{run['run']}", "shot": shot["shot_number"], **row}
        for run in runs
        for shot in run["shots"]
        for row in shot["stages"]
        if row.get("cause") == "CODE" and row["status"] == check.FAIL
    ]
    return report


def format_report(report: Mapping[str, Any]) -> str:
    """The printed form: a loud injection banner, then one table per shot."""
    lines = [f"OpenFlight pipeline check: {report['tester_id']}"]
    injected = report.get("injected")
    if injected:
        values = ", ".join(f"{key}={value}" for key, value in injected.items() if value is not None)
        banner = f"*** INJECTED SETUP VALUES, NOT RECORDED BY THE SESSION: {values} ***"
        lines += ["*" * len(banner), banner, "*" * len(banner)]
    if report.get("bypassed_gates"):
        lines.append(
            "*** GATES STEPPED PAST (diagnostic only; the kiosk would stop here): "
            + ", ".join(report["bypassed_gates"])
            + " ***"
        )
    if report.get("setup_only"):
        lines += ["", "Setup only (no swing runs):", check.format_table(report["setup_only"])]
    for run in report["runs"]:
        lines.append("")
        lines.append(f"== {run['arm_id']}/{run['run']} (session {run.get('session_uuid')})")
        if run.get("error"):
            lines.append(f"  run problem: {run['error']}")
        for shot in run["shots"]:
            lines.append(f"-- shot {shot['shot_number']} (context: {shot['context_source']})")
            lines.append(check.format_table(shot["stages"]))
    lines += ["", f"Summary: {json.dumps(report['summary'], sort_keys=True)}"]
    for broken in report.get("code_breaks") or []:
        lines.append(
            f"CODE: {broken['run']} shot {broken['shot']} {broken['label']}: {broken['evidence']}"
        )
    return "\n".join(lines)


def _tee(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("tee range must be a positive number of metres")
    return number


def main(argv: list[str] | None = None) -> int:
    """Parse the tester folder and injections, run the check, print and write it."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("tester_dir", type=Path, help="one tester's session folder")
    parser.add_argument("--inject-tee-range", type=_tee, metavar="M")
    parser.add_argument("--inject-setup-ball", metavar="X,Y,D")
    parser.add_argument("--inject-box", metavar="X0,Y0,X1,Y1")
    parser.add_argument(
        "--bypass-gate",
        action="append",
        choices=BYPASSABLE_GATES,
        default=[],
        help="step past this gate to see the next one (the row stays failed, marked BYPASSED)",
    )
    parser.add_argument("--output", type=Path, help="write the JSON report here")
    args = parser.parse_args(argv)
    try:
        injection = Injection(
            tee_range_m=args.inject_tee_range,
            setup_ball=parse_setup_ball(args.inject_setup_ball),
            box=parse_hitting_zone(args.inject_box),
            bypass=frozenset(args.bypass_gate),
        )
    except ValueError as error:
        parser.error(str(error))
    if not args.tester_dir.is_dir():
        print(f"no tester folder at {args.tester_dir}", file=sys.stderr)
        return 2
    report = check_tester(args.tester_dir, injection)
    print(format_report(report))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
