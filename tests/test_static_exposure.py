"""Static reference-ball exposure: ball-pixel gates, and the ball-targeted brightness loop."""

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


# P7-5: darkness is judged on the patch (the placement box before it), never on the
# whole frame (Outdoors-test-7, 30 Sept).
BOX = (40, 20, 80, 60)


def _split_frames(box_dn, outside_dn, *, count=5, shape=(80, 120)):
    """A frame lit differently inside the patch's box than around it."""
    image = np.full(shape, outside_dn, dtype=np.float32)
    x0, y0, x1, y1 = BOX
    image[y0:y1, x0:x1] = box_dn
    return np.repeat(image.astype(np.uint8)[None], count, axis=0)


def test_darkness_is_judged_on_the_patch_not_the_whole_frame():
    step = se.StaticExposureStep(10, 1.0)
    missing = {"status": "not_found", "selected": None}

    sunlit_box = se.assess_static_exposure(
        _split_frames(120.0, 20.0),
        missing,
        requested=step,
        applied_exposure_us=10,
        applied_gain=1.0,
        black_floor_dn=15.0,
        region=BOX,
    )
    shaded_box = se.assess_static_exposure(
        _split_frames(20.0, 200.0),
        missing,
        requested=step,
        applied_exposure_us=10,
        applied_gain=1.0,
        black_floor_dn=15.0,
        region=BOX,
    )

    assert sunlit_box.frame_signal_dn == pytest.approx(105.0)
    assert shaded_box.frame_signal_dn == pytest.approx(5.0)
    assert sunlit_box.frame_signal_region == "placement_box"


# P8-2: a few steps aimed at the ball's own brightness replace the 44-step search.
# "not found" never means dark: only the patch's pixels say whether it is dark.
MAX_EXPOSURE_US = se.max_static_exposure_us(120.0)


class Scene:
    """A ball and a patch whose levels follow exposure x gain above black, as the OV9281's do."""

    def __init__(self, ball_per_signal, patch_per_signal, *, found_above_dn=20.0, status=None):
        self.ball = ball_per_signal
        self.patch = patch_per_signal
        self.found_above = found_above_dn
        self.status = status

    def observe(self, step, *, applied=None):
        product = step.exposure_us * step.gain
        ball = min(255.0, 16.0 + self.ball * product)
        patch = min(255.0, 16.0 + self.patch * product)
        image = np.full((80, 120), patch, dtype=np.float32)
        yy, xx = np.ogrid[:80, :120]
        image[np.hypot(xx - 60, yy - 40) <= 8] = ball
        frames = np.repeat(np.clip(image, 0, 255).astype(np.uint8)[None], 5, axis=0)
        if self.status is not None:
            association = {"status": self.status, "selected": None, "stable_count": 0}
        elif self.ball > 0 and ball - 16.0 >= self.found_above:
            association = _association(stable_count=3)
        else:
            association = {"status": "not_found", "selected": None, "stable_count": 0}
        exposure, gain = applied if applied is not None else (step.exposure_us, step.gain)
        return se.assess_static_exposure(
            frames,
            association,
            requested=step,
            applied_exposure_us=exposure,
            applied_gain=gain,
            black_floor_dn=16.0,
            region=(20, 10, 100, 70),
        )


def _run(scene, start, *, warm_start=None, limit=60):
    search = se.BallBrightnessSearch(MAX_EXPOSURE_US, start=start, warm_start=warm_start)
    requested = []
    for _ in range(limit):
        step = search.current_step
        if step is None:
            break
        if not requested or requested[-1] != step:
            requested.append(step)
        search.record(scene.observe(step))
    return search, requested


def _lock_level(search, scene):
    lock = search.lock
    return 16.0 + scene.ball * lock.exposure_us * lock.gain


def test_a_dim_ball_is_brought_up_to_its_target_in_a_few_steps():
    scene = Scene(ball_per_signal=0.004, patch_per_signal=0.002, found_above_dn=5.0)

    search, requested = _run(scene, se.StaticExposureStep(300, 4.0))

    assert search.status == "locked"
    assert len(requested) <= 3
    level = _lock_level(search, scene)
    assert 36.0 <= level < 250.0  # at least 20 DN above black, and not clipped


def test_a_clipped_ball_is_brought_down_below_clipping():
    scene = Scene(ball_per_signal=0.2, patch_per_signal=0.05)

    search, requested = _run(scene, se.StaticExposureStep(2000, 12.0))

    assert search.status == "locked"
    assert len(requested) <= 4
    assert _lock_level(search, scene) < 250.0
    # never brighter than where it started
    assert all(step.exposure_us * step.gain <= 24000 for step in requested)


def test_no_ball_in_a_lit_patch_ends_quickly_and_never_brightens():
    """harjot-indoor-test-1: each 'not found' walked the old search brighter, to 8 ms x 12."""
    scene = Scene(ball_per_signal=0.0, patch_per_signal=0.02)

    search, requested = _run(scene, se.StaticExposureStep(300, 8.0))

    assert search.status == "ball_not_found"
    assert "patch" in search.reason
    assert requested == [se.StaticExposureStep(300, 8.0)]
    assert len(search.attempts) <= se.NOT_FOUND_LIMIT + 1


def test_a_dark_patch_is_brightened_by_its_own_pixels_then_the_ball_is_found():
    scene = Scene(ball_per_signal=0.01, patch_per_signal=0.004)

    search, requested = _run(scene, se.StaticExposureStep(100, 1.0))

    assert search.status == "locked"
    # the first step up was sized by the patch's level, not a fixed walk
    assert len(requested) <= 4
    assert _lock_level(search, scene) >= 36.0


def test_ambiguity_never_changes_the_brightness_and_is_counted_apart_from_not_found():
    scene = Scene(ball_per_signal=0.01, patch_per_signal=0.02, status="ambiguous")
    search = se.BallBrightnessSearch(MAX_EXPOSURE_US, start=se.StaticExposureStep(300, 4.0))
    lit_missing = Scene(ball_per_signal=0.0, patch_per_signal=0.02)

    for index in range(se.IDENTIFY_LIMIT + 2):
        step = search.current_step
        if step is None:
            break
        # a 'not found' in between does not reset the count of ambiguous looks
        observation = lit_missing.observe(step) if index == 2 else scene.observe(step)
        search.record(observation)

    assert search.status == "ball_not_identified"
    assert "patch" in search.reason
    assert {attempt["exposure_us"] for attempt in search.attempts} == {300}


@pytest.mark.parametrize(
    ("scene", "start", "status"),
    [
        (
            Scene(0.0001, 0.00005, found_above_dn=1.0),
            se.StaticExposureStep(8000, 12.0),
            "lighting_required",
        ),
        (Scene(50.0, 10.0), se.StaticExposureStep(10, 1.0), "too_bright"),
    ],
)
def test_the_sensors_limits_end_the_loop_with_what_is_needed(scene, start, status):
    search, _requested = _run(scene, start)

    assert search.status == status
    assert search.lock is None


def test_a_remembered_lock_that_still_passes_locks_at_once():
    scene = Scene(ball_per_signal=0.02, patch_per_signal=0.01)
    remembered = se.StaticExposureStep(1250, 4.0)

    search, requested = _run(scene, se.StaticExposureStep(300, 1.0), warm_start=remembered)

    assert search.status == "locked"
    assert requested == [remembered]
    assert search.attempts[0]["stage"] == "warm_start"


def test_controls_the_camera_never_applies_end_the_loop():
    search = se.BallBrightnessSearch(MAX_EXPOSURE_US, start=se.StaticExposureStep(300, 4.0))
    scene = Scene(ball_per_signal=0.02, patch_per_signal=0.01)
    for _ in range(40):
        step = search.current_step
        if step is None:
            break
        search.record(scene.observe(step, applied=(step.exposure_us * 3, step.gain)))

    assert search.status == "controls_not_applied"
    assert search.lock is None


def test_the_indoor_patch_locks_on_the_ball_in_a_few_steps():
    """harjot-indoor-test-1's light-screen frame with a ball composited in, its
    levels scaled linearly with exposure x gain from the 300 us x 8 screen."""
    from test_patch_ball_search import INDOOR_BALL_PX, indoor_scene  # noqa: PLC0415

    reference = np.median(indoor_scene(), axis=0)
    region = (661, 413, 817, 548)
    x, y = INDOOR_BALL_PX

    def observe(step):
        scale = step.exposure_us * step.gain / 2400.0
        image = np.clip(16.0 + (reference - 16.0) * scale, 0, 255).astype(np.uint8)
        frames = np.repeat(image[None], 5, axis=0)
        association = {
            "status": "selected",
            "selected": {"x_px": x, "y_px": y, "diameter_px": 31.8},
            "stable_count": 3,
        }
        return se.assess_static_exposure(
            frames,
            association,
            requested=step,
            applied_exposure_us=step.exposure_us,
            applied_gain=step.gain,
            black_floor_dn=16.0,
            region=region,
        )

    search = se.BallBrightnessSearch(MAX_EXPOSURE_US, start=se.StaticExposureStep(300, 1.0))
    requested = []
    for _ in range(40):
        step = search.current_step
        if step is None:
            break
        if not requested or requested[-1] != step:
            requested.append(step)
        search.record(observe(step))

    assert search.status == "locked"
    assert len(requested) <= 3
    # nowhere near the 8000 us x 12 the old search walked to
    assert search.lock.exposure_us * search.lock.gain < 8000


def test_lock_serializes_with_policy_identity_and_applied_controls(tmp_path):
    scene = Scene(ball_per_signal=0.02, patch_per_signal=0.01)
    search, _requested = _run(scene, se.StaticExposureStep(1250, 4.0))
    path = tmp_path / "lock.json"

    se.write_static_exposure_lock(path, search.lock)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["purpose"] == se.STATIC_EXPOSURE_PURPOSE
    assert payload["policy_sha256"] == se.static_exposure_policy_sha256()
    assert payload["applied_exposure_us"] == payload["exposure_us"]


def test_the_policy_names_the_ball_targeted_loop():
    policy = se.static_exposure_policy()

    assert policy["version"] == 6
    assert policy["search"]["method"] == "ball_targeted_brightness_steps"
    assert policy["search"]["not_found_is_never_darkness"] is True
    assert policy["gates"]["minimum_signal_above_floor_dn"] == 20.0
