"""The setup service: owns the per-unit setup record and performs the box, aim and range actions.

Every action runs the before hook, computes from the camera alone, saves the loaded record,
applies the sections that changed, announces the change and runs the after hook. Nothing
here is typed in by the tester: a value is measured, or it is a labelled default.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, NamedTuple

import numpy as np

from openflight import kiosk_extensions
from openflight.camera import ground_patch, placement_box, reference_ball_range, target_line
from openflight.setup_record import (
    SECTIONS,
    SaveRefused,
    SetupFileChanged,
    StartContext,
    evaluate,
    load,
    save,
    section_inputs,
)

logger = logging.getLogger(__name__)

# the wider look that tells "outside the box" from "no ball": the hitting area either side
WIDE_WINDOW_M = (0.7, 4.0)
WIDE_HALF_WIDTH_M = 1.2
# the fixed box drawn again this close (the tilt sensor's noise) is the box already confirmed
SAME_OUTLINE_PX = 0.5
TOO_UNCERTAIN = "too uncertain to measure"
ACTIONS = {
    "box": ("show", "confirm", "clear"),
    "aim": ("show", "confirm", "skip", "clear"),
    "range": ("measure", "clear"),
}


class Blocked(NamedTuple):
    """Why the kiosk as started can never set up; ``fault`` when something asked for failed
    (the tilt sensor did not start), which holds the ready light red."""

    reason: str
    fault: bool = False


class CameraView(NamedTuple):
    """What ``camera_provider`` returns: the camera model and the roll it does not apply."""

    camera: Any
    roll_deg: float | None


@dataclass
class Appliers:
    """Put a section into effect (None: it was cleared or went stale). Filled in by the kiosk."""

    box: Callable[[dict | None], None]
    aim: Callable[[dict | None], None]
    ball_range: Callable[[dict | None], None]
    net_range: Callable[[dict | None], None]
    scene: Callable[[dict | None], None]


class _Refused(Exception):
    """An action that cannot proceed; the message is the reason shown to the tester."""


def _usable(section: dict) -> bool:
    return section.get("state") not in ("not_set", "stale")


# The service writes its sections with these builders, as may anything else that writes a
# setup record a kiosk then starts from.


def box_section(  # pylint: disable=too-many-arguments
    *,
    config: placement_box.PlacementBoxConfig,
    box_px,
    ball_px,
    diameter_px: float,
    tilt_pad_deg: float,
    roll_pad_deg: float,
    ctx: StartContext,
    outline_px=None,
) -> dict:
    """A confirmed box, stamped with the lens, rig and tilt it was confirmed under.

    ``outline_px`` is the confirmed polygon; ``box_px`` bounds it and is only the detector's
    window. The id excludes the ball: a ball anywhere in the box keeps the aim and range.
    """
    section = {
        "state": "confirmed",
        "box_px": list(box_px),
        "ball_px": [float(ball_px[0]), float(ball_px[1])],
        "diameter_px": float(diameter_px),
        "source": placement_box.SOURCE,
        "uncertainty": {"tilt_pad_deg": tilt_pad_deg, "roll_pad_deg": roll_pad_deg},
        "inputs": section_inputs(
            "box", lens_id=ctx.lens_id, rig_sha256=ctx.rig_sha256, tilt_deg=ctx.tilt_offset_deg
        ),
    }
    if outline_px is not None:
        section["outline_px"] = _rounded(outline_px)
    geometry = {key: section.get(key) for key in ("box_px", "outline_px", "inputs")}
    digest = hashlib.sha256(json.dumps(geometry, sort_keys=True).encode("utf-8")).hexdigest()
    section["id"] = f"{placement_box.box_id(config, ctx.lens_id)}-{digest[:12]}"
    return section


def _rounded(outline) -> list[list[float]]:
    return [[round(float(x), 2), round(float(y), 2)] for x, y in outline]


def _same_outline(kept, outline) -> bool:
    if not kept or len(kept) != len(outline):
        return False
    return all(
        np.hypot(a[0] - b[0], a[1] - b[1]) <= SAME_OUTLINE_PX
        for a, b in zip(kept, outline, strict=True)
    )


def aim_section(line: dict, *, floor, floor_line_used: bool, box_id: str) -> dict:
    """An aim from a bounded ``target_line.target_line_record``."""
    return {
        "state": "set",
        "aim_px": line["aim_px"],
        "floor_line": floor,
        "yaw_deg": line["yaw_deg"],
        "iwr_yaw_deg": line["applies_to"]["iwr_yaw_deg"],
        "uncertainty_deg": line["total_uncertainty_deg"],
        "method": line["method"],
        "floor_line_used": floor_line_used,
        "warnings": line["warnings"],
        "ball_lfu_m": line["ball_lfu_m"],
        "source": line["source"],
        "inputs": section_inputs("aim", box_id=box_id),
    }


def skipped_aim_section(box_id: str) -> dict:
    return {
        "state": "skipped",
        "floor_line": None,
        "source": "skipped",
        "reason": "the tester skipped the aim point",
        "inputs": section_inputs("aim", box_id=box_id),
    }


def default_net(reason: str) -> dict:
    return {
        "state": "default",
        "value_m": target_line.DEFAULT_NET_RANGE_M,
        "source": "default_not_measured",
        "reason": reason,
    }


def floor_line_net(camera, roll, ball_lfu, floor, tilt_pad) -> dict:
    """The net distance the floor line measures, or the labelled default and why."""
    if floor is None:
        return default_net("no floor line marked")
    net = target_line.net_range_from_floor_line(
        camera, floor, ball_lfu=ball_lfu, tilt_pad_deg=tilt_pad, roll_deg=roll
    )
    if net["source"] != "camera_floor_line":
        return {"state": "default", **net}
    if net["uncertainty_m"] >= net["value_m"]:
        return default_net(TOO_UNCERTAIN)
    return {"state": "set", **net}


def stamped_net(net: dict, floor, ctx: StartContext) -> dict:
    """A net range stamped with the floor line, lens and tilt it was measured under."""
    return {
        **net,
        "inputs": section_inputs(
            "net_range", floor_line=floor, lens_id=ctx.lens_id, tilt_deg=ctx.tilt_offset_deg
        ),
    }


def ball_range_section(  # pylint: disable=too-many-arguments
    *,
    range_m: float,
    uncertainty_m: float,
    side_offset_m: float | None,
    ball_px,
    diameter_px,
    source: str,
    box_id: str,
    qualification: str | None = None,
    candidate_id: str | None = None,
) -> dict:
    """The resting ball's range from the radar's origin: a prior for each shot's own."""
    return {
        "state": "set",
        "range_m": float(range_m),
        "uncertainty_m": float(uncertainty_m),
        "side_offset_m": float(side_offset_m) if side_offset_m is not None else None,
        "ball_px": list(ball_px) if ball_px is not None else None,
        "diameter_px": diameter_px,
        "source": source,
        "qualification": qualification,
        "candidate_id": candidate_id,
        "inputs": section_inputs("ball_range", box_id=box_id),
    }


def scene_section(
    *, lens_height_m: float | None, uncertainty_m: float | None, source: str, box_id: str
) -> dict:
    """The lens height used: radar-solved against the box's ball (a gross error), else the
    rig file's, labelled."""
    return {
        "state": "default" if source == "rig_nominal" else "set",
        "lens_height_m": lens_height_m,
        "uncertainty_m": uncertainty_m,
        "source": source,
        "inputs": section_inputs("scene", box_id=box_id),
    }


class SetupService:  # pylint: disable=too-many-instance-attributes
    """The setup record and the actions on it."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        path: Path,
        ctx: StartContext,
        *,
        camera_provider: Callable[[], Any],
        frames_provider: Callable[[], np.ndarray | None],
        appliers: Appliers,
        hooks: Callable[..., Any] = kiosk_extensions.first,
        box_config: placement_box.PlacementBoxConfig | None = None,
        between_shots: Callable[[Callable[[], Any]], Any] = lambda apply: apply(),
        camera_available: Callable[[], bool] = lambda: True,
        setup_blocked: Callable[[], Blocked | None] = lambda: None,
    ):
        self._path = Path(path)
        self._ctx = ctx
        self._camera_provider = camera_provider
        self._frames_provider = frames_provider
        self._appliers = appliers
        self._hooks = hooks
        # runs one change's applies together, so no shot lands between two sections
        self._between_shots = between_shots
        self._camera_available = camera_available
        self._setup_blocked = setup_blocked
        self._config = box_config or placement_box.load_config()
        self._action_lock = threading.Lock()
        self._record = load(self._path)
        self._live: dict[str, Any] = {}
        self._unapplied: set[str] = set()
        self._apply_errors: dict[str, str] = {}
        self._applied_revision: int | None = None
        self.on_change: Callable[[dict], None] = lambda snapshot: None

    @property
    def revision(self) -> int:
        """The saved record's revision, which grows with every save."""
        return int(self._record["revision"])

    @property
    def applied_revision(self) -> int | None:
        """The revision the kiosk last put into effect, which each shot records; None before."""
        return self._applied_revision

    @property
    def sha256(self) -> str:
        """The saved record's hash (canonical JSON), logged with each revision."""
        canonical = json.dumps(self._record, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _evaluated(self) -> dict:
        margin = placement_box.pads_deg(self._config, self._ctx.tilt_calibrated)[0]
        return evaluate(self._record, self._ctx, tilt_margin_deg=margin)

    def snapshot(self) -> dict:
        """The record against the start context, the last live look, and why any is not in effect."""
        camera = bool(self._camera_available())
        blocked = self._setup_blocked() if camera else Blocked("no camera")
        return {
            **self._evaluated(),
            # a unit that can never set up is not offered the screen, nor held red for it
            "live": {
                **copy.deepcopy(self._live),
                "camera_available": camera,
                "setup_possible": blocked is None,
                "setup_blocked_reason": blocked.reason if blocked else None,
                "setup_fault": bool(blocked and blocked.fault),
            },
            "unapplied": dict(self._apply_errors),
        }

    def apply_all(self) -> list[str]:
        """Put every section into effect again; the sections that failed."""
        with self._action_lock:
            return self._apply(set(SECTIONS), self._evaluated())

    def apply_saved(self) -> list[str]:
        """At kiosk start: put every section ever set into effect, a stale one as None so it
        clears; one never set is left to the kiosk's own start values. The failures."""
        with self._action_lock:
            evaluated = self._evaluated()
            present = {name for name in SECTIONS if evaluated[name].get("state") != "not_set"}
            failed = self._apply(present, evaluated)
            if not present:
                self._applied_revision = self.revision  # nothing to put into effect
            return failed

    def box(self, action: Literal["show", "confirm", "clear"]) -> dict:
        """Look at the placement box, confirm the ball in it, or clear the setup."""
        perform = {"show": self._box_look, "confirm": self._box_confirm, "clear": self._box_clear}
        return self._run("box", action, {}, perform)

    def aim(
        self,
        action: Literal["show", "confirm", "skip", "clear"],
        *,
        aim_px=None,
        floor_line_px=None,
    ) -> dict:
        """Show the aim view, confirm a tapped aim point, skip it, or clear it."""
        payload = {"aim_px": aim_px, "floor_line_px": floor_line_px}
        perform = {
            "show": self._aim_show,
            "confirm": self._aim_confirm,
            "skip": self._aim_skip,
            "clear": lambda _record, _payload: self._cleared("aim", "net_range"),
        }
        return self._run("aim", action, payload, perform)

    def range(self, action: Literal["measure", "clear"]) -> dict:
        """Measure the resting ball's range from the camera, or clear it."""
        perform = {
            "measure": self._range_measure,
            # a solved lens height belongs to the box, not the range: it survives this clear
            "clear": lambda _record, _payload: self._cleared("ball_range"),
        }
        return self._run("ball_range", action, {}, perform)

    def _run(self, section: str, action: str, payload: dict, perform: dict) -> dict:
        with self._action_lock:
            if action not in perform:
                return self._result(False, f"unknown {section} action {action!r}")
            refusal = self._hooks("before_setup_action", section, action, payload)
            if refusal:
                return self._result(False, str(refusal))
            before = self._evaluated()
            looked = copy.deepcopy(self._live)
            refused = None
            try:
                changes = perform[action](before, payload)
                if changes:
                    self._save(changes)
            except _Refused as refusal_:
                refused = str(refusal_)
            after = self._evaluated()
            if refused is not None and after == before:
                return self._result(False, refused)
            # a refusal that reloaded another writer's record still puts that one into effect
            failed = self._apply({n for n in SECTIONS if after[n] != before[n]}, after)
            ok = refused is None and not failed
            result = self._result(ok, refused or "; ".join(failed) or None)
            # a look every few seconds is news only when it saw something new, and never
            # an action for the hooks
            if action != "show" or self._live != looked:
                self.on_change(result["setup"])
            if ok and action != "show":
                self._hooks("after_setup_action", section, action, result)
            return result

    def _save(self, changes: dict) -> None:
        record = copy.deepcopy(self._record)
        record.update(changes)
        try:
            save(self._path, record)
        except SetupFileChanged as exc:
            # the other writer's record is the one to build on from here
            self._record = load(self._path)
            raise _Refused(f"{exc}: reloaded it, try again") from exc
        except SaveRefused as exc:
            raise _Refused(str(exc)) from exc
        except OSError as exc:
            raise _Refused(f"could not save the setup: {exc}") from exc
        self._record = record

    def _result(self, ok: bool, reason: str | None) -> dict:
        return {"ok": ok, "reason": reason, "setup": self.snapshot()}

    def _apply(self, changed: set[str], evaluated: dict) -> list[str]:
        """Apply the changed sections and any an earlier failure left unapplied."""
        names = [name for name in SECTIONS if name in changed or name in self._unapplied]
        # with nothing to apply, a newly saved revision still comes into effect between
        # shots; before the start's apply_saved, none has
        if not names and self._applied_revision in (None, self.revision):
            return []
        try:
            return self._between_shots(lambda: self._apply_now(names, evaluated))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("[SETUP] no gap between shots to apply %s", names, exc_info=True)
            for name in names:
                self._unapplied.add(name)
                self._apply_errors[name] = str(exc)
            return [f"could not apply {name}: {exc}" for name in names]

    def _apply_now(self, names: list[str], evaluated: dict) -> list[str]:
        failed = []
        for name in names:
            try:
                getattr(self._appliers, name)(evaluated[name] if _usable(evaluated[name]) else None)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error("[SETUP] applying %s failed", name, exc_info=True)
                self._unapplied.add(name)
                self._apply_errors[name] = str(exc)
                failed.append(f"could not apply {name}: {exc}")
            else:
                self._unapplied.discard(name)
                self._apply_errors.pop(name, None)
        # each shot records the revision in effect, which a failed section is not
        if not failed:
            self._applied_revision = self.revision
        return failed

    def _cleared(self, *names: str) -> dict:
        return {name: {"state": "not_set", "reason": "cleared"} for name in names}

    def _view(self) -> tuple[Any, float | None]:
        try:
            view = self._camera_provider()
        except ValueError as exc:
            raise _Refused(str(exc)) from exc
        if view is None:
            raise _Refused("camera unavailable")
        return view.camera, view.roll_deg

    def _frames(self) -> np.ndarray:
        frames = self._frames_provider()
        if frames is None:
            raise _Refused("no camera frames")
        return frames

    def _search(self, frames, camera, search, **kwargs) -> dict:
        try:
            result = reference_ball_range.estimate_patch_ball(
                frames,
                camera,
                search=search,
                ball_center_height_m=ground_patch.BALL_CENTER_HEIGHT_M,
                **kwargs,
            )
        except ValueError as exc:
            raise _Refused(f"camera frames cannot be searched: {exc}") from exc
        return {
            "status": result.status,
            "selected": dataclasses.asdict(result.selected) if result.selected else None,
            "result": result,
        }

    def _box_look(self, _record: dict, _payload: dict) -> dict:
        self._classify_box()
        return {}

    def _classify_box(self) -> tuple[Any, dict]:
        camera, roll = self._view()
        frames = self._frames()
        projection = placement_box.project_fixed_box(
            camera, calibrated=self._ctx.tilt_calibrated, roll_deg=roll, config=self._config
        )
        search = ground_patch.PatchSearch.from_projection(projection)
        in_box = self._search(frames, camera, search)
        wide = None
        if in_box["status"] not in ("selected", "ambiguous"):
            wide_config = dataclasses.replace(
                self._config, placement_window_m=WIDE_WINDOW_M, half_width_m=WIDE_HALF_WIDTH_M
            )
            wide_projection = placement_box.project_fixed_box(
                camera, calibrated=self._ctx.tilt_calibrated, roll_deg=roll, config=wide_config
            )
            wide = self._search(
                frames, camera, ground_patch.PatchSearch.from_projection(wide_projection)
            )
        classified = placement_box.classify_ball(
            in_box, wide, outline_px=search.outline_px, settled=True
        )
        selected = classified["selected"]
        self._live = {
            **self._live,
            "box": {
                "state": classified["state"],
                "message": placement_box.BALL_MESSAGES[classified["state"]],
                "ball_px": [selected["x_px"], selected["y_px"]] if selected else None,
                "diameter_px": selected["diameter_px"] if selected else None,
                "outline_px": [list(point) for point in projection.search_outline_px],
            },
        }
        return projection, classified

    def _box_confirm(self, record: dict, _payload: dict) -> dict:
        projection, classified = self._classify_box()
        if classified["state"] != "found":
            raise _Refused(classified["state"])
        ball = classified["selected"]
        box_px, outline = list(projection.box_px), _rounded(projection.search_outline_px)
        previous = record["box"]
        if _usable(previous) and _same_outline(previous.get("outline_px"), outline):
            box_px, outline = previous["box_px"], previous["outline_px"]
        return {
            "box": box_section(
                config=self._config,
                box_px=box_px,
                ball_px=[float(ball["x_px"]), float(ball["y_px"])],
                diameter_px=ball["diameter_px"],
                tilt_pad_deg=projection.tilt_pad_deg,
                roll_pad_deg=projection.roll_pad_deg,
                ctx=self._ctx,
                outline_px=outline,
            )
        }

    def _box_clear(self, _record: dict, _payload: dict) -> dict:
        return self._cleared(*SECTIONS)

    def _need_box(self, evaluated: dict) -> dict:
        box = evaluated["box"]
        if not _usable(box):
            raise _Refused(f"box is {box.get('state')}: confirm the box first")
        return box

    def _aim_show(self, _record: dict, _payload: dict) -> dict:
        camera, _roll = self._view()
        self._live = {**self._live, "aim": {"default_aim_px": target_line.default_aim_px(camera)}}
        return {}

    def _aim_confirm(self, record: dict, payload: dict) -> dict:
        box = self._need_box(record)
        camera, roll = self._view()
        if payload["aim_px"] is None:
            raise _Refused("tap the target to set the aim point")
        ball = self._box_centre_lfu(camera)
        tilt_pad = placement_box.pads_deg(self._config, self._ctx.tilt_calibrated)[0]
        try:
            floor = target_line.parse_floor_line(payload["floor_line_px"], camera)
            net = floor_line_net(camera, roll, ball, floor, tilt_pad)
            lens_forward = float(camera.camera_origin_lfu[1])

            def solve(floor_line):
                return target_line.target_line_record(
                    camera,
                    ball,
                    payload["aim_px"],
                    floor_line,
                    tilt_pad_deg=tilt_pad,
                    roll_deg=roll,
                    target_distance_m=max(net["value_m"] - (ball[1] - lens_forward), 0.5),
                    target_distance_basis=f"net_range_{net['source']}_less_the_ball_offset",
                )

            line = solve(floor)
            used_floor = floor is not None
            if line["total_uncertainty_deg"] is None and used_floor:
                # an unbounded floor line gives the aim no honest +-: aim as a direction,
                # and keep the floor line for the net distance alone
                line, used_floor = solve(None), False
        except ValueError as exc:
            raise _Refused(str(exc)) from exc
        if line["total_uncertainty_deg"] is None:
            raise _Refused(
                "the camera cannot bound this aim point: aim at a farther target, or skip it"
            )
        return {
            "aim": aim_section(line, floor=floor, floor_line_used=used_floor, box_id=box["id"]),
            "net_range": stamped_net(net, floor, self._ctx),
        }

    def _aim_skip(self, record: dict, _payload: dict) -> dict:
        box = self._need_box(record)
        return {
            "aim": skipped_aim_section(box["id"]),
            "net_range": stamped_net(default_net("aim point skipped"), None, self._ctx),
        }

    def _box_centre_lfu(self, camera) -> tuple[float, float]:
        """The box's centre on the ground, which the aim is solved from: a ball anywhere in
        the box gives the same aim."""
        box = placement_box.fixed_box(camera, self._config)
        return box.centre_lateral_m, box.centre_forward_m

    def _range_measure(self, record: dict, _payload: dict) -> dict:
        box = self._need_box(record)
        camera, roll = self._view()
        frames = self._frames()
        projection = placement_box.project_fixed_box(
            camera, calibrated=self._ctx.tilt_calibrated, roll_deg=roll, config=self._config
        )
        # the ball wherever it is in the box now: the range depends on the box alone
        found = self._search(frames, camera, ground_patch.PatchSearch.from_projection(projection))
        ball = found["result"].selected
        if found["status"] != "selected" or ball is None or ball.size_radar_range_m is None:
            raise _Refused(f"ball not measured: {found['status']}")
        radar_x = float(camera.radar_origin_lfu[0])
        return {
            "ball_range": ball_range_section(
                range_m=ball.size_radar_range_m,
                uncertainty_m=ball.size_range_uncertainty_m,
                side_offset_m=float(ball.size_point_lfu_m[0]) - radar_x,
                ball_px=[ball.x_px, ball.y_px],
                diameter_px=ball.diameter_px,
                source="camera_prior",
                box_id=box["id"],
            )
        }
