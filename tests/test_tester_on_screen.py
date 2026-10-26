"""The tester page on the unit's own screen (25 Oct).

``start-tester.sh --on-screen`` opens the tester page in the kiosk's Electron shell.
That one window then switches between the tester page and the kiosk, so:

- free play started from it starts the kiosk with ``--no-browser`` (the page shows the
  kiosk in its own window) instead of a second fullscreen window over the page;
- every kiosk the tester starts is told the tester's port, so its Tester button can
  find the page again;
- the tester answers a cheap ping that says whether a phone could reach it.
"""

import pytest

from openflight.camera import tester_server as ts
from tests import test_tester_server as tester_server_tests
from tests.test_free_play import Jobs
from tests.test_tester_server import (
    RIG,
    TESTER_SETUP,
    eligible_app,
    params,
    setup_saw_ball,
)

KIOSK_ACTIONS = ("ladder", "swings", "free_play")


def _command(action, **kwargs):
    commands, _log = ts.action_commands(
        action, params(arm_id="arm5"), kwargs.pop("root"), RIG, tester_setup=TESTER_SETUP, **kwargs
    )
    return commands[0]


def test_free_play_shown_in_the_tester_window_opens_no_kiosk_window(tmp_path):
    command = _command("free_play", root=tmp_path, kiosk_window="shared")

    assert command[2] == "--no-browser"


def test_free_play_for_a_phone_still_opens_the_kiosk_on_the_unit_screen(tmp_path):
    assert "--no-browser" not in _command("free_play", root=tmp_path)
    assert "--no-browser" not in _command("free_play", root=tmp_path, kiosk_window="own")


def test_an_unknown_kiosk_window_is_refused(tmp_path):
    with pytest.raises(ValueError, match="kiosk_window"):
        _command("free_play", root=tmp_path, kiosk_window="popup")


@pytest.mark.parametrize("action", KIOSK_ACTIONS)
def test_every_kiosk_the_tester_starts_is_told_its_port(tmp_path, action):
    ts.write_arm_state(tmp_path, params(arm_id="arm5"), gain=3.0, gain_exposure_us=300)
    command = _command(action, root=tmp_path, tester_control_port=9000)

    assert command.count("--tester-control-port") == 1
    assert command[command.index("--tester-control-port") + 1] == "9000"


def test_a_kiosk_never_told_the_port_gets_none(tmp_path):
    assert "--tester-control-port" not in _command("free_play", root=tmp_path)


def _start_free_play(tmp_path, monkeypatch, **extra):
    setup_saw_ball(tmp_path)
    monkeypatch.setattr(ts, "setup_light_warning", lambda _: {"light_index_dn_per_us_gain": 0.25})
    manager = Jobs()
    app = eligible_app(sessions_root=tmp_path, manager=manager, tester_control_port=9000)
    response = app.test_client().post(
        "/api/tester/run",
        json={
            "action": "free_play",
            "tester_id": "20260922-name",
            "arm_id": "arm1",
            "environment": "indoors",
            "resolution": "arm5",
            **extra,
        },
    )
    return response, manager


def test_the_on_screen_page_starts_free_play_in_its_own_window(tmp_path, monkeypatch):
    response, manager = _start_free_play(tmp_path, monkeypatch, kiosk_window="shared")

    assert response.status_code == 202, response.get_json()
    command = manager.calls[0][1][0]
    assert "--no-browser" in command
    assert command.count("--tester-control-port") == 1
    assert command[command.index("--tester-control-port") + 1] == "9000"
    assert response.get_json()["free_play"]["kiosk_window"] == "shared"


def test_a_phone_starting_free_play_keeps_the_kiosk_window(tmp_path, monkeypatch):
    response, manager = _start_free_play(tmp_path, monkeypatch)

    assert response.status_code == 202, response.get_json()
    assert "--no-browser" not in manager.calls[0][1][0]
    assert response.get_json()["free_play"]["kiosk_window"] == "own"


def test_free_play_refuses_an_unknown_kiosk_window(tmp_path, monkeypatch):
    response, manager = _start_free_play(tmp_path, monkeypatch, kiosk_window="popup")

    assert response.status_code == 400
    assert "kiosk_window" in response.get_json()["error"]
    assert not manager.calls


def test_the_ladder_kiosk_is_told_the_tester_port(tmp_path, monkeypatch):
    seen = []
    original = ts.action_commands

    def recording(*args, **kwargs):
        seen.append(kwargs.get("tester_control_port"))
        return original(*args, **kwargs)

    monkeypatch.setattr(ts, "action_commands", recording)
    client, manager = tester_server_tests.TestTheLadderHoldsUp()._client(tmp_path, monkeypatch)
    try:
        assert (
            client.post(
                "/api/tester/ladder/start", json=tester_server_tests.TestTheLadderHoldsUp.body
            ).status_code
            == 200
        )
    finally:
        manager.cancel()

    assert seen == [8765]


@pytest.mark.parametrize("bind_host, lan", [("127.0.0.1", False), ("0.0.0.0", True)])
def test_the_ping_says_whether_a_phone_can_reach_the_page(tmp_path, bind_host, lan):
    app = ts.create_app(sessions_root=tmp_path, bind_host=bind_host)

    response = app.test_client().get("/api/tester/ping")

    assert response.status_code == 200
    assert response.get_json() == {"tester": True, "lan": lan}
