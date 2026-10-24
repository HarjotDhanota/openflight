"""Forward collision model: club delivery to ball launch, and its inverse.

From a club delivery (club speed, attack angle, club path, face angle, dynamic loft)
and an impact location, the model gives the ball's speed, launch angle, launch
direction, spin rate and spin axis. Its structure is published physics. The launch
and centre-speed constants, the spin, loss and gear gains and the index constants
were fitted by the maintainer, per club category (driver and woods, irons and
hybrids, wedges); the ball, the face friction and the restitution are published
values, and the head masses, centre-of-mass depths and moments of inertia are
typical values, assumed. Derivations and fits: ``docs/how-it-works/collision-model.md``.

1. **D plane.** The ball leaves in the plane of the club's direction and the face
   normal, between them (Jorgensen, *The Physics of Golf*, 1999), at an angle
   ``delta`` from the face normal. The spin loft ``SL`` is the angle between them.
2. **Launch.** While the ball rolls up the face its tangential to normal speed ratio
   grows with ``tan SL`` (Penner 2001, Eqs. (8)-(10)); once rolling needs more
   friction than the face has, the ball slides and the ratio grows more slowly
   (Penner 2001, Sec. II). A p-norm minimum joins the two lines:

       tan(delta) = K(L) * min_p(tan SL, sliding line(tan SL))

3. **Ball speed.** Normal impulse (Penner 2001, Eq. (7)): the normal speed is a
   fraction of the club's, so ``ball speed = V * S(L) * cos SL / cos delta``. An
   off-centre strike meets a smaller effective head mass (Cross & Nathan 2007;
   Penner 2001), centre of mass at the face centre:

       f = (1 + m/M) / (1 + m/M + c_x m x^2 / I_z + c_y m y^2 / I_x)

4. **Spin.** Rigid-ball impulse (Penner 2001, Eqs. (7)-(11); Cross 2002): the ball
   leaves rolling (grip) or, when that needs more friction than the face has,
   still sliding (slip), whichever spin is smaller:

       v_n  = (1 + e) V cos SL / (1 + m/M)
       grip = g V sin SL / (kappa r K),  K = 1 + 1/kappa + m/M + m d^2 / I_x
       slip = mu v_n / (kappa r)

   ``m d^2 / I_x`` is the head's rotation about its centre of mass (Penner 2001).
5. **Spin axis.** The D-plane tilt ``T`` (``metric_definitions``) scaled toward
   zero: ``tan(axis) = tan(T) * (a(L) + b * launch angle)``.
6. **Gear effect** (Cross & Nathan 2007, linear, from the same impulse algebra):
   with the ball's normal speed ``v_n``,

       side = -hand g_x m d x v_n / (I_z kappa r K_z),  back = -g_y m d y v_n / (I_x kappa r K)

   ``K_z`` is ``K`` with ``I_z``. A toe strike draws a right-hander and fades a
   left-hander. Without an impact location there is no gear effect.

``L`` is the club's nominal loft (``clubs.physics``); a hybrid launches like an
iron ``HYBRID_LOFT_SHIFT_DEG`` weaker. A constant that varies with ``L`` is given
by its values at the category's two anchor lofts, linear between and beyond them.

The inverse gives face and loft from the launch and the club's direction, by
solving the launch relation for ``delta``: the face normal then lies in the D plane
beyond the launch from the club, as in the rigid-body face (``collision_model``).

The expected spin and ball speed behind the spin and smash indices: ``V k sin SL``
(Penner 2001, Eq. (11)) and ``V (a cos SL + c)`` for the clubs with fitted indices;
other clubs take their category's ``k`` and the published normal and rolling
speeds (Penner 2001, Eqs. (7), (8)) times a category gain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Union

from . import metric_definitions as md
from .clubs import ClubType
from .clubs.physics import get_club_physics
from .constants import BALL_RADIUS_M, MPH_TO_MPS as MPH_TO_M_S
from .numeric import finite

DRIVER_WOOD = "driver_wood"
IRON_HYBRID = "iron_hybrid"
WEDGE = "wedge"

MODEL_NAME = "fitted collision model"
HYBRID_LOFT_SHIFT_DEG = 11.5
LAUNCH_SHARPNESS = 24.0
SLIDE_TAN_POINTS = (1.0, 2.25)
LEFT_HANDED = "LH"
RAD_S_TO_RPM = 60.0 / (2.0 * math.pi)

# Ball (Penner 2001), face friction (Henrikson et al. 2020, urethane on a dry
# grooved face) and restitution (Penner 2001 Eq. (5); USGA limit at its test speed).
BALL_MASS_KG = 0.0459
KAPPA = 0.4
FACE_FRICTION = 0.40
COR_SLOPE = 0.0029
IRON_COR_AT_REST = 0.86
USGA_COR = 0.830
USGA_TEST_SPEED_M_S = 48.77

# Typical heads, assumed: mass per club (Penner's 200 g driver, ~7 g per club after).
HEAD_MASS_G = {
    ClubType.DRIVER: 200.0,
    ClubType.WOOD_3: 212.0,
    ClubType.WOOD_5: 218.0,
    ClubType.WOOD_7: 224.0,
    ClubType.HYBRID_3: 236.0,
    ClubType.HYBRID_5: 243.0,
    ClubType.HYBRID_7: 250.0,
    ClubType.HYBRID_9: 257.0,
    ClubType.IRON_2: 233.0,
    ClubType.IRON_3: 240.0,
    ClubType.IRON_4: 247.0,
    ClubType.IRON_5: 254.0,
    ClubType.IRON_6: 261.0,
    ClubType.IRON_7: 268.0,
    ClubType.IRON_8: 275.0,
    ClubType.IRON_9: 282.0,
    ClubType.PW: 289.0,
    ClubType.GW: 296.0,
    ClubType.SW: 303.0,
    ClubType.LW: 310.0,
}

# The clubs with fitted indices: expected spin rpm per (mph sin SL), and (a, c) of
# the expected ball speed V (a cos SL + c).
EXPECTED_SPIN_PER_CLUB = {ClubType.DRIVER: 109.50, ClubType.IRON_7: 150.46, ClubType.PW: 165.13}
EXPECTED_SPEED_PER_CLUB = {
    ClubType.DRIVER: (1.5059, 0.0072),
    ClubType.IRON_7: (1.4956, 0.0321),
    ClubType.PW: (1.4682, 0.0716),
}

Pair = Union[float, tuple[float, float]]

_CATEGORY = {
    ClubType.DRIVER: DRIVER_WOOD,
    ClubType.WOOD_3: DRIVER_WOOD,
    ClubType.WOOD_5: DRIVER_WOOD,
    ClubType.WOOD_7: DRIVER_WOOD,
    ClubType.HYBRID_3: IRON_HYBRID,
    ClubType.HYBRID_5: IRON_HYBRID,
    ClubType.HYBRID_7: IRON_HYBRID,
    ClubType.HYBRID_9: IRON_HYBRID,
    ClubType.IRON_2: IRON_HYBRID,
    ClubType.IRON_3: IRON_HYBRID,
    ClubType.IRON_4: IRON_HYBRID,
    ClubType.IRON_5: IRON_HYBRID,
    ClubType.IRON_6: IRON_HYBRID,
    ClubType.IRON_7: IRON_HYBRID,
    ClubType.IRON_8: IRON_HYBRID,
    ClubType.IRON_9: IRON_HYBRID,
    ClubType.PW: WEDGE,
    ClubType.GW: WEDGE,
    ClubType.SW: WEDGE,
    ClubType.LW: WEDGE,
}
_HYBRIDS = frozenset({ClubType.HYBRID_3, ClubType.HYBRID_5, ClubType.HYBRID_7, ClubType.HYBRID_9})


@dataclass(frozen=True)
class CategoryConstants:
    """One category's constants (units in the module docstring)."""

    loft_anchors_deg: tuple[float, float]
    launch_ratio: Pair
    slide: tuple[float, float]
    smash: Pair
    axis_gain: Pair
    axis_per_launch: float
    expected_spin: Pair
    cg_depth_m: float
    moi_vertical_kg_m2: float
    moi_horizontal_kg_m2: float
    spin_gain: float
    loss_toe_heel: float
    loss_height: float
    gear_toe_heel: float
    gear_height: float
    expected_speed_gain: float

    def at(self, name: str, loft_deg: float) -> float:
        """A constant at the model loft: linear through the two anchors."""
        value = getattr(self, name)
        if not isinstance(value, tuple):
            return value
        low, high = self.loft_anchors_deg
        return value[0] + (value[1] - value[0]) * (loft_deg - low) / (high - low)


CONSTANTS = {
    DRIVER_WOOD: CategoryConstants(
        loft_anchors_deg=(17.0, 26.0),
        launch_ratio=(0.17932, 0.25073),
        slide=(0.99264, 1.1329),
        smash=(1.5151, 1.5302),
        axis_gain=(0.72464, 0.96077),
        axis_per_launch=0.08799,
        expected_spin=(154.74, 217.39),
        cg_depth_m=0.035,
        moi_vertical_kg_m2=4.5e-4,
        moi_horizontal_kg_m2=2.8e-4,
        spin_gain=0.99438,
        loss_toe_heel=1.1128,
        loss_height=2.1871,
        gear_toe_heel=0.586,
        gear_height=0.645,
        expected_speed_gain=0.99666,
    ),
    IRON_HYBRID: CategoryConstants(
        loft_anchors_deg=(29.0, 44.0),
        launch_ratio=(0.16911, 0.18485),
        slide=(0.99284, 1.1321),
        smash=(1.5164, 1.5383),
        axis_gain=0.67643,
        axis_per_launch=0.085951,
        expected_spin=(145.62, 161.44),
        cg_depth_m=0.006,
        moi_vertical_kg_m2=2.5e-4,
        moi_horizontal_kg_m2=0.7e-4,
        spin_gain=1.1149,
        loss_toe_heel=0.22025,
        loss_height=0.17351,
        gear_toe_heel=0.631,
        gear_height=0.828,
        expected_speed_gain=1.0217,
    ),
    WEDGE: CategoryConstants(
        loft_anchors_deg=(29.0, 44.0),
        launch_ratio=(0.10629, 0.17914),
        slide=(0.99329, 1.1309),
        smash=(1.5355, 1.5393),
        axis_gain=0.6769,
        axis_per_launch=0.086,
        expected_spin=(92.37, 156.57),
        cg_depth_m=0.005,
        moi_vertical_kg_m2=2.6e-4,
        moi_horizontal_kg_m2=0.7e-4,
        spin_gain=1.2407,
        loss_toe_heel=0.22698,
        loss_height=0.17243,
        gear_toe_heel=0.773,
        gear_height=1.478,
        expected_speed_gain=1.0220,
    ),
}


def club_category(club: Optional[ClubType]) -> Optional[str]:
    """The club's category, or None for an unknown club."""
    return _CATEGORY.get(club)


def model_loft_deg(club: ClubType) -> float:
    """The loft the constants are read at: nominal, a hybrid shifted to its iron."""
    loft = float(get_club_physics(club).nominal_loft_deg)
    return loft + HYBRID_LOFT_SHIFT_DEG if club in _HYBRIDS else loft


def _model(club: Optional[ClubType]) -> Optional[tuple[CategoryConstants, float]]:
    category = club_category(club)
    if category is None:
        return None
    return CONSTANTS[category], model_loft_deg(club)


def _finite(*values: float) -> bool:
    return all(finite(v) for v in values)


def _p_min(a: float, b: float, p: float) -> float:
    low = min(a, b)
    if low <= 0.0:
        return 0.0
    return low * ((low / a) ** p + (low / b) ** p) ** (-1.0 / p)


def launch_offset_deg(constants: CategoryConstants, loft: float, spin_loft_deg: float) -> float:
    """delta: the angle from the face normal back to the launch, in the D plane."""
    tan_sl = math.tan(math.radians(spin_loft_deg))
    if tan_sl <= 0.0:
        return 0.0
    (t1, t2), (s1, s2) = SLIDE_TAN_POINTS, constants.slide
    sliding = s1 + (s2 - s1) * (tan_sl - t1) / (t2 - t1)
    ratio = constants.at("launch_ratio", loft) * _p_min(tan_sl, sliding, LAUNCH_SHARPNESS)
    return math.degrees(math.atan(ratio))


@dataclass(frozen=True)
class _Head:
    mass_ratio: float
    k_loft: float
    k_toe: float
    constants: CategoryConstants


def _head(club: ClubType, constants: CategoryConstants) -> _Head:
    """The head's impulse terms: m/M, and K with the head's rotation about each axis."""
    m = BALL_MASS_KG
    mass_ratio = m / (HEAD_MASS_G[club] / 1000.0)
    base = 1.0 + 1.0 / KAPPA + mass_ratio
    depth2 = constants.cg_depth_m**2
    return _Head(
        mass_ratio,
        base + m * depth2 / constants.moi_horizontal_kg_m2,
        base + m * depth2 / constants.moi_vertical_kg_m2,
        constants,
    )


def _restitution(constants: CategoryConstants, normal_approach_m_s: float) -> float:
    if constants is CONSTANTS[DRIVER_WOOD]:
        e = USGA_COR - COR_SLOPE * (normal_approach_m_s - USGA_TEST_SPEED_M_S)
    else:
        e = IRON_COR_AT_REST - COR_SLOPE * normal_approach_m_s
    return min(max(e, 0.5), 0.95)


def _centre_spin(head: _Head, speed_mph: float, spin_loft_deg: float) -> float:
    v = speed_mph * MPH_TO_M_S
    sl = math.radians(spin_loft_deg)
    normal_approach = v * math.cos(sl)
    e = _restitution(head.constants, normal_approach)
    normal = (1.0 + e) * normal_approach / (1.0 + head.mass_ratio)
    grip = head.constants.spin_gain * v * math.sin(sl) / (KAPPA * BALL_RADIUS_M * head.k_loft)
    slip = FACE_FRICTION * normal / (KAPPA * BALL_RADIUS_M)
    return min(grip, slip) * RAD_S_TO_RPM


def centre_spin_rpm(
    *, club: Optional[ClubType], club_speed_mph: float, spin_loft_deg: float
) -> Optional[float]:
    """The face-centre spin for a club speed and spin loft, or None."""
    model = _model(club)
    if model is None or not _finite(club_speed_mph, spin_loft_deg) or club_speed_mph <= 0.0:
        return None
    if not 0.0 <= spin_loft_deg < 90.0:
        return None
    return _centre_spin(_head(club, model[0]), club_speed_mph, spin_loft_deg)


@dataclass(frozen=True)
class ForwardImpact:
    """The model's ball launch for one delivery (mph, deg, rpm; axis + curves right)."""

    ball_speed_mph: float
    launch_vertical_deg: float
    launch_horizontal_deg: float
    spin_rpm: float
    spin_axis_deg: float
    spin_loft_deg: float
    d_plane_tilt_deg: float
    centre_spin_rpm: float
    centre_spin_axis_deg: float
    gear_back_rpm: float
    gear_side_rpm: float
    impact_location: bool


def _plane_toward(origin, target) -> Optional[list[float]]:
    """Unit vector in the plane of two unit vectors, normal to ``origin``, toward ``target``."""
    along = math.fsum(p * q for p, q in zip(origin, target))
    e = [q - along * p for p, q in zip(origin, target)]
    length = math.sqrt(math.fsum(v * v for v in e))
    return [v / length for v in e] if length > 1e-12 else None


def combine_spin(spin_rpm: float, axis_deg: float, gear_back: float, gear_side: float):
    """(spin, axis) after adding the gear components to a spin at an axis."""
    back = spin_rpm * math.cos(math.radians(axis_deg)) + gear_back
    side = spin_rpm * math.sin(math.radians(axis_deg)) + gear_side
    return math.hypot(back, side), math.degrees(math.atan2(side, back))


def forward(
    *,
    club: Optional[ClubType],
    club_speed_mph: float,
    attack_angle_deg: float,
    club_path_deg: float,
    face_angle_deg: float,
    dynamic_loft_deg: float,
    impact_offset_mm: Optional[float] = None,
    impact_height_mm: Optional[float] = None,
    handedness: Optional[str] = None,
) -> Optional[ForwardImpact]:
    """The ball's launch for a delivery; None for an unknown club or an undefined one.

    ``impact_offset_mm`` is + toward the toe, ``impact_height_mm`` + above the face
    centre; either missing is the face centre.
    """
    model = _model(club)
    inputs = (club_speed_mph, attack_angle_deg, club_path_deg, face_angle_deg, dynamic_loft_deg)
    if model is None or not _finite(*inputs) or club_speed_mph <= 0.0:
        return None
    constants, loft = model
    u = md.club_direction(attack_angle_deg, club_path_deg)
    n = md.face_normal(dynamic_loft_deg, face_angle_deg)
    spin_loft = md.spin_loft_deg(dynamic_loft_deg, face_angle_deg, attack_angle_deg, club_path_deg)
    if u is None or n is None or spin_loft is None or not 0.0 <= spin_loft < 90.0:
        return None
    tilt = md.d_plane_tilt_deg(dynamic_loft_deg, face_angle_deg, attack_angle_deg, club_path_deg)
    tilt = 0.0 if tilt is None else tilt
    delta = launch_offset_deg(constants, loft, spin_loft)
    toward_club = _plane_toward(n, u) or [0.0, 0.0, 0.0]
    d = math.radians(delta)
    ball = [math.cos(d) * p + math.sin(d) * q for p, q in zip(n, toward_club)]
    launch_v = math.degrees(math.asin(max(-1.0, min(1.0, ball[2]))))
    launch_h = math.degrees(math.atan2(ball[1], ball[0]))

    centre_speed = (
        club_speed_mph
        * constants.at("smash", loft)
        * math.cos(math.radians(spin_loft))
        / math.cos(d)
    )
    head = _head(club, constants)
    centre_spin = _centre_spin(head, club_speed_mph, spin_loft)
    gain = constants.at("axis_gain", loft) + constants.axis_per_launch * launch_v / 10.0
    centre_axis = math.degrees(math.atan(math.tan(math.radians(tilt)) * gain))

    located = impact_offset_mm is not None or impact_height_mm is not None
    x = float(impact_offset_mm or 0.0) / 1000.0
    y = float(impact_height_mm or 0.0) / 1000.0
    hand = -1.0 if str(handedness or "").strip().upper() == LEFT_HANDED else 1.0
    normal = centre_speed * MPH_TO_M_S * math.cos(d)
    lever = BALL_MASS_KG * constants.cg_depth_m * normal / (KAPPA * BALL_RADIUS_M) * RAD_S_TO_RPM
    i_z, i_x = constants.moi_vertical_kg_m2, constants.moi_horizontal_kg_m2
    gear_side = -hand * constants.gear_toe_heel * lever * x / (i_z * head.k_toe)
    gear_back = -constants.gear_height * lever * y / (i_x * head.k_loft)
    spin, axis = combine_spin(centre_spin, centre_axis, gear_back, gear_side)
    base = 1.0 + head.mass_ratio
    effective = base + BALL_MASS_KG * (
        constants.loss_toe_heel * x * x / i_z + constants.loss_height * y * y / i_x
    )
    return ForwardImpact(
        ball_speed_mph=centre_speed * base / effective,
        launch_vertical_deg=launch_v,
        launch_horizontal_deg=launch_h,
        spin_rpm=spin,
        spin_axis_deg=axis,
        spin_loft_deg=spin_loft,
        d_plane_tilt_deg=tilt,
        centre_spin_rpm=centre_spin,
        centre_spin_axis_deg=centre_axis,
        gear_back_rpm=gear_back,
        gear_side_rpm=gear_side,
        impact_location=located,
    )


def face_and_loft_from_launch(
    *,
    club: Optional[ClubType],
    launch_vertical_deg: float,
    launch_horizontal_deg: float,
    club_path_deg: float,
    attack_angle_deg: float,
) -> Optional[tuple[float, float]]:
    """(face angle, dynamic loft) whose forward launch is the given one, or None."""
    model = _model(club)
    angles = (launch_vertical_deg, launch_horizontal_deg, club_path_deg, attack_angle_deg)
    if model is None or not _finite(*angles):
        return None
    constants, loft = model
    ball = md.ball_direction(launch_vertical_deg, launch_horizontal_deg)
    u = md.club_direction(attack_angle_deg, club_path_deg)
    if ball is None or u is None:
        return None
    psi = math.degrees(math.acos(max(-1.0, min(1.0, math.fsum(p * q for p, q in zip(ball, u))))))
    away = _plane_toward(ball, u)
    if away is None:
        return None
    away = [-v for v in away]
    delta = 0.0
    for _ in range(100):
        previous = delta
        delta = launch_offset_deg(constants, loft, min(psi + delta, 89.9))
        if abs(delta - previous) < 1e-12:
            break
    d = math.radians(delta)
    normal = [math.cos(d) * p + math.sin(d) * q for p, q in zip(ball, away)]
    face = math.degrees(math.atan2(normal[1], normal[0]))
    loft_deg = math.degrees(math.asin(max(-1.0, min(1.0, normal[2]))))
    return face, loft_deg


def has_fitted_index(club: Optional[ClubType]) -> bool:
    """Whether the club's expected spin and ball speed were fitted to its own shots."""
    return club in EXPECTED_SPEED_PER_CLUB


def _index_model(club, club_speed_mph: float, spin_loft_deg: float):
    model = _model(club)
    if model is None or not _finite(club_speed_mph, spin_loft_deg) or club_speed_mph <= 0.0:
        return None
    if not 0.0 < spin_loft_deg < 90.0:
        return None
    return model


def expected_spin_rpm(
    *, club: Optional[ClubType], club_speed_mph: float, spin_loft_deg: float
) -> Optional[float]:
    """The expected spin for a club speed and spin loft, or None."""
    model = _index_model(club, club_speed_mph, spin_loft_deg)
    if model is None:
        return None
    constants, loft = model
    k = EXPECTED_SPIN_PER_CLUB.get(club) or constants.at("expected_spin", loft)
    return club_speed_mph * k * math.sin(math.radians(spin_loft_deg))


def expected_ball_speed_mph(
    *, club: Optional[ClubType], club_speed_mph: float, spin_loft_deg: float
) -> Optional[float]:
    """The expected ball speed for a club speed and spin loft, or None."""
    model = _index_model(club, club_speed_mph, spin_loft_deg)
    if model is None:
        return None
    sl = math.radians(spin_loft_deg)
    if club in EXPECTED_SPEED_PER_CLUB:
        a, c = EXPECTED_SPEED_PER_CLUB[club]
        return club_speed_mph * (a * math.cos(sl) + c)
    constants = model[0]
    head = _head(club, constants)
    e = _restitution(constants, club_speed_mph * MPH_TO_M_S * math.cos(sl))
    normal = (1.0 + e) * math.cos(sl) / (1.0 + head.mass_ratio)
    tangential = min(math.sin(sl) / head.k_loft, FACE_FRICTION * normal)
    return club_speed_mph * constants.expected_speed_gain * math.hypot(normal, tangential)
