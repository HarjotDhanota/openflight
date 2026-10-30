"""The collision model against the in-repo TM4 export session (24 shots: driver, 7i, PW).

The gear gains (with a second session's export) and the per-club expected ball speed were
fitted to it; the launch, spin and loss constants were not. Budgets are the model's own numbers.
"""

from __future__ import annotations

import math

import pytest

from openflight import collision_forward as cf, collision_model as cm, metric_definitions as md
from openflight.clubs import ClubType
from tests.tm_owner_export import number, owner_shots, rms

CLUBS = {"7 Iron": ClubType.IRON_7, "Driver": ClubType.DRIVER, "Pitching Wedge": ClubType.PW}
COLUMNS = (
    "Club Speed",
    "Attack Angle",
    "Club Path",
    "Face Angle",
    "Dyn. Loft",
    "Ball Speed",
    "Launch Angle",
    "Launch Direction",
)


def _shots():
    shots = []
    for shot in owner_shots():
        club = CLUBS.get(shot["Club"])
        values = [number(shot, column) for column in COLUMNS]
        if club is None or None in values:
            continue
        shots.append((club, shot, *values))
    return shots


SHOTS = _shots()


def _forward(club, shot, speed, attack, path, face, loft):
    return cf.forward(
        club=club,
        club_speed_mph=speed,
        attack_angle_deg=attack,
        club_path_deg=path,
        face_angle_deg=face,
        dynamic_loft_deg=loft,
        impact_offset_mm=number(shot, "Impact Offset"),
        impact_height_mm=number(shot, "Impact Height"),
        assume_centred=True,
    )


def test_the_session_has_the_shots_it_should():
    assert len(SHOTS) == 23


def test_the_launch_follows_the_delivery_better_than_the_published_ratios():
    new_v, new_h, old_v, old_h = [], [], [], []
    for club, shot, speed, attack, path, face, loft, _ball, launch_v, launch_h in SHOTS:
        impact = _forward(club, shot, speed, attack, path, face, loft)
        new_v.append(impact.launch_vertical_deg - launch_v)
        new_h.append(impact.launch_horizontal_deg - launch_h)
        h, v = cm.forward_launch(
            face_angle_deg=face,
            dynamic_loft_deg=loft,
            club_path_deg=path,
            attack_angle_deg=attack,
            weights=cm.weights_for_club(club),
        )
        old_v.append(v - launch_v)
        old_h.append(h - launch_h)

    assert rms(new_v) < 0.35 and rms(new_v) < rms(old_v)
    assert rms(new_h) < 0.1 and rms(new_h) < rms(old_h)


def test_the_spin_follows_the_measured_spin_better_than_penner():
    new, old = [], []
    for club, shot, speed, attack, path, face, loft, *_ in SHOTS:
        spin = number(shot, "Spin Rate")
        if not spin:
            continue
        new.append(_forward(club, shot, speed, attack, path, face, loft).spin_rpm / spin - 1.0)
        spin_loft = md.spin_loft_deg(loft, face, attack, path)
        old.append(cm.penner_impact(speed * cm.MPH_TO_M_S, spin_loft).spin_rpm / spin - 1.0)

    assert rms(new) < 0.09
    assert rms(new) < 0.5 * rms(old)


def test_a_driver_axis_with_the_impact_location_follows_the_export():
    errors = []
    for club, shot, speed, attack, path, face, loft, *_ in SHOTS:
        axis = number(shot, "Spin Axis")
        if club is ClubType.DRIVER and axis is not None:
            errors.append(
                _forward(club, shot, speed, attack, path, face, loft).spin_axis_deg - axis
            )

    assert len(errors) == 8
    assert rms(errors) < 2.2


def test_face_and_loft_from_the_launch_beat_the_published_ratios():
    new_face, new_loft, old_face, old_loft = [], [], [], []
    for club, _shot, _speed, attack, path, face, loft, _ball, launch_v, launch_h in SHOTS:
        got = cf.face_and_loft_from_launch(
            club=club,
            launch_vertical_deg=launch_v,
            launch_horizontal_deg=launch_h,
            club_path_deg=path,
            attack_angle_deg=attack,
        )
        published = cm.invert_d_plane(
            launch_horizontal_deg=launch_h,
            launch_vertical_deg=launch_v,
            club_path_deg=path,
            attack_angle_deg=attack,
            weights=cm.weights_for_club(club),
        )
        new_face.append(got[0] - face)
        new_loft.append(got[1] - loft)
        old_face.append(published[0] - face)
        old_loft.append(published[1] - loft)

    assert rms(new_face) < 0.1 and rms(new_face) < rms(old_face)
    assert rms(new_loft) < 0.45 and rms(new_loft) < rms(old_loft)


@pytest.mark.parametrize(("column", "limit"), [("Spin Index", 0.5), ("Smash Index", 0.06)])
def test_the_indices_follow_the_exported_indices(column, limit):
    errors = []
    for club, shot, speed, attack, path, face, loft, ball, launch_v, launch_h in SHOTS:
        exported, spin = number(shot, column), number(shot, "Spin Rate")
        spin_loft = md.spin_loft_deg(loft, face, attack, path)
        if column == "Spin Index":
            if exported is None or not spin:
                continue
            expected = cf.expected_spin_rpm(
                club=club, club_speed_mph=speed, spin_loft_deg=spin_loft
            )
            errors.append(100.0 * spin / expected - exported)
        else:
            expected = cf.expected_ball_speed_mph(
                club=club, club_speed_mph=speed, spin_loft_deg=spin_loft
            )
            errors.append(100.0 * ball / expected - exported)

    assert len(errors) >= 20
    assert all(math.isfinite(error) for error in errors)
    assert rms(errors) < limit
