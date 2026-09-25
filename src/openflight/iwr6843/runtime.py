"""Runtime boundary joining TI capture to the frozen LCMF estimator."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import asdict, dataclass, field, replace

from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.club import ClubPathResult, ClubWindowPolicy, estimate_club_path
from openflight.iwr6843.lcmf import (
    LCMFResult,
    PreparedLCMFCapture,
    estimate_lcmf_v1,
    prepare_lcmf_capture,
)
from openflight.iwr6843.monitor import IWR6843Capture, IWR6843CaptureMonitor
from openflight.iwr6843.recovery import (
    RecoveryCandidate,
    RecoveryPrior,
    find_recovery_candidates,
    select_recovery_candidate,
)

logger = logging.getLogger(__name__)

# The ball estimate's measured tdm_sign_used takes priority; this only
# resolves the TDM sign for the club-path fallback when it is unavailable.
# "auto" has no fixed sign of its own, so it defaults to positive, same as
# this module's own tdm_sign_policy default.
_TDM_SIGN_BY_POLICY = {"positive": 1, "negative": -1, "auto": 1}

# TrackMan holdout tracks centered almost exactly on OPS speed. A larger
# disagreement is unusual enough to justify a bounded alternate-track pass,
# but not enough by itself to choose an angle.
OPS_TRACK_SPEED_TOLERANCE_FRAC = 0.15
OPS_GUIDED_MAX_CANDIDATES = 8
OPS_GUIDED_MIN_LAUNCH_DEG = 2.0
HORIZONTAL_CONFIDENCE_CEILING = 0.95


def horizontal_confidence_from(coherence: float | None) -> float:
    """Normalize HLCMF coherence exactly once for live and replay consumers."""
    if coherence is None:
        return 0.0
    return round(min(HORIZONTAL_CONFIDENCE_CEILING, max(0.0, float(coherence))), 3)


def _ops_candidate_rank(candidate: RecoveryCandidate) -> tuple[float, int, float]:
    """Rank truth-free range walks before the more expensive LCMF pass."""
    return (
        abs(candidate.speed_ratio - 1.0),
        -candidate.track.n_inliers,
        candidate.track.rms_bins,
    )


def _credible_ops_candidates(
    candidates: list[RecoveryCandidate],
) -> list[RecoveryCandidate]:
    """Return a small, deduplicated set of OPS-compatible range walks."""
    credible = [
        candidate
        for candidate in candidates
        if abs(candidate.speed_ratio - 1.0) <= OPS_TRACK_SPEED_TOLERANCE_FRAC
        and candidate.track.n_inliers >= 12
        and candidate.track.rms_bins <= 0.48
        and candidate.track.t_last - candidate.track.t_first >= 0.009
    ]
    credible.sort(key=_ops_candidate_rank)
    selected: list[RecoveryCandidate] = []
    seen: set[tuple[int, int]] = set()
    for candidate in credible:
        # RANSAC emits many nearly identical lines. Keep one representative
        # per approximately 1 mph / 1.5 ms speed-impact cell.
        key = (
            round(candidate.track.speed_mph),
            round(candidate.impact_s / 0.0015),
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(candidate)
        if len(selected) >= OPS_GUIDED_MAX_CANDIDATES:
            break
    return selected


def _recovery_result_rank(
    candidate: RecoveryCandidate,
    result: LCMFResult,
) -> tuple[bool, float, float, int, float]:
    """Combine OPS agreement with independent spatial-estimator evidence."""
    # A single channel can still be useful (shot 6 on 2026-08-14), but must
    # beat a corroborated candidate by a meaningful OPS-speed margin.
    single_channel_penalty = 0.03 if result.single_channel else 0.0
    spread = float(result.component_std_deg or 0.0)
    return (
        result.single_channel,
        abs(candidate.speed_ratio - 1.0) + single_channel_penalty + 0.01 * min(spread, 8.0),
        candidate.track.rms_bins,
        -result.n_frames,
        -candidate.track.n_inliers,
    )


def ops_guided_measurement(
    raw: bytes,
    calibration: Calibration,
    *,
    ball_speed_mph: float,
    club: str | None,
    baseline: LCMFResult,
    prepared: PreparedLCMFCapture,
    net_range_m: float | None,
    tx_order: str,
    tdm_sign_policy: str,
    horizontal_phase_reference_rad: float | None,
) -> LCMFResult:
    """Apply the production OPS-guided alternate-track selection without hardware."""
    if baseline is None or not hasattr(baseline, "track_speed_mph"):
        return baseline
    speed = baseline.track_speed_mph
    if baseline.accepted and speed is None:
        return replace(baseline, status="accepted_track_speed_warning")
    speed_error = (
        abs(speed / ball_speed_mph - 1.0)
        if speed is not None and ball_speed_mph > 0.0
        else float("inf")
    )
    if baseline.accepted and speed_error <= OPS_TRACK_SPEED_TOLERANCE_FRAC:
        return baseline
    try:
        candidates = _credible_ops_candidates(
            find_recovery_candidates(
                raw,
                calibration,
                ball_speed_mph=ball_speed_mph,
                net_range_m=net_range_m,
                prepared=prepared.vertical,
            )
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[IWR6843] OPS-guided track search failed: %s", error)
        return (
            replace(baseline, status="accepted_track_speed_warning")
            if baseline.accepted
            else baseline
        )
    recoveries: list[tuple[RecoveryCandidate, LCMFResult]] = []
    for candidate in candidates:
        result = estimate_lcmf_v1(
            raw,
            calibration,
            ball_speed_mph=ball_speed_mph,
            club=club,
            net_range_m=net_range_m,
            tx_order=tx_order,
            tdm_sign_policy=tdm_sign_policy,
            horizontal_phase_reference_rad=horizontal_phase_reference_rad,
            track_override=candidate.track,
            track_override_scope=candidate.scope,
            prepared=prepared,
        )
        if (
            result.accepted
            and result.n_frames >= 4
            and result.angle_deg is not None
            and result.angle_deg >= OPS_GUIDED_MIN_LAUNCH_DEG
        ):
            recoveries.append((candidate, result))
    if recoveries:
        _candidate, selected = min(
            recoveries, key=lambda item: _recovery_result_rank(item[0], item[1])
        )
        status = (
            "accepted_ops_guided_single_channel"
            if selected.single_channel
            else "accepted_ops_guided"
        )
        return replace(selected, status=status)
    return (
        replace(baseline, status="accepted_track_speed_warning") if baseline.accepted else baseline
    )


@dataclass(frozen=True)
class IWR6843ShotResult:
    """Capture transport result and optional angle measurement."""

    capture: IWR6843Capture | None
    measurement: LCMFResult | None
    club_path: ClubPathResult | None = None
    withheld_reason: str | None = None


def process_raw_capture(  # pylint: disable=too-many-arguments,too-many-locals
    raw: bytes,
    calibration: Calibration,
    *,
    ball_speed_mph: float,
    club: str | None,
    club_speed_mph: float | None,
    net_range_m: float | None,
    tx_order: str,
    tdm_sign_policy: str,
    azimuth_offset_deg: float,
    horizontal_phase_reference_rad: float | None,
    club_window_policy: ClubWindowPolicy,
    club_impact_correction_s: float,
    recovery_observations: list[tuple[float, float, float]],
) -> tuple[LCMFResult, ClubPathResult | None]:
    """Run the complete production ball and club estimator on frozen bytes."""
    prepared = prepare_lcmf_capture(raw)
    measurement = estimate_lcmf_v1(
        raw,
        calibration,
        ball_speed_mph=ball_speed_mph,
        club=club,
        net_range_m=net_range_m,
        tx_order=tx_order,
        tdm_sign_policy=tdm_sign_policy,
        horizontal_phase_reference_rad=horizontal_phase_reference_rad,
        prepared=prepared,
    )
    measurement = ops_guided_measurement(
        raw,
        calibration,
        ball_speed_mph=ball_speed_mph,
        club=club,
        baseline=measurement,
        prepared=prepared,
        net_range_m=net_range_m,
        tx_order=tx_order,
        tdm_sign_policy=tdm_sign_policy,
        horizontal_phase_reference_rad=horizontal_phase_reference_rad,
    )
    if measurement is not None and getattr(measurement, "horizontal_deg", None) is not None:
        measurement = replace(
            measurement,
            horizontal_deg=measurement.horizontal_deg + azimuth_offset_deg,
            horizontal_raw_deg=measurement.horizontal_deg,
        )
    if not club_speed_mph:
        return measurement, None
    ball_sign = getattr(measurement, "tdm_sign_used", None)
    fallback = ball_sign not in (-1, 1)
    impact_t_s = getattr(measurement, "impact_t_s", None)
    recovered_impact = False
    if impact_t_s is None and len(recovery_observations) >= 3:
        ratios, impacts, spans = zip(*recovery_observations)
        try:
            prior = RecoveryPrior.fit(list(ratios), list(impacts), list(spans))
            candidate = select_recovery_candidate(
                find_recovery_candidates(
                    raw,
                    calibration,
                    ball_speed_mph=ball_speed_mph,
                    net_range_m=net_range_m,
                ),
                prior,
            )
            impact_t_s = candidate.impact_s if candidate is not None else None
            recovered_impact = impact_t_s is not None
        except ValueError:
            impact_t_s = None
    if impact_t_s is not None:
        impact_t_s += club_impact_correction_s
    policy_sign = _TDM_SIGN_BY_POLICY.get(tdm_sign_policy, 1)
    club_path = estimate_club_path(
        raw,
        calibration,
        ops_club_speed_mph=club_speed_mph,
        impact_t_s=impact_t_s,
        aim_offset_deg=azimuth_offset_deg,
        phase_reference_rad=horizontal_phase_reference_rad,
        tdm_sign=policy_sign if fallback else ball_sign,
        window_policy=club_window_policy,
    )
    if recovered_impact:
        club_path.status = f"{club_path.status}_recovered_impact"
    if fallback:
        club_path.status = f"{club_path.status}_tdm_sign_fallback"
    return measurement, club_path


@dataclass
class IWR6843Runtime:
    """Configured TI hardware and estimator state for the server."""

    capture_monitor: IWR6843CaptureMonitor
    calibration: Calibration
    net_range_m: float | None
    tx_order: str = "normal"
    capture_timeout_s: float = 12.0
    azimuth_offset_deg: float = 0.0
    horizontal_phase_reference_rad: float | None = None
    tdm_sign_policy: str = "positive"
    club_window_policy: ClubWindowPolicy = field(default_factory=ClubWindowPolicy)
    # The ball-derived impact anchor runs ~2 ms late: on the 2026-08-07
    # 55-shot session, the club track's tee-contact error minimized at -2 ms
    # (0.026 m vs 0.035 m uncorrected), independently matching the camera's
    # ball-departure timing. Applied to the club estimators only; the ball
    # pipeline keeps its own anchor.
    club_impact_correction_s: float = -0.002
    # Accepted ball tracks establish a truth-free rolling prior. A rejected
    # vertical solution may use that prior to recover impact timing for the
    # independent experimental club search, never to publish vertical launch.
    recovery_observations: list[tuple[float, float, float]] = field(default_factory=list)
    calibration_provenance: dict | None = None
    radar_config_provenance: dict | None = None

    def replay_config_snapshot(
        self,
        *,
        ball_speed_mph: float | None = None,
        club: str | None = None,
        club_speed_mph: float | None = None,
        effective_tilt_deg: float | None = None,
    ) -> dict:
        """Freeze every primitive needed by the hardware-free estimator."""
        payload = {
            "schema_version": 1,
            "tee_range_status": (
                "unresolved"
                if getattr(self.calibration, "tee_range_m", None) is None
                else "configured"
            ),
            "net_range_m": self.net_range_m,
            "tx_order": self.tx_order,
            "tdm_sign_policy": self.tdm_sign_policy,
            "azimuth_offset_deg": self.azimuth_offset_deg,
            "horizontal_phase_reference_rad": self.horizontal_phase_reference_rad,
            "club_window_policy": asdict(self.club_window_policy),
            "club_impact_correction_s": self.club_impact_correction_s,
            "recovery_observations": [list(item) for item in self.recovery_observations],
            "source_revision_status": "captured_separately_in_session_runtime_provenance",
            "calibration": self.calibration_provenance,
            "radar_config": self.radar_config_provenance,
            "per_shot_inputs": {
                "ball_speed_mph": ball_speed_mph,
                "club": club,
                "club_speed_mph": club_speed_mph,
                "effective_tilt_deg": effective_tilt_deg,
            },
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return {**payload, "sha256": hashlib.sha256(encoded).hexdigest()}

    def _remember_recovery_observation(
        self, measurement: LCMFResult, ball_speed_mph: float
    ) -> None:
        if (
            not getattr(measurement, "accepted", False)
            or getattr(measurement, "impact_t_s", None) is None
            or getattr(measurement, "track_speed_mph", None) is None
            or getattr(measurement, "track_span_s", None) is None
        ):
            return
        self.recovery_observations.append(
            (
                float(measurement.track_speed_mph) / ball_speed_mph,
                float(measurement.impact_t_s),
                float(measurement.track_span_s),
            )
        )
        del self.recovery_observations[:-50]

    def _recover_impact_time(
        self, raw: bytes, calibration: Calibration, ball_speed_mph: float
    ) -> float | None:
        if len(self.recovery_observations) < 3:
            return None
        ratios, impacts, spans = zip(*self.recovery_observations)
        try:
            prior = RecoveryPrior.fit(list(ratios), list(impacts), list(spans))
            candidates = find_recovery_candidates(
                raw,
                calibration,
                ball_speed_mph=ball_speed_mph,
                net_range_m=self.net_range_m,
            )
        except ValueError:
            # Older/raw-ADC firmware formats cannot run the snapshot recovery.
            # Preserve the original no-impact behavior rather than losing the shot.
            return None
        candidate = select_recovery_candidate(candidates, prior)
        return candidate.impact_s if candidate is not None else None

    def _ops_guided_measurement(  # pylint: disable=too-many-return-statements
        self,
        raw: bytes,
        calibration: Calibration,
        *,
        ball_speed_mph: float,
        club: str | None,
        baseline: LCMFResult,
        prepared: PreparedLCMFCapture,
    ) -> LCMFResult:
        """Replace a suspicious TI range walk with an OPS-compatible one."""
        return ops_guided_measurement(
            raw,
            calibration,
            ball_speed_mph=ball_speed_mph,
            club=club,
            baseline=baseline,
            prepared=prepared,
            net_range_m=self.net_range_m,
            tx_order=self.tx_order,
            tdm_sign_policy=self.tdm_sign_policy,
            horizontal_phase_reference_rad=self.horizontal_phase_reference_rad,
        )

    def process_shot(  # pylint: disable=too-many-arguments
        self,
        *,
        impact_timestamp: float | None,
        ball_speed_mph: float,
        club: str | None,
        club_speed_mph: float | None = None,
        tilt_deg: float | None = None,
    ) -> IWR6843ShotResult:
        """Match one OPS shot to TI data and run LCMF-v1."""
        capture = self.capture_monitor.capture_for_shot(
            impact_timestamp,
            timeout_s=self.capture_timeout_s,
        )
        if capture is None or not capture.valid or capture.raw is None:
            return IWR6843ShotResult(capture=capture, measurement=None)
        shot_calibration = self.calibration
        if tilt_deg is not None:
            shot_calibration = replace(self.calibration, tilt_rad=math.radians(tilt_deg))
        if getattr(shot_calibration, "tee_range_m", 1.0) is None:
            return IWR6843ShotResult(
                capture=capture,
                measurement=None,
                withheld_reason="tee_range_unresolved",
            )
        measurement, club_path = process_raw_capture(
            capture.raw,
            shot_calibration,
            ball_speed_mph=ball_speed_mph,
            club=club,
            club_speed_mph=club_speed_mph,
            net_range_m=self.net_range_m,
            tx_order=self.tx_order,
            tdm_sign_policy=self.tdm_sign_policy,
            azimuth_offset_deg=self.azimuth_offset_deg,
            horizontal_phase_reference_rad=self.horizontal_phase_reference_rad,
            club_window_policy=self.club_window_policy,
            club_impact_correction_s=self.club_impact_correction_s,
            recovery_observations=list(self.recovery_observations),
        )
        self._remember_recovery_observation(measurement, ball_speed_mph)
        return IWR6843ShotResult(capture=capture, measurement=measurement, club_path=club_path)

    def stop(self) -> None:
        """Release TI hardware."""
        self.capture_monitor.stop()


__all__ = [
    "IWR6843Runtime",
    "IWR6843ShotResult",
    "ops_guided_measurement",
    "process_raw_capture",
    "horizontal_confidence_from",
]
