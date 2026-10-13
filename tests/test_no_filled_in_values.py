"""A fusion unit reports a missing value as missing (D17); OPS-only installs keep labelled estimates.

With the IWR or the camera running, the kiosk used to fill a missing vertical launch
from club tables, carry from an "optimal" spin and a club-typical one. On a fusion unit
a missing launch or spin stays missing, and carry is built only from measured inputs.
An OPS-only install has nothing to measure angles with, so it keeps its labelled
estimates for simulators. A calculated spin is a model's output: labelled calculated,
never high-confidence.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import server as server_module
from openflight.ballistics import resolve_launch
from openflight.clubs import ClubType
from openflight.launch_monitor import SPIN_CONFIDENCE_HIGH, Shot
from openflight.sim.resolver import resolve_shot
from openflight.sim.types import PlayerState


def _shot(**overrides):
    values = {
        "ball_speed_mph": 150.0,
        "club_speed_mph": 105.0,
        "timestamp": datetime.now(),
        "club": ClubType.DRIVER,
    }
    values.update(overrides)
    return Shot(**values)


@pytest.fixture
def fusion_unit(monkeypatch):
    monkeypatch.setattr(server_module, "iwr6843_runtime", SimpleNamespace())


@pytest.fixture
def ops_only(monkeypatch):
    monkeypatch.setattr(server_module, "iwr6843_runtime", None)
    monkeypatch.setattr(server_module, "camera_capture_runtime", None)


@pytest.fixture
def quiet_finalize(monkeypatch):
    monkeypatch.setattr(server_module, "ballistics_enabled", True)
    monkeypatch.setattr(server_module, "calculated_spin_enabled", False)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)


def _finalize(shot):
    server_module._finalize_shot_detected(shot, emit_event="shot")  # pylint: disable=protected-access


def test_a_fusion_unit_leaves_a_missing_vertical_launch_missing(fusion_unit):
    shot = _shot()

    server_module._ensure_user_facing_launch_angles(shot)  # pylint: disable=protected-access

    assert shot.launch_angle_vertical is None
    assert shot.launch_angle_vertical_source is None


def test_an_ops_only_install_keeps_its_labelled_launch_estimate(ops_only):
    shot = _shot()

    server_module._ensure_user_facing_launch_angles(shot)  # pylint: disable=protected-access

    assert shot.launch_angle_vertical is not None
    assert shot.launch_angle_vertical_source == "estimated"
    assert shot.launch_angle_horizontal == 0.0
    assert shot.launch_angle_horizontal_source == "estimated"


def test_a_fusion_unit_without_a_measured_launch_gives_no_carry(fusion_unit, quiet_finalize):
    shot = _shot(spin_rpm=2700, spin_confidence=0.85)

    _finalize(shot)

    assert shot.carry_spin_adjusted is None


def test_a_fusion_unit_without_a_measured_spin_gives_no_carry(fusion_unit, quiet_finalize):
    shot = _shot(
        launch_angle_vertical=12.0,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=1.0,
        launch_angle_horizontal_source="radar",
        spin_rpm=2700,
        spin_confidence=0.3,
    )

    _finalize(shot)

    assert shot.carry_spin_adjusted is None


def test_a_fusion_unit_with_measured_inputs_gets_ballistic_carry(fusion_unit, quiet_finalize):
    shot = _shot(
        launch_angle_vertical=12.0,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=1.0,
        launch_angle_horizontal_source="radar",
        spin_rpm=2700,
        spin_confidence=0.85,
        spin_axis_deg=2.0,
    )

    _finalize(shot)

    assert shot.carry_spin_adjusted is not None and shot.carry_spin_adjusted > 0


def test_an_ops_only_install_keeps_its_table_carry(ops_only, quiet_finalize):
    shot = _shot(spin_rpm=2700, spin_confidence=0.3)

    _finalize(shot)

    assert shot.carry_spin_adjusted is not None and shot.carry_spin_adjusted > 0


def test_ballistics_without_substitutes_needs_measured_spin_axis_and_both_angles():
    measured = _shot(
        launch_angle_vertical=12.0,
        launch_angle_horizontal=1.0,
        spin_rpm=2700,
        spin_confidence=0.85,
        spin_axis_deg=2.0,
    )

    assert resolve_launch(measured, substitutes=False).spin_source == "measured"
    measured.spin_axis_deg = None
    assert resolve_launch(measured, substitutes=False) is None
    assert (
        resolve_launch(
            _shot(
                launch_angle_vertical=12.0,
                launch_angle_horizontal=1.0,
                spin_rpm=2700,
                spin_confidence=0.3,
            ),
            substitutes=False,
        )
        is None
    )
    assert (
        resolve_launch(
            _shot(launch_angle_vertical=12.0, spin_rpm=2700, spin_confidence=0.85),
            substitutes=False,
        )
        is None
    )


# ---- a calculated spin is labelled calculated ------------------------------------------


def _calculated():
    shot = _shot(
        launch_angle_vertical=12.0,
        launch_angle_vertical_source="radar",
        spin_rpm=900,
        spin_confidence=0.2,
    )
    assert server_module._apply_calculated_spin(shot)  # pylint: disable=protected-access
    return shot


def test_a_calculated_spin_is_never_labelled_high_confidence():
    shot = _calculated()

    assert shot.spin_source == "calculated"
    assert shot.spin_confidence is None or shot.spin_confidence < SPIN_CONFIDENCE_HIGH
    assert shot.spin_rpm_measured == 900


def test_carry_and_simulators_use_a_calculated_spin_under_its_own_label():
    shot = _calculated()

    assert resolve_launch(shot).spin_source == "calculated"
    resolved = resolve_shot(shot, PlayerState())
    # a model's spin from measured inputs is calculated, never measured (D31, E2)
    assert resolved.provenance["total_spin"] == "calculated"
    assert resolved.total_spin_rpm == pytest.approx(shot.spin_rpm)


# ---- carry is committed once, in finalization (upstream #276) ---------------------------
#
# The rolling buffer monitor used to pre-fill carry_spin_adjusted with the ball-speed
# spin table whenever OPS spin was reliable, and finalization only ran the simulator
# while that field was still None. On a fusion unit a measured launch never reached the
# simulator, and a shot it could not simulate kept the table number (D17).


def _monitor_shot_with_reliable_spin():
    """A shot as the rolling buffer monitor builds it, with clean, reliable OPS spin."""
    from openflight.rolling_buffer import RollingBufferMonitor
    from openflight.rolling_buffer.types import (
        IQCapture,
        ProcessedCapture,
        SpeedTimeline,
        SpinResult,
    )

    monitor = RollingBufferMonitor(port=None, trigger_type="sound")
    monitor.set_club(ClubType.DRIVER)
    processed = ProcessedCapture(
        timeline=SpeedTimeline(readings=[], sample_rate_hz=937.5),
        ball_speed_mph=150.0,
        ball_timestamp_ms=60.0,
        club_speed_mph=105.0,
        spin=SpinResult(
            spin_rpm=2700,
            confidence=0.8,
            snr=12.0,
            quality="high",
            peak_freq_hz=45.0,
            seam_cycles=4.0,
        ),
        capture=IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=[2048] * 4096,
            q_samples=[2048] * 4096,
            first_byte_timestamp=12345.746,
            trigger_timestamp=12345.678,
        ),
    )
    assert processed.has_spin
    shot = monitor._create_shot(processed)  # pylint: disable=protected-access
    assert shot is not None and shot.spin_rpm == 2700
    return shot


def _spin_table_carry(shot):
    from openflight.rolling_buffer.monitor import estimate_carry_with_spin

    return estimate_carry_with_spin(
        shot.ball_speed_mph, shot.spin_rpm, shot.club, club_speed_mph=shot.club_speed_mph
    )


def _measure_launch(shot, *, horizontal=True, spin_axis=True):
    shot.launch_angle_vertical = 12.0
    shot.launch_angle_vertical_source = "radar"
    shot.launch_angle_confidence = 0.9
    if horizontal:
        shot.launch_angle_horizontal = 1.0
        shot.launch_angle_horizontal_source = "radar"
    if spin_axis:
        shot.spin_axis_deg = 2.0


def _ballistic_carry(shot, *, substitutes):
    from openflight.ballistics import simulate

    conditions = resolve_launch(shot, substitutes=substitutes)
    assert conditions is not None
    return simulate(conditions).carry_yards


def test_the_monitor_leaves_carry_to_finalization():
    shot = _monitor_shot_with_reliable_spin()

    assert shot.carry_spin_adjusted is None


def test_a_fusion_unit_with_reliable_spin_and_a_measured_launch_gets_ballistic_carry(
    fusion_unit, quiet_finalize
):
    shot = _monitor_shot_with_reliable_spin()
    _measure_launch(shot)
    expected = _ballistic_carry(shot, substitutes=False)
    table = _spin_table_carry(shot)
    assert expected != pytest.approx(table, abs=0.5)  # the two must be told apart

    _finalize(shot)

    assert shot.carry_spin_adjusted == pytest.approx(expected)
    assert shot.carry_spin_adjusted != pytest.approx(table, abs=0.5)


@pytest.mark.parametrize(
    "missing",
    [
        {"launch": False},
        {"launch": True, "horizontal": False},
        {"launch": True, "spin_axis": False},
    ],
    ids=["no-launch", "no-horizontal", "no-spin-axis"],
)
def test_a_fusion_unit_with_reliable_spin_but_unmeasured_inputs_gives_no_carry(
    fusion_unit, quiet_finalize, missing
):
    shot = _monitor_shot_with_reliable_spin()
    if missing.pop("launch"):
        _measure_launch(shot, **missing)

    _finalize(shot)

    assert shot.carry_spin_adjusted is None


def test_a_fusion_unit_with_ballistics_disabled_gives_no_carry(
    fusion_unit, quiet_finalize, monkeypatch
):
    monkeypatch.setattr(server_module, "ballistics_enabled", False)
    shot = _monitor_shot_with_reliable_spin()
    _measure_launch(shot)

    _finalize(shot)

    assert shot.carry_spin_adjusted is None


def test_finalization_overwrites_a_carry_already_on_a_fusion_shot(fusion_unit, quiet_finalize):
    shot = _monitor_shot_with_reliable_spin()
    shot.carry_spin_adjusted = _spin_table_carry(shot)  # a stale pre-fill

    _finalize(shot)

    assert shot.carry_spin_adjusted is None


def test_an_ops_only_install_with_reliable_spin_gets_ballistic_carry_on_its_estimated_launch(
    ops_only, quiet_finalize
):
    shot = _monitor_shot_with_reliable_spin()

    _finalize(shot)

    assert shot.launch_angle_vertical_source == "estimated"
    assert shot.carry_spin_adjusted == pytest.approx(_ballistic_carry(shot, substitutes=True))


def test_an_ops_only_install_with_ballistics_disabled_keeps_the_spin_table(
    ops_only, quiet_finalize, monkeypatch
):
    monkeypatch.setattr(server_module, "ballistics_enabled", False)
    shot = _monitor_shot_with_reliable_spin()

    _finalize(shot)

    assert shot.carry_spin_adjusted == pytest.approx(_spin_table_carry(shot))


# ---- "not measured" everywhere: payload, stats, simulators, logs -----------------------
#
# The kiosk used to show (and forward, and average) the ball-speed table
# ``estimated_carry_yards`` whenever ``carry_spin_adjusted`` was None. On a fusion unit
# that is a fill-in: an unmeasured carry is "not measured" everywhere. The shot says so
# in ``carry_status`` so no consumer has to guess.


def _payload(shot):
    return server_module.shot_to_dict(shot)


def test_a_fusion_payload_with_no_carry_says_not_measured_and_carries_no_table(
    fusion_unit, quiet_finalize
):
    shot = _monitor_shot_with_reliable_spin()

    _finalize(shot)
    payload = _payload(shot)

    assert payload["carry_status"] == "not_measured"
    assert payload["carry_spin_adjusted"] is None
    assert payload["estimated_carry_yards"] is None
    assert payload["carry_range"] is None
    assert shot.reported_carry_yards is None


def test_a_fusion_payload_with_a_simulated_carry_says_measured_and_carries_no_table(
    fusion_unit, quiet_finalize
):
    shot = _monitor_shot_with_reliable_spin()
    _measure_launch(shot)

    _finalize(shot)
    payload = _payload(shot)

    assert payload["carry_status"] == "measured"
    assert payload["carry_spin_adjusted"] == pytest.approx(
        _ballistic_carry(shot, substitutes=False), abs=0.5
    )
    assert payload["estimated_carry_yards"] is None
    assert payload["carry_range"] is None
    assert shot.reported_carry_yards == pytest.approx(shot.carry_spin_adjusted)


def test_a_fusion_carry_from_a_calculated_spin_says_calculated(fusion_unit, quiet_finalize):
    shot = _shot(
        launch_angle_vertical=12.0,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=1.0,
        launch_angle_horizontal_source="radar",
        spin_axis_deg=2.0,
        spin_rpm=2600,
        spin_source="calculated",
        mode="rolling-buffer",
    )

    _finalize(shot)

    assert shot.carry_status == "calculated"
    assert shot.carry_spin_adjusted is not None


@pytest.mark.parametrize("ballistics", [True, False], ids=["ballistic", "table"])
def test_an_ops_only_payload_says_estimated_and_keeps_its_table(
    ops_only, quiet_finalize, monkeypatch, ballistics
):
    monkeypatch.setattr(server_module, "ballistics_enabled", ballistics)
    shot = _monitor_shot_with_reliable_spin()

    _finalize(shot)
    payload = _payload(shot)

    assert payload["carry_status"] == "estimated"
    assert payload["carry_spin_adjusted"] is not None
    assert payload["estimated_carry_yards"] == round(shot.estimated_carry_yards)
    assert payload["carry_range"] is not None
    assert shot.reported_carry_yards == pytest.approx(shot.carry_spin_adjusted)


def test_an_unfinalized_shot_keeps_its_labelled_table_carry():
    """Shots that never reached finalization (replays, mock) keep the old fallback."""
    shot = _shot()

    assert shot.carry_status is None
    assert shot.reported_carry_yards == pytest.approx(shot.estimated_carry_yards)
    assert _payload(shot)["estimated_carry_yards"] == round(shot.estimated_carry_yards)


def test_a_carry_status_not_measured_wins_over_a_stray_carry():
    shot = _shot(carry_spin_adjusted=250.0, carry_status="not_measured")

    assert shot.reported_carry_yards is None


def _await_finalization():
    from tests.test_server import _wait_for_shot_finalization_idle

    _wait_for_shot_finalization_idle()


def test_a_fusion_unit_says_carry_not_measured_from_first_emit_to_session_log(
    monkeypatch, quiet_finalize
):
    """The initial OPS emit (before enrichment) must not show a table carry either."""
    emitted, logged, workers = [], [], []
    monkeypatch.setattr(server_module, "iwr6843_runtime", None)
    monkeypatch.setattr(server_module, "camera_capture_runtime", object())
    monkeypatch.setattr(server_module, "monitor", None)
    monkeypatch.setattr(server_module, "sim_connectors", [])
    monkeypatch.setattr(server_module, "ball_speed_correction_enabled", False)
    monkeypatch.setattr(server_module, "shot_enrichment_task", None)
    monkeypatch.setattr(
        server_module,
        "shot_enrichment_queue",
        server_module.queue.Queue(maxsize=server_module._SHOT_ENRICHMENT_QUEUE_CAPACITY),  # pylint: disable=protected-access
    )
    monkeypatch.setattr(
        server_module.socketio, "emit", lambda event, payload: emitted.append((event, payload))
    )
    monkeypatch.setattr(
        server_module.socketio,
        "start_background_task",
        lambda target, *_a, **_k: workers.append(target) or object(),
    )
    monkeypatch.setattr(
        server_module,
        "_enrich_shot_from_optional_hardware",
        lambda _shot: server_module._ShotEnrichmentResult(),  # pylint: disable=protected-access
    )
    monkeypatch.setattr(
        server_module,
        "get_session_logger",
        lambda: SimpleNamespace(
            log_shot=lambda shot, pipeline_ms=None, **_k: logged.append(shot.to_dict()) or True,
            log_fusion_diagnostic=lambda *_a, **_k: None,
        ),
    )
    server_module._reset_shot_sequence()  # pylint: disable=protected-access
    shot = _monitor_shot_with_reliable_spin()

    server_module.on_shot_detected(shot)
    workers[0]()
    _await_finalization()

    shot_payloads = [p["shot"] for e, p in emitted if e in ("shot", "shot_update") and "shot" in p]
    assert [e for e, _p in emitted if e in ("shot", "shot_update")] == ["shot", "shot_update"]
    for payload in shot_payloads + logged:
        assert payload["carry_status"] == "not_measured"
        assert payload["carry_spin_adjusted"] is None
        assert payload["estimated_carry_yards"] is None


def test_session_stats_average_only_reported_carries():
    from openflight.launch_monitor import summarize_shots

    measured = _shot(carry_spin_adjusted=240.0, carry_status="measured")
    not_measured = _shot(carry_status="not_measured")

    assert summarize_shots([measured, not_measured], "rolling-buffer")[
        "avg_carry_est"
    ] == pytest.approx(240.0)
    assert summarize_shots([not_measured], "rolling-buffer")["avg_carry_est"] is None


def test_the_carry_log_line_names_a_fusion_carry_not_measured_without_the_table():
    shot = _shot(carry_status="not_measured")

    text = server_module._carry_log_text(shot, None)  # pylint: disable=protected-access

    assert text == "carry not measured"


# ---- simulator connectors ---------------------------------------------------------------


def test_the_resolver_sends_no_carry_for_a_fusion_shot_without_one():
    shot = _shot(
        launch_angle_vertical=12.0,
        launch_angle_horizontal=1.0,
        spin_rpm=2700,
        spin_confidence=0.85,
        spin_axis_deg=2.0,
        carry_status="not_measured",
    )

    resolved = resolve_shot(shot, PlayerState())

    assert resolved.carry_yards is None
    assert "carry" not in resolved.provenance
    assert "carry" not in resolved.as_values()


def test_the_resolver_sends_a_fusion_shot_its_simulated_carry():
    shot = _shot(launch_angle_vertical=12.0, carry_spin_adjusted=243.8, carry_status="measured")

    resolved = resolve_shot(shot, PlayerState())

    assert resolved.carry_yards == pytest.approx(243.8)
    assert resolved.provenance["carry"] == "measured"


@pytest.mark.parametrize("target", ["gspro", "opengolfsim", "partee"])
def test_every_connector_omits_a_missing_carry_instead_of_sending_zero(target):
    import json

    from openflight.sim.codec import _codec_for  # pylint: disable=protected-access
    from openflight.sim.config import ConnectorConfig

    shot = _shot(
        launch_angle_vertical=12.0,
        launch_angle_horizontal=1.0,
        spin_rpm=2700,
        spin_confidence=0.85,
        spin_axis_deg=2.0,
        carry_status="not_measured",
    )
    codec = _codec_for(ConnectorConfig(type=target, host="127.0.0.1", port=921))

    wire = json.loads(codec.build_shot(resolve_shot(shot, PlayerState())))

    assert "CarryDistance" not in wire["BallData"]
    assert wire["BallData"]["Speed"] == pytest.approx(150.0)
    assert wire["BallData"]["VLA"] == pytest.approx(12.0)
    assert wire["BallData"]["TotalSpin"] == pytest.approx(2700)


def test_the_server_forwards_a_fusion_shot_without_carry_to_connected_simulators(monkeypatch):
    sent, emitted = [], []

    class Connector:
        name = "gspro"

        def __init__(self):
            from openflight.gspro.codec import GSProCodec

            self.codec = GSProCodec()

        @staticmethod
        def is_connected():
            return True

        def send_shot(self, resolved):
            sent.append(self.codec.build_shot(resolved))

    monkeypatch.setattr(server_module, "sim_connectors", [Connector()])
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(
        server_module.socketio, "emit", lambda event, payload: emitted.append((event, payload))
    )
    shot = _shot(
        launch_angle_vertical=12.0,
        launch_angle_horizontal=1.0,
        spin_rpm=2700,
        spin_confidence=0.85,
        spin_axis_deg=2.0,
        carry_status="not_measured",
    )

    server_module._forward_shot_to_simulators(shot)  # pylint: disable=protected-access

    assert len(sent) == 1 and b"CarryDistance" not in sent[0]
    sim_shot = next(payload for event, payload in emitted if event == "sim_shot")
    assert "carry" not in sim_shot["values"]
    assert "carry" not in sim_shot["provenance"]


# ---- the monitor's table carry at detection is a labelled diagnostic -------------------


def test_the_session_log_names_the_trigger_table_carry_a_diagnostic(tmp_path):
    import json

    from openflight.session_logger import SessionLogger

    session = SessionLogger(log_dir=tmp_path, enabled=True)
    session.start_session(mode="rolling-buffer", trigger_type="sound")

    session.log_trigger_event(
        trigger_type="sound", accepted=True, reason="accepted", table_carry_estimate_yards=257.0
    )

    entry = json.loads(session.session_path.read_text().strip().split("\n")[-1])
    assert entry["type"] == "trigger_event"
    assert "carry_yards" not in entry
    assert entry["table_carry_estimate_yards"] == 257.0


# ---- D31: a fusion unit sends simulators only what it measured -------------------------
#
# OpenConnect V1 (GSPro, OpenGolfSim, PAR-TEE) requires BallData Speed, VLA, HLA,
# TotalSpin and SpinAxis (BackSpin/SideSpin only without TotalSpin); CarryDistance and
# every ClubData field are optional. On a fusion unit a required field the shot did not
# measure means the shot is not sent, and the screen and the sim-send log say why; an
# optional one is left off the wire. An OPS-only unit keeps its labelled estimates.


def _fusion_sim_shot(**overrides):
    values = {
        "launch_angle_vertical": 12.0,
        "launch_angle_horizontal": 1.0,
        "spin_rpm": 2700,
        "spin_confidence": 0.85,
        "spin_axis_deg": 2.0,
        "club_path_deg": -1.5,
        "carry_spin_adjusted": 243.8,
        "carry_status": "measured",
    }
    values.update(overrides)
    return _shot(**values)


@pytest.mark.parametrize(
    "overrides, missing, reason",
    [
        ({"spin_confidence": 0.3}, ["spin"], "spin not measured"),
        ({"spin_rpm": None, "spin_confidence": None}, ["spin"], "spin not measured"),
        ({"launch_angle_vertical": None}, ["vertical launch"], "vertical launch not measured"),
        (
            {"launch_angle_horizontal": None},
            ["horizontal launch"],
            "horizontal launch not measured",
        ),
        ({"spin_axis_deg": None}, ["spin axis"], "spin axis not measured"),
        (
            {"launch_angle_vertical": None, "spin_confidence": 0.3, "spin_axis_deg": None},
            ["vertical launch", "spin", "spin axis"],
            "vertical launch, spin and spin axis not measured",
        ),
    ],
)
def test_a_fusion_shot_missing_a_required_field_is_not_sent(overrides, missing, reason):
    from openflight.sim.types import UnmeasuredShotError

    state = PlayerState()

    with pytest.raises(UnmeasuredShotError) as raised:
        resolve_shot(_fusion_sim_shot(**overrides), state, substitutes=False)

    assert raised.value.missing == missing
    assert str(raised.value) == reason
    assert state.shot_counter == 0  # no shot number spent on a shot that was not sent


def test_a_fusion_shot_with_a_calculated_spin_is_sent_under_its_label():
    shot = _fusion_sim_shot(spin_rpm=2600, spin_confidence=None, spin_source="calculated")

    resolved = resolve_shot(shot, PlayerState(), substitutes=False)

    assert resolved.total_spin_rpm == pytest.approx(2600)
    assert resolved.provenance["total_spin"] == "calculated"


def test_a_fusion_shot_missing_only_optional_fields_is_sent_without_them():
    import json

    from openflight.gspro.codec import GSProCodec

    shot = _fusion_sim_shot(
        club_speed_mph=None,
        club_path_deg=None,
        carry_spin_adjusted=None,
        carry_status="not_measured",
    )

    resolved = resolve_shot(shot, PlayerState(), substitutes=False)
    wire = json.loads(GSProCodec().build_shot(resolved))

    assert resolved.club_path_deg is None and resolved.club_speed_mph is None
    for field in ("carry", "club_path", "club_speed"):
        assert field not in resolved.provenance
        assert field not in resolved.as_values()
    assert "CarryDistance" not in wire["BallData"]
    assert "Path" not in wire["ClubData"]
    assert wire["ShotDataOptions"]["ContainsClubData"] is False
    for key in ("Speed", "VLA", "HLA", "TotalSpin", "SpinAxis"):
        assert key in wire["BallData"]


def test_a_fully_measured_fusion_shot_is_sent_unchanged():
    from openflight.gspro.codec import GSProCodec

    shot = _fusion_sim_shot()

    fusion = resolve_shot(shot, PlayerState(), substitutes=False)
    ops = resolve_shot(shot, PlayerState())

    assert fusion == ops
    assert set(fusion.provenance.values()) == {"measured"}
    assert GSProCodec().build_shot(fusion) == GSProCodec().build_shot(ops)


def test_an_ops_only_shot_keeps_its_labelled_simulator_estimates():
    resolved = resolve_shot(_shot(), PlayerState())

    assert resolved.vla > 0 and resolved.hla == 0.0 and resolved.spin_axis_deg == 0.0
    assert resolved.total_spin_rpm > 0 and resolved.club_path_deg == 0.0
    for field in ("vla", "hla", "total_spin", "spin_axis", "club_path"):
        assert resolved.provenance[field] == "estimated"


class _Connector:
    """A connected OpenConnect simulator that records what it was sent."""

    name = "gspro"

    def __init__(self, sent):
        from openflight.gspro.codec import GSProCodec

        self.codec = GSProCodec()
        self._sent = sent

    @staticmethod
    def is_connected():
        return True

    def send_shot(self, resolved):
        self._sent.append(self.codec.build_shot(resolved))


def _forward(monkeypatch, shot, *, fusion):
    sent, emitted = [], []
    session_log = SimpleNamespace(not_sent=[], sends=[])
    session_log.log_sim_not_sent = lambda **kw: session_log.not_sent.append(kw)
    session_log.log_sim_send = lambda **kw: session_log.sends.append(kw)
    monkeypatch.setattr(server_module, "iwr6843_runtime", SimpleNamespace() if fusion else None)
    monkeypatch.setattr(server_module, "camera_capture_runtime", None)
    monkeypatch.setattr(server_module, "sim_connectors", [_Connector(sent)])
    monkeypatch.setattr(server_module, "sim_player_state", PlayerState())
    monkeypatch.setattr(server_module, "get_session_logger", lambda: session_log)
    monkeypatch.setattr(
        server_module.socketio, "emit", lambda event, payload: emitted.append((event, payload))
    )
    server_module._forward_shot_to_simulators(shot)  # pylint: disable=protected-access
    return sent, emitted, session_log


def test_the_server_does_not_send_a_fusion_shot_without_measured_spin_and_says_why(monkeypatch):
    shot = _fusion_sim_shot(spin_confidence=0.3)

    sent, emitted, session_log = _forward(monkeypatch, shot, fusion=True)

    assert sent == []
    assert emitted == [
        (
            "sim_shot_dropped",
            {
                "reason": "spin not measured",
                "missing": ["spin"],
                "shot_timestamp": shot.timestamp.isoformat(),
                "targets": ["gspro"],
            },
        )
    ]
    assert session_log.not_sent == [
        {
            "targets": ["gspro"],
            "reason": "spin not measured",
            "missing": ["spin"],
            "shot_timestamp": shot.timestamp.isoformat(),
        }
    ]
    assert session_log.sends == []


def test_the_server_sends_the_same_shot_from_an_ops_only_unit_with_estimates(monkeypatch):
    sent, emitted, session_log = _forward(
        monkeypatch, _fusion_sim_shot(spin_confidence=0.3), fusion=False
    )

    assert len(sent) == 1
    assert [event for event, _payload in emitted] == ["sim_shot"]
    assert session_log.not_sent == [] and len(session_log.sends) == 1


def test_the_server_sends_a_fully_measured_fusion_shot(monkeypatch):
    sent, emitted, _session_log = _forward(monkeypatch, _fusion_sim_shot(), fusion=True)

    assert len(sent) == 1
    assert [event for event, _payload in emitted] == ["sim_shot"]


def test_the_sim_send_log_records_a_shot_not_sent_with_its_reason(tmp_path):
    import json

    from openflight.session_logger import SessionLogger

    session = SessionLogger(log_dir=tmp_path, enabled=True)
    session.start_session(mode="rolling-buffer", trigger_type="sound")

    session.log_sim_send(target="gspro", shot_number=3, provenance={"ball_speed": "measured"})
    session.log_sim_not_sent(
        targets=["gspro"], reason="spin not measured", missing=["spin"], shot_timestamp="t"
    )

    entries = [json.loads(line) for line in session.session_path.read_text().splitlines()]
    sends = [entry for entry in entries if entry["type"] == "sim_send"]
    assert sends[0]["sent"] is True
    assert sends[1] == {
        "ts": sends[1]["ts"],
        "type": "sim_send",
        "sent": False,
        "targets": ["gspro"],
        "reason": "spin not measured",
        "missing": ["spin"],
        "shot_timestamp": "t",
    }


# --- Merge of D31 with the base's rule: a non-finite value is a missing value --------
# On a fusion unit a NaN or infinite required field is unmeasured exactly like None:
# the shot is not sent, and the screen and the sim-send log say which field. A
# non-finite optional field is left out, and a non-finite carry is never reported.

_NON_FINITE = [float("nan"), float("inf"), float("-inf")]
_NON_FINITE_IDS = ["nan", "inf", "-inf"]


@pytest.mark.parametrize("bad", _NON_FINITE, ids=_NON_FINITE_IDS)
@pytest.mark.parametrize(
    "field, missing",
    [
        ("launch_angle_vertical", "vertical launch"),
        ("launch_angle_horizontal", "horizontal launch"),
        ("spin_rpm", "spin"),
        ("spin_confidence", "spin"),
        ("spin_axis_deg", "spin axis"),
    ],
)
def test_a_fusion_shot_with_a_non_finite_required_field_is_not_sent(field, missing, bad):
    from openflight.sim.types import UnmeasuredShotError

    state = PlayerState()

    with pytest.raises(UnmeasuredShotError) as raised:
        resolve_shot(_fusion_sim_shot(**{field: bad}), state, substitutes=False)

    assert raised.value.missing == [missing]
    assert str(raised.value) == f"{missing} not measured"
    assert state.shot_counter == 0


@pytest.mark.parametrize("bad", _NON_FINITE, ids=_NON_FINITE_IDS)
def test_a_fusion_shot_with_non_finite_optional_fields_is_sent_without_them(bad):
    shot = _fusion_sim_shot(club_speed_mph=bad, club_path_deg=bad)

    resolved = resolve_shot(shot, PlayerState(), substitutes=False)

    assert resolved.club_speed_mph is None and resolved.club_path_deg is None
    for field in ("club_speed", "club_path"):
        assert field not in resolved.provenance
        assert field not in resolved.as_values()


@pytest.mark.parametrize("bad", _NON_FINITE, ids=_NON_FINITE_IDS)
def test_a_fusion_shot_with_a_non_finite_carry_is_sent_without_carry(bad):
    shot = _fusion_sim_shot(carry_spin_adjusted=bad, carry_status="measured")

    resolved = resolve_shot(shot, PlayerState(), substitutes=False)

    assert resolved.carry_yards is None
    assert "carry" not in resolved.provenance and "carry" not in resolved.as_values()


def test_the_server_does_not_send_a_fusion_shot_with_a_nan_spin_and_says_why(monkeypatch):
    shot = _fusion_sim_shot(spin_rpm=float("nan"))

    sent, emitted, session_log = _forward(monkeypatch, shot, fusion=True)

    assert sent == []
    dropped = {
        "reason": "spin not measured",
        "missing": ["spin"],
        "shot_timestamp": shot.timestamp.isoformat(),
        "targets": ["gspro"],
    }
    assert emitted == [("sim_shot_dropped", dropped)]
    assert session_log.not_sent == [dropped]
    assert session_log.sends == []


def test_an_ops_only_unit_sends_a_nan_spin_shot_with_its_labelled_spin_estimate(monkeypatch):
    sent, emitted, _session_log = _forward(
        monkeypatch, _fusion_sim_shot(spin_rpm=float("nan")), fusion=False
    )

    assert len(sent) == 1
    assert [event for event, _payload in emitted] == ["sim_shot"]
    assert emitted[0][1]["provenance"]["total_spin"] == "estimated"


@pytest.mark.parametrize("bad", _NON_FINITE, ids=_NON_FINITE_IDS)
def test_a_non_finite_fusion_carry_is_not_reported_anywhere(bad):
    shot = _fusion_sim_shot(carry_spin_adjusted=bad, carry_status="measured")

    assert shot.reported_carry_yards is None
    payload = server_module.shot_to_dict(shot)
    assert payload["carry_spin_adjusted"] is None
    assert payload["estimated_carry_yards"] is None  # no table on a fusion unit (D17)
    assert payload["carry_status"] is None  # a nulled carry keeps no "measured" label
    assert {"id": "carry_spin_adjusted", "reason": f"non_finite_value:{bad!r}"} in payload[
        "metrics_not_measured"
    ]
    carry_log = server_module._carry_log_text(shot, None)  # pylint: disable=protected-access
    assert carry_log == "carry not measured"


def test_a_non_finite_ops_only_carry_falls_back_to_its_labelled_table():
    shot = _shot(carry_spin_adjusted=float("nan"), carry_status="estimated")

    assert shot.reported_carry_yards == pytest.approx(shot.estimated_carry_yards)
    assert server_module._carry_log_text(shot, None) == (  # pylint: disable=protected-access
        f"carry shown={shot.estimated_carry_yards:.0f} yds (table estimate)"
    )


def test_session_stats_average_only_finite_reported_carries():
    from openflight.launch_monitor import summarize_shots

    shots = [
        _fusion_sim_shot(carry_spin_adjusted=240.0),
        _fusion_sim_shot(carry_spin_adjusted=float("nan")),
        _fusion_sim_shot(carry_spin_adjusted=None, carry_status="not_measured"),
    ]

    assert summarize_shots(shots, "rolling-buffer")["avg_carry_est"] == pytest.approx(240.0)
