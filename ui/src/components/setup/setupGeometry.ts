import type { Handed } from '../../stores/useSystemStore';

export type Px = [number, number];

/** The view on screen (CSS px) and the camera picture drawn in it, whole and centred. */
export interface SetupViewRect {
  width: number;
  height: number;
  imageWidth: number;
  imageHeight: number;
}

function fit(view: SetupViewRect) {
  const scale = Math.min(view.width / view.imageWidth, view.height / view.imageHeight);
  return {
    scale,
    left: (view.width - view.imageWidth * scale) / 2,
    top: (view.height - view.imageHeight * scale) / 2,
  };
}

// A left-hander's view is drawn mirrored, so the golfer's side of the ball is the same
// side of the screen for both hands. Only the drawing mirrors: saved pixels never do.
function mirrored(handed: Handed | null): boolean {
  return handed === 'LH';
}

/** Where a camera pixel is drawn in the view. */
export function toScreen(px: Px, view: SetupViewRect, handed: Handed | null): Px {
  const { scale, left, top } = fit(view);
  const x = mirrored(handed) ? view.imageWidth - px[0] : px[0];
  return [left + x * scale, top + px[1] * scale];
}

/** The camera pixel under a point in the view. */
export function toCamera(pt: Px, view: SetupViewRect, handed: Handed | null): Px {
  const { scale, left, top } = fit(view);
  const x = (pt[0] - left) / scale;
  return [mirrored(handed) ? view.imageWidth - x : x, (pt[1] - top) / scale];
}

/** Whether a camera pixel is on the picture, not the bars beside it. */
export function isInImage(px: Px, view: SetupViewRect): boolean {
  return px[0] >= 0 && px[0] <= view.imageWidth && px[1] >= 0 && px[1] <= view.imageHeight;
}
