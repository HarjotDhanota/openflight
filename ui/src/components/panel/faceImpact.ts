import type { Shot, ShotMetric } from '../../types/shot';
import { isMeasured } from '../../utils/units';

export type FaceCategory = 'driver' | 'fairway' | 'hybrid' | 'iron' | 'wedge';

type Point = readonly [number, number];

/** USGA/R&A Equipment Rules, Part 2 (Rules for Clubs), the limits a face-on drawing can show:
 * 2c shaft attachment, 4b(i) wood head dimensions, 5c(i) grooves, 5d decorative markings and
 * interpretation 5.b(1) impact area (usga.org/equipment-standards/equipment-rules-2019). */
export const USGA = {
  /** 5c(i): groove width W at most 0.035 in. */
  grooveMaxWidthMm: 0.9,
  /** 5c(i): edge-to-edge separation S at least 3 W and at least 0.075 in. */
  grooveSeparationPerWidth: 3,
  grooveMinSeparationMm: 1.905,
  /** 5.b(1): a wood's impact area is the central 1.68 in strip of the face; grooves encroaching
   * its heel or toe side by less than 0.25 in are not in it. */
  woodImpactStripMm: 42.67,
  impactAreaEncroachMm: 6.35,
  /** 5d: the centre of the impact area may be marked within a 0.375 in square. */
  centreMarkSquareMm: 9.53,
  /** 4b(i): wood heel to toe at most 5 in, sole to crown at most 2.8 in. */
  woodHeelToToeMaxMm: 127,
  woodSoleToCrownMaxMm: 71.12,
  /** 2c: shaft fixed at the heel, the top of the neck or socket at most 5 in from the sole. */
  neckToSoleMaxMm: 127,
} as const;

/** Drawn score-line width, inside the USGA groove limit. */
export const GROOVE_WIDTH_MM = 0.6;

export interface Hosel {
  /** Where the tube starts, inside the head, and its lie. */
  ground: Point;
  lieDeg: number;
  /** Tube width at the start and at the ferrule or sleeve collar. */
  width: readonly [number, number];
  /** Distances along the axis from `ground`. */
  from: number;
  ferruleAt: number;
  ferruleLength: number;
  shaftTo: number;
  shaftWidth: number;
  /** Adjustable-sleeve joints across the tube, mm up the axis, and a grey ring this long where
   * the sleeve meets the head. */
  rings: readonly number[];
  collar: number;
}

export interface Groove {
  y: number;
  from: number;
  to: number;
}

/** chrome and satin steel blades, black-headed woods. */
export type Finish = 'chrome' | 'satin' | 'black';

/** Our own illustrative drawings, toe left and hosel right as seen from the target
 * (right-handed), svg mm (+x heel, +y down) around the centre of the scoring area. Iron length
 * within Mizuno's published 7-iron head lengths (74-87 mm), woods within the USGA limits. */
export interface FaceShape {
  finish: Finish;
  head: string;
  face: string;
  /** The milled scoring area of an iron or wedge. */
  scoring: string | null;
  edges: readonly string[];
  hosel: Hosel;
  grooves: readonly Groove[];
  mark: string | null;
  /** Where the TOE and HEEL labels sit on the heel-toe centre line, just outside the club. */
  ends: readonly [number, number];
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
  rings: string[];
  collar: string | null;
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
    rings: hosel.rings.map((s) => `M ${pt(edge(hosel, s, -1))} L ${pt(edge(hosel, s, 1))}`),
    collar: hosel.collar
      ? quad([
          edge(hosel, hosel.from, -1, 0.3),
          edge(hosel, hosel.from + hosel.collar, -1, 0.3),
          edge(hosel, hosel.from + hosel.collar, 1, 0.3),
          edge(hosel, hosel.from, 1, 0.3),
        ])
      : null,
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

/** A smooth closed curve through `points` (Catmull-Rom as cubic Beziers). */
function smooth(points: Point[]): string {
  const n = points.length;
  const p = (k: number) => points[(k + n) % n];
  const segments = points.map((_, k) => {
    const c1 = add(p(k), add(p(k + 1), p(k - 1), -1), 1 / 6);
    const c2 = add(p(k + 1), add(p(k + 2), p(k), -1), -1 / 6);
    return `C ${pt(c1)} ${pt(c2)} ${pt(p(k + 1))}`;
  });
  return `M ${pt(points[0])} ${segments.join(' ')} Z`;
}

/** Flattens an absolute M/L/C/Z path into a polygon. */
function flatten(d: string): Point[] {
  const tokens = d.match(/[MLCZ]|-?\d*\.?\d+/g) ?? [];
  const points: Point[] = [];
  let command = '';
  for (let i = 0; i < tokens.length;) {
    if (/[MLCZ]/.test(tokens[i])) command = tokens[i++];
    if (command === 'Z') continue;
    const take = (): Point => [Number(tokens[i++]), Number(tokens[i++])];
    if (command === 'C') {
      const [p0, p1, p2, p3] = [points[points.length - 1], take(), take(), take()];
      for (let k = 1; k <= 12; k += 1) {
        const t = k / 12;
        const [a, b, c, e] = [(1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3];
        points.push([a * p0[0] + b * p1[0] + c * p2[0] + e * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + e * p3[1]]);
      }
    } else points.push(take());
  }
  return points;
}

/** Where the polygon crosses the line x = `at` (vertical) or y = `at` (horizontal), sorted. */
function cross(polygon: Point[], at: number, vertical: boolean): number[] {
  const [u, v] = vertical ? [0, 1] : [1, 0];
  const hits: number[] = [];
  polygon.forEach((a, k) => {
    const b = polygon[(k + 1) % polygon.length];
    if ((a[u] - at) * (b[u] - at) <= 0 && a[u] !== b[u])
      hits.push(a[v] + ((at - a[u]) / (b[u] - a[u])) * (b[v] - a[v]));
  });
  return hits.sort((p, q) => p - q);
}

const UNFRAMED: FaceShape['bounds'] = { minX: 0, maxX: 0, minY: 0, maxY: 0 };

function movePath(d: string, dx: number, dy: number): string {
  let k = 0;
  return d.replace(/-?\d*\.?\d+/g, (n) => fmt(Number(n) + (k++ % 2 ? dy : dx)));
}

/** Moves a drawing by (dx, dy) so its scoring-area centre lands on the origin. */
function centred(shape: FaceShape, [dx, dy]: Point): FaceShape {
  const { minX, maxX, minY, maxY } = shape.bounds;
  return {
    ...shape,
    head: movePath(shape.head, dx, dy),
    face: movePath(shape.face, dx, dy),
    scoring: shape.scoring && movePath(shape.scoring, dx, dy),
    edges: shape.edges.map((d) => movePath(d, dx, dy)),
    hosel: { ...shape.hosel, ground: add(shape.hosel.ground, [dx, dy]) },
    grooves: shape.grooves.map((g) => ({
      y: Number(fmt(g.y + dy)),
      from: Number(fmt(g.from + dx)),
      to: Number(fmt(g.to + dx)),
    })),
    mark: shape.mark && movePath(shape.mark, dx, dy),
    ends: [shape.ends[0] + dx, shape.ends[1] + dx],
    bounds: { minX: minX + dx, maxX: maxX + dx, minY: minY + dy, maxY: maxY + dy },
  };
}

interface Blade {
  finish: Finish;
  /** Leading edge height and how much it bows down mid-blade. */
  le: number;
  bow: number;
  band: number;
  /** The toe's leftmost point. */
  toe: Point;
  /** A point the topline passes near the toe; it falls from there to the hosel, and rounds
   * over the toe from `arc` mm heel-ward of it. */
  peak: Point;
  arc: number;
  /** Where the topline meets the hosel, mm up its axis. */
  crotch: number;
  /** The face's heel side, and the radius the topline sweeps up into the hosel with. */
  side: number;
  sweep: number;
  /** The radius the face's heel side rounds into the topline with. */
  shoulder: number;
  hosel: Hosel;
}

/** Score lines at a constant pitch from `lowest` mm above the leading edge, all starting at
 * `from`; each runs to `to` or stops `clearance` mm under the topline, and the lines go on up the
 * face while the topline is `clearance` above them at `from`. The milled area runs `margin` mm
 * past the lines' toe and heel ends. */
interface GrooveBlock {
  lowest: number;
  pitch: number;
  from: number;
  to: number;
  clearance: number;
  margin: number;
}

/** Iron and wedge: a toe rounding down from a topline that falls to the hosel, a leading edge
 * that bows slightly, a milled scoring area; drawn around the centre of its score lines. */
function blade(o: Blade, block: GrooveBlock): FaceShape {
  const { up } = axes(o.hosel);
  const corner = edge(o.hosel, o.crotch, -1);
  const slope = (corner[1] - o.peak[1]) / (corner[0] - o.peak[0]);
  const lineY = (x: number, inset: number) => o.peak[1] + (x - o.peak[0]) * slope + inset;
  const norm = Math.hypot(1, slope);
  const toe = (inset: number, bottom: number) => {
    const a: Point = [o.peak[0] + o.arc, lineY(o.peak[0] + o.arc, inset)];
    const left: Point = [o.toe[0] + inset, o.toe[1]];
    const foot: Point = [o.toe[0] + 17, bottom];
    const reach = (o.toe[1] - o.peak[1]) * 0.62;
    return {
      a,
      path:
        `L ${pt(a)} C ${pt(add(a, [-1 / norm, -slope / norm], o.arc * 1.6))} ${pt([left[0], left[1] - reach])} ${pt(left)} ` +
        `C ${pt([left[0], left[1] + (bottom - left[1]) * 0.6])} ${pt([foot[0] - 9, bottom])} ${pt(foot)}`,
    };
  };
  const leadingEdge = (bottom: number, toX: number) => {
    const start: Point = [o.toe[0] + 17, bottom];
    return `C ${pt([start[0] + 20, bottom + o.bow])} ${pt([toX - 20, bottom + o.bow])} ${pt([toX, bottom - o.bow * 0.5])}`;
  };
  const sole = o.le + o.band;
  const heel = edge(o.hosel, 14, 1);
  const crown = fillet(edge(o.hosel, o.crotch + 30, -1), corner, [o.peak[0], lineY(o.peak[0], 0)], o.sweep);
  const outer = toe(0, sole);
  const head =
    `M ${pt([o.toe[0] + 17, sole])} ${leadingEdge(sole, heel[0] - 14)} ` +
    `C ${pt([heel[0] - 6, sole - o.bow * 0.5])} ${pt(add(heel, up, -9))} ${pt(heel)} ` +
    `L ${pt(edge(o.hosel, 20, 1))} L ${pt(crown.start)} ${crown.path} ${outer.path} Z`;
  const inset = 1.4;
  const inner = toe(inset, o.le);
  const lift = o.bow * 0.5;
  const shoulder = fillet([o.side, o.le - lift - 4], [o.side, lineY(o.side, inset)], inner.a, o.shoulder);
  const face =
    `M ${pt([o.toe[0] + 17, o.le])} ${leadingEdge(o.le, o.side - 3)} ` +
    `C ${pt([o.side - 1, o.le - lift])} ${pt([o.side, o.le - lift - 1.5])} ${pt([o.side, o.le - lift - 4])} ` +
    `L ${pt(shoulder.start)} ${shoulder.path} ${inner.path} Z`;
  const polygon = flatten(face);
  const top = (x: number) => cross(polygon, x, true)[0];
  const lines: Groove[] = [];
  const room = (y: number, x: number) => y - top(x) - block.clearance;
  for (let y = o.le - block.lowest; room(y, block.from) >= 0; y -= block.pitch) {
    let [low, high] = [block.from, block.to];
    if (room(y, high) < 0) {
      for (let k = 0; k < 40; k += 1) {
        const mid = (low + high) / 2;
        if (room(y, mid) >= 0) low = mid;
        else high = mid;
      }
    } else low = high;
    lines.push({ y: Number(y.toFixed(2)), from: block.from, to: Number(low.toFixed(2)) });
  }
  const [x0, x1] = [block.from - block.margin, block.to + block.margin];
  const floor = lines[0].y + block.lowest * 0.5;
  const steps = Math.ceil(x1 - x0);
  const ceiling = Array.from({ length: steps + 1 }, (_, k): Point => {
    const x = x1 - ((x1 - x0) * k) / steps;
    return [x, top(x) + 0.5];
  });
  const scoring = `M ${pt([x0, floor])} L ${pt([x1, floor])} L ${ceiling.map(pt).join(' L ')} Z`;
  const highlight = toe(0.7, o.le);
  const centreX = (block.from + block.to) / 2;
  const crossing = lines.filter((line) => line.to >= centreX);
  const centreY = (lines[0].y + crossing[crossing.length - 1].y) / 2;
  return centred(
    {
      finish: o.finish,
      head,
      face,
      scoring,
      edges: [`M ${pt([o.side - 4, lineY(o.side - 4, 0.7)])} ${highlight.path.split(' C ').slice(0, 2).join(' C ')}`],
      hosel: o.hosel,
      grooves: lines,
      mark: null,
      ends: [0, 0],
      bounds: UNFRAMED,
    },
    [-centreX, -centreY]
  );
}

function rows(centre: number, step: number, count: number, from: number, to: number): Groove[] {
  const first = centre + ((count - 1) * step) / 2;
  return Array.from({ length: count }, (_, k) => ({ y: Number((first - k * step).toFixed(2)), from, to }));
}

interface WoodLines {
  /** Heel and toe blocks: rows at a pitch, `margin` in from the face edge, inner ends `inner`
   * mm either side of the centre. */
  rows: number;
  pitch: number;
  margin: number;
  inner: number;
  /** Centre score lines: half-lengths at a pitch around the centre. */
  bars: readonly number[];
  barPitch: number;
}

/** Driver and fairway: a dark head framing a light face, heel and toe blocks of score lines and
 * a plain centre with short bars and a small cross, centred on the scoring area. */
function wood(facePoints: Point[], headPoints: Point[], hosel: Hosel, spec: WoodLines): FaceShape {
  const face = smooth(facePoints);
  const polygon = flatten(face);
  const middle = (x: number) => {
    const ys = cross(polygon, x, true);
    return (ys[0] + ys[ys.length - 1]) / 2;
  };
  let [cx, cy] = [0, middle(0)];
  let grooves: Groove[] = [];
  for (let pass = 0; pass < 3; pass += 1) {
    const ys = rows(cy, spec.pitch, spec.rows, 0, 0).map((line) => line.y);
    const toe = ys.map((y) => ({ y, from: cross(polygon, y, false)[0] + spec.margin, to: cx - spec.inner }));
    const heel = ys.map((y) => {
      const xs = cross(polygon, y, false);
      return { y, from: cx + spec.inner, to: xs[xs.length - 1] - spec.margin };
    });
    grooves = [...toe, ...heel];
    cx = (Math.min(...toe.map((line) => line.from)) + Math.max(...heel.map((line) => line.to))) / 2;
    cy = middle(cx);
  }
  const half = (spec.bars.length - 1) / 2;
  const bars = spec.bars.map((length, k) => ({
    y: cy + (k - half) * spec.barPitch,
    from: cx - length,
    to: cx + length,
  }));
  return centred(
    {
      finish: 'black',
      head: smooth(headPoints),
      face,
      scoring: null,
      edges: [],
      hosel,
      grooves: [...grooves, ...bars].map((g) => ({
        y: Number(fmt(g.y)),
        from: Number(fmt(g.from)),
        to: Number(fmt(g.to)),
      })),
      mark: `M ${pt([cx, cy - 1.8])} L ${pt([cx, cy + 1.8])}`,
      ends: [0, 0],
      bounds: UNFRAMED,
    },
    [-cx, -cy]
  );
}

/** Fairway: score lines across the face, each ending `toe` and `heel` mm in from the face
 * outline and at least `clearance` mm from it anywhere, so the lower lines shorten. */
interface FullLines {
  rows: number;
  pitch: number;
  toe: number;
  heel: number;
  clearance: number;
}

function distance(polygon: Point[], [x, y]: Point): number {
  return Math.min(
    ...polygon.map((a, k) => {
      const b = polygon[(k + 1) % polygon.length];
      const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
      const t = Math.max(0, Math.min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy || 1)));
      return Math.hypot(x - a[0] - t * dx, y - a[1] - t * dy);
    })
  );
}

function fullWood(facePoints: Point[], headPoints: Point[], hosel: Hosel, spec: FullLines): FaceShape {
  const face = smooth(facePoints);
  const polygon = flatten(face);
  const middle = (x: number) => {
    const ys = cross(polygon, x, true);
    return (ys[0] + ys[ys.length - 1]) / 2;
  };
  const line = (y: number): Groove => {
    const xs = cross(polygon, y, false);
    let [from, to] = [xs[0] + spec.toe, xs[xs.length - 1] - spec.heel];
    while (from < to && distance(polygon, [from, y]) < spec.clearance) from += 0.1;
    while (to > from && distance(polygon, [to, y]) < spec.clearance) to -= 0.1;
    return { y: Number(fmt(y)), from: Number(fmt(from)), to: Number(fmt(to)) };
  };
  let [cx, cy] = [0, middle(0)];
  let grooves: Groove[] = [];
  for (let pass = 0; pass < 3; pass += 1) {
    grooves = rows(cy, spec.pitch, spec.rows, 0, 0).map((row) => line(row.y));
    cx = (Math.min(...grooves.map((g) => g.from)) + Math.max(...grooves.map((g) => g.to))) / 2;
    cy = middle(cx);
  }
  return centred(
    {
      finish: 'black',
      head: smooth(headPoints),
      face,
      scoring: null,
      edges: [],
      hosel,
      grooves,
      mark: null,
      ends: [0, 0],
      bounds: UNFRAMED,
    },
    [-cx, -cy]
  );
}

/** Frames a drawing, already centred on its scoring area, with room for the TOE and HEEL labels. */
function framed(moved: FaceShape): FaceShape {
  const head = flatten(moved.head);
  const parts = hoselParts(moved.hosel);
  const tube = flatten(parts.tube);
  const club = [...head, ...tube, ...flatten(parts.ferrule)];
  const across = [...cross(head, 0, false), ...cross(tube, 0, false)];
  const ends: [number, number] = [Math.min(...across) - 2, Math.max(...across) + 2];
  const minY = Math.min(...club.map((point) => point[1])) - 5;
  const { up } = axes(moved.hosel);
  const shaftX = moved.hosel.ground[0] + ((minY - moved.hosel.ground[1]) / up[1]) * up[0] + moved.hosel.shaftWidth;
  return {
    ...moved,
    ends,
    bounds: {
      minX: Math.min(...head.map((point) => point[0]), ends[0] - 12) - 1,
      maxX: Math.max(...club.map((point) => point[0]), shaftX, ends[1] + 15) + 1,
      minY,
      maxY: Math.max(...head.map((point) => point[1])) + 2,
    },
  };
}

const DRIVER_FACE: Point[] = [
  [-50, -19],
  [-35, -26],
  [-13.5, -27.6],
  [17, -27.6],
  [39.5, -25.8],
  [50.5, -20.5],
  [54.2, -11.5],
  [54.8, 0.5],
  [51, 10],
  [41, 21],
  [31, 27.5],
  [1.7, 29.6],
  [-17, 28.3],
  [-33, 22.5],
  [-46.5, 11],
  [-52.5, -5],
];

const DRIVER_HEAD: Point[] = [
  [-61, -15],
  [-57.5, -23],
  [-48, -28.5],
  [-28, -31],
  [1.7, -31.4],
  [32, -30.6],
  [52, -27.2],
  [59, -22.5],
  [58.5, -12],
  [57, 2],
  [52.5, 12.5],
  [43.5, 23],
  [27, 31.2],
  [1.7, 34],
  [-20, 32.5],
  [-38, 26],
  [-52, 13],
  [-58.5, -1],
  [-60.5, -9],
];

const FAIRWAY_FACE: Point[] = [
  [-43.5, -16.2],
  [-25, -17.7],
  [-6, -17.9],
  [21, -17.6],
  [40.5, -16.9],
  [47.6, -12.6],
  [47.5, -6],
  [46.5, 2],
  [41, 10.5],
  [28, 16.2],
  [0.7, 17.6],
  [-22.7, 16.5],
  [-36, 12],
  [-44, 5],
  [-47.5, -3.5],
  [-46.5, -10.5],
];

const FAIRWAY_HEAD: Point[] = [
  [-55, -5],
  [-50.5, -12.5],
  [-38, -19.2],
  [-22, -21],
  [-6, -21.3],
  [21, -21.2],
  [40.6, -19.6],
  [47, -17.6],
  [53.5, -12],
  [54, -4],
  [51, 6.5],
  [45, 13.5],
  [35, 19.4],
  [7.6, 22.6],
  [-20, 21.3],
  [-37, 17.5],
  [-48, 9.5],
  [-54, 0],
];

const IRON_HOSEL: Hosel = {
  ground: [36, 23],
  lieDeg: 62,
  width: [12.6, 11.2],
  from: 8,
  ferruleAt: 64,
  ferruleLength: 8,
  shaftTo: 110,
  shaftWidth: 9,
  rings: [],
  collar: 0,
};

const SLEEVE: Hosel = {
  ground: [54, -19],
  lieDeg: 60,
  width: [13, 14.6],
  from: 0,
  ferruleAt: 30,
  ferruleLength: 6,
  shaftTo: 80,
  shaftWidth: 8.6,
  rings: [11, 21],
  collar: 0,
};

const DRAWN: Record<FaceCategory, FaceShape> = {
  driver: wood(
    DRIVER_FACE,
    DRIVER_HEAD,
    { ...SLEEVE, ground: [53.5, -19.5], ferruleAt: 27, rings: [10, 19] },
    { rows: 8, pitch: 4.8, margin: 6, inner: 21, bars: [10, 16, 10], barPitch: 7.5 }
  ),
  fairway: fullWood(
    FAIRWAY_FACE,
    FAIRWAY_HEAD,
    { ...SLEEVE, ground: [52.5, -15], lieDeg: 58, ferruleAt: 22, rings: [13], collar: 2.6 },
    { rows: 6, pitch: 4.3, toe: 9, heel: 7, clearance: 3 }
  ),
  hybrid: centred(
    {
      finish: 'black',
      head: smooth([
        [-44.1, -18.3],
        [-17, -18.8],
        [11.3, -18],
        [27, -15.6],
        [36, -17],
        [42, -20.5],
        [52.5, -19],
        [53, -12],
        [47.5, -3],
        [45.2, 7],
        [38.5, 15.9],
        [17, 19.3],
        [-17, 19],
        [-40.7, 15.9],
        [-46.9, 9],
        [-48.4, -2.3],
        [-47.3, -13.5],
      ]),
      face: smooth([
        [-41.9, -16.3],
        [-28.3, -17],
        [0, -16.3],
        [22.6, -14.1],
        [35.1, -12.3],
        [43, -7.9],
        [43.6, 6.5],
        [39.6, 14],
        [17, 17.1],
        [-17, 17.1],
        [-37.3, 15.3],
        [-42.4, 9],
        [-43, -2.3],
      ]),
      scoring: null,
      edges: [],
      hosel: { ...SLEEVE, ground: [46, -14], lieDeg: 58, ferruleAt: 32, rings: [22, 27] },
      grooves: [...rows(-1.95, 3.9, 6, -35, 22), { y: 11.7, from: -22, to: 9 }],
      mark: null,
      ends: [0, 0],
      bounds: UNFRAMED,
    },
    [6.5, 0]
  ),
  iron: blade(
    {
      finish: 'chrome',
      le: 23,
      bow: 0.3,
      band: 2,
      toe: [-35, -2],
      peak: [-21, -30],
      arc: 9,
      crotch: 30,
      side: 40,
      sweep: 8,
      shoulder: 2.5,
      hosel: IRON_HOSEL,
    },
    { lowest: 4, pitch: 3.2, from: -17, to: 37, clearance: 3, margin: 1.5 }
  ),
  wedge: blade(
    {
      finish: 'satin',
      le: 24,
      bow: 0.8,
      band: 4,
      toe: [-38, -2],
      peak: [-22, -38.6],
      arc: 20,
      crotch: 21,
      side: 39,
      sweep: 10,
      shoulder: 13,
      hosel: { ...IRON_HOSEL, ground: [35, 24], lieDeg: 63, ferruleAt: 70 },
    },
    { lowest: 4, pitch: 3.1, from: -17, to: 34, clearance: 3, margin: 1.5 }
  ),
};

export const FACE_SHAPES = Object.fromEntries(
  (Object.keys(DRAWN) as FaceCategory[]).map((category) => [category, framed(DRAWN[category])])
) as Record<FaceCategory, FaceShape>;

export function faceCategory(club: string | null | undefined): FaceCategory {
  const name = (club ?? '').trim().toLowerCase();
  if (name === 'driver') return 'driver';
  if (name.endsWith('-wood')) return 'fairway';
  if (name.endsWith('-hybrid')) return 'hybrid';
  if (['pw', 'gw', 'sw', 'lw'].includes(name)) return 'wedge';
  return 'iron';
}

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
