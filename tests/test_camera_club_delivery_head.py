"""The club-delivery tracker follows the clubhead, not the shaft's glint.

Oct 27 outdoors (harjot-outdoor-test-2): the sunlit sky set the glint threshold to
217 DN, so only the chrome shaft passed it, and the thickest moving blob near the
glint's ball-side end was the motion-blurred shaft. The head - a dark interior (about
44 DN on a 41 DN background) inside a bright outline (120-165 DN) - was never tracked.
These scenes reproduce that: a bright blurred shaft, a dark-interior head outline near
the ball, a saturated sky, and (second scene) sky uncovered between moving legs.
"""

import cv2
import numpy as np
import pytest

from openflight.camera.club_delivery import _adaptive_thresholds, _clubhead_pair_tracks
from openflight.camera.club_motion import ReferenceBall

HEIGHT, WIDTH = 400, 640
BALL = ReferenceBall(x=400.0, y=200.0, diameter_px=22.0, area_px=380)
# Blade outline relative to the hosel's lower end (heel), toe to image right.
BLADE = np.array([(0, 0), (8, -6), (40, -14), (50, -8), (50, 6), (42, 12), (4, 12)])
GROOVES = (((12, 0), (40, -6)), ((12, 6), (40, 2)))
SEED, SECOND = 20, 22
MOTION = np.array([6.0, 18.0])  # head pixels per interval, toward the ball


def _background(rng: np.random.Generator) -> np.ndarray:
    image = np.full((HEIGHT, WIDTH), 41.0)
    image[:30] = 255.0  # saturated sky: the scene's brightest content
    for column in range(20, WIDTH, 37):  # fence slats: static texture
        image[40:170, column : column + 4] = 75.0
    image[230:] = 55.0  # mat
    image += rng.normal(0.0, 2.0, image.shape)
    return image


def _draw_ball(image: np.ndarray) -> None:
    cv2.circle(image, (int(BALL.x), int(BALL.y)), int(BALL.diameter_px / 2), 235.0, -1)


def _draw_club(image: np.ndarray, hosel, hands, *, shaft_dn: float, shaft_px: int) -> None:
    hosel = np.asarray(hosel, float)
    cv2.line(image, tuple(map(int, hands)), tuple(map(int, hosel)), shaft_dn, shaft_px)
    blade = np.round(BLADE + hosel).astype(np.int32)
    cv2.fillPoly(image, [blade], 44.0)  # dark interior, ~background
    cv2.polylines(image, [blade], True, 150.0, 2)  # topline/toe/leading-edge highlights
    cv2.line(image, tuple(blade[5]), tuple(blade[6]), 165.0, 2)  # sole highlight
    for start, end in GROOVES:
        cv2.line(
            image,
            tuple(map(int, hosel + start)),
            tuple(map(int, hosel + end)),
            70.0,
            1,
        )


def _frames(hosel, *, shaft_dn=250.0, shaft_px=9, legs=False, seed=3):
    """Fifteen still frames, then a swing whose head moves ``MOTION`` per interval."""
    rng = np.random.default_rng(seed)
    base = _background(rng)
    if legs:
        base[30:110, 120:250] = 255.0  # sky seen through the gap between the legs
        base[0:230, 140:165] = 60.0
        base[0:230, 200:225] = 60.0
    frames = []
    for index in range(30):
        image = base.copy()
        _draw_ball(image)
        if index >= SEED - 2:
            step = index - SEED
            if legs:  # the legs shift and uncover sky the background never saw
                image[0:230, 140:165] = base[0:230, 120:140].mean()
                image[30:110, 140:165] = 255.0
                image[0:230, 160:185] = 60.0
                image[0:230, 215:240] = 60.0
            moved = np.asarray(hosel, float) + MOTION * step / 2.0
            _draw_club(image, moved, (290.0 - step, 0.0), shaft_dn=shaft_dn, shaft_px=shaft_px)
        image += rng.normal(0.0, 1.5, image.shape)
        frames.append(np.clip(image, 0, 255).astype(np.uint8))
    return np.stack(frames)


def _track(frames, reasons=None):
    background = np.median(frames[:15], axis=0).astype(np.uint8)
    _p995, _ball, bright_now, dark_bg = _adaptive_thresholds(background)
    assert bright_now > 200.0  # the sky, not the club, sets the glint threshold
    return _clubhead_pair_tracks(
        frames,
        background,
        BALL,
        first_idx=SEED,
        second_idx=SECOND,
        bright_now=bright_now,
        dark_bg=dark_bg,
        **({} if reasons is None else {"reasons": reasons}),
    )


def _on_head(points, hosel, margin=4.0) -> np.ndarray:
    blade = BLADE + np.asarray(hosel, float)
    low, high = blade.min(axis=0) - margin, blade.max(axis=0) + margin
    return np.all((points >= low) & (points <= high), axis=1)


@pytest.mark.parametrize(
    "shaft_dn, shaft_px", [(250.0, 9), (250.0, 5)], ids=["blurred_glint", "thin_glint"]
)
def test_the_head_is_tracked_not_the_glinting_shaft(shaft_dn, shaft_px):
    hosel = (370.0, 165.0)
    tracks, _thickness = _track(_frames(hosel, shaft_dn=shaft_dn, shaft_px=shaft_px))

    assert tracks is not None and len(tracks) >= 3
    assert _on_head(tracks[:, 0], hosel).all(), tracks[:, 0]
    assert np.allclose(np.median(tracks[:, 1] - tracks[:, 0], axis=0), MOTION * 1.0, atol=2.0)


def test_sky_uncovered_between_the_legs_is_not_the_club():
    hosel = (370.0, 165.0)
    tracks, _thickness = _track(_frames(hosel, shaft_dn=140.0, shaft_px=5, legs=True))

    assert tracks is not None and len(tracks) >= 3
    assert _on_head(tracks[:, 0], hosel).all(), tracks[:, 0]
    assert not np.any(tracks[:, 0, 0] < 260.0)  # nothing on the legs


def test_a_head_far_above_the_ball_band_is_refused_with_its_reason():
    hosel = (350.0, 95.0)  # club still high: about 5 ball diameters above the ball
    reasons: list[str] = []
    tracks, _thickness = _track(_frames(hosel), reasons)

    assert tracks is None
    assert any("above the ball" in reason for reason in reasons), reasons


def test_no_club_in_view_is_refused_with_its_reason():
    frames = _frames((370.0, 165.0))
    frames[SEED - 2 :] = frames[0]
    reasons: list[str] = []
    tracks, _thickness = _track(frames, reasons)

    assert tracks is None
    assert reasons and "shaft" in reasons[0], reasons
