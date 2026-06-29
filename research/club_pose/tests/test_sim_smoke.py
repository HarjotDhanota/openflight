def test_sim_imports_and_cv2_available():
    import club_pose.sim  # noqa: F401
    import cv2

    assert cv2.__version__
