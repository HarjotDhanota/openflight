"""The attack angle is read from the head's free flight only (C21a).

The contact tangent was a constant-acceleration fit over the four pre-contact frames nearest
contact (C10b, C18a). On Oct 27 shot 3 the heel reached the hitting surface at f42, ~180 mm
before the ball, and rode it into contact: a quadratic through 135, 73, 31, 35 mm bends
upward and read +15.5 deg (research/rig-geometry-lock-2026-11-02/c20/c20b-shot3-club.txt).
A head on the surface is not in free flight. The tangent now fits only frames whose heel is
above the surface by more than the heel anchor's own height over the sole
(HEEL_ANCHOR_ABOVE_SOLE_M: the average iron's hosel foot is 35.2 mm above its sole), within
the last CONTACT_TANGENT_WINDOW_S of free flight, read where free flight ended. A head that
reached the surface more than a ball diameter before the ball is a fat contact: the attack
angle is labelled with how far before the ball, its tier lowered to low, and withheld when
fewer than three free-flight frames remain.
"""

from __future__ import annotations

import math

import pytest

from openflight.camera import club_delivery as cd

# Oct 27 shots 1, 3, 4 on the live chain at b9705ea7 (generality run, shots2/): the club
# stage's head positions (t from its contact, lateral, up, forward m); heights are above the
# hitting surface (the ball's centre sits at its radius)
LIVE = {
    1: (
        [
            (-16.993934435504343, -0.24939553682452112, 0.18481567103024177, 1.2112134754616777),
            (-13.375934435316594, -0.22205044070468566, 0.1330851184635891, 1.3393289846650414),
            (-9.758934435467381, -0.2020121143377315, 0.09475063102745454, 1.4619032497690232),
            (-6.143934435385745, -0.18340492681146311, 0.06497031651280596, 1.5821285716953122),
            (-2.5279344354203204, -0.16360683106522111, 0.03703220427060101, 1.7012794292712277),
            (1.0890655644288927, -0.14726825916076303, 0.031961285839980896, 1.8195832000933725),
        ],
        -9.76,
    ),
    3: (
        [
            (-16.442576195913716, 0.02719471810910361, 0.1781629863094881, 1.6092719448982298),
            (-12.826576195948292, 0.06134537310870622, 0.13475181009950823, 1.7299650718641868),
            (-9.209576195644331, 0.1040651991682444, 0.07322723554904455, 1.8486406248372567),
            (-5.5935761956789065, 0.1323892773073584, 0.03142616191972601, 1.9657672985070402),
            (-1.9765761962844408, 0.14538216470891052, 0.03492450302458451, 2.0838257576476757),
            (1.6404238040195196, 0.16721171218097583, 0.02755652913053147, 2.201126362735996),
        ],
        15.45,
    ),
    4: (
        [
            (-15.15306279725337, 0.022532168001868146, 0.18193793791853757, 1.5089098995791268),
            (-11.536062796949409, 0.06242849166333394, 0.11691852858628701, 1.627419026562462),
            (-7.919062797554943, 0.08918459626984372, 0.07332200824397118, 1.7427833829462482),
            (-4.302062797250983, 0.1044802501301107, 0.044786203153589325, 1.8575313822065798),
            (-0.6840627975179814, 0.11196720144497149, 0.03204438911742427, 1.9725126785623543),
            (2.926937202573754, 0.12905315907229734, 0.023735514312806558, 2.086688152680556),
        ],
        -1.12,
    ),
}


def _positions(rows, contact=100.0):
    return tuple(
        cd.HeadPosition(t_s=contact + t / 1e3, lateral_m=lat, up_m=up, forward_m=fwd)
        for t, lat, up, fwd in rows
    )


def _tangent(rows, contact=100.0, surface=0.0):
    positions = _positions(rows, contact)
    last_rest = max(p.t_s for p in positions if p.t_s < contact)
    return cd.contact_tangent(
        positions, contact_t_s=contact, last_rest_t_s=last_rest, surface_up_m=surface
    )


def test_oct27_shot1_meets_the_surface_at_the_ball_and_is_unchanged():
    rows, live = LIVE[1]
    tangent = _tangent(rows)
    assert tangent.attack_angle_deg == pytest.approx(live, abs=0.05)
    assert tangent.fat_contact_mm is None and tangent.reason is None
    assert tangent.free_flight_frames == 4  # f40-f43: f39 is 17 ms out, beyond the window


def test_oct27_shot3_is_a_fat_contact_read_from_its_free_flight():
    rows, live = LIVE[3]
    tangent = _tangent(rows)
    assert tangent.fat_contact_mm == pytest.approx(184.0, abs=5.0)
    assert tangent.free_flight_frames == 3  # f39-f41; f42 and f43 ride the surface
    # the descent where free flight ended (f42), no longer the +15.5 a fit across the floor read
    assert tangent.attack_angle_deg == pytest.approx(-34.7, abs=0.5)
    assert tangent.attack_angle_deg < live - 40.0
    assert "fat contact: head reached the surface 184 mm before the ball" in tangent.note


def test_oct27_shot4_reaches_the_surface_at_the_ball_without_a_fat_label():
    rows, _live = LIVE[4]
    tangent = _tangent(rows)
    # its last rest frame is on the surface 22 mm from the contact point (within a diameter):
    # not free flight, so not fitted, but not a fat contact either
    assert tangent.fat_contact_mm is None
    assert tangent.free_flight_frames == 4
    assert tangent.attack_angle_deg == pytest.approx(0.05, abs=0.1)


def test_fewer_than_three_free_flight_frames_withhold_the_attack_angle():
    rows, _live = LIVE[3]
    tangent = _tangent(rows[1:])  # f40, f41 free; f42 lands
    assert tangent.attack_angle_deg is None
    assert tangent.fat_contact_mm == pytest.approx(184.0, abs=5.0)
    assert "fewer than 3 free-flight frames" in tangent.reason


def test_without_a_surface_every_frame_in_the_window_is_free_flight():
    rows, live = LIVE[1]
    positions = _positions(rows)
    last_rest = max(p.t_s for p in positions if p.t_s < 100.0)
    assert cd.attack_angle_at_contact(
        positions, contact_t_s=100.0, last_rest_t_s=last_rest
    ) == pytest.approx(live, abs=0.05)


def test_the_window_is_time_not_a_frame_count():
    # 2.1 ms frames (~480 fps): the last 15 ms of free flight hold 7 of them, not 4; a
    # straight descent at 13.3 m/s down and 33 m/s forward reads its own angle
    rows = [
        (-1.0 - 2.1 * k, 0.0, 0.05 + 0.0133 * (1.0 + 2.1 * k), 1.8 - 0.033 * (1.0 + 2.1 * k))
        for k in range(10)
    ][::-1]
    tangent = _tangent(rows, surface=None)
    assert tangent.free_flight_frames == 7
    assert tangent.attack_angle_deg == pytest.approx(
        -math.degrees(math.atan2(13.3, 33.0)), abs=0.05
    )


def test_the_club_stage_labels_a_fat_contact_and_lowers_its_tier(monkeypatch):
    from tests.test_camera_club_contact_tangent import _shot1_club  # noqa: PLC0415

    case = _shot1_club()
    # a sole margin high enough that the fixture's f43 (36 mm) rides the surface ~80 mm
    # before the ball: the stage's wiring of a fat contact, on real frames
    monkeypatch.setattr(cd, "HEEL_ANCHOR_ABOVE_SOLE_M", 0.05)
    result = cd.estimate_chained_delivery(case["frames"], case["host"], **case["kwargs"])

    assert result.attack_angle_deg is not None
    assert result.attack_confidence_tier == "low"
    assert result.status == "approach_mixed"
    assert any(note.startswith("fat contact: head reached the surface") for note in result.notes)


def test_a_heel_below_the_surface_itself_leaves_the_surface_out_of_it():
    # August 25 shot 4 (unmeasured rig, research/rig-geometry-lock-2026-11-02/shot1-club):
    # heels at 64, 11, 12, 22, 21 mm over a surface at 18.65 mm (ball centre 40 mm). A heel
    # anchor rides 35 mm above a grounded sole, so one below the surface itself shows the
    # heights are not on the surface's datum: the surface ends no free flight there
    rows = [
        (-9.62, 0.0, 0.0639, 1.157),
        (-7.48, 0.0, 0.0108, 1.234),
        (-5.35, 0.0, 0.0117, 1.314),
        (-3.21, 0.0, 0.0221, 1.396),
        (-1.07, 0.0, 0.0212, 1.476),
        (1.07, 0.0, 0.0004, 1.555),
    ]
    tangent = _tangent(rows, surface=0.04 - 0.02135)
    assert tangent.fat_contact_mm is None
    assert tangent.free_flight_frames == 5
    assert tangent.attack_angle_deg is not None
    assert "below the hitting surface" in tangent.datum_note
