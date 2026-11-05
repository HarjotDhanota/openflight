"""Saved calibrated inputs must bind to this archive, rig, mode and observed pose."""

import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server, shot_metrics
from openflight.camera import calibrated_projection
from openflight.rig_geometry import geometry_fingerprint
from scripts.analysis import analyze_tester_session as analyzer
from scripts.analysis.replay_raw_fusion import replay
from tests.test_calibrated_full_shot_replay import calibrated_signals
from tests.test_full_shot_replay import prepared_signals


def _read(session):
    return [json.loads(line) for line in session.read_text(encoding="utf-8").splitlines()]


def _write(session, rows):
    session.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _record(rows):
    return next(row for row in rows if row["type"] == "shot_detected")


def _rehash_context(context):
    geometry = context["geometry"]
    geometry["sha256"] = geometry_fingerprint(geometry["parameters"])
    context["sha256"] = geometry_fingerprint(
        {key: value for key, value in context.items() if key != "sha256"}
    )


def _full(root, session):
    return replay(analyzer._replay_args(session, 1, root))["stages"]["full_shot"]


def _assert_own_ops(result):
    assert result["session_uuid"] == "session-bundle" and result["shot_number"] == 1
    assert set(result["metrics"]) == set(shot_metrics.METRIC_IDS)
    assert len(result["metrics"]) == 37
    assert result["metrics"]["ball_speed"]["status"] == "measured"
    assert result["metrics"]["ball_speed"]["value"] == pytest.approx(100.7, abs=0.3)


def _assert_camera_refused(result):
    _assert_own_ops(result)
    assert result["status"] == "partial"
    assert result["errors"]["camera"]
    assert result["calibration"]["status"] == "refused"
    context = result["camera_fusion_context"]
    assert context is None or context["available"] is False
    assert result["impact"]["status"] == "rejected"
    for key in ("impact_offset", "impact_height", "dynamic_lie"):
        assert result["metrics"][key]["status"] == "not_measured"
        assert result["metrics"][key]["value"] is None


@pytest.mark.parametrize(
    "fault",
    [
        "nested_hash",
        "context_archive_hash",
        "rig_hash",
        "selected_lens",
        "session_pair_hash",
        "missing_pose",
        "pose_disagrees",
        "missing_payload",
        "missing_camera_raw",
    ],
)
def test_calibrated_binding_faults_refuse_camera_without_losing_own_ops(tmp_path, fault):
    root, session = calibrated_signals(tmp_path)
    rows = _read(session)
    config, record = rows[0]["config"], _record(rows)
    context = record["camera_fusion_context"]
    parameters = context["geometry"]["parameters"]
    if fault == "nested_hash":
        parameters["calibrated_model_snapshot"]["sha256"] = "0" * 64
        _rehash_context(context)
    elif fault == "context_archive_hash":
        context["capture_npz_sha256"] = "0" * 64
        _rehash_context(context)
    elif fault == "rig_hash":
        config["rig_geometry"]["snapshot"]["sha256"] = "0" * 64
    elif fault == "selected_lens":
        config["camera_capture"]["lens"]["lens_id"] = "another-lens"
    elif fault == "session_pair_hash":
        config["camera_calibrated_fusion"]["pairs"]["640x400"]["placement_sha256"] = "0" * 64
    elif fault == "missing_pose":
        record.pop("inclinometer")
    elif fault == "pose_disagrees":
        record["inclinometer"]["calibrated_pitch_deg"] = 0.25
    elif fault == "missing_payload":
        parameters["calibrated_model_snapshot"] = None
        _rehash_context(context)
    else:
        next(session.parent.rglob("frames.npz")).unlink()
    _write(session, rows)
    saved_bytes = session.read_bytes()
    result = _full(root, session)
    _assert_camera_refused(result)
    assert session.read_bytes() == saved_bytes


def test_actual_array_dimensions_must_match_calibration_even_when_metadata_says_they_do(tmp_path):
    root, session = calibrated_signals(tmp_path)
    rows = _read(session)
    path = next(session.parent.rglob("frames.npz"))
    with np.load(path, allow_pickle=False) as saved:
        archive = {key: saved[key] for key in saved.files}
    archive["frames"] = archive["frames"][:, :, :-1]
    np.savez(path, **archive)
    context = _record(rows)["camera_fusion_context"]
    context["capture_npz_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _rehash_context(context)
    _write(session, rows)
    result = _full(root, session)
    _assert_camera_refused(result)
    assert "dimensions" in result["errors"]["camera"]


def test_missing_embedded_pair_never_uses_the_current_operator_calibration(tmp_path, monkeypatch):
    root, session = calibrated_signals(tmp_path)
    rows = _read(session)
    context = _record(rows)["camera_fusion_context"]
    context["geometry"]["parameters"]["calibrated_model_snapshot"] = None
    _rehash_context(context)
    _write(session, rows)
    calls = []

    def forbidden_current_calibration(*_args, **_kwargs):
        calls.append("current calibration")
        raise AssertionError("offline replay consulted current operator calibration")

    current = SimpleNamespace(pair_for=forbidden_current_calibration)
    lens = SimpleNamespace(lens_id="operator-lens")
    monkeypatch.setattr(server, "camera_calibration_set", current)
    monkeypatch.setattr(server, "camera_lens", lens)
    monkeypatch.setattr(
        calibrated_projection, "load_calibration_dir", forbidden_current_calibration
    )
    result = _full(root, session)
    _assert_camera_refused(result)
    assert calls == []
    assert server.camera_calibration_set is current and server.camera_lens is lens


def test_set_wide_calibration_does_not_turn_an_unpaired_nominal_mode_into_calibrated_intent(
    tmp_path,
):
    root, session = prepared_signals(tmp_path)
    rows = _read(session)
    rows[0]["config"]["camera_capture"]["lens"] = {"lens_id": "lens-1", "intrinsics": "nominal"}
    _write(session, rows)
    nominal = _full(root, session)
    rows[0]["config"]["camera_capture"]["lens"]["intrinsics"] = "calibrated"
    rows[0]["config"]["camera_calibrated_fusion"] = {
        "enabled": True,
        "status": "loaded",
        "active_mode": "320x200",
        "principal_point": "image_centre",
        "in_use": None,
        "pairs": {"640x400": {"mode": "640x400", "lens_id": "lens-1"}},
    }
    _write(session, rows)
    result = _full(root, session)
    _assert_own_ops(result)
    assert result["calibration"]["status"] == "not_requested"
    assert "camera" not in result["errors"]
    assert result["camera_fusion_context"]["available"] is True
    assert result["metrics"] == nominal["metrics"]


def test_fresh_calibrated_chain_ignores_saved_composed_origins(tmp_path):
    root, session = calibrated_signals(tmp_path)
    baseline = _full(root, session)
    rows = _read(session)
    context = _record(rows)["camera_fusion_context"]
    snapshot = context["geometry"]["parameters"]["calibrated_model_snapshot"]
    snapshot["origins"] = {
        "camera_origin_lfu": [0.2, 0.3, 0.4],
        "radar_origin_lfu": [0.5, 0.6, 0.7],
        "basis": "old_composed_outputs",
    }
    snapshot["sha256"] = geometry_fingerprint(
        {key: value for key, value in snapshot.items() if key != "sha256"}
    )
    _rehash_context(context)
    _write(session, rows)
    result = _full(root, session)
    assert result["calibration"]["status"] == "restored"
    assert result["errors"] == {}
    assert result["metrics"] == baseline["metrics"]
    assert result["impact"] == baseline["impact"]
    fresh = result["camera_fusion_context"]["geometry"]["parameters"]["calibrated_model_snapshot"]
    original = baseline["camera_fusion_context"]["geometry"]["parameters"][
        "calibrated_model_snapshot"
    ]
    assert fresh["origins"] == original["origins"]
