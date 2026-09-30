"""P7-14 (D13): the ready light, one readiness state from what the sensors report."""

from __future__ import annotations

import logging

import pytest

from openflight.ready_light import IWR_TYPICAL_DUMP_S, ReadyLight, SensorStates, compute_state

NOW = 1_790_800_000.0


def _ops(phase="armed", since=NOW - 5.0, **overrides):
    return {"running": True, "serial_open": True, "phase": phase, "phase_since": since, **overrides}


def _iwr(**overrides):
    return {
        "running": True,
        "armed": True,
        "worker_alive": True,
        "serial_open": True,
        "dumping": False,
        "queued": False,
        "dump_started_at": None,
        "typical_dump_s": IWR_TYPICAL_DUMP_S,
        **overrides,
    }


def _camera(**overrides):
    return {
        "running": True,
        "latest_frame_age_s": 0.01,
        "requested_size": [1280, 800],
        "resolved_size": [1280, 800],
        "controls_purpose": "capture",
        "collecting_tail": False,
        "awaiting_handoff": False,
        "saving": False,
        "pending_saves": 0,
        "clip_started_at": None,
        "typical_clip_s": None,
        "buffered_frames": 24,
        "required_pre_frames": 24,
        "fps": 120.0,
        **overrides,
    }


def _states(**overrides):
    values = {"ops": _ops(), "iwr": _iwr(), "camera": _camera()}
    values.update(overrides)
    return SensorStates(**values)


def test_green_only_when_every_fitted_sensor_is_ready():
    light = compute_state(_states(), NOW)

    assert light["state"] == "green"
    assert light["word"] == "SWING"
    assert light["time_left_s"] is None


def test_green_without_optional_sensors_fitted():
    light = compute_state(SensorStates(ops=_ops(), iwr=None, camera=None), NOW)

    assert light["state"] == "green"


@pytest.mark.parametrize(
    ("phase", "cause"),
    [
        ("dumping", "OPS243 dumping"),
        ("draining", "OPS243 draining"),
        ("rearming", "OPS243 re-arming"),
        ("rearmed", "OPS243 processing the shot"),
    ],
)
def test_ops_busy_phases_are_amber_with_a_rough_time_left(phase, cause):
    light = compute_state(_states(ops=_ops(phase, since=NOW - 0.2)), NOW)

    assert light["state"] == "amber"
    assert light["word"] == "WAIT"
    assert light["cause"] == cause
    assert light["time_left_s"] is not None and light["time_left_s"] >= 0


def test_iwr_dump_is_amber_with_its_typical_seven_seconds_left():
    light = compute_state(_states(iwr=_iwr(dumping=True, dump_started_at=NOW - 2.0)), NOW)

    assert light["state"] == "amber"
    assert light["cause"] == "IWR6843 dumping"
    assert light["time_left_s"] == pytest.approx(IWR_TYPICAL_DUMP_S - 2.0)


def test_an_edge_queued_for_the_iwr_is_already_a_dump():
    light = compute_state(_states(iwr=_iwr(queued=True)), NOW)

    assert light["state"] == "amber"
    assert light["time_left_s"] == pytest.approx(IWR_TYPICAL_DUMP_S)


def test_an_overrunning_dump_shows_no_negative_time():
    light = compute_state(_states(iwr=_iwr(dumping=True, dump_started_at=NOW - 30.0)), NOW)

    assert light["time_left_s"] == 0.0


@pytest.mark.parametrize(
    "camera",
    [
        _camera(collecting_tail=True),
        _camera(awaiting_handoff=True),
        _camera(saving=True),
        _camera(pending_saves=1),
    ],
)
def test_camera_saving_a_clip_is_amber(camera):
    light = compute_state(_states(camera=camera), NOW)

    assert light["state"] == "amber"
    assert light["cause"] == "camera saving a clip"


def test_camera_save_time_left_comes_from_the_last_clip():
    camera = _camera(saving=True, clip_started_at=NOW - 1.0, typical_clip_s=3.0)

    assert compute_state(_states(camera=camera), NOW)["time_left_s"] == pytest.approx(2.0)


def test_camera_refilling_its_ring_is_amber():
    light = compute_state(_states(camera=_camera(buffered_frames=12)), NOW)

    assert light["state"] == "amber"
    assert light["cause"] == "camera refilling its ring"
    assert light["time_left_s"] == pytest.approx(0.1)


def test_amber_names_the_longest_wait():
    states = _states(
        ops=_ops("draining", since=NOW),
        iwr=_iwr(dumping=True, dump_started_at=NOW - 1.0),
    )
    light = compute_state(states, NOW)

    assert light["cause"] == "IWR6843 dumping"
    assert [cause["cause"] for cause in light["causes"]] == ["OPS243 draining", "IWR6843 dumping"]


@pytest.mark.parametrize(
    ("states", "cause"),
    [
        (_states(ops=None), "OPS243 radar is not connected"),
        (_states(ops=_ops(running=False)), "OPS243 radar is not running"),
        (_states(ops=_ops(serial_open=False)), "OPS243 serial link is closed"),
        (_states(ops=_ops(phase=None, since=None)), "OPS243 starting: not armed yet"),
        (_states(ops=_ops("stopped")), "OPS243 stopped"),
        (_states(iwr=_iwr(running=False)), "IWR6843 radar is not running"),
        (_states(iwr=_iwr(worker_alive=False)), "IWR6843 radar is not running"),
        (_states(iwr=_iwr(serial_open=False)), "IWR6843 serial link is closed"),
        (_states(iwr=_iwr(armed=False)), "IWR6843 not armed yet"),
        (_states(camera=_camera(running=False)), "camera is not running"),
        (_states(camera=_camera(latest_frame_age_s=None)), "camera frames stopped"),
        (_states(camera=_camera(latest_frame_age_s=3.0)), "camera frames stopped"),
        (
            _states(camera=_camera(resolved_size=[640, 400])),
            "camera is not in this setting (640x400 running, 1280x800 asked)",
        ),
        (
            _states(camera=_camera(controls_purpose="still_photo")),
            "camera is set for a still photo",
        ),
    ],
)
def test_a_missing_or_failed_sensor_is_red(states, cause):
    light = compute_state(states, NOW)

    assert light["state"] == "red"
    assert light["word"] == "NOT READY"
    assert light["cause"] == cause


def test_red_outranks_amber():
    states = _states(ops=_ops("dumping"), camera=_camera(running=False))

    assert compute_state(states, NOW)["cause"] == "camera is not running"


def test_setup_problems_and_holds_are_red_and_come_first():
    states = _states(setup_problems=("no admitted setup",))

    assert compute_state(states, NOW)["cause"] == "no admitted setup"
    held = compute_state(states, NOW, hold="ladder light check running")
    assert held["cause"] == "ladder light check running"
    assert [cause["cause"] for cause in held["causes"]] == [
        "ladder light check running",
        "no admitted setup",
    ]


def test_the_light_is_off_where_nothing_reports_readiness():
    light = ReadyLight().update(None, now=NOW)

    assert light["state"] == "off"


def test_each_change_is_logged_once_with_its_time(caplog):
    light = ReadyLight()
    caplog.set_level(logging.INFO, logger="openflight.ready_light")

    light.update(_states(), now=NOW)
    light.update(_states(), now=NOW + 0.2)
    light.update(_states(ops=_ops("dumping", since=NOW + 0.3)), now=NOW + 0.4)
    light.update(_states(ops=_ops("dumping", since=NOW + 0.3)), now=NOW + 0.6)
    light.update(_states(ops=_ops("draining", since=NOW + 1.0)), now=NOW + 1.1)
    light.update(_states(), now=NOW + 2.0)

    lines = [record.getMessage() for record in caplog.records]
    assert len(lines) == 4
    assert lines[0].startswith("[READY] SWING at ")
    assert "WAIT at" in lines[1] and "OPS243 dumping" in lines[1]
    assert "OPS243 draining" in lines[2]
    assert lines[3].startswith("[READY] SWING at ")


def test_state_since_is_the_time_of_the_change():
    light = ReadyLight()

    light.update(_states(), now=NOW)
    assert light.update(_states(), now=NOW + 5.0)["since"] == NOW
    assert light.update(_states(iwr=_iwr(armed=False)), now=NOW + 6.0)["since"] == NOW + 6.0


def test_a_hold_is_red_until_it_is_cleared():
    light = ReadyLight()

    light.set_hold("ladder stopped")
    held = light.update(_states(), now=NOW)
    assert (held["state"], held["cause"], held["hold"]) == (
        "red",
        "ladder stopped",
        "ladder stopped",
    )

    light.set_hold(None)
    assert light.update(_states(), now=NOW + 1.0)["state"] == "green"


def test_a_swing_is_picked_up_once_across_its_edge_and_ops_dump():
    light = ReadyLight()

    light.swing_detected("edge", at=NOW)
    light.swing_detected("ops_dump", at=NOW + 0.05)
    swing = light.update(_states(), now=NOW + 0.1)["swing"]

    assert swing["id"] == 1
    assert swing["at"] == NOW
    assert swing["result"] is None

    light.swing_detected("edge", at=NOW + 12.0)
    assert light.update(_states(), now=NOW + 12.1)["swing"]["id"] == 2


def test_the_shot_result_reaches_its_swing():
    light = ReadyLight()

    light.swing_detected("edge", at=NOW)
    light.swing_result("shot", ball_speed_mph=141.26, at=NOW + 1.5)
    swing = light.update(_states(), now=NOW + 2.0)["swing"]

    assert swing["id"] == 1
    assert swing["result"] == {"kind": "shot", "text": "141.3 mph", "ball_speed_mph": 141.26}


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        ("no_radar_shot", "No radar shot"),
        ("not_a_shot", "Not a shot"),
        ("not_counted", "Not counted: setup not ready"),
    ],
)
def test_a_swing_without_a_shot_says_so(kind, text):
    light = ReadyLight()

    light.swing_detected("edge", at=NOW)
    light.swing_result(kind, at=NOW + 1.0)

    assert light.update(_states(), now=NOW + 1.1)["swing"]["result"]["text"] == text


def test_a_result_with_no_swing_seen_is_its_own_swing():
    light = ReadyLight()

    light.swing_result("shot", ball_speed_mph=120.0, at=NOW)
    swing = light.update(_states(), now=NOW + 0.1)["swing"]

    assert swing["id"] == 1
    assert swing["result"]["kind"] == "shot"


def test_the_ball_hitting_the_net_does_not_replace_the_shot():
    """A real shot sounds twice: impact, then the net about a second later."""
    light = ReadyLight()

    light.swing_detected("edge", at=NOW)
    light.swing_detected("edge", at=NOW + 1.0)  # the net
    light.swing_result("shot", ball_speed_mph=120.0, at=NOW + 1.5)
    light.swing_detected("ops_dump", at=NOW + 1.6)  # the net's OPS dump
    light.swing_result("not_a_shot", at=NOW + 2.5)
    swing = light.update(_states(), now=NOW + 2.6)["swing"]

    assert swing["id"] == 1
    assert swing["result"]["kind"] == "shot"


def test_a_shot_replaces_an_earlier_non_shot_result_for_the_same_swing():
    light = ReadyLight()

    light.swing_detected("edge", at=NOW)
    light.swing_result("not_a_shot", at=NOW + 0.5)
    light.swing_result("shot", ball_speed_mph=99.0, at=NOW + 1.5)

    assert light.update(_states(), now=NOW + 2.0)["swing"]["result"]["kind"] == "shot"
