"""A smoothness penalty that costs more than the evidence is worth freezes.

`fit_sequence` defaulted to `smooth_deg=70.0`, which charges

    |dyaw| + |dpitch| + |droll|
    ---------------------------  =  1 / 210 of an IoU point per degree
            3 * 70

against a 20-40 px silhouette whose IoU changes by roughly 0.002 per degree of
yaw (section 11f's landscape sweep). The penalty is more than twice the
evidence, so the second frame cannot afford to move and every frame after it
inherits frame one's pose. On the seven-frame runs this pipeline actually fits,
that is the whole sequence.

The default is now None -- no angular penalty. The parameter stays, because a
long sequence with a genuinely noisy per-frame fit is what it was written for;
it is just not a default a seven-frame run can carry.

The stubs below make the relationship explicit: the "rendered mask" carries the
yaw, and the "IoU" is a triangle around each frame's true yaw with a stated
sensitivity in IoU per degree. Nothing here renders a mesh.
"""

from __future__ import annotations

import types

import numpy as np
import pytest

from openflight.camera.clubpose import fit

# What a 20-40 px silhouette buys per degree of yaw.
IOU_PER_DEGREE = 0.002
RAMP_DEG = 5.0
N_FRAMES = 5
RANGE_MM = 1581.0
YAW, PITCH, ROLL = fit.GROUNDED_POSE_DEG


def _truth_yaw(frame: int) -> float:
    return YAW + RAMP_DEG * frame / (N_FRAMES - 1)


def _masks() -> dict[int, np.ndarray]:
    """One mask per frame, its WIDTH encoding the frame so the stub IoU can see it."""
    return {frame: np.ones((10, 10 + frame), dtype=np.uint8) for frame in range(N_FRAMES)}


@pytest.fixture(name="stubs")
def _stubs(monkeypatch):
    monkeypatch.setattr(fit, "_ray_world", lambda _uv, _camera: np.asarray([1.0, 0.0, 0.0]))
    monkeypatch.setattr(
        fit,
        "render_mask_6dof",
        lambda _mesh, _centre, yaw, _pitch, _roll, _camera: np.asarray([[yaw]]),
    )

    def stub_iou(rendered, observed):
        frame = int(observed.shape[1]) - 10
        return 1.0 - abs(float(rendered[0, 0]) - _truth_yaw(frame)) * IOU_PER_DEGREE

    monkeypatch.setattr(fit, "iou", stub_iou)


def _fit(**kwargs) -> dict[int, dict]:
    return fit.fit_sequence(
        object(),
        _masks(),
        types.SimpleNamespace(center_world=np.zeros(3)),
        range_grid_mm=(RANGE_MM,),
        pitch_grid=(PITCH,),
        roll_grid=(ROLL,),
        refine_range=False,
        **kwargs,
    )


def test_the_default_carries_no_angular_penalty():
    import inspect

    assert inspect.signature(fit.fit_sequence).parameters["smooth_deg"].default is None


def test_a_five_frame_ramp_is_tracked_by_default(stubs):
    result = _fit()

    assert set(result) == set(range(N_FRAMES))
    tracked = result[N_FRAMES - 1]["yaw_deg"] - result[0]["yaw_deg"]
    assert tracked == pytest.approx(RAMP_DEG, abs=1.5), (
        f"the fit must follow a real {RAMP_DEG} deg ramp, got {tracked:.2f} deg"
    )
    for frame in range(N_FRAMES):
        assert result[frame]["yaw_deg"] == pytest.approx(_truth_yaw(frame), abs=1.0)


def test_the_old_default_freezes_the_sequence_at_frame_ones_pose(stubs):
    """The defect, measured. 70 deg costs 2.4x what a degree of yaw is worth."""
    result = _fit(smooth_deg=70.0)

    frozen = result[N_FRAMES - 1]["yaw_deg"] - result[0]["yaw_deg"]
    assert abs(frozen) < 1.0, f"expected a frozen sequence, it moved {frozen:.2f} deg"


def test_an_explicit_penalty_still_restrains_a_noisy_fit(stubs):
    """The parameter is kept, not deleted: it works when it is affordable."""
    loose = _fit(smooth_deg=1.0e6)
    tight = _fit(smooth_deg=70.0)

    assert abs(loose[N_FRAMES - 1]["yaw_deg"] - loose[0]["yaw_deg"]) > abs(
        tight[N_FRAMES - 1]["yaw_deg"] - tight[0]["yaw_deg"]
    )


def test_the_range_penalty_is_unaffected_by_the_angular_default():
    """`smooth_mm` is a separate term and keeps its own default."""
    import inspect

    assert inspect.signature(fit.fit_sequence).parameters["smooth_mm"].default == 300.0
    assert fit._smoothness_penalty(
        {"range_mm": 1500.0, "yaw_deg": YAW, "pitch_deg": PITCH, "roll_deg": ROLL},
        1800.0,
        YAW,
        PITCH,
        ROLL,
        None,
        300.0,
        penalise_range=True,
    ) == pytest.approx(1.0)
