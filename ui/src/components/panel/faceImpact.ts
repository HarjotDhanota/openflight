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
  /** Appendix II: where the heel's outermost point is not clearly defined it is taken 0.875 in
   * above the ground. */
  woodHeelPointHeightMm: 22.23,
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
type Lines = readonly (readonly [number, number])[];
type Segment = [Point, Point, Point, Point];

/** Solves the small dense system `a x = b` (two right-hand sides) by Gaussian elimination. */
function solve(a: number[][], b: Point[]): Point[] {
  const n = a.length;
  const m = a.map((row, k) => [...row, b[k][0], b[k][1]]);
  for (let col = 0; col < n; col += 1) {
    let pivot = col;
    for (let r = col + 1; r < n; r += 1) if (Math.abs(m[r][col]) > Math.abs(m[pivot][col])) pivot = r;
    [m[col], m[pivot]] = [m[pivot], m[col]];
    for (let r = 0; r < n; r += 1) {
      if (r === col || m[r][col] === 0) continue;
      const f = m[r][col] / m[col][col];
      for (let c = col; c < n + 2; c += 1) m[r][c] -= f * m[col][c];
    }
  }
  return m.map((row, k) => [row[n] / m[k][k], row[n + 1] / m[k][k]]);
}

/** Node slopes (per mm of chord) of a C2 cubic spline through `p`: periodic, or open with each
 * end either clamped to a unit tangent or free (zero curvature). */
function slopes(p: readonly Point[], start: Point | null, end: Point | null, periodic: boolean): Point[] {
  const n = p.length;
  const h = (k: number) => Math.hypot(p[(k + 1) % n][0] - p[k % n][0], p[(k + 1) % n][1] - p[k % n][1]);
  const chord = (k: number): Point => {
    const [a, b, len] = [p[k % n], p[(k + 1) % n], h(k)];
    return [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
  };
  const a = Array.from({ length: n }, () => new Array<number>(n).fill(0));
  const b: Point[] = Array.from({ length: n }, (): Point => [0, 0]);
  const interior = (k: number) => {
    const [hp, hn] = [h((k - 1 + n) % n), h(k)];
    const [dp, dn] = [chord((k - 1 + n) % n), chord(k)];
    a[k][(k - 1 + n) % n] += hn;
    a[k][k] += 2 * (hp + hn);
    a[k][(k + 1) % n] += hp;
    b[k] = [3 * (hn * dp[0] + hp * dn[0]), 3 * (hn * dp[1] + hp * dn[1])];
  };
  if (periodic) {
    for (let k = 0; k < n; k += 1) interior(k);
    return solve(a, b);
  }
  for (let k = 1; k < n - 1; k += 1) interior(k);
  const [first, last] = [chord(0), chord(n - 2)];
  if (start) [a[0][0], b[0]] = [1, start];
  else [a[0][0], a[0][1], b[0]] = [2, 1, [3 * first[0], 3 * first[1]]];
  if (end) [a[n - 1][n - 1], b[n - 1]] = [1, end];
  else [a[n - 1][n - 2], a[n - 1][n - 1], b[n - 1]] = [1, 2, [3 * last[0], 3 * last[1]]];
  return solve(a, b);
}

/** The cubic Béziers of a fair closed outline through `nodes`: C2 cubic splines (chord-length
 * parameter, so curvature is continuous) between designed breaks. A node in `corners` is a sharp
 * corner; one in `tangents` a smooth join along that unit direction; a pair in `lines` a straight
 * segment, whose ends join the curves either side smoothly unless they are corners. With no breaks
 * the outline is one periodic spline. Each spline piece is the cubic Bézier it exactly is. */
function segments(
  nodes: readonly Point[],
  corners: readonly number[] = [],
  tangents: Tangents = {},
  lines: Lines = []
): Segment[] {
  const n = nodes.length;
  const unit = (v: Point): Point => [v[0] / Math.hypot(...v), v[1] / Math.hypot(...v)];
  const ends = new Map<number, [Point | null, Point | null]>(corners.map((k) => [k, [null, null]]));
  for (const [k, t] of Object.entries(tangents)) ends.set(Number(k), [unit(t), unit(t)]);
  for (const [i, j] of lines) {
    const d = unit(add(nodes[j % n], nodes[i], -1));
    const [ei, ej] = [ends.get(i) ?? [null, null], ends.get(j % n) ?? [null, null]];
    ends.set(i, [corners.includes(i) ? ei[0] : d, d]);
    ends.set(j % n, [d, corners.includes(j % n) ? ej[1] : d]);
  }
  const piece = (a: Point, b: Point, ma: Point, mb: Point): Segment => {
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]) / 3;
    return [a, add(a, ma, len), add(b, mb, -len), b];
  };
  if (ends.size === 0) {
    const m = slopes(nodes, null, null, true);
    return nodes.map((a, k) => piece(a, nodes[(k + 1) % n], m[k], m[(k + 1) % n]));
  }
  const out: Segment[] = new Array(n);
  const keys = [...ends.keys()].sort((p, q) => p - q);
  keys.forEach((start, k) => {
    const stop = keys[(k + 1) % keys.length];
    const run = stop > start ? stop - start : stop + n - start;
    const idx = Array.from({ length: run + 1 }, (_, i) => (start + i) % n);
    const p = idx.map((i) => nodes[i]);
    const m = slopes(p, ends.get(start)![1], ends.get(stop)![0], false);
    for (let i = 0; i < run; i += 1) out[idx[i]] = piece(p[i], p[i + 1], m[i], m[i + 1]);
  });
  return out;
}

function outline(
  nodes: readonly Point[],
  corners: readonly number[] = [],
  tangents: Tangents = {},
  lines: Lines = []
): string {
  const parts = segments(nodes, corners, tangents, lines).map(([, c1, c2, b]) => `C ${pt(c1)} ${pt(c2)} ${pt(b)}`);
  return `M ${pt(nodes[0])} ${parts.join(' ')} Z`;
}

/** The open stretch of the outline from node `from` to node `to`. */
function stretch(nodes: readonly Point[], corners: readonly number[], lines: Lines, from: number, to: number): string {
  const parts = segments(nodes, corners, {}, lines)
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
function headAndSilhouette(
  nodes: readonly Point[],
  hosel: Hosel,
  lines: Lines = []
): { head: string; silhouette: string } {
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
    head: outline(
      [...nodes, footLeft, underLeft, underRight, footRight],
      [n + 1, n + 2],
      { [n]: up, [n + 3]: down },
      lines
    ),
    silhouette: outline(
      [...nodes, footLeft, ...sides, footRight],
      sides.map((_, k) => n + 1 + k),
      { [n]: up, [n + 1 + m]: down },
      lines
    ),
  };
}

function evenRows(top: number, pitch: number, ends: readonly Point[]): Groove[] {
  return ends.map(([from, to], k) => ({ y: top + k * pitch, from, to }));
}

interface Drawing {
  finish: Finish;
  head: readonly Point[];
  /** Straight stretches of the head outline, as pairs of consecutive node indices. */
  headLines?: Lines;
  face: readonly Point[];
  faceLines?: Lines;
  hosel: Hosel;
  grooves: Groove[];
  /** A centre cross this tall, on the computed face centre. */
  mark?: { half: number };
  faceFade?: Point;
}

function wood(category: FaceCategory, o: Drawing): FaceShape {
  const shape: FaceShape = {
    finish: o.finish,
    ...headAndSilhouette(o.head, o.hosel, o.headLines),
    face: outline(o.face, [], {}, o.faceLines),
    faceFade: o.faceFade ?? null,
    scoring: null,
    edges: [],
    hosel: o.hosel,
    grooves: o.grooves,
    mark: null,
    ends: [0, 0],
    bounds: UNFRAMED,
  };
  const [x, y] = faceCentre(category, shape);
  const mark = o.mark ? `M ${pt([x, y - o.mark.half])} L ${pt([x, y + o.mark.half])}` : null;
  return centred({ ...shape, mark }, [-x, -y]);
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
  const faceLines = o.faceLines ?? [];
  const face = outline(o.face, [0, last], {}, faceLines);
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
    ...headAndSilhouette(o.head, o.hosel, o.headLines),
    face,
    faceFade: null,
    scoring: `M ${[...top, ...bottom].map(pt).join(' L ')} Z`,
    edges: [
      { d: stretch(o.face, [0, last], faceLines, 0, toeMost - 1), fade: null },
      { d: stretch(o.face, [0, last], faceLines, toeMost - 1, toeMost), fade: [o.face[toeMost - 1], o.face[toeMost]] },
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

/* The population-average club of each category, drawn along the face normal (svg mm, y down)
 * from the research aggregates in docs/camera/impact-location.md: each outline is a few fair
 * curves (C2 splines between designed breaks). The head rims the face with a band of one width all
 * the way round (the population's leading-edge band), then blends into the hosel at the heel; the
 * face centre, band and score lines land on the population values. */

const DRIVER: Drawing = {
  finish: 'black',
  // prettier-ignore
  head: [
    [64.2, -39.42], [53.71, -23.3], [49.06, -17.42], [43.4, -12.12], [37.12, -7.7], [30.37, -4.4],
    [23.02, -2.18], [15.06, -0.79], [6.85, -0.06], [-1.54, 0.1], [-10.12, -0.11], [-18.2, -0.77],
    [-25.75, -2.35], [-32.94, -4.99], [-39.53, -8.66], [-45.34, -13.36], [-50.48, -19.09], [-55.14, -25.72],
    [-59.29, -32.71], [-61.53, -38.77], [-61.35, -43.95], [-58.84, -48.59], [-54.06, -52.4], [-47.45, -55.2],
    [-39.76, -57.11], [-31.57, -58.38], [-23.1, -59.2], [-14.53, -59.73], [-5.98, -59.94], [2.52, -59.82],
    [10.82, -59.23], [19.13, -58.09], [27.37, -56.5], [34.96, -55.32], [42.65, -54.86], [50.26, -55.48],
    [57.6, -57.93]
  ],
  headLines: [[0, 1]],
  // prettier-ignore
  face: [
    [52.15, -38.89], [40.29, -47.39], [20.6, -52.96], [6.48, -54.8], [-5.83, -55.14], [-21.09, -54.54],
    [-56.89, -40.14], [-52.97, -31.33], [-37.96, -13.42], [-20.41, -6], [-6.45, -4.79], [5.96, -4.82],
    [19.61, -6.37], [36.02, -12.67]
  ],
  hosel: {
    ground: [30.39, 0],
    lieDeg: 56.95,
    // prettier-ignore
    tube: [[70.71, 13.67], [86.42, 13.56], [92.31, 12.1], [102.13, 10.26]],
    ferruleLength: 0.98,
    shaftTo: 151.2,
    shaftWidth: 7.89,
    rings: [85.43],
    collar: null,
  },
  /* Seven lines a block; the outer ends step with the face edge, the inner ends lie on two lines
   * slanting in toward the sole; three centre bars, the middle one longest, the cross on the centre. */
  grooves: [
    // prettier-ignore
    ...evenRows(-43.35, 4.785, [[-41.85, -22.59], [-44.62, -21.69], [-44.08, -20.79], [-41.93, -19.89], [-40.19, -18.98], [-36.76, -18.08], [-30.73, -17.18]]),
    // prettier-ignore
    ...evenRows(-43.35, 4.785, [[26.08, 36.09], [25.06, 42.53], [24.04, 47.1], [23.02, 46.49], [21.99, 43.1], [20.97, 39.37], [19.95, 32.81]]),
    ...evenRows(-36.31, 6.417, [
      [-9.62, 9.38],
      [-15.42, 15.18],
      [-9.67, 9.43],
    ]),
  ],
  mark: { half: 1.9 },
};

const FAIRWAY: Drawing = {
  finish: 'black',
  // prettier-ignore
  head: [
    [49.81, -24.27], [44.98, -17.21], [40.26, -11.92], [34.18, -7.24], [27.52, -3.62], [20.65, -1.54],
    [12.71, -0.68], [4.04, -0.19], [-4.34, -0.19], [-12.91, -0.75], [-21.04, -1.68], [-27.97, -3.69],
    [-34.42, -7.23], [-40.41, -12.01], [-45.28, -17.49], [-48, -22.94], [-48, -27.39], [-45.64, -30.78],
    [-40.49, -33.21], [-32.97, -34.59], [-24.46, -35.35], [-15.82, -35.74], [-7.01, -35.9], [1.81, -35.91],
    [10.63, -35.77], [19.36, -35.48], [25.22, -35.29], [31.04, -35.54], [36.69, -36.93]
  ],
  headLines: [[0, 1]],
  // prettier-ignore
  face: [
    [43.86, -25.98], [31.12, -31.25], [14.34, -32.77], [3.7, -32.99], [-3.11, -33.02], [-13.73, -32.89],
    [-29.8, -32.01], [-44.79, -27.48], [-28.81, -7.27], [-13.73, -3.73], [-3.48, -3.06], [3.38, -3.07],
    [12.97, -3.6], [25.83, -6.06]
  ],
  hosel: {
    ground: [23.38, 0],
    lieDeg: 55.65,
    // prettier-ignore
    tube: [[43.51, 16.24], [64.55, 15.91], [75.91, 12.14]],
    ferruleLength: 1.47,
    shaftTo: 125.3,
    shaftWidth: 9.2,
    rings: [55.74, 64.06],
    collar: [43.51, 46.15],
  },
  // prettier-ignore
  grooves: evenRows(-27.11, 4.7, [[-37.28, 35.22], [-38.03, 35.97], [-35.03, 32.97], [-29.53, 27.47], [-21.03, 18.97]]),
};

const HYBRID: Drawing = {
  finish: 'black',
  // prettier-ignore
  head: [
    [45.9, -21.47], [40.74, -13.74], [36.16, -8.8], [30.04, -4.66], [23.35, -1.84], [16.13, -0.54],
    [7.61, -0.14], [-0.98, -0.05], [-9.38, -0.53], [-17.83, -1.43], [-24.98, -3.19], [-31.45, -6.41],
    [-37.52, -11.02], [-42.5, -16.42], [-45.3, -21.77], [-45.39, -26.26], [-43.08, -29.69], [-38, -32.13],
    [-30.51, -33.46], [-21.93, -34.19], [-13.23, -34.59], [-4.39, -34.79], [4.45, -34.85], [13.31, -34.77],
    [21.57, -34.41], [27.83, -33.89], [34.06, -34]
  ],
  headLines: [[0, 1]],
  // prettier-ignore
  face: [
    [39.44, -21.42], [27.66, -29.85], [14.59, -31.45], [2.15, -31.55], [-3.22, -31.51], [-12.89, -31.3],
    [-27.35, -30.46], [-41.85, -26.17], [-27.65, -8], [-12.65, -4.15], [-3.7, -3.44], [2.67, -3.34],
    [11.66, -3.59], [24.29, -5.63]
  ],
  hosel: {
    ground: [23.12, 0],
    lieDeg: 56.28,
    // prettier-ignore
    tube: [[39.8, 14.05], [43.61, 13.86], [51.79, 13.81], [51.98, 12.49], [60.74, 11.61], [62.64, 11.17], [65.02, 10.25]],
    ferruleLength: 0.95,
    shaftTo: 115.9,
    shaftWidth: 6.64,
    rings: [49.79, 54.07, 61.69],
    collar: null,
  },
  /* The brushed face runs into the neck with no edge: it fades out over its heel end. */
  faceFade: [23.94, 39.44],
  // prettier-ignore
  grooves: evenRows(-25.64, 3.5, [[-32.05, 29.95], [-33.55, 31.45], [-32.55, 30.45], [-30.05, 27.95], [-25.55, 23.45], [-18.95, 16.85]]),
};

/* Iron and wedge faces run from the face's top-heel corner over the top line and toe to its
 * bottom-heel corner; the heel side between them is straight. */

const IRON = {
  finish: 'chrome',
  // prettier-ignore
  head: [
    [84.87, -5.65], [79.53, -3.3], [58.06, -0.32], [36.01, -0.05], [16.19, -1.82], [8.2, -4.91], [0, -26],
    [8.17, -52.37], [12.92, -56.32], [30, -56.07], [74, -35.65], [81.38, -29.63]
  ],
  headLines: [[9, 10]],
  // prettier-ignore
  face: [
    [78, -28.93], [72.99, -33.47], [28.99, -53.9], [23.48, -55.53], [17.94, -55.51], [13.44, -53.85],
    [9.39, -50.1], [6.01, -44.9], [3.76, -38.89], [2.6, -32.31], [2.42, -25.35], [3.17, -18.58],
    [4.98, -12.67], [7.87, -8.31], [11.89, -5.64], [18.04, -3.88], [25.13, -2.88], [32.63, -2.5],
    [40.5, -2.42], [48.41, -2.48], [56.33, -2.66], [64.09, -3], [71.33, -3.78], [78, -5.32]
  ],
  faceLines: [[1, 2]],
  hosel: {
    ground: [78.2, 0],
    lieDeg: 58.4,
    // prettier-ignore
    tube: [[40, 13.05], [72, 13.05]],
    ferruleLength: 9.07,
    shaftTo: 126,
    shaftWidth: 9.3,
    rings: [],
    collar: null,
  },
} as const satisfies Omit<Drawing, 'grooves'>;

const WEDGE = {
  finish: 'satin',
  // prettier-ignore
  head: [
    [88.19, -6.88], [79.96, -3.61], [58.1, -0.46], [35.99, -0.06], [16.32, -2.51], [7.38, -7.47], [0, -30],
    [5.51, -51.73], [14.25, -61.5], [30.01, -62.31], [55.91, -50.94], [72.11, -39.32], [84.54, -29.04]
  ],
  // prettier-ignore
  face: [
    [77, -30.91], [71.46, -36.57], [66.33, -41.21], [60.51, -45.33], [54.02, -49.27], [47.39, -53.01],
    [40.84, -56.22], [34.24, -58.71], [27.6, -60.33], [21.38, -60.81], [16.31, -59.81], [12.35, -57.14],
    [8.7, -52.47], [5.67, -46.54], [3.62, -40.24], [2.57, -33.62], [2.46, -26.79], [3.32, -20.25],
    [5.26, -14.47], [8.29, -9.95], [12.56, -6.72], [18.29, -4.41], [24.81, -3.08], [31.95, -2.54],
    [39.62, -2.43], [47.37, -2.49], [55.11, -2.72], [62.77, -3.14], [70.1, -3.91], [77, -5.27]
  ],
  hosel: {
    ground: [81.8, 0],
    lieDeg: 48.7,
    // prettier-ignore
    tube: [[44, 13.6], [73.5, 15.12]],
    ferruleLength: 6.59,
    shaftTo: 112,
    shaftWidth: 9.38,
    rings: [63.2, 67.5],
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
    {
      count: 13,
      lowest: -6.74,
      pitch: 3.46,
      from: 17.4,
      to: 71,
      steps: [30.01, 37.34, 44.66, 52.75, 60.08, 65.57],
    },
    [16.8, 71.6]
  ),
  wedge: blade(
    'wedge',
    WEDGE,
    {
      count: 18,
      lowest: -6.06,
      pitch: 2.98,
      from: 18.9,
      to: 71.1,
      steps: [31.92, 38.27, 43.65, 48.78, 53.58, 57.75, 61.83, 66.1],
    },
    [18.3, 71.7]
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

/** What the club drawings read from a shot. A club with no shot yet is drawn from
 * `clubModel`: its model, every value unmeasured. */
export type DeliveryShot = Pick<Shot, 'club' | 'timestamp' | 'metrics' | 'impact_location' | 'handed'>;

/** The club's model with no shot behind it: nothing measured, so nothing drawn as read. */
export function clubModel(club: string): DeliveryShot {
  return { club, timestamp: '', metrics: null, impact_location: null, handed: null };
}

/** The hand the shot was read or hit with, else ``fallback`` (the kiosk's now), else right. */
export function shotHandedness(shot: DeliveryShot, fallback: 'RH' | 'LH' | null = null): 'right' | 'left' {
  const hand = shot.impact_location?.handedness;
  if (hand === 'right' || hand === 'left') return hand;
  const handed = shot.handed ?? fallback;
  return handed === 'LH' ? 'left' : 'right';
}

export function impactReading(shot: DeliveryShot): ImpactReading {
  const [offset, offsetSigma, offsetReason] = axis(shot.metrics?.impact_offset ?? undefined);
  const [height, heightSigma, heightReason] = axis(shot.metrics?.impact_height ?? undefined);
  return {
    offset,
    offsetSigma,
    offsetReason,
    height,
    heightSigma,
    heightReason,
    handedness: shotHandedness(shot),
  };
}

export function formatLength(mm: number): string {
  return `${Math.round(mm)} mm`;
}
