"""Step 2, Check the unit: all four sensors, and the LIS3DH's orientation, before the box."""

from __future__ import annotations

import json
import math
from pathlib import Path

from openflight import unit_check
from openflight.camera import tester_server as ts
from openflight.inclinometer import MountedAccelerometer, orientation_check as oc

RIG = Path(__file__).parents[1] / "config" / "enclosure_v3_rig_geometry.json"
TESTER = "20261019-unit"
QUERY = {"tester_id": TESTER}
RUN = {"tester_id": TESTER, "arm_id": "arm5", "environment": "indoors"}


class SimTilt:
    """The tester's LIS3DH, simulated: a board fitted ``true_mount`` in the unit.

    It reads in whatever mount the server last set, as EnclosureTilt does.
    """

    bus = 1
    address = 0x18
    zero_offset_deg = 0.0

    def __init__(self, true_mount: oc.Mount, assumed: oc.Mount = oc.Mount(180.0)):
        self.true_mount = true_mount
        self.mount = assumed
        self.front_up_deg = 0.0
        self.left_up_deg = 0.0
        self.mounts_set = []

    def reading(self):
        if self.left_up_deg:
            angle = math.radians(self.left_up_deg)
            raw = self.true_mount.inverse_apply(-math.sin(angle), 0.0, math.cos(angle))
        else:
            angle = math.radians(self.front_up_deg)
            raw = self.true_mount.inverse_apply(0.0, math.sin(angle), math.cos(angle))
        x, y, z = self.mount.apply(*raw)
        pitch = math.degrees(math.atan2(y, math.hypot(x, z)))
        return {
            "status": "stable",
            "pitch_deg": pitch,
            "roll_deg": 0.0,
            "x_g": x,
            "y_g": y,
            "z_g": z,
        }

    def set_mount(self, mount):
        self.mount = mount
        self.mounts_set.append(mount)

    def start(self):
        return None

    def stop(self):
        return None


class CheckJobs:
    """Runs Check the unit at once, writing the result the real script would."""

    def __init__(self, sensors_ok=None, write=True):
        self.sensors_ok = sensors_ok or {sensor: True for sensor in unit_check.SENSORS}
        self.write = write
        self.commands = None
        self._status = {"state": "idle", "action": None, "message": "Ready"}

    def status(self):
        return dict(self._status)

    def start(self, action, commands, _log_path, on_finish=None, **_kwargs):
        self.commands = [list(map(str, command)) for command in commands]
        rc = 0
        if action == "preflight":
            probe = next(c for c in self.commands if c[1].endswith("check_unit.py"))
            out = Path(probe[probe.index("--out") + 1])
            sensors = {
                sensor: {
                    "id": sensor,
                    "name": unit_check.SENSOR_NAMES[sensor],
                    "ok": ok,
                    "detail": "answered" if ok else "no answer",
                    "remedy": None if ok else unit_check.REMEDIES[sensor],
                }
                for sensor, ok in self.sensors_ok.items()
            }
            if self.write:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(
                    json.dumps({"schema": unit_check.SCHEMA, "sensors": sensors}), encoding="utf-8"
                )
            rc = 0 if all(self.sensors_ok.values()) else 1
        self._status = {"state": "complete", "action": action, "message": "Complete"}
        if on_finish:
            on_finish(action, rc)


def make_client(tmp_path, jobs=None, tilt=None, confirm=True, **kwargs):
    kwargs.setdefault("require_iwr_preflight", True)
    app = ts.create_app(
        sessions_root=tmp_path / "sessions",
        rig_geometry=RIG,
        tilt=tilt or SimTilt(oc.Mount(180.0)),
        manager=jobs or CheckJobs(),
        **kwargs,
    )
    client = app.test_client()
    if not confirm:
        return client
    eligibility = client.get("/api/tester/setup-eligibility", query_string=QUERY).get_json()
    confirmed = client.post(
        "/api/tester/setup-eligibility",
        json={
            **QUERY,
            "action": "confirm",
            "config_hash": eligibility["config_hash"],
            "physical_rig_confirmed": True,
        },
    )
    assert confirmed.status_code == 200, confirmed.get_json()
    return client


def eligibility(client):
    return client.get("/api/tester/setup-eligibility", query_string=QUERY).get_json()


def check_unit(client, **extra):
    response = client.post("/api/tester/run", json={**RUN, "action": "preflight", **extra})
    assert response.status_code == 202, response.get_json()
    return eligibility(client)


def blocker_ids(result):
    return [blocker["id"] for blocker in result["blockers"]]


class TestFourSensors:
    def test_before_the_check_every_sensor_waits_for_it(self, tmp_path):
        result = eligibility(make_client(tmp_path))

        assert result["eligible"] is False
        assert blocker_ids(result) == ["ops243", "camera", "iwr6843_cli"]
        rows = {row["id"]: row for row in result["checks"]}
        assert rows["ops243"]["label"] == "Speed radar (OPS243)"
        assert rows["ops243"]["reason"] == "not checked yet"
        assert result["hardware"] == {"checked": False, "missing": []}

    def test_all_four_answering_admits_the_unit(self, tmp_path):
        result = check_unit(make_client(tmp_path))

        assert result["eligible"] is True
        assert result["hardware"] == {"checked": True, "missing": []}

    def test_a_missing_sensor_is_refused_by_name(self, tmp_path):
        jobs = CheckJobs({"ops243": True, "camera": False, "iwr6843": True})
        result = check_unit(make_client(tmp_path, jobs))

        assert result["eligible"] is False
        assert blocker_ids(result) == ["camera"]
        camera = next(row for row in result["checks"] if row["id"] == "camera")
        assert camera["reason"] == "Camera (OV9281) is missing or not answering: no answer"
        assert "ribbon cable" in camera["remedy"]
        assert result["hardware"]["missing"] == ["Camera (OV9281)"]

    def test_a_missing_tilt_sensor_is_named_too(self, tmp_path):
        class NoTilt(SimTilt):
            def reading(self):
                return {"status": "off", "error": "OSError: no I2C bus"}

        client = ts.create_app(
            sessions_root=tmp_path / "sessions",
            rig_geometry=RIG,
            tilt=NoTilt(oc.Mount(180.0)),
            manager=CheckJobs(),
            require_iwr_preflight=True,
        ).test_client()
        result = check_unit(client)

        assert "lis3dh" in blocker_ids(result)
        assert result["hardware"]["missing"] == ["Tilt sensor (LIS3DH)"]

    def test_the_check_runs_the_unit_script_on_the_units_ports(self, tmp_path):
        jobs = CheckJobs()
        client = make_client(tmp_path, jobs, radar_port="/dev/ttyAMA0", iwr_static_port="/dev/iwr")
        check_unit(client, operator_reset=True)

        probe = next(c for c in jobs.commands if c[1].endswith("check_unit.py"))
        assert probe[probe.index("--ops-port") + 1] == "/dev/ttyAMA0"
        assert probe[probe.index("--iwr-port") + 1] == "/dev/iwr"
        assert probe[probe.index("--operator-reset") + 1] == "pressed"
        assert Path(probe[probe.index("--out") + 1]).name == unit_check.RESULT_FILE
        # the check that runs first is the sensors: nothing before it can stop it
        assert jobs.commands[0] == probe

    def test_a_check_that_left_no_result_falls_back_on_its_exit_code(self, tmp_path):
        ok = check_unit(make_client(tmp_path / "a", CheckJobs(write=False)))
        assert ok["eligible"] is True

        failed = check_unit(
            make_client(
                tmp_path / "b",
                CheckJobs({"ops243": True, "camera": True, "iwr6843": False}, write=False),
            )
        )
        assert blocker_ids(failed) == ["ops243", "camera", "iwr6843_cli"]
        assert all("did not report" in blocker["reason"] for blocker in failed["blockers"])

    def test_the_box_waits_for_the_unit_check(self, tmp_path):
        client = make_client(tmp_path)

        before = client.get("/api/tester/placement-box", query_string=QUERY).get_json()
        assert "camera" in [blocker["id"] for blocker in before["setup_blockers"]]
        refused = client.post("/api/tester/placement-box", json={**QUERY, "action": "show"})
        assert refused.status_code == 409

        check_unit(client)
        after = client.get("/api/tester/placement-box", query_string=QUERY).get_json()
        assert after["setup_blockers"] == []


class TestOrientationCheck:
    def client(self, tmp_path, tilt, jobs=None, confirm=True):
        client = make_client(
            tmp_path,
            jobs,
            tilt=tilt,
            confirm=confirm,
            require_lis3dh_orientation=True,
            lis3dh_mount_path=tmp_path / "config" / oc.FILE_NAME,
        )
        check_unit(client)
        return client

    def post(self, client, **body):
        response = client.post("/api/tester/lis3dh-check", json=body)
        assert response.status_code == 200, response.get_json()
        return response.get_json()

    def test_setup_cannot_finish_until_the_check_passes(self, tmp_path):
        tilt = SimTilt(oc.Mount(180.0))
        client = self.client(tmp_path, tilt)

        before = eligibility(client)
        assert blocker_ids(before) == ["lis3dh_orientation"]

        assert self.post(client, action="start")["state"] == "lift"
        tilt.front_up_deg = 6.0
        status = client.get("/api/tester/lis3dh-check").get_json()
        assert status["state"] == "lift_left"
        tilt.front_up_deg = 0.0
        # the front's pass is the setup's; the roll step only verifies the roll
        assert eligibility(client)["eligible"] is True
        tilt.left_up_deg = 6.0
        status = client.get("/api/tester/lis3dh-check").get_json()
        assert status["state"] == "passed"
        assert status["roll_verified"] is True
        saved = json.loads((tmp_path / "config" / oc.FILE_NAME).read_text(encoding="utf-8"))
        assert saved["roll_verified"] is True

    def test_a_turned_board_is_corrected_by_tap_and_saved_for_the_unit(self, tmp_path):
        tilt = SimTilt(oc.Mount(90.0))
        client = self.client(tmp_path, tilt)

        self.post(client, action="start")
        tilt.front_up_deg = 6.0
        status = client.get("/api/tester/lis3dh-check").get_json()
        assert status["state"] == "needs_correction"
        tilt.front_up_deg = 0.0
        after = self.post(client, action="correct", correction=status["suggestion"])

        # the reading follows the corrected mount at once, and the check runs again
        assert tilt.mounts_set == [oc.Mount(90.0)]
        assert after["state"] == "lift"
        tilt.front_up_deg = 6.0
        assert client.get("/api/tester/lis3dh-check").get_json()["state"] == "lift_left"
        saved = json.loads((tmp_path / "config" / oc.FILE_NAME).read_text(encoding="utf-8"))
        assert saved["mount"] == {"yaw_deg": 90.0, "upside_down": False}
        assert saved["check"]["state"] == "passed"
        # the rig file is shared by every unit: it is never written
        assert json.loads(RIG.read_text(encoding="utf-8"))["lis3dh_mount_yaw_deg"] == 180.0

    def test_an_upside_down_board_is_offered_its_correction(self, tmp_path):
        tilt = SimTilt(oc.Mount(180.0, True))
        # an upside-down reading cannot be confirmed: the check comes first
        client = self.client(tmp_path, tilt, confirm=False)

        status = self.post(client, action="start")
        assert status["state"] == "upside_down"
        assert status["suggestion"] == "upside_down"

    def test_an_unknown_correction_is_refused(self, tmp_path):
        client = self.client(tmp_path, SimTilt(oc.Mount(180.0)))
        response = client.post(
            "/api/tester/lis3dh-check", json={"action": "correct", "correction": "sideways"}
        )
        assert response.status_code == 400

    def test_no_check_while_a_capture_runs(self, tmp_path):
        class Busy(CheckJobs):
            recording = False

            def status(self):
                if self.recording:
                    return {"state": "running", "action": "free_play", "message": "Free play"}
                return super().status()

        jobs = Busy()
        client = self.client(tmp_path, SimTilt(oc.Mount(180.0)), jobs)
        jobs.recording = True
        response = client.post("/api/tester/lis3dh-check", json={"action": "start"})
        assert response.status_code == 409


class TestEveryReaderUsesTheCheckedMount:
    def test_the_tester_reads_the_lis3dh_with_the_units_mount(self, tmp_path):
        path = tmp_path / oc.FILE_NAME
        oc.MountStore(path, rig_yaw_deg=180.0).apply_correction("upside_down")

        class Board:
            def __init__(self, **_kwargs):
                pass

        tilt = ts.EnclosureTilt(RIG, mount_path=path, sensor_factory=Board)
        service = tilt._make()  # pylint: disable=protected-access

        assert isinstance(service.sensor, MountedAccelerometer)
        assert (service.sensor.yaw_deg, service.sensor.upside_down) == (180.0, True)

    def test_a_correction_restarts_the_tester_reading_with_it(self, tmp_path):
        made = []

        class Service:
            last_error = None

            def __init__(self):
                made.append(self)

            def start(self):
                return None

            def stop(self):
                return None

        tilt = ts.EnclosureTilt(RIG, service_factory=Service)
        tilt.start()
        tilt.set_mount(oc.Mount(90.0))

        assert len(made) == 2
        assert tilt.mount() == (oc.Mount(90.0), "tester_check")

    def test_the_kiosk_is_handed_the_units_mount_file(self, tmp_path):
        setup = {
            "config_hash": "c",
            "inclinometer_bus": 1,
            "inclinometer_address": 0x18,
            "inclinometer_zero_offset_deg": 0.0,
            "lis3dh_mount_file": str(tmp_path / oc.FILE_NAME),
        }
        params = ts.TesterParameters.from_payload(RUN)
        commands, _ = ts.action_commands("free_play", params, tmp_path, RIG, tester_setup=setup)
        flag = commands[0].index("--lis3dh-mount-file")
        assert commands[0][flag + 1] == str(tmp_path / oc.FILE_NAME)

    def test_without_a_unit_file_nothing_extra_is_handed_over(self, tmp_path):
        setup = {
            "config_hash": "c",
            "inclinometer_bus": 1,
            "inclinometer_address": 0x18,
            "inclinometer_zero_offset_deg": 0.0,
        }
        params = ts.TesterParameters.from_payload(RUN)
        commands, _ = ts.action_commands("free_play", params, tmp_path, RIG, tester_setup=setup)
        assert "--lis3dh-mount-file" not in commands[0]
