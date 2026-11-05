"""G2 follow-up (4 Nov 2026): a physical-plausibility label beside the sigma tiers.

The sigma-only tiers (C2) promise too much where the positions are precise but the framing
is wrong: the Oct 24 merged-tree replay read iron attack angles of -15 to -30 deg as 'high'
(oct24-13 -19.2, oct24-09 -17.0, oct24-30 -19.3) at 1280x800, where the range test used to
downgrade them. An attack angle outside PLAUSIBLE_ATTACK_RANGE_DEG (-12..+8) or a club path
outside PLAUSIBLE_PATH_RANGE_DEG (-25..+25) now carries ``plausibility: "implausible"`` with
the bound named, and its tier is capped at low. The value is never blocked (owner: never
park, label by the weakest input; a small positive attack angle can be correct).
"""

from __future__ import annotations

import numpy as np
import pytest

import openflight.camera.club_delivery as cd
from openflight.camera.club_delivery import (
    ApproachPairEstimate,
    CameraDeliveryGeometry,
    estimate_chained_delivery,
)
from openflight.camera.club_motion import ReferenceBall
from tests.test_camera_club_contact_tangent import _shot1_club


def _scene(monkeypatch, *, path_deg: float, attack_deg: float):
    """A clip whose every window reads ``path_deg`` / ``attack_deg`` with a 0.3 deg sigma."""
    ball = ReferenceBall(10.0, 10.0, 12.0, 120)
    monkeypatch.setattr(cd, "_detect_impact_index", lambda _frames, _ball, trigger_index: 40)
    monkeypatch.setattr(
        cd, "_clubhead_pair_tracks", lambda *_args, **_kwargs: (np.zeros((12, 2, 2)), 5.0)
    )
    monkeypatch.setattr(
        cd,
        "_delivery_from_feature_pair",
        lambda *_a, **_k: ApproachPairEstimate(
            path_deg, attack_deg, 1.0, 1.0, 12, attack_angle_sigma_deg=0.3
        ),
    )
    from types import SimpleNamespace  # noqa: PLC0415

    from openflight.iwr6843.club import ClubRangeEvidence  # noqa: PLC0415

    evidence = ClubRangeEvidence(
        track=SimpleNamespace(range_at=lambda t_s, _res: 1.2 + t_s),
        geometry=SimpleNamespace(range_res_m=0.046875),
        impact_t_s=0.5,
    )
    stamps = 1_000_000_000 + np.arange(60, dtype=np.int64) * 3_472_000
    return estimate_chained_delivery(
        np.full((60, 20, 20), 150, dtype=np.uint8),
        stamps,
        trigger_index=40,
        range_evidence=evidence,
        geometry=CameraDeliveryGeometry(
            camera_height_m=0.2032,
            radar_height_m=0.1524,
            tee_range_m=1.524,
            ball_height_m=0.04,
            image_width_px=20,
            image_height_px=20,
        ),
        ops_club_speed_mph=80.0,
        reference_ball=ball,
    )


def test_a_minus_20_deg_iron_attack_angle_is_reported_implausible_and_capped_low(monkeypatch):
    result = _scene(monkeypatch, path_deg=3.0, attack_deg=-20.0)
    assert result.attack_angle_deg == pytest.approx(-20.0)  # reported, never blocked
    flag = result.plausibility["attack_angle_deg"]
    assert flag["plausibility"] == "implausible"
    assert tuple(flag["bound_deg"]) == cd.PLAUSIBLE_ATTACK_RANGE_DEG == (-12.0, 8.0)
    assert "-12" in flag["note"] and "+8" in flag["note"]
    assert result.attack_confidence_tier == "low"  # its 0.3 deg sigma alone said high
    assert result.club_path_deg == pytest.approx(3.0)
    assert "club_path_deg" not in result.plausibility
    assert result.status != "approach_high"


def test_a_path_beyond_25_deg_is_implausible_and_capped_low(monkeypatch):
    result = _scene(monkeypatch, path_deg=30.0, attack_deg=-4.0)
    assert result.club_path_deg == pytest.approx(30.0)
    assert result.plausibility["club_path_deg"]["plausibility"] == "implausible"
    assert tuple(result.plausibility["club_path_deg"]["bound_deg"]) == (-25.0, 25.0)
    assert result.path_confidence_tier == "low"
    assert "attack_angle_deg" not in result.plausibility


def test_a_small_positive_attack_angle_is_plausible(monkeypatch):
    result = _scene(monkeypatch, path_deg=3.0, attack_deg=6.0)
    assert result.plausibility == {}
    assert result.attack_confidence_tier == "high"


def test_oct27_shot1_stays_unflagged():
    case = _shot1_club()
    result = cd.estimate_chained_delivery(case["frames"], case["host"], **case["kwargs"])
    assert -12.0 <= result.attack_angle_deg <= 8.0
    assert result.plausibility == {}
