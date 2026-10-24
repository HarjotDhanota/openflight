"""The camera runtime a tester's kiosk runs: its study controls and trigger-evidence matching.

The free play ladder and the face photo set the controls live; the tester's shot gate
reads the setup evidence frozen at each accepted trigger (``trigger_evidence_for_shot``).
"""

from __future__ import annotations

import time
from copy import deepcopy
from typing import Any, Callable

from openflight.camera import swing_exposure as sx
from openflight.camera.capture_runtime import (
    CameraCaptureRuntime,
    _nearest_trigger_index,
    logger,
    replace_settings,
)


class TesterRuntimeMixin:
    """The tester's held and still controls and trigger evidence, on a camera runtime class."""

    def __init__(self, *args, **kwargs):
        self._admission_evidence: list[tuple[float, dict | None]] = []
        self._still_hold: tuple[int, float, str] | None = None
        super().__init__(*args, **kwargs)

    def _reset_capture_state(self) -> None:
        super()._reset_capture_state()
        self._admission_evidence = []

    def _gather_trigger_evidence(self, trigger_epoch: float) -> dict | None:
        evidence = super()._gather_trigger_evidence(trigger_epoch)
        with self._trigger_exposure_lock:
            self._admission_evidence.append((trigger_epoch, deepcopy(evidence)))
        return evidence

    def _metering_held(self) -> bool:
        return self._still_hold is not None

    def trigger_evidence_for_shot(self, impact_timestamp: float | None) -> dict | None:
        """Consume the immutable trigger evidence nearest one OPS shot."""
        if impact_timestamp is None:
            return None
        with self._trigger_exposure_lock:
            cutoff = impact_timestamp - self.settings.match_tolerance_s
            self._admission_evidence = [
                item for item in self._admission_evidence if item[0] >= cutoff
            ]
            index = _nearest_trigger_index(
                [item[0] for item in self._admission_evidence],
                impact_timestamp,
                self.settings.match_tolerance_s,
            )
            if index is None:
                return None
            _, evidence = self._admission_evidence.pop(index)
            return deepcopy(evidence)

    @property
    def sensor_limits(self) -> sx.SensorLimits | None:
        """This mode's exposure and gain limits as the camera reported them, or None."""
        return self._sensor_limits

    def _while_no_clip_is_collected(self, work: Callable[[], Any], timeout_s: float, what: str):
        """Run ``work`` under the trigger lock once no clip's tail is being collected.

        A clip's post-trigger frames keep the controls it was triggered at, and a
        trigger waits for a control change in progress (the swing exposure's rule).
        """
        deadline = time.monotonic() + timeout_s
        while True:
            with self._trigger_exposure_lock:
                if not self._ring.capture_busy:
                    return work()
            if time.monotonic() >= deadline:
                raise RuntimeError(f"a clip is still being collected; {what} waits for it")
            time.sleep(0.01)

    def apply_held_controls(self, exposure_us: int, gain: float, *, timeout_s: float = 3.0) -> dict:
        """Set held (manual) controls live for the next swing: free play's ladder rungs.

        The camera keeps streaming and the ring keeps its frames; nothing restarts.
        Refused while the automatic exposure owns the controls.
        """
        if self.settings.auto_exposure:
            raise RuntimeError("exposure is automatic: held controls would be overridden")
        return self._while_no_clip_is_collected(
            lambda: self.update_image_controls(exposure_us=exposure_us, gain=gain),
            timeout_s,
            "the new setting",
        )

    def hold_still_controls(self, exposure_us: int, gain: float, *, timeout_s: float = 3.0) -> dict:
        """Hold still-photo controls live until ``release_still_controls``.

        The swing exposure is not metered or moved meanwhile; a clip taken now
        records the ``still_photo`` purpose, so its metrics are withheld.
        """

        def hold() -> dict:
            if self._still_hold is None:
                self._still_hold = (
                    self.settings.exposure_us,
                    self.settings.gain,
                    self._controls_purpose,
                )
            return self.update_image_controls(
                exposure_us=exposure_us, gain=gain, purpose="still_photo"
            )

        return self._while_no_clip_is_collected(hold, timeout_s, "the still photo")

    def release_still_controls(self, *, timeout_s: float = 3.0) -> dict | None:
        """Put back the controls a still photo replaced, or None when none is held."""
        held = self._still_hold
        if held is None:
            return None
        exposure_us, gain, purpose = held

        def release() -> dict:
            applied = self.update_image_controls(
                exposure_us=exposure_us, gain=gain, purpose=purpose
            )
            self._still_hold = None
            return applied

        return self._while_no_clip_is_collected(release, timeout_s, "the swing setting")

    def begin_automatic_exposure(self, *, timeout_s: float = 3.0) -> dict:
        """Hand the controls to the swing exposure policy mid-session, seeded from the light.

        Free play's ladder ends here. The seed (the setup's light index, as at a
        startup) is applied live with no ring refill or restart, and the resting-ball
        re-check loop starts. Calling it again changes nothing.
        """
        running = self._swing_thread is not None and self._swing_thread.is_alive()
        if self.settings.auto_exposure and running:
            return self.auto_exposure_status()

        def seed() -> sx.SwingExposureDecision:
            replace_settings(self, auto_exposure=True)
            return self._seed_swing_exposure()

        decision = self._while_no_clip_is_collected(seed, timeout_s, "the automatic exposure")
        self._start_auto_exposure(seeded=decision)
        logger.info("[CAMERA] Automatic swing exposure started: %s", decision.message)
        return decision.to_dict()


class TesterCaptureRuntime(TesterRuntimeMixin, CameraCaptureRuntime):
    """``CameraCaptureRuntime`` with the tester's controls and evidence."""


def runtime_class(base: type) -> type:
    """The tester's runtime on top of the runtime class the kiosk would build."""
    if base is CameraCaptureRuntime:
        return TesterCaptureRuntime
    return type("TesterCaptureRuntime", (TesterRuntimeMixin, base), {})
