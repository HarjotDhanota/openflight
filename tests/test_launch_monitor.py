"""Tests for launch_monitor module."""

from datetime import datetime

import pytest

from openflight.clubs import ClubType
from openflight.launch_monitor import (
    SMASH_RADIAL_RATIO,
    Shot,
    adjust_carry_for_launch_angle,
    estimate_carry_distance,
)


class TestEstimateCarryDistance:
    """Tests for the carry distance estimation function."""

    def test_driver_150_mph(self):
        """150 mph driver should be around 265 yards."""
        carry = estimate_carry_distance(150, ClubType.DRIVER)
        assert 250 <= carry <= 280

    def test_driver_100_mph(self):
        """100 mph driver should be around 136 yards."""
        carry = estimate_carry_distance(100, ClubType.DRIVER)
        assert 130 <= carry <= 145

    def test_driver_180_mph(self):
        """180 mph driver (pro level) should be around 335 yards."""
        carry = estimate_carry_distance(180, ClubType.DRIVER)
        assert 320 <= carry <= 350

    def test_same_ball_speed_same_carry_regardless_of_club(self):
        """Ball speed alone determines base carry — club differences come from spin/launch."""
        driver_carry = estimate_carry_distance(120, ClubType.DRIVER)
        iron_carry = estimate_carry_distance(120, ClubType.IRON_7)
        assert driver_carry == iron_carry

    def test_low_speed_extrapolation(self):
        """Very low speeds should still return positive distance."""
        carry = estimate_carry_distance(50, ClubType.DRIVER)
        assert carry > 0
        assert carry < 100

    def test_high_speed_extrapolation(self):
        """Very high speeds should extrapolate reasonably."""
        carry = estimate_carry_distance(220, ClubType.DRIVER)
        assert carry > 400
        assert carry < 500

    def test_driver_carry_monotonic_between_160_and_167_mph(self):
        """Driver carry should not decrease as ball speed increases in this range."""
        carries = [estimate_carry_distance(speed, ClubType.DRIVER) for speed in range(160, 168)]
        for previous, current in zip(carries, carries[1:]):
            assert current >= previous


class TestShot:
    """Tests for the Shot dataclass."""

    def test_basic_shot_creation(self):
        """Create a basic shot with ball speed only."""
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
        assert shot.ball_speed_mph == 150.0
        assert shot.club == ClubType.DRIVER  # default

    def test_shot_with_club_speed(self):
        """Shot with both ball and club speed."""
        shot = Shot(
            ball_speed_mph=150.0,
            club_speed_mph=103.0,
            timestamp=datetime.now(),
        )
        assert shot.ball_speed_mph == 150.0
        assert shot.club_speed_mph == 103.0

    def test_smash_factor_calculation(self):
        """Smash factor should be ball_speed / club_speed."""
        shot = Shot(
            ball_speed_mph=150.0,
            club_speed_mph=100.0,
            timestamp=datetime.now(),
        )
        assert shot.smash_factor == 1.5

    def test_smash_factor_none_without_club_speed(self):
        """Smash factor should be None if no club speed."""
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
        assert shot.smash_factor is None

    @pytest.mark.parametrize("club", [0.0, -100.0])
    def test_smash_factor_none_without_a_positive_club_speed(self, club):
        """D37: TrackMan's ratio needs a positive club speed; none is filled in."""
        shot = Shot(ball_speed_mph=150.0, club_speed_mph=club, timestamp=datetime.now())
        assert shot.smash_factor is None

    def test_smash_factor_uses_the_cited_definition(self, monkeypatch):
        """D37: Shot.smash_factor is metric_definitions.smash_factor, not a copy."""
        from openflight import metric_definitions

        calls = []
        monkeypatch.setattr(
            metric_definitions,
            "smash_factor",
            lambda ball, club: calls.append((ball, club)) or 9.99,
        )
        shot = Shot(ball_speed_mph=150.0, club_speed_mph=100.0, timestamp=datetime.now())
        assert shot.smash_factor == 9.99
        assert calls == [(150.0, 100.0)]

    def test_smash_factor_status_is_unlabelled_by_default(self):
        shot = Shot(ball_speed_mph=150.0, club_speed_mph=100.0, timestamp=datetime.now())
        assert shot.smash_factor_status is None
        assert shot.to_dict()["smash_factor_status"] is None

    def test_a_radial_ratio_label_rides_with_its_smash_factor(self):
        """D21/D37: radial ball over radial club speed is labelled, not passed off as smash."""
        shot = Shot(
            ball_speed_mph=150.0,
            club_speed_mph=100.0,
            timestamp=datetime.now(),
            smash_factor_status=SMASH_RADIAL_RATIO,
        )
        assert SMASH_RADIAL_RATIO == "radial_ratio"
        assert shot.to_dict()["smash_factor"] == 1.5
        assert shot.to_dict()["smash_factor_status"] == "radial_ratio"

    def test_no_smash_factor_carries_no_smash_label(self):
        shot = Shot(
            ball_speed_mph=150.0,
            timestamp=datetime.now(),
            smash_factor_status=SMASH_RADIAL_RATIO,
        )
        assert shot.to_dict()["smash_factor"] is None
        assert shot.to_dict()["smash_factor_status"] is None

    def test_speed_unit_conversion(self):
        """Test mph to m/s conversion."""
        shot = Shot(
            ball_speed_mph=100.0,
            club_speed_mph=70.0,
            timestamp=datetime.now(),
        )
        # 100 mph ~= 44.7 m/s
        assert 44.5 <= shot.ball_speed_ms <= 44.9
        assert 31.0 <= shot.club_speed_ms <= 31.5

    def test_estimated_carry_same_at_same_ball_speed(self):
        """Base carry depends only on ball speed, not club type."""
        driver_shot = Shot(
            ball_speed_mph=140.0,
            timestamp=datetime.now(),
            club=ClubType.DRIVER,
        )
        iron_shot = Shot(
            ball_speed_mph=140.0,
            timestamp=datetime.now(),
            club=ClubType.IRON_7,
        )
        assert driver_shot.estimated_carry_yards == iron_shot.estimated_carry_yards

    def test_carry_range(self):
        """Carry range should be ±10% of estimate."""
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
        low, high = shot.estimated_carry_range
        estimate = shot.estimated_carry_yards

        assert low == pytest.approx(estimate * 0.90, rel=0.01)
        assert high == pytest.approx(estimate * 1.10, rel=0.01)

    def test_carry_adjusts_for_launch_angle(self):
        """Shot with launch angle should adjust carry distance."""
        shot_no_angle = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
        shot_low_angle = Shot(
            ball_speed_mph=150.0,
            timestamp=datetime.now(),
            launch_angle_vertical=7.0,  # well below 11 optimal for driver
            launch_angle_confidence=1.0,
        )
        assert shot_low_angle.estimated_carry_yards < shot_no_angle.estimated_carry_yards

    def test_carry_unchanged_without_launch_angle(self):
        """Shot without launch angle should use current behavior."""
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
        base = estimate_carry_distance(150.0, ClubType.DRIVER)
        assert shot.estimated_carry_yards == base

    def test_carry_range_tighter_with_angle(self):
        """Shot with launch angle should have tighter carry range."""
        shot_no_angle = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
        shot_angle = Shot(
            ball_speed_mph=150.0,
            timestamp=datetime.now(),
            launch_angle_vertical=11.0,
            launch_angle_confidence=0.5,
        )
        no_angle_spread = (
            shot_no_angle.estimated_carry_range[1] - shot_no_angle.estimated_carry_range[0]
        )
        angle_spread = shot_angle.estimated_carry_range[1] - shot_angle.estimated_carry_range[0]
        assert angle_spread < no_angle_spread


class TestAdjustCarryForLaunchAngle:
    """Tests for launch-angle-based carry distance adjustment."""

    def test_optimal_angle_no_penalty(self):
        """Optimal launch angle should return base carry unchanged."""
        result = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=11.0, club=ClubType.DRIVER, confidence=0.5
        )
        assert result == pytest.approx(250, abs=1)

    def test_low_angle_reduces_carry(self):
        """Below-optimal launch angle should reduce carry."""
        result = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=7.0, club=ClubType.DRIVER, confidence=1.0
        )
        # 4 degrees low * 2.0 yards/deg = -8 yards
        assert result < 250
        assert result == pytest.approx(242, abs=1)

    def test_high_angle_reduces_carry(self):
        """Above-optimal launch angle should reduce carry (less severe)."""
        result = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=16.0, club=ClubType.DRIVER, confidence=1.0
        )
        # 5 degrees high * 1.5 yards/deg = -7.5 yards
        assert result < 250
        assert result == pytest.approx(242.5, abs=1)

    def test_confidence_scaling(self):
        """Low confidence should reduce the adjustment magnitude."""
        full_conf = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=7.0, club=ClubType.DRIVER, confidence=1.0
        )
        low_conf = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=7.0, club=ClubType.DRIVER, confidence=0.2
        )
        assert low_conf > full_conf
        assert low_conf < 250

    def test_penalty_capped_at_10_percent(self):
        """Carry penalty should never exceed 10% of base carry."""
        result = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=0.0, club=ClubType.DRIVER, confidence=1.0
        )
        assert result >= 250 * 0.90

    def test_iron_optimal_angle(self):
        """Iron clubs should use their own optimal launch angle."""
        result = adjust_carry_for_launch_angle(
            base_carry=150, launch_angle=20.5, club=ClubType.IRON_7, confidence=0.5
        )
        assert result == pytest.approx(150, abs=1)


class TestMultiObjectReporting:
    """Tests for multi-object radar configuration."""

    def test_set_num_reports_single_digit(self):
        """set_num_reports should use On format for 1-9."""
        from openflight.ops243 import OPS243Radar

        radar = OPS243Radar.__new__(OPS243Radar)
        radar.serial = None

        # Verify the method exists and handles single digits
        # Can't test actual command without hardware, but method should not raise
        assert hasattr(radar, "set_num_reports")

    def test_direction_constants(self):
        """Verify direction enum values."""
        from openflight.ops243 import Direction

        assert Direction.INBOUND.value == "inbound"
        assert Direction.OUTBOUND.value == "outbound"
        assert Direction.UNKNOWN.value == "unknown"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_launch_angle_is_no_launch_angle_for_carry(bad):
    """Task 3 fix round (I1): a non-finite value is a missing value."""
    from datetime import datetime as _dt

    with_bad = Shot(ball_speed_mph=140.0, timestamp=_dt(2026, 1, 1), launch_angle_vertical=bad)
    with_none = Shot(ball_speed_mph=140.0, timestamp=_dt(2026, 1, 1))
    assert with_bad.has_launch_angle is False
    assert with_bad.estimated_carry_yards == with_none.estimated_carry_yards
    assert with_bad.estimated_carry_range == with_none.estimated_carry_range


_DERIVED = (
    "smash_factor",
    "estimated_carry_yards",
    "estimated_carry_range",
    "has_launch_angle",
    "has_spin",
    "spin_quality",
)


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"]
)
@pytest.mark.parametrize(
    "field",
    [
        "launch_angle_vertical",
        "launch_angle_confidence",
        "club_speed_mph",
        "spin_rpm",
        "spin_confidence",
    ],
)
def test_every_derived_property_treats_a_non_finite_input_exactly_like_none(field, bad):
    """Fix round 2 (2a and its class): a non-finite value is a missing value."""
    from datetime import datetime as _dt

    measured = dict(
        ball_speed_mph=140.0,
        timestamp=_dt(2026, 1, 1),
        club_speed_mph=100.0,
        launch_angle_vertical=12.0,
        launch_angle_confidence=0.8,
        spin_rpm=2600.0,
        spin_confidence=0.9,
    )
    with_bad = Shot(**{**measured, field: bad})
    with_none = Shot(**{**measured, field: None})
    for name in _DERIVED:
        assert getattr(with_bad, name) == getattr(with_none, name), name


def test_a_zero_launch_confidence_keeps_its_existing_carry_default():
    from datetime import datetime as _dt

    def carry(confidence):
        return Shot(
            ball_speed_mph=140.0,
            timestamp=_dt(2026, 1, 1),
            launch_angle_vertical=12.0,
            launch_angle_confidence=confidence,
        ).estimated_carry_yards

    assert carry(0.0) == carry(None) == carry(0.2)


# --- summarize_shots: a non-finite value is a missing value (final review A-I1) ---


def _summary_shot(ball=150.0, club=100.0, spin=3000.0):
    return Shot(
        ball_speed_mph=ball,
        club_speed_mph=club,
        spin_rpm=spin,
        timestamp=datetime(2026, 10, 12, 9, 0, 0),
        club=ClubType.DRIVER,
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["ball", "club", "spin"])
def test_summarize_shots_skips_a_non_finite_value_in_every_averaged_field(field, bad):
    """One NaN shot must never make the session stats NaN (strict JSON, browser keeps parsing)."""
    import json
    import math

    from openflight.launch_monitor import summarize_shots

    good_a = _summary_shot(ball=140.0, club=100.0, spin=3000.0)
    good_b = _summary_shot(ball=160.0, club=110.0, spin=5000.0)
    broken = _summary_shot(**{field: bad})
    summary = summarize_shots([good_a, broken, good_b], mode="rolling-buffer")

    json.dumps(summary, allow_nan=False)
    for key, value in summary.items():
        if isinstance(value, float):
            assert math.isfinite(value), key
    assert summary["shot_count"] == 3
    if field == "ball":
        assert summary["avg_ball_speed"] == 150.0
        assert summary["max_ball_speed"] == 160.0
        assert summary["min_ball_speed"] == 140.0
        assert summary["avg_carry_est"] == pytest.approx(
            (good_a.estimated_carry_yards + good_b.estimated_carry_yards) / 2
        )
    if field == "club":
        assert summary["avg_club_speed"] == 105.0
        assert summary["avg_smash_factor"] == pytest.approx(
            (good_a.smash_factor + good_b.smash_factor) / 2
        )
    if field == "spin":
        assert summary["avg_spin_rpm"] == 4000.0
        # a non-finite spin is not a detected spin
        assert summary["spin_detection_rate"] == pytest.approx(2 / 3)


def test_summarize_shots_with_no_finite_ball_speed_reports_missing_not_zero():
    import json
    import math

    from openflight.launch_monitor import summarize_shots

    summary = summarize_shots(
        [_summary_shot(ball=math.nan, club=math.nan, spin=math.nan)], mode="rolling-buffer"
    )
    json.dumps(summary, allow_nan=False)
    assert summary["shot_count"] == 1
    for key in (
        "avg_ball_speed",
        "max_ball_speed",
        "min_ball_speed",
        "std_dev",
        "avg_club_speed",
        "avg_smash_factor",
        "avg_carry_est",
        "avg_spin_rpm",
    ):
        assert summary[key] is None, key
    assert summary["spin_detection_rate"] == 0.0


def test_summarize_shots_std_dev_needs_two_finite_ball_speeds():
    import math

    from openflight.launch_monitor import summarize_shots

    one_finite = summarize_shots([_summary_shot(ball=150.0), _summary_shot(ball=math.nan)], "x")
    assert one_finite["std_dev"] is None
    two_finite = summarize_shots([_summary_shot(ball=140.0), _summary_shot(ball=160.0)], "x")
    assert two_finite["std_dev"] == pytest.approx(14.142135623730951)
