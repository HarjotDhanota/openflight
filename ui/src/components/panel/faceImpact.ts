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

/** Our own drawings, toe left and hosel right as seen along the face normal from the target
 * (right-handed), svg mm (+x heel, +y down) around the face centre. Each outline was fitted to a
 * reference photo rectified to that view and scaled to one category dimension; the hosel rises
 * at atan(tan lie * cos loft) from the model's published specs (docs/camera/impact-location.md). */
export interface FaceShape {
  finish: Finish;
  head: string;
  /** The club's outer edge as one path: the head and the tube up to the ferrule, joined
   * tangent-on so its outline stroke runs on without a step. */
  silhouette: string;
  face: string;
  /** Where the face fades into the head, x from `[0]` (full) to `[1]` (gone): the hybrid's
   * brushed face runs into the neck with no edge. */
  faceFade: Point | null;
  /** The milled scoring area of an iron or wedge. */
  scoring: string | null;
  /** The topline highlight of an iron or wedge, on the face's edge from the heel round the top
   * of the toe; a stretch with `fade` fades out from its first point to its second. */
  edges: readonly { d: string; fade: readonly [Point, Point] | null }[];
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

type Tangents = Readonly<Record<number, Point>>;

/** The cubic Béziers of a smooth closed outline through `nodes` (Catmull-Rom). At a node listed
 * in `corners` the curve runs straight at its neighbours instead, a sharp corner; at a node in
 * `tangents` it runs along that unit direction. */
function segments(
  nodes: readonly Point[],
  corners: readonly number[],
  tangents: Tangents = {}
): [Point, Point, Point, Point][] {
  const n = nodes.length;
  const p = (k: number) => nodes[(k + n) % n];
  const sharp = (k: number) => corners.includes((k + n) % n);
  const half = (a: Point, b: Point): Point => [(a[0] - b[0]) / 2, (a[1] - b[1]) / 2];
  return nodes.map((a, k) => {
    const b = p(k + 1);
    const chord = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const along = (j: number, otherwise: Point): Point => {
      const t = tangents[(j + n) % n];
      return t ? [t[0] * chord, t[1] * chord] : otherwise;
    };
    const leave = along(k, sharp(k) ? add(b, a, -1) : half(p(k + 1), p(k - 1)));
    const arrive = along(k + 1, sharp(k + 1) ? add(b, a, -1) : half(p(k + 2), a));
    return [a, add(a, leave, 1 / 3), add(b, arrive, -1 / 3), b];
  });
}

function outline(nodes: readonly Point[], corners: readonly number[] = [], tangents: Tangents = {}): string {
  const parts = segments(nodes, corners, tangents).map(([, c1, c2, b]) => `C ${pt(c1)} ${pt(c2)} ${pt(b)}`);
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
    silhouette: movePath(shape.silhouette, dx, dy),
    face: movePath(shape.face, dx, dy),
    faceFade: shape.faceFade && [shape.faceFade[0] + dx, shape.faceFade[1] + dx],
    scoring: shape.scoring && movePath(shape.scoring, dx, dy),
    edges: shape.edges.map(({ d, fade }) => ({
      d: movePath(d, dx, dy),
      fade: fade && [add(fade[0], [dx, dy]), add(fade[1], [dx, dy])],
    })),
    hosel: { ...shape.hosel, ground: add(shape.hosel.ground, [dx, dy]) },
    grooves: shape.grooves.map((g) => ({
      y: Number(fmt(g.y + dy)),
      from: Number(fmt(g.from + dx)),
      to: Number(fmt(g.to + dx)),
    })),
    mark: shape.mark && movePath(shape.mark, dx, dy),
  };
}

/** The head and the club's silhouette. `nodes` run from the heel side of the neck round the
 * sole, toe and top line back to the neck, where they meet the tube's foot running along its
 * edges (a fixed tangent there, so no step). The head closes up to 1.5 mm inside the tube, which
 * covers the overlap so no seam shows; the silhouette carries on up the tube to the ferrule. */
function headAndSilhouette(nodes: readonly Point[], hosel: Hosel): { head: string; silhouette: string } {
  const n = nodes.length;
  const foot = hosel.tube[0][0];
  const under = Math.min(foot + 1.5, hosel.tube.length > 1 ? hosel.tube[1][0] : foot + 1.5);
  const [footLeft, underLeft, underRight, footRight] = [
    edge(hosel, foot, -1),
    edge(hosel, under, -1),
    edge(hosel, under, 1),
    edge(hosel, foot, 1),
  ];
  const unit = (v: Point): Point => [v[0] / Math.hypot(...v), v[1] / Math.hypot(...v)];
  const [up, down] = [unit(add(underLeft, footLeft, -1)), unit(add(footRight, underRight, -1))];
  const stations = hosel.tube.slice(1).map(([s]) => s);
  const sides = [...stations.map((s) => edge(hosel, s, -1)), ...[...stations].reverse().map((s) => edge(hosel, s, 1))];
  const m = sides.length;
  return {
    head: outline([...nodes, footLeft, underLeft, underRight, footRight], [n + 1, n + 2], { [n]: up, [n + 3]: down }),
    silhouette: outline(
      [...nodes, footLeft, ...sides, footRight],
      sides.map((_, k) => n + 1 + k),
      { [n]: up, [n + 1 + m]: down }
    ),
  };
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
  faceFade?: Point;
}

function wood(category: FaceCategory, o: Drawing): FaceShape {
  const shape: FaceShape = {
    finish: o.finish,
    ...headAndSilhouette(o.head, o.hosel),
    face: outline(o.face),
    faceFade: o.faceFade ?? null,
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
    ...headAndSilhouette(o.head, o.hosel),
    face,
    faceFade: null,
    scoring: `M ${[...top, ...bottom].map(pt).join(' L ')} Z`,
    edges: [
      { d: stretch(o.face, [0, last], 0, toeMost - 1), fade: null },
      { d: stretch(o.face, [0, last], toeMost - 1, toeMost), fade: [o.face[toeMost - 1], o.face[toeMost]] },
    ],
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

/* Node lists fitted to each reference photo rectified to the face-normal view, in its own mm
 * frame (the drawing is then moved onto its face centre). Driver, fairway and hybrid heads and faces, 16-18 nodes each. */

const DRIVER: Drawing = {
  finish: 'black',
  // prettier-ignore
  head: [
    [53.7, -11], [45.7, 4], [35.7, 14.8], [23.5, 22.6], [9.5, 26.4], [-6.3, 27.1], [-20.3, 24.2], [-34.6, 17],
    [-46.6, 6.9], [-58.2, -6.7], [-61.2, -17.4], [-56.8, -22.6], [-43.2, -28.9], [-26.4, -32.5], [-8.3, -33.4],
    [10.7, -31.3], [28.6, -26.9], [42.3, -22.7]
  ],
  // prettier-ignore
  face: [
    [-48, -2.8], [-57.4, -17], [-54.4, -21.6], [-34.7, -25.8], [-17.9, -26.9], [3.9, -26.3], [24, -23.9],
    [39.9, -20.1], [51.2, -14.9], [47.5, -5.3], [38, 8.9], [24.3, 18.2], [8.5, 22.3], [-7, 22.6], [-22.2, 19.5],
    [-36.1, 10]
  ],
  hosel: {
    ground: [20.02, 27.28],
    lieDeg: 56.67,
    tube: [
      [59.82, 13.67],
      [75.53, 13.56],
      [81.42, 12.1],
      [91.24, 10.26],
    ],
    ferruleLength: 0.98,
    shaftTo: 140.3,
    shaftWidth: 7.89,
    rings: [74.54],
    collar: null,
  },
  /* Seven lines a block; the outer ends step with the face edge, the inner ends lie on two lines
   * slanting in toward the sole; three centre bars, the middle one longest, the cross on it. */
  grooves: [
    ...evenRows(
      -16.33,
      4.723,
      [-42.5, -42.5, -40.8, -37.9, -35.4, -31.3, -24.9].map((from, k): Point => [
        from,
        -20.23 + 0.1884 * (-16.33 + k * 4.723),
      ])
    ),
    ...evenRows(
      -16.33,
      4.723,
      [38, 41.3, 42.6, 40.8, 37.5, 34.5, 28.5].map((to, k): Point => [21.88 + -0.2133 * (-16.33 + k * 4.723), to])
    ),
    ...evenRows(-8.68, 6.333, [
      [-8.7, 10.3],
      [-14.3, 16.3],
      [-8.4, 10.7],
    ]),
  ],
  mark: { at: [0.79, -2.22], half: 1.88 },
};

const FAIRWAY: Drawing = {
  finish: 'black',
  // prettier-ignore
  head: [
    [53.8, -2.3], [45.3, 6.2], [36.6, 11.5], [24.5, 15.7], [10.7, 17.9], [-3.6, 18.6], [-18.7, 17.8],
    [-31.8, 14.7], [-44.1, 8.4], [-51.8, -0.3], [-54, -10.5], [-45.5, -16.4], [-33.2, -20.3], [-20.7, -22.7],
    [-7, -24], [8.6, -23.7], [25.5, -21.5], [38.8, -18.6]
  ],
  // prettier-ignore
  face: [
    [-41.4, -3.1], [-42.3, -13.9], [-30, -17.2], [-8.1, -18.6], [14.2, -18.4], [38.1, -15.7], [50.5, -11.7],
    [49.9, -3], [41.4, 6.2], [25.9, 13.1], [8.7, 16], [-7.2, 16.1], [-20.6, 14.9], [-34.3, 6]
  ],
  hosel: {
    ground: [29.26, 18.6],
    lieDeg: 55.07,
    tube: [
      [41.75, 16.24],
      [42.24, 16.2],
      [62.79, 15.91],
      [74.15, 12.14],
    ],
    ferruleLength: 1.47,
    shaftTo: 123.5,
    shaftWidth: 9.2,
    rings: [53.98, 62.3],
    collar: [41.75, 44.39],
  },
  grooves: evenRows(-11.4, 4.117, [
    [-39.2, 39.9],
    [-38.2, 39.4],
    [-35.8, 37.5],
    [-33.7, 35.1],
    [-29, 30.9],
    [-22.4, 22.2],
  ]),
};

const HYBRID: Drawing = {
  finish: 'black',
  // prettier-ignore
  head: [
    [43.5, 6.6], [36.4, 12.4], [28.3, 15], [14, 17.1], [-19.9, 16.9], [-35.8, 13.3], [-45.2, 5.5], [-45.9, -7],
    [-41.9, -15.4], [-30.9, -17.3], [-16.8, -17.8], [-3.9, -17.1], [6.2, -15.7], [23.7, -12], [33.5, -10.2]
  ],
  // prettier-ignore
  face: [
    [-37.5, -0.9], [-37.4, -13], [-34, -16], [-13.8, -16.3], [-0.5, -15.1], [13.5, -13.4], [29.9, -9.2],
    [45.8, -9.5], [46.1, -5], [41.9, 2.3], [33.6, 9.7], [20.7, 13.2], [4.5, 13.9], [-15.5, 13], [-27, 11.6],
    [-34.7, 7.5]
  ],
  hosel: {
    ground: [27.91, 17.38],
    lieDeg: 56.97,
    tube: [
      [38.8, 14.05],
      [42.61, 13.86],
      [50.79, 13.81],
      [50.98, 12.49],
      [59.74, 11.61],
      [61.64, 11.17],
      [64.02, 10.25],
    ],
    ferruleLength: 0.95,
    shaftTo: 114.9,
    shaftWidth: 6.64,
    rings: [48.79, 53.07, 60.69],
    collar: null,
  },
  /* A short top line on the toe half (the lettering above it is left out), five long lines and a
   * shorter bottom line. */
  /* The brushed face runs into the neck with no edge: it fades out over its heel end. */
  faceFade: [29.2, 44.7],
  grooves: evenRows(-9.94, 3.593, [
    [-30.2, 0.4],
    [-30.2, 27.4],
    [-30.2, 27.4],
    [-30.2, 27.4],
    [-30.2, 27.4],
    [-30.2, 27.3],
    [-18.8, 16.2],
  ]),
};

/* Iron and wedge faces run from the face's top-heel corner over the top line and toe to its
 * bottom-heel corner; the heel side between them is straight. */

const IRON = {
  finish: 'chrome',
  // prettier-ignore
  head: [
    [40.7, 16.5], [28.4, 21.9], [17.3, 23.6], [3.2, 24.5], [-12.7, 24.4], [-29, 22.5], [-36.9, 18.8],
    [-42.6, 10.2], [-43.9, -4.5], [-39.5, -17.6], [-31.6, -25], [-21.1, -24.7], [-3.6, -18.8], [12.2, -12.9],
    [26.4, -7.2], [37, -3.9]
  ],
  // prettier-ignore
  face: [
    [32.5, -2.7], [19.5, -9.1], [-7.3, -19.1], [-25.2, -24.6], [-34.3, -21.7], [-40.4, -10.6], [-41.3, 3],
    [-37.1, 13.6], [-25.3, 19.1], [-6.9, 20.1], [21.6, 19.5], [32.5, 19.2]
  ],
  hosel: {
    ground: [29.6, 24.6],
    lieDeg: 58.17,
    tube: [
      [37.52, 12.92],
      [69.25, 12.92],
    ],
    ferruleLength: 9.07,
    shaftTo: 123.6,
    shaftWidth: 9.3,
    rings: [],
    collar: null,
  },
} as const satisfies Omit<Drawing, 'grooves'>;

const WEDGE = {
  finish: 'satin',
  // prettier-ignore
  head: [
    [50.4, 9.1], [34.8, 21.9], [22.8, 24.8], [5.4, 25.8], [-13, 25.4], [-29.8, 23.2], [-39.4, 15.4], [-44.1, 1.4],
    [-42.9, -14.9], [-35.8, -28.5], [-25, -33.9], [-10.7, -31.5], [5, -25.4], [18.7, -17.9], [32.9, -8.2],
    [44.9, -5.6]
  ],
  // prettier-ignore
  face: [
    [32.2, -7], [17.9, -17.1], [-0.9, -27.1], [-20.3, -32.8], [-32.5, -30.2], [-40.9, -18.5], [-43.2, -2.5],
    [-39, 12.8], [-29, 21.6], [-8.5, 24], [16.1, 23.8], [32.2, 22.4]
  ],
  hosel: {
    ground: [29.07, 25.87],
    lieDeg: 48.9,
    tube: [
      [43.16, 13.61],
      [72.25, 15.12],
    ],
    ferruleLength: 6.59,
    shaftTo: 109.1,
    shaftWidth: 9.38,
    rings: [62.12, 66.4],
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
    { count: 13, lowest: 17.42, pitch: 2.933, from: -26.4, to: 26.2, steps: [-12.6, -4.3, 3.7, 11.3, 18.3, 25.7] },
    [-27, 26.7]
  ),
  wedge: blade(
    'wedge',
    WEDGE,
    {
      count: 17,
      lowest: 20.27,
      pitch: 2.953,
      from: -25.2,
      to: 27.3,
      steps: [-13.3, -4.7, 2.1, 8, 13.1, 17.8, 21.9, 25.7],
    },
    [-25.6, 28.4]
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
