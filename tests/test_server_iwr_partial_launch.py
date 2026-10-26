"""A partial IWR dump's accepted launch is published, labelled estimated with its reason.

A transfer that lost bytes is an invalid capture, but the runtime still measures the
frames the decoder recovered (``iwr6843.partial_dump``). Its launch is the shot's radar
launch, weaker than measured; only a capture with nothing to measure is a failure."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import server as server_module, source_labels as labels
from openflight.clubs import ClubType
from openflight.iwr6843.lcmf import LCMFResult
from openflight.launch_monitor import Shot


def _shot():
    return Shot(
        ball_speed_mph=100.0,
        club_speed_mph=80.0,
        timestamp=datetime.now(),
        impact_timestamp=100.0,
        club=ClubType.IRON_7,
    )


def _partial_capture():
    return SimpleNamespace(
        trigger_timestamp=100.01,
        path=None,
        raw=None,
        dump_duration_s=4.5,
        error="short dump: 731,264 of 732,812 bytes",
        valid=False,
        sequence=7,
    )


@pytest.fixture
def emitted(monkeypatch):
    seen = []
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module, "shot_ball_at_address", lambda _ts: None)
    monkeypatch.setattr(
        server_module,
        "_emit_iwr6843_trigger_status",
        lambda _shot, **status: seen.append(status),
    )
    return seen


def _runtime(monkeypatch, measurement):
    monkeypatch.setattr(
        server_module,
        "iwr6843_runtime",
        SimpleNamespace(
            process_shot=lambda **_kw: SimpleNamespace(
                capture=_partial_capture(),
                measurement=measurement,
                club_path=None,
                withheld_reason=None,
                launch_range=None,
                ball_range=None,
            )
        ),
    )


def test_a_partial_dump_s_accepted_launch_is_the_shot_s_radar_launch(monkeypatch, emitted):
    _runtime(
        monkeypatch,
        LCMFResult(status="accepted_iwr_partial", angle_deg=14.2, component_std_deg=0.8),
    )
    shot = _shot()

    server_module._process_iwr6843_angle(shot)  # pylint: disable=protected-access

    assert shot.launch_angle_vertical == 14.2
    assert shot.launch_angle_vertical_source == "radar"
    assert shot.iwr6843_launch_status == "accepted_iwr_partial"
    assert emitted[-1]["state"] == "accepted"
    assert emitted[-1]["angle_deg"] == 14.2
    # the label says it came from a partial dump, whatever the range rested on
    label, note = labels.shot_launch_label(shot, "vertical")
    assert label == "estimated"
    assert labels.IWR_PARTIAL_NOTE in note


def test_a_partial_dump_s_rejected_launch_says_why(monkeypatch, emitted):
    _runtime(monkeypatch, LCMFResult(status="rejected_spread_iwr_partial"))
    shot = _shot()

    server_module._process_iwr6843_angle(shot)  # pylint: disable=protected-access

    assert shot.launch_angle_vertical is None
    assert emitted[-1] == {"state": "rejected", "reason": "rejected_spread_iwr_partial"}


def test_a_failed_capture_with_nothing_recovered_is_still_an_error(monkeypatch, emitted):
    _runtime(monkeypatch, None)
    shot = _shot()

    server_module._process_iwr6843_angle(shot)  # pylint: disable=protected-access

    assert shot.launch_angle_vertical is None
    assert emitted[-1] == {
        "state": "error",
        "reason": "short dump: 731,264 of 732,812 bytes",
    }
