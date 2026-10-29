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


def test_range_sources_in_order():
    run = session.RunRecord(
        {}, {}, {}, {}, {"selected_range_m": 1.6, "selected_uncertainty_m": 0.05}
    )
    assert session.shot_range_m({"ball_range_m": 1.5, "ball_range_source": "iwr"}, run, None)[
        2
    ] == ("shot_iwr")
    assert session.shot_range_m({}, run, None) == (1.6, 0.05, "run_tee_range_selected")
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
