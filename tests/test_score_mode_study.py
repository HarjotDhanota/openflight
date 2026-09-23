"""The scorer reads exports and applies the analysis plan; synthetic arms with a drawn ball."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "score_mode_study.py"
spec = importlib.util.spec_from_file_location("score_mode_study", SCRIPT)
scorer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scorer
spec.loader.exec_module(scorer)


def _frames(width: int, height: int, ball_diameter: float, n: int = 40, *, clip_ball: bool = False):
    """A mid-grey scene with a bright resting ball and a moving blob before the trigger.

    The ground sits at ~110 like a real exposure: on a flat black ground the
    detector's dark-first path reads the ball's blurred halo as a candidate.
    """
    frames = np.full((n, height, width), 110, dtype=np.uint8)
    yy, xx = np.mgrid[0:height, 0:width]
    cx, cy = width * 0.5, height * 0.72
    ball = np.hypot(xx - cx, yy - cy) <= ball_diameter / 2
    frames[:, ball] = 255 if clip_ball else 230
    for i in range(
        22, 29
    ):  # a "clubhead" approaching the ball before the trigger (pre_trigger = 30)
        x0 = int(cx - 60 + (i - 22) * 8)
        frames[i, int(cy) - 10 : int(cy) + 10, x0 : x0 + 14] = 200
    return frames


def _export(
    root: Path,
    arm_id: str,
    width: int,
    height: int,
    fps: float,
    ball_px: float,
    statuses,
    *,
    light_index=0.25,
    clip_ball=False,
    tester_id="t1",
    run="run-01",
) -> Path:
    out = root / tester_id / arm_id / run
    (out / "shots").mkdir(parents=True)
    rows = []
    shots = []
    for i, status in enumerate(statuses, start=1):
        d = f"shots/shot_{i:03d}_7-iron"
        (out / d).mkdir()
        np.savez(
            out / d / "frames.npz",
            frames=_frames(width, height, ball_px, clip_ball=clip_ball),
            exposure_us=np.full(40, 87, np.int32),
            analogue_gain=np.full(40, 6.0, np.float32),
            pre_trigger_count=np.int32(30),
        )
        (out / d / "camera_metadata.json").write_text(
            json.dumps(
                {
                    "delivered_fps": fps,
                    "gap_count": 0,
                    "pre_trigger_frames": 30,
                    "resolved": {"raw": {"size": [width, height], "format": "R8"}},
                }
            )
        )
        rows.append(
            {
                "shot_number": i,
                "dir": d,
                "fused_status": status,
                "experimental_fused_club_path_deg": 2.0 + 0.1 * i,
                "experimental_fused_attack_angle_deg": -4.0,
            }
        )
        shots.append(
            {"shot_number": i, "dir": d, "fused_status": status, "joined_by": "session_jsonl"}
        )
    with (out / "shots.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "contract_version": 1,
                "session_uuid": "u",
                "tester_id": tester_id,
                "arm": {
                    "arm_id": arm_id,
                    "label": f"{width}x{height} @{fps:.0f}",
                    "width": width,
                    "height": height,
                    "fps": fps,
                },
                "environment": {"light_index": light_index},
                "shots": shots,
                "excluded_shots": [],
            }
        )
    )
    return out


class TestPerShot:
    def test_it_finds_the_ball_and_reads_the_capture(self, tmp_path):
        export = _export(tmp_path, "arm1", 320, 200, 450.0, 12.0, ["ok"])
        score, shots = scorer.score_export(export)
        s = shots[0]
        assert s.ball_detected and 10.0 <= s.ball_diameter_px <= 14.0
        assert s.ball_clipped_pct == 0.0
        assert s.exposure_us == 87 and s.gain == 6.0
        assert s.delivered_fps == 450.0
        assert s.resolved_mode["raw"]["size"] == [320, 200]
        assert s.accepted is True

    def test_a_saturated_ball_is_measured_not_hidden(self, tmp_path):
        export = _export(tmp_path, "arm1", 320, 200, 450.0, 12.0, ["ok"], clip_ball=True)
        _, shots = scorer.score_export(export)
        # the zone is 1.5x the radius, so a fully clipped disc is ~44 % of it
        assert shots[0].ball_clipped_pct > 40.0
        assert shots[0].ball_peak_dn == 255.0

    def test_pre_impact_head_frames_counts_the_approach(self, tmp_path):
        export = _export(tmp_path, "arm1", 320, 200, 450.0, 12.0, ["ok"])
        _, shots = scorer.score_export(export)
        assert shots[0].pre_impact_head_frames is not None
        assert shots[0].pre_impact_head_frames >= 4


class TestArmAggregation:
    def test_availability_and_histogram_include_excluded_shots(self, tmp_path):
        export = _export(tmp_path, "arm1", 320, 200, 450.0, 12.0, ["ok", "ok", "low_light", "ok"])
        m = json.loads((export / "manifest.json").read_text())
        m["excluded_shots"] = [{"shot_number": 9, "reasons": ["no_radar_capture_event"]}]
        (export / "manifest.json").write_text(json.dumps(m))
        score, _ = scorer.score_export(export)
        assert score.attempted == 5 and score.accepted == 3
        assert score.availability == 0.6
        assert score.status_histogram["excluded:no_radar_capture_event"] == 1
        assert score.insufficient is False

    def test_fewer_than_three_accepted_is_insufficient(self, tmp_path):
        export = _export(tmp_path, "arm4", 1280, 800, 120.0, 24.0, ["ok", "low_light", "low_light"])
        score, _ = scorer.score_export(export)
        assert score.insufficient is True

    def test_light_bins_are_octaves(self):
        assert scorer.light_bin(0.25) == "2^-2"
        assert scorer.light_bin(1.5) == "2^0"
        assert scorer.light_bin(None) == "unknown"


class TestHypotheses:
    def test_h1_passes_when_the_1to1_ball_is_twice_the_size(self, tmp_path):
        arms = {}
        for arm_id, w, h, fps, px in (
            ("arm1", 320, 200, 450.0, 12.0),
            ("arm2", 640, 400, 120.0, 12.0),
            ("arm4", 1280, 800, 120.0, 24.0),
        ):
            arms[arm_id], _ = scorer.score_export(
                _export(tmp_path, arm_id, w, h, fps, px, ["ok"] * 3)
            )
        h = scorer.test_hypotheses(arms)
        assert h["H1"]["verdict"] == "pass", h["H1"]

    def test_h1_fails_on_mode_substitution(self, tmp_path):
        arms = {}
        for arm_id, w, h, fps, px in (
            ("arm1", 320, 200, 450.0, 12.0),
            ("arm4", 1280, 800, 120.0, 12.0),
        ):
            arms[arm_id], _ = scorer.score_export(
                _export(tmp_path, arm_id, w, h, fps, px, ["ok"] * 3)
            )
        assert scorer.test_hypotheses(arms)["H1"]["verdict"] == "FAIL"

    def test_h6_names_arms_below_the_reference_at_the_same_light(self, tmp_path):
        arms = {}
        arms["arm1"], _ = scorer.score_export(
            _export(tmp_path, "arm1", 320, 200, 450.0, 12.0, ["ok"] * 5)
        )
        arms["arm4"], _ = scorer.score_export(
            _export(
                tmp_path,
                "arm4",
                1280,
                800,
                120.0,
                24.0,
                ["ok", "ok", "ok", "low_light", "low_light"],
            )
        )
        assert "arm4" in scorer.test_hypotheses(arms)["H6"]["evidence"]


class TestDecision:
    def test_the_reference_is_never_judged(self, tmp_path):
        arms = {
            "arm1": scorer.score_export(
                _export(tmp_path, "arm1", 320, 200, 450.0, 12.0, ["ok"] * 5)
            )[0]
        }
        assert scorer.decide(arms)["arm1"]["preferred"] is None

    def test_lower_availability_is_never_preferred(self, tmp_path):
        arms = {}
        arms["arm1"], _ = scorer.score_export(
            _export(tmp_path, "arm1", 320, 200, 450.0, 12.0, ["ok"] * 5)
        )
        arms["arm4"], _ = scorer.score_export(
            _export(tmp_path, "arm4", 1280, 800, 120.0, 24.0, ["ok"] * 3 + ["low_light"] * 2)
        )
        v = scorer.decide(arms)["arm4"]
        assert v["preferred"] is False and "availability" in v["reason"]

    def test_runs_of_the_same_arm_merge(self, tmp_path):
        a = _export(tmp_path, "arm1", 320, 200, 450.0, 12.0, ["ok", "ok"], run="run-01")
        b = _export(tmp_path, "arm1", 320, 200, 450.0, 12.0, ["ok", "low_light"], run="run-02")
        testers = scorer.score_exports([a, b])
        score, shots = testers["t1"]["arm1"]
        assert score.attempted == 4 and score.accepted == 3 and len(shots) == 4

    def test_two_testers_never_overwrite_each_other(self, tmp_path):
        a = _export(tmp_path, "arm1", 320, 200, 450.0, 12.0, ["ok"] * 5, tester_id="t1")
        b = _export(tmp_path, "arm1", 320, 200, 450.0, 12.0, ["low_light"] * 5, tester_id="t2")
        testers = scorer.score_exports([a, b])
        assert testers["t1"]["arm1"][0].accepted == 5
        assert testers["t2"]["arm1"][0].accepted == 0

    def test_insufficient_cells_are_not_promoted(self, tmp_path):
        arms = {}
        arms["arm1"], _ = scorer.score_export(
            _export(tmp_path, "arm1", 320, 200, 450.0, 12.0, ["ok"] * 5)
        )
        arms["arm4"], _ = scorer.score_export(
            _export(tmp_path, "arm4", 1280, 800, 120.0, 24.0, ["ok", "low_light", "low_light"])
        )
        assert "insufficient" in scorer.decide(arms)["arm4"]["reason"]


class TestOutputs:
    def test_cli_writes_json_and_markdown(self, tmp_path, capsys):
        a = _export(tmp_path, "arm1", 320, 200, 450.0, 12.0, ["ok"] * 5)
        b = _export(tmp_path, "arm2", 640, 400, 120.0, 12.0, ["ok"] * 5)
        assert scorer.main(["--export", str(a), str(b), "--out", str(tmp_path / "study")]) == 0
        data = json.loads((tmp_path / "study" / "mode_study.json").read_text())
        assert set(data) == {"testers"}
        assert set(data["testers"]["t1"]) == {
            "light_bin",
            "arms",
            "shots",
            "hypotheses",
            "decision",
        }
        md = (tmp_path / "study" / "mode_study.md").read_text()
        assert "Consistency only" in md and "## Tester t1" in md and "| arm1 |" in md
        assert "approximation" in md
