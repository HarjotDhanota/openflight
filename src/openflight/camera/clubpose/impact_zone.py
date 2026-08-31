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

MIN_QUAD_FRAMES = 4
MIN_AVAILABLE_FRAMES = 4

# The capture's trigger is the IMPACT SOUND arriving at the microphone, not the
# ball being hit. On the shipped rig the unit sits 1.575 m from the ball, so the
# trigger lands 4.59 ms -- 2.15 frames at 468 fps -- after contact. Everything
# in this module is anchored on contact, so the walk-back happens once, here.
BALL_TO_UNIT_M = 1.575
SPEED_OF_SOUND_M_S = 343.0

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
    high_low_mm: float | None = None
    high_low_status: str = "experimental_unvalidated"
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
            "high_low_mm": self.high_low_mm,
            "high_low_status": self.high_low_status,
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
    speed_of_sound_m_s: float = SPEED_OF_SOUND_M_S,
) -> float:
    """The frame at which the ball was struck, from the acoustic trigger frame.

    ``trigger_index`` is the index of the last PRE-trigger frame, which is how
    the capture archive reports it, so the trigger itself is the frame after
    it. Contact is that instant minus the sound's flight time from the ball to
    the microphone.
    """
    rate = float(fps)
    if not math.isfinite(rate) or rate <= 0.0:
        raise ValueError(f"frame rate must be a positive number of frames per second, got {fps}")
    return (int(trigger_index) + 1) - float(ball_to_unit_m) / float(speed_of_sound_m_s) * rate


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
) -> tuple[float, float, float] | None:
    """(heel-toe mm, face width mm, high-low mm) under the stated convention."""
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
    return heel_toe_mm, length * mm_per_px, high_low_mm


def extract_impact_zone(
    swing: SwingFrames,
    template: ClubOutlineTemplate,
    *,
    ridge: bool = True,
    report: MaskReport | None = None,
) -> ImpactZoneResult:
    """Segment, align, gate, carry to contact and read the zone. Fails closed.

    Every path out of this function that is not a full answer is an
    `ImpactZoneResult` with ``status == "withheld"`` and a reason naming the
    gate that stopped it. There is no partial reading.
    """
    common = {"template_source": template.source, "club": template.club or swing.club}
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

    mm_per_px = swing.plate_mm_per_px
    reading = read_impact(carry, np.array([swing.ball.x, swing.ball.y]), mm_per_px)
    if reading is None:
        return withheld("carried_outline_has_no_heel_toe_span", rejected_frames=rejected, **common)
    heel_toe_mm, face_width_mm, high_low_mm = reading
    return ImpactZoneResult(
        status="ok",
        reason="ok",
        heel_toe_mm=heel_toe_mm,
        zone=zone_for(heel_toe_mm),
        face_width_mm=face_width_mm,
        high_low_mm=high_low_mm,
        carry_model=carry.model,
        carry_disagreement_mm=(
            None if carry.disagreement_px is None else carry.disagreement_px * mm_per_px
        ),
        frames_used=carry.frames,
        frame_iou={f: a.iou for f, a in sorted(accepted.items())},
        rejected_frames=rejected,
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
