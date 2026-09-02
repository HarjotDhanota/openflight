"""Calibrate the shipped camera's intrinsics from checkerboard captures.

WHY: every angle the camera reports scales with the focal length, and the
vendor page for the shipped module (Inno-Maker CAM-MIPI9281RAW-V2) specifies
a wide-angle FISHEYE lens with TV distortion < -17 percent. The nominal
466.7 px focal is vendor-arithmetic cross-checked to ~1 percent near the
image centre; the distortion has never been measured at all. One session
with this script replaces both assumptions with measurements.

PROCEDURE (15 minutes):

  1. Make the board and print it at 100 percent scale (no fit-to-page):

         uv run python scripts/hardware-test/calibrate_camera_intrinsics.py \
             --make-board board.png

     Tape it to something FLAT (clipboard, cutting board). Measure one
     square's edge with a ruler AFTER printing -- that number is the only
     manual input, and a 1 percent error in it does not affect angles.

  2. With the camera in its PRODUCTION mode and focus, capture 10-15 still
     frames of the board: near and far, tilted up to ~30 degrees in each
     direction, and -- important for the distortion -- with the board
     pushed into every corner of the frame. Save them as PNGs in one
     directory (or as .npz files with a ``frames`` array).

  3. Solve:

         uv run python scripts/hardware-test/calibrate_camera_intrinsics.py \
             captures/ --square-mm 24.8 --out camera_intrinsics.json

     The JSON's ``focal_px`` feeds `rig_geometry.RigGeometry` /
     `camera.ball_flight.CameraBallGeometry.focal_px`; the distortion
     coefficients are recorded for the (future) undistortion step and the
     report says, in pixels, what the lens bends at the image regions the
     pipeline actually uses.

The solve REFUSES quietly bad input by name: too few usable views, corners
that never approach the frame edges (distortion unconstrained), or a
reprojection error worse than half a pixel.
"""

# cv2's bindings are generated at import, so pylint cannot see any of them.
# pylint: disable=no-member

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

PATTERN = (9, 6)  # inner corners of a 10x7-square board
NOMINAL_FOCAL_PX_AT_320 = 466.6667
MIN_VIEWS = 8
MAX_RMS_PX = 0.5
MIN_COVERAGE_FRACTION = 0.55


@dataclass(frozen=True)
class CalibrationResult:
    """One solved camera, with everything a consumer or an auditor needs."""

    fx: float
    fy: float
    cx: float
    cy: float
    dist_coeffs: tuple[float, ...]
    image_width: int
    image_height: int
    rms_reprojection_px: float
    n_views: int
    coverage_fraction: float
    square_mm: float

    @property
    def focal_px(self) -> float:
        """The single focal the geometry consumes; fx/fy anisotropy is reported."""
        return 0.5 * (self.fx + self.fy)

    def nominal_focal_px(self) -> float:
        """The pre-calibration nominal for this image width."""
        return NOMINAL_FOCAL_PX_AT_320 * self.image_width / 320.0

    def distortion_displacement_px(self, radius_px: float) -> float:
        """How far the lens moves a point ``radius_px`` from the centre, in px.

        Positive = outward (pincushion), negative = inward (barrel). Computed
        by distorting an ideal point with the solved coefficients.
        """
        ideal = np.array([[[radius_px / self.focal_px, 0.0, 1.0]]], dtype=float)
        camera = np.array([[self.fx, 0, self.cx], [0, self.fy, self.cy], [0, 0, 1]])
        projected, _ = cv2.projectPoints(
            ideal,
            np.zeros(3),
            np.zeros(3),
            camera,
            np.asarray(self.dist_coeffs),
        )
        return float(projected[0, 0, 0] - self.cx - radius_px)


def make_board(path: Path, square_px: int = 120, margin_px: int = 60) -> None:
    """Write a printable 10x7 checkerboard with instructions in the margin."""
    columns, rows = PATTERN[0] + 1, PATTERN[1] + 1
    width = columns * square_px + 2 * margin_px
    height = rows * square_px + 2 * margin_px + 40
    board = np.full((height, width), 255, np.uint8)
    for row in range(rows):
        for column in range(columns):
            if (row + column) % 2 == 0:
                top = margin_px + row * square_px
                left = margin_px + column * square_px
                board[top : top + square_px, left : left + square_px] = 0
    cv2.putText(
        board,
        "print at 100% scale, tape FLAT, measure one square edge in mm",
        (margin_px, height - 14),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        0,
        2,
        cv2.LINE_AA,
    )
    cv2.imwrite(str(path), board)
    print(f"board -> {path}")
    print("print at 100% (no fit-to-page), tape flat, measure one square edge in mm")


def load_frames(sources: list[str]) -> list[np.ndarray]:
    """Grayscale frames from image files, directories of them, or npz dumps."""
    frames: list[np.ndarray] = []
    paths: list[Path] = []
    for source in sources:
        candidate = Path(source)
        if candidate.is_dir():
            paths.extend(sorted(candidate.iterdir()))
        else:
            paths.append(candidate)
    for path in paths:
        if path.suffix.lower() == ".npz":
            with np.load(path) as data:
                stack = data["frames"]
            frames.extend(np.asarray(frame, np.uint8) for frame in stack)
        elif path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}:
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is not None:
                frames.append(image)
    return frames


def find_corners(gray: np.ndarray) -> np.ndarray | None:
    """Sub-pixel checkerboard corners, or None when the board is not found."""
    found, corners = cv2.findChessboardCornersSB(
        gray, PATTERN, flags=cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
    )
    if found:
        return corners.reshape(-1, 2)
    found, corners = cv2.findChessboardCorners(gray, PATTERN)
    if not found:
        return None
    refined = cv2.cornerSubPix(
        gray,
        corners,
        (5, 5),
        (-1, -1),
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_COUNT, 40, 1e-3),
    )
    return refined.reshape(-1, 2)


def _object_grid(square_mm: float) -> np.ndarray:
    grid = np.zeros((PATTERN[0] * PATTERN[1], 3), np.float32)
    grid[:, :2] = np.mgrid[0 : PATTERN[0], 0 : PATTERN[1]].T.reshape(-1, 2) * square_mm
    return grid


def solve_from_correspondences(
    image_points: list[np.ndarray],
    image_size: tuple[int, int],
    square_mm: float,
) -> CalibrationResult:
    """Calibrate from per-view corner arrays. Pure solve; no file IO.

    ``image_size`` is (width, height). Raises ValueError, by name, on too few
    views, on corners that never approach the frame edges (the distortion
    would be unconstrained exactly where the fisheye bends most), and on a
    reprojection error worse than `MAX_RMS_PX`.
    """
    if len(image_points) < MIN_VIEWS:
        raise ValueError(f"only_{len(image_points)}_usable_views_need_{MIN_VIEWS}")
    width, height = image_size
    stacked = np.vstack(image_points)
    coverage_x = float(np.ptp(stacked[:, 0])) / width
    coverage_y = float(np.ptp(stacked[:, 1])) / height
    coverage = min(coverage_x, coverage_y)
    if coverage < MIN_COVERAGE_FRACTION:
        raise ValueError(f"corners_span_only_{coverage:.0%}_of_the_frame_distortion_unconstrained")
    object_points = [_object_grid(square_mm)] * len(image_points)
    rms, camera, dist, _rvecs, _tvecs = cv2.calibrateCamera(
        object_points,
        [points.astype(np.float32).reshape(-1, 1, 2) for points in image_points],
        (width, height),
        None,
        None,
    )
    if rms > MAX_RMS_PX:
        raise ValueError(f"reprojection_rms_{rms:.2f}_px_exceeds_{MAX_RMS_PX}")
    return CalibrationResult(
        fx=float(camera[0, 0]),
        fy=float(camera[1, 1]),
        cx=float(camera[0, 2]),
        cy=float(camera[1, 2]),
        dist_coeffs=tuple(float(value) for value in np.ravel(dist)),
        image_width=width,
        image_height=height,
        rms_reprojection_px=float(rms),
        n_views=len(image_points),
        coverage_fraction=coverage,
        square_mm=float(square_mm),
    )


def report(result: CalibrationResult) -> None:
    """Print the solved camera against the nominal, in consumer terms."""
    nominal = result.nominal_focal_px()
    print(f"focal:      fx {result.fx:.1f}  fy {result.fy:.1f}  (used: {result.focal_px:.1f} px)")
    print(
        f"vs nominal: {nominal:.1f} px -> {100 * (result.focal_px / nominal - 1):+.2f}%"
        f"  (~{abs(result.focal_px / nominal - 1) * 24:.2f} deg on a 24 deg launch)"
    )
    print(
        f"centre:     cx {result.cx:.2f}  cy {result.cy:.2f}"
        f"  (offset {result.cx - result.image_width / 2:+.2f}, "
        f"{result.cy - result.image_height / 2:+.2f} px from the image middle)"
    )
    print(f"distortion: {tuple(round(value, 5) for value in result.dist_coeffs)}")
    for radius in (30.0, 60.0, 120.0):
        if radius < math.hypot(result.image_width, result.image_height) / 2:
            shift = result.distortion_displacement_px(radius)
            print(f"  at r={radius:.0f} px the lens moves a point {shift:+.2f} px")
    print(
        f"quality:    rms {result.rms_reprojection_px:.3f} px over "
        f"{result.n_views} views, corner coverage {result.coverage_fraction:.0%}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("sources", nargs="*", help="image files, directories, or npz dumps")
    parser.add_argument("--make-board", metavar="PATH", help="write a printable board and exit")
    parser.add_argument("--square-mm", type=float, help="measured edge of one printed square")
    parser.add_argument("--out", default="camera_intrinsics.json", help="output JSON path")
    args = parser.parse_args()

    if args.make_board:
        make_board(Path(args.make_board))
        return 0
    if not args.sources or args.square_mm is None:
        parser.error("solving needs capture sources and --square-mm (see --help)")

    frames = load_frames(args.sources)
    if not frames:
        print("no readable frames in the given sources")
        return 1
    views = []
    for frame in frames:
        corners = find_corners(frame)
        if corners is not None:
            views.append(corners)
    print(f"{len(views)}/{len(frames)} frames show the full board")
    try:
        result = solve_from_correspondences(
            views, (frames[0].shape[1], frames[0].shape[0]), args.square_mm
        )
    except ValueError as error:
        print(f"REFUSED: {error}")
        return 1
    report(result)
    Path(args.out).write_text(json.dumps(asdict(result), indent=1), encoding="utf-8")
    print(f"-> {args.out}  (feed focal_px into RigGeometry / CameraBallGeometry.focal_px)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
