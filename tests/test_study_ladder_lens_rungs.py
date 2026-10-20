"""The lens catalogue offers exactly the tester ladder's own rungs."""

import pytest

from openflight.camera import lens_profiles, study_ladder


@pytest.fixture(name="catalogue")
def _catalogue():
    return lens_profiles.load_catalogue()


def test_the_allowed_rungs_are_the_ladder_s_own(catalogue):
    for arm_id in ("arm5", "arm6"):
        ladder = [rung.exposure_us for rung in study_ladder.LADDER if rung.arm_id == arm_id]
        assert list(catalogue.allowed_ladder_us[arm_id]) == ladder
