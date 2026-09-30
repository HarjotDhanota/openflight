"""P7-15: the hitting zone the tester is judged on is the confirmed placement box.

Wherever the light used to be judged on the fixed box (rows 45-90 %, columns
20-80 %), the tester's confirmed box replaces it: the gain screens (B), the
ladder's zone floor and zone checks, and the kiosk's capture-time zone rule. The
fixed box stays only as the fallback when no box was confirmed, and every stored
zone result says which one it was.
"""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from openflight import tee_range
from openflight.camera import study_ladder as sl, tester_server as ts
from openflight.camera.auto_exposure import measure_exposure
from openflight.camera.capture_runtime import (
    CameraCaptureRuntime,
    CameraCaptureSettings,
    parse_hitting_zone,
)
from openflight.camera.triggered_buffer import CameraFrame, TriggeredCapture

# A confirmed box around the ball the ladder tests draw at (640, 520), 1280x800.
BOX = (562, 470, 718, 600)
BLACK = 18.0
SETUP_BALL = {"x": 640.0, "y": 520.0, "diameter_px": 20.0}
RIG = Path(__file__).resolve().parents[1] / "config" / "enclosure_v3_rig_geometry.json"
TESTER_SETUP = {
    "config_hash": "test-config",
    "inclinometer_bus": 1,
    "inclinometer_address": 0x18,
    "inclinometer_zero_offset_deg": 0.0,
}


def _frames(level=60.0, *, count=5, box_level=None, patch_rows=None, ball_level=None):
    """Flat frames; optionally the box at ``box_level``, a sunlit patch, and the ball."""
    rng = np.random.default_rng(3)
    frames = np.clip(level + rng.normal(0, 1.0, (count, 800, 1280)), 0, 255)
    if patch_rows is not None:
        # a sunlit strip of mat inside the fixed zone but outside the box
        frames[:, patch_rows[0] : patch_rows[1], 256:1024] = 255
    if box_level is not None:
        x0, y0, x1, y1 = BOX
        frames[:, y0:y1, x0:x1] = box_level
    if ball_level is not None:
        yy, xx = np.indices((800, 1280))
        frames[:, np.hypot(xx - 640, yy - 520) <= 10] = ball_level
    return frames.astype(np.uint8)


# --- the meter itself -------------------------------------------------------------


def test_the_exposure_meter_rates_the_box_it_is_given():
    image = _frames(patch_rows=(620, 720), ball_level=180)[0]

    fixed = measure_exposure(image)
    boxed = measure_exposure(image, zone_box=BOX)

    assert fixed.zone_source == "fixed"
    assert fixed.clipped_pct > 20.0  # the sunlit strip is in the fixed box
    assert boxed.zone_source == "placement_box"
    assert list(boxed.zone_box_px) == list(BOX)
    assert boxed.clipped_pct == pytest.approx(0.0)
    assert boxed.to_dict()["zone_source"] == "placement_box"


def test_a_box_partly_off_the_frame_is_clipped_to_it():
    image = np.full((400, 640), 90, np.uint8)

    observation = measure_exposure(image, zone_box=(600, 350, 700, 450))

    assert observation.sample_available is True
    assert list(observation.zone_box_px) == [600, 350, 640, 400]


# --- the ladder -------------------------------------------------------------------


def test_the_ladder_judges_a_clipped_box_not_the_fixed_zone():
    frames = _frames(box_level=255)

    fixed = sl.judge_light(frames, BLACK)
    boxed = sl.judge_light(frames, BLACK, zone_box=BOX)

    # the box is 7 % of the fixed zone: a clipped background there
    assert fixed["cause"] == "background_clipped"
    assert fixed["zone"]["zone_source"] == "fixed"
    assert "of the hitting zone clipped" in fixed["message"]
    # all of the tester's box is clipped: too bright
    assert boxed["cause"] == "zone_clipped_no_ball"
    assert boxed["zone"]["zone_source"] == "placement_box"
    assert boxed["zone"]["zone_box_px"] == list(BOX)
    assert "100% of the box is clipped" in boxed["message"]


def test_a_dark_box_is_the_zone_floor_even_when_the_fixed_zone_is_lit():
    frames = _frames(level=90.0, box_level=22)

    boxed = sl.judge_light(frames, BLACK, zone_box=BOX)

    assert sl.judge_light(frames, BLACK)["cause"] == "ok"
    assert boxed["cause"] == "zone_dark"
    assert "the box is 4 DN above black" in boxed["message"]


def test_the_pre_rung_check_records_the_zone_it_judged():
    frames = _frames(patch_rows=(620, 720), ball_level=180)

    check = sl.pre_rung_check(frames, BLACK, 4.0, expected_ball=SETUP_BALL, zone_box=BOX)

    assert check["ok"] is True
    assert check["light_cause"] == "ok"  # the strip is outside the box
    assert check["zone_source"] == "placement_box"
    assert check["zone_box_px"] == list(BOX)


def _capture(tmp_path, frames, name="camera_1"):
    folder = tmp_path / name
    folder.mkdir()
    count = len(frames)
    np.savez(
        folder / "frames.npz",
        frames=frames,
        exposure_us=np.full(count, 150, np.int32),
        analogue_gain=np.full(count, 8.0, np.float32),
        pre_trigger_count=np.int32(count - 3),
    )
    (folder / "metadata.json").write_text(json.dumps({"delivered_fps": 120.0, "gap_count": 0}))
    return folder


def test_a_swing_verdict_is_judged_on_the_box_and_says_so(tmp_path):
    folder = _capture(tmp_path, _frames(count=12, patch_rows=(620, 720), ball_level=180))
    rung = sl.Rung("full-150", "arm5", 150, True)

    fixed = sl.swing_verdict(folder, rung, 8.0, BLACK, [], SETUP_BALL)
    boxed = sl.swing_verdict(folder, rung, 8.0, BLACK, [], SETUP_BALL, zone_box=BOX)

    assert fixed["color"] == "amber" and fixed["zone_source"] == "fixed"
    assert boxed["color"] == "green", boxed["reasons"]
    assert boxed["zone_source"] == "placement_box"
    assert boxed["clipped_pct"] == pytest.approx(0.0)


class StripKiosk:
    """A kiosk whose frames carry a sunlit strip outside the box."""

    def __init__(self):
        self.calls = []

    def ready(self):
        return True

    def set_controls(self, exposure_us, gain, purpose="capture"):
        del purpose
        self.calls.append((exposure_us, gain))
        return {"exposure_us": exposure_us, "gain": gain}

    def frames_with_controls(self, count):
        exposure, gain = self.calls[-1]
        return {
            "frames": _frames(count=count, patch_rows=(620, 720), ball_level=180),
            "exposure_us": np.full(count, exposure, np.int32),
            "gain": np.full(count, gain, np.float32),
        }


def test_the_ladder_runner_judges_each_mode_on_its_box(tmp_path):
    runner = sl.LadderRunner(
        sl.LadderState(tmp_path / "ladder.json"),
        StripKiosk(),
        run_dir=lambda: None,
        black_floor=lambda arm: BLACK,
        gain_at_300=lambda arm: 3.0,
        light_index=lambda arm: 0.05,
        photo_dir=tmp_path / "impact",
        on_mode_done=lambda arm: None,
        ready_timeout_s=1.0,
        expected_ball=lambda arm: SETUP_BALL,
        zone_box=lambda arm: BOX if arm == "arm5" else None,
    )

    runner.start_rung()

    check = runner.state.to_dict()["rungs"]["full-300"]["pre_check"]
    assert check["zone_source"] == "placement_box"
    assert check["zone_box_px"] == list(BOX)


# --- capture time (the kiosk) -----------------------------------------------------


def test_the_hitting_zone_argument_is_four_ordered_pixels():
    assert parse_hitting_zone("562,470,718,600") == (562, 470, 718, 600)
    assert parse_hitting_zone(None) is None
    for bad in ("1,2,3", "5,5,4,9", "1,2,3,x", "-1,2,3,4", "1,2,1,4"):
        with pytest.raises(ValueError):
            parse_hitting_zone(bad)


def _frame(image):
    return CameraFrame(
        image=image,
        sensor_timestamp_ns=1_000_000_000,
        host_timestamp_ns=1_000_001_000,
        exposure_us=150,
        analogue_gain=8.0,
    )


def test_the_kiosks_zone_rule_falls_back_to_the_box_it_was_given():
    image = _frames(patch_rows=(620, 720), ball_level=180)[0]
    settings = CameraCaptureSettings(
        width=1280, height=800, fps=120.0, auto_exposure=False, hitting_zone=BOX
    )
    snapshot = {
        "payload": {},
        "settings": settings,
        "controls_purpose": "capture",
        "frame": _frame(image),
    }

    payload = CameraCaptureRuntime._auto_exposure_payload(snapshot)  # pylint: disable=protected-access

    assert payload["observation"]["zone_source"] == "placement_box"
    assert payload["observation"]["zone_box_px"] == list(BOX)
    assert payload["observation"]["clipped_pct"] == pytest.approx(0.0)


def _triggered(frames):
    return TriggeredCapture(
        frames=tuple(
            CameraFrame(
                image=image,
                sensor_timestamp_ns=1_000_000_000 + index * 8_333_333,
                host_timestamp_ns=1_000_000_000 + index * 8_333_333 + 1_000,
                exposure_us=150,
                analogue_gain=8.0,
            )
            for index, image in enumerate(frames)
        ),
        pre_trigger_count=len(frames) - 2,
        trigger_host_timestamp_ns=1_000_000_000,
    )


def test_a_clip_judged_on_the_setup_ball_records_the_box_zone(tmp_path):
    frames = _frames(count=10, patch_rows=(620, 720), ball_level=180)
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(
            width=1280,
            height=800,
            fps=120.0,
            auto_exposure=False,
            setup_ball=SETUP_BALL,
            hitting_zone=BOX,
        ),
    )

    observation = measure_exposure(frames[-1])
    payload = {
        "status": "manual",
        "analysis_eligible": observation.acceptable,
        "message": observation.message,
        "observation": observation.to_dict(),
        "exposure_us": 150,
        "gain": 8.0,
        "controls_purpose": "capture",
    }

    saved = runtime._save_capture(  # pylint: disable=protected-access
        1, 123.0, _triggered(frames), auto_exposure=payload
    )

    judged = saved.metadata["auto_exposure"]["analysis_eligibility"]
    assert judged["zone"]["zone_source"] == "placement_box"
    assert judged["zone"]["zone_box_px"] == list(BOX)


def _run_kiosk_until_camera_init(monkeypatch, tmp_path, *extra):
    from openflight import server as server_module  # noqa: PLC0415

    received = {}

    class StopAfterCameraInit(Exception):
        pass

    def fake_init_camera_capture(**kwargs):
        received.update(kwargs)
        raise StopAfterCameraInit

    monkeypatch.setattr(server_module, "init_camera_capture", fake_init_camera_capture)
    monkeypatch.setattr(server_module, "init_session_logger", lambda **kwargs: None)
    monkeypatch.setattr(server_module, "profile_store", None)
    monkeypatch.setattr(
        "sys.argv",
        [
            "openflight-server",
            "--camera-capture",
            "--camera-capture-manual-exposure",
            "--rig-geometry",
            "config/enclosure_v3_rig_geometry.json",
            "--study-mode",
            "--no-logging",
            "--profiles-path",
            str(tmp_path / "profiles.json"),
            *extra,
        ],
    )
    with pytest.raises((StopAfterCameraInit, SystemExit)) as raised:
        server_module.main()
    return received, raised.value


def test_the_kiosk_hands_the_box_to_the_camera(monkeypatch, tmp_path):
    received, _stop = _run_kiosk_until_camera_init(
        monkeypatch, tmp_path, "--camera-hitting-zone", "562,470,718,600"
    )

    assert received["hitting_zone"] == BOX


def test_a_kiosk_without_a_box_keeps_the_fixed_zone(monkeypatch, tmp_path):
    received, _stop = _run_kiosk_until_camera_init(monkeypatch, tmp_path)

    assert received["hitting_zone"] is None


def test_a_malformed_box_stops_the_kiosk(monkeypatch, tmp_path):
    received, stop = _run_kiosk_until_camera_init(
        monkeypatch, tmp_path, "--camera-hitting-zone", "562,470,718"
    )

    assert isinstance(stop, SystemExit) and stop.code == 2
    assert received == {}


# --- the gain screen (B) ----------------------------------------------------------

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "hardware-test"
    / "calibrate_camera_exposure.py"
)


def _script():
    spec = importlib.util.spec_from_file_location("calibrate_camera_exposure", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_gain_screen_measures_the_box_and_records_it():
    images = _frames(count=3, patch_rows=(620, 720), ball_level=180)
    module = _script()

    fixed = module.summarize_images(images, 300, 4.0)
    boxed = module.summarize_images(images, 300, 4.0, zone_box=BOX)

    assert fixed["zone_source"] == "fixed" and fixed["zone_clipped_pct"] > 20.0
    assert boxed["zone_source"] == "placement_box"
    assert boxed["zone_box_px"] == list(BOX)
    assert boxed["zone_clipped_pct"] < 1.0
    assert module.parse_args(["--zone-box", "562,470,718,600"]).zone_box == BOX
    assert module.parse_args([]).zone_box is None


def test_choose_gain_keeps_the_zone_it_was_judged_on():
    rows = [
        {
            "gain": gain,
            "mean": 60.0 * gain,
            "zone_median": 40.0 * gain,
            "zone_clipped_pct": 0.0,
            "zone_source": "placement_box",
            "zone_box_px": list(BOX),
        }
        for gain in (1.0, 2.0, 3.0)
    ]

    choice = ts.choose_gain(rows)

    assert choice["zone_source"] == "placement_box"
    assert choice["zone_box_px"] == list(BOX)


def _params(arm_id):
    return ts.TesterParameters("20260922-name", arm_id, "indoors")


@pytest.mark.parametrize(
    "arm_id, expected", [("arm5", "562,470,718,600"), ("arm6", "281,235,359,300")]
)
def test_the_gain_screen_is_told_the_box_in_its_mode(tmp_path, arm_id, expected):
    record = {"box_px": list(BOX), "frame_size_px": [1280, 800]}
    box = ts.mode_placement_box(record, ts.ARMS[arm_id])

    command = ts.action_commands("gain", _params(arm_id), tmp_path, RIG, zone_box=box)[0][0]
    fallback = ts.action_commands("gain", _params(arm_id), tmp_path, RIG)[0][0]

    assert command[command.index("--zone-box") + 1] == expected
    assert "--zone-box" not in fallback


def _solution_with_box():
    confirmed = {
        "arm_id": "arm5",
        "frame_size_px": [1280, 800],
        "box_px": list(BOX),
        "size_px": [156, 130],
        "source": "tester_dragged",
    }
    camera = tee_range.TeeRangeCandidate(
        candidate_id="camera-setup-1-arm5",
        source="camera_reference_ball_size_range",
        source_group="camera",
        radar_slant_range_m=1.2,
        uncertainty_m=0.25,
        evidence={
            "result": {
                "status": "selected",
                "selected": {"x_px": 640.0, "y_px": 520.0, "diameter_px": 20.0},
            },
            "placement_box": {"box_px": list(BOX), "confirmed": confirmed},
        },
    )
    return tee_range.TeeRangeSolution.unresolved([camera], reason="test")


@pytest.mark.parametrize(
    "arm_id, expected", [("arm5", "562,470,718,600"), ("arm6", "281,235,359,300")]
)
def test_a_ladder_kiosk_is_told_the_setups_box(tmp_path, arm_id, expected):
    params = _params(arm_id)
    ts.write_arm_state(tmp_path, params, gain=3.0, gain_exposure_us=300)
    for action in ("ladder", "swings"):
        with_box = ts.action_commands(
            action,
            params,
            tmp_path,
            RIG,
            tester_setup=TESTER_SETUP,
            tee_range_solution=_solution_with_box(),
        )[0][0]
        without = ts.action_commands(action, params, tmp_path, RIG, tester_setup=TESTER_SETUP)[0][0]

        assert with_box[with_box.index("--camera-hitting-zone") + 1] == expected
        assert "--camera-hitting-zone" not in without


def test_the_ladders_zone_comes_from_the_setups_box():
    solution = _solution_with_box()

    assert ts.ladder_zone_box(solution, "arm5") == BOX
    assert ts.ladder_zone_box(solution, "arm6") == (281, 235, 359, 300)
    assert ts.ladder_zone_box(None, "arm5") is None
