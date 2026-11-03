import { FACE_SHAPES, faceCategory, flatten, type DeliveryShot, type FaceCategory } from './faceImpact';

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
    depth: 87.1,
    height: 36.9,
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
    depth: 67.0,
    height: 34.3,
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

/** Static loft and lie (deg) of each club type the kiosk can select, population averages from the
 * official spec tables in research/club-models (docs/camera/club-delivery-views.md lists the
 * source of every row). Irons and PW/GW: the 7-iron category value plus the mean per-club offset
 * from each model's own 7-iron in 13 saved OEM set tables (Cobra, Mizuno, Srixon, Wilson). SW and
 * LW: the official wedge tables' 56 deg and 58/60 deg rows. Driver, 3W, 3H: the category values
 * (themselves those clubs). 2-iron (utility/driving irons), 5W, 7W, 5H, 7H: the mean over official
 * OEM spec pages and sell sheets of 5 to 14 2022+ models; 9H: the 7H mean plus two per-number
 * steps (only one model lists a 9H). Only an unknown club name takes its category's. The drawings keep the category's shapes: only the side view's lean and the face
 * view's tilt use these, and a shot's dynamic loft or lie still wins. */
export const CLUB_STATIC: Readonly<Record<string, { loft: number; lie: number }>> = {
  '2-iron': { loft: 17.6, lie: 59.7 },
  '3-iron': { loft: 18.5, lie: 60.1 },
  '4-iron': { loft: 21.7, lie: 60.7 },
  '5-iron': { loft: 24.6, lie: 61.3 },
  '6-iron': { loft: 27.8, lie: 61.8 },
  '7-iron': { loft: 31.4, lie: 62.3 },
  '8-iron': { loft: 35.5, lie: 62.9 },
  '9-iron': { loft: 39.8, lie: 63.4 },
  pw: { loft: 44.4, lie: 63.9 },
  gw: { loft: 49.5, lie: 64.2 },
  sw: { loft: 56.0, lie: 64.0 },
  lw: { loft: 59.0, lie: 64.0 },
  driver: { loft: 10.4, lie: 57.4 },
  '3-wood': { loft: 14.9, lie: 56.6 },
  '5-wood': { loft: 18.1, lie: 57.5 },
  '7-wood': { loft: 21.0, lie: 58.1 },
  '3-hybrid': { loft: 18.8, lie: 57.7 },
  '5-hybrid': { loft: 25.2, lie: 59.4 },
  '7-hybrid': { loft: 32.2, lie: 60.6 },
  '9-hybrid': { loft: 39.1, lie: 61.6 },
};

/** The selected club's static loft and lie, else its category's (`source` says which). */
export function staticLoftLie(
  club: string | null | undefined,
  category: FaceCategory = faceCategory(club)
): { loft: number; lie: number; source: 'club' | 'category' } {
  const own = CLUB_STATIC[(club ?? '').trim().toLowerCase()];
  if (own) return { ...own, source: 'club' };
  return { loft: CLUB_PROFILES[category].loft, lie: CLUB_PROFILES[category].lie, source: 'category' };
}

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

/** The face view's tilt: none without a measured dynamic lie (at address every club's face view
 * sits the same way); with one, its difference from the selected club's own static lie, both seen
 * along the face normal (the static at the club's static loft, the dynamic at the shot's dynamic
 * loft, else the club's). The club's static loft and lie are only the reference, never a tilt. */
export function faceDeliveryRotation(shot: DeliveryShot, category: FaceCategory): number {
  const lie = deliveryValue(shot, 'dynamic_lie');
  if (lie === null) return 0;
  const club = staticLoftLie(shot.club, category);
  const radians = Math.PI / 180;
  const projected = (lieDeg: number, loftDeg: number) =>
    Math.atan2(Math.sin(lieDeg * radians) * Math.cos(loftDeg * radians), Math.cos(lieDeg * radians)) / radians;
  const loft = deliveryValue(shot, 'dynamic_loft') ?? club.loft;
  return projected(club.lie, club.loft) - projected(lie, loft);
}

const n = (value: number) => Number(value.toFixed(3));
const point = (x: number, y: number) => `${n(x)} ${n(y)}`;

// The source paths use absolute M/L/C pairs; an affine projection preserves their fair curves.
function bladePlan(path: string, scale: number, offset = 0): string {
  return path.replace(/(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)/g, (_, x: string, y: string) =>
    point(Number(y) * scale + offset, Number(x))
  );
}

/** The top and heel views' outlines of a category's head, built at `loft` (default the category's):
 * the face line at that loft from vertical about the face centre, the sole, top line or crown and
 * depth following it. A club's own static loft gives its own profile (sole on the ground, face at its
 * loft); nothing is rotated to stand in for a loft. */
export function clubProfilePaths(category: FaceCategory, loft: number = CLUB_PROFILES[category].loft) {
  const p = CLUB_PROFILES[category];
  const wood = p.depth > 0;
  const toe = p.toe;
  const heel = p.length - toe;
  const radians = (loft * Math.PI) / 180;
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
  // the heel view's back: the head's depth measured from the leading edge, as the plan measures it
  const woodBack = depth - bx;
  const soleY = by + (wood ? 4 : p.sole * Math.tan((p.bounce * Math.PI) / 180));
  const side = wood
    ? `M ${point(tx, ty)} C ${point(tx - 5, crown)} ${point(-woodBack * 0.2, crown)} ${point(-woodBack * 0.4, crown + 3)} C ${point(-woodBack * 0.7, crown + 7)} ${point(-woodBack, 0)} ${point(-woodBack, by - 5)} C ${point(-woodBack, soleY)} ${point(-woodBack * 0.3, soleY)} ${point(bx - 4, by + 2)} Q ${point(bx + 1, by + 1)} ${point(bx, by - 2)} L ${point(tx, ty)} Z`
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
export function hoselLayout(category: FaceCategory, view: 'top' | 'side', loft?: number) {
  const p = CLUB_PROFILES[category];
  const paths = clubProfilePaths(category, loft);
  const top = view === 'top';
  // the progression is the leading edge's lead on the hosel axis: from the leading edge in the heel
  // view (the face centre is the origin there), from the front edge in the plan (its origin)
  const shaftX = !p.depth || !top ? paths.bx - p.progression : -p.progression;
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

/** The top view's neck edges, back (top-line side) and front (leading-edge side): [mm toe-ward of
 * the face's top-heel corner where the edge leaves the blade's band, its handle there, its handle at
 * the ferrule (both as fractions of the edge's chord)]. One cubic each, tangent to the band and to
 * the ferrule; chosen for the smallest peak curvature that keeps the blade's band inside the neck
 * (iron about 12 mm and wedge 8 mm at the tightest on the back edge, nearly straight on the front). */
const TOP_NECK = {
  iron: { back: [5, 0.45, 0.45], front: [16, 0.4, 0.35] },
  wedge: { back: [14, 0.7, 0.3], front: [16, 0.45, 0.2] },
} as const;

/** A blade's neck in the top or heel view, and where its edge meets the head (`join`, running along
 * `tangent`). Top: both neck edges leave the blade's band (outside the projected face) tangent to it,
 * toe-ward of the face's top-heel corner, and sweep into the ferrule in one smooth curve each. Heel
 * view: the hosel stands in front of the lofted face and meets the head only at the neck, low on the
 * blade where its back edge crosses the face line; above that the gap to the face is open, as on a
 * real club. Svg mm before the hand's mirror. */
export function bladeNeck(
  category: FaceCategory,
  view: 'top' | 'side',
  loft?: number
): { d: string; join: Point; tangent: Point; front?: { join: Point; tangent: Point } } {
  const paths = clubProfilePaths(category, loft);
  const { shaftX, shaftY, ferruleBase, ferruleRadius } = hoselLayout(category, view, loft);
  const [left, right] = [shaftX - ferruleRadius, shaftX + ferruleRadius];
  if (view === 'top') {
    const heelCorner = paths.top
      .match(/-?\d+(?:\.\d+)?/g)!
      .slice(0, 2)
      .map(Number) as Point;
    const lengths = TOP_NECK[category === 'wedge' ? 'wedge' : 'iron'];
    // The shell is the face outline stroked 4.8 mm wide, projected by (x, y) -> (k y + o, x): its
    // band is 2.4 mm about the face outline in face coordinates. Where an edge leaves it: the face's
    // top (back, -1) or bottom (front, +1) edge `station` mm toe-ward of the face's top-heel corner,
    // carried out by the band, projected, with the edge's direction there (heading heel-ward).
    const p = CLUB_PROFILES[category];
    const radians = ((loft ?? p.loft) * Math.PI) / 180;
    const back = p.topline * Math.cos(radians);
    const [k, o] = [Math.sin(radians) + back / p.faceHeight, -back / 2];
    const face = flatten(FACE_SHAPES[category].face);
    const leave = (side: -1 | 1, station: number): [Point, Point] => {
      const at = heelCorner[1] - station;
      let best: { y: number; along: Point } | null = null;
      face.forEach((a, i) => {
        const b = face[(i + 1) % face.length];
        if ((a[0] - at) * (b[0] - at) > 0 || a[0] === b[0]) return;
        const y = a[1] + ((at - a[0]) / (b[0] - a[0])) * (b[1] - a[1]);
        if (!best || side * y > side * best.y)
          best = { y, along: unit(b[0] > a[0] ? [b[0] - a[0], b[1] - a[1]] : [a[0] - b[0], a[1] - b[1]]) };
      });
      const { y, along } = best!;
      const outward: Point = side < 0 ? [along[1], -along[0]] : [-along[1], along[0]];
      const [fx, fy] = [at + outward[0] * BLADE_EDGE_BAND_MM, y + outward[1] * BLADE_EDGE_BAND_MM];
      return [[k * fy + o, fx], unit([k * along[1], along[0]])];
    };
    const [join, along] = leave(-1, lengths.back[0]);
    const [frontJoin, frontAlong] = leave(1, lengths.front[0]);
    const [backReach, frontReach] = [
      Math.hypot(left - join[0], ferruleBase - join[1]),
      Math.hypot(right - frontJoin[0], ferruleBase - frontJoin[1]),
    ];
    const d = [
      `M ${point(...join)}`,
      `C ${point(join[0] + along[0] * backReach * lengths.back[1], join[1] + along[1] * backReach * lengths.back[1])} ${point(left, ferruleBase - backReach * lengths.back[2])} ${point(left, ferruleBase)}`,
      `L ${point(right, ferruleBase)}`,
      `C ${point(right, ferruleBase - frontReach * lengths.front[2])} ${point(frontJoin[0] + frontAlong[0] * frontReach * lengths.front[1], frontJoin[1] + frontAlong[1] * frontReach * lengths.front[1])} ${point(...frontJoin)}`,
      'Z',
    ].join(' ');
    return { d, join, tangent: along, front: { join: frontJoin, tangent: frontAlong } };
  }
  const { tx, ty, bx, by } = paths;
  // the hosel's back edge, from the heel's foot up to the ferrule; it meets the head where it crosses
  // the face line
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
  const t = (lo + hi) / 2;
  const [, , c2, crossing] = cubicHead(back, t);
  const d = [
    `M ${point(...back[0])}`,
    `C ${point(...back[1])} ${point(...back[2])} ${point(...back[3])}`,
    `L ${point(right, ferruleBase)}`,
    `C ${point(right, shaftY + 18)} ${point(shaftX + 5, by - 1)} ${point(bx - 4, by)} Z`,
  ].join(' ');
  return { d, join: crossing, tangent: unit([crossing[0] - c2[0], crossing[1] - c2[1]]) };
}
