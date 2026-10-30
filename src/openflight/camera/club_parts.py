"""Club-part label contract, crop mapping and observation record for learned club parts.

Version v1 is the August 2026 segmentation schema: the head includes hosel and
ferrule. Version v2 splits that head into body and neck; ``to_v1`` merges them back.
``IGNORE`` marks unknown or occluded pixels: they are never background.

Coordinates are CANONICAL native pixels: the un-mirrored frame, pixel centres at
integer (x, y), x right and y down. A crop covers ``side`` canonical pixels per edge
and is resampled to ``size`` model pixels per edge.
"""

# pylint: disable=no-member,too-many-arguments
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Mapping, Optional

import cv2
import numpy as np

LABEL_V1 = "openflight.club_parts.v1"
LABEL_V2 = "openflight.club_parts.v2"
V1_CLASSES = ("background", "head", "shaft", "ball", "shadow")
V2_CLASSES = ("background", "head_body", "neck", "shaft", "ball", "shadow")
IGNORE = 255

_V2_TO_V1 = np.arange(256, dtype=np.uint8)
_V2_TO_V1[: len(V2_CLASSES)] = [0, 1, 1, 2, 3, 4]
_V2_TO_V1[len(V2_CLASSES) : IGNORE] = IGNORE


def to_v1(mask_v2: np.ndarray) -> np.ndarray:
    """v2 class ids as v1 ids; ignore stays ignore, undefined ids become ignore."""
    return _V2_TO_V1[np.asarray(mask_v2, dtype=np.uint8)]


class Visibility(str, enum.Enum):
    VISIBLE = "visible"
    OCCLUDED = "occluded"
    OUT_OF_VIEW = "out_of_view"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CropTransform:
    """Square crop of a stored frame, expressed in canonical coordinates."""

    frame_width: int
    frame_height: int
    stored_mirrored: bool
    x0: int
    y0: int
    side: int
    size: int

    def __post_init__(self) -> None:
        if min(self.frame_width, self.frame_height, self.side, self.size) <= 0:
            raise ValueError("frame, crop side and crop size must be positive")

    @classmethod
    def centred(
        cls,
        frame_width: int,
        frame_height: int,
        *,
        stored_mirrored: bool,
        centre: tuple[float, float],
        side: int,
        size: int,
    ) -> "CropTransform":
        x0 = int(round(centre[0])) - side // 2
        y0 = int(round(centre[1])) - side // 2
        return cls(frame_width, frame_height, stored_mirrored, x0, y0, side, size)

    @property
    def scale(self) -> float:
        """Canonical pixels per crop pixel."""
        return self.side / self.size

    def to_crop(self, points: np.ndarray) -> np.ndarray:
        """Canonical (x, y) -> crop pixel coordinates."""
        points = np.asarray(points, dtype=float)
        origin = np.array([self.x0, self.y0], dtype=float)
        return (points - origin + 0.5) / self.scale - 0.5

    def to_canonical(self, points: np.ndarray) -> np.ndarray:
        """Crop pixel (u, v) -> canonical coordinates."""
        points = np.asarray(points, dtype=float)
        origin = np.array([self.x0, self.y0], dtype=float)
        return origin - 0.5 + (points + 0.5) * self.scale

    def canonical_to_stored(self, points: np.ndarray) -> np.ndarray:
        """Canonical (x, y) -> the stored frame's pixel coordinates."""
        points = np.array(points, dtype=float)
        if self.stored_mirrored:
            points[..., 0] = self.frame_width - 1 - points[..., 0]
        return points

    def _crop_to_stored_affine(self) -> np.ndarray:
        offset = np.array([self.x0, self.y0], dtype=float) - 0.5 + 0.5 * self.scale
        matrix = np.array([[self.scale, 0.0, offset[0]], [0.0, self.scale, offset[1]]])
        if self.stored_mirrored:
            matrix[0] = [-self.scale, 0.0, self.frame_width - 1 - offset[0]]
        return matrix

    def extract(self, stored_frame: np.ndarray, *, labels: bool = False) -> np.ndarray:
        """Crop pixels from the STORED frame; outside the frame is 0 (or IGNORE for labels)."""
        if stored_frame.shape[:2] != (self.frame_height, self.frame_width):
            raise ValueError("frame shape does not match the transform")
        return cv2.warpAffine(
            stored_frame,
            self._crop_to_stored_affine(),
            (self.size, self.size),
            flags=(cv2.INTER_NEAREST if labels else cv2.INTER_LINEAR) | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=IGNORE if labels else 0,
        )

    def in_frame(self) -> np.ndarray:
        """Crop pixels whose centre maps inside the frame."""
        grid = np.mgrid[0 : self.size, 0 : self.size][::-1].reshape(2, -1).T
        canonical = self.to_canonical(grid)
        inside = (
            (canonical[:, 0] >= -0.5)
            & (canonical[:, 0] < self.frame_width - 0.5)
            & (canonical[:, 1] >= -0.5)
            & (canonical[:, 1] < self.frame_height - 0.5)
        )
        return inside.reshape(self.size, self.size)


@dataclass(frozen=True)
class Landmark:
    """A semantic image point in canonical pixels; position is None unless visible."""

    visibility: Visibility
    position: Optional[tuple[float, float]] = None
    support: Optional[float] = None


@dataclass(frozen=True)
class ClubPartObservation:
    """One frame's learned (or deterministic) part evidence, never a pose or contact claim."""

    capture_id: str
    frame_index: int
    pixel_sha256: str
    sensor_timestamp_ns: Optional[int]
    exposure_us: Optional[float]
    transform: CropTransform
    label_version: str
    model_id: str
    probabilities: np.ndarray
    landmarks: Mapping[str, Landmark] = field(default_factory=dict)
    crop_source: str = "shaft_end"

    def __post_init__(self) -> None:
        classes = V2_CLASSES if self.label_version == LABEL_V2 else V1_CLASSES
        expected = (len(classes), self.transform.size, self.transform.size)
        if self.probabilities.shape != expected:
            raise ValueError(f"probabilities must have shape {expected}")


def mask_landmarks(
    mask_v2: np.ndarray,
    transform: CropTransform,
    *,
    toe_direction: int,
    topline_column: Optional[float] = None,
    probabilities: Optional[np.ndarray] = None,
) -> dict[str, Landmark]:
    """Heel, toe and topline from a v2 crop mask, following the August mark convention.

    toe: the outermost head-body pixel toward the toe; heel: the top-most body pixel
    touching the neck; topline: the top-most body pixel in ``topline_column`` (canonical
    x), or in the body's middle column. ``toe_direction`` is +1 when the toe is toward
    canonical +x. A landmark on the crop edge, the frame edge or next to IGNORE is not
    visible: its true extent is unknown there.
    """
    if toe_direction not in (-1, 1):
        raise ValueError("toe_direction must be +1 or -1")
    body = mask_v2 == 1
    neck = mask_v2 == 2
    unknown = (mask_v2 == IGNORE) | ~transform.in_frame()
    unknown[0, :] = unknown[-1, :] = unknown[:, 0] = unknown[:, -1] = True
    if not body.any():
        return {name: Landmark(Visibility.UNKNOWN) for name in ("heel", "toe", "topline")}
    near_unknown = cv2.dilate(unknown.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0

    def point(row: int, column: int) -> Landmark:
        if near_unknown[row, column]:
            edge = (
                not transform.in_frame()[row, column]
                or row in (0, body.shape[0] - 1)
                or (column in (0, body.shape[1] - 1))
            )
            return Landmark(Visibility.OUT_OF_VIEW if edge else Visibility.UNKNOWN)
        x, y = transform.to_canonical(np.array([column, row], dtype=float))
        support = None if probabilities is None else float(probabilities[1, row, column])
        return Landmark(Visibility.VISIBLE, (float(x), float(y)), support)

    rows, columns = np.nonzero(body)
    extreme = columns.max() if toe_direction > 0 else columns.min()
    toe_rows = np.sort(rows[columns == extreme])
    landmarks = {"toe": point(int(toe_rows[len(toe_rows) // 2]), int(extreme))}

    touching = body & (cv2.dilate(neck.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
    if touching.any():
        heel_rows, heel_columns = np.nonzero(touching)
        top = heel_rows.min()
        heel_column = heel_columns[heel_rows == top]
        heel_column = heel_column.min() if toe_direction > 0 else heel_column.max()
        landmarks["heel"] = point(int(top), int(heel_column))
    else:
        landmarks["heel"] = Landmark(Visibility.UNKNOWN)

    if topline_column is None:
        column = int(round((columns.min() + columns.max()) / 2))
    else:
        column = int(round(transform.to_crop(np.array([topline_column, 0.0]))[0]))
    if 0 <= column < body.shape[1] and body[:, column].any():
        landmarks["topline"] = point(int(np.argmax(body[:, column])), column)
    else:
        landmarks["topline"] = Landmark(Visibility.UNKNOWN)
    return landmarks
