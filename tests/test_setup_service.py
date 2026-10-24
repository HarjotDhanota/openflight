"""The setup service: box, aim and range actions on the per-unit record, camera only."""

from __future__ import annotations

import numpy as np
import pytest

from openflight import kiosk_extensions
from openflight.camera import ground_patch, placement_box, reference_ball_range, target_line
from openflight.camera.reference_ball_range import (
    BallPlaneCamera,
    ReferenceBallRangeCandidate,
    ReferenceBallRangeResult,
    project_to_pixel,
)
from openflight.setup_record import StartContext, load, save, section_inputs
from openflight.setup_service import Appliers, CameraView, SetupService

CTX = StartContext(
    lens_id="stock-2.8", rig_sha256="rig-a", tilt_offset_deg=2.0, tilt_calibrated=True
)
BALL_HEIGHT_M = ground_patch.BALL_CENTER_HEIGHT_M


def _camera() -> BallPlaneCamera:
    return BallPlaneCamera.nominal(
        focal_px=933.3334,
        image_width_px=1280,
        image_height_px=800,
        pitch_deg=0.0,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.6),
        radar_origin_lfu=(0.0, -0.0014, 0.55),
        angular_uncertainty_deg=3.0,
        focal_relative_uncertainty=0.08,
    )


CAMERA = _camera()
BALL_PX = list(project_to_pixel(CAMERA, (0.0, 2.35, BALL_HEIGHT_M)))


def _candidate(x_px, y_px) -> ReferenceBallRangeCandidate:
    return ReferenceBallRangeCandidate(
        x_px=x_px,
        y_px=y_px,
        diameter_px=24.0,
        area_px=450,
        size_point_lfu_m=(0.12, 2.35, BALL_HEIGHT_M),
        size_radar_range_m=2.41,
        floor_camera_range_m=2.37,
        size_camera_range_m=2.37,
        floor_range_uncertainty_m=0.19,
        size_range_uncertainty_m=0.19,
        range_disagreement_m=None,
        consistency_sigma=0.1,
        source="nominal",
        confidence="experimental",
        score=0.2,
        rejection_reason=None,
    )


def _selected(px) -> ReferenceBallRangeResult:
    candidate = _candidate(*px)
    return ReferenceBallRangeResult("selected", "experimental", candidate, (candidate,), {})


NOTHING = ReferenceBallRangeResult("not_found", "withheld", None, (), {})


class Scene:
    """What the faked ball search sees: a ball inside the box, one outside it, or none."""

    def __init__(self, monkeypatch):
        self.in_box = None
        self.wide = None
        self.calls = []
        monkeypatch.setattr(reference_ball_range, "estimate_patch_ball", self)

    def __call__(self, frames, camera, *, search, ball_center_height_m, **kwargs):
        self.calls.append(kwargs)
        wide = search.half_size_m > 0.5
        px = self.wide if wide else self.in_box
        return _selected(px) if px is not None else NOTHING


class Recorder:
    def __init__(self):
        self.calls = {name: [] for name in ("box", "aim", "ball_range", "net_range", "scene")}

    def appliers(self) -> Appliers:
        return Appliers(**{name: calls.append for name, calls in self.calls.items()})


@pytest.fixture
def scene(monkeypatch):
    return Scene(monkeypatch)


@pytest.fixture
def make(tmp_path):
    def build(hooks=lambda *args: None, **over) -> tuple[SetupService, Recorder]:
        recorder = Recorder()
        service = SetupService(
            tmp_path / "setup.json",
            over.pop("ctx", CTX),
            camera_provider=lambda: CameraView(CAMERA, 0.0),
            frames_provider=lambda: np.zeros((2, 800, 1280), dtype=np.uint8),
            appliers=recorder.appliers(),
            hooks=hooks,
        )
        return service, recorder

    return build


def _confirm_box(service, scene):
    scene.in_box = BALL_PX
    result = service.box("confirm")
    assert result["ok"], result["reason"]
    return result


def test_confirm_box_refused_without_a_ball(make, scene):
    service, recorder = make()

    result = service.box("confirm")

    assert result["ok"] is False and result["reason"] == "no_ball"
    assert result["setup"]["box"]["state"] == "not_set"
    assert service.revision == 0
    assert recorder.calls["box"] == []


def test_confirm_box_outside_refused(make, scene):
    service, recorder = make()
    scene.wide = [40.0, 40.0]

    result = service.box("confirm")

    assert result["ok"] is False and result["reason"] == "outside_box"
    assert result["setup"]["box"]["state"] == "not_set"
    assert recorder.calls["box"] == []


def test_confirm_box_saves_and_applies(make, scene, tmp_path):
    service, recorder = make()
    old = load(tmp_path / "setup.json")
    old["aim"] = {
        "state": "set",
        "floor_line": None,
        "inputs": section_inputs("aim", box_id="older"),
    }
    old["ball_range"] = {
        "state": "set",
        "range_m": 1.9,
        "inputs": section_inputs("ball_range", box_id="older"),
    }
    save(tmp_path / "setup.json", old)
    service, recorder = make()

    result = _confirm_box(service, scene)

    box = result["setup"]["box"]
    assert box["state"] == "confirmed"
    assert box["id"] and box["box_px"]
    assert box["ball_px"] == pytest.approx(BALL_PX) and box["diameter_px"] == 24.0
    assert box["image_size_px"] == [1280, 800]
    assert box["source"] == placement_box.SOURCE and box["uncertainty"]["tilt_pad_deg"] > 0
    assert box["inputs"] == section_inputs(
        "box", lens_id="stock-2.8", rig_sha256="rig-a", tilt_deg=2.0
    )
    assert recorder.calls["box"] == [box]
    assert result["setup"]["aim"]["state"] == "stale"
    assert result["setup"]["ball_range"]["state"] == "stale"
    kept = load(tmp_path / "setup.json")
    assert kept["box"]["state"] == "confirmed" and kept["aim"]["state"] == "set"
    assert service.revision == kept["revision"]


def test_reconfirming_a_still_ball_keeps_the_box_and_what_was_set_on_it(make, scene):
    service, _recorder = make()
    _confirm_box(service, scene)
    first = service.snapshot()["box"]["id"]
    assert service.range("measure")["ok"]

    scene.in_box = [BALL_PX[0] + 1.0, BALL_PX[1] - 1.0]
    result = service.box("confirm")

    assert result["setup"]["box"]["id"] == first
    assert result["setup"]["ball_range"]["state"] == "set"


def test_a_rig_change_then_the_same_ball_does_not_revive_aim_and_range(make, scene):
    service, _recorder = make()
    _confirm_box(service, scene)
    service.aim("skip")
    assert service.range("measure")["ok"]
    new_rig = StartContext(
        lens_id="stock-2.8", rig_sha256="rig-b", tilt_offset_deg=2.0, tilt_calibrated=True
    )
    restarted, _recorder = make(ctx=new_rig)
    assert restarted.snapshot()["box"]["state"] == "stale"

    result = _confirm_box(restarted, scene)

    assert result["setup"]["box"]["state"] == "confirmed"
    for name in ("aim", "ball_range", "net_range"):
        assert result["setup"][name]["state"] == "stale", name


def test_a_moved_ball_makes_a_new_box_and_stales_the_range(make, scene):
    service, _recorder = make()
    _confirm_box(service, scene)
    first = service.snapshot()["box"]["id"]
    assert service.range("measure")["ok"]

    scene.in_box = [BALL_PX[0] + 30.0, BALL_PX[1]]
    result = service.box("confirm")

    assert result["setup"]["box"]["id"] != first
    assert result["setup"]["ball_range"]["state"] == "stale"


def _floor_line(forward_m=4.6):
    return [
        list(project_to_pixel(CAMERA, (-1.5, forward_m, 0.0))),
        list(project_to_pixel(CAMERA, (1.5, forward_m, 0.0))),
    ]


AIM_PX = list(project_to_pixel(CAMERA, (0.0, 30.0, 0.6)))


def test_aim_is_refused_until_the_box_is_confirmed(make, scene):
    service, recorder = make()

    result = service.aim("confirm", aim_px=AIM_PX)

    assert result["ok"] is False and "box" in result["reason"]
    assert recorder.calls["aim"] == []


def test_aim_confirm_stores_record_and_net_from_floor_line(make, scene):
    service, recorder = make()
    _confirm_box(service, scene)
    floor = _floor_line()

    result = service.aim("confirm", aim_px=AIM_PX, floor_line_px=floor)

    assert result["ok"], result["reason"]
    aim, net = result["setup"]["aim"], result["setup"]["net_range"]
    assert aim["state"] == "set" and aim["floor_line"] == floor
    assert abs(aim["yaw_deg"]) < 1.0 and aim["source"] == "aim_point"
    assert aim["inputs"]["box_id"] == result["setup"]["box"]["id"]
    assert net["source"] == "camera_floor_line"
    assert net["value_m"] == pytest.approx(4.6, abs=0.3) and net["uncertainty_m"] > 0
    assert net["inputs"]["floor_line"] == floor
    assert recorder.calls["aim"] == [aim] and recorder.calls["net_range"] == [net]


def test_aim_skip_keeps_default_net(make, scene):
    service, recorder = make()
    _confirm_box(service, scene)

    result = service.aim("skip")

    assert result["ok"], result["reason"]
    assert result["setup"]["aim"]["state"] == "skipped"
    net = result["setup"]["net_range"]
    assert net["source"] == "default_not_measured" and net["value_m"] == 4.6
    assert net["reason"] and "uncertainty_m" not in net
    assert recorder.calls["net_range"] == [net]


def test_aim_without_a_floor_line_is_a_labelled_default_net(make, scene):
    service, _recorder = make()
    _confirm_box(service, scene)

    result = service.aim("confirm", aim_px=AIM_PX)

    assert result["setup"]["aim"]["state"] == "set"
    assert result["setup"]["net_range"]["source"] == "default_not_measured"


def test_a_floor_line_more_uncertain_than_it_is_long_is_the_default(make, scene, monkeypatch):
    service, _recorder = make()
    _confirm_box(service, scene)
    monkeypatch.setattr(
        target_line,
        "net_range_from_floor_line",
        lambda *args, **kwargs: {
            "value_m": 2.0,
            "uncertainty_m": 2.5,
            "source": "camera_floor_line",
        },
    )

    result = service.aim("confirm", aim_px=AIM_PX, floor_line_px=_floor_line())

    net = result["setup"]["net_range"]
    assert net["source"] == "default_not_measured" and net["value_m"] == 4.6
    assert net["reason"] == "too uncertain to measure"


def _fake_line(unbounded_with_floor: bool, solved: list):
    def record(camera, ball, aim_px, floor_line=None, **kwargs):
        solved.append(floor_line)
        bounded = not (unbounded_with_floor and floor_line is not None)
        return {
            "aim_px": list(aim_px),
            "yaw_deg": 0.5,
            "applies_to": {"iwr_yaw_deg": 0.4},
            "total_uncertainty_deg": 0.8 if bounded else None,
            "method": "direction" if floor_line is None else "floor_point",
            "warnings": [],
            "ball_lfu_m": [0.0, 2.35],
            "source": "aim_point",
        }

    return record


def test_an_unbounded_floor_line_aims_as_a_direction_and_keeps_the_net(make, scene, monkeypatch):
    service, _recorder = make()
    _confirm_box(service, scene)
    solved = []
    monkeypatch.setattr(target_line, "target_line_record", _fake_line(True, solved))
    floor = _floor_line()

    result = service.aim("confirm", aim_px=AIM_PX, floor_line_px=floor)

    assert result["ok"], result["reason"]
    assert solved == [floor, None]
    aim, net = result["setup"]["aim"], result["setup"]["net_range"]
    assert aim["uncertainty_deg"] == 0.8 and aim["method"] == "direction"
    assert aim["floor_line_used"] is False and aim["floor_line"] == floor
    assert net["source"] == "camera_floor_line" and net["inputs"]["floor_line"] == floor


def test_an_aim_that_cannot_be_bounded_is_refused(make, scene, monkeypatch):
    service, recorder = make()
    _confirm_box(service, scene)
    monkeypatch.setattr(
        target_line,
        "target_line_record",
        lambda *args, **kwargs: {"total_uncertainty_deg": None},
    )

    result = service.aim("confirm", aim_px=AIM_PX)

    assert result["ok"] is False and "cannot bound" in result["reason"]
    assert result["setup"]["aim"]["state"] == "not_set"
    assert recorder.calls["aim"] == []


def test_a_bad_aim_point_is_refused_not_raised(make, scene):
    service, recorder = make()
    _confirm_box(service, scene)

    outside = service.aim("confirm", aim_px=[5000.0, 5.0])
    missing = service.aim("confirm")

    assert outside["ok"] is False and missing["ok"] is False
    assert recorder.calls["aim"] == []


def test_range_measure_is_camera_only(make, scene):
    service, recorder = make()
    _confirm_box(service, scene)

    result = service.range("measure")

    assert result["ok"], result["reason"]
    ball = result["setup"]["ball_range"]
    assert ball["state"] == "set" and ball["source"] == "camera_prior"
    assert ball["range_m"] == pytest.approx(2.41) and ball["uncertainty_m"] == pytest.approx(0.19)
    assert ball["side_offset_m"] == pytest.approx(0.12)
    assert ball["inputs"] == section_inputs("ball_range", box_id=result["setup"]["box"]["id"])
    assert scene.calls[-1]["confirm_anchor"]["x_px"] == pytest.approx(BALL_PX[0])
    assert recorder.calls["ball_range"] == [ball]


def test_range_measure_refused_when_the_ball_is_gone(make, scene):
    service, recorder = make()
    _confirm_box(service, scene)
    scene.in_box = None

    result = service.range("measure")

    assert result["ok"] is False and "not_found" in result["reason"]
    assert recorder.calls["ball_range"] == []


def test_clearing_the_box_clears_everything_built_on_it(make, scene):
    service, recorder = make()
    _confirm_box(service, scene)
    service.aim("skip")
    service.range("measure")

    result = service.box("clear")

    assert result["ok"]
    for name in ("box", "aim", "ball_range", "net_range", "scene"):
        assert result["setup"][name]["state"] == "not_set"
    assert recorder.calls["ball_range"][-1] is None and recorder.calls["net_range"][-1] is None


def test_clearing_the_aim_clears_its_net_range(make, scene):
    service, _recorder = make()
    _confirm_box(service, scene)
    service.aim("skip")

    result = service.aim("clear")

    assert result["setup"]["aim"]["state"] == "not_set"
    assert result["setup"]["net_range"]["state"] == "not_set"
    assert result["setup"]["box"]["state"] == "confirmed"


def test_hook_refusal_blocks_action(make, scene):
    seen = []

    def hooks(name, *args):
        seen.append((name, *args))
        return "run in progress" if name == "before_setup_action" else None

    service, recorder = make(hooks)
    scene.in_box = BALL_PX

    result = service.box("confirm")

    assert result["ok"] is False and result["reason"] == "run in progress"
    assert seen == [("before_setup_action", "box", "confirm", {})]
    assert service.revision == 0 and recorder.calls["box"] == []


def test_a_finished_action_announces_the_change_then_runs_the_after_hook(make, scene, monkeypatch):
    order = []

    def hooks(name, *args):
        order.append(name)

    def call_all(name, *args):
        order.append(name)
        assert args[:2] == ("box", "confirm") and args[2]["ok"] is True

    monkeypatch.setattr(kiosk_extensions, "call_all", call_all)
    service, _recorder = make(hooks)
    service.on_change = lambda snapshot: order.append(("changed", snapshot["box"]["state"]))
    scene.in_box = BALL_PX

    service.box("confirm")

    assert order == ["before_setup_action", ("changed", "confirmed"), "after_setup_action"]


def test_an_applier_that_fails_is_reported_and_the_record_is_kept(make, scene, tmp_path):
    service, recorder = make()
    service._appliers.box = lambda section: (_ for _ in ()).throw(RuntimeError("camera busy"))
    scene.in_box = BALL_PX

    result = service.box("confirm")

    assert result["ok"] is False and "camera busy" in result["reason"]
    assert load(tmp_path / "setup.json")["box"]["state"] == "confirmed"


def test_a_box_the_kiosk_cannot_use_shows_why_until_it_applies(make, scene):
    service, _recorder = make()
    refusal = ["the box was confirmed on a 1280x720 picture"]

    def applier(_section):
        if refusal:
            raise ValueError(refusal[0])

    service._appliers.box = applier
    scene.in_box = BALL_PX
    service.box("confirm")

    failed = service.apply_all()

    assert failed == ["could not apply box: the box was confirmed on a 1280x720 picture"]
    assert service.snapshot()["unapplied"] == {"box": refusal[0]}
    refusal.clear()
    assert service.apply_all() == [] and service.snapshot()["unapplied"] == {}


def test_a_failed_apply_is_retried_by_the_same_action_and_by_apply_all(make, scene):
    service, recorder = make()
    attempts = []

    def flaky(section):
        attempts.append(section)
        if len(attempts) == 1:
            raise RuntimeError("camera busy")

    service._appliers.box = flaky
    scene.in_box = BALL_PX

    first = service.box("confirm")
    second = service.box("confirm")

    assert first["ok"] is False and "camera busy" in first["reason"]
    assert second["ok"] is True
    assert [item["state"] for item in attempts] == ["confirmed", "confirmed"]
    assert service.box("show")["ok"] and len(attempts) == 2

    service._unapplied.add("box")
    assert service.apply_all() == [] and len(attempts) == 3 and "box" not in service._unapplied


def test_show_looks_without_saving(make, scene):
    service, _recorder = make()
    scene.in_box = BALL_PX

    result = service.box("show")

    assert result["ok"] and service.revision == 0
    assert result["setup"]["live"]["box"]["state"] == "found"
    assert result["setup"]["live"]["box"]["ball_px"] == pytest.approx(BALL_PX)
    assert result["setup"]["box"]["state"] == "not_set"


def test_a_saved_setup_is_loaded_and_applied_at_start(make, scene, tmp_path):
    service, _recorder = make()
    _confirm_box(service, scene)
    restarted, recorder = make()

    restarted.apply_all()

    assert recorder.calls["box"][0]["state"] == "confirmed"
    assert recorder.calls["aim"] == [None]


def test_a_tilt_change_stales_the_box_on_restart(make, scene):
    service, _recorder = make()
    _confirm_box(service, scene)
    moved = StartContext(
        lens_id="stock-2.8", rig_sha256="rig-a", tilt_offset_deg=9.0, tilt_calibrated=True
    )

    restarted, _recorder = make(ctx=moved)

    assert restarted.snapshot()["box"]["state"] == "stale"


def test_the_kiosk_extension_hooks_reach_the_service(monkeypatch, make, scene):
    monkeypatch.setattr(kiosk_extensions, "_extensions", [])
    seen = []
    refuse = ["no setup during a run"]

    class Gate(kiosk_extensions.KioskExtension):
        def before_setup_action(self, section, action, payload):
            return refuse[0]

        def after_setup_action(self, section, action, result):
            seen.append((section, action, result["ok"]))

    kiosk_extensions.register(kiosk_extensions.KioskExtension())
    kiosk_extensions.register(Gate())
    service, _recorder = make(kiosk_extensions.first)
    scene.in_box = BALL_PX

    assert service.box("confirm")["reason"] == "no setup during a run"
    refuse[0] = None
    assert service.box("confirm")["ok"]
    assert seen == [("box", "confirm", True)]
