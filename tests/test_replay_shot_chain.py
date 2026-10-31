"""Recorded ball references must reach the live camera gates during replay."""

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server
from openflight.camera import fallback_chain, resting_ball_range
from openflight.rig_geometry import geometry_fingerprint
from scripts.analysis import replay_shot_chain as replay

SEED = {"x": 320.0, "y": 210.0, "diameter_px": 30.0}


def _record():
    return {
        "timestamp": "2026-10-27T12:00:00",
        "shot_number": 1,
        "club": "7-iron",
        "ball_speed_mph": 100.0,
    }


def _context(record, **changes):
    context = {
        "available": True,
        "session_uuid": "session-a",
        "shot_number": record["shot_number"],
        "capture_npz_sha256": "capture-hash",
        "setup_ball": dict(SEED),
        "camera_fallback": {"horizontal_offset_sd_deg": 2.3},
        **changes,
    }
    context["sha256"] = geometry_fingerprint(context)
    record["camera_fusion_context"] = context


@pytest.fixture
def chain(monkeypatch):
    start = {"session_uuid": "session-a"}
    archive = {
        "frames": np.zeros((12, 40, 60), np.uint8),
        "sensor_timestamp_ns": np.arange(12, dtype=np.int64) * 8_333_333,
        "_capture_npz_sha256": "capture-hash",
    }
    line = {"status": "set", "horizontal_offset_deg": 1.0}
    monkeypatch.setattr(server, "setup_handoff_config", {"target_line": line})
    monkeypatch.setattr(server, "camera_capture_config", {"setup_ball": dict(SEED, x=1.0)})
    monkeypatch.setattr(replay, "ops_track_for", lambda *_args: None)
    monkeypatch.setattr(
        replay, "load_clip", lambda *_args: (SimpleNamespace(path="camera/test"), dict(archive))
    )
    monkeypatch.setattr(server, "_finalize_shot_detected", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(replay.kiosk_extensions, "call_all", lambda *_args: None)

    def no_live_observation(*_args):
        raise AssertionError("replay consulted the current address monitor")

    monkeypatch.setattr(server, "shot_ball_at_address", no_live_observation)
    measurements = []

    def measure(_frames, *, seed, **_kwargs):
        measurements.append(seed)
        return resting_ball_range.RestingBallSize(
            x=320.1, y=209.9, diameter_px=45.0, diameter_sd_px=1.0, windows=5
        )

    monkeypatch.setattr(resting_ball_range, "measure_resting_ball_size", measure)
    fused = []

    def fuse(shot, _capture, clip):
        fused.append((deepcopy(shot.input_sources), deepcopy(server.setup_handoff_config)))
        reference = (shot.input_sources or {}).get("ball_reference") or {}
        resolution = fallback_chain.ball_range_resolution(
            shot_range={"ball_range_m": 3.2, "ball_range_source": "camera_prior"},
            prior=SimpleNamespace(range_m=3.2, uncertainty_m=0.5),
            range_prior_rejection=reference.get("range_prior_rejection"),
        )
        fused[-1] += (resolution, clip.get(fallback_chain.RESTING_BALL_KEY))

    monkeypatch.setattr(server, "_fuse_camera_measurements", fuse)
    return SimpleNamespace(start=start, fused=fused, measurements=measurements, line=line)


def test_rebuild_copies_address_evidence_without_copying_model_outputs():
    record = _record()
    record["input_sources"] = {
        "ball_at_address": {"look": {"state": "in_box"}, "seed": dict(SEED)},
        "ball_reference": {"status": "conflict", "observed": {"x": 321.0}},
        "spin": {"value": 5000.0},
    }
    shot = replay.rebuild_shot(record, ignore_iwr=True)
    assert set(shot.input_sources) == {"ball_at_address", "ball_reference"}
    shot.input_sources["ball_at_address"]["seed"]["x"] = 10.0
    shot.input_sources["ball_reference"]["observed"]["x"] = 10.0
    assert record["input_sources"]["ball_at_address"]["seed"] == SEED
    assert record["input_sources"]["ball_reference"]["observed"]["x"] == 321.0


def test_legacy_seed_reaches_cached_reference_gate_before_fusion(chain, tmp_path):
    record = _record()
    _context(record)
    original = deepcopy(record)
    result = replay.replay_shot([], chain.start, record, tmp_path, ignore_iwr=False)
    sources, _config, resolution, measure = chain.fused[0]
    assert sources["ball_at_address"]["seed"] == SEED
    assert sources["ball_at_address"]["look"] is None
    assert sources["ball_reference"]["status"] == "conflict"
    assert not resolution.present
    assert measure.seed == (320.0, 210.0, 30.0)
    assert chain.measurements == [(320.0, 210.0, 30.0)]
    assert result["replay_inputs"]["ball_seed_source"] == "recorded_context"
    assert any("freshness" in note for note in result["replay_inputs"]["limitations"])
    assert record == original


def test_recorded_unresolved_address_is_not_replaced_by_legacy_seed(chain, tmp_path):
    record = _record()
    _context(record)
    address = {
        "look": {"state": "unresolved"},
        "seed": None,
        "range_prior_rejection": "address was unresolved",
    }
    record["input_sources"] = {"ball_at_address": address}
    replay.replay_shot([], chain.start, record, tmp_path, ignore_iwr=False)
    sources = chain.fused[0][0]
    assert sources["ball_at_address"] == address
    assert sources["ball_reference"]["range_prior_rejection"] == "address was unresolved"
    assert chain.measurements == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_uuid", "other-session"),
        ("shot_number", 2),
        ("capture_npz_sha256", "other-capture"),
        ("sha256", "tampered"),
    ],
)
def test_context_from_another_shot_or_capture_refuses(chain, tmp_path, field, value):
    record = _record()
    _context(record, **({field: value} if field != "sha256" else {}))
    if field == "sha256":
        record["camera_fusion_context"][field] = value
    with pytest.raises(ValueError, match="context"):
        replay.replay_shot([], chain.start, record, tmp_path, ignore_iwr=False)
    assert chain.fused == []
    assert chain.measurements == []


def test_recorded_aim_is_per_shot_and_explicit_override_wins(chain, tmp_path):
    record = _record()
    _context(record)
    replay.replay_shot([], chain.start, record, tmp_path, ignore_iwr=False)
    assert chain.fused[-1][1]["target_line"]["total_uncertainty_deg"] == 2.3
    assert server.setup_handoff_config["target_line"] is chain.line
    result = replay.replay_shot([], chain.start, _record(), tmp_path, ignore_iwr=False)
    assert "total_uncertainty_deg" not in chain.fused[-1][1]["target_line"]
    assert any("aim" in note for note in result["replay_inputs"]["limitations"])
    replay.replay_shot([], chain.start, record, tmp_path, ignore_iwr=False, aim_uncertainty_deg=4.0)
    assert chain.fused[-1][1]["target_line"]["total_uncertainty_deg"] == 4.0
    assert "total_uncertainty_deg" not in chain.line


def test_missing_address_uses_only_labelled_session_seed(chain, tmp_path):
    result = replay.replay_shot([], chain.start, _record(), tmp_path, ignore_iwr=False)
    assert chain.fused[0][0]["ball_at_address"]["seed"]["x"] == 1.0
    assert result["replay_inputs"]["ball_seed_source"] == "session_setup"
    assert any("per-shot" in note for note in result["replay_inputs"]["limitations"])


def test_recorded_reference_rejection_survives_unavailable_refit(chain, tmp_path, monkeypatch):
    record = _record()
    _context(record)
    reference = {"status": "conflict", "range_prior_rejection": "recorded reference conflict"}
    record["input_sources"] = {"ball_reference": reference}
    monkeypatch.setattr(resting_ball_range, "measure_resting_ball_size", lambda *_a, **_k: None)
    replay.replay_shot([], chain.start, record, tmp_path, ignore_iwr=False)
    sources, _config, resolution, _measure = chain.fused[0]
    assert sources["ball_reference"]["range_prior_rejection"] == "recorded reference conflict"
    assert not resolution.present


def test_failed_fusion_does_not_leak_shot_aim(chain, tmp_path, monkeypatch):
    record = _record()
    _context(record)

    def fail(*_args):
        raise RuntimeError("failed fusion")

    monkeypatch.setattr(server, "_fuse_camera_measurements", fail)
    with pytest.raises(RuntimeError, match="failed fusion"):
        replay.replay_shot([], chain.start, record, tmp_path, ignore_iwr=False)
    assert server.setup_handoff_config["target_line"] is chain.line
    assert "total_uncertainty_deg" not in chain.line
