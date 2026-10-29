"""Templates cannot silently transfer across hand, optics, mode or equipment."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera import impact_live, impact_session


@pytest.mark.parametrize("change", ["hand", "mode", "optics", "equipment", "camera"])
def test_context_switch_does_not_reuse_previous_template(change):
    analyser = impact_live.LiveAnalyser()
    clip = SimpleNamespace(
        club="7-iron",
        handedness="right",
        frame_size="640x400",
        extra={"template_capture_context": "mode1"},
        range_source="test",
        error=None,
        prepared=SimpleNamespace(contact=SimpleNamespace(status="bad", reason="test")),
    )
    analyser._prepare = lambda _: clip
    job = {"session_uuid": "s", "equipment_id": "club1"}
    analyser.analyse(job)
    old = next(iter(analyser._pools.values()))
    old.template, old.built_from = "old_template", 5
    if change == "hand":
        clip.handedness = "left"
    elif change == "mode":
        clip.extra["template_capture_context"] = "mode2_same_dimensions"
    elif change == "optics":
        clip.extra["optics_template_key"] = "new_optics"
    elif change == "equipment":
        job["equipment_id"] = "short_hosel_club2"
    else:
        clip.prepared.camera = SimpleNamespace(
            focal_px=1200.0, principal_point_px=(320.0, 200.0), source="different_nominal_lens"
        )
    result = analyser.analyse(job)
    assert result["template"]["built_from"] is None
    assert len(analyser._pools) == 2
    assert old.template == "old_template"


def test_loader_fingerprints_recorded_controls_and_mode_without_claiming_calibration(tmp_path):
    path = tmp_path / "clip"
    path.mkdir()
    np.savez(path / "frames.npz", frames=np.zeros((8, 20, 30), np.uint8))
    metadata = {
        "settings": {"width": 30, "height": 20, "gain": 2},
        "capture_mode": {"unqualified_test": True},
    }
    sidecar = path / "metadata.json"
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")
    clip = impact_session.Clip("test", path, "test")
    impact_session._load_frames(clip)
    before = clip.extra["template_capture_context"]
    metadata["settings"]["gain"] = 3
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")
    impact_session._load_frames(clip)
    assert clip.extra["template_capture_context"] != before
    assert (
        clip.extra["template_context_scope"]
        == "recorded_settings_and_mode_not_physical_qualification"
    )


def test_offline_club_and_session_scopes_also_separate_hand_and_equipment():
    clip = impact_session.Clip("test", None, "test", club="7-iron", frame_size="640x400")
    before = {scope: impact_session._group(clip, scope) for scope in ("club", "session")}
    clip.handedness = "left"
    assert all(impact_session._group(clip, scope) != value for scope, value in before.items())
    hand = impact_session.template_context_key(clip)
    clip.extra["equipment_id"] = "different_7_iron"
    assert impact_session.template_context_key(clip) != hand


def test_different_analysis_roi_origin_preserves_same_full_camera_context():
    clip = impact_session.Clip("test", None, "test", club="7-iron", frame_size="640x400")
    clip.prepared = SimpleNamespace(
        camera=SimpleNamespace(
            focal_px=1000.0, principal_point_px=(120.0, 80.0), source="same_lens"
        ),
        roi_origin=(200, 100),
    )
    first = impact_session.template_context_key(clip)
    clip.prepared = SimpleNamespace(
        camera=SimpleNamespace(
            focal_px=1000.0, principal_point_px=(110.0, 60.0), source="same_lens"
        ),
        roi_origin=(210, 120),
    )
    assert impact_session.template_context_key(clip) == first
