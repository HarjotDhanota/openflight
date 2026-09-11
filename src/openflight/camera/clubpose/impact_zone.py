"""Where on the face the ball was struck, read off the data-built outline.

**CONSISTENCY ONLY. NO GROUND TRUTH EXISTS.** Nothing in this module has ever
been scored against a launch monitor, a foot-spray strike, or any other
independent reference. Every figure that supports it is a comparison against
one annotator's hand marks on the same pixels, whose own second-pass spread is
0.4-0.7 px at the median. Read `docs/clubface-impact-location.md` before
quoting anything from here.

What is supported, and what is not:

  * **Heel-toe is the usable channel.** Against the hand marks over 21 shots
    the automatic reading tracks at r = 0.97 with 2.6 mm rms over a 10.8 mm
    shot-to-shot spread. That is why `zone` exists and why it is heel-toe only.
  * **High-low is NOT.** 7.7 mm rms against a 10.5 mm spread, r = 0.84, one
    shot out by 18.6 mm. It is carried as `high_low_mm` and marked
    `experimental_unvalidated`, and it is never turned into a zone.
  * **The absolute offset is a CONVENTION, not a strike location.** The face
    centre here is the outline's heel-toe midpoint plus 8 mm toe-ward, which
    puts the ball about 30 mm heel-ward of centre on nearly every shot. That is
    not a plausible strike pattern; it is the oblique rear view and the
    midpoint convention. Only the shot-to-shot VARIATION has been shown to
    mean anything. The convention is replaced by the address-photo or
    foot-spray calibration in Stage 1, and until then
    `ImpactZoneResult.centre_convention` says so on every result.
  * **The SUPPORT-PLANE channel is additive and has its own gate.** Every
    vertical number is additionally expressed against the plane the ball rests
    on -- its centre lowered by one ball radius, mat or grass or tee alike --
    and the mat-geometry gate withholds the vertical channel BY NAME when the
    placement is one no clubhead could produce. See `mat_geometry_gate`.

The USGA gates below are the one part of this that is not a convention: they
are rules a conforming clubhead obeys, so a placement that violates one is
evidence of a SEGMENTATION error rather than of an unusual club, and the frame
is rejected.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np

# Imported as a module: `contact_frame_from_trigger` has a parameter named
# `speed_of_sound_m_s`, which would shadow the function of the same name.
from openflight import acoustics
from openflight.acoustics import SPEED_OF_SOUND_20C_M_S
from openflight.camera.clubpose.head_outline import (
    Alignment,
    ClubOutlineTemplate,
    MaskReport,
    SwingFrames,
    align_swing,
    head_masks,
    placed_top_edge,
    shaft_line,
    top_edge_at,
)
from openflight.camera.clubpose.impact_projection import (
    ASSUMED_DYNAMIC_LOFT_DEG,
    ASSUMED_FACE_HEIGHT_MM,
    ASSUMED_FACE_HEIGHT_PROVENANCE,
    BALL_RADIUS_MM,
    ImpactProjection,
    project_impact,
    read_support_plane,
)

# The convention, fixed before any number was read from it.
FACE_CENTRE_TOEWARD_MM = 8.0

# USGA Equipment Rules, Part 2. These are rules a conforming, 2021-or-later
# clubhead obeys, so a violation is a segmentation error, not a strange club.
#   1d  -- the heel must lie within 5/8 inch of the plane through the shaft
#          axis and the line of play.
#   4a(i) -- no part of the head may rise above the topline by more than
#          0.1 inch.
USGA_HEEL_TO_SHAFT_PLANE_MM = 15.88
USGA_ABOVE_TOPLINE_MM = 2.54
# THE TOPLINE RULE IS FINER THAN THE INSTRUMENT. 2.54 mm is 0.72 px at this
# plate scale, and this extractor's own pre-registered topline placement gate is
# 2.0 px (its measured p90 is 1.78 px). A test at the rule's own value rejects
# every frame in the session, because it is measuring the extractor's residual
# rather than the club. The gate is therefore the rule PLUS the extractor's own
# tolerance, which makes it a catcher of gross segmentation failures and NOT a
# test of conformance. Both numbers are named so that nobody reads it as one.
TOPLINE_PLACEMENT_TOLERANCE_PX = 2.0

# Zone bands, heel-toe, about the face centre. Toe-ward positive.
ZONE_CENTRE_MM = 5.0
ZONE_SHOULDER_MM = 15.0

# The physics gate on the reading itself. No conforming iron blade is longer
# heel-to-toe than the official game-improvement band's 87 mm
# (`head_outline.CATEGORY_BLADE_LENGTH_MM`), and the toe landmark's residual
# uncertainty after the toe calibration is of order 10-15 mm, so a reading that
# puts the contact further from the TOE than the sum of the two is not a
# strike location -- it is a failed anchor, and it is withheld by name. The
# veto that motivated it, 2026-08-31: seven readings sat 88-94+ mm from the
# toe on shots that left at 96-114 mph with normal smash factors, and hosel
# contact cannot produce those numbers.
MAX_IRON_BLADE_MM = 87.0
BLADE_GATE_MARGIN_MM = 15.0
TOE_SIDE_MARGIN_MM = 10.0

# ---------------------------------------------------------------------------
# the mat-geometry gate, on the SUPPORT-PLANE datum
# ---------------------------------------------------------------------------
#
# The vertical datum is the ball's own support plane -- the ball centre lowered
# by one radius, whatever the ball sits on. No mat thickness is ever known or
# asked for. Against it, two things a clubhead cannot do:
#
#   1. Put its TOPLINE below the ball's centre. A soled iron's topline sits
#      face_height*cos(loft) -- 38-40 mm for a 45 mm face -- above the support
#      plane, and even a head held high off the ground only raises it. A
#      topline reading below one ball radius is a club buried 20 mm into
#      whatever the ball rests on, which is impossible on every surface. It is
#      what the blurred template does when it locks onto the ball's highlight,
#      and it is the failure this gate was built for.
#   2. Put its SOLE below the support plane by more than the surface allows.
#      That one IS lie-dependent, so the tolerance is named per lie and the
#      DEFAULT is the widest: the lie is not a user input (no setup fields --
#      see the no-manual-setup-inputs rule), and until an auto-detector exists
#      "unknown" declines to enforce the sole rule at all rather than guess.
#
# Rule 1 is lie-INDEPENDENT and always applies. Rule 2 applies only when the
# caller can name the lie.
SOLE_NOISE_MARGIN_MM = 3.0
GRASS_DIVOT_MM = 15.0
# No tee height is sensed yet; 40 mm covers the tallest tee a ball sits on.
UNKNOWN_TEE_HEIGHT_MM = 40.0
DEFAULT_LIE = "unknown"
LIE_SOLE_ALLOWANCE_MM: dict[str, float | None] = {
    "mat": 0.0,
    "grass": GRASS_DIVOT_MM,
    "tee": UNKNOWN_TEE_HEIGHT_MM,
    "unknown": None,
}
LIE_TOLERANCE_REASON: dict[str, str] = {
    "mat": (
        "a mat has no give: the sole cannot pass below the ball's support "
        f"plane at all (0 mm), plus the {SOLE_NOISE_MARGIN_MM:.0f} mm "
        "placement-noise margin"
    ),
    "grass": (
        f"turf takes a divot: the sole may sit up to {GRASS_DIVOT_MM:.0f} mm "
        f"below the support plane, plus the {SOLE_NOISE_MARGIN_MM:.0f} mm "
        "placement-noise margin"
    ),
    "tee": (
        "unknown_tee_height: the tee's height is not sensed, so the sole may "
        f"sit up to {UNKNOWN_TEE_HEIGHT_MM:.0f} mm below the support plane, "
        f"plus the {SOLE_NOISE_MARGIN_MM:.0f} mm placement-noise margin"
    ),
    "unknown": (
        "lie not sensed and never asked for: the WIDEST tolerance applies, so "
        "the sole rule is not enforced and only the topline-below-ball-centre "
        "rule stands"
    ),
}

MIN_QUAD_FRAMES = 4
MIN_AVAILABLE_FRAMES = 4

# The carry may EXTRAPOLATE: the quadratic is evaluated at the contact
# instant even when no accepted frame lies beyond it. On the 2026-08-25
# session 11 of 20 readings do not bracket contact, by up to 0.853 frames
# (1.82 ms), and the pair cross-check (carry_disagreement_mm) is None exactly
# there -- the registered 0.14 px agreement applies only where both models
# exist. The extrapolation distance is therefore serialized on every result,
# and past this bound -- beyond anything the session produced, so nothing is
# retuned -- the reading is withheld by name (2026-09-01 audit).
MAX_CARRY_EXTRAPOLATION_FRAMES = 1.0

# The capture's trigger is the IMPACT SOUND arriving at the microphone, not the
# ball being hit. On the shipped rig the unit sits 1.575 m from the ball, so the
# trigger lands 4.59 ms -- 2.15 frames at 468 fps -- after contact in 20 C air
# (+-0.13 ms over 0-35 C; pass `temperature_c` to `contact_frame_from_trigger`
# to remove it). Everything in this module is anchored on contact, so the
# walk-back happens once, here.
BALL_TO_UNIT_M = 1.575
# Re-exported for the modules, scripts and tests that import it from here. The
# single definition, and the temperature dependence, live in
# `openflight.acoustics`.
SPEED_OF_SOUND_M_S = SPEED_OF_SOUND_20C_M_S

CENTRE_CONVENTION = (
    "face centre = heel-toe midpoint of the aligned outline, plus "
    f"{FACE_CENTRE_TOEWARD_MM:.0f} mm toe-ward. This is a CONVENTION, not a "
    "measurement: it places the ball about 30 mm heel-ward of centre on nearly "
    "every shot, which is the oblique rear view rather than the golfer. Only "
    "the shot-to-shot variation is supported. Replace with the address-photo "
    "or foot-spray calibration (Stage 1)."
)


@dataclass(frozen=True)
class ImpactZoneResult:
    """One swing's impact reading, or the reason there is not one.

    ``status`` is "ok" or "withheld"; on "withheld", ``reason`` says why and
    every measured field is None. Nothing here is ever a partial answer.
    """

    status: str
    reason: str
    template_source: str = ""
    club: str = ""
    heel_toe_mm: float | None = None
    zone: str | None = None
    face_width_mm: float | None = None
    ball_from_toe_mm: float | None = None
    high_low_mm: float | None = None
    high_low_status: str = "experimental_unvalidated"
    # The 3-D projection channel (`impact_projection`): the CONTACT POINT in
    # face coordinates, absorbing the drop, foreshortening and parallax the
    # 2-D channels above carry. Populated only when the caller supplies the
    # setup geometry; its own failure never withholds the 2-D reading.
    ball_from_toe_3d_mm: float | None = None
    high_low_3d_mm: float | None = None
    projection_status: str = "not_run"
    projection_assumptions: tuple[str, ...] = ()
    # The SUPPORT-PLANE channel: every vertical number re-expressed against
    # the plane the ball rests on (its centre lowered by one radius), which is
    # the only vertical datum this rig can see without being told what the
    # ball sits on. Additive: it never replaces the 2-D high-low reading, and
    # its own absence is named rather than silent.
    contact_above_support_mm: float | None = None
    contact_above_support_source: str = ""
    topline_above_support_mm: float | None = None
    topline_above_support_source: str = ""
    sole_above_support_mm: float | None = None
    sole_above_support_source: str = ""
    face_height_mm: float | None = None
    face_height_source: str = ""
    support_plane_status: str = "not_run"
    # The mat-geometry gate and what it was allowed to tolerate.
    lie: str = DEFAULT_LIE
    sole_tolerance_mm: float | None = None
    sole_tolerance_reason: str = ""
    mat_gate_status: str = "not_run"
    mat_gate_detail: str = ""
    vertical_channel_status: str = "not_run"
    # How far past the last ACCEPTED frame the carry extrapolated to reach
    # the contact instant, in frames. Zero when accepted frames bracket it.
    carry_extrapolation_frames: float | None = None
    carry_model: str = ""
    carry_disagreement_mm: float | None = None
    frames_used: tuple[int, ...] = ()
    frame_iou: dict[int, float] = field(default_factory=dict)
    rejected_frames: dict[int, str] = field(default_factory=dict)
    centre_convention: str = CENTRE_CONVENTION

    def as_dict(self) -> dict:
        """A JSON-safe view, for the shot record and the report."""
        return {
            "status": self.status,
            "reason": self.reason,
            "template_source": self.template_source,
            "club": self.club,
            "heel_toe_mm": self.heel_toe_mm,
            "zone": self.zone,
            "face_width_mm": self.face_width_mm,
            "ball_from_toe_mm": self.ball_from_toe_mm,
            "high_low_mm": self.high_low_mm,
            "high_low_status": self.high_low_status,
            "ball_from_toe_3d_mm": self.ball_from_toe_3d_mm,
            "high_low_3d_mm": self.high_low_3d_mm,
            "projection_status": self.projection_status,
            "projection_assumptions": list(self.projection_assumptions),
            "contact_above_support_mm": self.contact_above_support_mm,
            "contact_above_support_source": self.contact_above_support_source,
            "topline_above_support_mm": self.topline_above_support_mm,
            "topline_above_support_source": self.topline_above_support_source,
            "sole_above_support_mm": self.sole_above_support_mm,
            "sole_above_support_source": self.sole_above_support_source,
            "face_height_mm": self.face_height_mm,
            "face_height_source": self.face_height_source,
            "support_plane_status": self.support_plane_status,
            "lie": self.lie,
            "sole_tolerance_mm": self.sole_tolerance_mm,
            "sole_tolerance_reason": self.sole_tolerance_reason,
            "mat_gate_status": self.mat_gate_status,
            "mat_gate_detail": self.mat_gate_detail,
            "vertical_channel_status": self.vertical_channel_status,
            "carry_extrapolation_frames": self.carry_extrapolation_frames,
            "carry_model": self.carry_model,
            "carry_disagreement_mm": self.carry_disagreement_mm,
            "frames_used": list(self.frames_used),
            "frame_iou": {int(k): float(v) for k, v in self.frame_iou.items()},
            "rejected_frames": {int(k): v for k, v in self.rejected_frames.items()},
            "centre_convention": self.centre_convention,
        }


def contact_frame_from_trigger(
    trigger_index: int,
    fps: float,
    *,
    ball_to_unit_m: float = BALL_TO_UNIT_M,
    speed_of_sound_m_s: float | None = None,
    temperature_c: float | None = None,
) -> float:
    """The frame at which the ball was struck, from the acoustic trigger frame.

    ``trigger_index`` is the index of the last PRE-trigger frame, which is how
    the capture archive reports it, so the trigger itself is the frame after
    it. Contact is that instant minus the sound's flight time from the ball to
    the microphone.

    The speed of sound may be given directly (``speed_of_sound_m_s``) or
    derived from an ambient reading (``temperature_c``, via
    c = 331.3 + 0.606 T); supplying both is a caller bug and raises. With
    neither -- the default -- it is the conventional 20 C value, 343.0 m/s,
    exactly as every shipped and archived number was computed. Temperature is
    worth carrying because 0-35 C moves c by about 3 percent, which at the
    shipped 1.575 m is ~0.13 ms, a tenth of a frame at 467 fps.
    """
    rate = float(fps)
    if not math.isfinite(rate) or rate <= 0.0:
        raise ValueError(f"frame rate must be a positive number of frames per second, got {fps}")
    if speed_of_sound_m_s is not None and temperature_c is not None:
        raise ValueError(
            "give either speed_of_sound_m_s or temperature_c, not both "
            f"(got {speed_of_sound_m_s!r} and {temperature_c!r})"
        )
    if speed_of_sound_m_s is None:
        speed = acoustics.speed_of_sound_m_s(temperature_c)
    else:
        speed = float(speed_of_sound_m_s)
        if not math.isfinite(speed) or speed <= 0.0:
            raise ValueError(f"speed of sound must be positive m/s, got {speed_of_sound_m_s!r}")
    return (int(trigger_index) + 1) - float(ball_to_unit_m) / speed * rate


def contact_frame_from_ball(trigger_index: int, fps: float, ball_contact_s: float) -> float:
    """The frame at which the ball was struck, from the ball's own clock.

    ``ball_contact_s`` is the contact instant fitted from the ball's flight
    rays (`camera.ball_flight.fit_launch_from_rays`), on the camera's
    trigger-relative clock: negative means before the trigger. It replaces
    the acoustic walk-back of `contact_frame_from_trigger` whenever it
    exists, because on the 2026-08 session the trigger fired 0.5-4 ms after
    contact rather than the 4.4 ms the sound flight predicts, and jittered
    shot to shot -- a frame of error that moves the attack angle by ~3 deg.
    """
    rate = float(fps)
    if not math.isfinite(rate) or rate <= 0.0:
        raise ValueError(f"frame rate must be a positive number of frames per second, got {fps}")
    if not math.isfinite(ball_contact_s):
        raise ValueError(f"ball contact time must be finite, got {ball_contact_s}")
    return (int(trigger_index) + 1) + float(ball_contact_s) * rate


def withheld(reason: str, **extra) -> ImpactZoneResult:
    """A result that carries no numbers, only why it carries none."""
    return ImpactZoneResult(status="withheld", reason=reason, **extra)


# ---------------------------------------------------------------------------
# what each accepted frame contributes
# ---------------------------------------------------------------------------

OBSERVABLES = (
    "heel_x",
    "heel_y",
    "toe_x",
    "toe_y",
    "topline_at_ball_y",
)


def frame_observables(
    swing: SwingFrames, template: ClubOutlineTemplate, alignment: Alignment
) -> dict[str, float]:
    """The scalars carried to contact, read off one aligned frame.

    The heel and toe are the LANDMARKS, not the outline's x extremes: with the
    heel edge taken from the data the outline runs up the hosel neck, so its
    leftmost point is the hosel. The topline is read at the BALL's own column,
    because the topline mark sits a median +11 px toe-ward of the ball -- the
    ball's cap hides the crown directly above it -- and the crown slopes.
    """
    edge_x, edge_y = placed_top_edge(template, alignment)
    return {
        "heel_x": float(alignment.landmarks["heel"][0]),
        "heel_y": float(alignment.landmarks["heel"][1]),
        "toe_x": float(alignment.landmarks["toe"][0]),
        "toe_y": float(alignment.landmarks["toe"][1]),
        "topline_at_ball_y": top_edge_at(edge_x, edge_y, float(swing.ball.x)),
    }


# ---------------------------------------------------------------------------
# the USGA sanity gates
# ---------------------------------------------------------------------------


def heel_within_shaft_plane(
    swing: SwingFrames,
    alignment: Alignment,
    frame: int,
    *,
    limit_mm: float = USGA_HEEL_TO_SHAFT_PLANE_MM,
) -> tuple[bool, str]:
    """USGA Part 2 §1d: the heel sits within 5/8 inch of the shaft plane.

    In this rear view the plane through the shaft axis and the line of play
    projects to the shaft's own image line, so the check is the placed heel's
    perpendicular distance from the shaft line `split_head` found. A frame with
    no visible shaft PASSES: the rule is not testable there, and inventing a
    failure would reject half the set for a reason that is not evidence.
    """
    line = shaft_line(swing, frame)
    if line is None:
        return True, "no_shaft_visible_rule_not_testable"
    slope, intercept = line
    heel = alignment.landmarks["heel"]
    # x = slope * y + intercept  ->  x - slope*y - intercept = 0
    distance_px = abs(float(heel[0]) - slope * float(heel[1]) - intercept) / math.hypot(1.0, slope)
    distance_mm = distance_px * swing.mm_per_px(frame)
    if distance_mm > limit_mm:
        return False, f"heel_{distance_mm:.1f}_mm_from_the_shaft_plane"
    return True, "ok"


def nothing_rises_above_the_topline(
    swing: SwingFrames,
    template: ClubOutlineTemplate,
    alignment: Alignment,
    mask: np.ndarray,
    frame: int,
    *,
    limit_mm: float = USGA_ABOVE_TOPLINE_MM,
) -> tuple[bool, str]:
    """USGA Part 2 §4a(i): nothing rises above the topline by more than 0.1 inch.

    A conforming head has nothing above its own topline, so observed mask
    pixels sitting above the ALIGNED outline's top edge are segmentation error
    -- a ball highlight, a shaft stub, turf -- and the frame is rejected.

    Two exclusions, both structural rather than convenient:

      * the BALL's own columns, because before contact the head sits behind a
        stationary ball whose cap genuinely rises above the crown;
      * everything HEEL-ward of the outline's midpoint, because `split_head`
        cuts across the hosel NECK rather than below it, so the hosel is part
        of the head mask by construction and genuinely rises above the topline.
        This is the same toe-side scope, for the same reason, that
        `refine_on_ridge` uses to find the specular topline.

    And the threshold is the rule plus `TOPLINE_PLACEMENT_TOLERANCE_PX` -- see
    that constant. The rule is finer than this instrument, and pretending
    otherwise would reject the whole session.
    """
    edge_x, edge_y = placed_top_edge(template, alignment)
    if edge_x.size == 0:
        return False, "aligned_outline_has_no_top_edge"
    rows, cols = np.nonzero(mask)
    if rows.size == 0:
        return False, "empty_mask"
    radius = swing.ball.diameter_px / 2.0
    midpoint_x = float(alignment.landmarks["midpoint"][0])
    toe_x = float(alignment.landmarks["toe"][0])
    low, high = min(midpoint_x, toe_x), max(midpoint_x, toe_x)
    keep = (
        (cols >= max(low, math.ceil(edge_x[0])))
        & (cols <= min(high, math.floor(edge_x[-1])))
        & (np.abs(cols - swing.ball.x) > radius)
    )
    if not keep.any():
        return True, "no_toe_side_columns_clear_of_the_ball"
    edge = np.interp(cols[keep].astype(float), edge_x, edge_y)
    # Rows grow DOWNWARD, so "above" is a smaller row.
    rise_px = float(np.max(edge - rows[keep].astype(float)))
    mm_per_px = swing.mm_per_px(frame)
    limit_px = limit_mm / mm_per_px + TOPLINE_PLACEMENT_TOLERANCE_PX
    if rise_px > limit_px:
        return False, f"mask_rises_{rise_px * mm_per_px:.1f}_mm_above_the_topline"
    return True, "ok"


def apply_usga_gates(
    swing: SwingFrames,
    template: ClubOutlineTemplate,
    accepted: Mapping[int, Alignment],
    masks: Mapping[int, np.ndarray],
) -> tuple[dict[int, Alignment], dict[int, str]]:
    """Drop every frame whose placement a conforming clubhead could not produce."""
    kept: dict[int, Alignment] = {}
    rejected: dict[int, str] = {}
    for frame, alignment in accepted.items():
        ok, reason = heel_within_shaft_plane(swing, alignment, frame)
        if not ok:
            rejected[frame] = reason
            continue
        ok, reason = nothing_rises_above_the_topline(
            swing, template, alignment, masks[frame], frame
        )
        if not ok:
            rejected[frame] = reason
            continue
        kept[frame] = alignment
    return kept, rejected


# ---------------------------------------------------------------------------
# the carry to contact
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Carry:
    """Every observable evaluated at the contact instant, and by which model."""

    values: dict[str, float]
    model: str
    disagreement_px: float | None
    frames: tuple[int, ...]


def carry_to_contact(per_frame: Mapping[int, Mapping[str, float]], contact: float) -> Carry | None:
    """Fit each observable QUADRATICALLY over the accepted frames and evaluate it.

    Not linearly. The midpoint's projected motion is not close to linear over
    six frames -- the club is swinging onto the optical axis, so its transverse
    projection decelerates hard -- and a straight line was the single largest
    error in the whole pipeline at 6.1 px in x and 9.8 px in y, five to ten
    times the per-frame alignment error, with the same sign on all 21 shots.
    The quadratic is 1.0 px and 1.8 px.

    The interpolation between the two frames either side of contact is kept as
    a CROSS-CHECK and its disagreement is reported. The two land within 0.14 px
    of each other, which is how we know the carry is no longer where the error
    lives; the pair is not the primary because the frame after contact is the
    one whose mask most often fails.
    """
    frames = sorted(per_frame)
    if len(frames) < MIN_QUAD_FRAMES:
        return None
    times = np.asarray(frames, dtype=float)
    values: dict[str, float] = {}
    for name in OBSERVABLES:
        series = np.asarray([per_frame[f][name] for f in frames], dtype=float)
        finite = np.isfinite(series)
        if int(finite.sum()) < MIN_QUAD_FRAMES:
            return None
        values[name] = float(np.polyval(np.polyfit(times[finite], series[finite], 2), contact))

    before = max((f for f in frames if f <= contact), default=None)
    after = min((f for f in frames if f > contact), default=None)
    disagreement: float | None = None
    if before is not None and after is not None:
        weight = (contact - before) / (after - before)
        gaps = []
        for name in OBSERVABLES:
            first, second = per_frame[before][name], per_frame[after][name]
            if math.isfinite(first) and math.isfinite(second):
                gaps.append(abs(values[name] - (first + weight * (second - first))))
        if gaps:
            disagreement = float(max(gaps))
    return Carry(
        values=values, model="quadratic", disagreement_px=disagreement, frames=tuple(frames)
    )


def carry_extrapolation_frames(carry: Carry, contact_frame: float) -> float:
    """How far past the last ACCEPTED frame the carry reached for contact.

    Zero when the accepted frames bracket the contact instant. The audit
    measured 11 of 20 session readings extrapolating, by up to 0.853 frames;
    the value travels on every result so a consumer can weigh the reading,
    and `MAX_CARRY_EXTRAPOLATION_FRAMES` bounds it fail-closed.
    """
    return max(0.0, float(contact_frame) - max(carry.frames))


# ---------------------------------------------------------------------------
# the reading
# ---------------------------------------------------------------------------


def zone_for(heel_toe_mm: float) -> str:
    """The named band a heel-toe offset falls in. Toe-ward positive."""
    if abs(heel_toe_mm) <= ZONE_CENTRE_MM:
        return "centre"
    if abs(heel_toe_mm) <= ZONE_SHOULDER_MM:
        return "centre-toe" if heel_toe_mm > 0 else "heel-centre"
    return "toe" if heel_toe_mm > 0 else "heel"


def read_impact(
    carry: Carry, ball: np.ndarray, mm_per_px: float
) -> tuple[float, float, float, float] | None:
    """(heel-toe mm, face width mm, high-low mm, ball-from-toe mm).

    ``ball_from_toe_mm`` is the TOE-ANCHORED reading: the ball's offset from
    the toe landmark along the span, negative heel-ward. It exists because the
    toe is the better-measured end (toe p90 1.09 px against heel 1.71) and
    because the midpoint convention inherits the template's heel-side
    hosel tail; the physics gate below is expressed in it.
    """
    heel = np.array([carry.values["heel_x"], carry.values["heel_y"]])
    toe = np.array([carry.values["toe_x"], carry.values["toe_y"]])
    span = toe - heel
    length = float(np.hypot(*span))
    if length <= 0.0:
        return None
    unit = span / length
    centre = 0.5 * (heel + toe) + unit * (FACE_CENTRE_TOEWARD_MM / mm_per_px)
    heel_toe_mm = float(np.dot(ball - centre, unit)) * mm_per_px
    high_low_mm = (float(ball[1]) - carry.values["topline_at_ball_y"]) * mm_per_px
    ball_from_toe_mm = float(np.dot(ball - toe, unit)) * mm_per_px
    return heel_toe_mm, length * mm_per_px, high_low_mm, ball_from_toe_mm


def reading_is_physical(ball_from_toe_mm: float) -> tuple[bool, str]:
    """Whether a contact this far from the toe can exist on a conforming iron.

    Toe-ward of the toe by more than `TOE_SIDE_MARGIN_MM`, or heel-ward of it
    by more than the longest official iron blade plus the anchor's own margin,
    is not a place a ball can meet a face. Cross-sensor context makes the veto
    concrete: hosel contact does not send a ball out at 96-114 mph, so a
    reading out here is a failed anchor, never an exotic strike.
    """
    if ball_from_toe_mm > TOE_SIDE_MARGIN_MM:
        return False, f"contact_{ball_from_toe_mm:.0f}_mm_beyond_the_toe"
    if -ball_from_toe_mm > MAX_IRON_BLADE_MM + BLADE_GATE_MARGIN_MM:
        return False, (f"contact_{-ball_from_toe_mm:.0f}_mm_from_the_toe_is_beyond_any_iron_face")
    return True, "ok"


def run_projection(
    swing, carry: Carry, setup, rig, loft_deg, face_angle_deg, club
) -> tuple[ImpactProjection | None, str]:
    """(the 3-D projection, its status) -- computed ONCE for both channels.

    ``setup`` and ``rig`` are `rig_geometry.SetupSolution` / `RigGeometry`
    (duck-typed here to keep this module import-light). None with a named
    status is the only other outcome; there is no partial projection.
    """
    if setup is None or rig is None:
        return None, "not_run_no_setup_geometry"
    loft = loft_deg if loft_deg is not None else ASSUMED_DYNAMIC_LOFT_DEG.get(club)
    if loft is None:
        return None, f"not_run_no_loft_for_{club or 'unknown_club'}"
    projection = project_impact(
        (swing.ball.x, swing.ball.y),
        (carry.values["heel_x"], carry.values["heel_y"]),
        (carry.values["toe_x"], carry.values["toe_y"]),
        (swing.ball.x, carry.values["topline_at_ball_y"]),
        range_to_ball_mm=setup.range_to_ball_mm,
        focal_px=rig.focal_px,
        principal_point=rig.principal_point,
        loft_deg=loft,
        face_angle_deg=face_angle_deg,
    )
    if not projection.ok:
        return None, projection.reason
    return projection, "ok"


def projection_result_fields(projection: ImpactProjection | None, status: str) -> dict:
    """The 3-D projection channel's fields, from an already-run projection."""
    if projection is None:
        return {"projection_status": status}
    return {
        "projection_status": "ok",
        "ball_from_toe_3d_mm": projection.ball_from_toe_mm,
        "high_low_3d_mm": projection.high_low_mm,
        "projection_assumptions": projection.assumptions,
    }


def projection_fields(swing, carry: Carry, setup, rig, loft_deg, face_angle_deg, club) -> dict:
    """The 3-D projection channel's fields, or the named reason it did not run.

    The channel is additive: its failure populates ``projection_status`` and
    never withholds the 2-D reading, because the two rest on different
    assumptions.
    """
    return projection_result_fields(
        *run_projection(swing, carry, setup, rig, loft_deg, face_angle_deg, club)
    )


# ---------------------------------------------------------------------------
# the support-plane channel and the mat-geometry gate
# ---------------------------------------------------------------------------


def template_face_height_mm(template, club: str) -> tuple[float | None, str]:
    """(face height in mm, where it came from) for one club's template.

    The template's own topline-to-sole extent is preferred, because it is
    this club measured on this rig; the named per-club assumption stands in
    only when the outline has no vertical extent, and says so.
    """
    extent = None
    if template is not None and hasattr(template, "extent_mm"):
        height = float(template.extent_mm()[1])
        if math.isfinite(height) and height > 0.0:
            extent = height
    if extent is not None:
        return extent, (
            f"template outline's own topline-to-sole extent ({extent:.1f} mm), "
            "imaged at the ball's range; it is the head's silhouette height, "
            "which is the face height only insofar as the outline's bottom is "
            "the leading edge"
        )
    assumed = ASSUMED_FACE_HEIGHT_MM.get(club)
    if assumed is None:
        return None, f"no template extent and no assumed face height for {club or 'unknown_club'}"
    return assumed, f"ASSUMED {assumed:.1f} mm for {club}: {ASSUMED_FACE_HEIGHT_PROVENANCE}"


def sole_tolerance_for(lie: str) -> tuple[float | None, str]:
    """(how far below the support plane the sole may sit, why) for one lie.

    None is the WIDEST tolerance: the sole rule is not enforced at all. That
    is the default, because the lie is never a user input.
    """
    if lie not in LIE_SOLE_ALLOWANCE_MM:
        raise ValueError(f"unknown lie {lie!r}; expected one of {sorted(LIE_SOLE_ALLOWANCE_MM)}")
    allowance = LIE_SOLE_ALLOWANCE_MM[lie]
    reason = LIE_TOLERANCE_REASON[lie]
    if allowance is None:
        return None, reason
    return allowance + SOLE_NOISE_MARGIN_MM, reason


def mat_geometry_gate(
    reading,
    *,
    lie: str = DEFAULT_LIE,
    ball_radius_mm: float = BALL_RADIUS_MM,
) -> tuple[str, str, float | None, str]:
    """(gate status, the measurement behind it, the tolerance used, its reason).

    The status is "ok" or a NAMED impossibility -- never None and never a
    silent pass. Two rules, and the first is the one that matters:

      * ``impossible_topline_below_ball_centre`` -- the placed topline sits
        less than one ball radius above the support plane, i.e. below the
        ball's own centre. No club on any surface does that; the extractor
        locked onto the ball's highlight.
      * ``impossible_sole_below_support_plane`` -- the sole sits further below
        the plane than the named lie allows. Not enforced at all on the
        default "unknown" lie.
    """
    tolerance, tolerance_reason = sole_tolerance_for(lie)
    if reading is None or not reading.ok:
        return (
            "not_run_no_support_plane_reading",
            "" if reading is None else reading.reason,
            tolerance,
            tolerance_reason,
        )
    radius = float(ball_radius_mm)
    topline = reading.topline_above_support_mm
    if topline is None:
        return (
            "not_run_no_topline_height",
            reading.topline_above_support_source,
            tolerance,
            (tolerance_reason),
        )
    detail = f"topline_{topline:.1f}_mm_above_the_support_plane_ball_centre_is_{radius:.1f}_mm"
    if topline < radius:
        return "impossible_topline_below_ball_centre", detail, tolerance, tolerance_reason
    sole = reading.sole_above_support_mm
    if sole is not None and tolerance is not None and sole < -tolerance:
        return (
            "impossible_sole_below_support_plane",
            (
                f"sole_{-sole:.1f}_mm_below_the_support_plane_on_a_{lie}_lie_"
                f"which_allows_{tolerance:.1f}_mm; {detail}"
            ),
            tolerance,
            tolerance_reason,
        )
    return "ok", detail, tolerance, tolerance_reason


def support_plane_fields(
    projection: ImpactProjection | None,
    projection_status: str,
    template,
    club: str,
    *,
    lie: str = DEFAULT_LIE,
) -> dict:
    """The support-plane channel's fields and the mat gate's verdict.

    Additive, like the projection channel: nothing here withholds the
    heel-toe reading. What it DOES withhold, by name, is the VERTICAL
    channel -- ``high_low_mm``, ``high_low_3d_mm`` and the template-derived
    support-plane heights -- when the gate says the placement is impossible.
    ``contact_above_support_mm`` survives the gate because it is built from
    the ball and the loft alone and owes the suspect template nothing.
    """
    face_height, face_height_source = template_face_height_mm(template, club)
    reading = (
        None
        if projection is None
        else read_support_plane(
            projection, face_height_mm=face_height, face_height_source=face_height_source
        )
    )
    gate_status, gate_detail, tolerance, tolerance_reason = mat_geometry_gate(reading, lie=lie)
    fields = {
        "lie": lie,
        "face_height_mm": face_height,
        "face_height_source": face_height_source,
        "sole_tolerance_mm": tolerance,
        "sole_tolerance_reason": tolerance_reason,
        "mat_gate_status": gate_status,
        "mat_gate_detail": gate_detail,
    }
    if reading is None:
        return {
            **fields,
            "support_plane_status": f"not_run_projection_{projection_status}",
            "vertical_channel_status": f"not_run_projection_{projection_status}",
        }
    if not reading.ok:
        return {
            **fields,
            "support_plane_status": reading.reason,
            "vertical_channel_status": f"not_run_{reading.reason}",
        }
    gated = gate_status.startswith("impossible_")
    return {
        **fields,
        "support_plane_status": "ok",
        "vertical_channel_status": "ok" if not gated else f"withheld_{gate_status}",
        "contact_above_support_mm": reading.contact_above_support_mm,
        "contact_above_support_source": reading.contact_above_support_source,
        "topline_above_support_mm": None if gated else reading.topline_above_support_mm,
        "topline_above_support_source": reading.topline_above_support_source,
        "sole_above_support_mm": None if gated else reading.sole_above_support_mm,
        "sole_above_support_source": reading.sole_above_support_source,
    }


def extract_impact_zone(
    swing: SwingFrames,
    template: ClubOutlineTemplate,
    *,
    ridge: bool = True,
    report: MaskReport | None = None,
    setup=None,
    rig=None,
    loft_deg: float | None = None,
    face_angle_deg: float = 0.0,
    lie: str = DEFAULT_LIE,
) -> ImpactZoneResult:
    """Segment, align, gate, carry to contact and read the zone. Fails closed.

    Every path out of this function that is not a full answer is an
    `ImpactZoneResult` with ``status == "withheld"`` and a reason naming the
    gate that stopped it. There is no partial reading.

    With ``setup`` and ``rig`` (`rig_geometry.SetupSolution` / `RigGeometry`)
    the result additionally carries the 3-D projection channel -- the contact
    point B − r·n̂ in face coordinates (`impact_projection`) -- under its own
    status and named assumptions. ``loft_deg`` defaults to the per-club
    `ASSUMED_DYNAMIC_LOFT_DEG` convention until the D-plane loft ships.

    It then carries the SUPPORT-PLANE channel on top of that: the same
    vertical numbers referred to the plane the ball rests on, and the
    mat-geometry gate over them. ``lie`` is NOT a user input -- it defaults to
    "unknown", which applies the widest tolerance -- and exists so a future
    lie detector can tighten the sole rule without a setup field.
    """
    # The lie travels on EVERY exit, withheld ones included, so a reader can
    # see which tolerance the gate would have used. Validated here so an
    # unrecognised lie is refused before any work, not silently widened.
    sole_tolerance_for(lie)
    common = {
        "template_source": template.source,
        "club": template.club or swing.club,
        "lie": lie,
    }
    if template.outline.size == 0 or not template.outline.any():
        return withheld("template_outline_is_empty", **common)

    masks = head_masks(swing) if report is None else report
    if not masks.masks:
        return withheld("no_head_mask_on_any_frame", rejected_frames=dict(masks.reasons), **common)

    accepted, rejected = align_swing(swing, template, masks, ridge=ridge)
    accepted, usga_rejected = apply_usga_gates(swing, template, accepted, masks.masks)
    rejected.update(usga_rejected)
    if len(accepted) < MIN_AVAILABLE_FRAMES:
        return withheld(
            f"only_{len(accepted)}_frames_passed_the_gates",
            rejected_frames=rejected,
            frame_iou={f: a.iou for f, a in accepted.items()},
            **common,
        )

    per_frame = {
        frame: frame_observables(swing, template, alignment)
        for frame, alignment in accepted.items()
    }
    carry = carry_to_contact(per_frame, swing.contact_frame)
    if carry is None:
        return withheld(
            "carry_to_contact_had_too_few_finite_frames", rejected_frames=rejected, **common
        )
    extrapolation = carry_extrapolation_frames(carry, swing.contact_frame)
    if extrapolation > MAX_CARRY_EXTRAPOLATION_FRAMES:
        return withheld(
            f"carry_extrapolated_{extrapolation:.2f}_frames_past_the_last_accepted_frame",
            rejected_frames=rejected,
            **common,
        )

    mm_per_px = swing.plate_mm_per_px
    reading = read_impact(carry, np.array([swing.ball.x, swing.ball.y]), mm_per_px)
    if reading is None:
        return withheld("carried_outline_has_no_heel_toe_span", rejected_frames=rejected, **common)
    heel_toe_mm, face_width_mm, high_low_mm, ball_from_toe_mm = reading
    physical, physics_reason = reading_is_physical(ball_from_toe_mm)
    if not physical:
        return withheld(physics_reason, rejected_frames=rejected, **common)
    projection, projection_status = run_projection(
        swing, carry, setup, rig, loft_deg, face_angle_deg, common["club"]
    )
    projected = projection_result_fields(projection, projection_status)
    support = support_plane_fields(projection, projection_status, template, common["club"], lie=lie)
    support.pop("lie")  # already on `common`, which every exit carries
    gated = support["vertical_channel_status"].startswith("withheld_")
    if gated:
        # Fail closed on the VERTICAL channel only: a placement the mat
        # geometry rules out cannot carry a high-low number, but the heel-toe
        # reading rests on the span, not on the topline, and survives. The
        # heel-toe 3-D reading survives with it, so ``projection_status`` is
        # left alone and ``vertical_channel_status`` carries the withholding.
        high_low_mm = None
        projected["high_low_3d_mm"] = None
    return ImpactZoneResult(
        status="ok",
        reason="ok",
        heel_toe_mm=heel_toe_mm,
        zone=zone_for(heel_toe_mm),
        face_width_mm=face_width_mm,
        ball_from_toe_mm=ball_from_toe_mm,
        high_low_mm=high_low_mm,
        high_low_status=(
            f"withheld_{support['mat_gate_status']}" if gated else "experimental_unvalidated"
        ),
        carry_model=carry.model,
        carry_disagreement_mm=(
            None if carry.disagreement_px is None else carry.disagreement_px * mm_per_px
        ),
        frames_used=carry.frames,
        frame_iou={f: a.iou for f, a in sorted(accepted.items())},
        rejected_frames=rejected,
        carry_extrapolation_frames=extrapolation,
        **projected,
        **support,
        **common,
    )


def zone_correlation(automatic: Sequence[float], reference: Sequence[float]) -> float:
    """Pearson r between two heel-toe readings over a set of swings.

    The regression figure for this extractor: r >= 0.95 against the hand marks'
    own heel-toe reading over the 21 shots of the 2026-08-25 session. It is a
    CONSISTENCY figure between two readings of the same pixels.
    """
    first = np.asarray(automatic, dtype=float)
    second = np.asarray(reference, dtype=float)
    if first.size < 3 or first.size != second.size:
        raise ValueError("need at least three paired readings")
    return float(np.corrcoef(first, second)[0, 1])
