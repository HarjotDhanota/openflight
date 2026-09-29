"""Static reference-ball exposure: lowest applied controls that pass ball-pixel gates."""

import json

import numpy as np
import pytest

from openflight.camera import static_exposure as se


def _frames(ball_dn: float, background_dn: float = 40.0, *, count=5, shape=(80, 120)):
    height, width = shape
    yy, xx = np.ogrid[:height, :width]
    disc = np.hypot(xx - 60, yy - 40) <= 8
    image = np.full(shape, background_dn, dtype=np.float32)
    image[disc] = ball_dn
    return np.repeat(np.clip(image, 0, 255).astype(np.uint8)[None], count, axis=0)


def _association(stable_count=3, *, found=True):
    if not found:
        return {"status": "not_found", "selected": None, "stable_count": 0}
    return {
        "status": "selected",
        "selected": {"x_px": 60.0, "y_px": 40.0, "diameter_px": 16.0},
        "stable_count": stable_count,
    }


def _assess(step, frames, association, *, applied=None, floor=15.0):
    exposure, gain = applied if applied is not None else (step.exposure_us, step.gain)
    return se.assess_static_exposure(
        frames,
        association,
        requested=step,
        applied_exposure_us=exposure,
        applied_gain=gain,
        black_floor_dn=floor,
    )


STEP = se.StaticExposureStep(1250, 8.0)


def test_a_well_lit_stable_ball_passes_every_gate():
    observation = _assess(STEP, _frames(160.0), _association())

    assert observation.status == "accepted"
    assert observation.signal_above_floor_dn == pytest.approx(145.0)
    assert observation.local_contrast_dn == pytest.approx(120.0)


def test_controls_that_were_not_applied_never_qualify():
    observation = _assess(STEP, _frames(160.0), _association(), applied=(298, 12.0))

    assert observation.status == "settling"
    assert observation.applied_controls_match is False


def test_a_missing_ball_is_rejected_even_when_the_frame_is_bright():
    observation = _assess(STEP, _frames(200.0, 200.0), _association(found=False))

    assert observation.status == "rejected"
    assert observation.ball_found is False


@pytest.mark.parametrize(
    ("ball_dn", "background_dn", "reason"),
    [
        (30.0, 20.0, "signal"),
        (255.0, 40.0, "clipped"),
    ],
)
def test_ball_pixel_gates_reject_dark_and_clipped_balls(ball_dn, background_dn, reason):
    observation = _assess(STEP, _frames(ball_dn, background_dn), _association())

    assert observation.status == "rejected"
    assert reason in observation.reason


def test_a_low_contrast_ball_the_detector_holds_is_accepted_and_its_contrast_recorded():
    # Pi, 29 Sept: a white ball against a white door, selected and fitted, but only
    # 3 DN brighter than its surroundings. Contrast in DN scales with exposure like
    # the background does, so it is recorded, not gated.
    observation = _assess(STEP, _frames(58.0, 55.0), _association())

    assert observation.status == "accepted"
    assert observation.local_contrast_dn == pytest.approx(3.0)
    assert not observation.failed_gates


def test_a_passing_ball_waits_for_temporal_stability():
    observation = _assess(STEP, _frames(160.0), _association(stable_count=1))

    assert observation.status == "stabilizing"


def _driver(
    steps, brightness, *, applied_offset=None, clip_above=None, always_found=False, warm_start=None
):
    """Run a search against a scene whose ball level scales with exposure x gain."""
    search = se.StaticExposureSearch(steps, warm_start=warm_start)
    seen = []
    for _ in range(500):
        step = search.current_step
        if step is None:
            break
        seen.append(step)
        level = brightness * step.exposure_us * step.gain
        if clip_above is not None and level > clip_above:
            level = 255.0
        found = always_found or level >= 25.0
        applied = (
            (step.exposure_us + applied_offset, step.gain)
            if applied_offset is not None
            else (step.exposure_us, step.gain)
        )
        association = _association(stable_count=3, found=found)
        search.record(
            _assess(step, _frames(min(level + 15.0, 255.0)), association, applied=applied)
        )
    return search, seen


STEPS = se.exposure_steps_for_fps(120.0)


def test_search_selects_the_lowest_exposure_then_gain_that_passes():
    search, seen = _driver(STEPS, brightness=0.02)
    exhaustive = [
        step
        for step in sorted(STEPS)
        if _assess(
            step,
            _frames(min(0.02 * step.signal + 15.0, 255.0)),
            _association(found=0.02 * step.signal >= 25.0),
        ).acceptable
    ]

    assert search.status == "locked"
    assert (search.lock.exposure_us, search.lock.gain) == (
        exhaustive[0].exposure_us,
        exhaustive[0].gain,
    )
    assert len(seen) < len(STEPS)


def test_search_bootstraps_an_initially_invisible_ball_without_qualifying_it():
    search, seen = _driver(STEPS, brightness=0.004)

    assert search.status == "locked"
    assert seen[0] == se.StaticExposureStep(STEPS[0].exposure_us, max(se.GAINS))
    assert all(item["stage"] == "bootstrap" for item in search.attempts[:2])
    assert search.lock.observation.status == "accepted"


def test_a_ball_detectable_early_but_dim_still_reaches_a_long_passing_exposure():
    """Night scene: detection works at the dimmest step, but only a long exposure
    gives the ball 20 DN of signal."""
    search, seen = _driver(STEPS, brightness=0.0012, always_found=True)

    assert search.status == "locked"
    assert search.lock.exposure_us >= 1250
    assert search.lock.exposure_us * search.lock.gain * 0.0012 >= 20.0
    assert len(seen) <= 30


def test_search_reports_lighting_required_when_the_brightest_setting_hides_the_ball():
    search, _seen = _driver(STEPS, brightness=0.000001)

    assert search.status == "lighting_required"
    assert search.lock is None
    assert "not visible" in search.reason


def test_a_ball_that_clips_early_locks_below_clipping():
    search, _seen = _driver(STEPS, brightness=0.004, clip_above=40.0)

    assert search.status == "locked"
    assert search.lock.exposure_us * search.lock.gain * 0.004 <= 40.0


def test_controls_the_camera_never_applies_cannot_lock():
    search, _seen = _driver(STEPS, brightness=0.02, applied_offset=500)

    assert search.status == "lighting_required"
    assert search.lock is None
    assert any(item["reason"] == "controls_not_applied" for item in search.attempts)


def test_lock_serializes_with_policy_identity_and_applied_controls(tmp_path):
    search, _seen = _driver(STEPS, brightness=0.02)
    path = tmp_path / "lock.json"

    se.write_static_exposure_lock(path, search.lock)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["purpose"] == se.STATIC_EXPOSURE_PURPOSE
    assert payload["policy_sha256"] == se.static_exposure_policy_sha256()
    assert payload["applied_exposure_us"] == payload["exposure_us"]


def test_static_exposure_policy_identity_is_pinned():
    """A lattice or gate change must be a deliberate, reviewed identity change."""
    assert se.static_exposure_policy_sha256() == (
        "29872dd294346caeb41ede33c3c5aa0b402ea9b0b63003cac4ff963774cab1ba"
    )


def _ambiguous(step):
    return se.assess_static_exposure(
        _frames(160.0),
        {"status": "ambiguous", "selected": None, "stable_count": 0},
        requested=step,
        applied_exposure_us=step.exposure_us,
        applied_gain=step.gain,
        black_floor_dn=15.0,
    )


def test_an_ambiguous_ball_is_reported_as_unidentified_not_as_too_dark():
    """Several ball-like objects are an identification failure; more light cannot fix it."""
    search = se.StaticExposureSearch(STEPS)
    seen = []
    for _ in range(200):
        step = search.current_step
        if step is None:
            break
        seen.append(step)
        search.record(_ambiguous(step))

    assert search.status == "ball_not_identified"
    assert "could not be picked out" in search.reason
    assert search.lock is None
    assert len(seen) <= 8
    assert all(item["reason"] != "ball_not_visible" for item in search.attempts)


def test_ambiguity_does_not_prune_dimmer_settings_as_too_dark():
    search = se.StaticExposureSearch(STEPS)
    search.record(_ambiguous(search.current_step))
    first_refine = search.current_step
    search.record(_ambiguous(first_refine))

    assert search.stage == "refine"
    assert search.current_step == sorted(STEPS)[sorted(STEPS).index(first_refine) + 1]


def _observation(step, association, frames=None, applied=None):
    exposure, gain = applied if applied is not None else (step.exposure_us, step.gain)
    return se.assess_static_exposure(
        frames if frames is not None else _frames(160.0),
        association,
        requested=step,
        applied_exposure_us=exposure,
        applied_gain=gain,
        black_floor_dn=15.0,
    )


def test_a_clipped_ball_never_walks_the_search_brighter():
    """A saturated ball on a bright mat fails contrast and clipping; only darker can help."""
    search = se.StaticExposureSearch(STEPS)
    search.record(_observation(search.current_step, _association()))
    step = search.current_step
    clipped = _observation(step, _association(), frames=_frames(255.0, 250.0))
    assert "clipped" in clipped.failed_gates

    search.record(clipped)

    assert search.current_step is None or search.current_step.signal < step.signal


def test_a_pose_blip_retries_the_same_step_instead_of_skipping_it():
    search = se.StaticExposureSearch(STEPS)
    search.record(_observation(search.current_step, _association()))
    step = search.current_step

    search.record(_observation(step, {"status": "pose_changed", "selected": None}))

    assert search.current_step == step


def test_a_bootstrap_settle_timeout_is_not_taken_as_darkness():
    search = se.StaticExposureSearch(STEPS)
    first = search.current_step
    for _ in range(4):
        search.record(
            _observation(first, _association(), applied=(first.exposure_us + 500, first.gain))
        )
    search.record(_observation(search.current_step, _association()))

    assert search.stage == "refine"
    # not taken as darkness: the refine still starts at the shortest exposure
    assert search.current_step.exposure_us == sorted(STEPS)[0].exposure_us


def test_noise_candidates_in_a_black_frame_are_darkness_not_ambiguity():
    """Pi 2026-09-28: at 145 us x 6 the frame was nearly black and noise gave
    geometry-inconsistent candidates; the search must keep brightening."""
    search = se.StaticExposureSearch(STEPS)
    for _ in range(8):
        step = search.current_step
        if step is None:
            break
        search.record(
            _observation(
                step,
                {"status": "no_consistent_candidate", "selected": None},
                frames=_frames(26.0, 24.0),
            )
        )

    # it keeps brightening (jumping ahead on the dark frame) and never calls the
    # noise an unidentifiable ball
    assert search.stage == "bootstrap"
    assert search.status in {"searching", "lighting_required"}
    assert all(item["reason"] == "ball_not_visible" for item in search.attempts)
    assert search.attempts[-1]["exposure_us"] > STEPS[0].exposure_us


def test_ambiguity_in_a_lit_frame_is_still_reported_as_unidentified():
    search = se.StaticExposureSearch(STEPS)
    for _ in range(8):
        step = search.current_step
        if step is None:
            break
        search.record(
            _observation(
                step, {"status": "ambiguous", "selected": None}, frames=_frames(160.0, 90.0)
            )
        )

    assert search.status == "ball_not_identified"


def _lowest_lock(brightness):
    search, _seen = _driver(STEPS, brightness=brightness)
    return se.StaticExposureStep(search.lock.exposure_us, search.lock.gain)


def test_a_remembered_lock_that_still_passes_locks_on_the_first_step():
    remembered = _lowest_lock(0.02)

    search, seen = _driver(STEPS, brightness=0.02, warm_start=remembered)

    assert search.status == "locked"
    assert seen == [remembered]
    assert search.attempts[0]["stage"] == "warm_start"
    assert search.to_dict()["warm_start"] == {
        "exposure_us": remembered.exposure_us,
        "gain": remembered.gain,
    }


def test_a_remembered_lock_that_fails_falls_back_to_the_full_search():
    # remembered from a dim scene; in this bright one it clips the ball
    remembered = _lowest_lock(0.004)
    cold, _seen = _driver(STEPS, brightness=0.06)

    search, seen = _driver(STEPS, brightness=0.06, warm_start=remembered)

    assert seen[0] == remembered
    assert search.attempts[0]["stage"] == "warm_start"
    # the failed remembered step still measured the ball, so the search jumps
    # from it, and still ends at the lowest passing setting
    assert len(seen) < len(_driver(STEPS, brightness=0.06)[1]) + 2
    assert (search.lock.exposure_us, search.lock.gain) == (cold.lock.exposure_us, cold.lock.gain)


def test_a_remembered_lock_outside_the_lattice_is_ignored():
    search = se.StaticExposureSearch(STEPS, warm_start=se.StaticExposureStep(1234, 3.3))

    assert search.stage == "bootstrap"
    assert search.to_dict()["warm_start"] is None


def test_a_remembered_lock_in_the_dark_still_ends_in_lighting_required():
    remembered = _lowest_lock(0.02)

    search, _seen = _driver(STEPS, brightness=0.000001, warm_start=remembered)

    assert search.status == "lighting_required"
    assert search.lock is None


def _scene_search(ball_per_signal, background_per_signal, *, found=True):
    """A scene where ball and background both scale with exposure x gain."""
    search = se.StaticExposureSearch(STEPS)
    for _ in range(500):
        step = search.current_step
        if step is None:
            break
        background = min(15.0 + background_per_signal * step.signal, 255.0)
        ball = min(15.0 + ball_per_signal * step.signal, 255.0)
        search.record(
            _assess(step, _frames(ball, background), _association(stable_count=3, found=found))
        )
    return search


def test_a_ball_on_a_white_door_locks_low_instead_of_climbing_into_clipping():
    """White door behind the ball: brighter settings scale ball and door alike, so
    more exposure cannot make it stand out; the detector already holds it."""
    search = _scene_search(0.0100, 0.0094)

    assert search.status == "locked"
    assert search.lock.observation.ball_clipped_pct == 0.0
    assert search.lock.exposure_us * search.lock.gain * 0.0100 < 2.0 * 20.0


def test_no_ball_in_a_well_lit_picture_is_not_reported_as_needing_light():
    search = _scene_search(0.02, 0.02, found=False)

    assert search.status == "ball_not_identified"
    assert "well lit" in search.reason


def _physical_frames(ball_per_signal, background_per_signal, step):
    return _frames(
        min(15.0 + ball_per_signal * step.signal, 255.0),
        min(15.0 + background_per_signal * step.signal, 255.0),
    )


def _physical_search(ball_per_signal, background_per_signal):
    """Ball and background both brighten with exposure x gain, as on a real sensor."""
    search = se.StaticExposureSearch(STEPS)
    seen = []
    for _ in range(200):
        step = search.current_step
        if step is None:
            break
        seen.append(step)
        found = ball_per_signal * step.signal >= 25.0
        search.record(
            _assess(
                step,
                _physical_frames(ball_per_signal, background_per_signal, step),
                _association(stable_count=3, found=found),
            )
        )
    return search, seen


@pytest.mark.parametrize(("ball", "background"), [(0.02, 0.006), (0.004, 0.0012), (0.0012, 0.0004)])
def test_one_measured_ball_predicts_the_setting_and_skips_the_walk(ball, background):
    search, seen = _physical_search(ball, background)
    lowest = next(
        step
        for step in sorted(STEPS)
        if _assess(
            step,
            _physical_frames(ball, background, step),
            _association(found=ball * step.signal >= 25.0),
        ).acceptable
    )

    assert search.status == "locked"
    assert (search.lock.exposure_us, search.lock.gain) == (lowest.exposure_us, lowest.gain)
    assert len(seen) <= 10  # the 10-75 us sunlight steps add two in a dim scene
    assert search.to_dict()["prediction"]["binding_gate"] == "signal"


def test_the_29_sept_field_search_locks_near_one_millisecond_not_eight():
    """Replays the Pi search: ball found at 2 ms x 12 with signal 40 DN, contrast 3 DN.

    That search needed 8 ms x 10 for a 12 DN contrast and clipped the ball; with
    signal and clipping as the only exposure gates, one measured ball predicts
    about half that product.
    """
    per_product = 40.31 / 23952.0  # signal DN per exposure-us x gain at 2 ms x 12
    search = se.StaticExposureSearch(STEPS)
    for _ in range(200):
        step = search.current_step
        if step is None:
            break
        signal = per_product * step.signal
        ball = min(15.0 + signal, 255.0)
        background = min(15.0 + signal * 0.925, 255.0)  # contrast 3 DN at 40 DN signal
        search.record(
            _assess(
                step,
                _frames(ball, background),
                _association(stable_count=3, found=signal >= 10.0),
            )
        )

    assert search.status == "locked"
    assert search.lock.exposure_us <= 2000
    assert search.lock.exposure_us * search.lock.gain < 23952.0


def test_the_lattice_reaches_short_exposures_and_unity_gain_for_sunlight():
    steps = se.exposure_steps_for_fps(120.0)

    assert min(step.exposure_us for step in steps) <= 30
    assert min(step.gain for step in steps) == 1.0


def test_a_ball_clipped_even_at_the_darkest_setting_is_too_bright():
    """Outdoors 29 Sept: the old darkest setting, 100 us x 2, clipped 38 % of the ball."""
    search, _seen = _driver(STEPS, brightness=50.0)

    assert search.status == "too_bright"
    assert "sun" in search.reason or "bright" in search.reason


def test_an_unknown_black_floor_uses_the_sensor_black_level_not_the_background():
    # audit B5: with no floor the ring median stood in, which made the signal gate a
    # contrast gate again; a 190 DN ball on a 180 DN background was refused
    observation = se.assess_static_exposure(
        _frames(190.0, 180.0),
        _association(),
        requested=STEP,
        applied_exposure_us=STEP.exposure_us,
        applied_gain=STEP.gain,
        black_floor_dn=None,
    )

    assert "signal" not in observation.failed_gates
    assert observation.signal_above_floor_dn == pytest.approx(190.0 - se.SENSOR_BLACK_LEVEL_DN)
