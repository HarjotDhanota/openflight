"""Extract compact static-range regression fixtures from a tester bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from openflight.iwr6843.dump import is_range_snapshot, parse_dump
from openflight.iwr6843.range_evidence import static_range_profile

SCHEMA = "openflight.iwr6843.static_range_regression.v1"
EPOCHS = {
    "setup-20260925-fff56186f155": {
        "note": "The v1 selector rejected the approximately 1.012 m diagnostic peak.",
    },
    "setup-20260926-153ffb8aa4be": {
        "note": "The v1 selector accepted a lower-window-edge peak at global bin 12.",
    },
    "setup-20260926-b9f4dd8b3a75": {
        "note": "The v1 selector accepted global bin 36; the operator later reported about 1.01 m.",
        "external_observation": {
            "reported_range_m": 1.01,
            "source": "operator report after capture; not recorded in the bundle",
            "qualification_truth": False,
        },
    },
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _entry_bytes(archive: zipfile.ZipFile, suffix: str) -> bytes:
    matches = [item for item in archive.infolist() if item.filename.endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f"expected one bundle entry ending in {suffix!r}, found {len(matches)}")
    return archive.read(matches[0])


def _final_state(archive: zipfile.ZipFile, epoch_id: str) -> dict[str, Any]:
    marker = f"/epochs/{epoch_id}/state-"
    states = sorted(
        (item for item in archive.infolist() if marker in item.filename),
        key=lambda item: item.filename,
    )
    if not states:
        raise ValueError(f"bundle has no states for {epoch_id}")
    return json.loads(archive.read(states[-1]))


def _capture_fixture(raw: bytes, record: dict[str, Any]) -> dict[str, Any]:
    inputs = record["inputs"]
    profile = static_range_profile(
        raw,
        radar_profile_sha256=inputs["radar_config"]["sha256"],
        radar_profile_qualified=record["radar_profile_qualified"],
        rig_geometry_sha256=inputs["rig_geometry"]["sha256"],
    )
    metadata, cube = parse_dump(raw)
    count = profile.range_bin_count
    range_cube = cube if is_range_snapshot(metadata) else np.fft.fft(cube, axis=-1)
    frame_power = np.mean(np.abs(range_cube[..., :count]) ** 2, axis=(1, 2))
    if not np.allclose(
        np.mean(frame_power, axis=0),
        np.asarray(profile.power),
        rtol=1e-12,
        atol=1e-6,
    ):
        raise ValueError(f"per-frame reduction does not reproduce {record['capture_id']}")
    if profile.capture_sha256 != record["raw_evidence_sha256"]:
        raise ValueError(f"raw digest does not match {record['capture_id']}")
    return {
        "capture_id": record["capture_id"],
        "raw_sha256": profile.capture_sha256,
        "profile": asdict(profile),
        "frame_power": [[float(value) for value in row] for row in frame_power],
    }


def extract_fixture(
    archive: zipfile.ZipFile,
    *,
    bundle_name: str,
    bundle_sha256: str,
    epoch_id: str,
) -> dict[str, Any]:
    """Extract one epoch without treating operator memory as bundle truth."""
    state = _final_state(archive, epoch_id)
    evidence = state["evidence"]
    empty_record = evidence["empty_capture"]
    present_record = evidence["ball_present_capture"]
    prefix = f"/epochs/{epoch_id}/iwr/"
    empty_raw = _entry_bytes(archive, prefix + empty_record["artifacts"]["raw"]["path"])
    present_raw = _entry_bytes(archive, prefix + present_record["artifacts"]["raw"]["path"])
    difference = evidence["iwr_candidate"]["evidence"]["difference"]
    metadata = EPOCHS[epoch_id]
    return {
        "schema": SCHEMA,
        "epoch_id": epoch_id,
        "source_bundle": bundle_name,
        "source_bundle_sha256": bundle_sha256,
        "note": metadata["note"],
        "external_observation": metadata.get("external_observation"),
        "recorded_v1_difference": difference,
        "empty": _capture_fixture(empty_raw, empty_record),
        "present": _capture_fixture(present_raw, present_record),
    }


def _payload(path: Path) -> bytes:
    return (json.dumps(json.loads(path.read_text(encoding="utf-8")), indent=2) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="verify existing fixtures")
    args = parser.parse_args()

    bundle_bytes = args.bundle.read_bytes()
    bundle_sha256 = _sha256(bundle_bytes)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.bundle) as archive:
        for epoch_id in EPOCHS:
            fixture = extract_fixture(
                archive,
                bundle_name=args.bundle.name,
                bundle_sha256=bundle_sha256,
                epoch_id=epoch_id,
            )
            destination = args.output_dir / f"{epoch_id}.json"
            expected = (json.dumps(fixture, indent=2) + "\n").encode()
            if args.check:
                if not destination.is_file() or _payload(destination) != expected:
                    raise SystemExit(f"fixture differs: {destination}")
            else:
                destination.write_bytes(expected)
            print(f"{'verified' if args.check else 'wrote'} {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
