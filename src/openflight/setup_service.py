"""The setup service: owns the per-unit setup record and performs the box, aim and range actions.

Every action runs the before hook, computes from the camera alone, saves the loaded record,
applies the sections that changed, announces the change and runs the after hook. Nothing
here is typed in by the tester: a value is measured, or it is a labelled default.
"""

from __future__ import annotations

import copy
import dataclasses
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, NamedTuple

import numpy as np

from openflight import kiosk_extensions
from openflight.camera import ground_patch, placement_box, reference_ball_range, target_line
from openflight.setup_record import SECTIONS, StartContext, evaluate, load, save, section_inputs

logger = logging.getLogger(__name__)

# the wider look that tells "outside the box" from "no ball": the hitting area either side
WIDE_WINDOW_M = (0.7, 4.0)
WIDE_HALF_WIDTH_M = 1.2
# a re-confirmed box keeps its identity (and what was set against it) if the ball stayed put
SAME_BALL_PX = 3.0
TOO_UNCERTAIN = "too uncertain to measure"


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
    ):
        self._path = Path(path)
        self._ctx = ctx
        self._camera_provider = camera_provider
        self._frames_provider = frames_provider
        self._appliers = appliers
        self._hooks = hooks
        self._config = box_config or placement_box.load_config()
        self._action_lock = threading.Lock()
        self._record = load(self._path)
        self._live: dict[str, Any] = {}
        self.on_change: Callable[[dict], None] = lambda snapshot: None

    @property
    def revision(self) -> int:
        """The saved record's revision, which grows with every save."""
        return int(self._record["revision"])

    def _evaluated(self) -> dict:
        margin = placement_box.pads_deg(self._config, self._ctx.tilt_calibrated)[0]
        return evaluate(self._record, self._ctx, tilt_margin_deg=margin)

    def snapshot(self) -> dict:
        """The record as it stands against the start context, plus the last live look."""
        return {**self._evaluated(), "live": copy.deepcopy(self._live)}

    def apply_all(self) -> None:
        """Put every usable section into effect, as at kiosk start."""
        record = self._evaluated()
        for name in SECTIONS:
            getattr(self._appliers, name)(record[name] if _usable(record[name]) else None)

    # actions

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
            "clear": lambda _record, _payload: self._cleared("ball_range", "scene"),
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
            try:
                changes = perform[action](before, payload)
                if changes:
                    record = copy.deepcopy(self._record)
                    record.update(changes)
                    try:
                        save(self._path, record)
                    except OSError as exc:
                        raise _Refused(f"could not save the setup: {exc}") from exc
                    self._record = record
            except _Refused as refusal_:
                return self._result(False, str(refusal_))
            failed = self._apply_changes(before, self._evaluated())
            result = self._result(not failed, "; ".join(failed) or None)
            self.on_change(result["setup"])
            if not failed:
                self._hooks("after_setup_action", section, action, result)
            return result

    def _result(self, ok: bool, reason: str | None) -> dict:
        return {"ok": ok, "reason": reason, "setup": self.snapshot()}

    def _apply_changes(self, before: dict, after: dict) -> list[str]:
        failed = []
        for name in SECTIONS:
            if after[name] == before[name]:
                continue
            try:
                getattr(self._appliers, name)(after[name] if _usable(after[name]) else None)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error("[SETUP] applying %s failed", name, exc_info=True)
                failed.append(f"could not apply {name}: {exc}")
        return failed

    def _cleared(self, *names: str) -> dict:
        return {name: {"state": "not_set", "reason": "cleared"} for name in names}

    # camera access

    def _view(self) -> tuple[Any, float | None]:
        view = self._camera_provider()
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

    # box

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

    def _box_confirm(self, _record: dict, _payload: dict) -> dict:
        projection, classified = self._classify_box()
        if classified["state"] != "found":
            raise _Refused(classified["state"])
        ball = classified["selected"]
        centre = [float(ball["x_px"]), float(ball["y_px"])]
        previous = self._record["box"]
        base = placement_box.box_id(self._config, self._ctx.lens_id)
        kept = previous.get("ball_px")
        same_ball = (
            str(previous.get("id", "")).startswith(base)
            and kept is not None
            and np.hypot(kept[0] - centre[0], kept[1] - centre[1]) <= SAME_BALL_PX
        )
        box_id = previous["id"] if same_ball else f"{base}@{centre[0]:.0f},{centre[1]:.0f}"
        return {
            "box": {
                "state": "confirmed",
                "id": box_id,
                "box_px": list(projection.box_px),
                "ball_px": centre,
                "diameter_px": float(ball["diameter_px"]),
                "source": placement_box.SOURCE,
                "uncertainty": {
                    "tilt_pad_deg": projection.tilt_pad_deg,
                    "roll_pad_deg": projection.roll_pad_deg,
                },
                "inputs": section_inputs(
                    "box",
                    lens_id=self._ctx.lens_id,
                    rig_sha256=self._ctx.rig_sha256,
                    tilt_deg=self._ctx.tilt_offset_deg,
                ),
            }
        }

    def _box_clear(self, _record: dict, _payload: dict) -> dict:
        return self._cleared(*SECTIONS)

    # aim

    def _need_box(self, evaluated: dict) -> dict:
        box = evaluated["box"]
        if not _usable(box):
            raise _Refused(f"box is {box.get('state')}: confirm the box first")
        return box

    def _aim_show(self, _record: dict, _payload: dict) -> dict:
        camera, _roll = self._view()
        self._live = {**self._live, "aim": {"default_aim_px": target_line.default_aim_px(camera)}}
        return {}

    @staticmethod
    def _default_net(reason: str) -> dict:
        return {
            "state": "default",
            "value_m": target_line.DEFAULT_NET_RANGE_M,
            "source": "default_not_measured",
            "reason": reason,
        }

    def _net_range(self, camera, roll, ball_lfu, floor, tilt_pad) -> dict:
        if floor is None:
            return self._default_net("no floor line marked")
        net = target_line.net_range_from_floor_line(
            camera, floor, ball_lfu=ball_lfu, tilt_pad_deg=tilt_pad, roll_deg=roll
        )
        if net["source"] != "camera_floor_line":
            return {"state": "default", **net}
        if net["uncertainty_m"] >= net["value_m"]:
            return self._default_net(TOO_UNCERTAIN)
        return {"state": "set", **net}

    def _stamped_net(self, net: dict, floor) -> dict:
        net["inputs"] = section_inputs(
            "net_range",
            floor_line=floor,
            lens_id=self._ctx.lens_id,
            tilt_deg=self._ctx.tilt_offset_deg,
        )
        return net

    def _aim_confirm(self, record: dict, payload: dict) -> dict:
        box = self._need_box(record)
        camera, roll = self._view()
        if payload["aim_px"] is None:
            raise _Refused("tap the target to set the aim point")
        ball = self._box_ball_lfu(camera, box)
        tilt_pad = placement_box.pads_deg(self._config, self._ctx.tilt_calibrated)[0]
        try:
            floor = target_line.parse_floor_line(payload["floor_line_px"], camera)
            net = self._net_range(camera, roll, ball, floor, tilt_pad)
            lens_forward = float(camera.camera_origin_lfu[1])
            line = target_line.target_line_record(
                camera,
                ball,
                payload["aim_px"],
                floor,
                tilt_pad_deg=tilt_pad,
                roll_deg=roll,
                target_distance_m=max(net["value_m"] - (ball[1] - lens_forward), 0.5),
                target_distance_basis=f"net_range_{net['source']}_less_the_ball_offset",
            )
        except ValueError as exc:
            raise _Refused(str(exc)) from exc
        return {
            "aim": {
                "state": "set",
                "aim_px": line["aim_px"],
                "floor_line": floor,
                "yaw_deg": line["yaw_deg"],
                "iwr_yaw_deg": line["applies_to"]["iwr_yaw_deg"],
                "uncertainty_deg": line["total_uncertainty_deg"],
                "method": line["method"],
                "warnings": line["warnings"],
                "ball_lfu_m": line["ball_lfu_m"],
                "source": line["source"],
                "inputs": section_inputs("aim", box_id=box["id"]),
            },
            "net_range": self._stamped_net(net, floor),
        }

    def _aim_skip(self, record: dict, _payload: dict) -> dict:
        box = self._need_box(record)
        net = self._default_net("aim point skipped")
        return {
            "aim": {
                "state": "skipped",
                "floor_line": None,
                "source": "skipped",
                "reason": "the tester skipped the aim point",
                "inputs": section_inputs("aim", box_id=box["id"]),
            },
            "net_range": self._stamped_net(net, None),
        }

    def _box_ball_lfu(self, camera, box: dict) -> tuple[float, float]:
        try:
            hit = reference_ball_range.ray_to_ball_center_plane(
                camera, box["ball_px"], ball_center_height_m=ground_patch.BALL_CENTER_HEIGHT_M
            )
        except ValueError as exc:
            raise _Refused(f"the box's ball has no floor point: {exc}") from exc
        return float(hit.point_lfu_m[0]), float(hit.point_lfu_m[1])

    # ball range

    def _range_measure(self, record: dict, _payload: dict) -> dict:
        box = self._need_box(record)
        camera, roll = self._view()
        frames = self._frames()
        projection = placement_box.project_fixed_box(
            camera, calibrated=self._ctx.tilt_calibrated, roll_deg=roll, config=self._config
        )
        found = self._search(
            frames,
            camera,
            ground_patch.PatchSearch.from_projection(projection),
            confirm_anchor={
                "x_px": box["ball_px"][0],
                "y_px": box["ball_px"][1],
                "diameter_px": box["diameter_px"],
            },
        )
        ball = found["result"].selected
        if found["status"] != "selected" or ball is None or ball.size_radar_range_m is None:
            raise _Refused(f"ball not measured: {found['status']}")
        radar_x = float(camera.radar_origin_lfu[0])
        return {
            "ball_range": {
                "state": "set",
                "range_m": float(ball.size_radar_range_m),
                "uncertainty_m": float(ball.size_range_uncertainty_m),
                "side_offset_m": float(ball.size_point_lfu_m[0]) - radar_x,
                "ball_px": [ball.x_px, ball.y_px],
                "diameter_px": ball.diameter_px,
                "source": "camera_prior",
                "inputs": section_inputs("ball_range", box_id=box["id"]),
            }
        }
