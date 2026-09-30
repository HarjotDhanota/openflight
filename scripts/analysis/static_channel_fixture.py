"""Reduce a recorded setup's two static IWR captures to a small coherent-difference fixture.

Reads a guided setup epoch directory (its ``iwr/`` empty and ball-present records
and their raw dumps) and writes the two captures' per-channel mean range profiles,
the range bias and the rig's ground-level elevation window as one JSON file. The
raw dumps are about 0.7 MB each; the fixture keeps only what the coherent
difference reads (P7-6), a few tens of kilobytes.

    uv run python scripts/analysis/static_channel_fixture.py EPOCH_DIR OUT.json \\
        --name outdoors-test-7-5a7821af3641 --note "..."
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openflight.iwr6843.range_evidence import static_channel_profile
from openflight.rig_geometry import RigGeometry

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CALIBRATION = REPO_ROOT / "config" / "iwr6843_calibration_reference.json"
DEFAULT_RIG = REPO_ROOT / "config" / "enclosure_v3_rig_geometry.json"


def _latest_usable(iwr: Path, prefix: str) -> tuple[dict, bytes]:
    for record_path in sorted(iwr.glob(f"{prefix}-*.json"), reverse=True):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        raw_name = (record.get("artifacts") or {}).get("raw", {}).get("path")
        if record.get("usable") and raw_name and (iwr / raw_name).is_file():
            return record, (iwr / raw_name).read_bytes()
    raise ValueError(f"{iwr} has no usable {prefix} capture with its raw dump")


def _rounded(profile) -> dict:
    payload = profile.to_dict()
    payload["real"] = [round(value, 2) for value in payload["real"]]
    payload["imag"] = [round(value, 2) for value in payload["imag"]]
    return payload


def build_fixture(epoch: Path, *, name: str, note: str, calibration: Path, rig: Path) -> dict:
    """The fixture for one setup epoch."""
    # pylint: disable=import-outside-toplevel
    from openflight.camera.tester_server import iwr_ground_elevation_window_deg

    iwr = epoch / "iwr"
    empty_record, empty_raw = _latest_usable(iwr, "empty")
    present_record, present_raw = _latest_usable(iwr, "ball_present")
    profiles = {}
    for kind, record, raw in (
        ("empty", empty_record, empty_raw),
        ("present", present_record, present_raw),
    ):
        profiles[kind] = static_channel_profile(
            raw,
            radar_profile_sha256=record["inputs"]["radar_config"]["sha256"],
            rig_geometry_sha256=record["inputs"]["rig_geometry"]["sha256"],
        )
    bias = float(json.loads(calibration.read_text(encoding="utf-8"))["range_bias_const_m"])
    RigGeometry.from_json(rig)  # the window below is this rig's
    return {
        "schema": "openflight.iwr6843.static_coherent_regression.v1",
        "name": name,
        "epoch_id": epoch.name,
        "note": note,
        "range_bias_const_m": bias,
        "rig_geometry": rig.name,
        "ground_elevation_deg": list(iwr_ground_elevation_window_deg(rig, (1.0, 2.5))),
        "empty": _rounded(profiles["empty"]),
        "present": _rounded(profiles["present"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("epoch", type=Path, help="guided setup epoch directory")
    parser.add_argument("output", type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--note", default="")
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--rig", type=Path, default=DEFAULT_RIG)
    args = parser.parse_args(argv)
    fixture = build_fixture(
        args.epoch, name=args.name, note=args.note, calibration=args.calibration, rig=args.rig
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(fixture, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{args.output} ({args.output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
