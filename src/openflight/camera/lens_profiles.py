"""The lens on the unit's camera: its profile and its focal length per mode.

Profiles live in ``config/lenses.json``. A lens's nominal focal length is the rig file's
(reference lens) scaled by ``focal_mm / reference_focal_mm``; a checkerboard calibration
for the lens replaces it (``intrinsics: calibrated``). With no lens chosen, the rig file's
own lens is used.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "openflight.camera_lenses.v1"
DEFAULT_LENSES_PATH = Path(__file__).resolve().parents[3] / "config" / "lenses.json"
ARM_SIZES = {"arm5": (1280, 800), "arm6": (640, 400)}
NOT_SELECTED_BASIS = "not_selected_rig_file_lens"
SELECTED_BASIS = "selected_by_the_tester"


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
    """A lens's settings, normalized; ladder rungs keep the allowed list's order, no repeats."""
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
    """The newest checkerboard calibration for this lens in ``sources``: focal length per mode."""
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


def _positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def lens_record_focal_px(lens: Mapping[str, Any] | None, width_px: int) -> float | None:
    """A lens record's focal length for a capture this wide; None for another mode."""
    by_arm = lens.get("focal_px_by_arm") if isinstance(lens, Mapping) else None
    if not isinstance(by_arm, Mapping):
        return None
    for arm_id, (width, _height) in ARM_SIZES.items():
        value = by_arm.get(arm_id)
        if width == int(width_px) and _positive_number(value):
            return float(value)
    return None
