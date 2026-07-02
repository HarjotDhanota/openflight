import numpy as np

from club_pose.dplane import (
    DPlaneMeasurement,
    club_params,
    forward_model,
    invert_axis_route,
    invert_launch_route,
    measure_shot,
)


def test_degree_units_exact_launch_case_round_trips():
    truth = forward_model(
        "driver",
        face_deg=2.0,
        path_deg=-1.0,
        attack_deg=0.0,
        dynamic_loft_deg=10.0,
        speed_mps=45.0,
        impact_u_mm=0.0,
        impact_w_mm=0.0,
        c_h=0.8,
        c_v=0.8,
    )
    assert abs(truth.launch_h_deg - 1.4) < 1e-12

    meas = DPlaneMeasurement(
        launch_h_deg=truth.launch_h_deg,
        launch_v_deg=truth.launch_v_deg,
        path_deg=-1.0,
        attack_deg=0.0,
        spin_axis_deg=truth.spin_axis_deg,
        impact_u_mm=0.0,
        impact_w_mm=0.0,
    )
    launch = invert_launch_route("driver", meas, c_h_hat=0.8, c_v_hat=0.8, gear_mode="none")
    axis = invert_axis_route("driver", meas, launch.dynamic_loft_deg, gear_mode="none")

    assert abs(launch.face_deg - 2.0) < 1e-12
    assert abs(axis.face_deg - 2.0) < 1e-12
    assert abs(launch.dynamic_loft_deg - 10.0) < 1e-12


def test_zero_noise_known_coefficient_no_gear_exact_recovery():
    params = club_params("iron")
    c = params.c_mid
    truth = forward_model(
        "iron",
        face_deg=-3.0,
        path_deg=1.5,
        attack_deg=-4.0,
        dynamic_loft_deg=36.0,
        speed_mps=35.0,
        impact_u_mm=0.0,
        impact_w_mm=0.0,
        c_h=c,
        c_v=c,
    )
    meas = measure_shot(
        truth,
        sigma_launch=0.0,
        sigma_path=0.0,
        sigma_axis=0.0,
        sigma_impact=0.0,
        b_frame=0.0,
        gear_mode="none",
        rng=np.random.default_rng(0),
    )
    launch = invert_launch_route("iron", meas, c_h_hat=c, c_v_hat=c, gear_mode="none")
    axis = invert_axis_route("iron", meas, launch.dynamic_loft_deg, gear_mode="none")

    assert abs(launch.face_deg - truth.face_deg) < 1e-10
    assert abs(launch.dynamic_loft_deg - truth.dynamic_loft_deg) < 1e-10
    assert abs(axis.face_deg - truth.face_deg) < 1e-10


def test_frame_bias_moves_absolute_face_but_cancels_face_to_path():
    params = club_params("driver")
    c = params.c_mid
    truth = forward_model(
        "driver",
        face_deg=2.5,
        path_deg=-1.0,
        attack_deg=1.0,
        dynamic_loft_deg=12.0,
        speed_mps=45.0,
        impact_u_mm=0.0,
        impact_w_mm=0.0,
        c_h=c,
        c_v=c,
    )
    meas = measure_shot(
        truth,
        sigma_launch=0.0,
        sigma_path=0.0,
        sigma_axis=0.0,
        sigma_impact=0.0,
        b_frame=0.75,
        gear_mode="none",
        rng=np.random.default_rng(1),
    )
    launch = invert_launch_route("driver", meas, c_h_hat=c, c_v_hat=c, gear_mode="none")
    axis = invert_axis_route("driver", meas, launch.dynamic_loft_deg, gear_mode="none")

    assert abs((launch.face_deg - truth.face_deg) - 0.75) < 1e-10
    assert abs((axis.face_deg - truth.face_deg) - 0.75) < 1e-10
    assert abs((launch.face_deg - meas.path_deg) - (truth.face_deg - truth.path_deg)) < 1e-10
    assert abs((axis.face_deg - meas.path_deg) - (truth.face_deg - truth.path_deg)) < 1e-10


def test_coefficient_mismatch_is_visible_and_scales_with_face_to_path():
    params = club_params("driver")
    c_hat = params.c_mid
    low = forward_model(
        "driver", 1.0, 0.0, 0.0, 11.0, 45.0, 0.0, 0.0, params.c_min, params.c_mid
    )
    high = forward_model(
        "driver", 4.0, -2.0, 0.0, 11.0, 45.0, 0.0, 0.0, params.c_min, params.c_mid
    )

    def launch_face_error(truth):
        meas = measure_shot(truth, 0.0, 0.0, 0.0, 0.0, 0.0, "none", np.random.default_rng(2))
        est = invert_launch_route("driver", meas, c_hat, c_hat, "none")
        return abs(est.face_deg - truth.face_deg)

    assert launch_face_error(low) > 0.0
    assert launch_face_error(high) > launch_face_error(low) * 4.0


def test_gear_correction_reduces_axis_route_face_error_monotonically():
    params = club_params("driver")
    c = params.c_mid
    truth = forward_model(
        "driver",
        face_deg=1.0,
        path_deg=-1.0,
        attack_deg=1.0,
        dynamic_loft_deg=12.0,
        speed_mps=45.0,
        impact_u_mm=8.0,
        impact_w_mm=2.0,
        c_h=c,
        c_v=c,
    )
    errors = []
    for mode in ("none", "camera", "perfect"):
        meas = measure_shot(
            truth,
            sigma_launch=0.0,
            sigma_path=0.0,
            sigma_axis=0.0,
            sigma_impact=0.0,
            b_frame=0.0,
            gear_mode=mode,
            rng=np.random.default_rng(3),
        )
        launch = invert_launch_route("driver", meas, c, c, mode)
        axis = invert_axis_route("driver", meas, launch.dynamic_loft_deg, mode)
        errors.append(abs(axis.face_deg - truth.face_deg))

    assert errors[0] > errors[1]
    assert errors[1] <= errors[2] + 1e-10
    assert errors[2] < 1e-10
