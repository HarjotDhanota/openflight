"""G2 C2 (4 Nov 2026): the attack angle is read where the data ends, with its own sigma.

The contact tangent fitted a quadratic per axis over the last 15 ms of free flight and
differentiated it at the contact instant, 2.5 ms (0.7 frame) past the last pre-contact
frame at 288 fps. On Oct 27 shot 1 that turned measured chords of -21.9, -17.4, -13.9 and
-13.2 deg into -9.8, and the 0.7-frame extrapolation multiplied the vertical noise by 2.2
(research club-path-audit CODE-AUDIT s8). The fit is now evaluated at the LAST free-flight
frame, never beyond the data. Its sigma is the head positions' noise propagated through the
fit, and the confidence tier comes from that sigma, not from a range the value falls in.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.camera import club_delivery as cd
from openflight.camera.club_delivery import ApproachPairEstimate, combine_approach_estimates
from tests.test_camera_club_contact_tangent import _shot1_club

FRAME_S = 1.0 / 288.0


def _arc(*, attack_at_last_deg: float, radius_m: float = 1.2, speed: float = 33.0, frames=5):
    """Head positions on a vertical circle, one per 288 fps frame, the last 1.8 ms before
    contact, whose direction at the LAST frame is ``attack_at_last_deg``; and the times."""
    times = 100.0 - 0.0018 - FRAME_S * np.arange(frames - 1, -1, -1)
    # arc angle phi from the low point; the slope is tan(phi), phi = s / R
    phi_last = math.radians(attack_at_last_deg)
    rows = []
    for t in times:
        phi = phi_last + speed * (t - times[-1]) / radius_m
        rows.append((t, radius_m * (1 - math.cos(phi)), 1.8 + radius_m * math.sin(phi)))
    return rows


def _positions(rows, lateral=None):
    return tuple(
        cd.HeadPosition(
            t_s=float(t),
            lateral_m=0.0 if lateral is None else float(lateral[i]),
            up_m=float(up),
            forward_m=float(fwd),
        )
        for i, (t, up, fwd) in enumerate(rows)
    )


def test_the_fit_is_read_at_the_last_frame_never_at_the_contact():
    rows = _arc(attack_at_last_deg=-6.0)
    tangent = cd.contact_tangent(_positions(rows), contact_t_s=100.0, last_rest_t_s=rows[-1][0])
    assert tangent.attack_angle_deg == pytest.approx(-6.0, abs=0.1)
    assert tangent.read_t_s == pytest.approx(rows[-1][0])
    assert tangent.read_before_contact_ms == pytest.approx(1.8, abs=1e-6)


def test_a_circular_arc_is_recovered_at_288_fps_with_2_px_noise_and_an_honest_sigma():
    rows = _arc(attack_at_last_deg=-5.0)
    noise_m = 2 * 0.0018  # 2 px at 1.8 mm/px (640x400 at the tee)
    rng = np.random.default_rng(7)
    values, sigmas = [], []
    for _draw in range(400):
        noisy = [
            (t, up + rng.normal(0.0, noise_m), fwd + rng.normal(0.0, noise_m))
            for t, up, fwd in rows
        ]
        lateral = rng.normal(0.0, noise_m, len(rows))
        tangent = cd.contact_tangent(
            _positions(noisy, lateral),
            contact_t_s=100.0,
            last_rest_t_s=rows[-1][0],
            position_sigma_m=noise_m,
        )
        values.append(tangent.attack_angle_deg)
        sigmas.append(tangent.sigma_deg)
    values = np.asarray(values)
    assert np.mean(values) == pytest.approx(-5.0, abs=0.5)  # no bias from the arc
    # the propagated sigma is the scatter the noise really makes
    assert np.median(sigmas) == pytest.approx(np.std(values), rel=0.2)


def test_the_attack_tier_comes_from_the_sigma_not_a_range():
    paths = [ApproachPairEstimate(p, -4.0, 1.0, 1.0, 12) for p in (2.5, 3.0, 3.5)]

    def tier(attack_deg, sigma):
        window = ApproachPairEstimate(3.0, attack_deg, 1.0, 1.0, 12, attack_angle_sigma_deg=sigma)
        result = combine_approach_estimates(paths, attack_estimate=window, timing_plausible=True)
        return result.attack_confidence_tier, result.attack_angle_sigma_deg

    # -13.2 failed the old 'high' range (-12..+8); its sigma decides now
    assert tier(-13.2, 0.8) == ("high", 0.8)
    assert tier(-4.0, 1.6)[0] == "medium"
    assert tier(-4.0, 3.0)[0] == "low"
    assert tier(-4.0, None)[0] == "low"  # no sigma, no confidence


@pytest.fixture(scope="module")
def shot1():
    case = _shot1_club()
    return case, cd.estimate_chained_delivery(case["frames"], case["host"], **case["kwargs"])


def test_oct27_shot1_reads_the_last_chord_s_descent_with_its_sigma(shot1):
    _case, result = shot1
    by_time = sorted(result.head_positions, key=lambda p: p.t_s)
    f42, f43 = by_time[-2], by_time[-1]
    chord = math.degrees(
        math.atan2(
            f43.up_m - f42.up_m,
            math.hypot(f43.forward_m - f42.forward_m, f43.lateral_m - f42.lateral_m),
        )
    )
    assert result.attack_angle_sigma_deg is not None and result.attack_angle_sigma_deg > 0.0
    assert abs(result.attack_angle_deg - chord) <= result.attack_angle_sigma_deg
    assert result.attack_angle_read_before_contact_ms == pytest.approx(
        1e3 * (result.contact_t_s - f43.t_s), abs=1e-3
    )
    assert any("no extrapolation" in note for note in result.notes)


def test_the_shot_record_carries_the_attack_sigma_as_its_range(shot1):
    from openflight.club_arc import RANGE_SIGMAS, club_delivery_payload  # noqa: PLC0415
    from openflight.clubs import ClubType  # noqa: PLC0415

    _case, result = shot1
    entry = club_delivery_payload(result, club=ClubType.IRON_7, handedness="RH")["metrics"][
        "attack_angle_deg"
    ]
    sigma = result.attack_angle_sigma_deg
    assert entry["sigma"] == pytest.approx(sigma)
    assert entry["range"] == pytest.approx(
        [
            result.attack_angle_deg - RANGE_SIGMAS * sigma,
            result.attack_angle_deg + RANGE_SIGMAS * sigma,
        ],
        abs=0.01,
    )
