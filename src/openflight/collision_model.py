"""Published collision model for the club-face and spin metrics (Part E, item E2).

Every number here comes from a published source, read from the paper itself; the
derivations and units are in ``docs/how-it-works/collision-model.md``. Each shot
uses only its own inputs; a model constant is fixed, a shot's input never is. A
missing input makes every metric built on it "not measured", with the reason.

1. **Face angle and dynamic loft: D-plane inversion with published weights.**
   Wood, Henrikson & Broadie, "The Influence of Face Angle and Club Path on the
   Resultant Launch Angle of a Golf Ball", *Proceedings* 2(6):249 (2018),
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

4. **Spin axis:** the D-plane tilt (TrackMan's public definition: "the expected
   spin axis disregarding any gear effect"). With a measured impact location (E5)
   a gear sidespin from R. Cross & A. M. Nathan, "Experimental study of the gear
   effect in ball collisions", *Am. J. Phys.* 75, 658-664 (2007),
   doi:10.1119/1.2713788, Eq. (6), tilts it. Without one, the tilt alone, with
   the note "no impact location: gear effect not applied".

5. **Gyro:** Penner's collision is planar: the spin vector is normal to the D
   plane, so gyro is zero by construction, not a result. It is "not measured".

Labels: an output is ``calculated`` unless an input is ``model_geometry`` (D39),
which it then inherits. Horizontal outputs carry their inputs' frame
(``horizontal_frame``), ``mixed`` when the inputs disagree.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

from . import metric_definitions
from .clubs import ClubType
from .clubs.physics import get_club_physics
from .horizontal_frame import MIXED, combined_frame

MODEL_VERSION = "e2-2026-10-12"

# Input labels (Part E's three labels) and output statuses.
LABEL_MEASURED = "measured"
LABEL_CALCULATED = "calculated"
LABEL_MODEL_GEOMETRY = "model_geometry"
STATUS_CALCULATED = "calculated"
STATUS_MODEL_GEOMETRY = "model_geometry"
STATUS_NOT_MEASURED = "not_measured"
_INPUT_LABELS = (LABEL_MEASURED, LABEL_CALCULATED, LABEL_MODEL_GEOMETRY)

SOURCE_INVERSION = "D-plane inversion, Wood et al. 2018 weights"
SOURCE_D37 = "D37 definition (metric_definitions) of the calculated face and loft"
SOURCE_PENNER = "Penner 2001 collision model (Am. J. Phys. 69, 563)"
SOURCE_AXIS = "D-plane tilt (D37)"
SOURCE_GEAR = "Cross & Nathan 2007 gear effect (Am. J. Phys. 75, 658)"
NOTE_NO_GEAR = "no impact location: gear effect not applied"

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
    """calculated, unless any input is model geometry (D39), which it inherits."""
    if LABEL_MODEL_GEOMETRY in labels or STATUS_MODEL_GEOMETRY in labels:
        return STATUS_MODEL_GEOMETRY
    return STATUS_CALCULATED


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


def spin_axis(
    d_plane_tilt: Metric,
    spin_rate: Optional[Metric] = None,
    *,
    gear_sidespin_rpm: Optional[float] = None,
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
        note = NOTE_NO_GEAR if frame_note is None else f"{NOTE_NO_GEAR}; {frame_note}"
        return Metric(
            value=d_plane_tilt.value,
            status=_status_from(d_plane_tilt.status),
            source=SOURCE_AXIS,
            unit="deg",
            frame=d_plane_tilt.frame,
            note=note,
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
    )


GYRO_REASON = (
    "the collision model is planar: Penner's spin vector is normal to the D-plane, "
    "so gyro is zero by construction, not a result"
)


# ---- the whole model -----------------------------------------------------------------


@dataclass
class CollisionResult:
    metrics: dict[str, Metric]
    weights: Optional[LaunchWeights]
    inputs: dict[str, Input] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "model_version": MODEL_VERSION,
            "weights": asdict(self.weights) if self.weights is not None else None,
            "inputs": {name: asdict(value) for name, value in self.inputs.items()},
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
    )


def evaluate(
    *,
    club: ClubType,
    club_path: Input,
    attack_angle: Input,
    launch_horizontal: Input,
    launch_vertical: Input,
    club_speed_mph: Input,
    gear_sidespin_rpm: Optional[float] = None,
) -> CollisionResult:
    """Every E2 metric for one shot, from that shot's inputs."""
    weights = weights_for_club(club)
    face, frame = _face_angle(weights, club_path, launch_horizontal)
    loft = _dynamic_loft(weights, attack_angle, launch_vertical)

    if face.present and club_path.present:
        face_to_path = Metric(
            value=metric_definitions.face_to_path_deg(face.value, club_path.value),
            status=_status_from(face.status, club_path.label),
            source=SOURCE_D37,
            unit="deg",
            frame=frame,
            note=_frame_note(frame),
        )
    else:
        face_to_path = _not_measured("deg", f"face angle not measured ({face.reason})", SOURCE_D37)

    delivery_present = face.present and loft.present and attack_angle.present and club_path.present
    if delivery_present:
        status = _status_from(face.status, loft.status, attack_angle.label, club_path.label)
        loft_value = metric_definitions.spin_loft_deg(
            loft.value, face.value, attack_angle.value, club_path.value
        )
        tilt_value = metric_definitions.d_plane_tilt_deg(
            loft.value, face.value, attack_angle.value, club_path.value
        )
        spin_loft = (
            Metric(loft_value, status, SOURCE_D37, "deg", frame, note=_frame_note(frame))
            if loft_value is not None and math.isfinite(loft_value)
            else _not_measured("deg", "spin loft undefined for this delivery", SOURCE_D37)
        )
        tilt = (
            Metric(tilt_value, status, SOURCE_D37, "deg", frame, note=_frame_note(frame))
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
    axis = spin_axis(tilt, spin, gear_sidespin_rpm=gear_sidespin_rpm)
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
        inputs={
            "club_path": club_path,
            "attack_angle": attack_angle,
            "launch_horizontal": launch_horizontal,
            "launch_vertical": launch_vertical,
            "club_speed_mph": club_speed_mph,
        },
    )
