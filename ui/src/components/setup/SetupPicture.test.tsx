import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { SetupSnapshot } from '../../hooks/useSetup';
import { SetupPicture } from './SetupPicture';
import { aimLine, boxOutline, confirmedBox, sameShape } from './setupOverlay';

const notSet = { state: 'not_set', reason: 'not set' };

function snapshot(changes: Partial<SetupSnapshot> = {}): SetupSnapshot {
  return {
    revision: 3,
    box: {
      state: 'confirmed',
      box_px: [380, 560, 900, 720],
      ball_px: [640, 610],
      diameter_px: 22,
      image_size_px: [1280, 800],
    },
    aim: { state: 'set', aim_px: [900, 300] },
    ball_range: notSet,
    net_range: notSet,
    scene: notSet,
    live: { camera_available: true },
    unapplied: {},
    ...changes,
  };
}

describe('the setup drawn on a camera picture', () => {
  it('falls back to the box_px rectangle for a record saved before outlines were kept', () => {
    expect(confirmedBox(snapshot())).toEqual({
      imageSize: [1280, 800],
      outline: [
        [380, 560],
        [900, 560],
        [900, 720],
        [380, 720],
      ],
      ball: [640, 610],
      diameter: 22,
    });
  });

  it('draws the outline the user confirmed, not the rectangle around it', () => {
    const outline: [number, number][] = [
      [430, 700],
      [850, 700],
      [850, 580],
      [430, 580],
    ];
    const saved = snapshot({ box: { ...snapshot().box, outline_px: outline } });
    expect(confirmedBox(saved)?.outline).toEqual(outline);
    expect(boxOutline(saved)).toEqual(outline);
  });

  it("puts the Setup screen's live look first, only when asked", () => {
    const look: [number, number][] = [
      [1, 1],
      [9, 1],
      [9, 9],
    ];
    const setup = snapshot({ live: { box: { state: 'found', ball_px: null, diameter_px: null, outline_px: look } } });
    expect(boxOutline(setup, { live: true })).toEqual(look);
    expect(boxOutline(setup)).toEqual(confirmedBox(setup)?.outline);
  });

  it('draws no box the kiosk could not put into effect', () => {
    expect(confirmedBox(snapshot({ unapplied: { box: 'the box is outside the 640x400 picture' } }))).toBeNull();
    expect(aimLine(snapshot({ unapplied: { box: 'the box is outside the 640x400 picture' } }))).toBeNull();
  });

  it('draws no box that is not set or is stale, and none without its picture size', () => {
    expect(confirmedBox(null)).toBeNull();
    expect(confirmedBox(snapshot({ box: notSet }))).toBeNull();
    expect(confirmedBox(snapshot({ box: { ...snapshot().box, state: 'stale' } }))).toBeNull();
    expect(confirmedBox(snapshot({ box: { ...snapshot().box, image_size_px: undefined } }))).toBeNull();
  });

  it('runs the aim line from the box ball to the aim point', () => {
    expect(aimLine(snapshot())).toEqual({ imageSize: [1280, 800], from: [640, 610], to: [900, 300] });
  });

  it('draws no aim line for a skipped, unset or stale aim, or without a box', () => {
    expect(aimLine(snapshot({ aim: { state: 'skipped' } }))).toBeNull();
    expect(aimLine(snapshot({ aim: notSet }))).toBeNull();
    expect(aimLine(snapshot({ aim: { state: 'stale', aim_px: [900, 300] } }))).toBeNull();
    expect(aimLine(snapshot({ box: notSet }))).toBeNull();
  });

  it('draws only on a picture of the same shape as the one the setup was made on', () => {
    expect(sameShape([1280, 800], [640, 400])).toBe(true);
    expect(sameShape([1280, 800], [320, 200])).toBe(true);
    expect(sameShape([1280, 800], [1280, 720])).toBe(false);
  });

  it("mirrors a left-hander's picture, as the Setup view does", () => {
    const lh = renderToString(<SetupPicture src="x.jpg" alt="Camera preview" handed="LH" />);
    const rh = renderToString(<SetupPicture src="x.jpg" alt="Camera preview" handed="RH" />);
    expect(lh).toContain('setup-picture__image--mirrored');
    expect(rh).not.toContain('setup-picture__image--mirrored');
  });
});
