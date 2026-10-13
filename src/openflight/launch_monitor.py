"""
Golf Launch Monitor data types and carry estimation.

Provides Shot and carry distance estimation used by
RollingBufferMonitor and the Flask server.
"""

import statistics
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from . import metric_definitions
from .clubs import ClubType
from .clubs.physics import get_club_physics
from .ops243 import SpeedReading
from .pi_trace import _finite

# Spin confidence threshold for "high" quality — used across modules.
# Measured spin is trusted for physics simulation only above this level.
SPIN_CONFIDENCE_HIGH = 0.7

# Floor below which a measured spin is diagnostic only: it is still reported,
# but the carry table substitutes the club-optimal spin for the ball speed.
SPIN_CONFIDENCE_RELIABLE = 0.6

# Shot.carry_status: what the reported carry is. Server finalization commits it,
# and a fusion unit (IWR or camera) only ever reports a carry its simulator built
# from the shot's own measured inputs (D17).
CARRY_MEASURED = "measured"  # fusion: simulator on measured launch, spin and spin axis
CARRY_CALCULATED = "calculated"  # fusion: simulator on calculated or measured inputs
# fusion: simulator on a spin or spin axis built on model geometry (D39: the arc prior)
CARRY_MODEL_GEOMETRY = "model_geometry"
CARRY_ESTIMATED = "estimated"  # OPS-only: labelled estimate (substitutes or the spin table)
CARRY_NOT_MEASURED = "not_measured"  # fusion: no carry; nothing is filled in
# Statuses under which the ball-speed table may stand in for a missing carry. None
# is a shot that never reached finalization (mock, replays, older logs).
_TABLE_CARRY_STATUSES = (None, CARRY_ESTIMATED)

# Shot.smash_factor_status: what the shown smash factor is (D21, D37). TrackMan's
# smash factor is full ball speed over full club speed; a fusion unit's is the OPS
# radial ball speed over the OPS radial club speed, so it is labelled. None: no
# label (an OPS-only unit, a mock or a replay), exactly as before.
SMASH_RADIAL_RATIO = "radial_ratio"


def estimate_carry_distance(ball_speed_mph: float, club: ClubType = ClubType.DRIVER) -> float:
    """
    Estimate carry distance from ball speed using TrackMan-derived data.

    This uses interpolation from real-world data assuming optimal launch
    conditions (10-14° launch angle, appropriate spin for ball speed).

    Data sources:
    - TrackMan PGA Tour averages
    - pitchmarks.com ball speed to distance tables

    Args:
        ball_speed_mph: Ball speed in mph
        club: Club type (affects the model used)

    Returns:
        Estimated carry distance in yards

    Note:
        Without launch angle and spin rate, this is an approximation.
        Actual distance can vary ±10-15% based on:
        - Launch angle (optimal: 10-14° for driver)
        - Spin rate (optimal: 2000-3000 rpm for driver)
        - Weather conditions
        - Altitude
    """
    # Driver ball speed to carry distance lookup table
    # Based on TrackMan data assuming optimal launch conditions
    # Format: (ball_speed_mph, carry_yards_low, carry_yards_high)
    DRIVER_TABLE = [
        (100, 130, 142),
        (110, 157, 170),
        (120, 183, 197),
        (130, 207, 223),
        (140, 231, 249),
        (150, 254, 275),
        (160, 276, 301),
        (167, 275, 285),  # PGA Tour average
        (170, 298, 325),
        (180, 320, 349),
        (190, 342, 372),
        (200, 360, 389),
        (210, 383, 408),
    ]
    # Some source rows are mixed (e.g. PGA average vs optimal carry) and can
    # create local dips in the midpoint curve. Clamp midpoints to a
    # non-decreasing sequence so carry does not decrease as ball speed rises.
    monotonic_driver_curve = []
    max_carry_so_far = 0.0
    for speed, carry_min, carry_max in DRIVER_TABLE:
        midpoint = (carry_min + carry_max) / 2
        max_carry_so_far = max(max_carry_so_far, midpoint)
        monotonic_driver_curve.append((speed, max_carry_so_far))

    # No club factor applied — ball speed already reflects the club's smash
    # factor. Club-specific carry differences come from launch angle and spin
    # adjustments applied downstream.

    # Interpolate from driver table
    if ball_speed_mph <= monotonic_driver_curve[0][0]:
        # Below minimum - extrapolate linearly
        ratio = ball_speed_mph / monotonic_driver_curve[0][0]
        base_carry = monotonic_driver_curve[0][1]
        carry = base_carry * ratio
    elif ball_speed_mph >= monotonic_driver_curve[-1][0]:
        # Above maximum - extrapolate conservatively
        # Use ~1.8 yards per mph above 210 mph
        base_carry = monotonic_driver_curve[-1][1]
        carry = base_carry + (ball_speed_mph - monotonic_driver_curve[-1][0]) * 1.8
    else:
        # Interpolate between table entries
        for i in range(len(monotonic_driver_curve) - 1):
            if monotonic_driver_curve[i][0] <= ball_speed_mph < monotonic_driver_curve[i + 1][0]:
                # Linear interpolation
                speed_low, carry_low = monotonic_driver_curve[i]
                speed_high, carry_high = monotonic_driver_curve[i + 1]

                # Interpolate
                t = (ball_speed_mph - speed_low) / (speed_high - speed_low)
                carry = carry_low + t * (carry_high - carry_low)
                break
        else:
            # Fallback (shouldn't reach here)
            carry = ball_speed_mph * 1.65

    return carry


def adjust_carry_for_launch_angle(
    base_carry: float,
    launch_angle: float,
    club: ClubType = ClubType.DRIVER,
    confidence: float = 1.0,
) -> float:
    """
    Adjust carry distance based on launch angle deviation from optimal.

    Deviation from optimal costs carry:
    - Too low: -2.0 yards per degree (ball doesn't get enough height)
    - Too high: -1.5 yards per degree (ball balloons, less severe)
    - Penalty is scaled by confidence and capped at 10% of base carry.

    Args:
        base_carry: Base carry distance in yards (from estimate_carry_distance)
        launch_angle: Measured vertical launch angle in degrees
        club: Club type (determines optimal launch angle)
        confidence: Confidence in the launch angle measurement (0-1)

    Returns:
        Adjusted carry distance in yards
    """
    optimal = get_club_physics(club).optimal_launch_deg
    angle_delta = launch_angle - optimal

    if angle_delta < 0:
        raw_penalty = abs(angle_delta) * 2.0
    else:
        raw_penalty = angle_delta * 1.5

    penalty = raw_penalty * confidence
    max_penalty = base_carry * 0.10
    penalty = min(penalty, max_penalty)

    return base_carry - penalty


@dataclass
class Shot:
    """
    Represents a detected golf shot with ball and club data.

    Attributes:
        ball_speed_mph: Peak ball speed detected (mph)
        club_speed_mph: Peak club head speed detected (mph), if available
        smash_factor: Ratio of ball speed to club speed (typically 1.4-1.5 for driver)
        timestamp: When the shot was detected
        shot_number: Stable session sequence assigned when the server receives the shot
        impact_timestamp: Epoch timestamp aligned to impact/OPS trigger time
        impact_timestamp_kld7: Ball-contact instant used by the K-LD7
            geometry launch-angle estimator. This currently mirrors the
            trusted hardware sound-trigger timestamp; OPS ball_timestamp_ms is
            a radar-return position inside the capture and should not move
            geometry t=0.
        peak_magnitude: Signal strength of strongest reading
        readings: All raw speed readings for this shot
        club: Club type for distance estimation
        launch_angle_vertical: Vertical launch angle in degrees (from camera)
        launch_angle_horizontal: Horizontal launch angle in degrees (from camera)
        launch_angle_confidence: Backward-compatible primary launch angle confidence (0-1)
        launch_angle_vertical_confidence: Confidence in vertical launch angle measurement
        launch_angle_horizontal_confidence: Confidence in horizontal launch angle measurement
        launch_angle_vertical_source: Source for vertical launch angle
        launch_angle_horizontal_source: Source for horizontal launch angle
        spin_rpm: Spin rate in RPM (from rolling buffer mode)
        spin_confidence: Confidence in spin measurement (0-1)
        spin_method: Estimator that produced the spin candidate
        spin_result_quality: Processor quality label for the spin detection
        spin_multipath_fade_hz: Fitted two-ray fade frequency, when available
        spin_snr: Signal-to-noise ratio of the spin envelope peak
        spin_modulation_depth: Envelope std/mean inside the spin window
        spin_peak_freq_hz: Frequency of the detected spin candidate
        spin_seam_cycles: Number of spin cycles in the analysis window
        spin_at_lower_rail: Whether the spin candidate hit the low search boundary
        spin_at_upper_rail: Whether the spin candidate hit the high search boundary
        spin_candidates: Ranked spin candidates for offline analysis
        spin_phase_method: Phase confirmation method, if attempted
        spin_phase_rpm: Phase-derived spin candidate, if available
        spin_phase_snr: Phase-derived candidate SNR
        spin_phase_agreement_pct: Envelope/phase agreement percentage
        spin_phase_confirmed: Whether phase recovered a low-SNR spin
        spin_rejection_reason: Why spin was withheld, if it was rejected
        carry_spin_adjusted: Carry distance adjusted for spin (yards)
        carry_status: What the reported carry is (CARRY_* above); None until finalized
        air_density_kg_m3: The shot's air density (kg/m^3) when a density source supplied
            one before finalization: the owner's hook. None flies the as-measured
            flight in ISA air, labelled "ISA default" (openflight.flight)
        flight: Every flight output (carry, carry side, total, total side, apex,
            landing angle, hang time, curve), as measured and normalized, each with
            status, source, range and reason (openflight.flight); None until finalized
        smash_factor_status: What smash_factor is (SMASH_RADIAL_RATIO above), or None
        mode: Shot source — "streaming", "rolling-buffer", or "mock"
        readings_data: Serialized readings for session logging
        camera_replay: Public replay metadata for a matched high-speed capture
    """

    ball_speed_mph: float
    timestamp: datetime
    shot_number: Optional[int] = None
    impact_timestamp: Optional[float] = None
    impact_timestamp_kld7: Optional[float] = None
    club_speed_mph: Optional[float] = None
    # Raw OPS radial ball speed retained for compatibility and explicit provenance.
    ball_speed_raw_mph: Optional[float] = None
    # Which speed ball_speed_mph is. New live shots retain the OPS radial value.
    ball_speed_contract: Optional[str] = None
    experimental_ball_speed_total: Optional[dict] = None
    # E3 (openflight.full_speed): the full ball speed by measured projection,
    # referred to separation, "calculated" or "not_measured" with the reason; the
    # full club speed by the same projection; and smash from the two (D21, D37).
    # ball_speed_mph stays the canonical OPS radial reading.
    ball_speed_full: Optional[dict] = None
    # Its value alone, the field the flight model reads (E4): the calculated full
    # speed in mph, or None when it is not measured.
    ball_speed_full_mph: Optional[float] = None
    club_speed_full: Optional[dict] = None
    smash_factor_full: Optional[dict] = None
    # The OPS readings around impact on the OPS clock (full_speed.ops_speed_track);
    # transient, never serialized: the reading used travels in ball_speed_full.
    ops_speed_track: Optional[dict] = field(default=None, repr=False, compare=False)
    peak_magnitude: Optional[float] = None
    readings: List[SpeedReading] = field(default_factory=list)
    club: ClubType = ClubType.DRIVER
    launch_angle_vertical: Optional[float] = None
    launch_angle_horizontal: Optional[float] = None
    launch_angle_confidence: Optional[float] = None
    launch_angle_vertical_confidence: Optional[float] = None
    launch_angle_horizontal_confidence: Optional[float] = None
    launch_angle_vertical_source: Optional[str] = None
    launch_angle_horizontal_source: Optional[str] = None
    # "azimuth_uncalibrated" when the displayed horizontal is an IWR value
    # measured without a horizontal phase reference (audit F8).
    launch_angle_horizontal_status: Optional[str] = None
    # The zero of each horizontal metric (D36 item 2, openflight.horizontal_frame):
    # "unit_boresight" with no horizontal offset, "target_line" with one, "mixed" for a
    # value built from angles in two frames; None when the metric is absent.
    launch_angle_horizontal_frame: Optional[str] = None
    club_path_frame: Optional[str] = None
    experimental_club_path_frame: Optional[str] = None
    experimental_fused_club_path_frame: Optional[str] = None
    experimental_camera_horizontal_frame: Optional[str] = None
    experimental_face_angle_frame: Optional[str] = None
    spin_axis_frame: Optional[str] = None
    spin_rpm: Optional[float] = None
    spin_confidence: Optional[float] = None
    # Raw radar-measured spin, kept when --calculated-spin rewrites
    # spin_rpm with the kinematic estimate (for offline scoring)
    spin_rpm_measured: Optional[float] = None
    # "measured", "calculated", "model_geometry" (a model's spin built on an assumed
    # swing geometry, D39), or None
    spin_source: Optional[str] = None
    spin_method: Optional[str] = None
    # the measured spin's method, kept beside spin_rpm_measured when a model's spin
    # displaces it, and given back with it
    spin_method_measured: Optional[str] = None
    spin_result_quality: Optional[str] = None
    spin_multipath_fade_hz: Optional[float] = None
    spin_snr: Optional[float] = None
    spin_modulation_depth: Optional[float] = None
    spin_peak_freq_hz: Optional[float] = None
    spin_seam_cycles: Optional[float] = None
    spin_at_lower_rail: Optional[bool] = None
    spin_at_upper_rail: Optional[bool] = None
    spin_candidates: Optional[list] = None
    spin_phase_method: Optional[str] = None
    spin_phase_rpm: Optional[float] = None
    spin_phase_snr: Optional[float] = None
    spin_phase_agreement_pct: Optional[float] = None
    spin_phase_confirmed: bool = False
    spin_rejection_reason: Optional[str] = None
    carry_spin_adjusted: Optional[float] = None
    carry_status: Optional[str] = None
    air_density_kg_m3: Optional[float] = None
    flight: Optional[dict] = None
    smash_factor_status: Optional[str] = None
    mode: str = "rolling-buffer"
    profile_id: str = ""
    profile_name: str = ""
    readings_data: Optional[list] = None
    camera_replay: Optional[dict] = None
    camera_fusion_context: Optional[dict] = None
    camera_optical_quality: Optional[dict] = None
    camera_fusion_processing: Optional[dict] = None
    camera_fusion_session_uuid: Optional[str] = field(default=None, repr=False, compare=False)
    calibrated_camera_status: Optional[str] = None
    calibrated_camera_reason: Optional[str] = None
    angle_source: Optional[str] = None  # "radar", "camera", "estimated", or None
    club_angle_deg: Optional[float] = None  # Club angle of attack from K-LD7 (vertical)
    club_path_deg: Optional[float] = None  # Club path: IWR6843, or K-LD7 (deprecated, horizontal)
    experimental_attack_angle_deg: Optional[float] = None
    experimental_attack_angle_status: Optional[str] = None
    experimental_club_path_deg: Optional[float] = None
    experimental_club_path_status: Optional[str] = None
    # Camera-fused club delivery: radar AoA candidate + per-club calibration
    # offset, club path derived from the camera delivery-plane trace. Same
    # experimental estimator level as the fields above.
    experimental_fused_attack_angle_deg: Optional[float] = None
    experimental_fused_club_path_deg: Optional[float] = None
    experimental_fused_status: Optional[str] = None
    experimental_fused_attack_angle_confidence: Optional[str] = None
    experimental_fused_club_path_confidence: Optional[str] = None
    experimental_camera_trace_deg: Optional[float] = None
    # D-plane face angle: derived from start direction and club path, not seen
    # on the club. Experimental until checked against impact-tape truth.
    experimental_face_angle_deg: Optional[float] = None
    experimental_face_angle_status: Optional[str] = None
    # Which displayed club path and horizontal launch the face angle used.
    experimental_face_angle_path_source: Optional[str] = None
    experimental_face_angle_launch_source: Optional[str] = None
    # E2 collision model (openflight.collision_model): calculated, never measured.
    # collision_model["metrics"] holds each value's status, source, unit, frame and
    # reason; the experimental_face_angle_* fields above mirror face_angle_deg.
    face_angle_deg: Optional[float] = None
    dynamic_loft_deg: Optional[float] = None
    spin_loft_deg: Optional[float] = None
    face_to_path_deg: Optional[float] = None
    d_plane_tilt_deg: Optional[float] = None
    # Penner 2001 spin, recorded whenever it runs; spin_rpm takes it only without an
    # accepted measured spin. The --calculated-spin formula's value, likewise recorded.
    spin_rpm_collision: Optional[float] = None
    spin_rpm_calculated_empirical: Optional[float] = None
    # "calculated (D-plane tilt, Wood et al. 2018 weights)" from the collision model
    # (or "calculated (D-plane + gear effect, Cross & Nathan 2007)"); "model geometry
    # (D-plane tilt, ...; club path from the arc prior)" when built on model geometry
    # (D39); None otherwise
    spin_axis_source: Optional[str] = None
    collision_model: Optional[dict] = None
    experimental_aoa_offset_source: Optional[str] = None
    # E1 club delivery (openflight.club_arc.club_delivery_payload): club path,
    # attack angle, swing plane, swing direction and low point, each with status
    # (measured / calculated / model_geometry / not_measured), source, frame, range
    # and reason, plus the arc fit and the chained cross-check; None without a camera.
    club_delivery: Optional[dict] = None
    # Independent horizontal ball-flight evidence. The camera-assisted value
    # may become the displayed experimental result while the original IWR
    # value remains available for replay and comparison.
    iwr6843_horizontal_deg: Optional[float] = None
    iwr6843_horizontal_confidence: Optional[float] = None
    experimental_camera_horizontal_deg: Optional[float] = None
    experimental_camera_horizontal_confidence: Optional[float] = None
    experimental_camera_horizontal_status: Optional[str] = None
    experimental_camera_iwr_delta_deg: Optional[float] = None
    # Transient fitted IWR range trajectory consumed by live camera fusion.
    # Deliberately excluded from websocket/session serializers.
    iwr6843_club_range_evidence: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    iwr6843_ball_range_evidence: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    spin_axis_deg: Optional[float] = None  # Spin axis tilt: 0=backspin, +right(fade), -left(draw)
    inclinometer: Optional[dict] = None  # Stable enclosure orientation used for this shot
    stage_timing: Optional[dict] = None
    # Trigger evidence a tester shot lacked but was kept without, as
    # [{"id", "reason"}]; None when nothing was missing (P7-3).
    missing_trigger_evidence: Optional[list] = None
    # What blocked or mismatched trigger evidence said about a tester shot the
    # kiosk kept anyway (D15), as [{"id", "reason"}]; None when it said nothing.
    trigger_evidence_notes: Optional[list] = None
    # Labels on the camera's values that no longer withhold them (D15): the
    # capture's lighting and optical-quality verdicts; None when there are none.
    camera_notes: Optional[list] = None
    server_callback_started_monotonic_ns: Optional[int] = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Pi trace attempt this shot belongs to (--pi-trace); transient, never serialized.
    trace_attempt_id: Optional[str] = field(
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def ball_speed_ms(self) -> float:
        """Ball speed in meters per second."""
        return self.ball_speed_mph * 0.44704

    @property
    def club_speed_ms(self) -> Optional[float]:
        """Club speed in meters per second."""
        if self.club_speed_mph is None:
            return None
        return self.club_speed_mph * 0.44704

    @property
    def smash_factor(self) -> Optional[float]:
        """
        Smash factor: ball speed over club speed (metric_definitions.smash_factor, D37).

        Indicates quality of contact:
        - Driver: 1.44-1.50 (optimal)
        - Irons: 1.30-1.38
        - Wedges: 1.20-1.25

        smash_factor_status says which speeds it divides (SMASH_RADIAL_RATIO: both
        OPS radial readings, not TrackMan's full speeds; D21).

        Returns:
            Smash factor, or None without a positive finite club speed
        """
        return metric_definitions.smash_factor(self.ball_speed_mph, self.club_speed_mph)

    @property
    def estimated_carry_yards(self) -> float:
        """Estimated carry distance based on ball speed, club type, and launch angle."""
        base = estimate_carry_distance(self.ball_speed_mph, self.club)
        if self.has_launch_angle:
            return adjust_carry_for_launch_angle(
                base,
                self.launch_angle_vertical,
                self.club,
                (self.launch_angle_confidence if _finite(self.launch_angle_confidence) else None)
                or 0.2,
            )
        return base

    @property
    def table_carry_allowed(self) -> bool:
        """Whether the ball-speed table may stand in for a missing carry.

        Never on a fusion unit (D17); an OPS-only shot keeps its labelled estimate.
        """
        return self.carry_status in _TABLE_CARRY_STATUSES

    @property
    def reported_carry_yards(self) -> Optional[float]:
        """The carry the kiosk shows, averages and sends to simulators, or None.

        The committed (finite) carry when there is one; the ball-speed table only
        where table_carry_allowed; otherwise not measured.
        """
        if self.carry_status == CARRY_NOT_MEASURED:
            return None
        # a non-finite carry is a missing carry (owner rule), never reported
        if _finite(self.carry_spin_adjusted):
            return self.carry_spin_adjusted
        if not self.table_carry_allowed:
            return None
        table = self.estimated_carry_yards
        return table if _finite(table) else None

    @property
    def estimated_carry_range(self) -> tuple:
        """
        Return (min, max) carry distance estimate to show uncertainty.

        Returns:
            Tuple of (low_estimate, high_estimate) in yards
        """
        base = self.estimated_carry_yards
        # ±10% uncertainty without launch angle/spin data
        # Reduce to ±5% if we have launch angle data
        if self.has_launch_angle:
            return (base * 0.95, base * 1.05)
        return (base * 0.90, base * 1.10)

    @property
    def has_launch_angle(self) -> bool:
        """Check if launch angle data is available for this shot.

        A non-finite angle is a missing one: it never drives carry or its provenance.
        """
        return _finite(self.launch_angle_vertical)

    @property
    def has_spin(self) -> bool:
        """Check if spin data is available for this shot (a non-finite spin is not)."""
        return _finite(self.spin_rpm)

    @property
    def spin_quality(self) -> Optional[str]:
        """
        Get spin measurement quality as a string.

        Returns:
            "high", "medium", "low", or None if no spin data
        """
        if self.has_spin and self.spin_result_quality:
            return self.spin_result_quality
        if not _finite(self.spin_confidence):
            return None
        if self.spin_confidence >= SPIN_CONFIDENCE_HIGH:
            return "high"
        if self.spin_confidence >= 0.4:
            return "medium"
        return "low"

    def to_dict(self) -> dict:
        """Return the canonical, unrounded representation of this shot."""
        return {
            "shot_number": self.shot_number,
            "ball_speed_mph": self.ball_speed_mph,
            "ball_speed_raw_mph": self.ball_speed_raw_mph,
            "ball_speed_contract": self.ball_speed_contract,
            "experimental_ball_speed_total": self.experimental_ball_speed_total,
            "ball_speed_full": deepcopy(self.ball_speed_full),
            "ball_speed_full_mph": self.ball_speed_full_mph,
            "club_speed_full": deepcopy(self.club_speed_full),
            "smash_factor_full": deepcopy(self.smash_factor_full),
            "club_speed_mph": self.club_speed_mph,
            "smash_factor": self.smash_factor,
            # a label describes a smash value; with none there is nothing to label
            "smash_factor_status": (
                self.smash_factor_status if self.smash_factor is not None else None
            ),
            # the ball-speed table is not in a fusion unit's payload at all (D17)
            "estimated_carry_yards": (
                self.estimated_carry_yards if self.table_carry_allowed else None
            ),
            "carry_range": (list(self.estimated_carry_range) if self.table_carry_allowed else None),
            "club": self.club.value,
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "timestamp": self.timestamp.isoformat(),
            "impact_timestamp": self.impact_timestamp,
            "peak_magnitude": self.peak_magnitude,
            "readings_count": len(self.readings),
            "readings": self.readings_data,
            "mode": self.mode,
            "launch_angle_vertical": self.launch_angle_vertical,
            "launch_angle_horizontal": self.launch_angle_horizontal,
            "launch_angle_confidence": self.launch_angle_confidence,
            "launch_angle_vertical_confidence": self.launch_angle_vertical_confidence,
            "launch_angle_horizontal_confidence": self.launch_angle_horizontal_confidence,
            "launch_angle_vertical_source": self.launch_angle_vertical_source,
            "launch_angle_horizontal_source": self.launch_angle_horizontal_source,
            "launch_angle_horizontal_status": self.launch_angle_horizontal_status,
            "launch_angle_horizontal_frame": self.launch_angle_horizontal_frame,
            "angle_source": self.angle_source,
            "club_angle_deg": self.club_angle_deg,
            "club_path_deg": self.club_path_deg,
            "club_path_frame": self.club_path_frame,
            "experimental_attack_angle_deg": self.experimental_attack_angle_deg,
            "experimental_attack_angle_status": self.experimental_attack_angle_status,
            "experimental_club_path_deg": self.experimental_club_path_deg,
            "experimental_club_path_status": self.experimental_club_path_status,
            "experimental_club_path_frame": self.experimental_club_path_frame,
            "experimental_fused_attack_angle_deg": self.experimental_fused_attack_angle_deg,
            "experimental_fused_club_path_deg": self.experimental_fused_club_path_deg,
            "experimental_fused_club_path_frame": self.experimental_fused_club_path_frame,
            "experimental_fused_status": self.experimental_fused_status,
            "experimental_fused_attack_angle_confidence": (
                self.experimental_fused_attack_angle_confidence
            ),
            "experimental_fused_club_path_confidence": (
                self.experimental_fused_club_path_confidence
            ),
            "experimental_camera_trace_deg": self.experimental_camera_trace_deg,
            "experimental_face_angle_deg": self.experimental_face_angle_deg,
            "experimental_face_angle_status": self.experimental_face_angle_status,
            "experimental_face_angle_frame": self.experimental_face_angle_frame,
            "experimental_face_angle_path_source": self.experimental_face_angle_path_source,
            "experimental_face_angle_launch_source": self.experimental_face_angle_launch_source,
            "face_angle_deg": self.face_angle_deg,
            "dynamic_loft_deg": self.dynamic_loft_deg,
            "spin_loft_deg": self.spin_loft_deg,
            "face_to_path_deg": self.face_to_path_deg,
            "d_plane_tilt_deg": self.d_plane_tilt_deg,
            "spin_rpm_collision": self.spin_rpm_collision,
            "spin_rpm_calculated_empirical": self.spin_rpm_calculated_empirical,
            "spin_axis_source": self.spin_axis_source,
            "collision_model": deepcopy(self.collision_model),
            "experimental_aoa_offset_source": self.experimental_aoa_offset_source,
            "club_delivery": deepcopy(self.club_delivery),
            "iwr6843_horizontal_deg": self.iwr6843_horizontal_deg,
            "iwr6843_horizontal_confidence": self.iwr6843_horizontal_confidence,
            "experimental_camera_horizontal_deg": self.experimental_camera_horizontal_deg,
            "experimental_camera_horizontal_confidence": (
                self.experimental_camera_horizontal_confidence
            ),
            "experimental_camera_horizontal_status": self.experimental_camera_horizontal_status,
            "experimental_camera_horizontal_frame": self.experimental_camera_horizontal_frame,
            "experimental_camera_iwr_delta_deg": self.experimental_camera_iwr_delta_deg,
            "camera_replay": dict(self.camera_replay) if self.camera_replay else None,
            "stage_timing": deepcopy(self.stage_timing),
            "missing_trigger_evidence": deepcopy(self.missing_trigger_evidence),
            "trigger_evidence_notes": deepcopy(self.trigger_evidence_notes),
            "camera_notes": list(self.camera_notes) if self.camera_notes else None,
            "camera_fusion_context": (
                dict(self.camera_fusion_context) if self.camera_fusion_context else None
            ),
            "camera_optical_quality": (
                deepcopy(self.camera_optical_quality) if self.camera_optical_quality else None
            ),
            "camera_fusion_processing": (
                dict(self.camera_fusion_processing) if self.camera_fusion_processing else None
            ),
            "calibrated_camera_status": self.calibrated_camera_status,
            "calibrated_camera_reason": self.calibrated_camera_reason,
            "spin_axis_deg": self.spin_axis_deg,
            "spin_axis_frame": self.spin_axis_frame,
            "inclinometer": self.inclinometer,
            "spin_rpm": self.spin_rpm,
            "spin_rpm_measured": self.spin_rpm_measured,
            "spin_source": self.spin_source,
            "spin_method": self.spin_method,
            "spin_method_measured": self.spin_method_measured,
            "spin_confidence": self.spin_confidence,
            "spin_quality": self.spin_quality,
            "spin_multipath_fade_hz": self.spin_multipath_fade_hz,
            "spin_snr": self.spin_snr,
            "spin_modulation_depth": self.spin_modulation_depth,
            "spin_peak_freq_hz": self.spin_peak_freq_hz,
            "spin_candidate_rpm": (
                self.spin_peak_freq_hz * 60 if self.spin_peak_freq_hz is not None else None
            ),
            "spin_seam_cycles": self.spin_seam_cycles,
            "spin_at_lower_rail": self.spin_at_lower_rail,
            "spin_at_upper_rail": self.spin_at_upper_rail,
            "spin_candidates": self.spin_candidates,
            "spin_phase_method": self.spin_phase_method,
            "spin_phase_rpm": self.spin_phase_rpm,
            "spin_phase_snr": self.spin_phase_snr,
            "spin_phase_agreement_pct": self.spin_phase_agreement_pct,
            "spin_phase_confirmed": self.spin_phase_confirmed,
            "spin_rejection_reason": self.spin_rejection_reason,
            "carry_spin_adjusted": self.carry_spin_adjusted,
            "carry_status": self.carry_status,
            "air_density_kg_m3": self.air_density_kg_m3,
            "flight": deepcopy(self.flight),
        }


def _finite_values(values) -> list:
    """Only the finite numbers among ``values`` (None, NaN and inf are missing)."""
    return [value for value in values if _finite(value)]


def _mean_or_none(values: list) -> Optional[float]:
    return statistics.mean(values) if values else None


def summarize_shots(shots: List[Shot], mode: str) -> dict:
    """Calculate the shared session summary for a list of shots."""
    summary = {
        "shot_count": len(shots),
        "avg_ball_speed": 0,
        "max_ball_speed": 0,
        "min_ball_speed": 0,
        "avg_club_speed": None,
        "avg_smash_factor": None,
        "avg_carry_est": 0,
        "avg_spin_rpm": None,
        "spin_detection_rate": 0,
        "mode": mode,
    }
    if not shots:
        return summary

    # A non-finite value is a missing value (owner rule): it never enters an average,
    # so one NaN shot can't make every later emit's stats NaN.
    ball_speeds = _finite_values(shot.ball_speed_mph for shot in shots)
    club_speeds = _finite_values(shot.club_speed_mph for shot in shots if shot.club_speed_mph)
    smash_factors = _finite_values(shot.smash_factor for shot in shots)
    # only carries the kiosk reports: a fusion shot's missing carry is not averaged in
    carries = _finite_values(shot.reported_carry_yards for shot in shots)
    spin_rpms = _finite_values(shot.spin_rpm for shot in shots)
    summary.update(
        {
            "avg_ball_speed": _mean_or_none(ball_speeds),
            "max_ball_speed": max(ball_speeds) if ball_speeds else None,
            "min_ball_speed": min(ball_speeds) if ball_speeds else None,
            "std_dev": statistics.stdev(ball_speeds) if len(ball_speeds) > 1 else None,
            "avg_club_speed": _mean_or_none(club_speeds),
            "avg_smash_factor": _mean_or_none(smash_factors),
            "avg_carry_est": _mean_or_none(carries),
            "avg_spin_rpm": _mean_or_none(spin_rpms),
            # a non-finite spin is not a detected spin
            "spin_detection_rate": len(spin_rpms) / len(shots),
        }
    )
    return summary
