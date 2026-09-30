"""Build the P8 ground-patch regression fixtures from recorded field sessions.

Everything is reduced to what the tests read, well under 2 MB in all:

* ``harjot-indoor-test-1`` (setup-20260930-c77d227da087, 30 Sept, indoors on
  carpet; the ball's centre taped at about 1.25 m from the radar). The radar's two
  captures as per-channel means and power profiles. The camera never saved a frame
  with the ball in it, so the camera fixture is a CROP of the light screen's
  1280x800 median frame at 300 us x 8 (taken before the ball was placed); the tests
  COMPOSITE a diffusely lit sphere into it where the tester's screenshot showed the
  ball, at (744, 518), at the size a ball 1.25 m out has.
* ``Outdoors-test-7`` (setup-20260930-5a7821af3641): a CROP of the only saved frame
  in which the camera located a ball, (778.4, 464.6), 30.9 px, sunlit, and the
  radar's power profiles (its channel means are already in
  ``tests/fixtures/iwr6843_static_coherent``).

    uv run python scripts/analysis/patch_fixtures.py ../pi-handoff tests/fixtures/ground_patch
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from openflight.iwr6843.range_evidence import static_channel_profile

REPO_ROOT = Path(__file__).resolve().parents[2]
CALIBRATION = REPO_ROOT / "config" / "iwr6843_calibration_reference.json"
INDOOR = "harjot-indoor-test-1/calibration/tee-range/guided/epochs/setup-20260930-c77d227da087"
INDOOR_SCREEN = "harjot-indoor-test-1/arm5/gain/20260930_144449/exp0300_gain8_median.pgm"
OUTDOOR = "Outdoors-test-7/calibration/tee-range/guided/epochs/setup-20260930-5a7821af3641"
# the crop keeps the rows and columns a patch around the ball can reach
INDOOR_CROP = (250, 330, 1030, 650)  # x0, y0, x1, y1
OUTDOOR_CROP = (560, 380, 1000, 560)
OUTDOOR_BALL = (778.362489897664, 464.55225011829714, 30.934154798839483)


def read_pgm(path: Path) -> np.ndarray:
    data = path.read_bytes()
    magic, size, _maximum, pixels = data.split(b"\n", 3)
    if magic != b"P5":
        raise ValueError(f"{path} is not a binary PGM")
    width, height = (int(value) for value in size.split())
    return np.frombuffer(pixels, dtype=np.uint8).reshape(height, width)


def _usable(iwr: Path, prefix: str) -> tuple[dict, bytes]:
    for record_path in sorted(iwr.glob(f"{prefix}-*.json"), reverse=True):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        raw = (record.get("artifacts") or {}).get("raw", {}).get("path")
        if record.get("usable") and raw and (iwr / Path(raw).name).is_file():
            return record, (iwr / Path(raw).name).read_bytes()
    raise ValueError(f"{iwr} has no usable {prefix} capture")


def _rounded(profile) -> dict:
    payload = profile.to_dict()
    payload["real"] = [round(value, 2) for value in payload["real"]]
    payload["imag"] = [round(value, 2) for value in payload["imag"]]
    return payload


def radar_fixture(epoch: Path, *, name: str, note: str, channels: bool) -> dict:
    iwr = epoch / "iwr"
    fixture = {
        "schema": "openflight.ground_patch_radar_regression.v1",
        "name": name,
        "epoch_id": epoch.name,
        "note": note,
        "range_bias_const_m": float(
            json.loads(CALIBRATION.read_text(encoding="utf-8"))["range_bias_const_m"]
        ),
    }
    for kind, prefix in (("empty", "empty"), ("present", "ball_present")):
        record, raw = _usable(iwr, prefix)
        fixture[f"{kind}_power"] = record["profile"]
        if channels:
            fixture[f"{kind}_channels"] = _rounded(
                static_channel_profile(
                    raw,
                    radar_profile_sha256=record["inputs"]["radar_config"]["sha256"],
                    rig_geometry_sha256=record["inputs"]["rig_geometry"]["sha256"],
                )
            )
    return fixture


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handoff", type=Path, help="the pi-handoff folder")
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    out = args.output
    out.mkdir(parents=True, exist_ok=True)

    indoor = radar_fixture(
        args.handoff / INDOOR,
        name="harjot-indoor-test-1-c77d227da087",
        note=(
            "30 Sept 14:45, indoors on carpet, the ball near a door; its centre taped at "
            "about 1.25 m from the radar. The coherent difference accepted 1.575 m "
            "corrected (wrong object); the power profiles' strongest change was 1.266 m "
            "apparent (about 1.20 m corrected), rejected by the fractional gate."
        ),
        channels=True,
    )
    indoor["tape_ball_center_to_radar_m"] = 1.25
    indoor["lis3dh"] = {"camera_pitch_deg": 1.72, "roll_deg": -2.62}
    (out / "indoor-c77d227da087-radar.json").write_text(json.dumps(indoor), encoding="utf-8")

    outdoor = radar_fixture(
        args.handoff / OUTDOOR,
        name="outdoors-test-7-5a7821af3641",
        note="30 Sept, full sun on a mat; no tape. Power profiles only.",
        channels=False,
    )
    outdoor["lis3dh"] = {"camera_pitch_deg": 0.3, "roll_deg": -2.79}
    (out / "outdoors-test-7-5a7821af3641-radar.json").write_text(
        json.dumps(outdoor), encoding="utf-8"
    )

    screen = read_pgm(args.handoff / INDOOR_SCREEN)
    x0, y0, x1, y1 = INDOOR_CROP
    np.savez_compressed(
        out / "indoor-c77d227da087-scene.npz",
        background=screen[y0:y1, x0:x1],
        background_origin_px=np.asarray([x0, y0]),
        background_controls=np.asarray([300.0, 8.0]),
    )
    frame = read_pgm(args.handoff / OUTDOOR / "camera-arm5-000004.pgm")
    x0, y0, x1, y1 = OUTDOOR_CROP
    np.savez_compressed(
        out / "outdoors-test-7-5a7821af3641-camera.npz",
        frame=frame[y0:y1, x0:x1],
        origin_px=np.asarray([x0, y0]),
        ball_px=np.asarray(OUTDOOR_BALL),
    )
    total = sum(path.stat().st_size for path in out.iterdir())
    print(f"wrote {len(list(out.iterdir()))} files, {total / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
