"""What the tester's ladder says about light: the kiosk's seed in this light, and a label."""

from __future__ import annotations

import math
from typing import Any, Mapping

from . import swing_exposure
from .light_judging import BRIGHT_LIGHT_CAUSES, DARK_LIGHT_CAUSES
from .swing_exposure import (
    SensorLimits,
    blur_ceiling_us,
    build_envelope,
    choose_setting,
    load_config,
    mode_signal_factor,
)


def mode_label(arm_id: str, config: Mapping[str, Any] | None = None) -> str:
    config = config if config is not None else load_config()
    return str(config["modes"][arm_id]["label"]).replace("x", "×")


def kiosk_would_use(
    light_index_1280: float | None,
    arm_id: str,
    limits: SensorLimits | None,
    *,
    config: Mapping[str, Any] | None = None,
) -> dict:
    """The setting the kiosk's policy would seed for this light, in one mode.

    ``light_index_1280`` is the setup's 1280x800 ball index (DN above black per us x
    gain); the mode's measured signal factor carries it across. Without the sensor's
    recorded minimum for the mode, or without a light index, it says so and predicts
    nothing.
    """
    config = config if config is not None else load_config()
    base = {"arm_id": arm_id, "mode": mode_label(arm_id, config), "label": "estimated"}
    if light_index_1280 is None or not math.isfinite(light_index_1280) or light_index_1280 <= 0:
        return {**base, "status": "not_measured", "reason": "the setup measured no light"}
    if limits is None:
        return {
            **base,
            "status": "needs_sensor_minimum",
            "reason": (
                "the sensor's shortest exposure for this mode has not been read yet "
                "(start the camera in this mode once)"
            ),
        }
    mode = config["modes"][arm_id]
    envelope = build_envelope(
        limits, blur_ceiling_us=blur_ceiling_us(config, arm_id), fps=float(mode["fps"])
    )
    index = float(light_index_1280) * mode_signal_factor(config, arm_id)
    choice = choose_setting(index, index, envelope)
    setting = choice.setting
    return {
        **base,
        "status": choice.status,
        "exposure_us": setting.exposure_us,
        "gain": setting.gain,
        "predicted_signal_dn": round(choice.predicted_signal_dn, 2),
        "light_index_dn_per_us_gain": index,
        "blur_ceiling_us": envelope.blur_ceiling_us,
        "sensor_min_us": limits.exposure_min_us,
        "gain_cap": envelope.gain_cap,
        "text": (
            f"kiosk would use: {base['mode']} · {setting.exposure_us} µs × gain {round(setting.gain, 2):g}"
        ),
    }


def light_label(cause: str | None) -> str | None:
    """The tester-facing label for a light cause: what to do about it (13 Oct)."""
    if cause in BRIGHT_LIGHT_CAUSES:
        return swing_exposure.TOO_BRIGHT_LABEL
    if cause in DARK_LIGHT_CAUSES:
        return swing_exposure.TOO_DARK_LABEL
    return None
