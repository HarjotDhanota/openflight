"""The golfer's hand, changed at run time from the kiosk's club picker.

Right-handed is the default and left-handed is explicit. A tap sets the active
profile's hand, which is kept with the profile and beats the kiosk's ``--handed``
flag; a connected simulator that has said which hand still wins over both. Each
shot carries the hand it was detected with, so a change reaches the next shot and
never one already being processed, and the session log records every change.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import server as server_module
from openflight.launch_monitor import Shot
from openflight.profiles import ProfileStore
from openflight.server import MockLaunchMonitor, on_shot_detected
from openflight.sim.types import ConnectionState, PlayerUpdate


class _FakeLog:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def log_event(self, entry_type, data):
        self.events.append((entry_type, data))

    def __getattr__(self, name):
        # every other session-log call the shot path makes is a no-op here
        if name.startswith("log_"):
            return lambda *args, **kwargs: True
        raise AttributeError(name)


class _FakeConnector:
    def __init__(self, name="gspro", connected=True):
        self.name = name
        self._connected = connected

    def is_connected(self):
        return self._connected


@pytest.fixture(name="kiosk")
def fixture_kiosk(tmp_path, monkeypatch):
    store = ProfileStore(tmp_path / "profiles.json")
    emitted: list[tuple[str, object]] = []
    log = _FakeLog()
    monkeypatch.setattr(server_module, "profile_store", store)
    monkeypatch.setattr(
        server_module.socketio, "emit", lambda *args, **kwargs: emitted.append(args[:2])
    )
    monkeypatch.setattr(server_module, "get_session_logger", lambda: log)
    monkeypatch.setattr(server_module, "golfer_handed", None)
    monkeypatch.setattr(server_module, "sim_connectors", [])
    monkeypatch.setattr(server_module, "sim_player_state", server_module.SimPlayerState())
    return SimpleNamespace(store=store, emitted=emitted, log=log, path=tmp_path / "profiles.json")


def _sent_hands(emitted):
    return [payload for name, payload in emitted if name == "handedness"]


def test_a_tap_sets_the_active_profiles_hand_and_tells_the_kiosk(kiosk):
    response, status = server_module.apply_handedness({"handed": "LH"})

    assert status == 200
    assert response == {"status": "applied", "handed": "LH", "source": "profile"}
    assert server_module.golfer_handedness() == {"handed": "LH", "source": "profile"}
    assert _sent_hands(kiosk.emitted)[-1] == {"handed": "LH", "source": "profile"}
    assert kiosk.store.get_active().settings["handed"] == "LH"


def test_the_socket_event_applies_the_hand(kiosk):
    server_module.handle_set_handedness({"handed": "LH"})

    assert server_module.golfer_handedness()["handed"] == "LH"


def test_the_hand_is_kept_with_the_profile_across_a_restart(kiosk, monkeypatch):
    server_module.apply_handedness({"handed": "LH"})

    monkeypatch.setattr(server_module, "profile_store", ProfileStore(kiosk.path))

    assert server_module.golfer_handedness() == {"handed": "LH", "source": "profile"}


def test_the_roster_goes_out_with_the_new_setting(kiosk):
    server_module.apply_handedness({"handed": "LH"})

    snapshot = next(payload for name, payload in reversed(kiosk.emitted) if name == "profiles")
    active = next(p for p in snapshot["profiles"] if p["id"] == snapshot["active_profile_id"])
    assert active["settings"]["handed"] == "LH"


@pytest.mark.parametrize("payload", [None, "LH", {}, {"handed": "left"}, {"handed": None}])
def test_anything_but_rh_or_lh_is_refused_and_changes_nothing(kiosk, payload):
    response, status = server_module.apply_handedness(payload)

    assert status == 400
    assert "error" in response
    assert server_module.golfer_handedness() == {"handed": "RH", "source": "default"}
    assert "handed" not in kiosk.store.get_active().settings
    # the kiosk is told the unchanged hand, so a stale screen heals
    assert _sent_hands(kiosk.emitted) == [{"handed": "RH", "source": "default"}]
    assert not kiosk.log.events


def test_the_profiles_hand_beats_the_start_flag(kiosk, monkeypatch):
    monkeypatch.setattr(server_module, "golfer_handed", "LH")
    assert server_module.golfer_handedness() == {"handed": "LH", "source": "setup"}

    server_module.apply_handedness({"handed": "RH"})

    assert server_module.golfer_handedness() == {"handed": "RH", "source": "profile"}


def test_each_profile_keeps_its_own_hand(kiosk):
    first = kiosk.store.get_active()
    server_module.apply_handedness({"handed": "LH"})
    second = kiosk.store.add("Lefty's friend")

    assert server_module.golfer_handedness() == {"handed": "RH", "source": "default"}

    kiosk.emitted.clear()
    server_module.apply_active_profile({"profile_id": first.id})

    assert server_module.golfer_handedness() == {"handed": "LH", "source": "profile"}
    assert _sent_hands(kiosk.emitted) == [{"handed": "LH", "source": "profile"}]
    assert second.settings == {}


def test_adding_a_profile_tells_the_kiosk_its_hand(kiosk):
    server_module.apply_handedness({"handed": "LH"})
    kiosk.emitted.clear()

    server_module.handle_add_profile({"name": "New golfer"})

    assert _sent_hands(kiosk.emitted) == [{"handed": "RH", "source": "default"}]


def test_a_connected_simulators_hand_still_wins(kiosk):
    server_module.sim_connectors = [_FakeConnector()]
    server_module.sim_player_state.apply(PlayerUpdate(handed="RH"))

    response, status = server_module.apply_handedness({"handed": "LH"})

    assert status == 200
    assert response == {"status": "applied", "handed": "RH", "source": "simulator"}
    assert _sent_hands(kiosk.emitted)[-1] == {"handed": "RH", "source": "simulator"}
    # kept for when the simulator goes away
    assert kiosk.store.get_active().settings["handed"] == "LH"
    server_module.sim_connectors[0]._connected = False
    assert server_module.golfer_handedness() == {"handed": "LH", "source": "profile"}


def test_a_simulator_connecting_or_dropping_re_sends_the_hand(kiosk, monkeypatch):
    server_module.apply_handedness({"handed": "LH"})
    connector = _FakeConnector()
    server_module.sim_connectors = [connector]
    server_module.sim_player_state.apply(PlayerUpdate(handed="RH"))
    kiosk.emitted.clear()
    event = SimpleNamespace(
        state=ConnectionState.CONNECTED,
        host="127.0.0.1",
        port=921,
        attempt=0,
        next_retry_in_s=0.0,
        message="",
    )

    server_module._sim_on_status("gspro", event)
    assert _sent_hands(kiosk.emitted) == [{"handed": "RH", "source": "simulator"}]

    connector._connected = False
    kiosk.emitted.clear()
    server_module._sim_on_status(
        "gspro", SimpleNamespace(**{**vars(event), "state": ConnectionState.RECONNECT_BACKOFF})
    )
    assert _sent_hands(kiosk.emitted) == [{"handed": "LH", "source": "profile"}]


def test_the_session_log_records_each_change(kiosk):
    profile = kiosk.store.get_active()

    server_module.apply_handedness({"handed": "LH"})
    server_module.apply_handedness({"handed": "RH"})

    assert [entry for entry, _ in kiosk.log.events] == ["handedness", "handedness"]
    first, second = (record for _, record in kiosk.log.events)
    assert first == {
        "handed": "LH",
        "source": "profile",
        "requested": "LH",
        "cause": "kiosk",
        "previous": {"handed": "RH", "source": "default"},
        "profile_id": profile.id,
    }
    assert second["handed"] == "RH" and second["previous"]["handed"] == "LH"


def test_the_session_log_records_a_change_a_simulator_overrides(kiosk):
    server_module.sim_connectors = [_FakeConnector()]
    server_module.sim_player_state.apply(PlayerUpdate(handed="RH"))

    server_module.apply_handedness({"handed": "LH"})

    ((_, record),) = kiosk.log.events
    assert (record["requested"], record["handed"], record["source"]) == ("LH", "RH", "simulator")


def test_switching_profile_logs_the_hand_only_when_it_changes(kiosk):
    first = kiosk.store.get_active()
    second = kiosk.store.add("Second")
    server_module.apply_active_profile({"profile_id": first.id})
    assert not kiosk.log.events

    kiosk.store.set_setting(second.id, "handed", "LH")
    server_module.apply_active_profile({"profile_id": second.id})

    ((entry, record),) = kiosk.log.events
    assert entry == "handedness"
    assert (record["handed"], record["cause"], record["profile_id"]) == (
        "LH",
        "profile_switch",
        second.id,
    )


# --- every shot carries the hand it was detected with -----------------------


def _detected_shot(monkeypatch) -> Shot:
    monitor = MockLaunchMonitor()
    monitor.connect()
    monitor.start()
    monkeypatch.setattr(server_module, "monitor", monitor)
    shot = monitor.simulate_shot(ball_speed=140.0)
    on_shot_detected(shot)
    return shot


def test_a_shot_is_stamped_with_the_hand_it_was_hit_with(kiosk, monkeypatch):
    server_module.apply_handedness({"handed": "LH"})

    shot = _detected_shot(monkeypatch)

    assert (shot.handed, shot.handed_source) == ("LH", "profile")
    assert shot.to_dict()["handed"] == "LH"
    assert server_module.shot_to_dict(shot)["handed"] == "LH"


def test_a_new_shot_has_no_hand_until_it_is_detected():
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime(2026, 10, 24, 9, 0))

    assert shot.handed is None
    assert shot.to_dict()["handed"] is None


def test_a_shot_without_a_hand_takes_the_golfers(kiosk):
    server_module.apply_handedness({"handed": "LH"})
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime(2026, 10, 24, 9, 0))

    assert server_module.shot_handedness(shot) == "LH"
    shot.handed = "RH"
    assert server_module.shot_handedness(shot) == "RH"
