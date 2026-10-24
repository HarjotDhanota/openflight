import type { SetupSnapshot } from '../../hooks/useSetup';
import type { Px } from './setupGeometry';
import { isUsable } from './setupWords';

/** The confirmed box and its ball, in the camera pixels of `imageSize`. */
export interface BoxOverlay {
  imageSize: Px;
  outline: Px[];
  ball: Px | null;
  diameter: number | null;
}

/** The set aim: from the box's ball to the aim point, in the camera pixels of `imageSize`. */
export interface AimOverlay {
  imageSize: Px;
  from: Px;
  to: Px;
}

/** The box the kiosk is using; null when none is confirmed or it is stale. */
export function confirmedBox(setup: SetupSnapshot | null): BoxOverlay | null {
  const box = setup?.box;
  if (!box || !isUsable(box) || box.box_px?.length !== 4 || !box.image_size_px) return null;
  const [x0, y0, x1, y1] = box.box_px;
  return {
    imageSize: box.image_size_px,
    outline: [
      [x0, y0],
      [x1, y0],
      [x1, y1],
      [x0, y1],
    ],
    ball: box.ball_px ?? null,
    diameter: box.diameter_px ?? null,
  };
}

/** The aim line; null unless an aim is set against a box in use. */
export function aimLine(setup: SetupSnapshot | null): AimOverlay | null {
  const box = confirmedBox(setup);
  const aim = setup?.aim;
  if (!box?.ball || aim?.state !== 'set' || !aim.aim_px) return null;
  return { imageSize: box.imageSize, from: box.ball, to: aim.aim_px };
}

/** Whether two pictures are one field of view at different sizes (1280x800 and 640x400 are). */
export function sameShape(a: Px, b: Px): boolean {
  return Math.abs(a[0] / a[1] - b[0] / b[1]) <= 0.01 * (a[0] / a[1]);
}
