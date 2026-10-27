"""A missing value stays missing on every install (D17; owner decision 1, 25 Oct 2026).

The kiosk used to fill a missing vertical launch from club tables, a 0 deg horizontal
launch and spin axis, a club-typical spin and a ball-speed table carry, first on every
unit and later on OPS-only installs only (``_fusion_unit``). None of that is filled in
now, on any install: a metric with no real route is not measured, with the reason, and
carry is built only from the shot's own inputs. A calculated spin is a model's output:
labelled calculated, never high-confidence.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import server as server_module, shot_models
from openflight.clubs import ClubType
from openflight.clubs.physics import CLUB_TYPICAL_SPIN_RPM
from openflight.flight import FLIGHT_METRICS
from openflight.flight_sim import resolve_launch
from openflight.launch_monitor import SPIN_CONFIDENCE_HIGH, Shot
from openflight.sim.resolver import resolve_shot
from openflight.sim.types import PlayerState, UnmeasuredShotError


def _shot(**overrides):
    values = {
        "ball_speed_mph": 150.0,
        "club_speed_mph": 105.0,
        "timestamp": datetime.now(),
        "club": ClubType.DRIVER,
    }
    values.update(overrides)
    if values.get("spin_axis_deg") is not None:
        values.setdefault("spin_axis_source", "calculated (D-plane tilt)")
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
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)


@pytest.fixture
def full_ball_speed(monkeypatch):
    """The full speed stands in as the ball speed: a flight on the OPS line-of-sight speed is
    "estimated"."""

    def full_speed(shot):
        shot.ball_speed_full = {"status": "calculated", "value_mph": shot.ball_speed_mph}
        shot.ball_speed_full_mph = shot.ball_speed_mph

    monkeypatch.setattr(shot_models, "_attach_full_speeds", full_speed)


def _finalize(shot):
    server_module._finalize_shot_detected(shot, emit_event="shot")  # pylint: disable=protected-access


@pytest.mark.parametrize("install", ["fusion_unit", "ops_only"])
def test_no_install_fills_in_a_missing_launch(install, request, quiet_finalize):
    request.getfixturevalue(install)
    shot = _shot()

    _finalize(shot)

    assert shot.launch_angle_vertical is None
    assert shot.launch_angle_vertical_source is None
    assert shot.launch_angle_horizontal is None
    assert shot.launch_angle_horizontal_source is None
    assert shot.angle_source is None


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


def test_an_ops_only_install_has_no_table_carry(ops_only, quiet_finalize):
    shot = _shot(spin_rpm=2700, spin_confidence=0.3)

    _finalize(shot)

    assert shot.carry_spin_adjusted is None
    assert shot.carry_status == "not_measured"
    assert shot.reported_carry_yards is None


def test_the_flight_model_needs_a_measured_spin_axis_and_both_angles():
    measured = _shot(
        launch_angle_vertical=12.0,
        launch_angle_horizontal=1.0,
        spin_rpm=2700,
        spin_confidence=0.85,
        spin_axis_deg=2.0,
    )

    assert resolve_launch(measured).spin_source == "measured"
    measured.spin_axis_deg = None
    assert resolve_launch(measured) is None
    weak_spin = _shot(
        launch_angle_vertical=12.0,
        launch_angle_horizontal=1.0,
        spin_rpm=2700,
        spin_confidence=0.3,
    )
    assert resolve_launch(weak_spin) is None
    no_horizontal = _shot(launch_angle_vertical=12.0, spin_rpm=2700, spin_confidence=0.85)
    assert resolve_launch(no_horizontal) is None


def test_resolve_launch_has_no_substitutes_switch():
    import inspect

    assert "substitutes" not in inspect.signature(resolve_launch).parameters


def test_the_legacy_ballistics_launch_fills_nothing_in():
    from openflight.ballistics import resolve_launch as legacy_resolve_launch

    weak_spin = _shot(
        launch_angle_vertical=12.0, launch_angle_horizontal=1.0, spin_axis_deg=2.0, spin_rpm=2700
    )
    no_axis = _shot(
        launch_angle_vertical=12.0, launch_angle_horizontal=1.0, spin_rpm=2700, spin_confidence=0.9
    )
    no_horizontal = _shot(
        launch_angle_vertical=12.0, spin_axis_deg=2.0, spin_rpm=2700, spin_confidence=0.9
    )

    for shot in (weak_spin, no_axis, no_horizontal):
        assert legacy_resolve_launch(shot) is None


def _calculated():
    """A shot whose spin is a model's calculated one (the collision model's, say)."""
    return _shot(
        launch_angle_vertical=12.0,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=1.0,
        launch_angle_horizontal_source="radar",
        spin_axis_deg=2.0,
        spin_rpm=2600,
        spin_source="calculated",
        spin_confidence=None,
        inclinometer={"applied_to": ["iwr6843"]},
    )


def test_a_calculated_spin_is_never_labelled_high_confidence():
    shot = _calculated()

    assert shot.spin_source == "calculated"
    assert shot.spin_confidence is None or shot.spin_confidence < SPIN_CONFIDENCE_HIGH


def test_carry_and_simulators_use_a_calculated_spin_under_its_own_label():
    shot = _calculated()

    assert resolve_launch(shot).spin_source == "calculated"
    resolved = resolve_shot(shot, PlayerState())
    # a model's spin from measured inputs is calculated, never measured
    assert resolved.provenance["total_spin"] == "calculated"
    assert resolved.total_spin_rpm == pytest.approx(shot.spin_rpm)


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


def _ballistic_carry(shot):
    from openflight.flight_sim import simulate

    conditions = resolve_launch(shot)
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
    expected = _ballistic_carry(shot)
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


@pytest.mark.parametrize("ballistics", [True, False], ids=["ballistic", "disabled"])
def test_an_ops_only_install_with_reliable_spin_has_no_carry_without_a_launch(
    ops_only, quiet_finalize, monkeypatch, ballistics
):
    monkeypatch.setattr(server_module, "ballistics_enabled", ballistics)
    shot = _monitor_shot_with_reliable_spin()

    _finalize(shot)

    assert shot.launch_angle_vertical is None
    assert shot.carry_spin_adjusted is None
    assert shot.carry_status == "not_measured"


def test_an_ops_only_install_flies_a_shot_whose_inputs_were_measured(ops_only, quiet_finalize):
    """Same shot, same carry, whatever the install: the rule is per shot."""
    shot = _monitor_shot_with_reliable_spin()
    _measure_launch(shot)
    expected = _ballistic_carry(shot)

    _finalize(shot)

    assert shot.carry_spin_adjusted == pytest.approx(expected)


# an unmeasured carry is "not measured" everywhere, and ``carry_status`` says so


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


def test_a_fusion_payload_with_a_simulated_carry_says_calculated_and_carries_no_table(
    fusion_unit, quiet_finalize, full_ball_speed
):
    shot = _monitor_shot_with_reliable_spin()
    _measure_launch(shot)

    _finalize(shot)
    payload = _payload(shot)

    assert payload["carry_status"] == "calculated"
    assert payload["carry_status"] == payload["flight"]["as_measured"]["metrics"]["carry"]["status"]
    assert payload["carry_spin_adjusted"] == pytest.approx(_ballistic_carry(shot), abs=0.5)
    assert payload["estimated_carry_yards"] is None
    assert payload["carry_range"] is None
    assert shot.reported_carry_yards == pytest.approx(shot.carry_spin_adjusted)


def test_a_fusion_carry_from_a_calculated_spin_says_calculated(
    fusion_unit, quiet_finalize, full_ball_speed
):
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


_NO_FILL_IN_METRICS = ("launch_vertical", "launch_horizontal", "spin_axis", *FLIGHT_METRICS)


@pytest.mark.parametrize("spin_confidence", [0.8, 0.3, None], ids=["reliable", "weak", "none"])
def test_an_ops_only_shot_has_no_fabricated_value_anywhere(
    ops_only, quiet_finalize, spin_confidence
):
    """No 0.0 side, curve or axis, no typical spin, no table launch or carry (decision 1)."""
    shot = _monitor_shot_with_reliable_spin()
    shot.club = ClubType.IRON_7  # its typical spin is not the OPS's 2700 rpm
    if spin_confidence is None:
        shot.spin_rpm = shot.spin_confidence = None
    else:
        shot.spin_confidence = spin_confidence

    _finalize(shot)
    payload = _payload(shot)
    metrics = payload["metrics"]

    for name in _NO_FILL_IN_METRICS:
        record = metrics[name]
        assert record["value"] is None, name
        assert record["status"] == "not_measured", name
        assert record["reason"], name
    assert shot.spin_axis_deg is None and shot.spin_axis_source is None
    assert payload["carry_status"] == "not_measured"
    assert payload["carry_spin_adjusted"] is None
    assert payload["estimated_carry_yards"] is None
    assert payload["carry_range"] is None
    flight = payload["flight"]["as_measured"]
    assert flight["launch"] is None
    assert all(flight["metrics"][name]["value"] is None for name in FLIGHT_METRICS)
    # the spin is the OPS's own reading or nothing; never the club-typical one
    typical = CLUB_TYPICAL_SPIN_RPM[shot.club]
    if spin_confidence is None:
        assert metrics["spin_rate"]["value"] is None
    else:
        assert metrics["spin_rate"]["value"] == pytest.approx(2700)
        assert metrics["spin_rate"]["value"] != pytest.approx(typical)
    # what the OPS measured stays measured: radial speeds and the radial smash
    assert metrics["ball_speed"]["status"] == "measured"
    assert metrics["club_speed"]["status"] == "measured"
    assert metrics["smash_factor"]["status"] == "calculated"


@pytest.mark.parametrize("install", ["fusion_unit", "ops_only"])
def test_every_install_opens_a_shot_the_same_way(install, request):
    """No per-install split: the carry is not measured until flown, the smash is radial."""
    request.getfixturevalue(install)
    shot = _shot()

    server_module._open_carry_status(shot)  # pylint: disable=protected-access
    server_module._open_smash_factor_status(shot)  # pylint: disable=protected-access

    assert shot.carry_status == "not_measured"
    assert shot.smash_factor_status == "radial_ratio"
    assert not hasattr(server_module, "_fusion_unit")


def test_an_unfinalized_shot_has_no_table_carry():
    """Shots that never reached finalization (replays, older logs) get no table either."""
    shot = _shot()

    assert shot.carry_status is None
    assert shot.reported_carry_yards is None
    assert _payload(shot)["estimated_carry_yards"] is None
    assert _payload(shot)["carry_range"] is None


def test_a_carry_status_not_measured_wins_over_a_stray_carry():
    shot = _shot(carry_spin_adjusted=250.0, carry_status="not_measured")

    assert shot.reported_carry_yards is None


def _await_finalization():
    from tests.test_server import _wait_for_shot_finalization_idle

    _wait_for_shot_finalization_idle()


def test_a_fusion_unit_says_carry_not_measured_from_first_emit_to_session_log(
    monkeypatch, quiet_finalize
):
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
            log_pinned=lambda *_a, **_k: None,
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


def test_the_resolver_sends_a_shot_its_simulated_carry_as_calculated():
    shot = _fusion_sim_shot(carry_spin_adjusted=243.8, carry_status="measured")

    resolved = resolve_shot(shot, PlayerState())

    assert resolved.carry_yards == pytest.approx(243.8)
    # a simulated carry is never measured (C8)
    assert resolved.provenance["carry"] == "calculated"


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


# OpenConnect V1 requires BallData Speed, VLA, HLA, TotalSpin and SpinAxis; CarryDistance and
# every ClubData field are optional. On a fusion unit an unmeasured required field means the
# shot is not sent (the screen and sim-send log say why); an unmeasured optional one is left off.


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
        resolve_shot(_fusion_sim_shot(**overrides), state)

    assert raised.value.missing == missing
    assert str(raised.value) == reason
    assert state.shot_counter == 0  # no shot number spent on a shot that was not sent


def test_a_fusion_shot_with_a_calculated_spin_is_sent_under_its_label():
    shot = _fusion_sim_shot(spin_rpm=2600, spin_confidence=None, spin_source="calculated")

    resolved = resolve_shot(shot, PlayerState())

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

    resolved = resolve_shot(shot, PlayerState())
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


def test_a_fully_measured_fusion_shot_is_sent_with_its_labels():
    resolved = resolve_shot(_fusion_sim_shot(), PlayerState())

    # the spin axis (and the spin split by it) is the kiosk's computation (C8)
    computed = {"spin_axis", "back_spin", "side_spin", "carry"}
    assert {resolved.provenance[k] for k in resolved.provenance if k not in computed} == {
        "measured"
    }
    assert resolved.provenance["spin_axis"] == "calculated"


def test_an_ops_only_shot_is_not_sent_with_estimates_and_says_why():
    state = PlayerState()

    with pytest.raises(UnmeasuredShotError) as raised:
        resolve_shot(_shot(), state)

    assert raised.value.missing == ["vertical launch", "horizontal launch", "spin", "spin axis"]
    assert state.shot_counter == 0


def test_the_resolver_has_no_substitutes_switch():
    import inspect

    assert "substitutes" not in inspect.signature(resolve_shot).parameters


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


@pytest.mark.parametrize("fusion", [True, False], ids=["fusion", "ops-only"])
def test_the_server_treats_the_same_shot_the_same_on_every_install(monkeypatch, fusion):
    sent, emitted, session_log = _forward(
        monkeypatch, _fusion_sim_shot(spin_confidence=0.3), fusion=fusion
    )

    assert sent == []
    assert [event for event, _payload in emitted] == ["sim_shot_dropped"]
    assert session_log.not_sent[0]["missing"] == ["spin"] and session_log.sends == []


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


# a NaN or infinite field is unmeasured exactly like None

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
        resolve_shot(_fusion_sim_shot(**{field: bad}), state)

    assert raised.value.missing == [missing]
    assert str(raised.value) == f"{missing} not measured"
    assert state.shot_counter == 0


@pytest.mark.parametrize("bad", _NON_FINITE, ids=_NON_FINITE_IDS)
def test_a_fusion_shot_with_non_finite_optional_fields_is_sent_without_them(bad):
    shot = _fusion_sim_shot(club_speed_mph=bad, club_path_deg=bad)

    resolved = resolve_shot(shot, PlayerState())

    assert resolved.club_speed_mph is None and resolved.club_path_deg is None
    for field in ("club_speed", "club_path"):
        assert field not in resolved.provenance
        assert field not in resolved.as_values()


@pytest.mark.parametrize("bad", _NON_FINITE, ids=_NON_FINITE_IDS)
def test_a_fusion_shot_with_a_non_finite_carry_is_sent_without_carry(bad):
    shot = _fusion_sim_shot(carry_spin_adjusted=bad, carry_status="measured")

    resolved = resolve_shot(shot, PlayerState())

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


def test_an_ops_only_unit_does_not_send_a_nan_spin_shot(monkeypatch):
    sent, emitted, _session_log = _forward(
        monkeypatch, _fusion_sim_shot(spin_rpm=float("nan")), fusion=False
    )

    assert sent == []
    assert emitted[0][1]["missing"] == ["spin"]


@pytest.mark.parametrize("bad", _NON_FINITE, ids=_NON_FINITE_IDS)
def test_a_non_finite_fusion_carry_is_not_reported_anywhere(bad):
    shot = _fusion_sim_shot(carry_spin_adjusted=bad, carry_status="measured")

    assert shot.reported_carry_yards is None
    payload = server_module.shot_to_dict(shot)
    assert payload["carry_spin_adjusted"] is None
    assert payload["estimated_carry_yards"] is None  # no table on a fusion unit
    assert payload["carry_status"] is None  # a nulled carry keeps no "measured" label
    assert {"id": "carry_spin_adjusted", "reason": f"non_finite_value:{bad!r}"} in payload[
        "metrics_not_measured"
    ]
    carry_log = server_module._carry_log_text(shot, None)  # pylint: disable=protected-access
    assert carry_log == "carry not measured"


def test_a_non_finite_estimated_carry_is_not_measured_with_no_table():
    shot = _shot(carry_spin_adjusted=float("nan"), carry_status="estimated")

    assert shot.reported_carry_yards is None
    carry_log = server_module._carry_log_text(shot, None)  # pylint: disable=protected-access
    assert carry_log == "carry not measured"


def test_session_stats_average_only_finite_reported_carries():
    from openflight.launch_monitor import summarize_shots

    shots = [
        _fusion_sim_shot(carry_spin_adjusted=240.0),
        _fusion_sim_shot(carry_spin_adjusted=float("nan")),
        _fusion_sim_shot(carry_spin_adjusted=None, carry_status="not_measured"),
    ]

    assert summarize_shots(shots, "rolling-buffer")["avg_carry_est"] == pytest.approx(240.0)


# ---- no formula spin (owner decision, 25 Oct 2026) -------------------------------------
#
# The kinematic formula and its TrackMan refit missed TrackMan by 26-30 % median, so no
# formula spin stands in. The spin is an accepted OPS reading, the collision model's, or
# the OPS reading below the floor (experimental, feeding nothing); else not measured.


@pytest.mark.parametrize("install", ["fusion_unit", "ops_only"])
def test_a_measured_launch_with_no_trustworthy_spin_has_no_spin_and_no_carry(
    install, request, quiet_finalize
):
    request.getfixturevalue(install)
    shot = _shot(
        launch_angle_vertical=12.0,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=1.0,
        launch_angle_horizontal_source="radar",
    )

    _finalize(shot)
    metrics = _payload(shot)["metrics"]

    assert shot.spin_rpm is None
    assert metrics["spin_rate"]["status"] == "not_measured"
    reason = metrics["spin_rate"]["reason"]
    assert reason.startswith("no trustworthy spin: ")
    assert "ops: no OPS spin reading" in reason
    assert "collision model" in reason or "no club delivery" in reason
    assert metrics["carry"]["status"] == "not_measured"
    assert "spin not measured" in metrics["carry"]["reason"]


def test_the_spin_chain_has_no_formula_route(quiet_finalize):
    shot = _shot(launch_angle_vertical=12.0, launch_angle_vertical_source="radar")

    server_module._resolve_spin(shot)  # pylint: disable=protected-access

    routes = [entry["route"] for entry in shot.input_sources["spin"]["tried"]]
    assert routes == ["ops", "collision", "ops_below_floor"]
