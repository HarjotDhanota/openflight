"""Tests for the mode-study capture runner."""

from __future__ import annotations

import json
import math
import struct
import threading
import time
import zipfile
import zlib

import numpy as np
import pytest

from openflight.camera import tester_server as ts

RIG = ts.DEFAULT_RIG_GEOMETRY
TESTER_SETUP = {
    "config_hash": "test-config",
    "inclinometer_bus": 1,
    "inclinometer_address": 0x18,
    "inclinometer_zero_offset_deg": 0.0,
}


class _EligibleSetup:
    def _result(self, tester_id):
        return {
            "eligible": True,
            "config_hash": "test-config",
            "tester_id": tester_id,
            "operator_confirmation": {"confirmed": True, "confirmed_at": "test"},
            "checks": [],
            "blockers": [],
        }

    def evaluate(self, tester_id, _reading):
        return self._result(tester_id)

    def require(self, tester_id, _reading, _action):
        return self._result(tester_id)

    def confirmation_valid(self, _tester_id):
        return True

    def current_config_hash(self):
        return "test-config"


def eligible_app(**kwargs):
    return ts.create_app(setup_policy=_EligibleSetup(), **kwargs)


def params(**overrides):
    payload = {
        "tester_id": "20260922-name",
        "arm_id": "arm1",
        "environment": "indoors",
    }
    payload.update(overrides)
    return ts.TesterParameters.from_payload(payload)


def screened(root, p, gain=6.0):
    """An arm whose gain step ran at its own exposure."""
    ts.write_arm_state(root, p, gain=gain, gain_exposure_us=p.arm.exposure_us)


class TestTheArmsAreThePlan:
    def test_the_five_arms_exist_in_order(self):
        assert ts.ARM_ORDER == ("arm1", "arm2", "arm3", "arm4", "arm5", "arm6")
        shape = {a: (x.width, x.fps, x.exposure_us) for a, x in ts.ARMS.items() if a != "arm6"}
        assert shape == {
            "arm1": (320, 450.0, 300),
            "arm2": (320, 450.0, 175),
            "arm3": (320, 450.0, 87),
            "arm4": (640, 120.0, 300),
            "arm5": (1280, 120.0, 300),
        }

    def test_the_ceiling_is_a_millimetre_budget_the_same_in_every_mode(self):
        # 4 mm of smear at the 7-iron's measured 13.6 m/s across the image
        assert ts.EXPOSURE_CEILING_US * 13.6 / 1000 == pytest.approx(4.0, abs=0.1)
        for arm_id in ("arm1", "arm4", "arm5"):
            assert ts.ARMS[arm_id].exposure_us == ts.EXPOSURE_CEILING_US

    def test_it_is_a_seven_iron_study_of_five_swings(self):
        assert ts.CLUB == "7-iron"
        assert ts.SWINGS_PER_ARM == 5


class TestParameters:
    def test_tester_id_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="tester_id"):
            params(tester_id="../../etc")

    def test_unknown_arm_is_rejected(self):
        with pytest.raises(ValueError, match="unknown arm"):
            params(arm_id="arm9")

    def test_environment_is_one_tap(self):
        with pytest.raises(ValueError, match="indoors or outdoors"):
            params(environment="garage")

    def test_no_geometry_and_no_exposure_are_typed(self):
        p = params()
        assert not hasattr(p, "geometry")
        assert not hasattr(p, "exposure_us")


class TestGainChoice:
    def test_lowest_acceptable_gain_wins(self):
        results = [
            {"gain": 2.0, "mean": 40.0, "clipped_pct": 0.0},
            {"gain": 6.0, "mean": 95.0, "clipped_pct": 0.1},
            {"gain": 10.0, "mean": 140.0, "clipped_pct": 0.08},
        ]
        choice = ts.choose_gain(results)
        assert choice["gain"] == 6.0
        assert choice["lighting_required"] is False

    def test_clipping_disqualifies_even_when_the_mean_is_in_band(self):
        results = [
            {"gain": 4.0, "mean": 120.0, "clipped_pct": 3.0},
            {"gain": 8.0, "mean": 130.0, "clipped_pct": 0.05},
        ]
        assert ts.choose_gain(results)["gain"] == 8.0

    def test_nothing_acceptable_says_lighting_required_at_the_ceiling(self):
        results = [
            {"gain": 8.0, "mean": 30.0, "clipped_pct": 0.0},
            {"gain": 12.0, "mean": 40.0, "clipped_pct": 0.0},
            {"gain": 15.9, "mean": 60.0, "clipped_pct": 0.0},
        ]
        choice = ts.choose_gain(results)
        assert choice["lighting_required"] is True
        assert choice["gain"] == 12.0

    def test_a_gain_above_the_ceiling_is_never_picked_even_in_band(self):
        # above ~12x the floor lifts: brighter frames, no more signal
        results = [
            {"gain": 12.0, "mean": 70.0, "clipped_pct": 0.0},
            {"gain": 14.0, "mean": 90.0, "clipped_pct": 0.0},
        ]
        choice = ts.choose_gain(results)
        assert choice["gain"] == 12.0 and choice["lighting_required"] is True

    def test_empty_screen_is_an_error(self):
        with pytest.raises(ValueError):
            ts.choose_gain([])

    def test_light_index_is_the_slope_above_the_floor_at_the_applied_exposure(self):
        # the first real screen: 87 us requested, 80 applied, floor 31.7; the
        # top gains lift the floor and must not bend the fit
        results = [
            {
                "exposure_us": 87,
                "metadata_exposure_us": 80,
                "gain": g,
                "metadata_gain": g,
                "mean": 31.66 + 0.57 * g,
            }
            for g in (2.0, 4.0, 6.0, 8.0, 10.0, 12.0)
        ] + [
            {"exposure_us": 87, "metadata_exposure_us": 80, "gain": 14.0, "mean": 44.9},
            {"exposure_us": 87, "metadata_exposure_us": 80, "gain": 15.9, "mean": 60.7},
        ]
        light = ts.light_index(results)
        assert light["light_index"] == pytest.approx(0.57 / 80)
        assert light["black_floor_dn"] == pytest.approx(31.66)

    def test_light_index_is_the_same_whatever_gain_is_picked(self):
        results = [
            {"exposure_us": 300, "gain": g, "mean": 16.0 + 2.1 * g} for g in (2.0, 6.0, 10.0)
        ]
        assert ts.light_index(results)["light_index"] == pytest.approx(2.1 / 300)

    def test_one_gain_cannot_separate_light_from_floor(self):
        assert ts.light_index([{"exposure_us": 300, "gain": 4.0, "mean": 50.0}]) == {
            "light_index": None,
            "black_floor_dn": None,
        }


class TestCommands:
    def test_unknown_action_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="unknown tester action"):
            ts.action_commands("rm -rf /", params(), tmp_path, RIG)

    def test_gain_step_screens_gain_at_the_arms_exposure(self, tmp_path):
        commands, log_path = ts.action_commands("gain", params(arm_id="arm5"), tmp_path, RIG)
        command = commands[0]
        assert command[command.index("--exposures-us") + 1] == "300"
        assert command[command.index("--gains") + 1] == ts.GAIN_SCREEN
        assert "--no-prompt" in command
        assert log_path.name == "gain.log"

    def test_every_arm_screens_its_own_gain(self, tmp_path):
        commands, _ = ts.action_commands("gain", params(arm_id="arm3"), tmp_path, RIG)
        assert commands[0][commands[0].index("--exposures-us") + 1] == "87"

    def test_a_gain_screened_at_another_exposure_is_stale(self, tmp_path):
        p = params(tee_mm=1524)
        ts.write_arm_state(tmp_path, p, gain=15.9, gain_exposure_us=87)
        with pytest.raises(RuntimeError, match="gain step"):
            ts.action_commands("swings", p, tmp_path, RIG)

    def test_swings_refuse_to_run_before_the_gain_is_known(self, tmp_path):
        with pytest.raises(RuntimeError, match="gain step"):
            ts.action_commands("swings", params(), tmp_path, RIG)

    def test_swings_drive_the_kiosk_with_rig_geometry_and_inclinometer(self, tmp_path):
        p = params(arm_id="arm4", tee_mm=1524)
        screened(tmp_path, p)
        commands, _ = ts.action_commands("swings", p, tmp_path, RIG, tester_setup=TESTER_SETUP)
        command = commands[0]
        assert command[1].endswith("start-kiosk.sh")
        for flag in ("--debug", "--iwr6843", "--inclinometer", "--camera-capture"):
            assert flag in command
        assert command[command.index("--rig-geometry") + 1] == str(RIG)
        assert command[command.index("--camera-capture-exposure-us") + 1] == "300"
        assert command[command.index("--camera-capture-gain") + 1] == "6.0"
        assert command[command.index("--camera-capture-width") + 1] == "640"
        assert command[command.index("--session-location") + 1] == "arm4"
        # the fixes the first run would otherwise have tripped on
        assert "--camera-capture-manual-exposure" in command
        assert command[command.index("--radar-port") + 1] == "/dev/ttyAMA0"
        assert command[command.index("--club") + 1] == "7-iron"
        assert command[command.index("--iwr6843-tee-m") + 1] == "1.524"
        assert command[command.index("--log-dir") + 1].endswith("run-01")

    def test_the_exposure_arms_share_arm_1s_mode(self, tmp_path):
        p = params(arm_id="arm2", tee_mm=1524)
        screened(tmp_path, p)
        command = ts.action_commands("swings", p, tmp_path, RIG, tester_setup=TESTER_SETUP)[0][0]
        assert command[command.index("--camera-capture-exposure-us") + 1] == "175"
        assert command[command.index("--camera-capture-width") + 1] == "320"
        assert command[command.index("--camera-capture-fps") + 1] == "450.0"

    def test_calibrated_fusion_files_reach_every_capture_kiosk(self, tmp_path):
        p = params(arm_id="arm4", tee_mm=1524)
        screened(tmp_path, p)
        optical = tmp_path / "optical.json"
        placement = tmp_path / "placement.json"
        command = ts.action_commands(
            "swings",
            p,
            tmp_path,
            RIG,
            tester_setup=TESTER_SETUP,
            optical_calibration=optical,
            camera_placement=placement,
        )[0][0]
        assert command[command.index("--camera-optical-calibration") + 1] == str(optical)
        assert command[command.index("--camera-placement") + 1] == str(placement)

    @pytest.mark.parametrize("missing", ["calibration", "placement"])
    def test_capture_refuses_a_partial_calibrated_fusion_configuration(self, tmp_path, missing):
        p = params(arm_id="arm4", tee_mm=1524)
        screened(tmp_path, p)
        options = {
            "optical_calibration": tmp_path / "optical.json",
            "camera_placement": tmp_path / "placement.json",
        }
        options["optical_calibration" if missing == "calibration" else "camera_placement"] = None
        with pytest.raises(ValueError, match="requires both calibration and placement"):
            ts.action_commands("swings", p, tmp_path, RIG, tester_setup=TESTER_SETUP, **options)


def _write_session(root, arm_id, statuses, dumps=None, run="run-01"):
    paired = root / arm_id / "paired" / run
    camera = paired / arm_id / "camera"
    camera.mkdir(parents=True)
    (paired / "iwr6843").mkdir()
    lines = []
    for index, status in enumerate(statuses, start=1):
        shot = camera / f"camera_1_{index:03d}"
        shot.mkdir()
        (shot / "frames.npz").write_bytes(b"frames")
        lines.append(
            json.dumps(
                {
                    "type": "shot_detected",
                    "shot_number": index,
                    "experimental_fused_club_path_status": status,
                }
            )
        )
    for index in range(dumps if dumps is not None else len(statuses)):
        (paired / "iwr6843" / f"iwr6843_1_{index:03d}.l3dump").write_bytes(b"dump")
    (paired / "session_20260922_000000_arm.jsonl").write_text("\n".join(lines) + "\n")


class TestProgressCountsAcceptedNotFiles:
    def test_accepted_is_the_estimators_verdict(self, tmp_path):
        root = ts.tester_root(tmp_path, "20260922-name")
        _write_session(
            root, "arm1", ["ok", "ok", "low_light", "ok", "rejected_insufficient_features"]
        )
        progress = ts.arm_progress(tmp_path, params())
        assert progress["attempted"] == 5
        assert progress["accepted"] == 3
        assert progress["complete"] is False
        assert progress["status_histogram"] == {
            "ok": 3,
            "low_light": 1,
            "rejected_insufficient_features": 1,
        }

    def test_five_accepted_completes_the_arm_however_many_were_hit(self, tmp_path):
        root = ts.tester_root(tmp_path, "20260922-name")
        _write_session(root, "arm1", ["ok"] * 5 + ["low_light"] * 3)
        progress = ts.arm_progress(tmp_path, params())
        assert progress["attempted"] == 8
        assert progress["accepted"] == 5
        assert progress["complete"] is True

    def test_missing_radar_dumps_is_a_named_problem(self, tmp_path):
        root = ts.tester_root(tmp_path, "20260922-name")
        _write_session(root, "arm1", ["ok", "ok"], dumps=0)
        progress = ts.arm_progress(tmp_path, params())
        assert any("l3dump" in p for p in progress["problems"])

    def test_overview_lists_every_arm_with_its_gain_and_progress(self, tmp_path):
        ts.write_arm_state(tmp_path, params(arm_id="arm2"), gain=6.0, light_index=0.2)
        overview = ts.study_overview(tmp_path, "20260922-name")
        by_id = {a["arm_id"]: a for a in overview["arms"]}
        assert overview["club"] == "7-iron"
        assert by_id["arm2"]["gain"] == 6.0 and by_id["arm2"]["light_index"] == 0.2
        assert by_id["arm1"]["gain"] is None


class TestPackage:
    def test_archive_carries_every_arm_state(self, tmp_path):
        ts.write_arm_state(tmp_path, params(arm_id="arm1"), gain=4.0)
        ts.write_arm_state(tmp_path, params(arm_id="arm2"), gain=6.0)
        archive = ts.package_study(tmp_path, "20260922-name")
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            assert any(n.endswith("arm1/arm.json") for n in names)
            saved = json.loads(bundle.read(next(n for n in names if n.endswith("arm2/arm.json"))))
        assert saved["gain"] == 6.0
        assert saved["club"] == "7-iron"
        assert saved["exposure_us"] == 175


class TestApp:
    def test_page_arms_and_status_are_served(self, tmp_path):
        client = eligible_app(sessions_root=tmp_path, rig_geometry=RIG).test_client()
        assert client.get("/").status_code == 200
        arms = client.get("/api/tester/arms").get_json()
        assert [a["arm_id"] for a in arms["arms"]] == [
            "arm1",
            "arm2",
            "arm3",
            "arm4",
            "arm5",
            "arm6",
        ]
        response = client.post(
            "/api/tester/status",
            json={
                "tester_id": "20260922-name",
                "arm_id": "arm1",
                "environment": "indoors",
            },
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["available"] is True
        assert len(body["study"]["arms"]) == len(ts.ARMS)

    def test_swings_before_gain_returns_409_with_the_reason(self, tmp_path):
        client = eligible_app(sessions_root=tmp_path, rig_geometry=RIG).test_client()
        response = client.post(
            "/api/tester/run",
            json={
                "tester_id": "20260922-name",
                "arm_id": "arm1",
                "environment": "indoors",
                "action": "swings",
            },
        )
        assert response.status_code == 409
        assert "gain step" in response.get_json()["error"]

    def test_invalid_request_returns_400(self, tmp_path):
        client = eligible_app(sessions_root=tmp_path, rig_geometry=RIG).test_client()
        response = client.post("/api/tester/status", json={"tester_id": "../x"})
        assert response.status_code == 400

    def test_a_second_concurrent_action_is_refused(self, tmp_path):
        class BusyManager(ts.TesterJobManager):
            def status(self):
                return {"state": "running", "action": "swings", "message": "busy", "output": []}

            def start(self, action, commands, log_path, on_finish=None):
                raise RuntimeError("another action is already running")

        client = eligible_app(
            sessions_root=tmp_path, rig_geometry=RIG, manager=BusyManager()
        ).test_client()
        response = client.post(
            "/api/tester/run",
            json={
                "tester_id": "20260922-name",
                "arm_id": "arm1",
                "environment": "indoors",
                "action": "preflight",
            },
        )
        assert response.status_code == 409


class TestRunsAndTape:
    def test_swings_refuse_without_the_taped_distance(self, tmp_path):
        screened(tmp_path, params())
        with pytest.raises(ValueError, match="radar window"):
            ts.action_commands("swings", params(), tmp_path, RIG)

    def test_a_distance_outside_the_room_is_refused(self):
        with pytest.raises(ValueError, match="radar-to-ball"):
            params(tee_mm=120)

    def test_each_capture_run_gets_its_own_folder(self, tmp_path):
        p = params(tee_mm=1524)
        screened(tmp_path, p)
        root = ts.arm_directory(tmp_path, p)
        (root / "paired" / "run-01").mkdir(parents=True)
        commands, _ = ts.action_commands("swings", p, tmp_path, RIG, tester_setup=TESTER_SETUP)
        assert commands[0][commands[0].index("--log-dir") + 1].endswith("run-02")

    def test_progress_sums_every_run(self, tmp_path):
        root = ts.tester_root(tmp_path, "20260922-name")
        _write_session(root, "arm1", ["ok", "ok", "low_light"], run="run-01")
        _write_session(root, "arm1", ["ok", "ok", "ok"], run="run-02")
        progress = ts.arm_progress(tmp_path, params())
        assert progress["runs"] == 2
        assert progress["attempted"] == 6 and progress["accepted"] == 5
        assert progress["complete"] is True

    def test_the_radar_port_can_be_overridden_per_unit(self, tmp_path):
        p = params(tee_mm=1524)
        screened(tmp_path, p)
        commands, _ = ts.action_commands(
            "swings",
            p,
            tmp_path,
            RIG,
            radar_port="/dev/ttyACM0",
            tester_setup=TESTER_SETUP,
        )
        assert commands[0][commands[0].index("--radar-port") + 1] == "/dev/ttyACM0"


class TestSolvedRange:
    def test_the_gain_screen_frame_solves_range_beside_the_tape(self, tmp_path):
        arm = ts.ARMS["arm1"]
        run = tmp_path / "gain" / "20260922_120000"
        run.mkdir(parents=True)
        (run / "results.json").write_text("[]")
        image = np.full((200, 320), 110, dtype=np.uint8)
        yy, xx = np.mgrid[0:200, 0:320]
        image[np.hypot(xx - 160, yy - 144) <= 6.0] = 230
        with (run / "exp0300_gain6_median.pgm").open("wb") as handle:
            handle.write(b"P5\n320 200\n255\n")
            handle.write(image.tobytes())
        solved = ts.solved_range(tmp_path, arm, {"gain": 6.0}, RIG)
        # 466.67 px focal x 42.67 mm over a ~12 px ball, slightly off-axis
        assert solved["solved_range_m"] == pytest.approx(1.66, abs=0.05)
        assert solved["solved_ball_diameter_px"] == pytest.approx(12.0, abs=0.5)

    def test_no_ball_is_a_reason_not_an_exception(self, tmp_path):
        run = tmp_path / "gain" / "20260922_120000"
        run.mkdir(parents=True)
        (run / "results.json").write_text("[]")
        with (run / "exp0300_gain6_median.pgm").open("wb") as handle:
            handle.write(b"P5\n320 200\n255\n" + bytes(320 * 200))
        solved = ts.solved_range(tmp_path, ts.ARMS["arm1"], {"gain": 6.0}, RIG)
        assert solved["solved_range_m"] is None and solved["solved_range_note"]


class FakeRequest:
    def __init__(self, width, height, value):
        # PiSP hands R8 over as R16: the luminance byte is the high byte
        self.raw = np.zeros((height, width * 2), np.uint8)
        self.raw[:, 1::2] = value

    def make_array(self, name):
        assert name == "raw"
        return self.raw

    def get_metadata(self):
        return {"ExposureTime": 280, "AnalogueGain": 4.0}

    def release(self):
        pass


class FakeCamera:
    opened = 0

    def __init__(self):
        FakeCamera.opened += 1
        self.config = None
        self.controls = []
        self.closed = False

    def create_video_configuration(self, **kwargs):
        return kwargs

    def configure(self, config):
        self.config = config

    def start(self):
        pass

    def set_controls(self, controls):
        self.controls.append(controls)

    def capture_request(self):
        time.sleep(0.002)
        size = self.config["raw"]["size"]
        return FakeRequest(size[0], size[1], 40)

    def stop(self):
        pass

    def close(self):
        self.closed = True


def _wait(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestLiveView:
    def setup_method(self):
        FakeCamera.opened = 0
        self.cameras = []

        def factory():
            camera = FakeCamera()
            self.cameras.append(camera)
            return camera

        self.live = ts.LiveView(camera_factory=factory)

    def teardown_method(self):
        self.live.stop()

    def test_it_streams_the_arms_mode_at_the_requested_exposure(self):
        self.live.start(ts.ARMS["arm1"], 300, 4.0, black_floor=31.7)
        assert _wait(lambda: self.live.snapshot()[0] is not None)
        image, status = self.live.snapshot()
        assert image.shape == (200, 320) and int(image.mean()) == 40
        config = self.cameras[0].config
        assert config["raw"] == {"size": (320, 200), "format": "R8"}
        assert config["controls"]["ExposureTime"] == 300
        assert config["controls"]["AeEnable"] is False
        # the arm's own frame rate, so each frame is exposed as a capture would be
        assert config["controls"]["FrameDurationLimits"] == (2222, 2222)
        assert status["applied"] == {"exposure_us": 280, "gain": 4.0}
        assert status["stats"]["above_floor"] == pytest.approx(8.3)

    def test_changing_exposure_does_not_reopen_the_camera(self):
        self.live.start(ts.ARMS["arm1"], 300, 4.0)
        assert _wait(lambda: self.live.snapshot()[0] is not None)
        self.live.start(ts.ARMS["arm1"], 87, 12.0)
        assert _wait(lambda: self.cameras[0].controls)
        assert FakeCamera.opened == 1
        assert self.cameras[0].controls[-1]["ExposureTime"] == 87
        assert self.cameras[0].controls[-1]["AnalogueGain"] == 12.0

    def test_a_long_look_lengthens_the_frame_not_the_mode(self):
        controls = ts.live_controls(ts.ARMS["arm1"], 10000, 2.0)
        assert controls["FrameDurationLimits"] == (10200, 10200)

    def test_another_arm_reopens_in_its_mode_and_stop_releases_the_camera(self):
        self.live.start(ts.ARMS["arm1"], 300, 4.0)
        assert _wait(lambda: self.live.snapshot()[0] is not None)
        self.live.start(ts.ARMS["arm5"], 300, 4.0)
        assert _wait(lambda: len(self.cameras) == 2 and self.cameras[1].config is not None)
        assert self.cameras[0].closed
        assert self.cameras[1].config["raw"]["size"] == (1280, 800)
        self.live.stop()
        assert self.cameras[1].closed and not self.live.running

    def test_a_camera_error_is_shown_not_raised(self):
        def broken():
            raise IndexError("list index out of range")

        live = ts.LiveView(camera_factory=broken)
        live.start(ts.ARMS["arm1"], 300, 4.0)
        assert _wait(lambda: live.snapshot()[1]["error"])
        assert "IndexError" in live.snapshot()[1]["error"]


class TestFrameEncoding:
    def test_png_holds_the_frame_exactly(self):
        image = (np.arange(200 * 320) % 251).astype(np.uint8).reshape(200, 320)
        png = ts.encode_png(image)
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", png[16:24])
        assert (width, height) == (320, 200)
        idat = png[png.index(b"IDAT") + 4 : png.index(b"IEND") - 8]
        rows = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(200, 321)
        assert (rows[:, 0] == 0).all()
        assert np.array_equal(rows[:, 1:], image)

    def test_boost_spreads_a_dark_frame_over_the_full_range(self):
        rng = np.random.default_rng(0)
        dark = (32 + rng.integers(0, 12, (200, 320))).astype(np.uint8)
        stretched = ts.boost(dark)
        assert stretched.min() == 0 and stretched.max() == 255


class TestLiveEndpoints:
    def _client(self, tmp_path, manager=None):
        live = ts.LiveView(camera_factory=FakeCamera)
        app = eligible_app(
            sessions_root=tmp_path, rig_geometry=RIG, manager=manager, live_view=live
        )
        return app.test_client(), live

    def test_start_serve_and_stop(self, tmp_path):
        client, live = self._client(tmp_path)
        body = {"tester_id": "20260922-name", "arm_id": "arm3", "environment": "indoors"}
        assert client.post("/api/tester/live", json={**body, "gain": 12}).status_code == 200
        assert _wait(lambda: live.snapshot()[0] is not None)
        for view in ("raw", "boost"):
            frame = client.get(f"/api/tester/live.png?view={view}")
            assert frame.status_code == 200 and frame.mimetype == "image/png"
        assert client.get("/api/tester/live").get_json()["requested"]["exposure_us"] == 87
        client.post("/api/tester/live", json={"action": "stop"})
        assert not live.running

    def test_no_frame_yet_is_503(self, tmp_path):
        client, _live = self._client(tmp_path)
        assert client.get("/api/tester/live.png").status_code == 503

    def test_out_of_range_settings_are_refused(self, tmp_path):
        client, _live = self._client(tmp_path)
        body = {"tester_id": "20260922-name", "arm_id": "arm1", "environment": "indoors"}
        assert client.post("/api/tester/live", json={**body, "exposure_us": 5}).status_code == 400
        assert client.post("/api/tester/live", json={**body, "gain": 40}).status_code == 400

    def test_a_step_closes_the_live_view_to_free_the_camera(self, tmp_path):
        class Recorder(ts.TesterJobManager):
            def start(self, action, commands, log_path, on_finish=None):
                self.started = action

        manager = Recorder()
        client, live = self._client(tmp_path, manager)
        body = {"tester_id": "20260922-name", "arm_id": "arm1", "environment": "indoors"}
        client.post("/api/tester/live", json=body)
        assert _wait(lambda: live.running)
        assert client.post("/api/tester/run", json={**body, "action": "gain"}).status_code == 202
        assert manager.started == "gain" and not live.running

    def test_the_live_view_waits_for_a_running_step(self, tmp_path):
        class Busy(ts.TesterJobManager):
            def status(self):
                return {"state": "running", "action": "swings", "message": "busy", "output": []}

        client, live = self._client(tmp_path, Busy())
        body = {"tester_id": "20260922-name", "arm_id": "arm1", "environment": "indoors"}
        assert client.post("/api/tester/live", json=body).status_code == 409
        assert not live.running


def _ball_frames(ground, ball, n=5, width=320, height=200, diameter=12.0):
    frames = np.full((n, height, width), ground, np.uint8)
    yy, xx = np.mgrid[0:height, 0:width]
    frames[:, np.hypot(xx - width * 0.5, yy - height * 0.72) <= diameter / 2] = ball
    return frames


class TestLiveBall:
    def test_a_well_lit_ball_is_found_with_its_size_and_range(self):
        readout = ts.ball_readout(_ball_frames(110, 230), ts.FOCAL_PX_2X)
        assert readout["found"] is True
        assert readout["diameter_px"] == pytest.approx(12.0, abs=1.0)
        # 466.67 px x 42.67 mm / 12 px
        assert readout["range_m"] == pytest.approx(1.66, abs=0.15)
        assert readout["ball_dn"] == 230 and readout["around_dn"] == 110
        assert readout["edge_dn_per_px"] > 0

    def test_a_dim_ball_is_found_by_its_contrast(self):
        # nowhere near saturation, but it stands out from the ground
        readout = ts.ball_readout(_ball_frames(40, 70), ts.FOCAL_PX_2X)
        assert readout["found"] is True
        assert readout["diameter_px"] == pytest.approx(12.0, abs=1.5)

    def test_no_ball_says_why(self):
        readout = ts.ball_readout(_ball_frames(40, 40), ts.FOCAL_PX_2X)
        assert readout["found"] is False and readout["reason"]

    def test_the_ring_sits_just_outside_the_ball(self):
        marked = ts.mark_ball(
            np.zeros((200, 320), np.uint8), {"x": 160.0, "y": 144.0, "diameter_px": 12.0}
        )
        assert marked[144, 168] == 255
        assert marked[144, 160] == 0

    def test_the_live_view_reports_the_detector_verdict(self):
        live = ts.LiveView(camera_factory=FakeCamera)
        live.start(ts.ARMS["arm1"], 300, 4.0)
        try:
            assert _wait(lambda: live.snapshot()[1]["ball"] is not None)
            ball = live.snapshot()[1]["ball"]
            # the fake camera's frame is a flat 40: nothing to find, and it says so
            assert ball["found"] is False and ball["reason"]
        finally:
            live.stop()


class TestTheTapeGivesTheBallsSize:
    def test_the_tape_runs_from_the_radar_window_behind_the_lens(self):
        # 1071 mm from the radar window is 1041 mm from the lens in the v3 rig
        expected = ts.expected_ball_diameter_px(ts.ARMS["arm5"], 1071.0, RIG)
        assert expected == pytest.approx(ts.FOCAL_PX_1X * ts.BALL_DIAMETER_MM / 1041.0)
        assert ts.expected_ball_diameter_px(ts.ARMS["arm5"], None, RIG) is None

    def test_the_readout_puts_the_tape_beside_the_picture(self):
        readout = ts.ball_readout(_ball_frames(110, 230), ts.FOCAL_PX_2X, 12.0)
        assert readout["found"] is True
        assert readout["expected_diameter_px"] == 12.0
        assert readout["image_only_diameter_px"] == pytest.approx(12.0, abs=1.5)
        assert "size_check" not in readout

    def test_a_tape_far_from_the_picture_names_the_suspects(self):
        readout = ts.ball_readout(_ball_frames(110, 230), ts.FOCAL_PX_2X, 24.0)
        assert "check the tape" in readout.get("size_check", "")


class TestTheCameraSaysHowFar:
    def test_both_routes_agree_with_the_tape_on_a_level_camera(self):
        # a ball 1041 mm from the lens, on the floor, centred: where a level
        # 2.8 mm camera 95 mm up would see it
        focal, drop = ts.FOCAL_PX_1X, 95.0 - ts.BALL_DIAMETER_MM / 2
        along = (1041.0**2 - drop**2) ** 0.5
        ball = {
            "x": 640.0,
            "y": 400.0 + focal * drop / along,
            "diameter_px": focal * ts.BALL_DIAMETER_MM / 1041.0,
        }
        cues = ts.distance_cues(ball, ts.ARMS["arm5"], 1071.0, RIG)
        assert cues["tape_mm"] == 1041
        assert cues["from_size_mm"] == pytest.approx(1041, abs=2)
        assert cues["from_floor_mm"] == pytest.approx(1041, abs=2)
        assert cues["pitch_needed_deg"] == pytest.approx(0.0, abs=0.05)

    def test_a_ball_seen_too_low_names_the_pitch_that_explains_it(self):
        ball = {"x": 640.0, "y": 522.7, "diameter_px": 32.0}
        cues = ts.distance_cues(ball, ts.ARMS["arm5"], 1121.0, RIG)
        assert cues["from_floor_off_pct"] < -40
        assert cues["from_size_off_pct"] > 10
        # seen further below the axis than it lies below the horizon: the
        # camera points up
        assert cues["pitch_needed_deg"] == pytest.approx(3.7, abs=0.2)

    def test_a_placement_is_kept_with_its_frame(self, tmp_path):
        p = params(arm_id="arm5", tee_mm=1071)
        status = {"applied": {"exposure_us": 296, "gain": 8.0}, "ball": {"found": True, "x": 1.0}}
        frame = np.full((800, 1280), 60, np.uint8)
        assert ts.record_placement(tmp_path, p, status, frame) == 1
        assert ts.record_placement(tmp_path, p, status, frame) == 2
        folder = ts.tester_root(tmp_path, "20260922-name") / "calibration"
        rows = [json.loads(line) for line in (folder / "placements.jsonl").read_text().splitlines()]
        assert [r["placement"] for r in rows] == [1, 2]
        assert rows[0]["tee_mm"] == 1071 and rows[0]["arm"]["width"] == 1280
        assert (folder / rows[1]["frame"]).stat().st_size > 1280 * 800

    def test_recording_needs_the_tape_and_a_found_ball(self, tmp_path):
        client, _live = TestLiveEndpoints()._client(tmp_path)
        body = {"tester_id": "20260922-name", "arm_id": "arm5", "environment": "indoors"}
        assert client.post("/api/tester/placement", json=body).status_code == 400
        assert (
            client.post("/api/tester/placement", json={**body, "tee_mm": 1071}).status_code == 409
        )
        listed = client.get(
            "/api/tester/placements?" + "&".join(f"{k}={v}" for k, v in body.items())
        )
        assert listed.get_json() == {"placements": []}


class FakeTiltService:
    """The inclinometer service's surface, holding one still reading."""

    last_error = None

    def __init__(self, pitch_deg, x_g=0.0):
        from openflight.inclinometer.models import OrientationSnapshot

        y_g = math.sin(math.radians(pitch_deg))
        self.snapshot = OrientationSnapshot(
            timestamp=0.0,
            x_g=x_g,
            y_g=y_g,
            z_g=math.cos(math.radians(pitch_deg)),
            gravity_g=1.0,
            raw_pitch_deg=pitch_deg,
            calibrated_pitch_deg=pitch_deg,
            pitch_std_deg=0.1,
            sample_count=8,
        )
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def snapshot_for_impact(self, _timestamp):
        from openflight.inclinometer.models import SnapshotSelection

        return SnapshotSelection(snapshot=self.snapshot, status="stable", age_s=0.1)


class TestTheInclinometerRunsBesideThePage:
    def test_its_reading_becomes_the_cameras_pitch_the_kiosks_way(self):
        tilt = ts.EnclosureTilt(RIG, service_factory=lambda: FakeTiltService(3.3, x_g=0.052))
        tilt.start()
        reading = tilt.reading()
        # v3: the camera is level in a housing expected to sit level
        assert reading["pitch_deg"] == pytest.approx(3.3)
        assert reading["expected_pitch_deg"] == 0.0
        assert reading["camera_pitch_deg"] == pytest.approx(3.3)
        assert reading["roll_deg"] == pytest.approx(math.degrees(math.atan2(0.052, 1.0)), abs=0.05)
        # the page shows which way round the rig file says the board is
        assert reading["mount_yaw_deg"] == 180.0

    def test_without_a_sensor_it_says_so(self):
        def broken():
            raise OSError("no I2C bus")

        tilt = ts.EnclosureTilt(RIG, service_factory=broken)
        tilt.start()
        assert tilt.reading() == {"status": "off", "error": "OSError: no I2C bus"}

    def test_the_floor_agrees_with_the_tape_once_the_measured_pitch_is_applied(self):
        # where a camera pitched 3.5 deg up, 95 mm high, sees a ball 1041 mm away
        focal, drop, pitch = ts.FOCAL_PX_1X, 95.0 - ts.BALL_DIAMETER_MM / 2, math.radians(3.5)
        along = (1041.0**2 - drop**2) ** 0.5
        ball = {"x": 640.0, "y": 400.0 + focal * math.tan(pitch + math.atan(drop / along))}
        ball["diameter_px"] = focal * ts.BALL_DIAMETER_MM / 1041.0
        level = ts.distance_cues(ball, ts.ARMS["arm5"], 1071.0, RIG)
        measured = ts.distance_cues(ball, ts.ARMS["arm5"], 1071.0, RIG, {"camera_pitch_deg": 3.5})
        assert level["from_floor_off_pct"] < -40
        assert measured["from_floor_mm"] == pytest.approx(1041, abs=3)
        assert measured["pitch_unexplained_deg"] == pytest.approx(0.0, abs=0.05)
        assert measured["camera_pitch_source"] == "inclinometer"

    def test_the_swings_hand_the_sensor_to_the_kiosk_and_take_it_back(self, tmp_path):
        class Recorder(ts.TesterJobManager):
            def start(self, action, commands, log_path, on_finish=None):
                self.finish = on_finish

        fake = FakeTiltService(0.0)
        tilt = ts.EnclosureTilt(RIG, service_factory=lambda: fake)
        tilt.start()
        manager = Recorder()
        client = eligible_app(
            sessions_root=tmp_path,
            rig_geometry=RIG,
            manager=manager,
            live_view=ts.LiveView(camera_factory=FakeCamera),
            tilt=tilt,
        ).test_client()
        p = params(tee_mm=1524)
        screened(tmp_path, p)
        body = {"tester_id": "20260922-name", "arm_id": "arm1", "environment": "indoors"}
        response = client.post("/api/tester/run", json={**body, "tee_mm": 1524, "action": "swings"})
        assert response.status_code == 202
        assert fake.running is False
        manager.finish("swings", 0)
        assert fake.running is True
        status = client.post("/api/tester/status", json=body).get_json()
        assert status["inclinometer"]["status"] == "stable"


def test_the_page_reads_a_turned_board_the_way_the_kiosk_does(monkeypatch):
    from openflight.inclinometer import MountedAccelerometer

    made = {}

    class Board:
        def __init__(self, **_kwargs):
            pass

    class Service:
        def __init__(self, sensor, **_kwargs):
            made["sensor"] = sensor

    monkeypatch.setattr("openflight.inclinometer.LIS3DH", Board)
    monkeypatch.setattr("openflight.inclinometer.InclinometerService", Service)

    ts.EnclosureTilt(RIG)._make()

    assert isinstance(made["sensor"], MountedAccelerometer)
    assert made["sensor"].yaw_deg == 180.0


class BallCamera(FakeCamera):
    """The fake camera, looking at a well-lit resting ball in the 320x200 mode."""

    def capture_request(self):
        time.sleep(0.002)
        request_ = FakeRequest(320, 200, 0)
        request_.raw[:, 1::2] = _ball_frames(110, 230, n=1)[0]
        return request_


class TestEachPlacementUsesTheDistanceInTheBox:
    body = {"tester_id": "20260922-name", "arm_id": "arm1", "environment": "indoors"}

    def _client(self, tmp_path):
        live = ts.LiveView(camera_factory=BallCamera)
        app = eligible_app(sessions_root=tmp_path, rig_geometry=RIG, live_view=live)
        return app.test_client(), live

    def test_a_new_distance_needs_no_restart(self, tmp_path):
        client, live = self._client(tmp_path)
        try:
            client.post("/api/tester/live", json={**self.body, "tee_mm": 1661})
            assert _wait(lambda: live.recent_frames()[1] is not None)
            for tee in (1661, 1261):
                response = client.post("/api/tester/placement", json={**self.body, "tee_mm": tee})
                assert response.status_code == 200, response.get_json()
        finally:
            live.stop()
        query = "&".join(f"{k}={v}" for k, v in self.body.items())
        rows = client.get(f"/api/tester/placements?{query}").get_json()["placements"]
        assert [r["ball"]["camera_says"]["tape_mm"] for r in rows] == [1631, 1231]
        assert rows[1]["ball"]["expected_diameter_px"] == pytest.approx(
            ts.FOCAL_PX_2X * ts.BALL_DIAMETER_MM / 1231, abs=0.1
        )

    def test_a_placement_for_another_arm_is_refused(self, tmp_path):
        client, live = self._client(tmp_path)
        try:
            client.post("/api/tester/live", json={**self.body, "tee_mm": 1661})
            assert _wait(lambda: live.recent_frames()[1] is not None)
            response = client.post(
                "/api/tester/placement", json={**self.body, "arm_id": "arm5", "tee_mm": 1661}
            )
        finally:
            live.stop()
        assert response.status_code == 409
        assert "another arm" in response.get_json()["error"]


class TestTheRowTheFloorPredicts:
    def test_it_follows_distance_and_tilt(self):
        # 2021 mm from the lens, lens 95 mm up, camera pitched 3.4 deg up
        row, band = ts.expected_ball_row_px(ts.ARMS["arm5"], 2051.0, RIG, {"camera_pitch_deg": 3.4})
        assert row == pytest.approx(489.7, abs=0.5)
        assert band == pytest.approx(90.0)

    def test_without_a_measured_tilt_the_band_is_wider(self):
        _row, band = ts.expected_ball_row_px(ts.ARMS["arm5"], 2051.0, RIG, {"status": "off"})
        assert band == pytest.approx(150.0)
        assert ts.expected_ball_row_px(ts.ARMS["arm5"], None, RIG) is None


def test_the_size_route_never_reads_the_tapes_own_size_back():
    # the picture alone found something else: the ring's size is the tape's
    ball = {"x": 748.6, "y": 505.8, "diameter_px": 19.9, "expected_diameter_px": 19.7}
    ball["image_only_diameter_px"] = None
    cues = ts.distance_cues(ball, ts.ARMS["arm5"], 2051.0, RIG)
    assert cues["from_size_mm"] is None
    assert "from_size_off_pct" not in cues


def test_no_ball_on_a_floor_clipped_white_says_to_lower_the_exposure():
    frames = np.full((5, 800, 1280), 40, dtype=np.uint8)
    frames[:, 420:, :] = 255

    ball = ts.ball_readout(frames, ts.FOCAL_PX_1X, 19.7, (486.0, 90.0))

    assert ball["found"] is False
    assert "clipped white" in ball["reason"]
    assert "lower the exposure" in ball["reason"]


class TestTheLadder:
    body = {"tester_id": "20260922-name", "arm_id": "arm5", "environment": "indoors"}

    def test_the_second_mode_is_640x400_at_288(self):
        arm = ts.ARMS["arm6"]
        assert (arm.width, arm.height, arm.fps, arm.exposure_us) == (640, 400, 288.0, 300)

    def test_a_ladder_run_starts_the_kiosk_in_study_mode_without_a_tape(self, tmp_path):
        params = ts.TesterParameters("20260922-name", "arm5", "indoors")
        ts.write_arm_state(tmp_path, params, gain=3.0, gain_exposure_us=300)
        commands, _log = ts.action_commands(
            "ladder", params, tmp_path, RIG, "/dev/ttyAMA0", TESTER_SETUP
        )
        command = commands[0]
        assert "--study-mode" in command
        assert "--iwr6843-tee-m" not in command
        assert command[command.index("--tester-config-hash") + 1] == "test-config"
        assert command[command.index("--inclinometer-address") + 1] == "0x18"
        assert command[command.index("--camera-capture-exposure-us") + 1] == "300"

    def test_the_ladder_needs_both_gain_screens_first(self, tmp_path):
        client = eligible_app(sessions_root=tmp_path, rig_geometry=RIG).test_client()
        response = client.post("/api/tester/ladder/start", json=self.body)
        assert response.status_code == 409
        assert "gain" in response.get_json()["error"]

    def test_the_ladder_state_is_read_back(self, tmp_path):
        client = eligible_app(sessions_root=tmp_path, rig_geometry=RIG).test_client()
        response = client.get("/api/tester/ladder", query_string=self.body)
        assert response.status_code == 200
        assert response.get_json()["ladder"]["current"] == "full-300"

    def test_polling_an_unstarted_tester_does_not_create_a_session(self, tmp_path):
        client = eligible_app(sessions_root=tmp_path, rig_geometry=RIG).test_client()

        response = client.get(
            "/api/tester/ladder",
            query_string={**self.body, "tester_id": "partially-typed-name"},
        )

        assert response.status_code == 200
        assert response.get_json()["ladder"]["current"] == "full-300"
        assert not (tmp_path / "partially-typed-name").exists()

    def test_a_comparator_export_is_kept_in_the_package(self, tmp_path):
        import io as _io  # pylint: disable=import-outside-toplevel

        client = eligible_app(sessions_root=tmp_path, rig_geometry=RIG).test_client()
        response = client.post(
            "/api/tester/comparator",
            data={
                "tester_id": "20260922-name",
                "file": (_io.BytesIO(b"shot,speed\n1,120\n"), "tm4.csv"),
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 200
        saved = tmp_path / "20260922-name" / "comparator" / "tm4.csv"
        assert saved.read_bytes().startswith(b"shot")

    def test_stopping_a_job_asks_first_then_ends_the_whole_group(self, monkeypatch):
        monkeypatch.setattr(ts, "KILL_GRACE_S", 0.05)
        killed, asked = [], []
        monkeypatch.setattr(
            ts.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)), raising=False
        )
        manager = ts.TesterJobManager()
        manager._state["state"] = "running"  # pylint: disable=protected-access
        process = type(
            "P",
            (),
            {
                "pid": 4321,
                "terminate": lambda self: asked.append("terminate"),
                "poll": lambda self: None,
            },
        )()
        manager._process = process  # pylint: disable=protected-access
        assert manager.cancel() is True
        # the kiosk's own shutdown closes the radars and camera first
        assert asked == ["terminate"] and killed == []
        time.sleep(0.4)
        # a group still alive after the grace period is ended
        assert killed == [(4321, ts.signal.SIGTERM)]

    def test_group_cleanup_uses_the_owned_group_after_the_leader_exits(self, monkeypatch):
        killed = []
        monkeypatch.setattr(
            ts.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)), raising=False
        )
        process = type("ExitedLeader", (), {"pid": 4321, "poll": lambda self: 0})()
        ts.TesterJobManager._end_group(process, process.pid)  # pylint: disable=protected-access
        assert killed == [(4321, ts.signal.SIGTERM)]

    def test_cancel_before_process_handoff_terminates_the_new_process(self, tmp_path, monkeypatch):
        constructing = threading.Event()
        release = threading.Event()
        group_cleanup = threading.Event()

        class DelayedProcess(_Forever):
            def __init__(self, *args, **kwargs):
                constructing.set()
                assert release.wait(2)
                super().__init__(*args, **kwargs)

            def kill(self):
                group_cleanup.set()
                self._released.set()

        manager = ts.TesterJobManager(popen=DelayedProcess)
        monkeypatch.setattr(ts, "KILL_GRACE_S", 0.01)

        def no_fake_group(_pid, _signal):
            raise OSError("test process has no real group")

        monkeypatch.setattr(ts.os, "killpg", no_fake_group, raising=False)
        manager.start("gain", [["calibrate"]], tmp_path / "gain.log")
        assert constructing.wait(1)
        assert manager.cancel()
        release.set()
        deadline = time.monotonic() + 2
        while manager.status()["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert manager.status()["state"] == "stopped"
        assert group_cleanup.wait(1)

    def test_a_stuck_gain_screen_is_stopped_by_its_timeout(self, monkeypatch, tmp_path):
        import threading as _threading  # pylint: disable=import-outside-toplevel

        monkeypatch.setitem(ts.ACTION_TIMEOUT_S, "gain", 0.2)
        released = _threading.Event()

        class Stuck:
            pid = 99

            def __init__(self, *args, **kwargs):
                del args, kwargs
                self.stdout = self

            def __iter__(self):
                released.wait(5)
                return iter(())

            def wait(self):
                return -15

            def terminate(self):
                released.set()

        def no_group(pid):
            raise OSError(f"no process group for {pid}")

        monkeypatch.setattr(ts.os, "getpgid", no_group, raising=False)
        manager = ts.TesterJobManager(popen=Stuck)
        manager.start("gain", [["calibrate"]], tmp_path / "gain.log")
        deadline = time.monotonic() + 3
        while manager.status()["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert manager.status()["state"] == "stopped"


class _Forever:
    """A job that runs until it is stopped."""

    pid = 7

    def __init__(self, *args, **kwargs):
        del args, kwargs
        import threading as _threading  # pylint: disable=import-outside-toplevel

        self._released = _threading.Event()
        self.stdout = self

    def __iter__(self):
        self._released.wait(10)
        return iter(())

    def wait(self):
        return -15

    def poll(self):
        return -15 if self._released.is_set() else None

    def terminate(self):
        self._released.set()


class _LitKiosk:
    def ready(self):
        return True

    def set_controls(self, exposure_us, gain):
        return {"exposure_us": exposure_us, "gain": gain}

    def frames(self, count):
        return np.full((count, 800, 1280), 60, np.uint8)


class TestTheLadderHoldsUp:
    body = {"tester_id": "20260922-name", "arm_id": "arm5", "environment": "indoors"}

    def _client(self, tmp_path, monkeypatch):
        for arm in ("arm5", "arm6"):
            ts.write_arm_state(
                tmp_path,
                ts.TesterParameters("20260922-name", arm, "indoors"),
                gain=3.0,
                gain_exposure_us=300,
            )
        monkeypatch.setattr(ts.study_ladder, "KioskClient", _LitKiosk)
        monkeypatch.setattr(ts, "KILL_GRACE_S", 0.05, raising=False)
        monkeypatch.setattr(
            ts.os,
            "killpg",
            lambda _pid, _signal: (_ for _ in ()).throw(OSError("test process has no real group")),
            raising=False,
        )
        manager = ts.TesterJobManager(popen=_Forever)
        app = eligible_app(sessions_root=tmp_path, rig_geometry=RIG, manager=manager)
        return app.test_client(), manager

    def test_pressing_start_again_while_the_ladder_runs_keeps_it(self, tmp_path, monkeypatch):
        client, manager = self._client(tmp_path, monkeypatch)
        try:
            assert client.post("/api/tester/ladder/start", json=self.body).status_code == 200
            again = client.post("/api/tester/ladder/start", json=self.body)
            assert again.status_code == 200
            assert manager.status()["state"] == "running"
        finally:
            manager.cancel()

    def test_the_ladder_reads_only_the_run_it_started(self, tmp_path, monkeypatch):
        old = tmp_path / "20260922-name" / "arm5" / "paired" / "run-01"
        old.mkdir(parents=True)
        client, manager = self._client(tmp_path, monkeypatch)
        try:
            client.post("/api/tester/ladder/start", json=self.body)
            status = client.get("/api/tester/ladder", query_string=self.body).get_json()
            assert status["run_dir"].endswith("run-02")
        finally:
            manager.cancel()

    def test_stop_cancels_the_ladder_and_a_pending_mode_restart(self, tmp_path, monkeypatch):
        runners = []
        monkeypatch.setattr(
            ts.study_ladder.LadderRunner, "start", lambda runner: runners.append(runner)
        )
        client, manager = self._client(tmp_path, monkeypatch)
        waiting = threading.Event()
        release = threading.Event()
        original_status = manager.status

        def status():
            if threading.current_thread().name == "ladder-next-mode":
                waiting.set()
                assert release.wait(5)
            return original_status()

        monkeypatch.setattr(manager, "status", status)
        worker = None
        try:
            started = client.post("/api/tester/ladder/start", json=self.body).get_json()
            runner = runners[0]
            runner.state.begin("full-300", 3.0, {"ok": False, "reason": "too dark"})
            runner._on_mode_done("arm5")  # pylint: disable=protected-access
            assert waiting.wait(2)
            worker = next(t for t in threading.enumerate() if t.name == "ladder-next-mode")
            again = client.post("/api/tester/ladder/start", json=self.body)
            assert again.status_code == 200
            assert again.get_json()["run_dir"] == started["run_dir"]
            assert len(runners) == 1
            stopped = client.post("/api/tester/stop")
            assert stopped.status_code == 200
            release.set()
            worker.join(timeout=3)
            assert not worker.is_alive()
            status_data = client.get("/api/tester/ladder", query_string=self.body).get_json()
            assert status_data["run_dir"] == started["run_dir"]
            assert status_data["job"]["state"] != "running"
            assert runner.stopped
            assert runner.state.to_dict()["rungs"]["half-300"]["status"] == "pending"
        finally:
            release.set()
            if worker is not None:
                worker.join(timeout=3)
            client.post("/api/tester/stop")
            manager.cancel()

    def test_stop_then_resume_keeps_progress_and_uses_a_new_run(self, tmp_path, monkeypatch):
        runners = []
        monkeypatch.setattr(
            ts.study_ladder.LadderRunner, "start", lambda runner: runners.append(runner)
        )
        client, manager = self._client(tmp_path, monkeypatch)
        try:
            started = client.post("/api/tester/ladder/start", json=self.body).get_json()
            runner = runners[0]
            runner.state.begin("full-300", 3.0, {"ok": True})
            runner.state.record_swing({"capture": "camera_a", "color": "green", "reasons": []})
            client.post("/api/tester/stop")
            assert runner.stopped
            deadline = time.monotonic() + 2
            while manager.status()["state"] == "running" and time.monotonic() < deadline:
                time.sleep(0.01)
            manager._state["state"] = "stopped"  # pylint: disable=protected-access
            resumed = client.post("/api/tester/ladder/start", json=self.body)
            assert resumed.status_code == 200
            assert resumed.get_json()["run_dir"] != started["run_dir"]
            assert runners[1].state.accepted("full-300") == 1
            assert not runners[1].stopped
        finally:
            client.post("/api/tester/stop")
            manager.cancel()

    def test_pending_photo_resume_stays_in_full_mode_and_exposes_identity(
        self, tmp_path, monkeypatch
    ):
        runners = []
        monkeypatch.setattr(
            ts.study_ladder.LadderRunner, "start", lambda runner: runners.append(runner)
        )
        state = ts.study_ladder.LadderState(tmp_path / "20260922-name" / "ladder.json")
        state.begin("full-300", 3.0, {"ok": True})
        state.record_swing({"capture": "red-first", "color": "red", "reasons": []})
        state.record_swing({"capture": "green-middle", "color": "green", "reasons": []})
        state.record_swing({"capture": "camera_final", "color": "red", "reasons": []})
        client, manager = self._client(tmp_path, monkeypatch)
        try:
            response = client.post("/api/tester/ladder/start", json=self.body)
            data = response.get_json()
            assert response.status_code == 200
            assert "arm5" in data["run_dir"]
            assert data["pending_photo"] == {
                "capture": "camera_final",
                "rung_id": "full-300",
            }
            assert data["photo_target"] == data["pending_photo"]
            assert data["stopped"] is False
            assert runners[0].mode == "arm5"
        finally:
            client.post("/api/tester/stop")
            manager.cancel()

    def test_photo_route_rejects_stale_identity_and_skip_records_decision(
        self, tmp_path, monkeypatch
    ):
        runners = []
        monkeypatch.setattr(
            ts.study_ladder.LadderRunner, "start", lambda runner: runners.append(runner)
        )
        client, manager = self._client(tmp_path, monkeypatch)
        try:
            client.post("/api/tester/ladder/start", json=self.body)
            runner = runners[0]
            runner.state.begin("full-300", 3.0, {"ok": True})
            runner.state.record_swing({"capture": "red-first", "color": "red", "reasons": []})
            runner.state.record_swing({"capture": "green-middle", "color": "green", "reasons": []})
            runner.state.record_swing({"capture": "camera_final", "color": "red", "reasons": []})
            stale = client.post(
                "/api/tester/ladder/photo",
                json={
                    **self.body,
                    "capture": "camera_old",
                    "rung_id": "full-300",
                    "action": "skip",
                },
            )
            assert stale.status_code == 409
            skipped = client.post(
                "/api/tester/ladder/photo",
                json={
                    **self.body,
                    "capture": "camera_final",
                    "rung_id": "full-300",
                    "action": "skip",
                },
            )
            assert skipped.status_code == 200
            assert skipped.get_json()["pending_photo"] is None
            assert skipped.get_json()["photo_target"] is None
            assert runner.state.to_dict()["photo_skips"]["camera_final"]["rung_id"] == "full-300"
        finally:
            client.post("/api/tester/stop")
            manager.cancel()

    def test_package_waits_for_an_inflight_photo_action(self, tmp_path, monkeypatch):
        runners = []
        monkeypatch.setattr(
            ts.study_ladder.LadderRunner, "start", lambda runner: runners.append(runner)
        )
        client, manager = self._client(tmp_path, monkeypatch)
        called = threading.Event()
        archive = tmp_path / "package.zip"
        archive.write_bytes(b"zip")

        def package(*_args):
            called.set()
            return archive

        monkeypatch.setattr(ts, "package_study", package)
        held = False
        try:
            client.post("/api/tester/ladder/start", json=self.body)
            active = client.post("/api/tester/package", json=self.body)
            assert active.status_code == 409
            assert "stop" in active.get_json()["error"]
            client.post("/api/tester/stop")
            deadline = time.monotonic() + 2
            while manager.status()["state"] == "running" and time.monotonic() < deadline:
                time.sleep(0.01)
            manager._state["state"] = "stopped"  # pylint: disable=protected-access
            runner = runners[0]
            runner._lock.acquire()  # pylint: disable=protected-access
            held = True
            result = []

            def request_package():
                with client.application.test_client() as package_client:
                    response = package_client.post("/api/tester/package", json=self.body)
                    result.append((response.status_code, response.get_json()))

            worker = threading.Thread(target=request_package)
            worker.start()
            assert not called.wait(0.2)
            runner._lock.release()  # pylint: disable=protected-access
            held = False
            worker.join(3)
            assert called.is_set()
            assert result[0][0] == 200, result
        finally:
            if held:
                runner._lock.release()  # pylint: disable=protected-access
            manager.cancel()

    @pytest.mark.parametrize("restart_fails", [False, True])
    def test_mode_transition_starts_or_reports_its_failure(
        self, tmp_path, monkeypatch, restart_fails
    ):
        runners = []
        monkeypatch.setattr(
            ts.study_ladder.LadderRunner, "start", lambda runner: runners.append(runner)
        )
        client, manager = self._client(tmp_path, monkeypatch)
        completed = threading.Event()
        try:
            assert client.post("/api/tester/ladder/start", json=self.body).status_code == 200
            runner = runners[0]
            tick = runner.tick

            def tick_and_signal():
                try:
                    tick()
                finally:
                    completed.set()

            monkeypatch.setattr(runner, "tick", tick_and_signal)
            if restart_fails:
                stop = runner.stop

                def stop_and_signal(**kwargs):
                    stop(**kwargs)
                    completed.set()

                def failed_start(*args, **kwargs):
                    raise RuntimeError("camera unavailable")

                monkeypatch.setattr(runner, "stop", stop_and_signal)
                monkeypatch.setattr(manager, "start", failed_start)
            runner.state.begin("full-300", 3.0, {"ok": False, "reason": "too dark"})
            runner._on_mode_done("arm5")  # pylint: disable=protected-access
            assert completed.wait(3)
            result = client.get("/api/tester/ladder", query_string=self.body).get_json()
            if restart_fails:
                assert runner.stopped
                assert "camera unavailable" in result["last_verdict"]["reasons"][0]
                assert result["ladder"]["rungs"]["half-300"]["status"] == "pending"
            else:
                assert result["job"]["state"] == "running"
                assert "arm6" in result["run_dir"]
                assert result["ladder"]["rungs"]["half-300"]["status"] == "active"
        finally:
            client.post("/api/tester/stop")
            manager.cancel()

    def test_a_comparator_export_with_spaces_in_its_name_is_kept(self, tmp_path):
        import io as _io  # pylint: disable=import-outside-toplevel

        client = eligible_app(sessions_root=tmp_path, rig_geometry=RIG).test_client()
        response = client.post(
            "/api/tester/comparator",
            data={
                "tester_id": "20260922-name",
                "file": (_io.BytesIO(b"shot,speed\n"), "Mevo Export (1).csv"),
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 200
        saved = response.get_json()["saved"]
        assert (tmp_path / "20260922-name" / "comparator" / saved).is_file()


class TestAttemptLedgerRoutes:
    def _client(self, tmp_path):
        run = tmp_path / "tester" / "arm1" / "paired" / "run-01"
        run.mkdir(parents=True)
        return eligible_app(sessions_root=tmp_path, rig_geometry=RIG).test_client(), run

    def test_saved_stopped_run_accepts_idempotent_operator_attempt(self, tmp_path):
        client, run = self._client(tmp_path)
        body = {
            "tester_id": "tester",
            "arm_id": "arm1",
            "run": "run-01",
            "request_id": "request-1",
            "entry_id": "entry-1",
            "action": "add",
            "kind": "swing",
            "operator_missed": True,
        }
        assert client.post("/api/tester/attempts", json=body).status_code == 201
        retry = client.post("/api/tester/attempts", json=body)
        assert retry.status_code == 200
        assert retry.get_json()["counts"]["physical_operator_swings"] == 1
        assert len((run / "attempt_ledger.jsonl").read_text().splitlines()) == 1

    def test_scope_listing_and_missing_ledger_are_unknown(self, tmp_path):
        client, _run = self._client(tmp_path)
        listing = client.get("/api/tester/attempts", query_string={"tester_id": "tester"})
        assert listing.get_json()["scopes"][0]["run"] == "run-01"
        state = client.get(
            "/api/tester/attempts",
            query_string={"tester_id": "tester", "arm_id": "arm1", "run": "run-01"},
        ).get_json()
        assert state["counts"]["physical_operator_swings"] is None
        assert state["reconciliation"]["status"] == "unknown"

    def test_stale_arm_scope_and_symlink_escape_are_rejected(self, tmp_path):
        client, run = self._client(tmp_path)
        wrong = client.get(
            "/api/tester/attempts",
            query_string={
                "tester_id": "tester",
                "arm_id": "arm5",
                "run_dir": str(run),
            },
        )
        assert wrong.status_code == 400
        link = tmp_path / "tester" / "arm1" / "paired" / "run-02"
        try:
            link.symlink_to(run, target_is_directory=True)
        except OSError:
            pytest.skip("directory symlinks unavailable")
        escaped = client.get(
            "/api/tester/attempts",
            query_string={"tester_id": "tester", "arm_id": "arm1", "run": "run-02"},
        )
        assert escaped.status_code == 400

    def test_duplicate_and_late_events_count_distinct_sensor_shots(self, tmp_path):
        client, run = self._client(tmp_path)
        session = run / "session_events.jsonl"
        session.write_text(
            "\n".join(
                json.dumps(event)
                for event in (
                    {"type": "shot_detected", "shot_number": 1},
                    {"type": "shot_detected", "shot_number": 1},
                    {"type": "shot", "shot_number": "1"},
                    {"type": "shot_detected", "shot_number": None},
                    {"type": "shot_detected", "shot_number": "bad"},
                )
            )
            + "\n"
        )
        body = {
            "tester_id": "tester",
            "arm_id": "arm1",
            "run": "run-01",
            "request_id": "request-1",
            "entry_id": "entry-1",
            "action": "add",
            "kind": "swing",
            "operator_missed": False,
        }
        posted = client.post("/api/tester/attempts", json=body).get_json()
        assert posted["counts"]["physical_operator_swings"] == 1
        assert posted["counts"]["logged_sensor_shots"] == 1
        with session.open("a") as handle:
            handle.write(json.dumps({"type": "shot_detected", "shot_number": 2}) + "\n")
        refreshed = client.get(
            "/api/tester/attempts",
            query_string={"tester_id": "tester", "arm_id": "arm1", "run": "run-01"},
        ).get_json()
        assert refreshed["counts"]["physical_operator_swings"] == 1
        assert refreshed["counts"]["logged_sensor_shots"] == 2
        assert refreshed["reconciliation"]["difference"] == 1
