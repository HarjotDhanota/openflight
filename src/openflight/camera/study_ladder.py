"""The tester suite's exposure ladder: its rungs, their gains, and what a swing must show.

The ladder finds the shortest exposure at full resolution where the camera's
pictures still carry the ball and the club, and compares 640x400. A rung that
cannot work in the tester's light is skipped before anyone swings, and a swing
fails only on what its own pictures show; the club pipeline's live results never
fail a swing, because its thresholds were tuned for 320x200.
"""

from __future__ import annotations

import io
import json
import math
import os
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from openflight.camera.auto_exposure import measure_exposure
from openflight.camera.club_motion import detect_reference_ball
from openflight.camera.paired_eligibility import evaluate_paired_capture

SWINGS_PER_RUNG = 5
GAIN_CEILING = 12.0
GAIN_FLOOR = 1.0  # unity analogue gain: the sensor cannot go lower
LIGHT_FLOOR_DN = 10.0  # hitting-zone signal above the black floor
CLIPPED_MAX_PCT = 5.0
FPS_MIN_FRACTION = 0.9
EXPOSURE_TOLERANCE_US = 15  # exposure applies in whole rows
EXPOSURE_TOLERANCE_FRACTION = 0.10
GAIN_TOLERANCE_FRACTION = 0.10
# New controls reach the sensor a few frames late: a check waits this long for
# frames taken at them, and judges on no fewer than this many (wiring audit T7).
CONTROLS_WAIT_S = 1.0
MIN_MATCHED_FRAMES = 3
EARLY_EXIT_SWINGS = 3
EARLY_EXIT_REDS = 2
MAX_RED_SWINGS = 3  # at any point (D9)
DARK_REDS_IN_A_ROW = 2  # the light has fallen (D9)
PHOTO_TARGET_DN = 100.0
# A photo prefers unity gain (the least noise) and raises it only as far as 2 when
# the exposure would pass the frame period. In sun it needs tens of microseconds:
# the old 100 us x 2 floor clipped it (wiring audit T5). The kiosk's still-photo
# controls take any exposure under the frame period; 9 us is the sensor's one row.
PHOTO_GAIN_RANGE = (1.0, 2.0)
PHOTO_EXPOSURE_US_MIN = 10
PHOTO_FRAME_MARGIN_US = 300
BALL_MOVED_RADII = 3.0
RESTING_FRAMES = 10  # the first pre-impact frames, before the club arrives
# Outdoors the background beyond the ball can be 30x brighter than the ball's
# surroundings (29 Sept: a sunlit patio clipped at every setting while the shaded
# mat sat at 71 DN), so a rung is judged on the ball whenever it is visible.
BALL_MIN_SIGNAL_DN = 20.0
BALL_TARGET_MEDIAN_DN = 150.0
MAX_GAIN_CORRECTIONS = 3
# With no ball in view the hitting zone decides, and only a mostly clipped zone
# counts as too bright: a clipped strip of background is normal outdoors.
ZONE_TOO_BRIGHT_PCT = 50.0
# A dark hitting zone is the club's background, so it fails a rung even when the
# ball is fine; the pre-rung check aims it here when it can.
ZONE_TARGET_SIGNAL_DN = 40.0
BALL_CEILING_DN = 200.0  # a zone correction never pushes the ball's median past this
# One light rule for the pre-rung check and the per-swing verdict (wiring audit B6).
RED_LIGHT_CAUSES = frozenset({"ball_clipped", "ball_dark", "zone_dark", "zone_clipped_no_ball"})
# The ball is searched for this many diameters around where the setup saw it, and
# must lie within about one diameter of that spot (P7-9).
BALL_SEARCH_DIAMETERS = 2.0
BALL_SEARCH_MIN_PX = 30.0
BALL_MATCH_DIAMETERS = 1.0
DARK_LIGHT_CAUSES = frozenset({"ball_dark", "zone_dark"})
# No verdict without the setup's ball position (P6-1): without it the ball
# cannot be told from a speck, a spare ball or a white cloth.
NO_SETUP_BALL = "the setup has no ball position for this camera mode, so it can't be judged"
BRIGHT_LIGHT_CAUSES = frozenset({"ball_clipped", "zone_clipped_no_ball"})


@dataclass(frozen=True)
class Rung:
    """One exposure on one readout mode; ``photos`` asks for impact photos."""

    rung_id: str
    arm_id: str
    exposure_us: int
    photos: bool


# 50 and 30 us are for sunlight (outdoors 29 Sept a ball in sun clipped at 50 us x 1
# during setup); indoors they are skipped with the rest once 75 us is too dark. In
# full sun on 30 Sept the setup locked the ball at 10 us (7 applied) and 30 us was
# marginal, so 20 and 10 us follow, and 30 and 15 us at 640x400 (P7-9). The
# sensor's shortest exposure is 9 us (one row); both modes took 10 us and applied 7.
LADDER: tuple[Rung, ...] = tuple(
    [Rung(f"full-{e}", "arm5", e, True) for e in (300, 200, 150, 100, 75, 50, 30, 20, 10)]
    + [Rung(f"half-{e}", "arm6", e, False) for e in (300, 150, 75, 30, 15)]
)
RUNG_FPS = {"arm5": 120.0, "arm6": 288.0}
# A setting the tester unticked: review and analysis must not read it as a light failure.
NOT_SELECTED = "not selected by the tester"
# A pressed setting in the other mode waits this long for a swing taken just before
# the press to be saved, and up to SWITCH_WAIT_S for saved ones to be judged (past
# the paired check's 30 s no-radar-shot timeout), before the kiosk restarts (P7-13).
SWITCH_GRACE_S = 2.0
SWITCH_WAIT_S = 35.0


def find_rung(rung_id: object) -> Rung:
    """The rung with this id; refuses an unknown one."""
    rung = next((item for item in LADDER if item.rung_id == rung_id), None)
    if rung is None:
        raise ValueError(f"unknown ladder setting: {rung_id}")
    return rung


def validate_selection(rung_ids: object) -> list[str]:
    """The ticked rung ids in ladder order; refuses an empty choice or an unknown id."""
    if not isinstance(rung_ids, (list, tuple)) or not all(isinstance(r, str) for r in rung_ids):
        raise ValueError("the settings must be a list of setting names")
    unknown = sorted(set(rung_ids) - {rung.rung_id for rung in LADDER})
    if unknown:
        raise ValueError(f"unknown ladder setting: {', '.join(unknown)}")
    if not rung_ids:
        raise ValueError("choose at least one setting")
    return [rung.rung_id for rung in LADDER if rung.rung_id in rung_ids]


def rung_gain(gain_at_300: float, exposure_us: int) -> float:
    """The gain that keeps the gain screen's brightness at this exposure, within the sensor.

    In sunlight the light-equivalent gain at 300 us is below unity; a short rung
    then still gets a real gain, and a long rung sits at unity and may be too bright.
    """
    return round(max(GAIN_FLOOR, min(GAIN_CEILING, gain_at_300 * 300.0 / exposure_us)), 3)


def starting_gain(
    exposure_us: int, gain_at_300: float | None, basis: dict | None
) -> tuple[float, dict]:
    """A rung's first gain, and what set it (P7-12).

    ``basis`` is the setup's ball lock for this mode: ``signal_us`` is its applied
    exposure x gain, and each rung keeps that product, so the ball is as bright
    at every rung and the study compares blur, not brightness. ``source`` is
    ``setup_lock`` or ``setup_lock_scaled`` (the 1280x800 lock scaled to a mode
    without its own). Without one the zone gain screen decides, as before; in sun
    that started full-10 at gain 12 while the ball needed about 1.
    """
    if basis is not None:
        wanted = float(basis["signal_us"]) / float(exposure_us)
        return round(max(GAIN_FLOOR, min(GAIN_CEILING, wanted)), 3), dict(basis)
    if gain_at_300 is None:
        raise ValueError("a rung needs the setup's lock or a gain screen")
    return rung_gain(gain_at_300, exposure_us), {
        "source": "gain_screen",
        "gain_at_300": float(gain_at_300),
    }


def photo_controls(
    light_index: float | None,
    black_floor: float,
    fps: float,
    *,
    rung_exposure_us: int | None = None,
    rung_gain: float | None = None,
) -> tuple[int, float]:
    """Exposure and gain that put the hitting zone near 100 DN, under the frame period.

    Aimed with the zone light index; without one (a screen that measured none), the
    photo keeps the rung's own brightness, its exposure x gain.
    """
    frame_limit = int(1_000_000 / fps) - PHOTO_FRAME_MARGIN_US
    if light_index is None:
        if rung_exposure_us is None or rung_gain is None:
            raise ValueError("a photo needs a light index or the rung's controls")
        wanted = float(rung_exposure_us) * float(rung_gain)  # exposure x gain
    else:
        wanted = (PHOTO_TARGET_DN - black_floor) / max(float(light_index), 1e-9)
    low, high = PHOTO_GAIN_RANGE
    gain = round(max(low, min(high, wanted / frame_limit)), 3)
    exposure = int(max(PHOTO_EXPOSURE_US_MIN, min(frame_limit, round(wanted / gain))))
    return exposure, gain


def _zone(image: np.ndarray, black_floor: float) -> dict:
    observation = measure_exposure(np.clip(np.asarray(image), 0, 255).astype(np.uint8))
    median = float(observation.median or 0.0)
    return {
        "median_dn": median,
        "signal_dn": median - black_floor,
        "clipped_pct": float(observation.clipped_pct or 0.0),
    }


def _zone_noise(frames: np.ndarray) -> float:
    height, width = frames.shape[1:]
    zone = frames[
        :, round(height * 0.45) : round(height * 0.9), round(width * 0.2) : round(width * 0.8)
    ]
    return float(np.median(np.std(zone.astype(np.float32), axis=0)))


def _ball_core(image: np.ndarray, x: float, y: float, diameter: float) -> np.ndarray:
    yy, xx = np.indices(image.shape)
    return image[np.hypot(xx - x, yy - y) <= 0.8 * diameter / 2.0]


def ball_light(
    frames: np.ndarray, black_floor: float, expected_ball: dict | None = None
) -> dict | None:
    """The resting ball's brightness in these frames, if the ball can be found.

    ``expected_ball`` (x, y, diameter_px) is where the setup saw the ball in this
    mode. The ball is searched for only around that spot, and only a ball of that
    size within about one diameter of it counts (P7-9: a six-diameter match took
    fence clutter 175 px away for the ball in sun). Without it nothing is
    searched: a whole-frame search took a 5 px speck for the ball (P6-1).

    A ball in a sunlit, clipped patch of mat melts into it and cannot be found;
    when the setup's spot is itself clipped, that spot is judged (``found_by`` is
    ``setup_position``), since a ball placed there clips too.
    """
    if expected_ball is None:
        return None
    stack = np.clip(np.asarray(frames), 0, 255).astype(np.uint8)
    diameter = float(expected_ball["diameter_px"])
    expected_x, expected_y = float(expected_ball["x"]), float(expected_ball["y"])
    height, width = stack.shape[1:]
    half = max(BALL_SEARCH_DIAMETERS * diameter, BALL_SEARCH_MIN_PX)
    roi = (
        max(0, int(expected_x - half)),
        max(0, int(expected_y - half)),
        min(width, int(math.ceil(expected_x + half))),
        min(height, int(math.ceil(expected_y + half))),
    )
    image = np.median(stack, axis=0)
    try:
        # The detector's strict lit-sphere mode (given a size) refuses sunlit or
        # half-shaded balls, so the result is checked against the setup instead.
        found = detect_reference_ball(stack, roi=roi)
    except (RuntimeError, ValueError):
        found = None
    if found is not None and (
        0.6 * diameter <= found.diameter_px <= 1.6 * diameter
        and math.hypot(found.x - expected_x, found.y - expected_y)
        <= BALL_MATCH_DIAMETERS * diameter
    ):
        x, y, size, found_by = float(found.x), float(found.y), float(found.diameter_px), "detector"
    else:
        x, y, size, found_by = expected_x, expected_y, diameter, "setup_position"
    core = _ball_core(image, x, y, size)
    if not core.size:
        return None
    clipped = float(np.mean(core >= 250) * 100.0)
    if found_by == "setup_position" and clipped <= CLIPPED_MAX_PCT:
        return None  # no ball there, and nothing clipped where it should be
    median = float(np.median(core))
    return {
        "x": x,
        "y": y,
        "diameter_px": size,
        "median_dn": median,
        "signal_dn": median - black_floor,
        "clipped_pct": clipped,
        "found_by": found_by,
    }


def judge_light(frames: np.ndarray, black_floor: float, expected_ball: dict | None = None) -> dict:
    """The one light rule both the pre-rung check and the swing verdict apply.

    ``cause`` is one of: ``ball_clipped``, ``ball_dark``, ``zone_dark`` (the club's
    background), ``zone_clipped_no_ball``, ``background_clipped`` (amber only) or
    ``ok``. The ball is judged first when it can be found.
    """
    frames = np.asarray(frames)
    zone = _zone(np.median(frames, axis=0), black_floor)
    ball = ball_light(frames, black_floor, expected_ball)
    cause, message = "ok", None
    if ball is not None and ball["clipped_pct"] > CLIPPED_MAX_PCT:
        cause = "ball_clipped"
        message = f"too bright for the ball: {ball['clipped_pct']:.0f}% of it is clipped"
    elif ball is not None and ball["signal_dn"] < BALL_MIN_SIGNAL_DN:
        cause = "ball_dark"
        message = f"too dark for the ball: {ball['signal_dn']:.0f} DN above black"
    elif zone["signal_dn"] < LIGHT_FLOOR_DN:
        cause = "zone_dark"
        message = (
            f"too dark in this light: the hitting zone is {zone['signal_dn']:.0f} DN above "
            f"black, under {LIGHT_FLOOR_DN:.0f}"
        )
    elif ball is None and zone["clipped_pct"] > ZONE_TOO_BRIGHT_PCT:
        cause = "zone_clipped_no_ball"
        message = (
            f"too bright in this light: {zone['clipped_pct']:.0f}% of the hitting zone is "
            "clipped; a shorter exposure follows"
        )
    elif zone["clipped_pct"] > CLIPPED_MAX_PCT:
        cause = "background_clipped"
        message = f"background: {zone['clipped_pct']:.0f}% of the hitting zone clipped" + (
            " behind a well-exposed ball" if ball is not None else ""
        )
    return {"zone": zone, "ball": ball, "cause": cause, "message": message}


def resting_frames(frames: np.ndarray) -> np.ndarray:
    """A clip's first frames, before the club arrives: what the ball is judged on."""
    return frames[: max(3, min(RESTING_FRAMES, len(frames)))]


def capture_analysis_eligibility(
    frames: np.ndarray, black_floor: float, expected_ball: dict
) -> dict:
    """Whether a clip's light permits camera analysis, judged on the setup's ball (P7-8).

    The same ``judge_light`` as the ladder, on the clip's resting frames. Only the
    ball decides: its core clipped ``CLIPPED_MAX_PCT`` or less and
    ``BALL_MIN_SIGNAL_DN`` or more above black. A clipped background or a dark
    hitting zone does not withhold the camera (outdoors a sunny mat is always
    about 20 % clipped, and at the setup's own sun lock the zone reads too dark).
    No ball where the setup saw it is not eligible.
    """
    light = judge_light(resting_frames(np.asarray(frames)), black_floor, expected_ball)
    ball = light["ball"]
    if ball is None:
        reason = "the resting ball was not found where the setup saw it"
    elif ball["clipped_pct"] > CLIPPED_MAX_PCT:
        reason = f"too bright for the ball: {ball['clipped_pct']:.0f}% of it is clipped"
    elif ball["signal_dn"] < BALL_MIN_SIGNAL_DN:
        reason = f"too dark for the ball: {ball['signal_dn']:.0f} DN above black"
    else:
        reason = None
    return {
        "rule": "setup_ball",
        "eligible": reason is None,
        "reason": reason,
        "light_cause": light["cause"],
        "ball": ball,
        "zone": light["zone"],
        "setup_ball": dict(expected_ball),
        "black_floor_dn": float(black_floor),
    }


def _suggested_gain(light: dict, gain: float, black_floor: float) -> float | None:
    """The gain that would bring this rung's light back into range, if any."""
    ball, zone, cause = light["ball"], light["zone"], light["cause"]
    if cause == "ball_clipped":
        # always down: a half-sunlit ball can have a low median and still clip
        if ball["median_dn"] >= 250:
            return gain * 0.5
        return gain * min(0.8, BALL_TARGET_MEDIAN_DN / ball["median_dn"])
    if cause == "ball_dark":
        # always up, measured above black
        return gain * max(1.25, BALL_TARGET_MEDIAN_DN / max(ball["median_dn"] - black_floor, 1.0))
    if cause == "zone_dark":
        wanted = gain * max(1.25, ZONE_TARGET_SIGNAL_DN / max(zone["signal_dn"], 1.0))
        if ball is not None:
            wanted = min(wanted, gain * BALL_CEILING_DN / max(ball["median_dn"], 1.0))
        return wanted if wanted > gain * 1.05 else None
    return None


def pre_rung_check(
    frames: np.ndarray,
    black_floor: float,
    gain: float | None = None,
    *,
    expected_ball: dict | None = None,
) -> dict:
    """Whether this rung can work in this light, from a few raw frames and no swing.

    It applies ``judge_light``, the same rule as the swing verdict.
    ``suggested_gain`` is the gain that would bring the light back into range at
    this exposure; ``too_bright`` marks a rung a shorter exposure may still rescue.
    Without the setup's ball position it never passes (P6-1).
    """
    frames = np.asarray(frames)
    light = judge_light(frames, black_floor, expected_ball)
    if expected_ball is None:
        return {
            **light["zone"],
            "noise_dn": _zone_noise(frames),
            "judged_on": "nothing",
            "ball": None,
            "ok": False,
            "reason": NO_SETUP_BALL,
            "note": None,
            "light_cause": "no_setup_ball",
            "too_bright": False,
            "too_dark": False,
            "suggested_gain": None,
        }
    cause = light["cause"]
    red = cause in RED_LIGHT_CAUSES
    return {
        **light["zone"],
        "noise_dn": _zone_noise(frames),
        "judged_on": (
            "hitting_zone"
            if light["ball"] is None
            else ("setup_position" if light["ball"]["found_by"] == "setup_position" else "ball")
        ),
        "ball": light["ball"],
        "ok": not red,
        "reason": light["message"] if red else None,
        "note": light["message"] if not red else None,
        "light_cause": cause,
        "too_bright": cause in BRIGHT_LIGHT_CAUSES,
        "too_dark": cause in DARK_LIGHT_CAUSES,
        "suggested_gain": _suggested_gain(light, gain, black_floor) if gain and red else None,
    }


def exposure_matches(applied_us: float, wanted_us: float) -> bool:
    """Whether an exposure is the wanted one, allowing for whole-row steps."""
    tolerance = max(EXPOSURE_TOLERANCE_US, EXPOSURE_TOLERANCE_FRACTION * wanted_us)
    return abs(float(applied_us) - float(wanted_us)) <= tolerance


def gain_matches(applied: float, wanted: float) -> bool:
    """Whether a gain is the wanted one, allowing for the sensor's 1/16 steps."""
    return abs(float(applied) - float(wanted)) <= GAIN_TOLERANCE_FRACTION * float(wanted)


def trigger_controls_mismatch(metadata: dict, rung: Rung, gain: float | None) -> str | None:
    """Why a capture was not taken at this rung's controls, or None if it was.

    The kiosk records the requested controls and their purpose at the trigger
    (``auto_exposure``); swings taken during a gain correction, a still photo or
    before the rung was set must not count for or against it (wiring audit T3).
    Captures without that record are judged as before.
    """
    controls = metadata.get("auto_exposure")
    if not isinstance(controls, dict):
        return None
    purpose = controls.get("controls_purpose") or "capture"
    if purpose != "capture":
        return f"taken during a {purpose}, not at this rung's controls"
    exposure = controls.get("exposure_us")
    if exposure is not None and not exposure_matches(exposure, rung.exposure_us):
        return f"taken at {float(exposure):.0f} us, not this rung's {rung.exposure_us} us"
    requested_gain = controls.get("gain")
    if gain and requested_gain is not None and not gain_matches(requested_gain, gain):
        return f"taken at gain {float(requested_gain):.2f}, not this rung's {gain:.2f}"
    return None


def swing_verdict(  # pylint: disable=too-many-locals,too-many-arguments
    capture_dir: Path,
    rung: Rung,
    gain: float,
    black_floor: float,
    previous_balls: list[dict],
    expected_ball: dict | None = None,
) -> dict:
    """Green, amber or red for one saved swing, from its own pictures and timing."""
    metadata = json.loads((capture_dir / "metadata.json").read_text(encoding="utf-8"))
    with np.load(capture_dir / "frames.npz") as data:
        frames = data["frames"]
        applied_exposure = float(np.median(data["exposure_us"]))
        applied_gain = float(np.median(data["analogue_gain"]))
    fps = RUNG_FPS[rung.arm_id]
    red: list[str] = []
    amber: list[str] = []
    delivered = float(metadata.get("delivered_fps", 0.0))
    gaps = int(metadata.get("gap_count", 0))
    if delivered < FPS_MIN_FRACTION * fps:
        red.append(f"frames: {delivered:.0f} fps delivered, under 90% of {fps:.0f}")
    if gaps:
        red.append(f"frames: {gaps} gap(s) in the capture")
    tolerance = max(EXPOSURE_TOLERANCE_US, EXPOSURE_TOLERANCE_FRACTION * rung.exposure_us)
    if abs(applied_exposure - rung.exposure_us) > tolerance:
        red.append(f"controls: exposure {applied_exposure:.0f} us, not {rung.exposure_us}")
    if abs(applied_gain - gain) > GAIN_TOLERANCE_FRACTION * gain:
        red.append(f"controls: gain {applied_gain:.2f}, not {gain:.2f}")
    light = judge_light(resting_frames(frames), black_floor, expected_ball)
    stats = light["zone"]
    lit = light["ball"]
    if light["cause"] in RED_LIGHT_CAUSES:
        red.append(f"light: {light['message']}")
    elif light["message"]:
        amber.append(light["message"])
    ball = None
    if expected_ball is None:
        red.append(f"ball: {NO_SETUP_BALL}")
    elif lit is None:
        amber.append("resting ball not found in the pre-impact frames")
    elif lit["found_by"] == "detector":
        ball = {"x": lit["x"], "y": lit["y"], "diameter_px": lit["diameter_px"]}
    if ball and previous_balls:
        x = float(np.median([b["x"] for b in previous_balls]))
        y = float(np.median([b["y"] for b in previous_balls]))
        if math.hypot(ball["x"] - x, ball["y"] - y) > BALL_MOVED_RADII * ball["diameter_px"] / 2:
            amber.append("resting ball found somewhere else than on this rung's other swings")
    return {
        "capture": capture_dir.name,
        "color": "red" if red else ("amber" if amber else "green"),
        "reasons": red + amber,
        "delivered_fps": delivered,
        "gaps": gaps,
        "exposure_us": applied_exposure,
        "gain": applied_gain,
        "signal_dn": stats["signal_dn"],
        "clipped_pct": stats["clipped_pct"],
        "light_cause": light["cause"],
        "ball": ball,
    }


def _new_rung_entry(rung: Rung) -> dict:
    return {
        "arm_id": rung.arm_id,
        "exposure_us": rung.exposure_us,
        "status": "pending",
        "gain": None,
        "pre_check": None,
        "reason": None,
        "swings": [],
    }


class LadderState:
    """The ladder's progress for one tester, kept in ``ladder.json`` so a reload resumes it."""

    def __init__(self, path: Path, *, persist_initial: bool = True):
        self.path = path
        if path.is_file():
            self._data = json.loads(path.read_text(encoding="utf-8"))
            self._data.setdefault("photos", {})
            self._data.setdefault("photo_skips", {})
            self._data.setdefault("photo_target", None)
            self._data.setdefault("pending_photo", None)
            self._data.setdefault("ineligible_captures", [])
            # a ladder from before the tester could choose ran every setting
            self._data.setdefault("selected_rungs", [rung.rung_id for rung in LADDER])
            if self._add_missing_rungs():
                self._save()
        else:
            self._data = {
                "rungs": {rung.rung_id: _new_rung_entry(rung) for rung in LADDER},
                "photos": {},
                "photo_skips": {},
                "photo_target": None,
                "pending_photo": None,
                "ineligible_captures": [],
                "selected_rungs": [rung.rung_id for rung in LADDER],
            }
            if persist_initial:
                self._save()

    def _add_missing_rungs(self) -> list[str]:
        """Give a ladder file written before a rung existed that rung.

        A rung the ladder has already moved past (a later rung has started) is
        skipped, so an old ladder never switches back to a finished mode.
        """
        rungs = self._data.setdefault("rungs", {})
        added = []
        for index, rung in enumerate(LADDER):
            if rung.rung_id in rungs:
                continue
            entry = _new_rung_entry(rung)
            later = [r.rung_id for r in LADDER[index + 1 :] if r.rung_id in rungs]
            if any(rungs[rung_id]["status"] != "pending" for rung_id in later):
                entry["status"] = "skipped"
                entry["reason"] = "added after this ladder had moved past it"
            rungs[rung.rung_id] = entry
            added.append(rung.rung_id)
        if added:
            self._data["migrated_added_rungs"] = [
                *self._data.get("migrated_added_rungs", []),
                *added,
            ]
        return added

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", delete=False, suffix=".tmp", dir=self.path.parent
            ) as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(self._data, indent=2) + "\n")
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @property
    def current(self) -> Rung | None:
        """The active rung, else a pressed one not begun yet, else the next pending one.

        None at the end.
        """
        rungs = self._data["rungs"]
        pressed = self._data.get("pressed")
        for rung in LADDER:
            if rungs[rung.rung_id]["status"] == "active":
                return rung
        if pressed in rungs and rungs[pressed]["status"] == "pending":
            return find_rung(pressed)
        for rung in LADDER:
            if rungs[rung.rung_id]["status"] == "pending":
                return rung
        return None

    @property
    def moved_on(self) -> bool:
        """Whether a setting other than the current one has run or been judged.

        Settings the tester unticked do not count: a ladder that starts at its
        first ticked setting has not moved on, nor has one pressed straight onto
        a later setting (P7-13).
        """
        current = self.current
        if current is None:
            return False
        return any(
            entry["swings"]
            or entry["status"] not in ("pending", "skipped")
            or (entry["status"] == "skipped" and entry["reason"] != NOT_SELECTED)
            for rung_id, entry in self._data["rungs"].items()
            if rung_id != current.rung_id
        )

    def select(self, rung_ids) -> None:
        """Apply the tester's ticks to the settings that have not run yet.

        A pending setting not ticked is skipped as not selected; one skipped that
        way and ticked again is pending again. Any other status is never changed,
        nor is a pressed setting not begun yet (P7-13).
        """
        selected = validate_selection(rung_ids)
        for rung in LADDER:
            entry = self._data["rungs"][rung.rung_id]
            if (
                entry["status"] == "pending"
                and rung.rung_id not in selected
                and rung.rung_id != self._data.get("pressed")
            ):
                entry["status"] = "skipped"
                entry["reason"] = NOT_SELECTED
            elif (
                entry["status"] == "skipped"
                and entry["reason"] == NOT_SELECTED
                and rung.rung_id in selected
            ):
                entry["status"] = "pending"
                entry["reason"] = None
        self._data["selected_rungs"] = selected
        self._settle_boundary_photo()
        self._save()

    def _settle_boundary_photo(self) -> None:
        following = self.current
        if following is not None and following.arm_id == "arm5":
            # 1280x800 has settings to run again: its last photo is not a handoff yet
            self._data["pending_photo"] = None
        else:
            self._require_boundary_photo(LADDER[0])

    def jump(self, rung_id: str) -> None:
        """The tester pressed this setting: it runs now, ticked or not (P7-13, D12).

        The setting left behind keeps its swings and waits for its turn (not
        selected again when unticked), so returning to it continues it. A failed
        or skipped setting reopens with its swings kept and a fresh red count; a
        setting that has its good swings can't be pressed. When the pressed
        setting finishes or fails, the ticked order continues. Each press is
        recorded in ``switches``.
        """
        rung = find_rung(rung_id)
        rungs = self._data["rungs"]
        entry = rungs[rung_id]
        if entry["status"] == "done":
            raise ValueError(f"{rung_id} is done: it has its {SWINGS_PER_RUNG} good swings")
        previous = self.current
        if previous == rung and entry["status"] == "active":
            return
        if previous is not None and previous != rung:
            left = rungs[previous.rung_id]
            if left["status"] in ("active", "pending"):
                if previous.rung_id in self._data["selected_rungs"]:
                    left["status"] = "pending"
                else:
                    left["status"] = "skipped"
                    left["reason"] = NOT_SELECTED
        if entry["status"] in ("failed", "skipped"):
            if entry["status"] == "failed" or entry["reason"] != NOT_SELECTED:
                entry.setdefault("reopened", []).append(
                    {
                        "status": entry["status"],
                        "reason": entry["reason"],
                        "swings": len(entry["swings"]),
                    }
                )
            entry["status"] = "pending"
            entry["reason"] = None
            entry["reds_from"] = len(entry["swings"])  # earlier reds stop counting
        self._data["pressed"] = rung_id
        self._data.setdefault("switches", []).append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "from": previous.rung_id if previous else None,
                "to": rung_id,
                "kind": "manual",
            }
        )
        self._settle_boundary_photo()
        self._save()

    def runs_anyway(self, rung_id: str) -> bool:
        """Whether this is the pressed setting, which runs whatever its light (P7-13)."""
        return self._data.get("pressed") == rung_id

    def gain(self, rung_id: str) -> float | None:
        return self._data["rungs"][rung_id]["gain"]

    def accepted(self, rung_id: str) -> int:
        swings = self._data["rungs"][rung_id]["swings"]
        return sum(1 for swing in swings if swing["color"] in ("green", "amber"))

    def seen_captures(self) -> set[str]:
        rungs = self._data["rungs"].values()
        return {
            *{swing["capture"] for rung in rungs for swing in rung["swings"]},
            *{swing["capture"] for rung in rungs for swing in rung.get("superseded_swings", [])},
            *{item["capture"] for item in self._data["ineligible_captures"]},
        }

    def record_resume_check(self, rung_id: str, check: dict) -> None:
        """A resumed rung passed its check again at its own gain, or runs anyway (P7-13)."""
        entry = self._data["rungs"][rung_id]
        entry.setdefault("resume_checks", []).append(check)
        if check.get("ok"):
            entry.pop("light_warning", None)
        else:
            entry["light_warning"] = check.get("reason") or "failed the pre-rung check"
        self._save()

    def restart(self, rung_id: str, check: dict) -> None:
        """A resumed rung failed its check: it starts again, and its swings stop counting.

        They were taken in light that has since changed; they stay on record as
        superseded and are never verdicted again (wiring audit T14).
        """
        entry = self._data["rungs"][rung_id]
        entry.setdefault("resume_checks", []).append(check)
        entry["superseded_swings"] = [*entry.get("superseded_swings", []), *entry["swings"]]
        entry["swings"] = []
        entry["status"] = "pending"
        self._save()

    def record_ineligible_capture(
        self,
        capture: str,
        rung_id: str,
        readiness: dict,
        reason: str = "runtime setup readiness blocked at capture review",
    ) -> None:
        """Retain a capture that must not count, without advancing or failing its rung."""
        if capture in self.seen_captures():
            return
        self._data["ineligible_captures"].append(
            {
                "capture": capture,
                "rung_id": rung_id,
                "reason": reason,
                "readiness": readiness,
            }
        )
        self._save()

    def _skip_from(self, rung: Rung, status: str, reason: str) -> None:
        """This rung, and every shorter rung of its mode after it, will not be captured."""
        entries = self._data["rungs"]
        entries[rung.rung_id]["status"] = status
        entries[rung.rung_id]["reason"] = reason
        for other in LADDER[LADDER.index(rung) + 1 :]:
            if other.arm_id == rung.arm_id and entries[other.rung_id]["status"] == "pending":
                entries[other.rung_id]["status"] = "skipped"
                entries[other.rung_id]["reason"] = f"{rung.rung_id} {status}: {reason}"

    def begin(self, rung_id: str, gain: float, check: dict, gain_basis: dict | None = None) -> None:
        rung = next(r for r in LADDER if r.rung_id == rung_id)
        entry = self._data["rungs"][rung_id]
        entry["gain"] = gain
        entry["pre_check"] = check
        if gain_basis is not None:
            # what set the rung's first gain (P7-12); a pre-check may correct it
            entry["gain_source"] = gain_basis["source"]
            entry["gain_basis"] = gain_basis
        entry.pop("light_warning", None)
        if check.get("ok"):
            entry["status"] = "active"
        elif self.runs_anyway(rung_id) and (
            check.get("light_cause") in RED_LIGHT_CAUSES
            or check.get("too_bright")
            or check.get("too_dark")
        ):
            # the tester pressed it: bad light is a warning, not a skip (P7-13)
            entry["status"] = "active"
            entry["light_warning"] = check.get("reason") or "failed the pre-rung check"
        elif check.get("too_bright"):
            # shorter exposures in this mode are darker and may still work
            entry["status"] = "skipped"
            entry["reason"] = check.get("reason") or "too bright"
        else:
            self._skip_from(rung, "skipped", check.get("reason") or "failed the pre-rung check")
        self._require_boundary_photo(rung)
        self._save()

    def _require_boundary_photo(self, rung: Rung) -> None:
        following = self.current
        target = self._data["photo_target"]
        if (
            rung.arm_id == "arm5"
            and (following is None or following.arm_id != rung.arm_id)
            and target is not None
            and target["capture"] not in self._data["photos"]
            and target["capture"] not in self._data["photo_skips"]
        ):
            self._data["pending_photo"] = target

    @staticmethod
    def _failure(swings: list[dict]) -> tuple[str, bool] | None:
        """Why these swings fail their setting, and whether the light fell, or None.

        The early exit (2 reds in the first 3) stands; two dark reds in a row mean
        the light has fallen; and 3 reds at any point end a setting that would
        otherwise never finish (D9, Outdoors-test-5).
        """
        first = swings[:EARLY_EXIT_SWINGS]
        early = [swing for swing in first if swing["color"] == "red"]
        reds = [swing for swing in swings if swing["color"] == "red"]

        def dark(items: list[dict]) -> bool:
            return any(swing.get("light_cause") in DARK_LIGHT_CAUSES for swing in items)

        if len(early) >= EARLY_EXIT_REDS:
            return f"{len(early)} of the first {len(first)} swings red", dark(early)
        last = swings[-DARK_REDS_IN_A_ROW:]
        if len(last) == DARK_REDS_IN_A_ROW and all(
            swing["color"] == "red" and swing.get("light_cause") in DARK_LIGHT_CAUSES
            for swing in last
        ):
            return f"{DARK_REDS_IN_A_ROW} dark red swings in a row", True
        if len(reds) >= MAX_RED_SWINGS:
            return f"{len(reds)} red swings", dark(reds)
        return None

    def record_swing(self, verdict: dict, rung_id: str | None = None) -> str:
        """Count a swing for the current rung, or for ``rung_id``, the one it was taken at.

        A swing taken just before the tester pressed another setting still counts
        for the setting it was taken at (P7-13).
        """
        rung = self.current if rung_id is None else find_rung(rung_id)
        if rung is None:
            return "done"
        entry = self._data["rungs"][rung.rung_id]
        if verdict["capture"] in self.seen_captures():
            return entry["status"]
        entry["swings"].append(verdict)
        if rung.photos:
            self._data["photo_target"] = {
                "capture": verdict["capture"],
                "rung_id": rung.rung_id,
            }
        # a reopened setting counts only its new reds (P7-13)
        failure = self._failure(entry["swings"][entry.get("reds_from", 0) :])
        if entry["status"] == "done":
            pass  # a late swing for a setting that already has its good swings
        elif failure is not None:
            reason, dark = failure
            if dark:
                # a shorter exposure is darker still
                self._skip_from(rung, "failed", reason)
            else:
                entry["status"] = "failed"
                entry["reason"] = reason
        elif self.accepted(rung.rung_id) >= SWINGS_PER_RUNG:
            entry["status"] = "done"
        if entry["status"] in ("done", "failed") and self.runs_anyway(rung.rung_id):
            self._data["pressed"] = None  # over: the ticked order takes up again
        self._require_boundary_photo(rung)
        self._save()
        return entry["status"]

    def record_photo(self, capture: str, rung_id: str, path: str) -> None:
        target = {"capture": capture, "rung_id": rung_id}
        previous_target = self._data["photo_target"]
        previous_photos = dict(self._data["photos"])
        self._data["photos"][capture] = path
        if previous_target == target:
            self._data["photo_target"] = None
        try:
            self._save()
        except OSError:
            self._data["photos"] = previous_photos
            self._data["photo_target"] = previous_target
            raise

    def _check_pending_photo(self, capture: str, rung_id: str) -> None:
        if self._data["pending_photo"] != {"capture": capture, "rung_id": rung_id}:
            raise RuntimeError("that capture is no longer the pending photo")

    def finish_photo(self, capture: str, rung_id: str, path: str) -> None:
        self._check_pending_photo(capture, rung_id)
        previous_photos = dict(self._data["photos"])
        previous_pending = self._data["pending_photo"]
        previous_target = self._data["photo_target"]
        self._data["photos"][capture] = path
        self._data["pending_photo"] = None
        if previous_target == {"capture": capture, "rung_id": rung_id}:
            self._data["photo_target"] = None
        try:
            self._save()
        except OSError:
            self._data["photos"] = previous_photos
            self._data["pending_photo"] = previous_pending
            self._data["photo_target"] = previous_target
            raise

    def skip_photo(self, capture: str, rung_id: str) -> None:
        self._check_pending_photo(capture, rung_id)
        previous_skips = dict(self._data["photo_skips"])
        previous_pending = self._data["pending_photo"]
        previous_target = self._data["photo_target"]
        self._data["photo_skips"][capture] = {"rung_id": rung_id}
        self._data["pending_photo"] = None
        if previous_target == {"capture": capture, "rung_id": rung_id}:
            self._data["photo_target"] = None
        try:
            self._save()
        except OSError:
            self._data["photo_skips"] = previous_skips
            self._data["pending_photo"] = previous_pending
            self._data["photo_target"] = previous_target
            raise

    def to_dict(self) -> dict:
        current = self.current
        return {**self._data, "current": current.rung_id if current else None}


class KioskClient:
    """The study-mode endpoints of the kiosk running on this Pi."""

    def __init__(self, base_url: str = "http://127.0.0.1:8080", timeout_s: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _get(self, path: str) -> bytes:
        with urllib.request.urlopen(self.base_url + path, timeout=self.timeout_s) as response:
            return response.read()

    def ready(self) -> bool:
        try:
            quality = json.loads(self._get("/api/camera/exposure-quality"))
        except (OSError, ValueError):
            return False
        return bool(quality.get("sample_available"))

    def setup_readiness(self) -> dict:
        try:
            return json.loads(self._get("/api/camera/study/readiness"))
        except (OSError, ValueError) as exc:
            return {
                "schema_version": 1,
                "ready": False,
                "checks": [],
                "blockers": [{"id": "kiosk", "reason": str(exc)}],
            }

    def set_controls(self, exposure_us: int, gain: float, purpose: str = "capture") -> dict:
        body = json.dumps(
            {"exposure_us": int(exposure_us), "gain": float(gain), "purpose": purpose}
        ).encode()
        request = urllib.request.Request(
            self.base_url + "/api/camera/study/controls",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            return json.loads(response.read())

    def frames_with_controls(self, count: int) -> dict:
        """The next frames, with the exposure and gain the sensor applied to each."""
        with np.load(io.BytesIO(self._get(f"/api/camera/study/frames?n={int(count)}"))) as data:
            return {
                "frames": data["frames"],
                "exposure_us": data["exposure_us"],
                "gain": data["analogue_gain"],
            }

    def frames(self, count: int) -> np.ndarray:
        return self.frames_with_controls(count)["frames"]

    def ready_light(self) -> dict | None:
        """The kiosk's ready light (P7-14), or None when it does not answer."""
        try:
            return json.loads(self._get("/api/ready-light"))
        except (OSError, ValueError):
            return None

    def set_ready_hold(self, cause: str | None) -> dict | None:
        """Hold the kiosk's light red for the ladder's phase; None when it does not answer."""
        request = urllib.request.Request(
            self.base_url + "/api/ready-light/hold",
            data=json.dumps({"cause": cause}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return json.loads(response.read())
        except (OSError, ValueError):
            return None


SETTLE_S = 0.5  # new controls take a few frames to reach the sensor


class LadderRunner:  # pylint: disable=too-many-instance-attributes
    """Walks the ladder: sets each rung on the kiosk, checks it, and verdicts each swing."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        state: LadderState,
        client,
        *,
        run_dir,
        black_floor,
        gain_at_300,
        light_index,
        photo_dir: Path,
        on_mode_done,
        ready_timeout_s: float = 90.0,
        expected_ball=None,
        gain_basis=None,
    ):
        self.state = state
        self.client = client
        self._run_dir = run_dir
        self._black_floor = black_floor
        self._gain_at_300 = gain_at_300
        self._light_index = light_index
        # arm id -> where the setup saw the ball in that mode, or None
        self._expected_ball = expected_ball or (lambda _arm_id: None)
        # arm id -> the setup's ball lock a rung's gain starts from, or None (P7-12)
        self._gain_basis = gain_basis or (lambda _arm_id: None)
        self.photo_dir = photo_dir
        self._on_mode_done = on_mode_done
        self.ready_timeout_s = ready_timeout_s
        self.last_verdict: dict | None = None
        # the mode whose kiosk is running; None means any (a lone runner in tests).
        # Between modes it names no rung, so nothing is set on a kiosk shutting down.
        self.mode: str | None = None
        self._last_capture: str | None = None
        self._configured_rung: str | None = None
        # rungs the tester pressed away from: a swing taken at their controls just
        # before the press still counts for them (P7-13)
        self._left: list[dict] = []
        # a press into the other mode, waiting for swings in flight before the kiosk restarts
        self._mode_switch: dict | None = None
        self._required_config_hash: str | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def ready_hold(self) -> str | None:
        """Why a swing now would not count toward the ladder, for the ready light
        (P7-14); None once the current rung's controls are set and checked.

        Reads the state without the runner's lock, which the light check holds.
        """
        data = self.state.to_dict()
        if self.stopped:
            finished = data["current"] is None and data.get("pending_photo") is None
            return "ladder finished" if finished else "ladder stopped"
        if self.mode == "between modes":
            return "kiosk restarting between settings"
        if data.get("pending_photo") is not None:
            return "photo of the club face owed: do not swing"
        if data["current"] is None:
            return "ladder finished"
        if self._configured_rung != data["current"]:
            return "ladder light check running"
        return None

    def _wait_ready(self) -> bool:
        deadline = time.monotonic() + self.ready_timeout_s
        while not self.stopped:
            readiness = self._setup_readiness()
            if readiness["ready"]:
                return not self.stopped
            if time.monotonic() > deadline:
                raise RuntimeError("the kiosk's camera did not come up")
            self._stop.wait(0.2 if self.ready_timeout_s < 5 else 1.0)
        return False

    def _setup_readiness(self) -> dict:
        if hasattr(self.client, "setup_readiness"):
            readiness = self.client.setup_readiness()
            if isinstance(readiness, dict) and isinstance(readiness.get("ready"), bool):
                expected_run = self._run_dir()
                if readiness.get("config_hash") and expected_run is not None:
                    observations = readiness.get("observations")
                    runtime = (
                        observations.get("runtime") if isinstance(observations, dict) else None
                    )
                    actual_run = runtime.get("run_dir") if isinstance(runtime, dict) else None
                    session_uuid = (
                        runtime.get("session_uuid") if isinstance(runtime, dict) else None
                    )
                    try:
                        matches = Path(str(actual_run)).resolve() == expected_run.resolve()
                    except (OSError, ValueError):
                        matches = False
                    if not matches or not isinstance(session_uuid, str) or not session_uuid:
                        return {
                            **readiness,
                            "ready": False,
                            "blockers": [
                                *(readiness.get("blockers") or []),
                                {
                                    "id": "runtime_identity",
                                    "reason": "the kiosk responder is not the expected capture run",
                                },
                            ],
                        }
                return readiness
            return {"ready": False, "blockers": [{"id": "kiosk", "reason": "invalid readiness"}]}
        return {"ready": bool(self.client.ready()), "checks": [], "blockers": []}

    def _status(self, rung: Rung) -> str:
        return self.state.to_dict()["rungs"][rung.rung_id]["status"]

    def _pending_photo(self) -> dict | None:
        return self.state.to_dict().get("pending_photo")

    def _finish_mode(self, arm_id: str) -> None:
        if arm_id == "arm5" and self._pending_photo() is not None:
            return
        self._on_mode_done(arm_id)

    def _prepare_pending_photo(self) -> bool:
        target = self._pending_photo()
        if target is None:
            return False
        rung = next(rung for rung in LADDER if rung.rung_id == target["rung_id"])
        if self.mode not in (None, rung.arm_id) or not self._wait_ready():
            return True
        self.client.set_controls(rung.exposure_us, self.state.gain(rung.rung_id))
        if not self._stop.wait(SETTLE_S):
            self._configured_rung = rung.rung_id
        return True

    def start_rung(self) -> dict | None:
        """Set the current rung, or skip on to the next that can work, within this mode."""
        with self._lock:
            if self._prepare_pending_photo():
                return None
            rung = self.state.current
            if rung is None or self.mode not in (None, rung.arm_id) or not self._wait_ready():
                return None
            while not self.stopped:
                rung = self.state.current
                if rung is None or self.mode not in (None, rung.arm_id):
                    return None
                if self._status(rung) == "active":
                    gain = self.state.gain(rung.rung_id)
                    self.client.set_controls(rung.exposure_us, gain)
                    if self._stop.wait(SETTLE_S):
                        return None
                    # Resumed after a Stop or a restart: the light may have changed
                    # since the rung's own check, so it is checked again (T14).
                    check = self._pre_check(
                        rung, gain, self._black_floor(rung.arm_id), self._expected_ball(rung.arm_id)
                    )
                    if self.stopped:
                        return None
                    if not check["ok"] and not self.state.runs_anyway(rung.rung_id):
                        self.state.restart(rung.rung_id, check)
                        continue
                    # a pressed rung keeps its swings and runs on with a warning (P7-13)
                    self.state.record_resume_check(rung.rung_id, check)
                    self._configured_rung = rung.rung_id
                    return self.state.to_dict()["rungs"][rung.rung_id]
                basis = self._gain_basis(rung.arm_id)
                gain, basis = starting_gain(
                    rung.exposure_us,
                    self._gain_at_300(rung.arm_id) if basis is None else None,
                    basis,
                )
                self.client.set_controls(rung.exposure_us, gain)
                if self._stop.wait(SETTLE_S):
                    return None
                black = self._black_floor(rung.arm_id)
                expected = self._expected_ball(rung.arm_id)
                check = self._pre_check(rung, gain, black, expected)
                for _ in range(MAX_GAIN_CORRECTIONS):
                    suggested = check.get("suggested_gain")
                    if check["ok"] or suggested is None:
                        break
                    corrected = round(max(GAIN_FLOOR, min(GAIN_CEILING, suggested)), 3)
                    if abs(corrected - gain) < 0.05:
                        break  # at unity or the ceiling: this exposure cannot be fixed
                    gain = corrected
                    self.client.set_controls(rung.exposure_us, gain)
                    if self._stop.wait(SETTLE_S):
                        return None
                    check = self._pre_check(rung, gain, black, expected)
                if self.stopped:
                    return None
                self.state.begin(rung.rung_id, gain, check, basis)
                if self._status(rung) == "active":  # passed, or pressed and run anyway
                    self._configured_rung = rung.rung_id
                    return self.state.to_dict()["rungs"][rung.rung_id]
                following = self.state.current
                if following is None or following.arm_id != rung.arm_id:
                    self._finish_mode(rung.arm_id)
                    return None
        return None

    def _frames_at(self, exposure_us: int, gain: float, count: int) -> tuple[np.ndarray, dict]:
        """Frames the sensor took at these controls, waiting up to a second for them.

        Frames read too soon after a change were judged at the old controls
        (wiring audit T7). Raises when too few arrive in time; ``tick`` shows it and
        tries again.
        """
        needed = min(count, MIN_MATCHED_FRAMES)
        deadline = time.monotonic() + CONTROLS_WAIT_S
        while True:
            taken = self.client.frames_with_controls(count)
            applied = list(zip(taken["exposure_us"], taken["gain"]))
            matched = [
                index
                for index, (exposure, applied_gain) in enumerate(applied)
                if exposure_matches(exposure, exposure_us) and gain_matches(applied_gain, gain)
            ]
            if len(matched) == len(applied) or (
                len(matched) >= needed and time.monotonic() >= deadline
            ):
                frames = np.asarray(taken["frames"])[matched]
                return frames, {
                    "exposure_us": float(np.median([applied[i][0] for i in matched])),
                    "gain": float(np.median([applied[i][1] for i in matched])),
                    "frames": len(matched),
                }
            if time.monotonic() >= deadline:
                last_exposure, last_gain = applied[-1] if applied else (None, None)
                raise RuntimeError(
                    f"the camera did not apply {exposure_us} us x {gain:.2f} within "
                    f"{CONTROLS_WAIT_S:.0f} s (last frame {last_exposure} us x {last_gain})"
                )
            if self._stop.wait(0.05):
                raise RuntimeError("the ladder is stopped")

    def _pre_check(self, rung: Rung, gain: float, black: float, expected: dict | None) -> dict:
        frames, applied = self._frames_at(rung.exposure_us, gain, 5)
        check = pre_rung_check(frames, black, gain, expected_ball=expected)
        check["applied_controls"] = applied
        return check

    def poll_once(self) -> list[dict]:
        """Verdict every complete capture not yet seen, and move on when a rung finishes."""
        with self._lock:
            return self._poll_once()

    def _poll_once(self) -> list[dict]:
        if self.stopped:
            return []
        run_dir = self._run_dir()
        current = self._set_rung()
        left = self._left_rungs()
        # a swing only counts against a rung whose exposure is set, or was just before a press
        if current is None and not left:
            return []
        owner = current or left[0]  # what a capture set aside is filed under
        if run_dir is None or not run_dir.exists():
            return []
        readiness = self._setup_readiness()
        if readiness.get("ready") and isinstance(readiness.get("config_hash"), str):
            self._required_config_hash = readiness["config_hash"]
        if not readiness["ready"]:
            self.last_verdict = {
                "color": "red",
                "reasons": ["setup readiness blocked; waiting for immutable capture evidence"],
                "capture": None,
                "setup_readiness": readiness,
            }
            if self._required_config_hash is None:
                return []
        seen = self.state.seen_captures()
        verdicts = []
        for metadata in sorted(run_dir.rglob("camera_*/metadata.json")):
            folder = metadata.parent
            if folder.name in seen or not (folder / "frames.npz").is_file():
                continue
            if self._required_config_hash:
                try:
                    capture_metadata = json.loads(metadata.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    capture_metadata = {}
                trigger_setup = capture_metadata.get("tester_setup")
                valid_trigger = bool(
                    isinstance(trigger_setup, dict)
                    and trigger_setup.get("required") is True
                    and trigger_setup.get("ready") is True
                    and trigger_setup.get("config_hash") == self._required_config_hash
                )
                if not valid_trigger:
                    evidence = trigger_setup if isinstance(trigger_setup, dict) else {}
                    self.state.record_ineligible_capture(
                        folder.name,
                        owner.rung_id,
                        {
                            **evidence,
                            "ready": False,
                            "blockers": evidence.get("blockers")
                            or [
                                {
                                    "id": "trigger_evidence",
                                    "reason": "capture has no matching eligible trigger evidence",
                                }
                            ],
                        },
                    )
                    continue
                paired = evaluate_paired_capture(run_dir, folder)
                if paired["status"] == "pending":
                    continue
                no_shot = next(
                    (item for item in paired["blockers"] if item["id"] == "no_radar_shot"), None
                )
                if no_shot is not None:
                    # a real swing, perhaps, that the OPS243 never logged (P7-10)
                    self.state.record_ineligible_capture(
                        folder.name,
                        owner.rung_id,
                        {
                            "ready": False,
                            "config_hash": self._required_config_hash,
                            "checks": paired["checks"],
                            "blockers": paired["blockers"],
                        },
                        reason=no_shot["reason"],
                    )
                    continue
                observations = trigger_setup.get("observations")
                runtime_identity = (
                    observations.get("runtime") if isinstance(observations, dict) else None
                )
                trigger_session = (
                    runtime_identity.get("session_uuid")
                    if isinstance(runtime_identity, dict)
                    else None
                )
                trigger_run = (
                    runtime_identity.get("run_dir") if isinstance(runtime_identity, dict) else None
                )
                try:
                    same_run = Path(str(trigger_run)).resolve() == run_dir.resolve()
                except (OSError, ValueError):
                    same_run = False
                if paired["session_uuid"] != trigger_session or not same_run:
                    paired = {
                        **paired,
                        "status": "ineligible",
                        "blockers": [
                            *paired["blockers"],
                            {
                                "id": "session_identity",
                                "reason": "paired evidence is not from the trigger's runtime session",
                            },
                        ],
                    }
                if paired["status"] == "ineligible":
                    self.state.record_ineligible_capture(
                        folder.name,
                        owner.rung_id,
                        {
                            "ready": False,
                            "config_hash": self._required_config_hash,
                            "checks": paired["checks"],
                            "blockers": paired["blockers"],
                            "session_uuid": paired["session_uuid"],
                            "shot_number": paired["shot_number"],
                        },
                    )
                    continue
            try:
                trigger_metadata = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                trigger_metadata = {}
            rung = self._swing_rung(trigger_metadata)
            if self.stopped:
                break
            if rung is None:
                continue  # it waits for the pressed setting to be set
            mismatch = trigger_controls_mismatch(
                trigger_metadata, rung, self.state.gain(rung.rung_id)
            )
            if mismatch is not None:
                self.state.record_ineligible_capture(
                    folder.name,
                    rung.rung_id,
                    {"trigger_controls": trigger_metadata.get("auto_exposure")},
                    reason=mismatch,
                )
                continue
            rungs = self.state.to_dict()["rungs"]
            previous = [s["ball"] for s in rungs[rung.rung_id]["swings"] if s.get("ball")]
            verdict = swing_verdict(
                folder,
                rung,
                self.state.gain(rung.rung_id),
                self._black_floor(rung.arm_id),
                previous,
                self._expected_ball(rung.arm_id),
            )
            if self.stopped:
                break
            status = self.state.record_swing(verdict, rung.rung_id)
            self.last_verdict = {**verdict, "rung_id": rung.rung_id}
            self._last_capture = folder.name
            verdicts.append(verdict)
            # a swing for a rung pressed away from moves nothing on
            if status in ("done", "failed") and rung.rung_id == self._configured_rung:
                following = self.state.current
                if following is None or following.arm_id != rung.arm_id:
                    self._finish_mode(rung.arm_id)
                    break
                self.start_rung()
        return verdicts

    def _set_rung(self) -> Rung | None:
        """The current rung when its exposure is set on this mode's kiosk, else None."""
        current = self.state.current
        if (
            current is None
            or self._status(current) != "active"
            or self._configured_rung != current.rung_id
            or self.mode not in (None, current.arm_id)
        ):
            return None
        return current

    def _left_rungs(self) -> list[Rung]:
        """Rungs pressed away from on this mode's kiosk, newest first, still in their wait."""
        now = time.monotonic()
        rungs = [find_rung(item["rung_id"]) for item in reversed(self._left) if item["until"] > now]
        return [rung for rung in rungs if self.mode in (None, rung.arm_id)]

    def _swing_rung(self, trigger_metadata: dict) -> Rung | None:
        """The rung a capture counts for: one pressed away from if it was taken at its
        controls, else the current rung if set, else None until it is (P7-13)."""
        left = self._left_rungs()
        if isinstance(trigger_metadata.get("auto_exposure"), dict):
            for rung in left:
                gain = self.state.gain(rung.rung_id)
                if trigger_controls_mismatch(trigger_metadata, rung, gain) is None:
                    return rung
        current = self._set_rung()
        if current is None and self._mode_switch is not None and left:
            return left[0]  # this kiosk is closing: set aside against the setting left
        return current

    def _unsaved_captures(self) -> bool:
        """Whether a capture in this run is still being saved or waits to be judged."""
        run_dir = self._run_dir()
        if run_dir is None or not run_dir.exists():
            return False
        seen = self.state.seen_captures()
        return any(
            folder.is_dir() and folder.name not in seen for folder in run_dir.rglob("camera_*")
        )

    def jump(self, rung_id: str) -> None:
        """Switch to the setting the tester pressed, now (P7-13, D12).

        Waits for a swing being judged. A swing taken at the old setting's controls
        still counts for it after the press. Within a mode only the controls change;
        a press into the other mode restarts the kiosk once the swings in flight
        are judged (``tick``).
        """
        with self._lock:
            if self.stopped:
                raise RuntimeError("the ladder is stopped")
            if self.mode == "between modes":
                raise RuntimeError(
                    "the ladder is changing camera mode; press the setting again in a moment"
                )
            left = self._configured_rung
            running = (
                self.mode if self.mode in RUNG_FPS else (find_rung(left).arm_id if left else None)
            )
            self.state.jump(rung_id)
            if left == rung_id:
                return  # the setting already running
            now = time.monotonic()
            if left is not None:
                self._left = [
                    item for item in self._left if item["rung_id"] != left and item["until"] > now
                ]
                self._left.append({"rung_id": left, "until": now + SWITCH_WAIT_S})
            self._configured_rung = None
            target = self._pending_photo() or {"rung_id": self.state.current.rung_id}
            wanted = find_rung(target["rung_id"]).arm_id
            if running is None or wanted == running:
                self._mode_switch = None  # pressed back into the running mode
            elif self._mode_switch is None:
                self._mode_switch = {"arm_id": running, "since": now, "until": now + SWITCH_WAIT_S}

    def _hand_over_mode(self) -> None:
        """Restart into the pressed setting's mode once no swing is in flight."""
        switch = self._mode_switch
        if switch is None or self.stopped:
            return
        self._poll_once()
        now = time.monotonic()
        if now < switch["since"] + SWITCH_GRACE_S:
            return  # a swing just triggered may not be in a folder yet
        if self._unsaved_captures() and now < switch["until"]:
            return
        self._mode_switch = None
        self._finish_mode(switch["arm_id"])

    def photograph(self, capture: str, rung_id: str) -> Path:
        """A still of the club face, saved against the last swing; the rung is restored after."""
        with self._lock:
            if self.stopped:
                raise RuntimeError("the ladder is stopped")
            target = self._pending_photo() or self.state.to_dict().get("photo_target")
            if target != {"capture": capture, "rung_id": rung_id}:
                raise RuntimeError("that capture is no longer the current photo target")
            rung = next((item for item in LADDER if item.rung_id == rung_id), None)
            if rung is None or not rung.photos or self.mode not in (None, rung.arm_id):
                raise RuntimeError("impact photos are taken on the 1280x800 rungs")
            configured = next(
                (item for item in LADDER if item.rung_id == self._configured_rung), None
            )
            if configured is None or configured.arm_id != rung.arm_id:
                raise RuntimeError("the pending photo camera is not ready")
            light = self._light_index(rung.arm_id)
            still, still_gain = photo_controls(
                float(light) if light is not None else None,
                self._black_floor(rung.arm_id),
                RUNG_FPS[rung.arm_id],
                rung_exposure_us=configured.exposure_us,
                rung_gain=self.state.gain(configured.rung_id),
            )
            try:
                self.client.set_controls(still, still_gain, purpose="still_photo")
                if self._stop.wait(SETTLE_S):
                    raise RuntimeError("the ladder is stopped")
                image = self._frames_at(still, still_gain, 1)[0][0]
                if self.stopped:
                    raise RuntimeError("the ladder is stopped")
            finally:
                # Restore even after Stop: a still-photo exposure left on the kiosk
                # would be applied to the next swing it captures.
                try:
                    self.client.set_controls(
                        configured.exposure_us, self.state.gain(configured.rung_id)
                    )
                except OSError:
                    if not self.stopped:
                        raise
            if self.stopped:
                raise RuntimeError("the ladder is stopped")
            name = capture
            self.photo_dir.mkdir(parents=True, exist_ok=True)
            path = self.photo_dir / f"{name}.pgm"
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".tmp", dir=self.photo_dir
                ) as handle:
                    temporary = Path(handle.name)
                    handle.write(f"P5\n{image.shape[1]} {image.shape[0]}\n255\n".encode("ascii"))
                    handle.write(np.asarray(image, dtype=np.uint8).tobytes())
                if self.stopped:
                    raise RuntimeError("the ladder is stopped")
                os.replace(temporary, path)
                temporary = None
                # relative to the tester folder, so ladder.json stays valid off the Pi
                recorded = path.relative_to(self.photo_dir.parent).as_posix()
                pending = self._pending_photo() is not None
                if pending:
                    self.state.finish_photo(name, rung_id, recorded)
                    self._on_mode_done(rung.arm_id)
                else:
                    self.state.record_photo(name, rung_id, recorded)
                return path
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def skip_photo(self, capture: str, rung_id: str) -> None:
        with self._lock:
            if self.stopped:
                raise RuntimeError("the ladder is stopped")
            self.state.skip_photo(capture, rung_id)
            rung = next(item for item in LADDER if item.rung_id == rung_id)
            self._on_mode_done(rung.arm_id)

    def wait_for_photo_action(self) -> None:
        """Wait until a photo or skip request has left its serialized state transition."""
        with self._lock:
            return

    def start(self) -> None:
        if self.stopped or (self._thread is not None and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="study-ladder")
        self._thread.start()

    def stop(self, *, wait: bool = True) -> None:
        self._stop.set()
        if wait and self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def tick(self) -> None:
        """One step: set the rung if it is not set yet (a slow kiosk is retried), else verdict."""
        try:
            if self._mode_switch is not None:
                with self._lock:
                    self._hand_over_mode()
                return
            if self._pending_photo() is not None:
                if self._left_rungs():
                    self.poll_once()  # swings taken just before a press, then the photo
                target = self._pending_photo()
                if target is not None and self._configured_rung != target["rung_id"]:
                    self.start_rung()
                return
            rung = self.state.current
            if self.stopped or rung is None or self.mode not in (None, rung.arm_id):
                return
            if self._status(rung) != "active" or self._configured_rung != rung.rung_id:
                self.start_rung()
            else:
                self.poll_once()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # nobody watches the logs on the Pi: every failure reaches the page
            if not self.stopped:
                self.last_verdict = {"color": "red", "reasons": [f"ladder: {exc}"], "capture": None}

    def _loop(self) -> None:
        self.tick()
        while not self._stop.wait(1.0):
            self.tick()
