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
DARK_LIGHT_CAUSES = frozenset({"ball_dark", "zone_dark"})
BRIGHT_LIGHT_CAUSES = frozenset({"ball_clipped", "zone_clipped_no_ball"})


@dataclass(frozen=True)
class Rung:
    """One exposure on one readout mode; ``photos`` asks for impact photos."""

    rung_id: str
    arm_id: str
    exposure_us: int
    photos: bool


# 50 and 30 us are for sunlight (outdoors 29 Sept a ball in sun clipped at 50 us x 1
# during setup); indoors they are skipped with the rest once 75 us is too dark. The
# sensor's shortest exposure is 9 us (one row) at 1280x800.
LADDER: tuple[Rung, ...] = tuple(
    [Rung(f"full-{e}", "arm5", e, True) for e in (300, 200, 150, 100, 75, 50, 30)]
    + [Rung(f"half-{e}", "arm6", e, False) for e in (300, 150, 75)]
)
RUNG_FPS = {"arm5": 120.0, "arm6": 288.0}


def rung_gain(gain_at_300: float, exposure_us: int) -> float:
    """The gain that keeps the gain screen's brightness at this exposure, within the sensor.

    In sunlight the light-equivalent gain at 300 us is below unity; a short rung
    then still gets a real gain, and a long rung sits at unity and may be too bright.
    """
    return round(max(GAIN_FLOOR, min(GAIN_CEILING, gain_at_300 * 300.0 / exposure_us)), 3)


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


def ball_light(
    frames: np.ndarray, black_floor: float, expected_ball: dict | None = None
) -> dict | None:
    """The resting ball's brightness in these frames, if the ball can be found.

    ``expected_ball`` (x, y, diameter_px) is where the setup saw the ball in this
    mode. With it, only a ball of that size near that spot counts, so a shadow
    or a sun patch is not taken for it.
    """
    stack = np.clip(np.asarray(frames), 0, 255).astype(np.uint8)
    try:
        found = detect_reference_ball(stack)
    except (RuntimeError, ValueError):
        return None
    if expected_ball is not None:
        # The detector's strict lit-sphere mode (given a size) refuses sunlit or
        # half-shaded balls, so the result is checked against the setup instead.
        diameter = float(expected_ball["diameter_px"])
        reach = max(6.0 * diameter, 60.0)
        if not (
            0.6 * diameter <= found.diameter_px <= 1.6 * diameter
            and abs(found.x - float(expected_ball["x"])) <= reach
            and abs(found.y - float(expected_ball["y"])) <= max(4.0 * diameter, 40.0)
        ):
            return None
    image = np.median(stack, axis=0)
    yy, xx = np.indices(image.shape)
    core = image[np.hypot(xx - found.x, yy - found.y) <= 0.8 * found.diameter_px / 2.0]
    if not core.size:
        return None
    median = float(np.median(core))
    return {
        "x": float(found.x),
        "y": float(found.y),
        "diameter_px": float(found.diameter_px),
        "median_dn": median,
        "signal_dn": median - black_floor,
        "clipped_pct": float(np.mean(core >= 250) * 100.0),
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
    """
    frames = np.asarray(frames)
    light = judge_light(frames, black_floor, expected_ball)
    cause = light["cause"]
    red = cause in RED_LIGHT_CAUSES
    return {
        **light["zone"],
        "noise_dn": _zone_noise(frames),
        "judged_on": "ball" if light["ball"] is not None else "hitting_zone",
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
    resting = frames[: max(3, min(RESTING_FRAMES, len(frames)))]
    light = judge_light(resting, black_floor, expected_ball)
    stats = light["zone"]
    lit = light["ball"]
    if light["cause"] in RED_LIGHT_CAUSES:
        red.append(f"light: {light['message']}")
    elif light["message"]:
        amber.append(light["message"])
    ball = None
    if lit is None:
        amber.append("resting ball not found in the pre-impact frames")
    else:
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
        """The active rung, else the next pending one, else None at the end."""
        for status in ("active", "pending"):
            for rung in LADDER:
                if self._data["rungs"][rung.rung_id]["status"] == status:
                    return rung
        return None

    def gain(self, rung_id: str) -> float | None:
        return self._data["rungs"][rung_id]["gain"]

    def accepted(self, rung_id: str) -> int:
        swings = self._data["rungs"][rung_id]["swings"]
        return sum(1 for swing in swings if swing["color"] in ("green", "amber"))

    def seen_captures(self) -> set[str]:
        rungs = self._data["rungs"].values()
        return {
            *{swing["capture"] for rung in rungs for swing in rung["swings"]},
            *{item["capture"] for item in self._data["ineligible_captures"]},
        }

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

    def begin(self, rung_id: str, gain: float, check: dict) -> None:
        rung = next(r for r in LADDER if r.rung_id == rung_id)
        entry = self._data["rungs"][rung_id]
        entry["gain"] = gain
        entry["pre_check"] = check
        if check.get("ok"):
            entry["status"] = "active"
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

    def record_swing(self, verdict: dict) -> str:
        rung = self.current
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
        first = entry["swings"][:EARLY_EXIT_SWINGS]
        reds = [swing for swing in first if swing["color"] == "red"]
        if len(reds) >= EARLY_EXIT_REDS:
            reason = f"{len(reds)} of the first {len(first)} swings red"
            if any(swing.get("light_cause") in DARK_LIGHT_CAUSES for swing in reds):
                # a shorter exposure is darker still
                self._skip_from(rung, "failed", reason)
            else:
                entry["status"] = "failed"
                entry["reason"] = reason
        elif self.accepted(rung.rung_id) >= SWINGS_PER_RUNG:
            entry["status"] = "done"
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
    ):
        self.state = state
        self.client = client
        self._run_dir = run_dir
        self._black_floor = black_floor
        self._gain_at_300 = gain_at_300
        self._light_index = light_index
        # arm id -> where the setup saw the ball in that mode, or None
        self._expected_ball = expected_ball or (lambda _arm_id: None)
        self.photo_dir = photo_dir
        self._on_mode_done = on_mode_done
        self.ready_timeout_s = ready_timeout_s
        self.last_verdict: dict | None = None
        # the mode whose kiosk is running; None means any (a lone runner in tests).
        # Between modes it names no rung, so nothing is set on a kiosk shutting down.
        self.mode: str | None = None
        self._last_capture: str | None = None
        self._configured_rung: str | None = None
        self._required_config_hash: str | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

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
                    self.client.set_controls(rung.exposure_us, self.state.gain(rung.rung_id))
                    if self._stop.wait(SETTLE_S):
                        return None
                    self._configured_rung = rung.rung_id
                    return self.state.to_dict()["rungs"][rung.rung_id]
                gain = rung_gain(self._gain_at_300(rung.arm_id), rung.exposure_us)
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
                self.state.begin(rung.rung_id, gain, check)
                if check["ok"]:
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
        current = self.state.current
        # a swing only counts against a rung whose exposure is set
        if (
            current is None
            or self._status(current) != "active"
            or self._configured_rung != current.rung_id
            or self.mode not in (None, current.arm_id)
        ):
            return []
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
                        current.rung_id,
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
                        current.rung_id,
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
            rung = self.state.current
            if self.stopped or rung is None or self._status(rung) != "active":
                break
            try:
                trigger_metadata = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                trigger_metadata = {}
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
            status = self.state.record_swing(verdict)
            self.last_verdict = {**verdict, "rung_id": rung.rung_id}
            self._last_capture = folder.name
            verdicts.append(verdict)
            if status in ("done", "failed"):
                following = self.state.current
                if following is None or following.arm_id != rung.arm_id:
                    self._finish_mode(rung.arm_id)
                    break
                self.start_rung()
        return verdicts

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
            if self._pending_photo() is not None:
                if self._configured_rung != self._pending_photo()["rung_id"]:
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
