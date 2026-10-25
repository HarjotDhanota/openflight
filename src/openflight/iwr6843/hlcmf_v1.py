"""HLCMF-v1, the IWR's late-flight horizontal bearing (a research comparator)."""

from __future__ import annotations

from openflight.iwr6843.lcmf import (
    HORIZONTAL_COHERENCE_MIN,
    PreparedLCMFCapture,
    _frame_balanced_horizontal_mean,
    _tx2_phase_snapshots,
)
from openflight.iwr6843.shot import ShotMeasurement


def tx2_horizontal_proxy(
    raw: bytes,
    shot: ShotMeasurement,
    *,
    tdm_sign: int,
    phase_reference_rad: float | None = None,
    phase_velocity_ms: float | None = None,
    prepared: PreparedLCMFCapture | None = None,
) -> tuple[float | None, float | None, str | None]:
    """HLCMF-v1: the ball's late-flight bearing, an offline comparator only.

    From behind a tee 1-2.5 m away the bearing reads about 0.5-0.7x the launch
    direction, so production uses ``_tx2_horizontal_motion``.
    """
    collected = _tx2_phase_snapshots(
        raw,
        shot,
        tdm_sign=tdm_sign,
        phase_velocity_ms=phase_velocity_ms,
        prepared=prepared,
    )
    if collected is None:
        return None, None, None
    _geometry, snapshots = collected
    if len(snapshots) < 6:
        return None, None, "hlcmf_v1_insufficient_tail_snapshots"
    angle_deg, coherence = _frame_balanced_horizontal_mean(
        [(frame, phase, weight) for frame, _time_s, phase, weight in snapshots],
        phase_reference_rad=phase_reference_rad,
    )
    if coherence < HORIZONTAL_COHERENCE_MIN:
        return angle_deg, coherence, "hlcmf_v1_low_coherence"
    return angle_deg, coherence, "hlcmf_v1_accepted"
