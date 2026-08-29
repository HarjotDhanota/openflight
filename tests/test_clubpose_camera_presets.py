"""No preset may claim to describe hardware we own and then describe another.

`camera_presets()` shipped an `A0` entry whose `physical_status` read
``existing_320x200_plus_10us_strobe`` -- i.e. this is the camera in the kiosk --
with fx = 1033 px and a 0.656 px/mm plate scale. The camera in the kiosk has
fx = 466.7 px and a 0.295 px/mm plate scale, so A0 was wrong by a factor of
2.2 in the quantity that converts pixels to millimetres. Nothing in `src/`,
`scripts/` or `tests/` called it, which is the only reason it never produced a
wrong number.

The remaining presets are explicitly hypothetical -- a plate-scale sensitivity
study and an unrun experimental mode -- and say so in their own
`physical_status`. This file holds that line: a preset either declares itself
hypothetical, or it agrees with `measured_camera()`.
"""

from __future__ import annotations

import pytest

from openflight.camera.clubpose.fit import measured_camera
from openflight.camera.clubpose.projection import camera_presets

# A preset with any other `physical_status` is asserting it is real hardware.
HYPOTHETICAL_STATUSES = {
    "plate_scale_sensitivity_only",
    "experimental_gate_b1_not_run",
}


def test_the_a0_preset_is_gone():
    assert "A0" not in camera_presets()


def test_no_preset_claims_to_be_the_shipped_camera_while_disagreeing_with_it():
    measured = measured_camera()
    for name, preset in camera_presets().items():
        if preset.physical_status in HYPOTHETICAL_STATUSES:
            continue
        assert preset.fx == pytest.approx(measured.fx, rel=0.02), (
            f"preset {name} claims real hardware ({preset.physical_status}) "
            f"but its fx {preset.fx} is not the measured {measured.fx}"
        )


def test_every_remaining_preset_carries_a_camera_centre():
    """A preset without a centre is a camera without a position."""
    for name, preset in camera_presets().items():
        assert len(preset.center_world_mm) == 3, name
        assert preset.center_world[0] < 0.0, name
