"""Tests for the mode-study capture runner."""

from __future__ import annotations

import json
import zipfile

import numpy as np
import pytest

from openflight.camera import tester_server as ts

RIG = ts.DEFAULT_RIG_GEOMETRY


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
        assert ts.ARM_ORDER == ("arm1", "arm2", "arm3", "arm4", "arm5")
        shape = {a: (x.width, x.fps, x.exposure_us) for a, x in ts.ARMS.items()}
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
        commands, _ = ts.action_commands("swings", p, tmp_path, RIG)
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
        command = ts.action_commands("swings", p, tmp_path, RIG)[0][0]
        assert command[command.index("--camera-capture-exposure-us") + 1] == "175"
        assert command[command.index("--camera-capture-width") + 1] == "320"
        assert command[command.index("--camera-capture-fps") + 1] == "450.0"


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
        client = ts.create_app(sessions_root=tmp_path, rig_geometry=RIG).test_client()
        assert client.get("/").status_code == 200
        arms = client.get("/api/tester/arms").get_json()
        assert [a["arm_id"] for a in arms["arms"]] == ["arm1", "arm2", "arm3", "arm4", "arm5"]
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
        client = ts.create_app(sessions_root=tmp_path, rig_geometry=RIG).test_client()
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
        client = ts.create_app(sessions_root=tmp_path, rig_geometry=RIG).test_client()
        response = client.post("/api/tester/status", json={"tester_id": "../x"})
        assert response.status_code == 400

    def test_a_second_concurrent_action_is_refused(self, tmp_path):
        class BusyManager(ts.TesterJobManager):
            def status(self):
                return {"state": "running", "action": "swings", "message": "busy", "output": []}

            def start(self, action, commands, log_path, on_finish=None):
                raise RuntimeError("another action is already running")

        client = ts.create_app(
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
        commands, _ = ts.action_commands("swings", p, tmp_path, RIG)
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
        commands, _ = ts.action_commands("swings", p, tmp_path, RIG, radar_port="/dev/ttyACM0")
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
