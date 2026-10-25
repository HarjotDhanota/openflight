"""Tests for sim.resolver — the shared fallback table + provenance."""
import math
from datetime import datetime

import pytest

from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from openflight.sim.resolver import SPIN_MODEL_RPM, resolve_shot
from openflight.sim.types import IncompleteShotError, PlayerState


def _shot(**kw) -> Shot:
    base = dict(ball_speed_mph=140.0, timestamp=datetime(2026, 4, 26, 12, 0, 0),
                club=ClubType.DRIVER)
    base.update(kw)
    return Shot(**base)


def test_full_measured_shot():
    shot = _shot(
        club_speed_mph=110.0, launch_angle_vertical=12.0,
        launch_angle_horizontal=1.5, spin_rpm=2500.0, spin_confidence=0.9,
        spin_axis_deg=-3.0, club_path_deg=0.5,
    )
    r = resolve_shot(shot, PlayerState())
    assert r.ball_speed_mph == 140.0
    assert r.vla == 12.0
    assert r.hla == 1.5
    assert r.total_spin_rpm == 2500.0
    assert r.spin_axis_deg == -3.0
    assert math.isclose(r.back_spin_rpm, 2500 * math.cos(math.radians(-3.0)), rel_tol=0.01)
    assert math.isclose(r.side_spin_rpm, 2500 * math.sin(math.radians(-3.0)), rel_tol=0.01)
    assert r.club_speed_mph == 110.0
    assert r.club_path_deg == 0.5
    for f in (
        "ball_speed",
        "vla",
        "hla",
        "total_spin",
        "club_speed",
        "club_path",
    ):
        assert r.provenance[f] == "measured", f
    # no sensor measures the spin axis: the kiosk computes it
    for f in ("spin_axis", "back_spin", "side_spin"):
        assert r.provenance[f] == "calculated", f


def test_missing_vla_falls_back_to_optimal_launch():
    shot = _shot(spin_rpm=2500.0, spin_confidence=0.9, club=ClubType.IRON_7)
    r = resolve_shot(shot, PlayerState())
    assert r.vla == 20.5  # _OPTIMAL_LAUNCH[IRON_7]
    assert r.provenance["vla"] == "estimated"


def test_missing_hla_falls_back_to_zero():
    r = resolve_shot(_shot(spin_rpm=2500.0, spin_confidence=0.9), PlayerState())
    assert r.hla == 0.0
    assert r.provenance["hla"] == "estimated"


def test_low_spin_confidence_uses_model():
    shot = _shot(spin_rpm=2500.0, spin_confidence=0.4, club=ClubType.DRIVER)
    r = resolve_shot(shot, PlayerState())
    assert r.total_spin_rpm == SPIN_MODEL_RPM[ClubType.DRIVER]
    assert r.provenance["total_spin"] == "estimated"


def test_missing_spin_uses_model():
    r = resolve_shot(_shot(club=ClubType.IRON_7), PlayerState())
    assert r.total_spin_rpm == SPIN_MODEL_RPM[ClubType.IRON_7]
    assert r.provenance["total_spin"] == "estimated"


def test_missing_spin_axis_falls_back_to_zero():
    shot = _shot(spin_rpm=2500.0, spin_confidence=0.9)  # no spin_axis_deg
    r = resolve_shot(shot, PlayerState())
    assert r.spin_axis_deg == 0.0
    assert r.provenance["spin_axis"] == "estimated"
    assert r.back_spin_rpm == 2500.0
    assert r.side_spin_rpm == 0.0


def test_derived_spin_provenance_estimated_when_either_input_estimated():
    shot = _shot(spin_rpm=2500.0, spin_confidence=0.9)  # axis missing
    r = resolve_shot(shot, PlayerState())
    assert r.provenance["back_spin"] == "estimated"
    assert r.provenance["side_spin"] == "estimated"


def test_missing_club_speed_is_none():
    r = resolve_shot(_shot(spin_rpm=2500.0, spin_confidence=0.9), PlayerState())
    assert r.club_speed_mph is None
    assert r.provenance["club_speed"] == "estimated"


def test_missing_club_path_falls_back_to_zero():
    shot = _shot(spin_rpm=2500.0, spin_confidence=0.9, club_speed_mph=100.0)
    r = resolve_shot(shot, PlayerState())
    assert r.club_path_deg == 0.0
    assert r.provenance["club_path"] == "estimated"


def test_an_ops_only_carry_is_always_estimated():
    """An OPS-only carry flies a club-typical spin, so it is an estimate even with a measured launch."""
    with_launch = resolve_shot(_shot(launch_angle_vertical=12.0), PlayerState())
    assert with_launch.provenance["carry"] == "estimated"
    estimated = resolve_shot(_shot(), PlayerState())
    assert estimated.provenance["carry"] == "estimated"


def test_a_fusion_carry_from_measured_inputs_is_sent_as_calculated():
    """A simulated carry is the flight model's output: at most calculated."""
    shot = _shot(
        launch_angle_vertical=12.0,
        launch_angle_horizontal=1.5,
        spin_rpm=2500.0,
        spin_confidence=0.9,
        spin_axis_deg=-3.0,
        carry_spin_adjusted=244.0,
        carry_status="measured",
    )
    resolved = resolve_shot(shot, PlayerState(), substitutes=False)
    assert resolved.carry_yards == pytest.approx(244.0)
    assert resolved.provenance["carry"] == "calculated"


def test_missing_ball_speed_raises():
    with pytest.raises(IncompleteShotError):
        resolve_shot(_shot(ball_speed_mph=0.0), PlayerState())


def test_shot_number_uses_player_state():
    ps = PlayerState()
    ps.next_shot_number()  # consume one
    r = resolve_shot(_shot(spin_rpm=2500.0, spin_confidence=0.9), ps)
    assert r.shot_number == 2


def test_carry_prefers_committed_spin_adjusted_carry():
    shot = _shot(launch_angle_vertical=12.0, carry_spin_adjusted=244.0)
    assert resolve_shot(shot, PlayerState()).carry_yards == pytest.approx(244.0)


def test_carry_falls_back_to_table_when_nothing_committed():
    shot = _shot(launch_angle_vertical=12.0)
    assert resolve_shot(shot, PlayerState()).carry_yards == pytest.approx(
        shot.estimated_carry_yards
    )


_NON_FINITE = [math.nan, math.inf, -math.inf]


def _same_resolution(a, b):
    assert a.as_values() == b.as_values()
    assert a.provenance == b.provenance


@pytest.mark.parametrize("bad", _NON_FINITE, ids=["nan", "inf", "-inf"])
@pytest.mark.parametrize(
    "field",
    [
        "launch_angle_vertical",
        "launch_angle_horizontal",
        "spin_axis_deg",
        "club_speed_mph",
        "club_path_deg",
        "spin_rpm",
        "spin_confidence",
    ],
)
def test_a_non_finite_input_resolves_exactly_like_a_missing_one(field, bad):
    measured = dict(
        club_speed_mph=110.0,
        launch_angle_vertical=12.0,
        launch_angle_horizontal=1.5,
        spin_rpm=2500.0,
        spin_confidence=0.9,
        spin_axis_deg=-3.0,
        club_path_deg=0.5,
    )
    with_bad = resolve_shot(_shot(**{**measured, field: bad}), PlayerState())
    with_none = resolve_shot(_shot(**{**measured, field: None}), PlayerState())
    _same_resolution(with_bad, with_none)
    assert all(v is None or math.isfinite(v) for v in with_bad.as_values().values())


@pytest.mark.parametrize("bad", _NON_FINITE, ids=["nan", "inf", "-inf"])
def test_a_non_finite_calculated_spin_uses_the_model(bad):
    r = resolve_shot(_shot(spin_rpm=bad, spin_source="calculated"), PlayerState())
    assert r.total_spin_rpm == SPIN_MODEL_RPM[ClubType.DRIVER]
    assert r.provenance["total_spin"] == "estimated"


@pytest.mark.parametrize("bad", _NON_FINITE, ids=["nan", "inf", "-inf"])
def test_a_non_finite_ball_speed_is_not_sendable(bad):
    with pytest.raises(IncompleteShotError):
        resolve_shot(_shot(ball_speed_mph=bad), PlayerState())


def test_no_resolved_value_can_leave_non_finite(monkeypatch):
    """Last line: whatever produced it, a non-finite resolved value is never sent."""
    from openflight.sim import resolver

    class Physics:
        optimal_launch_deg = math.nan

    monkeypatch.setattr(resolver, "get_club_physics", lambda _club: Physics())
    with pytest.raises(IncompleteShotError, match="vla"):
        resolve_shot(_shot(), PlayerState())


def test_a_calculated_spin_and_axis_go_out_labelled_calculated():
    r = resolve_shot(
        _shot(
            spin_rpm=2700.0,
            spin_source="calculated",
            spin_axis_deg=4.0,
            spin_axis_source="calculated (D-plane tilt, Wood et al. 2018 weights)",
        ),
        PlayerState(),
    )

    for field in ("total_spin", "spin_axis", "back_spin", "side_spin"):
        assert r.provenance[field] == "calculated", field


def test_a_spin_component_is_as_good_as_its_weaker_source():
    measured_spin = dict(spin_rpm=2500.0, spin_confidence=0.9)
    calculated_axis = resolve_shot(
        _shot(
            **measured_spin,
            spin_axis_deg=2.0,
            spin_axis_source="calculated (D-plane tilt, Wood et al. 2018 weights)",
        ),
        PlayerState(),
    )
    estimated_axis = resolve_shot(_shot(spin_rpm=2700.0, spin_source="calculated"), PlayerState())

    assert calculated_axis.provenance["spin_axis"] == "calculated"
    assert calculated_axis.provenance["side_spin"] == "calculated"
    assert estimated_axis.provenance["spin_axis"] == "estimated"
    assert estimated_axis.provenance["side_spin"] == "estimated"


def test_server_estimated_launch_angles_are_tagged_estimated():
    # server._ensure_user_facing_launch_angles fills missing angles before the
    # sim fan-out and marks them with source "estimated".
    shot = _shot(
        launch_angle_vertical=11.0, launch_angle_vertical_source="estimated",
        launch_angle_horizontal=0.0, launch_angle_horizontal_source="estimated",
    )
    r = resolve_shot(shot, PlayerState())
    assert r.vla == 11.0
    assert r.hla == 0.0
    assert r.provenance["vla"] == "estimated"
    assert r.provenance["hla"] == "estimated"
    assert r.provenance["carry"] == "estimated"


def test_measured_vla_with_estimated_hla_keeps_each_angle_s_own_label():
    # A vertical angle radar alone gives a measured VLA, and the server fills in
    # the neutral 0.0 HLA. An OPS-only carry stays "estimated" whatever the launch angle.
    shot = _shot(
        launch_angle_vertical=12.0, launch_angle_vertical_source="radar",
        launch_angle_horizontal=0.0, launch_angle_horizontal_source="estimated",
    )
    r = resolve_shot(shot, PlayerState())
    assert r.provenance["vla"] == "measured"
    assert r.provenance["hla"] == "estimated"
    assert r.provenance["carry"] == "estimated"
