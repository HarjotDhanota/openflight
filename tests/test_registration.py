"""In-situ registration: a constant datum is found, noise is not mistaken for
one, refusals carry their reason, and a bumped unit is noticed."""

from __future__ import annotations

import numpy as np
import pytest

from openflight.registration import (
    AZIMUTH,
    ELEVATION,
    MIN_SHOTS,
    Registration,
    RegistrationSample,
    RegistrationTracker,
    register,
    register_axis,
)


def _samples(elevation, azimuth=None):
    azimuth = azimuth if azimuth is not None else [None] * len(elevation)
    return [
        RegistrationSample(shot=i, elevation_delta_deg=e, azimuth_delta_deg=a)
        for i, (e, a) in enumerate(zip(elevation, azimuth, strict=True))
    ]


class TestRegisterAxis:
    def test_a_constant_offset_under_noise_is_recovered(self):
        rng = np.random.default_rng(7)
        values = 5.34 + rng.normal(0.0, 0.5, size=21)

        result = register_axis(values, ELEVATION)

        assert result.status == "registered"
        assert result.n == 21
        assert result.offset_deg == pytest.approx(5.34, abs=0.35)
        assert result.spread_deg == pytest.approx(0.5, abs=0.25)
        assert result.standard_error_deg == pytest.approx(1.2533 * result.spread_deg / np.sqrt(21))

    def test_one_wild_shot_cannot_own_the_median(self):
        values = [5.0] * 9 + [40.0]

        result = register_axis(values, ELEVATION)

        assert result.status == "registered"
        assert result.offset_deg == pytest.approx(5.0)
        assert result.spread_deg == pytest.approx(0.0)

    def test_too_few_shots_is_refused_by_name_but_still_reports_the_spread(self):
        result = register_axis([5.0, 5.4, 4.8], ELEVATION)

        assert result.status == "withheld_insufficient_shots"
        assert result.offset_deg is None
        assert result.n == 3
        assert result.spread_deg is not None
        assert "need" in result.reason

    def test_shot_by_shot_disagreement_is_not_a_datum(self):
        values = np.linspace(-6.0, 6.0, 12)

        result = register_axis(values, AZIMUTH)

        assert result.status == "withheld_unstable"
        assert result.offset_deg is None
        assert result.spread_deg > 2.0
        assert "not a constant datum" in result.reason

    def test_missing_and_nonfinite_values_are_ignored(self):
        values = [5.0, None, float("nan"), 5.2, float("inf")] + [5.1] * 6

        result = register_axis(values, ELEVATION)

        assert result.n == 8
        assert result.status == "registered"

    def test_no_shots_at_all(self):
        result = register_axis([], ELEVATION)

        assert result.status == "withheld_insufficient_shots"
        assert result.n == 0
        assert result.spread_deg is None

    def test_the_gates_are_tunable(self):
        values = [5.0, 5.5, 4.5, 5.2]

        assert register_axis(values, ELEVATION, min_shots=4).status == "registered"
        assert (
            register_axis(values, ELEVATION, min_shots=4, max_spread_deg=0.1).status
            == "withheld_unstable"
        )


class TestRegister:
    def test_both_axes_are_registered_independently(self):
        samples = _samples(elevation=[5.3] * 10, azimuth=[-1.2] * 10)

        result = register(samples)

        assert isinstance(result, Registration)
        assert result.n_samples == 10
        assert result.elevation.offset_deg == pytest.approx(5.3)
        assert result.azimuth.offset_deg == pytest.approx(-1.2)
        assert result.axis(ELEVATION) is result.elevation
        assert result.axis(AZIMUTH) is result.azimuth

    def test_an_axis_no_shot_measured_is_withheld_while_the_other_registers(self):
        samples = _samples(elevation=[5.3] * 10)

        result = register(samples)

        assert result.elevation.status == "registered"
        assert result.azimuth.status == "withheld_insufficient_shots"
        assert result.azimuth.n == 0

    def test_unknown_axis_names_are_rejected(self):
        with pytest.raises(ValueError, match="unknown axis"):
            RegistrationSample(shot=1).delta("roll")
        with pytest.raises(ValueError, match="unknown axis"):
            register(_samples([5.0] * 8)).axis("roll")


class TestRegistrationTracker:
    def test_the_estimate_refreshes_every_shot(self):
        tracker = RegistrationTracker()

        for shot in range(MIN_SHOTS - 1):
            current = tracker.add(RegistrationSample(shot, elevation_delta_deg=5.0))
            assert current.elevation.status == "withheld_insufficient_shots"
        current = tracker.add(RegistrationSample(MIN_SHOTS, elevation_delta_deg=5.0))

        assert current.elevation.status == "registered"
        assert len(tracker) == MIN_SHOTS

    def test_a_bumped_unit_is_noticed_only_after_enough_shots_agree(self):
        tracker = RegistrationTracker(jump_shots=5, jump_deg=2.0)
        for shot in range(12):
            tracker.add(RegistrationSample(shot, elevation_delta_deg=5.0))
        assert not tracker.jump(ELEVATION)

        # One stray shot is not a bump.
        tracker.add(RegistrationSample(12, elevation_delta_deg=9.0))
        assert not tracker.jump(ELEVATION)

        # Five in a row at the new datum are.
        for shot in range(13, 17):
            tracker.add(RegistrationSample(shot, elevation_delta_deg=9.0))
        assert tracker.jump(ELEVATION)
        assert not tracker.jump(AZIMUTH), "an axis with no data cannot jump"

    def test_a_jump_is_not_called_before_the_base_is_registered(self):
        tracker = RegistrationTracker(jump_shots=3)
        for shot in range(6):
            tracker.add(RegistrationSample(shot, elevation_delta_deg=5.0 if shot < 3 else 9.0))

        assert not tracker.jump(ELEVATION)

    def test_the_window_forgets_the_old_datum(self):
        tracker = RegistrationTracker(window=13, jump_shots=5)
        for shot in range(13):
            tracker.add(RegistrationSample(shot, elevation_delta_deg=5.0))
        for shot in range(13, 26):
            tracker.add(RegistrationSample(shot, elevation_delta_deg=9.0))

        assert tracker.current().elevation.offset_deg == pytest.approx(9.0)
        assert not tracker.jump(ELEVATION), "once the window is all new datum, it is the datum"

    def test_a_window_too_small_for_its_own_tests_is_refused(self):
        with pytest.raises(ValueError, match="window"):
            RegistrationTracker(window=5)
