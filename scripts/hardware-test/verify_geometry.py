#!/usr/bin/env python3
"""Check the solved enclosure geometry against a tape measure before a session.

Every camera-derived number starts at the resting ball: diameter sets range and
scale, column sets lateral offset, row sets height. This solves them, prints
each against the tape you typed, reads the inclinometer against what the rig
file expects, and names what it could not establish. A setup check, not an
accuracy measurement.

    uv run python scripts/hardware-test/verify_geometry.py         --rig-geometry config/enclosure_v3_rig_geometry.json --tape-range-mm 1524
    # or offline: add --frames path/to/frames.npz
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from openflight.camera.club_motion import detect_reference_ball  # noqa: E402
from openflight.rig_geometry import RigGeometry, solve_setup  # noqa: E402

# A tape and a pixel solve that disagree by more than this are not describing
# the same setup; half a pixel of ball diameter is already 60-65 mm of range.
RANGE_TOLERANCE_MM = 80.0
OFFSET_TOLERANCE_MM = 25.0
# The placement gate's own band, so this agrees with what the server reports.
TILT_TOLERANCE_DEG = 2.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rig-geometry", required=True, type=Path)
    parser.add_argument("--frames", type=Path, help="Solve from an existing frames.npz")
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=200)
    parser.add_argument("--fps", type=float, default=450.0)
    parser.add_argument("--exposure-us", type=int, default=298)
    parser.add_argument("--gain", type=float, default=5.0)
    parser.add_argument("--settle-frames", type=int, default=20)
    parser.add_argument(
        "--tape-range-mm", type=float, help="Tape: lens front vertex to ball centre"
    )
    parser.add_argument(
        "--tape-lateral-mm",
        type=float,
        help="Tape: camera left of the ball line is POSITIVE, matching the solve",
    )
    parser.add_argument("--tape-height-mm", type=float, help="Tape: camera above the ball centre")
    parser.add_argument("--radar-range-mm", type=float, help="The radar's own tee range, if known")
    parser.add_argument("--no-inclinometer", action="store_true")
    return parser.parse_args(argv)


def capture_static_frames(args: argparse.Namespace) -> np.ndarray:
    """Grab a short run of the static scene at fixed exposure and gain."""
    from picamera2 import Picamera2  # pylint: disable=import-error,import-outside-toplevel

    frame_duration_us = int(round(1_000_000 / args.fps))
    camera = Picamera2()
    camera.configure(
        camera.create_video_configuration(
            main={"size": (args.width, args.height), "format": "YUV420"},
            raw={"size": (args.width, args.height), "format": "R8"},
            controls={
                "AeEnable": False,
                "ExposureTime": min(args.exposure_us, frame_duration_us - 1),
                "AnalogueGain": args.gain,
                "FrameDurationLimits": (frame_duration_us, frame_duration_us),
            },
            buffer_count=8,
            display=None,
            encode=None,
        )
    )
    try:
        camera.start()
        frames = []
        for index in range(args.settle_frames + 5):
            plane = camera.capture_array("raw")[: args.height, : args.width]
            if index >= args.settle_frames:
                frames.append(np.ascontiguousarray(plane))
        return np.stack(frames)
    finally:
        camera.close()


def load_frames(args: argparse.Namespace) -> tuple[np.ndarray, str]:
    """Return the frames to solve from, and where they came from."""
    if args.frames is not None:
        with np.load(args.frames) as bundle:
            return np.asarray(bundle["frames"]), f"{args.frames}"
    return capture_static_frames(args), "live capture"


def _line(label: str, solved: float, tape: float | None, unit: str, tolerance: float) -> str:
    """One comparison row; the verdict is only meaningful when a tape exists."""
    if tape is None:
        return f"  {label:<26s} {solved:9.1f} {unit}   (no tape given)"
    delta = solved - tape
    verdict = "ok" if abs(delta) <= tolerance else "DISAGREES"
    return (
        f"  {label:<26s} {solved:9.1f} {unit}   tape {tape:8.1f}   delta {delta:+7.1f}   {verdict}"
    )


def read_inclinometer(bus_number: int = 1, address: int = 0x18):
    """Read the LIS3DH once, or say why not. Pitch only on this branch."""
    service = None
    try:
        from openflight.inclinometer import LIS3DH, InclinometerService  # noqa: PLC0415

        service = InclinometerService(LIS3DH(bus_number=bus_number, address=address))
        service.start()
        startup = service.wait_for_stable(timeout_s=3.0)
        if startup.snapshot is None:
            return None, f"no stable reading ({startup.status})"
        return startup.snapshot, "ok"
    except ImportError as exc:  # pragma: no cover - platform dependent
        return None, f"module unavailable ({exc})"
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return None, f"could not read ({exc})"
    finally:
        if service is not None:
            try:
                service.stop()
            except Exception:  # pylint: disable=broad-exception-caught
                pass


def main(argv: list[str] | None = None) -> int:
    """Solve the setup from the resting ball and report it against the tape."""
    args = parse_args(argv)
    rig = RigGeometry.from_json(args.rig_geometry)
    setup = rig.enclosure_setup()

    print(f"Rig geometry: {args.rig_geometry}")
    print(f"  focal {rig.focal_px:.1f} px at {rig.image_width}x{rig.image_height}")
    for key, value in setup.as_dict().items():
        if key in ("provenance", "missing"):
            continue
        print(f"  {key:<26s} {value}")
    if setup.missing:
        print(f"  MISSING (flags still stand in): {', '.join(setup.missing)}")

    frames, source = load_frames(args)
    if frames.ndim != 3 or len(frames) == 0:
        print(f"\nframes from {source} are not a usable stack: {frames.shape}")
        return 2
    if frames.shape[1:] != (rig.image_height, rig.image_width):
        print(
            f"\nWARNING: frames are {frames.shape[2]}x{frames.shape[1]} but the rig file "
            f"describes {rig.image_width}x{rig.image_height}. The focal length is per "
            f"readout mode, so the solve below is against the wrong scale."
        )

    background = np.median(frames, axis=0).astype(np.uint8)
    print(
        f"\nScene from {source}: {len(frames)} frames, "
        f"mean DN {background.mean():.1f}, p99 {np.percentile(background, 99):.0f}"
    )
    if np.percentile(background, 99) >= 250:
        print("  WARNING: the scene clips. A saturated ball has no edge, so the diameter")
        print("           below is soft however tight it looks.")

    try:
        ball = detect_reference_ball(frames)
    except (ValueError, RuntimeError) as exc:
        print(f"\nNo resting ball found: {exc}")
        print("Place a ball in the normal address position and run this again.")
        return 1

    solution = solve_setup(ball, rig)
    print(
        f"\nBall at ({ball.x:.1f}, {ball.y:.1f}) px, diameter {ball.diameter_px:.2f} px, "
        f"area {ball.area_px} px"
    )
    print("\nSolved from the ball, against your tape:")
    print(
        _line(
            "range to ball", solution.range_to_ball_mm, args.tape_range_mm, "mm", RANGE_TOLERANCE_MM
        )
    )
    print(
        _line(
            "lateral offset",
            solution.lateral_offset_mm,
            args.tape_lateral_mm,
            "mm",
            OFFSET_TOLERANCE_MM,
        )
    )
    print(
        _line(
            "height above ball",
            solution.height_above_ball_mm,
            args.tape_height_mm,
            "mm",
            OFFSET_TOLERANCE_MM,
        )
    )
    print(f"  {'scale at the ball':<26s} {solution.mm_per_px_at_ball:9.3f} mm/px")
    if solution.mic_to_ball_m is not None:
        print(
            f"  {'mic to ball':<26s} {solution.mic_to_ball_m * 1000:9.1f} mm"
            f"   (acoustic walk-back {solution.mic_to_ball_m / 343.0 * 1000:.2f} ms)"
        )

    if args.radar_range_mm is not None:
        delta = solution.range_disagreement_mm(args.radar_range_mm)
        verdict = "ok" if abs(delta) <= RANGE_TOLERANCE_MM else "DISAGREES"
        print(f"\n  camera minus radar range   {delta:+7.1f} mm   {verdict}")

    if solution.warnings:
        print("\nWarnings from the solve:")
        for warning in solution.warnings:
            print(f"  - {warning}")

    # Half a pixel of diameter is the whole error budget; show what it costs here.
    if ball.diameter_px > 0:
        softness = solution.range_to_ball_mm / ball.diameter_px * 0.5
        print(f"\nHalf a pixel of ball diameter moves the range by {softness:.0f} mm.")

    if not args.no_inclinometer:
        snapshot, status = read_inclinometer()
        expected = rig.expected_inclinometer_orientation()
        print(f"\nInclinometer: {status}")
        if snapshot is not None:
            measured = snapshot.calibrated_pitch_deg
            want = expected.pitch_deg
            print(
                f"  raw pitch {snapshot.raw_pitch_deg:+.2f} deg, "
                f"calibrated {measured:+.2f} deg, spread {snapshot.pitch_std_deg:.3f} deg"
            )
            if want is None:
                print(f"  no expectation: {expected.provenance.get('pitch_deg', '')}")
            else:
                departure = measured - want
                verdict = "ok" if abs(departure) <= TILT_TOLERANCE_DEG else "OUT OF LEVEL"
                print(
                    f"  {'pitch vs expected':<26s} {measured:9.2f} deg   "
                    f"expected {want:6.2f}   delta {departure:+6.2f}   {verdict}"
                )
                designed = rig.iwr_boresight_pitch_deg
                if designed is not None:
                    print(
                        f"  {'effective radar tilt':<26s} {designed + departure:9.2f} deg   "
                        f"= designed {designed:.2f} + departure {departure:+.2f}"
                    )
            print("  Roll is not measured on this branch, so a sideways tilt is invisible here.")

    print("\nThis compares the rig against itself and your tape. It is a setup check,")
    print("not an accuracy measurement of any shipped metric.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
