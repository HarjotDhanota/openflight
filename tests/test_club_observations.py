"""Current-pixel part evidence does not imply a physical club pose."""

import cv2
import numpy as np
import pytest

from openflight.camera import club_observations as obs, head_track as ht


def scene():
    background = np.full((180, 240), 80, np.float32)
    frame = background.astype(np.uint8)
    cv2.line(frame, (45, 15), (100, 110), 150, 4)
    cv2.ellipse(frame, (120, 111), (26, 17), -10, 0, 360, 88, -1)
    cv2.line(frame, (97, 112), (141, 116), 130, 2)
    return frame, background, np.ones_like(background)


def record():
    return {
        "anchor": [100.0, 112.0],
        "axis": [0.5, 0.8660254],
        "shaft_band_px": 5.0,
        "search_radius_px": 55.0,
        "ball_veto": None,
    }


def test_current_shaft_axis_keeps_endpoint_contract_and_real_support():
    frame, background, _ = scene()
    changed = abs(frame.astype(float) - background)
    kwargs = dict(min_length_px=60, band_px=5, gap_px=15, golfer_on_left=True)
    axis = ht.observe_shaft_axis(changed > 18, changed > 6, **kwargs)
    assert axis is not None and axis.end_px is not None
    assert axis.end_px == ht._shaft_end(changed > 18, changed > 6, **kwargs)
    assert len(axis.support_pixels_px) >= 10
    assert np.linalg.norm(axis.direction_px) == pytest.approx(1)
    assert axis.direction_px[1] > 0
    for x, y in axis.support_pixels_px:
        assert changed[y, x] > 18


def test_no_current_axis_does_not_supply_a_prediction():
    empty = np.zeros((100, 100), bool)
    assert (
        ht.observe_shaft_axis(
            empty, empty, min_length_px=30, band_px=4, gap_px=8, golfer_on_left=True
        )
        is None
    )


def test_part_regions_keep_neck_and_shaft_separate_without_hosel_length():
    regions = obs.part_regions((180, 240), record())
    assert set(regions) == {"head", "neck", "shaft"}
    assert not np.array_equal(regions["head"], regions["shaft"])
    assert regions["neck"].any() and regions["shaft"].any()
    vetoed = obs.part_regions((180, 240), {**record(), "ball_veto": [120, 111, 12]})
    for region in vetoed.values():
        assert not region[111, 120]


def test_weak_region_extends_beyond_bright_ridge_without_becoming_observed_mask():
    frame, background, variation = scene()
    result = obs.observe_parts(frame, background, variation, record())
    assert len(result["strong_points"]) >= 3
    assert result["region"]["inferred_area_px"] > len(result["strong_points"])
    assert result["weak_points"]
    assert result["region"]["curve_is_inferred"]
    assert result["qualified_outline"] is result["physical_pose"] is result["strike"] is None
    assert not result["measurement_eligible"]
    for item in result["weak_points"]:
        x, y = item["xy"]
        local = frame[max(0, y - 1) : y + 2, max(0, x - 1) : x + 2].astype(float)
        assert np.any(abs(local - 80) > 0)
        assert np.linalg.norm(item["gradient_unit_image"]) == pytest.approx(1)


def test_empty_head_region_refuses_without_lowering_strong_gate():
    _, background, variation = scene()
    result = obs.observe_parts(background.astype(np.uint8), background, variation, record())
    assert result["strong_points"] == []
    assert result["region"]["status"] == "withheld_no_current_strong_support"
    assert result["strong_floor_dn"] == 18


def test_current_ball_veto_does_not_hide_a_vacated_resting_position():
    frame, background, variation = scene()
    plain = obs.observe_parts(frame, background, variation, record())
    away = obs.observe_parts(frame, background, variation, {**record(), "ball_veto": [200, 30, 8]})
    assert away == plain


@pytest.mark.parametrize("change", ["axis", "anchor", "band", "radius"])
def test_invalid_roi_geometry_is_rejected(change):
    current = record()
    current[{"band": "shaft_band_px", "radius": "search_radius_px"}.get(change, change)] = (
        [0, 0] if change == "axis" else [np.nan, 20] if change == "anchor" else -1
    )
    with pytest.raises(ValueError):
        obs.part_regions((180, 240), current)


def test_cropped_search_is_explicit_and_points_remain_inside_image():
    frame, background, variation = scene()
    result = obs.observe_parts(frame, background, variation, {**record(), "search_radius_px": 130})
    assert result["search_touches_image_border"]
    assert result["physical_pose"] is None
    assert all(0 <= p[0] < 240 and 0 <= p[1] < 180 for p in result["strong_points"])
