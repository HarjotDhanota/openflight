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
  /** Where the axis meets the sole line, and its angle to the score lines. */
  ground: Point;
  lieDeg: number;
  /** The neck or sleeve: [distance up the axis from `ground`, width] from its foot to its top. */
  tube: readonly Point[];
  /** The ferrule or sleeve cap above the tube, tapering to the shaft. */
  ferruleLength: number;
  shaftTo: number;
  shaftWidth: number;
  /** Joints across the tube, mm up the axis, and a grey band between two such distances. */
  rings: readonly number[];
  collar: Point | null;
}

export interface Groove {
  y: number;
  from: number;
  to: number;
}

/** chrome and satin steel blades, black-headed woods. */
export type Finish = 'chrome' | 'satin' | 'black';

/** Our own drawings, toe left and hosel right as seen from the target (right-handed), svg mm
 * (+x heel, +y down) around the face centre. Each outline was fitted to a reference photo
 * levelled on its score lines and scaled to one category dimension (docs/camera/impact-location.md). */
export interface FaceShape {
  finish: Finish;
  head: string;
  face: string;
  /** The milled scoring area of an iron or wedge. */
  scoring: string | null;
  /** The topline highlight of an iron or wedge, on the face's edge from the heel to the upper toe. */
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

function axes(hosel: Hosel): { up: Point; right: Point } {
  const lie = (hosel.lieDeg * Math.PI) / 180;
  return { up: [Math.cos(lie), -Math.sin(lie)], right: [Math.sin(lie), Math.cos(lie)] };
}

function tubeWidth(hosel: Hosel, s: number): number {
  const { tube } = hosel;
  if (s <= tube[0][0]) return tube[0][1];
  for (let k = 1; k < tube.length; k += 1) {
    const [s0, w0] = tube[k - 1];
    const [s1, w1] = tube[k];
    if (s <= s1) return w0 + ((s - s0) / (s1 - s0)) * (w1 - w0);
  }
  return tube[tube.length - 1][1];
}

/** A point on the tube's left (-1) or right (+1) side `s` mm up the axis. */
function edge(hosel: Hosel, s: number, side: -1 | 1): Point {
  const { up, right } = axes(hosel);
  return add(add(hosel.ground, up, s), right, (side * tubeWidth(hosel, s)) / 2);
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
  const stations = hosel.tube.map(([s]) => s);
  const [top, topWidth] = hosel.tube[hosel.tube.length - 1];
  const cap = top + hosel.ferruleLength;
  const band = ([from, to]: Point) =>
    quad([edge(hosel, from, -1), edge(hosel, to, -1), edge(hosel, to, 1), edge(hosel, from, 1)]);
  return {
    tube: quad([...stations.map((s) => edge(hosel, s, -1)), ...[...stations].reverse().map((s) => edge(hosel, s, 1))]),
    ferrule: quad([
      at(top, topWidth, -1),
      at(cap, hosel.shaftWidth + 0.6, -1),
      at(cap, hosel.shaftWidth + 0.6, 1),
      at(top, topWidth, 1),
    ]),
    shaft: quad([
      at(cap - 0.5, hosel.shaftWidth, -1),
      at(hosel.shaftTo, hosel.shaftWidth * 0.94, -1),
      at(hosel.shaftTo, hosel.shaftWidth * 0.94, 1),
      at(cap - 0.5, hosel.shaftWidth, 1),
    ]),
    rings: hosel.rings.map((s) => `M ${pt(edge(hosel, s, -1))} L ${pt(edge(hosel, s, 1))}`),
    collar: hosel.collar ? band(hosel.collar) : null,
    shaftLine: [add(hosel.ground, up, cap), add(hosel.ground, up, hosel.shaftTo)],
  };
}

/** The cubic Béziers of a smooth closed outline through `nodes` (Catmull-Rom). At a node listed
 * in `corners` the curve runs straight at its neighbours instead, a sharp corner. */
function segments(nodes: readonly Point[], corners: readonly number[]): [Point, Point, Point, Point][] {
  const n = nodes.length;
  const p = (k: number) => nodes[(k + n) % n];
  const sharp = (k: number) => corners.includes((k + n) % n);
  const half = (a: Point, b: Point): Point => [(a[0] - b[0]) / 2, (a[1] - b[1]) / 2];
  return nodes.map((a, k) => {
    const b = p(k + 1);
    const leave = sharp(k) ? add(b, a, -1) : half(p(k + 1), p(k - 1));
    const arrive = sharp(k + 1) ? add(b, a, -1) : half(p(k + 2), a);
    return [a, add(a, leave, 1 / 3), add(b, arrive, -1 / 3), b];
  });
}

function outline(nodes: readonly Point[], corners: readonly number[] = []): string {
  const parts = segments(nodes, corners).map(([, c1, c2, b]) => `C ${pt(c1)} ${pt(c2)} ${pt(b)}`);
  return `M ${pt(nodes[0])} ${parts.join(' ')} Z`;
}

/** The open stretch of the outline from node `from` to node `to`. */
function stretch(nodes: readonly Point[], corners: readonly number[], from: number, to: number): string {
  const parts = segments(nodes, corners)
    .slice(from, to)
    .map(([, c1, c2, b]) => `C ${pt(c1)} ${pt(c2)} ${pt(b)}`);
  return `M ${pt(nodes[from])} ${parts.join(' ')}`;
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

/** The middle of the face's chord through `at`, across (heel-toe, at height y) or up (at x). */
function chordMiddle(face: Point[], at: number, vertical: boolean): number {
  const hits = cross(face, at, vertical);
  return (hits[0] + hits[hits.length - 1]) / 2;
}

/** The face centre, per club type (R&A/USGA TPX3004 centre template: equal heel-toe and
 * sole-crown distances to the face edges).
 * Woods and hybrids: on the drawn face outline, from the centre of its box, alternately move to
 * the middle of the heel-toe chord (parallel to the score lines) and of the sole-crown chord
 * until the point moves less than 0.01 mm.
 * Irons and wedges: heel-toe at the middle of the longest score line (the milled area's edges
 * sit within 0.6 mm of its ends on both sides, so they agree to 0.1 mm); up the face midway
 * between the leading edge and the top line on the line through that point. */
export function faceCentre(category: FaceCategory, shape: Pick<FaceShape, 'face' | 'grooves'>): Point {
  const face = flatten(shape.face);
  if (category === 'iron' || category === 'wedge') {
    const longest = shape.grooves.reduce((best, groove) =>
      groove.to - groove.from > best.to - best.from ? groove : best
    );
    const x = (longest.from + longest.to) / 2;
    return [x, chordMiddle(face, x, true)];
  }
  const xs = face.map((point) => point[0]);
  const ys = face.map((point) => point[1]);
  let [x, y] = [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
  for (let pass = 0; pass < 200; pass += 1) {
    const nextX = chordMiddle(face, y, false);
    const nextY = chordMiddle(face, nextX, true);
    const moved = Math.hypot(nextX - x, nextY - y);
    [x, y] = [nextX, nextY];
    if (moved < 0.01) break;
  }
  return [x, y];
}

const UNFRAMED: FaceShape['bounds'] = { minX: 0, maxX: 0, minY: 0, maxY: 0 };

function movePath(d: string, dx: number, dy: number): string {
  let k = 0;
  return d.replace(/-?\d*\.?\d+/g, (n) => fmt(Number(n) + (k++ % 2 ? dy : dx)));
}

/** Moves a drawing by (dx, dy) so its face centre lands on the origin. */
function centred(shape: FaceShape, [dx, dy]: Point): FaceShape {
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
  };
}

/** The head outline: `nodes` from the heel side of the neck round the sole, toe and top line
 * back to the neck, where its edges meet the tube's at the tube's foot; it closes 1.5 mm up
 * inside the tube, which covers the overlap, so no seam shows across the neck. */
function headOutline(nodes: readonly Point[], hosel: Hosel): string {
  const n = nodes.length;
  const under = hosel.tube[0][0] + 1.5;
  return outline([...nodes, edge(hosel, under, -1), edge(hosel, under, 1)], [n, n + 1]);
}

function evenRows(top: number, pitch: number, ends: readonly Point[]): Groove[] {
  return ends.map(([from, to], k) => ({ y: top + k * pitch, from, to }));
}

interface Drawing {
  finish: Finish;
  head: readonly Point[];
  face: readonly Point[];
  hosel: Hosel;
  grooves: Groove[];
  mark?: { at: Point; half: number };
}

function wood(category: FaceCategory, o: Drawing): FaceShape {
  const shape: FaceShape = {
    finish: o.finish,
    head: headOutline(o.head, o.hosel),
    face: outline(o.face),
    scoring: null,
    edges: [],
    hosel: o.hosel,
    grooves: o.grooves,
    mark: o.mark ? `M ${pt(add(o.mark.at, [0, -o.mark.half]))} L ${pt(add(o.mark.at, [0, o.mark.half]))}` : null,
    ends: [0, 0],
    bounds: UNFRAMED,
  };
  const [x, y] = faceCentre(category, shape);
  return centred(shape, [-x, -y]);
}

/** Iron and wedge score lines: `count` lines at a pitch up from the lowest, all from `from`; the
 * long ones run to `to`, the top ones step back under the top line to `steps` (top first). */
interface BladeLines {
  count: number;
  lowest: number;
  pitch: number;
  from: number;
  to: number;
  steps: readonly number[];
}

function blade(category: FaceCategory, o: Omit<Drawing, 'grooves'>, lines: BladeLines, milled: Point): FaceShape {
  const last = o.face.length - 1;
  const face = outline(o.face, [0, last]);
  const polygon = flatten(face);
  const grooves = Array.from({ length: lines.count }, (_, k) => {
    const fromTop = lines.count - 1 - k;
    return {
      y: lines.lowest - k * lines.pitch,
      from: lines.from,
      to: fromTop < lines.steps.length ? lines.steps[fromTop] : lines.to,
    };
  });
  const [x0, x1] = milled;
  const steps = Math.ceil(x1 - x0);
  const along = (k: number) => x0 + ((x1 - x0) * k) / steps;
  const top = Array.from({ length: steps + 1 }, (_, k): Point => [along(k), cross(polygon, along(k), true)[0] + 0.4]);
  const bottom = Array.from({ length: steps + 1 }, (_, k): Point => {
    const x = along(steps - k);
    const ys = cross(polygon, x, true);
    return [x, ys[ys.length - 1] - 0.4];
  });
  const toeMost = o.face.reduce((best, point, k) => (point[0] < o.face[best][0] ? k : best), 0);
  const shape: FaceShape = {
    finish: o.finish,
    head: headOutline(o.head, o.hosel),
    face,
    scoring: `M ${[...top, ...bottom].map(pt).join(' L ')} Z`,
    edges: [stretch(o.face, [0, last], 0, toeMost - 1)],
    hosel: o.hosel,
    grooves,
    mark: null,
    ends: [0, 0],
    bounds: UNFRAMED,
  };
  const [x, y] = faceCentre(category, shape);
  return centred(shape, [-x, -y]);
}

/** Frames a drawing, already centred on its face centre, with room for the TOE and HEEL labels. */
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

/* Node lists fitted to each reference photo in its own levelled mm frame (the drawing is then
 * moved onto its face centre). Driver, fairway and hybrid heads and faces, 16-18 nodes each. */

const DRIVER: Drawing = {
  finish: 'black',
  // prettier-ignore
  head: [
    [58.6, -17.5], [53, -7], [49.7, -0.9], [43.8, 8.5], [33.2, 18.2], [19.6, 25.2], [-0.5, 28.3], [-19.8, 25.6],
    [-39.6, 14.9], [-57.6, -3.7], [-60.9, -12.6], [-59.2, -19], [-42, -27.8], [-19.9, -32.4], [3.3, -32],
    [24.6, -27.5], [41.9, -21.8], [46.7, -24.6]
  ],
  // prettier-ignore
  face: [
    [-48.3, -2.1], [-57.3, -15.7], [-53.1, -20.4], [-32.4, -25.2], [-16.9, -26.5], [4, -26.1], [23, -23.8],
    [38.1, -20], [51.4, -14.2], [48.5, -5.4], [39.2, 9.2], [25.3, 18.8], [9.5, 23], [-6, 23.4], [-23, 20.1],
    [-37.5, 9.8]
  ],
  hosel: {
    ground: [23.14, 28.34],
    lieDeg: 59.19,
    tube: [
      [58, 13.86],
      [74, 13.75],
      [80, 12.27],
      [90, 10.4],
    ],
    ferruleLength: 1,
    shaftTo: 140,
    shaftWidth: 8,
    rings: [73],
    collar: null,
  },
  /* Seven lines a block; the outer ends step with the face edge, the inner ends lie on two lines
   * slanting in toward the sole; three centre bars, the middle one longest, the cross on it. */
  grooves: [
    ...evenRows(
      -15.93,
      4.712,
      [-42.4, -42.5, -41, -38.2, -35.9, -31.8, -25.5].map((from, k): Point => [
        from,
        -20.52 + 0.1761 * (-15.93 + k * 4.712),
      ])
    ),
    ...evenRows(
      -15.93,
      4.712,
      [38.1, 41.6, 43.1, 41.4, 38.2, 35.3, 29.2].map((to, k): Point => [22.26 - 0.1969 * (-15.93 + k * 4.712), to])
    ),
    ...evenRows(-8.7, 6.39, [
      [-8.8, 10.4],
      [-14.4, 16.5],
      [-8.5, 10.8],
    ]),
  ],
  mark: { at: [0.8, -2.25], half: 1.9 },
};

const FAIRWAY: Drawing = {
  finish: 'black',
  // prettier-ignore
  head: [
    [54.8, -3.1], [45.5, 6.2], [37.9, 11], [25.8, 15.6], [11.3, 18.3], [-3.9, 19.1], [-19.7, 18.5], [-33.2, 15.2],
    [-45.8, 8.8], [-53.7, -0.1], [-55.9, -10.6], [-47.4, -16.2], [-34.7, -20.4], [-22.6, -22.8], [-8.3, -24.3],
    [6.9, -24.2], [22.7, -22.3], [40.8, -18.9]
  ],
  // prettier-ignore
  face: [
    [-42.7, -3.2], [-44.1, -12.5], [-37.4, -16.3], [-17.6, -18.4], [3.9, -18.9], [26.8, -17.6], [45.5, -14.5],
    [51.4, -10.9], [50, -2.6], [41.8, 6.1], [29.4, 12.1], [14.3, 15.9], [-2.8, 16.8], [-15.3, 16.6], [-22.9, 15.1],
    [-36.6, 5.5]
  ],
  hosel: {
    ground: [31.15, 19.17],
    lieDeg: 58.05,
    tube: [
      [41.5, 16.59],
      [42, 16.55],
      [63, 16.25],
      [74.6, 12.4],
    ],
    ferruleLength: 1.5,
    shaftTo: 125,
    shaftWidth: 9.4,
    rings: [54, 62.5],
    collar: [41.5, 44.2],
  },
  grooves: evenRows(-11.61, 4.198, [
    [-40.2, 40.1],
    [-39.2, 39.7],
    [-36.8, 37.9],
    [-34.7, 35.5],
    [-29.9, 31.5],
    [-23, 22.7],
  ]),
};

const HYBRID: Drawing = {
  finish: 'black',
  // prettier-ignore
  head: [
    [47.2, 4.5], [41, 11.5], [32.5, 15.3], [15, 18.1], [-18.7, 18], [-24.2, 17.5], [-36.3, 14.7], [-46.3, 6.8],
    [-46.2, -7.4], [-41.3, -15.4], [-29.5, -17.3], [-15.7, -17.6], [-2.9, -16.9], [8.4, -15.3], [21.2, -12.7],
    [35.2, -10.4]
  ],
  // prettier-ignore
  face: [
    [-38.2, -0.7], [-37.6, -13.3], [-34.1, -16], [-13.7, -16.3], [-0.2, -15.2], [14.6, -13.3], [30.9, -9.3],
    [46.9, -9.6], [47.4, -4.8], [43.2, 2.6], [34.8, 10.2], [21.4, 13.7], [4.5, 14.3], [-16.3, 13.3], [-27.9, 11.8],
    [-35.4, 7.7]
  ],
  hosel: {
    ground: [28.69, 18.43],
    lieDeg: 57.67,
    tube: [
      [40, 14.4],
      [44, 14.2],
      [52.5, 14.15],
      [53.2, 12.8],
      [62, 11.9],
      [64, 11.45],
      [66.5, 10.5],
    ],
    ferruleLength: 1,
    shaftTo: 120,
    shaftWidth: 6.8,
    rings: [50.5, 55, 63],
    collar: null,
  },
  /* A short top line on the toe half (the lettering above it is left out), five long lines and a
   * shorter bottom line. */
  grooves: evenRows(-10.08, 3.66, [
    [-30.6, 0.4],
    [-30.7, 28.1],
    [-30.7, 28.1],
    [-30.8, 28.2],
    [-30.9, 28.3],
    [-30.9, 28.3],
    [-19.4, 16.9],
  ]),
};

/* Iron and wedge faces run from the face's top-heel corner over the top line and toe to its
 * bottom-heel corner; the heel side between them is straight. */

const IRON = {
  finish: 'chrome',
  // prettier-ignore
  head: [
    [44.7, 12.2], [34.1, 20.2], [18.9, 23], [4, 23.8], [-12.2, 23.7], [-28.6, 21.8], [-37.3, 17.3], [-42.6, 8.6],
    [-43.8, -5.4], [-39.6, -18], [-32.1, -25.1], [-21.8, -24.9], [-4.7, -19.2], [12, -13.1], [28.4, -6.6],
    [38.3, -4.3]
  ],
  // prettier-ignore
  face: [
    [32.6, -2.7], [19.4, -9.1], [-7.7, -19.1], [-25.8, -24.5], [-34.8, -21.6], [-40.7, -10.6], [-41.4, 3],
    [-37, 13.6], [-25, 19], [-6.5, 20.1], [22.8, 19.4], [32.6, 19.6]
  ],
  hosel: {
    ground: [31.08, 23.73],
    lieDeg: 59.33,
    tube: [
      [36, 13.05],
      [64, 13.05],
    ],
    ferruleLength: 8,
    shaftTo: 112,
    shaftWidth: 9.4,
    rings: [],
    collar: null,
  },
} as const satisfies Omit<Drawing, 'grooves'>;

const WEDGE = {
  finish: 'satin',
  // prettier-ignore
  head: [
    [55.5, 6.7], [39.2, 21.4], [25, 25.2], [6.7, 26.4], [-10.3, 26.2], [-28.9, 24.5], [-38.1, 18.8], [-43.7, 7.5],
    [-44.1, -8.3], [-38.2, -23.1], [-28.6, -30.8], [-16, -30.9], [1.4, -25.2], [17, -17.5], [35.4, -5.7],
    [46, -4.6]
  ],
  // prettier-ignore
  face: [
    [31.9, -6.7], [17.1, -16.4], [-1.6, -25.7], [-20.9, -30.9], [-32.9, -27.8], [-41.1, -16], [-43.3, -0.2],
    [-38.7, 14.4], [-28.4, 22], [-6.8, 24.1], [17.5, 23.8], [31.9, 22.3]
  ],
  hosel: {
    ground: [33.79, 26.49],
    lieDeg: 55.23,
    tube: [
      [40, 13.63],
      [75.3, 15.15],
    ],
    ferruleLength: 8,
    shaftTo: 120,
    shaftWidth: 9.4,
    rings: [63, 68.2],
    collar: null,
  },
} as const satisfies Omit<Drawing, 'grooves'>;

const DRAWN: Record<FaceCategory, FaceShape> = {
  driver: wood('driver', DRIVER),
  fairway: wood('fairway', FAIRWAY),
  hybrid: wood('hybrid', HYBRID),
  iron: blade(
    'iron',
    IRON,
    { count: 13, lowest: 17.38, pitch: 2.926, from: -26.5, to: 26.4, steps: [-13, -4.6, 3.5, 11.1, 18.3, 25.7] },
    [-27.1, 26.8]
  ),
  wedge: blade(
    'wedge',
    WEDGE,
    {
      count: 17,
      lowest: 19.84,
      pitch: 2.858,
      from: -25.3,
      to: 27.6,
      steps: [-13.1, -4.8, 1.9, 7.7, 12.7, 17.4, 21.5, 25.4],
    },
    [-25.7, 28.3]
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
