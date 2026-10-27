"""Collision model: club face, dynamic loft, spin, spin axis and the spin and smash indices.

Derivations, sources and units: docs/how-it-works/collision-model.md. A missing input makes
each metric built on it "not measured", with the reason; outputs are ``calculated`` unless
an input is ``model_geometry``, which they inherit. Horizontal outputs keep their inputs' frame.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from typing import Optional

from . import collision_forward, metric_definitions, shot_metrics
from .ballistics import BALL_RADIUS_M, MPH_TO_MPS as MPH_TO_M_S
from .clubs import ClubType
from .clubs.physics import get_club_physics
from .constants import BALL_DIAMETER_M
from .horizontal_frame import MIXED
from .numeric import finite as _finite
from .source_labels import (  # noqa: F401  (the predicates are re-exported)
    LABEL_CALCULATED,
    LABEL_ESTIMATED,
    LABEL_MEASURED,
    LABEL_MODEL_GEOMETRY,
    SPIN_AXIS_ESTIMATED_PREFIX,
    SPIN_AXIS_MODEL_GEOMETRY_PREFIX,
    is_calculated_source,
    is_estimated_source,
    is_model_geometry_source,
)

MODEL_VERSION = "e2-2026-10-20-fitted"

# Output statuses (the input labels are source_labels').
STATUS_CALCULATED = shot_metrics.CALCULATED
STATUS_MODEL_GEOMETRY = shot_metrics.MODEL_GEOMETRY
STATUS_ESTIMATED = shot_metrics.ESTIMATED
STATUS_NOT_MEASURED = shot_metrics.NOT_MEASURED
_INPUT_LABELS = (LABEL_MEASURED, LABEL_CALCULATED, LABEL_MODEL_GEOMETRY, LABEL_ESTIMATED)

SOURCE_INVERSION = "D-plane inversion of the fitted launch model"
SOURCE_D37 = "D37 definition (metric_definitions) of the calculated face and loft"
SOURCE_PENNER = "Penner 2001 collision model (Am. J. Phys. 69, 563)"
SOURCE_SPIN = (
    "impulse collision model (Penner 2001 normal impulse; rolling or sliding, Cross 2002; "
    "gains fitted by the maintainer)"
)
SOURCE_AXIS_MODEL = "fitted collision model: D-plane tilt, centre-face strike"
SOURCE_SPIN_INDEX = "measured spin / expected spin (club speed x k x sin spin loft)"
SOURCE_SMASH_INDEX = "ball speed / expected ball speed (normal-impulse index)"
SOURCE_AXIS = "D-plane tilt (D37)"
SOURCE_GEAR = "Cross & Nathan 2007 gear effect (Am. J. Phys. 75, 658)"
NOTE_NO_GEAR = "no impact location: centre-face strike assumed, no gear effect"
NOTE_NO_FITTED_INDEX = "no fitted index for this club: expected value from its category"
NOTE_CONTACT = "low confidence: possible flier or heavy contact"
CONTACT_ATTACK_LIMIT_DEG = -2.0
CONTACT_HEIGHT_LIMIT_MM = 8.0
NOTE_VERTICAL_PLANE = "no horizontal launch or path: loft from the vertical plane alone"
NOTE_HORIZONTAL_RATIO = "no vertical launch or attack: face from the horizontal ratio alone"
SOURCE_HORIZONTAL_RATIO = "D-plane horizontal launch ratio, Wood et al. 2018 weights"
SOURCE_RIGID_BODY = (
    "rigid-body relation: ball speed x sin(ball to face normal) = I/(m r) x spin, "
    "I/(m r) = 7.50 mm (Cross & Nathan 2007; Penner 2001)"
)
SOURCE_AXIS_IRON = (
    "D-plane tilt x (0.81385 + 0.00342 x dynamic loft), clean-room fit to one session's "
    "TM4 irons (16 shots, 0.014 deg)"
)
FACE_METHOD_RIGID_BODY = "rigid_body"
FACE_METHOD_INVERSION = "d_plane_inversion"
NOTE_RIGID_BODY_NOT_USED = "rigid-body relation not used"
CIRCULAR_SPIN_REASON = (
    "spin was calculated from face and loft (collision model): using it for them would be circular"
)
# Shot.spin_axis_source values for a calculated axis; one built on a model-geometry
# input starts with "model geometry" instead and names those inputs.
FACE_BASIS_INVERSION = "fitted launch-model inversion"
FACE_BASIS_RIGID_BODY = "rigid-body face"
AXIS_BASIS_IRON = "model: D-plane tilt x TM4 iron factor"
AXIS_BASIS_MODEL = "fitted collision model, centre-face strike"
AXIS_BASIS_GEAR = "fitted collision model + gear effect, Cross & Nathan 2007"
SPIN_AXIS_SOURCE_MODEL = f"calculated ({AXIS_BASIS_MODEL}, {FACE_BASIS_INVERSION})"
SPIN_AXIS_SOURCE_IRON = f"calculated ({AXIS_BASIS_IRON}, {FACE_BASIS_INVERSION})"
SPIN_AXIS_SOURCE_GEAR = f"calculated ({AXIS_BASIS_GEAR})"
# Shot.spin_method for the model's spin, by the labels of its inputs; an older
# record names the earlier fitted model or Penner 2001.
SPIN_METHOD_PENNER = "Penner 2001"
SPIN_METHOD_FITTED = "fitted collision model"
SPIN_METHOD_MODEL = "impulse collision model"
SPIN_METHOD_MODEL_GEOMETRY = f"{SPIN_METHOD_MODEL}; club inputs model geometry (D39)"
SPIN_METHOD_ESTIMATED = f"{SPIN_METHOD_MODEL}; an input estimated"

RAD_S_TO_RPM = collision_forward.RAD_S_TO_RPM

# Wood, Henrikson & Broadie 2018, Proceedings 2(6):249, Table 1:
# (category, horizontal ratio toward face angle, vertical ratio toward dynamic loft)
PING_2018_ANCHORS = (
    ("driver", 0.76, 0.83),
    ("7-iron", 0.69, 0.81),
    ("58-degree wedge", 0.61, 0.72),
)
# The paper gives only the wedge's loft; the driver and 7-iron anchors sit at the
# nominal lofts in clubs.physics.
PING_2018_WEDGE_LOFT_DEG = 58.0


def combined_frame(*frames: Optional[str]) -> Optional[str]:
    """The one frame values built from these angles are in, ``mixed``, or None if none known."""
    known = {frame for frame in frames if frame is not None}
    if not known:
        return None
    return known.pop() if len(known) == 1 else MIXED


def _anchor_lofts() -> tuple[float, float, float]:
    return (
        get_club_physics(ClubType.DRIVER).nominal_loft_deg,
        get_club_physics(ClubType.IRON_7).nominal_loft_deg,
        PING_2018_WEDGE_LOFT_DEG,
    )


# Penner 2001 (Am. J. Phys. 69, 563), Sec. II A
PENNER_BALL_MASS_KG = collision_forward.BALL_MASS_KG
PENNER_BALL_DIAMETER_M = BALL_DIAMETER_M
PENNER_HEAD_MASS_KG = 0.200
PENNER_INERTIA_FACTOR = 0.4  # I = (2/5) m r^2, uniform solid sphere
# Henrikson et al. 2020, Sec. 2.2: 0.40 urethane, 0.35 surlyn (sled on a grooved,
# blasted iron-type plate). The lower one bounds the rolling regime.
FRICTION_MU_DRY_MIN = 0.35

RIGID_BODY_BALL_RADIUS_M = BALL_RADIUS_M
RIGID_BODY_C_M = 7.5005e-3

IRON_AXIS_FACTOR_BASE = 0.81385
IRON_AXIS_FACTOR_PER_LOFT_DEG = 0.00342
_IRON_AXIS_CLUBS = frozenset(
    {
        ClubType.IRON_2,
        ClubType.IRON_3,
        ClubType.IRON_4,
        ClubType.IRON_5,
        ClubType.IRON_6,
        ClubType.IRON_7,
        ClubType.IRON_8,
        ClubType.IRON_9,
        ClubType.PW,
        ClubType.GW,
        ClubType.SW,
        ClubType.LW,
    }
)


@dataclass(frozen=True)
class Input:
    """One labelled shot input; a None or non-finite ``value`` is missing (``reason``).

    ``frame`` is the horizontal frame for a horizontal angle, else None.
    ``experimental``: the value is experimental under the owner's label policy
    (source_labels). The model has no experimental label (it would read one as missing),
    so such an input enters "estimated" with its reason and this flag; every output built
    on it is experimental in its record, and no simulator gets it.
    """

    value: Optional[float]
    label: Optional[str] = LABEL_MEASURED
    source: Optional[str] = None
    frame: Optional[str] = None
    reason: Optional[str] = None
    experimental: bool = False

    def __post_init__(self) -> None:
        if _finite(self.value):
            object.__setattr__(self, "value", float(self.value))

    @property
    def present(self) -> bool:
        return _finite(self.value) and self.label in _INPUT_LABELS

    def missing_reason(self, name: str) -> str:
        if self.value is not None and not _finite(self.value):
            return f"{name} not finite"
        if self.value is not None and self.label not in _INPUT_LABELS:
            return f"{name} is {self.label}, not measured or calculated"
        return self.reason or f"{name} not measured"


@dataclass(frozen=True)
class Metric:
    """One output: value, status, source, unit, frame, and why it is missing."""

    value: Optional[float]
    status: str
    source: Optional[str]
    unit: str
    frame: Optional[str] = None
    reason: Optional[str] = None
    note: Optional[str] = None

    @property
    def present(self) -> bool:
        return self.value is not None and self.status != STATUS_NOT_MEASURED

    def to_dict(self) -> dict:
        return asdict(self)


def _not_measured(unit: str, reason: str, source: Optional[str] = None) -> Metric:
    return Metric(value=None, status=STATUS_NOT_MEASURED, source=source, unit=unit, reason=reason)


def _status_from(*labels: Optional[str]) -> str:
    """The weakest input label: estimated, then model geometry, else calculated."""
    if LABEL_ESTIMATED in labels:
        return STATUS_ESTIMATED
    if LABEL_MODEL_GEOMETRY in labels or STATUS_MODEL_GEOMETRY in labels:
        return STATUS_MODEL_GEOMETRY
    return STATUS_CALCULATED


def _estimated_reason(*sources) -> Optional[str]:
    """The reasons of the estimated inputs (or outputs) a value is built on, or None."""
    reasons = []
    for source in sources:
        label = getattr(source, "label", None) or getattr(source, "status", None)
        reason = getattr(source, "reason", None)
        if label == LABEL_ESTIMATED and reason and reason not in reasons:
            reasons.append(reason)
    return "; ".join(reasons) or None


def _frame_note(frame: Optional[str]) -> Optional[str]:
    if frame == MIXED:
        return "horizontal inputs in two frames (unit boresight and target line)"
    return None


@dataclass(frozen=True)
class LaunchWeights:
    """Fraction of the way from club direction to face, per plane (Wood et al. 2018)."""

    horizontal: float
    vertical: float
    category: str
    basis: str


def club_nominal_loft_deg(club: ClubType) -> float:
    return get_club_physics(club).nominal_loft_deg


def weights_for_loft(loft_deg: float) -> LaunchWeights:
    """Piecewise-linear in nominal loft between the paper's three anchors, clamped."""
    lofts = _anchor_lofts()
    anchors = [(loft, name, h, v) for loft, (name, h, v) in zip(lofts, PING_2018_ANCHORS)]
    if loft_deg <= anchors[0][0]:
        _, name, h, v = anchors[0]
        return LaunchWeights(h, v, name, f"Wood et al. 2018 Table 1, {name}")
    if loft_deg >= anchors[-1][0]:
        _, name, h, v = anchors[-1]
        return LaunchWeights(h, v, name, f"Wood et al. 2018 Table 1, {name}")
    for (lo, lo_name, lo_h, lo_v), (hi, hi_name, hi_h, hi_v) in zip(anchors, anchors[1:]):
        if lo <= loft_deg <= hi:
            if loft_deg == lo:
                return LaunchWeights(lo_h, lo_v, lo_name, f"Wood et al. 2018 Table 1, {lo_name}")
            if loft_deg == hi:
                return LaunchWeights(hi_h, hi_v, hi_name, f"Wood et al. 2018 Table 1, {hi_name}")
            t = (loft_deg - lo) / (hi - lo)
            return LaunchWeights(
                lo_h + t * (hi_h - lo_h),
                lo_v + t * (hi_v - lo_v),
                f"{lo_name}-{hi_name}",
                f"Wood et al. 2018 Table 1, interpolated in nominal loft "
                f"({loft_deg:g} deg between {lo:g} and {hi:g})",
            )
    raise AssertionError("unreachable")  # pragma: no cover


def weights_for_club(club: ClubType) -> Optional[LaunchWeights]:
    """The club's category weights, or None for an unknown club (no category)."""
    if club is None or club is ClubType.UNKNOWN:
        return None
    return weights_for_loft(club_nominal_loft_deg(club))


def forward_launch(
    *,
    face_angle_deg: float,
    dynamic_loft_deg: float,
    club_path_deg: float,
    attack_angle_deg: float,
    weights: LaunchWeights,
) -> tuple[float, float]:
    """(launch direction, vertical launch) the paper's ratios give for a delivery."""
    launch_h = club_path_deg + weights.horizontal * (face_angle_deg - club_path_deg)
    launch_v = attack_angle_deg + weights.vertical * (dynamic_loft_deg - attack_angle_deg)
    return launch_h, launch_v


def invert_d_plane(
    *,
    launch_horizontal_deg: float,
    launch_vertical_deg: float,
    club_path_deg: float,
    attack_angle_deg: float,
    weights: LaunchWeights,
) -> tuple[float, float]:
    """(face angle, dynamic loft) from the launch and the club's direction."""
    face = club_path_deg + (launch_horizontal_deg - club_path_deg) / weights.horizontal
    loft = attack_angle_deg + (launch_vertical_deg - attack_angle_deg) / weights.vertical
    return face, loft


def spin_built_from_face(spin: Input) -> bool:
    """Whether a spin input is the collision model's own spin, built from face and loft."""
    source = spin.source or ""
    return spin.label != LABEL_MEASURED and any(
        method in source for method in (SPIN_METHOD_MODEL, SPIN_METHOD_FITTED, SPIN_METHOD_PENNER)
    )


def rigid_body_face(
    *,
    ball_speed_mph: Input,
    spin_rpm: Input,
    launch_horizontal: Input,
    launch_vertical: Input,
    club_path: Input,
    attack_angle: Input,
) -> tuple[Metric, Metric]:
    """(face angle, dynamic loft) from the ball and the club's direction.

    The face normal is ``cos(theta) b + sin(theta) u``: b the ball's launch direction,
    u the unit vector in the plane of b and the club direction v, normal to b and
    pointing away from v, and ``sin(theta) = c spin / ball speed``.
    """
    inputs = {
        "ball speed": ball_speed_mph,
        "spin rate": spin_rpm,
        "launch direction": launch_horizontal,
        "vertical launch": launch_vertical,
        "club path": club_path,
        "attack angle": attack_angle,
    }

    def refused(reason: str) -> tuple[Metric, Metric]:
        return (
            _not_measured("deg", reason, SOURCE_RIGID_BODY),
            _not_measured("deg", reason, SOURCE_RIGID_BODY),
        )

    for name, value in inputs.items():
        if not value.present:
            return refused(value.missing_reason(name))
    if spin_built_from_face(spin_rpm):
        return refused(CIRCULAR_SPIN_REASON)
    if ball_speed_mph.value <= 0.0:
        return refused("ball speed not positive")
    if spin_rpm.value < 0.0:
        return refused("spin rate negative")
    b = metric_definitions.ball_direction(launch_vertical.value, launch_horizontal.value)
    v = metric_definitions.club_direction(attack_angle.value, club_path.value)
    if b is None or v is None:
        return refused("launch or club direction beyond vertical")
    sin_theta = (
        RIGID_BODY_C_M * (spin_rpm.value / RAD_S_TO_RPM) / (ball_speed_mph.value * MPH_TO_M_S)
    )
    if sin_theta > 1.0:
        return refused("spin too high for the ball speed: no face satisfies the relation")
    along = math.fsum(p * q for p, q in zip(v, b))
    u = [p * along - q for p, q in zip(b, v)]
    length = math.sqrt(math.fsum(p * p for p in u))
    if length < 1e-12 and sin_theta > 0.0:
        return refused("ball launched along the club's direction: no D plane")
    u = [p / length for p in u] if length >= 1e-12 else [0.0, 0.0, 0.0]
    cos_theta = math.sqrt(1.0 - sin_theta * sin_theta)
    normal = [cos_theta * p + sin_theta * q for p, q in zip(b, u)]
    frame = combined_frame(club_path.frame, launch_horizontal.frame)
    status = _status_from(*(value.label for value in inputs.values()))
    reason = _estimated_reason(*inputs.values())
    face = Metric(
        value=math.degrees(math.atan2(normal[1], normal[0])),
        status=status,
        source=SOURCE_RIGID_BODY,
        unit="deg",
        frame=frame,
        note=_frame_note(frame),
        reason=reason,
    )
    loft = Metric(
        value=math.degrees(math.asin(max(-1.0, min(1.0, normal[2])))),
        status=status,
        source=SOURCE_RIGID_BODY,
        unit="deg",
        reason=reason,
    )
    return face, loft


@dataclass(frozen=True)
class PennerImpact:
    restitution: float
    normal_speed_m_s: float
    parallel_speed_m_s: float
    launch_speed_m_s: float
    launch_angle_from_club_deg: float  # Eq. (10): theta - atan(v_p / v_n)
    spin_rad_s: float
    spin_rpm: float
    required_friction: float  # v_p / v_n, the Coulomb mu that rolling needs


def penner_impact(
    club_speed_m_s: float,
    theta_deg: float,
    *,
    head_mass_kg: float = PENNER_HEAD_MASS_KG,
    ball_mass_kg: float = PENNER_BALL_MASS_KG,
    ball_diameter_m: float = PENNER_BALL_DIAMETER_M,
) -> PennerImpact:
    """Penner 2001 Eqs. (5), (7)-(11) for a clubhead speed (m/s) and angle theta."""
    theta = math.radians(theta_deg)
    radius = ball_diameter_m / 2.0
    mass_ratio = ball_mass_kg / head_mass_kg
    inertia_term = 1.0 / PENNER_INERTIA_FACTOR  # m r^2 / I
    restitution = 0.86 - 0.0029 * club_speed_m_s * math.cos(theta)
    v_normal = (1.0 + restitution) * club_speed_m_s * math.cos(theta) / (1.0 + mass_ratio)
    v_parallel = club_speed_m_s * math.sin(theta) / (1.0 + mass_ratio + inertia_term)
    spin = v_parallel / (PENNER_INERTIA_FACTOR * radius)  # m v_p r / I
    return PennerImpact(
        restitution=restitution,
        normal_speed_m_s=v_normal,
        parallel_speed_m_s=v_parallel,
        launch_speed_m_s=math.hypot(v_normal, v_parallel),
        launch_angle_from_club_deg=theta_deg - math.degrees(math.atan2(v_parallel, v_normal)),
        spin_rad_s=spin,
        spin_rpm=spin * RAD_S_TO_RPM,
        required_friction=v_parallel / v_normal if v_normal > 0 else math.inf,
    )


def calculated_spin(*, club_speed: Input, spin_loft: Metric) -> Metric:
    """Calculated total spin (rpm) from club speed (mph) and spin loft, Penner 2001."""
    if not spin_loft.present:
        return _not_measured("rpm", f"spin loft not measured ({spin_loft.reason})", SOURCE_PENNER)
    if not club_speed.present:
        return _not_measured("rpm", club_speed.missing_reason("club speed"), SOURCE_PENNER)
    if club_speed.value <= 0.0:
        return _not_measured("rpm", "club speed not positive", SOURCE_PENNER)
    if not 0.0 <= spin_loft.value < 90.0:
        return _not_measured("rpm", "spin loft outside 0-90 deg", SOURCE_PENNER)
    impact = penner_impact(club_speed.value * MPH_TO_M_S, spin_loft.value)
    if impact.required_friction > FRICTION_MU_DRY_MIN:
        return _not_measured(
            "rpm",
            f"spin loft {spin_loft.value:.1f} deg is outside the rolling regime of Penner's "
            f"model (needs mu {impact.required_friction:.2f} > {FRICTION_MU_DRY_MIN}, "
            "the lower dry value measured by Henrikson et al. 2020)",
            SOURCE_PENNER,
        )
    return Metric(
        value=impact.spin_rpm,
        status=_status_from(club_speed.label, spin_loft.status),
        source=SOURCE_PENNER,
        unit="rpm",
        frame=spin_loft.frame,
        note=_frame_note(spin_loft.frame),
        reason=_estimated_reason(club_speed, spin_loft),
    )


def cross_nathan_gear_spin_rad_s(
    *,
    normal_speed_m_s: float,
    restitution: float,
    cg_depth_m: float,
    impact_offset_m: float,
    head_moi_kg_m2: float,
    head_mass_kg: float,
    ball_mass_kg: float = PENNER_BALL_MASS_KG,
    ball_radius_m: float = PENNER_BALL_DIAMETER_M / 2.0,
) -> float:
    """Cross & Nathan 2007 Eq. (6): ball spin from the gear effect (rad/s).

        omega = -(1 + e) d b v_y1 / (1.4 r [b^2 + I_B (1/m + 1/M)])

    d: centre-of-mass depth behind the face; b: impact offset along the face; I_B: head
    inertia about its centre of mass. Positive b gives negative (CCW from above) spin.
    """
    denominator = (
        1.4
        * ball_radius_m
        * (impact_offset_m**2 + head_moi_kg_m2 * (1.0 / ball_mass_kg + 1.0 / head_mass_kg))
    )
    return -(1.0 + restitution) * cg_depth_m * impact_offset_m * normal_speed_m_s / denominator


def uses_iron_axis_model(club: Optional[ClubType]) -> bool:
    """Irons and wedges take the iron spin-axis model; other clubs the D-plane tilt."""
    return club in _IRON_AXIS_CLUBS


def iron_spin_axis(d_plane_tilt: Metric, dynamic_loft: Metric) -> Metric:
    """Iron spin axis (deg): tan(axis) = tan(tilt) x (0.81385 + 0.00342 x dynamic loft)."""
    if not d_plane_tilt.present:
        return _not_measured(
            "deg", f"D-plane tilt not measured ({d_plane_tilt.reason})", SOURCE_AXIS_IRON
        )
    if not dynamic_loft.present:
        return _not_measured(
            "deg", f"dynamic loft not measured ({dynamic_loft.reason})", SOURCE_AXIS_IRON
        )
    factor = IRON_AXIS_FACTOR_BASE + IRON_AXIS_FACTOR_PER_LOFT_DEG * dynamic_loft.value
    value = math.degrees(math.atan(math.tan(math.radians(d_plane_tilt.value)) * factor))
    return Metric(
        value=value,
        status=_status_from(d_plane_tilt.status, dynamic_loft.status),
        source=SOURCE_AXIS_IRON,
        unit="deg",
        frame=d_plane_tilt.frame,
        note=_frame_note(d_plane_tilt.frame),
        reason=_estimated_reason(d_plane_tilt, dynamic_loft),
    )


def spin_axis(
    d_plane_tilt: Metric,
    spin_rate: Optional[Metric] = None,
    *,
    gear_sidespin_rpm: Optional[float] = None,
    no_gear_note: str = NOTE_NO_GEAR,
) -> Metric:
    """Spin axis (deg, + = curves right): the D-plane tilt, plus gear effect when given.

    ``gear_sidespin_rpm`` (needs a measured impact location) is in the spin-axis sign; the
    tilted axis is atan2(S sin t + g, S cos t) for spin S at tilt t.
    """
    if not d_plane_tilt.present:
        return _not_measured(
            "deg", f"D-plane tilt not measured ({d_plane_tilt.reason})", SOURCE_AXIS
        )
    frame_note = _frame_note(d_plane_tilt.frame)
    if gear_sidespin_rpm is None:
        note = no_gear_note if frame_note is None else f"{no_gear_note}; {frame_note}"
        return Metric(
            value=d_plane_tilt.value,
            status=_status_from(d_plane_tilt.status),
            source=SOURCE_AXIS,
            unit="deg",
            frame=d_plane_tilt.frame,
            note=note,
            reason=_estimated_reason(d_plane_tilt),
        )
    if spin_rate is None or not spin_rate.present:
        return _not_measured("deg", "gear effect needs the calculated spin rate", SOURCE_AXIS)
    tilt = math.radians(d_plane_tilt.value)
    back = spin_rate.value * math.cos(tilt)
    side = spin_rate.value * math.sin(tilt) + gear_sidespin_rpm
    return Metric(
        value=math.degrees(math.atan2(side, back)),
        status=_status_from(d_plane_tilt.status, spin_rate.status),
        source=f"{SOURCE_AXIS} plus {SOURCE_GEAR}",
        unit="deg",
        frame=d_plane_tilt.frame,
        note=frame_note,
        reason=_estimated_reason(d_plane_tilt, spin_rate),
    )


_INPUT_NAMES = {
    "club_path": "club path",
    "attack_angle": "attack angle",
    "launch_horizontal": "launch direction",
    "launch_vertical": "vertical launch",
    "club_speed_mph": "club speed",
    "ball_speed_mph": "ball speed",
    "spin_rpm": "spin rate",
    "impact_offset_mm": "impact offset",
    "impact_height_mm": "impact height",
}
# club_arc's arc sources (ARC_PRIOR_SOURCE, ARC_FIT_SOURCE), in words
_SOURCE_NAMES = {
    "club_arc:published_prior": "the arc prior",
    "club_arc:shot_fit": "the arc fit",
}


def model_geometry_inputs(inputs: dict) -> list[str]:
    """Each present model-geometry input, named with its source, in input order.

    ``inputs`` maps names to ``Input`` or their recorded dicts (``CollisionResult.to_dict()``).
    """
    named = []
    for key, value in (inputs or {}).items():
        record = asdict(value) if isinstance(value, Input) else dict(value or {})
        if record.get("label") != LABEL_MODEL_GEOMETRY or record.get("value") is None:
            continue
        source = record.get("source")
        named.append(
            f"{_INPUT_NAMES.get(key, key)} from {_SOURCE_NAMES.get(source, source or 'an unnamed source')}"
        )
    return named


def _axis_basis(axis: Metric, face_method: Optional[str]) -> str:
    if SOURCE_GEAR in (axis.source or ""):
        return AXIS_BASIS_GEAR
    face = FACE_BASIS_RIGID_BODY if face_method == FACE_METHOD_RIGID_BODY else FACE_BASIS_INVERSION
    if axis.source == SOURCE_AXIS_IRON:
        return f"{AXIS_BASIS_IRON}, {face}"
    if axis.source == SOURCE_AXIS_MODEL:
        return f"{AXIS_BASIS_MODEL}, {face}"
    return f"D-plane tilt, {face}"


def shot_spin_axis_source(
    axis: Metric, inputs: Optional[dict] = None, face_method: Optional[str] = None
) -> str:
    """The Shot.spin_axis_source string for a calculated or model-geometry spin axis."""
    basis = _axis_basis(axis, face_method)
    if axis.status == STATUS_ESTIMATED:
        return f"{SPIN_AXIS_ESTIMATED_PREFIX} ({basis}; {axis.reason or 'an input estimated'})"
    if axis.status == STATUS_MODEL_GEOMETRY:
        named = ", ".join(model_geometry_inputs(inputs or {})) or "inputs unnamed"
        return f"{SPIN_AXIS_MODEL_GEOMETRY_PREFIX} ({basis}; {named})"
    return f"calculated ({basis})"


def shot_spin_method(status: str, input_labels) -> str:
    """The Shot.spin_method string for the model's spin, naming its inputs' weakest label."""
    if status == STATUS_ESTIMATED:
        return SPIN_METHOD_ESTIMATED
    if status == STATUS_MODEL_GEOMETRY:
        return SPIN_METHOD_MODEL_GEOMETRY
    weakest = LABEL_CALCULATED if LABEL_CALCULATED in set(input_labels) else LABEL_MEASURED
    return f"{SPIN_METHOD_MODEL}; club inputs {weakest}"


GYRO_REASON = (
    "the collision model is planar: Penner's spin vector is normal to the D-plane, "
    "so gyro is zero by construction, not a result"
)


def _input_record(name: str, value: Input) -> dict:
    """An input as recorded: a missing or non-finite value is None, with the reason."""
    record = asdict(value)
    if not value.present:
        record["value"] = None
        record["reason"] = value.missing_reason(name.replace("_", " "))
    return record


@dataclass
class CollisionResult:
    metrics: dict[str, Metric]
    weights: Optional[LaunchWeights]
    inputs: dict[str, Input] = field(default_factory=dict)
    face_method: Optional[str] = None
    forward: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "model_version": MODEL_VERSION,
            "face_method": self.face_method,
            "weights": asdict(self.weights) if self.weights is not None else None,
            "inputs": {name: _input_record(name, value) for name, value in self.inputs.items()},
            "metrics": {name: metric.to_dict() for name, metric in self.metrics.items()},
            "forward": self.forward,
        }


def _with_note(metric: Metric, note: str) -> Metric:
    if not metric.present:
        return metric
    return replace(metric, note=f"{note}; {metric.note}" if metric.note else note)


def _inverted_face_and_loft(
    club: ClubType, inputs: dict[str, Input]
) -> tuple[Metric, Metric, Optional[str]]:
    """(face, loft, frame) from the fitted launch model's inverse, each labelled."""
    path, attack = inputs["club_path"], inputs["attack_angle"]
    launch_h, launch_v = inputs["launch_horizontal"], inputs["launch_vertical"]
    frame = combined_frame(path.frame, launch_h.frame)
    if collision_forward.club_category(club) is None:
        gone = _not_measured("deg", "no club category: club unknown", SOURCE_INVERSION)
        return gone, gone, frame
    vertical = {"vertical launch": launch_v, "attack angle": attack}
    horizontal = {"launch direction": launch_h, "club path": path}
    absent = [value.missing_reason(name) for name, value in vertical.items() if not value.present]
    if absent:
        loft = _not_measured("deg", "; ".join(absent), SOURCE_INVERSION)
        if not (launch_h.present and path.present):
            return loft, loft, frame
        face = path.value + (launch_h.value - path.value) / weights_for_club(club).horizontal
        status = _status_from(path.label, launch_h.label)
        reason = _estimated_reason(path, launch_h)
        note = (
            NOTE_HORIZONTAL_RATIO
            if frame != MIXED
            else f"{NOTE_HORIZONTAL_RATIO}; {_frame_note(frame)}"
        )
        return (
            Metric(face, status, SOURCE_HORIZONTAL_RATIO, "deg", frame, reason, note),
            loft,
            frame,
        )
    missing = [
        value.missing_reason(name) for name, value in horizontal.items() if not value.present
    ]
    solved = collision_forward.face_and_loft_from_launch(
        club=club,
        launch_vertical_deg=launch_v.value,
        launch_horizontal_deg=launch_h.value if not missing else 0.0,
        club_path_deg=path.value if not missing else 0.0,
        attack_angle_deg=attack.value,
    )
    if solved is None:
        gone = _not_measured(
            "deg", "launch along the club's direction: no D plane", SOURCE_INVERSION
        )
        return gone, gone, frame
    face_value, loft_value = solved
    used = {**vertical, **({} if missing else horizontal)}
    status = _status_from(*(value.label for value in used.values()))
    reason = _estimated_reason(*used.values())
    loft = Metric(loft_value, status, SOURCE_INVERSION, "deg", reason=reason)
    if missing:
        face = _not_measured("deg", "; ".join(missing), SOURCE_INVERSION)
        return face, _with_note(loft, NOTE_VERTICAL_PLANE), frame
    face = Metric(
        face_value, status, SOURCE_INVERSION, "deg", frame, reason=reason, note=_frame_note(frame)
    )
    return face, loft, frame


def _face_and_loft(
    club: ClubType,
    inputs: dict[str, Input],
) -> tuple[Metric, Metric, Optional[str], Optional[str]]:
    """(face, loft, frame, method): rigid-body with a measured spin, else the inversion."""
    path, attack = inputs["club_path"], inputs["attack_angle"]
    launch_h, launch_v = inputs["launch_horizontal"], inputs["launch_vertical"]
    spin = inputs["spin_rpm"]
    if spin.present and spin.label == LABEL_MEASURED:
        face, loft = rigid_body_face(
            ball_speed_mph=inputs["ball_speed_mph"],
            spin_rpm=spin,
            launch_horizontal=launch_h,
            launch_vertical=launch_v,
            club_path=path,
            attack_angle=attack,
        )
        if face.present and loft.present:
            return face, loft, face.frame, FACE_METHOD_RIGID_BODY
        why = face.reason
    elif spin.present:
        why = (
            CIRCULAR_SPIN_REASON
            if spin_built_from_face(spin)
            else f"spin is {spin.label}, not measured"
        )
    else:
        why = spin.missing_reason("spin rate")
    face, loft, frame = _inverted_face_and_loft(club, inputs)
    note = f"{NOTE_RIGID_BODY_NOT_USED}: {why}"
    method = FACE_METHOD_INVERSION if face.present or loft.present else None
    return _with_note(face, note), _with_note(loft, note), frame, method


def _measured(value: Optional[Input]) -> bool:
    return value is not None and value.present and value.label == LABEL_MEASURED


def _delivery_metrics(face, loft, attack_angle, club_path, frame):
    """(spin loft, D-plane tilt) of the delivery, or not measured with the reason."""
    if not (face.present and loft.present and attack_angle.present and club_path.present):
        missing = face if not face.present else loft
        reason = missing.reason if not missing.present else "club delivery not measured"
        return _not_measured("deg", reason, SOURCE_D37), _not_measured("deg", reason, SOURCE_D37)
    status = _status_from(face.status, loft.status, attack_angle.label, club_path.label)
    why = _estimated_reason(face, loft, attack_angle, club_path)
    args = (loft.value, face.value, attack_angle.value, club_path.value)
    loft_value = metric_definitions.spin_loft_deg(*args)
    tilt_value = metric_definitions.d_plane_tilt_deg(*args)
    note = _frame_note(frame)
    spin_loft = (
        Metric(loft_value, status, SOURCE_D37, "deg", frame, reason=why, note=note)
        if loft_value is not None and math.isfinite(loft_value)
        else _not_measured("deg", "spin loft undefined for this delivery", SOURCE_D37)
    )
    tilt = (
        Metric(tilt_value, status, SOURCE_D37, "deg", frame, reason=why, note=note)
        if tilt_value is not None and math.isfinite(tilt_value)
        else _not_measured(
            "deg", "no D plane: club direction parallel to the face normal", SOURCE_D37
        )
    )
    return spin_loft, tilt


def _forward_impact(club, club_speed, attack, path, face, loft, location, handedness):
    if not (club_speed.present and club_speed.value > 0.0 and face.present and loft.present):
        return None
    return collision_forward.forward(
        club=club,
        club_speed_mph=club_speed.value,
        attack_angle_deg=attack.value,
        club_path_deg=path.value,
        face_angle_deg=face.value,
        dynamic_loft_deg=loft.value,
        impact_offset_mm=location.get("impact_offset_mm"),
        impact_height_mm=location.get("impact_height_mm"),
        handedness=handedness,
    )


def contact_flags(
    club: Optional[ClubType],
    attack_angle: Optional[Input],
    impact_height_mm: Optional[float],
    low_point: Optional[Input],
) -> list[str]:
    """Why a strike may be a flier or heavy (empty for a clean one); irons and wedges only."""
    if club not in _IRON_AXIS_CLUBS:
        return []
    flags = []
    if attack_angle is not None and attack_angle.present:
        if attack_angle.value >= CONTACT_ATTACK_LIMIT_DEG:
            flags.append(f"attack angle {attack_angle.value:+.1f} deg, not below -2 deg")
    if impact_height_mm is not None and impact_height_mm > CONTACT_HEIGHT_LIMIT_MM:
        flags.append(f"impact {impact_height_mm:.0f} mm above the face centre")
    if low_point is not None and not low_point.present:
        flags.append("no low point")
    return flags


def _spin_metric(impact, centre: Metric, club_speed: Input, spin_loft: Metric, located: bool):
    if not spin_loft.present:
        return _not_measured("rpm", f"spin loft not measured ({spin_loft.reason})", SOURCE_SPIN)
    if not club_speed.present:
        return _not_measured("rpm", club_speed.missing_reason("club speed"), SOURCE_SPIN)
    if impact is None:
        reason = (
            "club speed not positive" if club_speed.value <= 0.0 else "delivery outside the model"
        )
        return _not_measured("rpm", reason, SOURCE_SPIN)
    value = impact.spin_rpm
    if located and centre.present:
        value, _ = collision_forward.combine_spin(
            impact.centre_spin_rpm, centre.value, impact.gear_back_rpm, impact.gear_side_rpm
        )
    note = _frame_note(spin_loft.frame)
    if not located:
        note = NOTE_NO_GEAR if note is None else f"{NOTE_NO_GEAR}; {note}"
    return Metric(
        value=value,
        status=_status_from(club_speed.label, spin_loft.status),
        source=SOURCE_SPIN,
        unit="rpm",
        frame=spin_loft.frame,
        note=note,
        reason=_estimated_reason(club_speed, spin_loft),
    )


def _centre_axis(club, tilt: Metric, loft: Metric, attack, path, face) -> Metric:
    if uses_iron_axis_model(club):
        return iron_spin_axis(tilt, loft)
    if not tilt.present:
        return _not_measured("deg", f"D-plane tilt not measured ({tilt.reason})", SOURCE_AXIS_MODEL)
    impact = collision_forward.forward(
        club=club,
        club_speed_mph=1.0,
        attack_angle_deg=attack.value,
        club_path_deg=path.value,
        face_angle_deg=face.value,
        dynamic_loft_deg=loft.value,
    )
    if impact is None:
        return _not_measured("deg", "delivery outside the model", SOURCE_AXIS_MODEL)
    return Metric(
        value=impact.centre_spin_axis_deg,
        status=_status_from(tilt.status, loft.status),
        source=SOURCE_AXIS_MODEL,
        unit="deg",
        frame=tilt.frame,
        note=_frame_note(tilt.frame),
        reason=_estimated_reason(tilt, loft),
    )


def _axis_metric(centre: Metric, impact, spin: Metric, located: bool) -> Metric:
    if not centre.present:
        return centre
    if not located:
        note = NOTE_NO_GEAR if centre.note is None else f"{NOTE_NO_GEAR}; {centre.note}"
        return replace(centre, note=note)
    if impact is None or not spin.present:
        return _not_measured("deg", "gear effect needs the calculated spin rate", centre.source)
    _, value = collision_forward.combine_spin(
        impact.centre_spin_rpm, centre.value, impact.gear_back_rpm, impact.gear_side_rpm
    )
    return Metric(
        value=value,
        status=_status_from(centre.status, spin.status),
        source=f"{centre.source} plus {SOURCE_GEAR}",
        unit="deg",
        frame=centre.frame,
        note=_frame_note(centre.frame),
        reason=_estimated_reason(centre, spin),
    )


def _spin_index(club, spin_rpm: Input, club_speed: Input, spin_loft: Metric) -> Metric:
    if not _measured(spin_rpm):
        why = spin_rpm.missing_reason("spin rate") if not spin_rpm.present else spin_rpm.label
        return _not_measured(
            "%", f"the index needs a measured spin (spin: {why})", SOURCE_SPIN_INDEX
        )
    if not spin_loft.present:
        return _not_measured("%", f"spin loft not measured ({spin_loft.reason})", SOURCE_SPIN_INDEX)
    if not club_speed.present:
        return _not_measured("%", club_speed.missing_reason("club speed"), SOURCE_SPIN_INDEX)
    expected = collision_forward.expected_spin_rpm(
        club=club, club_speed_mph=club_speed.value, spin_loft_deg=spin_loft.value
    )
    if not expected:
        return _not_measured("%", "no expected spin for this club or delivery", SOURCE_SPIN_INDEX)
    return Metric(
        value=100.0 * spin_rpm.value / expected,
        status=_status_from(spin_rpm.label, club_speed.label, spin_loft.status),
        source=SOURCE_SPIN_INDEX,
        unit="%",
        reason=_estimated_reason(spin_rpm, club_speed, spin_loft),
        note=_index_note(club),
    )


def _index_note(club) -> Optional[str]:
    return None if collision_forward.has_fitted_index(club) else NOTE_NO_FITTED_INDEX


def _smash_index(club, ball_speed: Input, club_speed: Input, spin_loft, face, loft):
    if not ball_speed.present:
        return _not_measured("%", ball_speed.missing_reason("ball speed"), SOURCE_SMASH_INDEX)
    if not club_speed.present:
        return _not_measured("%", club_speed.missing_reason("club speed"), SOURCE_SMASH_INDEX)
    if not (spin_loft.present and face.present and loft.present):
        missing = next(metric for metric in (face, loft, spin_loft) if not metric.present)
        return _not_measured(
            "%", f"club delivery not measured ({missing.reason})", SOURCE_SMASH_INDEX
        )
    expected = collision_forward.expected_ball_speed_mph(
        club=club, club_speed_mph=club_speed.value, spin_loft_deg=spin_loft.value
    )
    if not expected:
        return _not_measured(
            "%", "no expected ball speed for this club or delivery", SOURCE_SMASH_INDEX
        )
    return Metric(
        value=100.0 * ball_speed.value / expected,
        status=_status_from(ball_speed.label, club_speed.label, spin_loft.status, face.status),
        source=SOURCE_SMASH_INDEX,
        unit="%",
        reason=_estimated_reason(ball_speed, club_speed, spin_loft, face),
        note=_index_note(club),
    )


def _forward_record(impact) -> Optional[dict]:
    if impact is None:
        return None
    return {
        "model": collision_forward.MODEL_NAME,
        "ball_speed_mph": impact.ball_speed_mph,
        "launch_vertical_deg": impact.launch_vertical_deg,
        "launch_horizontal_deg": impact.launch_horizontal_deg,
        "spin_rpm": impact.spin_rpm,
        "spin_axis_deg": impact.spin_axis_deg,
        "impact_location": impact.impact_location,
    }


def evaluate(
    *,
    club: ClubType,
    club_path: Input,
    attack_angle: Input,
    launch_horizontal: Input,
    launch_vertical: Input,
    club_speed_mph: Input,
    ball_speed_mph: Optional[Input] = None,
    spin_rpm: Optional[Input] = None,
    impact_offset_mm: Optional[Input] = None,
    impact_height_mm: Optional[Input] = None,
    handedness: Optional[str] = None,
    low_point_distance_mm: Optional[Input] = None,
) -> CollisionResult:
    """Every collision-model metric for one shot, from that shot's inputs.

    ``ball_speed_mph`` is the full speed; impact location is mm (+ toe, + up) and drives
    the gear effect; ``handedness`` is RH unless "LH". ``low_point_distance_mm`` feeds only
    the contact note (None: no camera club data).
    """
    inputs = {
        "club_path": club_path,
        "attack_angle": attack_angle,
        "launch_horizontal": launch_horizontal,
        "launch_vertical": launch_vertical,
        "club_speed_mph": club_speed_mph,
    }
    ball_speed = ball_speed_mph or Input(None, reason="ball speed not given")
    spin_input = spin_rpm or Input(None, reason="spin rate not measured")
    face, loft, frame, face_method = _face_and_loft(
        club, {**inputs, "ball_speed_mph": ball_speed, "spin_rpm": spin_input}
    )
    if face_method == FACE_METHOD_RIGID_BODY:
        inputs.update({"ball_speed_mph": ball_speed, "spin_rpm": spin_input})

    if face.present and club_path.present:
        face_to_path = Metric(
            value=metric_definitions.face_to_path_deg(face.value, club_path.value),
            status=_status_from(face.status, club_path.label),
            source=SOURCE_D37,
            unit="deg",
            frame=frame,
            note=_frame_note(frame),
            reason=_estimated_reason(face, club_path),
        )
    else:
        face_to_path = _not_measured("deg", f"face angle not measured ({face.reason})", SOURCE_D37)

    spin_loft, tilt = _delivery_metrics(face, loft, attack_angle, club_path, frame)
    location = {
        name: value.value
        for name, value in (
            ("impact_offset_mm", impact_offset_mm),
            ("impact_height_mm", impact_height_mm),
        )
        if _measured(value)
    }
    impact = (
        _forward_impact(
            club, club_speed_mph, attack_angle, club_path, face, loft, location, handedness
        )
        if spin_loft.present
        else None
    )
    centre = (
        _centre_axis(club, tilt, loft, attack_angle, club_path, face)
        if tilt.present
        else _not_measured("deg", f"D-plane tilt not measured ({tilt.reason})", SOURCE_AXIS)
    )
    spin = _spin_metric(impact, centre, club_speed_mph, spin_loft, bool(location))
    flags = contact_flags(
        club, attack_angle, location.get("impact_height_mm"), low_point_distance_mm
    )
    if spin.present and flags:
        spin = _with_note(spin, f"{NOTE_CONTACT} ({'; '.join(flags)})")
    axis = _axis_metric(centre, impact, spin, bool(location))
    if location and axis.present:
        given = {"impact_offset_mm": impact_offset_mm, "impact_height_mm": impact_height_mm}
        inputs.update({name: given[name] for name in location})
    spin_index = _spin_index(club, spin_input, club_speed_mph, spin_loft)
    smash_index = _smash_index(club, ball_speed, club_speed_mph, spin_loft, face, loft)
    if spin_index.present:
        inputs["spin_rpm"] = spin_input
    if smash_index.present:
        inputs["ball_speed_mph"] = ball_speed
    gyro = _not_measured("deg", GYRO_REASON, SOURCE_PENNER)

    return CollisionResult(
        metrics={
            "face_angle": face,
            "dynamic_loft": loft,
            "spin_loft": spin_loft,
            "face_to_path": face_to_path,
            "d_plane_tilt": tilt,
            "spin_rate": spin,
            "spin_axis": axis,
            "gyro": gyro,
            "spin_index": spin_index,
            "smash_index": smash_index,
        },
        weights=weights_for_club(club),
        inputs=inputs,
        face_method=face_method,
        forward=_forward_record(impact),
    )
