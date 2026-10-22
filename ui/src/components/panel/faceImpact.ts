import type { Shot, ShotMetric } from '../../types/shot';
import { isMeasured } from '../../utils/units';

export type FaceCategory = 'driver' | 'fairway' | 'hybrid' | 'iron' | 'wedge';

type Point = readonly [number, number];

export interface Hosel {
  /** A point on the hosel axis at the leading-edge height. */
  ground: Point;
  lieDeg: number;
  /** Tube width at the ground and at the ferrule. */
  width: readonly [number, number];
  /** Distances along the axis from the ground point. */
  from: number;
  ferruleAt: number;
  ferruleLength: number;
  shaftTo: number;
  shaftWidth: number;
}

export interface Groove {
  y: number;
  from: number;
  to: number;
}

/** Our own illustrative drawings, toe left and hosel right as seen from the target
 * (right-handed), svg mm (+x heel, +y down) around the centre of the scoring area: midway between
 * the score lines' heel and toe ends, and midway up the face there. Iron length within Mizuno's
 * published 7-iron head lengths (74-87 mm), woods within the USGA 127 x 71 mm head limit. */
export interface FaceShape {
  head: string;
  face: string;
  edges: readonly string[];
  hosel: Hosel;
  grooves: readonly Groove[];
  mark: string | null;
  bounds: { minX: number; maxX: number; minY: number; maxY: number };
}

const fmt = (value: number) => String(Math.round(value * 100) / 100);
const pt = ([x, y]: Point) => `${fmt(x)} ${fmt(y)}`;
const add = (a: Point, b: Point, k = 1): Point => [a[0] + k * b[0], a[1] + k * b[1]];
const lerp = (a: Point, b: Point, k: number): Point => [a[0] + k * (b[0] - a[0]), a[1] + k * (b[1] - a[1])];

function axes(hosel: Hosel): { up: Point; right: Point } {
  const lie = (hosel.lieDeg * Math.PI) / 180;
  return { up: [Math.cos(lie), -Math.sin(lie)], right: [Math.sin(lie), Math.cos(lie)] };
}

/** A point on the hosel's left (-1) or right (+1) line `s` mm up the axis, pushed `out` further. */
function edge(hosel: Hosel, s: number, side: -1 | 1, out = 0): Point {
  const { up, right } = axes(hosel);
  const k = Math.min(1, s / hosel.ferruleAt);
  const half = (hosel.width[0] + k * (hosel.width[1] - hosel.width[0])) / 2;
  return add(add(hosel.ground, up, s), right, side * (half + out));
}

function quad(points: Point[]): string {
  return `M ${points.map(pt).join(' L ')} Z`;
}

export function hoselParts(hosel: Hosel): {
  tube: string;
  ferrule: string;
  shaft: string;
  shaftLine: [Point, Point];
} {
  const { up, right } = axes(hosel);
  const at = (s: number, width: number, side: -1 | 1) => add(add(hosel.ground, up, s), right, (side * width) / 2);
  const top = hosel.ferruleAt;
  const collar = top + hosel.ferruleLength;
  return {
    tube: quad([edge(hosel, hosel.from, -1), edge(hosel, top, -1), edge(hosel, top, 1), edge(hosel, hosel.from, 1)]),
    ferrule: quad([
      at(top, hosel.width[1], -1),
      at(collar, hosel.shaftWidth + 0.6, -1),
      at(collar, hosel.shaftWidth + 0.6, 1),
      at(top, hosel.width[1], 1),
    ]),
    shaft: quad([
      at(collar - 0.5, hosel.shaftWidth, -1),
      at(hosel.shaftTo, hosel.shaftWidth * 0.94, -1),
      at(hosel.shaftTo, hosel.shaftWidth * 0.94, 1),
      at(collar - 0.5, hosel.shaftWidth, 1),
    ]),
    shaftLine: [add(hosel.ground, up, collar), add(hosel.ground, up, hosel.shaftTo)],
  };
}

/** Rounds the corner `at` between the run from `a` and the run to `b`, `r` mm back on each. */
function fillet(a: Point, at: Point, b: Point, r: number): { start: Point; end: Point; path: string } {
  const toward = (from: Point) => lerp(at, from, r / Math.hypot(from[0] - at[0], from[1] - at[1]));
  const start = toward(a);
  const end = toward(b);
  return { start, end, path: `C ${pt(lerp(start, at, 0.55))} ${pt(lerp(end, at, 0.55))} ${pt(end)}` };
}

interface Blade {
  le: number;
  band: number;
  toe: number;
  top: number;
  topX: number;
  /** How far heel-ward the toe's top arc runs and how far it drops there. */
  round: number;
  drop: number;
  /** Where the topline meets the hosel, mm up its axis. */
  crotch: number;
  /** The face's heel end at the leading edge. */
  heelX: number;
  hosel: Hosel;
}

/** Iron and wedge: straight leading edge, rounded toe, topline falling to a hosel at lie. */
function blade(o: Blade, grooves: Groove[], bounds: FaceShape['bounds']): FaceShape {
  const { up } = axes(o.hosel);
  const top = (inset: number, concave: boolean) => {
    const corner = add(edge(o.hosel, o.crotch, -1, inset), [0, inset]);
    const along: Point = [o.topX + o.round, o.top + o.drop + inset];
    const round = fillet(edge(o.hosel, o.crotch + (concave ? 10 : -10), -1, inset), corner, along, concave ? 3 : 5);
    const line =
      `L ${pt(along)} C ${pt([o.topX + o.round * 0.45, o.top + inset])} ` +
      `${pt([o.topX + o.round * 0.2, o.top + inset])} ${pt([o.topX, o.top + inset])} ` +
      `C ${pt([o.topX - o.round * 0.6, o.top + inset])} ${pt([o.toe + inset, o.top + o.round * 0.3 + inset * 0.5])} ` +
      `${pt([o.toe + inset, o.top + o.round])}`;
    return { start: round.start, end: round.end, path: `${round.path} ${line}`, line };
  };
  const toeDown = (inset: number, bottom: number) =>
    `C ${pt([o.toe - 0.8 + inset, o.top + o.round + 12])} ${pt([o.toe + inset, o.le - 12])} ${pt([o.toe + 3 + inset, o.le - 3])} ` +
    `C ${pt([o.toe + 5 + inset, bottom - 0.5])} ${pt([o.toe + 12, bottom])} ${pt([o.toe + 21, bottom])} Z`;
  const outer = top(0, true);
  const inner = top(1.4, false);
  const heel = edge(o.hosel, 10, 1);
  const sole = o.le + o.band;
  const head =
    `M ${pt([o.toe + 21, sole])} L ${pt([heel[0] - 10, sole])} ` +
    `C ${pt([heel[0] - 3, sole])} ${pt(add(heel, up, -5))} ${pt(heel)} ` +
    `L ${pt(edge(o.hosel, 16, 1))} L ${pt(outer.start)} ${outer.path} ${toeDown(0, sole)}`;
  const face =
    `M ${pt([o.toe + 21, o.le])} L ${pt([o.heelX, o.le])} ` +
    `C ${pt([o.heelX + 4.5, o.le])} ${pt([o.heelX + 6.5, o.le - 5])} ${pt([o.heelX + 6.5, o.le - 13])} ` +
    `C ${pt([o.heelX + 6.5, o.le - 19])} ${pt(add(inner.start, up, -4))} ${pt(inner.start)} ` +
    `${inner.path} ${toeDown(1.4, o.le)}`;
  const topline = top(0.7, false);
  return {
    head,
    face,
    edges: [`M ${pt(topline.end)} ${topline.line}`],
    hosel: o.hosel,
    grooves,
    mark: null,
    bounds,
  };
}

function rows(centre: number, step: number, count: number, from: number, to: number): Groove[] {
  const first = centre + ((count - 1) * step) / 2;
  return Array.from({ length: count }, (_, k) => ({ y: Number((first - k * step).toFixed(2)), from, to }));
}

function sides(centre: number, step: number, count: number, toe: Point, heel: Point): Groove[] {
  return [...rows(centre, step, count, toe[0], toe[1]), ...rows(centre, step, count, heel[0], heel[1])];
}

type Edge = (s: number, side: -1 | 1, along?: number) => string;

function movePath(d: string, dx: number, dy: number): string {
  let k = 0;
  return d.replace(/-?\d*\.?\d+/g, (n) => fmt(Number(n) + (k++ % 2 ? dy : dx)));
}

/** Moves a drawing by (dx, dy) so its scoring-area centre lands on the origin. */
function centred(shape: FaceShape, [dx, dy]: Point): FaceShape {
  const { minX, maxX, minY, maxY } = shape.bounds;
  return {
    head: movePath(shape.head, dx, dy),
    face: movePath(shape.face, dx, dy),
    edges: shape.edges.map((d) => movePath(d, dx, dy)),
    hosel: { ...shape.hosel, ground: add(shape.hosel.ground, [dx, dy]) },
    grooves: shape.grooves.map((g) => ({ y: g.y + dy, from: g.from + dx, to: g.to + dx })),
    mark: shape.mark && movePath(shape.mark, dx, dy),
    bounds: { minX: minX + dx, maxX: maxX + dx, minY: minY + dy, maxY: maxY + dy },
  };
}

/** Woods: `head` draws the silhouette with the hosel's edge points to hand. */
function wood(
  face: string,
  hosel: Hosel,
  head: (at: Edge) => string,
  extras: Pick<FaceShape, 'grooves' | 'mark' | 'edges' | 'bounds'>
): FaceShape {
  const { up } = axes(hosel);
  const at: Edge = (s, side, along = 0) => pt(add(edge(hosel, s, side), up, along));
  return { face, head: head(at), hosel, ...extras };
}

const IRON_HOSEL: Hosel = {
  ground: [37, 23],
  lieDeg: 62,
  width: [14, 12.2],
  from: 8,
  ferruleAt: 64,
  ferruleLength: 8,
  shaftTo: 110,
  shaftWidth: 9,
};

const WEDGE_HOSEL: Hosel = { ...IRON_HOSEL, ground: [36, 24], lieDeg: 64, ferruleAt: 68 };

const DRIVER_HOSEL: Hosel = {
  ground: [37.7, 30],
  lieDeg: 58,
  width: [13.6, 12.4],
  from: 30,
  ferruleAt: 76,
  ferruleLength: 8,
  shaftTo: 120,
  shaftWidth: 8.6,
};

const FAIRWAY_HOSEL: Hosel = { ...DRIVER_HOSEL, ground: [36.5, 17], lieDeg: 58.5, from: 24, ferruleAt: 56 };

const HYBRID_HOSEL: Hosel = {
  ...DRIVER_HOSEL,
  ground: [30.5, 20],
  lieDeg: 60,
  width: [14, 12.4],
  from: 26,
  ferruleAt: 60,
};

export const FACE_SHAPES: Record<FaceCategory, FaceShape> = {
  driver: centred(
    wood(
      'M 0 30 C 22 30 42 29 53 23 C 60 19 62 8 61 -2 C 60 -10 55 -16 46 -19 C 32 -25 12 -28.5 -10 -28.5 ' +
        'C -30 -28.5 -44 -23 -47.5 -10 C -50 0 -49 14 -42 22 C -34 28.5 -18 30 0 30 Z',
      DRIVER_HOSEL,
      (at) =>
        `M 0 33.5 C 24 33.5 46 32.5 57 25 C 63 21 65.5 12 65.3 4 C 65.2 1 ${at(44, 1, -2)} ${at(44, 1)} ` +
        `L ${at(48, 1)} L ${at(56, -1)} C ${at(56, -1, -4)} 44 -26 28 -29.5 C 8 -33 -20 -33.5 -34 -29.5 ` +
        'C -47 -26 -52.5 -16 -52.5 -6 C -53 6 -50 18 -44 25 C -34.5 32.5 -18 33.5 0 33.5 Z',
      {
        grooves: sides(1.06, 5, 6, [-38, -24], [36.5, 50.5]),
        mark: 'M 6.25 -25 L 6.25 -21',
        edges: ['M 30 -28 C 18 -30.9 4 -32 -12 -32 C -30 -31.8 -43 -26.5 -48.5 -14'],
        bounds: { minX: -58, maxX: 92, minY: -48, maxY: 38 },
      }
    ),
    [-6.25, -1.06]
  ),
  fairway: centred(
    wood(
      'M 0 17 C 20 17 38 16.5 47 12 C 52 9 53 2 52 -3 C 51 -8 46 -11 39 -12.5 C 26 -16 8 -21 -10 -21 ' +
        'C -26 -21 -37 -18 -40.5 -10 C -43 -3 -42 7 -37 12 C -30 16.5 -16 17 0 17 Z',
      FAIRWAY_HOSEL,
      (at) =>
        `M 0 19.5 C 22 19.5 42 19 51 14 C 56 10.5 57.5 4 57 -1 C 56.8 -3 ${at(31, 1, -3)} ${at(31, 1)} ` +
        `L ${at(35, 1)} L ${at(42, -1)} C ${at(42, -1, -4)} 36 -21.5 20 -23.5 C 6 -24.8 -18 -24.5 -28 -22 ` +
        'C -40 -19.5 -44.5 -12 -44.5 -4 C -45 6 -42 12 -38 15 C -31 19.5 -16 19.5 0 19.5 Z',
      {
        grooves: sides(-1.47, 3.6, 6, [-33, -22], [32.5, 43.5]),
        mark: 'M 5.25 -17.5 L 5.25 -14',
        edges: ['M 22 -22.4 C 12 -23.6 0 -23.8 -12 -23.6 C -28 -23.2 -39 -19 -42.5 -10'],
        bounds: { minX: -50, maxX: 80, minY: -45, maxY: 24 },
      }
    ),
    [-5.25, 1.47]
  ),
  hybrid: centred(
    wood(
      'M 4 20 C 22 20 37 19.5 44 15 C 49 11 50 3 49 -4 C 48 -10 43 -13 36 -14.5 C 24 -18 8 -22.5 -8 -23 ' +
        'C -22 -23 -31 -19 -34 -10 C -36.5 -2 -35.5 8 -31 13.5 C -25 19.5 -11 20 4 20 Z',
      HYBRID_HOSEL,
      (at) =>
        `M 4 22.5 C 24 22.5 40 22 48 17 C 53.5 13 55 5 54.5 -1 C 54.3 -3 ${at(34, 1, -3)} ${at(34, 1)} ` +
        `L ${at(38, 1)} L ${at(46, -1)} C ${at(46, -1, -4)} 26 -21 10 -25 C -2 -26.5 -20 -26 -28 -22.5 ` +
        'C -36 -19 -38.5 -11 -38.5 -3 C -38.5 6 -36 13 -32 17 C -25.5 22.5 -11 22.5 4 22.5 Z',
      {
        grooves: rows(-0.75, 4.6, 6, -19, 33),
        mark: null,
        edges: ['M 28 -21.5 C 18 -23.8 6 -25.4 -8 -25.4 C -22 -25.2 -31 -21 -35 -13'],
        bounds: { minX: -44, maxX: 76, minY: -46, maxY: 27 },
      }
    ),
    [-7, 0.75]
  ),
  iron: centred(
    blade(
      {
        le: 23,
        band: 3,
        toe: -34,
        top: -27.3,
        topX: -18,
        round: 14,
        drop: 1.6,
        crotch: 33,
        heelX: 37,
        hosel: IRON_HOSEL,
      },
      rows(-0.25, 2.9, 10, -29.5, 26.5),
      { minX: -40, maxX: 84, minY: -50, maxY: 29 }
    ),
    [1.5, 0.25]
  ),
  wedge: centred(
    blade(
      {
        le: 24,
        band: 5,
        toe: -33,
        top: -34.2,
        topX: -14,
        round: 24,
        drop: 5,
        crotch: 40,
        heelX: 36,
        hosel: WEDGE_HOSEL,
      },
      rows(-3.71, 2.9, 12, -29, 26),
      { minX: -40, maxX: 82, minY: -54, maxY: 32 }
    ),
    [1.5, 3.71]
  ),
};

export function faceCategory(club: string | null | undefined): FaceCategory {
  const name = (club ?? '').trim().toLowerCase();
  if (name === 'driver') return 'driver';
  if (name.endsWith('-wood')) return 'fairway';
  if (name.endsWith('-hybrid')) return 'hybrid';
  if (['pw', 'gw', 'sw', 'lw'].includes(name)) return 'wedge';
  return 'iron';
}

export const GRID_STEP_MM = 10;

export interface ImpactReading {
  offset: number | null;
  offsetSigma: number | null;
  offsetReason: string | null;
  height: number | null;
  heightSigma: number | null;
  heightReason: string | null;
  handedness: 'right' | 'left';
}

function axis(record: ShotMetric | undefined): [number | null, number | null, string | null] {
  if (!record || !isMeasured(record.value) || record.status === 'not_measured') {
    return [null, null, record?.reason || 'not in this shot’s payload'];
  }
  const sigma = record.range ? Math.abs(record.range[1] - record.range[0]) / 2 : null;
  return [record.value, sigma, null];
}

export function impactReading(shot: Shot): ImpactReading {
  const [offset, offsetSigma, offsetReason] = axis(shot.metrics?.impact_offset ?? undefined);
  const [height, heightSigma, heightReason] = axis(shot.metrics?.impact_height ?? undefined);
  return {
    offset,
    offsetSigma,
    offsetReason,
    height,
    heightSigma,
    heightReason,
    handedness: shot.impact_location?.handedness === 'left' ? 'left' : 'right',
  };
}

export function formatLength(mm: number): string {
  return `${Math.round(mm)} mm`;
}
