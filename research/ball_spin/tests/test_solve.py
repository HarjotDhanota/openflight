import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig


def _detected_frames(
    rate_rpm=3000.0,
    tilt_deg=8.0,
    dt_s=0.0042,
    n_frames=4,
    n_dots=40,
    seed_base=100,
    **detect_kwargs,
):
    from ball_spin.detect import detect_frame
    from ball_spin.dotball import dot_pattern
    from ball_spin.flight import Launch, Spin, ball_states

    dots = dot_pattern(n_dots, seed=20)
    launch = Launch(speed_mps=70.0, vla_deg=12.0, hla_deg=0.0)
    spin = Spin(rate_rpm=rate_rpm, axis_tilt_deg=tilt_deg)
    times = 0.002 + np.arange(n_frames) * dt_s
    states = ball_states(launch, spin, times, r0=Rotation.identity())
    frames = [
        detect_frame(mono_rig(), dots, state, rng=np.random.default_rng(seed_base + i), **detect_kwargs)
        for i, state in enumerate(states)
    ]
    return dots, launch, spin, frames


def test_solve_spin_zero_noise_round_trips_degrees_and_units():
    from ball_spin.solve import solve_spin

    for regime, rate, dt in [("driver", 3000.0, 0.0042), ("iron", 6500.0, 0.002), ("wedge", 10000.0, 0.0042)]:
        dots, launch, spin, frames = _detected_frames(rate_rpm=rate, tilt_deg=7.0, dt_s=dt)
        result = solve_spin(frames, dots, regime=regime, launch_vector=launch)

        assert result.ok
        assert abs(result.rate_rpm - spin.rate_rpm) < 1e-6
        assert abs(result.signed_tilt_deg - spin.axis_tilt_deg) < 1e-6


def test_center_honesty_error_grows_when_only_limb_center_is_noisy():
    from ball_spin.solve import axis_angle_error_deg, solve_spin

    errors = []
    for sigma in [0.0, 1.0, 2.0]:
        trial_errors = []
        for seed in range(20):
            dots, launch, _, frames = _detected_frames(
                rate_rpm=3000.0,
                tilt_deg=5.0,
                sigma_center_px=sigma,
                seed_base=1000 + seed * 10,
            )
            result = solve_spin(frames, dots, regime="driver", launch_vector=launch)
            assert result.ok
            trial_errors.append(axis_angle_error_deg(result.axis_world, frames[0].spin_axis_world))
        errors.append(float(np.median(trial_errors)))

    assert errors[0] < 1e-6
    assert errors[1] > 0.001
    assert errors[2] > errors[1]


def test_principal_angle_wrap_rule_uses_regime_prior_and_flags_ambiguity():
    from ball_spin.solve import solve_spin

    dots, launch, _, wedge_frames = _detected_frames(rate_rpm=10000.0, tilt_deg=4.0, dt_s=0.0042)
    wedge = solve_spin(wedge_frames, dots, regime="wedge", launch_vector=launch)
    assert wedge.ok
    assert abs(wedge.rate_rpm - 10000.0) < 1e-6

    dots, launch, _, iron_frames = _detected_frames(rate_rpm=7500.0, tilt_deg=4.0, dt_s=0.0042)
    iron = solve_spin(iron_frames, dots, regime="iron", launch_vector=launch)
    assert not iron.ok
    assert iron.ambiguous

    dots, launch, _, safe_iron_frames = _detected_frames(rate_rpm=7500.0, tilt_deg=4.0, dt_s=0.002)
    safe_iron = solve_spin(safe_iron_frames, dots, regime="iron", launch_vector=launch)
    assert safe_iron.ok
    assert abs(safe_iron.rate_rpm - 7500.0) < 1e-6


def test_signed_fade_tilt_survives_end_to_end():
    from ball_spin.solve import solve_spin

    dots, launch, _, frames = _detected_frames(rate_rpm=3000.0, tilt_deg=12.0, dt_s=0.0042)
    result = solve_spin(frames, dots, regime="driver", launch_vector=launch)

    assert result.ok
    assert result.signed_tilt_deg > 0.0


def test_misid_outlier_loop_bounds_degradation_and_disabled_loop_goes_bad():
    from ball_spin.solve import axis_angle_error_deg, solve_spin

    dots, launch, _, frames = _detected_frames(
        rate_rpm=3000.0,
        tilt_deg=7.0,
        p_misid=0.05,
        sigma_dot_px=0.5,
        dt_s=0.0042,
        n_frames=6,
        n_dots=80,
        seed_base=1083,
    )

    robust = solve_spin(frames, dots, regime="driver", launch_vector=launch, sigma_dot_px=0.5)
    brittle = solve_spin(
        frames,
        dots,
        regime="driver",
        launch_vector=launch,
        sigma_dot_px=0.5,
        outlier_loop=False,
    )

    assert robust.ok
    assert axis_angle_error_deg(robust.axis_world, frames[0].spin_axis_world) < 5.0
    assert (
        not brittle.ok
        or axis_angle_error_deg(brittle.axis_world, frames[0].spin_axis_world) > 2.0
        or abs(brittle.rate_error_pct(frames[0].rate_rpm)) > 3.0
    )
