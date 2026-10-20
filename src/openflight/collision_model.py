"""Published collision model for the club-face and spin metrics (Part E, item E2).

Every number here comes from a published source, read from the paper itself, or
from the named clean-room fit to the owner's TM4 export; the derivations and units
are in ``docs/how-it-works/collision-model.md``. Each shot
uses only its own inputs; a model constant is fixed, a shot's input never is. A
missing input makes every metric built on it "not measured", with the reason.

1. **Face angle and dynamic loft.** With a measured spin, from the ball and the
   club's direction by the rigid-body relation (``rigid_body_face``): the face
   normal lies in the plane of the ball's launch direction and the club's
   direction, beyond the ball from the club, at an angle theta from the ball with

       ball speed x sin(theta) = c x spin,   c = I / (m r)

   the tangential-impulse identity m v_t r = I omega (Cross & Nathan 2007, below;
   Penner 2001, Eq. (11)). c = 7.5005 mm (I = 0.352 m r^2, r = 21.335 mm), fitted
   in a clean-room study of the owner's TM4 export. The collision model's own
   spin is built from face and loft, so it never feeds them: without a measured
   spin, face and loft come from the D-plane inversion below, and say why.

   **D-plane inversion with published weights.** Wood, Henrikson & Broadie, "The
   Influence of Face Angle and Club Path on the Resultant Launch Angle of a Golf
   Ball", *Proceedings* 2(6):249 (2018),
   doi:10.3390/proceedings2060249, define the launch ratios per plane:

       horizontal ratio = (launch direction - club path) / (face angle - club path)
       vertical ratio   = (vertical launch - attack angle) / (dynamic loft - attack angle)

   and measure them (Table 1, 157 golfers, Vicon + Foresight GC2): horizontal
   76 % driver, 69 % 7-iron, 61 % 58 deg wedge; vertical 83 / 81 / 72 %. Inverted:

       face = path + (launch direction - path) / w_h
       loft = attack + (vertical launch - attack) / w_v

   Clubs between the paper's three categories interpolate linearly in nominal loft
   (``weights_for_loft``); outside them the nearest category's weights apply.
   Both PING papers support a ratio that falls as loft rises: Wood et al. 2018
   (Fig. 3c,d and Conclusions: "higher numbers when the dynamic loft is lower")
   and Henrikson, Wood, Broadie & Nuttall 2020, *Proceedings* 49(1):27,
   doi:10.3390/proceedings2020049027 (Fig. 6: "the launch ratio monotonically
   decreases for increasing loft" for dry contact). The linear shape between
   anchors is our choice, not the paper's; E8 refits it to TM4.

2. **Spin loft, face to path, D-plane tilt:** ``metric_definitions`` (D37).

3. **Calculated spin rate: Penner 2001.** A. R. Penner, "The physics of golf: The
   optimum loft of a driver", *Am. J. Phys.* 69, 563-568 (2001),
   doi:10.1119/1.1344164. Clubhead a free flat plate of mass M, ball a uniform
   solid sphere (I = 2/5 m r^2), ball rolling when it leaves the face. Eqs. (5),
   (7), (8), (10), (11):

       e     = 0.86 - 0.0029 v cos(theta)                    (v in m/s)
       v_n   = (1 + e) v cos(theta) / (1 + m/M)
       v_p   = v sin(theta) / (1 + m/M + m r^2 / I)
       omega = m v_p r / I                                    (rad/s)

   theta is the angle between the club's direction and the face normal: Penner's
   club moves horizontally, so it is his dynamic loft; in 3D it is the spin loft
   (the collision happens in the D plane). Constants as Penner states them:
   m = 45.9 g, diameter 4.27 cm, M = 200 g. Rolling needs Coulomb friction
   mu >= v_p / v_n; a spin loft needing more than 0.35, the lower of the dry
   ball-face values Henrikson et al. 2020 measured (0.35 surlyn, 0.40 urethane),
   is outside the model and is not calculated.

4. **Spin axis.** Irons and wedges: tan(axis) = tan(D-plane tilt) x (0.81385 +
   0.00342 x dynamic loft), the clean-room fit to the owner's TM4 irons.
   Drivers, woods and hybrids: the D-plane tilt (TrackMan's public definition:
   "the expected spin axis disregarding any gear effect"). With a measured
   impact offset a gear sidespin from R. Cross & A. M. Nathan, "Experimental
   study of the gear effect in ball collisions", *Am. J. Phys.* 75, 658-664
   (2007), doi:10.1119/1.2713788, Eq. (6), tilts it. Without one, the tilt
   alone, with the note "no impact location: gear effect not applied".

5. **Gyro:** Penner's collision is planar: the spin vector is normal to the D
   plane, so gyro is zero by construction, not a result. It is "not measured".

Labels: an output is ``calculated`` unless an input is ``model_geometry`` (D39),
which it then inherits. Horizontal outputs carry their inputs' frame
(``horizontal_frame``), ``mixed`` when the inputs disagree.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from typing import Optional

from . import metric_definitions
from .clubs import ClubType
from .clubs.physics import get_club_physics
from .horizontal_frame import MIXED, combined_frame
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

MODEL_VERSION = "e2-2026-10-20"

# Input labels (source_labels: measured, calculated, model geometry, estimated) and
# output statuses.
STATUS_CALCULATED = "calculated"
STATUS_MODEL_GEOMETRY = "model_geometry"
STATUS_ESTIMATED = "estimated"
STATUS_NOT_MEASURED = "not_measured"
_INPUT_LABELS = (LABEL_MEASURED, LABEL_CALCULATED, LABEL_MODEL_GEOMETRY, LABEL_ESTIMATED)

SOURCE_INVERSION = "D-plane inversion, Wood et al. 2018 weights"
SOURCE_D37 = "D37 definition (metric_definitions) of the calculated face and loft"
SOURCE_PENNER = "Penner 2001 collision model (Am. J. Phys. 69, 563)"
SOURCE_AXIS = "D-plane tilt (D37)"
SOURCE_GEAR = "Cross & Nathan 2007 gear effect (Am. J. Phys. 75, 658)"
NOTE_NO_GEAR = "no impact location: gear effect not applied"
NOTE_GEAR_NO_HEAD_MODEL = "measured impact location but no head model: gear effect not applied"
SOURCE_RIGID_BODY = (
    "rigid-body relation: ball speed x sin(ball to face normal) = I/(m r) x spin, "
    "I/(m r) = 7.50 mm (Cross & Nathan 2007; Penner 2001)"
)
SOURCE_AXIS_IRON = (
    "D-plane tilt x (0.81385 + 0.00342 x dynamic loft), clean-room fit to the owner's "
    "TM4 irons (16 shots, 0.014 deg)"
)
FACE_METHOD_RIGID_BODY = "rigid_body"
FACE_METHOD_INVERSION = "d_plane_inversion"
NOTE_RIGID_BODY_NOT_USED = "rigid-body relation not used"
CIRCULAR_SPIN_REASON = (
    "spin was calculated from face and loft (Penner 2001): using it for them would be circular"
)
# Shot.spin_axis_source for a calculated axis (read by E4's flight and the simulators;
# both start with "calculated"). An axis built on a model-geometry input (D39) starts
# with "model geometry" instead and names those inputs.
FACE_BASIS_INVERSION = "Wood et al. 2018 weights"
FACE_BASIS_RIGID_BODY = "rigid-body face"
AXIS_BASIS_TILT = f"D-plane tilt, {FACE_BASIS_INVERSION}"
AXIS_BASIS_IRON = "model: D-plane tilt x TM4 iron factor"
AXIS_BASIS_GEAR = "D-plane + gear effect, Cross & Nathan 2007"
SPIN_AXIS_SOURCE_TILT = f"calculated ({AXIS_BASIS_TILT})"
SPIN_AXIS_SOURCE_IRON = f"calculated ({AXIS_BASIS_IRON}, {FACE_BASIS_INVERSION})"
SPIN_AXIS_SOURCE_GEAR = f"calculated ({AXIS_BASIS_GEAR})"
# Shot.spin_method for the Penner spin, by the labels of the inputs it was built on.
SPIN_METHOD_PENNER = "Penner 2001"
SPIN_METHOD_MODEL_GEOMETRY = f"{SPIN_METHOD_PENNER}; club inputs model geometry (D39)"
SPIN_METHOD_ESTIMATED = f"{SPIN_METHOD_PENNER}; an input estimated"

MPH_TO_M_S = 0.44704
RAD_S_TO_RPM = 60.0 / (2.0 * math.pi)

# ---- Wood, Henrikson & Broadie 2018, Table 1 ----------------------------------------
# (category, horizontal ratio toward face angle, vertical ratio toward dynamic loft)
PING_2018_ANCHORS = (
    ("driver", 0.76, 0.83),
    ("7-iron", 0.69, 0.81),
    ("58-degree wedge", 0.61, 0.72),
)
# The paper states the wedge's loft (58 deg) but not the driver's or the 7-iron's
# ("a club loft ... appropriate for their swing speed"); those two anchors sit at the
# fork's nominal lofts for the same clubs (clubs.physics).
PING_2018_WEDGE_LOFT_DEG = 58.0


def _anchor_lofts() -> tuple[float, float, float]:
    return (
        get_club_physics(ClubType.DRIVER).nominal_loft_deg,
        get_club_physics(ClubType.IRON_7).nominal_loft_deg,
        PING_2018_WEDGE_LOFT_DEG,
    )


# ---- Penner 2001 constants (Sec. II A) -----------------------------------------------
PENNER_BALL_MASS_KG = 0.0459
PENNER_BALL_DIAMETER_M = 0.0427
PENNER_HEAD_MASS_KG = 0.200
PENNER_INERTIA_FACTOR = 0.4  # I = (2/5) m r^2, uniform solid sphere
# Henrikson et al. 2020, Sec. 2.2: 0.40 urethane, 0.35 surlyn (sled on a grooved,
# blasted iron-type plate). The lower one bounds the rolling regime.
FRICTION_MU_DRY_MIN = 0.35

RIGID_BODY_BALL_RADIUS_M = 0.021335
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
    """One shot's input with its label (measured / calculated / model_geometry).

    ``value`` None (or non-finite) is a missing input; ``reason`` says why.
    ``frame`` is the horizontal frame for a horizontal angle, else None.
    """

    value: Optional[float]
    label: Optional[str] = LABEL_MEASURED
    source: Optional[str] = None
    frame: Optional[str] = None
    reason: Optional[str] = None

    @property
    def present(self) -> bool:
        return (
            self.value is not None
            and isinstance(self.value, (int, float))
            and math.isfinite(self.value)
            and self.label in _INPUT_LABELS
        )

    def missing_reason(self, name: str) -> str:
        if self.value is not None and not (
            isinstance(self.value, (int, float)) and math.isfinite(self.value)
        ):
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
    """The weakest input label: estimated, then model geometry (D39), else calculated."""
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


# ---- weights -------------------------------------------------------------------------


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


# ---- D-plane inversion ---------------------------------------------------------------


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


# ---- rigid-body face from the ball ---------------------------------------------------


def spin_built_from_face(spin: Input) -> bool:
    """Whether a spin input is the collision model's own (Penner) spin, built from face."""
    return spin.label != LABEL_MEASURED and SPIN_METHOD_PENNER in (spin.source or "")


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


# ---- Penner 2001 ---------------------------------------------------------------------


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


# ---- spin axis and gear effect -------------------------------------------------------


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

    d: centre of mass depth behind the face; b: impact offset along the face;
    I_B: head moment of inertia about the rotation axis through its centre of mass.
    Sign as in the paper (Fig. 2): positive b gives a negative (counter-clockwise
    seen from above) ball spin. Normal incidence; the paper's Appendix treats
    oblique impacts.
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

    ``gear_sidespin_rpm`` is the gear effect's spin component about the vertical, in
    the spin-axis sign (+ curves right); it needs a measured impact location (E5) and
    the head's centre-of-mass depth and inertia. The D-plane spin (``spin_rate``) at
    tilt t has components S cos t (back) and S sin t (side), so the tilted axis is
    atan2(S sin t + g, S cos t).
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
}
# E1's arc sources (club_arc.ARC_PRIOR_SOURCE, ARC_FIT_SOURCE), in words
_SOURCE_NAMES = {
    "club_arc:published_prior": "the arc prior",
    "club_arc:shot_fit": "the arc fit",
}


def model_geometry_inputs(inputs: dict) -> list[str]:
    """Each present model-geometry input (D39), named with its source, in input order.

    ``inputs`` maps input names to ``Input`` or to their recorded dicts
    (``CollisionResult.to_dict()["inputs"]``).
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
    tilt = AXIS_BASIS_IRON if axis.source == SOURCE_AXIS_IRON else "D-plane tilt"
    return f"{tilt}, {face}"


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
    """The Shot.spin_method string for the Penner spin, naming its inputs' weakest label."""
    if status == STATUS_ESTIMATED:
        return SPIN_METHOD_ESTIMATED
    if status == STATUS_MODEL_GEOMETRY:
        return SPIN_METHOD_MODEL_GEOMETRY
    weakest = LABEL_CALCULATED if LABEL_CALCULATED in set(input_labels) else LABEL_MEASURED
    return f"{SPIN_METHOD_PENNER}; club inputs {weakest}"


GYRO_REASON = (
    "the collision model is planar: Penner's spin vector is normal to the D-plane, "
    "so gyro is zero by construction, not a result"
)


# ---- the whole model -----------------------------------------------------------------


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

    def to_dict(self) -> dict:
        return {
            "model_version": MODEL_VERSION,
            "face_method": self.face_method,
            "weights": asdict(self.weights) if self.weights is not None else None,
            "inputs": {name: _input_record(name, value) for name, value in self.inputs.items()},
            "metrics": {name: metric.to_dict() for name, metric in self.metrics.items()},
        }


def _face_angle(
    weights: Optional[LaunchWeights], path: Input, launch_h: Input
) -> tuple[Metric, Optional[str]]:
    frame = combined_frame(path.frame, launch_h.frame)
    if weights is None:
        return _not_measured("deg", "no club category: club unknown", SOURCE_INVERSION), frame
    if not launch_h.present:
        return _not_measured(
            "deg", launch_h.missing_reason("launch direction"), SOURCE_INVERSION
        ), frame
    if not path.present:
        return _not_measured("deg", path.missing_reason("club path"), SOURCE_INVERSION), frame
    face = path.value + (launch_h.value - path.value) / weights.horizontal
    return (
        Metric(
            value=face,
            status=_status_from(path.label, launch_h.label),
            source=SOURCE_INVERSION,
            unit="deg",
            frame=frame,
            note=_frame_note(frame),
            reason=_estimated_reason(path, launch_h),
        ),
        frame,
    )


def _dynamic_loft(weights: Optional[LaunchWeights], attack: Input, launch_v: Input) -> Metric:
    if weights is None:
        return _not_measured("deg", "no club category: club unknown", SOURCE_INVERSION)
    if not launch_v.present:
        return _not_measured("deg", launch_v.missing_reason("vertical launch"), SOURCE_INVERSION)
    if not attack.present:
        return _not_measured("deg", attack.missing_reason("attack angle"), SOURCE_INVERSION)
    loft = attack.value + (launch_v.value - attack.value) / weights.vertical
    if not -90.0 < loft < 90.0:
        return _not_measured("deg", "inverted loft beyond vertical", SOURCE_INVERSION)
    return Metric(
        value=loft,
        status=_status_from(attack.label, launch_v.label),
        source=SOURCE_INVERSION,
        unit="deg",
        reason=_estimated_reason(attack, launch_v),
    )


def _with_note(metric: Metric, note: str) -> Metric:
    if not metric.present:
        return metric
    return replace(metric, note=f"{note}; {metric.note}" if metric.note else note)


def _face_and_loft(
    weights: Optional[LaunchWeights],
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
    face, frame = _face_angle(weights, path, launch_h)
    loft = _dynamic_loft(weights, attack, launch_v)
    note = f"{NOTE_RIGID_BODY_NOT_USED}: {why}"
    method = FACE_METHOD_INVERSION if face.present or loft.present else None
    return _with_note(face, note), _with_note(loft, note), frame, method


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
    gear_sidespin_rpm: Optional[float] = None,
) -> CollisionResult:
    """Every E2 metric for one shot, from that shot's inputs.

    ``spin_rpm`` and ``ball_speed_mph`` (the full speed) feed the rigid-body face when
    the spin is measured. ``gear_sidespin_rpm`` tilts a driver's or wood's axis only
    with a measured ``impact_offset_mm``.
    """
    weights = weights_for_club(club)
    inputs = {
        "club_path": club_path,
        "attack_angle": attack_angle,
        "launch_horizontal": launch_horizontal,
        "launch_vertical": launch_vertical,
        "club_speed_mph": club_speed_mph,
    }
    rigid_inputs = {
        "ball_speed_mph": ball_speed_mph or Input(None, reason="ball speed not given"),
        "spin_rpm": spin_rpm or Input(None, reason="spin rate not measured"),
    }
    face, loft, frame, face_method = _face_and_loft(weights, {**inputs, **rigid_inputs})
    if face_method == FACE_METHOD_RIGID_BODY:
        inputs.update(rigid_inputs)

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

    delivery_present = face.present and loft.present and attack_angle.present and club_path.present
    if delivery_present:
        status = _status_from(face.status, loft.status, attack_angle.label, club_path.label)
        why = _estimated_reason(face, loft, attack_angle, club_path)
        loft_value = metric_definitions.spin_loft_deg(
            loft.value, face.value, attack_angle.value, club_path.value
        )
        tilt_value = metric_definitions.d_plane_tilt_deg(
            loft.value, face.value, attack_angle.value, club_path.value
        )
        spin_loft = (
            Metric(
                loft_value, status, SOURCE_D37, "deg", frame, reason=why, note=_frame_note(frame)
            )
            if loft_value is not None and math.isfinite(loft_value)
            else _not_measured("deg", "spin loft undefined for this delivery", SOURCE_D37)
        )
        tilt = (
            Metric(
                tilt_value, status, SOURCE_D37, "deg", frame, reason=why, note=_frame_note(frame)
            )
            if tilt_value is not None and math.isfinite(tilt_value)
            else _not_measured(
                "deg", "no D plane: club direction parallel to the face normal", SOURCE_D37
            )
        )
    else:
        missing = face if not face.present else loft
        reason = missing.reason if not missing.present else "club delivery not measured"
        spin_loft = _not_measured("deg", reason, SOURCE_D37)
        tilt = _not_measured("deg", reason, SOURCE_D37)

    spin = calculated_spin(club_speed=club_speed_mph, spin_loft=spin_loft)
    if uses_iron_axis_model(club):
        axis = iron_spin_axis(tilt, loft)
    else:
        offset_measured = (
            impact_offset_mm is not None
            and impact_offset_mm.present
            and impact_offset_mm.label == LABEL_MEASURED
        )
        gear_spin = spin
        if spin_rpm is not None and spin_rpm.present and spin_rpm.label == LABEL_MEASURED:
            gear_spin = Metric(spin_rpm.value, _status_from(spin_rpm.label), spin_rpm.source, "rpm")
        axis = spin_axis(
            tilt,
            gear_spin,
            gear_sidespin_rpm=gear_sidespin_rpm if offset_measured else None,
            no_gear_note=NOTE_GEAR_NO_HEAD_MODEL if offset_measured else NOTE_NO_GEAR,
        )
        if offset_measured:
            inputs["impact_offset_mm"] = impact_offset_mm
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
        },
        weights=weights,
        inputs=inputs,
        face_method=face_method,
    )
