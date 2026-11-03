import { FACE_SHAPES, flatten, type DeliveryShot, type FaceCategory } from './faceImpact';

export type DeliveryView = 'face' | 'top' | 'side';
export const VIEW_METRICS = {
  face: ['impact_height', 'impact_offset', 'dynamic_lie'],
  top: ['face_to_path', 'face_angle', 'club_path'],
  side: ['spin_loft', 'dynamic_loft', 'attack_angle'],
} as const;

export interface ClubProfile {
  length: number;
  toe: number;
  depth: number;
  height: number;
  faceHeight: number;
  loft: number;
  lie: number;
  progression: number;
  topline: number;
  sole: number;
  bounce: number;
}

// Population aggregates and provisional dimensions: docs/camera/club-delivery-views.md.
export const CLUB_PROFILES: Record<FaceCategory, ClubProfile> = {
  driver: {
    length: 117,
    toe: 66.39,
    depth: 114.5,
    height: 62,
    faceHeight: 50.3,
    loft: 10.4,
    lie: 57.4,
    progression: 16.2,
    topline: 0,
    sole: 0,
    bounce: 0,
  },
  fairway: {
    length: 105,
    toe: 56.1,
    depth: 87.9,
    height: 40.6,
    faceHeight: 30,
    loft: 14.9,
    lie: 56.6,
    progression: 15.4,
    topline: 0,
    sole: 0,
    bounce: 0,
  },
  hybrid: {
    length: 101,
    toe: 52.29,
    depth: 68.5,
    height: 37.5,
    faceHeight: 28.2,
    loft: 18.8,
    lie: 57.7,
    progression: 15.3,
    topline: 0,
    sole: 0,
    bounce: 0,
  },
  iron: {
    length: 79.4,
    toe: 44.24,
    depth: 0,
    height: 49.2,
    faceHeight: 44.4,
    loft: 31.4,
    lie: 62.3,
    progression: 3.03,
    topline: 5.83,
    sole: 20.6,
    bounce: 6.09,
  },
  wedge: {
    length: 80,
    toe: 45,
    depth: 0,
    height: 56.6,
    faceHeight: 51.8,
    loft: 56.3,
    lie: 64,
    progression: 4.55,
    topline: 5.83,
    sole: 23.84,
    bounce: 10.44,
  },
};

export function deliveryValue(shot: DeliveryShot, metric: string): number | null {
  const record = shot.metrics?.[metric];
  return record && record.status !== 'not_measured' && typeof record.value === 'number' && Number.isFinite(record.value)
    ? record.value
    : null;
}

export function direction(degrees: number, length = 86): [number, number] {
  const angle = (degrees * Math.PI) / 180;
  return [length * Math.cos(angle), -length * Math.sin(angle)];
}

export function faceDeliveryRotation(shot: DeliveryShot, category: FaceCategory): number {
  const lie = deliveryValue(shot, 'dynamic_lie');
  if (lie === null) return 0;
  const loft = deliveryValue(shot, 'dynamic_loft') ?? CLUB_PROFILES[category].loft;
  const radians = Math.PI / 180;
  const projected = Math.atan2(Math.sin(lie * radians) * Math.cos(loft * radians), Math.cos(lie * radians)) / radians;
  return FACE_SHAPES[category].hosel.lieDeg - projected;
}

const n = (value: number) => Number(value.toFixed(3));
const point = (x: number, y: number) => `${n(x)} ${n(y)}`;

// The source paths use absolute M/L/C pairs; an affine projection preserves their fair curves.
function bladePlan(path: string, scale: number, offset = 0): string {
  return path.replace(/(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)/g, (_, x: string, y: string) =>
    point(Number(y) * scale + offset, Number(x))
  );
}

export function clubProfilePaths(category: FaceCategory) {
  const p = CLUB_PROFILES[category];
  const wood = p.depth > 0;
  const toe = p.toe;
  const heel = p.length - toe;
  const radians = (p.loft * Math.PI) / 180;
  const projection = p.faceHeight * Math.sin(radians);
  const depth = wood ? p.depth : projection + p.topline;
  const back = p.topline * Math.cos(radians);
  const top = wood
    ? `M 0 0 C 0 ${-toe * 0.65} -5 ${-toe} ${-depth * 0.36} ${-toe} C ${-depth * 0.74} ${-toe} ${-depth} ${-toe * 0.66} ${-depth} 0 C ${-depth} ${heel * 0.72} ${-depth * 0.65} ${heel} ${-depth * 0.34} ${heel} C -6 ${heel} 0 ${heel * 0.56} 0 0 Z`
    : bladePlan(FACE_SHAPES[category].face, Math.sin(radians) + back / p.faceHeight, -back / 2);
  const topFace = wood
    ? `M ${-depth * 0.36} ${-toe} C -5 ${-toe} 0 ${-toe * 0.65} 0 0 C 0 ${heel * 0.56} -6 ${heel} ${-depth * 0.34} ${heel} C ${-6 - projection * 0.3} ${heel} ${-projection} ${heel * 0.56} ${-projection} 0 C ${-projection} ${-toe * 0.65} ${-5 - projection * 0.3} ${-toe} ${-depth * 0.36} ${-toe} Z`
    : bladePlan(FACE_SHAPES[category].face, Math.sin(radians));
  const topFaceJoin = wood
    ? `M ${-depth * 0.36} ${-toe} C ${-5 - projection * 0.3} ${-toe} ${-projection} ${-toe * 0.65} ${-projection} 0 C ${-projection} ${heel * 0.56} ${-6 - projection * 0.3} ${heel} ${-depth * 0.34} ${heel}`
    : '';
  const [tx, ty] = [(-Math.sin(radians) * p.faceHeight) / 2, (-Math.cos(radians) * p.faceHeight) / 2];
  const [bx, by] = [-tx, -ty];
  const crown = -p.height + p.faceHeight / 2;
  const soleY = by + (wood ? 4 : p.sole * Math.tan((p.bounce * Math.PI) / 180));
  const side = wood
    ? `M ${point(tx, ty)} C ${point(tx - 5, crown)} ${point(-depth * 0.2, crown)} ${point(-depth * 0.4, crown + 3)} C ${point(-depth * 0.7, crown + 7)} ${point(-depth, 0)} ${point(-depth, by - 5)} C ${point(-depth, soleY)} ${point(-depth * 0.3, soleY)} ${point(bx - 4, by + 2)} Q ${point(bx + 1, by + 1)} ${point(bx, by - 2)} L ${point(tx, ty)} Z`
    : `M ${point(tx, ty)} Q ${point(tx - p.topline / 2, ty - 2)} ${point(tx - p.topline, ty)} C ${point(tx - p.topline, ty + p.faceHeight * 0.3)} ${point(bx - p.sole - 3, by - 14)} ${point(bx - p.sole, soleY - 3)} Q ${point(bx - p.sole / 2, soleY + 1)} ${point(bx - 2, by + 1)} Q ${point(bx + 1, by)} ${point(bx, by - 2)} L ${point(tx, ty)} Z`;
  const topShellTransform = `matrix(0 1 ${Math.sin(radians) + back / p.faceHeight} 0 ${-back / 2} 0)`;
  return {
    top,
    topFace,
    topFaceJoin,
    topShellTransform,
    side,
    sideFace: `M ${point(tx, ty)} L ${point(bx, by)}`,
    depth,
    heel,
    tx,
    ty,
    bx,
    by,
  };
}

/** Half the blade shell's 4.8 mm outline stroke: the metal edge band round the projected face. */
export const BLADE_EDGE_BAND_MM = 2.4;

/** Where the shaft, ferrule and hosel sit in the top or heel view (svg mm, before the hand's mirror). */
export function hoselLayout(category: FaceCategory, view: 'top' | 'side') {
  const p = CLUB_PROFILES[category];
  const paths = clubProfilePaths(category);
  const top = view === 'top';
  const shaftX = !p.depth ? paths.bx - p.progression : -p.progression;
  const shaftY = top ? paths.heel + (p.depth ? -8 : 7) : p.depth ? -p.height + p.faceHeight / 2 + 5 : -24;
  const shaftEnd = top ? shaftY + 24 : -64;
  const ferruleEnd = top ? shaftY + 17 : shaftY - 20;
  const ferruleBase = ferruleEnd + (top ? -8 : 8);
  return {
    shaftX,
    shaftY,
    shaftEnd,
    ferruleEnd,
    ferruleBase,
    ferruleRadius: 4.8 + (1.6 * 8) / Math.abs(ferruleEnd - shaftY),
    shaftRadius: 4.6 - (0.4 * (ferruleEnd - shaftY)) / (shaftEnd - shaftY),
    crownJoin: shaftY + (top ? -10 : 10),
  };
}

type Point = [number, number];
const unit = ([x, y]: Point): Point => [x / Math.hypot(x, y), y / Math.hypot(x, y)];

/** The cubic from p0 to p3 at t. */
function cubicAt(p: readonly Point[], t: number): Point {
  const [a, b, c, e] = [(1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3];
  return [a * p[0][0] + b * p[1][0] + c * p[2][0] + e * p[3][0], a * p[0][1] + b * p[1][1] + c * p[2][1] + e * p[3][1]];
}

/** The first part (t from 0 to `t`) of a cubic, as its four control points. */
function cubicHead(p: readonly Point[], t: number): Point[] {
  const lerp = (a: Point, b: Point): Point => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  const [ab, bc, cd] = [lerp(p[0], p[1]), lerp(p[1], p[2]), lerp(p[2], p[3])];
  const [abc, bcd] = [lerp(ab, bc), lerp(bc, cd)];
  return [p[0], ab, abc, lerp(abc, bcd)];
}

/** How far toe-ward of the face's top-heel corner the top view's neck leaves the blade, mm. */
const TOP_NECK_JOIN_MM = 6;
/** How far above the top line's cap the heel view's fillet meets the hosel's back edge, mm. */
const SIDE_NECK_RISE_MM = 10;

/** A blade's neck in the top or heel view, joined to the head as the face view's top line runs into
 * its hosel: one fillet tangent to the head's own edge (`join`, along `tangent`) and to the hosel.
 * Top: the neck leaves the blade's back edge (the shell's band outside the projected top line)
 * toe-ward of the face's top-heel corner and sweeps into the ferrule. Heel view: the hosel's back
 * edge comes down into the top of the top line's rounded cap, and the neck fills the crotch between
 * the hosel and the lofted face line. Svg mm before the hand's mirror. */
export function bladeNeck(category: FaceCategory, view: 'top' | 'side'): { d: string; join: Point; tangent: Point } {
  const p = CLUB_PROFILES[category];
  const paths = clubProfilePaths(category);
  const { shaftX, shaftY, ferruleBase, ferruleRadius } = hoselLayout(category, view);
  const [left, right] = [shaftX - ferruleRadius, shaftX + ferruleRadius];
  if (view === 'top') {
    const heelCorner = paths.top
      .match(/-?\d+(?:\.\d+)?/g)!
      .slice(0, 2)
      .map(Number) as Point;
    // the shell's back edge (its least x) where it crosses y = the join's station, and its direction there
    const at = heelCorner[1] - TOP_NECK_JOIN_MM;
    const shell = flatten(paths.top);
    let best: { x: number; along: Point } | null = null;
    shell.forEach((a, k) => {
      const b = shell[(k + 1) % shell.length];
      if ((a[1] - at) * (b[1] - at) > 0 || a[1] === b[1]) return;
      const x = a[0] + ((at - a[1]) / (b[1] - a[1])) * (b[0] - a[0]);
      if (!best || x < best.x)
        best = { x, along: unit(b[1] > a[1] ? [b[0] - a[0], b[1] - a[1]] : [a[0] - b[0], a[1] - b[1]]) };
    });
    const { x, along } = best!;
    const outward: Point = [-along[1], along[0]];
    const join: Point = [x + outward[0] * BLADE_EDGE_BAND_MM, at + outward[1] * BLADE_EDGE_BAND_MM];
    // straight on along the back edge past the corner's band, then the fillet into the ferrule
    const past = (heelCorner[1] + BLADE_EDGE_BAND_MM - join[1]) / along[1];
    const on: Point = [join[0] + along[0] * past, join[1] + along[1] * past];
    const reach = ferruleBase - on[1];
    const d = [
      `M ${point(...join)}`,
      `L ${point(...on)}`,
      `C ${point(on[0] + along[0] * reach * 0.4, on[1] + along[1] * reach * 0.4)} ${point(left, ferruleBase - reach * 0.45)} ${point(left, ferruleBase)}`,
      `L ${point(right, ferruleBase)}`,
      `C ${point(right, ferruleBase - 12)} ${point(shaftX + 4, heelCorner[1] + 2)} ${point(shaftX + 4, heelCorner[1] - 9)}`,
      `L ${point(heelCorner[0] - 5, heelCorner[1] - 9)} Z`,
    ].join(' ');
    return { d, join, tangent: along };
  }
  const { tx, ty, bx, by } = paths;
  // the old back edge from the heel's foot up to the ferrule, kept up to where it crosses the face line
  const back: Point[] = [
    [bx - 13, by - 3],
    [shaftX - 5, by - 8],
    [left, shaftY + 8],
    [left, ferruleBase],
  ];
  const side = (q: Point) => (q[0] - tx) * (by - ty) - (q[1] - ty) * (bx - tx);
  let [lo, hi] = [0, 1];
  for (let k = 0; k < 60; k += 1) {
    const mid = (lo + hi) / 2;
    if (Math.sign(side(cubicAt(back, mid))) === Math.sign(side(back[0]))) lo = mid;
    else hi = mid;
  }
  const [, c1, c2, crossing] = cubicHead(back, (lo + hi) / 2);
  const join: Point = [tx - p.topline / 2, ty - 1];
  const rise = join[1] - SIDE_NECK_RISE_MM;
  const run = left - join[0];
  const d = [
    `M ${point(...back[0])}`,
    `C ${point(...c1)} ${point(...c2)} ${point(...crossing)}`,
    `L ${point(tx, ty)}`,
    `Q ${point(tx - p.topline / 4, ty - 1)} ${point(...join)}`,
    `C ${point(join[0] + run * 0.55, join[1])} ${point(left, rise + SIDE_NECK_RISE_MM * 0.55)} ${point(left, rise)}`,
    `L ${point(left, ferruleBase)}`,
    `L ${point(right, ferruleBase)}`,
    `C ${point(right, shaftY + 18)} ${point(shaftX + 5, by - 1)} ${point(bx - 4, by)} Z`,
  ].join(' ');
  return { d, join, tangent: [1, 0] };
}
