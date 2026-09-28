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
        (80.0, 75.0, "contrast"),
        (255.0, 40.0, "clipped"),
    ],
)
def test_ball_pixel_gates_reject_dark_low_contrast_and_clipped_balls(
    ball_dn, background_dn, reason
):
    observation = _assess(STEP, _frames(ball_dn, background_dn), _association())

    assert observation.status == "rejected"
    assert reason in observation.reason


def test_a_passing_ball_waits_for_temporal_stability():
    observation = _assess(STEP, _frames(160.0), _association(stable_count=1))

    assert observation.status == "stabilizing"


def _driver(steps, brightness, *, applied_offset=None, clip_above=None, always_found=False):
    """Run a search against a scene whose ball level scales with exposure x gain."""
    search = se.StaticExposureSearch(steps)
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
    """Night scene: detection works at the dimmest step, only ~3 ms passes the gates."""
    search, seen = _driver(STEPS, brightness=0.0012, always_found=True)

    assert search.status == "locked"
    assert search.lock.exposure_us >= 3000
    assert len(seen) <= 30


def test_search_reports_lighting_required_when_the_brightest_setting_hides_the_ball():
    search, _seen = _driver(STEPS, brightness=0.000001)

    assert search.status == "lighting_required"
    assert search.lock is None
    assert "not visible" in search.reason


def test_search_fails_when_no_visible_setting_passes_the_optical_gates():
    search, _seen = _driver(STEPS, brightness=0.004, clip_above=40.0)

    assert search.status == "lighting_required"
    assert search.lock is None


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
        "12c5310d66f3409d2bf7110052445e1e226f402e6d714f364980c664037e7bbf"
    )
