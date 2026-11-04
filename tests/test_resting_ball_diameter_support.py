"""The resting ball's diameter carries the uncertainty of the rim it rests on (C26b).

The existing finder fits a circle to the lit side of the ball's rim. On the Oct 24 clips that rim
is often one-sided (the sunlit band beside the ball), only the lit cap (dusk: no side support), or
not fitted at all (the lit blob's own size), and the diameter is then 2-17 px wrong against two
labellers' truth (tests/fixtures/resting_ball_truth). The value is unchanged; its sigma now
follows the rim's support class, each class's sigma the RMS of the held-out truth errors of that
class, so the located tee, the ball finder's expected size and the surface solve inherit it.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from openflight.camera import resting_ball_range as rbr

FIXTURE = Path(__file__).parent / "fixtures" / "resting_ball_truth"


def _support(mode="all_round", quadrants=(30, 12, 0, 12), rms=0.3):
    return {
        "mode": mode,
        "rays": 91,
        "kept": sum(quadrants),
        "rms_px": rms,
        "quadrants": list(quadrants),
    }


@pytest.mark.parametrize(
    ("support", "label", "sigma"),
    [
        (_support(), "supported", rbr.DIAMETER_FIT_SIGMA_PX),  # Oct 27 shot 1: 13 | 13, 0.27 px
        (_support(quadrants=(28, 9, 0, 9), rms=0.9), "disagree", 2.35),
        (_support(mode="toward_light", quadrants=(24, 0, 0, 6)), "one_sided", 2.62),
        (_support(mode="toward_light", quadrants=(22, 3, 0, 0)), "cap_only", 4.13),
        (
            {"mode": "lit_blob", "rays": 0, "kept": 0, "rms_px": None, "quadrants": None},
            "no_rim",
            14.42,
        ),
        (None, "no_rim", 14.42),
    ],
    ids=["supported", "disagree", "one-sided", "cap-only", "lit-blob", "none"],
)
def test_each_support_class_carries_its_own_sigma(support, label, sigma):
    assert rbr.diameter_support(support) == (label, pytest.approx(sigma))


def test_a_wide_diameter_sigma_widens_the_located_tee():
    from openflight.camera.reference_ball_range import BallPlaneCamera  # noqa: PLC0415

    camera = BallPlaneCamera.nominal(
        focal_px=1005.7,
        image_width_px=640,
        image_height_px=400,
        pitch_deg=1.0,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.093),
        radar_origin_lfu=(0.0, -0.02, 0.05),
        angular_uncertainty_deg=1.0,
        focal_relative_uncertainty=0.01,
    )
    tight = rbr.located_tee_range(camera, (320.0, 205.0), 24.0, focal_rel_sigma=0.01)
    wide = rbr.located_tee_range(
        camera, (320.0, 205.0), 24.0, focal_rel_sigma=0.01, diameter_sigma_px=2.62
    )
    assert wide["range_m"] == pytest.approx(tight["range_m"])
    assert wide["uncertainty_m"] > 5 * tight["uncertainty_m"]


def test_the_resting_ball_reports_its_support_and_sigma():
    from openflight.camera import swing_tilt  # noqa: PLC0415

    crop = np.load(FIXTURE / "crops.npz")["crop_06"]  # Oct 27 shot 1, 640x400
    x0, y0 = json.loads((FIXTURE / "crops.json").read_text(encoding="utf-8"))["crop_06"][
        "origin_in_frame"
    ]
    frame = np.full((400, 640), int(np.median(np.r_[crop[0], crop[-1]])), np.uint8)
    frame[y0 : y0 + crop.shape[0], x0 : x0 + crop.shape[1]] = crop
    frames = np.repeat(frame[None], 6, axis=0)
    box = (x0, y0, x0 + crop.shape[1], y0 + crop.shape[0])
    ball = swing_tilt.resting_ball_px(frames, trigger_index=6, box_px=box)
    assert ball is not None
    assert ball["diameter_px"] == pytest.approx(24.0, abs=0.5)  # the shot-1 pin
    assert ball["diameter_support"] in ("supported", "disagree", "one_sided", "cap_only", "no_rim")
    assert ball["diameter_sigma_px"] == pytest.approx(rbr.diameter_support(ball["rim_support"])[1])


def test_the_truth_set_records_the_current_finder_on_its_held_out_half():
    """A characterisation, not a target: the numbers a future diameter method must beat."""
    truth = json.loads((FIXTURE / "truth.json").read_text(encoding="utf-8"))
    split = json.loads((FIXTURE / "split.json").read_text(encoding="utf-8"))
    finder = json.loads((FIXTURE / "finder.json").read_text(encoding="utf-8"))
    errors = np.array(
        [finder[c]["d"] - truth[c]["truth_d"] for c in truth if split[c]["half"] == "heldout"]
    )
    assert len(errors) == 14
    assert np.mean(np.abs(errors) <= 1.5) == pytest.approx(5 / 14)  # 36 %
    assert errors.mean() == pytest.approx(3.87, abs=0.01)
    assert np.abs(errors).max() == pytest.approx(16.8, abs=0.1)
    # every class but the supported one says it may be this wrong: the held-out errors sit
    # within 3 of their class's sigma
    for c in truth:
        if split[c]["half"] != "heldout":
            continue
        label, sigma = rbr.diameter_support(finder[c]["rim_support"])
        assert abs(finder[c]["d"] - truth[c]["truth_d"]) <= 3.0 * sigma, (c, label)


def test_the_located_tee_carries_the_ball_s_diameter_sigma_and_support(monkeypatch):
    from types import SimpleNamespace  # noqa: PLC0415

    from openflight import server  # noqa: PLC0415

    ball = {
        "x_px": 320.0,
        "y_px": 205.0,
        "diameter_px": 24.0,
        "diameter_support": "one_sided",
        "diameter_sigma_px": 2.62,
    }
    monkeypatch.setattr(server, "rig_geometry", SimpleNamespace(lens_height_above_floor_mm=93.0))
    monkeypatch.setattr(server, "_shot_resting_ball", lambda archive: ball)
    monkeypatch.setattr(
        server, "_rig_sensor_origins_lfu", lambda: ([0.0, 0.0, 0.093], [0.0, -0.02, 0.05])
    )
    monkeypatch.setattr(server, "_shot_calibrated_camera", lambda shot, w, h: (None, None, "test"))
    monkeypatch.setattr(server, "_lis3dh_camera_pitch", lambda inclinometer: 1.0)
    monkeypatch.setattr(server, "_mode_focal", lambda width: (1005.7, "test", 0.01))
    monkeypatch.setattr(server, "_swing_roll_correction_deg", lambda shot: 0.0)
    monkeypatch.setattr(server, "_surface_in_force_m", lambda: None)
    shot = SimpleNamespace(inclinometer=None)

    record = server._camera_launch_range(  # pylint: disable=protected-access
        shot, {"frames": np.zeros((5, 400, 640), np.uint8)}
    )

    assert record["diameter_sigma_px"] == pytest.approx(2.62)
    assert record["diameter_support"] == "one_sided"
    assert "one_sided" in record["diameter_note"]
