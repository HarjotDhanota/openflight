"""The hitting zone's exposure rating for the high-speed OV9281 camera.

The swing exposure itself is chosen by ``swing_exposure.SwingExposurePolicy``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np

ExposureQualityStatus = Literal[
    "good",
    "marginal",
    "too_dark",
    "too_bright",
    "unavailable",
]
ExposureRecommendation = Literal["brighter", "darker", "hold"]
ZoneSource = Literal["placement_box", "fixed"]
# The fallback hitting zone when no placement box was confirmed.
FIXED_ZONE_FRACTIONS = (0.2, 0.45, 0.8, 0.9)  # x0, y0, x1, y1


@dataclass(frozen=True)
class ExposureObservation:
    """Brightness and contrast measurements from the impact-zone ROI."""

    sample_available: bool
    status: ExposureQualityStatus
    recommendation: ExposureRecommendation
    message: str
    p10: float | None = None
    median: float | None = None
    p90: float | None = None
    contrast: float | None = None
    clipped_pct: float | None = None
    dark_pct: float | None = None
    zone_source: ZoneSource = "fixed"
    zone_box_px: tuple[int, int, int, int] | None = None

    @property
    def acceptable(self) -> bool:
        """Whether camera-derived metrics may use frames with this exposure."""
        return self.status in {"good", "marginal"}

    def to_dict(self) -> dict:
        """Serialize the observation for logs and the operator UI."""
        payload = asdict(self)
        if self.zone_box_px is not None:
            payload["zone_box_px"] = list(self.zone_box_px)
        return payload


# The resting ball is ~0.4 % of a 320x200 frame, so a saturated ball would pass
# a 2 % clip gate; its diameter sets range, so keep its peak off the rail.
BALL_CLIP_PCT = 0.1


def hitting_zone(
    height: int, width: int, zone_box: Sequence[int] | None = None
) -> tuple[tuple[int, int, int, int], ZoneSource]:
    """The hitting zone in this frame's pixels (x0, y0, x1, y1) and where it came from.

    ``zone_box`` is the confirmed placement box in this mode's pixels, clipped to the frame.
    """
    if zone_box is not None:
        x0, y0, x1, y1 = (int(value) for value in zone_box)
        return (
            (
                min(max(x0, 0), width),
                min(max(y0, 0), height),
                min(max(x1, 0), width),
                min(max(y1, 0), height),
            ),
            "placement_box",
        )
    fx0, fy0, fx1, fy1 = FIXED_ZONE_FRACTIONS
    return (
        (round(width * fx0), round(height * fy0), round(width * fx1), round(height * fy1)),
        "fixed",
    )


def measure_exposure(
    image: np.ndarray, zone_box: Sequence[int] | None = None
) -> ExposureObservation:
    """Rate exposure in the hitting zone of one grayscale frame."""
    pixels = np.asarray(image)
    if pixels.ndim != 2 or not pixels.size:
        return ExposureObservation(
            sample_available=False,
            status="unavailable",
            recommendation="hold",
            message="Waiting for a camera frame",
        )

    height, width = pixels.shape
    (x0, y0, x1, y1), zone_source = hitting_zone(height, width, zone_box)
    region = pixels[y0:y1, x0:x1]
    zone = {"zone_source": zone_source, "zone_box_px": (x0, y0, x1, y1)}
    if not region.size:
        return ExposureObservation(
            sample_available=False,
            status="unavailable",
            recommendation="hold",
            message="Impact-area exposure region is unavailable",
            **zone,
        )

    p10, median, p90 = (float(np.percentile(region, value)) for value in (10, 50, 90))
    clipped_pct = float(np.mean(region >= 250) * 100.0)
    dark_pct = float(np.mean(region <= 12) * 100.0)
    contrast = p90 - p10

    if clipped_pct >= 8.0 or median >= 205.0:
        status: ExposureQualityStatus = "too_bright"
        recommendation: ExposureRecommendation = "darker"
        message = "Impact area is clipping; reducing exposure"
    elif p90 < 75.0 or median < 28.0 or contrast < 30.0:
        status = "too_dark"
        recommendation = "brighter"
        if p90 <= 12.0 and dark_pct >= 90.0:
            message = "Camera view is nearly black; check lighting and the lens cover"
        else:
            message = "Club contrast is low; increasing exposure"
    elif clipped_pct <= BALL_CLIP_PCT and 45.0 <= median <= 180.0 and p90 >= 100.0:
        status = "good"
        recommendation = "hold"
        message = "Impact-area exposure and contrast look good"
    else:
        status = "marginal"
        recommendation = "darker" if clipped_pct > BALL_CLIP_PCT or median > 180.0 else "brighter"
        message = (
            "A ball-sized highlight is clipping; reducing exposure"
            if BALL_CLIP_PCT < clipped_pct <= 2.0
            else f"Exposure is usable; a {recommendation} setting may improve contrast"
        )

    return ExposureObservation(
        sample_available=True,
        status=status,
        recommendation=recommendation,
        message=message,
        p10=round(p10, 1),
        median=round(median, 1),
        p90=round(p90, 1),
        contrast=round(contrast, 1),
        clipped_pct=round(clipped_pct, 2),
        dark_pct=round(dark_pct, 2),
        **zone,
    )
