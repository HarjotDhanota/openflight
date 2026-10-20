"""Forward collision model: club delivery to ball launch, and its inverse.

From a club delivery (club speed, attack angle, club path, face angle, dynamic loft)
and an impact location, the model gives the ball's speed, launch angle, launch
direction, spin rate and spin axis. Its structure is published physics; its
constants were fitted by the maintainer, per club category (driver and woods, irons
and hybrids, wedges), to a dense grid of deliveries, 20 % held out. The derivations,
fit and hold-out residuals are in ``docs/how-it-works/collision-model.md``.

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
   off-centre strike loses speed (Cross & Nathan 2007): a smooth V in the toe-heel
   and vertical offsets, steeper on the heel.
4. **Spin.** Rises with the club speed and the angle ``psi = SL - delta`` between the
   club's direction and the launch, with a floor at small ``psi`` and a roll-off at
   high ``psi`` where the ball slides (Penner 2001, Sec. II):

       spin = V * g(L) * psi_e / (1 + (psi_e / k(L))^n)^(1/m),
       psi_e = (psi^q + f(L)^q)^(1/q)

5. **Spin axis.** The D-plane tilt ``T`` (``metric_definitions``) scaled toward
   zero: ``tan(axis) = tan(T) * (a(L) + b * launch angle)``.
6. **Gear effect** (Cross & Nathan 2007): a heel-toe offset adds a sidespin and a
   vertical offset takes off backspin, both in proportion to the ball speed; the
   heel-toe response steepens with the offset and saturates. A toe strike draws a
   right-hander and fades a left-hander. Without an impact location the strike is
   taken at the face centre.

``L`` is the club's nominal loft (``clubs.physics``); a hybrid launches like an
iron ``HYBRID_LOFT_SHIFT_DEG`` weaker. A constant that varies with ``L`` is given
by its values at the category's two anchor lofts, linear between and beyond them.

The inverse gives face and loft from the launch and the club's direction, by
solving the launch relation for ``delta``: the face normal then lies in the D plane
beyond the launch from the club, as in the rigid-body face (``collision_model``).

The expected spin and ball speed behind the spin and smash indices are a club
speed times ``sin SL`` (Penner 2001, Eq. (11)) and the normal-impulse speed along
the face normal (Penner 2001, Eq. (7)), with their own fitted constants.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Union

from . import metric_definitions as md
from .clubs import ClubType
from .clubs.physics import get_club_physics

DRIVER_WOOD = "driver_wood"
IRON_HYBRID = "iron_hybrid"
WEDGE = "wedge"

MODEL_NAME = "fitted collision model"
HYBRID_LOFT_SHIFT_DEG = 11.5
LAUNCH_SHARPNESS = 24.0
SLIDE_TAN_POINTS = (1.0, 2.5)
SPIN_FLOOR_SHARPNESS = 4.5
SPEED_LOSS_SMOOTHING_MM = 2.0
LEFT_HANDED = "LH"

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
    """One category's fitted constants (units in the module docstring)."""

    loft_anchors_deg: tuple[float, float]
    launch_ratio: Pair
    slide: tuple[float, float]
    smash: Pair
    loss_offset: float
    loss_toe: float
    loss_height: float
    spin_gain: Pair
    spin_knee: Pair
    rolloff: tuple[float, float]
    spin_floor: Pair
    axis_gain: Pair
    axis_per_launch: float
    expected_spin: Pair
    expected_smash: Pair
    gear_height: Pair = 0.0
    gear_offset: Pair = 0.0
    gear_shape: tuple[float, float] = (0.0, 1.0)

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
        slide=(0.99264, 1.1609),
        smash=(1.5151, 1.5302),
        loss_offset=0.0016177,
        loss_toe=-0.00053407,
        loss_height=0.0033814,
        spin_gain=(3.1461, 4.1268),
        spin_knee=(34.743, 19.335),
        rolloff=(53.504, 18.179),
        spin_floor=(4.5888, 2.2968),
        axis_gain=(0.72464, 0.96077),
        axis_per_launch=0.08799,
        expected_spin=(154.74, 217.39),
        expected_smash=(1.5237, 1.5388),
        gear_height=(0.081142, -0.18538),
        gear_offset=(0.017099, -0.044229),
        gear_shape=(0.2886, 95.484),
    ),
    IRON_HYBRID: CategoryConstants(
        loft_anchors_deg=(29.0, 44.0),
        launch_ratio=(0.16911, 0.18485),
        slide=(0.99284, 1.16),
        smash=(1.5164, 1.5383),
        loss_offset=0.00065315,
        loss_toe=-7.8911e-05,
        loss_height=0.0011455,
        spin_gain=3.3273,
        spin_knee=(38.18, 38.203),
        rolloff=(50.375, 17.134),
        spin_floor=(5.1183, 5.0379),
        axis_gain=0.67643,
        axis_per_launch=0.085951,
        expected_spin=(145.62, 161.44),
        expected_smash=(1.5195, 1.5414),
        gear_height=(0.0047562, -0.00022572),
        gear_offset=(0.0030627, 0.001053),
        gear_shape=(0.022397, 97.131),
    ),
    WEDGE: CategoryConstants(
        loft_anchors_deg=(29.0, 44.0),
        launch_ratio=(0.10629, 0.17914),
        slide=(0.99329, 1.1584),
        smash=(1.5355, 1.5393),
        loss_offset=0.00065357,
        loss_toe=-7.881e-05,
        loss_height=0.0011442,
        spin_gain=3.7248,
        spin_knee=37.911,
        rolloff=(79.469, 28.734),
        spin_floor=(4.946, 5.0695),
        axis_gain=0.6769,
        axis_per_launch=0.086,
        expected_spin=(92.37, 156.57),
        expected_smash=(1.5386, 1.5424),
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
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


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


def _centre_spin_rpm(constants: CategoryConstants, loft: float, speed: float, psi: float) -> float:
    floor = abs(constants.at("spin_floor", loft))
    q = SPIN_FLOOR_SHARPNESS
    psi_e = (max(psi, 0.0) ** q + floor**q) ** (1.0 / q)
    n, m = constants.rolloff
    knee = constants.at("spin_knee", loft)
    return (
        speed * constants.at("spin_gain", loft) * psi_e / (1.0 + (psi_e / knee) ** n) ** (1.0 / m)
    )


def _soft_abs(value: float) -> float:
    w = SPEED_LOSS_SMOOTHING_MM
    return math.sqrt(value * value + w * w) - w


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
    centre_spin = _centre_spin_rpm(constants, loft, club_speed_mph, spin_loft - delta)
    gain = constants.at("axis_gain", loft) + constants.axis_per_launch * launch_v / 10.0
    centre_axis = math.degrees(math.atan(math.tan(math.radians(tilt)) * gain))

    located = impact_offset_mm is not None or impact_height_mm is not None
    x = float(impact_offset_mm or 0.0)
    y = float(impact_height_mm or 0.0)
    hand = -1.0 if str(handedness or "").strip().upper() == LEFT_HANDED else 1.0
    convexity, limit = constants.gear_shape
    toe_response = limit * math.tanh((x + convexity * x * abs(x)) / limit)
    gear_back = -max(constants.at("gear_height", loft), 0.0) * centre_speed * y
    gear_side = -hand * max(constants.at("gear_offset", loft), 0.0) * centre_speed * toe_response
    spin, axis = combine_spin(centre_spin, centre_axis, gear_back, gear_side)
    loss = (
        constants.loss_offset * _soft_abs(x)
        + constants.loss_toe * x
        + constants.loss_height * _soft_abs(y)
    )
    return ForwardImpact(
        ball_speed_mph=centre_speed * max(0.0, 1.0 - loss),
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


def expected_spin_rpm(
    *, club: Optional[ClubType], club_speed_mph: float, spin_loft_deg: float
) -> Optional[float]:
    """The expected spin for a club speed and spin loft, or None."""
    model = _model(club)
    if model is None or not _finite(club_speed_mph, spin_loft_deg) or club_speed_mph <= 0.0:
        return None
    if not 0.0 < spin_loft_deg < 90.0:
        return None
    constants, loft = model
    sine = math.sin(math.radians(spin_loft_deg))
    return club_speed_mph * constants.at("expected_spin", loft) * sine


def expected_ball_speed_mph(
    *,
    club: Optional[ClubType],
    club_speed_mph: float,
    spin_loft_deg: float,
    face_angle_deg: float,
    dynamic_loft_deg: float,
    launch_vertical_deg: float,
    launch_horizontal_deg: float,
) -> Optional[float]:
    """The expected ball speed: the normal-impulse speed along the face normal, or None."""
    model = _model(club)
    values = (club_speed_mph, spin_loft_deg, face_angle_deg, dynamic_loft_deg)
    if model is None or not _finite(*values, launch_vertical_deg, launch_horizontal_deg):
        return None
    if club_speed_mph <= 0.0 or not 0.0 <= spin_loft_deg < 90.0:
        return None
    normal = md.face_normal(dynamic_loft_deg, face_angle_deg)
    ball = md.ball_direction(launch_vertical_deg, launch_horizontal_deg)
    if normal is None or ball is None:
        return None
    along = math.fsum(p * q for p, q in zip(normal, ball))
    if along <= 0.0:
        return None
    constants, loft = model
    smash = constants.at("expected_smash", loft) * math.cos(math.radians(spin_loft_deg)) / along
    return club_speed_mph * smash
