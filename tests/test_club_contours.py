"""Current edge chains preserve ambiguity and never invent measured silhouettes."""

import copy

import cv2
import numpy as np
import pytest

from openflight.camera._club_contour_topology import repair
from openflight.camera._club_edge_path import trace_column
from openflight.camera.club_contours import contour_hypotheses
from openflight.camera.club_observations import observe_parts


def inputs():
    background = np.full((100, 130), 80, np.float32)
    frame = background.astype(np.uint8)
    cv2.line(frame, (40, 5), (40, 65), 150, 3)
    cv2.ellipse(frame, (62, 62), (24, 18), 0, 0, 360, 90, -1)
    cv2.line(frame, (42, 65), (84, 70), 135, 2)
    geometry = dict(anchor=[40, 65], axis=[0, 1], shaft_band_px=4, search_radius_px=50)
    return frame, background, np.ones_like(background), geometry


def test_region_rivals_and_current_membership_survive_without_physical_selection():
    frame, bg, var, geo = inputs()
    e = observe_parts(frame, bg, var, geo)
    e["region"]["components"].append(copy.deepcopy(e["region"]["components"][0]))
    before = copy.deepcopy(e)
    result = contour_hypotheses(frame.shape, e, geo, abs(frame - bg))
    assert e == before
    assert len(result["candidates"]) == 2
    assert [c["body_id"] for c in result["candidates"]] == [0, 1]
    strong = {tuple(p) for p in e["strong_points"]}
    for candidate in result["candidates"]:
        assert {tuple(p) for p in candidate["current_strong_points"]} <= strong
        assert candidate["curve_is_inferred"]
    assert result["physical_pose"] is result["strike"] is result["selected_candidate"] is None
    assert not result["measurement_eligible"]


def test_empty_input_cannot_create_contour_from_a_shaft_axis():
    frame, bg, var, geo = inputs()
    e = observe_parts(bg.astype(np.uint8), bg, var, geo)
    result = contour_hypotheses(frame.shape, e, geo, np.zeros_like(bg))
    assert result["candidates"] == []
    assert result["status"] == "withheld_no_current_region"


def test_current_edge_chain_uses_outer_boundary_above_internal_ridge():
    edges = np.zeros((80, 100), bool)
    edges[30, 20:71] = True
    edges[45, 20:71] = True
    change = np.zeros(edges.shape)
    change[31:61, 20:71] = 5
    result = trace_column(
        np.array([70, 30]),
        np.array([[20, 30], [20, 31], [20, 32]]),
        np.array([20, 50]),
        np.eye(2),
        1,
        4,
        edges,
        change,
        np.ones_like(edges),
    )
    assert result["current_edge_fraction"] == 1
    assert np.asarray(result["path"])[:, 1] == pytest.approx(30)


def test_no_neck_support_retains_region_without_fabricating_a_connection():
    frame, bg, var, geo = inputs()
    e = observe_parts(frame, bg, var, geo)
    geo = {**geo, "anchor": [12, 65]}
    result = contour_hypotheses(frame.shape, e, geo, abs(frame - bg))
    assert result["candidates"]
    assert result["neck_hypotheses"] == 0
    assert all(c["chains"] == [] for c in result["candidates"])


def test_large_self_crossing_is_withheld_rather_than_filled():
    result = repair([[0, 0], [20, 20], [0, 20], [20, 0]])
    assert result["outline"] is None
    assert result["status"] == "withheld_ambiguous_crossing"


@pytest.mark.parametrize("bad", [np.nan, -1])
def test_invalid_change_rejected(bad):
    frame, bg, var, geo = inputs()
    e = observe_parts(frame, bg, var, geo)
    change = abs(frame - bg)
    change[0, 0] = bad
    with pytest.raises(ValueError):
        contour_hypotheses(frame.shape, e, geo, change)
