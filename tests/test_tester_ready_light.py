"""P7-14: the tester page's ready light, the kiosk's plus what only the tester knows."""

from __future__ import annotations

import pytest

from openflight.camera import study_ladder as sl, tester_server as ts
from openflight.camera.tester_ready_light import ReadyLightRelay, compose, page_causes

RIG = ts.DEFAULT_RIG_GEOMETRY
GREEN = {
    "schema_version": 1,
    "state": "green",
    "word": "SWING",
    "cause": "Ready for a swing",
    "time_left_s": None,
    "causes": [],
    "since": 1.0,
    "checked_at": 2.0,
    "hold": None,
    "swing": {"id": 4, "at": 1.5, "source": "edge", "result": None},
}
LADDER_JOB = {"state": "running", "action": "ladder"}
SWINGS_JOB = {"state": "running", "action": "swings"}
IDLE_JOB = {"state": "idle", "action": None}


def _runner(tmp_path):
    return sl.LadderRunner(
        sl.LadderState(tmp_path / "ladder.json"),
        client=None,
        run_dir=lambda: None,
        black_floor=lambda arm: 18.0,
        gain_at_300=lambda arm: 3.0,
        light_index=lambda arm: 0.05,
        photo_dir=tmp_path / "impact",
        on_mode_done=lambda arm: None,
    )


# ---------------------------------------------------------------- the ladder's hold


def test_ladder_holds_red_while_a_rung_is_being_checked(tmp_path):
    runner = _runner(tmp_path)

    assert runner.ready_hold() == "ladder light check running"

    rung = runner.state.current.rung_id
    runner.state.begin(rung, 3.0, {"ok": True})
    runner._configured_rung = rung  # pylint: disable=protected-access
    assert runner.ready_hold() is None


def test_ladder_restarting_between_settings_is_red(tmp_path):
    runner = _runner(tmp_path)
    runner.mode = "between modes"

    assert runner.ready_hold() == "kiosk restarting between settings"


def test_a_stopped_ladder_is_red(tmp_path):
    runner = _runner(tmp_path)
    runner.stop(wait=False)

    assert runner.ready_hold() == "ladder stopped"


def test_a_finished_ladder_is_red(tmp_path):
    runner = _runner(tmp_path)
    for rung in sl.LADDER:
        runner.state.begin(rung.rung_id, 2.0, {"ok": False, "reason": "too dark"})
        if runner.state.current is None:
            break
    runner.stop(wait=False)

    assert runner.ready_hold() == "ladder finished"


def test_a_face_photo_owed_is_red(tmp_path):
    runner = _runner(tmp_path)
    runner.state._data["pending_photo"] = {  # pylint: disable=protected-access
        "capture": "camera_a",
        "rung_id": "full-300",
    }

    assert runner.ready_hold() == "photo of the club face owed: do not swing"


# ---------------------------------------------------------------- tester side


@pytest.mark.parametrize(
    ("job", "hold", "expected"),
    [
        (LADDER_JOB, "ladder light check running", (None, "ladder light check running")),
        (LADDER_JOB, None, (None, None)),
        (SWINGS_JOB, "ladder stopped", (None, None)),  # a plain swings kiosk has no ladder
        (IDLE_JOB, None, ("kiosk not running", None)),
    ],
)
def test_tester_side_names_the_kiosk_and_ladder_causes(tmp_path, job, hold, expected):
    runner = _runner(tmp_path)
    runner.ready_hold = lambda: hold

    assert page_causes(job, runner) == expected


def test_between_settings_the_kiosk_is_down_and_restarting(tmp_path):
    runner = _runner(tmp_path)
    runner.mode = "between modes"
    job = {"state": "cancelled", "action": "ladder"}

    assert page_causes(job, runner) == (
        "kiosk restarting between settings",
        "kiosk restarting between settings",
    )


def test_after_the_ladder_the_page_says_it_finished(tmp_path):
    runner = _runner(tmp_path)
    runner.ready_hold = lambda: "ladder finished"

    assert page_causes({"state": "cancelled", "action": "ladder"}, runner)[0] == "ladder finished"
    assert page_causes(IDLE_JOB, None) == ("kiosk not running", None)


# ---------------------------------------------------------------- compose


def test_the_kiosk_light_passes_through():
    light = compose(GREEN, down=None, hold=None)

    assert light["state"] == "green"
    assert light["swing"]["id"] == 4
    assert light["source"] == "kiosk"


def test_a_down_kiosk_is_red_with_the_reason():
    light = compose(None, down="kiosk not running", hold=None)

    assert (light["state"], light["word"], light["cause"]) == (
        "red",
        "NOT READY",
        "kiosk not running",
    )
    assert light["source"] == "tester"


def test_an_unreachable_kiosk_is_restarting():
    light = compose(None, down=None, hold=None)

    assert (light["state"], light["cause"]) == ("red", "kiosk restarting")


def test_a_hold_the_kiosk_has_not_taken_is_red_here_already():
    light = compose(GREEN, down=None, hold="ladder light check running")

    assert (light["state"], light["cause"]) == ("red", "ladder light check running")
    assert light["swing"]["id"] == 4  # a swing still flashes


# ---------------------------------------------------------------- relay


class FakeKiosk:
    def __init__(self, *, answers=True):
        self.light = dict(GREEN) if answers else None
        self.holds = []

    def ready_light(self):
        return None if self.light is None else dict(self.light)

    def set_ready_hold(self, cause):
        self.holds.append(cause)
        if self.light is None:
            return None
        self.light = {**self.light, "hold": cause}
        if cause:
            self.light.update(state="red", word="NOT READY", cause=cause)
        return dict(self.light)


def test_the_relay_pushes_the_ladder_hold_to_the_kiosk_once(tmp_path):
    kiosk = FakeKiosk()
    hold = {"value": "ladder light check running"}
    relay = ReadyLightRelay(kiosk, lambda _tester: (None, hold["value"]))

    first = relay.sync()
    second = relay.sync()
    hold["value"] = None
    third = relay.sync()

    assert kiosk.holds == ["ladder light check running", None]
    assert (first["state"], first["cause"]) == ("red", "ladder light check running")
    assert second["cause"] == "ladder light check running"
    assert third["hold"] is None


def test_the_relay_does_not_ask_a_kiosk_that_is_down():
    kiosk = FakeKiosk()
    relay = ReadyLightRelay(kiosk, lambda _tester: ("kiosk not running", None))

    assert relay.sync()["cause"] == "kiosk not running"
    assert kiosk.holds == []


def test_the_relay_reports_an_unreachable_kiosk_as_restarting():
    relay = ReadyLightRelay(FakeKiosk(answers=False), lambda _tester: (None, None))

    assert relay.sync()["cause"] == "kiosk restarting"


# ---------------------------------------------------------------- endpoint


class _EligibleSetup:
    def current_config_hash(self):
        return "test-config"

    def confirmation_valid(self, _tester_id):
        return True


class _Jobs(ts.TesterJobManager):
    def __init__(self, status):
        super().__init__()
        self._fixed = status

    def status(self):
        return {**self._fixed, "message": "", "output": []}


def test_the_page_polls_the_light_through_the_tester_server(tmp_path, monkeypatch):
    monkeypatch.setattr(sl.KioskClient, "ready_light", lambda self: dict(GREEN))
    monkeypatch.setattr(sl.KioskClient, "set_ready_hold", lambda self, cause: None)
    app = ts.create_app(
        sessions_root=tmp_path,
        rig_geometry=RIG,
        setup_policy=_EligibleSetup(),
        manager=_Jobs({"state": "running", "action": "swings"}),
    )

    response = app.test_client().get("/api/tester/ready-light?tester_id=20260922-name")

    assert response.status_code == 200
    assert response.get_json()["state"] == "green"
    assert response.headers["Cache-Control"] == "no-store"


def test_the_page_shows_the_kiosk_not_running_when_idle(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(sl.KioskClient, "ready_light", lambda self: asked.append(1))
    app = ts.create_app(
        sessions_root=tmp_path,
        rig_geometry=RIG,
        setup_policy=_EligibleSetup(),
        manager=_Jobs({"state": "idle", "action": None}),
    )

    light = app.test_client().get("/api/tester/ready-light").get_json()

    assert (light["state"], light["cause"]) == ("red", "kiosk not running")
    assert asked == []


def test_an_unknown_tester_is_refused(tmp_path):
    app = ts.create_app(sessions_root=tmp_path, rig_geometry=RIG, setup_policy=_EligibleSetup())

    assert app.test_client().get("/api/tester/ready-light?tester_id=../x").status_code == 400
