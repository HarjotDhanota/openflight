import { describe, expect, it } from 'vitest';
import { isInImage, toCamera, toScreen, type SetupViewRect } from './setupGeometry';

const KIOSK_SIZES = [
  { width: 1024, height: 600 },
  { width: 800, height: 480 },
] as const;
const CAPTURE_SIZES = [
  { imageWidth: 1280, imageHeight: 800 },
  { imageWidth: 640, imageHeight: 400 },
] as const;
const HANDS = ['RH', 'LH'] as const;
const CAMERA_POINTS: Array<[number, number]> = [
  [0, 0],
  [37.5, 612.25],
  [640, 400],
  [1279, 799],
  [212, 51],
];

const views: SetupViewRect[] = KIOSK_SIZES.flatMap((size) => CAPTURE_SIZES.map((capture) => ({ ...size, ...capture })));

describe('setup view geometry', () => {
  for (const view of views) {
    for (const hand of HANDS) {
      it(`round-trips camera pixels for ${hand} on ${view.width}x${view.height} (${view.imageWidth}x${view.imageHeight})`, () => {
        for (const [x, y] of CAMERA_POINTS) {
          const px: [number, number] = [(x * view.imageWidth) / 1280, (y * view.imageHeight) / 800];
          const back = toCamera(toScreen(px, view, hand), view, hand);
          expect(back[0]).toBeCloseTo(px[0], 9);
          expect(back[1]).toBeCloseTo(px[1], 9);
        }
      });

      it(`round-trips screen taps for ${hand} on ${view.width}x${view.height} (${view.imageWidth}x${view.imageHeight})`, () => {
        for (const pt of [
          [view.width / 2, view.height / 2],
          [view.width * 0.1, view.height * 0.9],
          [view.width * 0.8, view.height * 0.25],
        ] as Array<[number, number]>) {
          const again = toScreen(toCamera(pt, view, hand), view, hand);
          expect(again[0]).toBeCloseTo(pt[0], 9);
          expect(again[1]).toBeCloseTo(pt[1], 9);
        }
      });
    }

    it(`maps a left-handed tap on screen-left to the mirrored camera x on ${view.width}x${view.height}`, () => {
      const scale = Math.min(view.width / view.imageWidth, view.height / view.imageHeight);
      const left = (view.width - view.imageWidth * scale) / 2;
      const tap: [number, number] = [left + 10 * scale, view.height / 2];
      const rh = toCamera(tap, view, 'RH');
      const lh = toCamera(tap, view, 'LH');
      expect(rh[0]).toBeCloseTo(10, 9);
      expect(lh[0]).toBeCloseTo(view.imageWidth - 10, 9);
      expect(lh[1]).toBeCloseTo(rh[1], 9);
    });
  }

  it('fits the whole picture, centred, without stretching it', () => {
    const view = { width: 1024, height: 600, imageWidth: 1280, imageHeight: 800 };
    // 600 / 800 = 0.75 is the tighter scale: 960 px wide, 32 px either side
    expect(toScreen([0, 0], view, 'RH')).toEqual([32, 0]);
    expect(toScreen([1280, 800], view, 'RH')).toEqual([992, 600]);
    expect(toScreen([0, 0], view, 'LH')).toEqual([992, 0]);
    expect(toScreen([1280, 800], view, 'LH')).toEqual([32, 600]);
  });

  it('treats an unknown hand as right-handed', () => {
    const view = { width: 800, height: 480, imageWidth: 640, imageHeight: 400 };
    expect(toScreen([100, 50], view, null)).toEqual(toScreen([100, 50], view, 'RH'));
  });

  it('tells a tap on the picture from one on the letterbox', () => {
    const view = { width: 1024, height: 600, imageWidth: 1280, imageHeight: 800 };
    expect(isInImage(toCamera([20, 300], view, 'RH'), view)).toBe(false);
    expect(isInImage(toCamera([20, 300], view, 'LH'), view)).toBe(false);
    expect(isInImage(toCamera([512, 300], view, 'LH'), view)).toBe(true);
  });
});
