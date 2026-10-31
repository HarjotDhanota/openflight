"""The post-session impact-location run over a recorded session in the fork's layout."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from openflight.camera import (  # noqa: E402
    impact_location as il,
    impact_session as session,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_camera_impact_location as synthetic  # noqa: E402
from impact_angle_fixtures import bind_test_angles  # noqa: E402
from impact_range_fixtures import bind_test_range  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "impact_location.py"
_spec = importlib.util.spec_from_file_location("impact_location_cli", SCRIPT)
cli = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cli
_spec.loader.exec_module(cli)

RIG = json.loads(
    (Path(__file__).resolve().parents[1] / "config" / "enclosure_v3_rig_geometry.json").read_text(
        encoding="utf-8"
    )
)
CLIP = "camera_20261020_100001_000_001"


def test_explicit_optics_candidate_is_not_silently_ignored(tmp_path):
    from tests.camera_helpers import calibration_artifact

    root = write_session(tmp_path)
    clip = session.find_clips(root)[0]
    run = session.read_run(clip.run_dir)
    options = session.Options(ball=(320, 200, 26))
    options.optical_calibration = calibration_artifact()
    session.prepare_clip(clip, {clip.run_dir: run}, {}, options)
    assert clip.error == "calibrated_optics: raw ball override requires rectified coordinates"
    assert clip.prepared is None


def test_capture_bound_calibrated_context_reaches_impact_and_live_diagnostics(tmp_path):
    from openflight.camera.calibrated_projection import build_calibrated_camera_model
    from openflight.camera.fusion_processing import build_context
    from openflight.camera.impact_live import LiveAnalyser
    from openflight.rig_geometry import geometry_fingerprint
    from tests.camera_helpers import calibration_artifact, camera_placement, mode_profile
    from tests.test_calibrated_projection import _capture_mode
    from tests.test_camera_fusion_processing import _context_kwargs

    root = write_session(tmp_path)
    clip = session.find_clips(root)[0]
    run = session.read_run(clip.run_dir)
    profile = mode_profile()
    profile["crop_readout_mapping"].update(
        scaler_crop="not_applicable_raw_stream", driver_vertical_offset_px=174
    )
    artifact = calibration_artifact(profile)
    model = build_calibrated_camera_model(
        artifact, camera_placement(), observed_pitch_deg=7, observed_roll_deg=-3
    )
    metadata_path = clip.path / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    with np.load(clip.path / "frames.npz", allow_pickle=False) as archive:
        count = len(archive["frames"])
    mode = _capture_mode(model.projection.mode_profile)
    for key, values in mode["frames"].items():
        mode["frames"][key] = [values[0]] * count
    metadata["capture_mode"] = mode
    metadata_path.write_text(json.dumps(metadata))
    geometry = _context_kwargs()["geometry"].with_calibrated_model(
        model.snapshot,
        {
            "status": "unverified",
            "reasons": ["calibration remains unqualified"],
            "mode_profile_sha256": model.projection.mode_profile["sha256"],
        },
    )
    shot = run.shots[1]
    shot["camera_fusion_context"] = build_context(
        **{
            **_context_kwargs(),
            "geometry": geometry,
            "shot_number": 1,
            "session_uuid": run.start["session_uuid"],
            "capture_npz_sha256": shot["impact_range_evidence"]["camera_capture_sha256"],
        }
    )
    job = {
        "session_uuid": run.start["session_uuid"],
        "shot_number": 1,
        "clip_path": str(clip.path),
        "shot": shot,
        "start": run.start["config"],
    }
    reading = LiveAnalyser(analysis_max_width=320).analyse(job)
    decision = reading["optics_input_decision"]
    assert decision["applied"] and not decision["research_override"]
    assert decision["depth_origin_source"] == "recorded_calibrated_placement"
    assert decision["analysis_binning_factor"] == 2
    assert reading["range_input_decision"]["accepted"]
    assert reading["dynamic_lie"]["status"] == "not_measured"
    assert (
        reading["dynamic_lie"]["reason"]
        == "rectified optics need a full-placement shaft-plane estimator"
    )
    context = shot["camera_fusion_context"]
    context["capture_npz_sha256"] = "b" * 64
    context.pop("sha256")
    context["sha256"] = geometry_fingerprint(context)
    refused = LiveAnalyser().analyse(job)
    assert refused["status"] == "rejected"
    assert "does not match" in refused["optics_input_decision"]["reason"]
    assert not refused["optics_input_decision"]["applied"]
    shot["calibrated_camera_status"] = "rejected"
    shot["camera_fusion_context"] = {"available": False, "reason": "mode mismatch"}
    refused = LiveAnalyser().analyse(job)
    assert refused["status"] == "rejected"
    assert (
        refused["optics_input_decision"]["reason"]
        == "declared calibrated camera was rejected upstream"
    )


def _rig_parameters() -> dict:
    return {**RIG, "iwr_offset_mm": [0.0, 0.0, 0.0], "lens_height_above_floor_mm": 95.0}


def _slant_m() -> float:
    drop = synthetic.CAMERA_HEIGHT_MM - il.BALL_RADIUS_MM
    return math.sqrt(20.0**2 + drop**2 + synthetic.BALL_RANGE_MM**2) / 1000.0


def write_session(
    tmp_path: Path,
    *,
    handed: str = "RH",
    truth_mm: float = 8.0,
    mirror_pixels: bool = False,
    range_source: str = "iwr_post_impact",
    render: dict | None = None,
) -> Path:
    frames, _ball = synthetic.render_swing(truth_mm=truth_mm, **(render or {}))
    if mirror_pixels:
        frames = np.ascontiguousarray(frames[:, :, ::-1])
    run = tmp_path / "tester" / "arm6" / "paired" / "run-01"
    clip = run / "arm6" / "camera" / CLIP
    clip.mkdir(parents=True)
    np.savez(
        clip / "frames.npz",
        frames=frames,
        pre_trigger_count=np.int32(11),
        exposure_us=np.full(len(frames), 300.0),
        sensor_timestamp_ns=1_000_000_000
        + np.arange(len(frames), dtype=np.int64) * round(1e9 / synthetic.FPS),
    )
    (clip / "metadata.json").write_text(
        json.dumps({"delivered_fps": synthetic.FPS, "settings": {"mirror_horizontal": False}}),
        encoding="utf-8",
    )
    start = {
        "type": "session_start",
        "session_uuid": "synthetic-session",
        "config": {
            "golfer": {"handed": handed, "source": "setup"},
            "rig_geometry": {
                "snapshot": {"parameters": _rig_parameters()},
                "lens": {"focal_px_by_arm": {"arm5": 1866.67, "arm6": synthetic.FOCAL}},
            },
        },
    }
    shot = {
        "type": "shot_detected",
        "shot_number": 1,
        "club": "7-iron",
        "club_speed_mph": synthetic.CLUB_SPEED / session.MPH_TO_MPS,
        "ball_range_m": _slant_m(),
        "ball_range_source": range_source,
        "ball_range_uncertainty_m": 0.03,
        "metrics": {
            "dynamic_loft": {"value": 0.0, "status": "calculated"},
            "face_angle": {"value": 0.0, "status": "calculated", "frame": "unit_boresight"},
            "attack_angle": {"value": -2.0, "status": "measured"},
        },
    }
    bind_test_angles(shot, clip / "frames.npz")
    bind_test_range(shot, clip / "frames.npz", "synthetic-session")
    events = [
        start,
        {"type": "camera_capture", "shot_number": 1, "capture_path": f"/srv/sessions/{CLIP}"},
        shot,
        {"type": "face_photo", "shot_number": 1, "path": "face-photos/shot-0001.pgm"},
    ]
    (run / "session_20261020_100000_arm6.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    (run / "face-photos").mkdir()
    cv2.imwrite(str(run / "face-photos" / "shot-0001.pgm"), np.zeros((80, 128), np.uint8))
    return tmp_path / "tester"


def _anchors(tmp_path: Path) -> Path:
    path = tmp_path / "anchors.json"
    path.write_text(
        json.dumps(
            {
                "schema": session.ANCHOR_SCHEMA,
                "clubs": {
                    "7-iron": {
                        "value_mm": synthetic.FACE_CENTRE_HEIGHT_MM,
                        "sigma_mm": 2.0,
                        "source": "face_photo_marks_n3",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_the_cli_reads_both_axes_from_a_recorded_session(tmp_path):
    root = write_session(tmp_path)
    truth = tmp_path / "truth.csv"
    truth.write_text(f"shot_id,impact_offset_mm,impact_height_mm\n{CLIP},7.0,-2.0\n")
    out = tmp_path / "out"
    assert (
        cli.main(
            [
                str(root),
                "--out",
                str(out),
                "--anchors",
                str(_anchors(tmp_path)),
                "--truth",
                str(truth),
            ]
        )
        == 0
    )

    report = json.loads((out / "impact_location.json").read_text(encoding="utf-8"))
    row = report["rows"][0]
    assert row["label"] == "experimental_unqualified"
    assert row["status"] == "ok", row["reason"]
    assert row["height_status"] == "ok", row["height_reason"]
    assert row["club"] == "7-iron" and row["handedness"] == "right"
    assert row["club_speed_source"] == "shot_ops_club_speed"
    assert row["ball_range_source"] == "shot_iwr_post_impact"
    assert row["ball_range_mm"] == pytest.approx(synthetic.BALL_RANGE_MM, abs=1.0)
    assert row["face_centre_height_source"] == "face_photo_marks_n3"
    assert row["face_centre_from_toe_source"] == "reference_geometry_face_centre_rule"
    assert row["dynamic_loft_source"] == "synthetic_independent_angle"
    assert row["face_photo"] == "face-photos/shot-0001.pgm"
    assert row["impact_offset_mm"] == pytest.approx(8.0, abs=2.5)
    assert row["impact_height_mm"] == pytest.approx(synthetic.height_truth(0.0, 0.0), abs=2.5)
    assert row["impact_offset_sigma_mm"] > 0 and row["impact_height_sigma_mm"] > 0
    assert report["agreement"]["impact_offset_mm"]["n"] == 1
    assert report["agreement"]["impact_height_mm"]["n"] == 1
    assert (out / "overlays" / f"{CLIP}.png").is_file()
    header = (out / "impact_location.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "impact_height_sigma_mm" in header and "height_reason" in header
    assert "longest score line" in report["caveat"] and "midpoint" not in report["caveat"]


def test_the_cli_moves_the_face_centre_only_through_the_research_override(tmp_path):
    parser = cli.build_parser()
    flags = {flag for action in parser._actions for flag in action.option_strings}
    assert "--face-centre-offset-mm" not in flags
    assert parser.parse_args(["session"]).face_centre_from_toe_mm is None
    assert session.Options().face_centre_from_toe_mm is None
    root = write_session(tmp_path)
    default = session.analyse_session(root)
    moved = session.analyse_session(
        root, session.Options(face_centre_from_toe_mm=synthetic.FACE_CENTRE_FROM_TOE_MM - 10.0)
    )
    row, shifted = default["rows"][0], moved["rows"][0]
    assert shifted["impact_offset_mm"] - row["impact_offset_mm"] == pytest.approx(-10.0, abs=1e-6)
    assert shifted["face_centre_from_toe_source"] == "research_override"
    assert "research override" in moved["caveat"]


def test_a_left_handed_session_is_read_through_the_mirror(tmp_path):
    root = write_session(tmp_path, handed="LH", mirror_pixels=True)
    report = session.analyse_session(root)
    row = report["rows"][0]
    assert row["handedness"] == "left"
    assert row["status"] == "ok", row["reason"]
    assert row["impact_offset_mm"] == pytest.approx(8.0, abs=2.5)


def test_a_camera_range_prior_is_never_used_and_the_refusal_is_named(tmp_path):
    root = write_session(tmp_path, range_source="camera_prior")
    row = session.analyse_session(root, session.Options(ball=(320.0, 200.0, 25.0)))["rows"][0]
    assert row["status"] == "rejected"
    assert row["reason"] == "no_ball_range_for_this_shot"
    assert row["height_reason"] == "no_ball_range_for_this_shot"
    assert row["label"] == "experimental_unqualified"


def test_unbound_range_sources_are_refused_and_explicit_research_override_remains():
    run = session.RunRecord(
        {}, {}, {}, {}, {"selected_range_m": 1.6, "selected_uncertainty_m": 0.05}
    )
    assert session.shot_range_m({"ball_range_m": 1.5, "ball_range_source": "iwr"}, run, None) == (
        None,
        None,
        "none",
    )
    assert session.shot_range_m({}, run, None) == (None, None, "none")
    empty = session.RunRecord({}, {}, {}, {}, None)
    assert session.shot_range_m({}, empty, 2.0) == (2.0, None, "command_line")
    assert session.shot_range_m({}, empty, None) == (None, None, "none")


def test_handedness_defaults_to_right_and_reads_the_session_record():
    assert session.session_handedness({}) == ("right", "default_right_handed")
    lefty = {"config": {"golfer": {"handed": "LH", "source": "setup"}}}
    assert session.session_handedness(lefty) == ("left", "session_setup")


def test_a_shots_own_hand_beats_the_session_record():
    """The hand can change mid-session, so each shot's recorded hand comes first."""
    lefty = {"config": {"golfer": {"handed": "LH", "source": "setup"}}}
    assert session.shot_handedness({"handed": "RH"}, lefty) == ("right", "shot_record")
    assert session.shot_handedness({"handed": "LH"}, {}) == ("left", "shot_record")
    assert session.shot_handedness({}, lefty) == ("left", "session_setup")
    assert session.shot_handedness({"handed": "left"}, {}) == ("right", "default_right_handed")


def test_anchors_must_carry_their_schema(tmp_path):
    path = tmp_path / "anchors.json"
    path.write_text(json.dumps({"schema": "other", "clubs": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        session.read_anchors(path)


def test_a_named_lens_takes_its_focal_length_from_the_lens_profiles():
    rig, _source = session.session_rig({})
    stock = session.focal_px({}, rig, 1280)
    six = session.focal_px({}, rig, 1280, lens_id="6mm")
    assert stock[1] == "rig_file_lens_nominal"
    assert six[1] == "lens_profile_6mm_nominal"
    assert six[0] == pytest.approx(stock[0] * 6.0 / 2.8)
    recorded = {"config": {"rig_geometry": {"lens": {"focal_px_by_arm": {"arm5": 1500.0}}}}}
    assert session.focal_px(recorded, rig, 1280, lens_id="6mm")[1] == "session_lens_nominal"


WIDE = {"width": 1280, "height": 800, "focal": 2.0 * synthetic.FOCAL}


def test_a_wide_clip_is_binned_to_the_analysis_width_and_read_the_same(tmp_path):
    root = write_session(tmp_path, render=WIDE)
    row = session.analyse_session(root, session.Options(analysis_max_width=640))["rows"][0]
    assert row["frame_size"] == "640x400"
    assert row["status"] == "ok", row["reason"]
    assert row["impact_offset_mm"] == pytest.approx(8.0, abs=2.5)


def test_without_an_analysis_width_the_clip_keeps_its_size():
    frames = np.arange(2 * 4 * 6, dtype=np.uint8).reshape(2, 4, 6)
    assert session.bin_frames(frames, 1) is frames
    binned = session.bin_frames(frames, 2)
    assert binned.shape == (2, 2, 3) and binned.dtype == np.uint8
    assert binned[0, 0, 0] == round(np.mean(frames[0, :2, :2]))


def test_the_lens_height_is_the_shot_s_own_above_the_hitting_surface():
    """surface_height (30 Oct): a solved surface moves the lens height impact uses."""
    from openflight.rig_geometry import RigGeometry

    rig = RigGeometry(**_rig_parameters())
    shot = {
        "scene_heights": {
            "in_force": {
                "source": "solved_session",
                "lens_height_m": 0.027,
                "surface_height_sigma_m": 0.012,
            }
        }
    }

    value, sigma, source = session.shot_lens_height_mm(shot, {}, rig)
    assert (value, sigma, source) == (pytest.approx(27.0), pytest.approx(12.0), "solved_session")
    face = session.shot_face_inputs(
        shot,
        "7-iron",
        "right",
        {},
        lens_height_mm=value,
        lens_height_sigma_mm=sigma,
        lens_height_source=source,
    )
    assert (face.lens_height.value, face.lens_height.sigma, face.lens_height.source) == (
        pytest.approx(27.0),
        pytest.approx(12.0),
        "solved_session",
    )


def test_without_a_solve_the_rig_s_lens_height_stands():
    from openflight.rig_geometry import RigGeometry

    rig = RigGeometry(**_rig_parameters())
    nominal = {"scene_heights": {"in_force": {"source": "rig_nominal", "lens_height_m": 0.095}}}

    for shot in ({}, nominal):
        value, sigma, source = session.shot_lens_height_mm(shot, {}, rig)
        assert value == pytest.approx(95.0)
        assert sigma == pytest.approx(il.LENS_HEIGHT_SIGMA_MM)
        assert source == il.RIG_LENS_HEIGHT


def test_a_session_s_setup_solve_stands_when_the_shot_has_none():
    from openflight.rig_geometry import RigGeometry

    rig = RigGeometry(**_rig_parameters())
    start = {
        "config": {
            "scene": {
                "lens_height_used_m": 0.081,
                "lens_height_used_source": "range_setup",
                "lens_height_solved_uncertainty_m": 0.010,
            }
        }
    }

    assert session.shot_lens_height_mm({}, start, rig) == (
        pytest.approx(81.0),
        pytest.approx(10.0),
        "range_setup",
    )


def test_a_face_angle_comes_into_the_analysis_orientation_through_one_rule():
    convert = session.face_angle_in_analysis_orientation
    assert convert(3.0, "unit_boresight", None, "right") == 3.0
    assert convert(3.0, "target_line", 2.0, "right") == pytest.approx(1.0)
    assert convert(3.0, "target_line", 2.0, "left") == pytest.approx(-1.0)
    assert convert(3.0, "unit_boresight", 2.0, "left") == -3.0
    assert convert(3.0, "target_line", None, "right") is None
