"""The tester writes the shared setup record and starts every kiosk from it, not from flags."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openflight import kiosk_extensions, server, tee_range, tester_hooks
from openflight.camera import lens_profiles, placement_box, tester_server as ts
from openflight.camera.capture_runtime import CameraCaptureSettings
from openflight.rig_geometry import RigGeometry
from openflight.setup_record import SECTIONS, load
from tests.test_setup_api import RIG, _kiosk, _scene, _start, _start_kiosk  # noqa: F401

TESTER = "20260922-name"
TESTER_SETUP = {
    "config_hash": "test-config",
    "inclinometer_bus": 1,
    "inclinometer_address": 0x18,
    "inclinometer_zero_offset_deg": 0.0,
    "lis3dh_mount_file": "lis3dh-mount.json",
}
# what the kiosk no longer takes: the setup record carries all of it
REMOVED_FLAGS = (
    "--camera-ball-box",
    "--camera-hitting-zone",
    "--camera-setup-ball",
    "--target-line-",
    "--camera-capture-horizontal-offset-deg",
    "--iwr6843-azimuth-offset-deg",
    "--iwr6843-tee-",
    "--tee-side-offset-m",
    "--iwr6843-net-",
    "--net-range-m",
    "--solved-camera-height-m",
    "--scene-lens-height-solved-",
    "--camera-capture-mount-height-m",
    "--iwr6843-radar-height-m",
    "--iwr6843-ball-height-m",
    "--inclinometer-zero-offset",
    "--camera-capture-roll-deg",
)
TILT = {"offset_deg": 1.25, "uncertainty_deg": 0.3, "calibrated_at_utc": "2026-10-01"}
BALL = {"x_px": 640.0, "y_px": 450.0, "diameter_px": 20.0}


def _box_record():
    return {
        "mode": placement_box.MODE,
        "box_px": [480, 380, 800, 520],
        "frame_size_px": [1280, 800],
        "camera_tilt": {"search_pad_deg": 1.0, "roll_pad_deg": 2.0},
        "ball_at_confirm": {"state": "found", "center_px": [641.0, 451.0], "diameter_px": 20.5},
        "confirmed_at_utc": "2026-10-24T10:00:00+00:00",
    }


def _line_record(total=0.8):
    return {
        "schema": "openflight.tester_target_line.v1",
        "source": "aim_point",
        "aim_px": [700.0, 300.0],
        "floor_line_px": None,
        "ball_lfu_m": [0.01, 2.2],
        "method": "direction",
        "yaw_deg": 2.5,
        "kiosk_offset_deg": -2.5,
        "applies_to": {"camera_yaw_deg": 2.5, "iwr_yaw_deg": 2.5},
        "total_uncertainty_deg": total,
        "warnings": [],
    }


def _camera_search(arm="arm5"):
    return tee_range.TeeRangeCandidate(
        candidate_id=f"camera-setup-1-{arm}",
        source="camera_reference_ball_size_range",
        source_group="camera",
        radar_slant_range_m=2.3,
        uncertainty_m=0.3,
        selectable=False,
        evidence={"result": {"status": "selected", "selected": BALL}},
    )


def _prior_solution():
    prior = {
        "status": "saved",
        "range_m": 2.31,
        "uncertainty_m": 0.35,
        "side_offset_m": 0.02,
        "ball_px": [BALL["x_px"], BALL["y_px"]],
        "diameter_px": BALL["diameter_px"],
    }
    return ts.camera_prior_solution("epoch-1", [_camera_search()], prior, _box_record())


def _radar_solution(*, solved_height=None):
    camera = _camera_search()
    if solved_height is not None:
        camera = tee_range.TeeRangeCandidate.from_dict(
            {
                **camera.to_dict(),
                "evidence": {
                    **camera.to_dict()["evidence"],
                    "camera_height": {
                        "source": "static_iwr_range",
                        "height_m": solved_height,
                        "uncertainty_m": 0.02,
                    },
                },
            }
        )
    decision = {
        "schema": "openflight.tester_patch_ball.v1",
        "status": "validated",
        "source": "static_iwr_coherent_magnitude",
        "range_m": 2.274,
        "uncertainty_m": 0.04,
        "side_offset_m": -0.03,
    }
    return ts.patch_ball_solution("epoch-1", [camera], decision, _box_record())


def _lens():
    return lens_profiles.active_lens(
        RigGeometry.from_json(RIG),
        lens_profiles.load_catalogue(),
        None,
        [lens_profiles.default_calibration_dir()],
    )


def _sections(solution, *, tilt=None, line=None):
    return ts.tester_setup_sections(
        solution,
        placement_box_record=_box_record(),
        target_line_record=line if line is not None else _line_record(),
        net_line=None,
        rig_geometry=RIG,
        lens=_lens(),
        camera_tilt_calibration=tilt,
    )


def _params(arm_id="arm5"):
    return ts.TesterParameters.from_payload(
        {"tester_id": TESTER, "arm_id": arm_id, "environment": "indoors"}
    )


@pytest.mark.parametrize("action", ["swings", "free_play", "ladder"])
def test_tester_launch_passes_setup_file_not_flags(tmp_path, action):
    params = _params()
    ts.write_arm_state(tmp_path, params, gain=3.0, gain_exposure_us=params.arm.exposure_us)
    tilt_path = tmp_path / "camera-tilt.json"
    solution = _prior_solution()
    _args, handed = ts.tee_range_handoff(
        solution,
        rig_geometry=RIG,
        camera_tilt_calibration=TILT,
        target_line_record=_line_record(),
        lens=_lens(),
        camera_tilt_path=tilt_path,
    )

    command = ts.action_commands(
        action,
        params,
        tmp_path,
        RIG,
        tester_setup=TESTER_SETUP,
        handed_to_swings=handed,
        light_index_dn_per_us_gain=0.5 if action == "free_play" else None,
    )[0][0]

    setup_file = command[command.index("--setup-file") + 1]
    assert setup_file == str(ts.tester_root(tmp_path, TESTER) / ts.SETUP_FILE)
    present = [token for token in command if any(token.startswith(flag) for flag in REMOVED_FLAGS)]
    assert present == []
    assert handed["cli_args"] == [token for token in handed["cli_args"] if token in command], (
        "the record says what the kiosk was given"
    )
    # the kept flags
    assert command[command.index("--rig-geometry") + 1] == str(RIG)
    assert command[command.index("--camera-vertical-offset-deg") + 1] == "1.25"
    assert command[command.index("--camera-vertical-offset-source") + 1] == "unit_calibration"
    assert command[command.index("--camera-tilt-calibration-path") + 1] == str(tilt_path)
    assert command[command.index("--handed") + 1] == "RH"
    assert command[command.index("--lis3dh-mount-file") + 1] == "lis3dh-mount.json"
    assert command[command.index("--tester-config-hash") + 1] == "test-config"
    if action == "free_play":
        assert command[command.index("--camera-light-index") + 1] == "0.5"


def test_a_typed_lis3dh_zero_offset_refuses_the_launch(tmp_path):
    params = _params()
    ts.write_arm_state(tmp_path, params, gain=3.0, gain_exposure_us=params.arm.exposure_us)

    with pytest.raises(ValueError, match="zero offset"):
        ts.action_commands(
            "swings",
            params,
            tmp_path,
            RIG,
            tester_setup={**TESTER_SETUP, "inclinometer_zero_offset_deg": 1.5},
        )


def test_the_tester_s_sections_are_the_kiosk_s_shape():
    sections = _sections(_prior_solution(), tilt=TILT)

    assert set(sections) == set(SECTIONS)
    box = sections["box"]
    assert box["state"] == "confirmed"
    # the setup's camera search saw the ball: it is the box's ball
    assert box["ball_px"] == [640.0, 450.0] and box["diameter_px"] == 20.0
    assert box["image_size_px"] == [1280, 800] and box["box_px"] == [480, 380, 800, 520]
    assert box["uncertainty"] == {"tilt_pad_deg": 1.0, "roll_pad_deg": 2.0}
    assert box["inputs"] == {
        "lens_id": _lens().to_dict()["effective_lens_id"],
        "rig_sha256": RigGeometry.from_json(RIG).snapshot()["sha256"],
        "tilt_deg": 1.25,
    }
    aim = sections["aim"]
    assert aim["state"] == "set" and aim["yaw_deg"] == 2.5 and aim["uncertainty_deg"] == 0.8
    assert aim["inputs"] == {"box_id": box["id"]}
    rng = sections["ball_range"]
    assert rng["source"] == "camera_prior"
    assert (rng["range_m"], rng["uncertainty_m"], rng["side_offset_m"]) == (2.31, 0.35, 0.02)
    assert rng["inputs"] == {"box_id": box["id"]}
    net = sections["net_range"]
    assert net["state"] == "default" and net["value_m"] == 4.6
    assert net["source"] == "default_not_measured"
    scene = sections["scene"]
    assert scene["source"] == "rig_nominal" and scene["state"] == "default"
    assert scene["inputs"] == {"ball_range_m": 2.31}


def test_an_unbounded_aim_is_not_set_unless_its_direction_is_bounded():
    unbounded = _line_record(total=None)

    assert _sections(_prior_solution(), line=unbounded)["aim"]["state"] == "not_set"

    direction = {**_line_record(total=1.4), "yaw_deg": 2.0, "floor_line_px": None}
    floor = [[100.0, 600.0], [1100.0, 600.0]]
    with_fallback = {**unbounded, "floor_line_px": floor, "setup_direction": direction}
    aim = _sections(_prior_solution(), line=with_fallback)["aim"]

    assert aim["state"] == "set" and aim["yaw_deg"] == 2.0 and aim["uncertainty_deg"] == 1.4
    # the floor line stays, for the net distance alone
    assert aim["floor_line"] == floor and aim["floor_line_used"] is False


def test_radar_diagnostic_supplies_radar_range_via_hook():
    sections = _sections(_radar_solution(solved_height=0.181))

    rng = sections["ball_range"]
    assert rng["source"] == "radar"
    assert (rng["range_m"], rng["uncertainty_m"], rng["side_offset_m"]) == (2.274, 0.04, -0.03)
    scene = sections["scene"]
    assert scene["source"] == "radar_solved_height" and scene["state"] == "set"
    assert scene["lens_height_m"] == 0.181 and scene["uncertainty_m"] == 0.02
    assert scene["inputs"] == {"ball_range_m": 2.274}


def test_writing_the_same_setup_again_keeps_its_revision(tmp_path):
    path = tmp_path / ts.SETUP_FILE
    sections = _sections(_prior_solution())

    first = ts.write_tester_setup(path, sections)
    again = ts.write_tester_setup(path, _sections(_prior_solution()))

    assert first["revision"] == again["revision"] == 1
    moved = ts.write_tester_setup(path, _sections(_radar_solution()))
    assert moved["revision"] == 2 and load(path)["ball_range"]["source"] == "radar"


@pytest.mark.parametrize(("width", "height"), [(1280, 800), (640, 400)])
def test_a_kiosk_started_from_the_tester_s_record_applies_it_with_nothing_stale(
    kiosk, start_kiosk, monkeypatch, tmp_path, width, height
):
    tilt_path = tmp_path / "camera-tilt.json"
    tilt_path.write_text(json.dumps({"status": "calibrated", **TILT}), encoding="utf-8")
    ts.write_tester_setup(kiosk.path, _sections(_prior_solution(), tilt=TILT))
    args, _record = ts.tee_range_handoff(
        _prior_solution(),
        rig_geometry=RIG,
        camera_tilt_calibration=TILT,
        target_line_record=_line_record(),
        lens=_lens(),
        camera_tilt_path=tilt_path,
    )
    apply_saved = server.apply_saved_setup

    def with_camera():
        monkeypatch.setattr(
            server,
            "camera_capture_runtime",
            SimpleNamespace(
                settings=CameraCaptureSettings(width=width, height=height), stop=lambda: None
            ),
        )
        return apply_saved()

    monkeypatch.setattr(server, "apply_saved_setup", with_camera)

    started = start_kiosk(*args)

    assert {name: started[name]["state"] for name in SECTIONS} == {
        "box": "confirmed",
        "aim": "set",
        "ball_range": "set",
        "net_range": "default",
        "scene": "default",
    }
    assert started["unapplied"] == {}
    scale = width / 1280
    assert server.camera_ball_box_px == tuple(round(v * scale) for v in (480, 380, 800, 520))
    assert server.setup_handoff_config["target_line"]["yaw_deg"] == 2.5
    assert server.horizontal_offset_deg == -2.5
    assert server.setup_handoff_config["tee_range"]["prior"]["range_m"] == 2.31


@pytest.fixture(name="bound_run")
def _bound_run(monkeypatch):
    monkeypatch.setattr(tester_hooks, "tester_setup_required", True)
    monkeypatch.setattr(tester_hooks, "tester_config_hash", "test-config")
    kiosk_extensions.register(tester_hooks.extension)


def test_setup_refused_during_bound_run(kiosk, scene, bound_run):  # noqa: ARG001
    _start(kiosk)

    response = kiosk.client.post("/api/setup/box", json={"action": "confirm"})

    assert response.status_code == 409
    body = response.get_json()
    assert body["ok"] is False and body["reason"] == "a run is in progress"
    assert not kiosk.path.exists()


def test_setup_is_allowed_when_no_run_is_bound(monkeypatch):
    monkeypatch.setattr(tester_hooks, "tester_setup_required", False)
    monkeypatch.setattr(tester_hooks, "tester_config_hash", None)

    assert tester_hooks.extension.before_setup_action("box", "confirm", {}) is None
