"""Address identity must not revive a setup seed or a rejected camera prior."""

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server
from openflight.camera import ball_at_address as baa
from tests.test_ball_at_address import BOX, SETUP, _scene
from tests.test_server_ball_range import _shot

A = [640.0, 450.0, 20.0]
B = [700.0, 420.0, 16.0]


def monitor():
    return baa.BallAtAddress(box_px=BOX, setup_ball=SETUP, setup_range_m=2.2)


def look(locator, candidate, at, **kwargs):
    return locator.observe(
        np.zeros((800, 1280), np.uint8),
        at=at,
        locate=lambda *_args, **_kwargs: {"in_box": candidate, "outside": None},
        **kwargs,
    )


def test_a_replacement_needs_two_fresh_observations_and_carries_no_prior_until_then():
    locator = monitor()
    look(locator, A, 1.0)
    pending = look(locator, B, 2.0)
    assert pending["center_px"] is None
    assert pending["range_m"] is None
    assert pending["range_prior_rejection"]
    confirmed = look(locator, B, 3.0)
    assert confirmed["center_px"] == B[:2]
    assert confirmed["range_m"] == pytest.approx(2.75)


def test_the_same_captured_frame_cannot_confirm_a_replacement():
    locator = monitor()
    look(locator, A, 1.0, frame_id=1)
    pending = look(locator, B, 2.0, frame_id=2)
    repeated = look(locator, B, 3.0, frame_id=2)
    assert repeated["observed_at"] == pending["observed_at"]
    assert repeated["range_m"] is None


def test_a_missed_look_cannot_reset_a_changed_winner_to_initial_acquisition():
    locator = monitor()
    look(locator, A, 1.0)
    look(locator, None, 2.0)
    pending = look(locator, B, 3.0)
    assert pending["state"] == "acquiring"
    assert pending["range_m"] is None
    assert look(locator, B, 4.0)["state"] == "in_box"


def test_a_currently_visible_old_ball_makes_a_new_winner_ambiguous():
    image = _scene([tuple(A), tuple(B)])
    winner = SimpleNamespace(x=B[0], y=B[1], diameter_px=B[2])
    result = baa.locate(image, BOX, None, 1.0, find=lambda *_args: winner, anchor=A)
    assert result["ambiguous"] is True
    assert result["in_box"] is None
    assert len(result["candidates"]) == 2


def test_a_removed_old_ball_does_not_block_reacquisition():
    image = _scene([tuple(B)])
    winner = SimpleNamespace(x=B[0], y=B[1], diameter_px=B[2])
    result = baa.locate(image, BOX, None, 1.0, find=lambda *_args: winner, anchor=A)
    assert result["in_box"] == B
    assert not result.get("ambiguous")


def test_a_worker_failure_is_unavailable_not_a_fresh_old_answer():
    locator = monitor()
    look(locator, A, 1.0)

    def failed(*_args, **_kwargs):
        raise baa.LookUnavailable("worker unavailable")

    with pytest.raises(baa.LookUnavailable):
        locator.observe(np.zeros((800, 1280), np.uint8), at=2.0, locate=failed)
    latest = locator.for_shot(2.5)
    assert latest["state"] == "unavailable"
    assert latest["range_prior_rejection"]


def test_shot_evidence_is_frozen_before_live_history_changes(monkeypatch):
    values = [{"state": "in_box", "center_px": A[:2], "diameter_px": A[2], "range_m": 2.2}]
    monkeypatch.setattr(server, "shot_ball_at_address", lambda _at: deepcopy(values[0]))
    shot = _shot()
    first = server._shot_ball_context(shot)
    values[0] = {"state": "in_box", "center_px": B[:2], "diameter_px": B[2]}
    assert server._shot_ball_context(shot) == first
    first["look"]["center_px"][0] = 0.0
    assert server._shot_ball_context(shot)["look"]["center_px"] == A[:2]


@pytest.mark.parametrize("state", ["ambiguous", "acquiring", "not_seen", "unavailable"])
def test_an_unresolved_look_cannot_restore_the_setup_reference(monkeypatch, state):
    monkeypatch.setattr(server, "camera_capture_config", {"setup_ball": dict(SETUP)})
    monkeypatch.setattr(server, "shot_ball_at_address", lambda _at: {"state": state})
    assert server.shot_reference_ball(100.0) is None
    shot = _shot()
    server._record_ball_reference_check(shot, None)
    assert shot.input_sources["ball_reference"]["range_prior_rejection"]


def test_no_fresh_look_with_a_live_monitor_rejects_the_default_camera_prior(monkeypatch):
    monkeypatch.setattr(server, "ball_at_address_monitor", monitor())
    monkeypatch.setattr(server, "camera_capture_config", {"setup_ball": dict(SETUP)})
    shot = _shot()
    context = server._shot_ball_context(shot)
    assert context["seed"] is None
    assert context["range_prior_rejection"]


def test_enrichment_freezes_the_look_before_the_prefetch_worker_runs(monkeypatch):
    current = [{"state": "in_box", "center_px": A[:2], "diameter_px": A[2]}]
    seen = {}
    monkeypatch.setattr(server, "shot_ball_at_address", lambda _at: deepcopy(current[0]))

    def prefetch(_shot):
        current[0] = {"state": "in_box", "center_px": B[:2], "diameter_px": B[2]}
        return server._CameraPrefetchResult(None, None, 0.0, 0.0, 0.0)

    def snapshot(**kwargs):
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(server, "camera_capture_runtime", object())
    monkeypatch.setattr(server, "_prefetch_camera_capture", prefetch)
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(
            replay_config_snapshot=snapshot,
            process_shot=lambda **_kwargs: SimpleNamespace(
                capture=None, measurement=None, club_path=None
            ),
        ),
    )
    for name in (
        "_snapshot_inclinometer_for_shot",
        "_attach_camera_replay",
        "_fuse_camera_measurements",
    ):
        monkeypatch.setattr(server, name, lambda *_args: None)
    monkeypatch.setattr(server, "kld7_vertical", None)
    monkeypatch.setattr(server, "kld7_horizontal", None)
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    shot = _shot()
    server._enrich_shot_from_optional_hardware(shot)
    assert seen["ball_at_address"]["center_px"] == A[:2]
    assert shot.input_sources["ball_at_address"]["seed"]["x"] == A[0]


def test_unresolved_identity_is_forwarded_even_without_a_clip(monkeypatch):
    seen = {}

    def process(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(capture=None, measurement=None, club_path=None)

    monkeypatch.setattr(server, "shot_ball_at_address", lambda _at: {"state": "ambiguous"})
    monkeypatch.setattr(server, "iwr6843_runtime", SimpleNamespace(process_shot=process))
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    shot = _shot()
    server._record_ball_reference_check(shot, None)
    server._process_iwr6843_angle(shot)
    assert seen["range_prior_rejection"]
    assert shot.input_sources["ball_reference"]["status"] == "unavailable"


def test_a_repeated_frame_does_no_additional_detector_work():
    locator = monitor()
    calls = []

    def search(*_args, **_kwargs):
        calls.append(1)
        return {"in_box": A, "outside": None}

    image = np.zeros((800, 1280), np.uint8)
    for at in (1.0, 2.0, 3.0):
        locator.observe(image, at=at, frame_id=1, locate=search)
    assert len(calls) == 1
    assert locator.latest()["observed_at"] == 1.0


def test_an_older_result_cannot_overwrite_a_more_recent_address():
    locator = monitor()
    recent = look(locator, A, 10.0)
    assert look(locator, B, 9.0) == recent
    assert locator.latest() == recent


def test_a_shot_refuses_an_address_older_than_the_exposure_look_limit():
    locator = monitor()
    look(locator, A, 1.0)
    assert locator.for_shot(6.1) is None


@pytest.mark.parametrize("reader", ["exposure", "endpoint", "readiness"])
def test_a_stopped_camera_cannot_leave_a_ready_live_address(monkeypatch, reader):
    locator = monitor()
    original = look(locator, A, 1.0)
    monkeypatch.setattr(server, "ball_at_address_monitor", locator)
    monkeypatch.setattr(server.time, "time", lambda: 6.1)
    if reader == "endpoint":
        current = server.app.test_client().get("/api/ball-at-address").get_json()
    elif reader == "readiness":
        for name in ("monitor", "iwr6843_runtime", "camera_capture_runtime", "setup_service"):
            monkeypatch.setattr(server, name, None)
        monkeypatch.setattr(server.kiosk_extensions, "adjust_ready_light", lambda states: states)
        current = server._ready_light_states().ball_look
    else:
        current = server.resting_ball_look()
    assert current["state"] == "unknown"
    assert current["range_m"] is None
    assert current["center_px"] is None
    assert current["range_prior_rejection"]
    assert locator.latest() == original


def test_an_anchor_fit_error_is_unavailable_not_evidence_the_old_ball_left(monkeypatch):
    from openflight.camera import club_motion

    locator = monitor()
    look(locator, A, 1.0)
    winner = SimpleNamespace(x=B[0], y=B[1], diameter_px=B[2])
    monkeypatch.setattr(baa, "_find_ball", lambda *_args: winner)

    def failed(*_args):
        raise ValueError("fit failed")

    monkeypatch.setattr(club_motion, "measure_resting_ball", failed)
    with pytest.raises(baa.LookUnavailable):
        locator.observe(_scene([tuple(A), tuple(B)]), at=2.0)
    assert locator.latest()["state"] == "unavailable"
    assert locator.latest()["range_m"] is None
