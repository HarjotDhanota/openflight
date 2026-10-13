"""The lens on the unit's camera: its profile, its focal length per mode, and its settings.

The tester picks the lens by touch (stock 2.8 mm, 6 mm or 8 mm). The profiles
live in ``config/lenses.json``; the choice and each lens's own settings (which
camera modes it uses, the default mode, and the exposure ladder per mode) are the
unit's, kept with its other local settings (``~/.config/openflight/``), never in
the shared rig file.

**Focal length.** The rig file's ``focal_px`` describes the reference (stock)
lens. Another lens's *nominal* focal length is the rig's scaled by
``focal_mm / reference_focal_mm``, per mode by the binning ratio as before
(``ball_pixels.mode_focal_px``). When a checkerboard calibration artifact for the
lens exists (``optical_calibration.calibrate_checkerboard``, its
``mode_profile.lens_id``), its fitted focal length replaces the nominal one and
the lens reports ``intrinsics: calibrated``; otherwise ``nominal``, and every
camera-derived number says so in its label.

**What the lens drives.** Every camera calculation takes its focal length from
the selected lens: the placement box, the ball's size-to-distance, the camera
tilt solve (stored per lens), the aim yaw, and the kiosk's camera metrics
(``--camera-lens-id``). A lens never chosen leaves ``lens_id`` null; the
calculations then use the rig file's own lens (the reference) and say so.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "openflight.camera_lenses.v1"
UNIT_SCHEMA = "openflight.unit_camera_lens.v1"
UNIT_FILE_NAME = "camera-lens.json"
DEFAULT_LENSES_PATH = Path(__file__).resolve().parents[3] / "config" / "lenses.json"
# the ladder's camera modes, and their output sizes
ARM_SIZES = {"arm5": (1280, 800), "arm6": (640, 400)}
NOT_SELECTED_BASIS = "not_selected_rig_file_lens"
SELECTED_BASIS = "selected_by_the_tester"


def default_unit_settings_path() -> Path:
    """Where this unit keeps its lens choice: with its other local settings."""
    return Path.home() / ".config" / "openflight" / UNIT_FILE_NAME


def default_calibration_dir() -> Path:
    """Where this unit keeps checkerboard calibration artifacts, one or more per lens."""
    return Path.home() / ".config" / "openflight" / "optical-calibration"


def _finite_or_none(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number or null")
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    return float(value)


@dataclass(frozen=True)
class LensProfile:  # pylint: disable=too-many-instance-attributes
    """One lens the unit can carry."""

    lens_id: str
    label: str
    focal_mm: float
    f_number: float | None
    hfov_deg: float | None
    hfov_source: str
    tv_distortion_pct: float | None
    provenance: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LensProfile":
        lens_id = str(payload["lens_id"]).strip()
        if not lens_id:
            raise ValueError("a lens needs a lens_id")
        focal = _finite_or_none(payload.get("focal_mm"), "focal_mm")
        if focal is None or focal <= 0.0:
            raise ValueError(f"lens {lens_id} needs a positive focal_mm")
        return cls(
            lens_id=lens_id,
            label=str(payload.get("label") or lens_id),
            focal_mm=focal,
            f_number=_finite_or_none(payload.get("f_number"), "f_number"),
            hfov_deg=_finite_or_none(payload.get("hfov_deg"), "hfov_deg"),
            hfov_source=str(payload.get("hfov_source") or "unknown"),
            tv_distortion_pct=_finite_or_none(
                payload.get("tv_distortion_pct"), "tv_distortion_pct"
            ),
            provenance=str(payload.get("provenance") or ""),
        )


@dataclass(frozen=True)
class LensCatalogue:
    """Every lens profile, the reference lens the rig file describes, and the mode rules."""

    lenses: tuple[LensProfile, ...]
    reference_lens_id: str
    reference_focal_mm: float
    allowed_ladder_us: Mapping[str, tuple[int, ...]]
    default_settings: Mapping[str, Any]
    sha256: str

    def get(self, lens_id: str | None) -> LensProfile:
        """The lens with this id; None is the reference lens (the rig file's)."""
        wanted = self.reference_lens_id if lens_id is None else lens_id
        for lens in self.lenses:
            if lens.lens_id == wanted:
                return lens
        raise ValueError(f"unknown lens: {lens_id}")

    def ids(self) -> list[str]:
        return [lens.lens_id for lens in self.lenses]

    def focal_scale(self, lens_id: str | None) -> float:
        """The lens's nominal focal length over the reference lens's."""
        return self.get(lens_id).focal_mm / self.reference_focal_mm


def load_catalogue(path: Path | None = None) -> LensCatalogue:
    """The lens profiles from ``config/lenses.json`` (or ``path``), validated."""
    import hashlib  # noqa: PLC0415

    source = Path(path) if path is not None else DEFAULT_LENSES_PATH
    raw = source.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema") != SCHEMA:
        raise ValueError(f"{source} is not an {SCHEMA} file")
    lenses = tuple(LensProfile.from_dict(item) for item in payload.get("lenses") or [])
    if not lenses:
        raise ValueError(f"{source} lists no lens")
    if len({lens.lens_id for lens in lenses}) != len(lenses):
        raise ValueError(f"{source} repeats a lens_id")
    reference = str(payload.get("reference_lens_id") or "")
    if reference not in {lens.lens_id for lens in lenses}:
        raise ValueError(f"{source}'s reference_lens_id is not one of its lenses")
    reference_focal = _finite_or_none(payload.get("reference_focal_mm"), "reference_focal_mm")
    if reference_focal is None or reference_focal <= 0.0:
        raise ValueError(f"{source} needs a positive reference_focal_mm")
    modes = payload.get("modes") or {}
    allowed = {
        arm_id: tuple(
            int(value) for value in (modes.get(arm_id) or {}).get("allowed_ladder_us", [])
        )
        for arm_id in ARM_SIZES
    }
    if not all(allowed.values()):
        raise ValueError(f"{source} needs allowed_ladder_us for {', '.join(ARM_SIZES)}")
    catalogue = LensCatalogue(
        lenses=lenses,
        reference_lens_id=reference,
        reference_focal_mm=reference_focal,
        allowed_ladder_us=allowed,
        default_settings={},
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    defaults = validate_settings(payload.get("default_settings") or {}, catalogue)
    return LensCatalogue(**{**catalogue.__dict__, "default_settings": defaults})


def validate_settings(settings: Any, catalogue: LensCatalogue) -> dict[str, Any]:
    """A lens's settings, normalized: enabled modes, the default mode, the ladder per mode.

    Ladder rungs must come from the mode's allowed list; they are kept in the
    allowed list's order (longest exposure first), without repeats.
    """
    if not isinstance(settings, Mapping):
        raise ValueError("settings must be an object")
    modes = settings.get("modes")
    if not isinstance(modes, Sequence) or isinstance(modes, (str, bytes)) or not modes:
        raise ValueError("settings.modes must name at least one mode")
    modes = [str(mode) for mode in modes]
    unknown = sorted(set(modes) - set(ARM_SIZES))
    if unknown:
        raise ValueError(f"unknown mode: {', '.join(unknown)}")
    modes = [arm_id for arm_id in ARM_SIZES if arm_id in modes]
    default_arm = str(settings.get("default_arm") or modes[0])
    if default_arm not in modes:
        raise ValueError("settings.default_arm must be one of the enabled modes")
    ladder_in = settings.get("ladder") or {}
    if not isinstance(ladder_in, Mapping):
        raise ValueError("settings.ladder must be an object of mode: [exposure us]")
    ladder: dict[str, list[int]] = {}
    for arm_id in ARM_SIZES:
        allowed = catalogue.allowed_ladder_us[arm_id]
        values = ladder_in.get(arm_id)
        if values is None:
            values = (
                list(allowed)
                if not catalogue.default_settings
                else list(catalogue.default_settings["ladder"][arm_id])
            )
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise ValueError(f"settings.ladder.{arm_id} must be a list of exposures")
        try:
            chosen = {int(value) for value in values}
        except (TypeError, ValueError) as exc:
            raise ValueError(f"settings.ladder.{arm_id} must be whole microseconds") from exc
        refused = sorted(chosen - set(allowed))
        if refused:
            raise ValueError(
                f"settings.ladder.{arm_id}: {', '.join(map(str, refused))} us is not an allowed "
                f"rung (allowed: {', '.join(map(str, allowed))})"
            )
        if arm_id in modes and not chosen:
            raise ValueError(f"settings.ladder.{arm_id} needs at least one exposure")
        ladder[arm_id] = [value for value in allowed if value in chosen]
    return {"modes": modes, "default_arm": default_arm, "ladder": ladder}


def nominal_focal_px_by_arm(rig: Any, catalogue: LensCatalogue, lens_id: str | None) -> dict:
    """The lens's nominal focal length in each ladder mode: the rig's, scaled by focal_mm."""
    from openflight.camera.ball_pixels import mode_focal_px  # noqa: PLC0415

    scale = catalogue.focal_scale(lens_id)
    return {arm_id: mode_focal_px(width, rig) * scale for arm_id, (width, _h) in ARM_SIZES.items()}


def _binning(width: int) -> int:
    from openflight.camera.ball_pixels import binning_factor  # noqa: PLC0415

    return binning_factor(width)


def find_calibration(
    lens_id: str | None, sources: Sequence[Path | None] = ()
) -> dict[str, Any] | None:
    """A checkerboard calibration for this lens, if one is kept: its focal length per mode.

    ``sources`` are artifact files or directories of them. An artifact counts
    when it fitted (``status: candidate``) and its mode profile names this
    ``lens_id``; a mode it was not fitted in takes its focal length by the
    binning ratio. The newest file wins.
    """
    if lens_id is None:
        return None
    files: list[Path] = []
    for source in sources:
        if source is None:
            continue
        source = Path(source)
        if source.is_dir():
            files.extend(sorted(source.glob("*.json")))
        elif source.is_file():
            files.append(source)
    found = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            artifact = payload.get("candidate", payload)
            if artifact.get("status") != "candidate":
                continue
            profile = artifact.get("mode_profile") or {}
            if profile.get("lens_id") != lens_id:
                continue
            saved = profile.get("saved_image") or {}
            width = int(saved["width"])
            focal = float(artifact["camera_matrix"][0][0])
            if not math.isfinite(focal) or focal <= 0.0:
                continue
            found.append((path.stat().st_mtime, path, width, focal, artifact))
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
            continue
    if not found:
        return None
    _mtime, path, width, focal, artifact = max(found, key=lambda item: item[0])
    by_arm = {
        arm_id: focal * _binning(width) / _binning(arm_width)
        for arm_id, (arm_width, _h) in ARM_SIZES.items()
    }
    return {
        "path": str(path),
        "fitted_width_px": width,
        "focal_px_by_arm": by_arm,
        "distortion_coefficients": artifact.get("distortion_coefficients"),
        "mode_profile_sha256": artifact.get("mode_profile_sha256"),
        "validation": (artifact.get("validation") or {}).get("median_view_rms_px")
        if isinstance(artifact.get("validation"), Mapping)
        else None,
    }


@dataclass(frozen=True)
class ActiveLens:
    """The lens every camera calculation uses now, and how sure its focal length is."""

    lens_id: str | None
    profile: LensProfile
    focal_px_by_arm: Mapping[str, float]
    intrinsics: str
    basis: str
    calibration: Mapping[str, Any] | None

    def focal_px(self, width_px: int) -> float:
        """This lens's focal length for an output width (binning by width)."""
        for arm_id, (width, _h) in ARM_SIZES.items():
            if width == int(width_px):
                return float(self.focal_px_by_arm[arm_id])
        # another mode (320x200): it shares 640x400's binning
        return float(self.focal_px_by_arm["arm6"]) * _binning(640) / _binning(int(width_px))

    def label(self) -> str:
        """What a camera-derived number says about the lens it came through."""
        name = self.profile.label
        if self.intrinsics == "calibrated":
            return f"lens {name}, checkerboard intrinsics"
        return f"lens {name}, nominal intrinsics (focal length from the listing, uncalibrated)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "lens_id": self.lens_id,
            "effective_lens_id": self.profile.lens_id,
            "label": self.profile.label,
            "focal_mm": self.profile.focal_mm,
            "f_number": self.profile.f_number,
            "intrinsics": self.intrinsics,
            "basis": self.basis,
            "focal_px_by_arm": {
                key: round(value, 4) for key, value in self.focal_px_by_arm.items()
            },
            "calibration_path": (self.calibration or {}).get("path"),
            "note": self.label(),
        }


def active_lens(
    rig: Any,
    catalogue: LensCatalogue,
    lens_id: str | None,
    calibration_sources: Sequence[Path | None] = (),
) -> ActiveLens:
    """The lens the calculations use: the chosen one, else the rig file's own."""
    profile = catalogue.get(lens_id)
    calibration = find_calibration(profile.lens_id, calibration_sources)
    if calibration is not None:
        by_arm = dict(calibration["focal_px_by_arm"])
        intrinsics = "calibrated"
    else:
        by_arm = nominal_focal_px_by_arm(rig, catalogue, profile.lens_id)
        intrinsics = "nominal"
    return ActiveLens(
        lens_id=lens_id,
        profile=profile,
        focal_px_by_arm=by_arm,
        intrinsics=intrinsics,
        basis=SELECTED_BASIS if lens_id is not None else NOT_SELECTED_BASIS,
        calibration=calibration,
    )


def rig_with_lens(rig: Any, lens: ActiveLens) -> Any:
    """The rig with the lens's focal length in the rig file's own mode."""
    from dataclasses import replace  # noqa: PLC0415

    return replace(rig, focal_px=lens.focal_px(int(rig.image_width)))


def hfov_deg(lens: LensProfile, focal_px_arm5: float) -> float:
    """The listed horizontal field of view, else the nominal pinhole's at 1280 px."""
    if lens.hfov_deg is not None:
        return float(lens.hfov_deg)
    return math.degrees(2.0 * math.atan(ARM_SIZES["arm5"][0] / 2.0 / float(focal_px_arm5)))


def _write(path: Path, payload: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class LensSettingsStore:
    """The unit's lens choice and each lens's settings, on disk (or in memory without a path)."""

    def __init__(self, path: Path | None, catalogue: LensCatalogue):
        self.path = Path(path) if path is not None else None
        self.catalogue = catalogue
        self._lock = threading.Lock()
        self._memory: dict[str, Any] = {}

    def _read(self) -> dict[str, Any]:
        if self.path is None:
            return dict(self._memory)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, Mapping) or payload.get("schema") != UNIT_SCHEMA:
            return {}
        return dict(payload)

    def _save(self, payload: Mapping[str, Any]) -> None:
        payload = {**dict(payload), "schema": UNIT_SCHEMA}
        if self.path is None:
            self._memory = payload
            return
        _write(self.path, payload)

    def selected(self) -> str | None:
        """The lens the tester chose, or None; an id no longer in the catalogue is None."""
        with self._lock:
            lens_id = self._read().get("selected_lens_id")
        return lens_id if lens_id in self.catalogue.ids() else None

    def settings(self, lens_id: str) -> dict[str, Any]:
        """This lens's saved settings, else the catalogue's defaults."""
        self.catalogue.get(lens_id)
        with self._lock:
            saved = (self._read().get("settings") or {}).get(lens_id)
        if isinstance(saved, Mapping):
            try:
                return validate_settings(saved, self.catalogue)
            except ValueError:
                pass
        return {
            "modes": list(self.catalogue.default_settings["modes"]),
            "default_arm": self.catalogue.default_settings["default_arm"],
            "ladder": {
                key: list(value) for key, value in self.catalogue.default_settings["ladder"].items()
            },
        }

    def select(self, lens_id: str) -> tuple[str | None, bool]:
        """Choose a lens; returns the previous choice and whether it changed."""
        self.catalogue.get(lens_id)
        with self._lock:
            payload = self._read()
            previous = payload.get("selected_lens_id")
            changed = previous != lens_id
            if changed:
                history = list(payload.get("history") or [])
                history.append(
                    {
                        "at_utc": datetime.now(timezone.utc).isoformat(),
                        "from": previous,
                        "to": lens_id,
                    }
                )
                payload.update({"selected_lens_id": lens_id, "history": history[-50:]})
                self._save(payload)
        return previous, changed

    def save_settings(self, lens_id: str, settings: Any) -> dict[str, Any]:
        """Validate and keep this lens's settings."""
        self.catalogue.get(lens_id)
        normalized = validate_settings(settings, self.catalogue)
        with self._lock:
            payload = self._read()
            stored = dict(payload.get("settings") or {})
            stored[lens_id] = normalized
            payload["settings"] = stored
            self._save(payload)
        return normalized


def lens_listing(
    rig: Any,
    catalogue: LensCatalogue,
    store: LensSettingsStore,
    calibration_sources: Sequence[Path | None] = (),
) -> list[dict[str, Any]]:
    """Every lens as the page lists it (contract ``GET /api/tester/lens``)."""
    listing = []
    for profile in catalogue.lenses:
        lens = active_lens(rig, catalogue, profile.lens_id, calibration_sources)
        listing.append(
            {
                "lens_id": profile.lens_id,
                "label": profile.label,
                "focal_mm": profile.focal_mm,
                "f_number": profile.f_number,
                "hfov_deg": round(hfov_deg(profile, lens.focal_px_by_arm["arm5"]), 1),
                "hfov_source": profile.hfov_source,
                "tv_distortion_pct": profile.tv_distortion_pct,
                "intrinsics": lens.intrinsics,
                "focal_px_by_arm": {
                    key: round(value, 2) for key, value in lens.focal_px_by_arm.items()
                },
                "settings": store.settings(profile.lens_id),
                "provenance": profile.provenance,
            }
        )
    return listing


def tilt_calibration_path(base: Path | None, lens_id: str | None, catalogue: LensCatalogue):
    """The camera-tilt calibration file for this lens: the reference lens keeps the old name."""
    if base is None:
        return None
    base = Path(base)
    effective = catalogue.get(lens_id).lens_id
    if effective == catalogue.reference_lens_id:
        return base
    return base.with_name(f"{base.stem}-{effective}{base.suffix}")
