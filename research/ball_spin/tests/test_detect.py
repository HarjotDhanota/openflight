import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig


def _state():
    from ball_spin.flight import BallState

    return BallState(
        t_s=0.0,
        center_world=np.array([0.0, 0.0, 0.0]),
        orientation=Rotation.identity(),
        spin_axis_world=np.array([0.0, 0.0, 1.0]),
        rate_rpm=3_000.0,
        axis_tilt_deg=0.0,
        launch_vector_mm_s=np.array([70_000.0, 0.0, 0.0]),
    )


def test_detect_frame_uses_physical_visibility_and_limb_cutoff():
    from ball_spin.detect import detect_frame

    dots = np.array(
        [
            [-1.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [-0.30, 0.95, 0.0],
            [-0.10, 0.99, 0.0],
            [0.0, 0.0, 1.0],
        ]
    )

    frame = detect_frame(mono_rig(), dots, _state(), beta=0.25, rng=np.random.default_rng(1))

    assert 0 in frame.visible_true_ids
    assert 1 not in frame.visible_true_ids
    assert 2 in frame.visible_true_ids
    assert 3 not in frame.visible_true_ids


def test_detect_frame_limb_fit_estimates_are_noisy_not_truth():
    from ball_spin.detect import detect_frame

    dots = np.array([[-1.0, 0.0, 0.0], [-0.6, 0.8, 0.0], [-0.6, -0.8, 0.0], [-0.7, 0.0, 0.7], [-0.7, 0.0, -0.7]])
    perfect = detect_frame(mono_rig(), dots, _state(), beta=0.0, rng=np.random.default_rng(2))
    noisy = detect_frame(
        mono_rig(),
        dots,
        _state(),
        beta=0.0,
        sigma_center_px=2.0,
        sigma_radius_px=2.0,
        rng=np.random.default_rng(2),
    )

    assert np.allclose(perfect.estimated_center_px, perfect.true_center_px)
    assert perfect.estimated_radius_px == perfect.true_radius_px
    assert not np.allclose(noisy.estimated_center_px, noisy.true_center_px)
    assert noisy.estimated_radius_px != noisy.true_radius_px


def test_detect_frame_misid_swaps_visible_labels():
    from ball_spin.detect import detect_frame

    dots = np.array([[-1.0, 0.0, 0.0], [-0.8, 0.6, 0.0], [-0.8, -0.6, 0.0], [-0.8, 0.0, 0.6], [-0.8, 0.0, -0.6]])

    clean = detect_frame(mono_rig(), dots, _state(), beta=0.0, rng=np.random.default_rng(3))
    swapped = detect_frame(
        mono_rig(),
        dots,
        _state(),
        beta=0.0,
        p_misid=1.0,
        rng=np.random.default_rng(3),
    )

    assert [det.dot_id for det in clean.detections] == [det.true_id for det in clean.detections]
    assert any(det.dot_id != det.true_id for det in swapped.detections)
    assert sorted(det.dot_id for det in swapped.detections) == sorted(
        det.true_id for det in swapped.detections
    )
