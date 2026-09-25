import json
from pathlib import Path

import pytest

from openflight.camera import tester_server as ts
from openflight.camera.setup_eligibility import SetupEligibility, inspect_geometry, placement_guard


@pytest.mark.parametrize(
    ("reading", "ready", "warned"),
    [
        ({"calibrated_pitch_deg": 2.0, "x_g": 0.0, "y_g": 0.0, "z_g": 1.0}, True, False),
        ({"calibrated_pitch_deg": 2.01, "x_g": 0.0, "y_g": 0.0, "z_g": 1.0}, True, True),
        ({"calibrated_pitch_deg": -2.01, "x_g": 0.0, "y_g": 0.0, "z_g": 1.0}, True, True),
        ({"calibrated_pitch_deg": 0.0, "x_g": 1.0, "y_g": 0.0, "z_g": 0.01}, True, True),
        ({"calibrated_pitch_deg": 0.0, "x_g": -1.0, "y_g": 0.0, "z_g": 0.01}, True, True),
        ({"calibrated_pitch_deg": 0.0, "x_g": 0.0, "y_g": 0.0, "z_g": -1.0}, False, False),
        (
            {
                "calibrated_pitch_deg": 0.0,
                "roll_deg": True,
                "x_g": 0.0,
                "y_g": 0.0,
                "z_g": 1.0,
            },
            False,
            False,
        ),
        (
            {
                "calibrated_pitch_deg": float("nan"),
                "x_g": 0.0,
                "y_g": 0.0,
                "z_g": 1.0,
            },
            False,
            False,
        ),
    ],
)
def test_placement_guard_flags_deviation_and_blocks_invalid_orientation(reading, ready, warned):
    result = placement_guard(reading)
    assert result["ready"] is ready
    assert result["warned"] is warned


RIG = Path(__file__).parents[1] / "config" / "enclosure_v3_rig_geometry.json"
TESTER = "20260924-setup"
STABLE = {
    "status": "stable",
    "pitch_deg": 0.0,
    "roll_deg": 0.0,
    "x_g": 0.0,
    "y_g": 0.0,
    "z_g": 1.0,
}


class StaticTilt:
    bus = 1
    address = 0x18
    zero_offset_deg = 0.0

    def __init__(self, reading=None):
        self.value = reading or dict(STABLE)

    def reading(self):
        return dict(self.value)

    def start(self):
        return None

    def stop(self):
        return None


def policy(tmp_path, rig=RIG):
    return SetupEligibility(
        rig,
        tmp_path,
        inclinometer_bus=1,
        inclinometer_address=0x18,
        inclinometer_zero_offset_deg=0.0,
    )


def test_confirmation_is_explicit_config_bound_and_process_local(tmp_path):
    gate = policy(tmp_path)
    initial = gate.evaluate(TESTER, STABLE)
    assert not initial["eligible"]
    assert initial["config_hash"]

    confirmed = gate.confirm(TESTER, initial["config_hash"], True, STABLE)
    assert confirmed["eligible"]
    assert gate.confirmation_valid(TESTER)
    assert not policy(tmp_path).confirmation_valid(TESTER)


def test_failed_evidence_write_does_not_publish_confirmation(tmp_path, monkeypatch):
    gate = policy(tmp_path)
    config_hash = gate.evaluate(TESTER, STABLE)["config_hash"]
    monkeypatch.setattr(gate, "record", lambda *_args: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError, match="disk"):
        gate.confirm(TESTER, config_hash, True, STABLE)
    assert not gate.confirmation_valid(TESTER)


@pytest.mark.parametrize(
    "reading,status",
    [
        ({"status": "off"}, "off"),
        ({"status": "stale"}, "stale"),
        ({"status": "moving"}, "moving"),
        ({"status": "missing"}, "missing"),
    ],
)
def test_lis3dh_must_be_stable(tmp_path, reading, status):
    result = policy(tmp_path).evaluate(TESTER, reading)
    blocker = next(item for item in result["blockers"] if item["id"] == "lis3dh")
    assert status in blocker["reason"]


@pytest.mark.parametrize(
    "pitch,roll,z_g,status",
    [
        (2.0, -2.0, 1.0, "pass"),
        (-2.0, 2.0, 1.0, "pass"),
        (2.01, -2.01, 1.0, "warn"),
        (-2.01, 2.01, 1.0, "warn"),
    ],
)
def test_placement_deviation_is_warned_without_blocking(tmp_path, pitch, roll, z_g, status):
    gate = policy(tmp_path)
    reading = {**STABLE, "pitch_deg": pitch, "roll_deg": roll, "z_g": z_g}
    result = gate.evaluate(TESTER, reading)
    lis = next(check for check in result["checks"] if check["id"] == "lis3dh")
    assert lis["status"] == status
    assert [blocker["id"] for blocker in result["blockers"]] == ["operator_confirmation"]


def test_tilted_stable_setup_can_be_confirmed_and_warning_is_recorded(tmp_path):
    gate = policy(tmp_path)
    reading = {**STABLE, "pitch_deg": 6.5, "roll_deg": -3.25}
    initial = gate.evaluate(TESTER, reading)
    result = gate.confirm(TESTER, initial["config_hash"], True, reading)
    assert result["eligible"] is True
    assert result["warnings"][0]["id"] == "lis3dh"
    admitted = gate.require(TESTER, reading, "swings")
    assert admitted["eligible"] is True
    entries = [
        json.loads(line)
        for line in (tmp_path / TESTER / "setup_eligibility.jsonl").read_text().splitlines()
    ]
    assert entries[-1]["warnings"][0]["id"] == "lis3dh"
    assert entries[-1]["checks"][1]["placement_guard"]["pitch_error_deg"] == 6.5


def test_confirmed_tilted_swing_starts_and_persists_run_admission(tmp_path):
    class Manager(ts.TesterJobManager):
        def start(self, action, commands, log_path, on_finish=None):
            self.started = (action, commands, log_path, on_finish)

    reading = {**STABLE, "pitch_deg": 5.25, "roll_deg": -3.5}
    manager = Manager()
    client = ts.create_app(
        sessions_root=tmp_path,
        rig_geometry=RIG,
        tilt=StaticTilt(reading),
        manager=manager,
    ).test_client()
    eligibility = client.get(
        "/api/tester/setup-eligibility", query_string={"tester_id": TESTER}
    ).get_json()
    confirmed = client.post(
        "/api/tester/setup-eligibility",
        json={
            "tester_id": TESTER,
            "action": "confirm",
            "config_hash": eligibility["config_hash"],
            "physical_rig_confirmed": True,
        },
    )
    assert confirmed.get_json()["eligible"] is True
    params = ts.TesterParameters(TESTER, "arm1", "indoors", 1524.0)
    ts.write_arm_state(tmp_path, params, gain=4.0, gain_exposure_us=params.arm.exposure_us)

    response = client.post(
        "/api/tester/run",
        json={
            "tester_id": TESTER,
            "arm_id": "arm1",
            "environment": "indoors",
            "tee_mm": 1524,
            "action": "swings",
        },
    )

    assert response.status_code == 202
    assert manager.started[0] == "swings"
    admission = json.loads(
        (tmp_path / TESTER / "arm1" / "paired" / "run-01" / "setup_admission.json").read_text(
            encoding="utf-8"
        )
    )
    assert admission["warnings"][0]["id"] == "lis3dh"
    guard = admission["checks"][1]["placement_guard"]
    assert guard["pitch_deg"] == 5.25
    assert guard["roll_error_deg"] == -3.5
    assert guard["accuracy_qualified"] is False


def test_changed_or_malformed_geometry_revokes_confirmation(tmp_path):
    rig = tmp_path / "rig.json"
    rig.write_bytes(RIG.read_bytes())
    gate = policy(tmp_path, rig)
    current = gate.evaluate(TESTER, STABLE)
    gate.confirm(TESTER, current["config_hash"], True, STABLE)
    data = json.loads(rig.read_text(encoding="utf-8"))
    data["lens_height_above_floor_mm"] = 96.0
    rig.write_text(json.dumps(data), encoding="utf-8")
    changed = gate.evaluate(TESTER, STABLE)
    assert not changed["eligible"]
    assert changed["config_hash"] is None
    assert not gate.confirmation_valid(TESTER)

    rig.write_text('{"focal_px": NaN}', encoding="utf-8")
    assert inspect_geometry(rig)[0] is None
    assert not gate.confirmation_valid(TESTER)


def test_direct_acquisition_post_is_blocked_until_server_confirmation(tmp_path):
    tilt = StaticTilt()
    app = ts.create_app(sessions_root=tmp_path, rig_geometry=RIG, tilt=tilt)
    client = app.test_client()
    body = {
        "tester_id": TESTER,
        "arm_id": "arm1",
        "environment": "indoors",
        "action": "gain",
    }
    blocked = client.post("/api/tester/run", json=body)
    assert blocked.status_code == 409
    assert blocked.get_json()["setup_eligibility"]["eligible"] is False

    eligibility = client.get(
        "/api/tester/setup-eligibility", query_string={"tester_id": TESTER}
    ).get_json()
    confirmed = client.post(
        "/api/tester/setup-eligibility",
        json={
            "tester_id": TESTER,
            "action": "confirm",
            "config_hash": eligibility["config_hash"],
            "physical_rig_confirmed": True,
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.get_json()["eligible"] is True


def test_confirmation_rejects_wrong_hash_false_ack_and_invalid_tester(tmp_path):
    client = ts.create_app(
        sessions_root=tmp_path, rig_geometry=RIG, tilt=StaticTilt()
    ).test_client()
    for body in (
        {
            "tester_id": TESTER,
            "action": "confirm",
            "config_hash": "bad",
            "physical_rig_confirmed": True,
        },
        {
            "tester_id": TESTER,
            "action": "confirm",
            "config_hash": "bad",
            "physical_rig_confirmed": False,
        },
    ):
        assert client.post("/api/tester/setup-eligibility", json=body).status_code == 400
    assert (
        client.get(
            "/api/tester/setup-eligibility", query_string={"tester_id": "../bad"}
        ).status_code
        == 400
    )
