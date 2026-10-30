"""The per-unit setup record: box, aim, ball range, net range and scene.

Each section stores the inputs it was confirmed against; evaluate() marks it stale
when they differ from the start context or a section it builds on is stale. A
missing, corrupt or future-schema file reads as all not_set; a save never replaces a
future-schema file, nor one another writer saved since it was loaded. Roll is never an input.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from pathlib import Path

SCHEMA = "openflight.setup.v1"
SECTIONS = ("box", "aim", "ball_range", "net_range", "scene")

_INPUT_KEYS = {
    "box": ("lens_id", "rig_sha256", "tilt_deg"),
    "aim": ("box_id",),
    "ball_range": ("box_id",),
    "net_range": ("floor_line", "lens_id", "tilt_deg", "box_id"),
    # a lens height is solved against the box's ball, with or without a ball range
    "scene": ("box_id",),
}


@dataclass(frozen=True)
class StartContext:
    lens_id: str | None
    rig_sha256: str
    tilt_offset_deg: float | None
    tilt_calibrated: bool


def section_inputs(name: str, **values) -> dict:
    """The ``inputs`` stamp a section stores: only the keys that section depends on."""
    if name not in _INPUT_KEYS:
        raise ValueError(f"unknown setup section {name!r}")
    unknown = set(values) - set(_INPUT_KEYS[name])
    if unknown:
        raise ValueError(f"{name} does not depend on {sorted(unknown)}")
    return dict(values)


class SaveRefused(Exception):
    """The file on disk must not be replaced; the message says why."""


class SetupFileChanged(SaveRefused):
    """Another writer saved the file since this record was loaded."""


def _not_set(reason: str) -> dict:
    record = {"schema": SCHEMA, "revision": 0}
    for name in SECTIONS:
        record[name] = {"state": "not_set", "reason": reason}
    return record


def _read(path: Path) -> tuple[dict | None, str | None]:
    if not path.is_file():
        return None, "no setup file"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "unreadable"
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None, "unknown schema"
    if any(name in data and not isinstance(data[name], dict) for name in SECTIONS):
        return None, "unreadable"
    revision = data.get("revision")
    data["revision"] = revision if isinstance(revision, int) else 0
    return data, None


def load(path: Path) -> dict:
    data, reason = _read(Path(path))
    if data is None:
        return _not_set(reason)
    for name in SECTIONS:
        data.setdefault(name, {"state": "not_set", "reason": "not set"})
    return data


def save(path: Path, record: dict) -> None:
    """Replace the file with ``record`` at the next revision. Refused (``SaveRefused``) over a
    file of a schema this code does not read, or one saved since ``record`` was loaded."""
    path = Path(path)
    base = int(record.get("revision", 0))
    on_disk, reason = _read(path)
    if reason == "unknown schema":
        raise SaveRefused(
            f"the setup file's schema is not {SCHEMA} (a newer kiosk's?): it is left as it is; "
            "move it aside to set up here"
        )
    if on_disk is not None and on_disk["revision"] != base:
        raise SetupFileChanged(
            f"the setup file changed underneath (revision {on_disk['revision']} on disk, "
            f"{base} here)"
        )
    record["schema"] = SCHEMA
    record["revision"] = base + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _changed(label: str, stored, now) -> str | None:
    if stored != now:
        return f"{label} changed ({stored} -> {now})"
    return None


def _tilt_changed(stored, now, margin: float) -> str | None:
    if stored is None and now is None:
        return None
    if stored is None or now is None:
        return f"tilt changed ({stored} -> {now})"
    if abs(stored - now) > margin:
        return f"tilt moved {abs(stored - now):.1f} deg, more than the {margin:g} deg margin"
    return None


def evaluate(record: dict, ctx: StartContext, *, tilt_margin_deg: float) -> dict:
    out = copy.deepcopy(record)

    def usable(name: str) -> bool:
        return out[name].get("state") not in ("not_set", "stale")

    def blocked(name: str) -> str | None:
        """Why a section this one builds on cannot be trusted, or None."""
        if usable(name):
            return None
        reason = out[name].get("reason")
        return f"{name} is {out[name].get('state')}" + (f": {reason}" if reason else "")

    def direct(name: str) -> str | None:
        inputs = out[name].get("inputs")
        if not isinstance(inputs, dict):
            return "no inputs recorded"
        box, aim = out["box"], out["aim"]
        if name == "box":
            return (
                _changed("lens", inputs.get("lens_id"), ctx.lens_id)
                or _changed("rig", inputs.get("rig_sha256"), ctx.rig_sha256)
                or _tilt_changed(inputs.get("tilt_deg"), ctx.tilt_offset_deg, tilt_margin_deg)
            )
        if name in ("aim", "ball_range", "scene"):
            return blocked("box") or _changed("box", inputs.get("box_id"), box.get("id"))
        if name == "net_range":
            return (
                _changed("lens", inputs.get("lens_id"), ctx.lens_id)
                or _tilt_changed(inputs.get("tilt_deg"), ctx.tilt_offset_deg, tilt_margin_deg)
                or blocked("aim")
                or (
                    blocked("box") or _changed("box", inputs.get("box_id"), box.get("id"))
                    if "ball_to_net_m" in out[name]
                    else None
                )
                or _changed("floor line", inputs.get("floor_line"), aim.get("floor_line"))
            )
        raise ValueError(f"unknown setup section {name!r}")

    for name in SECTIONS:
        if out[name].get("state") in ("not_set", "stale"):
            continue
        reason = direct(name)
        if reason:
            out[name] = {"state": "stale", "reason": reason}
    return out
