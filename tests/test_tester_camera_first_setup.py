"""Setup step 6 is camera-only; the radar check is a diagnostic (13 Oct decision "a").

The resting ball's distance is no longer measured by static IWR empty/ball captures:
the camera locks the ball in the fixed box and gives a coarse size distance, handed
to the kiosk as a PRIOR. Each swing's IWR capture measures its launch point. The
static captures stay available as an optional "Radar check (diagnostic)", never
required and never the source of a swing's distance.
"""

from __future__ import annotations

import pytest

from openflight import tee_range
from openflight.camera import tester_server as ts
from tests.test_tester_tee_range_flow import app_for, make_inputs, phase, post


@pytest.fixture
def flow_inputs(tmp_path):
    return make_inputs(tmp_path)


def _camera_only(client, tester):
    for index, action in enumerate(("start", "start_camera_arm5", "evaluate_camera_arm5")):
        response = post(client, tester, action, f"camera-only-{index}")
        assert response.status_code == 200, response.get_json()
    return phase(client, tester)


def _with_radar_check(client, tester):
    actions = (
        "start",
        "capture_empty",
        "capture_ball",
        "start_camera_arm5",
        "evaluate_camera_arm5",
    )
    for index, action in enumerate(actions):
        response = post(client, tester, action, f"radar-check-{index}")
        assert response.status_code == 200, response.get_json()
    return phase(client, tester)


def test_the_setup_starts_at_the_camera_not_at_a_radar_capture(tmp_path, flow_inputs, monkeypatch):
    app, tester = app_for(tmp_path, flow_inputs, monkeypatch)
    client = app.test_client()

    response = post(client, tester, "start", "start")

    assert response.status_code == 200, response.get_json()
    assert phase(client, tester)["phase"] == "needs_camera_arm5"


def test_the_camera_alone_finishes_the_setup_with_a_coarse_prior(
    tmp_path, flow_inputs, monkeypatch
):
    app, tester = app_for(tmp_path, flow_inputs, monkeypatch)
    client = app.test_client()

    state = _camera_only(client, tester)

    assert state["phase"] == "experimental"
    assert state["solution"]["reason"] == ts.CAMERA_PRIOR_SAVED_REASON
    prior = state["evidence"]["camera_prior"]
    assert prior["range_m"] == pytest.approx(1.2)
    assert prior["label"] == "experimental"
    assert prior["display_label"] == "camera estimate (coarse)"
    assert prior["source"] == "camera_size_coarse"
    # coarse: never tighter than the floor on a size range
    assert prior["uncertainty_m"] >= ts.CAMERA_PRIOR_MIN_FRACTION * 1.2 - 1e-9
    assert "iwr_candidate" not in state["evidence"]


def test_the_kiosk_gets_the_camera_estimate_as_a_prior_never_a_hard_range(
    tmp_path, flow_inputs, monkeypatch
):
    app, tester = app_for(tmp_path, flow_inputs, monkeypatch)
    state = _camera_only(app.test_client(), tester)
    solution = tee_range.TeeRangeSolution.from_dict(state["solution"])

    args, record = ts.tee_range_handoff(solution)

    assert not [token for token in args if token.startswith("--iwr6843-tee")]
    uncertainty = record["prior"]["uncertainty_m"]
    assert uncertainty == pytest.approx(state["evidence"]["camera_prior"]["uncertainty_m"])
    assert record["prior"]["source"] == record["tee_range_source"] == "camera_size_coarse"
    assert record["tee_range_status"] == "prior"
    assert record["tee_m"] is None
    assert record["prior"]["range_m"] == pytest.approx(1.2)


def test_the_display_says_the_radar_measures_it_on_each_swing(tmp_path, flow_inputs, monkeypatch):
    app, tester = app_for(tmp_path, flow_inputs, monkeypatch)
    client = app.test_client()
    _camera_only(client, tester)

    display = client.get("/api/tester/tee-range", query_string={"tester_id": tester}).get_json()[
        "display"
    ]

    assert display["canonical"]["state"] == "camera_prior"
    assert display["canonical"]["range_m"] == pytest.approx(1.2)
    assert display["swings"]["state"] == "camera_prior"
    assert display["summary"] == (
        "Ball found · camera estimate 1.20 m (coarse) · the radar measures it on each swing"
    )


def test_the_radar_check_is_kept_as_a_diagnostic_and_never_the_distance(
    tmp_path, flow_inputs, monkeypatch
):
    # the radar double's ball sits at 1.2 m; the camera reads 1.5 m
    app, tester = app_for(tmp_path, flow_inputs, monkeypatch, camera_m=1.5)
    state = _with_radar_check(app.test_client(), tester)
    solution = tee_range.TeeRangeSolution.from_dict(state["solution"])

    _args, record = ts.tee_range_handoff(solution)

    assert state["phase"] == "experimental"
    assert state["evidence"]["camera_prior"]["range_m"] == pytest.approx(1.5)
    assert "radar_check" in state["evidence"]
    assert state["evidence"]["radar_check"]["label"] == "diagnostic"
    assert record["tee_m"] is None
    assert record["prior"]["range_m"] == pytest.approx(1.5)


def test_the_setup_no_longer_calibrates_the_camera_tilt_from_a_static_pair(
    tmp_path, flow_inputs, monkeypatch
):
    app, tester = app_for(tmp_path, flow_inputs, monkeypatch)
    state = _with_radar_check(app.test_client(), tester)

    assert state["evidence"]["camera_tilt"]["action"] == "not_applicable"
    assert "swings" in state["evidence"]["camera_tilt"]["reason"]


def test_without_a_ball_nothing_is_handed_over_and_the_reason_is_kept(
    tmp_path, flow_inputs, monkeypatch
):
    from openflight.camera.reference_ball_range import ReferenceBallRangeResult  # noqa: PLC0415

    app, tester = app_for(tmp_path, flow_inputs, monkeypatch, black_floor_dn=0.0)
    nothing = ReferenceBallRangeResult("not_found", "withheld", None, (), {})
    monkeypatch.setattr(ts, "estimate_patch_ball", lambda *_args, **_kwargs: nothing)
    client = app.test_client()
    for index, action in enumerate(("start", "start_camera_arm5", "save_camera_arm5_diagnostic")):
        response = post(client, tester, action, f"nothing-{index}")
        assert response.status_code == 200, response.get_json()
    state = phase(client, tester)
    solution = tee_range.TeeRangeSolution.from_dict(state["solution"])

    _args, record = ts.tee_range_handoff(solution)

    assert state["phase"] == "raw_only"
    assert record["prior"] is None
    assert record["tee_range_status"] == "pending"
