"""Runtime boundary joining TI capture to the LCMF estimator."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field, replace

from openflight.horizontal_frame import frame_for, to_reported_deg
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.club import ClubPathResult, ClubWindowPolicy, estimate_club_path
from openflight.iwr6843.launch_range import (
    LaunchRangeEstimate,
    LaunchRangePrior,
    ShotBallRange,
    estimate_launch_range,
    shot_ball_range,
)
from openflight.iwr6843.lcmf import (
    LCMFResult,
    PreparedLCMFCapture,
    estimate_lcmf_v1,
    prepare_lcmf_capture,
)
from openflight.iwr6843.monitor import IWR6843Capture, IWR6843CaptureMonitor
from openflight.iwr6843.partial_dump import (
    PARTIAL_LABEL,
    PartialDumpRecovery,
    recover_partial_dump,
)
from openflight.iwr6843.recovery import (
    RecoveryCandidate,
    find_recovery_candidates,
)
from openflight.iwr6843.transfer_mode import (
    NOT_CHECKED_REASON,
    TRANSFER_FULL,
    TRANSFER_TWO_ROUND,
    TRANSFERS,
    TransferMode,
    validate_transfer,
)
from openflight.iwr6843.two_round import TwoRoundTransferError, run_two_round
from openflight.iwr6843.two_round_file import pack_sparse_capture

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
# A launch angle on a range walk the tracker could not separate from a different walk,
# when the OPS-guided pass found no replacement.
TRACK_AMBIGUOUS_STATUS = "accepted_track_ambiguous"
HORIZONTAL_CONFIDENCE_CEILING = 0.95

# How the shot's IWR data reaches the host (``transfer_mode``): the whole dump, or the
# two-round transfer (an overview, then only the cells the estimators read). Two-round
# runs only when the radar reported support and the capture offers a link to its frozen
# session as ``TWO_ROUND_LINK_ATTR``; no live capture does until the stage-2 reader exists.
TWO_ROUND_LINK_ATTR = "two_round_link"
# what the monitor reports from the radar's startup check: {"supported": bool, "reason": str}
TWO_ROUND_SUPPORT_ATTR = "two_round_support"
TWO_ROUND_FAILED = "iwr_two_round_transfer_failed"
FULL_DUMP_ARRIVED_REASON = "this capture arrived as a full dump (no two-round link on the capture)"


def horizontal_confidence_from(coherence: float | None) -> float:
    """Normalize HLCMF coherence exactly once for live and replay consumers."""
    if coherence is None:
        return 0.0
    return round(min(HORIZONTAL_CONFIDENCE_CEILING, max(0.0, float(coherence))), 3)


LOW_COHERENCE_HORIZONTAL = "hlcmf_v2_low_coherence"
# Recorded sessions carry the superseded late-flight bearing's label.
LOW_COHERENCE_HORIZONTAL_STATUSES = frozenset({"hlcmf_v1_low_coherence", LOW_COHERENCE_HORIZONTAL})


def camera_horizontal_input(measurement) -> tuple[float | None, float | None]:
    """The IWR horizontal and confidence camera fusion may use; a low-coherence
    horizontal is shown, labelled, but never weighed against the camera."""
    if measurement is None or not getattr(measurement, "accepted", False):
        return None, None
    horizontal = getattr(measurement, "horizontal_deg", None)
    if (
        horizontal is None
        or getattr(measurement, "horizontal_status", None) in LOW_COHERENCE_HORIZONTAL_STATUSES
    ):
        return None, None
    return horizontal, horizontal_confidence_from(
        getattr(measurement, "horizontal_confidence", None)
    )


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
        # The candidate search emits many nearly identical lines. Keep one representative
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
    sound = baseline.accepted and baseline.tracker_quality != "reject"
    if sound and speed is None:
        return replace(baseline, status="accepted_track_speed_warning")
    speed_error = (
        abs(speed / ball_speed_mph - 1.0)
        if speed is not None and ball_speed_mph > 0.0
        else float("inf")
    )
    # a track the IWR could not tell from a different walk is settled by the OPS speed
    ambiguous = getattr(baseline, "track_ambiguity", None) is not None
    if sound and speed_error <= OPS_TRACK_SPEED_TOLERANCE_FRAC and not ambiguous:
        return baseline
    unresolved = (
        TRACK_AMBIGUOUS_STATUS
        if sound and speed_error <= OPS_TRACK_SPEED_TOLERANCE_FRAC
        else "accepted_track_speed_warning"
    )
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
        return replace(baseline, status=unresolved) if sound else baseline
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
    return replace(baseline, status=unresolved) if sound else baseline


@dataclass(frozen=True)
class IWR6843ShotResult:
    """Capture transport result and optional angle measurement."""

    capture: IWR6843Capture | None
    measurement: LCMFResult | None
    club_path: ClubPathResult | None = None
    withheld_reason: str | None = None
    capture_wait_duration_ns: int | None = None
    estimator_analysis_duration_ns: int | None = None
    aggregate_duration_ns: int | None = None
    launch_range: LaunchRangeEstimate | None = None
    ball_range: ShotBallRange | None = None
    # set when the transfer lost bytes: what ``partial_dump`` recovered and what was missing
    partial_recovery: dict | None = None
    # set for a two-round transfer: its session, rounds and bytes (``TwoRoundTransfer``)
    transfer: dict | None = None
    # every shot: the mode asked for, the mode used, and why they differ
    transfer_mode: dict | None = None
    # a two-round shot's payloads as a ``.l3sparse`` file, for the session folder
    sparse_capture: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _Analysis:
    """The estimators' answer on one capture's bytes."""

    measurement: LCMFResult | None
    club_path: ClubPathResult | None = None
    withheld_reason: str | None = None
    estimator_analysis_duration_ns: int | None = None
    launch_range: LaunchRangeEstimate | None = None
    ball_range: ShotBallRange | None = None


def _capture_bytes(capture) -> tuple[bytes | None, PartialDumpRecovery | None]:
    """The dump to measure: the whole capture's, else the frames a failed transfer kept."""
    if capture is None:
        return None, None
    if capture.valid and capture.raw is not None:
        return capture.raw, None
    partial_raw = getattr(capture, "partial_raw", None)
    if not partial_raw:
        return None, None
    recovery = recover_partial_dump(partial_raw)
    if recovery.usable:
        logger.info(
            "[IWR6843] Partial capture #%s: %s", capture.sequence, recovery.missing_summary()
        )
        return recovery.raw, recovery
    logger.info("[IWR6843] Partial capture #%s not decoded: %s", capture.sequence, recovery.reason)
    return None, recovery


def _from_partial(estimate: LaunchRangeEstimate, partial: dict | None) -> LaunchRangeEstimate:
    """A launch range made from a partial dump carries its record (and so its label)."""
    return replace(estimate, partial=partial) if partial is not None else estimate


def _labelled_partial(result, partial: PartialDumpRecovery | None):
    """An estimator result from a partial dump says so in its status."""
    if partial is None or result is None or not getattr(result, "status", None):
        return result
    return replace(result, status=f"{result.status}_{PARTIAL_LABEL}")


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
    azimuth_offset_deg: float | None,
    horizontal_phase_reference_rad: float | None,
    club_window_policy: ClubWindowPolicy,
    club_impact_correction_s: float,
    prepared: PreparedLCMFCapture | None = None,
) -> tuple[LCMFResult, ClubPathResult | None]:
    """Run the complete production ball and club estimator on frozen bytes."""
    if prepared is None:
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
            horizontal_deg=float(to_reported_deg(measurement.horizontal_deg, azimuth_offset_deg)),
            horizontal_raw_deg=measurement.horizontal_deg,
            horizontal_frame=frame_for(azimuth_offset_deg),
        )
    if not club_speed_mph:
        return measurement, None
    ball_sign = getattr(measurement, "tdm_sign_used", None)
    fallback = ball_sign not in (-1, 1)
    impact_t_s = getattr(measurement, "impact_t_s", None)
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
    # -(target yaw), + when the boresight points right of the target line.
    azimuth_offset_deg: float | None = None
    horizontal_phase_reference_rad: float | None = None
    tdm_sign_policy: str = "positive"
    club_window_policy: ClubWindowPolicy = field(default_factory=ClubWindowPolicy)
    # The ball-derived impact anchor runs ~2 ms late: on the 2026-08-07
    # 55-shot session, the club track's tee-contact error minimized at -2 ms
    # (0.026 m vs 0.035 m uncorrected), independently matching the camera's
    # ball-departure timing. Applied to the club estimators only; the ball
    # pipeline keeps its own anchor.
    club_impact_correction_s: float = -0.002
    calibration_provenance: dict | None = None
    radar_config_provenance: dict | None = None
    # The camera's coarse resting-ball range: a prior only, labelled where used.
    range_prior: LaunchRangePrior | None = None
    # the requested transfer at startup, TRANSFER_FULL (default) or TRANSFER_TWO_ROUND;
    # ``set_transfer`` changes it between shots
    transfer: str = TRANSFER_FULL

    def __post_init__(self):
        self.transfer_mode = TransferMode(validate_transfer(self.transfer))
        self.refresh_transfer_support()

    def set_transfer(self, mode: str):
        """Ask for a transfer mode; a shot already begun keeps the mode it began with."""
        status = self.transfer_mode.request(mode)
        self.transfer = status.requested
        return status

    def refresh_transfer_support(self):
        """Take the radar's two-round support from its monitor's startup check."""
        support = getattr(self.capture_monitor, TWO_ROUND_SUPPORT_ATTR, None)
        if isinstance(support, dict) and "supported" in support:
            self.transfer_mode.set_support(bool(support["supported"]), support.get("reason"))
        return self.transfer_mode.status()

    def replay_config_snapshot(
        self,
        *,
        ball_speed_mph: float | None = None,
        club: str | None = None,
        club_speed_mph: float | None = None,
        effective_tilt_deg: float | None = None,
        effective_roll_deg: float | None = None,
        tee_range_m: float | None = None,
        ball_at_address: dict | None = None,
        range_prior: LaunchRangePrior | None = None,
    ) -> dict:
        """Freeze every primitive needed by the hardware-free estimator."""
        payload = {
            "range_prior": self.range_prior.to_dict() if self.range_prior else None,
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
            "source_revision_status": "captured_separately_in_session_runtime_provenance",
            "calibration": self.calibration_provenance,
            "radar_config": self.radar_config_provenance,
            "per_shot_inputs": {
                "ball_speed_mph": ball_speed_mph,
                "club": club,
                "club_speed_mph": club_speed_mph,
                "effective_tilt_deg": effective_tilt_deg,
                "effective_roll_deg": effective_roll_deg,
                "tee_range_m": tee_range_m,
                "ball_at_address": ball_at_address,
                "range_prior": (range_prior or self.range_prior).to_dict()
                if (range_prior or self.range_prior)
                else None,
            },
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return {**payload, "sha256": hashlib.sha256(encoded).hexdigest()}

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
        tee_range_m: float | None = None,
        range_prior: LaunchRangePrior | None = None,
        roll_deg: float | None = None,
    ) -> IWR6843ShotResult:
        """Match one OPS shot to TI data, range its launch point, and run LCMF.

        Range: the shot's own launch point, else the labelled camera prior, else none.
        ``roll_deg`` (the unit's verified roll) turns the board about its boresight.
        """
        process_started_ns = time.monotonic_ns()
        # the mode in effect as the shot begins; a change from now on is the next shot's
        status = self.transfer_mode.status()
        capture = self.capture_monitor.capture_for_shot(
            impact_timestamp,
            timeout_s=self.capture_timeout_s,
        )
        capture_returned_ns = time.monotonic_ns()
        link = (
            getattr(capture, TWO_ROUND_LINK_ATTR, None)
            if status.effective == TRANSFER_TWO_ROUND
            else None
        )
        raw, partial = (None, None) if link is not None else _capture_bytes(capture)
        partial_record = partial.to_dict() if partial is not None else None

        def mode_record(used: str, reason: str | None) -> dict:
            if status.requested == TRANSFER_TWO_ROUND:
                self.transfer_mode.note_fallback(reason)
            return {"requested": status.requested, "used": used, "fallback_reason": reason}

        used_mode: dict | None = None
        if link is None:
            fallback = None
            if status.requested == TRANSFER_TWO_ROUND:
                fallback = (
                    status.reason or NOT_CHECKED_REASON
                    if status.effective != TRANSFER_TWO_ROUND
                    else FULL_DUMP_ARRIVED_REASON
                )
            used_mode = mode_record(TRANSFER_FULL if raw is not None else "none", fallback)

        def result(
            analysis: _Analysis | None = None,
            *,
            withheld_reason: str | None = None,
            transfer: dict | None = None,
            transfer_mode: dict | None = None,
            sparse_capture: bytes | None = None,
        ) -> IWR6843ShotResult:
            if analysis is None:
                analysis = _Analysis(measurement=None, withheld_reason=withheld_reason)
            return IWR6843ShotResult(
                capture=capture,
                measurement=analysis.measurement,
                club_path=analysis.club_path,
                withheld_reason=analysis.withheld_reason,
                capture_wait_duration_ns=capture_returned_ns - process_started_ns,
                estimator_analysis_duration_ns=analysis.estimator_analysis_duration_ns,
                aggregate_duration_ns=time.monotonic_ns() - process_started_ns,
                launch_range=analysis.launch_range,
                ball_range=analysis.ball_range,
                partial_recovery=partial_record,
                transfer=transfer,
                transfer_mode=transfer_mode if transfer_mode is not None else used_mode,
                sparse_capture=sparse_capture,
            )

        def analyse(dump: bytes, recovered: PartialDumpRecovery | None = None) -> _Analysis:
            return self._analyse(
                dump,
                recovered,
                partial_record if recovered is not None else None,
                ball_speed_mph=ball_speed_mph,
                club=club,
                club_speed_mph=club_speed_mph,
                tilt_deg=tilt_deg,
                tee_range_m=tee_range_m,
                range_prior=range_prior,
                roll_deg=roll_deg,
            )

        if link is not None:
            try:
                analysis, transfer = run_two_round(link, analyse)
            except TwoRoundTransferError as error:
                reason = (
                    f"the two-round transfer failed ({error.transfer.error}); the ring was "
                    + ("released" if error.transfer.released else "left to the radar's timeout")
                    + ", so the full dump is no longer available"
                )
                logger.error("[IWR6843] Shot not measured: %s", reason)
                return result(
                    withheld_reason=TWO_ROUND_FAILED,
                    transfer=error.transfer.to_dict(),
                    transfer_mode=mode_record("none", reason),
                    sparse_capture=pack_sparse_capture(error.transfer)
                    if error.transfer.payloads
                    else None,
                )
            return result(
                analysis,
                transfer=transfer.to_dict(),
                transfer_mode=mode_record(TRANSFER_TWO_ROUND, transfer.fallback),
                sparse_capture=pack_sparse_capture(transfer),
            )
        if raw is None:
            return result()
        return result(analyse(raw, partial))

    def _analyse(  # pylint: disable=too-many-arguments,too-many-locals
        self,
        raw: bytes,
        partial: PartialDumpRecovery | None,
        partial_record: dict | None,
        *,
        ball_speed_mph: float,
        club: str | None,
        club_speed_mph: float | None,
        tilt_deg: float | None,
        tee_range_m: float | None,
        range_prior: LaunchRangePrior | None,
        roll_deg: float | None,
    ) -> _Analysis:
        """Range the launch point, settle the tee, and run every estimator on ``raw``."""
        launch_range: LaunchRangeEstimate | None = None
        ball_range: ShotBallRange | None = None
        shot_calibration = self.calibration
        if tilt_deg is not None:
            shot_calibration = replace(self.calibration, tilt_rad=math.radians(tilt_deg))
        if roll_deg is not None:
            shot_calibration = replace(shot_calibration, roll_rad=math.radians(roll_deg))
        if tee_range_m is not None:
            shot_calibration = replace(shot_calibration, tee_range_m=float(tee_range_m))
        prepared = None
        if hasattr(shot_calibration, "tee_range_m"):
            prior = range_prior or self.range_prior
            try:
                prepared = prepare_lcmf_capture(raw)
            except (ValueError, IndexError) as error:
                launch_range = LaunchRangeEstimate(
                    status="not_measured",
                    reason=f"the capture could not be decoded: {error}",
                    prior=prior,
                )
            else:
                launch_range = _from_partial(
                    self._launch_range(
                        prepared,
                        shot_calibration,
                        ball_speed_mph=ball_speed_mph,
                        club_speed_mph=club_speed_mph,
                        prior=prior,
                    ),
                    partial_record,
                )
            configured = shot_calibration.tee_range_m
            if configured is not None:
                ball_range = ShotBallRange(
                    range_m=float(configured),
                    uncertainty_m=float(getattr(shot_calibration, "tee_range_uncertainty_m", 0.0)),
                    source="configured_tee",
                    label="measured",
                    reason="a fixed range set in code (not used by the kiosk)",
                )
            else:
                ball_range = shot_ball_range(launch_range, prior)
                if ball_range.range_m is not None:
                    shot_calibration = replace(
                        shot_calibration,
                        tee_range_m=float(ball_range.range_m),
                        tee_range_uncertainty_m=float(ball_range.uncertainty_m or 0.0),
                    )
        if getattr(shot_calibration, "tee_range_m", 1.0) is None:
            return _Analysis(
                measurement=None,
                withheld_reason="tee_range_unresolved",
                launch_range=launch_range,
                ball_range=ball_range,
            )
        analysis_started_ns = time.monotonic_ns()
        measurement, club_path = process_raw_capture(
            raw,
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
            prepared=prepared,
        )
        analysis_completed_ns = time.monotonic_ns()
        return _Analysis(
            measurement=_labelled_partial(measurement, partial),
            club_path=_labelled_partial(club_path, partial),
            estimator_analysis_duration_ns=analysis_completed_ns - analysis_started_ns,
            launch_range=launch_range,
            ball_range=ball_range,
        )

    def _launch_range(
        self,
        prepared: PreparedLCMFCapture,
        calibration: Calibration,
        *,
        ball_speed_mph: float,
        club_speed_mph: float | None,
        prior: LaunchRangePrior | None,
    ) -> LaunchRangeEstimate:
        """This shot's launch point; an estimator failure is a reason, never a lost shot."""
        try:
            return estimate_launch_range(
                prepared.vertical,
                ball_speed_mph=ball_speed_mph,
                club_speed_mph=club_speed_mph,
                prior=prior,
                net_range_m=self.net_range_m,
                range_bias_m=float(getattr(calibration, "range_bias_m", 0.0) or 0.0),
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.warning("[IWR6843] launch-point range failed: %s", error)
            return LaunchRangeEstimate(
                status="not_measured",
                reason=f"the launch-point estimator failed: {error}",
                prior=prior,
            )

    def stop(self) -> None:
        """Release TI hardware."""
        self.capture_monitor.stop()


__all__ = [
    "TRANSFER_FULL",
    "TRANSFER_TWO_ROUND",
    "TRANSFERS",
    "IWR6843Runtime",
    "IWR6843ShotResult",
    "ops_guided_measurement",
    "process_raw_capture",
    "horizontal_confidence_from",
    "camera_horizontal_input",
]
