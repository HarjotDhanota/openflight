"""EXPERIMENTAL, UNQUALIFIED: impact location by placing a fixed-orientation club at contact.

Stages S1-S3 of the D-plane-constrained placement model (research/impact-model-2026-10-30/
PLAN.txt). The D-plane supplies the head's ORIENTATION at impact; the ball's position is
known; the camera only PLACES a fixed-orientation club model at the contact moment.

S1 orientation. Face angle and dynamic loft come in with their sigmas and labels, by one
rule (``orientation_for_shot``, per angle): the shot's capture-bound angle as
``impact_session`` already reads it (``orientation_from_face_inputs``), else the
collision model's D-plane inversion the shot record carries (``orientation_from_collision``:
the metric's status sets its sigma, ``ANGLE_SIGMA_BY_STATUS``), else the category priors
``FaceInputs.priors`` keeps; ``Orientation.provenance`` names each angle's origin and
label. Dynamic lie (the face's roll about its normal, dynamic minus static lie, + toe up)
is a labelled prior unless a shaft lie is supplied (``lie_from_shaft``). All of them are
conditional inputs, never "measured".

S2 placement. The club is the population-average head outline of its category
(``club_outline_reference.json``, the kiosk's face-on drawing, about the face centre of
``face_centre_reference.json``) as a plate in the face plane, plus its sole strip
(``CATEGORY_PRIORS`` sole width and effective bounce), right-handed, a left-handed golfer
mapped in by ``impact_location``'s analysis flip. At contact the face is tangent to the
ball: the contact point is P = B - r n_hat, which fixes the head along its normal; on each
bracket frame the head sits along the normal by its normal speed times the time to
contact (the club speed less the in-plane speed the two frames show; the head's speed is
one vector). The camera fits ONLY the two in-face-plane translations, per frame, to the
head's EDGES in the background-difference image: the toe edge (heel-toe) and the sole
(height), each sample searched along its own image normal with a box as long as that
edge's predicted motion blur, the edge chosen where changed meets unchanged (a saturated
difference, so the ball behind the head cannot outscore the silhouette) and placed on
the raw difference. The topline is reported, not fitted (face heights vary more than the
centre's height). The heel end is never fitted (hosel and shaft). After contact the
ball's spot is read against the clip's last quiet frames, and its climb and the flying
ball are not read. The two frames' translations are interpolated to contact with the
head slowed by ``POST_IMPACT_SPEED_FRACTION`` after it. Depth comes from the ball range;
nothing is ranged from the head's size. The lens height above the hitting surface is the
one the face inputs carry (``impact_session.shot_lens_height_mm``); it enters only the
height self-checks, since the ball's pixel and range already place the ball.

S3 read. The strike is the contact point in face coordinates about the category's face
centre: ``impact_offset_mm`` heel to toe (+ toe, either hand) and ``impact_height_mm``
up the face (+ up), with 1-sigma budgets of named terms in quadrature: the placement
fit, the ball's centre, contact timing, every orientation sigma (face angle, loft, lie),
the range, the club's speed, the sole geometry, camera pitch and roll, the face centre's
population spread and the bracket's chord terms; each orientation, timing, range, speed,
sole and tilt term is a refit of both frames at +-1 sigma. Fails closed with named
reasons.

S4 (gear effect) is NOT done here: ``refine_with_orientation_update`` is the hook that
re-places the club after a caller corrects the orientation for the strike.

Reused, not re-implemented: ``impact_location``'s prepared shot (contact frame, bracket,
ball, mm-scaled masks, analysis flip), its physical gates and chord terms, its category
priors and face-centre reference; the collision model's metrics; ``impact_session``'s
session walk and its face-angle rule. Frames are exposures starting at their timestamps
(libcamera's SensorTimestamp): an edge is read at mid-exposure and contact falls after the
last rest frame's exposure ends.
"""

# pylint: disable=no-member,too-many-arguments,too-many-locals,too-many-lines
# pylint: disable=too-many-positional-arguments

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import cv2
import numpy as np
from scipy import ndimage

from openflight.camera import impact_location as il, impact_session as session
from openflight.camera.impact_angle_evidence import ANGLE_SIGMA_BY_STATUS
from openflight.collision_model import STATUS_NOT_MEASURED, Metric
from openflight.source_labels import LABEL_ESTIMATED

SCHEMA = "openflight.impact_placement.v1"
LABEL = il.EXPERIMENTAL_LABEL

PRIOR_LIE = "prior_dynamic_lie_equals_static_lie"
# Where each orientation angle came from (``Orientation.provenance``): an angle bound to
# the shot's own capture, the shot's collision-model D-plane inversion, a category prior,
# or a value a caller gave. A prior is an assumed input: its label is "estimated".
ORIGIN_CAPTURE = "capture_angle"
ORIGIN_D_PLANE = "d_plane"
ORIGIN_PRIOR = "prior"
ORIGIN_GIVEN = "given"
ANGLES = ("face_angle", "dynamic_loft", "lie")
METRIC_FIELDS = frozenset(Metric.__dataclass_fields__)
# ASSUMED, not sourced: no population dynamic-lie spread is imported.
LIE_PRIOR_SIGMA_DEG = 4.0
PRIOR_PATH = "prior_square_club_path"
PATH_PRIOR_SIGMA_DEG = 4.0
CAMERA_LEVEL = "assumed_level_rig_design"
CAMERA_TILT_SIGMA_DEG = 1.0
SCENE_BALL_HEIGHT_SIGMA_MM = 2.0
BALL_ON_SURFACE = "one_radius_above_the_hitting_surface"

OUTLINE_REFERENCE: dict[str, Any] = json.loads(
    Path(__file__).with_name("club_outline_reference.json").read_text(encoding="utf-8")
)
SAMPLE_SPACING_MM = 1.5
PART_NORMAL_COSINE = 0.6
FITTED_PARTS = ("toe", "sole")
MIN_EDGE_SAMPLES = {"toe": 4, "sole": 6}

EDGE_BOX_MIN_PX = 2.0
EDGE_BOX_STEP_PX = 0.5
EDGE_SEARCH_PX = 6.0
EDGE_SEARCH_STEP_PX = 0.25
EDGE_REFINE_PX = 1.0
EDGE_CLIP_FLOORS = 2.0
MIN_EDGE_CONTRAST_DN = 8.0
EDGE_CONTRAST_NOISE = 4.0
COARSE_STRIKE_MM = 70.0
COARSE_MARGIN_MM = 20.0
ICP_ITERATIONS = 15
ICP_TOLERANCE_MM = 0.02
TUKEY_C = 4.685
MIN_RESIDUAL_PX = 0.25
LOCKED_AXIS = 1e6
POST_DEPARTURE_FRAMES = 4
POST_DEPARTURE_GAP_FRAMES = 3
BALL_DISK_PAD_PX = 2.0
# After contact the ball climbs out of its spot behind the head, partly hidden, too dim
# for flying_ball: the column above the spot's upper half is not read.
BALL_CORRIDOR_RADII = 1.5
BALL_CORRIDOR_START_RADII = 0.3
STEP_MM = 0.5
NORMAL_SPEED_PASSES = 2

GEAR_LOOP_MAX_ITERATIONS = 6
GEAR_LOOP_TOLERANCE_MM = 1.0
GEAR_NOT_APPLIED = {
    "status": "not_applied",
    "reason": "S4 not run: the orientation assumes a centred strike; "
    "refine_with_orientation_update is the hook",
}


# --- S1: orientation ---------------------------------------------------------------


@dataclass(frozen=True)
class Orientation:
    """The head's orientation at impact, each angle labelled with its 1-sigma.

    ``face_angle``: analysis orientation, + open (the toe trailing). ``dynamic_loft``:
    the face normal's elevation. ``lie``: the face's roll about its normal, dynamic minus
    static lie, + toe up. ``notes`` say why an angle fell back to its prior.
    """

    face_angle: il.Labelled
    dynamic_loft: il.Labelled
    lie: il.Labelled
    notes: tuple[str, ...] = ()
    origins: Mapping[str, str] = field(default_factory=dict, compare=False)
    labels: Mapping[str, str] = field(default_factory=dict, compare=False)

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready fields."""
        return {
            name: getattr(self, name).as_dict() for name in ("face_angle", "dynamic_loft", "lie")
        }

    def values(self) -> tuple[float, float, float]:
        """(face angle, loft, lie) in degrees."""
        return (self.face_angle.value, self.dynamic_loft.value, self.lie.value)

    def provenance(self) -> dict[str, dict[str, str]]:
        """Each angle's origin (``ORIGIN_*``) and label; an unrecorded one is a given value."""
        return {
            name: {
                "origin": self.origins.get(name, ORIGIN_GIVEN),
                "label": self.labels.get(name, LABEL_ESTIMATED),
            }
            for name in ANGLES
        }


def _lie_prior() -> il.Labelled:
    return il.Labelled(0.0, LIE_PRIOR_SIGMA_DEG, PRIOR_LIE)


_PRIORS_ONLY = {name: ORIGIN_PRIOR for name in ANGLES}
_ESTIMATED = {name: LABEL_ESTIMATED for name in ANGLES}


def _lie_origin(lie: il.Labelled | None) -> tuple[str, str]:
    return (ORIGIN_PRIOR, LABEL_ESTIMATED) if lie is None else (ORIGIN_GIVEN, LABEL_ESTIMATED)


def orientation_priors(club: str | None) -> Orientation:
    """The category priors ``FaceInputs.priors`` keeps; lie the static lie, labelled."""
    face = il.FaceInputs.priors(club)
    return Orientation(
        face.face_angle, face.dynamic_loft, _lie_prior(), (), dict(_PRIORS_ONLY), dict(_ESTIMATED)
    )


def orientation_from_face_inputs(
    face: il.FaceInputs, lie: il.Labelled | None = None
) -> Orientation:
    """The face angle and loft a shot's ``FaceInputs`` carries (its own angles or priors).

    An angle with capture-bound evidence carries its producer's status as its label; a
    category prior (``FaceInputs.priors``, its source starting "prior") is a prior;
    anything else a given value.
    """
    origins, labels = {}, {}
    for name in ("face_angle", "dynamic_loft"):
        value = getattr(face, name)
        if value.evidence is not None:
            origins[name], labels[name] = ORIGIN_CAPTURE, value.evidence.status
        elif value.source.startswith("prior"):
            origins[name], labels[name] = ORIGIN_PRIOR, LABEL_ESTIMATED
        else:
            origins[name], labels[name] = ORIGIN_GIVEN, LABEL_ESTIMATED
    origins["lie"], labels["lie"] = _lie_origin(lie)
    return Orientation(face.face_angle, face.dynamic_loft, lie or _lie_prior(), (), origins, labels)


def orientation_from_collision(
    face_angle: Any,
    dynamic_loft: Any,
    *,
    club: str | None,
    handedness: str = "right",
    target_line_offset_deg: float | None = None,
    lie: il.Labelled | None = None,
) -> Orientation:
    """Face angle and loft from collision-model metrics (D-plane inversion), each labelled.

    A present metric whose status has a sigma (``ANGLE_SIGMA_BY_STATUS``) enters with it,
    its source starting with its status word; the face angle is brought into the
    analysis orientation by ``impact_session``'s rule. Anything else falls back to the
    category prior and says why in ``notes``.
    """
    prior = orientation_priors(club)
    notes: list[str] = []
    origins, labels = dict(prior.origins), dict(prior.labels)
    origins["lie"], labels["lie"] = _lie_origin(lie)

    def take(name: str, metric: Any, fallback: il.Labelled) -> il.Labelled:
        value = getattr(metric, "value", None)
        status = getattr(metric, "status", None)
        if value is None or not math.isfinite(float(value)) or status not in ANGLE_SIGMA_BY_STATUS:
            reason = getattr(metric, "reason", None) or f"status_{status}"
            notes.append(f"{name}: {reason}; prior used")
            return fallback
        value = float(value)
        if name == "face_angle":
            value = session.face_angle_in_analysis_orientation(
                value, getattr(metric, "frame", None), target_line_offset_deg, handedness
            )
            if value is None:
                notes.append(f"{name}: target_line_transform_unknown; prior used")
                return fallback
        origins[name], labels[name] = ORIGIN_D_PLANE, status
        return il.Labelled(value, ANGLE_SIGMA_BY_STATUS[status], f"{status} ({metric.source})")

    return Orientation(
        take("face_angle", face_angle, prior.face_angle),
        take("dynamic_loft", dynamic_loft, prior.dynamic_loft),
        lie or prior.lie,
        tuple(notes),
        origins,
        labels,
    )


def _recorded_metric(entry: Any) -> Metric:
    """A collision-model metric as a shot record carries it (``Metric.to_dict``)."""
    if not isinstance(entry, Mapping):
        return Metric(None, STATUS_NOT_MEASURED, None, "deg", reason="not_recorded_on_this_shot")
    return Metric(**{key: entry.get(key) for key in METRIC_FIELDS})


def orientation_for_shot(
    face: il.FaceInputs,
    collision: Mapping[str, Any] | None,
    *,
    club: str | None,
    handedness: str = "right",
    target_line_offset_deg: float | None = None,
    lie: il.Labelled | None = None,
) -> Orientation:
    """The one orientation rule for a recorded or live shot, each angle labelled.

    Per angle: the shot's own capture-bound angle (``FaceInputs`` evidence, as
    ``impact_session.shot_face_inputs`` accepts it), else the shot's collision-model
    D-plane inversion (``collision``: the shot record's ``collision_model`` block), else
    the category prior. Notes say why a D-plane angle was not used.
    """
    own = orientation_from_face_inputs(face, lie)
    metrics = collision.get("metrics") if isinstance(collision, Mapping) else None
    metrics = metrics if isinstance(metrics, Mapping) else {}
    d_plane = orientation_from_collision(
        _recorded_metric(metrics.get("face_angle")),
        _recorded_metric(metrics.get("dynamic_loft")),
        club=club,
        handedness=handedness,
        target_line_offset_deg=target_line_offset_deg,
        lie=lie,
    )
    chosen = {
        name: own if own.origins[name] == ORIGIN_CAPTURE else d_plane
        for name in ("face_angle", "dynamic_loft")
    }
    return Orientation(
        chosen["face_angle"].face_angle,
        chosen["dynamic_loft"].dynamic_loft,
        own.lie,
        tuple(note for note in d_plane.notes if chosen[note.split(":", 1)[0]] is d_plane),
        {**{n: chosen[n].origins[n] for n in chosen}, "lie": own.origins["lie"]},
        {**{n: chosen[n].labels[n] for n in chosen}, "lie": own.labels["lie"]},
    )


def lie_from_shaft(
    shaft_lie_deg: float, sigma_deg: float, club: str | None, source: str
) -> il.Labelled:
    """A shaft's lie to the ground as the face's roll: minus the category's static lie."""
    model = ClubModel.for_club(club)
    static = (
        model.static_lie_deg
        if model is not None
        else OUTLINE_REFERENCE["categories"]["iron"]["static_lie_deg"]
    )
    return il.Labelled(float(shaft_lie_deg) - float(static), float(sigma_deg), source)


def head_axes(
    face_deg: float, loft_deg: float, lie_deg: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(toe-ward, up the face, face normal) in the camera's level frame.

    The level frame: x image right (toward a right-handed toe), y DOWN, z downrange.
    Loft tilts the normal up and leans the face's top back toward the camera; an open
    face turns the normal to +x so the toe trails; toe-up lie raises the toe.
    """
    f, lo, li = (math.radians(v) for v in (face_deg, loft_deg, lie_deg))
    normal = np.array([math.sin(f) * math.cos(lo), -math.sin(lo), math.cos(f) * math.cos(lo)])
    toe = np.array([math.cos(f), 0.0, -math.sin(f)])
    up = np.array([-math.sin(f) * math.sin(lo), -math.cos(lo), -math.cos(f) * math.sin(lo)])
    return math.cos(li) * toe + math.sin(li) * up, -math.sin(li) * toe + math.cos(li) * up, normal


@dataclass(frozen=True)
class ContactPose:
    """Where the face touches the ball, and the face centre that strike puts the head at."""

    contact_point: np.ndarray
    face_centre: np.ndarray


def contact_pose(
    ball: np.ndarray, axes: Sequence[np.ndarray], strike_mm: tuple[float, float]
) -> ContactPose:
    """The contact constraint: P = B - r n_hat, the face centre ``strike`` away in the face."""
    toe, up, normal = axes
    point = np.asarray(ball, dtype=float) - il.BALL_RADIUS_MM * normal
    return ContactPose(point, point - strike_mm[0] * toe - strike_mm[1] * up)


# --- S2: the club model, the scene and the camera ----------------------------------------


@dataclass(frozen=True)
class ClubModel:  # pylint: disable=too-many-instance-attributes
    """The category's population-average head: outline, fitted edge samples, sole and centre."""

    category: str
    club: str
    outline_uv: np.ndarray
    samples: dict[str, tuple[np.ndarray, np.ndarray]]
    sole_width: il.Labelled
    effective_bounce: il.Labelled
    centre_from_toe: il.Labelled
    centre_height: il.Labelled
    static_lie_deg: float
    reference_from_toe: float = 0.0
    reference_height: float = 0.0
    source: str = "club_outline_reference"

    def with_centre(
        self, from_toe: il.Labelled | None = None, height: il.Labelled | None = None
    ) -> "ClubModel":
        """The same head read about another face centre (a marked photo's, a research override).

        The outline stays where the reference centre puts it; only the point the strike is
        read from moves.
        """
        return replace(
            self,
            centre_from_toe=from_toe or self.centre_from_toe,
            centre_height=height or self.centre_height,
        )

    @property
    def parts(self) -> tuple[str, ...]:
        """The outline's parts that have samples."""
        return tuple(name for name, (points, _n) in self.samples.items() if len(points))

    @classmethod
    def for_club(cls, club: str | None) -> "ClubModel | None":
        """The model for a kiosk club name; None for a category with no outline (woods)."""
        category = il.club_category(club)
        name = category or "iron"
        entry = OUTLINE_REFERENCE["categories"].get(name)
        if entry is None:
            return None
        widen = 1.0 if category else il.UNKNOWN_CLUB_SIGMA_SCALE
        suffix = "" if category else "_unknown_club"
        centre = il.FACE_CENTRE_REFERENCE["categories"][name]
        priors = il.CATEGORY_PRIORS[name]

        def labelled(value: float, sigma: float, source: str) -> il.Labelled:
            return il.Labelled(float(value), float(sigma) * widen, source + suffix)

        outline = np.asarray(entry["head_outline_uv_mm"], dtype=float)
        return cls(
            category=name,
            club=club or "",
            outline_uv=outline,
            samples=_edge_samples(outline, float(entry["heel_excluded_beyond_mm_from_toe"])),
            sole_width=labelled(*priors["sole_width"]),
            effective_bounce=labelled(*priors["effective_bounce"]),
            centre_from_toe=labelled(
                centre["centre_from_toe_mm"],
                centre["centre_from_toe_sigma_mm"],
                il.REFERENCE_FACE_CENTRE,
            ),
            centre_height=labelled(
                centre["centre_above_leading_edge_mm"],
                centre["centre_above_leading_edge_sigma_mm"],
                il.REFERENCE_FACE_CENTRE,
            ),
            static_lie_deg=float(entry["static_lie_deg"]),
            reference_from_toe=float(centre["centre_from_toe_mm"]),
            reference_height=float(centre["centre_above_leading_edge_mm"]),
        )

    def summary(self) -> dict[str, Any]:
        """JSON-ready description."""
        return {
            "category": self.category,
            "club": self.club,
            "source": self.source,
            "samples": {name: int(len(points)) for name, (points, _n) in self.samples.items()},
            "sole_width": self.sole_width.as_dict(),
            "effective_bounce": self.effective_bounce.as_dict(),
            "centre_from_toe": self.centre_from_toe.as_dict(),
            "centre_height": self.centre_height.as_dict(),
            "static_lie_deg": self.static_lie_deg,
        }


def _edge_samples(
    outline: np.ndarray, heel_beyond_mm: float
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Points every ``SAMPLE_SPACING_MM`` along the outline with outward normals, by part."""
    closed = np.vstack([outline, outline[:1]])
    area = 0.5 * float(np.sum(closed[:-1, 0] * closed[1:, 1] - closed[1:, 0] * closed[:-1, 1]))
    toe_u = float(outline[:, 0].max())
    parts: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {
        "toe": [],
        "sole": [],
        "topline": [],
        "corner": [],
        "heel": [],
    }
    carry = 0.0
    for start, stop in zip(closed[:-1], closed[1:]):
        edge = stop - start
        length = float(np.hypot(*edge))
        if length <= 1e-9:
            continue
        direction = edge / length
        normal = (
            np.array([direction[1], -direction[0]])
            if area > 0
            else np.array([-direction[1], direction[0]])
        )
        position = carry
        while position < length:
            point = start + direction * position
            if toe_u - point[0] > heel_beyond_mm:
                part = "heel"
            elif normal[1] < -PART_NORMAL_COSINE:
                part = "sole"
            elif normal[0] > PART_NORMAL_COSINE:
                part = "toe"
            elif normal[1] > PART_NORMAL_COSINE:
                part = "topline"
            else:
                part = "corner"
            parts[part].append((point, normal))
            position += SAMPLE_SPACING_MM
        carry = position - length
    return {
        name: (
            np.array([p for p, _ in items]).reshape(-1, 2),
            np.array([n for _, n in items]).reshape(-1, 2),
        )
        for name, items in parts.items()
    }


@dataclass(frozen=True)
class SceneHeights:
    """Heights above the hitting surface, mm, each labelled.

    The lens height is the one impact_location's face inputs carry
    (``impact_session.shot_lens_height_mm``: the shot's own surface solve, else the
    setup's, else the rig file's); there is no second height path. The ball's centre is
    one radius above the hitting surface (``surface_height``'s convention).
    """

    lens_height: il.Labelled
    ball_height: il.Labelled = il.Labelled(
        il.BALL_RADIUS_MM, SCENE_BALL_HEIGHT_SIGMA_MM, BALL_ON_SURFACE
    )

    @classmethod
    def from_face_inputs(cls, face: il.FaceInputs) -> "SceneHeights":
        """The lens height a shot's face inputs carry."""
        return cls(lens_height=face.lens_height)

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready fields."""
        return {
            "lens_height": self.lens_height.as_dict(),
            "ball_height": self.ball_height.as_dict(),
        }


@dataclass(frozen=True)
class CameraTilt:
    """The camera's pitch (+ up) and roll about its axis, labelled; level unless measured."""

    pitch: il.Labelled
    roll: il.Labelled

    @classmethod
    def level(cls) -> "CameraTilt":
        """The rig design's level camera, labelled as an assumption."""
        return cls(
            il.Labelled(0.0, CAMERA_TILT_SIGMA_DEG, CAMERA_LEVEL),
            il.Labelled(0.0, CAMERA_TILT_SIGMA_DEG, CAMERA_LEVEL),
        )

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready fields."""
        return {"pitch": self.pitch.as_dict(), "roll": self.roll.as_dict()}


def _level_to_camera(pitch_deg: float, roll_deg: float) -> np.ndarray:
    """Rotation taking level-frame vectors into the camera frame (pitched up, then rolled)."""
    p, r = math.radians(pitch_deg), math.radians(roll_deg)
    pitch = np.array(
        [[1.0, 0.0, 0.0], [0.0, math.cos(p), math.sin(p)], [0.0, -math.sin(p), math.cos(p)]]
    )
    roll = np.array(
        [[math.cos(r), math.sin(r), 0.0], [-math.sin(r), math.cos(r), 0.0], [0.0, 0.0, 1.0]]
    )
    return roll @ pitch


# --- the fit state -------------------------------------------------------------------


@dataclass(frozen=True)
class _Inputs:  # pylint: disable=too-many-instance-attributes
    """Every number the placement depends on, so a budget term is a refit with one changed."""

    face: float
    loft: float
    lie: float
    pitch: float
    roll: float
    range_mm: float
    speed: float
    attack: float
    path: float
    sole_width: float
    bounce: float
    fraction: float


@dataclass
class _Frame:
    """One frame's difference image, its mask and timing."""

    index: int
    diff: np.ndarray
    clipped: np.ndarray
    time_ms: float
    after_contact: bool
    masked: str
    coarse: np.ndarray | None = None


@dataclass
class FramePlacement:  # pylint: disable=too-many-instance-attributes
    """One bracket frame's fitted in-face-plane position and what the edges said."""

    frame: int
    toe_mm: float
    up_mm: float
    normal_mm: float
    covariance_mm2: np.ndarray
    edges: dict[str, dict[str, Any]]
    outline_px: np.ndarray
    sole_px: np.ndarray
    samples_px: dict[str, tuple[np.ndarray, np.ndarray]]
    anchor_px: np.ndarray | None = None
    diff: np.ndarray | None = field(default=None, repr=False)
    contact_point_px: tuple[float, float] | None = None
    face_centre_px: tuple[float, float] | None = None

    def start(self) -> tuple[float, float, np.ndarray | None]:
        """Where a refit of this frame starts: here, held on the same image point."""
        return (self.toe_mm, self.up_mm, self.anchor_px)


@dataclass
class _Solution:
    """One solve: the strike, the frames, and the normal speed it used."""

    strike: tuple[float, float]
    fits: dict[int, FramePlacement]
    normal_speed: float
    in_plane_velocity: np.ndarray
    normal_speed_source: str


class _Refused(Exception):
    """A named refusal from inside the fit; ``axis`` is 'offset', 'height' or 'shot'."""

    def __init__(self, reason: str, axis: str = "shot") -> None:
        super().__init__(reason)
        self.reason = reason
        self.axis = axis


class _Placer:  # pylint: disable=too-many-instance-attributes
    """The fit for one prepared shot: frames, geometry, per-frame ICP and interpolation."""

    def __init__(self, shot: il.PreparedShot, model: ClubModel) -> None:
        self.shot = shot
        self.model = model
        camera = shot.camera
        self.focal = float(camera.focal_px)
        self.centre = np.asarray(camera.principal_point_px, dtype=float)
        self.exposure_ms = (shot.exposure_us or 0.0) / 1000.0
        self.frame_ms = 1000.0 / shot.fps
        last_rest = shot.contact.last_rest_frame
        assert last_rest is not None
        self.last_rest = last_rest
        self.ball_ray = np.array(
            [
                (shot.ball.x - self.centre[0]) / self.focal,
                (shot.ball.y - self.centre[1]) / self.focal,
                1.0,
            ]
        )
        frames = shot.frames
        first = shot.window[0] if shot.window else max(0, last_rest - 1)
        stop = min(first, last_rest + 1 - int(math.ceil(il.BACKGROUND_LEAD_S * shot.fps)))
        if stop < il.MIN_BACKGROUND_FRAMES:
            raise _Refused("no_background_frames_before_the_swing")
        stack = frames[:stop].astype(np.float32)
        self.background = np.median(stack, axis=0)
        self.noise = float(np.median(stack.std(axis=0)))
        self.floor = max(MIN_EDGE_CONTRAST_DN, EDGE_CONTRAST_NOISE * self.noise)
        self.after_background, self.after_background_source = self._post_departure_background()
        self._frames: dict[int, _Frame] = {}

    # -- images --

    def _ball_disk(self, pad: float = BALL_DISK_PAD_PX) -> np.ndarray:
        rows, cols = np.mgrid[0 : self.shot.frames.shape[1], 0 : self.shot.frames.shape[2]]
        radius = self.shot.ball.diameter_px / 2.0 + pad
        return (cols - self.shot.ball.x) ** 2 + (rows - self.shot.ball.y) ** 2 <= radius**2

    def _post_departure_background(self) -> tuple[np.ndarray | None, str]:
        """The resting ball's spot after the ball left, from the clip's last quiet frames."""
        frames = self.shot.frames
        start = max(
            self.last_rest + 1 + POST_DEPARTURE_GAP_FRAMES, frames.shape[0] - POST_DEPARTURE_FRAMES
        )
        if frames.shape[0] - start < 2:
            return None, "masked_no_late_frames"
        late = frames[start:].astype(np.float32)
        disk = self._ball_disk()
        spread = float(np.median(late.std(axis=0)[disk]))
        if spread > 3.0 * self.noise + 3.0:
            return None, f"masked_late_frames_not_quiet_{spread:.1f}_dn"
        return np.median(
            late, axis=0
        ), f"post_departure_median_of_frames_{start}_{frames.shape[0] - 1}"

    def frame(self, index: int) -> _Frame:
        """The background-difference image of one frame, ball spots masked or replaced."""
        if index in self._frames:
            return self._frames[index]
        image = self.shot.frames[index].astype(np.float32)
        reference = self.background.copy()
        after = index > self.last_rest
        masked = "none"
        if after:
            disk = self._ball_disk()
            if self.after_background is not None:
                reference[disk] = self.after_background[disk]
                masked = self.after_background_source
            else:
                masked = self.after_background_source
        diff = np.abs(image - reference)
        if after:
            if self.after_background is None:
                diff[self._ball_disk()] = np.nan
            rows, cols = np.mgrid[0 : diff.shape[0], 0 : diff.shape[1]]
            radius = self.shot.ball.diameter_px / 2.0
            corridor = (
                np.abs(cols - self.shot.ball.x) <= BALL_CORRIDOR_RADII * radius + BALL_DISK_PAD_PX
            ) & (rows <= self.shot.ball.y - BALL_CORRIDOR_START_RADII * radius)
            diff[corridor] = np.nan
            masked += "_departure_corridor"
            found = il.flying_ball(self.shot.frames[index], self.shot.ball, self.background)
            if found is not None:
                x, y, veto = found
                diff[(cols - x) ** 2 + (rows - y) ** 2 <= (veto + BALL_DISK_PAD_PX) ** 2] = np.nan
                masked += "_flying_ball"
        record = _Frame(
            index,
            diff,
            np.minimum(diff, EDGE_CLIP_FLOORS * self.floor),
            self._time(index),
            after,
            masked,
        )
        self._frames[index] = record
        return record

    def _time(self, index: int) -> float:
        """Mid-exposure time of a frame, ms."""
        return index * self.frame_ms + self.exposure_ms / 2.0

    def contact_ms(self, fraction: float) -> float:
        """Contact: after the last rest frame's exposure ends, ``fraction`` into the gap."""
        return (self.last_rest + fraction) * self.frame_ms + self.exposure_ms

    # -- geometry --

    def rotation(self, inputs: _Inputs) -> np.ndarray:
        """Level frame to camera frame for these inputs' pitch and roll."""
        return _level_to_camera(inputs.pitch, inputs.roll)

    def ball_level(self, inputs: _Inputs) -> np.ndarray:
        """The ball centre in the level frame from its pixel and depth."""
        return self.rotation(inputs).T @ (self.ball_ray * inputs.range_mm)

    def velocity(self, inputs: _Inputs) -> np.ndarray:
        """The club's velocity before contact in the level frame, mm/ms."""
        p, a = math.radians(inputs.path), math.radians(inputs.attack)
        return inputs.speed * np.array(
            [math.sin(p) * math.cos(a), -math.sin(a), math.cos(p) * math.cos(a)]
        )

    def project(self, points: np.ndarray, rotation: np.ndarray) -> np.ndarray:
        """Level-frame points (mm) to analysis pixels through the pinhole."""
        camera = np.asarray(points, dtype=float) @ rotation.T
        return self.centre + self.focal * camera[..., :2] / camera[..., 2:3]

    def head_points(
        self, inputs: _Inputs, axes, origin: np.ndarray, uv: np.ndarray, sole: bool
    ) -> tuple[np.ndarray, np.ndarray | None]:
        """Face-plane points of the head at a pose and, for the sole, its trailing edge."""
        toe, up, normal = axes
        points = origin + uv[:, :1] * toe + uv[:, 1:2] * up
        if not sole:
            return points, None
        back = -np.array([normal[0], 0.0, normal[2]])
        back /= max(float(np.hypot(back[0], back[2])), 1e-12)
        bounce = math.radians(inputs.bounce)
        offset = inputs.sole_width * (
            math.cos(bounce) * back + math.sin(bounce) * np.array([0.0, 1.0, 0.0])
        )
        return points, points + offset

    @staticmethod
    def origin(axes, contact_point, toe_mm, up_mm, normal_mm) -> np.ndarray:
        """The face centre: the contact point less the in-plane position, off along the normal."""
        toe, up, normal = axes
        return contact_point - toe_mm * toe - up_mm * up + normal_mm * normal

    def silhouette(self, inputs, axes, origin, rotation, part: str):
        """(image points, image outward normals) of one part's samples at a pose."""
        uv, normals_uv = self.model.samples[part]
        toe, up, _normal = axes
        lead, trail = self.head_points(inputs, axes, origin, uv, sole=part == "sole")
        points = lead
        if trail is not None:
            lead_px, trail_px = self.project(lead, rotation), self.project(trail, rotation)
            points = np.where((trail_px[:, 1] > lead_px[:, 1])[:, None], trail, lead)
        outward = normals_uv[:, :1] * toe + normals_uv[:, 1:2] * up
        start = self.project(points, rotation)
        ahead = self.project(points + STEP_MM * outward, rotation)
        direction = ahead - start
        direction /= np.maximum(np.hypot(direction[:, 0], direction[:, 1]), 1e-12)[:, None]
        return start, direction, points

    # -- edges --

    def contrast(
        self,
        diff: np.ndarray,
        points: np.ndarray,
        normals: np.ndarray,
        box_px: np.ndarray,
        deltas: np.ndarray,
    ) -> np.ndarray:
        """Box contrast across the edge (inside mean minus outside mean), per sample and shift."""
        steps = np.arange(EDGE_BOX_STEP_PX, float(box_px.max()) + 1e-9, EDGE_BOX_STEP_PX)
        weight = (steps[None, :] <= box_px[:, None] + 1e-9).astype(float)
        weight /= weight.sum(axis=1, keepdims=True)
        offsets = deltas[None, :, None] - steps[None, None, :]
        inside = points[:, None, None, :] + offsets[..., None] * normals[:, None, None, :]
        outside = (
            points[:, None, None, :]
            + (deltas[None, :, None] + steps[None, None, :])[..., None] * normals[:, None, None, :]
        )

        def sample(where: np.ndarray) -> np.ndarray:
            flat = where.reshape(-1, 2)
            values = ndimage.map_coordinates(diff, [flat[:, 1], flat[:, 0]], order=1, cval=np.nan)
            return values.reshape(where.shape[:-1])

        inner = np.sum(sample(inside) * weight[:, None, :], axis=2)
        outer = np.sum(sample(outside) * weight[:, None, :], axis=2)
        return inner - outer

    def locate_edges(self, record: "_Frame", points, normals, box_px):
        """Each sample's signed distance to the observed edge along its normal, and contrast.

        The edge is chosen on the SATURATED difference (``EDGE_CLIP_FLOORS`` times the
        floor): the head's silhouette is changed-inside against unchanged-outside, so an
        edge inside the head (the resting ball behind it, a highlight) cannot outscore it.
        Its position is then refined on the raw difference within ``EDGE_REFINE_PX``, where
        a blurred edge's box contrast peaks at its mid-exposure position.
        """
        deltas = np.arange(-EDGE_SEARCH_PX, EDGE_SEARCH_PX + 1e-9, EDGE_SEARCH_STEP_PX)
        chosen = self.contrast(record.clipped, points, normals, box_px, deltas)
        raw = self.contrast(record.diff, points, normals, box_px, deltas)
        usable = np.isfinite(chosen) & np.isfinite(raw)
        safe = np.where(usable, chosen, -np.inf)
        best = np.argmax(safe, axis=1)
        strength = safe[np.arange(len(best)), best]
        valid = np.isfinite(strength)
        near = np.abs(deltas[None, :] - deltas[best][:, None]) <= EDGE_REFINE_PX + 1e-9
        refined = np.where(near & usable, raw, -np.inf)
        top = np.argmax(refined, axis=1)
        peak = refined[np.arange(len(top)), top]
        shift = deltas[top].astype(float)
        rows = np.arange(len(best))
        inside = (best > 0) & (best < len(deltas) - 1)
        interior = inside & (top > 0) & (top < len(deltas) - 1)
        interior &= np.isfinite(safe[rows, np.clip(best - 1, 0, None)])
        interior &= np.isfinite(safe[rows, np.clip(best + 1, None, len(deltas) - 1)])
        for i in np.flatnonzero(interior & valid):
            left, mid, right = refined[i, top[i] - 1], refined[i, top[i]], refined[i, top[i] + 1]
            if not (math.isfinite(left) and math.isfinite(right)):
                continue
            denominator = left - 2.0 * mid + right
            if denominator < 0.0:
                shift[i] += 0.5 * (left - right) / denominator * EDGE_SEARCH_STEP_PX
        accepted = valid & interior & (strength >= self.floor)
        return shift, peak, accepted

    def predicted_blur(self, inputs, axes, origin, rotation, part, in_plane_velocity) -> np.ndarray:
        """Each sample's image motion across its own edge during the exposure, px."""
        if self.exposure_ms <= 0.0:
            return np.zeros(len(self.model.samples[part][0]))
        start, normals, points = self.silhouette(inputs, axes, origin, rotation, part)
        moved = self.project(points + in_plane_velocity * self.exposure_ms, rotation)
        return np.abs(np.sum((moved - start) * normals, axis=1))

    # -- one frame --

    def fit_frame(
        self,
        inputs: _Inputs,
        record: _Frame,
        start: tuple[float, float],
        normal_mm: float,
        velocity3: np.ndarray,
        *,
        coarse: bool,
    ) -> FramePlacement:
        """One frame: (coarse search, then) ICP on the toe and sole edges at a fixed normal."""
        axes = head_axes(inputs.face, inputs.loft, inputs.lie)
        rotation = self.rotation(inputs)
        contact_point = self.ball_level(inputs) - il.BALL_RADIUS_MM * axes[2]
        toe_mm, up_mm = start
        if coarse:
            toe_mm, up_mm = self.coarse(
                inputs, axes, rotation, record, contact_point, start, normal_mm
            )
        edges: dict[str, dict[str, Any]] = {}
        covariance = np.eye(2)
        samples_px: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for _ in range(ICP_ITERATIONS):
            origin = self.origin(axes, contact_point, toe_mm, up_mm, normal_mm)
            rows, residuals, counts = [], [], {}
            for part in FITTED_PARTS:
                points, normals, points3d = self.silhouette(inputs, axes, origin, rotation, part)
                if len(points) == 0:
                    counts[part] = 0
                    edges[part] = {"samples": 0, "accepted": 0, "predicted_blur_px": 0.0}
                    continue
                blur = self.predicted_blur(inputs, axes, origin, rotation, part, velocity3)
                box = np.maximum(EDGE_BOX_MIN_PX, 0.5 * blur + 1.0)
                shift, peak, accepted = self.locate_edges(record, points, normals, box)
                counts[part] = int(accepted.sum())
                jacobian = self._jacobian(axes, rotation, normals, points3d)
                rows.append(jacobian[accepted])
                residuals.append(shift[accepted])
                samples_px[part] = (points, accepted)
                edges[part] = {
                    "samples": int(len(points)),
                    "accepted": int(accepted.sum()),
                    "predicted_blur_px": round(float(np.median(blur)) if len(blur) else 0.0, 2),
                    "median_contrast_dn": round(float(np.median(peak[accepted])), 1)
                    if accepted.any()
                    else None,
                }
            jac = np.vstack(rows) if rows else np.zeros((0, 2))
            res = np.concatenate(residuals) if residuals else np.zeros(0)
            if len(res) == 0:
                break
            scale = max(1.4826 * float(np.median(np.abs(res - np.median(res)))), MIN_RESIDUAL_PX)
            cut = TUKEY_C * scale
            weight = np.where(np.abs(res) < cut, (1.0 - (res / cut) ** 2) ** 2, 0.0)
            lock = np.diag(
                [
                    LOCKED_AXIS if counts.get("toe", 0) < MIN_EDGE_SAMPLES["toe"] else 1e-9,
                    LOCKED_AXIS if counts.get("sole", 0) < MIN_EDGE_SAMPLES["sole"] else 1e-9,
                ]
            )
            normal_matrix = jac.T @ (jac * weight[:, None]) + lock
            step = np.linalg.solve(normal_matrix, jac.T @ (weight * res))
            toe_mm += float(step[0])
            up_mm += float(step[1])
            kept = weight > 0
            spread = max(
                1.4826 * float(np.median(np.abs(res[kept]))) if kept.any() else MIN_RESIDUAL_PX,
                MIN_RESIDUAL_PX,
            )
            covariance = spread**2 * np.linalg.inv(normal_matrix)
            if math.hypot(*step) < ICP_TOLERANCE_MM:
                break
        origin = self.origin(axes, contact_point, toe_mm, up_mm, normal_mm)
        depth = float((rotation @ origin)[2])
        floor = il.ALIGN_SIGMA_PX * depth / self.focal
        covariance = covariance.copy()
        for axis in range(2):
            covariance[axis, axis] = max(covariance[axis, axis], floor**2)
        topline, topline_normals, _ = self.silhouette(inputs, axes, origin, rotation, "topline")
        shift, _peak, accepted = self.locate_edges(
            record, topline, topline_normals, np.full(len(topline), EDGE_BOX_MIN_PX)
        )
        edges["topline"] = {
            "samples": int(len(topline)),
            "accepted": int(accepted.sum()),
            "median_residual_mm": round(float(np.median(shift[accepted])) * depth / self.focal, 2)
            if accepted.any()
            else None,
            "fitted": False,
        }
        samples_px["topline"] = (topline, accepted)
        outline, _sole = self.head_points(inputs, axes, origin, self.model.outline_uv, sole=True)
        sole_rows = self.model.samples["sole"][0]
        _lead, trail = self.head_points(inputs, axes, origin, sole_rows, sole=True)
        return FramePlacement(
            anchor_px=self.project(origin[None, :], rotation)[0],
            diff=record.diff,
            frame=record.index,
            toe_mm=toe_mm,
            up_mm=up_mm,
            normal_mm=normal_mm,
            covariance_mm2=covariance,
            edges=edges,
            outline_px=self.project(outline, rotation),
            sole_px=self.project(trail, rotation) if trail is not None else np.zeros((0, 2)),
            samples_px=samples_px,
        )

    def coarse_image(self, record: "_Frame") -> np.ndarray:
        """The saturated difference with its masked spots filled in from around them.

        A masked spot read as zero would put a false edge on its rim; filled from its
        surroundings it carries no edge of its own.
        """
        if record.coarse is None:
            clipped = record.clipped
            hole = ~np.isfinite(clipped)
            if hole.any():
                level = EDGE_CLIP_FLOORS * self.floor
                scaled = np.round(np.nan_to_num(clipped, nan=0.0) * 255.0 / level).astype(np.uint8)
                filled = cv2.inpaint(scaled, hole.astype(np.uint8), 3, cv2.INPAINT_TELEA)
                record.coarse = filled.astype(np.float32) * level / 255.0
            else:
                record.coarse = clipped.astype(np.float32)
        return record.coarse

    def reanchor(self, inputs, axes, normal_mm, start) -> tuple[float, float]:
        """The in-plane position that puts the face centre back on its earlier image point.

        A refit with a changed input (orientation, timing, range...) starts here, so the
        edges it searches for are where the earlier fit found them.
        """
        toe_mm, up_mm, anchor = start
        if anchor is None:
            return toe_mm, up_mm
        rotation = self.rotation(inputs)
        contact_point = self.ball_level(inputs) - il.BALL_RADIUS_MM * axes[2]
        toe, up, _normal = axes
        for _ in range(4):
            origin = self.origin(axes, contact_point, toe_mm, up_mm, normal_mm)
            here = self.project(origin[None, :], rotation)[0]
            jacobian = np.zeros((2, 2))
            for column, direction in enumerate((toe, up)):
                moved = self.project((origin - STEP_MM * direction)[None, :], rotation)[0]
                jacobian[:, column] = (moved - here) / STEP_MM
            delta = np.linalg.solve(jacobian, np.asarray(anchor) - here)
            toe_mm, up_mm = toe_mm + float(delta[0]), up_mm + float(delta[1])
            if math.hypot(*delta) < ICP_TOLERANCE_MM:
                break
        return toe_mm, up_mm

    def _jacobian(self, axes, rotation, normals, points):
        """d(image position along each sample's normal) / d(toe_mm, up_mm), px per mm."""
        toe, up, _normal = axes
        base = self.project(points, rotation)
        out = np.zeros((len(points), 2))
        for column, direction in enumerate((toe, up)):
            moved = self.project(points - STEP_MM * direction, rotation)
            out[:, column] = np.sum((moved - base) * normals, axis=1) / STEP_MM
        return out

    def coarse(
        self, inputs, axes, rotation, record, contact_point, start, normal_mm
    ) -> tuple[float, float]:
        """The best translation over a wide window: box contrast of every edge but the heel's."""
        origin = self.origin(axes, contact_point, start[0], start[1], normal_mm)
        anchor = self.project(origin[None, :], rotation)[0]
        weights: list[tuple[np.ndarray, float]] = []
        for part in ("toe", "sole", "topline"):
            points, normals, _ = self.silhouette(inputs, axes, origin, rotation, part)
            if len(points) == 0:
                continue
            steps = np.arange(EDGE_BOX_STEP_PX, EDGE_BOX_MIN_PX + 1e-9, EDGE_BOX_STEP_PX)
            value = 1.0 / (len(steps) * len(points))
            for step in steps:
                weights.extend((p, value) for p in points - step * normals)
                weights.extend((p, -value) for p in points + step * normals)
        positions = np.array([p for p, _ in weights])
        values = np.array([v for _, v in weights])
        x0, y0 = np.floor(positions.min(axis=0)) - 1
        x1, y1 = np.ceil(positions.max(axis=0)) + 2
        kernel = np.zeros((int(y1 - y0) + 1, int(x1 - x0) + 1), np.float32)
        local = positions - np.array([x0, y0])
        cols, rows = np.floor(local[:, 0]).astype(int), np.floor(local[:, 1]).astype(int)
        fx, fy = local[:, 0] - cols, local[:, 1] - rows
        for dx, dy, share in (
            (0, 0, (1 - fx) * (1 - fy)),
            (1, 0, fx * (1 - fy)),
            (0, 1, (1 - fx) * fy),
            (1, 1, fx * fy),
        ):
            np.add.at(kernel, (rows + dy, cols + dx), (values * share).astype(np.float32))
        ax, ay = int(round(anchor[0] - x0)), int(round(anchor[1] - y0))
        ax = min(max(ax, 0), kernel.shape[1] - 1)
        ay = min(max(ay, 0), kernel.shape[0] - 1)
        image = self.coarse_image(record)
        score = cv2.filter2D(
            image, cv2.CV_32F, kernel, anchor=(ax, ay), borderType=cv2.BORDER_CONSTANT
        )
        depth = float((rotation @ origin)[2])
        in_plane = abs(record.time_ms - self.contact_ms(inputs.fraction)) * inputs.speed
        radius_mm = COARSE_STRIKE_MM + COARSE_MARGIN_MM + 0.5 * in_plane
        radius_px = radius_mm * self.focal / depth
        centre_x, centre_y = x0 + ax, y0 + ay
        rows_grid, cols_grid = np.mgrid[0 : score.shape[0], 0 : score.shape[1]]
        window = (cols_grid - centre_x) ** 2 + (rows_grid - centre_y) ** 2 <= radius_px**2
        if not window.any():
            raise _Refused(f"head_not_found_on_frame_{record.index}_outside_the_image")
        masked = np.where(window, score, -np.inf)
        flat = int(np.argmax(masked))
        best_y, best_x = divmod(flat, score.shape[1])
        best = float(masked.ravel()[flat])
        if best < 0.5 * self.floor:
            raise _Refused(
                f"head_not_found_on_frame_{record.index}_best_edge_contrast_{best:.1f}_dn"
            )
        if math.hypot(best_x - centre_x, best_y - centre_y) > radius_px - 1.5:
            raise _Refused(
                f"head_not_found_on_frame_{record.index}_search_ended_on_its_"
                f"{radius_mm:.0f}_mm_boundary"
            )
        shift = np.array([best_x - centre_x, best_y - centre_y], dtype=float)
        toe, up, _normal = axes
        jacobian = np.zeros((2, 2))
        for column, direction in enumerate((toe, up)):
            moved = self.project((origin - STEP_MM * direction)[None, :], rotation)[0]
            jacobian[:, column] = (moved - anchor) / STEP_MM
        delta = np.linalg.solve(jacobian, shift)
        return start[0] + float(delta[0]), start[1] + float(delta[1])

    # -- the bracket --

    def solve(
        self,
        inputs: _Inputs,
        starts: dict[int, tuple[float, float, np.ndarray | None]] | None,
        frames: tuple[int, int],
        *,
        coarse: bool,
    ) -> "_Solution":
        """Both bracket frames fitted, then interpolated to contact.

        The head's speed along its normal sets where it sits off the contact plane on each
        frame. It starts from the prior club direction; then the fitted in-face-plane
        velocity is taken out of the club speed (the head's speed is all one vector),
        and the frames are refitted with what is left, ``NORMAL_SPEED_PASSES`` times.
        """
        axes = head_axes(inputs.face, inputs.loft, inputs.lie)
        prior = self.velocity(inputs)
        normal_speed = float(prior @ axes[2])
        in_plane = prior - normal_speed * axes[2]
        source = "prior_club_direction"
        solution = None
        for attempt in range(NORMAL_SPEED_PASSES + 1):
            fits = self._fit_frames(
                inputs, starts, frames, axes, normal_speed, in_plane, coarse=coarse and attempt == 0
            )
            starts = {f: fits[f].start() for f in frames}
            fitted = self.in_plane_velocity(inputs, fits, frames, axes)
            solution = _Solution(
                self.interpolate(inputs, fits, frames), fits, normal_speed, fitted, source
            )
            if attempt == NORMAL_SPEED_PASSES:
                break
            remaining = inputs.speed**2 - float(fitted @ fitted)
            if remaining <= 0.0:
                solution.normal_speed_source = (
                    "prior_club_direction_fitted_in_plane_speed_exceeds_club_speed"
                )
                break
            normal_speed, in_plane, source = (
                math.sqrt(remaining),
                fitted,
                "club_speed_less_fitted_in_plane_speed",
            )
        assert solution is not None
        return solution

    def _fit_frames(self, inputs, starts, frames, axes, normal_speed, in_plane, *, coarse):
        contact = self.contact_ms(inputs.fraction)
        velocity = in_plane + normal_speed * axes[2]
        fits: dict[int, FramePlacement] = {}
        for index in frames:
            record = self.frame(index)
            elapsed = record.time_ms - contact
            factor = 1.0 if elapsed <= 0.0 else il.POST_IMPACT_SPEED_FRACTION
            normal_mm = normal_speed * factor * elapsed
            if starts is not None and index in starts:
                start = self.reanchor(inputs, axes, normal_mm, starts[index])
            else:
                start = (
                    -float(velocity @ axes[0]) * factor * elapsed,
                    -float(velocity @ axes[1]) * factor * elapsed,
                )
            fits[index] = self.fit_frame(
                inputs, record, start, normal_mm, velocity * factor, coarse=coarse
            )
        return fits

    def in_plane_velocity(self, inputs, fits, frames, axes) -> np.ndarray:
        """The head's in-face-plane velocity before contact the two fits imply, mm/ms."""
        contact = self.contact_ms(inputs.fraction)
        lead = max(contact - self._time(frames[0]), 0.0)
        lag = max(self._time(frames[1]) - contact, 0.0) * il.POST_IMPACT_SPEED_FRACTION
        if lead + lag <= 0.0:
            return np.zeros(3)
        before, after = fits[frames[0]], fits[frames[1]]
        toe_rate = -(after.toe_mm - before.toe_mm) / (lead + lag)
        up_rate = -(after.up_mm - before.up_mm) / (lead + lag)
        return toe_rate * axes[0] + up_rate * axes[1]

    def interpolate(self, inputs, fits, frames) -> tuple[float, float]:
        """The two frames' in-plane positions at contact."""
        before, after = fits[frames[0]], fits[frames[1]]
        weight_before, weight_after = self.weights(inputs, frames)
        return (
            weight_before * before.toe_mm + weight_after * after.toe_mm,
            weight_before * before.up_mm + weight_after * after.up_mm,
        )

    def weights(self, inputs: _Inputs, frames: tuple[int, int]) -> tuple[float, float]:
        """Interpolation weights to contact with the head slowed after it (linear in time)."""
        contact = self.contact_ms(inputs.fraction)
        before_gap = max(contact - self._time(frames[0]), 0.0)
        after_gap = max(self._time(frames[1]) - contact, 0.0) * il.POST_IMPACT_SPEED_FRACTION
        total = before_gap + after_gap
        if total <= 0.0:
            return 0.5, 0.5
        return after_gap / total, before_gap / total


# --- S3: the reading -----------------------------------------------------------------


@dataclass(frozen=True)
class ImpactPlacement:  # pylint: disable=too-many-instance-attributes
    """One shot's strike from the placed club. EXPERIMENTAL and UNQUALIFIED.

    ``impact_offset_mm`` heel to toe about the category's face centre (+ toe, either
    hand); ``impact_height_mm`` up the face (+ up). ``status``/``reason`` cover the offset
    (the shot fails with it); ``height_status``/``height_reason`` the height, which can
    fail alone. Sigmas are 1-sigma budgets of named terms in quadrature.
    """

    status: str
    reason: str
    label: str = LABEL
    handedness: str = "right"
    impact_offset_mm: float | None = None
    impact_offset_sigma_mm: float | None = None
    height_status: str = "rejected"
    height_reason: str = "not_reached"
    impact_height_mm: float | None = None
    impact_height_sigma_mm: float | None = None
    contact_frame: int | None = None
    last_rest_frame: int | None = None
    face_centre_convention: str = il.FACE_CENTRE_CONVENTION
    diagnostics: dict[str, Any] = field(default_factory=dict)
    frames: dict[int, FramePlacement] = field(default_factory=dict, repr=False)

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready fields (the per-frame pixels stay behind)."""
        return {
            "status": self.status,
            "reason": self.reason,
            "label": self.label,
            "handedness": self.handedness,
            "impact_offset_mm": self.impact_offset_mm,
            "impact_offset_sigma_mm": self.impact_offset_sigma_mm,
            "height_status": self.height_status,
            "height_reason": self.height_reason,
            "impact_height_mm": self.impact_height_mm,
            "impact_height_sigma_mm": self.impact_height_sigma_mm,
            "contact_frame": self.contact_frame,
            "last_rest_frame": self.last_rest_frame,
            "face_centre_convention": self.face_centre_convention,
            "diagnostics": self.diagnostics,
        }


def _labelled_ok(value: il.Labelled) -> bool:
    return math.isfinite(value.value) and math.isfinite(value.sigma) and value.sigma >= 0.0


def place_impact(  # pylint: disable=too-many-return-statements,too-many-branches,too-many-statements
    shot: il.PreparedShot,
    orientation: Orientation,
    *,
    scene: SceneHeights,
    tilt: CameraTilt | None = None,
    model: ClubModel | None = None,
    attack_angle: il.Labelled | None = None,
    club_path: il.Labelled | None = None,
    centre_from_toe: il.Labelled | None = None,
    centre_height: il.Labelled | None = None,
) -> ImpactPlacement:
    """Place the club model at the given orientation on the shot's bracket frames; read the strike.

    ``shot`` is ``impact_location.prepare_shot``'s (analysis orientation). ``attack_angle``
    and ``club_path`` set the club's direction (normal speed and blur); they default to
    the category prior and a square path, labelled. ``centre_from_toe`` and
    ``centre_height`` move the face centre the strike is read from (a marked face photo's
    anchor, a research override); by default the category's reference centre. Fails
    closed with a named reason.
    """
    tilt = tilt or CameraTilt.level()
    priors = il.FaceInputs.priors(shot.club)
    attack_angle = attack_angle or priors.attack_angle
    club_path = club_path or il.Labelled(0.0, PATH_PRIOR_SIGMA_DEG, PRIOR_PATH)
    contact = shot.contact
    diagnostics: dict[str, Any] = {
        "orientation": orientation.as_dict(),
        "orientation_notes": list(orientation.notes),
        "scene": scene.as_dict(),
        "camera": {
            "focal_px": shot.camera.focal_px,
            "source": shot.camera.source,
            "tilt": tilt.as_dict(),
            "model": "pinhole, no distortion",
        },
        "attack_angle": attack_angle.as_dict(),
        "club_path": club_path.as_dict(),
        "contact": contact.as_dict(),
        "fps": shot.fps,
        "exposure_us": shot.exposure_us,
        "ball_range_mm": round(shot.ball_range_mm, 1),
        "ball_range_sigma_mm": round(shot.ball_range_sigma_mm, 1),
        "ball_range_source": shot.ball_range_source,
        "club_speed_mps": shot.club_speed_mps,
        "club_speed_source": shot.club_speed_source,
        "contact_fraction": shot.contact_fraction,
        "contact_fraction_source": shot.contact_fraction_source,
        "gear_effect": dict(GEAR_NOT_APPLIED),
    }
    common: dict[str, Any] = {
        "handedness": shot.handedness,
        "contact_frame": contact.contact_frame,
        "last_rest_frame": contact.last_rest_frame,
        "diagnostics": diagnostics,
    }
    if contact.status != "ok" or contact.last_rest_frame is None:
        return ImpactPlacement("rejected", contact.reason, **common)
    for name in ("face_angle", "dynamic_loft", "lie"):
        if not _labelled_ok(getattr(orientation, name)):
            return ImpactPlacement("rejected", f"orientation_{name}_not_finite", **common)
    model = model or ClubModel.for_club(shot.club)
    if model is None:
        return ImpactPlacement(
            "rejected", f"no_outline_model_for_category_{il.club_category(shot.club)}", **common
        )
    model = model.with_centre(centre_from_toe, centre_height)
    diagnostics["club_model"] = model.summary()
    frames = (contact.last_rest_frame, contact.last_rest_frame + 1)
    if frames[1] >= shot.frames.shape[0]:
        return ImpactPlacement("rejected", "no_frame_after_contact", **common)
    span_s = (frames[1] - frames[0]) / shot.fps
    if span_s > il.MAX_BRACKET_S + 1e-9:
        return ImpactPlacement("rejected", f"bracket_spans_{span_s * 1000:.1f}_ms", **common)
    diagnostics["bracket_frames"] = list(frames)
    try:
        placer = _Placer(shot, model)
    except _Refused as refused:
        return ImpactPlacement("rejected", refused.reason, **common)
    inputs = _Inputs(
        face=orientation.face_angle.value,
        loft=orientation.dynamic_loft.value,
        lie=orientation.lie.value,
        pitch=tilt.pitch.value,
        roll=tilt.roll.value,
        range_mm=shot.ball_range_mm,
        speed=shot.club_speed_mps,
        attack=attack_angle.value,
        path=club_path.value,
        sole_width=model.sole_width.value,
        bounce=model.effective_bounce.value,
        fraction=shot.contact_fraction,
    )
    try:
        solution = placer.solve(inputs, None, frames, coarse=True)
    except _Refused as refused:
        return ImpactPlacement("rejected", refused.reason, **common)
    strike, fits = solution.strike, solution.fits
    starts = {f: fits[f].start() for f in frames}
    result_frames = dict(fits)
    _mark_contact(placer, inputs, result_frames, strike)
    diagnostics["frames"] = {
        str(f): {
            "toe_mm": round(fits[f].toe_mm, 2),
            "up_mm": round(fits[f].up_mm, 2),
            "normal_mm": round(fits[f].normal_mm, 2),
            "sigma_mm": [round(math.sqrt(fits[f].covariance_mm2[i, i]), 2) for i in range(2)],
            "edges": fits[f].edges,
            "time_ms": round(placer.frame(f).time_ms, 3),
            "ball_spot": placer.frame(f).masked,
        }
        for f in frames
    }
    axes = head_axes(*orientation.values())
    weight_before, weight_after = placer.weights(inputs, frames)
    diagnostics.update(
        {
            "normal_speed_mm_per_ms": round(solution.normal_speed, 3),
            "normal_speed_source": solution.normal_speed_source,
            "fitted_in_plane_velocity_mm_per_ms": [
                round(float(v), 2) for v in solution.in_plane_velocity
            ],
            "interpolation_weights": [round(weight_before, 4), round(weight_after, 4)],
            "contact_time_ms": round(placer.contact_ms(inputs.fraction), 3),
        }
    )
    _scene_checks(placer, inputs, scene, axes, strike, diagnostics, model)

    offset_problems = [
        f"toe_edge_not_found_on_frame_{f}_{fits[f].edges['toe']['accepted']}_samples"
        for f in frames
        if fits[f].edges["toe"]["accepted"] < MIN_EDGE_SAMPLES["toe"]
    ]
    height_problems = [
        f"sole_edge_not_found_on_frame_{f}_{fits[f].edges['sole']['accepted']}_samples"
        for f in frames
        if fits[f].edges["sole"]["accepted"] < MIN_EDGE_SAMPLES["sole"]
    ]
    if offset_problems:
        return ImpactPlacement("rejected", offset_problems[0], frames=result_frames, **common)
    from_toe = strike[0] - model.reference_from_toe
    above_edge = strike[1] + model.reference_height
    reading = (from_toe + model.centre_from_toe.value, above_edge - model.centre_height.value)
    diagnostics["contact_from_toe_mm"] = round(from_toe, 2)
    diagnostics["contact_above_leading_edge_mm"] = round(above_edge, 2)
    physical, why = il.reading_is_physical(from_toe)
    if not physical:
        return ImpactPlacement("rejected", why, frames=result_frames, **common)

    def refit(**changes: float) -> tuple[float, float]:
        changed = replace(inputs, **changes)
        try:
            return placer.solve(changed, starts, frames, coarse=False).strike
        except _Refused:
            return strike

    def term(name: str, sigma: float, value: float) -> tuple[float, float]:
        if sigma <= 0.0:
            return (0.0, 0.0)
        high = refit(**{name: value + sigma})
        low = refit(**{name: value - sigma})
        return (abs(high[0] - low[0]) / 2.0, abs(high[1] - low[1]) / 2.0)

    fraction_sigma = (
        il.UNIFORM_FRACTION_SIGMA
        if shot.contact_fraction_source == "uniform_midpoint"
        else il.GIVEN_FRACTION_SIGMA
    )
    speed_sigma = (
        il.ASSUMED_CLUB_SPEED_RELATIVE_SIGMA
        if shot.club_speed_source == "assumed_default"
        else il.MEASURED_CLUB_SPEED_RELATIVE_SIGMA
    )
    low_f = max(0.0, inputs.fraction - fraction_sigma)
    high_f = min(1.0, inputs.fraction + fraction_sigma)
    timing_high, timing_low = refit(fraction=high_f), refit(fraction=low_f)
    timing_scale = max((high_f - low_f) / fraction_sigma, 1e-9)
    terms = {
        "contact_timing_mm": (
            abs(timing_high[0] - timing_low[0]) / timing_scale,
            abs(timing_high[1] - timing_low[1]) / timing_scale,
        ),
        "face_angle_mm": term("face", orientation.face_angle.sigma, inputs.face),
        "dynamic_loft_mm": term("loft", orientation.dynamic_loft.sigma, inputs.loft),
        "lie_mm": term("lie", orientation.lie.sigma, inputs.lie),
        "range_scale_mm": term("range_mm", shot.ball_range_sigma_mm, inputs.range_mm),
        "club_speed_mm": term("speed", speed_sigma * inputs.speed, inputs.speed),
        "camera_pitch_mm": term("pitch", tilt.pitch.sigma, inputs.pitch),
        "camera_roll_mm": term("roll", tilt.roll.sigma, inputs.roll),
    }
    width = term("sole_width", model.sole_width.sigma, inputs.sole_width)
    bounce = term("bounce", model.effective_bounce.sigma, inputs.bounce)
    terms["sole_geometry_mm"] = (math.hypot(width[0], bounce[0]), math.hypot(width[1], bounce[1]))
    before, after = fits[frames[0]], fits[frames[1]]
    fit_sigma = [
        math.sqrt(
            weight_before**2 * before.covariance_mm2[i, i]
            + weight_after**2 * after.covariance_mm2[i, i]
        )
        for i in range(2)
    ]
    chord = il.bracket_chord_terms_mm(shot, frames[0], frames[1])
    ball_centre = il.BALL_CENTRE_SIGMA_PX * shot.plate_mm_per_px
    offset_budget = {"placement_fit_mm": fit_sigma[0], "ball_centre_mm": ball_centre}
    offset_budget.update({name: value[0] for name, value in terms.items()})
    offset_budget.update(
        {
            "face_centre_mm": model.centre_from_toe.sigma,
            "arc_chord_mm": chord["arc_chord_mm"],
            "face_rotation_mm": chord["face_rotation_mm"],
        }
    )
    height_budget = {"placement_fit_mm": fit_sigma[1], "ball_centre_mm": ball_centre}
    height_budget.update({name: value[1] for name, value in terms.items()})
    height_budget["face_centre_mm"] = model.centre_height.sigma
    if contact.warning is not None:
        offset_budget["contact_frame_ambiguity_mm"] = abs(after.toe_mm - before.toe_mm) / 2.0
        height_budget["contact_frame_ambiguity_mm"] = abs(after.up_mm - before.up_mm) / 2.0
    diagnostics["offset_uncertainty_budget_mm"] = {k: round(v, 3) for k, v in offset_budget.items()}
    diagnostics["face_rotation_rate_deg_s"] = {
        "value": il.FACE_CLOSURE_RATE_DEG_S,
        "source": il.FACE_CLOSURE_RATE_SOURCE,
    }
    diagnostics["not_in_uncertainty"] = [
        "lens distortion (pinhole assumed) and the principal point",
        "the club's own outline against the population average beyond the face-centre spread",
        "topline thickness and the back of the head (thin face plate plus sole strip)",
        "the head's arc within the bracket for the height axis",
    ]
    offset_sigma = math.sqrt(sum(v * v for v in offset_budget.values()))
    height: dict[str, Any] = {"height_status": "rejected"}
    if height_problems:
        height["height_reason"] = height_problems[0]
    else:
        ok, why = il.height_is_physical(above_edge, reading[1])
        if not ok:
            height["height_reason"] = why
        else:
            diagnostics["height_uncertainty_budget_mm"] = {
                k: round(v, 3) for k, v in height_budget.items()
            }
            height.update(
                height_status="ok",
                height_reason="ok",
                impact_height_mm=reading[1],
                impact_height_sigma_mm=math.sqrt(sum(v * v for v in height_budget.values())),
            )
    return ImpactPlacement(
        "ok",
        "ok",
        impact_offset_mm=reading[0],
        impact_offset_sigma_mm=offset_sigma,
        frames=result_frames,
        **height,
        **common,
    )


def _mark_contact(
    placer: _Placer, inputs: _Inputs, fits: dict[int, FramePlacement], strike
) -> None:
    """The contact point and the face centre at contact, in pixels, for the overlay."""
    axes = head_axes(inputs.face, inputs.loft, inputs.lie)
    rotation = placer.rotation(inputs)
    pose = contact_pose(placer.ball_level(inputs), axes, strike)
    point = placer.project(pose.contact_point[None, :], rotation)[0]
    centre = placer.project(pose.face_centre[None, :], rotation)[0]
    for fit in fits.values():
        fit.contact_point_px = (float(point[0]), float(point[1]))
        fit.face_centre_px = (float(centre[0]), float(centre[1]))


def _scene_checks(placer, inputs, scene, axes, strike, diagnostics, model) -> None:
    """Heights above the hitting surface the placed head and the ball imply (diagnostics only)."""
    ball = placer.ball_level(inputs)
    lens = scene.lens_height.value
    diagnostics["ball_centre_above_surface_mm"] = round(lens - float(ball[1]), 2)
    diagnostics["ball_centre_above_surface_scene_mm"] = round(scene.ball_height.value, 2)
    pose = contact_pose(ball, axes, strike)
    lead, trail = placer.head_points(inputs, axes, pose.face_centre, model.outline_uv, sole=True)
    lowest = float(max(lead[:, 1].max(), trail[:, 1].max() if trail is not None else -math.inf))
    diagnostics["head_lowest_point_above_surface_mm"] = round(lens - lowest, 2)
    # Whatever the ball rests on, the head can only skim or dig under it: a self-check that
    # needs no scene height at all.
    diagnostics["head_lowest_point_above_ball_bottom_mm"] = round(
        float(ball[1]) + il.BALL_RADIUS_MM - lowest, 2
    )
    above = lens - float(ball[1])
    if abs(above - scene.ball_height.value) > 3.0 * math.hypot(
        scene.ball_height.sigma, scene.lens_height.sigma
    ):
        diagnostics.setdefault("warnings", []).append(
            f"ball_centre_{above:.0f}_mm_above_the_surface_not_"
            f"{scene.ball_height.value:.0f}_mm_lens_height_or_camera_pitch_is_off"
        )


# --- S4 hook -------------------------------------------------------------------------


def refine_with_orientation_update(
    shot: il.PreparedShot,
    orientation: Orientation,
    update: Callable[[ImpactPlacement, Orientation], Orientation],
    *,
    scene: SceneHeights,
    tilt: CameraTilt | None = None,
    model: ClubModel | None = None,
    attack_angle: il.Labelled | None = None,
    club_path: il.Labelled | None = None,
    tolerance_mm: float = GEAR_LOOP_TOLERANCE_MM,
    max_iterations: int = GEAR_LOOP_MAX_ITERATIONS,
) -> ImpactPlacement:
    """The S4 hook: place, let ``update`` correct the orientation for the strike, re-place.

    Stops when the strike moves less than ``tolerance_mm`` or after ``max_iterations``
    updates (flagged ``not_converged``). The gear-effect correction itself is the caller's.
    """
    options = {
        "scene": scene,
        "tilt": tilt,
        "model": model,
        "attack_angle": attack_angle,
        "club_path": club_path,
    }
    placement = place_impact(shot, orientation, **options)
    moved: list[float] = []
    status = "not_converged"
    current = orientation
    iterations = 0
    while placement.status == "ok" and iterations < max_iterations:
        current = update(placement, current)
        iterations += 1
        again = place_impact(shot, current, **options)
        if again.status != "ok":
            placement, status = again, f"stopped_{again.reason}"
            break
        step = abs(again.impact_offset_mm - placement.impact_offset_mm)
        if again.height_status == "ok" and placement.height_status == "ok":
            step = math.hypot(step, again.impact_height_mm - placement.impact_height_mm)
        moved.append(round(step, 3))
        placement = again
        if step < tolerance_mm:
            status = "converged"
            break
    placement.diagnostics["gear_effect"] = {
        "status": status if iterations else f"stopped_{placement.reason}",
        "iterations": iterations,
        "moved_mm": moved,
        "tolerance_mm": tolerance_mm,
    }
    return placement


# --- overlay -----------------------------------------------------------------------


OVERLAY_SCALE = 3
PART_COLOURS = {"toe": (0, 220, 0), "sole": (255, 200, 0), "topline": (0, 220, 255)}


def render_overlay(  # pylint: disable=too-many-branches
    shot: il.PreparedShot, placement: ImpactPlacement, *, text: str | None = None
) -> np.ndarray:
    """Each bracket frame with the club model drawn at its fitted pose, as the SAVED frame shows.

    Orange: the placed outline; blue: the sole's trailing edge. Edge samples: green toe,
    cyan sole, yellow topline (reported, not fitted); hollow where refused. Yellow circle:
    the resting ball. Magenta: the contact point P = B - r n_hat; magenta ring: the face
    centre at contact. Each frame twice: the picture and the background difference the
    fit read (x4; dark red where it read nothing: the ball's spot, its climb, the flying
    ball).
    """
    plate = shot.plate_mm_per_px
    x0 = max(0, int(shot.ball.x - il.OVERLAY_HALF_WIDTH_MM / plate))
    x1 = min(shot.frames.shape[2], int(shot.ball.x + il.OVERLAY_HALF_WIDTH_MM / plate) + 1)
    y0 = max(0, int(shot.ball.y - il.OVERLAY_ABOVE_MM / plate))
    y1 = min(shot.frames.shape[1], int(shot.ball.y + il.OVERLAY_BELOW_MM / plate) + 1)
    scale = OVERLAY_SCALE
    frames = sorted(placement.frames) or [
        f
        for f in (placement.last_rest_frame, placement.contact_frame)
        if f is not None and f < shot.frames.shape[0]
    ]
    background = None
    if shot.window:
        last_rest = shot.contact.last_rest_frame or 0
        stop = min(shot.window[0], last_rest + 1 - int(math.ceil(il.BACKGROUND_LEAD_S * shot.fps)))
        if stop >= 1:
            background = np.median(shot.frames[:stop].astype(np.float32), axis=0)
    tiles = []
    for frame in frames:
        picture = shot.frames[frame]
        fitted = placement.frames.get(frame)
        seen = fitted.diff if fitted is not None and fitted.diff is not None else None
        if seen is None and background is not None:
            seen = np.abs(picture.astype(np.float32) - background)
        views = [cv2.cvtColor(picture, cv2.COLOR_GRAY2BGR)]
        if seen is not None:
            shown = cv2.cvtColor(
                np.clip(4.0 * np.nan_to_num(seen, nan=0.0), 0, 255).astype(np.uint8),
                cv2.COLOR_GRAY2BGR,
            )
            shown[~np.isfinite(seen)] = (0, 0, 90)
            views.append(shown)
        for number, view in enumerate(views):
            tile = cv2.resize(view, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
            centre = (
                int(round((shot.ball.x + 0.5) * scale)),
                int(round((shot.ball.y + 0.5) * scale)),
            )
            cv2.circle(
                tile, centre, int(round(shot.ball.diameter_px * scale / 2)), (0, 220, 220), 1
            )
            fit = placement.frames.get(frame)
            if fit is not None:

                def px(points: np.ndarray) -> np.ndarray:
                    return np.round((np.asarray(points) + 0.5) * scale * 16).astype(np.int32)

                cv2.polylines(
                    tile, [px(fit.outline_px)], True, (0, 140, 255), 1, cv2.LINE_AA, shift=4
                )
                if len(fit.sole_px) > 1:
                    cv2.polylines(
                        tile, [px(fit.sole_px)], False, (255, 120, 0), 1, cv2.LINE_AA, shift=4
                    )
                for part, (points, accepted) in fit.samples_px.items():
                    colour = PART_COLOURS.get(part, (200, 200, 200))
                    for point, ok in zip(px(points), accepted):
                        cv2.circle(
                            tile,
                            (int(point[0]), int(point[1])),
                            2 * 16,
                            colour,
                            -1 if ok else 1,
                            cv2.LINE_AA,
                            shift=4,
                        )
                if fit.contact_point_px is not None:
                    p = px(np.array(fit.contact_point_px))
                    cv2.drawMarker(
                        tile,
                        (int(p[0]) // 16, int(p[1]) // 16),
                        (255, 0, 255),
                        cv2.MARKER_CROSS,
                        10,
                        1,
                    )
                if fit.face_centre_px is not None:
                    c = px(np.array(fit.face_centre_px))
                    cv2.circle(
                        tile, (int(c[0]), int(c[1])), 4 * 16, (255, 0, 255), 1, cv2.LINE_AA, shift=4
                    )
            tile = tile[y0 * scale : y1 * scale, x0 * scale : x1 * scale]
            if shot.flipped:
                tile = tile[:, ::-1]
            tile = np.ascontiguousarray(tile)
            label = f"f{frame}"
            if frame == shot.contact.last_rest_frame:
                label += " last rest"
            elif frame == shot.contact.contact_frame:
                label += " ball moved"
            if number:
                label += " |diff| x4, red unread"
            cv2.putText(
                tile,
                label,
                (4, 14),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
            tiles.append(tile)
    if not tiles:
        tiles = [np.zeros((200, 200, 3), np.uint8)]
    height = max(t.shape[0] for t in tiles)
    tiles = [np.pad(t, ((0, height - t.shape[0]), (0, 2), (0, 0))) for t in tiles]
    board = np.concatenate(tiles, axis=1)
    header = np.zeros((22, board.shape[1], 3), np.uint8)
    cv2.putText(
        header,
        text or caption(placement),
        (4, 15),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return np.concatenate([header, board], axis=0)


def caption(placement: ImpactPlacement) -> str:
    """One line: the reading or the refusal."""
    if placement.status != "ok":
        return f"REJECTED: {placement.reason}"
    height = (
        f"height {placement.impact_height_mm:+.1f} +/- {placement.impact_height_sigma_mm:.1f} mm"
        if placement.height_status == "ok"
        else f"height rejected: {placement.height_reason}"
    )
    return (
        f"EXPERIMENTAL placement: offset {placement.impact_offset_mm:+.1f} +/- "
        f"{placement.impact_offset_sigma_mm:.1f} mm (+ toe, {placement.handedness}-handed), "
        f"{height}"
    )


# --- the session route ---------------------------------------------------------------


def place_clip(
    clip: session.Clip,
    shot: Mapping[str, Any],
    start: Mapping[str, Any],
    *,
    centre_from_toe: il.Labelled | None = None,
) -> tuple[ImpactPlacement, Orientation, SceneHeights]:
    """One prepared clip placed and read: the session route's and the live producer's call.

    ``clip`` as ``impact_session.prepare_clip`` left it (prepared, with its face inputs);
    ``shot`` its shot record (its ``collision_model`` block gives the D-plane angles);
    ``start`` the session start record (the aim). The orientation is
    ``orientation_for_shot``'s, the scene the face inputs' lens height, the attack and
    face centre the face inputs'; ``centre_from_toe`` overrides the face centre for research.
    """
    face = clip.face
    scene = SceneHeights.from_face_inputs(face)
    orientation = orientation_for_shot(
        face,
        shot.get("collision_model") if isinstance(shot, Mapping) else None,
        club=clip.club,
        handedness=clip.handedness,
        target_line_offset_deg=session.target_line_offset_deg(start),
    )
    placement = place_impact(
        clip.prepared,
        orientation,
        scene=scene,
        attack_angle=face.attack_angle,
        centre_from_toe=centre_from_toe or face.face_centre_from_toe,
        centre_height=face.face_centre_height,
    )
    placement.diagnostics["orientation_provenance"] = orientation.provenance()
    placement.diagnostics["dynamic_lie_estimate_not_used"] = clip.extra.get("dynamic_lie")
    return placement, orientation, scene


def orientation_origin(orientation: Orientation) -> str:
    """Each angle's origin in one line, e.g. ``face_angle=d_plane dynamic_loft=prior lie=prior``."""
    return " ".join(f"{n}={p['origin']}" for n, p in orientation.provenance().items())


CSV_FIELDS = (
    "shot_id",
    "run",
    "shot_number",
    "club",
    "handedness",
    "frame_size",
    "fps",
    "label",
    "status",
    "reason",
    "impact_offset_mm",
    "impact_offset_sigma_mm",
    "height_status",
    "height_reason",
    "impact_height_mm",
    "impact_height_sigma_mm",
    "contact_frame",
    "last_rest_frame",
    "face_angle_deg",
    "face_angle_source",
    "dynamic_loft_deg",
    "dynamic_loft_source",
    "lie_deg",
    "lie_source",
    "orientation_origin",
    "lens_height_mm",
    "lens_height_source",
    "ball_range_mm",
    "ball_range_source",
    "club_speed_source",
    "face_photo",
    "relative_path",
)


def analyse_session(
    root: Path, options: session.Options | None = None, *, overlays: Path | None = None
) -> dict[str, Any]:
    """Every clip of a recorded session placed and read; the report as a JSON-ready dict.

    Clips are prepared exactly as ``impact_session`` prepares them (range, ball, lens)
    and placed by ``place_clip`` (the orientation rule ``orientation_for_shot``: the
    shot's capture-bound angles, else its D-plane, else the priors); the scene's heights
    come from the session's start record. No self-built template is made or used.
    """
    options = options or session.Options()
    clips = session.find_clips(root)
    if options.shots:
        clips = [c for c in clips if any(s in c.relative_path for s in options.shots)]
    runs = {c.run_dir: session.read_run(c.run_dir) for c in clips if c.run_dir is not None}
    ladder = session._ladder_balls(root)  # pylint: disable=protected-access
    rows, details = [], []
    for clip in clips:
        session.prepare_clip(clip, runs, ladder, options)
        row: dict[str, Any] = {
            "shot_id": clip.shot_id,
            "run": clip.run_dir.name if clip.run_dir is not None else None,
            "shot_number": clip.shot_number,
            "club": clip.club,
            "handedness": clip.handedness,
            "frame_size": clip.frame_size,
            "fps": clip.fps,
            "label": LABEL,
            "ball_range_mm": clip.range_mm,
            "ball_range_source": clip.range_source,
            "club_speed_source": clip.extra.get("club_speed_source"),
            "face_photo": clip.face_photo,
            "relative_path": clip.relative_path,
        }
        if clip.prepared is None or clip.face is None:
            reason = clip.error or "not_analysed"
            row.update(
                status="rejected", reason=reason, height_status="rejected", height_reason=reason
            )
            rows.append(row)
            continue
        run = runs.get(clip.run_dir) if clip.run_dir is not None else None
        placement, orientation, scene = place_clip(
            clip,
            run.shots.get(clip.shot_number, {}) if run is not None else {},
            run.start if run is not None else {},
            centre_from_toe=(
                il.Labelled(float(options.face_centre_from_toe_mm), 0.0, il.RESEARCH_OVERRIDE)
                if options.face_centre_from_toe_mm is not None
                else None
            ),
        )
        row.update(
            status=placement.status,
            reason=placement.reason,
            impact_offset_mm=placement.impact_offset_mm,
            impact_offset_sigma_mm=placement.impact_offset_sigma_mm,
            height_status=placement.height_status,
            height_reason=placement.height_reason if placement.status == "ok" else placement.reason,
            impact_height_mm=placement.impact_height_mm,
            impact_height_sigma_mm=placement.impact_height_sigma_mm,
            contact_frame=placement.contact_frame,
            last_rest_frame=placement.last_rest_frame,
            face_angle_deg=orientation.face_angle.value,
            face_angle_source=orientation.face_angle.source,
            dynamic_loft_deg=orientation.dynamic_loft.value,
            dynamic_loft_source=orientation.dynamic_loft.source,
            lie_deg=orientation.lie.value,
            lie_source=orientation.lie.source,
            orientation_origin=orientation_origin(orientation),
            lens_height_mm=scene.lens_height.value,
            lens_height_source=scene.lens_height.source,
        )
        rows.append(row)
        details.append({"shot_id": clip.shot_id, **placement.as_dict()})
        if overlays is not None:
            overlays.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(
                str(overlays / f"placement_{clip.shot_id}.png"),
                render_overlay(clip.prepared, placement),
            )
        clip.prepared = replace(clip.prepared, frames=clip.prepared.frames[:0])
    return {
        "schema": SCHEMA,
        "label": LABEL,
        "caveat": (
            "EXPERIMENTAL, UNQUALIFIED post-session analysis; not shown on the kiosk. The club "
            "is the category's population-average outline placed at the D-plane orientation "
            "(or its labelled priors); orientation inputs are conditional, never measured. "
            f"Face centre: {il.FACE_CENTRE_CONVENTION}."
        ),
        "session": str(root),
        "rows": rows,
        "details": details,
    }


def write_report(report: Mapping[str, Any], out: Path) -> tuple[Path, Path]:
    """``impact_placement.csv`` (one row per clip) and ``impact_placement.json``."""
    out.mkdir(parents=True, exist_ok=True)
    csv_path, json_path = out / "impact_placement.csv", out / "impact_placement.json"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in report["rows"]:
            writer.writerow(
                {
                    k: (round(v, 3) if isinstance(v, float) and math.isfinite(v) else v)
                    for k, v in ((key, row.get(key)) for key in CSV_FIELDS)
                }
            )
    json_path.write_text(
        json.dumps(report, indent=2, default=_json_default) + "\n", encoding="utf-8"
    )
    return csv_path, json_path


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return str(value)
