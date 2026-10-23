"""The tester's lens choice and each lens's settings, kept with the unit's local settings.

The choice and each lens's camera modes, default mode and exposure ladder live in
``~/.config/openflight/``, never in the shared rig file.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .lens_profiles import (
    ARM_SIZES,
    LensCatalogue,
    LensProfile,
    active_lens,
    validate_settings,
)

UNIT_SCHEMA = "openflight.unit_camera_lens.v1"


UNIT_FILE_NAME = "camera-lens.json"


def default_unit_settings_path() -> Path:
    """Where this unit keeps its lens choice: with its other local settings."""
    return Path.home() / ".config" / "openflight" / UNIT_FILE_NAME


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
