"""Tests for sim.resolver: what a simulator is sent, with provenance; nothing filled in."""

import math
from datetime import datetime

import pytest

from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from openflight.sim.resolver import resolve_shot
from openflight.sim.types import IncompleteShotError, PlayerState, UnmeasuredShotError


def _shot(**kw) -> Shot:
    base = dict(
        ball_speed_mph=140.0, timestamp=datetime(2026, 4, 26, 12, 0, 0), club=ClubType.DRIVER
    )
    base.update(kw)
    return Shot(**base)


def test_full_measured_shot():
    shot = _shot(
        club_speed_mph=110.0,
        launch_angle_vertical=12.0,
        launch_angle_horizontal=1.5,
        spin_rpm=2500.0,
        spin_confidence=0.9,
        spin_axis_deg=-3.0,
        club_path_deg=0.5,
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


_MEASURED = dict(
    launch_angle_vertical=12.0,
    launch_angle_horizontal=1.5,
    spin_rpm=2500.0,
    spin_confidence=0.9,
    spin_axis_deg=-3.0,
)


@pytest.mark.parametrize(
    "missing, named",
    [
        ({"launch_angle_vertical": None}, "vertical launch"),
        ({"launch_angle_horizontal": None}, "horizontal launch"),
        ({"spin_confidence": 0.4}, "spin"),
        ({"spin_rpm": None, "spin_confidence": None}, "spin"),
        ({"spin_axis_deg": None}, "spin axis"),
    ],
)
def test_a_missing_required_field_is_never_filled_in(missing, named):
    """No club-table launch, 0 deg angle or typical spin on any install (decision 1)."""
    state = PlayerState()
    with pytest.raises(UnmeasuredShotError) as refused:
        resolve_shot(_shot(**{**_MEASURED, **missing}), state)
    assert refused.value.missing == [named]
    assert state.shot_counter == 0


def test_missing_club_speed_and_path_are_left_out():
    r = resolve_shot(_shot(**_MEASURED), PlayerState())
    assert r.club_speed_mph is None and r.club_path_deg is None
    assert "club_speed" not in r.provenance and "club_path" not in r.provenance


def test_an_unlabelled_carry_is_not_sent():
    """A carry with no label has unknown inputs; nothing is sent in its place."""
    r = resolve_shot(_shot(**_MEASURED, carry_spin_adjusted=244.0), PlayerState())
    assert r.carry_yards is None and "carry" not in r.provenance


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
    resolved = resolve_shot(shot, PlayerState())
    assert resolved.carry_yards == pytest.approx(244.0)
    assert resolved.provenance["carry"] == "calculated"


def test_missing_ball_speed_raises():
    with pytest.raises(IncompleteShotError):
        resolve_shot(_shot(ball_speed_mph=0.0), PlayerState())


def test_shot_number_uses_player_state():
    ps = PlayerState()
    ps.next_shot_number()  # consume one
    r = resolve_shot(_shot(**_MEASURED), ps)
    assert r.shot_number == 2


def test_carry_is_the_committed_simulated_carry():
    shot = _shot(**_MEASURED, carry_spin_adjusted=244.0, carry_status="calculated")
    assert resolve_shot(shot, PlayerState()).carry_yards == pytest.approx(244.0)


def test_carry_is_never_the_ball_speed_table():
    shot = _shot(**_MEASURED)
    assert resolve_shot(shot, PlayerState()).carry_yards is None


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

    def resolved(value):
        try:
            return resolve_shot(_shot(**{**measured, field: value}), PlayerState())
        except UnmeasuredShotError as refused:
            return refused.missing

    with_bad, with_none = resolved(bad), resolved(None)
    if isinstance(with_none, list):
        assert with_bad == with_none
        return
    _same_resolution(with_bad, with_none)
    assert all(v is None or math.isfinite(v) for v in with_bad.as_values().values())


@pytest.mark.parametrize("bad", _NON_FINITE, ids=["nan", "inf", "-inf"])
def test_a_non_finite_launch_is_refused_like_a_missing_one(bad):
    with pytest.raises(UnmeasuredShotError) as refused:
        resolve_shot(_shot(**{**_MEASURED, "launch_angle_vertical": bad}), PlayerState())
    assert refused.value.missing == ["vertical launch"]


@pytest.mark.parametrize("bad", _NON_FINITE, ids=["nan", "inf", "-inf"])
def test_a_non_finite_calculated_spin_is_refused(bad):
    shot = _shot(
        **{**_MEASURED, "spin_rpm": bad, "spin_confidence": None}, spin_source="calculated"
    )
    with pytest.raises(UnmeasuredShotError) as refused:
        resolve_shot(shot, PlayerState())
    assert refused.value.missing == ["spin"]


@pytest.mark.parametrize("bad", _NON_FINITE, ids=["nan", "inf", "-inf"])
def test_a_non_finite_ball_speed_is_not_sendable(bad):
    with pytest.raises(IncompleteShotError):
        resolve_shot(_shot(ball_speed_mph=bad), PlayerState())


def test_no_resolved_value_can_leave_non_finite(monkeypatch):
    """Last line: whatever produced it, a non-finite resolved value is never sent."""
    from openflight.sim import resolver

    monkeypatch.setattr(resolver, "_measured_total_spin", lambda _shot: (math.nan, "measured"))
    with pytest.raises(IncompleteShotError, match="total_spin"):
        resolve_shot(_shot(**_MEASURED), PlayerState())


def test_a_calculated_spin_and_axis_go_out_labelled_calculated():
    r = resolve_shot(
        _shot(
            launch_angle_vertical=12.0,
            launch_angle_horizontal=1.5,
            spin_rpm=2700.0,
            spin_source="calculated",
            spin_axis_deg=4.0,
            spin_axis_source="calculated (D-plane tilt, Wood et al. 2018 weights)",
        ),
        PlayerState(),
    )

    for field in ("total_spin", "spin_axis", "back_spin", "side_spin"):
        assert r.provenance[field] == "calculated", field


def test_a_spin_component_is_as_good_as_its_weaker_source(monkeypatch):
    from openflight.sim import resolver

    angles = dict(launch_angle_vertical=12.0, launch_angle_horizontal=1.5)
    calculated_axis = resolve_shot(
        _shot(
            **angles,
            spin_rpm=2500.0,
            spin_confidence=0.9,
            spin_axis_deg=2.0,
            spin_axis_source="calculated (D-plane tilt, Wood et al. 2018 weights)",
        ),
        PlayerState(),
    )
    monkeypatch.setattr(resolver, "SIMULATORS_ACCEPT_ESTIMATED", True)
    estimated_axis = resolve_shot(
        _shot(
            **angles,
            spin_rpm=2700.0,
            spin_source="calculated",
            spin_axis_deg=2.0,
            spin_axis_source="estimated (D-plane tilt; horizontal launch is low-coherence)",
        ),
        PlayerState(),
    )

    assert calculated_axis.provenance["spin_axis"] == "calculated"
    assert calculated_axis.provenance["side_spin"] == "calculated"
    assert estimated_axis.provenance["spin_axis"] == "estimated"
    assert estimated_axis.provenance["side_spin"] == "estimated"


def test_a_weak_horizontal_keeps_its_own_estimated_label():
    # a low-coherence IWR horizontal is a weak measured angle, not a filled-in one
    shot = _shot(**_MEASURED, launch_angle_vertical_source="radar")
    shot.launch_angle_horizontal_source = "radar_low_coherence"
    r = resolve_shot(shot, PlayerState())
    assert r.provenance["vla"] == "measured"
    assert r.provenance["hla"] == "estimated"


@pytest.mark.parametrize(
    ("field", "words"),
    [
        ("launch_angle_vertical_source", "vertical launch (experimental)"),
        ("launch_angle_horizontal_source", "horizontal launch (experimental)"),
    ],
)
def test_a_fusion_shot_on_the_camera_fallback_launch_is_shown_but_never_flown(field, words):
    from openflight.sim.types import UnmeasuredShotError  # noqa: PLC0415

    shot = _shot(
        launch_angle_vertical=12.0,
        launch_angle_horizontal=1.5,
        spin_rpm=2500.0,
        spin_confidence=0.9,
        spin_axis_deg=-3.0,
        **{field: "camera_fallback"},
    )
    with pytest.raises(UnmeasuredShotError) as refused:
        resolve_shot(shot, PlayerState())
    assert words in str(refused.value)
