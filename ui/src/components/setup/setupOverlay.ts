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

/**
 * The box outline to draw, in the box's `image_size_px` pixels: the Setup screen's live look
 * when `live` and there is one, else the saved confirmed outline, else (a record saved before
 * outlines were kept) the `box_px` rectangle the kiosk then tests the ball against.
 */
export function boxOutline(setup: SetupSnapshot | null, { live = false } = {}): Px[] | null {
  const looked = setup?.live.box?.outline_px;
  if (live && looked && looked.length > 2) return looked;
  const box = setup?.box;
  if (box?.outline_px && box.outline_px.length > 2) return box.outline_px;
  if (box?.box_px?.length !== 4) return null;
  const [x0, y0, x1, y1] = box.box_px;
  return [
    [x0, y0],
    [x1, y0],
    [x1, y1],
    [x0, y1],
  ];
}

/** The box the kiosk is using; null when none is confirmed or it is stale or unapplied. */
export function confirmedBox(setup: SetupSnapshot | null): BoxOverlay | null {
  const box = setup?.box;
  const outline = boxOutline(setup);
  if (!box || !isUsable(box) || 'box' in (setup?.unapplied ?? {}) || !outline || !box.image_size_px) return null;
  return {
    imageSize: box.image_size_px,
    outline,
    ball: box.ball_px ?? null,
    diameter: box.diameter_px ?? null,
  };
}

/** The aim line; null unless an aim is set against a box in use. */
export function aimLine(setup: SetupSnapshot | null): AimOverlay | null {
  const box = confirmedBox(setup);
  const aim = setup?.aim;
  if (!box?.ball || aim?.state !== 'set' || !aim.aim_px) return null;
  const to = toPicture(aim.aim_px, aim.image_size_px, box.imageSize);
  return to ? { imageSize: box.imageSize, from: box.ball, to } : null;
}

/** Whether two pictures are one field of view at different sizes (1280x800 and 640x400 are). */
export function sameShape(a: Px, b: Px): boolean {
  return Math.abs(a[0] / a[1] - b[0] / b[1]) <= 0.01 * (a[0] / a[1]);
}

/**
 * A pixel saved on a `from`-sized picture, in a `to`-sized one of the same field of view
 * (the kiosk's linear rescale between binned modes); null when the shapes differ. Without
 * either size the pixel stands.
 */
export function toPicture(px: Px, from: Px | null | undefined, to: Px | null | undefined): Px | null {
  if (!from || !to) return px;
  if (!sameShape(from, to)) return null;
  return [(px[0] * to[0]) / from[0], (px[1] * to[1]) / from[1]];
}

/** The saved box and aim, in the pixels of the picture on screen; null where they cannot be. */
export interface SavedMarks {
  outline: Px[] | null;
  ball: Px | null;
  diameter: number | null;
  aim: Px | null;
  floor: Px[] | null;
}

/**
 * The Setup screen's saved marks moved into `picture` (the still's size): the box's in the
 * box's `image_size_px`, the aim's in its own (an older aim's are the box's).
 */
export function savedMarks(setup: SetupSnapshot, picture: Px | null): SavedMarks {
  const boxSize = setup.box.image_size_px ?? null;
  const aimSize = setup.aim.image_size_px ?? boxSize;
  const every = (points: Px[] | null | undefined, from: Px | null): Px[] | null => {
    if (!points) return null;
    const moved = points.map((px) => toPicture(px, from, picture));
    return moved.every((px) => px !== null) ? (moved as Px[]) : null;
  };
  const ball = setup.box.ball_px ? toPicture(setup.box.ball_px, boxSize, picture) : null;
  const scale = boxSize && picture ? picture[0] / boxSize[0] : 1;
  const aimSet = setup.aim.state === 'set';
  return {
    outline: every(boxOutline(setup), boxSize),
    ball,
    diameter: ball && setup.box.diameter_px != null ? setup.box.diameter_px * scale : null,
    aim: aimSet && setup.aim.aim_px ? toPicture(setup.aim.aim_px, aimSize, picture) : null,
    floor: aimSet ? every(setup.aim.floor_line, aimSize) : null,
  };
}
