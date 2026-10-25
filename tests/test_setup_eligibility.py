import json
from pathlib import Path

import pytest

from openflight.camera import tester_server as ts
from openflight.camera.setup_eligibility import SetupEligibility, inspect_geometry

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


def test_unverified_geometry_is_allowed_only_for_free_play_collection(tmp_path):
    unknown = tmp_path / "unknown.json"
    content = json.loads(RIG.read_text(encoding="utf-8"))
    content["lens_height_above_floor_mm"] = 110.0
    unknown.write_text(json.dumps(content), encoding="utf-8")
    gate = SetupEligibility(
        unknown,
        tmp_path / "sessions",
        inclinometer_bus=1,
        inclinometer_address=0x18,
        inclinometer_zero_offset_deg=0.0,
        allow_unverified_geometry=True,
    )
    before = gate.evaluate(TESTER, STABLE)
    assert before["config_hash"]
    confirmed = gate.confirm(TESTER, before["config_hash"], True, STABLE)
    assert confirmed["eligible"] is True
    assert any(item["id"] == "geometry" for item in confirmed["warnings"])
    assert gate.require(TESTER, STABLE, "free_play")["eligible"] is True
    assert gate.require(TESTER, STABLE, "gain")["eligible"] is True
    assert gate.require(TESTER, STABLE, "ladder")["eligible"] is False


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


def test_confirmation_is_explicit_config_bound_and_survives_a_server_restart(tmp_path):
    gate = policy(tmp_path)
    initial = gate.evaluate(TESTER, STABLE)
    assert not initial["eligible"]
    assert initial["config_hash"]

    confirmed = gate.confirm(TESTER, initial["config_hash"], True, STABLE)
    assert confirmed["eligible"]
    assert gate.confirmation_valid(TESTER)
    # the confirmation is on disk: a restarted tester server keeps it, with its identity
    restarted = policy(tmp_path)
    assert restarted.confirmation_valid(TESTER)
    again = restarted.evaluate(TESTER, STABLE)
    assert again["eligible"]
    assert (
        again["operator_confirmation"]["confirmation_id"]
        == confirmed["operator_confirmation"]["confirmation_id"]
    )


def test_a_reconfirmation_without_a_real_change_keeps_the_identity(tmp_path):
    gate = policy(tmp_path)
    config_hash = gate.evaluate(TESTER, STABLE)["config_hash"]
    first = gate.confirm(TESTER, config_hash, True, STABLE)
    nudged = {**STABLE, "pitch_deg": 0.3, "roll_deg": -0.2}

    second = policy(tmp_path).confirm(TESTER, config_hash, True, nudged)

    assert (
        second["operator_confirmation"]["confirmation_id"]
        == first["operator_confirmation"]["confirmation_id"]
    )


class Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def watched(tmp_path, clock):
    return SetupEligibility(
        RIG,
        tmp_path,
        inclinometer_bus=1,
        inclinometer_address=0x18,
        inclinometer_zero_offset_deg=0.0,
        clock=clock,
    )


def operator_check(result):
    return next(item for item in result["checks"] if item["id"] == "operator_confirmation")


@pytest.mark.parametrize("name", ["pitch_deg", "roll_deg"])
def test_a_move_that_persists_needs_confirming_again(tmp_path, name):
    clock = Clock()
    gate = watched(tmp_path, clock)
    config_hash = gate.evaluate(TESTER, STABLE)["config_hash"]
    first = gate.confirm(TESTER, config_hash, True, STABLE)
    moved = {**STABLE, name: 1.5}

    # a move is only a move once it has lasted: three still readings over 3 s
    for _ in range(3):
        result = gate.evaluate(TESTER, moved)
        assert result["eligible"], "a move is not latched before it persists"
        assert operator_check(result)["status"] == "warn"
        assert "keep it still" in operator_check(result)["reason"]
        clock.now += 1.0
    result = gate.evaluate(TESTER, moved)

    assert not result["eligible"]
    assert "moved" in operator_check(result)["reason"]
    assert result["operator_confirmation"]["confirmation_id"] is None
    # latched: a reading back where it was does not bring the confirmation back
    assert not gate.evaluate(TESTER, STABLE)["eligible"]
    # a reading the kiosk holds (no angles) does not count as a move
    assert policy(tmp_path).evaluate(TESTER, {"status": "stable"})["operator_confirmation"][
        "confirmed"
    ]
    second = gate.confirm(TESTER, config_hash, True, moved)
    assert second["eligible"]
    assert (
        second["operator_confirmation"]["confirmation_id"]
        != first["operator_confirmation"]["confirmation_id"]
    )
    moves = [entry for entry in _records(tmp_path) if entry["outcome"] == "unit_moved"]
    assert len(moves) == 1
    assert moves[0]["confirmation_id"] == first["operator_confirmation"]["confirmation_id"]


def test_the_field_noise_never_revokes_a_confirmation(tmp_path):
    """Field 24 Oct (outdoor test 1): still readings of roll -1.73 to -1.91 deg.

    At 0.5 deg against one reading of the unit being set down (-2.25) about one
    reading in ten crossed, and the box and aim steps reset each time.
    """
    clock = Clock()
    gate = watched(tmp_path, clock)
    config_hash = gate.evaluate(TESTER, STABLE)["config_hash"]
    baseline = {**STABLE, "pitch_deg": -0.11, "roll_deg": -2.25}
    confirmation_id = gate.confirm(TESTER, config_hash, True, baseline)["operator_confirmation"][
        "confirmation_id"
    ]
    rolls = [-1.79, -1.73, -1.87, -1.91, -1.75, -1.81, -1.73, -1.79, -1.85, -1.73] * 6
    for roll in rolls:
        result = gate.evaluate(TESTER, {**STABLE, "pitch_deg": 0.22, "roll_deg": roll})
        assert result["eligible"]
        assert operator_check(result)["status"] == "pass"
        assert result["operator_confirmation"]["confirmation_id"] == confirmation_id
        clock.now += 0.5


def test_a_reading_back_inside_the_release_band_clears_a_pending_move(tmp_path):
    clock = Clock()
    gate = watched(tmp_path, clock)
    config_hash = gate.evaluate(TESTER, STABLE)["config_hash"]
    gate.confirm(TESTER, config_hash, True, STABLE)
    moved = {**STABLE, "roll_deg": 1.2}
    for _ in range(2):
        gate.evaluate(TESTER, moved)
        clock.now += 1.0
    # inside the tolerance but outside the release band: the streak is held, not reset
    assert operator_check(gate.evaluate(TESTER, {**STABLE, "roll_deg": 0.9}))["status"] == "warn"
    clock.now += 1.0
    # back inside the release band: the streak starts over
    assert operator_check(gate.evaluate(TESTER, {**STABLE, "roll_deg": 0.2}))["status"] == "pass"
    for _ in range(3):
        assert gate.evaluate(TESTER, moved)["eligible"]
        clock.now += 1.0
    assert not gate.evaluate(TESTER, moved)["eligible"]


@pytest.mark.parametrize("status", ["moving", "stale", "no_stable_preimpact_reading"])
def test_a_moving_or_stale_reading_waits_and_never_revokes(tmp_path, status):
    clock = Clock()
    gate = watched(tmp_path, clock)
    config_hash = gate.evaluate(TESTER, STABLE)["config_hash"]
    gate.confirm(TESTER, config_hash, True, STABLE)
    moved = {**STABLE, "roll_deg": 1.5}
    gate.evaluate(TESTER, moved)
    for _ in range(10):
        clock.now += 1.0
        result = gate.evaluate(TESTER, {"status": status})
        assert result["operator_confirmation"]["confirmed"]
    # the moving readings did not count: one still reading is not three
    assert gate.evaluate(TESTER, moved)["operator_confirmation"]["confirmed"]
    # a reading the page's poll held over from the last still one is not counted either
    held = {**STABLE, "roll_deg": 1.5, "held_over": "moving"}
    for _ in range(5):
        clock.now += 1.0
        assert gate.evaluate(TESTER, held)["operator_confirmation"]["confirmed"]


def test_a_held_over_reading_warns_instead_of_blocking(tmp_path):
    gate = policy(tmp_path)
    config_hash = gate.evaluate(TESTER, STABLE)["config_hash"]
    gate.confirm(TESTER, config_hash, True, STABLE)
    result = gate.evaluate(TESTER, {**STABLE, "held_over": "moving", "held_s": 1.2})
    lis = next(item for item in result["checks"] if item["id"] == "lis3dh")
    assert result["eligible"]
    assert lis["status"] == "warn"
    assert "keep the unit still" in lis["reason"]


def test_eligibility_changes_are_logged_with_the_reading(tmp_path, caplog):
    clock = Clock()
    gate = watched(tmp_path, clock)
    config_hash = gate.evaluate(TESTER, STABLE)["config_hash"]
    gate.confirm(TESTER, config_hash, True, STABLE)
    caplog.clear()
    with caplog.at_level("INFO", logger="openflight.camera.setup_eligibility"):
        for _ in range(3):
            gate.evaluate(TESTER, STABLE)
        gate.evaluate(TESTER, {"status": "moving"})
        gate.evaluate(TESTER, STABLE)
    lines = [
        record.getMessage()
        for record in caplog.records
        if "[SETUP] eligibility" in record.getMessage()
    ]
    assert len(lines) == 2
    assert "eligible -> blocked" in lines[0]
    assert "moving" in lines[0]
    assert "blocked -> eligible" in lines[1]
    assert "roll +0.00" in lines[1]


def test_an_unreadable_confirmation_file_is_no_confirmation(tmp_path):
    gate = policy(tmp_path)
    config_hash = gate.evaluate(TESTER, STABLE)["config_hash"]
    gate.confirm(TESTER, config_hash, True, STABLE)
    (tmp_path / TESTER / "setup-confirmation.json").write_text("{not json", encoding="utf-8")

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


def _rig_with(tmp_path, **changes):
    data = json.loads(RIG.read_text(encoding="utf-8"))
    data.update(changes)
    path = tmp_path / "rig-new.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _approve(tmp_path, *rigs):
    from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

    path = tmp_path / "approved.json"
    path.write_text(
        json.dumps(
            {
                "schema": "openflight.approved_rig_geometry.v1",
                "approved": [
                    {
                        "rig_geometry_params_sha256": RigGeometry.from_json(rig).snapshot()[
                            "sha256"
                        ],
                        "file": rig.name,
                    }
                    for rig in rigs
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


class TestTheApprovedRigList:
    """C2: admission checks the rig file against an approved-hash list in config/."""

    def test_the_list_holds_the_v3_file_and_keeps_its_first_entry(self):
        from openflight.camera.setup_eligibility import (  # noqa: PLC0415
            DEFAULT_APPROVED_RIGS,
            approved_rig_hashes,
        )
        from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

        assert DEFAULT_APPROVED_RIGS.parent.name == "config"
        approved = approved_rig_hashes(DEFAULT_APPROVED_RIGS)
        # the 2026-09-22 file before F11 recorded the IWR board's rotation; kept
        # so earlier sessions still name an approved rig
        assert approved[0] == "79870a2be3475a405260fa2ff8d00b8f5fe501632da634369fd4a800382cbaa1"
        assert RigGeometry.from_json(RIG).snapshot()["sha256"] in approved
        assert inspect_geometry(RIG)[1]["status"] == "pass"

    def test_a_new_focal_with_an_approved_hash_is_admitted_and_used(self, tmp_path):
        rig = _rig_with(tmp_path, focal_px=480.0)
        gate = SetupEligibility(
            rig,
            tmp_path,
            inclinometer_bus=1,
            inclinometer_address=0x18,
            inclinometer_zero_offset_deg=0.0,
            approved_rigs=_approve(tmp_path, RIG, rig),
        )
        admission = gate.evaluate(TESTER, STABLE)
        assert admission["checks"][0]["status"] == "pass"
        assert admission["config_hash"] is not None

        # the tester's models take the new focal, per binning: 1280x800 is 1:1
        arm5, arm6 = ts.ARMS["arm5"], ts.ARMS["arm6"]
        assert ts.mode_focal_px(arm5, rig) == pytest.approx(960.0)
        assert ts.mode_focal_px(arm6, rig) == pytest.approx(480.0)
        camera = ts._reference_ball_camera(  # pylint: disable=protected-access
            arm5, rig, {"camera_pitch_deg": 0.0}, None, None
        )
        assert camera.focal_size_px == pytest.approx(960.0)
        assert ts.expected_ball_diameter_px(arm5, 1071.0, rig) == pytest.approx(
            960.0 * ts.BALL_DIAMETER_MM / 1041.0
        )

    def test_an_unapproved_file_is_refused(self, tmp_path):
        rig = _rig_with(tmp_path, focal_px=480.0)
        fingerprint, check = inspect_geometry(rig)
        assert fingerprint is None
        assert check["status"] == "block"
        assert "approved" in check["reason"]

    def test_the_remedy_reads_its_heights_from_the_rig(self, tmp_path):
        rig = _rig_with(tmp_path, lens_height_above_floor_mm=110.0)
        gate = SetupEligibility(
            rig,
            tmp_path,
            inclinometer_bus=1,
            inclinometer_address=0x18,
            inclinometer_zero_offset_deg=0.0,
            approved_rigs=_approve(tmp_path, rig),
        )
        operator = gate.evaluate(TESTER, STABLE)["checks"][2]
        assert "110 mm" in operator["remedy"]
        assert "95 mm" not in operator["remedy"]


def _records(tmp_path):
    path = tmp_path / TESTER / "setup_eligibility.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _confirmed_client(tmp_path, **kwargs):
    client = ts.create_app(
        sessions_root=tmp_path, rig_geometry=RIG, tilt=StaticTilt(), **kwargs
    ).test_client()
    eligibility = client.get(
        "/api/tester/setup-eligibility", query_string={"tester_id": TESTER}
    ).get_json()
    client.post(
        "/api/tester/setup-eligibility",
        json={
            "tester_id": TESTER,
            "action": "confirm",
            "config_hash": eligibility["config_hash"],
            "physical_rig_confirmed": True,
        },
    )
    return client


def test_polls_evaluate_without_recording(tmp_path):
    """Wiring audit T12: the page's polls add nothing to setup_eligibility.jsonl."""
    client = _confirmed_client(tmp_path)
    before = _records(tmp_path)
    query = {"tester_id": TESTER}
    for _ in range(10):
        assert client.get("/api/tester/setup-eligibility", query_string=query).status_code == 200
        assert client.get("/api/tester/tee-range", query_string=query).status_code == 200
        body = {**query, "arm_id": "arm5", "environment": "indoors"}
        assert client.post("/api/tester/status", json=body).status_code == 200

    assert _records(tmp_path) == before


def test_an_action_refused_by_the_hardware_check_is_logged_as_refused(tmp_path):
    """Wiring audit T12: the record is written after the IWR check is applied."""
    client = _confirmed_client(tmp_path, require_iwr_preflight=True)
    params = ts.TesterParameters(TESTER, "arm5", "indoors")
    ts.write_arm_state(tmp_path, params, gain=4.0, gain_exposure_us=params.arm.exposure_us)

    response = client.post(
        "/api/tester/run",
        json={"tester_id": TESTER, "arm_id": "arm5", "environment": "indoors", "action": "gain"},
    )

    assert response.status_code == 409
    last = _records(tmp_path)[-1]
    assert last["outcome"] == "blocked"
    assert last["action"] == "gain"
    assert [blocker["id"] for blocker in last["blockers"]] == ["ops243", "camera", "iwr6843_cli"]
