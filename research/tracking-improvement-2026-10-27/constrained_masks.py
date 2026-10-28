"""Offline candidate experiment; never imported by the live pipeline."""

# pylint: disable=no-member,protected-access

import cv2
import numpy as np

from openflight.camera import impact_location as il
from openflight.camera.head_track import HOSEL_PIXEL_SIGMA


def fully_precontact_frames(stamps, exposures_us, earliest_contact_ns):
    """Only complete exposures before the earliest declared possible contact."""
    stamps = np.asarray(stamps, dtype=float)
    exposures = np.asarray(exposures_us, dtype=float)
    if (
        stamps.ndim != 1
        or exposures.shape != stamps.shape
        or not np.isfinite(stamps).all()
        or not np.isfinite(exposures).all()
        or not np.isfinite(earliest_contact_ns)
        or (exposures < 0).any()
        or (np.diff(stamps) <= 0).any()
    ):
        raise ValueError("unaligned_or_unknown_exposure_timing")
    return np.flatnonzero(stamps + exposures * 1000 <= earliest_contact_ns)


def select_component(current, scale, anchor, *, occlusion=None):
    """Keep one observed hosel's component, retaining the outline's existing gates."""
    if anchor is None:
        return None, "no_observed_hosel"
    x, y = anchor
    height, width = current.shape
    if not np.isfinite([x, y]).all() or not (0 <= x < width and 0 <= y < height):
        return None, "invalid_hosel"
    _, labels, stats, _ = cv2.connectedComponentsWithStats(current.astype(np.uint8), 8)
    yy, xx = np.mgrid[:height, :width]
    near = (xx - x) ** 2 + (yy - y) ** 2 <= HOSEL_PIXEL_SIGMA**2
    eligible = [
        k
        for k in np.unique(labels[near])
        if k > 0 and stats[k, 4] * scale**2 >= il.MIN_COMPONENT_AREA_MM2
    ]
    if len(eligible) != 1:
        return (
            None,
            "ambiguous_components_at_observed_hosel"
            if eligible
            else "no_component_at_observed_hosel",
        )
    label = eligible[0]
    split = il._split_head((labels == label).astype(np.uint8), scale)
    if split is None:
        return None, "no_body_thick_enough_to_be_a_clubhead"
    head = split[0].astype(bool)
    area = head.sum() * scale**2
    if area < il.MIN_HEAD_AREA_MM2:
        return None, "head_area_below_existing_gate"
    if area > il.MAX_HEAD_AREA_MM2:
        return None, "head_area_above_existing_gate"
    if head[0].any() or head[-1].any() or head[:, 0].any() or head[:, -1].any():
        return None, "head_touches_crop"
    if occlusion is not None and (head & occlusion).any():
        return None, "head_overlaps_occlusion"
    return head, "candidate_only"
