import type { SetupSnapshot } from '../../hooks/useSetup';
import type { Px } from './setupGeometry';
import { isUsable } from './setupWords';

/** The confirmed box and its ball, in camera pixels. */
export interface BoxOverlay {
  outline: Px[];
  ball: Px | null;
  diameter: number | null;
}

/** The set aim: from the box's ball to the aim point, in camera pixels. */
export interface AimOverlay {
  from: Px;
  to: Px;
}

/**
 * The box outline to draw, in camera pixels: the Setup screen's live look
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
  if (!box || !isUsable(box) || 'box' in (setup?.unapplied ?? {}) || !outline) return null;
  return {
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
  return { from: box.ball, to: aim.aim_px };
}
