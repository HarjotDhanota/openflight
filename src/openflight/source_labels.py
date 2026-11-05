"""The label a value carries, and how a Shot's source text names it.

The owner's policy (25 Oct 2026; the weakest input always wins):

- measured: a sensor observed it on this shot, its own gates passed, and every geometry
  input it needs is measured or calibrated, not nominal. Never compared against TrackMan
  is fine (Q1): validation is tracked apart and does not change the label.
- calculated: a published or physics model on inputs that are all measured or
  calculated. The model's own assumptions (a centre-face strike, the air) do not lower
  it (Q2); the record's note says them.
- model_geometry: rests on an assumed swing geometry (D39).
- estimated: at least one input is nominal or assumed (an uncalibrated lens, a
  configured tilt, the camera prior range...).
- experimental: the method fails or skips its own gate, or rests on an experimental
  input (Q3). It feeds on, what it feeds is experimental, and no simulator gets it.

A model's source text starts with its label's word."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional

LABEL_MEASURED = "measured"
LABEL_CALCULATED = "calculated"
LABEL_MODEL_GEOMETRY = "model_geometry"
# a measured input too weak to stand as one: every output built on it is "estimated"
LABEL_ESTIMATED = "estimated"
LABEL_EXPERIMENTAL = "experimental"

MODEL_LABELS = (LABEL_CALCULATED, LABEL_MODEL_GEOMETRY, LABEL_ESTIMATED)

# Shot.spin_axis_source prefixes for an axis built on a model-geometry or estimated input.
SPIN_AXIS_MODEL_GEOMETRY_PREFIX = "model geometry"
SPIN_AXIS_ESTIMATED_PREFIX = "estimated"
SPIN_AXIS_UNRECORDED_NOTE = "spin axis of unrecorded origin (older log)"

# What every collision-model output assumes while no impact location is measured (Q2: the
# assumption is in the model, so the value stays calculated and the note says it).
CENTRE_STRIKE_NOTE = "assumes a centre-face strike"

# Horizontal-launch sources that fail their own gate (P8-7), each with the gate: shown,
# labelled "experimental" (Q3, 25 Oct 2026) with every output built on them.
WEAK_HORIZONTAL_SOURCES = {
    "radar_low_coherence": "low-coherence",
    "radar_model_unqualified": "horizontal model or status not qualified",
    "camera_low_consensus": "low-consensus",
}

# Shot.launch_angle_horizontal_status of an IWR horizontal while the board has no
# horizontal phase reference: its start direction has its own zero, so the value is
# "estimated", like a weak source, until the azimuth is calibrated.
AZIMUTH_UNCALIBRATED = "azimuth_uncalibrated"
AZIMUTH_UNCALIBRATED_NOTE = "azimuth uncalibrated: the IWR start direction has its own zero"

# The vertical launch's status while the IWR's tilt is the configured one: no LIS3DH
# reading was applied to the IWR on this shot (fix 13, 25 Oct 2026).
TILT_CONFIGURED = "tilt_configured"
TILT_CONFIGURED_NOTE = "tilt is the configured value, not measured"

# The lens's intrinsics state (camera/lens_profiles.ActiveLens.intrinsics), stamped on each
# shot with a camera clip (Shot.camera_lens_intrinsics). Any other value (None on an older
# log) counts as nominal.
INTRINSICS_CALIBRATED = "calibrated"
INTRINSICS_NOMINAL = "nominal"
NOMINAL_LENS_NOTE = "nominal lens intrinsics: the listed focal length, not calibrated"
# The camera club tier that can stand as measured (camera/club_delivery confidence tiers:
# high, medium, low, experimental, withheld), and the camera + OPS fused source, which has
# no IWR depth under the club and stays experimental whatever its tier.
CAMERA_HIGH_TIER = "high"
CAMERA_FUSED_OPS_SOURCE = "camera_fused_ops"
# The camera-assisted horizontal (camera/ball_flight.select_camera_assisted_horizontal):
# its high tier is the status "camera_assisted_high"; every other status is weaker.
CAMERA_ASSISTED_SOURCE = "camera_assisted_experimental"
CAMERA_ASSISTED_HIGH_STATUS = "camera_assisted_high"

# Launch sources read by a camera with no validated geometry (an inferred pose, the
# lens's listed focal length): shown, labelled "experimental". Two follow the lens
# (``launch_label``): the camera-assisted horizontal of the high tier (measured on a
# calibrated lens, estimated on a nominal one, Q5) and the camera fallback (estimated on
# a calibrated lens). camera_fallback is the camera's own launch on a shot the IWR did
# not measure.
CAMERA_FALLBACK_SOURCE = "camera_fallback"
EXPERIMENTAL_LAUNCH_SOURCES = {
    CAMERA_FALLBACK_SOURCE: "camera fallback launch (the IWR did not measure this shot)",
    "camera_assisted_experimental": "camera horizontal on the IWR's depth",
    "camera_only_experimental": "camera-only horizontal",
    "camera_legacy_fallback": "camera horizontal after the calibrated model was refused",
}
EXPERIMENTAL_LAUNCH_NOTE = "experimental: the camera's geometry is not validated against TrackMan"

# The camera head track's path and attack angle (camera/head_track.SOURCE): they
# feed the collision model as "estimated" with their reason (owner decision 5, 25 Oct
# 2026), and every output built on them is experimental.
CAMERA_HEAD_TRACK_SOURCE = "camera_head_track"
# Collision-model input sources whose outputs are experimental, whatever label the
# model gave them: never sent to a simulator. A record that carries its own
# "experimental" flag (collision_model.Input.experimental) is read by the flag instead;
# this list reads older logs.
EXPERIMENTAL_INPUT_SOURCES = frozenset(
    {CAMERA_HEAD_TRACK_SOURCE, "iwr_candidate", *EXPERIMENTAL_LAUNCH_SOURCES}
)

# An IWR estimate from a partial dump (iwr6843/partial_dump.PARTIAL_LABEL): a chain label
# between estimated and experimental (source_chain.LABEL_ORDER). Its records read
# "estimated", with the partial dump named in the note. Why estimated, not experimental:
# the decoder never fills a sample in and keeps only frames whose every sample it placed
# and whose loops agree, so the estimators run on real, bit-exact samples (32 field-like
# loss patterns from the 24 Oct shortfalls never placed a sample wrongly, and a late run in
# shot 14 left its range within 0.05 m of the whole dump's 1.908 m:
# tests/test_iwr6843_partial_decode_field.py). The method passed its gates; it is the
# frames that are fewer, and that has been checked on one shot only: an input weaker than
# measured, not one that failed a gate.
LABEL_IWR_PARTIAL = "iwr_partial"
IWR_PARTIAL_RECORD_LABEL = LABEL_ESTIMATED
IWR_PARTIAL_NOTE = (
    "from a partial IWR dump: the intact frames of a transfer that lost bytes (estimated)"
)


# The IWR LCMF status of a launch on a range walk the tracker could not separate from a
# different walk (iwr6843/runtime.TRACK_AMBIGUOUS_STATUS), stamped on the shot as
# Shot.iwr6843_launch_status: its launch angles are estimated.
TRACK_AMBIGUOUS_STATUS = "accepted_track_ambiguous"
TRACK_AMBIGUOUS_NOTE = (
    f"the IWR could not separate the ball's range walk from another ({TRACK_AMBIGUOUS_STATUS})"
)
# The note an IWR launch point carries when two IWR tracks compete for it (the reason
# iwr6843.launch_range.competition_note writes starts with the same words).
IWR_COMPETING_NOTE = "two IWR tracks compete for the launch point (estimated)"


def record_label(label: Optional[str]) -> Optional[str]:
    """The metric-record label for a chain label: iwr_partial reads "estimated"."""
    return IWR_PARTIAL_RECORD_LABEL if label == LABEL_IWR_PARTIAL else label


def experimental_collision_inputs(collision_record) -> list:
    """The present collision-model inputs (``CollisionResult.to_dict()``) that are
    experimental, by name: flagged so, or (an older log) from an experimental source;
    empty when none is or the model did not run."""
    inputs = (collision_record or {}).get("inputs") or {}
    return [
        name
        for name, record in inputs.items()
        if isinstance(record, dict)
        and record.get("value") is not None
        and (
            bool(record["experimental"])
            if "experimental" in record
            else record.get("source") in EXPERIMENTAL_INPUT_SOURCES
        )
    ]


def lens_calibrated(intrinsics: Optional[str]) -> bool:
    """Whether a lens's intrinsics are a checkerboard calibration's (not the listing's)."""
    return intrinsics == INTRINSICS_CALIBRATED


# G2 C4 (4 Nov 2026): the club stage's range evidence when no IWR club walk was accepted:
# the located tee + the OPS club speed (camera.ball_flight.LocatedTeeRangeEvidence.status).
# The head's depth and forward speed are then assumed, not measured, so a camera club value
# on it is at best estimated, with this reason.
ASSUMED_DEPTH_STATUSES = frozenset({"accepted_located_tee"})
ASSUMED_DEPTH_NOTE = "depth from tee range + OPS club speed (not measured)"


def depth_assumed(depth_status: Optional[str]) -> bool:
    """Whether the club stage's range evidence status is an assumed depth."""
    return depth_status in ASSUMED_DEPTH_STATUSES


def camera_club_label(
    tier: Optional[str],
    intrinsics: Optional[str],
    source: Optional[str] = None,
    *,
    depth_status: Optional[str] = None,
) -> str:
    """The label of a camera club value (fused path or attack) or a camera-assisted
    horizontal: measured on the high tier through a calibrated lens over a measured depth,
    estimated on the high tier through a nominal lens or over an assumed depth
    (``ASSUMED_DEPTH_STATUSES``), else experimental (a weaker tier, or camera + OPS)."""
    if source == CAMERA_FUSED_OPS_SOURCE or tier != CAMERA_HIGH_TIER:
        return LABEL_EXPERIMENTAL
    if depth_assumed(depth_status):
        return LABEL_ESTIMATED
    return LABEL_MEASURED if lens_calibrated(intrinsics) else LABEL_ESTIMATED


def camera_club_note(
    tier: Optional[str],
    intrinsics: Optional[str],
    source: Optional[str] = None,
    *,
    depth_status: Optional[str] = None,
) -> Optional[str]:
    """Why a camera club value is weaker than measured (``camera_club_label``), or None."""
    if source == CAMERA_FUSED_OPS_SOURCE:
        return "camera + OPS delivery: no IWR depth under the club (experimental)"
    if tier != CAMERA_HIGH_TIER:
        note = f"camera confidence tier {tier or 'unknown'}, not high (experimental)"
    else:
        note = None if lens_calibrated(intrinsics) else NOMINAL_LENS_NOTE
    if depth_assumed(depth_status):
        note = f"{ASSUMED_DEPTH_NOTE}; {note}" if note else ASSUMED_DEPTH_NOTE
    return note


def launch_label(
    source: Optional[str],
    status: Optional[str] = None,
    *,
    camera_tier: Optional[str] = None,
    intrinsics: Optional[str] = None,
) -> str:
    """The label a Shot launch angle carries for its source and status.

    - a gate the value failed (a low-coherence IWR or low-consensus camera horizontal) or a
      camera source with no validated geometry: experimental;
    - the camera-assisted horizontal: ``camera_club_label`` on its tier and the lens (Q5);
    - the camera fallback: estimated through a calibrated lens, else experimental;
    - a table estimate, an uncalibrated IWR azimuth or a configured IWR tilt: estimated;
    - else measured.
    """
    if source == CAMERA_ASSISTED_SOURCE:
        return camera_club_label(camera_tier, intrinsics)
    if source == CAMERA_FALLBACK_SOURCE:
        return LABEL_ESTIMATED if lens_calibrated(intrinsics) else LABEL_EXPERIMENTAL
    if source in EXPERIMENTAL_LAUNCH_SOURCES or source in WEAK_HORIZONTAL_SOURCES:
        return LABEL_EXPERIMENTAL
    if source == LABEL_ESTIMATED or status in (AZIMUTH_UNCALIBRATED, TILT_CONFIGURED):
        return LABEL_ESTIMATED
    return LABEL_MEASURED


def launch_label_note(
    source: Optional[str],
    status: Optional[str] = None,
    *,
    camera_tier: Optional[str] = None,
    intrinsics: Optional[str] = None,
) -> Optional[str]:
    """Why a launch angle is weaker than measured (``launch_label``), or None."""
    label = launch_label(source, status, camera_tier=camera_tier, intrinsics=intrinsics)
    if label == LABEL_MEASURED:
        return None
    notes = []
    if source in WEAK_HORIZONTAL_SOURCES:
        notes.append(f"{WEAK_HORIZONTAL_SOURCES[source]} horizontal: it failed its own gate")
    if status == AZIMUTH_UNCALIBRATED:
        notes.append(AZIMUTH_UNCALIBRATED_NOTE)
    if status == TILT_CONFIGURED:
        notes.append(TILT_CONFIGURED_NOTE)
    if source == CAMERA_ASSISTED_SOURCE:
        notes.append(camera_club_note(camera_tier, intrinsics))
    elif source == CAMERA_FALLBACK_SOURCE and label == LABEL_EXPERIMENTAL:
        notes.append(NOMINAL_LENS_NOTE)
    return "; ".join(note for note in notes if note) or None


def _field(shot: Any, name: str) -> Any:
    if isinstance(shot, Mapping):
        return shot.get(name)
    return getattr(shot, name, None)


def vertical_launch_status(shot: Any) -> Optional[str]:
    """``TILT_CONFIGURED`` for an IWR vertical whose tilt no LIS3DH reading corrected."""
    source = _field(shot, "launch_angle_vertical_source") or _field(shot, "angle_source")
    if source != "radar" or _field(shot, "mode") == "mock":
        return None
    inclinometer = _field(shot, "inclinometer")
    applied = (inclinometer.get("applied_to") if isinstance(inclinometer, Mapping) else None) or ()
    return None if "iwr6843" in applied else TILT_CONFIGURED


def camera_horizontal_tier(shot: Any) -> Optional[str]:
    """The camera-assisted horizontal's tier as the shot recorded it (its status)."""
    status = _field(shot, "experimental_camera_horizontal_status")
    return CAMERA_HIGH_TIER if status == CAMERA_ASSISTED_HIGH_STATUS else None


_RECORD_ORDER = (
    LABEL_MEASURED,
    LABEL_CALCULATED,
    LABEL_MODEL_GEOMETRY,
    LABEL_ESTIMATED,
    LABEL_EXPERIMENTAL,
)


def _weaker(first: str, second: Optional[str]) -> str:
    if second not in _RECORD_ORDER:
        return first
    return max(first, second, key=_RECORD_ORDER.index)


# The ball range's source, as the range chain (camera/fallback_chain) leaves it on the
# shot, and what every value resting on a range weaker than an IWR launch point is
# labelled, with why.
CAMERA_PRIOR_NOTE = "rests on the camera prior's ball distance (coarse, estimated)"
BALL_SIZE_RANGE_NOTE = (
    "rests on the resting ball's own size for its distance (experimental, not validated "
    "against a tape)"
)
BALL_SIZE_RANGE_CALIBRATED_NOTE = (
    "rests on the resting ball's own size through a calibrated lens (estimated, not "
    "validated against a tape)"
)
# The camera's located tee (C8a): its own label (estimated through a calibrated model,
# measured when the IWR confirms it, experimental through a nominal one).
LOCATED_TEE_NOTE = (
    "rests on the tee the camera located (the setup box's resting ball through the camera "
    "model; the IWR's launch point only cross-checks it)"
)
RANGE_SOURCE_LABELS = {
    "camera_prior": (LABEL_ESTIMATED, CAMERA_PRIOR_NOTE),
    "camera_ball_size": (LABEL_EXPERIMENTAL, BALL_SIZE_RANGE_NOTE),
}


def range_source_label(shot: Any) -> tuple[Optional[str], Optional[str]]:
    """(record label, why) every value resting on the shot's ball range takes from it, or
    (None, None) for a measured one: the camera prior (estimated), the resting ball's size
    (experimental; estimated through a calibrated lens, Q6), an IWR launch point from a
    partial dump or with two tracks competing for it (estimated).

    The one source of truth for the range's label: the published metrics
    (``shot_metrics``) and the radar's launch angles (``shot_launch_label``, in the chain
    and the collision input) read it, since the LCMF uses the range as its tee range."""
    source, label = _field(shot, "ball_range_source"), _field(shot, "ball_range_label")
    if source == "iwr_post_impact" and label == LABEL_IWR_PARTIAL:
        return record_label(label), IWR_PARTIAL_NOTE
    if source == "iwr_post_impact" and label == LABEL_ESTIMATED:
        # two IWR tracks compete for the launch point (iwr6843.launch_range): its reason
        return LABEL_ESTIMATED, _field(shot, "ball_range_reason") or IWR_COMPETING_NOTE
    if source == "camera_ball_size" and label == LABEL_ESTIMATED:
        return LABEL_ESTIMATED, BALL_SIZE_RANGE_CALIBRATED_NOTE
    if source == "camera_resting_ball":
        if label in (LABEL_ESTIMATED, LABEL_EXPERIMENTAL):
            return label, f"{LOCATED_TEE_NOTE}: {_field(shot, 'ball_range_reason') or label}"
        return None, None
    return RANGE_SOURCE_LABELS.get(source, (None, None))


def _positive(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


# The vertical launch a struck ball can leave at, generous on purpose (a gross-error
# check, not a gate): a topped or bladed ball starts a few degrees down at most, and a
# flop shot off a lob wedge leaves near 50 deg at most. A vertical outside -10..+60 deg
# is not the ball's (a phase wrap, the club, a reflection): every vertical route of the
# launch chain (camera/fallback_chain) refuses it with why and the kiosk withholds it, the
# way a horizontal beyond a shank is (horizontal_frame.MAX_PLAUSIBLE_LAUNCH_DEG). 30 Oct
# 2026.
MIN_PLAUSIBLE_VERTICAL_DEG = -10.0
MAX_PLAUSIBLE_VERTICAL_DEG = 60.0


def implausible_vertical_reason(vertical_deg: Any) -> Optional[str]:
    """Why a vertical launch is no golf ball's, or None for a plausible (or missing) one."""
    value = _number(vertical_deg)
    if value is None or MIN_PLAUSIBLE_VERTICAL_DEG <= value <= MAX_PLAUSIBLE_VERTICAL_DEG:
        return None
    return (
        f"{value:+.1f} deg is outside {MIN_PLAUSIBLE_VERTICAL_DEG:+.0f}.."
        f"{MAX_PLAUSIBLE_VERTICAL_DEG:+.0f} deg: not the ball's vertical launch"
    )


# The radar is primary for the ball (owner, 30 Oct 2026) while it agrees with the camera:
# a radar vertical within this many combined 1-sigmas (the camera's vertical_sd_deg and the
# radar's own, when its measurement carries one) of a MEASURED camera vertical (the accepted
# camera fallback, a departure fit) stays first. Beyond it the radar reading is bad and the
# camera's vertical stands with its own label and sigma, the radar's value in its note
# ("it should fall back to camera if the radar reading is bad", owner, 3 Nov 2026; C22a).
# Oct 27 on the live chain: shot 3's radar 28.86 against the camera's 19.34 +- 0.42 is 22.7
# sigma, shot 4's 41.15 against 26.73 +- 0.64 is 22.5: both take the camera's. With no
# measured camera vertical the radar stands as before.
VERTICAL_DISAGREEMENT_SIGMAS = 3.0
# how a note says the camera's vertical stands over a radar beyond the band
CAMERA_VERTICAL_STANDS = "camera stands"


def vertical_disagreement(
    radar_deg: Any, camera_deg: Any, camera_sd_deg: Any, *, radar_sd_deg: Any = None
) -> Optional[float]:
    """How many combined 1-sigmas a radar vertical is from the camera's, when that is more
    than ``VERTICAL_DISAGREEMENT_SIGMAS``; None when it is within, or a value or the
    camera's sigma is missing. A radar sigma not given counts as 0."""
    radar, camera = _number(radar_deg), _number(camera_deg)
    camera_sd = _positive(camera_sd_deg)
    if radar is None or camera is None or camera_sd is None:
        return None
    combined = math.hypot(camera_sd, _positive(radar_sd_deg) or 0.0)
    sigmas = abs(radar - camera) / combined
    return sigmas if sigmas > VERTICAL_DISAGREEMENT_SIGMAS else None


def radar_vertical_disagreement_note(
    radar_deg: Any, camera_fallback: Any, *, radar_sd_deg: Any = None
) -> Optional[str]:
    """Why a radar vertical gives way to the measured camera vertical of an accepted camera
    fallback (beyond ``VERTICAL_DISAGREEMENT_SIGMAS``), or None. The IWR's LCMF vertical
    carries no per-shot sigma, so the radar's counts as 0 unless one is given."""
    if not isinstance(camera_fallback, Mapping) or camera_fallback.get("status") != "accepted":
        return None
    camera, camera_sd = camera_fallback.get("vertical_deg"), camera_fallback.get("vertical_sd_deg")
    sigmas = vertical_disagreement(radar_deg, camera, camera_sd, radar_sd_deg=radar_sd_deg)
    if sigmas is None:
        return None
    radar_sd = _positive(radar_sd_deg)
    radar_text = f"{float(radar_deg):.2f} deg" + (
        f" +- {radar_sd:.2f}" if radar_sd is not None else " (no per-shot sigma)"
    )
    return (
        f"radar vertical {radar_text} disagrees with the camera's {float(camera):.2f} +- "
        f"{float(camera_sd):.2f} deg by {sigmas:.1f} sigma (beyond "
        f"{VERTICAL_DISAGREEMENT_SIGMAS:g}); {CAMERA_VERTICAL_STANDS}"
    )


def _vertical_disagreement_note(shot: Any, camera_fallback: Any) -> Optional[str]:
    """``radar_vertical_disagreement_note`` for the shot's own radar vertical, against
    ``camera_fallback`` or else the shot's own ``camera_fallback_launch``."""
    fallback = camera_fallback
    if fallback is None:
        fallback = _field(shot, "camera_fallback_launch")
    return radar_vertical_disagreement_note(_field(shot, "launch_angle_vertical"), fallback)


def _radar_set_aside_note(shot: Any) -> Optional[str]:
    """The radar vertical's note when the launch chain set it aside for the camera's (its
    refusal in ``input_sources.launch_vertical.tried``), or None."""
    sources = _field(shot, "input_sources")
    record = sources.get("launch_vertical") if isinstance(sources, Mapping) else None
    for tried in (record or {}).get("tried") or () if isinstance(record, Mapping) else ():
        reason = tried.get("reason") if isinstance(tried, Mapping) else None
        if isinstance(reason, str) and reason.endswith(CAMERA_VERTICAL_STANDS):
            return reason
    return None


def shot_launch_label(
    shot: Any, axis: str, *, camera_fallback: Optional[Mapping[str, Any]] = None
) -> tuple[str, Optional[str]]:
    """(label, note) of a shot's vertical or horizontal launch, read from its own fields
    (a ``Shot`` or its record): source, status, the camera tier and the lens. A radar
    launch also takes an ambiguous IWR track (estimated) and the label of the ball range
    it rests on (``range_source_label``). A radar vertical beyond
    ``VERTICAL_DISAGREEMENT_SIGMAS`` of an accepted camera fallback (``camera_fallback``,
    else the shot's own ``camera_fallback_launch``) gives way to the camera's in the launch
    chain (C22a); read outside it, it is experimental. A camera vertical that stood over a
    radar one carries the radar's value in its note."""
    intrinsics = _field(shot, "camera_lens_intrinsics")
    if axis == "vertical":
        source = _field(shot, "launch_angle_vertical_source") or _field(shot, "angle_source")
        status, tier = vertical_launch_status(shot), None
    else:
        source = _field(shot, "launch_angle_horizontal_source") or _field(shot, "angle_source")
        status = _field(shot, "launch_angle_horizontal_status")
        tier = camera_horizontal_tier(shot)
    label = launch_label(source, status, camera_tier=tier, intrinsics=intrinsics)
    notes = [launch_label_note(source, status, camera_tier=tier, intrinsics=intrinsics)]
    if str(source or "").startswith("radar"):
        # a partial dump's estimate carries the suffix on its status (iwr6843.runtime)
        launch_status = str(_field(shot, "iwr6843_launch_status") or "")
        partial_suffix = f"_{LABEL_IWR_PARTIAL}"
        if launch_status.endswith(partial_suffix):
            label = _weaker(label, IWR_PARTIAL_RECORD_LABEL)
            notes.append(IWR_PARTIAL_NOTE)
            launch_status = launch_status.removesuffix(partial_suffix)
        if launch_status == TRACK_AMBIGUOUS_STATUS:
            label = _weaker(label, LABEL_ESTIMATED)
            notes.append(TRACK_AMBIGUOUS_NOTE)
        range_label, range_note = range_source_label(shot)
        if range_label is not None:
            label = _weaker(label, range_label)
            notes.append(range_note)
        disagreement = (
            _vertical_disagreement_note(shot, camera_fallback) if axis == "vertical" else None
        )
        if disagreement is not None:
            label = _weaker(label, LABEL_EXPERIMENTAL)
            notes.append(disagreement)
    elif axis == "vertical":
        notes.append(_radar_set_aside_note(shot))
    return label, "; ".join(dict.fromkeys(note for note in notes if note)) or None


def horizontal_launch_label(source: Optional[str], status: Optional[str] = None) -> str:
    """The label a Shot horizontal launch carries (``launch_label``, no camera tier)."""
    return launch_label(source, status)


@dataclass(frozen=True)
class HorizontalOption:
    """One horizontal launch on offer: its label, attached 1-sigma (deg) if any, whether
    the radar measured it, and whether it failed its own gate (WEAK_HORIZONTAL_SOURCES)."""

    label: str
    sigma_deg: Optional[float] = None
    radar: bool = False
    failed_gate: bool = False


def _sigma(option: HorizontalOption) -> Optional[float]:
    value = option.sigma_deg
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


_HORIZONTAL_LABEL_RANK = (LABEL_MEASURED, LABEL_CALCULATED, LABEL_MODEL_GEOMETRY, LABEL_ESTIMATED)


def prefers_horizontal(candidate: HorizontalOption, *, over: Optional[HorizontalOption]) -> bool:
    """Whether ``candidate`` replaces the horizontal shown now (``over``), Q5 (25 Oct 2026):

    0. a value that failed its own gate never outranks one that passed (30 Oct 2026: a
       low-coherence radar +43.8 beat an accepted 9.5 camera value and made face +55);
    1. an experimental value never outranks one that is not (it would never reach a
       simulator, and the weakest input wins);
    2. with a sigma on both, the smaller one wins, whatever the labels;
    3. otherwise the radar's wins; between two of a kind, the stronger label; a tie keeps
       the one shown.
    """
    if over is None:
        return True
    if candidate.failed_gate != over.failed_gate:
        return over.failed_gate
    if (candidate.label == LABEL_EXPERIMENTAL) != (over.label == LABEL_EXPERIMENTAL):
        return over.label == LABEL_EXPERIMENTAL
    mine, theirs = _sigma(candidate), _sigma(over)
    if mine is not None and theirs is not None:
        return mine < theirs
    if candidate.radar != over.radar:
        return candidate.radar
    rank = {label: index for index, label in enumerate(_HORIZONTAL_LABEL_RANK)}
    worst = len(_HORIZONTAL_LABEL_RANK)
    return rank.get(candidate.label, worst) < rank.get(over.label, worst)


def is_experimental_launch_source(source: Optional[str]) -> bool:
    """Whether a launch source is a camera value (experimental unless its lens and tier
    lift it, ``launch_label``)."""
    return source in EXPERIMENTAL_LAUNCH_SOURCES


def is_measured_source(source: Optional[str]) -> bool:
    """Whether a Shot source label names a measured value (a sensor saw it)."""
    return bool(source) and source.startswith(LABEL_MEASURED)


def is_calculated_source(source: Optional[str]) -> bool:
    """Whether a Shot source label names a calculated (model) value."""
    return bool(source) and source.startswith(LABEL_CALCULATED)


def is_model_geometry_source(source: Optional[str]) -> bool:
    """Whether a Shot source label names a value built on model geometry."""
    return bool(source) and source.startswith(SPIN_AXIS_MODEL_GEOMETRY_PREFIX)


def is_estimated_source(source: Optional[str]) -> bool:
    """Whether a Shot source label names a value built on an estimated input."""
    return bool(source) and source.startswith(SPIN_AXIS_ESTIMATED_PREFIX)
